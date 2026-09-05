from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from accretion.contracts import PrincipalRef, PrincipalStatus, Provider
from accretion.contracts.refs import EnvironmentRef, RuntimeRef, VerifierRef
from accretion.contracts.routing import (
    ConfigurationCandidate,
    ConstructionStage,
    DecisionType,
    DistributionEstimate,
    EnvironmentBinding,
    ExecutionConfiguration,
    ModelBinding,
    PredictedOutcomes,
    VerifierBinding,
)
from accretion.ids import derived_id
from accretion.routing.selector import (
    DETERMINISTIC_PROPENSITY,
    DeterministicSelector,
    cold_start_predictions,
)

NOW = datetime(2026, 9, 6, 2, 0, tzinfo=UTC)
PRINCIPAL = PrincipalRef(
    principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    display_name="selector",
    status=PrincipalStatus.ACTIVE,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def estimate(mean: float, lower: float | None = None) -> DistributionEstimate:
    low = mean if lower is None else lower
    return DistributionEstimate(
        mean=mean,
        lower_bound=low,
        upper_bound=max(mean, low),
        confidence=0.9,
        method="selector-test/1",
    )


def predictions(
    *, quality: float, cost: float, latency: float, success: float
) -> PredictedOutcomes:
    return PredictedOutcomes(
        quality=estimate(quality),
        cost=estimate(cost),
        latency=estimate(latency),
        node_verified_success=estimate(success),
        run_verified_success=estimate(success),
    )


def candidate(
    name: str,
    *,
    predicted: PredictedOutcomes | None = None,
    fallback: bool = False,
    eligible: bool = True,
    uncertainty: float = 0.1,
) -> ConfigurationCandidate:
    predicted = predicted or cold_start_predictions()
    configuration = ExecutionConfiguration(  # type: ignore[call-arg]
        contract_id=derived_id("execution_configuration", name),
        created_at=NOW,
        created_by=PRINCIPAL,
        workspace_id="wks_selector",
        project_id="prj_selector",
        environment=EnvironmentBinding(
            environment=EnvironmentRef(
                environment_id="local", image_digest=digest("local"), policy_profile="restricted"
            ),
            workspace_isolation="WORKTREE",
        ),
        runtime=RuntimeRef(
            runtime_id=f"runtime-{name}",
            adapter_version="1.0.0",
            provider=Provider.FAKE,
            model=name,
            capability_profile_digest=digest(f"profile-{name}"),
        ),
        model=ModelBinding(model_id=name, provider=Provider.FAKE),
        verifier=VerifierBinding(
            verifier=VerifierRef(
                verifier_contract_id="output-contract", implementation_digest=digest("verifier")
            ),
            version="1.0.0",
            verification_spec_hash=digest("spec"),
        ),
    )
    return ConfigurationCandidate(  # type: ignore[call-arg]
        contract_id=derived_id("configuration_candidate", name),
        created_at=NOW,
        created_by=PRINCIPAL,
        workspace_id="wks_selector",
        project_id="prj_selector",
        routing_request_id=derived_id("routing_request", "selector"),
        configuration=configuration,
        construction_stage=ConstructionStage.PREDICT_OUTCOME,
        hard_eligible=eligible,
        predicted=predicted,
        uncertainty_score=uncertainty,
        lower_confidence_success=predicted.node_verified_success.lower_bound,
        fallback_eligible=fallback,
    )


def select(*items: ConfigurationCandidate):
    return DeterministicSelector().select(
        items,
        verified_success_floor=0.5,
        created_at=NOW,
        created_by=PRINCIPAL,
        workspace_id="wks_selector",
        project_id="prj_selector",
    )


@pytest.mark.acceptance("AC4-M2-022")
def test_cold_start_never_exploits_and_uses_audited_fallback() -> None:
    result = select(candidate("ordinary"), candidate("fallback", fallback=True))

    assert result.decision_type is DecisionType.FALLBACK
    assert result.selected is not None and result.selected.fallback_eligible
    assert result.selection_propensity == DETERMINISTIC_PROPENSITY == 1.0
    assert result.explanation.factors


@pytest.mark.acceptance("AC4-M2-022")
def test_insufficient_evidence_without_fallback_requires_human_review() -> None:
    result = select(candidate("ordinary"))

    assert result.selected is None
    assert result.decision_type is DecisionType.HUMAN_REVIEW_REQUIRED
    assert result.explanation.rejected_candidates[0].reason_code == "INSUFFICIENT_EVIDENCE"


def test_high_confidence_candidate_is_selected_by_utility_deterministically() -> None:
    strong = candidate(
        "strong",
        predicted=predictions(quality=0.9, cost=0.2, latency=0.2, success=0.8),
    )
    weak = candidate(
        "weak",
        predicted=predictions(quality=0.6, cost=0.4, latency=0.4, success=0.8),
    )
    forward = select(weak, strong)
    reverse = select(strong, weak)

    assert forward.decision_type is DecisionType.EXPLOIT
    assert forward.selected is not None
    assert (
        forward.selected.configuration.configuration_hash
        == strong.configuration.configuration_hash
    )
    assert reverse.selected is not None
    assert (
        reverse.selected.configuration.configuration_hash
        == strong.configuration.configuration_hash
    )
    assert forward.selected.content_hash == reverse.selected.content_hash


def test_near_frontier_candidate_within_epsilon_is_not_pareto_pruned() -> None:
    left = candidate(
        "left",
        predicted=predictions(quality=0.80, cost=0.20, latency=0.20, success=0.8),
    )
    near = candidate(
        "near",
        predicted=predictions(quality=0.79, cost=0.21, latency=0.21, success=0.8),
    )
    result = select(left, near)

    by_id = {item.contract_id: item for item in result.candidates}
    assert not by_id[near.contract_id].pareto_dominated


@pytest.mark.acceptance("AC4-M2-010")
def test_materially_dominated_candidate_is_pruned_but_fallback_is_retained() -> None:
    dominant = candidate(
        "dominant",
        predicted=predictions(quality=0.9, cost=0.1, latency=0.1, success=0.8),
    )
    dominated = candidate(
        "dominated",
        predicted=predictions(quality=0.5, cost=0.8, latency=0.8, success=0.8),
    )
    fallback = candidate(
        "fallback",
        predicted=predictions(quality=0.4, cost=0.9, latency=0.9, success=0.3),
        fallback=True,
    )
    result = select(dominated, fallback, dominant)
    by_id = {item.contract_id: item for item in result.candidates}

    assert by_id[dominated.contract_id].pareto_dominated
    assert not by_id[fallback.contract_id].pareto_dominated
    assert result.selected is not None and result.selected.contract_id == dominant.contract_id


def test_ineligible_candidate_is_never_selected_even_with_the_best_score() -> None:
    forbidden = candidate(
        "forbidden",
        predicted=predictions(quality=1.0, cost=0.0, latency=0.0, success=1.0),
        eligible=False,
    )
    fallback = candidate("fallback", fallback=True)
    result = select(forbidden, fallback)

    assert result.selected is not None
    assert result.selected.contract_id == fallback.contract_id
    assert result.decision_type is DecisionType.FALLBACK
