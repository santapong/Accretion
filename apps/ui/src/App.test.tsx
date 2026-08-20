import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, test, vi } from "vitest";
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

test("renders runtime health and empty run state", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
  expect(screen.getByText("Runtime observatory")).toBeInTheDocument();
  expect(await screen.findByText("FAKE")).toBeInTheDocument();
  expect(screen.getByText("No runs yet. Create one through the API.")).toBeInTheDocument();
});
