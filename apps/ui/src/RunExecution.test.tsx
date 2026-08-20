import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { RunExecution } from "./RunExecution";
import type { GraphProjection, LoopExecution, Run, VerificationResult } from "./types";

const schemaVersion = { schema_version: "1.0" } as const;

const run: Run = {
  run_id: "run_loop_fixture",
  task_id: "tsk_fixture",
  project_id: "prj_fixture",
  provider: "FAKE",
  state: "RUNNING",
  last_sequence: 12,
  revision: 4,
};

const loop: LoopExecution = {
  schema_version: "1.0",
  loop_execution_id: "loop_fixture",
  run_id: run.run_id,
  node_key: "evaluate",
  attempt: 1,
  spec: {
    schema_version: "1.0",
    loop_id: "loop_fixture",
    version: "loop-engine-v1",
    max_iterations: 4,
    max_wall_time_seconds: 300,
    max_tool_calls: 20,
    max_turns: 10,
    success_condition: "ACCEPTANCE_POLICY_PASS",
    no_progress_condition: "UNCHANGED_EVIDENCE_FINGERPRINT",
    no_progress_window: 2,
    repeated_failure_threshold: 2,
    provider_failure_threshold: 2,
    escalation_target: "HUMAN",
    verifier_refs: ["command-suite"],
  },
  state: {
    schema_version: "1.0",
    iteration: 2,
    latest_observation_ref: "artifact://iteration-002.patch",
    accumulated_evidence_refs: ["artifact://iteration-001.patch"],
    progress_score: 0.5,
    consecutive_no_progress: 0,
    repeated_failure_count: 1,
    provider_failure_count: 0,
    budget_remaining: { wall_time_seconds: 123, tool_calls: 11, turns: 7, iterations: 2 },
  },
  acceptance_policy_ref: "acceptance-policy-v1",
  status: "RUNNING",
  stop_reason: null,
  revision: 4,
  created_at: "2026-08-20T00:00:00Z",
  updated_at: "2026-08-20T00:01:00Z",
};

const graph: GraphProjection = {
  schema_version: "1.0",
  version: "loop-projection-v1",
  run_id: run.run_id,
  workflow_template_id: "feedback-loop-v1",
  run_graph_version: 4,
  generated_at: "2026-08-20T00:01:00Z",
  nodes: [
    { ...schemaVersion, node_id: "initialize", kind: "TASK", label: "Initialize", status: "SUCCEEDED", provider: "DETERMINISTIC", artifact_count: 0, risk: "LOW" },
    { ...schemaVersion, node_id: "act", kind: "LOOP", label: "Act", status: "RUNNING", provider: "FAKE", iteration: 2, max_iterations: 4, artifact_count: 1, risk: "LOW" },
    { ...schemaVersion, node_id: "observe", kind: "TASK", label: "Observe", status: "PENDING", provider: "DETERMINISTIC", artifact_count: 1, risk: "LOW" },
    { ...schemaVersion, node_id: "evaluate", kind: "GATE", label: "Evaluate", status: "WAITING", provider: "DETERMINISTIC", artifact_count: 0, risk: "LOW" },
    { ...schemaVersion, node_id: "verify", kind: "VERIFIER", label: "Verify", status: "WAITING", provider: "DETERMINISTIC", artifact_count: 0, verifier_state: "FAIL", risk: "LOW" },
    { ...schemaVersion, node_id: "complete", kind: "TERMINAL", label: "Complete", status: "PENDING", provider: null, artifact_count: 0, risk: "LOW" },
  ],
  edges: [
    { ...schemaVersion, edge_id: "initialize-act", source: "initialize", target: "act", kind: "NORMAL", active: false, traversal_count: 1 },
    { ...schemaVersion, edge_id: "act-observe", source: "act", target: "observe", kind: "NORMAL", active: true, traversal_count: 2 },
    { ...schemaVersion, edge_id: "observe-evaluate", source: "observe", target: "evaluate", kind: "NORMAL", active: false, traversal_count: 1 },
    { ...schemaVersion, edge_id: "evaluate-observe", source: "evaluate", target: "observe", kind: "LOOP_BACK", label: "repair", active: true, traversal_count: 2 },
    { ...schemaVersion, edge_id: "evaluate-verify", source: "evaluate", target: "verify", kind: "CONDITION", label: "candidate", active: false, traversal_count: 1 },
    { ...schemaVersion, edge_id: "verify-complete", source: "verify", target: "complete", kind: "CONDITION", label: "accepted", active: false, traversal_count: 0 },
  ],
};

const verifications: VerificationResult[] = [{
  schema_version: "1.0",
  verification_id: "ver_fixture",
  run_id: run.run_id,
  iteration_id: "itr_002",
  verifier_id: "command-suite",
  verifier_version: "command-suite-v1",
  target_ref: "artifact://iteration-002.patch",
  status: "FAIL",
  score: 0.25,
  findings: [{
    ...schemaVersion,
    code: "TEST_FAILED",
    severity: "ERROR",
    message: "One acceptance test failed.",
    path: "tests/test_feature.py",
    line: 42,
    evidence_ref: "evidence://pytest-output",
    fingerprint: "finding_fixture",
  }],
  evidence_refs: ["evidence://pytest-output"],
  false_accept_risk_estimate: 0.8,
  executed_at: "2026-08-20T00:01:00Z",
  duration_ms: 812,
}];

function response(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response;
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/loop")) return response(loop);
    if (url.endsWith("/graph")) return response(graph);
    if (url.endsWith("/verifications")) return response(verifications);
    return response({});
  }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test("renders the persisted loop projection, budgets, and curved loop-back route", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={client}><RunExecution run={run} /></QueryClientProvider>,
  );

  expect(await screen.findByRole("heading", { name: "feedback-loop-v1" })).toBeInTheDocument();
  expect(screen.getAllByText("Iteration 2 / 4").length).toBeGreaterThan(0);

  const routes = screen.getByRole("list", { name: "Projection routes" });
  expect(within(routes).getByText("Evaluate → Observe")).toBeInTheDocument();
  expect(within(routes).getByText("LOOP BACK")).toBeInTheDocument();
  expect(within(routes).getAllByText("2 traversals").length).toBeGreaterThan(0);
  expect(within(routes).getByText("LOOP BACK").closest("li")).toHaveAttribute("data-edge-visual", "curved-loop-back");

  const nodeStates = screen.getByRole("list", { name: "Projection node states" });
  const actState = within(nodeStates).getByText("Act").closest("li");
  expect(actState).toHaveTextContent("RUNNING");

  const loopState = screen.getByRole("region", { name: "Loop state" });
  expect(within(loopState).getByText("123s")).toBeInTheDocument();
  expect(within(loopState).getByText("11")).toBeInTheDocument();
  expect(within(loopState).getByText("Not stopped")).toBeInTheDocument();

  const renderedLoopEdge = container.querySelector(".projection-loop-edge");
  expect(renderedLoopEdge?.tagName.toLowerCase()).toBe("path");
});

test("shows verifier outcome, findings, paths, and evidence references", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><RunExecution run={run} /></QueryClientProvider>);

  await screen.findByText("command-suite");
  const panel = screen.getByRole("region", { name: "Verifications" });
  expect(within(panel).getByText("command-suite")).toBeInTheDocument();
  expect(within(panel).getAllByText("FAIL").length).toBeGreaterThan(0);
  expect(within(panel).getByText("One acceptance test failed.")).toBeInTheDocument();
  expect(within(panel).getByText("tests/test_feature.py:42")).toBeInTheDocument();
  expect(within(panel).getByText("evidence://pytest-output")).toBeInTheDocument();
});
