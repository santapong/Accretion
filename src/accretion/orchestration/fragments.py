from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from accretion.contracts import (
    RISK_RANK,
    GraphEdgeKind,
    GraphNodeKind,
    RiskLevel,
    Task,
    TaskProfile,
    TaskType,
)
from accretion.ids import new_id
from accretion.orchestration.models import (
    ConditionOperator,
    DynamicLoopSpec,
    DynamicWorkflowEdgeSpec,
    DynamicWorkflowNodeSpec,
    PlannerRuntime,
    RuntimeRequirement,
    TypedCondition,
    WorkflowProposal,
)


@dataclass(frozen=True, slots=True)
class WorkflowFragment:
    fragment_id: str
    version: str
    nodes: tuple[DynamicWorkflowNodeSpec, ...]
    edges: tuple[DynamicWorkflowEdgeSpec, ...]


def _node(
    local_id: str,
    kind: GraphNodeKind,
    objective: str,
    *,
    runtime: RuntimeRequirement = RuntimeRequirement.DETERMINISTIC,
    risk: RiskLevel = RiskLevel.LOW,
    verifiers: tuple[str, ...] = (),
    loop: DynamicLoopSpec | None = None,
    fragment: str,
) -> DynamicWorkflowNodeSpec:
    return DynamicWorkflowNodeSpec(
        local_id=local_id,
        kind=kind,
        objective=objective,
        runtime_requirement=runtime,
        risk_level=risk,
        verifier_refs=list(verifiers),
        loop_spec=loop,
        fragment_ref=fragment,
    )


def _edge(
    local_id: str,
    source: str,
    target: str,
    *,
    kind: GraphEdgeKind = GraphEdgeKind.NORMAL,
    status: str | None = None,
    max_traversals: int | None = None,
) -> DynamicWorkflowEdgeSpec:
    condition = (
        TypedCondition(
            operator=ConditionOperator.EQ,
            path="node.outcome",
            value=status,
        )
        if kind is GraphEdgeKind.CONDITION
        else None
    )
    return DynamicWorkflowEdgeSpec(
        local_id=local_id,
        source=source,
        target=target,
        kind=kind,
        condition=condition,
        max_traversals=max_traversals,
    )


SINGLE_ACT_VERIFY = WorkflowFragment(
    fragment_id="single-act-verify",
    version="1.0.0",
    nodes=(
        _node(
            "start",
            GraphNodeKind.TASK,
            "Initialize the typed task.",
            fragment="single-act-verify@1.0.0",
        ),
        _node(
            "act",
            GraphNodeKind.AGENT,
            "Produce the bounded candidate.",
            runtime=RuntimeRequirement.ANY,
            fragment="single-act-verify@1.0.0",
        ),
        _node(
            "verify",
            GraphNodeKind.VERIFIER,
            "Independently verify the candidate.",
            verifiers=("output-contract", "trajectory-policy"),
            fragment="single-act-verify@1.0.0",
        ),
        _node(
            "complete",
            GraphNodeKind.TERMINAL,
            "Commit the verified outcome.",
            fragment="single-act-verify@1.0.0",
        ),
    ),
    edges=(
        _edge("start-act", "start", "act"),
        _edge("act-verify", "act", "verify"),
        _edge("verify-complete", "verify", "complete"),
    ),
)

BOUNDED_REPAIR = WorkflowFragment(
    fragment_id="bounded-repair",
    version="1.0.0",
    nodes=(
        _node(
            "start",
            GraphNodeKind.TASK,
            "Initialize the typed task.",
            fragment="bounded-repair@1.0.0",
        ),
        _node(
            "repair-loop",
            GraphNodeKind.LOOP,
            "Produce, observe, and repair a candidate within the task budget.",
            runtime=RuntimeRequirement.ANY,
            loop=DynamicLoopSpec(max_iterations=3),
            fragment="bounded-repair@1.0.0",
        ),
        _node(
            "verify",
            GraphNodeKind.VERIFIER,
            "Independently verify the candidate.",
            verifiers=("output-contract", "trajectory-policy"),
            fragment="bounded-repair@1.0.0",
        ),
        _node(
            "complete",
            GraphNodeKind.TERMINAL,
            "Commit the verified outcome.",
            fragment="bounded-repair@1.0.0",
        ),
    ),
    edges=(
        _edge("start-loop", "start", "repair-loop"),
        _edge("loop-verify", "repair-loop", "verify"),
        _edge("verify-loop", "verify", "repair-loop", kind=GraphEdgeKind.RETRY, max_traversals=1),
        _edge("verify-complete", "verify", "complete"),
    ),
)

APPROVAL_GATED_CHANGE = WorkflowFragment(
    fragment_id="approval-gated-change",
    version="1.0.0",
    nodes=(
        _node(
            "start",
            GraphNodeKind.TASK,
            "Initialize the typed task.",
            fragment="approval-gated-change@1.0.0",
        ),
        _node(
            "approve-plan",
            GraphNodeKind.GATE,
            "Approve the high-risk plan.",
            risk=RiskLevel.HIGH,
            fragment="approval-gated-change@1.0.0",
        ),
        _node(
            "act",
            GraphNodeKind.AGENT,
            "Produce the approved candidate.",
            runtime=RuntimeRequirement.ANY,
            risk=RiskLevel.HIGH,
            fragment="approval-gated-change@1.0.0",
        ),
        _node(
            "verify",
            GraphNodeKind.VERIFIER,
            "Independently verify the candidate.",
            verifiers=("output-contract", "git-diff", "trajectory-policy"),
            fragment="approval-gated-change@1.0.0",
        ),
        _node(
            "approve-outcome",
            GraphNodeKind.GATE,
            "Approve the verified outcome.",
            risk=RiskLevel.HIGH,
            fragment="approval-gated-change@1.0.0",
        ),
        _node(
            "complete",
            GraphNodeKind.TERMINAL,
            "Commit the approved outcome.",
            fragment="approval-gated-change@1.0.0",
        ),
    ),
    edges=(
        _edge("start-approval", "start", "approve-plan"),
        _edge("approval-act", "approve-plan", "act"),
        _edge("act-verify", "act", "verify"),
        _edge("verify-approval", "verify", "approve-outcome"),
        _edge("approval-complete", "approve-outcome", "complete"),
    ),
)

PARALLEL_ANALYSIS_JOIN = WorkflowFragment(
    fragment_id="dual-analysis-join",
    version="1.0.0",
    nodes=(
        _node(
            "start",
            GraphNodeKind.TASK,
            "Initialize the typed task.",
            fragment="dual-analysis-join@1.0.0",
        ),
        _node(
            "analyze-a",
            GraphNodeKind.AGENT,
            "Analyze the first independent concern.",
            runtime=RuntimeRequirement.ANY,
            fragment="dual-analysis-join@1.0.0",
        ),
        _node(
            "analyze-b",
            GraphNodeKind.AGENT,
            "Analyze the second independent concern.",
            runtime=RuntimeRequirement.ANY,
            fragment="dual-analysis-join@1.0.0",
        ),
        _node(
            "join",
            GraphNodeKind.JOIN,
            "Combine compatible evidence.",
            fragment="dual-analysis-join@1.0.0",
        ),
        _node(
            "verify",
            GraphNodeKind.VERIFIER,
            "Independently verify the combined candidate.",
            verifiers=("output-contract", "trajectory-policy"),
            fragment="dual-analysis-join@1.0.0",
        ),
        _node(
            "complete",
            GraphNodeKind.TERMINAL,
            "Commit the verified outcome.",
            fragment="dual-analysis-join@1.0.0",
        ),
    ),
    edges=(
        # P5 keeps one mutable worktree and evaluates independent analyses in a
        # deterministic order. True speculative parallel candidates belong to P6.
        _edge("start-a", "start", "analyze-a"),
        _edge("a-b", "analyze-a", "analyze-b"),
        _edge("b-join", "analyze-b", "join"),
        _edge("join-verify", "join", "verify"),
        _edge("verify-complete", "verify", "complete"),
    ),
)

FRAGMENTS = {
    fragment.fragment_id: fragment
    for fragment in (
        SINGLE_ACT_VERIFY,
        BOUNDED_REPAIR,
        APPROVAL_GATED_CHANGE,
        PARALLEL_ANALYSIS_JOIN,
    )
}


class FragmentWorkflowPlanner:
    version = "fragment-planner-v2"

    def propose(
        self,
        task: Task,
        profile: TaskProfile,
        *,
        planner_runtime: PlannerRuntime = PlannerRuntime.DETERMINISTIC,
        run_id: str | None = None,
        based_on_graph_revision: int | None = None,
    ) -> WorkflowProposal:
        fragment = self._select_fragment(task, profile)
        verifiers = ["output-contract", "trajectory-policy"]
        if task.envelope.task_type in {TaskType.IMPLEMENT, TaskType.EXPERIMENT}:
            verifiers.insert(1, "git-diff")
        nodes = [
            node.model_copy(
                update={
                    "verifier_refs": verifiers
                    if node.kind is GraphNodeKind.VERIFIER
                    else node.verifier_refs,
                    "capability_refs": list(task.envelope.allowed_capabilities)
                    if node.kind in {GraphNodeKind.AGENT, GraphNodeKind.LOOP}
                    else node.capability_refs,
                    "risk_level": max(
                        (node.risk_level, task.envelope.risk_level),
                        key=lambda item: RISK_RANK[item],
                    )
                    if node.kind is GraphNodeKind.AGENT
                    else node.risk_level,
                }
            )
            for node in fragment.nodes
        ]
        gates = [node.local_id for node in nodes if node.kind is GraphNodeKind.GATE]
        return WorkflowProposal(
            proposal_id=new_id("workflow_proposal"),
            task_id=task.envelope.task_id,
            run_id=run_id,
            based_on_graph_revision=based_on_graph_revision,
            planner_version=self.version,
            planner_runtime=planner_runtime,
            objective=task.envelope.objective,
            assumptions=[
                "Task permissions and budgets remain authoritative.",
                "All candidate output is independently verified.",
            ],
            nodes=nodes,
            edges=list(fragment.edges),
            required_capabilities=sorted(task.envelope.allowed_capabilities),
            expected_verifiers=verifiers,
            expected_approval_gates=gates,
            rationale_summary=(
                f"Composed reviewed fragment {fragment.fragment_id}@{fragment.version}."
            ),
            confidence=max(0.0, min(profile.profile_confidence, 1.0)),
            provenance_refs=[profile.profile_id, task.prompt_contract_id or "prompt:pending"],
            fragment_refs=[f"{fragment.fragment_id}@{fragment.version}"],
        )

    def repair(self, proposal: WorkflowProposal) -> WorkflowProposal:
        if proposal.repair_attempt >= 1:
            raise ValueError("workflow proposal repair budget is exhausted")
        return proposal.model_copy(
            update={
                "proposal_id": new_id("workflow_proposal"),
                "repair_attempt": proposal.repair_attempt + 1,
                "rationale_summary": f"Bounded repair of {proposal.proposal_id}.",
                "provenance_refs": [*proposal.provenance_refs, proposal.proposal_id],
                "created_at": datetime.now(UTC),
            }
        )

    @staticmethod
    def _select_fragment(task: Task, profile: TaskProfile) -> WorkflowFragment:
        if (
            RISK_RANK[task.envelope.risk_level] >= RISK_RANK[RiskLevel.HIGH]
            or profile.irreversible_actions
        ):
            return APPROVAL_GATED_CHANGE
        if (
            profile.parallelism_potential or 0.0
        ) >= 0.65 and task.envelope.budgets.max_parallel_runs >= 2:
            return PARALLEL_ANALYSIS_JOIN
        if (
            profile.feedback_dependency or 0.0
        ) >= 0.55 or task.envelope.budgets.max_loop_iterations > 1:
            return BOUNDED_REPAIR
        return SINGLE_ACT_VERIFY
