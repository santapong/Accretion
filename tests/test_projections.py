from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from accretion.contracts import (
    AcceptancePolicy,
    AgentEvent,
    EventType,
    ExecutionMode,
    GraphNodeStatus,
    LoopBudgetRemaining,
    LoopExecutionStatus,
    LoopState,
    Provider,
    RiskLevel,
    Run,
    RunGraph,
    RunState,
    Task,
    TaskBudgets,
    TaskEnvelope,
    VerificationResult,
    VerificationStatus,
)
from accretion.ids import new_id
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


@pytest.mark.acceptance("V01-P2-006")
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


def hybrid_fixture() -> tuple[Run, Task, RunGraph]:
    from accretion.contracts import TaskEnvelope
    from accretion.templates import HYBRID_RD_V1, instantiate_run_graph

    run_id = new_id("run")
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=new_id("project"),
            objective="Hybrid projection fixture.",
        )
    )
    run = Run(
        run_id=run_id,
        task_id=task.envelope.task_id,
        project_id=task.envelope.project_id,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
        execution_mode=ExecutionMode.HYBRID,
        workflow_template_id="hybrid-rd-v1",
    )
    graph = instantiate_run_graph(
        HYBRID_RD_V1,
        run_id=run_id,
        task_id=task.envelope.task_id,
        budgets=task.envelope.budgets,
    )
    return run, task, graph


def node_event(run: Run, key: str, *, entered: bool, entered_via: str | None = None) -> AgentEvent:
    payload: dict[str, object] = {"status": "RUNNING" if entered else "SUCCEEDED"}
    if entered and entered_via:
        payload["entered_via"] = entered_via
    return AgentEvent(
        event_id=new_id("event"),
        run_id=run.run_id,
        session_id="ses_fixture",
        provider=Provider.DETERMINISTIC,
        native_type="fixture/node",
        normalized_type=EventType.NODE_ENTERED if entered else EventType.NODE_EXITED,
        correlation_id=run.run_id,
        node_id=f"{run.run_id}:{key}",
        payload=payload,
        adapter_version="fixture-v1",
    )


@pytest.mark.acceptance("V01-P3-006")
def test_graph_projection_never_expands_nodes_with_iterations() -> None:
    from accretion.projections import build_graph_projection

    run, task, graph = hybrid_fixture()
    events: list[AgentEvent] = []
    for _ in range(7):
        events.append(node_event(run, "experiment-act", entered=True))
        events.append(node_event(run, "experiment-act", entered=False))
        events.append(node_event(run, "experiment-observe", entered=True))
        events.append(node_event(run, "experiment-observe", entered=False))
    projection = build_graph_projection(
        run=run,
        task=task,
        run_graph=graph,
        events=events,
        loop_executions={},
        verifications=[],
    )
    assert len(projection.nodes) == len(graph.nodes)
    assert len(projection.edges) == len(graph.edges)
    assert projection.version == "graph-projection-v1"
    assert projection.run_graph_version == graph.graph_revision
    loop_back = next(
        edge for edge in projection.edges if edge.edge_id.endswith("experiment-observe-act")
    )
    # The loop-back is the member's only in-edge, so every entry attributes
    # to it under the deterministic fallback.
    assert loop_back.traversal_count == 7


def test_graph_projection_emits_parent_ids_for_subflow_children() -> None:
    from accretion.projections import build_graph_projection

    run, task, graph = hybrid_fixture()
    projection = build_graph_projection(
        run=run,
        task=task,
        run_graph=graph,
        events=[],
        loop_executions={},
        verifications=[],
    )
    by_id = {node.node_id: node for node in projection.nodes}
    assert by_id[f"{run.run_id}:experiment-act"].parent_id == f"{run.run_id}:experiment"
    assert by_id[f"{run.run_id}:develop-observe"].parent_id == f"{run.run_id}:develop"
    assert by_id[f"{run.run_id}:research"].parent_id is None


def test_graph_projection_counts_exact_entered_via_edges() -> None:
    from accretion.projections import build_graph_projection

    run, task, graph = hybrid_fixture()
    events = [
        node_event(run, "initialize", entered=True),
        node_event(run, "initialize", entered=False),
        node_event(run, "research", entered=True, entered_via="initialize-research"),
        node_event(run, "research", entered=False),
    ]
    projection = build_graph_projection(
        run=run,
        task=task,
        run_graph=graph,
        events=events,
        loop_executions={},
        verifications=[],
    )
    first_edge = next(
        edge for edge in projection.edges if edge.edge_id.endswith("initialize-research")
    )
    assert first_edge.traversal_count == 1
    assert first_edge.active


def test_graph_projection_marks_waiting_gate_and_active_approval_edge() -> None:
    from accretion.contracts import TaskEnvelope
    from accretion.projections import build_graph_projection
    from accretion.templates import FIXED_GRAPH_V1, instantiate_run_graph

    run_id = new_id("run")
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=new_id("project"),
            objective="Gate fixture.",
        )
    )
    run = Run(
        run_id=run_id,
        task_id=task.envelope.task_id,
        project_id=task.envelope.project_id,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
        execution_mode=ExecutionMode.GRAPH,
        workflow_template_id="fixed-graph-v1",
    )
    graph = instantiate_run_graph(
        FIXED_GRAPH_V1,
        run_id=run_id,
        task_id=task.envelope.task_id,
        budgets=task.envelope.budgets,
    )
    approval_required = AgentEvent(
        event_id=new_id("event"),
        run_id=run_id,
        session_id="ses_fixture",
        provider=Provider.DETERMINISTIC,
        native_type="fixture/approval",
        normalized_type=EventType.APPROVAL_REQUIRED,
        correlation_id=run_id,
        node_id=f"{run_id}:approve-plan",
        payload={"approval_id": "apr_fixture"},
        adapter_version="fixture-v1",
    )
    projection = build_graph_projection(
        run=run,
        task=task,
        run_graph=graph,
        events=[node_event(run, "approve-plan", entered=True), approval_required],
        loop_executions={},
        verifications=[],
    )
    gate = next(node for node in projection.nodes if node.node_id.endswith("approve-plan"))
    assert gate.status is GraphNodeStatus.WAITING
    approval_edge = next(
        edge for edge in projection.edges if edge.edge_id.endswith("plan-approve-plan")
    )
    assert approval_edge.active


@pytest.mark.acceptance("V01-P3-005")
def test_graph_projection_rejects_template_mismatch() -> None:
    from accretion.projections import build_graph_projection

    run, task, graph = hybrid_fixture()
    wrong_run = run.model_copy(update={"workflow_template_id": "direct-v1"})
    with pytest.raises(ValueError, match="does not match"):
        build_graph_projection(
            run=wrong_run,
            task=task,
            run_graph=graph,
            events=[],
            loop_executions={},
            verifications=[],
        )
