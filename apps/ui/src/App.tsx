import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { AgentEvent, Run } from "./types";
import "./styles.css";

const terminal = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);

function shortId(value: string) {
  return `${value.slice(0, 8)}…${value.slice(-5)}`;
}

function StatePill({ state }: { state: string }) {
  return <span className={`pill pill-${state.toLowerCase()}`}>{state.replaceAll("_", " ")}</span>;
}

function EventStream({ run }: { run: Run | undefined }) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connection, setConnection] = useState("idle");
  const runId = run?.run_id;
  const runState = run?.state;

  useEffect(() => {
    setEvents([]);
    if (!runId) return;
    setConnection("connecting");
    const source = new EventSource(api.eventUrl(runId));
    source.addEventListener("open", () => setConnection("live"));
    source.addEventListener("agent_event", (message) => {
      const event = JSON.parse((message as MessageEvent).data) as AgentEvent;
      setEvents((current) => [...current.filter((item) => item.event_id !== event.event_id), event]);
    });
    source.addEventListener("error", () => {
      setConnection(runState && terminal.has(runState) ? "complete" : "reconnecting");
    });
    return () => source.close();
  }, [runId, runState]);

  if (!run) {
    return <div className="empty">Select a run to inspect its normalized trace.</div>;
  }

  return (
    <section className="event-panel">
      <header className="panel-header">
        <div>
          <p className="eyebrow">Normalized trace</p>
          <h2>{shortId(run.run_id)}</h2>
        </div>
        <span className={`connection connection-${connection}`}><i />{connection}</span>
      </header>
      <div className="event-list" aria-live="polite">
        {events.length === 0 ? <div className="empty">Waiting for events…</div> : null}
        {events.map((event) => (
          <article className="event" key={event.event_id}>
            <span className="sequence">{String(event.sequence).padStart(3, "0")}</span>
            <div>
              <strong>{event.normalized_type.replaceAll("_", " ")}</strong>
              <p>{event.provider} · {event.native_type}</p>
            </div>
            <time>{new Date(event.timestamp).toLocaleTimeString()}</time>
          </article>
        ))}
      </div>
    </section>
  );
}

export default function App() {
  const runtimeQuery = useQuery({ queryKey: ["runtimes"], queryFn: api.runtimes, refetchInterval: 5000 });
  const runQuery = useQuery({ queryKey: ["runs"], queryFn: api.runs, refetchInterval: 2500 });
  const [selectedId, setSelectedId] = useState<string>();
  const runs = runQuery.data ?? [];
  const selected = runs.find((run) => run.run_id === selectedId) ?? runs[0];
  const activeCount = runs.filter((run) => !terminal.has(run.state)).length;

  return (
    <main>
      <nav>
        <div className="brand-mark">A</div>
        <div><strong>Accretion</strong><span>Operator / P0</span></div>
        <div className="nav-status"><i />Control plane</div>
      </nav>

      <div className="shell">
        <header className="hero">
          <div>
            <p className="eyebrow">Runtime observatory</p>
            <h1>One control plane.<br /><em>Every execution visible.</em></h1>
          </div>
          <div className="stat"><span>{activeCount}</span><p>active runs</p></div>
        </header>

        <section className="runtime-grid" aria-label="Runtime health">
          {(runtimeQuery.data ?? []).map((runtime) => (
            <article className="runtime-card" key={runtime.runtime_id}>
              <div className="runtime-title"><span>{runtime.provider.slice(0, 1)}</span><h2>{runtime.provider}</h2></div>
              <StatePill state={runtime.status} />
              <dl>
                <div><dt>Version</dt><dd>{runtime.runtime_version}</dd></div>
                <div><dt>Pressure</dt><dd>{runtime.observed_usage_pressure}</dd></div>
                <div><dt>Active</dt><dd>{runtime.active_runs}</dd></div>
              </dl>
            </article>
          ))}
          {runtimeQuery.isError ? <div className="error">Runtime health is unavailable.</div> : null}
        </section>

        <div className="workspace-grid">
          <section className="runs-panel">
            <header className="panel-header"><div><p className="eyebrow">Recent activity</p><h2>Runs</h2></div><span>{runs.length}</span></header>
            <div className="run-list">
              {runs.map((run) => (
                <button className={selected?.run_id === run.run_id ? "run selected" : "run"} key={run.run_id} onClick={() => setSelectedId(run.run_id)}>
                  <span className="provider-dot">{run.provider.slice(0, 1)}</span>
                  <span><strong>{shortId(run.run_id)}</strong><small>{run.provider} · {run.last_sequence} events</small></span>
                  <StatePill state={run.state} />
                </button>
              ))}
              {runs.length === 0 ? <div className="empty">No runs yet. Create one through the API.</div> : null}
            </div>
          </section>
          <EventStream run={selected} />
        </div>
      </div>
    </main>
  );
}
