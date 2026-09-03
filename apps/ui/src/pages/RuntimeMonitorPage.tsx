import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { shortId } from "../runState";
import { StatePill } from "../StatePill";

function RuntimeSessions({ runtimeId }: { runtimeId: string }) {
  const sessions = useQuery({
    queryKey: ["runtime-sessions", runtimeId],
    queryFn: () => api.runtimeSessions(runtimeId),
    refetchInterval: 5000,
  });
  return (
    <ul className="registry-list">
      {(sessions.data ?? []).map((session) => (
        <li key={session.session_id}><strong>{shortId(session.session_id)}</strong><span>{shortId(session.run_id)} · {session.native_session_id ? shortId(session.native_session_id) : "pending native session"}</span></li>
      ))}
      {!sessions.data?.length ? <li><span>No persisted sessions.</span></li> : null}
    </ul>
  );
}

export function RuntimeMonitorPage() {
  const runtimes = useQuery({ queryKey: ["runtimes"], queryFn: api.runtimes, refetchInterval: 5000 });
  return (
    <section className="page-panel">
      <header className="section-heading"><div><p className="eyebrow">Provider health</p><h1>Runtime monitor</h1></div></header>
      <div className="registry-grid">
        {(runtimes.data ?? []).map((runtime) => (
          <article className="registry-card" key={runtime.runtime_id}>
            <header><h2>{runtime.provider}</h2><StatePill state={runtime.status} /></header>
            <p>{runtime.runtime_version} · {runtime.auth_mode} · {runtime.observed_usage_pressure}</p>
            <RuntimeSessions runtimeId={runtime.runtime_id} />
          </article>
        ))}
      </div>
    </section>
  );
}
