"""M6.2's two stage collaborators: what they record, when they fork, and when they refuse.

The claims here are about the *gates*, because the gates are what stop a shadow stage from being
a second production execution nobody authorised. A fork is taken only of a ``LOW_DIGITAL`` node
in a branchable workspace, only inside the sampled fraction, and only while the policy has budget
left; a fork that explodes is a log line and never a failed run; and both arms of a trial are
recorded under one index and one seed or they are not a pair at all.

The inputs are the *production* ones. ``test_v04_m6_e2e`` attaches a recording post-node hook
beside the real executor, so the frozen node, the committed receipt, the claimed configuration
and the live lease that reach the assertions below are the objects the scheduler actually built.
A test that assembled its own ``FrozenNode`` would be checking the gates against whatever this
file imagines a frozen node looks like.

Two graphs are executed for this module and cached, because each is a real run over a real git
worktree: a pristine one every read-only claim shares, and a sacrificial one for the budget claim,
which has to spend a policy's whole day and cannot then hand the store back.

There is no ``conftest.py`` in this repository and none is added here.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from test_v04_m6_e2e import RunOutcome, run_graph

from accretion.contracts import WorkspaceLease
from accretion.contracts.routing import (
    NodeContract,
    RiskClass,
    ShadowRolloutKind,
    ShadowRolloutResult,
)
from accretion.ids import derived_id, new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.bootstrap import build_node_routing
from accretion.routing.protocols import RoutingMode
from accretion.routing.rollout import (
    BUDGET_EXHAUSTED,
    NOT_FORK_ELIGIBLE,
    NOT_SAMPLED,
    SHADOW_ROUTING_STATUS,
    BranchedRolloutExecutor,
    ShadowRoutingHook,
)
from accretion.routing.settlement import ExplorationSettlement
from accretion.workspace import WorkspaceError

_PRISTINE: RunOutcome | None = None
_SACRIFICIAL: RunOutcome | None = None


async def pristine_once(tmp_path_factory: pytest.TempPathFactory) -> RunOutcome:
    """One shadowed run every read-only claim shares."""

    global _PRISTINE
    if _PRISTINE is None:
        _PRISTINE = await run_graph(
            tmp_path_factory.mktemp("m6-rollout-pristine") / "shadow", RoutingMode.SHADOW
        )
    return _PRISTINE


async def sacrificial_once(tmp_path_factory: pytest.TempPathFactory) -> RunOutcome:
    """A second shadowed run for the one claim that spends a policy's budget permanently."""

    global _SACRIFICIAL
    if _SACRIFICIAL is None:
        _SACRIFICIAL = await run_graph(
            tmp_path_factory.mktemp("m6-rollout-spent") / "shadow", RoutingMode.SHADOW
        )
    return _SACRIFICIAL


def hook_call(outcome: RunOutcome) -> dict[str, Any]:
    """The arguments the scheduler handed the post-node stage for the last routed node."""

    assert outcome.hook_calls, "the shadowed run dispatched no routed AGENT or TOOL node"
    return dict(outcome.hook_calls[-1])


async def rollout_count(store: MemoryStore, workspace_id: str) -> int:
    return len(await store.list_shadow_rollout_results(workspace_id=workspace_id))


async def skip_reasons(outcome: RunOutcome) -> list[str]:
    return [
        str(event.payload["reason"])
        for event in await outcome.store.list_events(outcome.run_id)
        if event.native_type == "router.shadow.rollout-skipped"
    ]


# --------------------------------------------------------------------------------------
# What a completed pair looks like.
# --------------------------------------------------------------------------------------


async def test_both_arms_of_one_trial_are_recorded_under_one_index_and_one_seed(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A pair is a pair: same decision, same trial index, same seed, two different kinds.

    ``paired_deltas`` joins on ``(shadow_decision_id, trial_index)`` and drops any group that is
    not exactly one SHADOW row and one CONTROL row, so an executor that numbered its arms
    independently would produce rows that look complete and pair with nothing.
    """

    outcome = await pristine_once(tmp_path_factory)
    rows = await outcome.store.list_shadow_rollout_results(workspace_id=outcome.workspace_id)
    assert rows
    by_decision: dict[str, list[ShadowRolloutResult]] = {}
    for row in rows:
        by_decision.setdefault(row.shadow_decision_id, []).append(row)
    for arms in by_decision.values():
        assert {arm.kind for arm in arms} == {ShadowRolloutKind.SHADOW, ShadowRolloutKind.CONTROL}
        assert len({arm.trial_index for arm in arms}) == 1
        assert len({arm.seed for arm in arms}) == 1
        assert len({arm.fork_execution_id for arm in arms}) == len(arms)


async def test_each_arm_records_the_configuration_that_ran_in_it_and_not_the_recommendation(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The SHADOW arm carries the shadow receipt's choice; the CONTROL arm the executed one.

    ``ShadowRolloutResult.configuration_hash`` is documented as the configuration *executed in
    this fork*, and on the CONTROL arm that is deliberately not what the router recommended.
    Recording the recommendation on both arms would misattribute half of every measurement.
    """

    outcome = await pristine_once(tmp_path_factory)
    receipts = {
        receipt.contract_id: receipt
        for receipt in await outcome.store.list_routing_receipts(
            workspace_id=outcome.workspace_id
        )
    }
    decisions = {
        item.contract_id: item
        for item in await outcome.store.list_shadow_decisions(workspace_id=outcome.workspace_id)
    }
    rows = await outcome.store.list_shadow_rollout_results(workspace_id=outcome.workspace_id)
    assert rows
    for row in rows:
        decision = decisions[row.shadow_decision_id]
        expected = (
            receipts[decision.shadow_receipt_id]
            if row.kind is ShadowRolloutKind.SHADOW
            else receipts[decision.executed_receipt_id]
        )
        assert row.configuration_hash == expected.selected_configuration_hash
        assert row.serving.model_id
        assert row.budget_consumed >= 0
        assert row.observed.verified is False
        assert row.verification_result_id is None


async def test_the_shadow_receipt_the_arm_ran_was_never_the_head_of_its_node(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The shadow receipt is real, is under its own request id, and is superseded by itself.

    ``latest_receipt`` returns the newest receipt no other receipt supersedes. Two heads for one
    node would make the decision a resumed run claims a coin flip, and the losing side of that
    flip is a configuration nobody approved for execution.
    """

    outcome = await pristine_once(tmp_path_factory)
    receipts = await outcome.store.list_routing_receipts(workspace_id=outcome.workspace_id)
    shadows = [
        receipt
        for receipt in receipts
        if receipt.labels.get("routing_status") == SHADOW_ROUTING_STATUS
    ]
    assert shadows
    for receipt in shadows:
        assert receipt.supersedes_contract_id == receipt.contract_id
        assert receipt.labels["shadow_of"] != receipt.routing_request_id
        assert receipt.workspace_router_version.startswith("rmv_")


# --------------------------------------------------------------------------------------
# The gates.
# --------------------------------------------------------------------------------------


async def test_a_receipt_outside_the_fork_fraction_is_never_forked(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """``fork_fraction=0`` takes no fork at all, and says so rather than failing quietly."""

    outcome = await pristine_once(tmp_path_factory)
    before = await rollout_count(outcome.store, outcome.workspace_id)
    executor = BranchedRolloutExecutor(outcome.manager, fork_fraction=0.0)
    await executor.after_node(**hook_call(outcome))
    assert await rollout_count(outcome.store, outcome.workspace_id) == before
    assert NOT_SAMPLED in await skip_reasons(outcome)


def test_the_sampling_decision_is_a_pure_function_of_the_receipt_id(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Sampled from a digest and never from a random source, so a replay forks the same nodes."""

    class _Manager:
        store = MemoryStore()

    always = BranchedRolloutExecutor(_Manager(), fork_fraction=1.0)  # type: ignore[arg-type]
    never = BranchedRolloutExecutor(_Manager(), fork_fraction=0.0)  # type: ignore[arg-type]
    half = BranchedRolloutExecutor(_Manager(), fork_fraction=0.5)  # type: ignore[arg-type]
    ids = [new_id("routing_receipt") for _ in range(200)]
    assert all(always.samples(item) for item in ids)
    assert not any(never.samples(item) for item in ids)
    assert [half.samples(item) for item in ids] == [half.samples(item) for item in ids]
    assert 0 < sum(half.samples(item) for item in ids) < len(ids)


def test_a_fork_fraction_outside_zero_to_one_is_refused_at_construction() -> None:
    """A share of nodes, not a multiplier: a value outside [0, 1] is a wiring mistake."""

    class _Manager:
        store = MemoryStore()

    with pytest.raises(ValueError) as refusal:
        BranchedRolloutExecutor(_Manager(), fork_fraction=1.5)  # type: ignore[arg-type]
    assert "fork_fraction" in str(refusal.value)


async def test_a_node_above_low_digital_risk_is_never_forked(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """ADR-060's safety precondition: a second unasked-for execution needs the lowest risk class.

    Every higher class either reaches outside the workspace or spent a human approval that was
    given for one execution, and a fork would spend it twice.
    """

    outcome = await pristine_once(tmp_path_factory)
    call = hook_call(outcome)
    frozen = call["frozen"]
    payload = frozen.node_contract.model_dump(mode="python")
    payload.update(
        allowed_risk_class=RiskClass.HIGH_DIGITAL, content_hash="", immutable_hash=""
    )
    call["frozen"] = replace(frozen, node_contract=NodeContract.model_validate(payload))

    before = await rollout_count(outcome.store, outcome.workspace_id)
    await BranchedRolloutExecutor(outcome.manager, fork_fraction=1.0).after_node(**call)
    assert await rollout_count(outcome.store, outcome.workspace_id) == before
    assert NOT_FORK_ELIGIBLE in await skip_reasons(outcome)


async def test_a_node_that_is_not_workspace_isolated_is_never_forked(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The other half of the precondition: a fork is only a sandbox if the workspace branches."""

    outcome = await pristine_once(tmp_path_factory)
    call = hook_call(outcome)
    configuration = call["configuration"]
    payload = configuration.model_dump(mode="python")
    payload["environment"]["workspace_isolation"] = "SHARED"
    payload.update(content_hash="", configuration_hash="")
    call["configuration"] = type(configuration).model_validate(payload)

    before = await rollout_count(outcome.store, outcome.workspace_id)
    await BranchedRolloutExecutor(outcome.manager, fork_fraction=1.0).after_node(**call)
    assert await rollout_count(outcome.store, outcome.workspace_id) == before


async def test_a_node_with_no_lease_is_never_forked(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """There is nothing to branch from without a lease, and inventing a base revision is worse."""

    outcome = await pristine_once(tmp_path_factory)
    call = hook_call(outcome)
    call["lease"] = None
    before = await rollout_count(outcome.store, outcome.workspace_id)
    await BranchedRolloutExecutor(outcome.manager, fork_fraction=1.0).after_node(**call)
    assert await rollout_count(outcome.store, outcome.workspace_id) == before


async def test_a_policy_that_has_spent_its_day_is_refused_with_a_budget_exhausted_note(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """ADR-060 pays for evidence, so the stage stops when the workspace's day is spent.

    The budget is charged against ``completed_at``'s UTC day, so the row written below is dated
    today on purpose: a policy whose spending was counted against the header's ``created_at``
    would be refused for forks it took yesterday.
    """

    outcome = await sacrificial_once(tmp_path_factory)
    decisions = await outcome.store.list_shadow_decisions(workspace_id=outcome.workspace_id)
    assert decisions
    spender = decisions[0]
    now = datetime.now(UTC)
    await outcome.store.put_shadow_rollout_result(
        ShadowRolloutResult.model_validate(
            {
                "contract_id": derived_id("shadow_rollout_result", spender.contract_id, "SPENT"),
                "created_at": now,
                "created_by": spender.created_by,
                "workspace_id": spender.workspace_id,
                "project_id": spender.project_id,
                "shadow_decision_id": spender.contract_id,
                "kind": ShadowRolloutKind.SHADOW,
                "fork_execution_id": new_id("execution_instance"),
                "configuration_hash": "f" * 64,
                "serving": {
                    "provider": "FAKE",
                    "runtime_version": "fake-p2-v1",
                    "model_id": "fake-model",
                },
                "observed": {
                    "quality": 0.0,
                    "cost": 0.0,
                    "latency_ms": 0.0,
                    "verified": False,
                },
                "budget_consumed": 999.0,
                "trial_index": 99,
                "seed": 1,
                "completed_at": now,
            }
        )
    )

    before = await rollout_count(outcome.store, outcome.workspace_id)
    await BranchedRolloutExecutor(outcome.manager, fork_fraction=1.0).after_node(
        **hook_call(outcome)
    )
    assert await rollout_count(outcome.store, outcome.workspace_id) == before
    assert BUDGET_EXHAUSTED in await skip_reasons(outcome)


class ExplodingWorktrees:
    """A hand-written worktree manager that refuses to acquire, with a call counter."""

    def __init__(self, real: Any) -> None:
        self.real = real
        self.attempts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real, name)

    async def acquire_candidate(self, **_kwargs: Any) -> WorkspaceLease:
        self.attempts += 1
        raise WorkspaceError("injected fork failure")


async def test_a_fork_that_cannot_be_taken_never_reaches_the_run(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A rollout is bought with a whole second execution, and buying it can fail.

    The node it is about has already finished and been reported, so a fork that raised must be a
    log line: no rollout row, no change to the run, and no exception out of ``after_node``.
    """

    outcome = await pristine_once(tmp_path_factory)
    before = await rollout_count(outcome.store, outcome.workspace_id)
    state_before = (await outcome.store.get_run(outcome.run_id)).state  # type: ignore[union-attr]
    exploding = ExplodingWorktrees(outcome.manager.worktrees)
    outcome.manager.worktrees = exploding
    try:
        await BranchedRolloutExecutor(outcome.manager, fork_fraction=1.0).after_node(
            **hook_call(outcome)
        )
    finally:
        outcome.manager.worktrees = exploding.real
    assert exploding.attempts == 1
    assert await rollout_count(outcome.store, outcome.workspace_id) == before
    assert (await outcome.store.get_run(outcome.run_id)).state is state_before  # type: ignore[union-attr]


# --------------------------------------------------------------------------------------
# Assembly.
# --------------------------------------------------------------------------------------


async def test_a_workspace_with_no_registered_shadow_stage_has_nothing_to_shadow() -> None:
    """No SHADOW version means no recommendation, and an absent stage is not an error."""

    store = MemoryStore()
    hook = ShadowRoutingHook(store, ArtifactStore(Path("/tmp/accretion-m6-rollout-artifacts")))
    assert await hook.shadow_version(new_id("workspace_entity")) is None


async def test_the_two_stages_are_attached_together_or_not_at_all(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A deployment either runs the whole shadow stage or none of it.

    Attaching the recorder without the executor would write decisions nothing ever measures;
    attaching the executor without the recorder would look for a decision nothing ever wrote.
    ``BASELINE_ONLY`` attaches neither, which is what makes "shadow evaluation is off" a
    structural fact rather than a branch inside a hook.

    M7 attaches a second post-node hook under ``AUTO`` — the exploration settlement, which is
    half of its own pair — so the M6 claim is stated as "the recorder is the only post-route
    hook, and the executor is a post-node hook in both learned modes" rather than as an exact
    list that any later milestone adding a hook would have to edit. The exact list is still
    asserted per mode below, so a stage attached in the *wrong* mode is still a failure here:
    ``SHADOW`` gets the M6 executor alone, and gaining a settlement there would mean crediting
    a budget for explorations a shadow stage never took.
    """

    outcome = await pristine_once(tmp_path_factory)
    artifacts = ArtifactStore(tmp_path_factory.mktemp("m6-rollout-assembly"))
    off = build_node_routing(
        outcome.manager,
        policy_id="local-capability-policy",
        granted_permissions=set(),
        mode=RoutingMode.BASELINE_ONLY,
        artifacts=artifacts,
    )
    assert off.post_route == () and off.post_node == ()
    expected_post_node = {
        RoutingMode.SHADOW: [BranchedRolloutExecutor],
        RoutingMode.AUTO: [BranchedRolloutExecutor, ExplorationSettlement],
    }
    for mode in (RoutingMode.SHADOW, RoutingMode.AUTO):
        on = build_node_routing(
            outcome.manager,
            policy_id="local-capability-policy",
            granted_permissions=set(),
            mode=mode,
            artifacts=artifacts,
        )
        assert [type(hook) for hook in on.post_route] == [ShadowRoutingHook]
        assert BranchedRolloutExecutor in [type(hook) for hook in on.post_node]
        assert [type(hook) for hook in on.post_node] == expected_post_node[mode]
        assert on.default_mode is mode
