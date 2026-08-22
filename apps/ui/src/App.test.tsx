import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";

class EventSourceStub {
  static readonly OPEN = 1;
  addEventListener = vi.fn();
  close = vi.fn();
}

function response(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function renderApp(path = "/") {
  window.history.pushState({}, "", path);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
}

function planning(mode: "DIRECT" | "LOOP" | "GRAPH" | "HYBRID", template: string) {
  return {
    task_id: "tsk_fixture",
    prompt_contract: {}, context_bundle: {}, profile_history: [], decision_history: [], override_history: [],
    current_profile: {
      profile_id: "prf_fixture", task_id: "tsk_fixture", complexity: 0.68,
      structure_certainty: 0.5, feedback_dependency: 0.65, dependency_complexity: 0.1,
      parallelism_potential: 0.1, uncertainty: 0.4, verifier_strength: 0.5,
      profile_confidence: 1, unknown_features: [], observed_features: [], risk: "LOW",
    },
    current_decision: {
      decision_id: "dec_fixture", task_id: "tsk_fixture", selected_mode: mode,
      selected_template_id: template, matched_rules: [`threshold:${mode.toLowerCase()}`],
      alternatives: ["DIRECT", "LOOP", "GRAPH", "HYBRID"], rationale: `${mode} is the deterministic selection.`,
      requires_approval: false, requires_independent_verifier: false,
    },
  };
}

const templateSummaries = [
  { template_id: "direct-v1", version: "1.0.0", mode: "DIRECT", status: "VALIDATED", checksum: "0".repeat(64) },
  { template_id: "feedback-loop-v1", version: "1.0.0", mode: "LOOP", status: "VALIDATED", checksum: "1".repeat(64) },
  { template_id: "fixed-graph-v1", version: "1.0.0", mode: "GRAPH", status: "VALIDATED", checksum: "2".repeat(64) },
  { template_id: "hybrid-rd-v1", version: "1.0.0", mode: "HYBRID", status: "VALIDATED", checksum: "3".repeat(64) },
  { template_id: "safe-unknown-v1", version: "1.0.0", mode: "HYBRID", status: "VALIDATED", checksum: "4".repeat(64) },
];

function installPlanningApi(
  mode: "DIRECT" | "LOOP" | "GRAPH" | "HYBRID",
  template: string,
  options: { runStatus?: number; runBody?: unknown } = {},
) {
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/api/v1/runtimes")) return response([]);
    if (url.includes("/api/v1/templates")) return response(templateSummaries);
    if (url === "/api/v1/runs?limit=50") return response([]);
    if (url.endsWith("/api/v1/projects") && !init?.method) return response([{
      project_id: "prj_fixture", name: "Fixture", repository_path: "/tmp/fixture",
      created_at: "2026-08-20T00:00:00Z",
    }]);
    if (url.endsWith("/api/v1/tasks") && init?.method === "POST") return response({
      envelope: { task_id: "tsk_fixture", project_id: "prj_fixture", objective: "Investigate" },
      created_at: "2026-08-20T00:00:00Z",
    });
    if (url.endsWith("/planning")) return response(planning(mode, template));
    if (url.endsWith("/runs") && init?.method === "POST") {
      if (options.runStatus && options.runStatus >= 400) {
        return response(options.runBody ?? { message: "blocked" }, options.runStatus);
      }
      return response({
        run_id: "run_fixture", task_id: "tsk_fixture", project_id: "prj_fixture", provider: "FAKE",
        state: "PENDING", last_sequence: 0, revision: 0,
      }, 202);
    }
    return response({ message: "Not found" }, 404);
  });
}

async function createPlannedTask() {
  await screen.findByRole("option", { name: "Fixture" });
  fireEvent.change(screen.getByLabelText(/^Project$/), { target: { value: "prj_fixture" } });
  fireEvent.change(screen.getByPlaceholderText("Describe the outcome without routing instructions."), { target: { value: "Investigate" } });
  fireEvent.change(screen.getByLabelText(/Required output paths/), { target: { value: "reports/result.json\nsrc/generated-summary.md" } });
  fireEvent.click(screen.getByRole("button", { name: "Create and profile task" }));
  await screen.findByRole("region", { name: "Task planning review" });
}

vi.stubGlobal("EventSource", EventSourceStub);
vi.stubGlobal("fetch", vi.fn());

beforeEach(() => {
  window.history.pushState({}, "", "/");
  vi.mocked(fetch).mockImplementation(async (input) => response(String(input).includes("runtimes") ? [{
    runtime_id: "runtime_fake", provider: "FAKE", status: "READY", auth_mode: "LOCAL",
    runtime_version: "fake-p0-v1", capabilities: [], active_sessions: 0, active_runs: 0,
    observed_usage_pressure: "LOW", observed_at: "2026-08-20T00:00:00Z",
  }] : []));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test("renders the P4 runtime dashboard and operator navigation", async () => {
  renderApp();
  expect(screen.getByText("Runtime observatory")).toBeInTheDocument();
  expect(screen.getByText("Operator / P4")).toBeInTheDocument();
  expect(await screen.findByText("FAKE")).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: "New task" })).toHaveLength(2);
  expect(screen.getByRole("link", { name: "Runtimes" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Capabilities" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "ACR-ARCH" })).toBeInTheDocument();
  expect(screen.getByText("No runs yet. Create and profile a task.")).toBeInTheDocument();
});

test("navigates to the required operator screens", async () => {
  renderApp();
  fireEvent.click(screen.getByRole("link", { name: "Runtimes" }));
  expect(await screen.findByRole("heading", { name: "Runtime monitor" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: "Capabilities" }));
  expect(await screen.findByRole("heading", { name: "Capabilities, skills, and plugins" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: "Approvals" }));
  expect(await screen.findByRole("heading", { name: "Verifiers / approvals" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: "History" }));
  expect(await screen.findByRole("heading", { name: "Run history / trace replay" })).toBeInTheDocument();
});

test("filters and reproduces the versioned ACR-ARCH benchmark", async () => {
  const metric = {
    metric_id: "acm_fixture", benchmark_run_id: "bnr_fixture", benchmark_task_id: "acr-001",
    task_version: "1.0.0", category: "DIRECT_SIMPLE", task_type: "REVIEW", mode: "DIRECT",
    provider: "CLAUDE", execution_source: "REPLAY", verifier_id: "output-contract",
    selector_version: "selector-v1", success: true, quality: 0.95, cost: 0.2, latency: 0.1,
    risk: 0, human_burden: 0, utility: 0.9, architecture_regret: 0, duration_ms: 100,
    turns: 2, tool_calls: 3, approvals: 0, trace_ref: "trace#fixture",
    environment_ref: "acr-env-direct-simple", environment_version: "1.0.0",
  };
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes("/api/v1/benchmarks/acr-arch/tasks/acr-001")) return response({
      task: {
        benchmark_task_id: "acr-001", version: "1.0.0", title: "Direct fixture",
        category: "DIRECT_SIMPLE", task_type: "REVIEW", environment_ref: "acr-env-direct-simple",
        environment_version: "1.0.0", verifier_id: "output-contract", verifier_version: "1.0.0",
        success_criteria: ["artifact passes"], budgets: {}, applicable_modes: ["DIRECT", "LOOP"],
        selector_mode: "DIRECT", selector_version: "selector-v1",
      }, metrics: [metric],
    });
    if (url.endsWith("/api/v1/benchmarks/acr-arch/run") && init?.method === "POST") return response({
      benchmark_run_id: "bnr_fixture", suite_version: "1.0.0", configuration_version: "1.0.0",
      execution_source: "REPLAY", status: "COMPLETED", corpus_sha256: "a".repeat(64),
      trace_sha256: "b".repeat(64), scenario_count: 68,
    }, 201);
    if (url.includes("/api/v1/benchmarks/acr-arch?")) return response({
      suite: "ACR-ARCH", suite_version: "1.0.0", configuration_version: "1.0.0",
      task_count: 30, scenario_count: 68, metrics: [metric], filters: {
        mode: ["DIRECT", "LOOP"], provider: ["CLAUDE", "CODEX"], task_type: ["REVIEW"],
        verifier: ["output-contract"], selector_version: ["selector-v1"],
      },
    });
    return response([]);
  });

  renderApp("/benchmarks/acr-arch");
  expect(await screen.findByText("68")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("mode"), { target: { value: "LOOP" } });
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(
    expect.stringContaining("mode=LOOP"),
  ));
  fireEvent.click(await screen.findByRole("button", { name: "acr-001" }));
  expect(await screen.findByRole("heading", { name: "Direct fixture" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Reproduce replay" }));
  expect(await screen.findByText(/Reproduced 68 scenarios/)).toBeInTheDocument();
});

test.each([
  ["DIRECT", "direct-v1"],
  ["LOOP", "feedback-loop-v1"],
  ["GRAPH", "fixed-graph-v1"],
  ["HYBRID", "hybrid-rd-v1"],
] as const)("enables run creation for %s / %s", async (mode, template) => {
  installPlanningApi(mode, template);
  renderApp("/tasks/new");
  await createPlannedTask();

  expect(screen.getByText(`${mode} / ${template}`)).toBeInTheDocument();
  const createRun = screen.getByRole("button", { name: "Create run" });
  expect(createRun).toBeEnabled();
  expect(screen.queryByText(/Execution is blocked until P3/)).not.toBeInTheDocument();

  fireEvent.click(createRun);
  expect(await screen.findByText(/Run .* created\./)).toBeInTheDocument();
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(
    "/api/v1/tasks/tsk_fixture/runs",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ provider: "FAKE" }) }),
  ));

  const taskRequest = vi.mocked(fetch).mock.calls.find(([input, init]) => String(input).endsWith("/tasks") && init?.method === "POST");
  const taskBody = JSON.parse(String(taskRequest?.[1]?.body));
  expect(taskBody.budgets.max_tool_calls).toBe(100);
  expect(taskBody.required_outputs).toEqual([
    { path: "reports/result.json", kind: "file", non_empty: true },
    { path: "src/generated-summary.md", kind: "file", non_empty: true },
  ]);
});

test("surfaces the server's fail-closed template rejection", async () => {
  installPlanningApi("DIRECT", "direct-v1", {
    runStatus: 409,
    runBody: {
      code: "TEMPLATE_NOT_VALIDATED",
      message: "workflow template direct-v1 is RETIRED; only VALIDATED templates may execute",
      correlation_id: "corr_fixture",
      retryable: false,
    },
  });
  renderApp("/tasks/new");
  await createPlannedTask();

  const createRun = screen.getByRole("button", { name: "Create run" });
  expect(createRun).toBeEnabled();
  fireEvent.click(createRun);
  expect(await screen.findByText(/only VALIDATED templates may execute/)).toBeInTheDocument();
});

test("override template options come from the validated template registry", async () => {
  installPlanningApi("HYBRID", "hybrid-rd-v1");
  renderApp("/tasks/new");
  await createPlannedTask();

  await screen.findByRole("option", { name: "hybrid-rd-v1" });
  expect(screen.getByRole("option", { name: "safe-unknown-v1" })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "fixed-graph-v1" })).not.toBeInTheDocument();
});
