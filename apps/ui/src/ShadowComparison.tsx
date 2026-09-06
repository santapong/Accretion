import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "./api";
import { StatePill } from "./StatePill";
import { shadowStages } from "./shadowStages";
import type { GateStatus, Run, ShadowPair } from "./types";

/**
 * The SDD §17.2 shadow comparison: what a shadow policy would have chosen, and what happened.
 *
 * ## What it shows, and for whom
 *
 * Beside the §17.1 routing panel, which explains one node's executed decision, this panel
 * explains the counterfactual beside it. For every receipt of this run that a shadow stage
 * scored it shows the executed baseline receipt against the shadow recommendation, whether
 * the two agreed, the delta the router *predicted* and the delta the two forks actually
 * *observed* — and beneath them the accumulated non-inferiority evidence and the gates a
 * promotion still owes.
 *
 * Predicted and observed are rendered side by side and never collapsed into one number,
 * because they are the two halves of the only question this view exists to answer: a policy
 * that projects a large gain and observes none is exactly the policy the shadow stage is
 * there to catch, and a panel showing either alone would hide it.
 *
 * ## Where its data comes from
 *
 * `GET /api/v1/shadow-policies/{version_id}/report` — the M6.2 read, whose numbers M8.2's
 * promotion gate is defined over, so an operator arguing with a promotion is reading the
 * same arithmetic that authorised it. The version ids come from `GET /api/v1/router-models`,
 * filtered to `SHADOW` here rather than server-side because that route takes no status
 * filter, and the workspace comes from the caller's first membership — the shell's own rule.
 *
 * ## What it does not ask for
 *
 * A run whose audit named no routing receipt asks for nothing at all: routing is opt-in, so
 * most runs have no receipts, and a run with none cannot have been shadowed. A workspace
 * with no `SHADOW` version stops at the version list and never requests a report, because
 * there is no version id to request one for — the disabled state is the absence of a policy,
 * not an empty report.
 *
 * ## Why the aggregate is labelled as the stage's
 *
 * The pairs are narrowed to this run; `paired_count`, `agreement_rate`, `mean_delta` and
 * `delta_lcb` are not, and cannot be — they are computed over every decision of the stage,
 * and recomputing them from one run's pairs would produce a second, weaker interval that no
 * promotion is gated on. So the aggregate says which population it describes, next to the
 * pairs that say which of them came from here.
 */
export function ShadowComparison({
  run,
  receipts,
}: {
  run: Run;
  receipts: readonly string[];
}) {
  const routed = receipts.length > 0;
  const meQuery = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false, enabled: routed });
  const workspaceId = meQuery.data?.memberships?.[0]?.workspace_id;

  const versionsQuery = useQuery({
    queryKey: ["router-models", workspaceId],
    queryFn: () => api.routerModels(workspaceId!),
    enabled: routed && Boolean(workspaceId),
    retry: false,
  });
  const shadowVersions = useMemo(
    () => shadowStages(versionsQuery.data, run.project_id),
    [versionsQuery.data, run.project_id],
  );

  const [selectedVersionId, setSelectedVersionId] = useState<string>();
  const version =
    shadowVersions.find((candidate) => candidate.contract_id === selectedVersionId) ??
    shadowVersions[0];
  const versionId = version?.contract_id;

  const reportQuery = useQuery({
    queryKey: ["shadow-report", versionId],
    queryFn: () => api.shadowPolicyReport(versionId!),
    enabled: Boolean(versionId),
    retry: false,
  });
  const report = reportQuery.data;
  const pairs = report?.pairs ?? [];
  const gates = report?.remaining_gates ?? [];
  const mine = useMemo(() => runPairs(report?.pairs ?? [], receipts), [report, receipts]);

  return (
    <section className="dynamic-inspector" aria-labelledby="shadow-comparison-heading">
      <header>
        <div>
          <p className="eyebrow">§17.2 shadow comparison</p>
          <h3 id="shadow-comparison-heading">Shadow comparison</h3>
        </div>
        {report ? <StatePill state={report.non_inferior ? "PASS" : "INCONCLUSIVE"} /> : null}
      </header>

      {!routed ? (
        <p className="quiet">
          Run {run.run_id} recorded no routing receipt, so no decision of it was shadowed.
          A shadow policy scores the decisions a routed run made; there are none here to
          score.
        </p>
      ) : !workspaceId ? (
        <p className="quiet">
          The workspace behind this run could not be read, so the shadow stages registered
          against it cannot be listed.
        </p>
      ) : versionsQuery.isError ? (
        <p className="quiet">The workspace&apos;s router versions could not be read.</p>
      ) : versionsQuery.isPending ? (
        <p className="quiet">Reading the workspace&apos;s router versions…</p>
      ) : !versionId ? (
        <p className="quiet">
          No router version is in SHADOW for this run&apos;s project or its workspace, so there
          is no recommendation to compare against what ran. Register a trained candidate with
          POST /api/v1/shadow-policies to start a shadow stage; until one exists this view has
          no report to read.
        </p>
      ) : (
        <>
          {shadowVersions.length > 1 ? (
            <div className="replan-control">
              <label htmlFor="shadow-version-picker">
                Shadow stage
                <select
                  id="shadow-version-picker"
                  value={versionId}
                  onChange={(event) => setSelectedVersionId(event.target.value)}
                >
                  {shadowVersions.map((candidate) => (
                    <option key={candidate.contract_id} value={candidate.contract_id}>
                      {candidate.contract_id} · {candidate.algorithm_id}
                    </option>
                  ))}
                </select>
              </label>
              <p className="quiet">
                {shadowVersions.length} shadow stages are registered; each is scored
                separately and their evidence is never pooled.
              </p>
            </div>
          ) : (
            <p className="quiet">
              Shadow stage <code>{versionId}</code> ({version?.scope.replaceAll("_", " ")},{" "}
              {version?.algorithm_id}).
            </p>
          )}

          {reportQuery.isError ? (
            <p className="quiet">The shadow report for this stage could not be read.</p>
          ) : null}

          {report ? (
            <article className="proposal-inspector">
              <div className="dynamic-metrics">
                <span>
                  <strong>{report.paired_count}</strong> complete pairs in the stage
                </span>
                <span>
                  <strong>{percent(report.agreement_rate)}</strong> agreement with the
                  executed decision
                </span>
                <span>
                  <strong>{signed(report.mean_delta)}</strong> mean observed utility delta
                </span>
                <span>
                  <strong>{signed(report.delta_lcb)}</strong> lower confidence bound
                </span>
                <span>
                  <strong>{report.non_inferior ? "non-inferior" : "not yet non-inferior"}</strong>{" "}
                  on the accumulated evidence
                </span>
              </div>
              <p className="quiet">
                Those five are the whole stage&apos;s evidence, over every run it scored. The
                pairs below are the {mine.length} of {pairs.length} that came from this run.
              </p>

              <div className="router-evidence">
                <div>
                  <h4 id="shadow-pairs-heading">Executed vs shadow</h4>
                  {mine.length ? (
                    <ul
                      className="router-fallback"
                      aria-labelledby="shadow-pairs-heading"
                      tabIndex={0}
                    >
                      {mine.map((pair, index) => (
                        <li key={`${pair.executed_receipt_id}-${pair.shadow_receipt_id}-${index}`}>
                          <span>{pair.agreement ? "agreed" : "differed"}</span>
                          <dl className="router-features">
                            <div>
                              <dt>executed</dt>
                              <dd>
                                <code>{pair.executed_receipt_id}</code>
                              </dd>
                            </div>
                            <div>
                              <dt>shadow</dt>
                              <dd>
                                <code>{pair.shadow_receipt_id}</code>
                              </dd>
                            </div>
                            <div>
                              <dt>predicted delta</dt>
                              <dd>{signed(pair.projected_utility_delta)}</dd>
                            </div>
                            <div>
                              <dt>observed delta</dt>
                              <dd>{observed(pair)}</dd>
                            </div>
                          </dl>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="quiet">
                      None of this stage&apos;s {pairs.length} pairs came from this run. The
                      stage scores the workspace&apos;s routed decisions, and it scored none
                      of this run&apos;s.
                    </p>
                  )}
                </div>
                <div>
                  <h4 id="shadow-gates-heading">Promotion gates</h4>
                  <ul className="router-fallback" aria-labelledby="shadow-gates-heading">
                    {gates.map((gate) => (
                      <li key={gate.gate}>
                        <span>{gate.gate.replaceAll("_", " ")}</span>
                        <StatePill state={gate.met ? "PASS" : "PENDING"} />
                        <small>{gate.evidence}</small>
                      </li>
                    ))}
                  </ul>
                  <p className="quiet">{unmetSummary(gates)}</p>
                </div>
              </div>
            </article>
          ) : null}
        </>
      )}
    </section>
  );
}

/**
 * The stage's pairs that belong to this run, in report order.
 *
 * A pair is this run's when either half names a receipt the run's audit recorded. Matching
 * on `executed_receipt_id` alone would be the obvious rule and is not sufficient: the
 * branched rollout writes the shadow fork's own receipt, and an audit that recorded it makes
 * the pair this run's evidence whichever side it landed on. Matching on neither — rendering
 * the whole report — would put another run's decisions on this run's page, which is the one
 * thing a comparison view must never do.
 */
function runPairs(
  pairs: readonly ShadowPair[],
  receipts: readonly string[],
): readonly ShadowPair[] {
  const mine = new Set(receipts);
  return pairs.filter(
    (pair) => mine.has(pair.executed_receipt_id) || mine.has(pair.shadow_receipt_id),
  );
}

/**
 * One delta, signed, at the precision the report recorded it.
 *
 * Deliberately not re-rounded: `shadow_report` already rounds to six decimals, and a panel
 * that rounded again to three would render a 0.0004 mean as `0.000` — the value a reader
 * would take for "no difference measured", which is a different claim from "a difference too
 * small to matter". The sign is explicit because the whole quantity is a comparison.
 */
function signed(value: number): string {
  return value > 0 ? `+${value}` : String(value);
}

/** An agreement rate as a percentage; a rate, unlike a delta, is safe to round for reading. */
function percent(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

/**
 * What the two forks observed, or why there is no number yet.
 *
 * `observed_delta` is null exactly when one of the forks produced no result, and the
 * contract's own validator refuses any other combination. It is rendered as pending rather
 * than as `0`, because zero is the value that says "the shadow changed nothing" — the
 * verdict this stage exists to reach — and showing it for a pair that was never measured
 * would be evidence invented out of an absence.
 */
function observed(pair: ShadowPair): string {
  return pair.observed_delta === null || pair.observed_delta === undefined
    ? "pending — the paired forks produced no result"
    : signed(pair.observed_delta);
}

/** Which gates a promotion still owes, named, or that it owes none. */
function unmetSummary(gates: readonly GateStatus[]): string {
  const unmet = gates.filter((gate) => !gate.met).map((gate) => gate.gate.replaceAll("_", " "));
  if (!gates.length) return "This report names no promotion gate.";
  return unmet.length
    ? `Promotion is blocked on ${unmet.length} unmet gate(s): ${unmet.join(", ")}.`
    : "Every gate this report names is met; promotion is still a human decision.";
}
