import { expect, test } from "vitest";
import { layoutProjection } from "./graphLayout";
import type { GraphProjection } from "./types";

const schemaVersion = { schema_version: "1.0" } as const;

const projection: GraphProjection = {
  schema_version: "1.0",
  version: "graph-projection-v1",
  run_id: "run_layout",
  workflow_template_id: "hybrid-rd-v1",
  run_graph_version: 1,
  generated_at: "2026-08-21T00:00:00Z",
  nodes: [
    { ...schemaVersion, node_id: "a", kind: "TASK", label: "Initialize", status: "SUCCEEDED", provider: null, artifact_count: 0, risk: "LOW" },
    { ...schemaVersion, node_id: "b", kind: "LOOP", label: "Experiment", status: "RUNNING", provider: null, artifact_count: 0, risk: "LOW" },
    { ...schemaVersion, node_id: "b1", parent_id: "b", kind: "AGENT", label: "Act", status: "RUNNING", provider: "FAKE", artifact_count: 0, risk: "LOW" },
    { ...schemaVersion, node_id: "b2", parent_id: "b", kind: "TOOL", label: "Observe", status: "PENDING", provider: null, artifact_count: 0, risk: "LOW" },
    { ...schemaVersion, node_id: "c", kind: "TERMINAL", label: "Complete", status: "PENDING", provider: null, artifact_count: 0, risk: "LOW" },
  ],
  edges: [
    { ...schemaVersion, edge_id: "a-b", source: "a", target: "b", kind: "NORMAL", active: true, traversal_count: 1 },
    { ...schemaVersion, edge_id: "b1-b2", source: "b1", target: "b2", kind: "NORMAL", active: true, traversal_count: 3 },
    { ...schemaVersion, edge_id: "b2-b1", source: "b2", target: "b1", kind: "LOOP_BACK", label: "iterate", active: true, traversal_count: 2 },
    { ...schemaVersion, edge_id: "b-c", source: "b", target: "c", kind: "NORMAL", active: false, traversal_count: 0 },
    // A return edge must not create a cycle in the layering pass.
    { ...schemaVersion, edge_id: "c-a", source: "c", target: "a", kind: "RETRY", active: false, traversal_count: 0 },
  ],
};

test("layout is deterministic and layers follow forward edges only", () => {
  const first = layoutProjection(projection);
  const second = layoutProjection(projection);
  expect(second).toEqual(first);
  expect(first.a.x).toBeLessThan(first.b.x);
  expect(first.b.x).toBeLessThan(first.c.x);
});

test("subflow children sit inside their parent bounds", () => {
  const layout = layoutProjection(projection);
  const parent = layout.b;
  for (const childId of ["b1", "b2"]) {
    const child = layout[childId];
    expect(child.x).toBeGreaterThanOrEqual(0);
    expect(child.y).toBeGreaterThanOrEqual(0);
    expect(child.x + child.width).toBeLessThanOrEqual(parent.width);
    expect(child.y + child.height).toBeLessThanOrEqual(parent.height);
  }
  expect(parent.width).toBeGreaterThan(layout.a.width);
});
