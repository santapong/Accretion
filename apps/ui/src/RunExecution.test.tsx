import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { RunExecution } from "./RunExecution";
import type { GraphProjection, LoopExecution, Run, RunGraphRevision, VerificationResult } from "./types";

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

// One P5 fixture family, overridden per test, so each inherited v0.2 criterion gets a
// differential of its own rather than sharing a single existence check.
const proposalFixture = {
  proposal_id: "wfp_fixture", run_id: run.run_id, planner_version: "fragment-planner-v2",
  confidence: 0.9, nodes: [{ local_id: "start" }, { local_id: "complete" }], edges: [],
  fragment_refs: ["single-act-verify@1.0.0"], assumptions: ["Budgets remain authoritative."],
  required_capabilities: ["capability:repo.write", "capability:shell.exec"],
  rationale_summary: "Composed a reviewed workflow fragment.",
};

const acceptedValidation = {
  validation_id: "gvl_fixture", proposal_id: "wfp_fixture", status: "ACCEPT",
  errors: [], warnings: [], required_repairs: [], validator_version: "graph-validator-v2",
};

const rejectedValidation = {
  validation_id: "gvl_rejected", proposal_id: "wfp_fixture", status: "REJECT",
  errors: [{
    code: "PROTECTED_STATE_DROPPED", severity: "ERROR",
    message: "Revision drops a protected state reference.", path: "nodes/review",
  }],
  warnings: [{
    code: "UNVERIFIED_FRAGMENT", severity: "WARNING",
    message: "Fragment has no verifier attached.", path: "nodes/start",
  }],
  required_repairs: ["reattach-protected-state"], validator_version: "graph-validator-v2",
};

const revisionOne: RunGraphRevision = {
  schema_version: "2.0", run_id: run.run_id, proposal_id: "wfp_fixture",
  run_graph_id: "rgr_1", nodes: [], edges: [],
  revision_id: "grv_1", revision: 1, reason: "INITIAL",
  normalized_graph_hash: "a".repeat(64), protected_state_refs: [],
};

const revisionTwo: RunGraphRevision = {
  schema_version: "2.0", run_id: run.run_id, proposal_id: "wfp_fixture",
  run_graph_id: "rgr_2", nodes: [], edges: [],
  revision_id: "grv_2", revision: 2, reason: "HUMAN_REQUEST",
  normalized_graph_hash: "b".repeat(64), protected_state_refs: ["run:start"],
};

const runtimeDecisions = [{
  decision_id: "rtd_fixture", selected_runtime: "FAKE", policy_version: "performance-router-v2",
  selected_reason: "selected from observable evidence", candidates: [{
    provider: "FAKE", runtime_version: "fake-p2-v1", score: 0.8, available: true,
  }],
}];

type DynamicOverrides = {
  proposals?: unknown[];
  validations?: unknown[];
  revisions?: () => unknown[];
  replan?: () => unknown;
};

function stubDynamicRun(overrides: DynamicOverrides = {}) {
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/replan") && init?.method === "POST") {
      return response(overrides.replan?.() ?? { request: { status: "APPLIED" } });
    }
    if (url.endsWith("/workflow/proposals")) return response(overrides.proposals ?? [proposalFixture]);
    if (url.endsWith("/validations")) return response(overrides.validations ?? [acceptedValidation]);
    if (url.endsWith("/graph/revisions")) {
      return response(overrides.revisions ? overrides.revisions() : [revisionOne, revisionTwo]);
    }
    if (url.includes("/graph/diff")) return response({
      from_revision: 1, to_revision: 2, added_nodes: ["review"], removed_nodes: [],
      changed_nodes: [], protected_state_refs: ["run:start"],
    });
    if (url.endsWith("/runtime-decisions")) return response(runtimeDecisions);
    if (url.endsWith("/replans")) return response([]);
    if (url.endsWith("/graph")) return response(graph);
    if (url.endsWith("/verifications")) return response(verifications);
    if (url.includes("/api/v1/approvals")) return response([]);
    if (url.endsWith("/loop")) return response(loop);
    return response({});
  });
}

function renderDynamic(state: Run["state"] = "PAUSED") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}><RunExecution run={{ ...run, state }} /></QueryClientProvider>,
  );
  return client;
}

test("shows P5 proposal authority, revision diff, router evidence, and replan control", async () => {
  stubDynamicRun();
  renderDynamic();

  await screen.findByText("single-act-verify@1.0.0");
  const inspector = screen.getByRole("region", { name: "Dynamic workflow" });
  expect(within(inspector).getByText("single-act-verify@1.0.0")).toBeInTheDocument();
  expect(within(inspector).getByRole("button", { name: "r2" })).toBeInTheDocument();
  expect(within(inspector).getByText("1 protected state refs")).toBeInTheDocument();
  expect(within(inspector).getByText("Runtime: FAKE")).toBeInTheDocument();
  expect(within(inspector).getByRole("button", { name: "Request safe replan" })).toBeInTheDocument();
});

test("the proposal inspector names the assumptions, the required capabilities, and the validation verdict", async () => {
  stubDynamicRun();
  renderDynamic();
  await screen.findByText("single-act-verify@1.0.0");
  const inspector = screen.getByRole("region", { name: "Dynamic workflow" });

  const assumptions = within(inspector).getByRole("list", { name: "Assumptions" });
  expect(within(assumptions).getByText("Budgets remain authoritative.")).toBeInTheDocument();

  const capabilities = within(inspector).getByRole("list", { name: "Required capabilities" });
  expect(within(capabilities).getByText("capability:repo.write")).toBeInTheDocument();
  expect(within(capabilities).getByText("capability:shell.exec")).toBeInTheDocument();

  const validation = within(inspector).getByRole("group", { name: "Validation findings" });
  expect(within(validation).getByText("ACCEPT")).toBeInTheDocument();
  expect(within(validation).getByText("The validator reported no findings.")).toBeInTheDocument();
  expect(within(validation).queryByText("PROTECTED_STATE_DROPPED")).not.toBeInTheDocument();
});

test("a rejected validation renders every finding code, severity, and message", async () => {
  stubDynamicRun({ validations: [rejectedValidation] });
  renderDynamic();
  await screen.findByText("single-act-verify@1.0.0");
  const validation = within(screen.getByRole("region", { name: "Dynamic workflow" }))
    .getByRole("group", { name: "Validation findings" });

  expect(within(validation).getByText("REJECT")).toBeInTheDocument();
  expect(within(validation).getByText("PROTECTED_STATE_DROPPED")).toBeInTheDocument();
  expect(within(validation).getByText("Revision drops a protected state reference.")).toBeInTheDocument();
  expect(within(validation).getByText("UNVERIFIED_FRAGMENT")).toBeInTheDocument();
  expect(within(validation).getByText("Fragment has no verifier attached.")).toBeInTheDocument();
  expect(within(validation).queryByText("The validator reported no findings.")).not.toBeInTheDocument();
});

test("the revision timeline keeps prior revisions reachable and inspectable", async () => {
  stubDynamicRun();
  renderDynamic();
  await screen.findByText("single-act-verify@1.0.0");
  const inspector = screen.getByRole("region", { name: "Dynamic workflow" });
  const timeline = within(inspector).getByRole("group", { name: "Graph revision timeline" });

  // Both revisions are listed, and the scroll region is keyboard reachable (F4).
  expect(timeline).toHaveAttribute("tabindex", "0");
  expect(within(timeline).getByRole("button", { name: "r1" })).toBeInTheDocument();
  expect(within(timeline).getByRole("button", { name: "r2" })).toBeInTheDocument();

  // The active revision is inspected by default; its hash, not the prior one, is shown.
  const detail = within(inspector).getByRole("group", { name: "Selected graph revision" });
  expect(within(detail).getByText("b".repeat(64))).toBeInTheDocument();
  expect(within(detail).getByText("active revision")).toBeInTheDocument();

  // Selecting the prior revision renders r1's own hash, so history stays reachable.
  fireEvent.click(within(timeline).getByRole("button", { name: "r1" }));
  expect(within(detail).getByText("a".repeat(64))).toBeInTheDocument();
  expect(within(detail).queryByText("b".repeat(64))).not.toBeInTheDocument();
  expect(within(detail).getByText("prior revision")).toBeInTheDocument();
});

test("a pending proposal reads as PROPOSED and an activated revision reads as ACTIVE", async () => {
  // Fixture one: a proposal exists, nothing has been activated.
  stubDynamicRun({ revisions: () => [] });
  renderDynamic("RUNNING");
  await screen.findByText("single-act-verify@1.0.0");
  const pending = screen.getByRole("region", { name: "Dynamic workflow" });
  expect(within(pending).getByText("PROPOSED")).toBeInTheDocument();
  expect(within(pending).queryByText("ACTIVE")).not.toBeInTheDocument();
  expect(
    within(pending).getByText("Pending proposal: not executable until a graph revision is activated."),
  ).toBeInTheDocument();
  cleanup();

  // Fixture two: the same proposal, now activated as a revision.
  stubDynamicRun();
  renderDynamic("RUNNING");
  await screen.findByText("single-act-verify@1.0.0");
  const active = screen.getByRole("region", { name: "Dynamic workflow" });
  expect(within(active).getByText("ACTIVE")).toBeInTheDocument();
  expect(within(active).queryByText("PROPOSED")).not.toBeInTheDocument();
  expect(
    within(active).getByText("Executable: activated as graph revision r2."),
  ).toBeInTheDocument();
});

test("a replan adds the new revision without dropping the pre-replan execution trace", async () => {
  let revisions = [revisionOne];
  stubDynamicRun({
    revisions: () => revisions,
    replan: () => {
      revisions = [revisionOne, revisionTwo];
      return { request: { status: "APPLIED" }, revision: revisionTwo };
    },
  });
  renderDynamic();

  await screen.findByText("single-act-verify@1.0.0");
  const inspector = screen.getByRole("region", { name: "Dynamic workflow" });
  const timeline = within(inspector).getByRole("group", { name: "Graph revision timeline" });
  expect(within(timeline).getByRole("button", { name: "r1" })).toBeInTheDocument();
  expect(within(timeline).queryByRole("button", { name: "r2" })).not.toBeInTheDocument();

  // The trace and history rendered before the replan.
  const routes = screen.getByRole("list", { name: "Projection routes" });
  expect(within(routes).getByText("Evaluate → Observe")).toBeInTheDocument();
  expect(within(routes).getAllByText("2 traversals").length).toBeGreaterThan(0);
  const verificationPanel = screen.getByRole("region", { name: "Verifications" });
  expect(within(verificationPanel).getByText("One acceptance test failed.")).toBeInTheDocument();

  fireEvent.click(within(inspector).getByRole("button", { name: "Request safe replan" }));
  await screen.findByText("Revision 2 activated with protected history preserved.");
  await within(timeline).findByRole("button", { name: "r2" });

  // The replan added a revision; nothing the run had already executed disappeared.
  expect(within(timeline).getByRole("button", { name: "r1" })).toBeInTheDocument();
  expect(within(screen.getByRole("list", { name: "Projection routes" })).getByText("Evaluate → Observe"))
    .toBeInTheDocument();
  expect(
    within(screen.getByRole("list", { name: "Projection routes" })).getAllByText("2 traversals").length,
  ).toBeGreaterThan(0);
  expect(
    within(screen.getByRole("region", { name: "Verifications" })).getByText("One acceptance test failed."),
  ).toBeInTheDocument();
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

test("renders P7 experience provenance, compatibility, transfer risk, and reused segments", async () => {
  const replayRun: Run = { ...run, task_id: "tsk_replay", state: "SUCCEEDED" };
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/searches")) return response([{
      schema_version: "2.0", revision: 5, status: "SUCCEEDED",
      selected_candidate_id: "candidate_replay", stop_reason: "ACCEPTED",
      budget_spent: { schema_version: "2.0", wall_time_seconds: 2, turns: 2, tool_calls: 2 },
      plan: {
        schema_version: "2.0", search_id: "search_replay", run_id: replayRun.run_id,
        parent_node_id: "act", graph_revision: 1, mode: "REPLAY_BRANCH",
        branch_count: 2, max_parallel: 1, candidate_directives: [],
        replay_seed_match_ids: ["match_positive"],
        negative_guidance_match_ids: ["match_negative"],
        per_branch_budget: { schema_version: "2.0", wall_time_seconds: 60, max_turns: 2, max_tool_calls: 5 },
        total_budget: { schema_version: "2.0", wall_time_seconds: 120, max_turns: 4, max_tool_calls: 10 },
        verifier_policy_ref: "policy", router_policy_version: "performance-router-v2",
        requested_by: "operator",
      },
    }]);
    if (url.endsWith("/search_replay/candidates")) return response([
      {
        schema_version: "2.0", candidate_id: "candidate_fresh", search_id: "search_replay",
        run_id: replayRun.run_id, ordinal: 1, provider: "FAKE", runtime_id: "runtime_fake",
        runtime_model: "default", runtime_version: "fake-p2-v1", source_kind: "FRESH",
        status: "PRUNED", latency_ms: 100, terminal_reason: "control not selected",
        budget_spent: { schema_version: "2.0", wall_time_seconds: 1, turns: 1, tool_calls: 1 },
      },
      {
        schema_version: "2.0", candidate_id: "candidate_replay", search_id: "search_replay",
        run_id: replayRun.run_id, ordinal: 2, provider: "FAKE", runtime_id: "runtime_fake",
        runtime_model: "default", runtime_version: "fake-p2-v1", source_kind: "REPLAY",
        replay_seed_id: "seed_fixture", source_experience_id: "exp_positive",
        source_match_id: "match_positive", trajectory_segment_refs: ["segment_workflow"],
        seed_revalidation_status: "ELIGIBLE", seed_revalidation_reasons: [],
        status: "SELECTED", latency_ms: 90, terminal_reason: "selected by scorer",
        budget_spent: { schema_version: "2.0", wall_time_seconds: 1, turns: 1, tool_calls: 1 },
      },
    ]);
    if (url.endsWith("/search_replay/scores")) return response([]);
    if (url.endsWith("/search_replay/replay-seeds")) return response([{
      schema_version: "2.0", seed_id: "seed_fixture", search_id: "search_replay",
      candidate_id: "candidate_replay", match_id: "match_positive",
      experience_id: "exp_positive", segment_ids: ["segment_workflow"],
      procedural_guidance: ["Follow the verified workflow stages in order."],
      required_revalidations: ["Revalidate policy and repository."],
      validation_status: "ELIGIBLE",
    }]);
    if (url.endsWith("/tasks/tsk_replay/experience-matches")) return response([
      {
        schema_version: "2.0", match_id: "match_positive", query_id: "query_fixture",
        experience_id: "exp_positive", rank: 1, trust: "HIGH", polarity: "POSITIVE",
        assessment: { schema_version: "2.0", semantic_score: 0.94, environment_score: 1,
          version_score: 0.93, freshness_score: 1, final_score: 0.966,
          transfer_risk: 0.07, disposition: "ACCEPTED", replay_eligible: true,
          negative_guidance_eligible: false, reasons: [] },
      },
      {
        schema_version: "2.0", match_id: "match_negative", query_id: "query_fixture",
        experience_id: "exp_negative", rank: 2, trust: "MEDIUM", polarity: "NEGATIVE",
        assessment: { schema_version: "2.0", semantic_score: 0.8, environment_score: 1,
          version_score: 0.9, freshness_score: 1, final_score: 0.92,
          transfer_risk: 0.1, disposition: "ACCEPTED", replay_eligible: false,
          negative_guidance_eligible: true, reasons: [] },
      },
    ]);
    if (url.includes("/experiences/exp_")) {
      const positive = url.endsWith("exp_positive");
      return response({
        schema_version: "2.0", embedding_version: "deterministic-hybrid-384-v1",
        embedding_input_digest: "a".repeat(64),
        experience: {
          schema_version: "2.0", experience_id: positive ? "exp_positive" : "exp_negative",
          project_id: "prj_fixture", repository_identity: "b".repeat(64), task_id: "source_task",
          task_type: "IMPLEMENT", task_family: "api", source_kind: "RUN",
          source_run_id: positive ? "run_verified_source" : "run_failed_source",
          source_commit: "1234567890abcdef", architecture_version: "2.0",
          manifest_digest: "c".repeat(64), policy_digest: "d".repeat(64),
          verifier_digest: "e".repeat(64), prompt_digest: "f".repeat(64),
          context_digest: "1".repeat(64), tool_profile_digest: "2".repeat(64),
          provider: "FAKE", runtime_model: "default", runtime_version: "fake-p2-v1",
          trust: positive ? "HIGH" : "MEDIUM", polarity: positive ? "POSITIVE" : "NEGATIVE",
          outcome: positive ? "VERIFIED_SUCCESS" : "FAILED", content_digest: "3".repeat(64),
          protected_side_effects: false, retracted: false, revision: 1,
        },
        segments: positive ? [{ schema_version: "2.0", segment_id: "segment_workflow",
          experience_id: "exp_positive", ordinal: 1, kind: "WORKFLOW_PATH",
          content: { nodes: ["act"] }, content_digest: "4".repeat(64) }] : [],
      });
    }
    if (url.endsWith("/graph")) return response(graph);
    if (url.endsWith("/verifications")) return response(verifications);
    if (url.includes("/api/v1/approvals")) return response([]);
    if (url.endsWith("/loop")) return response(loop);
    return response([]);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}><RunExecution run={replayRun} /></QueryClientProvider>,
  );

  const lineage = await screen.findByRole("region", { name: "Experience replay lineage" });
  expect(within(lineage).getByText("0.966 compatibility")).toBeInTheDocument();
  expect(within(lineage).getByText("0.070 transfer risk")).toBeInTheDocument();
  expect(within(lineage).getByText(/run_verified_source/)).toBeInTheDocument();
  expect(within(lineage).getByText(/segment_workflow/)).toBeInTheDocument();
  expect(within(lineage).getByText("NEGATIVE GUIDANCE")).toBeInTheDocument();
  expect(screen.getByText("Fresh control")).toBeInTheDocument();
  expect(screen.getByText("Verified replay treatment")).toBeInTheDocument();
});

// --- M6: read-only capability badges, diff identities, and router evidence ---------
// (imports are declared here, beside their only use, so the evidence anchors above
// keep their line numbers.)
import type { components } from "./api/schema";
import auditFixture from "./__fixtures__/run-audit.json";
import graphDiffFixture from "./__fixtures__/graph-diff.json";
import graphDiffRollbackFixture from "./__fixtures__/graph-diff-rollback.json";
import runtimeDecisionsFixture from "./__fixtures__/runtime-decisions.json";
// The three fixtures below are generated by tests/test_v03_m6_run_inspectors.py from a
// real gateway execution, two really-activated graph revisions, and the production
// runtime router, and are byte-compared there.

const badgeAudit = auditFixture as Pick<
  components["schemas"]["RunAudit"],
  "schema_version" | "capability_results"
>;
const auditedResults = badgeAudit.capability_results ?? [];
const boundResult = auditedResults.find((item) => item.connector_id)!;
const unboundResult = auditedResults.find((item) => !item.connector_id)!;
const pluginResult = auditedResults.find(
  (item) => item.connector_id?.startsWith("conndef_plugin_"),
)!;
const pluginId = pluginResult.connector_id!.slice("conndef_plugin_".length);

const auditedGraph: GraphProjection = {
  ...graph,
  nodes: auditedResults.map((item, index) => ({
    ...schemaVersion,
    node_id: item.request.node_id,
    kind: "TASK",
    label: `Audited ${index + 1}`,
    status: "SUCCEEDED",
    provider: "FAKE",
    artifact_count: 0,
    risk: "LOW",
  })),
  edges: [],
};

function mockRunEndpoints(audit: unknown) {
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/audit")) return response(audit);
    if (url.endsWith("/graph")) return response(auditedGraph);
    if (url.endsWith("/loop")) return response(loop);
    if (url.endsWith("/verifications")) return response(verifications);
    if (url.includes("/api/v1/approvals")) return response([]);
    return response({});
  });
}

test("badges every graph node with the connector, connection, and binding the audit recorded", async () => {
  mockRunEndpoints(badgeAudit);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}><RunExecution run={run} /></QueryClientProvider>,
  );

  const summary = await screen.findByRole("list", { name: "Projection node states" });
  const boundRow = within(summary)
    .getAllByRole("listitem")
    .find((item) => item.textContent?.includes(boundResult.request.capability_id))!;
  expect(within(boundRow).getByText(`connector ${boundResult.connector_id}`)).toBeInTheDocument();
  expect(within(boundRow).getByText(`connection ${boundResult.connection_id}`)).toBeInTheDocument();
  expect(within(boundRow).getByText(`binding ${boundResult.binding_id}`)).toBeInTheDocument();
  expect(within(boundRow).queryByText(/^plugin /)).toBeNull();

  const unboundRow = within(summary)
    .getAllByRole("listitem")
    .find((item) => item.textContent?.includes(unboundResult.request.capability_id))!;
  expect(unboundRow).not.toBe(boundRow);
  expect(within(unboundRow).queryByText(/^connector /)).toBeNull();
  expect(within(unboundRow).queryByText(/^connection /)).toBeNull();
  expect(within(unboundRow).queryByText(/^binding /)).toBeNull();
});

test("names the plugin that served a call, and only for the plugin-served node", async () => {
  mockRunEndpoints(badgeAudit);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}><RunExecution run={run} /></QueryClientProvider>,
  );

  const summary = await screen.findByRole("list", { name: "Projection node states" });
  const pluginRow = within(summary)
    .getAllByRole("listitem")
    .find((item) => item.textContent?.includes(pluginResult.request.capability_id))!;
  expect(within(pluginRow).getByText(`plugin ${pluginId}`)).toBeInTheDocument();
  expect(within(pluginRow).getByText(`connector ${pluginResult.connector_id}`)).toBeInTheDocument();
  expect(within(pluginRow).getByText(`binding ${pluginResult.binding_id}`)).toBeInTheDocument();
  // A plugin capability is bound through a credential-free local connector, so there
  // is no connection to name and the badge must not invent one.
  expect(within(pluginRow).queryByText(/^connection /)).toBeNull();

  const oauthRow = within(summary)
    .getAllByRole("listitem")
    .find((item) => item.textContent?.includes(boundResult.request.capability_id))!;
  expect(oauthRow).not.toBe(pluginRow);
  expect(within(oauthRow).queryByText(new RegExp(`^plugin `))).toBeNull();
});

test("renders the badges on the React Flow node itself, not only in the summary list", async () => {
  mockRunEndpoints(badgeAudit);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={client}><RunExecution run={run} /></QueryClientProvider>,
  );

  await screen.findByRole("list", { name: "Projection node states" });
  const flow = container.querySelector(".projection-flow") as HTMLElement;
  const onNode = [...flow.querySelectorAll(".projection-node-content .node-badge")];
  expect(onNode.map((badge) => badge.getAttribute("data-capability-id")).sort()).toEqual(
    auditedResults.map((item) => item.request.capability_id).sort(),
  );
  const pluginBadge = onNode.find(
    (badge) => badge.getAttribute("data-capability-id") === pluginResult.request.capability_id,
  )!;
  expect(pluginBadge.textContent).toContain(`plugin ${pluginId}`);
});

test("a badge is not a control: it offers no interactive role and clicking it asks the API for nothing", async () => {
  mockRunEndpoints(badgeAudit);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={client}><RunExecution run={run} /></QueryClientProvider>,
  );

  await screen.findAllByText(`connector ${boundResult.connector_id}`);
  const badges = [...container.querySelectorAll(".node-badge")];
  // Every badge is rendered twice: once on the flow node, once in the summary mirror.
  expect(badges.length).toBe(auditedResults.length * 2);
  for (const badge of badges) {
    for (const role of ["button", "textbox", "link", "checkbox", "combobox"] as const) {
      expect(within(badge as HTMLElement).queryAllByRole(role)).toEqual([]);
    }
    expect(badge.querySelector("button, a, input, select, textarea")).toBeNull();
    expect(badge.getAttribute("tabindex")).toBeNull();
  }

  const before = vi.mocked(fetch).mock.calls.length;
  for (const badge of badges) fireEvent.click(badge);
  expect(vi.mocked(fetch).mock.calls.length).toBe(before);
});

test("a newer audit replaces the badge identities instead of showing a cached copy", async () => {
  mockRunEndpoints(badgeAudit);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}><RunExecution run={run} /></QueryClientProvider>,
  );
  await screen.findAllByText(`connection ${boundResult.connection_id}`);

  const reconnected = {
    ...badgeAudit,
    capability_results: auditedResults.map((item) =>
      item.connector_id ? { ...item, connection_id: "conn_reauthorized" } : item,
    ),
  };
  mockRunEndpoints(reconnected);
  await client.invalidateQueries({ queryKey: ["run-audit-badges", run.run_id] });

  expect((await screen.findAllByText("connection conn_reauthorized")).length).toBeGreaterThan(0);
  expect(screen.queryAllByText(`connection ${boundResult.connection_id}`)).toEqual([]);
});

test("a run whose audit carries no capability results renders no badges", async () => {
  mockRunEndpoints({ schema_version: "1.0" });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={client}><RunExecution run={run} /></QueryClientProvider>,
  );

  await screen.findByRole("list", { name: "Projection node states" });
  expect(container.querySelectorAll(".node-badge")).toHaveLength(0);
  expect(screen.queryAllByText(`connector ${boundResult.connector_id}`)).toEqual([]);
});

const revisionList = [
  { revision_id: "grv_1", revision: 1, reason: "INITIAL", normalized_graph_hash: "a".repeat(64), protected_state_refs: [] },
  { revision_id: "grv_2", revision: 2, reason: "HUMAN_REQUEST", normalized_graph_hash: "b".repeat(64), protected_state_refs: ["run_m6_01:start"] },
];

function mockDynamicEndpoints(diff: unknown, decisions: unknown) {
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/workflow/proposals")) return response([]);
    if (url.endsWith("/graph/revisions")) return response(revisionList);
    if (url.includes("/graph/diff")) return response(diff);
    if (url.endsWith("/runtime-decisions")) return response(decisions);
    if (url.endsWith("/replans")) return response([]);
    if (url.endsWith("/graph")) return response(graph);
    if (url.endsWith("/loop")) return response(loop);
    if (url.endsWith("/verifications")) return response(verifications);
    if (url.includes("/api/v1/approvals")) return response([]);
    return response({});
  });
}

const diffSectionsUnderTest = [
  ["added_nodes", "Added nodes"],
  ["removed_nodes", "Removed nodes"],
  ["changed_nodes", "Changed nodes"],
  ["added_edges", "Added edges"],
  ["removed_edges", "Removed edges"],
  ["changed_edges", "Changed edges"],
] as const;

function assertDiffIdentitiesRendered(diff: components["schemas"]["GraphRevisionDiff"], panel: HTMLElement) {
  for (const [key, label] of diffSectionsUnderTest) {
    const section = panel.querySelector(`[data-diff-section="${key}"]`) as HTMLElement;
    expect(section, `${label} is missing from the diff panel`).not.toBeNull();
    expect(within(section).getByText(label)).toBeInTheDocument();
    const identities = (diff[key] ?? []) as string[];
    if (identities.length) {
      for (const identity of identities) {
        expect(within(section).getByText(identity)).toBeInTheDocument();
      }
    } else {
      expect(within(section).getByText("none")).toBeInTheDocument();
    }
  }
}

test("the graph diff names every added, removed, and changed node and edge", async () => {
  const forward = graphDiffFixture as components["schemas"]["GraphRevisionDiff"];
  mockDynamicEndpoints(forward, []);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}><RunExecution run={{ ...run, state: "PAUSED" }} /></QueryClientProvider>,
  );

  const panel = await screen.findByRole("group", { name: "Graph revision diff identities" });
  assertDiffIdentitiesRendered(forward, panel);
  // Non-vacuous: the added node and the removed edge are named, not counted.
  expect(within(panel).getByText("review")).toBeInTheDocument();
  expect(within(panel).getByText("act-verify")).toBeInTheDocument();
});

test("the rollback diff renders its own identities, not the previous diff's", async () => {
  const backward = graphDiffRollbackFixture as components["schemas"]["GraphRevisionDiff"];
  mockDynamicEndpoints(backward, []);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}><RunExecution run={{ ...run, state: "PAUSED" }} /></QueryClientProvider>,
  );

  const panel = await screen.findByRole("group", { name: "Graph revision diff identities" });
  assertDiffIdentitiesRendered(backward, panel);
  const removed = panel.querySelector('[data-diff-section="removed_nodes"]') as HTMLElement;
  expect(within(removed).getByText("review")).toBeInTheDocument();
  const added = panel.querySelector('[data-diff-section="added_nodes"]') as HTMLElement;
  expect(within(added).getByText("none")).toBeInTheDocument();
});

test("the router inspector lists the fallback order and every observed feature", async () => {
  const decisions = runtimeDecisionsFixture as components["schemas"]["RuntimeDecision"][];
  const decision = decisions[0];
  expect(decision.fallback_order?.length).toBeGreaterThan(1);
  mockDynamicEndpoints(null, decisions);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}><RunExecution run={{ ...run, state: "PAUSED" }} /></QueryClientProvider>,
  );

  const fallback = await screen.findByRole("list", { name: "Fallback order" });
  expect(within(fallback).getAllByRole("listitem").map((item) => item.textContent)).toEqual(
    (decision.fallback_order ?? []).map((provider, index) => `${index + 1}${provider}`),
  );
  const features = document.querySelector(`[aria-labelledby="features-${decision.decision_id}"]`) as HTMLElement;
  expect(features).not.toBeNull();
  for (const [key, value] of Object.entries(decision.observed_features ?? {})) {
    expect(within(features).getByText(key)).toBeInTheDocument();
    expect(within(features).getByText(String(value))).toBeInTheDocument();
  }
});

test("the router inspector renders only the fields the decision declares, never a credential", async () => {
  const sentinel = "gho_router_panel_sentinel_value";
  const decisions = runtimeDecisionsFixture as components["schemas"]["RuntimeDecision"][];
  // Hostile payload: a credential smuggled onto the decision as an undeclared field,
  // and onto a candidate. The panel projects named fields, so neither may be rendered.
  const hostile = decisions.map((decision) => ({
    ...decision,
    access_token: sentinel,
    candidates: decision.candidates.map((candidate) => ({ ...candidate, secret: sentinel })),
  }));
  mockDynamicEndpoints(null, hostile);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}><RunExecution run={{ ...run, state: "PAUSED" }} /></QueryClientProvider>,
  );

  await screen.findByRole("list", { name: "Fallback order" });
  const inspector = screen.getByRole("region", { name: "Dynamic workflow" });
  expect(inspector.textContent).not.toContain(sentinel);
  expect(inspector.textContent).not.toMatch(/gho_|ghr_|Bearer\s/);
  expect(document.body.textContent).not.toContain(sentinel);
});
