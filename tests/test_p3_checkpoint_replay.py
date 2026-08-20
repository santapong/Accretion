from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

from accretion.checkpoints import (
    CheckpointEvaluation,
    CheckpointInvalidReason,
    ReconcileClassification,
    classify_run,
    evaluate_checkpoint,
)
from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    AcceptancePolicy,
    Checkpoint,
    CheckpointKind,
    CheckpointLoopCursor,
    EventType,
    ExecutionMode,
    LoopBudgetRemaining,
    LoopExecutionStatus,
    LoopIteration,
    LoopIterationStatus,
    LoopState,
    LoopStopReason,
    Provider,
    Run,
    RunState,
    SessionConfig,
    SessionRef,
    TaskType,
)
from accretion.ids import new_id
from accretion.looping import build_loop_execution, build_loop_spec
from accretion.persistence.store import MemoryStore
from accretion.runtimes.fake import FakeCallOutcome, FakeRuntime
from accretion.services.run_manager import RunManager
from accretion.templates import seed_templates
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
    runtime: FakeRuntime,
    *,
    auto_resume: bool = False,
) -> RunManager:
    return RunManager(
        store=store,
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: runtime},
        limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
        live_providers_enabled=False,
        auto_resume_on_reconcile=auto_resume,
    )


def write_valid(session: SessionRef, _request: object) -> None:
    (session.workspace / "result.json").write_text('{"ok": true}\n')


async def crashed_loop_fixture(
    tmp_path: Path, *, with_checkpoint: bool
) -> tuple[MemoryStore, RunManager, Run]:
    """A RUNNING loop run with one committed iteration, as a crash leaves it."""

    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    store = MemoryStore()
    await seed_templates(store)
    bootstrap = build_manager(tmp_path, store, FakeRuntime())
    project = await bootstrap.create_project("fixture", repository)
    task = await bootstrap.create_task(
        project_id=project.project_id,
        objective="Recover after restart.",
        task_patch={
            "task_type": TaskType.IMPLEMENT,
            "success_criteria": ["result.json is valid"],
            "required_outputs": [{"path": "result.json", "kind": "json"}],
            "budgets": {"max_loop_iterations": 3},
        },
    )
    planning = await bootstrap.get_task_planning(task.envelope.task_id)
    policy = AcceptancePolicy(
        policy_id=new_id("acceptance_policy"),
        required_verifiers=["output-contract", "trajectory-policy"],
    )
    await store.save_acceptance_policy(policy)
    run = await store.create_run(
        Run(
            run_id=new_id("run"),
            task_id=task.envelope.task_id,
            project_id=project.project_id,
            provider=Provider.FAKE,
            state=RunState.RUNNING,
            strategy_decision_id=planning.current_decision.decision_id,
            execution_mode=ExecutionMode.LOOP,
            workflow_template_id="feedback-loop-v1",
            acceptance_policy_id=policy.policy_id,
        )
    )
    execution = build_loop_execution(
        run_id=run.run_id,
        spec=build_loop_spec(task.envelope, policy.required_verifiers),
        policy=policy,
    )
    execution = await store.create_loop_execution(execution)
    lease = await bootstrap.worktrees.acquire(
        project_id=project.project_id, run_id=run.run_id, repository=repository
    )
    await store.save_lease(lease)
    session = await bootstrap.runtimes[Provider.FAKE].create_session(
        SessionConfig(run_id=run.run_id, workspace=lease.path)
    )
    await store.save_session(session)
    run = await store.update_run(
        run.run_id,
        RunState.RUNNING,
        session_id=session.session_id,
        workspace_lease_id=lease.lease_id,
    )
    iteration = LoopIteration(
        iteration_id=new_id("iteration"),
        loop_execution_id=execution.loop_execution_id,
        run_id=run.run_id,
        number=1,
        status=LoopIterationStatus.FAILED,
        output_fingerprint="candidate-one",
        finding_signature="failure-one",
        turns=1,
    )
    next_state = LoopState(
        iteration=1,
        latest_observation_ref=iteration.iteration_id,
        repeated_failure_signature="failure-one",
        repeated_failure_count=1,
        budget_remaining=LoopBudgetRemaining(
            wall_time_seconds=execution.spec.max_wall_time_seconds,
            tool_calls=execution.spec.max_tool_calls,
            turns=execution.spec.max_turns - 1,
            iterations=execution.spec.max_iterations - 1,
        ),
    )
    checkpoint = (
        Checkpoint(
            checkpoint_id=new_id("checkpoint"),
            run_id=run.run_id,
            kind=CheckpointKind.NODE_BOUNDARY,
            sequence=0,
            run_state=RunState.RUNNING,
            run_revision=run.revision,
            active_node_ids=[f"{run.run_id}:evaluate"],
            loop_cursors=[
                CheckpointLoopCursor(
                    loop_execution_id=execution.loop_execution_id,
                    iteration=1,
                    revision=execution.revision + 1,
                    status=LoopExecutionStatus.RUNNING,
                )
            ],
            workspace_lease_id=lease.lease_id,
            workspace_revision=lease.base_revision,
        )
        if with_checkpoint
        else None
    )
    await store.append_loop_iteration(
        execution.loop_execution_id,
        iteration,
        next_state,
        status=LoopExecutionStatus.RUNNING,
        expected_revision=execution.revision,
        events=[
            bootstrap._control_event(  # noqa: SLF001
                run,
                "fixture/iteration-completed",
                EventType.LOOP_ITERATION_COMPLETED,
                session_id=session.session_id,
                node_key="evaluate",
                payload={"iteration_id": iteration.iteration_id, "number": 1},
            )
        ],
        checkpoint=checkpoint,
    )
    return store, bootstrap, run


async def test_reconcile_auto_resumes_from_last_valid_checkpoint(tmp_path: Path) -> None:
    store, bootstrap, run = await crashed_loop_fixture(tmp_path, with_checkpoint=True)
    stored_checkpoint = await store.get_latest_checkpoint(run.run_id)
    assert stored_checkpoint is not None
    assert stored_checkpoint.sequence == run.last_sequence + 1  # includes the event

    replacement = build_manager(
        tmp_path,
        store,
        FakeRuntime(scripted_outcomes=[FakeCallOutcome(hook=write_valid)]),
        auto_resume=True,
    )
    await replacement.reconcile()

    paused_events = [
        event
        for event in await store.list_events(run.run_id)
        if event.normalized_type is EventType.RUN_PAUSED
    ]
    assert paused_events, "the durable PAUSED landing must precede auto-resume"

    auto_task = replacement.background.get(f"auto-resume:{run.run_id}")
    assert auto_task is not None, "a valid checkpoint must schedule auto-resume"
    await asyncio.wait_for(auto_task, 5)
    worker = replacement.background.get(run.run_id)
    if worker is not None:
        await asyncio.wait_for(worker, 5)

    completed = await store.get_run(run.run_id)
    assert completed is not None and completed.state is RunState.SUCCEEDED
    execution = await store.get_loop_execution_for_run(run.run_id)
    assert execution is not None
    assert execution.state.iteration == 2
    assert execution.stop_reason is LoopStopReason.VERIFIED_SUCCESS


async def test_p2_recovery_behavior_unchanged_without_checkpoints(tmp_path: Path) -> None:
    store, bootstrap, run = await crashed_loop_fixture(tmp_path, with_checkpoint=False)
    replacement = build_manager(tmp_path, store, FakeRuntime(), auto_resume=True)
    await replacement.reconcile()

    reconciled = await store.get_run(run.run_id)
    assert reconciled is not None and reconciled.state is RunState.PAUSED
    assert f"auto-resume:{run.run_id}" not in replacement.background


async def test_workspace_revision_conflict_invalidates_checkpoint(tmp_path: Path) -> None:
    store, bootstrap, run = await crashed_loop_fixture(tmp_path, with_checkpoint=True)
    lease = await store.get_lease((await store.get_run(run.run_id)).workspace_lease_id)
    assert lease is not None
    (lease.path / "drift.txt").write_text("post-crash mutation\n")
    subprocess.run(["git", "-C", str(lease.path), "add", "drift.txt"], check=True)
    subprocess.run(["git", "-C", str(lease.path), "commit", "-qm", "drift"], check=True)
    assert await bootstrap.worktrees.inspect(lease) == "REVISION_CONFLICT"

    replacement = build_manager(tmp_path, store, FakeRuntime(), auto_resume=True)
    await replacement.reconcile()

    escalated = await store.get_run(run.run_id)
    assert escalated is not None and escalated.state is RunState.REQUIRES_HUMAN
    assert escalated.error is not None
    assert "CHECKPOINT_INVALID" in escalated.error.message


async def test_corrupt_checkpoint_sequence_fails_closed(tmp_path: Path) -> None:
    store, bootstrap, run = await crashed_loop_fixture(tmp_path, with_checkpoint=False)
    # Inject corruption directly: a checkpoint claiming a cut beyond the log.
    current = await store.get_run(run.run_id)
    assert current is not None
    store.checkpoints[run.run_id] = [
        Checkpoint(
            checkpoint_id=new_id("checkpoint"),
            run_id=run.run_id,
            kind=CheckpointKind.NODE_BOUNDARY,
            sequence=current.last_sequence + 50,
            run_state=RunState.RUNNING,
            run_revision=current.revision,
        )
    ]
    replacement = build_manager(tmp_path, store, FakeRuntime(), auto_resume=True)
    await replacement.reconcile()

    escalated = await store.get_run(run.run_id)
    assert escalated is not None and escalated.state is RunState.REQUIRES_HUMAN
    assert escalated.error is not None
    assert "SEQUENCE_AHEAD_OF_LOG" in escalated.error.message


async def test_reconcile_recreates_workspace_when_no_candidate_work_lost(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    store = MemoryStore()
    await seed_templates(store)
    manager = build_manager(tmp_path, store, FakeRuntime())
    project = await manager.create_project("fixture", repository)
    task = await manager.create_task(
        project_id=project.project_id, objective="interrupted", task_patch={}
    )
    run = await store.create_run(
        Run(
            run_id=new_id("run"),
            task_id=task.envelope.task_id,
            project_id=project.project_id,
            provider=Provider.FAKE,
            state=RunState.RUNNING,
            execution_mode=ExecutionMode.DIRECT,
            workflow_template_id="direct-v1",
        )
    )
    lease = await manager.worktrees.acquire(
        project_id=project.project_id, run_id=run.run_id, repository=repository
    )
    await store.save_lease(lease)
    await store.update_run(run.run_id, RunState.RUNNING, workspace_lease_id=lease.lease_id)
    shutil.rmtree(lease.path)

    await manager.reconcile()

    recovered = await store.get_run(run.run_id)
    assert recovered is not None and recovered.state is RunState.PAUSED
    assert recovered.workspace_lease_id != lease.lease_id
    fresh = await store.get_lease(recovered.workspace_lease_id)
    assert fresh is not None
    assert fresh.path.exists()
    assert fresh.base_revision == lease.base_revision
    classified = [
        event
        for event in await store.list_events(run.run_id)
        if event.payload.get("classification")
    ]
    assert classified and classified[-1].payload["classification"] == "recreate"


async def test_restart_reproduces_projection_and_trace(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    store = MemoryStore()
    await seed_templates(store)
    runtime = FakeRuntime(scripted_outcomes=[FakeCallOutcome(hook=write_valid)])
    manager = build_manager(tmp_path, store, runtime)
    project = await manager.create_project("fixture", repository)
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Deterministic review with a bounded result.",
        task_patch={
            "task_type": TaskType.REVIEW,
            "required_outputs": [{"path": "result.json", "kind": "json"}],
        },
    )
    run = await manager.start_run(task.envelope.task_id, Provider.FAKE)
    await asyncio.wait_for(manager.background[run.run_id], 10)
    final = await store.get_run(run.run_id)
    assert final is not None and final.state is RunState.SUCCEEDED

    graph_before = await manager.get_graph(run.run_id)
    trace_before = await manager.get_trace(run.run_id)

    restarted = build_manager(tmp_path, store, FakeRuntime())
    await restarted.reconcile()
    graph_after = await restarted.get_graph(run.run_id)
    trace_after = await restarted.get_trace(run.run_id)

    exclude = {"generated_at"}
    assert graph_after.model_dump(mode="json", exclude=exclude) == graph_before.model_dump(
        mode="json", exclude=exclude
    )
    assert trace_after.model_dump(mode="json", exclude=exclude) == trace_before.model_dump(
        mode="json", exclude=exclude
    )
    assert trace_after.checkpoints, "the trace must carry checkpoint evidence"
    assert trace_after.terminal_state is RunState.SUCCEEDED


def test_evaluate_checkpoint_pure_rules() -> None:
    checkpoint = Checkpoint(
        checkpoint_id=new_id("checkpoint"),
        run_id=new_id("run"),
        kind=CheckpointKind.NODE_BOUNDARY,
        sequence=5,
        run_state=RunState.RUNNING,
        run_revision=1,
    )
    ahead = evaluate_checkpoint(
        checkpoint, run_last_sequence=4, loop_executions={}, workspace_status=None
    )
    assert not ahead.valid
    assert ahead.reason is CheckpointInvalidReason.SEQUENCE_AHEAD_OF_LOG

    ok = evaluate_checkpoint(
        checkpoint, run_last_sequence=9, loop_executions={}, workspace_status=None
    )
    assert ok.valid and not ok.stale


def test_classify_run_pure_rules() -> None:
    escalated = classify_run(
        workspace_status="MISSING",
        has_uncertain_side_effects=True,
        has_candidate_work=False,
        checkpoint_evaluation=None,
    )
    assert escalated.classification is ReconcileClassification.REQUIRES_HUMAN

    recreate = classify_run(
        workspace_status="MISSING",
        has_uncertain_side_effects=False,
        has_candidate_work=False,
        checkpoint_evaluation=None,
    )
    assert recreate.classification is ReconcileClassification.RECREATE

    lossy = classify_run(
        workspace_status="MISSING",
        has_uncertain_side_effects=False,
        has_candidate_work=True,
        checkpoint_evaluation=None,
    )
    assert lossy.classification is ReconcileClassification.REQUIRES_HUMAN

    resumable = classify_run(
        workspace_status="CONSISTENT",
        has_uncertain_side_effects=False,
        has_candidate_work=True,
        checkpoint_evaluation=CheckpointEvaluation(valid=True),
    )
    assert resumable.classification is ReconcileClassification.RESUMABLE
