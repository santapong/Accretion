from __future__ import annotations

from dataclasses import dataclass

from accretion.contracts import (
    AcceptancePolicy,
    RiskLevel,
    VerificationResult,
    VerificationStatus,
)


@dataclass(frozen=True, slots=True)
class AcceptanceEvaluation:
    status: VerificationStatus
    accepted: bool
    requires_human: bool
    reasons: tuple[str, ...]


def evaluate_acceptance(
    policy: AcceptancePolicy,
    results: list[VerificationResult],
    *,
    risk: RiskLevel | None = None,
) -> AcceptanceEvaluation:
    """Apply a persisted policy; uncertainty fails closed to human review."""
    by_verifier = {result.verifier_id: result for result in results}
    if policy.require_independent_reviewer:
        reviewer_ref = policy.independent_reviewer_ref
        if reviewer_ref is None or reviewer_ref not in by_verifier:
            return AcceptanceEvaluation(
                status=VerificationStatus.INCONCLUSIVE,
                accepted=False,
                requires_human=True,
                reasons=("The required independent reviewer is unavailable.",),
            )
    risk_rank = {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 1,
        RiskLevel.HIGH: 2,
        RiskLevel.CRITICAL: 3,
    }
    if (
        risk is not None
        and policy.require_human_if_risk_gte is not None
        and risk_rank[risk] >= risk_rank[policy.require_human_if_risk_gte]
    ):
        return AcceptanceEvaluation(
            status=VerificationStatus.INCONCLUSIVE,
            accepted=False,
            requires_human=True,
            reasons=(f"{risk.value} risk requires human acceptance.",),
        )
    missing = [
        verifier_id
        for verifier_id in policy.required_verifiers
        if verifier_id not in by_verifier
    ]
    if not policy.required_verifiers:
        return AcceptanceEvaluation(
            status=VerificationStatus.INCONCLUSIVE,
            accepted=False,
            requires_human=True,
            reasons=("No required verifiers are configured.",),
        )
    if missing:
        return AcceptanceEvaluation(
            status=VerificationStatus.INCONCLUSIVE,
            accepted=False,
            requires_human=True,
            reasons=(f"Required verifiers are unavailable: {', '.join(sorted(missing))}.",),
        )

    required_results = [by_verifier[verifier_id] for verifier_id in policy.required_verifiers]
    failed = [
        result.verifier_id
        for result in required_results
        if result.status is VerificationStatus.FAIL
    ]
    threshold_failures: list[str] = []
    for verifier_id, threshold in policy.score_thresholds.items():
        result = by_verifier.get(verifier_id)
        if result is not None and (result.score is None or result.score < threshold):
            threshold_failures.append(verifier_id)
    if failed or threshold_failures:
        names = sorted(set(failed + threshold_failures))
        return AcceptanceEvaluation(
            status=VerificationStatus.FAIL,
            accepted=False,
            requires_human=False,
            reasons=(f"Acceptance checks failed: {', '.join(names)}.",),
        )
    inconclusive = [
        result.verifier_id
        for result in required_results
        if result.status is VerificationStatus.INCONCLUSIVE
    ]
    if inconclusive and not policy.allow_inconclusive:
        return AcceptanceEvaluation(
            status=VerificationStatus.INCONCLUSIVE,
            accepted=False,
            requires_human=True,
            reasons=(
                f"Required verifiers were inconclusive: {', '.join(sorted(inconclusive))}.",
            ),
        )
    if policy.all_required_must_pass and any(
        result.status is not VerificationStatus.PASS
        and not (
            policy.allow_inconclusive
            and result.status is VerificationStatus.INCONCLUSIVE
        )
        for result in required_results
    ):
        return AcceptanceEvaluation(
            status=VerificationStatus.INCONCLUSIVE,
            accepted=False,
            requires_human=True,
            reasons=("Not every required verifier produced PASS.",),
        )
    return AcceptanceEvaluation(
        status=VerificationStatus.PASS,
        accepted=True,
        requires_human=False,
        reasons=("All required verifier checks passed.",),
    )
