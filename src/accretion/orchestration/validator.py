from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque

from accretion.contracts import (
    LIVE_PROVIDERS,
    RISK_RANK,
    GraphEdgeKind,
    GraphNodeKind,
    Provider,
    TaskBudgets,
)
from accretion.ids import new_id
from accretion.orchestration.condition_dsl import validate_condition
from accretion.orchestration.models import (
    CapabilitySnapshot,
    ConditionOperator,
    DynamicWorkflowEdgeSpec,
    DynamicWorkflowNodeSpec,
    GraphValidationResult,
    GraphValidationStatus,
    PolicySnapshot,
    RuntimeRequirement,
    ValidationFinding,
    ValidationSeverity,
    WorkflowProposal,
)

MAX_NODES = 32
MAX_EDGES = 64
MAX_DEPTH = 8
MAX_FANOUT = 4
MAX_TRAVERSALS = 3


class GraphValidator:
    version = "graph-validator-v2"

    def validate(
        self,
        proposal: WorkflowProposal,
        capability_snapshot: CapabilitySnapshot,
        policy_snapshot: PolicySnapshot,
        budget: TaskBudgets,
    ) -> GraphValidationResult:
        findings: list[ValidationFinding] = []

        def error(
            code: str, message: str, path: str | None = None, *, repairable: bool = False
        ) -> None:
            findings.append(
                ValidationFinding(
                    code=code,
                    message=message,
                    path=path,
                    severity=ValidationSeverity.ERROR,
                    repairable=repairable,
                )
            )

        if len(proposal.nodes) > MAX_NODES:
            error("GRAPH_TOO_MANY_NODES", f"graph exceeds {MAX_NODES} nodes", repairable=True)
        if len(proposal.edges) > MAX_EDGES:
            error("GRAPH_TOO_MANY_EDGES", f"graph exceeds {MAX_EDGES} edges", repairable=True)
        nodes = {node.local_id: node for node in proposal.nodes}
        if len(nodes) != len(proposal.nodes):
            error("DUPLICATE_NODE", "node identifiers must be unique")
        edge_ids = {edge.local_id for edge in proposal.edges}
        if len(edge_ids) != len(proposal.edges):
            error("DUPLICATE_EDGE", "edge identifiers must be unique")
        outgoing: dict[str, list[DynamicWorkflowEdgeSpec]] = defaultdict(list)
        incoming: dict[str, list[DynamicWorkflowEdgeSpec]] = defaultdict(list)
        for index, edge in enumerate(proposal.edges):
            if edge.source not in nodes or edge.target not in nodes:
                error(
                    "UNKNOWN_ENDPOINT",
                    f"edge {edge.local_id} references an unknown node",
                    f"edges.{index}",
                )
                continue
            outgoing[edge.source].append(edge)
            incoming[edge.target].append(edge)
            if edge.kind in {GraphEdgeKind.LOOP_BACK, GraphEdgeKind.RETRY}:
                if edge.max_traversals is None or edge.max_traversals > MAX_TRAVERSALS:
                    error("UNBOUNDED_TRAVERSAL", f"edge {edge.local_id} exceeds traversal bounds")
            if edge.condition is not None:
                for problem in validate_condition(edge.condition):
                    error("INVALID_CONDITION", problem, f"edges.{index}.condition")
            if edge.kind in {
                GraphEdgeKind.ERROR,
                GraphEdgeKind.FANOUT,
                GraphEdgeKind.MERGE,
                GraphEdgeKind.LOOP_BACK,
            }:
                error(
                    "P5_EXECUTOR_UNSUPPORTED_EDGE",
                    f"edge kind {edge.kind.value} is not executable in the serial P5 scheduler",
                    f"edges.{index}.kind",
                )
            if edge.kind is GraphEdgeKind.CONDITION and (
                edge.condition is None
                or edge.condition.operator is not ConditionOperator.EQ
                or edge.condition.path != "node.outcome"
                or not isinstance(edge.condition.value, str)
                or edge.condition.value.upper()
                not in {"SUCCESS", "FAIL", "INCONCLUSIVE", "APPROVED", "DENIED"}
            ):
                error(
                    "P5_EXECUTOR_UNSUPPORTED_CONDITION",
                    "P5 executable conditions must compare node.outcome to a known outcome",
                    f"edges.{index}.condition",
                )
        roots = [key for key in nodes if not incoming[key]]
        terminals = [key for key, node in nodes.items() if node.kind is GraphNodeKind.TERMINAL]
        if len(roots) != 1:
            error("ROOT_COUNT", "a dynamic graph must have exactly one root", repairable=True)
        if not terminals:
            error("MISSING_TERMINAL", "a dynamic graph requires a terminal node", repairable=True)
        for key, edges in outgoing.items():
            fanout = sum(edge.kind is GraphEdgeKind.FANOUT for edge in edges)
            if fanout > min(MAX_FANOUT, budget.max_parallel_runs):
                error("FANOUT_EXCEEDED", f"node {key} exceeds the fan-out/concurrency ceiling")
        if roots:
            reachable = self._reachable(roots[0], outgoing)
            for key in sorted(set(nodes) - reachable):
                error("UNREACHABLE_NODE", f"node {key} is unreachable", repairable=True)
            if not any(key in reachable for key in terminals):
                error("UNREACHABLE_TERMINAL", "no terminal is reachable from the root")
            has_unbounded_cycle = self._has_unbounded_cycle(roots[0], outgoing)
            if has_unbounded_cycle:
                error("UNBOUNDED_CYCLE", "cycles must use bounded LOOP_BACK or RETRY edges")
            else:
                depth = self._depth(roots[0], outgoing)
                if depth > MAX_DEPTH:
                    error(
                        "GRAPH_TOO_DEEP",
                        f"graph depth {depth} exceeds {MAX_DEPTH}",
                        repairable=True,
                    )
            if policy_snapshot.required_verifiers and self._terminal_reachable_without_kind(
                roots[0], terminals, nodes, outgoing, GraphNodeKind.VERIFIER
            ):
                error("VERIFIER_BYPASS", "a terminal path can bypass independent verification")
            needs_gate = any(
                RISK_RANK[node.risk_level]
                >= RISK_RANK[policy_snapshot.require_approval_at_or_above]
                or bool(
                    set(node.capability_refs)
                    & capability_snapshot.protected_capabilities
                )
                for node in proposal.nodes
            )
            if needs_gate and self._terminal_reachable_without_kind(
                roots[0], terminals, nodes, outgoing, GraphNodeKind.GATE
            ):
                error("APPROVAL_BYPASS", "a high-risk terminal path can bypass approval")
            for node in proposal.nodes:
                protected = (
                    RISK_RANK[node.risk_level]
                    >= RISK_RANK[policy_snapshot.require_approval_at_or_above]
                    or bool(
                        set(node.capability_refs)
                        & capability_snapshot.protected_capabilities
                    )
                )
                if (
                    protected
                    and node.kind
                    in {GraphNodeKind.AGENT, GraphNodeKind.TOOL, GraphNodeKind.LOOP}
                    and self._target_reachable_without_kind(
                        roots[0], node.local_id, nodes, outgoing, GraphNodeKind.GATE
                    )
                ):
                    error(
                        "APPROVAL_BEFORE_PROTECTED_NODE",
                        f"protected node {node.local_id} can execute before an approval gate",
                    )
        for index, node in enumerate(proposal.nodes):
            if len(node.local_id) > 32 or (
                node.kind is GraphNodeKind.LOOP and len(node.local_id) > 24
            ):
                error(
                    "P5_EXECUTOR_IDENTIFIER_TOO_LONG",
                    f"node {node.local_id} cannot be represented by the P5 executor",
                    f"nodes.{index}.local_id",
                )
            self._validate_node(
                node,
                index,
                capability_snapshot,
                policy_snapshot,
                budget,
                error,
            )
        unknown_declared = set(proposal.required_capabilities) - set(
            capability_snapshot.capabilities
        )
        if unknown_declared:
            error("UNKNOWN_CAPABILITY", f"unknown capabilities: {sorted(unknown_declared)}")
        denied_declared = set(proposal.required_capabilities) & policy_snapshot.denied_capabilities
        if denied_declared:
            error("DENIED_CAPABILITY", f"denied capabilities: {sorted(denied_declared)}")
        missing_verifiers = policy_snapshot.required_verifiers - set(proposal.expected_verifiers)
        if missing_verifiers:
            error("MISSING_VERIFIER", f"missing required verifiers: {sorted(missing_verifiers)}")
        self._validate_contract_edges(nodes, proposal.edges, error)
        errors = [item for item in findings if item.severity is ValidationSeverity.ERROR]
        status = GraphValidationStatus.ACCEPT
        if errors:
            status = (
                GraphValidationStatus.REPAIRABLE
                if all(item.repairable for item in errors) and proposal.repair_attempt < 1
                else GraphValidationStatus.REJECT
            )
        graph_hash = self.normalized_hash(proposal) if not errors else None
        return GraphValidationResult(
            validation_id=new_id("graph_validation"),
            proposal_id=proposal.proposal_id,
            status=status,
            errors=errors,
            warnings=[item for item in findings if item.severity is ValidationSeverity.WARNING],
            normalized_graph_hash=graph_hash,
            required_repairs=[item.message for item in errors if item.repairable],
            validator_version=self.version,
        )

    @staticmethod
    def normalized_hash(proposal: WorkflowProposal) -> str:
        normalized = {
            "nodes": [
                item.model_dump(mode="json", exclude={"schema_version"})
                for item in sorted(proposal.nodes, key=lambda item: item.local_id)
            ],
            "edges": [
                item.model_dump(mode="json", exclude={"schema_version"})
                for item in sorted(proposal.edges, key=lambda item: item.local_id)
            ],
            "capabilities": sorted(proposal.required_capabilities),
            "verifiers": sorted(proposal.expected_verifiers),
            "gates": sorted(proposal.expected_approval_gates),
        }
        return hashlib.sha256(
            json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _reachable(root: str, outgoing: dict[str, list[DynamicWorkflowEdgeSpec]]) -> set[str]:
        result: set[str] = set()
        pending = [root]
        while pending:
            key = pending.pop()
            if key in result:
                continue
            result.add(key)
            pending.extend(edge.target for edge in outgoing[key])
        return result

    @staticmethod
    def _depth(root: str, outgoing: dict[str, list[DynamicWorkflowEdgeSpec]]) -> int:
        bounded = {GraphEdgeKind.LOOP_BACK, GraphEdgeKind.RETRY}
        depth = 0
        pending: deque[tuple[str, int]] = deque([(root, 1)])
        visited: dict[str, int] = {}
        while pending:
            key, current = pending.popleft()
            if visited.get(key, 0) >= current:
                continue
            visited[key] = current
            depth = max(depth, current)
            for edge in outgoing[key]:
                if edge.kind not in bounded:
                    pending.append((edge.target, current + 1))
        return depth

    @staticmethod
    def _has_unbounded_cycle(root: str, outgoing: dict[str, list[DynamicWorkflowEdgeSpec]]) -> bool:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> bool:
            visiting.add(key)
            for edge in outgoing[key]:
                if edge.kind in {GraphEdgeKind.LOOP_BACK, GraphEdgeKind.RETRY}:
                    continue
                if edge.target in visiting or (edge.target not in visited and visit(edge.target)):
                    return True
            visiting.remove(key)
            visited.add(key)
            return False

        return visit(root)

    @staticmethod
    def _terminal_reachable_without_kind(
        root: str,
        terminals: list[str],
        nodes: dict[str, DynamicWorkflowNodeSpec],
        outgoing: dict[str, list[DynamicWorkflowEdgeSpec]],
        blocked_kind: GraphNodeKind,
    ) -> bool:
        pending = [root]
        visited: set[str] = set()
        while pending:
            key = pending.pop()
            if key in visited or nodes[key].kind is blocked_kind:
                continue
            if key in terminals:
                return True
            visited.add(key)
            pending.extend(edge.target for edge in outgoing[key])
        return False

    @staticmethod
    def _target_reachable_without_kind(
        root: str,
        target: str,
        nodes: dict[str, DynamicWorkflowNodeSpec],
        outgoing: dict[str, list[DynamicWorkflowEdgeSpec]],
        blocked_kind: GraphNodeKind,
    ) -> bool:
        pending = [root]
        visited: set[str] = set()
        while pending:
            key = pending.pop()
            if key in visited or nodes[key].kind is blocked_kind:
                continue
            if key == target:
                return True
            visited.add(key)
            pending.extend(edge.target for edge in outgoing[key])
        return False

    @staticmethod
    def _validate_node(
        node: DynamicWorkflowNodeSpec,
        index: int,
        capabilities: CapabilitySnapshot,
        policy: PolicySnapshot,
        budget: TaskBudgets,
        error: object,
    ) -> None:
        report = error  # retain a precisely typed local callable below
        assert callable(report)
        path = f"nodes.{index}"
        for capability in node.capability_refs:
            if capability not in capabilities.capabilities:
                report("UNKNOWN_CAPABILITY", f"unknown capability {capability}", path)
            elif capability in policy.denied_capabilities:
                report("DENIED_CAPABILITY", f"capability {capability} is denied", path)
            elif policy.allowed_capabilities and capability not in policy.allowed_capabilities:
                report(
                    "PRIVILEGE_EXPANSION", f"capability {capability} exceeds the task ceiling", path
                )
        for skill in node.skill_refs:
            if skill not in capabilities.skills:
                report("UNKNOWN_SKILL", f"unknown skill {skill}", path)
        for verifier in node.verifier_refs:
            if verifier not in capabilities.verifiers:
                report("UNKNOWN_VERIFIER", f"unknown verifier {verifier}", path)
        if RISK_RANK[node.risk_level] > RISK_RANK[policy.maximum_risk]:
            report("RISK_EXPANSION", f"node {node.local_id} exceeds the risk ceiling", path)
        required_provider = {
            RuntimeRequirement.CLAUDE: Provider.CLAUDE,
            RuntimeRequirement.CODEX: Provider.CODEX,
            RuntimeRequirement.DETERMINISTIC: Provider.DETERMINISTIC,
        }.get(node.runtime_requirement)
        if (
            required_provider is not None
            and required_provider not in capabilities.available_runtimes
        ):
            report("RUNTIME_UNAVAILABLE", f"runtime {required_provider.value} is unavailable", path)
        if (
            required_provider in LIVE_PROVIDERS
            and policy.execution_runtime is not None
            and required_provider is not policy.execution_runtime
        ):
            report(
                "RUNTIME_INCOMPATIBLE",
                f"node {node.local_id} requires {required_provider.value} but the P5 run uses "
                f"{policy.execution_runtime.value}",
                path,
            )
        if node.runtime_requirement is RuntimeRequirement.HUMAN and node.kind not in {
            GraphNodeKind.GATE,
            GraphNodeKind.HUMAN,
        }:
            report(
                "HUMAN_RUNTIME_KIND_MISMATCH",
                "HUMAN runtime is allowed only on GATE or HUMAN nodes",
                path,
            )
        if node.timeout_seconds > budget.wall_time_seconds:
            report(
                "NODE_TIMEOUT_EXCEEDED",
                f"node {node.local_id} exceeds the task wall-time budget",
                path,
                repairable=True,
            )

    @staticmethod
    def _validate_contract_edges(
        nodes: dict[str, DynamicWorkflowNodeSpec],
        edges: list[DynamicWorkflowEdgeSpec],
        error: object,
    ) -> None:
        report = error
        assert callable(report)
        for edge in edges:
            if edge.source not in nodes or edge.target not in nodes:
                continue
            source = nodes[edge.source].output_contract
            target = nodes[edge.target].input_contract
            required = set(target.get("required", []))
            available = set(source.get("properties", {}))
            if required - available:
                report(
                    "CONTRACT_MISMATCH",
                    f"edge {edge.local_id} cannot supply required fields "
                    f"{sorted(required - available)}",
                    repairable=True,
                )
