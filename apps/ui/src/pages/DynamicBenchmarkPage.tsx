import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { StatePill } from "../StatePill";

export function DynamicBenchmarkPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<string>();
  const summary = useQuery({
    queryKey: ["dynamic-benchmark"],
    queryFn: api.dynamicBenchmark,
  });

  async function replay() {
    setStatus("Replaying frozen P5 static and dynamic treatments…");
    try {
      const report = await api.runDynamicBenchmark();
      setStatus(`Reproduced ${report.trace_count} traces; gate ${report.gate.passed ? "passed" : "failed"}.`);
      await queryClient.invalidateQueries({ queryKey: ["dynamic-benchmark"] });
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Dynamic benchmark replay failed.");
    }
  }

  const gate = summary.data?.gate;
  return (
    <section className="page-panel experience-benchmark-page">
      <header className="section-heading"><div><p className="eyebrow">P5 preregistered evidence</p><h1>Dynamic workflow gate</h1></div><button className="primary-button" onClick={replay}>Reproduce static vs dynamic</button></header>
      <div className="benchmark-summary">
        <div><strong>{summary.data?.task_count ?? 0}</strong><span>held-out tasks</span></div>
        <div><strong>{summary.data?.trace_count ?? 0}</strong><span>paired traces</span></div>
        <div><strong>{gate?.research_classification ?? "—"}</strong><span>research result</span></div>
        <div><strong>{gate ? (gate.passed ? "PASS" : "FAIL") : "—"}</strong><span>release gate</span></div>
      </div>
      {status ? <p className="form-status benchmark-status" role="status">{status}</p> : null}
      {gate ? <div className="experience-gate-grid" aria-label="P5 dynamic workflow gate checks">
        <article><StatePill state={gate.benefit_passed ? "PASS" : "EXPERIMENTAL"} /><strong>Heterogeneous benefit</strong><span>{gate.heterogeneous_uncertain_uplift >= 0 ? "+" : ""}{gate.heterogeneous_uncertain_uplift.toFixed(3)} utility</span></article>
        <article><StatePill state={gate.predictable_non_inferiority_passed ? "PASS" : "FAIL"} /><strong>Predictable cohort</strong><span>{gate.predictable_uplift >= 0 ? "+" : ""}{gate.predictable_uplift.toFixed(3)} utility</span></article>
        <article><StatePill state={gate.safety_invariants_passed ? "PASS" : "FAIL"} /><strong>v0.1 invariants</strong><span>No risk or false-accept regression</span></article>
        <article><StatePill state={gate.static_fallback_operational ? "PASS" : "FAIL"} /><strong>Static fallback</strong><span>Invalid proposals degrade safely</span></article>
      </div> : null}
      <div className="benchmark-table-wrap" role="region" aria-label="Benchmark results" tabIndex={0}>
        <table className="benchmark-table">
          <thead><tr><th>Treatment</th><th>Success</th><th>Quality</th><th>Utility</th><th>Turns</th><th>Tools</th><th>Invalid</th><th>Replan</th><th>Human</th><th>Graph nodes/depth</th><th>Variation</th></tr></thead>
          <tbody>{(summary.data?.treatments ?? []).map((treatment) => <tr key={treatment.treatment}><td>{treatment.treatment}</td><td>{Math.round(treatment.success_rate * 100)}%</td><td>{treatment.mean_quality.toFixed(3)}</td><td>{treatment.mean_utility.toFixed(3)}</td><td>{treatment.mean_turns.toFixed(1)}</td><td>{treatment.mean_tool_calls.toFixed(1)}</td><td>{Math.round(treatment.invalid_proposal_rate * 100)}%</td><td>{Math.round(treatment.replan_rate * 100)}%</td><td>{Math.round(treatment.human_intervention_rate * 100)}%</td><td>{treatment.mean_graph_nodes.toFixed(1)} / {treatment.mean_graph_depth.toFixed(1)}</td><td>{Math.round(treatment.structural_variation_rate * 100)}%</td></tr>)}</tbody>
        </table>
      </div>
      <div className="experience-benchmark-evidence">
        <div><h2>Cohort comparison</h2><ul>{(summary.data?.cohorts ?? []).map((cohort) => <li key={cohort.cohort}><strong>{cohort.cohort}</strong> · static {cohort.static_mean_utility.toFixed(3)} · dynamic {cohort.dynamic_mean_utility.toFixed(3)} · uplift {cohort.utility_uplift >= 0 ? "+" : ""}{cohort.utility_uplift.toFixed(3)}</li>)}</ul></div>
        <div><h2>Frozen fixture hashes</h2><code>{summary.data?.corpus_sha256 ?? "loading corpus…"}</code><code>{summary.data?.trace_sha256 ?? "loading traces…"}</code><code>{summary.data?.config_sha256 ?? "loading config…"}</code></div>
      </div>
    </section>
  );
}
