import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import auditEvents from "../__fixtures__/routing/audit-events.json";
import candidateFixtures from "../__fixtures__/routing/candidates.json";
import receiptFixture from "../__fixtures__/routing/receipt.json";
import { routingBadges } from "../routingBadges";
import { RunExecution } from "../RunExecution";
import type {
  ConfigurationCandidate,
  GraphProjection,
  RoutingDecisionReceipt,
  Run,
  ShadowPair,
} from "../types";
import { ProjectionCanvas } from "./index";

/**
 * AC4-M9-043 — React Flow remains a projection only.
 *
 * The claim has two halves and this file proves both, because either alone is satisfiable by
 * something nobody wants. A canvas that renders nothing passes the behavioural half; a canvas
 * that imports the API client and merely happens not to call it during one render passes
 * nothing at all but would go unnoticed until the day it did.
 *
 * So the first half is STRUCTURAL and reads the package's own source: no module under
 * `src/projection/` may name the API client, react-query, `fetch` or `EventSource`. The
 * second half DRIVES the rendered canvas — the zoom controls, the wheel, the nodes, the
 * badges — and asserts that the global `fetch` stub was never called and that nothing inside
 * a badge exposes an interactive role.
 *
 * `.ts` rather than `.tsx`, so the components are constructed with `createElement`: the
 * structural half is a source-text check that has no JSX in it, and keeping the whole claim
 * in one file is worth the two `createElement` calls.
 *
 * The sources are read through `import.meta.glob(..., { query: "?raw" })` rather than through
 * `node:fs`, because a file under `src/` is typechecked against the browser lib by
 * `tsconfig.app.json`, which carries no `@types/node` (`tsconfig.node.json` says so in as many
 * words). Vite hands back the same bytes and the glob has the side benefit of naming every file
 * in the package, which is what the floor below counts.
 */

const SOURCES: Record<string, string> = import.meta.glob("./*.{ts,tsx}", {
  query: "?raw",
  eager: true,
  import: "default",
});

const schemaVersion = { schema_version: "1.0" } as const;

const receipt = receiptFixture as unknown as RoutingDecisionReceipt;
const candidates = candidateFixtures as unknown as ConfigurationCandidate[];

const graph: GraphProjection = {
  schema_version: "1.0",
  version: "loop-projection-v1",
  run_id: "run_projection_fixture",
  workflow_template_id: "feedback-loop-v1",
  run_graph_version: 4,
  generated_at: "2026-08-20T00:01:00Z",
  nodes: [
    { ...schemaVersion, node_id: "act", kind: "LOOP", label: "Act", status: "RUNNING", provider: "FAKE", iteration: 2, max_iterations: 4, artifact_count: 1, risk: "LOW", verifier_state: "PASS" },
    { ...schemaVersion, node_id: "verify", kind: "VERIFIER", label: "Verify", status: "WAITING", provider: "DETERMINISTIC", artifact_count: 0, verifier_state: "FAIL", risk: "LOW" },
  ],
  edges: [
    { ...schemaVersion, edge_id: "act-verify", source: "act", target: "verify", kind: "NORMAL", active: true, traversal_count: 2 },
  ],
};

/** The audit's node → receipt mapping, as `routingIndex.ts` hands it to the run page. */
const byNode = new Map([["act", [receipt.contract_id]]]);

function canvas(shadowPairs: readonly ShadowPair[] = []) {
  return createElement(ProjectionCanvas, {
    projection: graph,
    badges: new Map(),
    routing: routingBadges({
      byNode,
      receipts: [receipt],
      candidates,
      shadowPairs,
      nodes: graph.nodes ?? [],
    }),
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }) as Response));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test("no module in the projection package can reach the API, react-query, fetch or an event stream", () => {
  const sources = Object.entries(SOURCES)
    .map(([path, source]) => [path.replace("./", ""), source] as const)
    .filter(([name]) => !name.endsWith(".test.ts") && !name.endsWith(".test.tsx"));

  // The floor: a package that lost its files would satisfy every check below vacuously.
  expect(sources.map(([name]) => name).sort()).toEqual([
    "LoopBackEdge.tsx",
    "NodeBadges.tsx",
    "ProjectionCanvas.tsx",
    "ProjectionNodeLabel.tsx",
    "index.ts",
  ]);

  for (const [name, source] of sources) {
    // Comments are stripped first: `ProjectionCanvas.tsx` explains at length why it does not
    // fetch, and the word `fetch(` appearing in that explanation is not a fetch.
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
    expect(code, `${name} imports the API client`).not.toContain('from "../api"');
    expect(code, `${name} imports react-query`).not.toContain("@tanstack/react-query");
    expect(code, `${name} calls fetch`).not.toContain("fetch(");
    expect(code, `${name} opens an event stream`).not.toContain("EventSource(");
  }
});

test("the badge reports the decision, runtime, model, tool count, verification, cost and latency", () => {
  render(canvas());

  // Twice: once inside the React Flow node and once in the summary list that mirrors it, and
  // both from the same badge, so the canvas and its accessible twin cannot disagree.
  const badges = document.querySelectorAll<HTMLElement>("[data-routing-decision]");
  expect(badges).toHaveLength(2);
  for (const badge of badges) {
    expect(badge.dataset.routingDecision).toBe("explore");
    expect(badge.dataset.receiptId).toBe(receipt.contract_id);
    expect(badge.textContent).toContain("explore");
    expect(badge.textContent).toContain("runtime claude-cli");
    expect(badge.textContent).toContain("model claude-opus-4");
    expect(badge.textContent).toContain("tools 1");
    expect(badge.textContent).toContain("verification PASS");
    expect(badge.textContent).toContain("cost 4.5");
    expect(badge.textContent).toContain("latency 41000");
  }

  // The node the audit never routed carries no routing badge at all rather than an empty one.
  const summary = screen.getByRole("list", { name: "Projection node states" });
  const rows = within(summary).getAllByRole("listitem");
  expect(rows).toHaveLength(2);
  expect(rows[1].querySelector("[data-routing-decision]")).toBeNull();
});

test("a shadow stage that scored the decision changes the word and nothing else", () => {
  const pair: ShadowPair = {
    schema_version: "1.0",
    executed_receipt_id: receipt.contract_id,
    shadow_receipt_id: "rcp_SHADOWFORKRECEIPT000000000",
    agreement: false,
    projected_utility_delta: 0.04,
    observed_delta: null,
    control_result_id: null,
    shadow_result_id: null,
  };
  const exploited = { ...receipt, decision_type: "EXPLOIT" } as RoutingDecisionReceipt;
  const shadowed = routingBadges({
    byNode,
    receipts: [exploited],
    candidates,
    shadowPairs: [pair],
    nodes: graph.nodes ?? [],
  }).get("act");
  const plain = routingBadges({
    byNode,
    receipts: [exploited],
    candidates,
    shadowPairs: [],
    nodes: graph.nodes ?? [],
  }).get("act");

  expect(shadowed?.decision).toBe("shadowed");
  expect(plain?.decision).toBe("routed");
  expect({ ...shadowed, decision: "routed" }).toEqual(plain);
});

test("the run page badges a node from the receipt the routing panel read, and asks for it once", async () => {
  // The page-level half of the same claim. The canvas is handed its badges as props and
  // subscribes to NOTHING it fetches: `RunExecution.tsx` reads the §17.1 panel's own cache
  // entries with `skipToken`, so the badge appears because the panel asked for the receipt,
  // and the number of requests for it stays one. A canvas that fetched per node would show
  // the same badge and double the count, which is what this counts.
  const routed: GraphProjection = {
    ...graph,
    run_id: "run_routing_fixture",
    nodes: [{ ...schemaVersion, node_id: "n:act", kind: "LOOP", label: "Act", status: "RUNNING", provider: "FAKE", artifact_count: 1, risk: "LOW", verifier_state: "PASS" }],
    edges: [],
  };
  const run: Run = {
    run_id: "run_routing_fixture",
    task_id: "tsk_fixture",
    project_id: "prj_8W5DH3HW6DPAFFPBHQ47R21DK9",
    provider: "FAKE",
    state: "SUCCEEDED",
    last_sequence: 4,
    revision: 2,
  };
  const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
  const missing = () => ({ ok: false, status: 404, json: async () => ({ detail: "not found" }) }) as Response;
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.endsWith("/graph")) return ok(routed);
      if (url.endsWith("/audit")) return ok({ schema_version: "1.0", events: auditEvents });
      if (url.endsWith("/verifications")) return ok([]);
      if (url.endsWith("/loop")) return missing();
      if (url.includes("/api/v1/approvals")) return ok([]);
      if (url.includes("/api/v1/me")) return ok({ memberships: [{ workspace_id: "wks_fixture" }] });
      if (url.includes("/router-models")) return ok([]);
      if (url.includes(`/routing-decisions/${receipt.contract_id}/candidates`)) return ok(candidates);
      if (url.includes(`/routing-decisions/${receipt.contract_id}`)) return ok(receipt);
      return missing();
    }),
  );

  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    createElement(
      QueryClientProvider,
      { client },
      createElement(RunExecution, { run }),
    ),
  );

  const rendered = await screen.findAllByText("explore", { selector: ".node-badge-capability" });
  // Both renderings of the node: the canvas box and the summary list that mirrors it.
  expect(rendered).toHaveLength(2);
  for (const badge of rendered) {
    expect(badge.closest(".node-badge")?.getAttribute("data-receipt-id")).toBe(receipt.contract_id);
    expect(badge.textContent).toBe("explore");
  }
  expect(rendered.some((badge) => badge.closest(".projection-node-content"))).toBe(true);
  expect(
    calls.filter((url) => url.endsWith(`/routing-decisions/${receipt.contract_id}`)),
  ).toHaveLength(1);
});

test("driving the canvas issues no request and exposes no control inside a badge", () => {
  render(canvas());
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  expect(fetchMock).not.toHaveBeenCalled();

  // React Flow's own chrome: zoom in, zoom out, fit view. They move the viewport and are the
  // only buttons the canvas has; a control that changed run state would be here.
  const controls = document.querySelectorAll<HTMLButtonElement>(".react-flow__controls-button");
  expect(controls.length).toBeGreaterThan(0);
  for (const control of controls) fireEvent.click(control);

  const viewport = document.querySelector(".react-flow");
  expect(viewport).not.toBeNull();
  fireEvent.wheel(viewport!, { deltaY: -120 });

  for (const node of document.querySelectorAll(".projection-node")) {
    fireEvent.click(node);
    fireEvent.doubleClick(node);
  }

  for (const badge of document.querySelectorAll<HTMLElement>(".node-badge")) {
    fireEvent.click(badge);
    for (const role of ["button", "link", "checkbox", "textbox", "combobox"] as const) {
      expect(within(badge).queryAllByRole(role), `a ${role} inside a node badge`).toEqual([]);
    }
    expect(badge.getAttribute("tabindex")).toBeNull();
  }

  expect(fetchMock).not.toHaveBeenCalled();
});
