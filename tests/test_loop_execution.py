from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

import pytest

from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    AcceptancePolicy,
    EventType,
    ExecutionMode,
    IterationDirectiveKind,
    LoopBudgetRemaining,
    LoopExecutionStatus,
    LoopIteration,
    LoopIterationStatus,
    LoopState,
    LoopStopReason,
    Provider,
    Run,
    RunState,
    RuntimeExecutionRequest,
    SessionConfig,
    TaskBudgets,
    TaskType,
    VerificationStatus,
)
from accretion.ids import new_id
from accretion.looping import build_loop_execution, build_loop_spec
from accretion.persistence.store import MemoryStore
from accretion.runtimes.fake import FakeCallOutcome, FakeRuntime
from accretion.services.run_manager import RunManager
from accretion.workspace import WorktreeManager


def initialize_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Accretion Test"],
        check=True,
    )
    (path / "result.json").write_text('{"ok": false}\n')
    subprocess.run(["git", "-C", str(path), "add", "result.json"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)


def manager(tmp_path: Path, runtime: FakeRuntime) -> tuple[RunManager, MemoryStore]:
    store = MemoryStore()
    return (
        RunManager(
            store=store,
            worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
            runtimes={Provider.FAKE: runtime},
            limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
            live_providers_enabled=False,
        ),
        store,
    )


async def create_loop_task(run_manager: RunManager, repository: Path):  # type: ignore[no-untyped-def]
    project = await run_manager.create_project("fixture", repository)
    task = await run_manager.create_task(
        project_id=project.project_id,
        objective="Investigate and produce a verified result.",
        task_patch={
            "task_type": TaskType.RESEARCH,
            "required_outputs": [{"path": "result.json", "kind": "json"}],
            "budgets": TaskBudgets(max_loop_iterations=3).model_dump(),
        },
    )
    planning = await run_manager.get_task_planning(task.envelope.task_id)
    assert planning.current_decision.selected_mode is ExecutionMode.LOOP
    return project, task, planning


@pytest.mark.acceptance("V01-P2-002")
async def test_bad_candidate_is_rejected_then_repaired_in_same_session(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    observed_session_ids: list[str] = []

    def write_bad(session, request) -> None:  # type: ignore[no-untyped-def]
        observed_session_ids.append(session.session_id)
        assert isinstance(request, RuntimeExecutionRequest)
        assert request.directive.kind is IterationDirectiveKind.INITIAL
        (session.workspace / "result.json").write_text("{invalid\n")

    def repair(session, request) -> None:  # type: ignore[no-untyped-def]
        observed_session_ids.append(session.session_id)
        assert isinstance(request, RuntimeExecutionRequest)
        assert request.directive.kind is IterationDirectiveKind.REPAIR
        assert {item.code for item in request.directive.findings} == {"OUTPUT_JSON_INVALID"}
        (session.workspace / "result.json").write_text('{"ok": true}\n')

    run_manager, store = manager(
        tmp_path,
        FakeRuntime(
            scripted_outcomes=[
                FakeCallOutcome(hook=write_bad),
                FakeCallOutcome(hook=repair),
            ]
        ),
    )
    project = await run_manager.create_project("fixture", repository)
    task = await run_manager.create_task(
        project_id=project.project_id,
        objective="Investigate and produce a verified result.",
        task_patch={
            "task_type": TaskType.RESEARCH,
            "success_criteria": ["result.json contains a boolean ok field"],
            "required_outputs": [
                {"path": "result.json", "kind": "json", "required_keys": ["ok"]}
            ],
            "budgets": TaskBudgets(max_loop_iterations=2).model_dump(),
        },
    )
    planning = await run_manager.get_task_planning(task.envelope.task_id)
    assert planning.current_decision.selected_mode.value == "LOOP"

    run = await run_manager.start_run(task.envelope.task_id, Provider.FAKE)
    await run_manager.background[run.run_id]

    completed = await store.get_run(run.run_id)
    assert completed and completed.state is RunState.SUCCEEDED
    execution = await store.get_loop_execution_for_run(run.run_id)
    assert execution and execution.state.iteration == 2
    assert execution.stop_reason and execution.stop_reason.value == "VERIFIED_SUCCESS"
    iterations = await store.list_loop_iterations(execution.loop_execution_id)
    assert len(iterations) == 2
    first_results = await store.list_verifications(run.run_id, iterations[0].iteration_id)
    second_results = await store.list_verifications(run.run_id, iterations[1].iteration_id)
    assert {result.status for result in first_results} == {
        VerificationStatus.FAIL,
        VerificationStatus.PASS,
    }
    assert {result.status for result in second_results} == {VerificationStatus.PASS}
    assert len(set(observed_session_ids)) == 1
    event_types = [event.normalized_type for event in await store.list_events(run.run_id)]
    assert event_types.count(EventType.LOOP_ITERATION_STARTED) == 2
    assert event_types.count(EventType.LOOP_ITERATION_COMPLETED) == 2
    assert event_types.count(EventType.RUN_COMPLETED) == 1


@pytest.mark.acceptance("V01-P2-004")
async def test_failed_candidate_stops_at_iteration_ceiling(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)

    def write_bad(session, _request) -> None:  # type: ignore[no-untyped-def]
        (session.workspace / "result.json").write_text("{invalid\n")

    run_manager, store = manager(
        tmp_path,
        FakeRuntime(
            scripted_outcomes=[FakeCallOutcome(hook=write_bad), FakeCallOutcome(hook=write_bad)]
        ),
    )
    project = await run_manager.create_project("fixture", repository)
    task = await run_manager.create_task(
        project_id=project.project_id,
        objective="Investigate a bounded result.",
        task_patch={
            "task_type": TaskType.RESEARCH,
            "required_outputs": [{"path": "result.json", "kind": "json"}],
            "budgets": TaskBudgets(max_loop_iterations=2).model_dump(),
        },
    )
    run = await run_manager.start_run(task.envelope.task_id, Provider.FAKE)
    await asyncio.wait_for(run_manager.background[run.run_id], 5)

    stopped = await store.get_run(run.run_id)
    execution = await store.get_loop_execution_for_run(run.run_id)
    assert stopped and stopped.state is RunState.REQUIRES_HUMAN
    assert execution and execution.stop_reason
    assert execution.stop_reason.value in {"MAX_ITERATIONS", "REPEATED_FAILURE"}
    assert execution.state.iteration <= 2


async def test_pause_persists_interrupted_attempt_and_consumes_iteration_budget(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    call_started = asyncio.Event()
    hold_call = asyncio.Event()

    async def block_call(_session, _request) -> None:  # type: ignore[no-untyped-def]
        call_started.set()
        await hold_call.wait()

    run_manager, store = manager(
        tmp_path,
        FakeRuntime(scripted_outcomes=[FakeCallOutcome(hook=block_call)]),
    )
    _project, task, _planning = await create_loop_task(run_manager, repository)
    run = await run_manager.start_run(task.envelope.task_id, Provider.FAKE)
    background = run_manager.background[run.run_id]
    await asyncio.wait_for(call_started.wait(), 2)

    paused = await run_manager.pause(run.run_id)
    await asyncio.gather(background, return_exceptions=True)

    assert paused.state is RunState.PAUSED
    execution = await store.get_loop_execution_for_run(run.run_id)
    assert execution is not None
    assert execution.status is LoopExecutionStatus.PAUSED
    assert execution.stop_reason is LoopStopReason.INTERRUPTED
    assert execution.state.iteration == 1
    assert execution.state.budget_remaining.iterations == 2
    assert execution.state.budget_remaining.turns == 19
    iterations = await store.list_loop_iterations(execution.loop_execution_id)
    assert [iteration.status for iteration in iterations] == [
        LoopIterationStatus.INTERRUPTED
    ]
    events = await store.list_events(run.run_id)
    assert sum(
        event.normalized_type is EventType.LOOP_ITERATION_COMPLETED for event in events
    ) == 1
    assert sum(event.normalized_type is EventType.RUN_PAUSED for event in events) == 1
    assert not any(
        event.normalized_type
        in {EventType.RUN_COMPLETED, EventType.RUN_FAILED, EventType.RUN_CANCELLED}
        for event in events
    )


@pytest.mark.acceptance("V01-P2-001")
async def test_cancellation_has_one_terminal_and_terminal_controls_are_idempotent(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    call_started = asyncio.Event()
    hold_call = asyncio.Event()

    async def block_call(_session, _request) -> None:  # type: ignore[no-untyped-def]
        call_started.set()
        await hold_call.wait()

    run_manager, store = manager(
        tmp_path,
        FakeRuntime(scripted_outcomes=[FakeCallOutcome(hook=block_call)]),
    )
    _project, task, _planning = await create_loop_task(run_manager, repository)
    run = await run_manager.start_run(task.envelope.task_id, Provider.FAKE)
    background = run_manager.background[run.run_id]
    await asyncio.wait_for(call_started.wait(), 2)

    cancelled, duplicate_cancel = await asyncio.gather(
        run_manager.cancel(run.run_id),
        run_manager.cancel(run.run_id),
    )
    await asyncio.gather(background, return_exceptions=True)
    assert cancelled.state is RunState.CANCELLED
    assert duplicate_cancel.state is RunState.CANCELLED

    before = await store.list_events(run.run_id)
    terminals = [
        event
        for event in before
        if event.normalized_type
        in {EventType.RUN_COMPLETED, EventType.RUN_FAILED, EventType.RUN_CANCELLED}
    ]
    assert [event.normalized_type for event in terminals] == [EventType.RUN_CANCELLED]
    execution = await store.get_loop_execution_for_run(run.run_id)
    assert execution is not None
    assert execution.status is LoopExecutionStatus.CANCELLED
    assert execution.stop_reason is LoopStopReason.OPERATOR_CANCELLED
    assert execution.state.iteration == 1
    assert execution.state.budget_remaining.iterations == 2
    iterations = await store.list_loop_iterations(execution.loop_execution_id)
    assert [iteration.status for iteration in iterations] == [
        LoopIterationStatus.CANCELLED
    ]

    assert (await run_manager.cancel(run.run_id)).state is RunState.CANCELLED
    assert (await run_manager.pause(run.run_id)).state is RunState.CANCELLED
    assert (await run_manager.resume(run.run_id)).state is RunState.CANCELLED
    assert await store.list_events(run.run_id) == before


@pytest.mark.acceptance("V01-P2-003")
async def test_reconcile_and_resume_preserve_committed_iteration_count(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    initial_runtime = FakeRuntime()
    first_manager, store = manager(tmp_path, initial_runtime)
    project, task, planning = await create_loop_task(first_manager, repository)
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
    lease = await first_manager.worktrees.acquire(
        project_id=project.project_id,
        run_id=run.run_id,
        repository=repository,
    )
    await store.save_lease(lease)
    session = await initial_runtime.create_session(
        SessionConfig(run_id=run.run_id, workspace=lease.path)
    )
    await store.save_session(session)
    run = await store.update_run(
        run.run_id,
        RunState.RUNNING,
        session_id=session.session_id,
        workspace_lease_id=lease.lease_id,
    )
    first_iteration = LoopIteration(
        iteration_id=new_id("iteration"),
        loop_execution_id=execution.loop_execution_id,
        run_id=run.run_id,
        number=1,
        status=LoopIterationStatus.FAILED,
        output_fingerprint="candidate-one",
        finding_signature="failure-one",
        turns=1,
    )
    first_state = LoopState(
        iteration=1,
        latest_observation_ref=first_iteration.iteration_id,
        repeated_failure_signature="failure-one",
        repeated_failure_count=1,
        budget_remaining=LoopBudgetRemaining(
            wall_time_seconds=execution.spec.max_wall_time_seconds,
            tool_calls=execution.spec.max_tool_calls,
            turns=execution.spec.max_turns - 1,
            iterations=execution.spec.max_iterations - 1,
        ),
    )
    execution = await store.append_loop_iteration(
        execution.loop_execution_id,
        first_iteration,
        first_state,
        status=LoopExecutionStatus.RUNNING,
        expected_revision=execution.revision,
        events=[
            first_manager._control_event(  # noqa: SLF001
                run,
                "fixture/iteration-completed",
                EventType.LOOP_ITERATION_COMPLETED,
                session_id=session.session_id,
                node_key="evaluate",
                payload={"iteration_id": first_iteration.iteration_id, "number": 1},
            )
        ],
    )

    def write_valid(session_ref, _request) -> None:  # type: ignore[no-untyped-def]
        (session_ref.workspace / "result.json").write_text('{"ok": true}\n')

    replacement_runtime = FakeRuntime(
        scripted_outcomes=[FakeCallOutcome(hook=write_valid)]
    )
    replacement = RunManager(
        store=store,
        worktrees=first_manager.worktrees,
        runtimes={Provider.FAKE: replacement_runtime},
        limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
        live_providers_enabled=False,
    )
    await replacement.reconcile()

    reconciled = await store.get_loop_execution(execution.loop_execution_id)
    reconciled_run = await store.get_run(run.run_id)
    assert reconciled is not None and reconciled.state.iteration == 1
    assert reconciled.status is LoopExecutionStatus.PAUSED
    assert reconciled_run is not None and reconciled_run.state is RunState.PAUSED
    assert any(
        event.normalized_type is EventType.RUN_PAUSED
        for event in await store.list_events(run.run_id)
    )

    resumed = await replacement.resume(run.run_id)
    assert resumed.state is RunState.RUNNING
    resumed_background = replacement.background[run.run_id]
    await asyncio.wait_for(resumed_background, 5)
    completed = await store.get_loop_execution(execution.loop_execution_id)
    completed_run = await store.get_run(run.run_id)
    assert completed is not None and completed.state.iteration == 2
    assert completed.stop_reason is LoopStopReason.VERIFIED_SUCCESS
    assert completed_run is not None and completed_run.state is RunState.SUCCEEDED


async def test_reconcile_finishes_a_durable_terminal_loop_exactly_once(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    run_manager, store = manager(tmp_path, FakeRuntime())
    project, task, planning = await create_loop_task(run_manager, repository)
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
    ).model_copy(
        update={
            "status": LoopExecutionStatus.SUCCEEDED,
            "stop_reason": LoopStopReason.VERIFIED_SUCCESS,
        }
    )
    execution = await store.create_loop_execution(execution)
    lease = await run_manager.worktrees.acquire(
        project_id=project.project_id,
        run_id=run.run_id,
        repository=repository,
    )
    await store.save_lease(lease)
    await store.update_run(
        run.run_id,
        RunState.RUNNING,
        workspace_lease_id=lease.lease_id,
    )

    await run_manager.reconcile()
    await run_manager.reconcile()

    reconciled_run = await store.get_run(run.run_id)
    reconciled_loop = await store.get_loop_execution(execution.loop_execution_id)
    events = await store.list_events(run.run_id)
    assert reconciled_run is not None and reconciled_run.state is RunState.SUCCEEDED
    assert reconciled_loop is not None
    assert reconciled_loop.status is LoopExecutionStatus.SUCCEEDED
    assert reconciled_loop.stop_reason is LoopStopReason.VERIFIED_SUCCESS
    assert sum(event.normalized_type is EventType.RUN_COMPLETED for event in events) == 1


async def test_resume_loop_without_recoverable_session_escalates_exactly_once(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    run_manager, store = manager(tmp_path, FakeRuntime())
    project, task, planning = await create_loop_task(run_manager, repository)
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
            state=RunState.PAUSED,
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
    ).model_copy(
        update={
            "status": LoopExecutionStatus.PAUSED,
            "stop_reason": LoopStopReason.INTERRUPTED,
        }
    )
    await store.create_loop_execution(execution)
    lease = await run_manager.worktrees.acquire(
        project_id=project.project_id,
        run_id=run.run_id,
        repository=repository,
    )
    await store.save_lease(lease)
    await store.update_run(
        run.run_id,
        RunState.PAUSED,
        workspace_lease_id=lease.lease_id,
    )

    resumed = await run_manager.resume(run.run_id)
    assert resumed.state is RunState.RUNNING
    await asyncio.wait_for(run_manager.background[run.run_id], 5)
    await run_manager.reconcile()
    assert (await run_manager.resume(run.run_id)).state is RunState.REQUIRES_HUMAN

    escalated = await store.get_run(run.run_id)
    escalated_loop = await store.get_loop_execution_for_run(run.run_id)
    events = await store.list_events(run.run_id)
    assert escalated is not None and escalated.state is RunState.REQUIRES_HUMAN
    assert escalated.error and escalated.error.code == "RUN_RECOVERY_REQUIRES_HUMAN"
    assert escalated_loop is not None
    assert escalated_loop.status is LoopExecutionStatus.REQUIRES_HUMAN
    assert escalated_loop.stop_reason is LoopStopReason.INTERRUPTED
    assert sum(event.normalized_type is EventType.RUN_FAILED for event in events) == 1
    assert not any(
        event.normalized_type in {EventType.RUN_COMPLETED, EventType.RUN_CANCELLED}
        for event in events
    )


async def test_requires_human_run_closes_event_wait_without_poll_delay(
    tmp_path: Path,
) -> None:
    run_manager, store = manager(tmp_path, FakeRuntime())
    run = await store.create_run(
        Run(
            run_id=new_id("run"),
            task_id=new_id("task"),
            project_id=new_id("project"),
            provider=Provider.FAKE,
            state=RunState.REQUIRES_HUMAN,
        )
    )

    await asyncio.wait_for(
        run_manager.wait_for_events(run.run_id, after=0, timeout_seconds=30),
        timeout=0.1,
    )


@pytest.mark.acceptance("V01-P2-004")
async def test_loop_wall_deadline_is_enforced_around_an_unbounded_runtime(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    call_started = asyncio.Event()
    never_release = asyncio.Event()

    async def block_forever(_session, _request) -> None:  # type: ignore[no-untyped-def]
        call_started.set()
        await never_release.wait()

    run_manager, store = manager(
        tmp_path,
        FakeRuntime(scripted_outcomes=[FakeCallOutcome(hook=block_forever)]),
    )
    project = await run_manager.create_project("fixture", repository)
    task = await run_manager.create_task(
        project_id=project.project_id,
        objective="Produce an answer within the persisted wall deadline.",
        task_patch={
            "task_type": TaskType.RESEARCH,
            "required_outputs": [{"path": "never-created.json", "kind": "json"}],
            "budgets": TaskBudgets(
                wall_time_seconds=1,
                max_loop_iterations=2,
            ).model_dump(),
        },
    )
    started_at = time.monotonic()
    run = await run_manager.start_run(task.envelope.task_id, Provider.FAKE)
    background = run_manager.background[run.run_id]
    await asyncio.wait_for(call_started.wait(), 1)
    await asyncio.wait_for(background, 3)

    assert time.monotonic() - started_at < 2.5
    completed_run = await store.get_run(run.run_id)
    execution = await store.get_loop_execution_for_run(run.run_id)
    assert completed_run is not None and completed_run.state is RunState.REQUIRES_HUMAN
    assert execution is not None
    assert execution.status is LoopExecutionStatus.REQUIRES_HUMAN
    assert execution.stop_reason is LoopStopReason.WALL_TIME_EXCEEDED
    assert execution.state.iteration == 1
    events = await store.list_events(run.run_id)
    assert sum(event.normalized_type is EventType.RUNTIME_CALL_CANCELLED for event in events) == 1
    assert sum(event.normalized_type is EventType.RUN_FAILED for event in events) == 1


@pytest.mark.acceptance("V01-P2-004")
async def test_loop_tool_ceiling_interrupts_before_an_extra_tool_starts(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    runtime: FakeRuntime

    async def emit_tool_calls(session, request) -> None:  # type: ignore[no-untyped-def]
        assert isinstance(request, RuntimeExecutionRequest)
        assert request.max_tool_calls == 1
        call_id = request.runtime_call_id
        run_ref = runtime.run_refs[call_id]
        for number in range(1, 3):
            payload = {"tool_call_id": f"tool-{number}", "runtime_call_id": call_id}
            await runtime._put_event(  # noqa: SLF001
                call_id,
                run_ref,
                session,
                native_type="fixture/tool-requested",
                normalized_type=EventType.TOOL_REQUESTED,
                payload=payload,
            )
            if number > request.max_tool_calls:
                return
            await runtime._put_event(  # noqa: SLF001
                call_id,
                run_ref,
                session,
                native_type="fixture/tool-started",
                normalized_type=EventType.TOOL_STARTED,
                payload=payload,
            )

    runtime = FakeRuntime(
        scripted_outcomes=[FakeCallOutcome(hook=emit_tool_calls)]
    )
    run_manager, store = manager(tmp_path, runtime)
    project = await run_manager.create_project("fixture", repository)
    task = await run_manager.create_task(
        project_id=project.project_id,
        objective="Stop before exceeding the tool-call ceiling.",
        task_patch={
            "task_type": TaskType.RESEARCH,
            "required_outputs": [{"path": "result.json", "kind": "json"}],
            "budgets": TaskBudgets(
                max_tool_calls=1,
                max_loop_iterations=2,
            ).model_dump(),
        },
    )
    run = await run_manager.start_run(task.envelope.task_id, Provider.FAKE)
    await asyncio.wait_for(run_manager.background[run.run_id], 3)

    completed_run = await store.get_run(run.run_id)
    execution = await store.get_loop_execution_for_run(run.run_id)
    assert completed_run is not None and completed_run.state is RunState.REQUIRES_HUMAN
    assert execution is not None
    assert execution.stop_reason is LoopStopReason.MAX_TOOL_CALLS
    assert execution.state.budget_remaining.tool_calls == 0
    iterations = await store.list_loop_iterations(execution.loop_execution_id)
    assert len(iterations) == 1 and iterations[0].tool_calls == 1
    events = await store.list_events(run.run_id)
    started_tools = {
        event.payload.get("tool_call_id")
        for event in events
        if event.normalized_type is EventType.TOOL_STARTED
    }
    assert started_tools == {"tool-1"}
    assert sum(event.normalized_type is EventType.RUNTIME_CALL_COMPLETED for event in events) == 1
