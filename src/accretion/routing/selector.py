"""Deterministic cold-start selection over hard-eligible complete configurations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from accretion.contracts import PrincipalRef
from accretion.contracts.routing import (
    ConfigurationCandidate,
    ConstructionStage,
    DecisionType,
    DistributionEstimate,
    ExplanationFactor,
    PredictedOutcomes,
    RejectedCandidate,
    StructuredExplanation,
    UtilityWeights,
)
from accretion.ids import derived_id
from accretion.routing.catalog import PARETO_EPSILON

COLD_START_PRIOR_METHOD = "cold-start-prior/1"
DEFAULT_UTILITY_WEIGHTS = UtilityWeights(quality=1.0, cost=0.25, latency=0.15)
DETERMINISTIC_PROPENSITY = 1.0


def _estimate() -> DistributionEstimate:
    return DistributionEstimate(
        mean=0.5,
        lower_bound=0.25,
        upper_bound=0.75,
        confidence=0.5,
        method=COLD_START_PRIOR_METHOD,
    )


def cold_start_predictions() -> PredictedOutcomes:
    """The bounded, deliberately unconfident prior used when no evidence is available."""

    return PredictedOutcomes(
        quality=_estimate(),
        cost=_estimate(),
        latency=_estimate(),
        node_verified_success=_estimate(),
        run_verified_success=_estimate(),
    )


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """A deterministic choice and the scored slate that supports it."""

    candidates: tuple[ConfigurationCandidate, ...]
    selected: ConfigurationCandidate | None
    decision_type: DecisionType
    selection_propensity: float
    explanation: StructuredExplanation


def _dominates(
    left: ConfigurationCandidate,
    right: ConfigurationCandidate,
    epsilon: float,
) -> bool:
    """Whether ``left`` is materially better on the normalized Pareto axes."""

    left_axes = (
        left.predicted.quality.mean,
        -left.predicted.cost.mean,
        -left.predicted.latency.mean,
        -left.uncertainty_score,
    )
    right_axes = (
        right.predicted.quality.mean,
        -right.predicted.cost.mean,
        -right.predicted.latency.mean,
        -right.uncertainty_score,
    )
    no_material_regression = all(
        a >= b - epsilon for a, b in zip(left_axes, right_axes, strict=True)
    )
    material_gain = any(
        a > b + epsilon for a, b in zip(left_axes, right_axes, strict=True)
    )
    return no_material_regression and material_gain


class DeterministicSelector:
    """Apply success, Pareto, utility, and baseline fallback policy in fixed order."""

    def __init__(self, *, pareto_epsilon: float = PARETO_EPSILON) -> None:
        if pareto_epsilon < 0:
            raise ValueError("pareto epsilon cannot be negative")
        self.pareto_epsilon = pareto_epsilon

    def select(
        self,
        candidates: Sequence[ConfigurationCandidate],
        rejected: Sequence[RejectedCandidate] = (),
        *,
        verified_success_floor: float,
        created_at: datetime,
        created_by: PrincipalRef,
        workspace_id: str,
        project_id: str | None,
        utility_weights: UtilityWeights = DEFAULT_UTILITY_WEIGHTS,
    ) -> SelectionResult:
        """Return one deterministic action; insufficient evidence never becomes exploit."""

        if not 0 <= verified_success_floor <= 1:
            raise ValueError("verified success floor must lie in [0, 1]")
        ordered = tuple(
            sorted(
                candidates,
                key=lambda item: (item.configuration.configuration_hash, item.contract_id),
            )
        )
        eligible = tuple(item for item in ordered if item.hard_eligible)
        updated: list[ConfigurationCandidate] = []
        for candidate in ordered:
            dominated = False
            if candidate.hard_eligible and not candidate.fallback_eligible:
                dominated = any(
                    other.contract_id != candidate.contract_id
                    and other.hard_eligible
                    and _dominates(other, candidate, self.pareto_epsilon)
                    for other in eligible
                )
            passes_success = candidate.lower_confidence_success >= verified_success_floor
            utility = None
            stage = ConstructionStage.SUCCESS_GATE
            if candidate.hard_eligible and passes_success and not dominated:
                utility = round(
                    utility_weights.quality * candidate.predicted.quality.mean
                    - utility_weights.cost * candidate.predicted.cost.mean
                    - utility_weights.latency * candidate.predicted.latency.mean,
                    12,
                )
                stage = ConstructionStage.RANK_BY_UTILITY
            updated.append(
                _rebuild_candidate(
                    candidate,
                    construction_stage=stage,
                    utility_score=utility,
                    pareto_dominated=dominated,
                )
            )

        ranked = sorted(
            (
                item
                for item in updated
                if item.hard_eligible
                and not item.pareto_dominated
                and item.utility_score is not None
                and not item.fallback_eligible
            ),
            key=lambda item: (
                -(item.utility_score or 0.0),
                item.uncertainty_score,
                item.configuration.configuration_hash,
            ),
        )
        if ranked:
            selected = _rebuild_candidate(
                ranked[0], construction_stage=ConstructionStage.SELECT_BEHAVIOR
            )
            updated = [
                selected if item.contract_id == selected.contract_id else item
                for item in updated
            ]
            decision_type = DecisionType.EXPLOIT
        else:
            fallbacks = sorted(
                (item for item in updated if item.hard_eligible and item.fallback_eligible),
                key=lambda item: item.configuration.configuration_hash,
            )
            if fallbacks:
                selected = _rebuild_candidate(
                    fallbacks[0], construction_stage=ConstructionStage.SELECT_BEHAVIOR
                )
                updated = [
                    selected if item.contract_id == selected.contract_id else item
                    for item in updated
                ]
                decision_type = DecisionType.FALLBACK
            else:
                selected = None
                decision_type = DecisionType.HUMAN_REVIEW_REQUIRED

        explanation_rejections = list(
            {item.candidate_id: item for item in rejected}.values()
        )
        explanation_rejections.sort(key=lambda item: item.candidate_id)
        seen_rejections = {item.candidate_id for item in explanation_rejections}
        for item in updated:
            if selected is not None and item.contract_id == selected.contract_id:
                continue
            if item.contract_id in seen_rejections:
                continue
            if not item.hard_eligible:
                code, stage = "HARD_INELIGIBLE", ConstructionStage.JOINT_COMPATIBILITY
            elif item.pareto_dominated:
                code, stage = "PARETO_DOMINATED", ConstructionStage.RANK_BY_UTILITY
            elif item.lower_confidence_success < verified_success_floor:
                code, stage = "INSUFFICIENT_EVIDENCE", ConstructionStage.SUCCESS_GATE
            else:
                code, stage = "LOWER_UTILITY", ConstructionStage.RANK_BY_UTILITY
            explanation_rejections.append(
                RejectedCandidate(
                    candidate_id=item.contract_id,
                    stage=stage,
                    reason_code=code,
                    detail=f"Candidate was not selected ({code}).",
                )
            )
            seen_rejections.add(item.contract_id)

        if selected is None:
            summary = (
                "No hard-eligible audited fallback had sufficient authority; "
                "human review is required."
            )
            factors: list[ExplanationFactor] = []
        else:
            summary = (
                "Selected the audited deterministic fallback because evidence was insufficient."
                if decision_type is DecisionType.FALLBACK
                else "Selected the highest-utility hard-eligible candidate deterministically."
            )
            factors = [
                ExplanationFactor(
                    factor_id="quality",
                    description="Predicted normalized quality.",
                    weight=utility_weights.quality * selected.predicted.quality.mean,
                ),
                ExplanationFactor(
                    factor_id="cost",
                    description="Predicted normalized cost penalty.",
                    weight=-utility_weights.cost * selected.predicted.cost.mean,
                ),
                ExplanationFactor(
                    factor_id="latency",
                    description="Predicted normalized latency penalty.",
                    weight=-utility_weights.latency * selected.predicted.latency.mean,
                ),
            ]
        explanation = StructuredExplanation(  # type: ignore[call-arg]
            contract_id=derived_id(
                "routing_receipt",
                "explanation",
                selected.contract_id if selected is not None else "human-review",
                *(item.candidate_id for item in explanation_rejections),
            ),
            created_at=created_at,
            created_by=created_by,
            workspace_id=workspace_id,
            project_id=project_id,
            summary=summary,
            factors=factors,
            rejected_candidates=explanation_rejections,
        )
        return SelectionResult(
            candidates=tuple(updated),
            selected=selected,
            decision_type=decision_type,
            selection_propensity=DETERMINISTIC_PROPENSITY,
            explanation=explanation,
        )


def _rebuild_candidate(
    candidate: ConfigurationCandidate, **updates: object
) -> ConfigurationCandidate:
    """Apply selector fields and re-seal the immutable candidate document."""

    payload = candidate.model_dump(mode="python")
    payload.update(updates)
    payload["content_hash"] = ""
    return ConfigurationCandidate.model_validate(payload)


__all__ = [
    "COLD_START_PRIOR_METHOD",
    "DEFAULT_UTILITY_WEIGHTS",
    "DETERMINISTIC_PROPENSITY",
    "DeterministicSelector",
    "SelectionResult",
    "cold_start_predictions",
]
