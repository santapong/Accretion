from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from accretion.contracts import (
    AgentEvent,
    EventType,
    GraphEdgeKind,
    GraphNodeKind,
    GraphNodeStatus,
    GraphProjection,
    GraphProjectionEdge,
    GraphProjectionNode,
    LoopExecution,
    Run,
    RunState,
    Task,
    VerificationResult,
    VerificationStatus,
)


def _node_id(run_id: str, key: str) -> str:
    return f"{run_id}:{key}"


def _status_from_value(value: object, fallback: GraphNodeStatus) -> GraphNodeStatus:
    try:
        return GraphNodeStatus(str(value))
    except ValueError:
        return fallback


def build_loop_projection(
    *,
    run: Run,
    task: Task,
    execution: LoopExecution,
    events: Iterable[AgentEvent],
    verifications: Iterable[VerificationResult],
) -> GraphProjection:
    """Build the read-only P2 topology from authoritative state and events."""

    event_items = list(events)
    keys = ("initialize", "act", "observe", "evaluate", "verify", "complete")
    node_ids = {_node_id(run.run_id, key) for key in keys}
    statuses = {key: GraphNodeStatus.PENDING for key in keys}
    entries: Counter[str] = Counter()
    aggregate_verification: VerificationStatus | None = None
    for event in event_items:
        if event.node_id not in node_ids:
            continue
        key = event.node_id.rsplit(":", 1)[-1]
        if event.normalized_type is EventType.NODE_ENTERED:
            entries[key] += 1
            statuses[key] = GraphNodeStatus.RUNNING
        elif event.normalized_type is EventType.NODE_EXITED:
            statuses[key] = _status_from_value(
                event.payload.get("status"), GraphNodeStatus.SUCCEEDED
            )
        if (
            event.normalized_type is EventType.VERIFICATION_RESULT
            and event.payload.get("acceptance") is not None
        ):
            try:
                aggregate_verification = VerificationStatus(
                    str(event.payload["acceptance"])
                )
            except ValueError:
                aggregate_verification = VerificationStatus.INCONCLUSIVE

    verification_items = list(verifications)
    latest_verification = (
        max(
            verification_items,
            key=lambda result: (result.executed_at, result.verification_id),
        )
        if verification_items
        else None
    )
    if aggregate_verification is None and latest_verification is not None:
        latest_iteration_id = latest_verification.iteration_id
        latest_results = [
            result
            for result in verification_items
            if result.iteration_id == latest_iteration_id
        ]
        if any(result.status is VerificationStatus.FAIL for result in latest_results):
            aggregate_verification = VerificationStatus.FAIL
        elif any(
            result.status is VerificationStatus.INCONCLUSIVE for result in latest_results
        ):
            aggregate_verification = VerificationStatus.INCONCLUSIVE
        else:
            aggregate_verification = VerificationStatus.PASS
    if aggregate_verification is not None:
        statuses["verify"] = {
            VerificationStatus.PASS: GraphNodeStatus.SUCCEEDED,
            VerificationStatus.FAIL: GraphNodeStatus.FAILED,
            VerificationStatus.INCONCLUSIVE: GraphNodeStatus.WAITING,
        }[aggregate_verification]

    if run.state is RunState.SUCCEEDED:
        statuses = {key: GraphNodeStatus.SUCCEEDED for key in keys}
    elif run.state is RunState.CANCELLED:
        statuses["complete"] = GraphNodeStatus.CANCELLED
    elif run.state is RunState.FAILED:
        statuses["complete"] = GraphNodeStatus.FAILED
    elif run.state is RunState.REQUIRES_HUMAN:
        statuses["complete"] = GraphNodeStatus.WAITING

    if run.state not in {RunState.SUCCEEDED, RunState.CANCELLED, RunState.FAILED}:
        statuses["evaluate"] = {
            "PENDING": GraphNodeStatus.PENDING,
            "RUNNING": GraphNodeStatus.RUNNING,
            "PAUSED": GraphNodeStatus.WAITING,
            "REQUIRES_HUMAN": GraphNodeStatus.WAITING,
        }.get(execution.status.value, statuses["evaluate"])

    iteration = execution.state.iteration
    max_iterations = execution.spec.max_iterations
    artifact_count = len(
        {
            reference
            for reference in execution.state.accumulated_evidence_refs
            if reference.startswith("art_")
        }
    )
    nodes = [
        GraphProjectionNode(
            node_id=_node_id(run.run_id, "initialize"),
            kind=GraphNodeKind.TASK,
            label="Initialize",
            status=statuses["initialize"],
            risk=task.envelope.risk_level,
        ),
        GraphProjectionNode(
            node_id=_node_id(run.run_id, "act"),
            kind=GraphNodeKind.AGENT,
            label="Act",
            status=statuses["act"],
            provider=run.provider,
            artifact_count=artifact_count,
            risk=task.envelope.risk_level,
        ),
        GraphProjectionNode(
            node_id=_node_id(run.run_id, "observe"),
            kind=GraphNodeKind.TOOL,
            label="Observe workspace",
            status=statuses["observe"],
            artifact_count=artifact_count,
            risk=task.envelope.risk_level,
        ),
        GraphProjectionNode(
            node_id=_node_id(run.run_id, "evaluate"),
            kind=GraphNodeKind.LOOP,
            label="Evaluate feedback",
            status=statuses["evaluate"],
            iteration=iteration,
            max_iterations=max_iterations,
            risk=task.envelope.risk_level,
        ),
        GraphProjectionNode(
            node_id=_node_id(run.run_id, "verify"),
            kind=GraphNodeKind.VERIFIER,
            label="Verify candidate",
            status=statuses["verify"],
            verifier_state=aggregate_verification,
            risk=task.envelope.risk_level,
        ),
        GraphProjectionNode(
            node_id=_node_id(run.run_id, "complete"),
            kind=GraphNodeKind.TERMINAL,
            label="Complete or escalate",
            status=statuses["complete"],
            risk=task.envelope.risk_level,
        ),
    ]
    edge_specs = [
        (
            "initialize-act",
            "initialize",
            "act",
            GraphEdgeKind.NORMAL,
            None,
            min(entries["act"], 1),
        ),
        ("act-observe", "act", "observe", GraphEdgeKind.NORMAL, None, entries["observe"]),
        (
            "observe-evaluate",
            "observe",
            "evaluate",
            GraphEdgeKind.NORMAL,
            None,
            entries["observe"],
        ),
        (
            "evaluate-act",
            "evaluate",
            "act",
            GraphEdgeKind.LOOP_BACK,
            "repair",
            max(0, entries["act"] - 1),
        ),
        (
            "evaluate-verify",
            "evaluate",
            "verify",
            GraphEdgeKind.CONDITION,
            "candidate",
            entries["verify"],
        ),
        (
            "verify-complete",
            "verify",
            "complete",
            GraphEdgeKind.CONDITION,
            "pass",
            entries["complete"],
        ),
    ]
    edges = [
        GraphProjectionEdge(
            edge_id=f"{run.run_id}:{edge_id}",
            source=_node_id(run.run_id, source),
            target=_node_id(run.run_id, target),
            kind=kind,
            label=label,
            active=traversals > 0,
            traversal_count=traversals,
        )
        for edge_id, source, target, kind, label, traversals in edge_specs
    ]
    return GraphProjection(
        run_id=run.run_id,
        workflow_template_id=run.workflow_template_id or "feedback-loop-v1",
        nodes=nodes,
        edges=edges,
    )
