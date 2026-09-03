import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AcrArchFilters } from "../api";
import { StatePill } from "../StatePill";

export function BenchmarkPage() {
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState<AcrArchFilters>({});
  const [selectedTask, setSelectedTask] = useState<string>();
  const [status, setStatus] = useState<string>();
  const summary = useQuery({
    queryKey: ["acr-arch", filters],
    queryFn: () => api.acrArch(filters),
  });
  const detail = useQuery({
    queryKey: ["acr-arch-task", selectedTask],
    queryFn: () => api.acrArchTask(selectedTask!),
    enabled: Boolean(selectedTask),
  });
  const options = summary.data?.filters ?? {};

  function select(name: keyof AcrArchFilters, value: string) {
    setFilters((current) => ({ ...current, [name]: value || undefined }));
  }

  async function replay() {
    setStatus("Replaying frozen provider traces…");
    try {
      const run = await api.runAcrArch();
      setStatus(`Reproduced ${run.scenario_count} scenarios from ${run.trace_sha256.slice(0, 12)}…`);
      await queryClient.invalidateQueries({ queryKey: ["acr-arch"] });
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Benchmark replay failed.");
    }
  }

  return (
    <section className="page-panel">
      <header className="section-heading"><div><p className="eyebrow">Architecture evaluation</p><h1>ACR-ARCH</h1></div><button className="primary-button" onClick={replay}>Reproduce replay</button></header>
      <div className="benchmark-summary">
        <div><strong>{summary.data?.task_count ?? 0}</strong><span>tasks</span></div>
        <div><strong>{summary.data?.scenario_count ?? 0}</strong><span>mode scenarios</span></div>
        <div><strong>{summary.data?.suite_version ?? "—"}</strong><span>corpus version</span></div>
        <div><strong>{summary.data?.configuration_version ?? "—"}</strong><span>config version</span></div>
      </div>
      <div className="benchmark-filters" aria-label="ACR-ARCH filters">
        {(["mode", "provider", "task_type", "verifier", "selector_version"] as const).map((name) => (
          <label key={name}>{name.replaceAll("_", " ")}<select value={filters[name] ?? ""} onChange={(event) => select(name, event.target.value)}><option value="">All</option>{(options[name] ?? []).map((value) => <option key={value}>{value}</option>)}</select></label>
        ))}
      </div>
      {status ? <p className="form-status benchmark-status" role="status">{status}</p> : null}
      <div className="benchmark-table-wrap" role="region" aria-label="Benchmark results" tabIndex={0}>
        <table className="benchmark-table">
          <thead><tr><th>Task</th><th>Mode</th><th>Provider</th><th>Success</th><th>Quality</th><th>Cost</th><th>Latency</th><th>Risk</th><th>Human</th><th>Utility</th><th>Regret</th></tr></thead>
          <tbody>{(summary.data?.metrics ?? []).map((metric) => <tr key={metric.metric_id}><td><button onClick={() => setSelectedTask(metric.benchmark_task_id)}>{metric.benchmark_task_id}</button></td><td>{metric.mode}</td><td>{metric.provider}</td><td><StatePill state={metric.success ? "PASS" : "FAIL"} /></td><td>{metric.quality.toFixed(3)}</td><td>{metric.cost.toFixed(3)}</td><td>{metric.latency.toFixed(3)}</td><td>{metric.risk.toFixed(3)}</td><td>{metric.human_burden.toFixed(3)}</td><td>{metric.utility.toFixed(3)}</td><td>{metric.architecture_regret.toFixed(3)}</td></tr>)}</tbody>
        </table>
        {!summary.isLoading && !summary.data?.metrics?.length ? <div className="empty">No benchmark scenarios match these filters.</div> : null}
      </div>
      {detail.data ? <aside className="benchmark-detail"><div><p className="eyebrow">Versioned task</p><h3>{detail.data.task.title}</h3></div><StatePill state={detail.data.task.category} /><p>{detail.data.task.environment_ref}@{detail.data.task.environment_version} · {detail.data.task.verifier_id}@{detail.data.task.verifier_version}</p><ul>{detail.data.task.success_criteria.map((criterion) => <li key={criterion}>{criterion}</li>)}</ul></aside> : null}
    </section>
  );
}
