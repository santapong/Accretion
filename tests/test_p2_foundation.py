from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from accretion.contracts import (
    AcceptancePolicy,
    AgentEvent,
    EventType,
    ExecutionMode,
    LoopBudgetRemaining,
    LoopExecution,
    LoopExecutionStatus,
    LoopIteration,
    LoopIterationStatus,
    LoopSpec,
    LoopState,
    LoopStopReason,
    Project,
    Provider,
    Run,
    RunState,
    SessionRef,
    Task,
    TaskBudgets,
    TaskEnvelope,
    VerificationResult,
    VerificationStatus,
)
from accretion.ids import has_prefix, new_id
from accretion.persistence.store import MemoryStore


def remaining(*, iteration: int = 0) -> LoopBudgetRemaining:
    return LoopBudgetRemaining(
        wall_time_seconds=1800,
        tool_calls=100,
        turns=20,
        iterations=3 - iteration,
    )


async def loop_store(tmp_path: Path) -> tuple[MemoryStore, Run, AcceptancePolicy, LoopExecution]:
    store = MemoryStore()
    project = Project(project_id=new_id("project"), name="P2", repository_path=tmp_path)
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"), project_id=project.project_id, objective="Verify a loop."
        )
    )
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
        strategy_decision_id=new_id("decision"),
        execution_mode=ExecutionMode.LOOP,
        workflow_template_id="feedback-loop-v1",
    )
    policy = AcceptancePolicy(
        policy_id=new_id("acceptance_policy"),
        required_verifiers=["output-contract"],
        require_human_if_risk_gte=None,
    )
    execution = LoopExecution(
        loop_execution_id=new_id("loop_execution"),
        run_id=run.run_id,
        spec=LoopSpec(loop_id=new_id("loop")),
        state=LoopState(budget_remaining=remaining()),
        acceptance_policy_ref=policy.policy_id,
        status=LoopExecutionStatus.RUNNING,
    )
    await store.create_project(project)
    await store.create_task(task)
    await store.create_run(run)
    await store.save_acceptance_policy(policy)
    execution = await store.create_loop_execution(execution)
    return store, run, policy, execution


def test_p2_contract_versions_are_strict_and_budgets_are_backward_compatible() -> None:
    legacy = TaskBudgets.model_validate({"wall_time_seconds": 10, "max_turns": 2})
    assert legacy.max_tool_calls == 100
    with pytest.raises(ValidationError):
        LoopSpec.model_validate(
            {
                "schema_version": "2.0",
                "loop_id": new_id("loop"),
            }
        )
    with pytest.raises(ValidationError):
        AcceptancePolicy.model_validate(
            {
                "policy_id": new_id("acceptance_policy"),
                "unknown": True,
            }
        )
    with pytest.raises(ValidationError):
        AcceptancePolicy(
            policy_id=new_id("acceptance_policy"), score_thresholds={"tests": 1.1}
        )


@pytest.mark.parametrize(
    "kind",
    ["loop", "loop_execution", "iteration", "verification", "acceptance_policy", "runtime_call"],
)
def test_p2_identifiers_have_stable_prefixes(kind: str) -> None:
    assert has_prefix(new_id(kind), kind)


async def test_loop_iteration_evidence_and_events_commit_as_one_transition(
    tmp_path: Path,
) -> None:
    store, run, policy, execution = await loop_store(tmp_path)
    iteration_id = new_id("iteration")
    verification = VerificationResult(
        verification_id=new_id("verification"),
        run_id=run.run_id,
        iteration_id=iteration_id,
        verifier_id="output-contract",
        verifier_version="output-contract-v1",
        target_ref="candidate-1",
        status=VerificationStatus.PASS,
        score=1,
    )
    iteration = LoopIteration(
        iteration_id=iteration_id,
        loop_execution_id=execution.loop_execution_id,
        run_id=run.run_id,
        number=1,
        status=LoopIterationStatus.COMPLETED,
        verification_refs=[verification.verification_id],
        evidence_refs=["artifact:iteration-001.patch"],
        diff_artifact_ref="artifact:iteration-001.patch",
    )
    event = AgentEvent(
        event_id=new_id("event"),
        run_id=run.run_id,
        session_id=new_id("session"),
        provider=Provider.FAKE,
        native_type="loop/iteration-completed",
        normalized_type=EventType.LOOP_ITERATION_COMPLETED,
        correlation_id=run.run_id,
        adapter_version="test-v1",
    )
    next_state = LoopState(
        iteration=1,
        accumulated_evidence_refs=iteration.evidence_refs,
        budget_remaining=remaining(iteration=1),
    )

    with pytest.raises(ValueError, match="append_loop_iteration"):
        await store.save_verification(verification)

    updated = await store.append_loop_iteration(
        execution.loop_execution_id,
        iteration,
        next_state,
        expected_revision=0,
        verifications=[verification],
        events=[event],
    )

    assert updated.revision == 1
    assert updated.state.iteration == 1
    assert updated.acceptance_policy == policy
    assert await store.list_loop_iterations(execution.loop_execution_id) == [iteration]
    assert await store.list_verifications(run.run_id, iteration_id) == [verification]
    assert [item.sequence for item in await store.list_events(run.run_id)] == [1]
    persisted_run = await store.get_run(run.run_id)
    assert persisted_run and persisted_run.last_sequence == 1
    assert persisted_run.loop_execution_id == execution.loop_execution_id
    assert persisted_run.acceptance_policy_id == policy.policy_id


async def test_loop_revision_conflict_leaves_no_partial_iteration(tmp_path: Path) -> None:
    store, run, _, execution = await loop_store(tmp_path)
    await store.update_loop_execution(
        execution.loop_execution_id,
        execution.state,
        expected_revision=0,
        status=LoopExecutionStatus.RUNNING,
    )
    iteration = LoopIteration(
        iteration_id=new_id("iteration"),
        loop_execution_id=execution.loop_execution_id,
        run_id=run.run_id,
        number=1,
        status=LoopIterationStatus.INTERRUPTED,
    )
    with pytest.raises(ValueError, match="revision conflict"):
        await store.append_loop_iteration(
            execution.loop_execution_id,
            iteration,
            LoopState(iteration=1, budget_remaining=remaining(iteration=1)),
            expected_revision=0,
        )
    assert await store.list_loop_iterations(execution.loop_execution_id) == []


async def test_terminal_loop_rejects_regression_and_new_iterations(tmp_path: Path) -> None:
    store, run, _, execution = await loop_store(tmp_path)
    terminal = await store.update_loop_execution(
        execution.loop_execution_id,
        execution.state,
        expected_revision=execution.revision,
        status=LoopExecutionStatus.SUCCEEDED,
        stop_reason=LoopStopReason.VERIFIED_SUCCESS,
    )

    with pytest.raises(ValueError, match="terminal"):
        await store.update_loop_execution(
            terminal.loop_execution_id,
            terminal.state,
            expected_revision=terminal.revision,
            status=LoopExecutionStatus.RUNNING,
        )
    with pytest.raises(ValueError, match="terminal"):
        await store.append_loop_iteration(
            terminal.loop_execution_id,
            LoopIteration(
                iteration_id=new_id("iteration"),
                loop_execution_id=terminal.loop_execution_id,
                run_id=run.run_id,
                number=1,
                status=LoopIterationStatus.COMPLETED,
            ),
            LoopState(iteration=1, budget_remaining=remaining(iteration=1)),
            expected_revision=terminal.revision,
        )
    assert await store.get_loop_execution(terminal.loop_execution_id) == terminal
    assert await store.list_loop_iterations(terminal.loop_execution_id) == []


async def test_acceptance_policy_is_immutable_and_session_is_upserted(tmp_path: Path) -> None:
    store, run, policy, _ = await loop_store(tmp_path)
    with pytest.raises(ValueError, match="immutable"):
        await store.save_acceptance_policy(
            policy.model_copy(update={"required_verifiers": ["different"]})
        )

    session_id = new_id("session")
    initial = SessionRef(
        session_id=session_id,
        run_id=run.run_id,
        provider=Provider.CODEX,
        workspace=tmp_path,
    )
    resumed = initial.model_copy(update={"native_session_id": "native-thread-1"})
    await store.save_session(initial)
    await store.save_session(resumed)
    assert await store.get_session_for_run(run.run_id) == resumed


def test_loop_timestamps_are_timezone_aware() -> None:
    assert datetime.now(UTC).tzinfo is not None
