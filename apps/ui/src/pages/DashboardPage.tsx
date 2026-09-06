import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { durationLabel } from "../runDuration";
import { shortId, terminal, useNow } from "../runState";
import { StatePill } from "../StatePill";

function RuntimeCards() {
  const runtimeQuery = useQuery({
    queryKey: ["runtimes"],
    queryFn: api.runtimes,
    refetchInterval: 5000,
  });
  return (
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
  );
}

export function DashboardPage() {
  const runQuery = useQuery({ queryKey: ["runs"], queryFn: api.runs, refetchInterval: 2500 });
  const approvalQuery = useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () => api.approvals(undefined, "PENDING"),
    refetchInterval: 2500,
  });
  const runs = runQuery.data ?? [];
  const activeCount = runs.filter((run) => !terminal.has(run.state)).length;
  const now = useNow(activeCount > 0);
  const failureCount = runs.filter((run) => run.state === "FAILED").length;
  return (
    <>
      <header className="hero">
        <div><p className="eyebrow">Runtime observatory</p><h1>One control plane.<br /><em>Every execution visible.</em></h1></div>
        <div className="dashboard-stats">
          <div className="stat"><span>{activeCount}</span><p>active runs</p></div>
          <div className="stat"><span>{failureCount}</span><p>failures</p></div>
          <div className="stat"><span>{approvalQuery.data?.length ?? 0}</span><p>approvals</p></div>
        </div>
      </header>
      <RuntimeCards />
      <section className="runs-panel page-panel">
        <header className="panel-header"><div><p className="eyebrow">Recent activity</p><h2>Runs</h2></div><Link to="/tasks/new" className="secondary-button">New task</Link></header>
        <div className="run-list">
          {runs.map((run) => (
            <Link className="run" key={run.run_id} to={`/runs/${run.run_id}`}>
              <span className="provider-dot">{run.provider.slice(0, 1)}</span>
              <span><strong>{shortId(run.run_id)}</strong><small>{run.provider} · {run.last_sequence} events{durationLabel(run, now) ? ` · ${durationLabel(run, now)}` : ""}</small></span>
              <StatePill state={run.state} />
            </Link>
          ))}
          {!runs.length ? <div className="empty">No runs yet. Create and profile a task.</div> : null}
        </div>
      </section>
    </>
  );
}
