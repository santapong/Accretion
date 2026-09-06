"""M6.1: registering a shadow policy, recording what it would have chosen, and pairing.

Every claim here is about something a later milestone could break by accident. That a shadow
version is a *second* router version parented on the candidate rather than a status flip on it.
That the evaluation gate AC4-M4-016 states holds at this second door and not only at the
trainer's. That recording a shadow decision writes exactly one record and dispatches nothing.
That a pair is a pair — one SHADOW arm and one CONTROL arm of the *same trial* — and that an
arm without its partner is dropped rather than scored, which is the difference between
measuring the router and measuring the node.

The contracts are built from the committed golden fixtures through ``test_v04_m0_store.build``
rather than assembled field by field, for the reason that file gives: a test that invented its
own ``ShadowRolloutResult`` would prove the pairing works on whatever this file thinks a
rollout looks like.

The route tests share ``app`` with every other API test, so each installs its state and clears
it in a ``finally``; there is no ``conftest.py`` in this repository and none is added here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from test_v04_m0_store import (
    FIXTURE_PROJECT_ID,
    FIXTURE_WORKSPACE_ID,
    build,
    digest,
    new_store,
)

from accretion.api.auth import AuthRuntime
from accretion.api.main import app
from accretion.api.shadow import SHADOW_POLICIES_PATH
from accretion.contracts import (
    EventType,
    Principal,
    PrincipalRef,
    PrincipalStatus,
    Provider,
    Run,
    RunState,
    Task,
    TaskEnvelope,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.contracts.routing import (
    RouterModelVersion,
    RouterStatus,
    RoutingDecisionReceipt,
    ShadowDecision,
    ShadowRolloutResult,
    UtilityWeights,
)
from accretion.identity import IdentityService
from accretion.ids import derived_id, new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.shadow import (
    NON_INFERIORITY_GATE,
    PAIRED_RUNS_GATE,
    SHADOW_PARENT_LABEL,
    ShadowBudget,
    ShadowEvaluator,
    ShadowRegistrationError,
    ShadowReportConfig,
    paired_deltas,
    record_shadow_decision,
    shadow_gate,
    shadow_report,
)
from accretion.routing.train import (
    ACCEPTANCE_LABEL,
    CALIBRATION_REPORT_LABEL,
    RouterNotEvaluatedError,
)

WEIGHTS = UtilityWeights(quality=1.0, cost=0.25, latency=0.1)
"""Cost and latency priced high enough that a change in either moves the delta visibly."""

BUDGET = ShadowBudget(daily_cost_cap=12.5, max_trials_per_day=40)

EVALUATED_LABELS = {
    ACCEPTANCE_LABEL: digest("holdout"),
    CALIBRATION_REPORT_LABEL: digest("calibration"),
}
"""What a version the M4 trainer produced carries; the loader gates on exactly these two."""

PRINCIPAL = PrincipalRef(
    principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    display_name="v0.4 M6 test",
    status=PrincipalStatus.ACTIVE,
)


def candidate_version(**overrides: Any) -> RouterModelVersion:
    """An evaluated CANDIDATE, unless a test says otherwise."""

    fields: dict[str, Any] = {
        "status": RouterStatus.CANDIDATE.value,
        "labels": dict(EVALUATED_LABELS),
    }
    fields.update(overrides)
    return build(RouterModelVersion, **fields)


async def setup_evaluator(
    tmp_path: Path, **overrides: Any
) -> tuple[MemoryStore, ShadowEvaluator, RouterModelVersion]:
    """A store holding one candidate, and the evaluator that will shadow it.

    The clock is frozen to the fixture's own ``created_at`` so that a registration written
    twice differs in nothing at all — which is what makes the idempotence claim below a claim
    about the derived id rather than about two timestamps happening to match.
    """

    store = await new_store()
    candidate = await store.put_router_model_version(candidate_version(**overrides))
    evaluator = ShadowEvaluator(
        store, ArtifactStore(tmp_path / "artifacts"), lambda: candidate.created_at
    )
    return store, evaluator, candidate


def receipt(**overrides: Any) -> RoutingDecisionReceipt:
    """One routing receipt. The golden minimal selects nothing; overrides make it select."""

    return build(RoutingDecisionReceipt, **overrides)


def selecting_receipt(configuration_hash: str, **overrides: Any) -> RoutingDecisionReceipt:
    """A receipt that exploited a configuration with the given hash."""

    fields: dict[str, Any] = {
        "decision_type": "EXPLOIT",
        "selected_configuration_id": new_id("execution_configuration"),
        "selected_configuration_hash": configuration_hash,
    }
    fields.update(overrides)
    return receipt(**fields)


def rollout(
    decision_id: str,
    kind: str,
    trial_index: int,
    *,
    quality: float,
    cost: float,
    latency_ms: float = 10_000.0,
) -> ShadowRolloutResult:
    """One arm of one trial, with the observed numbers utility is computed from."""

    return build(
        ShadowRolloutResult,
        shadow_decision_id=decision_id,
        kind=kind,
        trial_index=trial_index,
        fork_execution_id=new_id("runtime_call"),
        observed={
            "quality": quality,
            "cost": cost,
            "latency_ms": latency_ms,
            "verified": False,
        },
    )


def shadow_decision_record(version_id: str, **overrides: Any) -> ShadowDecision:
    """A shadow decision naming ``version_id``, not stored."""

    fields: dict[str, Any] = {
        "shadow_router_version_id": version_id,
        "executed_receipt_id": new_id("routing_receipt"),
        "shadow_receipt_id": new_id("routing_receipt"),
    }
    fields.update(overrides)
    return build(ShadowDecision, **fields)


# --------------------------------------------------------------------------------------
# Registration.
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M4-016")
async def test_a_candidate_with_no_holdout_evaluation_cannot_be_given_a_shadow_stage(
    tmp_path: Path,
) -> None:
    """The second door AC4-M4-016 has to hold at, and the workspace's shadow list stays empty.

    M4 proved the refusal on the *load* path. Shadow registration is the first caller that
    could route around it by copying an unevaluated candidate forward under a new status, so
    the refusal is asserted here against the store rather than against the return value: a
    registration that raised after writing would still have left a shadow version behind.
    """

    store, evaluator, candidate = await setup_evaluator(tmp_path, labels={})
    with pytest.raises(RouterNotEvaluatedError) as refusal:
        await evaluator.register(
            candidate_version_id=candidate.contract_id,
            budget=BUDGET,
            principal=PRINCIPAL,
            workspace_id=FIXTURE_WORKSPACE_ID,
        )
    assert ACCEPTANCE_LABEL in str(refusal.value)
    versions = await store.list_router_model_versions(workspace_id=FIXTURE_WORKSPACE_ID)
    assert [version.contract_id for version in versions] == [candidate.contract_id]
    assert all(version.status is RouterStatus.CANDIDATE for version in versions)


async def test_a_candidate_without_a_calibration_report_cannot_be_given_a_shadow_stage(
    tmp_path: Path,
) -> None:
    """Half an evaluation is not an evaluation: the loader gates on both labels, so does this."""

    store, evaluator, candidate = await setup_evaluator(
        tmp_path, labels={ACCEPTANCE_LABEL: digest("holdout")}
    )
    with pytest.raises(RouterNotEvaluatedError) as refusal:
        await evaluator.register(
            candidate_version_id=candidate.contract_id,
            budget=BUDGET,
            principal=PRINCIPAL,
            workspace_id=FIXTURE_WORKSPACE_ID,
        )
    assert CALIBRATION_REPORT_LABEL in str(refusal.value)
    assert len(await store.list_router_model_versions(workspace_id=FIXTURE_WORKSPACE_ID)) == 1


async def test_the_registered_version_is_a_shadow_parented_on_the_candidate(
    tmp_path: Path,
) -> None:
    """A second row, in SHADOW, whose parent is the candidate and whose lineage it inherits.

    Read back from the store rather than asserted on the returned object, because a service
    that built a correct document and never persisted it would pass the weaker check.
    """

    store, evaluator, candidate = await setup_evaluator(tmp_path)
    registered = await evaluator.register(
        candidate_version_id=candidate.contract_id,
        budget=BUDGET,
        principal=PRINCIPAL,
        workspace_id=FIXTURE_WORKSPACE_ID,
    )

    stored = await store.get_router_model_version(registered.contract_id)
    assert stored is not None
    assert stored.status is RouterStatus.SHADOW
    assert stored.parent_version_id == candidate.contract_id
    assert stored.contract_id != candidate.contract_id
    assert stored.training_snapshot_id == candidate.training_snapshot_id
    assert stored.artifact_digest == candidate.artifact_digest
    assert stored.calibration_artifact_digest == candidate.calibration_artifact_digest
    assert stored.scope is candidate.scope

    reread = await store.get_router_model_version(candidate.contract_id)
    assert reread is not None
    assert reread.status is RouterStatus.CANDIDATE


async def test_the_shadow_version_carries_the_budget_and_the_candidates_evaluation_digests(
    tmp_path: Path,
) -> None:
    """The two labels the loader gates on are copied, and the budget is on the row itself.

    Copied and not referenced: ``require_evaluated`` reads labels off the version in hand, so
    a shadow row that only pointed at its parent would load unevaluated the first time
    anything read it directly.
    """

    store, evaluator, candidate = await setup_evaluator(tmp_path)
    registered = await evaluator.register(
        candidate_version_id=candidate.contract_id,
        budget=BUDGET,
        principal=PRINCIPAL,
        workspace_id=FIXTURE_WORKSPACE_ID,
    )

    stored = await store.get_router_model_version(registered.contract_id)
    assert stored is not None
    assert stored.labels[ACCEPTANCE_LABEL] == EVALUATED_LABELS[ACCEPTANCE_LABEL]
    assert stored.labels[CALIBRATION_REPORT_LABEL] == EVALUATED_LABELS[CALIBRATION_REPORT_LABEL]
    assert stored.labels[SHADOW_PARENT_LABEL] == candidate.contract_id
    assert ShadowBudget.from_labels(stored.labels) == BUDGET


async def test_registering_the_same_candidate_and_budget_twice_returns_the_first_version(
    tmp_path: Path,
) -> None:
    """A retry is free and leaves one row; a different budget is a different policy.

    The append-only store would refuse a second write under the same id, so an evaluator that
    minted a fresh id per call would either raise on retry or accumulate shadow versions
    nobody asked for. Both failures are invisible until an operator retries a timeout.
    """

    store, evaluator, candidate = await setup_evaluator(tmp_path)
    first = await evaluator.register(
        candidate_version_id=candidate.contract_id,
        budget=BUDGET,
        principal=PRINCIPAL,
        workspace_id=FIXTURE_WORKSPACE_ID,
    )
    again = await evaluator.register(
        candidate_version_id=candidate.contract_id,
        budget=BUDGET,
        principal=PRINCIPAL,
        workspace_id=FIXTURE_WORKSPACE_ID,
    )
    assert again.contract_id == first.contract_id

    wider = await evaluator.register(
        candidate_version_id=candidate.contract_id,
        budget=ShadowBudget(daily_cost_cap=99.0, max_trials_per_day=40),
        principal=PRINCIPAL,
        workspace_id=FIXTURE_WORKSPACE_ID,
    )
    assert wider.contract_id != first.contract_id

    versions = await store.list_router_model_versions(workspace_id=FIXTURE_WORKSPACE_ID)
    shadows = [version for version in versions if version.status is RouterStatus.SHADOW]
    assert sorted(version.contract_id for version in shadows) == sorted(
        [first.contract_id, wider.contract_id]
    )


async def test_a_candidate_in_another_workspace_is_absent_rather_than_forbidden(
    tmp_path: Path,
) -> None:
    """Naming a real id from a workspace the caller is not in must not confirm the id."""

    _, evaluator, candidate = await setup_evaluator(tmp_path)
    with pytest.raises(KeyError):
        await evaluator.register(
            candidate_version_id=candidate.contract_id,
            budget=BUDGET,
            principal=PRINCIPAL,
            workspace_id="wks_" + uuid4().hex[:22].upper(),
        )
    with pytest.raises(KeyError):
        await evaluator.register(
            candidate_version_id=new_id("router_model_version"),
            budget=BUDGET,
            principal=PRINCIPAL,
            workspace_id=FIXTURE_WORKSPACE_ID,
        )


async def test_a_version_that_is_not_a_candidate_cannot_be_given_a_shadow_stage(
    tmp_path: Path,
) -> None:
    """ACTIVE is the live policy and RETIRED is a rollback target; neither is a candidate.

    ``require_evaluated`` accepts all three statuses because all three may *route*. Shadowing
    is a different question, and letting an ACTIVE version into it would run the live policy
    against itself and call the tie evidence.
    """

    store, evaluator, _ = await setup_evaluator(tmp_path)
    active = await store.put_router_model_version(
        candidate_version(status=RouterStatus.ACTIVE.value)
    )
    with pytest.raises(ShadowRegistrationError) as refusal:
        await evaluator.register(
            candidate_version_id=active.contract_id,
            budget=BUDGET,
            principal=PRINCIPAL,
            workspace_id=FIXTURE_WORKSPACE_ID,
        )
    assert "ACTIVE" in str(refusal.value)
    versions = await store.list_router_model_versions(workspace_id=FIXTURE_WORKSPACE_ID)
    assert not [version for version in versions if version.status is RouterStatus.SHADOW]


async def test_registration_announces_the_shadow_stage_only_when_a_run_is_named(
    tmp_path: Path,
) -> None:
    """No run, no event — and with a run, one event naming both versions.

    ``AgentEvent`` requires a ``run_id`` and ``PostgresStore.append_event`` refuses one that
    names no run, so an evaluator that synthesised an id would work in memory and fail
    against the database.
    """

    store, evaluator, candidate = await setup_evaluator(tmp_path)
    silent = await evaluator.register(
        candidate_version_id=candidate.contract_id,
        budget=BUDGET,
        principal=PRINCIPAL,
        workspace_id=FIXTURE_WORKSPACE_ID,
    )
    assert silent is not None

    run = await seed_run(store)
    louder = await evaluator.register(
        candidate_version_id=candidate.contract_id,
        budget=ShadowBudget(daily_cost_cap=1.5, max_trials_per_day=3),
        principal=PRINCIPAL,
        workspace_id=FIXTURE_WORKSPACE_ID,
        run_id=run.run_id,
    )
    events = await store.list_events(run.run_id)
    assert [event.native_type for event in events] == ["router.shadow.registered"]
    assert events[0].normalized_type is EventType.ROUTER_CANDIDATE_TRAINED
    assert events[0].payload["shadow_version_id"] == louder.contract_id
    assert events[0].payload["candidate_version_id"] == candidate.contract_id


async def seed_run(store: MemoryStore) -> Run:
    """One persisted run, so the announcement has something real to attach to."""

    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=FIXTURE_PROJECT_ID,
            objective="Announce a shadow registration.",
        )
    )
    await store.create_task(task)
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=FIXTURE_PROJECT_ID,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
    )
    await store.create_run(run)
    return run


# --------------------------------------------------------------------------------------
# Recording a decision.
# --------------------------------------------------------------------------------------


async def test_a_shadow_decision_agrees_only_when_both_receipts_chose_the_same_configuration(
    tmp_path: Path,
) -> None:
    """Agreement is the two configuration hashes and nothing else.

    Read back from the store, because ``ShadowDecision`` re-derives the same comparison on the
    way in: asserting on the returned object alone could not tell a correct computation from
    a contract that quietly corrected a wrong one.
    """

    store, _, candidate = await setup_evaluator(tmp_path)
    chosen = digest("configuration-a")
    other = digest("configuration-b")

    same = await record_shadow_decision(
        store,
        executed_receipt=selecting_receipt(chosen),
        shadow_receipt=selecting_receipt(chosen),
        version_id=candidate.contract_id,
        notes="both arms selected the same configuration",
    )
    differ = await record_shadow_decision(
        store,
        executed_receipt=selecting_receipt(chosen),
        shadow_receipt=selecting_receipt(other),
        version_id=candidate.contract_id,
        notes="the shadow preferred a cheaper runtime",
    )

    stored_same = await store.get_shadow_decision(same.contract_id)
    stored_differ = await store.get_shadow_decision(differ.contract_id)
    assert stored_same is not None and stored_same.agreement is True
    assert stored_differ is not None and stored_differ.agreement is False
    assert stored_differ.executed_configuration_hash == chosen
    assert stored_differ.shadow_configuration_hash == other


async def test_two_decisions_that_selected_nothing_have_not_agreed(tmp_path: Path) -> None:
    """A deferral is not a choice, so two deferrals are not a matching choice."""

    store, _, candidate = await setup_evaluator(tmp_path)
    decision = await record_shadow_decision(
        store,
        executed_receipt=receipt(),
        shadow_receipt=receipt(),
        version_id=candidate.contract_id,
        notes="both arms deferred to a human",
    )
    stored = await store.get_shadow_decision(decision.contract_id)
    assert stored is not None
    assert stored.agreement is False
    assert stored.executed_configuration_hash is None
    assert stored.shadow_configuration_hash is None


async def test_recording_a_shadow_decision_dispatches_nothing_and_writes_one_record(
    tmp_path: Path,
) -> None:
    """The whole safety property of the stage: a shadow is a record, never an execution.

    Both receipts are read back unchanged and no second receipt, no run and no event appears.
    A future refactor that "helpfully" re-routed the executed node from here would break this
    and nothing else.
    """

    store, _, candidate = await setup_evaluator(tmp_path)
    executed = await store.put_routing_receipt(selecting_receipt(digest("executed")))
    shadow = selecting_receipt(digest("shadow"))

    decision = await record_shadow_decision(
        store,
        executed_receipt=executed,
        shadow_receipt=shadow,
        version_id=candidate.contract_id,
        notes="recorded without dispatch",
    )

    assert await store.get_routing_receipt(shadow.contract_id) is None
    assert await store.get_routing_receipt(executed.contract_id) == executed
    decisions = await store.list_shadow_decisions(workspace_id=FIXTURE_WORKSPACE_ID)
    assert [item.contract_id for item in decisions] == [decision.contract_id]
    assert decisions[0].shadow_router_version_id == candidate.contract_id


async def test_the_projected_delta_is_the_two_receipts_own_predictions(
    tmp_path: Path,
) -> None:
    """The projection is the difference of the two predicted run-verified success means.

    A receipt that predicted nothing projects zero rather than being skipped, so a shadow
    decision against a deferral still has a number and that number is not a guess.
    """

    store, _, candidate = await setup_evaluator(tmp_path)
    decision = await record_shadow_decision(
        store,
        executed_receipt=selecting_receipt(
            digest("executed"), predicted_outcomes=predicted(0.60)
        ),
        shadow_receipt=selecting_receipt(digest("shadow"), predicted_outcomes=predicted(0.72)),
        version_id=candidate.contract_id,
        notes="the shadow expects more",
    )
    stored = await store.get_shadow_decision(decision.contract_id)
    assert stored is not None
    assert stored.projected_utility_delta == pytest.approx(0.12)


def predicted(run_verified_success: float) -> dict[str, Any]:
    """A five-headed prediction whose run-verified success is the value under test."""

    def estimate(mean: float, spread: float) -> dict[str, Any]:
        return {
            "mean": mean,
            "lower_bound": mean - spread,
            "upper_bound": mean + spread,
            "confidence": 0.9,
            "method": "conformal-v1",
        }

    return {
        "quality": estimate(0.8, 0.05),
        "cost": estimate(2.0, 0.5),
        "latency": estimate(30_000.0, 5_000.0),
        "node_verified_success": estimate(0.85, 0.05),
        "run_verified_success": estimate(run_verified_success, 0.05),
    }


# --------------------------------------------------------------------------------------
# Pairing.
# --------------------------------------------------------------------------------------


def test_paired_deltas_joins_one_shadow_arm_to_the_control_arm_of_the_same_trial() -> None:
    """The delta is ``U(shadow) - U(control)`` for the pair, in decision-and-trial order."""

    decision_id = new_id("shadow_decision")
    other_id = new_id("shadow_decision")
    rows = [
        rollout(other_id, "CONTROL", 0, quality=0.5, cost=0.4),
        rollout(other_id, "SHADOW", 0, quality=0.9, cost=0.4),
        rollout(decision_id, "SHADOW", 1, quality=0.8, cost=0.2),
        rollout(decision_id, "CONTROL", 1, quality=0.6, cost=0.2),
    ]
    pairs = paired_deltas(rows, weights=WEIGHTS)

    assert [(pair.shadow_decision_id, pair.trial_index) for pair in pairs] == sorted(
        [(other_id, 0), (decision_id, 1)]
    )
    for pair in pairs:
        assert pair.delta == pytest.approx(pair.shadow_utility - pair.control_utility)
    assert sorted(pair.delta for pair in pairs) == [pytest.approx(0.2), pytest.approx(0.4)]


def test_a_shadow_arm_whose_control_fork_never_finished_produces_no_pair() -> None:
    """The drop is the claim. A lone arm measures the node, not the router.

    Removing the completeness filter makes this test fail with one pair where there should be
    none, which is exactly the bug it exists to catch: an unpaired shadow arm scored against
    anything at all reintroduces the per-node difficulty the branching removed.
    """

    lonely = new_id("shadow_decision")
    complete = new_id("shadow_decision")
    rows = [
        rollout(lonely, "SHADOW", 0, quality=0.99, cost=0.0),
        rollout(complete, "SHADOW", 0, quality=0.7, cost=0.3),
        rollout(complete, "CONTROL", 0, quality=0.7, cost=0.3),
    ]
    pairs = paired_deltas(rows, weights=WEIGHTS)
    assert [pair.shadow_decision_id for pair in pairs] == [complete]


def test_pairing_never_crosses_two_trials_of_one_decision() -> None:
    """The trial index is part of the join key, not a detail carried along beside it."""

    decision_id = new_id("shadow_decision")
    rows = [
        rollout(decision_id, "SHADOW", 0, quality=0.9, cost=0.1),
        rollout(decision_id, "CONTROL", 3, quality=0.2, cost=0.9),
    ]
    assert paired_deltas(rows, weights=WEIGHTS) == []


def test_a_duplicated_arm_is_dropped_rather_than_chosen_between() -> None:
    """Two rows claiming to be the same fork cannot both be it, and picking one picks by luck."""

    decision_id = new_id("shadow_decision")
    rows = [
        rollout(decision_id, "SHADOW", 0, quality=0.9, cost=0.1),
        rollout(decision_id, "SHADOW", 0, quality=0.1, cost=0.9),
        rollout(decision_id, "CONTROL", 0, quality=0.5, cost=0.5),
    ]
    assert paired_deltas(rows, weights=WEIGHTS) == []


def test_a_later_fork_is_priced_by_the_latency_budget_it_is_normalised_against() -> None:
    """``latency_budget_ms`` is a real input: the same rows score differently under two of them."""

    decision_id = new_id("shadow_decision")
    rows = [
        rollout(decision_id, "SHADOW", 0, quality=0.8, cost=0.2, latency_ms=40_000.0),
        rollout(decision_id, "CONTROL", 0, quality=0.8, cost=0.2, latency_ms=10_000.0),
    ]
    generous = paired_deltas(rows, weights=WEIGHTS, latency_budget_ms=100_000.0)
    tight = paired_deltas(rows, weights=WEIGHTS, latency_budget_ms=50_000.0)
    assert generous[0].delta < 0
    assert tight[0].delta < generous[0].delta


# --------------------------------------------------------------------------------------
# The report and the gate.
# --------------------------------------------------------------------------------------


def paired_corpus(
    version_id: str, deltas: list[float], *, trials: int = 1
) -> tuple[list[ShadowDecision], list[ShadowRolloutResult]]:
    """One decision per requested delta, each branched ``trials`` times at that difference.

    The decision ids are *derived* rather than minted. ``hierarchical_bootstrap`` sorts its
    group keys before sampling, so a corpus built from fresh random ids would resample the
    same numbers in a different order and produce a different lower bound on every run —
    which would make the seeded-determinism claim below true within one process and
    unreproducible between two.
    """

    decisions: list[ShadowDecision] = []
    results: list[ShadowRolloutResult] = []
    for index, delta in enumerate(deltas):
        decision = shadow_decision_record(
            version_id,
            contract_id=derived_id("shadow_decision", "m6-report-corpus", str(index)),
        )
        decisions.append(decision)
        for trial in range(trials):
            results.append(
                rollout(decision.contract_id, "CONTROL", trial, quality=0.5, cost=0.0)
            )
            results.append(
                rollout(
                    decision.contract_id, "SHADOW", trial, quality=0.5 + delta, cost=0.0
                )
            )
    return decisions, results


def test_the_reports_lower_bound_is_the_same_number_on_every_run_of_one_seed() -> None:
    """A seeded bootstrap is a function of its inputs, or the number it prints is decoration."""

    version_id = new_id("router_model_version")
    decisions, results = paired_corpus(version_id, [0.05, 0.10, 0.02, 0.08, 0.06, 0.04])
    config = ShadowReportConfig(seed=20260906, bootstraps=200)

    first = shadow_report(results, decisions, weights=WEIGHTS, config=config)
    second = shadow_report(
        list(reversed(results)), list(reversed(decisions)), weights=WEIGHTS, config=config
    )
    assert first.delta_lcb == second.delta_lcb
    assert first.mean_delta == second.mean_delta
    assert first.pairs == second.pairs

    # The seed is consumed rather than decorative: the mean is a fixed function of the rows,
    # the lower bound is not. Several seeds rather than one because two percentile bounds
    # over the same rows can coincide by chance, and a claim that failed one run in twenty
    # would be a flake rather than a proof.
    bounds = set()
    for seed in (1, 2, 3, 7, 11, 13, 17, 19):
        other = shadow_report(
            results,
            decisions,
            weights=WEIGHTS,
            config=ShadowReportConfig(seed=seed, bootstraps=200),
        )
        assert other.mean_delta == first.mean_delta
        bounds.add(other.delta_lcb)
    assert len(bounds) > 1


def test_the_report_counts_complete_pairs_and_the_agreement_rate_over_decisions() -> None:
    """``paired_count`` is trials, ``agreement_rate`` is decisions; they count different things."""

    version_id = new_id("router_model_version")
    decisions, results = paired_corpus(version_id, [0.05, 0.05], trials=3)
    disagreeing = shadow_decision_record(version_id, agreement=False)
    decisions.append(disagreeing)

    report = shadow_report(
        results, decisions, weights=WEIGHTS, config=ShadowReportConfig(seed=11, bootstraps=200)
    )
    assert report.version_id == version_id
    assert report.paired_count == 6
    assert report.agreement_rate == pytest.approx(0.0)
    assert len(report.pairs) == 7


def test_a_decision_whose_forks_never_completed_is_listed_with_no_observed_delta() -> None:
    """The M9b UI narrows the report to one run; a shadowed run with no pair must still show.

    Filtering incomplete decisions out would make "every fork failed" indistinguishable from
    "this run was never shadowed", which is the difference an operator is looking for.
    """

    version_id = new_id("router_model_version")
    decisions, results = paired_corpus(version_id, [0.05])
    orphan = shadow_decision_record(version_id)
    decisions.append(orphan)

    report = shadow_report(
        results, decisions, weights=WEIGHTS, config=ShadowReportConfig(seed=3, bootstraps=200)
    )
    empty = [pair for pair in report.pairs if pair.observed_delta is None]
    assert [pair.executed_receipt_id for pair in empty] == [orphan.executed_receipt_id]
    assert empty[0].shadow_result_id is None
    assert empty[0].control_result_id is None
    assert report.paired_count == 1


def test_a_report_with_no_complete_pair_is_not_non_inferior() -> None:
    """An empty sample is not a null result; zero measurements assert nothing."""

    version_id = new_id("router_model_version")
    decisions = [shadow_decision_record(version_id)]
    report = shadow_report(
        [], decisions, weights=WEIGHTS, config=ShadowReportConfig(seed=5, bootstraps=200)
    )
    assert report.paired_count == 0
    assert report.mean_delta == 0.0
    assert report.non_inferior is False
    assert not shadow_gate(report, min_paired_runs=1, delta_ni=-1.0)


def test_a_report_may_not_pool_two_shadow_versions() -> None:
    """One report describes one policy; averaging two is a verdict about neither."""

    decisions = [
        shadow_decision_record(new_id("router_model_version")),
        shadow_decision_record(new_id("router_model_version")),
    ]
    with pytest.raises(ValueError, match="one policy"):
        shadow_report(
            [], decisions, weights=WEIGHTS, config=ShadowReportConfig(seed=1, bootstraps=50)
        )
    with pytest.raises(ValueError, match="at least one shadow decision"):
        shadow_report(
            [], [], weights=WEIGHTS, config=ShadowReportConfig(seed=1, bootstraps=50)
        )


def test_the_shadow_gate_is_false_below_the_minimum_number_of_paired_runs() -> None:
    """Four excellent pairs are still four pairs; the count gate is not decorative."""

    version_id = new_id("router_model_version")
    decisions, results = paired_corpus(version_id, [0.20, 0.21, 0.19, 0.22])
    report = shadow_report(
        results, decisions, weights=WEIGHTS, config=ShadowReportConfig(seed=9, bootstraps=200)
    )
    assert report.delta_lcb > 0.0
    assert not shadow_gate(report, min_paired_runs=30, delta_ni=0.0)
    assert shadow_gate(report, min_paired_runs=4, delta_ni=0.0)


def test_the_shadow_gate_is_false_when_the_lower_bound_sits_under_the_floor() -> None:
    """A shadow that lost on every pair cannot clear a floor of zero, however many pairs it has."""

    version_id = new_id("router_model_version")
    decisions, results = paired_corpus(version_id, [-0.10] * 8)
    report = shadow_report(
        results, decisions, weights=WEIGHTS, config=ShadowReportConfig(seed=13, bootstraps=200)
    )
    assert report.mean_delta < 0
    assert report.delta_lcb < 0.0
    assert not shadow_gate(report, min_paired_runs=4, delta_ni=0.0)
    assert shadow_gate(report, min_paired_runs=4, delta_ni=-0.5)


def test_the_reports_remaining_gates_name_both_conditions_and_their_evidence() -> None:
    """M8.2 quotes these strings into a refusal, so they carry the numbers that decided it."""

    version_id = new_id("router_model_version")
    decisions, results = paired_corpus(version_id, [0.05, 0.06])
    report = shadow_report(
        results,
        decisions,
        weights=WEIGHTS,
        config=ShadowReportConfig(seed=17, bootstraps=200, min_paired_runs=30),
    )
    gates = {gate.gate: gate for gate in report.remaining_gates}
    assert set(gates) == {PAIRED_RUNS_GATE, NON_INFERIORITY_GATE}
    assert gates[PAIRED_RUNS_GATE].met is False
    assert "2 complete pairs of the 30 required" in gates[PAIRED_RUNS_GATE].evidence
    assert gates[NON_INFERIORITY_GATE].met is report.non_inferior
    assert str(report.delta_lcb) in gates[NON_INFERIORITY_GATE].evidence


def test_the_report_is_immutable_once_built() -> None:
    """M6.2 returns it and M8.2 gates on it; a consumer that could edit it could edit evidence."""

    version_id = new_id("router_model_version")
    decisions, results = paired_corpus(version_id, [0.05, 0.06])
    report = shadow_report(
        results, decisions, weights=WEIGHTS, config=ShadowReportConfig(seed=19, bootstraps=50)
    )
    with pytest.raises(ValueError):
        report.delta_lcb = 1.0


# --------------------------------------------------------------------------------------
# The route.
# --------------------------------------------------------------------------------------


async def setup_route(
    tmp_path: Path,
) -> tuple[MemoryStore, RouterModelVersion, Principal, Principal, Principal]:
    """A workspace with an owner, a plain member and an outsider, plus one candidate.

    All three are real ``principals`` rows with real memberships (or, for the outsider,
    deliberately none), so both refusals come from the membership lookup production uses.
    """

    store, _, candidate = await setup_evaluator(tmp_path)
    suffix = uuid4().hex[:8]
    owner = Principal(
        principal_id=f"usr_owner_{suffix}", issuer="test", subject=f"owner-{suffix}"
    )
    member = Principal(
        principal_id=f"usr_member_{suffix}", issuer="test", subject=f"member-{suffix}"
    )
    outsider = Principal(
        principal_id=f"usr_outsider_{suffix}", issuer="test", subject=f"outsider-{suffix}"
    )
    for principal in (owner, member, outsider):
        await store.upsert_principal(principal)
    await store.upsert_workspace(
        WorkspaceEntity(workspace_id=FIXTURE_WORKSPACE_ID, name="v0.4 M6")
    )
    for principal, role in ((owner, WorkspaceRole.OWNER), (member, WorkspaceRole.DEVELOPER)):
        await store.upsert_workspace_membership(
            WorkspaceMembership(
                membership_id=new_id("workspace_membership"),
                workspace_id=FIXTURE_WORKSPACE_ID,
                principal_id=principal.principal_id,
                role=role,
            )
        )
    return store, candidate, owner, member, outsider


def install_app_state(store: MemoryStore, artifacts: Path, who: Principal) -> None:
    """Wire ``app`` the way the lifespan does, for one principal."""

    app.state.manager = type("Manager", (), {"store": store})()
    app.state.shadow = ShadowEvaluator(store, ArtifactStore(artifacts))
    app.state.auth = AuthRuntime(
        mode="LOCAL_PRINCIPAL",
        identity=IdentityService(store),
        cookie_name="session",
        cookie_secure=False,
        session_ttl_seconds=3600,
        local_principal_cache=who,
    )


def clear_app_state() -> None:
    for attribute in ("auth", "shadow", "manager"):
        if hasattr(app.state, attribute):
            delattr(app.state, attribute)


async def call(method: str, url: str, **kwargs: Any) -> Any:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    async with client:
        return await client.request(method, url, **kwargs)


def policy_body(candidate: RouterModelVersion, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "workspace_id": FIXTURE_WORKSPACE_ID,
        "candidate_version_id": candidate.contract_id,
        "daily_cost_cap": 12.5,
        "max_trials_per_day": 40,
    }
    body.update(overrides)
    return body


async def test_registering_a_shadow_policy_over_http_writes_the_version_it_returns(
    tmp_path: Path,
) -> None:
    """The 201 body is the stored SHADOW version, read back from the store to prove it."""

    store, candidate, owner, _, _ = await setup_route(tmp_path)
    install_app_state(store, tmp_path / "route-artifacts", owner)
    try:
        response = await call(
            "POST",
            SHADOW_POLICIES_PATH,
            json=policy_body(candidate),
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "SHADOW"
        assert body["parent_version_id"] == candidate.contract_id

        stored = await store.get_router_model_version(body["contract_id"])
        assert stored is not None
        assert stored.status is RouterStatus.SHADOW
        assert ShadowBudget.from_labels(stored.labels) == BUDGET
    finally:
        clear_app_state()


async def test_replaying_a_registration_under_a_second_key_returns_the_same_version(
    tmp_path: Path,
) -> None:
    """A retried timeout must not leave two shadow policies where the operator sees one."""

    store, candidate, owner, _, _ = await setup_route(tmp_path)
    install_app_state(store, tmp_path / "route-artifacts", owner)
    try:
        first = await call(
            "POST",
            SHADOW_POLICIES_PATH,
            json=policy_body(candidate),
            headers={"Idempotency-Key": "idem-first"},
        )
        second = await call(
            "POST",
            SHADOW_POLICIES_PATH,
            json=policy_body(candidate),
            headers={"Idempotency-Key": "idem-second"},
        )
        assert first.status_code == second.status_code == 201
        assert first.json()["contract_id"] == second.json()["contract_id"]

        versions = await store.list_router_model_versions(workspace_id=FIXTURE_WORKSPACE_ID)
        assert len([v for v in versions if v.status is RouterStatus.SHADOW]) == 1
    finally:
        clear_app_state()


async def test_registering_a_shadow_policy_without_an_idempotency_key_is_refused(
    tmp_path: Path,
) -> None:
    """SDD §11's rule for every mutating endpoint, with nothing written on the way out."""

    store, candidate, owner, _, _ = await setup_route(tmp_path)
    install_app_state(store, tmp_path / "route-artifacts", owner)
    try:
        response = await call("POST", SHADOW_POLICIES_PATH, json=policy_body(candidate))
        assert response.status_code == 400
        assert response.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"
        versions = await store.list_router_model_versions(workspace_id=FIXTURE_WORKSPACE_ID)
        assert not [v for v in versions if v.status is RouterStatus.SHADOW]
    finally:
        clear_app_state()


@pytest.mark.parametrize("role", ["member", "outsider"])
async def test_only_a_workspace_administrator_may_register_a_shadow_policy(
    tmp_path: Path, role: str
) -> None:
    """A shadow policy spends the workspace's budget, so membership alone is not enough."""

    store, candidate, _, member, outsider = await setup_route(tmp_path)
    install_app_state(
        store, tmp_path / "route-artifacts", member if role == "member" else outsider
    )
    try:
        response = await call(
            "POST",
            SHADOW_POLICIES_PATH,
            json=policy_body(candidate),
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
        )
        assert response.status_code == 403
        assert response.json()["code"] == "FORBIDDEN"
        versions = await store.list_router_model_versions(workspace_id=FIXTURE_WORKSPACE_ID)
        assert not [v for v in versions if v.status is RouterStatus.SHADOW]
    finally:
        clear_app_state()


async def test_an_unevaluated_candidate_is_refused_over_http_with_the_trainers_wording(
    tmp_path: Path,
) -> None:
    """AC4-M4-016 reaching the wire on the shadow route, with the same code M4 publishes."""

    store, _, owner, _, _ = await setup_route(tmp_path)
    bare = await store.put_router_model_version(candidate_version(labels={}))
    install_app_state(store, tmp_path / "route-artifacts", owner)
    try:
        response = await call(
            "POST",
            SHADOW_POLICIES_PATH,
            json=policy_body(bare),
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "ROUTER_NOT_EVALUATED"
    finally:
        clear_app_state()


async def test_an_already_promoted_version_is_refused_with_a_shadow_specific_code(
    tmp_path: Path,
) -> None:
    """A client branching on the envelope can tell "unevaluated" from "wrong lifecycle"."""

    store, _, owner, _, _ = await setup_route(tmp_path)
    active = await store.put_router_model_version(
        candidate_version(status=RouterStatus.ACTIVE.value)
    )
    install_app_state(store, tmp_path / "route-artifacts", owner)
    try:
        response = await call(
            "POST",
            SHADOW_POLICIES_PATH,
            json=policy_body(active),
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "SHADOW_CANDIDATE_NOT_REGISTRABLE"
    finally:
        clear_app_state()


async def test_the_route_reports_the_service_unavailable_rather_than_failing_obscurely(
    tmp_path: Path,
) -> None:
    """The ``NODE_ROUTING_UNAVAILABLE`` shape from ``api/routing.py``, for the shadow service.

    An app assembled without the M6 lifespan line answers a typed 409 instead of raising an
    ``AttributeError`` that would surface as a 500 with no code to branch on.
    """

    store, candidate, owner, _, _ = await setup_route(tmp_path)
    install_app_state(store, tmp_path / "route-artifacts", owner)
    app.state.shadow = None
    try:
        response = await call(
            "POST",
            SHADOW_POLICIES_PATH,
            json=policy_body(candidate),
            headers={"Idempotency-Key": f"idem-{uuid4().hex[:8]}"},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "SHADOW_EVALUATION_UNAVAILABLE"
    finally:
        clear_app_state()


def test_the_shadow_route_is_published_and_is_not_exempt_from_authentication() -> None:
    """The generated TypeScript client is built from this document, so the path must be in it."""

    from accretion.api.auth import is_exempt

    document = app.openapi()
    assert SHADOW_POLICIES_PATH in document["paths"]
    post = document["paths"][SHADOW_POLICIES_PATH]["post"]
    assert "201" in post["responses"]
    assert (
        post["responses"]["201"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/RouterModelVersion"
    )
    assert any(
        parameter["name"] == "Idempotency-Key" for parameter in post.get("parameters", [])
    )
    assert not is_exempt(SHADOW_POLICIES_PATH)
