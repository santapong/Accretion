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

function renderApp() {
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

function installPlanningApi(mode: "DIRECT" | "LOOP" | "GRAPH" | "HYBRID", template: string) {
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/api/v1/runtimes")) return response([]);
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
    if (url.endsWith("/runs") && init?.method === "POST") return response({
      run_id: "run_fixture", task_id: "tsk_fixture", project_id: "prj_fixture", provider: "FAKE",
      state: "PENDING", last_sequence: 0, revision: 0,
    }, 202);
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

test("renders the P2 runtime dashboard and empty run state", async () => {
  renderApp();
  expect(screen.getByText("Runtime observatory")).toBeInTheDocument();
  expect(screen.getByText("Operator / P2")).toBeInTheDocument();
  expect(await screen.findByText("FAKE")).toBeInTheDocument();
  expect(screen.getByText("New task")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Create and profile task" })).toBeDisabled();
  expect(screen.getByText("No runs yet. Create and profile a task above.")).toBeInTheDocument();
  expect(screen.getByText("Select a run to inspect its orchestration state.")).toBeInTheDocument();
});

test.each([
  ["DIRECT", "direct-v1"],
  ["LOOP", "feedback-loop-v1"],
] as const)("enables run creation for %s / %s", async (mode, template) => {
  installPlanningApi(mode, template);
  renderApp();
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

test.each([
  ["GRAPH", "fixed-graph-v1"],
  ["HYBRID", "hybrid-rd-v1"],
  ["DIRECT", "safe-unknown-v1"],
] as const)("keeps unsupported %s / %s execution blocked until P3", async (mode, template) => {
  installPlanningApi(mode, template);
  renderApp();
  await createPlannedTask();

  expect(screen.getByText(`${mode} / ${template}`)).toBeInTheDocument();
  expect(screen.getByText(/Execution is blocked until P3/)).toHaveTextContent("GRAPH, HYBRID, and safe-unknown");
  expect(screen.getByRole("button", { name: "Create run" })).toBeDisabled();
});
