import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, test, vi } from "vitest";
import App from "./App";

class EventSourceStub {
  static readonly OPEN = 1;
  addEventListener = vi.fn();
  close = vi.fn();
}

vi.stubGlobal("EventSource", EventSourceStub);
vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => ({
  ok: true,
  json: async () => String(input).includes("runtimes") ? [{
    runtime_id: "runtime_fake", provider: "FAKE", status: "READY",
    runtime_version: "fake-p0-v1", active_runs: 0, observed_usage_pressure: "LOW",
  }] : [],
})));

afterEach(() => cleanup());

test("renders runtime health and empty run state", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
  expect(screen.getByText("Runtime observatory")).toBeInTheDocument();
  expect(await screen.findByText("FAKE")).toBeInTheDocument();
  expect(screen.getByText("New task")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Create and profile task" })).toBeDisabled();
  expect(screen.getByText("No runs yet. Create one through the API.")).toBeInTheDocument();
});

test("creates a task and explains a blocked P2 strategy", async () => {
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    let body: unknown = [];
    if (url.includes("/runtimes")) body = [];
    if (url.includes("/runs")) body = [];
    if (url.endsWith("/projects") && !init?.method) body = [{
      project_id: "prj_fixture", name: "Fixture", repository_path: "/tmp/fixture",
      created_at: "2026-08-20T00:00:00Z",
    }];
    if (url.endsWith("/tasks") && init?.method === "POST") body = {
      envelope: { task_id: "tsk_fixture", project_id: "prj_fixture", objective: "Investigate" },
      created_at: "2026-08-20T00:00:00Z",
    };
    if (url.endsWith("/planning")) body = {
      task_id: "tsk_fixture",
      prompt_contract: {}, context_bundle: {}, profile_history: [], decision_history: [], override_history: [],
      current_profile: {
        profile_id: "prf_fixture", task_id: "tsk_fixture", complexity: 0.68,
        structure_certainty: 0.5, feedback_dependency: 0.65, dependency_complexity: 0.1,
        parallelism_potential: 0.1, uncertainty: 0.4, verifier_strength: 0.5,
        profile_confidence: 1, unknown_features: [], observed_features: [], risk: "LOW",
      },
      current_decision: {
        decision_id: "dec_fixture", task_id: "tsk_fixture", selected_mode: "LOOP",
        selected_template_id: "feedback-loop-v1", matched_rules: ["threshold:loop"],
        alternatives: ["DIRECT", "GRAPH", "HYBRID"], rationale: "Feedback favors a loop.",
        requires_approval: false, requires_independent_verifier: false,
      },
    };
    return { ok: true, json: async () => body } as Response;
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
  await screen.findByRole("option", { name: "Fixture" });
  fireEvent.change(screen.getByLabelText(/^Project$/), { target: { value: "prj_fixture" } });
  const objective = screen.getByPlaceholderText("Describe the outcome without routing instructions.");
  fireEvent.change(objective, { target: { value: "Investigate" } });
  fireEvent.click(await screen.findByRole("button", { name: "Create and profile task" }));
  expect(await screen.findByText("LOOP / feedback-loop-v1")).toBeInTheDocument();
  expect(screen.getByText(/Execution is blocked in P1/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Create run" })).toBeDisabled();
});
