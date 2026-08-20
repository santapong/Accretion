from __future__ import annotations

from datetime import UTC, datetime, timedelta

from accretion.contracts import (
    AcceptancePolicy,
    EventType,
    ExecutionMode,
    GraphNodeStatus,
    LoopBudgetRemaining,
    LoopExecutionStatus,
    LoopState,
    Provider,
    RiskLevel,
    Run,
    RunState,
    Task,
    TaskBudgets,
    TaskEnvelope,
    VerificationResult,
    VerificationStatus,
)
from accretion.looping import build_loop_execution, build_loop_spec
from accretion.projections import build_loop_projection
from accretion.runtimes.common import make_event


def test_loop_projection_uses_aggregate_verdict_instead_of_last_verifier() -> None:
    run = Run(
        run_id="run_projection",
        task_id="task_projection",
        project_id="project_projection",
        provider=Provider.FAKE,
        state=RunState.RUNNING,
        execution_mode=ExecutionMode.LOOP,
        workflow_template_id="feedback-loop-v1",
    )
    task = Task(
        envelope=TaskEnvelope(
            task_id=run.task_id,
            project_id=run.project_id,
            objective="Reject a bad candidate deterministically.",
            risk_level=RiskLevel.LOW,
        )
    )
    policy = AcceptancePolicy(
        policy_id="acceptance_policy_projection",
        required_verifiers=["output-contract", "trajectory-policy"],
    )
    execution = build_loop_execution(
        run_id=run.run_id,
        spec=build_loop_spec(task.envelope, policy.required_verifiers),
        policy=policy,
    )
    execution = execution.model_copy(
        update={
            "state": LoopState(
                iteration=1,
                budget_remaining=LoopBudgetRemaining(
                    wall_time_seconds=100,
                    tool_calls=10,
                    turns=9,
                    iterations=2,
                ),
            )
        }
    )
    aggregate_event = make_event(
        run_id=run.run_id,
        session_id="session_projection",
        provider=Provider.DETERMINISTIC,
        native_type="fixture/verification-result",
        normalized_type=EventType.VERIFICATION_RESULT,
        payload={"iteration_id": "iteration_projection", "acceptance": "FAIL"},
        adapter_version="fixture-v1",
    ).model_copy(update={"node_id": f"{run.run_id}:verify"})
    now = datetime.now(UTC)
    results = [
        VerificationResult(
            verification_id="verification_output",
            run_id=run.run_id,
            iteration_id="iteration_projection",
            verifier_id="output-contract",
            verifier_version="fixture-v1",
            target_ref="candidate",
            status=VerificationStatus.FAIL,
            executed_at=now,
        ),
        VerificationResult(
            verification_id="verification_trajectory",
            run_id=run.run_id,
            iteration_id="iteration_projection",
            verifier_id="trajectory-policy",
            verifier_version="fixture-v1",
            target_ref="candidate",
            status=VerificationStatus.PASS,
            executed_at=now + timedelta(milliseconds=1),
        ),
    ]

    projection = build_loop_projection(
        run=run,
        task=task,
        execution=execution,
        events=[aggregate_event],
        verifications=results,
    )

    verifier_node = next(node for node in projection.nodes if node.kind.value == "VERIFIER")
    assert verifier_node.status is GraphNodeStatus.FAILED
    assert verifier_node.verifier_state is VerificationStatus.FAIL


def test_loop_projection_fallback_folds_latest_iteration_results_fail_closed() -> None:
    run = Run(
        run_id="run_projection_fallback",
        task_id="task_projection_fallback",
        project_id="project_projection_fallback",
        provider=Provider.FAKE,
        state=RunState.RUNNING,
        execution_mode=ExecutionMode.LOOP,
        workflow_template_id="feedback-loop-v1",
    )
    task = Task(
        envelope=TaskEnvelope(
            task_id=run.task_id,
            project_id=run.project_id,
            objective="Project persisted verifier evidence.",
        )
    )
    policy = AcceptancePolicy(
        policy_id="acceptance_policy_projection_fallback",
        required_verifiers=["one", "two"],
    )
    execution = build_loop_execution(
        run_id=run.run_id,
        spec=build_loop_spec(task.envelope, policy.required_verifiers),
        policy=policy,
    )
    results = [
        VerificationResult(
            verification_id="verification_fallback_fail",
            run_id=run.run_id,
            iteration_id="iteration_fallback",
            verifier_id="one",
            verifier_version="fixture-v1",
            target_ref="candidate",
            status=VerificationStatus.FAIL,
        ),
        VerificationResult(
            verification_id="verification_fallback_pass",
            run_id=run.run_id,
            iteration_id="iteration_fallback",
            verifier_id="two",
            verifier_version="fixture-v1",
            target_ref="candidate",
            status=VerificationStatus.PASS,
        ),
    ]

    projection = build_loop_projection(
        run=run,
        task=task,
        execution=execution,
        events=[],
        verifications=results,
    )

    verifier_node = next(node for node in projection.nodes if node.kind.value == "VERIFIER")
    assert verifier_node.status is GraphNodeStatus.FAILED
    assert verifier_node.verifier_state is VerificationStatus.FAIL


def test_loop_projection_traversals_and_artifact_count_follow_trace() -> None:
    run = Run(
        run_id="run_projection_trace",
        task_id="task_projection_trace",
        project_id="project_projection_trace",
        provider=Provider.FAKE,
        state=RunState.REQUIRES_HUMAN,
        execution_mode=ExecutionMode.LOOP,
        workflow_template_id="feedback-loop-v1",
    )
    task = Task(
        envelope=TaskEnvelope(
            task_id=run.task_id,
            project_id=run.project_id,
            objective="Render only traversed feedback-loop routes.",
            budgets=TaskBudgets(max_loop_iterations=3),
        )
    )
    policy = AcceptancePolicy(policy_id="acceptance_policy_trace")
    execution = build_loop_execution(
        run_id=run.run_id,
        spec=build_loop_spec(task.envelope, []),
        policy=policy,
    ).model_copy(
        update={
            "status": LoopExecutionStatus.REQUIRES_HUMAN,
            "state": LoopState(
                iteration=2,
                accumulated_evidence_refs=[
                    "art_iteration_one",
                    "ver_output_one",
                    "file-sha256:result.json:digest",
                ],
                budget_remaining=LoopBudgetRemaining(
                    wall_time_seconds=10,
                    tool_calls=5,
                    turns=0,
                    iterations=0,
                ),
            ),
        }
    )

    def entered(key: str):  # type: ignore[no-untyped-def]
        return make_event(
            run_id=run.run_id,
            session_id="session_trace",
            provider=Provider.DETERMINISTIC,
            native_type=f"fixture/{key}-entered",
            normalized_type=EventType.NODE_ENTERED,
            adapter_version="fixture-v1",
        ).model_copy(update={"node_id": f"{run.run_id}:{key}"})

    projection = build_loop_projection(
        run=run,
        task=task,
        execution=execution,
        events=[
            entered("act"),
            entered("observe"),
            entered("act"),
            entered("observe"),
            entered("verify"),
            entered("complete"),
        ],
        verifications=[],
    )

    traversals = {
        edge.edge_id.rsplit(":", 1)[-1]: edge.traversal_count
        for edge in projection.edges
    }
    assert traversals == {
        "initialize-act": 1,
        "act-observe": 2,
        "observe-evaluate": 2,
        "evaluate-act": 1,
        "evaluate-verify": 1,
        "verify-complete": 1,
    }
    loop_node = next(node for node in projection.nodes if node.kind.value == "LOOP")
    act_node = next(node for node in projection.nodes if node.kind.value == "AGENT")
    assert loop_node.status is GraphNodeStatus.WAITING
    assert (loop_node.iteration, loop_node.max_iterations) == (2, 3)
    assert act_node.artifact_count == 1
