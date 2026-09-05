from __future__ import annotations

import pytest

from accretion.contracts import (
    GraphEdgeKind,
    GraphNodeKind,
    RunEdge,
    RunGraph,
    RunNode,
)
from accretion.routing.graph_features import graph_features


def node(node_id: str, kind: GraphNodeKind) -> RunNode:
    return RunNode(node_id=node_id, key=node_id, kind=kind, label=node_id)


def edge(source: str, target: str, kind: GraphEdgeKind = GraphEdgeKind.NORMAL) -> RunEdge:
    key = f"{source}-{target}-{kind.value}"
    return RunEdge(
        edge_id=key,
        key=key,
        source=source,
        target=target,
        kind=kind,
    )


def graph(*, nodes: list[RunNode], edges: list[RunEdge]) -> RunGraph:
    return RunGraph(
        run_graph_id="rgr_graph_features",
        run_id="run_graph_features",
        task_id="tsk_graph_features",
        template_record_id="tpl_record_graph_features",
        template_id="graph-features",
        template_version="1.0.0",
        template_checksum="graph-features-checksum",
        nodes=nodes,
        edges=edges,
    )


def test_longest_path_depth_and_unweighted_critical_path_are_exact() -> None:
    run_graph = graph(
        nodes=[
            node("root", GraphNodeKind.AGENT),
            node("plan", GraphNodeKind.TASK),
            node("target", GraphNodeKind.TOOL),
            node("verify", GraphNodeKind.VERIFIER),
            node("done", GraphNodeKind.TERMINAL),
            node("side", GraphNodeKind.HUMAN),
        ],
        edges=[
            edge("root", "plan"),
            edge("plan", "target"),
            edge("target", "verify"),
            edge("verify", "done"),
            edge("root", "side"),
            edge("side", "done"),
        ],
    )

    target = graph_features(run_graph, "target", attempt=3)
    side = graph_features(run_graph, "side", attempt=1)

    assert target.depth == 2
    assert target.critical_path
    assert target.parent_node_types == [GraphNodeKind.TASK]
    assert target.child_node_types == [GraphNodeKind.VERIFIER]
    assert target.retry_number == 2
    assert side.depth == 1
    assert not side.critical_path


def test_loop_back_and_retry_edges_do_not_change_structural_features() -> None:
    structural = graph(
        nodes=[
            node("root", GraphNodeKind.AGENT),
            node("target", GraphNodeKind.TASK),
            node("done", GraphNodeKind.TERMINAL),
        ],
        edges=[edge("root", "target"), edge("target", "done")],
    )
    with_control_edges = structural.model_copy(
        update={
            "edges": [
                *reversed(structural.edges),
                edge("done", "target", GraphEdgeKind.RETRY),
                edge("done", "root", GraphEdgeKind.LOOP_BACK),
            ]
        }
    )

    assert graph_features(with_control_edges, "target", 1) == graph_features(
        structural, "target", 1
    )


def test_neighbour_types_are_deterministic_and_deduplicate_parallel_edges() -> None:
    nodes = [
        node("root", GraphNodeKind.AGENT),
        node("a-parent", GraphNodeKind.GATE),
        node("z-parent", GraphNodeKind.HUMAN),
        node("target", GraphNodeKind.TASK),
        node("a-child", GraphNodeKind.TOOL),
        node("z-child", GraphNodeKind.VERIFIER),
    ]
    edges = [
        edge("root", "a-parent"),
        edge("root", "z-parent"),
        edge("z-parent", "target"),
        edge("a-parent", "target"),
        edge("a-parent", "target").model_copy(
            update={"edge_id": "parallel", "key": "parallel"}
        ),
        edge("target", "z-child"),
        edge("target", "a-child"),
    ]

    forward = graph_features(graph(nodes=nodes, edges=edges), "target", 1)
    reverse = graph_features(graph(nodes=nodes, edges=list(reversed(edges))), "target", 1)

    assert forward == reverse
    assert forward.parent_node_types == [GraphNodeKind.GATE, GraphNodeKind.HUMAN]
    assert forward.child_node_types == [GraphNodeKind.TOOL, GraphNodeKind.VERIFIER]


@pytest.mark.parametrize("kind", [GraphEdgeKind.NORMAL, GraphEdgeKind.RETRY])
def test_missing_edge_endpoint_is_rejected_even_when_edge_is_non_structural(
    kind: GraphEdgeKind,
) -> None:
    malformed = graph(
        nodes=[node("root", GraphNodeKind.AGENT)],
        edges=[edge("root", "missing", kind)],
    )

    with pytest.raises(ValueError, match="references a missing node"):
        graph_features(malformed, "root", 1)


def test_structural_cycle_duplicate_node_id_and_missing_target_are_rejected() -> None:
    cycle = graph(
        nodes=[node("a", GraphNodeKind.AGENT), node("b", GraphNodeKind.TASK)],
        edges=[edge("a", "b"), edge("b", "a")],
    )
    duplicate = graph(
        nodes=[node("same", GraphNodeKind.AGENT), node("same", GraphNodeKind.TASK)],
        edges=[],
    )
    valid = graph(nodes=[node("root", GraphNodeKind.AGENT)], edges=[])

    with pytest.raises(ValueError, match="structural cycle"):
        graph_features(cycle, "a", 1)
    with pytest.raises(ValueError, match="duplicate node ids"):
        graph_features(duplicate, "same", 1)
    with pytest.raises(ValueError, match="has no node"):
        graph_features(valid, "missing", 1)


@pytest.mark.parametrize("attempt", [0, -1, True])
def test_attempt_must_be_one_based(attempt: int) -> None:
    valid = graph(nodes=[node("root", GraphNodeKind.AGENT)], edges=[])

    with pytest.raises(ValueError, match="greater than or equal to one"):
        graph_features(valid, "root", attempt)
