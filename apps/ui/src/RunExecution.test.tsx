import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
    if (url.includes("/api/v1/approvals")) return response([]);
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

const hybridRun: Run = {
  run_id: "run_hybrid_fixture",
  task_id: "tsk_hybrid",
  project_id: "prj_fixture",
  provider: "FAKE",
  state: "RUNNING",
  last_sequence: 40,
  revision: 6,
};

const hybridGraph: GraphProjection = {
  schema_version: "1.0",
  version: "graph-projection-v1",
  run_id: hybridRun.run_id,
  workflow_template_id: "hybrid-rd-v1",
  run_graph_version: 1,
  generated_at: "2026-08-21T00:00:00Z",
  nodes: [
    { ...schemaVersion, node_id: "n:research", kind: "AGENT", label: "Research", status: "SUCCEEDED", provider: "FAKE", artifact_count: 0, risk: "LOW" },
    { ...schemaVersion, node_id: "n:experiment", kind: "LOOP", label: "Experiment loop", status: "RUNNING", provider: null, iteration: 3, max_iterations: 5, artifact_count: 2, risk: "LOW" },
    { ...schemaVersion, node_id: "n:experiment-act", parent_id: "n:experiment", kind: "AGENT", label: "Run experiment", status: "RUNNING", provider: "FAKE", artifact_count: 0, risk: "LOW" },
    { ...schemaVersion, node_id: "n:experiment-observe", parent_id: "n:experiment", kind: "TOOL", label: "Observe results", status: "SUCCEEDED", provider: null, artifact_count: 2, risk: "LOW" },
    { ...schemaVersion, node_id: "n:gate", kind: "GATE", label: "Outcome approval", status: "WAITING", provider: null, artifact_count: 0, risk: "HIGH" },
    { ...schemaVersion, node_id: "n:complete", kind: "TERMINAL", label: "Complete or escalate", status: "PENDING", provider: null, artifact_count: 0, risk: "LOW" },
  ],
  edges: [
    { ...schemaVersion, edge_id: "e:research-experiment", source: "n:research", target: "n:experiment", kind: "NORMAL", active: true, traversal_count: 1 },
    { ...schemaVersion, edge_id: "e:experiment-act-observe", source: "n:experiment-act", target: "n:experiment-observe", kind: "NORMAL", active: true, traversal_count: 7 },
    { ...schemaVersion, edge_id: "e:experiment-observe-act", source: "n:experiment-observe", target: "n:experiment-act", kind: "LOOP_BACK", label: "iterate", active: true, traversal_count: 7 },
    { ...schemaVersion, edge_id: "e:experiment-gate", source: "n:experiment", target: "n:gate", kind: "APPROVAL", label: "verified", active: true, traversal_count: 0 },
    { ...schemaVersion, edge_id: "e:gate-complete", source: "n:gate", target: "n:complete", kind: "CONDITION", label: "approved", active: false, traversal_count: 0 },
  ],
};

const pendingApproval = {
  approval_id: "apr_fixture",
  run_id: hybridRun.run_id,
  node_id: "n:gate",
  native_request_id: "gate:approve-outcome",
  method: "accretion/gate",
  summary: "Approve the verified outcome before completion.",
  payload: {},
  status: "PENDING",
  decision: null,
  created_at: "2026-08-21T00:00:00Z",
  decided_at: null,
};

test("renders hybrid subflows without expanding iterations and offers the gate decision", async () => {
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/graph")) return response(hybridGraph);
    if (url.endsWith("/verifications")) return response([]);
    if (url.includes("/decision") && init?.method === "POST") {
      return response({ ...pendingApproval, status: "APPROVED", decision: "APPROVE" });
    }
    if (url.includes("/api/v1/approvals")) return response([pendingApproval]);
    if (url.endsWith("/loop")) return { ok: false, status: 409, json: async () => ({ message: "mismatch" }) } as Response;
    return response({});
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={client}><RunExecution run={hybridRun} /></QueryClientProvider>,
  );

  expect(await screen.findByRole("heading", { name: "hybrid-rd-v1" })).toBeInTheDocument();

  // Node count equals the projection payload even with seven traversals.
  const nodeStates = screen.getByRole("list", { name: "Projection node states" });
  expect(within(nodeStates).getAllByRole("listitem")).toHaveLength(hybridGraph.nodes!.length);
  expect(screen.getAllByText("Iteration 3 / 5").length).toBeGreaterThan(0);

  // Subflow children render inside the parent group container.
  const group = container.querySelector(".projection-node-group");
  expect(group).not.toBeNull();
  const childNode = container.querySelector('[data-id="n:experiment-act"]');
  expect(childNode?.parentElement?.closest('[data-id="n:experiment"]') ?? group).not.toBeNull();

  // The waiting gate surfaces its hint and the approval decision controls.
  expect(screen.getAllByText("Waiting for approval").length).toBeGreaterThan(0);
  const approvalPanel = await screen.findByRole("region", { name: "Pending approvals" });
  expect(within(approvalPanel).getByText("Approve the verified outcome before completion.")).toBeInTheDocument();
  fireEvent.click(within(approvalPanel).getByRole("button", { name: "Approve" }));
  await screen.findByText("Approved.");
  expect(vi.mocked(fetch)).toHaveBeenCalledWith(
    "/api/v1/approvals/apr_fixture/decision",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ decision: "APPROVE" }) }),
  );

  const routes = screen.getByRole("list", { name: "Projection routes" });
  expect(within(routes).getAllByText("7 traversals").length).toBeGreaterThan(0);
});

test("shows P5 proposal authority, revision diff, router evidence, and replan control", async () => {
  const dynamicRun: Run = { ...run, state: "PAUSED" };
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/workflow/proposals")) return response([{
      proposal_id: "wfp_fixture", run_id: run.run_id, planner_version: "fragment-planner-v2",
      confidence: 0.9, nodes: [{ local_id: "start" }, { local_id: "complete" }], edges: [],
      fragment_refs: ["single-act-verify@1.0.0"], assumptions: ["Budgets remain authoritative."],
      rationale_summary: "Composed a reviewed workflow fragment.",
    }]);
    if (url.endsWith("/validations")) return response([{
      validation_id: "gvl_fixture", proposal_id: "wfp_fixture", status: "ACCEPT",
      errors: [], warnings: [], required_repairs: [], validator_version: "graph-validator-v2",
    }]);
    if (url.endsWith("/graph/revisions")) return response([
      { revision_id: "grv_1", revision: 1, reason: "INITIAL", normalized_graph_hash: "a".repeat(64), protected_state_refs: [] },
      { revision_id: "grv_2", revision: 2, reason: "HUMAN_REQUEST", normalized_graph_hash: "b".repeat(64), protected_state_refs: ["run:start"] },
    ]);
    if (url.includes("/graph/diff")) return response({
      from_revision: 1, to_revision: 2, added_nodes: ["review"], removed_nodes: [],
      changed_nodes: [], protected_state_refs: ["run:start"],
    });
    if (url.endsWith("/runtime-decisions")) return response([{
      decision_id: "rtd_fixture", selected_runtime: "FAKE", policy_version: "performance-router-v2",
      selected_reason: "selected from observable evidence", candidates: [{
        provider: "FAKE", runtime_version: "fake-p2-v1", score: 0.8, available: true,
      }],
    }]);
    if (url.endsWith("/replans")) return response([]);
    if (url.endsWith("/graph")) return response(graph);
    if (url.endsWith("/verifications")) return response(verifications);
    if (url.includes("/api/v1/approvals")) return response([]);
    if (url.endsWith("/loop")) return response(loop);
    return response({});
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}><RunExecution run={dynamicRun} /></QueryClientProvider>,
  );

  await screen.findByText("single-act-verify@1.0.0");
  const inspector = screen.getByRole("region", { name: "Dynamic workflow" });
  expect(within(inspector).getByText("single-act-verify@1.0.0")).toBeInTheDocument();
  expect(within(inspector).getByText("r2")).toBeInTheDocument();
  expect(within(inspector).getByText("1 protected state refs")).toBeInTheDocument();
  expect(within(inspector).getByText("Runtime: FAKE")).toBeInTheDocument();
  expect(within(inspector).getByRole("button", { name: "Request safe replan" })).toBeInTheDocument();
});

test("renders P6 candidate lineage, provenance, scores, spend, and selection reason", async () => {
  const searchRun: Run = { ...run, state: "SUCCEEDED" };
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/searches")) return response([{
      schema_version: "2.0", revision: 5, status: "SUCCEEDED",
      selected_candidate_id: "candidate_2", stop_reason: "ACCEPTED",
      budget_spent: { schema_version: "2.0", wall_time_seconds: 2, turns: 2, tool_calls: 3 },
      plan: {
        schema_version: "2.0", search_id: "search_fixture", run_id: searchRun.run_id,
        parent_node_id: "act", graph_revision: 1, mode: "CROSS_PROVIDER",
        branch_count: 2, max_parallel: 2, candidate_directives: [],
        per_branch_budget: { schema_version: "2.0", wall_time_seconds: 120, max_turns: 4, max_tool_calls: 12 },
        total_budget: { schema_version: "2.0", wall_time_seconds: 240, max_turns: 8, max_tool_calls: 24 },
        verifier_policy_ref: "policy", router_policy_version: "performance-router-v2",
        requested_by: "operator",
      },
    }]);
    if (url.endsWith("/search_fixture/candidates")) return response([
      {
        schema_version: "2.0", candidate_id: "candidate_1", search_id: "search_fixture",
        run_id: searchRun.run_id, ordinal: 1, provider: "CLAUDE", runtime_id: "claude-cli",
        runtime_model: "default", runtime_version: "2.1.0", status: "PRUNED", latency_ms: 1100,
        terminal_reason: "not selected by independent candidate scorer",
        trajectory_ref: "events:20-25", budget_spent: { schema_version: "2.0", wall_time_seconds: 1, turns: 1, tool_calls: 2 },
      },
      {
        schema_version: "2.0", candidate_id: "candidate_2", search_id: "search_fixture",
        run_id: searchRun.run_id, ordinal: 2, provider: "CODEX", runtime_id: "codex-cli",
        runtime_model: "default", runtime_version: "0.149.0", status: "SELECTED", latency_ms: 900,
        terminal_reason: "selected by independent candidate scorer",
        trajectory_ref: "events:26-31", budget_spent: { schema_version: "2.0", wall_time_seconds: 1, turns: 1, tool_calls: 1 },
      },
    ]);
    if (url.endsWith("/search_fixture/scores")) return response([
      { schema_version: "2.0", score_id: "score_1", search_id: "search_fixture", candidate_id: "candidate_1", verifier_policy_ref: "policy", verifier_status: "PASS", eligible: true, quality_score: 0.75, cost_proxy: 0.2, latency_proxy: 0.1, risk_score: 0, total_score: 0.7, explanation: "accepted", scorer_version: "candidate-scorer-v2" },
      { schema_version: "2.0", score_id: "score_2", search_id: "search_fixture", candidate_id: "candidate_2", verifier_policy_ref: "policy", verifier_status: "PASS", eligible: true, quality_score: 0.9, cost_proxy: 0.1, latency_proxy: 0.08, risk_score: 0, total_score: 0.86, explanation: "accepted", scorer_version: "candidate-scorer-v2" },
    ]);
    if (url.endsWith("/graph")) return response(graph);
    if (url.endsWith("/verifications")) return response(verifications);
    if (url.includes("/api/v1/approvals")) return response([]);
    if (url.endsWith("/loop")) return response(loop);
    return response([]);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}><RunExecution run={searchRun} /></QueryClientProvider>,
  );

  const tree = await screen.findByRole("region", { name: "Candidate search tree" });
  expect(await within(tree).findByText("CROSS PROVIDER")).toBeInTheDocument();
  expect(within(tree).getByText("codex-cli · default · 0.149.0")).toBeInTheDocument();
  expect(within(tree).getByText("0.860")).toBeInTheDocument();
  expect(within(tree).getByText("selected by independent candidate scorer")).toBeInTheDocument();
  expect(within(tree).getAllByText("SELECTED").length).toBeGreaterThan(0);
  expect(within(tree).getByText("2/8 turns")).toBeInTheDocument();
  expect(within(tree).getByText("3/24 tools")).toBeInTheDocument();
});
