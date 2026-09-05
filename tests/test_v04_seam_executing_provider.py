"""The executing provider travels with the session, not with the run.

``run.provider`` is what the operator *requested*. The provider that actually runs a
node is a property of the session that node's call was submitted on. Today every
session on a run is created on ``run.provider``, so the two coincide and this seam
changes nothing observable --- which is the first test here, a byte-for-byte golden
trace. The remaining three prove the seam is real: cancellation, the concurrency slot
and capability attribution all follow the session, so v0.4 M2 can create a node's
session on a different runtime and nothing else has to move.

Regenerating the golden file (it must come from the *base* commit, never from this
branch, or it proves nothing)::

    git -C <repo> worktree add /tmp/golden-base <base-sha>
    cd /tmp/golden-base && PYTHONPATH=/tmp/golden-base/src python -c "
    import asyncio, json, sys, tempfile, pathlib
    sys.path.insert(0, '<this-worktree>')
    from tests.test_v04_seam_executing_provider import collect_seam_trace
    with tempfile.TemporaryDirectory() as tmp:
        trace = asyncio.run(collect_seam_trace(pathlib.Path(tmp)))
    print(json.dumps(trace, indent=2, sort_keys=True))
    "

That recipe is why every module-level import below predates this PR and why the one
symbol this PR adds, ``ActiveRuntimeRef``, is imported inside the test that needs it:
the dumper has to import cleanly against the base commit for the golden to mean
anything.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    AcceptancePolicy,
    ApprovalDecisionValue,
    ApprovalRecord,
    ApprovalStatus,
    Capability,
    CapabilityBackend,
    CapabilityExecutionResult,
    CapabilityExecutionStatus,
    CapabilityKind,
    CapabilityRequest,
    Checkpoint,
    CheckpointKind,
    EventType,
    ExecutionMode,
    GraphNodeKind,
    GraphNodeStatus,
    IdempotencyMode,
    LoopExecutionStatus,
    LoopStopReason,
    Provider,
    RiskLevel,
    Run,
    RunNode,
    RunRef,
    RunState,
    SessionConfig,
    SessionRef,
    Task,
    TaskBudgets,
    WorkflowNodeSpec,
    WorkspaceLease,
)
from accretion.governance import (
    CapabilityExecutor,
    CapabilityGateway,
    CapabilityPolicyEngine,
    CredentialBroker,
    seed_governance,
)
from accretion.ids import new_id
from accretion.looping import build_loop_execution, build_loop_spec
from accretion.persistence.side_effects import MemorySideEffectLedger
from accretion.persistence.store import MemoryStore
from accretion.runtimes.common import RuntimeSubmission
from accretion.runtimes.fake import FakeCallOutcome, FakeRuntime
from accretion.services.run_manager import RunManager, _GraphCursor
from accretion.templates import instantiate_run_graph, seed_templates
from accretion.workspace import WorktreeManager

GOLDEN_TRACE = Path(__file__).parent / "golden" / "v04_seam_fake_graph_trace.json"

# The provider the operator asks for, and a second, non-live provider standing in for
# "some other runtime the router picked". Both are registered in the runtime map, so a
# lookup keyed on the wrong one still finds *a* runtime --- which is exactly why these
# tests count calls per runtime instead of asserting that a lookup raised.
REQUESTED = Provider.FAKE
EXECUTING = Provider.DETERMINISTIC

# One capability per authorization terminal the gateway can reach, so every branch
# that names a provider is driven by a real policy decision rather than by a stub.
PROBE_CAPABILITY = "accretion.seam-probe"  # allowed, low risk: succeeds
DENIED_CAPABILITY = "accretion.seam-denied"  # never in a task's allowed list
GUARDED_CAPABILITY = "accretion.seam-guarded"  # high risk: needs an approval
WRITE_CAPABILITY = "accretion.seam-write"  # side-effecting: idempotency-keyed
FAILING_CAPABILITY = "accretion.seam-failing"  # allowed, but the handler raises
ABSENT_CAPABILITY = "accretion.seam-absent"  # never registered at all


# --- deterministic normalisation for the golden trace -----------------------------


# Minted ids are ULID-shaped: a three-character kind prefix and 26 base32 characters.
_MINTED_ID = re.compile(r"\b([a-z]{3})_([0-9A-Z]{26})\b")
# Git object ids and artifact digests are content- and clock-dependent.
_DIGEST = re.compile(r"\b[0-9a-f]{40}(?:[0-9a-f]{24})?\b")
# Wall-clock and elapsed-time fields. Everything else is kept, including every payload
# key, every event type, every node key and every status: a trace that differed in any
# of those would not survive this normalisation.
_VOLATILE_KEYS = frozenset(
    {
        "completed_at",
        "created_at",
        "deadline",
        "decided_at",
        "generated_at",
        "retrieved_at",
        "timestamp",
        "updated_at",
        "wall_time_seconds",
    }
)


def _stable_id(raw: str, prefix: str, table: dict[str, str]) -> str:
    if raw not in table:
        seen = sum(1 for value in table.values() if value.startswith(f"{prefix}_"))
        table[raw] = f"{prefix}_{seen + 1:03d}"
    return table[raw]


def _normalize_text(value: str, table: dict[str, str], root: Path) -> str:
    value = value.replace(str(root), "<root>")
    value = _MINTED_ID.sub(lambda m: _stable_id(m.group(0), m.group(1), table), value)
    return _DIGEST.sub("<digest>", value)


def _normalize(value: Any, table: dict[str, str], root: Path) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize(item, table, root)
            for key, item in sorted(value.items())
            if key not in _VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_normalize(item, table, root) for item in value]
    if isinstance(value, str):
        return _normalize_text(value, table, root)
    return value


# --- fixtures shared by the golden dumper and the seam tests ----------------------


def initialize_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Accretion Test"], check=True)
    (path / "result.json").write_text('{"ok": false}\n')
    subprocess.run(["git", "-C", str(path), "add", "result.json"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)


def write_valid_output(session: SessionRef, _request: object) -> None:
    (session.workspace / "result.json").write_text('{"ok": true}\n')


def build_manager(
    root: Path,
    *,
    store: MemoryStore,
    runtimes: dict[Provider, Any],
    limiter: ConcurrencyLimiter | None = None,
    auto_resume: bool = False,
) -> RunManager:
    return RunManager(
        store=store,
        worktrees=WorktreeManager(root / "worktrees", root / "artifacts"),
        runtimes=runtimes,
        limiter=limiter
        or ConcurrencyLimiter(global_limit=2, provider_limit=2, project_limit=2),
        live_providers_enabled=False,
        auto_resume_on_reconcile=auto_resume,
    )


async def graph_task(manager: RunManager, project_id: str) -> str:
    """The task shape that plans onto ``fixed-graph-v1``: high risk, declared output."""

    task = await manager.create_task(
        project_id=project_id,
        objective="Apply a high-risk change with plan and outcome approval.",
        task_patch={
            "task_type": "OTHER",
            "risk_level": "HIGH",
            "required_outputs": [{"path": "result.json", "kind": "json"}],
        },
    )
    return task.envelope.task_id


async def wait_for_approval(manager: RunManager, run_id: str) -> str:
    for _ in range(500):
        pending = await manager.store.list_approvals(run_id, ApprovalStatus.PENDING)
        if pending:
            return pending[0].approval_id
        await asyncio.sleep(0.01)
    raise AssertionError("no pending approval appeared")


async def paused_graph_fixture(
    root: Path,
    store: MemoryStore,
    manager: RunManager,
    *,
    session_runtime: FakeRuntime,
    active_key: str,
    arrival_edge_key: str | None,
    node_statuses: dict[str, GraphNodeStatus],
) -> Run:
    """A ``fixed-graph-v1`` run parked at ``active_key`` with a durable session.

    ``session_runtime`` is what makes this fixture useful here: the persisted session
    can be created on a runtime other than ``run.provider``'s, which is precisely the
    state v0.4 M2 will produce and which resume must survive.
    """

    repository = root / "repository"
    if not repository.exists():
        repository.mkdir(parents=True)
        initialize_repository(repository)
    await seed_templates(store)
    project = await manager.create_project("fixture", repository)
    task_id = await graph_task(manager, project.project_id)
    planning = await manager.get_task_planning(task_id)
    policy = AcceptancePolicy(
        policy_id=new_id("acceptance_policy"),
        required_verifiers=["output-contract", "git-diff", "trajectory-policy"],
        require_human_if_risk_gte=None,
        outcome_check="all declared deterministic verifiers must pass",
    )
    await store.save_acceptance_policy(policy)
    run = await store.create_run(
        Run(
            run_id=new_id("run"),
            task_id=task_id,
            project_id=project.project_id,
            provider=REQUESTED,
            state=RunState.RUNNING,
            strategy_decision_id=planning.current_decision.decision_id,
            execution_mode=ExecutionMode.GRAPH,
            workflow_template_id="fixed-graph-v1",
            acceptance_policy_id=policy.policy_id,
        )
    )
    template = await store.get_workflow_template("fixed-graph-v1")
    assert template is not None
    graph = instantiate_run_graph(
        template, run_id=run.run_id, task_id=run.task_id, budgets=TaskBudgets()
    )
    await store.create_run_graph(graph)
    lease = await manager.worktrees.acquire(
        project_id=project.project_id, run_id=run.run_id, repository=repository
    )
    await store.save_lease(lease)
    session = await session_runtime.create_session(
        SessionConfig(run_id=run.run_id, workspace=lease.path)
    )
    await store.save_session(session)
    run = await store.update_run(
        run.run_id,
        RunState.RUNNING,
        session_id=session.session_id,
        workspace_lease_id=lease.lease_id,
    )
    await store.append_checkpoint(
        Checkpoint(
            checkpoint_id=new_id("checkpoint"),
            run_id=run.run_id,
            kind=CheckpointKind.NODE_BOUNDARY,
            sequence=0,
            run_state=RunState.RUNNING,
            run_revision=run.revision,
            active_node_ids=[f"{run.run_id}:{active_key}"],
            arrival_edge_key=arrival_edge_key,
            node_statuses=node_statuses,
            run_graph_id=graph.run_graph_id,
            graph_revision=graph.graph_revision,
            workspace_lease_id=lease.lease_id,
            workspace_revision=lease.base_revision,
        )
    )
    decided = await store.save_approval(
        ApprovalRecord(
            approval_id=new_id("approval"),
            run_id=run.run_id,
            node_id=f"{run.run_id}:approve-outcome",
            native_request_id="gate:approve-outcome",
            method="accretion/gate",
            summary="Approve the verified outcome before completion.",
        )
    )
    await store.decide_approval(decided.approval_id, ApprovalDecisionValue.APPROVE)
    return await store.update_run(run.run_id, RunState.PAUSED)


async def prepare_run_with_session(
    root: Path,
    store: MemoryStore,
    manager: RunManager,
    *,
    session_runtime: FakeRuntime,
    execution_mode: ExecutionMode,
    workflow_template_id: str,
) -> tuple[Run, AcceptancePolicy, Task]:
    """A RUNNING run with a lease and one durable session on ``session_runtime``.

    Shared by the DIRECT and LOOP fixtures below. ``paused_graph_fixture`` deliberately
    keeps its own copy: it feeds the golden dumper, which has to keep minting exactly
    the ids, in exactly the order, that it minted when the golden file was captured
    against the base commit.
    """

    repository = root / "repository"
    if not repository.exists():
        repository.mkdir(parents=True)
        initialize_repository(repository)
    await seed_templates(store)
    project = await manager.create_project("fixture", repository)
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Finish the interrupted work on the session that was carrying it.",
        task_patch={
            "task_type": "IMPLEMENT",
            "risk_level": "LOW",
            "required_outputs": [{"path": "result.json", "kind": "json"}],
            "budgets": {"max_loop_iterations": 2},
        },
    )
    policy = AcceptancePolicy(
        policy_id=new_id("acceptance_policy"),
        required_verifiers=["output-contract", "trajectory-policy"],
        require_human_if_risk_gte=None,
        outcome_check="all declared deterministic verifiers must pass",
    )
    await store.save_acceptance_policy(policy)
    run = await store.create_run(
        Run(
            run_id=new_id("run"),
            task_id=task.envelope.task_id,
            project_id=project.project_id,
            provider=REQUESTED,
            state=RunState.RUNNING,
            execution_mode=execution_mode,
            workflow_template_id=workflow_template_id,
            acceptance_policy_id=policy.policy_id,
        )
    )
    lease = await manager.worktrees.acquire(
        project_id=project.project_id, run_id=run.run_id, repository=repository
    )
    await store.save_lease(lease)
    session = await session_runtime.create_session(
        SessionConfig(run_id=run.run_id, workspace=lease.path)
    )
    await store.save_session(session)
    run = await store.update_run(
        run.run_id,
        RunState.RUNNING,
        session_id=session.session_id,
        workspace_lease_id=lease.lease_id,
    )
    return run, policy, task


async def paused_direct_fixture(
    root: Path, store: MemoryStore, manager: RunManager, *, session_runtime: FakeRuntime
) -> Run:
    """A paused DIRECT run --- no run graph, so ``resume`` takes ``_resume_direct``."""

    run, _policy, _task = await prepare_run_with_session(
        root,
        store,
        manager,
        session_runtime=session_runtime,
        execution_mode=ExecutionMode.DIRECT,
        workflow_template_id="direct-v1",
    )
    return await store.update_run(run.run_id, RunState.PAUSED)


async def paused_loop_fixture(
    root: Path, store: MemoryStore, manager: RunManager, *, session_runtime: FakeRuntime
) -> Run:
    """A paused LOOP run with its loop execution parked, taking ``_resume_loop``."""

    run, policy, task = await prepare_run_with_session(
        root,
        store,
        manager,
        session_runtime=session_runtime,
        execution_mode=ExecutionMode.LOOP,
        workflow_template_id="feedback-loop-v1",
    )
    execution = await store.create_loop_execution(
        build_loop_execution(
            run_id=run.run_id,
            spec=build_loop_spec(task.envelope, policy.required_verifiers),
            policy=policy,
        )
    )
    await store.update_loop_execution(
        execution.loop_execution_id,
        execution.state,
        status=LoopExecutionStatus.PAUSED,
        stop_reason=LoopStopReason.INTERRUPTED,
        expected_revision=execution.revision,
    )
    return await store.update_run(run.run_id, RunState.PAUSED)


# --- the golden dumper ------------------------------------------------------------


async def _dump_phase(
    name: str, store: MemoryStore, run_id: str, table: dict[str, str], root: Path
) -> dict[str, Any]:
    run = await store.get_run(run_id)
    assert run is not None
    events = await store.list_events(run_id)
    checkpoints = await store.list_checkpoints(run_id)
    approvals = await store.list_approvals(run_id)
    return {
        "name": name,
        "run_state": run.state.value,
        "execution_mode": run.execution_mode.value if run.execution_mode else None,
        "workflow_template_id": run.workflow_template_id,
        "last_sequence": run.last_sequence,
        "events": [
            {
                "sequence": event.sequence,
                "normalized_type": event.normalized_type.value,
                "native_type": event.native_type,
                "provider": event.provider.value,
                "adapter_version": event.adapter_version,
                "session_id": _normalize_text(event.session_id, table, root),
                "node_id": (
                    _normalize_text(event.node_id, table, root) if event.node_id else None
                ),
                "payload": _normalize(event.payload, table, root),
            }
            for event in events
        ],
        "checkpoints": [
            _normalize(checkpoint.model_dump(mode="json"), table, root)
            for checkpoint in checkpoints
        ],
        "approvals": [
            {
                "node_id": _normalize_text(approval.node_id or "", table, root),
                "native_request_id": approval.native_request_id,
                "method": approval.method,
                "status": approval.status.value,
            }
            for approval in approvals
        ],
    }


async def _phase_graph_run(root: Path, table: dict[str, str]) -> dict[str, Any]:
    """A whole ``fixed-graph-v1`` run: TASK, AGENT, GATE, TOOL and VERIFIER nodes."""

    repository = root / "repository"
    repository.mkdir(parents=True)
    initialize_repository(repository)
    store = MemoryStore()
    await seed_templates(store)
    runtime = FakeRuntime(
        scripted_outcomes=[
            FakeCallOutcome(),  # plan
            FakeCallOutcome(hook=write_valid_output),  # act
        ]
    )
    manager = build_manager(root, store=store, runtimes={REQUESTED: runtime})
    project = await manager.create_project("fixture", repository)
    task_id = await graph_task(manager, project.project_id)
    run = await manager.start_run(task_id, REQUESTED)
    plan_approval = await wait_for_approval(manager, run.run_id)
    await manager.resolve_approval(plan_approval, ApprovalDecisionValue.APPROVE)
    outcome_approval = await wait_for_approval(manager, run.run_id)
    await manager.resolve_approval(outcome_approval, ApprovalDecisionValue.APPROVE)
    await asyncio.wait_for(manager.background[run.run_id], 30)
    return await _dump_phase("graph-run-to-verified-success", store, run.run_id, table, root)


async def _phase_interrupted_resume(root: Path, table: dict[str, str]) -> dict[str, Any]:
    """An interrupted run picked back up: the resume half of the seam."""

    store = MemoryStore()
    runtime = FakeRuntime(scripted_outcomes=[FakeCallOutcome(hook=write_valid_output)])
    manager = build_manager(root, store=store, runtimes={REQUESTED: runtime})
    run = await paused_graph_fixture(
        root,
        store,
        manager,
        session_runtime=runtime,
        active_key="act",
        arrival_edge_key="approve-plan-act",
        node_statuses={
            "initialize": GraphNodeStatus.SUCCEEDED,
            "plan": GraphNodeStatus.SUCCEEDED,
            "approve-plan": GraphNodeStatus.SUCCEEDED,
        },
    )
    await manager.resume(run.run_id)
    await asyncio.wait_for(manager.background[run.run_id], 30)
    return await _dump_phase("interrupted-run-resumed", store, run.run_id, table, root)


async def collect_seam_trace(root: Path) -> dict[str, Any]:
    """Every durable trace the fake provider produces, normalised deterministically.

    Importable from a checkout of the base commit --- that is the whole point of it
    being a module-level function with no dependency on anything this PR adds.
    """

    table: dict[str, str] = {}
    return {
        "version": "v04-seam-1",
        "phases": [
            await _phase_graph_run(root / "phase-a", table),
            await _phase_interrupted_resume(root / "phase-b", table),
        ],
    }


# --- test doubles -----------------------------------------------------------------


class ProviderRuntime(FakeRuntime):
    """A ``FakeRuntime`` that owns a provider and counts the control calls it receives.

    ``FakeRuntime`` hard-codes ``Provider.FAKE`` into every session it mints, which is
    exactly the assumption under test, so this double stamps its own provider on the
    session instead. The counters are what distinguish "reached the right runtime" from
    "reached a runtime".
    """

    def __init__(
        self,
        provider: Provider,
        *,
        scripted_outcomes: list[FakeCallOutcome] | None = None,
    ) -> None:
        super().__init__(scripted_outcomes=scripted_outcomes)
        self.provider = provider
        self.created_sessions: list[str] = []
        self.submitted: list[str] = []
        self.interrupted: list[str] = []
        self.resumed: list[str] = []
        self.terminated: list[str] = []

    async def create_session(self, config: SessionConfig) -> SessionRef:
        session = await super().create_session(config)
        stamped = session.model_copy(update={"provider": self.provider})
        self.sessions[stamped.session_id] = stamped
        self.created_sessions.append(stamped.session_id)
        return stamped

    async def submit(self, session: SessionRef, request: RuntimeSubmission) -> RunRef:
        # The single lookup in ``_runtime_call`` dispatches submit, events, artifacts
        # and usage for every agent call on the run, so this counter is what says the
        # call itself --- not just the session that preceded it --- reached the runtime
        # that owns the session.
        self.submitted.append(session.session_id)
        return await super().submit(session, request)

    async def interrupt(self, run: RunRef) -> None:
        self.interrupted.append(run.session_id)
        await super().interrupt(run)

    async def resume(self, run: RunRef) -> None:
        self.resumed.append(run.session_id)
        await super().resume(run)

    async def terminate(self, run: RunRef) -> None:
        self.terminated.append(run.session_id)
        await super().terminate(run)


class RecordingLimiter(ConcurrencyLimiter):
    """A limiter that remembers which provider each slot was taken for."""

    def __init__(self) -> None:
        super().__init__(global_limit=2, provider_limit=2, project_limit=2)
        self.acquired: list[Provider] = []

    @asynccontextmanager
    async def slot(self, provider: Provider, project_id: str) -> AsyncIterator[None]:
        self.acquired.append(provider)
        async with super().slot(provider, project_id):
            yield


class CountingHandler:
    """A capability handler that counts the calls that actually reached a backend.

    The duplicate-idempotency branch is only proven if the second attempt did *not*
    run the handler again, so the count is load-bearing rather than decorative.
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def __call__(
        self, arguments: dict[str, Any], credentials: Mapping[str, str]
    ) -> dict[str, Any]:
        del credentials
        self.calls += 1
        if self.fail:
            raise RuntimeError("the seam probe handler refused to run")
        return {"echoed": str(arguments["query"])}


def seam_capability(
    capability_id: str,
    *,
    risk: RiskLevel = RiskLevel.LOW,
    side_effects: list[str] | None = None,
    idempotency: IdempotencyMode = IdempotencyMode.NONE,
) -> Capability:
    """One governed capability, identical but for what the policy engine will say."""

    return Capability(
        capability_id=capability_id,
        kind=CapabilityKind.TOOL,
        version="1.0.0",
        description="Echo a node's query back through the governed gateway.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "maxLength": 2000}},
            "required": ["query"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"echoed": {"type": "string"}},
            "required": ["echoed"],
            "additionalProperties": False,
        },
        risk=risk,
        side_effects=list(side_effects or []),
        idempotency=idempotency,
        backend=CapabilityBackend.PYTHON,
        created_at=datetime(2026, 9, 5, tzinfo=UTC),
    )


class DirectGatewayInvoker:
    """The resolver-free half of ``GatewayCapabilityInvoker``.

    Resolution is not what this PR touches; attribution is. This double therefore does
    what the production invoker does minus the connector lookup --- build the request
    and hand the gateway the provider it was given --- so the assertion lands on the
    gateway's own terminal rather than on a stub of it.

    Every request it mints is kept, because the gateway stamps ``request_id`` into each
    event as ``tool_call_id``: that is how a test isolates the events of one call from
    the events of the calls before it on the same run.
    """

    def __init__(self, gateway: CapabilityGateway) -> None:
        self.gateway = gateway
        self.requests: list[CapabilityRequest] = []

    async def __call__(
        self,
        *,
        run_id: str,
        node_id: str,
        capability_id: str,
        arguments: dict[str, object],
        executing_provider: Provider | None = None,
        idempotency_key: str | None = None,
    ) -> CapabilityExecutionResult | None:
        request = CapabilityRequest(
            request_id=new_id("capability_request"),
            run_id=run_id,
            node_id=node_id,
            capability_id=capability_id,
            capability_version="1.0.0",
            arguments=dict(arguments),
            declared_reason="seam attribution probe",
            idempotency_key=idempotency_key,
        )
        self.requests.append(request)
        return await self.gateway.execute(request, executing_provider=executing_provider)


async def setup_capability_stack(
    root: Path,
) -> tuple[
    MemoryStore,
    RunManager,
    ProviderRuntime,
    ProviderRuntime,
    DirectGatewayInvoker,
    dict[str, CountingHandler],
]:
    """A manager whose TOOL nodes spend governed capabilities through a real gateway."""

    store = MemoryStore()
    await seed_governance(store)
    await seed_templates(store)
    for capability in (
        seam_capability(PROBE_CAPABILITY),
        seam_capability(DENIED_CAPABILITY),
        seam_capability(GUARDED_CAPABILITY, risk=RiskLevel.HIGH),
        seam_capability(
            WRITE_CAPABILITY,
            side_effects=["durable seam record"],
            idempotency=IdempotencyMode.KEYED,
        ),
        seam_capability(FAILING_CAPABILITY),
    ):
        await store.upsert_capability(capability)
    handlers = {
        PROBE_CAPABILITY: CountingHandler(),
        DENIED_CAPABILITY: CountingHandler(),
        GUARDED_CAPABILITY: CountingHandler(),
        WRITE_CAPABILITY: CountingHandler(),
        FAILING_CAPABILITY: CountingHandler(fail=True),
    }
    requested = ProviderRuntime(REQUESTED)
    executing = ProviderRuntime(EXECUTING)
    manager = build_manager(
        root, store=store, runtimes={REQUESTED: requested, EXECUTING: executing}
    )
    invoker = DirectGatewayInvoker(
        CapabilityGateway(
            store=store,
            side_effects=MemorySideEffectLedger(),
            broker=CredentialBroker(),
            executor=CapabilityExecutor(dict(handlers)),
            policy_engine=CapabilityPolicyEngine(),
        )
    )
    manager.capability_invoker = invoker
    return store, manager, requested, executing, invoker, handlers


async def setup_capability_run(
    root: Path,
    store: MemoryStore,
    manager: RunManager,
    executing: ProviderRuntime,
    *,
    allowed: list[str],
) -> tuple[Run, WorkspaceLease, SessionRef]:
    """A run requested on FAKE whose work is carried by a DETERMINISTIC session."""

    repository = root / "repository"
    if not repository.exists():
        repository.mkdir(parents=True)
        initialize_repository(repository)
    project = await manager.create_project("fixture", repository)
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Spend one governed capability from a TOOL node.",
        task_patch={
            "task_type": "OTHER",
            "risk_level": "LOW",
            "allowed_capabilities": list(allowed),
        },
    )
    run = await store.create_run(
        Run(
            run_id=new_id("run"),
            task_id=task.envelope.task_id,
            project_id=project.project_id,
            provider=REQUESTED,
            state=RunState.RUNNING,
            execution_mode=ExecutionMode.GRAPH,
        )
    )
    lease = await manager.worktrees.acquire(
        project_id=project.project_id, run_id=run.run_id, repository=repository
    )
    await store.save_lease(lease)
    session = await executing.create_session(
        SessionConfig(run_id=run.run_id, workspace=lease.path)
    )
    await store.save_session(session)
    return run, lease, session


async def attributed_events(
    store: MemoryStore, invoker: DirectGatewayInvoker, run_id: str
) -> dict[EventType, Provider]:
    """Which provider the gateway named on each event of the most recent call."""

    request_id = invoker.requests[-1].request_id
    return {
        event.normalized_type: event.provider
        for event in await store.list_events(run_id)
        if event.payload.get("tool_call_id") == request_id
    }


# --- tests ------------------------------------------------------------------------


async def test_golden_trace_is_byte_identical_with_the_seam_present(tmp_path: Path) -> None:
    """Zero behaviour change, asserted against a trace captured before the change.

    The golden file was produced by this same dumper running against the base commit.
    Every event type, native type, provider, session id, node key, payload key and
    payload value survives normalisation; only minted ids (renumbered in order of first
    appearance, so a *different number* of them still fails), digests and clock fields
    are folded away.
    """

    expected = json.loads(GOLDEN_TRACE.read_text())
    # The golden must be worth diffing against: a normalisation that erased the trace
    # would let anything pass.
    dumped = json.dumps(expected)
    for marker in (
        "RUNTIME_CALL_STARTED",
        "NODE_ENTERED",
        "CHECKPOINT_SAVED",
        "APPROVAL_REQUIRED",
        "RUN_RESUMED",
        "RUN_COMPLETED",
        '"plan"',
        '"act"',
        '"observe"',
        '"verify"',
        "SUCCEEDED",
    ):
        assert marker in dumped, f"the golden trace no longer proves {marker}"

    observed = await collect_seam_trace(tmp_path)

    assert observed == expected


async def test_interrupt_resume_terminate_reach_the_runtime_that_owns_the_session(
    tmp_path: Path,
) -> None:
    """Cancellation follows the session, not the run.

    The run was requested on FAKE and its in-flight call lives on a DETERMINISTIC
    session. Both runtimes are registered, so a lookup on ``run.provider`` would find a
    runtime and silently interrupt nothing; only the per-runtime counters catch it.
    """

    from accretion.services.run_manager import ActiveRuntimeRef

    store = MemoryStore()
    requested = ProviderRuntime(REQUESTED)
    executing = ProviderRuntime(EXECUTING)
    manager = build_manager(
        tmp_path, store=store, runtimes={REQUESTED: requested, EXECUTING: executing}
    )
    project = await manager.create_project("fixture", tmp_path)
    task_id = await graph_task(manager, project.project_id)
    run = await store.create_run(
        Run(
            run_id=new_id("run"),
            task_id=task_id,
            project_id=project.project_id,
            provider=REQUESTED,
            state=RunState.RUNNING,
            execution_mode=ExecutionMode.DIRECT,
        )
    )
    session = await executing.create_session(
        SessionConfig(run_id=run.run_id, workspace=tmp_path)
    )
    await store.save_session(session)
    ref = RunRef(
        run_id=run.run_id,
        session_id=session.session_id,
        runtime_call_id=new_id("runtime_call"),
    )
    manager.active_refs[run.run_id] = ActiveRuntimeRef(session=session, ref=ref)

    assert (await manager.resume(run.run_id)).state is RunState.RUNNING
    assert (await manager.pause(run.run_id)).state is RunState.PAUSED
    await manager.cancel(run.run_id)

    assert executing.resumed == [session.session_id]
    # pause interrupts once; terminate interrupts again through the fake's own path.
    assert executing.interrupted == [session.session_id, session.session_id]
    assert executing.terminated == [session.session_id]
    assert (requested.resumed, requested.interrupted, requested.terminated) == ([], [], [])
    final = await store.get_run(run.run_id)
    assert final is not None and final.state is RunState.CANCELLED


async def test_limiter_slot_is_taken_for_the_executing_provider(tmp_path: Path) -> None:
    """The concurrency slot names the runtime the call is actually made on.

    A resume continues the persisted session, so the slot it holds --- and the runtime
    it re-enters --- belong to that session's provider. Charging ``run.provider``
    instead would throttle a runtime that is not being used and leave the one that is
    unmetered.
    """

    store = MemoryStore()
    limiter = RecordingLimiter()
    requested = ProviderRuntime(REQUESTED)
    executing = ProviderRuntime(
        EXECUTING, scripted_outcomes=[FakeCallOutcome(hook=write_valid_output)]
    )
    manager = build_manager(
        tmp_path,
        store=store,
        runtimes={REQUESTED: requested, EXECUTING: executing},
        limiter=limiter,
    )
    run = await paused_graph_fixture(
        tmp_path,
        store,
        manager,
        session_runtime=executing,
        active_key="act",
        arrival_edge_key="approve-plan-act",
        node_statuses={
            "initialize": GraphNodeStatus.SUCCEEDED,
            "plan": GraphNodeStatus.SUCCEEDED,
            "approve-plan": GraphNodeStatus.SUCCEEDED,
        },
    )
    assert limiter.acquired == []

    await manager.resume(run.run_id)
    await asyncio.wait_for(manager.background[run.run_id], 30)

    assert limiter.acquired == [EXECUTING]
    assert len(executing.created_sessions) == 2
    assert requested.created_sessions == []
    final = await store.get_run(run.run_id)
    assert final is not None and final.state is RunState.SUCCEEDED
    resumed_session = await store.get_session_for_run(run.run_id)
    assert resumed_session is not None and resumed_session.provider is EXECUTING
    # The one lookup in ``_runtime_call`` dispatches submit, events, artifacts and
    # usage for the whole call. Both runtimes succeed by default, so the run would
    # still reach SUCCEEDED if the agent call had been made on the wrong one; only the
    # per-runtime submit log says where the work actually happened.
    assert executing.submitted == [resumed_session.session_id]
    assert requested.submitted == []


async def test_limiter_slot_is_taken_for_the_executing_provider_on_direct_resume(
    tmp_path: Path,
) -> None:
    """The DIRECT resume path follows the session too.

    There are three resume implementations --- graph, direct and loop --- each with its
    own copy of the slot-and-create-session pair, so proving one proves only one.
    """

    store = MemoryStore()
    limiter = RecordingLimiter()
    requested = ProviderRuntime(REQUESTED)
    executing = ProviderRuntime(
        EXECUTING, scripted_outcomes=[FakeCallOutcome(hook=write_valid_output)]
    )
    manager = build_manager(
        tmp_path,
        store=store,
        runtimes={REQUESTED: requested, EXECUTING: executing},
        limiter=limiter,
    )
    run = await paused_direct_fixture(tmp_path, store, manager, session_runtime=executing)

    await manager.resume(run.run_id)
    await asyncio.wait_for(manager.background[run.run_id], 30)

    assert limiter.acquired == [EXECUTING]
    assert (requested.created_sessions, requested.submitted) == ([], [])
    assert len(executing.created_sessions) == 2
    resumed_session = await store.get_session_for_run(run.run_id)
    assert resumed_session is not None and resumed_session.provider is EXECUTING
    assert executing.submitted == [resumed_session.session_id]
    final = await store.get_run(run.run_id)
    assert final is not None and final.state is RunState.SUCCEEDED


async def test_limiter_slot_is_taken_for_the_executing_provider_on_loop_resume(
    tmp_path: Path,
) -> None:
    """The LOOP resume path follows the session too."""

    store = MemoryStore()
    limiter = RecordingLimiter()
    requested = ProviderRuntime(REQUESTED)
    executing = ProviderRuntime(
        EXECUTING, scripted_outcomes=[FakeCallOutcome(hook=write_valid_output)]
    )
    manager = build_manager(
        tmp_path,
        store=store,
        runtimes={REQUESTED: requested, EXECUTING: executing},
        limiter=limiter,
    )
    run = await paused_loop_fixture(tmp_path, store, manager, session_runtime=executing)

    await manager.resume(run.run_id)
    await asyncio.wait_for(manager.background[run.run_id], 30)

    assert limiter.acquired == [EXECUTING]
    assert (requested.created_sessions, requested.submitted) == ([], [])
    assert len(executing.created_sessions) == 2
    resumed_session = await store.get_session_for_run(run.run_id)
    assert resumed_session is not None and resumed_session.provider is EXECUTING
    assert executing.submitted == [resumed_session.session_id]
    final = await store.get_run(run.run_id)
    assert final is not None and final.state is RunState.SUCCEEDED


async def test_reconcile_probes_the_runtime_the_resume_would_actually_reach(
    tmp_path: Path,
) -> None:
    """Auto-resume eligibility is decided against the runtime the resume will call.

    The run was requested on FAKE, which this manager cannot reach at all; the session
    carrying its work is on DETERMINISTIC, which it can. Asking whether ``run.provider``
    is available would refuse a run that is perfectly resumable.
    """

    store = MemoryStore()
    executing = ProviderRuntime(
        EXECUTING, scripted_outcomes=[FakeCallOutcome(hook=write_valid_output)]
    )
    manager = build_manager(
        tmp_path, store=store, runtimes={EXECUTING: executing}, auto_resume=True
    )
    run = await paused_graph_fixture(
        tmp_path,
        store,
        manager,
        session_runtime=executing,
        active_key="act",
        arrival_edge_key="approve-plan-act",
        node_statuses={
            "initialize": GraphNodeStatus.SUCCEEDED,
            "plan": GraphNodeStatus.SUCCEEDED,
            "approve-plan": GraphNodeStatus.SUCCEEDED,
        },
    )
    # A crash leaves the run RUNNING with a valid checkpoint; that is what reconcile
    # classifies. The fixture parks it PAUSED, which reconcile would skip.
    await store.update_run(run.run_id, RunState.RUNNING)

    await manager.reconcile()

    auto_resume = manager.background.get(f"auto-resume:{run.run_id}")
    assert auto_resume is not None, "a resumable run whose session runtime is present"
    await asyncio.wait_for(auto_resume, 30)
    worker = manager.background.get(run.run_id)
    if worker is not None:
        await asyncio.wait_for(worker, 30)

    final = await store.get_run(run.run_id)
    assert final is not None and final.state is RunState.SUCCEEDED
    assert len(executing.created_sessions) == 2


async def test_reconcile_refuses_auto_resume_when_the_session_runtime_is_absent(
    tmp_path: Path,
) -> None:
    """The converse: the run's own provider being reachable is not enough.

    Everything here is configured for FAKE except the one thing a resume would use ---
    the session --- which is on a runtime this manager does not have. Clearing the run
    for auto-resume would schedule a task that cannot make its first call.
    """

    store = MemoryStore()
    requested = ProviderRuntime(REQUESTED)
    unreachable = ProviderRuntime(EXECUTING)
    manager = build_manager(
        tmp_path, store=store, runtimes={REQUESTED: requested}, auto_resume=True
    )
    run = await paused_graph_fixture(
        tmp_path,
        store,
        manager,
        session_runtime=unreachable,
        active_key="act",
        arrival_edge_key="approve-plan-act",
        node_statuses={
            "initialize": GraphNodeStatus.SUCCEEDED,
            "plan": GraphNodeStatus.SUCCEEDED,
            "approve-plan": GraphNodeStatus.SUCCEEDED,
        },
    )
    await store.update_run(run.run_id, RunState.RUNNING)

    await manager.reconcile()

    assert f"auto-resume:{run.run_id}" not in manager.background
    assert requested.created_sessions == []
    reconciled = await store.get_run(run.run_id)
    assert reconciled is not None and reconciled.state is RunState.PAUSED


async def test_capability_terminal_names_the_executing_provider(tmp_path: Path) -> None:
    """Governance attributes the capability to the runtime that spent it.

    The run is requested on FAKE; the node executes on a DETERMINISTIC session. Every
    audit event the gateway writes for that call --- the request, the start and the
    terminal --- must say DETERMINISTIC, because that is who acted.
    """

    store, manager, _requested, executing, _invoker, _handlers = (
        await setup_capability_stack(tmp_path)
    )
    run, lease, session = await setup_capability_run(
        tmp_path, store, manager, executing, allowed=[PROBE_CAPABILITY]
    )
    task_id = run.task_id
    template = await store.get_workflow_template("fixed-graph-v1")
    assert template is not None
    policy = AcceptancePolicy(
        policy_id=new_id("acceptance_policy"),
        required_verifiers=["output-contract"],
        require_human_if_risk_gte=None,
        outcome_check="unused by a TOOL node",
    )
    spec = WorkflowNodeSpec(
        key="gather",
        kind=GraphNodeKind.TOOL,
        label="Gather",
        instruction="collect the seam evidence",
        capability_refs=[PROBE_CAPABILITY],
    )
    node = RunNode(node_id=spec.key, key=spec.key, kind=spec.kind, label=spec.label)

    outcome, _session = await manager._run_graph_node(
        run,
        await manager._require_task(task_id),
        lease,
        session,
        node=node,
        template_node=spec,
        template=template,
        gate=None,
        policy=policy,
        deadline=datetime.now(UTC).timestamp() + 60,
        cursor=_GraphCursor(statuses={}, entered_via={}, current_key=spec.key),
    )

    assert outcome.value == "SUCCESS"
    events = await store.list_events(run.run_id)
    attributed = {
        event.normalized_type: event.provider
        for event in events
        if event.payload.get("capability_id") == PROBE_CAPABILITY
    }
    assert attributed == {
        EventType.TOOL_REQUESTED: EXECUTING,
        EventType.TOOL_STARTED: EXECUTING,
        EventType.TOOL_COMPLETED: EXECUTING,
    }, "the gateway attributed the call to the requested provider, not the executing one"
    results = await store.list_capability_results(run.run_id)
    assert [result.status.value for result in results] == ["SUCCEEDED"]


async def test_every_capability_terminal_names_the_executing_provider(
    tmp_path: Path,
) -> None:
    """Not just the happy path: every way a governed call can end.

    The gateway names a provider in eight places, and a success touches three of them.
    The audit trail for a capability that was denied, gated behind an approval,
    collapsed onto a prior idempotent operation or failed outright is exactly the
    trail an operator reads when something went wrong, so each of those terminals is
    driven here by a real policy decision and its event checked by itself. Events are
    isolated per call through ``tool_call_id``, which is the request id the gateway
    stamps on everything it writes for one execution.
    """

    store, manager, _requested, executing, invoker, handlers = (
        await setup_capability_stack(tmp_path)
    )
    run, _lease, _session = await setup_capability_run(
        tmp_path,
        store,
        manager,
        executing,
        allowed=[PROBE_CAPABILITY, GUARDED_CAPABILITY, WRITE_CAPABILITY, FAILING_CAPABILITY],
    )

    async def spend(
        capability_id: str, *, node_id: str, idempotency_key: str | None = None
    ) -> tuple[CapabilityExecutionResult, dict[EventType, Provider]]:
        result = await invoker(
            run_id=run.run_id,
            node_id=node_id,
            capability_id=capability_id,
            arguments={"query": "seam"},
            executing_provider=EXECUTING,
            idempotency_key=idempotency_key,
        )
        assert result is not None
        return result, await attributed_events(store, invoker, run.run_id)

    # (a) A capability id the registry has never heard of: denied before any event
    # announcing the request, so the terminal is the only place a provider is named.
    unknown, attributed = await spend(ABSENT_CAPABILITY, node_id="gather-unknown")
    assert unknown.status is CapabilityExecutionStatus.DENIED
    assert unknown.error is not None and unknown.error.code == "CAPABILITY_UNKNOWN"
    assert attributed == {EventType.TOOL_FAILED: EXECUTING}

    # (b) A registered capability the task never allowed: denied by the policy engine.
    denied, attributed = await spend(DENIED_CAPABILITY, node_id="gather-denied")
    assert denied.status is CapabilityExecutionStatus.DENIED
    assert denied.error is not None and denied.error.code == "CAPABILITY_DENIED"
    assert handlers[DENIED_CAPABILITY].calls == 0
    assert attributed == {
        EventType.TOOL_REQUESTED: EXECUTING,
        EventType.TOOL_FAILED: EXECUTING,
    }

    # (c) A high-risk capability with no operator approval on file.
    gated, attributed = await spend(GUARDED_CAPABILITY, node_id="gather-guarded")
    assert gated.status is CapabilityExecutionStatus.REQUIRES_APPROVAL
    assert handlers[GUARDED_CAPABILITY].calls == 0
    assert attributed == {
        EventType.TOOL_REQUESTED: EXECUTING,
        EventType.APPROVAL_REQUIRED: EXECUTING,
    }

    # (d) The same side-effecting request replayed on one idempotency key. The first
    # call opens an approval, the second spends it, and the third must collapse onto
    # the recorded operation instead of running the handler a second time.
    key = "seam-idempotency-key"
    first, _ = await spend(WRITE_CAPABILITY, node_id="gather-write", idempotency_key=key)
    assert first.status is CapabilityExecutionStatus.REQUIRES_APPROVAL
    assert first.authorization.approval_id is not None
    await store.decide_approval(first.authorization.approval_id, ApprovalDecisionValue.APPROVE)
    second, _ = await spend(WRITE_CAPABILITY, node_id="gather-write", idempotency_key=key)
    assert second.status is CapabilityExecutionStatus.SUCCEEDED
    assert handlers[WRITE_CAPABILITY].calls == 1
    duplicate, attributed = await spend(
        WRITE_CAPABILITY, node_id="gather-write", idempotency_key=key
    )
    assert duplicate.status is CapabilityExecutionStatus.SUCCEEDED
    assert duplicate.side_effect_operation_id == second.side_effect_operation_id
    assert handlers[WRITE_CAPABILITY].calls == 1, "the duplicate re-ran the side effect"
    assert attributed == {
        EventType.TOOL_REQUESTED: EXECUTING,
        EventType.TOOL_COMPLETED: EXECUTING,
    }

    # (e) An allowed capability whose backend raises.
    failed, attributed = await spend(FAILING_CAPABILITY, node_id="gather-failing")
    assert failed.status is CapabilityExecutionStatus.FAILED
    assert failed.error is not None and failed.error.code == "CAPABILITY_EXECUTION_FAILED"
    assert handlers[FAILING_CAPABILITY].calls == 1
    assert attributed == {
        EventType.TOOL_REQUESTED: EXECUTING,
        EventType.TOOL_STARTED: EXECUTING,
        EventType.TOOL_FAILED: EXECUTING,
    }

    # Nothing on this run was ever attributed to the provider that was merely asked for.
    assert not [
        event
        for event in await store.list_events(run.run_id)
        if event.payload.get("capability_id") and event.provider is REQUESTED
    ]
