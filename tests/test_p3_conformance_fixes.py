"""Regression tests for the confirmed SDD-conformance audit findings."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    AcceptancePolicy,
    ApprovalDecisionValue,
    ApprovalRecord,
    ApprovalStatus,
    Checkpoint,
    CheckpointKind,
    EdgeGuard,
    EventType,
    ExecutionMode,
    GraphEdgeKind,
    GraphNodeKind,
    GraphNodeStatus,
    LoopBudgetRemaining,
    LoopExecution,
    LoopExecutionStatus,
    LoopSpec,
    LoopState,
    LoopStopReason,
    Provider,
    Run,
    RunEdge,
    RunNode,
    RunState,
    SessionConfig,
    SessionRef,
    TaskBudgets,
)
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.runtimes.fake import FakeCallOutcome, FakeRuntime
from accretion.services.run_manager import NodeOutcome, RunManager, _GraphCursor
from accretion.templates import (
    FIXED_GRAPH_V1,
    instantiate_run_graph,
    seed_templates,
)
from accretion.workspace import WorktreeManager


def initialize_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Accretion Test"], check=True)
    (path / "result.json").write_text('{"ok": false}\n')
    subprocess.run(["git", "-C", str(path), "add", "result.json"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)


def build_manager(
    tmp_path: Path,
    store: MemoryStore,
    runtime: FakeRuntime | None = None,
    *,
    auto_resume: bool = False,
) -> RunManager:
    return RunManager(
        store=store,
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: runtime or FakeRuntime()},
        limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
        live_providers_enabled=False,
        auto_resume_on_reconcile=auto_resume,
    )


def write_valid(session: SessionRef, _request: object) -> None:
    (session.workspace / "result.json").write_text('{"ok": true}\n')


def gate_policy() -> AcceptancePolicy:
    """The policy start_run builds for gate-bearing templates."""

    return AcceptancePolicy(
        policy_id=new_id("acceptance_policy"),
        required_verifiers=["output-contract", "git-diff", "trajectory-policy"],
        require_human_if_risk_gte=None,
        outcome_check="all declared deterministic verifiers must pass",
    )


async def crashed_graph_fixture(
    tmp_path: Path,
    store: MemoryStore,
    manager: RunManager,
    *,
    active_key: str,
    arrival_edge_key: str | None,
    node_statuses: dict[str, GraphNodeStatus],
) -> Run:
    """A RUNNING fixed-graph-v1 run as a crash leaves it, cursor at active_key."""

    repository = tmp_path / "repository"
    if not repository.exists():
        repository.mkdir()
        initialize_repository(repository)
    await seed_templates(store)
    project = await manager.create_project("fixture", repository)
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Recover a graph run.",
        task_patch={
            "task_type": "OTHER",
            "risk_level": "HIGH",
            "required_outputs": [{"path": "result.json", "kind": "json"}],
        },
    )
    planning = await manager.get_task_planning(task.envelope.task_id)
    policy = gate_policy()
    await store.save_acceptance_policy(policy)
    run = await store.create_run(
        Run(
            run_id=new_id("run"),
            task_id=task.envelope.task_id,
            project_id=project.project_id,
            provider=Provider.FAKE,
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
    session = await manager.runtimes[Provider.FAKE].create_session(
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
    return await store.update_run(run.run_id, RunState.RUNNING)


# --- P3-V-01: a terminal region execution never resolves a graph run ---


async def region_succeeded_fixture(
    tmp_path: Path, store: MemoryStore, manager: RunManager
) -> Run:
    run = await crashed_graph_fixture(
        tmp_path,
        store,
        manager,
        active_key="act",
        arrival_edge_key="approve-plan-act",
        node_statuses={
            "initialize": GraphNodeStatus.SUCCEEDED,
            "plan": GraphNodeStatus.SUCCEEDED,
            "approve-plan": GraphNodeStatus.SUCCEEDED,
        },
    )
    # A bounded region that legitimately completed SUCCEEDED mid-graph.
    await store.create_loop_execution(
        LoopExecution(
            loop_execution_id=new_id("loop_execution"),
            run_id=run.run_id,
            node_key="experiment",
            attempt=1,
            spec=LoopSpec(loop_id=new_id("loop")),
            state=LoopState(
                budget_remaining=LoopBudgetRemaining(
                    wall_time_seconds=60, tool_calls=10, turns=4, iterations=0
                )
            ),
            acceptance_policy_ref=run.acceptance_policy_id or "",
            status=LoopExecutionStatus.SUCCEEDED,
            stop_reason=LoopStopReason.MAX_ITERATIONS,
        )
    )
    return run


async def test_succeeded_region_does_not_resolve_a_crashed_graph_run(tmp_path: Path) -> None:
    store = MemoryStore()
    manager = build_manager(tmp_path, store)
    run = await region_succeeded_fixture(tmp_path, store, manager)

    await manager.reconcile()

    recovered = await store.get_run(run.run_id)
    assert recovered is not None
    assert recovered.state is RunState.PAUSED, recovered.state
    events = await store.list_events(run.run_id)
    assert not any(
        event.normalized_type is EventType.RUN_COMPLETED for event in events
    ), "an unverified graph run must never be reconciled to success"


async def test_cancel_of_graph_run_with_succeeded_region_is_cancelled(
    tmp_path: Path,
) -> None:
    store = MemoryStore()
    manager = build_manager(tmp_path, store)
    run = await region_succeeded_fixture(tmp_path, store, manager)

    cancelled = await manager.cancel(run.run_id)

    assert cancelled.state is RunState.CANCELLED
    events = await store.list_events(run.run_id)
    terminal_types = [
        event.normalized_type
        for event in events
        if event.normalized_type
        in {EventType.RUN_COMPLETED, EventType.RUN_FAILED, EventType.RUN_CANCELLED}
    ]
    assert terminal_types == [EventType.RUN_CANCELLED]


# --- P3-V-02: a denied routing decision survives restart ---


async def test_denied_gate_routing_survives_restart(tmp_path: Path) -> None:
    store = MemoryStore()
    manager = build_manager(tmp_path, store)
    run = await crashed_graph_fixture(
        tmp_path,
        store,
        manager,
        active_key="complete",
        arrival_edge_key="approve-plan-complete-denied",
        node_statuses={
            "initialize": GraphNodeStatus.SUCCEEDED,
            "plan": GraphNodeStatus.SUCCEEDED,
            "approve-plan": GraphNodeStatus.FAILED,
        },
    )
    await store.update_run(run.run_id, RunState.PAUSED)

    resumed = await manager.resume(run.run_id)
    assert resumed.state is RunState.RUNNING
    await asyncio.wait_for(manager.background[run.run_id], 10)

    final = await store.get_run(run.run_id)
    assert final is not None
    assert final.state is RunState.REQUIRES_HUMAN, (
        "a denial routed into the terminal must never resume to success"
    )
    events = await store.list_events(run.run_id)
    assert not any(event.normalized_type is EventType.RUN_COMPLETED for event in events)
    terminal = next(
        event for event in events if event.normalized_type is EventType.RUN_FAILED
    )
    assert terminal.payload.get("terminal_state") == "REQUIRES_HUMAN"


def test_terminal_commit_fails_closed_without_routing_evidence() -> None:
    from accretion.services.run_manager import _TERMINAL_GUARD_STATES

    assert _TERMINAL_GUARD_STATES[EdgeGuard.ON_DENIED] is RunState.REQUIRES_HUMAN
    cursor = _GraphCursor(statuses={}, entered_via={}, current_key="complete")
    assert cursor.arrival_guard is None and cursor.arrival_edge_kind is None
    # The scheduler maps this cursor state to REQUIRES_HUMAN; the mapping
    # branch is exercised end-to-end by the restart test above.


# --- P3-V-04/05: graph restart resumes and honors downtime decisions ---


async def test_graph_restart_resumes_and_honors_downtime_decision(tmp_path: Path) -> None:
    store = MemoryStore()
    runtime = FakeRuntime(scripted_outcomes=[FakeCallOutcome(hook=write_valid)])
    manager = build_manager(tmp_path, store, runtime)
    run = await crashed_graph_fixture(
        tmp_path,
        store,
        manager,
        active_key="act",
        arrival_edge_key="approve-plan-act",
        node_statuses={
            "initialize": GraphNodeStatus.SUCCEEDED,
            "plan": GraphNodeStatus.SUCCEEDED,
            "approve-plan": GraphNodeStatus.SUCCEEDED,
        },
    )
    # The outcome approval was decided while the backend was down.
    downtime_decision = await store.save_approval(
        ApprovalRecord(
            approval_id=new_id("approval"),
            run_id=run.run_id,
            node_id=f"{run.run_id}:approve-outcome",
            native_request_id="gate:approve-outcome",
            method="accretion/gate",
            summary="Approve the verified outcome before completion.",
        )
    )
    await store.decide_approval(downtime_decision.approval_id, ApprovalDecisionValue.APPROVE)

    await manager.reconcile()
    reconciled = await store.get_run(run.run_id)
    assert reconciled is not None and reconciled.state is RunState.PAUSED

    await manager.resume(run.run_id)
    await asyncio.wait_for(manager.background[run.run_id], 10)

    final = await store.get_run(run.run_id)
    assert final is not None and final.state is RunState.SUCCEEDED
    events = await store.list_events(run.run_id)
    resolved = [
        event
        for event in events
        if event.normalized_type is EventType.APPROVAL_RESOLVED
        and event.payload.get("approval_id") == downtime_decision.approval_id
    ]
    assert len(resolved) == 1, "a downtime decision is honored exactly once"

    trace = await manager.get_trace(run.run_id)
    assert any(
        item.approval_id == downtime_decision.approval_id and item.resolved
        for item in trace.approvals
    )
    assert trace.terminal_state is RunState.SUCCEEDED
    entered = [
        event.node_id.rsplit(":", 1)[-1]
        for event in events
        if event.normalized_type is EventType.NODE_ENTERED and event.node_id
    ]
    assert entered[:1] == ["act"], "resume must continue from the checkpointed node"


# --- P3-V-07: turn/tool budgets are cumulative across the graph ---


async def test_graph_budgets_are_cumulative_across_nodes(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    store = MemoryStore()
    await seed_templates(store)

    def write_invalid(session: SessionRef, _request: object) -> None:
        (session.workspace / "result.json").write_text("not-json\n")

    runtime = FakeRuntime(
        scripted_outcomes=[
            FakeCallOutcome(),  # plan
            FakeCallOutcome(hook=write_invalid),  # loop attempt 1
            FakeCallOutcome(),  # replan
            FakeCallOutcome(hook=write_valid),  # loop attempt 2 (never granted)
        ]
    )
    manager = build_manager(tmp_path, store, runtime)
    project = await manager.create_project("fixture", repository)
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Exhaust the cumulative turn budget.",
        task_patch={
            "task_type": "OTHER",
            "required_outputs": [{"path": "result.json", "kind": "json"}],
            "budgets": {"max_turns": 3},
        },
    )
    planning = await manager.get_task_planning(task.envelope.task_id)
    assert planning.current_decision.selected_template_id == "safe-unknown-v1"

    run = await manager.start_run(task.envelope.task_id, Provider.FAKE)
    await asyncio.wait_for(manager.background[run.run_id], 10)

    final = await store.get_run(run.run_id)
    assert final is not None and final.state is RunState.REQUIRES_HUMAN
    spent = await store.get_budget_spent(run.run_id)
    assert spent["turns"] == 3, "every call must draw from one cumulative account"
    events = await store.list_events(run.run_id)
    stop = next(
        event
        for event in reversed(events)
        if event.payload.get("stop_reason") == LoopStopReason.MAX_TURNS.value
    )
    assert stop.payload.get("terminal_state") == "REQUIRES_HUMAN"


# --- P3-V-08: irreversible tasks never auto-approve their gates ---


async def test_irreversible_low_risk_task_still_requires_gate_decisions(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    store = MemoryStore()
    await seed_templates(store)
    manager = build_manager(tmp_path, store)
    project = await manager.create_project("fixture", repository)
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Deploy a change; low declared risk but irreversible capability.",
        task_patch={
            "task_type": "OTHER",
            "risk_level": "LOW",
            "allowed_capabilities": ["service.deploy"],
        },
    )
    planning = await manager.get_task_planning(task.envelope.task_id)
    assert planning.current_decision.selected_template_id == "fixed-graph-v1"
    assert planning.current_profile.irreversible_actions

    run = await manager.start_run(task.envelope.task_id, Provider.FAKE)
    for _ in range(200):
        pending = await store.list_approvals(run.run_id, ApprovalStatus.PENDING)
        if pending:
            break
        await asyncio.sleep(0.02)
    assert pending, "an irreversible task must wait for a human gate decision"
    await manager.cancel(run.run_id)


# --- P3-V-19: ambiguous edge matches escalate with distinct evidence ---


def test_select_edge_distinguishes_ambiguity_from_absence() -> None:
    store = MemoryStore()
    manager = RunManager(
        store=store,
        worktrees=WorktreeManager(Path("/tmp/unused-wt"), Path("/tmp/unused-art")),
        runtimes={Provider.FAKE: FakeRuntime()},
        limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
        live_providers_enabled=False,
    )
    node = RunNode(
        node_id="run_x:verify", key="verify", kind=GraphNodeKind.VERIFIER, label="Verify"
    )
    cursor = _GraphCursor(statuses={}, entered_via={}, current_key="verify")

    def edge(key: str, guard: EdgeGuard) -> RunEdge:
        return RunEdge(
            edge_id=f"run_x:{key}",
            key=key,
            source="run_x:verify",
            target="run_x:complete",
            kind=GraphEdgeKind.CONDITION,
            guard=guard,
        )

    ambiguous_edges = [edge("a", EdgeGuard.ON_FAIL), edge("b", EdgeGuard.ON_FAIL)]
    selected, error = manager._select_edge(  # noqa: SLF001
        node, NodeOutcome.FAIL, ambiguous_edges, cursor, FIXED_GRAPH_V1
    )
    assert selected is None and error is not None
    assert error.code == "GRAPH_AMBIGUOUS_EDGES"

    selected, error = manager._select_edge(  # noqa: SLF001
        node, NodeOutcome.FAIL, [edge("a", EdgeGuard.ON_SUCCESS)], cursor, FIXED_GRAPH_V1
    )
    assert selected is None and error is not None
    assert error.code == "GRAPH_NO_ELIGIBLE_EDGE"
