"""AC4-M6-017: a shadow stage observes a run and never changes it.

Two real runs of the same seeded graph, over the same repository fixture and the same scripted
runtime, differing in exactly one thing: one routes under ``SHADOW`` with a registered shadow
policy and the other under ``BASELINE_ONLY``. Four things are then compared, and each of them is
a different way for a shadow to leak into production:

* the ordered ``(node_key, node_contract_hash)`` of every ``RUNTIME_CALL_STARTED`` --- which
  nodes were dispatched, in which order, under which frozen contract. The receipt *ids*
  deliberately differ between the two runs, because §8.2 puts the routing mode inside the
  request id and a ``SHADOW`` decision and a ``BASELINE_ONLY`` decision over identical inputs
  are different records on purpose; what may not differ is which nodes ran;
* the node status map the scheduler ended on;
* the final ``RunState``;
* every diff the run captured, as ``(kind, sha256)`` --- the bytes the run produced.

The fourth is the one that catches a fork taken in the wrong place: a rollout that branched into
the run's own lease instead of a fresh sandbox would write the fork's output into the run's
workspace, and the digest would move.

The runtime here is a spy rather than a mock: it records every session it was asked to open and
every submission it received, so the test can say where each arm of each rollout actually ran.

``_routed_graph`` is imported from ``test_v04_m2_end_to_end`` and never edited: the fixture that
proves M2's receipt-first execution is the fixture a shadow stage must not disturb.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from test_v04_m2_end_to_end import _routed_graph

from accretion.contracts import (
    ApprovalDecisionValue,
    ApprovalStatus,
    EventType,
    PrincipalRef,
    PrincipalStatus,
    Provider,
    RunState,
    SessionConfig,
    SessionRef,
)
from accretion.contracts.routing import (
    RouterModelVersion,
    RouterScope,
    RouterStatus,
    RoutingContext,
    ShadowRolloutKind,
)
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.bootstrap import build_node_routing
from accretion.routing.identity import routing_request_id
from accretion.routing.protocols import RoutingMode
from accretion.routing.rollout import SHADOW_FORK_PREFIX, SHADOW_ROUTING_STATUS
from accretion.routing.shadow import ShadowBudget, ShadowEvaluator
from accretion.routing.snapshot import RoutingSnapshot
from accretion.routing.train import ACCEPTANCE_LABEL, CALIBRATION_REPORT_LABEL
from accretion.runtimes.fake import FakeCallOutcome, FakeRuntime

BUDGET = ShadowBudget(daily_cost_cap=12.5, max_trials_per_day=40)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _write_valid_output(session: SessionRef, _request: object) -> None:
    """Every call, in every workspace, writes the same bytes.

    One scripted behaviour rather than a queue, and that is load-bearing: a shared queue of
    outcomes would be drained by the rollout forks as well as by the run, so the run's *n*-th
    call would get a different outcome in the shadow arm than in the baseline arm and the two
    runs would diverge for a reason that has nothing to do with the router.
    """

    (session.workspace / "result.json").write_text('{"ok": true}\n')


@dataclass(slots=True)
class OpenedSession:
    session_id: str
    workspace: Path
    model: str | None


class ShadowSpyRuntime(FakeRuntime):
    """A FakeRuntime that remembers where and under what model every session was opened."""

    def __init__(self) -> None:
        super().__init__()
        self.opened: list[OpenedSession] = []
        self.submitted: list[str] = []

    async def create_session(self, config: SessionConfig) -> SessionRef:
        session = await super().create_session(config)
        self.opened.append(
            OpenedSession(
                session_id=session.session_id,
                workspace=config.workspace,
                model=config.model,
            )
        )
        return session

    async def submit(self, session, request):  # type: ignore[no-untyped-def]
        self.submitted.append(session.session_id)
        return await super().submit(session, request)

    def _next_outcome(self, session_id: str) -> FakeCallOutcome:
        return FakeCallOutcome(hook=_write_valid_output)


class PostNodeRecorder:
    """A post-node hook that records the arguments the scheduler handed it, and nothing else.

    Attached beside the real executor so that ``test_v04_m6_rollout`` can drive
    :class:`~accretion.routing.rollout.BranchedRolloutExecutor` with the *production* inputs --
    the frozen node, the committed receipt, the claimed configuration and the live lease -- rather
    than with a reconstruction of them assembled in a test.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def after_node(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


@dataclass(slots=True)
class RunOutcome:
    """Everything about a finished run that a shadow stage is forbidden to change."""

    store: MemoryStore
    runtime: ShadowSpyRuntime
    manager: Any
    workspace_id: str
    lease_path: Path
    state: RunState
    dispatched: list[tuple[str, str, str]]
    statuses: dict[str, str]
    workspace_digests: list[tuple[str, str]]
    run_id: str
    hook_calls: list[dict[str, Any]]


async def register_shadow_policy(
    store: MemoryStore, workspace_id: str, artifacts: Path
) -> RouterModelVersion:
    """One evaluated CANDIDATE promoted to a SHADOW stage through the production path.

    Registering through :class:`~accretion.routing.shadow.ShadowEvaluator` rather than writing a
    ``SHADOW`` row by hand is what keeps this test honest about what a shadow policy *is*: the
    budget labels, the parent link and the copied evaluation digests all come from the code that
    will write them in production.
    """

    candidate = await store.put_router_model_version(
        RouterModelVersion.model_validate(
            {
                "contract_id": new_id("router_model_version"),
                "created_by": PrincipalRef(
                    principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
                    status=PrincipalStatus.ACTIVE,
                ),
                "workspace_id": workspace_id,
                "scope": RouterScope.TEAM_WORKSPACE,
                "algorithm_id": "gradient-boosted-ranker",
                "feature_schema_version": "1.0.0",
                "training_snapshot_id": new_id("router_training_snapshot"),
                "artifact_digest": _digest("m6-e2e-artifact"),
                "calibration_artifact_digest": _digest("m6-e2e-calibration"),
                "status": RouterStatus.CANDIDATE,
                "labels": {
                    ACCEPTANCE_LABEL: _digest("m6-e2e-holdout"),
                    CALIBRATION_REPORT_LABEL: _digest("m6-e2e-calibration-report"),
                },
            }
        )
    )
    return await ShadowEvaluator(store, ArtifactStore(artifacts)).register(
        candidate_version_id=candidate.contract_id,
        budget=BUDGET,
        principal=candidate.created_by,
        workspace_id=workspace_id,
    )


async def low_risk_task(fixture: Any) -> str:
    """A second task on the fixture's project, at ``LOW`` risk rather than ``HIGH``.

    ADR-060 lets a fork be taken of ``LOW_DIGITAL`` nodes only, and
    ``NodeContractFreezer`` maps the task's ``risk_level`` straight onto the node's
    ``allowed_risk_class``. ``_routed_graph`` builds a ``HIGH`` task because M2's own witness is
    about approval gates, and it is imported here rather than edited, so the risk level this
    milestone needs is expressed as a second task on the same project instead.
    """

    seed = await fixture.store.get_task(fixture.task_id)
    assert seed is not None
    task = await fixture.manager.create_task(
        project_id=seed.envelope.project_id,
        objective="Apply an approved change and verify the result.",
        task_patch={
            "task_type": "IMPLEMENT",
            "risk_level": "LOW",
            "required_outputs": [{"path": "result.json", "kind": "json"}],
        },
    )
    return task.envelope.task_id


async def drive_to_completion(manager: Any, run_id: str, background: Any) -> None:
    """Approve whatever the graph asks for, until it finishes.

    A loop rather than two named waits: the template a ``LOW``-risk task plans is not
    necessarily the one a ``HIGH``-risk task plans, and a test that hard-coded the number of
    gates would be asserting the planner's shape rather than the router's behaviour.
    """

    while not background.done():
        for approval in await manager.store.list_approvals(run_id, ApprovalStatus.PENDING):
            await manager.resolve_approval(approval.approval_id, ApprovalDecisionValue.APPROVE)
        await asyncio.sleep(0.02)
    await asyncio.wait_for(background, 30)


async def run_graph(root: Path, mode: RoutingMode) -> RunOutcome:
    """Run the M2 routed graph to completion under ``mode`` and collect what it produced."""

    root.mkdir(parents=True, exist_ok=True)
    store = MemoryStore()
    fixture = await _routed_graph(
        root, store=store, runtime_factory=lambda _store, _workspace: ShadowSpyRuntime()
    )
    if mode is not RoutingMode.BASELINE_ONLY:
        await register_shadow_policy(store, fixture.workspace_id, root / "router-artifacts")
    service = build_node_routing(
        fixture.manager,
        policy_id="local-capability-policy",
        granted_permissions=set(),
        mode=mode,
        artifacts=ArtifactStore(root / "router-artifacts"),
    )
    recorder = PostNodeRecorder()
    service.post_node = (*service.post_node, recorder)
    fixture.manager.routing_service = service
    run = await fixture.manager.start_run(
        await low_risk_task(fixture), Provider.FAKE, principal_id=fixture.principal_id
    )
    background = fixture.manager.background[run.run_id]
    await drive_to_completion(fixture.manager, run.run_id, background)

    final = await store.get_run(run.run_id)
    assert final is not None
    events = await store.list_events(run.run_id)
    # The node *key*, not the node id or the contract hash: both of those derive from the
    # run id, which is minted fresh for every run, so two runs of one template can never agree
    # on them and a comparison of them would be a comparison of two ULIDs.
    dispatched = [
        (
            str(event.node_id or "").split(":", 1)[-1],
            str(event.payload["routing_receipt_id"]),
            str(event.payload["node_contract_hash"]),
        )
        for event in events
        if event.normalized_type is EventType.RUNTIME_CALL_STARTED
        and "routing_receipt_id" in event.payload
    ]
    checkpoint = await store.get_latest_checkpoint(run.run_id)
    statuses = (
        {key: value.value for key, value in checkpoint.node_statuses.items()} if checkpoint else {}
    )
    # Every diff the *run* captured, in store order, as ``(kind, sha256)``. The rollout forks
    # capture their own diffs against the same run id and are excluded by kind: they are the
    # evidence the shadow stage exists to produce, and counting them would make the comparison
    # trivially unequal for the one reason that is allowed.
    workspace_digests = [
        (artifact.kind, artifact.sha256)
        for artifact in await store.list_artifacts(run.run_id)
        if artifact.kind != "SHADOW_ROLLOUT_GIT_DIFF"
    ]
    runtime = fixture.runtime
    assert isinstance(runtime, ShadowSpyRuntime)
    return RunOutcome(
        store=store,
        runtime=runtime,
        manager=fixture.manager,
        workspace_id=fixture.workspace_id,
        lease_path=(root / "worktrees" / run.run_id).resolve(),
        state=final.state,
        dispatched=dispatched,
        statuses=statuses,
        workspace_digests=workspace_digests,
        run_id=run.run_id,
        hook_calls=recorder.calls,
    )


def derive_request_id(context: RoutingContext, snapshot: RoutingSnapshot, mode: RoutingMode) -> str:
    """SDD §8.2's request id, recomputed from the record the route left behind.

    Every input the digest covers *except the mode* is persisted on the ``RoutingContext``
    that the route wrote: the four snapshot ids as columns, the fallback bundle digest as a
    label, the node's immutable hash on the contract ref, and the two version labels. The
    mode is the one input that exists only inside the id, which is exactly what makes this a
    measurement of the mode --- pass the mode the service was assembled with and the executed
    receipt's id must come back, pass any other and it must not.

    ``snapshot`` is a live snapshot with those persisted digests written over it, because
    :func:`~accretion.routing.identity.routing_request_id` takes a whole
    :class:`~accretion.routing.snapshot.RoutingSnapshot` but reads only the five fields
    substituted here.
    """

    return routing_request_id(
        context.node_contract_ref.immutable_hash,
        replace(
            snapshot,
            capability_registry_snapshot_id=context.capability_registry_snapshot_id,
            available_runtime_snapshot_id=context.available_runtime_snapshot_id,
            connection_availability_snapshot_id=context.connection_availability_snapshot_id,
            policy_snapshot_id=context.policy_snapshot_id,
            # `DefaultNodeRoutingService._context` files the catalog's fallback digest under
            # this label; it is the only part of the id that is not a context column.
            fallback_bundle_digest=str(context.labels["fallback_digest"]),
        ),
        context.workspace_router_version,
        context.project_adapter_version,
        mode,
    )


_PAIRED: tuple[RunOutcome, RunOutcome] | None = None


async def paired_runs(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[RunOutcome, RunOutcome]:
    """The two real runs, executed once. Each is a full graph with two human approvals."""

    global _PAIRED
    if _PAIRED is None:
        root = tmp_path_factory.mktemp("m6-e2e")
        shadowed = await run_graph(root / "shadow", RoutingMode.SHADOW)
        baseline = await run_graph(root / "baseline", RoutingMode.BASELINE_ONLY)
        _PAIRED = (shadowed, baseline)
    return _PAIRED


@pytest.mark.acceptance("AC4-M6-017")
async def test_a_shadowed_run_dispatches_exactly_what_the_unshadowed_run_dispatched(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The four observable facts about a run, compared across the two modes.

    A shadow stage that dispatched its own recommendation would change the first list; one that
    forked into the run's lease would change the digest; one that failed the run would change the
    state. The receipt ids themselves are excluded on purpose --- §8.2 puts the mode inside the
    request id, so two modes over identical inputs are two records by design.
    """

    shadowed, baseline = await paired_runs(tmp_path_factory)
    assert shadowed.state is baseline.state is RunState.SUCCEEDED
    assert [row[0] for row in shadowed.dispatched] == [row[0] for row in baseline.dispatched]
    assert shadowed.dispatched
    assert shadowed.statuses == baseline.statuses
    assert shadowed.workspace_digests == baseline.workspace_digests
    assert shadowed.workspace_digests

    # Every dispatch in the shadowed run still resolves to a real executed receipt for the node
    # the event names.  The two runs cannot share receipt ids -- §8.2 puts the mode inside the
    # request id and the run id inside the node contract -- so the invariant is checked inside
    # each run rather than across them, which is what makes a shadow receipt reaching an event
    # payload visible here.
    for outcome in (shadowed, baseline):
        receipts = {
            receipt.contract_id: receipt
            for receipt in await outcome.store.list_routing_receipts(
                workspace_id=outcome.workspace_id
            )
        }
        for _key, receipt_id, node_hash in outcome.dispatched:
            assert receipt_id in receipts
            assert receipts[receipt_id].node_contract_hash == node_hash
            assert receipts[receipt_id].labels.get("routing_status") == "READY"


@pytest.mark.acceptance("AC4-M6-017")
async def test_the_shadow_stage_produced_paired_evidence_that_the_baseline_run_did_not(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The runs are indistinguishable *and* the shadow one measured something. Both, or neither.

    Without this the previous test would pass for a shadow stage that did nothing at all, which
    is the cheapest possible way to leave AC4-M6-017 green and AC4-M6-041 unserved.
    """

    shadowed, baseline = await paired_runs(tmp_path_factory)
    decisions = await shadowed.store.list_shadow_decisions(workspace_id=shadowed.workspace_id)
    assert decisions
    rows = await shadowed.store.list_shadow_rollout_results(workspace_id=shadowed.workspace_id)
    assert {row.kind for row in rows} == {ShadowRolloutKind.SHADOW, ShadowRolloutKind.CONTROL}

    assert not await baseline.store.list_shadow_decisions(workspace_id=baseline.workspace_id)
    assert not await baseline.store.list_shadow_rollout_results(workspace_id=baseline.workspace_id)


@pytest.mark.acceptance("AC4-M6-017")
async def test_every_rollout_arm_ran_in_a_fresh_sandbox_and_never_in_the_runs_lease(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Where the forks ran, read off the sessions the runtime was actually asked to open.

    The run's own sessions are opened at the lease path; every other session the spy saw belongs
    to a rollout arm and is rooted under the shadow fork prefix. A fork taken in the lease would
    appear here as a session at the lease path that the run never asked for --- and would already
    have failed the digest comparison above.
    """

    shadowed, _ = await paired_runs(tmp_path_factory)
    fork_sessions = [
        opened for opened in shadowed.runtime.opened if opened.workspace != shadowed.lease_path
    ]
    assert fork_sessions
    for opened in fork_sessions:
        assert SHADOW_FORK_PREFIX in str(opened.workspace)
        assert shadowed.lease_path not in opened.workspace.parents
        assert opened.model is not None
    assert any(opened.workspace == shadowed.lease_path for opened in shadowed.runtime.opened)


@pytest.mark.acceptance("AC4-M6-017")
async def test_no_shadow_receipt_can_be_the_decision_the_scheduler_dispatches(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Every shadow receipt is written under its own request id and excluded from every head.

    Two mutations die here. Writing the shadow receipt under the *executed* request id would be
    refused by the store's uniqueness guard, so no shadow decision would survive to be found.
    Leaving it a head would put a second candidate in front of ``latest_receipt``, which is the
    receipt a resumed run would claim and dispatch.
    """

    shadowed, _ = await paired_runs(tmp_path_factory)
    receipts = await shadowed.store.list_routing_receipts(workspace_id=shadowed.workspace_id)
    shadows = [
        receipt
        for receipt in receipts
        if receipt.labels.get("routing_status") == SHADOW_ROUTING_STATUS
    ]
    executed = [receipt for receipt in receipts if receipt not in shadows]
    assert shadows and executed

    request_ids = [receipt.routing_request_id for receipt in receipts]
    assert len(set(request_ids)) == len(request_ids)
    executed_requests = {receipt.routing_request_id for receipt in executed}
    assert all(receipt.routing_request_id not in executed_requests for receipt in shadows)

    superseded = {receipt.supersedes_contract_id for receipt in receipts}
    for receipt in shadows:
        assert receipt.contract_id in superseded
    for receipt in executed:
        assert receipt.contract_id not in superseded


@pytest.mark.acceptance("AC4-M6-017")
async def test_the_dispatched_receipts_are_the_executed_ones_and_never_a_shadow(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Read off the dispatch events: no shadow receipt id ever reached the claim path."""

    shadowed, _ = await paired_runs(tmp_path_factory)
    receipts = await shadowed.store.list_routing_receipts(workspace_id=shadowed.workspace_id)
    shadow_ids = {
        receipt.contract_id
        for receipt in receipts
        if receipt.labels.get("routing_status") == SHADOW_ROUTING_STATUS
    }
    events = await shadowed.store.list_events(shadowed.run_id)
    dispatched = {
        str(event.payload["receipt_id"])
        for event in events
        if event.native_type == "accretion/routing/dispatch"
    }
    assert dispatched
    assert dispatched.isdisjoint(shadow_ids)


@pytest.mark.acceptance("AC4-M6-017")
async def test_the_scheduler_routed_the_run_under_the_mode_the_service_was_assembled_with(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The mode the scheduler handed ``route()``, measured rather than read off the source.

    ``RunManager`` used to pin ``RoutingMode.BASELINE_ONLY`` at its call site, which made the
    service's ``default_mode`` unreachable and left every deployment baseline-only whatever it
    had been assembled as. None of the comparisons above can see that: a shadow decision is
    recorded off the service's own mode rather than the receipt's, and the two runs are
    *required* to be indistinguishable, so a scheduler that silently routed the shadowed run
    under ``BASELINE_ONLY`` would keep them all green.

    §8.2 is what makes the regression observable. The mode is one of the seven inputs to the
    request id digest, and it is the only one not also written to the ``RoutingContext``, so
    re-deriving the id from the persisted context under a chosen mode asks precisely one
    question: which mode reached ``route()``? Under the assembled ``SHADOW`` the executed
    receipt's own ``routing_request_id`` comes back; under ``BASELINE_ONLY`` it cannot, and
    the run's own ``ROUTING_REQUESTED`` events --- written inside ``route()`` from its ``mode``
    argument --- say ``SHADOW`` for the shadowed run and ``BASELINE_ONLY`` for the baseline one.

    The receipts walked here are the ones the ``RUNTIME_CALL_STARTED`` events name, so each is
    a real routed AGENT or TOOL node that the real scheduler drove to a real dispatch.
    """

    shadowed, baseline = await paired_runs(tmp_path_factory)
    for outcome, mode in ((shadowed, RoutingMode.SHADOW), (baseline, RoutingMode.BASELINE_ONLY)):
        run = await outcome.store.get_run(outcome.run_id)
        assert run is not None
        task = await outcome.store.get_task(run.task_id)
        assert task is not None
        live = await outcome.manager.routing_service.snapshot(
            workspace_id=outcome.workspace_id, project_id=run.project_id, task=task
        )
        receipts = {
            receipt.contract_id: receipt
            for receipt in await outcome.store.list_routing_receipts(
                workspace_id=outcome.workspace_id
            )
        }

        assert outcome.dispatched
        for _key, receipt_id, _node_hash in outcome.dispatched:
            context = await outcome.store.get_routing_request(
                receipts[receipt_id].routing_request_id
            )
            assert context is not None
            assert derive_request_id(context, live, mode) == context.contract_id
            for other in RoutingMode:
                if other is not mode:
                    assert derive_request_id(context, live, other) != context.contract_id

        events = await outcome.store.list_events(outcome.run_id)
        requested = [
            event for event in events if event.normalized_type is EventType.ROUTING_REQUESTED
        ]
        assert requested
        assert {str(event.payload["mode"]) for event in requested} == {mode.value}


def test_the_scheduler_routes_under_the_services_own_mode() -> None:
    """The one line M6.2 changed in the scheduler, pinned as source beside the behaviour above.

    The behavioural witness is
    ``test_the_scheduler_routed_the_run_under_the_mode_the_service_was_assembled_with``; this
    is a second, cheaper padlock on the same line, because the regression is a one-word edit
    and this test names the word. It is deliberately not the only guard: an equivalent
    refactor of that call site --- a local variable, a ternary --- would fail this assert while
    remaining correct, and the behavioural test is the one that would then say so.
    """

    source = Path("src/accretion/services/run_manager.py").read_text(encoding="utf-8")
    assert "mode=self.routing_service.default_mode," in source
    assert "mode=RoutingMode.BASELINE_ONLY," not in source
