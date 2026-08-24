from __future__ import annotations

from collections import defaultdict

from accretion.contracts import (
    BudgetPolicy,
    EdgeGuard,
    ExecutionMode,
    GateSpec,
    GraphEdgeKind,
    GraphNodeKind,
    NodeLoopPolicy,
    TemplateStatus,
    WorkflowEdgeSpec,
    WorkflowNodeSpec,
    WorkflowTemplate,
)
from accretion.ids import new_id
from accretion.orchestration.models import (
    ConditionOperator,
    DynamicWorkflowEdgeSpec,
    DynamicWorkflowNodeSpec,
    WorkflowProposal,
)
from accretion.templates import compute_template_checksum, validate_template


class DynamicMaterializationError(ValueError):
    """An accepted semantic graph cannot be represented by the P5 executor."""


_FAILURE_OUTCOMES: dict[GraphNodeKind, tuple[EdgeGuard, ...]] = {
    GraphNodeKind.AGENT: (EdgeGuard.ON_FAIL,),
    GraphNodeKind.VERIFIER: (EdgeGuard.ON_FAIL, EdgeGuard.ON_INCONCLUSIVE),
    GraphNodeKind.GATE: (EdgeGuard.ON_DENIED,),
    GraphNodeKind.LOOP: (EdgeGuard.ON_FAIL,),
}


def _executor_kind(kind: GraphNodeKind) -> GraphNodeKind:
    # P5 keeps execution serial. JOIN is therefore a deterministic boundary,
    # while HUMAN uses the existing durable approval scheduler.
    if kind is GraphNodeKind.JOIN:
        return GraphNodeKind.TASK
    if kind is GraphNodeKind.HUMAN:
        return GraphNodeKind.GATE
    return kind


def _label(objective: str) -> str:
    first = objective.strip().splitlines()[0]
    return first if len(first) <= 120 else f"{first[:117]}..."


def _condition_guard(edge: DynamicWorkflowEdgeSpec) -> EdgeGuard:
    condition = edge.condition
    if (
        condition is None
        or condition.operator is not ConditionOperator.EQ
        or condition.path != "node.outcome"
        or not isinstance(condition.value, str)
    ):
        raise DynamicMaterializationError(
            f"edge {edge.local_id} uses a valid condition that P5 cannot execute"
        )
    try:
        return EdgeGuard(f"ON_{condition.value.upper()}")
    except ValueError as exc:
        raise DynamicMaterializationError(
            f"edge {edge.local_id} references unsupported outcome {condition.value!r}"
        ) from exc


def _materialize_node(node: DynamicWorkflowNodeSpec) -> list[WorkflowNodeSpec]:
    kind = _executor_kind(node.kind)
    if kind is not GraphNodeKind.LOOP:
        return [
            WorkflowNodeSpec(
                key=node.local_id,
                kind=kind,
                label=_label(node.objective),
                instruction=node.objective if kind is GraphNodeKind.AGENT else None,
            )
        ]
    assert node.loop_spec is not None
    act_key = f"{node.local_id}-act"
    observe_key = f"{node.local_id}-observe"
    if len(act_key) > 32 or len(observe_key) > 32:
        raise DynamicMaterializationError(
            f"loop node {node.local_id} leaves no room for executor region identifiers"
        )
    return [
        WorkflowNodeSpec(
            key=node.local_id,
            kind=GraphNodeKind.LOOP,
            label=_label(node.objective),
            loop=NodeLoopPolicy(
                region_keys=[act_key, observe_key],
                act_key=act_key,
                observe_key=observe_key,
                verify_in_region=False,
                max_iterations_source="FIXED",
                fixed_max_iterations=node.loop_spec.max_iterations,
            ),
        ),
        WorkflowNodeSpec(
            key=act_key,
            kind=GraphNodeKind.AGENT,
            label=f"{_label(node.objective)} — act",
            parent_key=node.local_id,
            instruction=node.objective,
        ),
        WorkflowNodeSpec(
            key=observe_key,
            kind=GraphNodeKind.TOOL,
            label=f"{_label(node.objective)} — observe",
            parent_key=node.local_id,
        ),
    ]


def _materialize_edge(edge: DynamicWorkflowEdgeSpec) -> WorkflowEdgeSpec:
    if edge.kind in {GraphEdgeKind.FANOUT, GraphEdgeKind.MERGE, GraphEdgeKind.ERROR}:
        raise DynamicMaterializationError(
            f"edge kind {edge.kind.value} is reserved for a later executor milestone"
        )
    if edge.kind is GraphEdgeKind.LOOP_BACK:
        raise DynamicMaterializationError(
            "semantic LOOP_BACK edges are expressed by a LOOP node in P5"
        )
    if edge.kind is GraphEdgeKind.RETRY:
        return WorkflowEdgeSpec(
            key=edge.local_id,
            source=edge.source,
            target=edge.target,
            kind=GraphEdgeKind.RETRY,
            label=f"retry up to {edge.max_traversals}",
            guard=EdgeGuard.ON_FAIL,
        )
    if edge.kind is GraphEdgeKind.CONDITION:
        return WorkflowEdgeSpec(
            key=edge.local_id,
            source=edge.source,
            target=edge.target,
            kind=GraphEdgeKind.CONDITION,
            label="typed condition",
            guard=_condition_guard(edge),
        )
    if edge.kind is GraphEdgeKind.APPROVAL:
        return WorkflowEdgeSpec(
            key=edge.local_id,
            source=edge.source,
            target=edge.target,
            kind=GraphEdgeKind.APPROVAL,
            label="approved",
            guard=EdgeGuard.ON_APPROVED,
        )
    return WorkflowEdgeSpec(
        key=edge.local_id,
        source=edge.source,
        target=edge.target,
        kind=GraphEdgeKind.NORMAL,
    )


def materialize_workflow_template(
    proposal: WorkflowProposal,
    *,
    normalized_graph_hash: str,
) -> WorkflowTemplate:
    """Compile a validated proposal into the existing fail-closed executor grammar."""

    terminals = [
        node.local_id for node in proposal.nodes if node.kind is GraphNodeKind.TERMINAL
    ]
    if not terminals:
        raise DynamicMaterializationError("a materialized graph needs a terminal")
    fallback_terminal = terminals[0]
    dynamic_nodes = {node.local_id: node for node in proposal.nodes}
    nodes = [item for node in proposal.nodes for item in _materialize_node(node)]
    edges = [_materialize_edge(edge) for edge in proposal.edges]
    for node in proposal.nodes:
        if node.kind is not GraphNodeKind.LOOP:
            continue
        act_key = f"{node.local_id}-act"
        observe_key = f"{node.local_id}-observe"
        edges.extend(
            [
                WorkflowEdgeSpec(
                    key=f"{node.local_id}-act-observe",
                    source=act_key,
                    target=observe_key,
                    kind=GraphEdgeKind.NORMAL,
                ),
                WorkflowEdgeSpec(
                    key=f"{node.local_id}-observe-act",
                    source=observe_key,
                    target=act_key,
                    kind=GraphEdgeKind.LOOP_BACK,
                    label="bounded iteration",
                ),
            ]
        )

    outgoing: dict[str, list[WorkflowEdgeSpec]] = defaultdict(list)
    for edge in edges:
        source = dynamic_nodes.get(edge.source)
        if (
            source is not None
            and source.kind in {GraphNodeKind.GATE, GraphNodeKind.HUMAN}
            and edge.kind is GraphEdgeKind.NORMAL
        ):
            edge = edge.model_copy(
                update={"kind": GraphEdgeKind.CONDITION, "guard": EdgeGuard.ON_APPROVED}
            )
        outgoing[edge.source].append(edge)
    edges = [edge for values in outgoing.values() for edge in values]

    for node in proposal.nodes:
        for guard in _FAILURE_OUTCOMES.get(_executor_kind(node.kind), ()):
            covered = any(
                edge.guard is guard and edge.kind is not GraphEdgeKind.RETRY
                for edge in outgoing[node.local_id]
            )
            if covered:
                continue
            suffix = guard.value.removeprefix("ON_").lower()
            fallback = WorkflowEdgeSpec(
                key=f"{node.local_id}-fallback-{suffix}",
                source=node.local_id,
                target=fallback_terminal,
                kind=GraphEdgeKind.CONDITION,
                label=f"fail closed: {suffix}",
                guard=guard,
            )
            edges.append(fallback)
            outgoing[node.local_id].append(fallback)

    gates = [
        GateSpec(
            gate_id=f"dynamic-{node.local_id}",
            node_key=node.local_id,
            summary=node.objective,
            required_for_risk_gte=node.risk_level,
        )
        for node in proposal.nodes
        if node.kind in {GraphNodeKind.GATE, GraphNodeKind.HUMAN}
    ]
    retries = [
        edge.max_traversals or 0
        for edge in proposal.edges
        if edge.kind is GraphEdgeKind.RETRY
    ]
    draft = WorkflowTemplate(
        template_record_id=new_id("workflow_template"),
        template_id=f"dynamic-{normalized_graph_hash[:20]}",
        version="2.0.0",
        mode=ExecutionMode.GRAPH,
        # Proposal provenance lives in immutable RunGraphRevision records. The
        # executable template body stays content-addressed by normalized graph.
        input_schema={"dynamic_graph_schema": "2.0"},
        nodes=nodes,
        edges=edges,
        global_budget_policy=BudgetPolicy(
            max_node_retries=max(retries, default=0),
            max_replans=0,
        ),
        required_verifiers=list(dict.fromkeys(proposal.expected_verifiers)),
        required_approval_gates=gates,
        checksum="pending",
        status=TemplateStatus.DRAFT,
    )
    problems = validate_template(draft)
    if problems:
        raise DynamicMaterializationError("; ".join(problems))
    return draft.model_copy(
        update={
            "checksum": compute_template_checksum(draft),
            "status": TemplateStatus.VALIDATED,
        }
    )
