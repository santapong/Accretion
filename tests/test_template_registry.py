from __future__ import annotations

import pytest

from accretion.contracts import (
    BudgetPolicy,
    EdgeGuard,
    ExecutionMode,
    ExpectedHorizon,
    GraphEdgeKind,
    GraphNodeKind,
    RiskLevel,
    TaskBudgets,
    TaskProfile,
    TemplateStatus,
)
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.planning import evaluate_override, select_strategy
from accretion.templates import (
    ALL_TEMPLATES,
    ALLOWED_TEMPLATES_BY_MODE,
    FEEDBACK_LOOP_V1,
    FIXED_GRAPH_V1,
    HYBRID_RD_V1,
    SAFE_UNKNOWN_V1,
    compute_template_checksum,
    instantiate_run_graph,
    seed_templates,
    validate_template,
)

# The durable P2 loop topology: node keys and edge (key, kind, label) triples
# must stay identical so historical event node_ids and projection edge ids
# keep resolving (V01-P3-005 parity).
P2_LOOP_NODE_KEYS = ("initialize", "act", "observe", "evaluate", "verify", "complete")
P2_LOOP_EDGES = {
    ("initialize-act", GraphEdgeKind.NORMAL, None),
    ("act-observe", GraphEdgeKind.NORMAL, None),
    ("observe-evaluate", GraphEdgeKind.NORMAL, None),
    ("evaluate-act", GraphEdgeKind.LOOP_BACK, "repair"),
    ("evaluate-verify", GraphEdgeKind.CONDITION, "candidate"),
    ("verify-complete", GraphEdgeKind.CONDITION, "pass"),
}


def profile(risk: RiskLevel = RiskLevel.LOW, *, irreversible: bool = False) -> TaskProfile:
    return TaskProfile(
        profile_id=new_id("profile"),
        task_id=new_id("task"),
        complexity=0.5,
        structure_certainty=0.5,
        feedback_dependency=0.5,
        dependency_complexity=0.5,
        parallelism_potential=0.5,
        uncertainty=0.5,
        verifier_strength=0.5,
        risk=risk,
        irreversible_actions=irreversible,
        expected_horizon=ExpectedHorizon.MEDIUM,
        profile_confidence=0.9,
        semantic_rationale="fixture",
    )


def test_all_five_templates_are_validated_with_stable_checksums() -> None:
    assert [template.template_id for template in ALL_TEMPLATES] == [
        "direct-v1",
        "feedback-loop-v1",
        "fixed-graph-v1",
        "hybrid-rd-v1",
        "safe-unknown-v1",
    ]
    for template in ALL_TEMPLATES:
        assert template.status is TemplateStatus.VALIDATED
        assert validate_template(template) == []
        assert compute_template_checksum(template) == template.checksum


async def test_seed_templates_is_idempotent() -> None:
    store = MemoryStore()
    first = await seed_templates(store)
    second = await seed_templates(store)
    assert first == second
    validated = await store.list_workflow_templates(TemplateStatus.VALIDATED)
    assert len(validated) == 5


def test_feedback_loop_template_preserves_p2_topology() -> None:
    assert tuple(node.key for node in FEEDBACK_LOOP_V1.nodes) == P2_LOOP_NODE_KEYS
    assert {
        (edge.key, edge.kind, edge.label) for edge in FEEDBACK_LOOP_V1.edges
    } == P2_LOOP_EDGES
    evaluate = next(node for node in FEEDBACK_LOOP_V1.nodes if node.key == "evaluate")
    assert evaluate.kind is GraphNodeKind.LOOP
    assert evaluate.loop is not None
    assert evaluate.loop.verify_in_region is True
    assert set(evaluate.loop.region_keys) == {"act", "observe", "evaluate", "verify"}


def test_fixed_graph_template_is_the_risk_template() -> None:
    gates = [node for node in FIXED_GRAPH_V1.nodes if node.kind is GraphNodeKind.GATE]
    assert {node.key for node in gates} == {"approve-plan", "approve-outcome"}
    assert {gate.gate_id for gate in FIXED_GRAPH_V1.required_approval_gates} == {
        "plan-approval",
        "outcome-approval",
    }
    retry_edges = [
        edge for edge in FIXED_GRAPH_V1.edges if edge.kind is GraphEdgeKind.RETRY
    ]
    assert len(retry_edges) == 1
    assert retry_edges[0].guard is EdgeGuard.ON_FAIL
    assert FIXED_GRAPH_V1.global_budget_policy.max_node_retries == 1
    assert FIXED_GRAPH_V1.required_verifiers == [
        "output-contract",
        "git-diff",
        "trajectory-policy",
    ]
    # Denial routes to the terminal explicitly; operator denial is never a
    # silent failure path.
    denied = [edge for edge in FIXED_GRAPH_V1.edges if edge.guard is EdgeGuard.ON_DENIED]
    assert {edge.source for edge in denied} == {"approve-plan", "approve-outcome"}


@pytest.mark.acceptance("V01-P3-006")
def test_hybrid_template_nests_bounded_loop_subflows() -> None:
    children = {
        node.key: node.parent_key
        for node in HYBRID_RD_V1.nodes
        if node.parent_key is not None
    }
    assert children == {
        "experiment-act": "experiment",
        "experiment-observe": "experiment",
        "develop-act": "develop",
        "develop-observe": "develop",
    }
    for owner in ("experiment", "develop"):
        node = next(node for node in HYBRID_RD_V1.nodes if node.key == owner)
        assert node.loop is not None
        assert node.loop.verify_in_region is False
        assert node.loop.budget_fraction == 0.5


def test_safe_unknown_template_has_exactly_one_bounded_replan() -> None:
    replan_available = [
        edge for edge in SAFE_UNKNOWN_V1.edges if edge.guard is EdgeGuard.ON_REPLAN_AVAILABLE
    ]
    replan_exhausted = [
        edge for edge in SAFE_UNKNOWN_V1.edges if edge.guard is EdgeGuard.ON_REPLAN_EXHAUSTED
    ]
    assert len(replan_available) == 1
    assert replan_available[0].kind is GraphEdgeKind.RETRY
    assert replan_available[0].target == "replan"
    assert len(replan_exhausted) == 1
    assert replan_exhausted[0].kind is GraphEdgeKind.CONDITION
    assert SAFE_UNKNOWN_V1.global_budget_policy.max_replans == 1


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda t: t.model_copy(update={"nodes": t.nodes + [t.nodes[0]]}),
            "unique",
        ),
        (
            lambda t: t.model_copy(
                update={
                    "edges": [
                        edge.model_copy(update={"guard": EdgeGuard.ON_SUCCESS})
                        if edge.kind is GraphEdgeKind.NORMAL
                        else edge
                        for edge in t.edges
                    ]
                }
            ),
            "must not carry a guard",
        ),
        (
            lambda t: t.model_copy(
                update={
                    "nodes": [
                        node
                        for node in t.nodes
                        if node.kind is not GraphNodeKind.TERMINAL
                    ],
                    "edges": [edge for edge in t.edges if edge.target != "complete"],
                }
            ),
            "TERMINAL",
        ),
        (
            lambda t: t.model_copy(
                update={"edges": [edge for edge in t.edges if edge.key != "act-verify"]}
            ),
            "covering outcome",
        ),
        (
            lambda t: t.model_copy(update={"global_budget_policy": BudgetPolicy(max_replans=2)}),
            "ON_REPLAN",
        ),
    ],
)
def test_validator_rejects_structural_defects(mutate, expected) -> None:  # type: ignore[no-untyped-def]
    from accretion.templates import DIRECT_V1

    problems = validate_template(mutate(DIRECT_V1))
    assert problems, "expected validation problems"
    assert any(expected in problem for problem in problems)


def test_selector_prefers_the_risk_specific_static_template() -> None:
    decision = select_strategy(profile(RiskLevel.HIGH))
    assert decision.selected_mode is ExecutionMode.GRAPH
    assert decision.selected_template_id == "fixed-graph-v1"
    assert ALLOWED_TEMPLATES_BY_MODE[ExecutionMode.GRAPH] == frozenset({"fixed-graph-v1"})


def test_operator_can_now_override_to_safe_unknown() -> None:
    current = select_strategy(profile())
    override, decision = evaluate_override(
        profile=profile(),
        current=current,
        requested_mode=ExecutionMode.HYBRID,
        requested_template_id="safe-unknown-v1",
        reason="Deliberately choose the bounded fallback.",
        operator_identity="test-operator",
    )
    assert override.accepted
    assert decision is not None
    assert decision.selected_template_id == "safe-unknown-v1"

    denied, no_decision = evaluate_override(
        profile=profile(),
        current=current,
        requested_mode=ExecutionMode.DIRECT,
        requested_template_id="safe-unknown-v1",
        reason="Cross-mode template pairing must stay denied.",
        operator_identity="test-operator",
    )
    assert not denied.accepted
    assert no_decision is None
    assert denied.denial_reason is not None
    assert "direct-v1" in denied.denial_reason


def test_instantiated_graph_matches_event_id_conventions() -> None:
    run_id = new_id("run")
    graph = instantiate_run_graph(
        HYBRID_RD_V1, run_id=run_id, task_id=new_id("task"), budgets=TaskBudgets()
    )
    assert graph.graph_revision == 1
    assert all(node.node_id == f"{run_id}:{node.key}" for node in graph.nodes)
    assert all(edge.edge_id == f"{run_id}:{edge.key}" for edge in graph.edges)
    experiment_act = next(node for node in graph.nodes if node.key == "experiment-act")
    assert experiment_act.parent_id == f"{run_id}:experiment"
    loops = [node for node in graph.nodes if node.kind is GraphNodeKind.LOOP]
    assert all(node.max_iterations == TaskBudgets().max_loop_iterations for node in loops)
    assert all(node.iteration == 0 for node in loops)
