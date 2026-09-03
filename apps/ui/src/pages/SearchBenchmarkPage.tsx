import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

export function SearchBenchmarkPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<string>();
  const summary = useQuery({
    queryKey: ["search-benchmark"],
    queryFn: api.searchBenchmark,
  });
  const curve = summary.data?.curve ?? [];
  const points = curve.map((point, index) => ({
    ...point,
    x: 64 + index * 190,
    y: 190 - point.mean_quality * 145,
  }));

  async function replay() {
    setStatus("Replaying frozen P6 candidate traces…");
    try {
      const report = await api.runSearchBenchmark();
      setStatus(`Reproduced ${report.task_count} held-out tasks from ${report.trace_sha256.slice(0, 12)}…`);
      await queryClient.invalidateQueries({ queryKey: ["search-benchmark"] });
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Search replay failed.");
    }
  }

  return (
    <section className="page-panel search-benchmark-page">
      <header className="section-heading"><div><p className="eyebrow">P6 research evidence</p><h1>Quality vs compute</h1></div><button className="primary-button" onClick={replay}>Reproduce N=1/2/4</button></header>
      <div className="benchmark-summary">
        <div><strong>{summary.data?.task_count ?? 0}</strong><span>held-out tasks</span></div>
        <div><strong>{curve.at(-1)?.acceptance_rate == null ? "—" : `${Math.round(curve.at(-1)!.acceptance_rate * 100)}%`}</strong><span>N=4 accepted</span></div>
        <div><strong>{summary.data?.null_gain_task_ids.length ?? 0}</strong><span>null-gain results</span></div>
        <div><strong>{summary.data?.selector_version ?? "—"}</strong><span>selector</span></div>
      </div>
      {status ? <p className="form-status benchmark-status" role="status">{status}</p> : null}
      <div className="search-research-grid">
        <figure className="search-curve">
          <figcaption><strong>Verified quality curve</strong><span>Mean selected quality rises only when a candidate passes independent verification.</span></figcaption>
          <svg viewBox="0 0 520 230" role="img" aria-label="Mean verified quality for one, two, and four candidates">
            <path d="M 44 26 V 194 H 500" className="curve-axis" />
            {[0.25, 0.5, 0.75, 1].map((value) => <g key={value}><path d={`M 44 ${190 - value * 145} H 500`} className="curve-grid" /><text x="8" y={194 - value * 145}>{value.toFixed(2)}</text></g>)}
            <polyline points={points.map((point) => `${point.x},${point.y}`).join(" ")} className="curve-line" />
            {points.map((point) => <g key={point.candidate_count}><circle cx={point.x} cy={point.y} r="6" /><text className="curve-value" x={point.x} y={point.y - 14}>{point.mean_quality.toFixed(3)}</text><text className="curve-label" x={point.x} y="216">N={point.candidate_count}</text></g>)}
          </svg>
        </figure>
        <div className="provider-comparison">
          <h2>Cross-provider replay</h2>
          {(summary.data?.provider_comparison ?? []).map((provider) => <article key={provider.provider}><div><strong>{provider.provider}</strong><span className="provider-rate">{Math.round(provider.acceptance_rate * 100)}% accepted</span></div><span>{provider.mean_best_quality.toFixed(3)}</span><small>mean best eligible quality · {provider.accepted_tasks}/{provider.task_count} tasks accepted</small></article>)}
          <p>Frozen fixture hashes</p>
          <code>{summary.data?.corpus_sha256 ?? "loading corpus…"}</code>
          <code>{summary.data?.trace_sha256 ?? "loading traces…"}</code>
          <code>{summary.data?.config_sha256 ?? "loading config…"}</code>
        </div>
      </div>
      <div className="benchmark-table-wrap" role="region" aria-label="Benchmark results" tabIndex={0}>
        <table className="benchmark-table">
          <thead><tr><th>Task</th><th>Family</th><th>N=1</th><th>N=2</th><th>N=4</th><th>N2→N4 gain</th><th>Selected provider</th></tr></thead>
          <tbody>{(summary.data?.tasks ?? []).map((task) => <tr key={task.task_id} className={summary.data?.null_gain_task_ids.includes(task.task_id) ? "null-result" : undefined}><td>{task.task_id} · {task.title}</td><td>{task.family}</td><td>{task.quality_by_candidate_count["1"].toFixed(3)}</td><td>{task.quality_by_candidate_count["2"].toFixed(3)}</td><td>{task.quality_by_candidate_count["4"].toFixed(3)}</td><td>{task.gain_from_two_to_four.toFixed(3)}</td><td>{task.selected_provider_at_four ?? "none"}</td></tr>)}</tbody>
        </table>
      </div>
    </section>
  );
}
