from __future__ import annotations

import os
from pathlib import Path

import pytest

from accretion.contracts import (
    AcceptancePolicy,
    AgentEvent,
    EventType,
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
    TaskEnvelope,
    VerificationResult,
    VerificationStatus,
)
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import PostgresStore

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]


async def test_postgres_loop_transition_is_atomic_and_session_is_recoverable(
    tmp_path: Path,
) -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    project = Project(
        project_id=new_id("project"), name="P2 PostgreSQL", repository_path=tmp_path
    )
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"), project_id=project.project_id, objective="Persist a loop."
        )
    )
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
    )
    policy = AcceptancePolicy(
        policy_id=new_id("acceptance_policy"),
        required_verifiers=["output-contract"],
        require_human_if_risk_gte=None,
    )
    execution = LoopExecution(
        loop_execution_id=new_id("loop_execution"),
        run_id=run.run_id,
        spec=LoopSpec(loop_id=new_id("loop"), max_iterations=2),
        state=LoopState(
            budget_remaining=LoopBudgetRemaining(
                wall_time_seconds=60, tool_calls=10, turns=4, iterations=2
            )
        ),
        acceptance_policy_ref=policy.policy_id,
        status=LoopExecutionStatus.RUNNING,
    )
    iteration_id = new_id("iteration")
    verification = VerificationResult(
        verification_id=new_id("verification"),
        run_id=run.run_id,
        iteration_id=iteration_id,
        verifier_id="output-contract",
        verifier_version="output-contract-v1",
        target_ref="candidate",
        status=VerificationStatus.PASS,
    )
    iteration = LoopIteration(
        iteration_id=iteration_id,
        loop_execution_id=execution.loop_execution_id,
        run_id=run.run_id,
        number=1,
        status=LoopIterationStatus.COMPLETED,
        verification_refs=[verification.verification_id],
    )
    event = AgentEvent(
        event_id=new_id("event"),
        run_id=run.run_id,
        session_id=new_id("session"),
        provider=Provider.FAKE,
        native_type="loop/complete",
        normalized_type=EventType.LOOP_ITERATION_COMPLETED,
        correlation_id=run.run_id,
        adapter_version="test-v1",
    )
    try:
        await store.create_project(project)
        await store.create_task(task)
        await store.create_run(run)
        await store.save_acceptance_policy(policy)
        await store.create_loop_execution(execution)
        updated = await store.append_loop_iteration(
            execution.loop_execution_id,
            iteration,
            LoopState(
                iteration=1,
                budget_remaining=LoopBudgetRemaining(
                    wall_time_seconds=50, tool_calls=8, turns=3, iterations=1
                ),
            ),
            expected_revision=0,
            verifications=[verification],
            events=[event],
        )
        assert updated.state.iteration == 1
        assert await store.list_loop_iterations(execution.loop_execution_id) == [iteration]
        assert await store.list_verifications(run.run_id, iteration_id) == [verification]
        assert [item.sequence for item in await store.list_events(run.run_id)] == [1]

        session = SessionRef(
            session_id=new_id("session"),
            run_id=run.run_id,
            provider=Provider.FAKE,
            native_session_id="native-1",
            workspace=tmp_path,
        )
        await store.save_session(session.model_copy(update={"native_session_id": None}))
        await store.save_session(session)
        assert await store.get_session_for_run(run.run_id) == session

        terminal = await store.update_loop_execution(
            execution.loop_execution_id,
            updated.state,
            status=LoopExecutionStatus.SUCCEEDED,
            stop_reason=LoopStopReason.VERIFIED_SUCCESS,
            expected_revision=updated.revision,
        )
        with pytest.raises(ValueError, match="terminal"):
            await store.update_loop_execution(
                terminal.loop_execution_id,
                terminal.state,
                status=LoopExecutionStatus.RUNNING,
                expected_revision=terminal.revision,
            )
        with pytest.raises(ValueError, match="terminal"):
            await store.append_loop_iteration(
                terminal.loop_execution_id,
                LoopIteration(
                    iteration_id=new_id("iteration"),
                    loop_execution_id=terminal.loop_execution_id,
                    run_id=run.run_id,
                    number=2,
                    status=LoopIterationStatus.COMPLETED,
                ),
                LoopState(
                    iteration=2,
                    budget_remaining=LoopBudgetRemaining(
                        wall_time_seconds=40,
                        tool_calls=6,
                        turns=2,
                        iterations=0,
                    ),
                ),
                expected_revision=terminal.revision,
            )
    finally:
        await engine.dispose()
