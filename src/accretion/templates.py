"""Code-defined workflow template registry.

Every v0.1 template topology lives here and nowhere else: the graph executor,
the projection builders, and the UI all derive from these declarations. There
is deliberately no API that authors or mutates templates (V01-P3-002); the only
write path is the idempotent, checksum-pinned seed at startup.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import TYPE_CHECKING

from accretion.contracts import (
    BudgetPolicy,
    EdgeGuard,
    ExecutionMode,
    GateSpec,
    GraphEdgeKind,
    GraphNodeKind,
    NodeLoopPolicy,
    RunEdge,
    RunGraph,
    RunNode,
    TaskBudgets,
    TemplateStatus,
    WorkflowEdgeSpec,
    WorkflowNodeSpec,
    WorkflowTemplate,
)
from accretion.ids import new_id

if TYPE_CHECKING:
    from accretion.persistence.store import StateStore


class TemplateDefinitionError(ValueError):
    """A code-defined template failed structural validation at import time."""

    def __init__(self, template_id: str, problems: list[str]) -> None:
        self.template_id = template_id
        self.problems = problems
        joined = "; ".join(problems)
        super().__init__(f"template {template_id} is invalid: {joined}")


_CHECKSUM_EXCLUDED_FIELDS = {"template_record_id", "checksum", "status", "created_at"}

# Outcomes each node kind can produce toward its outgoing edges. ERROR, budget
# exhaustion, pause, and cancellation are handled by executor precedence rules
# and escalation paths, never by per-template guard coverage.
_KIND_OUTCOMES: dict[GraphNodeKind, frozenset[str]] = {
    GraphNodeKind.TASK: frozenset({"SUCCESS"}),
    GraphNodeKind.AGENT: frozenset({"SUCCESS", "FAIL"}),
    GraphNodeKind.TOOL: frozenset({"SUCCESS"}),
    GraphNodeKind.VERIFIER: frozenset({"SUCCESS", "FAIL", "INCONCLUSIVE"}),
    GraphNodeKind.GATE: frozenset({"APPROVED", "DENIED"}),
    GraphNodeKind.LOOP: frozenset({"SUCCESS", "FAIL"}),
    GraphNodeKind.JOIN: frozenset({"SUCCESS"}),
    GraphNodeKind.TERMINAL: frozenset(),
}

_GUARDED_EDGE_KINDS = {GraphEdgeKind.CONDITION, GraphEdgeKind.APPROVAL, GraphEdgeKind.RETRY}
_UNGUARDED_EDGE_KINDS = {GraphEdgeKind.NORMAL, GraphEdgeKind.LOOP_BACK}


def compute_template_checksum(template: WorkflowTemplate) -> str:
    """Content digest over the normative template body, per governance rules."""

    payload = template.model_dump(mode="json", exclude=_CHECKSUM_EXCLUDED_FIELDS)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _region_members(template: WorkflowTemplate) -> set[str]:
    members: set[str] = set()
    for node in template.nodes:
        if node.loop is not None:
            members.update(node.loop.region_keys)
    return members


def validate_template(template: WorkflowTemplate) -> list[str]:
    """Structural fail-closed validation; an empty list means valid."""

    problems: list[str] = []
    nodes = {node.key: node for node in template.nodes}
    if len(nodes) != len(template.nodes):
        problems.append("node keys must be unique")
    edge_keys = [edge.key for edge in template.edges]
    if len(set(edge_keys)) != len(edge_keys):
        problems.append("edge keys must be unique")
    for edge in template.edges:
        if edge.source not in nodes or edge.target not in nodes:
            problems.append(f"edge {edge.key} references an unknown node")
        if edge.kind in _GUARDED_EDGE_KINDS and edge.guard is None:
            problems.append(f"{edge.kind.value} edge {edge.key} requires a guard")
        if edge.kind in _UNGUARDED_EDGE_KINDS and edge.guard is not None:
            problems.append(f"{edge.kind.value} edge {edge.key} must not carry a guard")
    if problems:
        return problems

    inbound: dict[str, list[WorkflowEdgeSpec]] = {key: [] for key in nodes}
    outbound: dict[str, list[WorkflowEdgeSpec]] = {key: [] for key in nodes}
    for edge in template.edges:
        inbound[edge.target].append(edge)
        outbound[edge.source].append(edge)

    entries = [key for key, edges in inbound.items() if not edges]
    if len(entries) != 1:
        problems.append(f"exactly one entry node is required, found {sorted(entries)}")
    elif nodes[entries[0]].kind is not GraphNodeKind.TASK:
        problems.append("the entry node must be a TASK node")

    terminals = [node.key for node in template.nodes if node.kind is GraphNodeKind.TERMINAL]
    if not terminals:
        problems.append("at least one TERMINAL node is required")
    for key in terminals:
        if outbound[key]:
            problems.append(f"TERMINAL node {key} must have no outgoing edges")

    # A LOOP node's region members are entered by the loop engine, not by macro
    # edges: reachability treats the owner as reaching its members, and members
    # as reaching whatever their owner reaches.
    members_by_owner = {
        node.key: set(node.loop.region_keys) - {node.key}
        for node in template.nodes
        if node.kind is GraphNodeKind.LOOP and node.loop is not None
    }
    if entries:
        reachable = {entries[0]}
        frontier = deque(entries[:1])
        while frontier:
            current = frontier.popleft()
            successors = [edge.target for edge in outbound[current]]
            successors.extend(members_by_owner.get(current, ()))
            for target in successors:
                if target not in reachable:
                    reachable.add(target)
                    frontier.append(target)
        unreachable = set(nodes) - reachable
        if unreachable:
            problems.append(f"nodes unreachable from the entry: {sorted(unreachable)}")

    reaches_terminal = set(terminals)
    frontier = deque(terminals)
    while frontier:
        current = frontier.popleft()
        predecessors = [
            edge.source
            for edge in inbound[current]
            if edge.kind is not GraphEdgeKind.LOOP_BACK
        ]
        predecessors.extend(members_by_owner.get(current, ()))
        for source in predecessors:
            if source not in reaches_terminal:
                reaches_terminal.add(source)
                frontier.append(source)
    stranded = set(nodes) - reaches_terminal
    if stranded:
        problems.append(
            f"nodes cannot reach a TERMINAL via non-LOOP_BACK edges: {sorted(stranded)}"
        )

    region_members = _region_members(template)
    region_by_owner: dict[str, set[str]] = {}
    for node in template.nodes:
        if node.kind is GraphNodeKind.LOOP:
            if node.loop is None:
                problems.append(f"LOOP node {node.key} requires a loop policy")
                continue
            region = set(node.loop.region_keys)
            missing = region - set(nodes)
            if missing:
                problems.append(f"LOOP node {node.key} region references {sorted(missing)}")
            if node.loop.act_key not in region:
                problems.append(f"LOOP node {node.key} act_key must be inside its region")
            if node.loop.observe_key is not None and node.loop.observe_key not in region:
                problems.append(f"LOOP node {node.key} observe_key must be inside its region")
            if (
                node.loop.max_iterations_source == "FIXED"
                and node.loop.fixed_max_iterations is None
            ):
                problems.append(f"LOOP node {node.key} FIXED source needs fixed_max_iterations")
            for owner, other in region_by_owner.items():
                overlap = (region - {node.key}) & (other - {owner})
                if overlap:
                    problems.append(
                        f"loop regions {owner} and {node.key} overlap on {sorted(overlap)}"
                    )
            region_by_owner[node.key] = region
        elif node.loop is not None:
            problems.append(f"non-LOOP node {node.key} must not carry a loop policy")

    for edge in template.edges:
        if edge.kind is GraphEdgeKind.LOOP_BACK:
            inside = any(
                edge.source in region and edge.target in region
                for region in region_by_owner.values()
            )
            if not inside:
                problems.append(f"LOOP_BACK edge {edge.key} must stay within one loop region")

    for node in template.nodes:
        if node.parent_key is None:
            continue
        parent = nodes.get(node.parent_key)
        if parent is None or parent.kind is not GraphNodeKind.LOOP:
            problems.append(f"node {node.key} parent must be a LOOP node")
        elif parent.loop is not None and node.key not in parent.loop.region_keys:
            problems.append(f"node {node.key} parent region does not include it")
        if node.parent_key == node.key:
            problems.append(f"node {node.key} cannot be its own parent")

    gate_nodes = {node.key for node in template.nodes if node.kind is GraphNodeKind.GATE}
    gate_specs = {gate.node_key for gate in template.required_approval_gates}
    if gate_nodes != gate_specs:
        problems.append(
            f"GATE nodes {sorted(gate_nodes)} must match required_approval_gates "
            f"{sorted(gate_specs)}"
        )
    gate_ids = [gate.gate_id for gate in template.required_approval_gates]
    if len(set(gate_ids)) != len(gate_ids):
        problems.append("gate ids must be unique")

    replan_guards = {
        edge.guard
        for edge in template.edges
        if edge.guard in {EdgeGuard.ON_REPLAN_AVAILABLE, EdgeGuard.ON_REPLAN_EXHAUSTED}
    }
    if replan_guards:
        if replan_guards != {EdgeGuard.ON_REPLAN_AVAILABLE, EdgeGuard.ON_REPLAN_EXHAUSTED}:
            problems.append("replan guards must appear as a complete pair")
        if template.global_budget_policy.max_replans <= 0:
            problems.append("replan guards require global_budget_policy.max_replans > 0")
    elif template.global_budget_policy.max_replans > 0:
        problems.append("max_replans > 0 requires ON_REPLAN_* guarded edges")

    problems.extend(_validate_guard_coverage(template, nodes, outbound, region_members))
    return problems


def _validate_guard_coverage(
    template: WorkflowTemplate,
    nodes: dict[str, WorkflowNodeSpec],
    outbound: dict[str, list[WorkflowEdgeSpec]],
    region_members: set[str],
) -> list[str]:
    """Outcome->guard matching table, applied to every node outside loop regions.

    SUCCESS is matched by one unguarded NORMAL edge or one ON_SUCCESS guard;
    FAIL requires a non-RETRY catch (ON_FAIL or ON_REPLAN_EXHAUSTED) so retry
    exhaustion can never dead-end; the remaining outcomes each need exactly one
    matching guard. Region members are driven by the loop engine and exempt.
    """

    problems: list[str] = []
    for key, node in nodes.items():
        if key in region_members or node.kind is GraphNodeKind.TERMINAL:
            continue
        edges = outbound[key]
        for outcome in sorted(_KIND_OUTCOMES[node.kind]):
            if outcome == "SUCCESS":
                covering = [
                    edge
                    for edge in edges
                    if (edge.kind is GraphEdgeKind.NORMAL and edge.guard is None)
                    or edge.guard is EdgeGuard.ON_SUCCESS
                ]
            elif outcome == "FAIL":
                covering = [
                    edge
                    for edge in edges
                    if edge.kind is not GraphEdgeKind.RETRY
                    and edge.guard in {EdgeGuard.ON_FAIL, EdgeGuard.ON_REPLAN_EXHAUSTED}
                ]
            else:
                covering = [edge for edge in edges if edge.guard is EdgeGuard(f"ON_{outcome}")]
            if not covering:
                problems.append(f"node {key} has no edge covering outcome {outcome}")
            elif len(covering) > 1:
                keys = sorted(edge.key for edge in covering)
                problems.append(f"node {key} outcome {outcome} matches multiple edges {keys}")
    return problems


def _template(
    *,
    template_id: str,
    mode: ExecutionMode,
    nodes: list[WorkflowNodeSpec],
    edges: list[WorkflowEdgeSpec],
    budget: BudgetPolicy | None = None,
    required_verifiers: list[str] | None = None,
    gates: list[GateSpec] | None = None,
) -> WorkflowTemplate:
    template = WorkflowTemplate(
        template_record_id=new_id("workflow_template"),
        template_id=template_id,
        version="1.0.0",
        mode=mode,
        nodes=nodes,
        edges=edges,
        global_budget_policy=budget or BudgetPolicy(),
        required_verifiers=required_verifiers or [],
        required_approval_gates=gates or [],
        checksum="pending",
        status=TemplateStatus.DRAFT,
    )
    problems = validate_template(template)
    if problems:
        raise TemplateDefinitionError(template_id, problems)
    return template.model_copy(
        update={
            "checksum": compute_template_checksum(template),
            "status": TemplateStatus.VALIDATED,
        }
    )


def _node(
    key: str,
    kind: GraphNodeKind,
    label: str,
    *,
    parent: str | None = None,
    instruction: str | None = None,
    loop: NodeLoopPolicy | None = None,
) -> WorkflowNodeSpec:
    return WorkflowNodeSpec(
        key=key, kind=kind, label=label, parent_key=parent, instruction=instruction, loop=loop
    )


def _edge(
    key: str,
    source: str,
    target: str,
    kind: GraphEdgeKind,
    *,
    label: str | None = None,
    guard: EdgeGuard | None = None,
) -> WorkflowEdgeSpec:
    return WorkflowEdgeSpec(
        key=key, source=source, target=target, kind=kind, label=label, guard=guard
    )


DIRECT_V1 = _template(
    template_id="direct-v1",
    mode=ExecutionMode.DIRECT,
    nodes=[
        _node("initialize", GraphNodeKind.TASK, "Initialize"),
        _node("act", GraphNodeKind.AGENT, "Execute"),
        _node("verify", GraphNodeKind.VERIFIER, "Verify candidate"),
        _node("complete", GraphNodeKind.TERMINAL, "Complete or escalate"),
    ],
    edges=[
        _edge("initialize-act", "initialize", "act", GraphEdgeKind.NORMAL),
        _edge("act-verify", "act", "verify", GraphEdgeKind.NORMAL),
        _edge(
            "act-complete-failed",
            "act",
            "complete",
            GraphEdgeKind.CONDITION,
            label="provider failure",
            guard=EdgeGuard.ON_FAIL,
        ),
        _edge(
            "verify-complete-success",
            "verify",
            "complete",
            GraphEdgeKind.CONDITION,
            label="pass",
            guard=EdgeGuard.ON_SUCCESS,
        ),
        _edge(
            "verify-complete-failed",
            "verify",
            "complete",
            GraphEdgeKind.CONDITION,
            label="fail",
            guard=EdgeGuard.ON_FAIL,
        ),
        _edge(
            "verify-complete-inconclusive",
            "verify",
            "complete",
            GraphEdgeKind.CONDITION,
            label="escalate",
            guard=EdgeGuard.ON_INCONCLUSIVE,
        ),
    ],
)

# Node keys, labels, edge keys, kinds, and labels for the loop region below are
# the durable P2 identifiers (see build_loop_projection); guards are additive.
FEEDBACK_LOOP_V1 = _template(
    template_id="feedback-loop-v1",
    mode=ExecutionMode.LOOP,
    nodes=[
        _node("initialize", GraphNodeKind.TASK, "Initialize"),
        _node("act", GraphNodeKind.AGENT, "Act"),
        _node("observe", GraphNodeKind.TOOL, "Observe workspace"),
        _node(
            "evaluate",
            GraphNodeKind.LOOP,
            "Evaluate feedback",
            loop=NodeLoopPolicy(
                region_keys=["act", "observe", "evaluate", "verify"],
                act_key="act",
                observe_key="observe",
                verify_in_region=True,
            ),
        ),
        _node("verify", GraphNodeKind.VERIFIER, "Verify candidate"),
        _node("complete", GraphNodeKind.TERMINAL, "Complete or escalate"),
    ],
    edges=[
        _edge("initialize-act", "initialize", "act", GraphEdgeKind.NORMAL),
        _edge("act-observe", "act", "observe", GraphEdgeKind.NORMAL),
        _edge("observe-evaluate", "observe", "evaluate", GraphEdgeKind.NORMAL),
        _edge("evaluate-act", "evaluate", "act", GraphEdgeKind.LOOP_BACK, label="repair"),
        _edge(
            "evaluate-verify",
            "evaluate",
            "verify",
            GraphEdgeKind.CONDITION,
            label="candidate",
            guard=EdgeGuard.ON_SUCCESS,
        ),
        _edge(
            "verify-complete",
            "verify",
            "complete",
            GraphEdgeKind.CONDITION,
            label="pass",
            guard=EdgeGuard.ON_SUCCESS,
        ),
    ],
)

FIXED_GRAPH_V1 = _template(
    template_id="fixed-graph-v1",
    mode=ExecutionMode.GRAPH,
    nodes=[
        _node("initialize", GraphNodeKind.TASK, "Initialize"),
        _node(
            "plan",
            GraphNodeKind.AGENT,
            "Plan",
            instruction=(
                "Produce an explicit written plan artifact for the requested change "
                "before any modification is made."
            ),
        ),
        _node("approve-plan", GraphNodeKind.GATE, "Plan approval"),
        _node("act", GraphNodeKind.AGENT, "Act"),
        _node("observe", GraphNodeKind.TOOL, "Observe workspace"),
        _node("verify", GraphNodeKind.VERIFIER, "Verify candidate"),
        _node("approve-outcome", GraphNodeKind.GATE, "Outcome approval"),
        _node("complete", GraphNodeKind.TERMINAL, "Complete or escalate"),
    ],
    edges=[
        _edge("initialize-plan", "initialize", "plan", GraphEdgeKind.NORMAL),
        _edge(
            "plan-approve-plan",
            "plan",
            "approve-plan",
            GraphEdgeKind.APPROVAL,
            label="plan ready",
            guard=EdgeGuard.ON_SUCCESS,
        ),
        _edge(
            "plan-complete-failed",
            "plan",
            "complete",
            GraphEdgeKind.CONDITION,
            label="plan failed",
            guard=EdgeGuard.ON_FAIL,
        ),
        _edge(
            "approve-plan-act",
            "approve-plan",
            "act",
            GraphEdgeKind.CONDITION,
            label="approved",
            guard=EdgeGuard.ON_APPROVED,
        ),
        _edge(
            "approve-plan-complete-denied",
            "approve-plan",
            "complete",
            GraphEdgeKind.CONDITION,
            label="denied",
            guard=EdgeGuard.ON_DENIED,
        ),
        _edge("act-observe", "act", "observe", GraphEdgeKind.NORMAL),
        _edge(
            "act-complete-failed",
            "act",
            "complete",
            GraphEdgeKind.CONDITION,
            label="provider failure",
            guard=EdgeGuard.ON_FAIL,
        ),
        _edge("observe-verify", "observe", "verify", GraphEdgeKind.NORMAL),
        _edge(
            "verify-act",
            "verify",
            "act",
            GraphEdgeKind.RETRY,
            label="repair once",
            guard=EdgeGuard.ON_FAIL,
        ),
        _edge(
            "verify-complete-failed",
            "verify",
            "complete",
            GraphEdgeKind.CONDITION,
            label="fail",
            guard=EdgeGuard.ON_FAIL,
        ),
        _edge(
            "verify-approve-outcome",
            "verify",
            "approve-outcome",
            GraphEdgeKind.APPROVAL,
            label="verified",
            guard=EdgeGuard.ON_SUCCESS,
        ),
        _edge(
            "verify-complete-inconclusive",
            "verify",
            "complete",
            GraphEdgeKind.CONDITION,
            label="escalate",
            guard=EdgeGuard.ON_INCONCLUSIVE,
        ),
        _edge(
            "approve-outcome-complete-approved",
            "approve-outcome",
            "complete",
            GraphEdgeKind.CONDITION,
            label="approved",
            guard=EdgeGuard.ON_APPROVED,
        ),
        _edge(
            "approve-outcome-complete-denied",
            "approve-outcome",
            "complete",
            GraphEdgeKind.CONDITION,
            label="denied",
            guard=EdgeGuard.ON_DENIED,
        ),
    ],
    budget=BudgetPolicy(max_node_retries=1),
    required_verifiers=["output-contract", "git-diff", "trajectory-policy"],
    gates=[
        GateSpec(
            gate_id="plan-approval",
            node_key="approve-plan",
            summary="Approve the proposed plan before execution.",
        ),
        GateSpec(
            gate_id="outcome-approval",
            node_key="approve-outcome",
            summary="Approve the verified outcome before completion.",
        ),
    ],
)


def _bounded_loop(owner: str, *, budget_fraction: float) -> NodeLoopPolicy:
    return NodeLoopPolicy(
        region_keys=[f"{owner}-act", f"{owner}-observe"],
        act_key=f"{owner}-act",
        observe_key=f"{owner}-observe",
        verify_in_region=False,
        budget_fraction=budget_fraction,
    )


HYBRID_RD_V1 = _template(
    template_id="hybrid-rd-v1",
    mode=ExecutionMode.HYBRID,
    nodes=[
        _node("initialize", GraphNodeKind.TASK, "Initialize"),
        _node("research", GraphNodeKind.AGENT, "Research"),
        _node("theorize", GraphNodeKind.AGENT, "Theory / hypothesis"),
        _node(
            "experiment",
            GraphNodeKind.LOOP,
            "Experiment loop",
            loop=_bounded_loop("experiment", budget_fraction=0.5),
        ),
        _node("experiment-act", GraphNodeKind.AGENT, "Run experiment", parent="experiment"),
        _node("experiment-observe", GraphNodeKind.TOOL, "Observe results", parent="experiment"),
        _node(
            "develop",
            GraphNodeKind.LOOP,
            "Develop loop",
            loop=_bounded_loop("develop", budget_fraction=0.5),
        ),
        _node("develop-act", GraphNodeKind.AGENT, "Implement", parent="develop"),
        _node("develop-observe", GraphNodeKind.TOOL, "Observe workspace", parent="develop"),
        _node("verify", GraphNodeKind.VERIFIER, "Verify candidate"),
        _node("complete", GraphNodeKind.TERMINAL, "Complete or escalate"),
    ],
    edges=[
        _edge("initialize-research", "initialize", "research", GraphEdgeKind.NORMAL),
        _edge("research-theorize", "research", "theorize", GraphEdgeKind.NORMAL),
        _edge(
            "research-complete-failed",
            "research",
            "complete",
            GraphEdgeKind.CONDITION,
            label="provider failure",
            guard=EdgeGuard.ON_FAIL,
        ),
        _edge("theorize-experiment", "theorize", "experiment", GraphEdgeKind.NORMAL),
        _edge(
            "theorize-complete-failed",
            "theorize",
            "complete",
            GraphEdgeKind.CONDITION,
            label="provider failure",
            guard=EdgeGuard.ON_FAIL,
        ),
        _edge("experiment-develop", "experiment", "develop", GraphEdgeKind.NORMAL),
        _edge(
            "experiment-complete-failed",
            "experiment",
            "complete",
            GraphEdgeKind.CONDITION,
            label="loop failed",
            guard=EdgeGuard.ON_FAIL,
        ),
        _edge(
            "experiment-act-observe",
            "experiment-act",
            "experiment-observe",
            GraphEdgeKind.NORMAL,
        ),
        _edge(
            "experiment-observe-act",
            "experiment-observe",
            "experiment-act",
            GraphEdgeKind.LOOP_BACK,
            label="iterate",
        ),
        _edge("develop-verify", "develop", "verify", GraphEdgeKind.NORMAL),
        _edge(
            "develop-complete-failed",
            "develop",
            "complete",
            GraphEdgeKind.CONDITION,
            label="loop failed",
            guard=EdgeGuard.ON_FAIL,
        ),
        _edge("develop-act-observe", "develop-act", "develop-observe", GraphEdgeKind.NORMAL),
        _edge(
            "develop-observe-act",
            "develop-observe",
            "develop-act",
            GraphEdgeKind.LOOP_BACK,
            label="iterate",
        ),
        _edge(
            "verify-complete-success",
            "verify",
            "complete",
            GraphEdgeKind.CONDITION,
            label="pass",
            guard=EdgeGuard.ON_SUCCESS,
        ),
        _edge(
            "verify-complete-failed",
            "verify",
            "complete",
            GraphEdgeKind.CONDITION,
            label="fail",
            guard=EdgeGuard.ON_FAIL,
        ),
        _edge(
            "verify-complete-inconclusive",
            "verify",
            "complete",
            GraphEdgeKind.CONDITION,
            label="escalate",
            guard=EdgeGuard.ON_INCONCLUSIVE,
        ),
    ],
)

SAFE_UNKNOWN_V1 = _template(
    template_id="safe-unknown-v1",
    mode=ExecutionMode.HYBRID,
    nodes=[
        _node("initialize", GraphNodeKind.TASK, "Initialize"),
        _node("plan", GraphNodeKind.AGENT, "Plan"),
        _node(
            "loop",
            GraphNodeKind.LOOP,
            "Bounded execution loop",
            loop=_bounded_loop("loop", budget_fraction=1.0),
        ),
        _node("loop-act", GraphNodeKind.AGENT, "Act", parent="loop"),
        _node("loop-observe", GraphNodeKind.TOOL, "Observe workspace", parent="loop"),
        _node(
            "replan",
            GraphNodeKind.AGENT,
            "Replan once",
            instruction=(
                "Revise the plan once using the verifier findings, staying within "
                "the same bounded topology."
            ),
        ),
        _node("verify", GraphNodeKind.VERIFIER, "Verify candidate"),
        _node("complete", GraphNodeKind.TERMINAL, "Complete or escalate"),
    ],
    edges=[
        _edge("initialize-plan", "initialize", "plan", GraphEdgeKind.NORMAL),
        _edge("plan-loop", "plan", "loop", GraphEdgeKind.NORMAL),
        _edge(
            "plan-complete-failed",
            "plan",
            "complete",
            GraphEdgeKind.CONDITION,
            label="provider failure",
            guard=EdgeGuard.ON_FAIL,
        ),
        _edge("loop-verify", "loop", "verify", GraphEdgeKind.NORMAL),
        _edge(
            "loop-complete-failed",
            "loop",
            "complete",
            GraphEdgeKind.CONDITION,
            label="loop failed",
            guard=EdgeGuard.ON_FAIL,
        ),
        _edge("loop-act-observe", "loop-act", "loop-observe", GraphEdgeKind.NORMAL),
        _edge(
            "loop-observe-act",
            "loop-observe",
            "loop-act",
            GraphEdgeKind.LOOP_BACK,
            label="iterate",
        ),
        _edge(
            "verify-complete-success",
            "verify",
            "complete",
            GraphEdgeKind.CONDITION,
            label="pass",
            guard=EdgeGuard.ON_SUCCESS,
        ),
        _edge(
            "verify-replan",
            "verify",
            "replan",
            GraphEdgeKind.RETRY,
            label="replan once",
            guard=EdgeGuard.ON_REPLAN_AVAILABLE,
        ),
        _edge(
            "verify-complete-exhausted",
            "verify",
            "complete",
            GraphEdgeKind.CONDITION,
            label="escalate",
            guard=EdgeGuard.ON_REPLAN_EXHAUSTED,
        ),
        _edge(
            "verify-complete-inconclusive",
            "verify",
            "complete",
            GraphEdgeKind.CONDITION,
            label="escalate",
            guard=EdgeGuard.ON_INCONCLUSIVE,
        ),
        _edge("replan-loop", "replan", "loop", GraphEdgeKind.NORMAL),
        _edge(
            "replan-complete-failed",
            "replan",
            "complete",
            GraphEdgeKind.CONDITION,
            label="provider failure",
            guard=EdgeGuard.ON_FAIL,
        ),
    ],
    budget=BudgetPolicy(max_replans=1),
)

ALL_TEMPLATES: tuple[WorkflowTemplate, ...] = (
    DIRECT_V1,
    FEEDBACK_LOOP_V1,
    FIXED_GRAPH_V1,
    HYBRID_RD_V1,
    SAFE_UNKNOWN_V1,
)

DEFAULT_TEMPLATE_BY_MODE: dict[ExecutionMode, str] = {
    ExecutionMode.DIRECT: "direct-v1",
    ExecutionMode.LOOP: "feedback-loop-v1",
    ExecutionMode.GRAPH: "fixed-graph-v1",
    ExecutionMode.HYBRID: "hybrid-rd-v1",
}

SAFE_UNKNOWN_TEMPLATE = "safe-unknown-v1"

ALLOWED_TEMPLATES_BY_MODE: dict[ExecutionMode, frozenset[str]] = {
    ExecutionMode.DIRECT: frozenset({"direct-v1"}),
    ExecutionMode.LOOP: frozenset({"feedback-loop-v1"}),
    ExecutionMode.GRAPH: frozenset({"fixed-graph-v1"}),
    ExecutionMode.HYBRID: frozenset({"hybrid-rd-v1", "safe-unknown-v1"}),
}


async def seed_templates(store: StateStore) -> list[WorkflowTemplate]:
    """Idempotently persist the code-defined templates; drift fails closed.

    A stored row with the same (template_id, version) but a different checksum
    is a deployment error and aborts startup rather than being silently
    accepted.
    """

    return [await store.upsert_workflow_template(template) for template in ALL_TEMPLATES]


def instantiate_run_graph(
    template: WorkflowTemplate,
    *,
    run_id: str,
    task_id: str,
    budgets: TaskBudgets,
) -> RunGraph:
    """Materialize a validated template into a per-run graph (V01-P3-001)."""

    if template.status is not TemplateStatus.VALIDATED:
        raise ValueError(
            f"template {template.template_id} is {template.status.value}; "
            "only VALIDATED templates may instantiate a run graph"
        )
    if compute_template_checksum(template) != template.checksum:
        raise ValueError(f"template {template.template_id} content drifted from its checksum")

    def node_id(key: str) -> str:
        return f"{run_id}:{key}"

    nodes = []
    for spec in template.nodes:
        max_iterations: int | None = None
        if spec.loop is not None:
            max_iterations = (
                spec.loop.fixed_max_iterations
                if spec.loop.max_iterations_source == "FIXED"
                else budgets.max_loop_iterations
            )
        nodes.append(
            RunNode(
                node_id=node_id(spec.key),
                key=spec.key,
                kind=spec.kind,
                label=spec.label,
                parent_id=node_id(spec.parent_key) if spec.parent_key else None,
                iteration=0 if spec.kind is GraphNodeKind.LOOP else None,
                max_iterations=max_iterations,
            )
        )
    edges = [
        RunEdge(
            edge_id=f"{run_id}:{spec.key}",
            key=spec.key,
            source=node_id(spec.source),
            target=node_id(spec.target),
            kind=spec.kind,
            label=spec.label,
            guard=spec.guard,
        )
        for spec in template.edges
    ]
    return RunGraph(
        run_graph_id=new_id("run_graph"),
        run_id=run_id,
        task_id=task_id,
        template_record_id=template.template_record_id,
        template_id=template.template_id,
        template_version=template.version,
        template_checksum=template.checksum,
        nodes=nodes,
        edges=edges,
    )
