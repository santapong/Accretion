import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { EventStream } from "./App";
import type { AgentEvent, Run } from "./types";

class ControllableEventSource {
  static instances: ControllableEventSource[] = [];
  listeners = new Map<string, (event: Event) => void>();
  close = vi.fn();

  constructor(readonly url: string) {
    ControllableEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    if (typeof listener === "function") this.listeners.set(type, listener);
  }

  emit(type: string, event: Event) {
    this.listeners.get(type)?.(event);
  }
}

const run: Run = {
  run_id: "run_sse_fixture",
  task_id: "tsk_fixture",
  project_id: "prj_fixture",
  provider: "FAKE",
  state: "RUNNING",
  last_sequence: 5,
  revision: 1,
};

function event(sequence: number): AgentEvent {
  return {
    event_id: `evt_${sequence}`,
    run_id: run.run_id,
    session_id: "ses_fixture",
    provider: "FAKE",
    native_type: "fixture/progress",
    normalized_type: "RUN_PROGRESS",
    sequence,
    timestamp: "2026-08-22T00:00:00Z",
    correlation_id: run.run_id,
    payload: {},
    adapter_version: "fixture-v1",
  };
}

beforeEach(() => {
  ControllableEventSource.instances = [];
  vi.stubGlobal("EventSource", ControllableEventSource);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("recovers a missed SSE sequence from the authoritative audit snapshot", async () => {
  let auditReads = 0;
  vi.stubGlobal("fetch", vi.fn(async () => {
    auditReads += 1;
    const lastSequence = auditReads === 1 ? 5 : 7;
    return {
      ok: true,
      status: 200,
      json: async () => ({ run: { ...run, last_sequence: lastSequence }, events: lastSequence === 5 ? [] : [event(7)] }),
    } as Response;
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}><EventStream run={run} /></QueryClientProvider>,
  );

  await waitFor(() => expect(ControllableEventSource.instances).toHaveLength(1));
  expect(ControllableEventSource.instances[0].url).toContain("?after=5");

  act(() => {
    ControllableEventSource.instances[0].emit(
      "agent_event",
      new MessageEvent("agent_event", { data: JSON.stringify(event(7)) }),
    );
  });

  await waitFor(() => expect(auditReads).toBeGreaterThanOrEqual(2));
  await waitFor(() => expect(
    ControllableEventSource.instances.some((source) => source.url.includes("?after=7")),
  ).toBe(true));
  expect(ControllableEventSource.instances[0].close).toHaveBeenCalled();
  expect(await screen.findByText("RUN PROGRESS")).toBeInTheDocument();
});
