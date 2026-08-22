from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from accretion.contracts import (
    AgentEvent,
    ArtifactRef,
    EventType,
    GraphEdgeKind,
    GraphNodeKind,
    GraphNodeStatus,
    GraphProjection,
    GraphProjectionEdge,
    GraphProjectionNode,
    LoopExecution,
    LoopExecutionStatus,
    Provider,
    Run,
    RunGraph,
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


@dataclass
class _FoldResult:
    statuses: dict[str, GraphNodeStatus]
    entries: Counter[str] = field(default_factory=Counter)
    entered_via: Counter[str] = field(default_factory=Counter)
    aggregate_verification: VerificationStatus | None = None
    unresolved_approvals: dict[str, set[str]] = field(default_factory=dict)


def _fold_node_events(
    run_id: str,
    node_keys: Iterable[str],
    events: Iterable[AgentEvent],
) -> _FoldResult:
    """Fold NODE/VERIFICATION/APPROVAL events onto a fixed node-key set.

    Events whose node_id is not in the key set are skipped — the projection
    can never grow nodes from events (V01-P3-006)."""

    keys = list(node_keys)
    node_ids = {_node_id(run_id, key) for key in keys}
    result = _FoldResult(statuses={key: GraphNodeStatus.PENDING for key in keys})
    for event in events:
        if (
            event.normalized_type is EventType.VERIFICATION_RESULT
            and event.payload.get("acceptance") is not None
        ):
            try:
                result.aggregate_verification = VerificationStatus(
                    str(event.payload["acceptance"])
                )
            except ValueError:
                result.aggregate_verification = VerificationStatus.INCONCLUSIVE
        if event.node_id not in node_ids:
            continue
        key = event.node_id.rsplit(":", 1)[-1]
        if event.normalized_type is EventType.NODE_ENTERED:
            result.entries[key] += 1
            result.statuses[key] = GraphNodeStatus.RUNNING
            entered_via = event.payload.get("entered_via")
            if entered_via:
                result.entered_via[str(entered_via)] += 1
        elif event.normalized_type is EventType.NODE_EXITED:
            # Evidence never guesses optimistically: an unreadable exit status
            # renders as FAILED, matching the trace fold's pessimism.
            result.statuses[key] = _status_from_value(
                event.payload.get("status"), GraphNodeStatus.FAILED
            )
        elif (
            event.normalized_type is EventType.APPROVAL_REQUIRED
            and event.payload.get("approval_id")
        ):
            result.unresolved_approvals.setdefault(key, set()).add(
                str(event.payload["approval_id"])
            )
        elif (
            event.normalized_type is EventType.APPROVAL_RESOLVED
            and event.payload.get("approval_id")
        ):
            result.unresolved_approvals.setdefault(key, set()).discard(
                str(event.payload["approval_id"])
            )
    return result


def _aggregate_from_results(
    verification_items: list[VerificationResult],
) -> VerificationStatus | None:
    latest_verification = (
        max(
            verification_items,
            key=lambda result: (result.executed_at, result.verification_id),
        )
        if verification_items
        else None
    )
    if latest_verification is None:
        return None
    latest_iteration_id = latest_verification.iteration_id
    latest_results = [
        result
        for result in verification_items
        if result.iteration_id == latest_iteration_id
    ]
    if any(result.status is VerificationStatus.FAIL for result in latest_results):
        return VerificationStatus.FAIL
    if any(result.status is VerificationStatus.INCONCLUSIVE for result in latest_results):
        return VerificationStatus.INCONCLUSIVE
    return VerificationStatus.PASS


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
    fold = _fold_node_events(run.run_id, keys, event_items)
    statuses = fold.statuses
    entries = fold.entries
    aggregate_verification = fold.aggregate_verification

    verification_items = list(verifications)
    if aggregate_verification is None:
        aggregate_verification = _aggregate_from_results(verification_items)
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


def _edge_traversals(run_graph: RunGraph, fold: _FoldResult) -> dict[str, int]:
    """Attribute node entries to edges.

    Exact when the scheduler stamped entered_via on NODE_ENTERED payloads;
    otherwise a deterministic fallback: a sole in-edge receives every entry,
    and a LOOP_BACK/RETRY in-edge receives the re-entries while the remaining
    edge receives the first."""

    counts: dict[str, int] = {}
    in_edges: dict[str, list[str]] = {}
    for edge in run_graph.edges:
        in_edges.setdefault(edge.target, []).append(edge.key)
    for edge in run_graph.edges:
        counts[edge.key] = fold.entered_via.get(edge.key, 0)
    kinds = {edge.key: edge.kind for edge in run_graph.edges}
    for target, edge_keys in in_edges.items():
        if any(fold.entered_via.get(key, 0) for key in edge_keys):
            continue
        target_key = target.rsplit(":", 1)[-1]
        entries = fold.entries.get(target_key, 0)
        if entries == 0:
            continue
        loop_like = [
            key
            for key in edge_keys
            if kinds[key] in {GraphEdgeKind.LOOP_BACK, GraphEdgeKind.RETRY}
        ]
        plain = [key for key in edge_keys if key not in loop_like]
        if len(edge_keys) == 1:
            counts[edge_keys[0]] = entries
        elif len(plain) == 1 and loop_like:
            counts[plain[0]] = min(entries, 1)
            counts[loop_like[0]] = max(0, entries - 1)
        else:
            counts[edge_keys[0]] = entries
    return counts


def build_graph_projection(
    *,
    run: Run,
    task: Task,
    run_graph: RunGraph,
    events: Iterable[AgentEvent],
    loop_executions: Mapping[str, LoopExecution],
    verifications: Iterable[VerificationResult],
    artifacts: Iterable[ArtifactRef] = (),
) -> GraphProjection:
    """Data-driven projection for any RunGraph-backed run.

    Structure comes from the persisted RunGraph; statuses, traversal counts,
    verifier verdicts, and approval waits derive from the immutable event log
    plus authoritative loop-execution rows. The node set never expands with
    iterations (V01-P3-006)."""

    if run.workflow_template_id and run.workflow_template_id != run_graph.template_id:
        raise ValueError(
            f"run {run.run_id} template {run.workflow_template_id} does not match "
            f"run graph template {run_graph.template_id}"
        )
    event_items = list(events)
    keys = [node.key for node in run_graph.nodes]
    fold = _fold_node_events(run.run_id, keys, event_items)
    statuses = fold.statuses

    aggregate_verification = fold.aggregate_verification
    verification_items = list(verifications)
    if aggregate_verification is None:
        aggregate_verification = _aggregate_from_results(verification_items)
    verifier_status = (
        {
            VerificationStatus.PASS: GraphNodeStatus.SUCCEEDED,
            VerificationStatus.FAIL: GraphNodeStatus.FAILED,
            VerificationStatus.INCONCLUSIVE: GraphNodeStatus.WAITING,
        }[aggregate_verification]
        if aggregate_verification is not None
        else None
    )

    loops_by_node: dict[str, LoopExecution] = {}
    for execution in loop_executions.values():
        current = loops_by_node.get(execution.node_key)
        if current is None or execution.attempt > current.attempt:
            loops_by_node[execution.node_key] = execution

    # Diff artifacts attribute to nodes by their capture-name convention:
    # "{key}-…​.patch" for TOOL/region captures, "final.patch" to the verifier.
    artifact_counts: Counter[str] = Counter()
    verifier_keys = [
        node.key for node in run_graph.nodes if node.kind is GraphNodeKind.VERIFIER
    ]
    for artifact in artifacts:
        name = artifact.path.name
        owner = next((key for key in keys if name.startswith(f"{key}-")), None)
        if owner is None and name == "final.patch" and verifier_keys:
            owner = verifier_keys[0]
        if owner is not None:
            artifact_counts[owner] += 1

    terminal_keys = [
        node.key for node in run_graph.nodes if node.kind is GraphNodeKind.TERMINAL
    ]
    if run.state is RunState.SUCCEEDED:
        statuses = {key: GraphNodeStatus.SUCCEEDED for key in keys}
    elif run.state in {RunState.CANCELLED, RunState.FAILED, RunState.REQUIRES_HUMAN}:
        terminal_status = {
            RunState.CANCELLED: GraphNodeStatus.CANCELLED,
            RunState.FAILED: GraphNodeStatus.FAILED,
            RunState.REQUIRES_HUMAN: GraphNodeStatus.WAITING,
        }[run.state]
        for key in terminal_keys:
            statuses[key] = terminal_status

    nodes = []
    for spec in run_graph.nodes:
        status = statuses[spec.key]
        iteration = None
        max_iterations = None
        verifier_state = None
        if spec.kind is GraphNodeKind.LOOP:
            node_loop = loops_by_node.get(spec.key)
            if node_loop is not None:
                iteration = node_loop.state.iteration
                max_iterations = node_loop.spec.max_iterations
                if run.state not in {
                    RunState.SUCCEEDED,
                    RunState.CANCELLED,
                    RunState.FAILED,
                }:
                    status = {
                        LoopExecutionStatus.PENDING: GraphNodeStatus.PENDING,
                        LoopExecutionStatus.RUNNING: GraphNodeStatus.RUNNING,
                        LoopExecutionStatus.PAUSED: GraphNodeStatus.WAITING,
                        LoopExecutionStatus.REQUIRES_HUMAN: GraphNodeStatus.WAITING,
                        LoopExecutionStatus.SUCCEEDED: GraphNodeStatus.SUCCEEDED,
                        LoopExecutionStatus.FAILED: GraphNodeStatus.FAILED,
                        LoopExecutionStatus.CANCELLED: GraphNodeStatus.CANCELLED,
                    }[node_loop.status]
            else:
                iteration = spec.iteration
                max_iterations = spec.max_iterations
        if spec.kind is GraphNodeKind.VERIFIER and verifier_status is not None:
            verifier_state = aggregate_verification
            if run.state not in {RunState.SUCCEEDED, RunState.CANCELLED, RunState.FAILED}:
                status = verifier_status
        if (
            spec.kind is GraphNodeKind.GATE
            and fold.unresolved_approvals.get(spec.key)
            and run.state
            not in {RunState.SUCCEEDED, RunState.CANCELLED, RunState.FAILED}
        ):
            status = GraphNodeStatus.WAITING
        if spec.kind is GraphNodeKind.AGENT:
            provider = run.provider
        elif spec.kind in {GraphNodeKind.TOOL, GraphNodeKind.VERIFIER}:
            provider = Provider.DETERMINISTIC
        elif spec.kind is GraphNodeKind.GATE:
            provider = Provider.HUMAN
        else:
            provider = None
        nodes.append(
            GraphProjectionNode(
                node_id=spec.node_id,
                parent_id=spec.parent_id,
                kind=spec.kind,
                label=spec.label,
                status=status,
                provider=provider,
                iteration=iteration,
                max_iterations=max_iterations,
                artifact_count=artifact_counts.get(spec.key, 0),
                verifier_state=verifier_state,
                risk=task.envelope.risk_level,
            )
        )

    traversals = _edge_traversals(run_graph, fold)
    edges = []
    for spec_edge in run_graph.edges:
        target_key = spec_edge.target.rsplit(":", 1)[-1]
        count = traversals.get(spec_edge.key, 0)
        waiting_gate = bool(
            fold.unresolved_approvals.get(target_key)
            and spec_edge.kind is GraphEdgeKind.APPROVAL
        )
        edges.append(
            GraphProjectionEdge(
                edge_id=spec_edge.edge_id,
                source=spec_edge.source,
                target=spec_edge.target,
                kind=spec_edge.kind,
                label=spec_edge.label,
                active=count > 0 or waiting_gate,
                traversal_count=count,
            )
        )

    return GraphProjection(
        version="graph-projection-v1",
        run_id=run.run_id,
        workflow_template_id=run_graph.template_id,
        run_graph_version=run_graph.graph_revision,
        nodes=nodes,
        edges=edges,
    )
