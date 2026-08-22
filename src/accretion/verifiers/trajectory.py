from __future__ import annotations

import time

from accretion.contracts import (
    Finding,
    FindingSeverity,
    VerificationContext,
    VerificationResult,
    VerificationStatus,
    VerificationTarget,
    VerificationTargetKind,
)
from accretion.verifiers.results import finding, verification_result


class TrajectoryPolicyVerifier:
    verifier_id = "trajectory-policy"
    verifier_version = "trajectory-policy-v1"

    async def verify(
        self, target: VerificationTarget, context: VerificationContext
    ) -> VerificationResult:
        started_at = time.monotonic()
        if target.kind is not VerificationTargetKind.TRAJECTORY_POLICY:
            return verification_result(
                verifier_id=self.verifier_id,
                verifier_version=self.verifier_version,
                target=target,
                status=VerificationStatus.INCONCLUSIVE,
                started_at=started_at,
                findings=[
                    finding(
                        "TARGET_KIND_MISMATCH",
                        FindingSeverity.ERROR,
                        f"Expected TRAJECTORY_POLICY target, received {target.kind.value}.",
                    )
                ],
            )

        findings: list[Finding] = []
        denied = set(context.denied_capabilities)
        observed_denied = sorted(denied.intersection(context.observed_capabilities))
        for capability in observed_denied:
            findings.append(
                finding(
                    "DENIED_CAPABILITY_OBSERVED",
                    FindingSeverity.ERROR,
                    f"Denied capability was observed: {capability}.",
                )
            )
        for event in context.trajectory_events:
            if event.get("policy_violation") is True:
                findings.append(
                    finding(
                        "TRAJECTORY_POLICY_VIOLATION",
                        FindingSeverity.ERROR,
                        "The recorded trajectory contains a policy violation.",
                        evidence_ref=str(event.get("event_id")) if event.get("event_id") else None,
                    )
                )
        if context.unresolved_approval_ids:
            findings.append(
                finding(
                    "UNRESOLVED_APPROVALS",
                    FindingSeverity.ERROR,
                    "The trajectory contains unresolved approval requests.",
                    evidence_ref=",".join(sorted(context.unresolved_approval_ids)),
                )
            )
        status = VerificationStatus.FAIL if findings else VerificationStatus.PASS
        evidence_refs = [
            str(event["event_id"])
            for event in context.trajectory_events
            if event.get("event_id") is not None
        ]
        return verification_result(
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            target=target,
            status=status,
            started_at=started_at,
            findings=findings,
            evidence_refs=evidence_refs,
            score=0.0 if findings else 1.0,
            false_accept_risk_estimate=0.05 if not findings else None,
        )
