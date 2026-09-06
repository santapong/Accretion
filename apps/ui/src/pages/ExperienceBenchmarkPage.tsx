import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { StatePill } from "../StatePill";

export function ExperienceBenchmarkPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<string>();
  const summary = useQuery({
    queryKey: ["experience-benchmark"],
    queryFn: api.experienceBenchmark,
  });

  async function replay() {
    setStatus("Replaying frozen P7 experience treatments…");
    try {
      const report = await api.runExperienceBenchmark();
      setStatus(`Reproduced ${report.trace_count} traces; gate ${report.gate.passed ? "passed" : "failed"}.`);
      await queryClient.invalidateQueries({ queryKey: ["experience-benchmark"] });
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Experience benchmark replay failed.");
    }
  }

  const gate = summary.data?.gate;
  return (
    <section className="page-panel experience-benchmark-page">
      <header className="section-heading"><div><p className="eyebrow">P7 preregistered evidence</p><h1>Experience transfer gate</h1></div><button className="primary-button" onClick={replay}>Reproduce P7 gate</button></header>
      <div className="benchmark-summary">
        <div><strong>{summary.data?.task_count ?? 0}</strong><span>held-out tasks</span></div>
        <div><strong>{summary.data?.source_count ?? 0}</strong><span>frozen sources</span></div>
        <div><strong>{summary.data?.trace_count ?? 0}</strong><span>treatment traces</span></div>
        <div><strong>{gate ? (gate.passed ? "PASS" : "FAIL") : "—"}</strong><span>release gate</span></div>
      </div>
      {status ? <p className="form-status benchmark-status" role="status">{status}</p> : null}
      {gate ? <div className="experience-gate-grid" aria-label="P7 gate checks">
        <article><StatePill state={gate.false_accepts_not_increased ? "PASS" : "FAIL"} /><strong>False accepts</strong><span>No increase</span></article>
        <article><StatePill state={gate.stale_rejection_passed ? "PASS" : "FAIL"} /><strong>Stale rejection</strong><span>{Math.round(gate.stale_rejection_rate * 100)}%</span></article>
        <article><StatePill state={gate.negative_transfer_passed ? "PASS" : "FAIL"} /><strong>Negative transfer</strong><span>{(gate.negative_transfer_rate * 100).toFixed(2)}%</span></article>
        <article><StatePill state={gate.benefit_passed ? "PASS" : "FAIL"} /><strong>Replay benefit</strong><span>+{gate.replay_quality_uplift.toFixed(3)} quality · {Math.round(gate.replay_tool_call_reduction * 100)}% fewer tools</span></article>
      </div> : null}
      <div className="benchmark-table-wrap" role="region" aria-label="Benchmark results" tabIndex={0}>
        <table className="benchmark-table">
          <thead><tr><th>Treatment</th><th>Success</th><th>Quality</th><th>Uplift</th><th>Turns</th><th>Tools</th><th>Tool reduction</th><th>Negative transfer</th><th>False accepts</th><th>Use / reject / null</th></tr></thead>
          <tbody>{(summary.data?.treatments ?? []).map((treatment) => <tr key={treatment.treatment}><td>{treatment.treatment.replaceAll("_", " ")}</td><td>{Math.round(treatment.success_rate * 100)}%</td><td>{treatment.mean_quality.toFixed(3)}</td><td>{treatment.quality_uplift.toFixed(3)}</td><td>{treatment.mean_turns.toFixed(1)}</td><td>{treatment.mean_tool_calls.toFixed(1)}</td><td>{Math.round(treatment.tool_call_reduction * 100)}%</td><td>{treatment.negative_transfers}</td><td>{treatment.false_accepts}</td><td>{Math.round(treatment.experience_use_rate * 100)} / {Math.round(treatment.experience_rejection_rate * 100)} / {Math.round(treatment.experience_null_rate * 100)}%</td></tr>)}</tbody>
        </table>
      </div>
      <div className="experience-benchmark-evidence">
        <div><h2>Reported negative results</h2><ul>{(summary.data?.tasks ?? []).filter((task) => task.negative_transfer_treatments.length).map((task) => <li key={task.task_id}><strong>{task.task_id}</strong> · {task.title} · {task.negative_transfer_treatments.join(", ")}</li>)}</ul></div>
        <div><h2>Frozen fixture hashes</h2><code>{summary.data?.corpus_sha256 ?? "loading corpus…"}</code><code>{summary.data?.source_sha256 ?? "loading sources…"}</code><code>{summary.data?.trace_sha256 ?? "loading traces…"}</code><code>{summary.data?.config_sha256 ?? "loading config…"}</code></div>
      </div>
    </section>
  );
}
