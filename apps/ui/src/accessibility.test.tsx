/**
 * Accessibility regressions for the inherited v0.2 findings F1-F4.
 *
 * Only F3 (heading structure) and F4 (keyboard-reachable scroll region) are
 * decidable here. jsdom has no layout engine and `src/test/setup.ts` fakes
 * `getBoundingClientRect`, so F1 (390px overflow) and F2 (colour contrast)
 * cannot be asserted in vitest without writing a test that passes on a fake
 * geometry - they are verified in a real browser and recorded in
 * docs/releases/v0.3/browser-a11y-evidence.md.
 *
 * These carry no acceptance marker: F1-F4 are backlog findings, not criteria.
 */

import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";

class EventSourceStub {
  static readonly OPEN = 1;
  addEventListener = vi.fn();
  close = vi.fn();
}

function response(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function renderApp(path = "/") {
  window.history.pushState({}, "", path);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
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

// F3 — every route names itself with exactly one h1.
const ROUTES: Array<[string, string | RegExp]> = [
  ["/", /One control plane\./],
  ["/tasks/new", "New task"],
  ["/runtimes", "Runtime monitor"],
  ["/history", "Run history / trace replay"],
  ["/approvals", "Verifiers / approvals"],
  ["/capabilities", "Capabilities, skills, and plugins"],
  ["/benchmarks/acr-arch", "ACR-ARCH"],
  ["/benchmarks/dynamic", "Dynamic workflow gate"],
  ["/benchmarks/search", "Quality vs compute"],
  ["/benchmarks/experience", "Experience transfer gate"],
];

test.each(ROUTES)("F3: %s names itself with a level-1 heading", async (path, name) => {
  renderApp(path);
  expect(await screen.findByRole("heading", { level: 1, name })).toBeInTheDocument();
});

test("F3: every route has exactly one h1, so the document outline has one root", async () => {
  for (const [path, name] of ROUTES) {
    renderApp(path);
    await screen.findByRole("heading", { level: 1, name });
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings, `${path} should have exactly one h1`).toHaveLength(1);
    cleanup();
  }
});

test("F3: an unknown route still names itself rather than rendering an unlabelled page", async () => {
  renderApp("/definitely-not-a-route");
  expect(
    await screen.findByRole("heading", { level: 1, name: "Page not found" }),
  ).toBeInTheDocument();
});

test("F3: section headings below the title stay at h2, so levels are not skipped", async () => {
  renderApp("/");
  await screen.findByRole("heading", { level: 1 });
  // The dashboard's "Runs" panel is a section of the page, not a second page
  // title; promoting it too would leave the outline with two roots.
  expect(screen.getByRole("heading", { level: 2, name: "Runs" })).toBeInTheDocument();
});

// F4 — the normalized-trace scroll region is reachable and named.
// `EventStream` returns an empty placeholder when it has no run, so the run
// list has to actually contain the run the route names.
const RUN_FIXTURE = {
  run_id: "run_fixture",
  task_id: "tsk_fixture",
  project_id: "prj_fixture",
  provider: "FAKE",
  state: "SUCCEEDED",
  acceptance_policy_id: "acp_fixture",
};

function installRunApi() {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/runs/") && url.includes("/audit")) {
      return response({ run: RUN_FIXTURE, events: [], verifications: [], capability_results: [] });
    }
    if (url.includes("runtimes")) return response([]);
    // The run-detail page also fetches loop/graph/trace projections. This
    // fixture is about the trace region, not those panels, so they 404 and
    // render their empty states - returning `[]` instead would hand
    // RunExecution a malformed LoopExecution and throw asynchronously.
    if (/\/runs\/[^/]+\/(loop|graph|trace|verifications|experience)/.test(url)) {
      return response({ detail: "not found" }, 404);
    }
    if (url.includes("/api/v1/runs")) return response([RUN_FIXTURE]);
    return response([]);
  });
}

async function traceRegion() {
  installRunApi();
  renderApp("/runs/run_fixture");
  return screen.findByRole("log", { name: "Normalized event trace" });
}
test("F4: the event trace scroll region is keyboard focusable and named", async () => {
  const region = await traceRegion();
  expect(region).toHaveAttribute("tabindex", "0");
  expect(region).toHaveClass("event-list");
});

test("F4: the trace region announces updates politely rather than interrupting", async () => {
  const region = await traceRegion();
  expect(region).toHaveAttribute("aria-live", "polite");
});

// Deliberately not tested here: that focus actually lands on the region.
// jsdom does not move focus onto a tabindex-bearing div the way a browser
// does, so a passing assertion would be an artefact of the fake environment
// rather than evidence. Real keyboard reachability is verified in Chromium and
// recorded in docs/releases/v0.3/browser-a11y-evidence.md.
