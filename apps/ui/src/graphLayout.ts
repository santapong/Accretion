import type { GraphProjection } from "./types";

// Deterministic layered layout with no layout-engine dependency (OQ-007):
// positions are a pure function of the projection, so what renders never
// depends on an optional runtime import. Return/retry edges are ignored for
// layering; subflow children are laid out inside their parent's bounds.

export interface NodeGeometry {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type GraphLayout = Record<string, NodeGeometry>;

const NODE_WIDTH = 168;
const NODE_HEIGHT = 112;
const CHILD_GAP = 16;
const GROUP_PADDING_X = 20;
const GROUP_PADDING_TOP = 64;
const GROUP_PADDING_BOTTOM = 20;
const COLUMN_GAP = 64;
const ROW_GAP = 44;

const RETURN_EDGE_KINDS = new Set(["LOOP_BACK", "RETRY", "ERROR"]);

export function layoutProjection(projection: GraphProjection): GraphLayout {
  const nodes = projection.nodes ?? [];
  const edges = projection.edges ?? [];
  const parentOf = new Map<string, string>();
  const childrenOf = new Map<string, string[]>();
  for (const node of nodes) {
    if (node.parent_id) {
      parentOf.set(node.node_id, node.parent_id);
      const children = childrenOf.get(node.parent_id) ?? [];
      children.push(node.node_id);
      childrenOf.set(node.parent_id, children);
    }
  }

  // Group parents grow to hold their children in a single padded row.
  const sizes = new Map<string, { width: number; height: number }>();
  for (const node of nodes) {
    const children = childrenOf.get(node.node_id) ?? [];
    if (children.length) {
      sizes.set(node.node_id, {
        width:
          GROUP_PADDING_X * 2 +
          children.length * NODE_WIDTH +
          (children.length - 1) * CHILD_GAP,
        height: GROUP_PADDING_TOP + NODE_HEIGHT + GROUP_PADDING_BOTTOM,
      });
    } else {
      sizes.set(node.node_id, { width: NODE_WIDTH, height: NODE_HEIGHT });
    }
  }

  // Longest-path layering over forward edges between top-level nodes;
  // child endpoints collapse onto their parent.
  const topLevel = nodes.filter((node) => !node.parent_id).map((node) => node.node_id);
  const topLevelSet = new Set(topLevel);
  const resolve = (nodeId: string) => parentOf.get(nodeId) ?? nodeId;
  const forward: Array<[string, string]> = [];
  for (const edge of edges) {
    if (RETURN_EDGE_KINDS.has(edge.kind)) continue;
    const source = resolve(edge.source);
    const target = resolve(edge.target);
    if (source !== target && topLevelSet.has(source) && topLevelSet.has(target)) {
      forward.push([source, target]);
    }
  }
  const layers = new Map<string, number>(topLevel.map((nodeId) => [nodeId, 0]));
  // Bounded relaxation keeps the pass deterministic even on unexpected cycles.
  for (let pass = 0; pass < topLevel.length; pass += 1) {
    let changed = false;
    for (const [source, target] of forward) {
      const proposed = (layers.get(source) ?? 0) + 1;
      if (proposed > (layers.get(target) ?? 0)) {
        layers.set(target, proposed);
        changed = true;
      }
    }
    if (!changed) break;
  }

  const byLayer = new Map<number, string[]>();
  for (const nodeId of topLevel) {
    const layer = layers.get(nodeId) ?? 0;
    const row = byLayer.get(layer) ?? [];
    row.push(nodeId);
    byLayer.set(layer, row);
  }

  const layout: GraphLayout = {};
  const layerXs: number[] = [];
  let x = 0;
  const maxLayer = Math.max(0, ...byLayer.keys());
  for (let layer = 0; layer <= maxLayer; layer += 1) {
    layerXs[layer] = x;
    const widths = (byLayer.get(layer) ?? []).map((nodeId) => sizes.get(nodeId)!.width);
    x += Math.max(NODE_WIDTH, ...widths) + COLUMN_GAP;
  }
  for (const [layer, members] of byLayer) {
    let y = 0;
    for (const nodeId of members) {
      const size = sizes.get(nodeId)!;
      layout[nodeId] = { x: layerXs[layer], y, width: size.width, height: size.height };
      y += size.height + ROW_GAP;
    }
  }

  // Children sit in template order inside their parent, parent-relative.
  for (const [parentId, children] of childrenOf) {
    const ordered = nodes
      .filter((node) => node.parent_id === parentId)
      .map((node) => node.node_id);
    ordered.forEach((childId, index) => {
      layout[childId] = {
        x: GROUP_PADDING_X + index * (NODE_WIDTH + CHILD_GAP),
        y: GROUP_PADDING_TOP,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
      };
    });
    void children;
  }
  return layout;
}
