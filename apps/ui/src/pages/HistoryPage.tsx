import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { durationLabel } from "../runDuration";
import { shortId, terminal, useNow } from "../runState";
import { StatePill } from "../StatePill";

export function HistoryPage() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const [selectedId, setSelectedId] = useState<string>();
  const now = useNow((runs.data ?? []).some((run) => !terminal.has(run.state)));
  const selected = selectedId ?? runs.data?.[0]?.run_id;
  const audit = useQuery({
    queryKey: ["run-audit", selected],
    queryFn: () => api.audit(selected!),
    enabled: Boolean(selected),
  });
  return (
    <section className="page-panel">
      <header className="section-heading"><div><p className="eyebrow">Immutable evidence</p><h1>Run history / trace replay</h1></div></header>
      <div className="history-grid">
        <div className="run-list">{(runs.data ?? []).map((run) => <button className={selected === run.run_id ? "run selected" : "run"} key={run.run_id} onClick={() => setSelectedId(run.run_id)}><span><strong>{shortId(run.run_id)}</strong><small>{run.provider} · {run.last_sequence} events{durationLabel(run, now) ? ` · ${durationLabel(run, now)}` : ""}</small></span><StatePill state={run.state} /></button>)}</div>
        {audit.data ? (
          <article className="audit-chain">
            <h2>Complete provenance</h2>
            <ol>
              <li>Task <strong>{shortId(audit.data.task.envelope.task_id)}</strong></li>
              <li>Profile <strong>{shortId(audit.data.profile.profile_id)}</strong></li>
              <li>Strategy <strong>{audit.data.strategy.selected_mode}</strong></li>
              <li>Template <strong>{audit.data.template.template_id}@{audit.data.template.version}</strong></li>
              <li>Runtime <strong>{audit.data.runtime.provider} / {audit.data.runtime.runtime_version}</strong></li>
              <li>Events <strong>{audit.data.events?.length ?? 0}</strong></li>
              <li>Artifacts <strong>{audit.data.artifacts?.length ?? 0}</strong></li>
              <li>Verifications <strong>{audit.data.verifications?.length ?? 0}</strong></li>
            </ol>
            <p>{audit.data.trace.traversals?.length ?? 0} traversals · {audit.data.trace.tool_calls?.length ?? 0} tool calls · {audit.data.trace.checkpoints?.length ?? 0} checkpoints</p>
            <Link to={`/runs/${audit.data.run.run_id}`} className="primary-button">Open live run</Link>
          </article>
        ) : <div className="empty">Select a run to replay its linked audit trace.</div>}
      </div>
    </section>
  );
}
