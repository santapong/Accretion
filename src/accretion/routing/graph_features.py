"""Deterministic structural graph features for one routed node."""

from __future__ import annotations

import heapq

from accretion.contracts import GraphEdgeKind, RunGraph
from accretion.contracts.routing import GraphFeatures

_NON_STRUCTURAL_EDGES = frozenset({GraphEdgeKind.LOOP_BACK, GraphEdgeKind.RETRY})


def graph_features(graph: RunGraph, node_id: str, attempt: int) -> GraphFeatures:
    """Derive frozen topology features from a persisted run graph.

    ``depth`` is the number of structural edges on the longest path from any root to
    ``node_id`` (so roots have depth zero). ``critical_path`` is true when the node lies
    on at least one globally longest root-to-leaf structural path. This is an unweighted
    topology property, not an estimate of runtime latency.

    Loop-back and retry edges express bounded execution control rather than structural
    precedence, so they are excluded from neighbours and path calculations. Their
    endpoints are still validated: a malformed persisted edge is never silently ignored.
    """

    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("attempt must be an integer greater than or equal to one")

    nodes = {node.node_id: node for node in graph.nodes}
    if len(nodes) != len(graph.nodes):
        raise ValueError("run graph contains duplicate node ids")
    if node_id not in nodes:
        raise ValueError(f"run graph has no node {node_id!r}")

    parents: dict[str, set[str]] = {key: set() for key in nodes}
    children: dict[str, set[str]] = {key: set() for key in nodes}
    for edge in graph.edges:
        if edge.source not in nodes or edge.target not in nodes:
            raise ValueError(
                f"run graph edge {edge.edge_id!r} references a missing node"
            )
        if edge.kind in _NON_STRUCTURAL_EDGES:
            continue
        children[edge.source].add(edge.target)
        parents[edge.target].add(edge.source)

    indegree = {key: len(values) for key, values in parents.items()}
    ready = [key for key, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    topological: list[str] = []
    while ready:
        current = heapq.heappop(ready)
        topological.append(current)
        for child in sorted(children[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(ready, child)
    if len(topological) != len(nodes):
        raise ValueError("run graph contains a structural cycle")

    depth = {key: 0 for key in nodes}
    for current in topological:
        if parents[current]:
            depth[current] = max(depth[parent] + 1 for parent in parents[current])

    distance_to_leaf = {key: 0 for key in nodes}
    for current in reversed(topological):
        if children[current]:
            distance_to_leaf[current] = max(
                distance_to_leaf[child] + 1 for child in children[current]
            )

    longest_path = max(depth.values())
    return GraphFeatures(
        parent_node_types=[nodes[parent].kind for parent in sorted(parents[node_id])],
        child_node_types=[nodes[child].kind for child in sorted(children[node_id])],
        depth=depth[node_id],
        critical_path=depth[node_id] + distance_to_leaf[node_id] == longest_path,
        retry_number=attempt - 1,
    )
