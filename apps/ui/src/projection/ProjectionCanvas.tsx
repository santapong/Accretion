import { useMemo } from "react";
import {
  Background,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "../react-flow.css";
import { layoutProjection } from "../graphLayout";
import { StatePill } from "../StatePill";
import type { GraphProjection } from "../types";
import type { NodeBadgeIndex } from "../runBadges";
import type { RoutingBadgeIndex } from "../routingBadges";
import { LoopBackEdge } from "./LoopBackEdge";
import { NodeBadges, RoutingBadges } from "./NodeBadges";
import { ProjectionNodeLabel } from "./ProjectionNodeLabel";

/** The edge type table React Flow resolves `type: "loopBack"` through. */
const edgeTypes = { loopBack: LoopBackEdge };

/**
 * The read-only execution graph, and the two lists that mirror it for assistive technology.
 *
 * ## Why the stylesheet imports live here
 *
 * `@xyflow/react/dist/style.css` is unlayered, so `../react-flow.css` — our overrides for
 * the elements inside the canvas — has to be unlayered too AND has to come after it: several
 * of its rules tie xyflow's own on specificity (`.projection-node-kind-gate` is (0,1,0)
 * against `.react-flow__node-default`) and are settled by source order alone. The two
 * imports therefore travel together, adjacently, in the component that mounts the canvas.
 * `e2e/cssPort.test.ts` asserts that adjacency in the source text and `e2e/style-diff.spec.ts`
 * asserts the resulting order in the built stylesheet, in a browser.
 *
 * ## Why it is passive
 *
 * `nodesDraggable`, `nodesConnectable`, `nodesFocusable`, `edgesFocusable` and
 * `elementsSelectable` are all false, and nothing rendered inside a node is focusable or
 * clickable. AC4-M9-043 is the claim that React Flow stays a PROJECTION: the canvas reads
 * persisted state and offers no way to change it, and the panels above it own every control.
 * `projection/projection.test.ts` pins both halves — that no module under `src/projection/`
 * imports the API client, react-query, `fetch` or `EventSource`, and that driving the canvas
 * issues no request and exposes no interactive role inside a badge.
 */
export function ProjectionCanvas({
  projection,
  badges,
  routing,
}: {
  projection: GraphProjection;
  badges: NodeBadgeIndex;
  routing: RoutingBadgeIndex;
}) {
  const projectionNodes = useMemo(() => projection.nodes ?? [], [projection.nodes]);
  const projectionEdges = useMemo(() => projection.edges ?? [], [projection.edges]);
  const layout = useMemo(() => layoutProjection(projection), [projection]);
  const parentIds = useMemo(
    () => new Set(projectionNodes.map((node) => node.parent_id).filter(Boolean)),
    [projectionNodes],
  );
  const flowNodes = useMemo<Node[]>(() => {
    // React Flow requires subflow parents to precede their children.
    const ordered = [...projectionNodes].sort((left, right) =>
      Number(Boolean(left.parent_id)) - Number(Boolean(right.parent_id)),
    );
    return ordered.map((node) => {
      const geometry = layout[node.node_id] ?? { x: 0, y: 0, width: 168, height: 112 };
      const isGroup = parentIds.has(node.node_id);
      return {
        id: node.node_id,
        position: { x: geometry.x, y: geometry.y },
        parentId: node.parent_id ?? undefined,
        extent: node.parent_id ? ("parent" as const) : undefined,
        initialWidth: geometry.width,
        initialHeight: geometry.height,
        style: isGroup ? { width: geometry.width, height: geometry.height } : undefined,
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        className: [
          "projection-node",
          `projection-node-${node.status.toLowerCase()}`,
          `projection-node-kind-${node.kind.toLowerCase()}`,
          isGroup ? "projection-node-group" : "",
        ].filter(Boolean).join(" "),
        data: {
          label: (
            <ProjectionNodeLabel
              node={node}
              badges={badges.get(node.node_id) ?? []}
              routing={routing.get(node.node_id)}
            />
          ),
        },
      };
    });
  }, [projectionNodes, layout, parentIds, badges, routing]);

  const flowEdges = useMemo<Edge[]>(() => {
    const parentByNode = new Map(
      projectionNodes.map((node) => [node.node_id, node.parent_id ?? null]),
    );
    return projectionEdges.map((edge) => {
      const sharedParent = parentByNode.get(edge.source) != null
        && parentByNode.get(edge.source) === parentByNode.get(edge.target);
      return {
        id: edge.edge_id,
        source: edge.source,
        target: edge.target,
        type: edge.kind === "LOOP_BACK" ? "loopBack" : "smoothstep",
        label: edge.label ?? (edge.kind === "LOOP_BACK" ? "retry" : undefined),
        animated: edge.active,
        data: edge.kind === "LOOP_BACK" && sharedParent ? { compact: true } : undefined,
        className: `projection-edge projection-edge-${edge.kind.toLowerCase().replaceAll("_", "-")}`,
        markerEnd: { type: MarkerType.ArrowClosed, color: edge.active ? "#75db91" : "#657069" },
        style: { stroke: edge.active ? "#75db91" : "#657069", strokeWidth: edge.active ? 2 : 1.25 },
      };
    });
  }, [projectionNodes, projectionEdges]);

  const labels = new Map(projectionNodes.map((node) => [node.node_id, node.label]));
  const loopNode = projectionNodes.find((node) => node.iteration != null && node.max_iterations != null);

  return (
    <section className="projection-card" aria-labelledby="projection-heading">
      <header className="projection-heading">
        <div>
          <p className="eyebrow">Read-only topology</p>
          <h3 id="projection-heading">{projection.workflow_template_id}</h3>
        </div>
        <div className="projection-meta">
          {loopNode ? <span className="iteration-badge">Iteration {loopNode.iteration} / {loopNode.max_iterations}</span> : null}
          <span>Graph v{projection.run_graph_version}</span>
        </div>
      </header>
      <div className="projection-flow" aria-label="Execution graph">
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          edgeTypes={edgeTypes}
          nodesDraggable={false}
          nodesConnectable={false}
          nodesFocusable={false}
          edgesFocusable={false}
          elementsSelectable={false}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          minZoom={0.35}
          maxZoom={1.5}
          colorMode="dark"
        >
          <Background color="#3a493f" gap={24} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <ul className="projection-node-summary" aria-label="Projection node states">
        {projectionNodes.map((node) => (
          <li key={node.node_id}>
            <span>{node.label}</span>
            <StatePill state={node.status} />
            <NodeBadges badges={badges.get(node.node_id) ?? []} />
            <RoutingBadges badge={routing.get(node.node_id)} />
          </li>
        ))}
      </ul>
      <ul className="projection-routes" aria-label="Projection routes">
        {projectionEdges.map((edge) => (
          <li
            key={edge.edge_id}
            className={edge.kind === "LOOP_BACK" ? "loop-route" : undefined}
            data-edge-visual={edge.kind === "LOOP_BACK" ? "curved-loop-back" : "standard"}
          >
            <span>{labels.get(edge.source) ?? edge.source} → {labels.get(edge.target) ?? edge.target}</span>
            <strong>{edge.kind.replaceAll("_", " ")}</strong>
            <small>{edge.traversal_count} {edge.traversal_count === 1 ? "traversal" : "traversals"}</small>
          </li>
        ))}
      </ul>
    </section>
  );
}
