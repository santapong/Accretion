import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { shortId, terminal } from "./runState";
import type { AgentEvent, Run } from "./types";

export function EventStream({ run }: { run: Run | undefined }) {
  const queryClient = useQueryClient();
  const [liveEvents, setLiveEvents] = useState<{ runId: string; events: AgentEvent[] }>({
    runId: "",
    events: [],
  });
  const [connectionState, setConnectionState] = useState<{ runId: string; value: string }>({
    runId: "",
    value: "idle",
  });
  const [reconnect, setReconnect] = useState(0);
  const runId = run?.run_id;
  const runState = run?.state;
  const auditQuery = useQuery({
    queryKey: ["run-audit", runId],
    queryFn: () => api.audit(runId!),
    enabled: Boolean(runId),
    retry: false,
  });

  const eventsById = new Map(
    [
      ...(auditQuery.data?.events ?? []),
      ...(liveEvents.runId === runId ? liveEvents.events : []),
    ].map((event) => [event.event_id, event]),
  );
  const events = [...eventsById.values()].sort((left, right) => left.sequence - right.sequence);
  const connection = connectionState.runId === runId
    ? connectionState.value
    : auditQuery.data
      ? "connecting"
      : "idle";

  useEffect(() => {
    if (!runId || !auditQuery.data) return;
    let recovering = false;
    let expected = auditQuery.data.run.last_sequence + 1;
    const source = new EventSource(api.eventUrl(runId, auditQuery.data.run.last_sequence), { withCredentials: true });
    source.addEventListener("open", () => setConnectionState({ runId, value: "live" }));
    source.addEventListener("agent_event", (message) => {
      const event = JSON.parse((message as MessageEvent).data) as AgentEvent;
      if (event.sequence < expected) return;
      if (event.sequence !== expected) {
        recovering = true;
        source.close();
        setConnectionState({ runId, value: "recovering snapshot" });
        void Promise.all([
          queryClient.refetchQueries({ queryKey: ["run-audit", runId] }),
          queryClient.refetchQueries({ queryKey: ["run-graph", runId] }),
          queryClient.refetchQueries({ queryKey: ["run-trace", runId] }),
          queryClient.refetchQueries({ queryKey: ["runs"] }),
        ]).then(() => setReconnect((value) => value + 1));
        return;
      }
      expected = event.sequence + 1;
      setLiveEvents((current) => ({
        runId,
        events: [
          ...(current.runId === runId
            ? current.events.filter((item) => item.event_id !== event.event_id)
            : []),
          event,
        ],
      }));
    });
    source.addEventListener("error", () => {
      if (!recovering) {
        setConnectionState({
          runId,
          value: runState && terminal.has(runState) ? "complete" : "reconnecting",
        });
      }
    });
    return () => source.close();
  }, [auditQuery.data, queryClient, reconnect, runId, runState]);

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
      <div
        className="event-list"
        aria-live="polite"
        role="log"
        tabIndex={0}
        aria-label="Normalized event trace"
      >
        {auditQuery.isLoading ? <div className="empty">Loading authoritative snapshot…</div> : null}
        {!auditQuery.isLoading && events.length === 0 ? <div className="empty">Waiting for events…</div> : null}
        {events.map((event) => (
          <article className="event" key={event.event_id}>
            <span className="sequence">{String(event.sequence).padStart(3, "0")}</span>
            <div>
              <strong>{event.normalized_type.replaceAll("_", " ")}</strong>
              <p>{event.provider} · {event.native_type}</p>
            </div>
            <time>{event.timestamp ? new Date(event.timestamp).toLocaleTimeString() : "pending"}</time>
          </article>
        ))}
      </div>
    </section>
  );
}
