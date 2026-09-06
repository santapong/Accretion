import { FormEvent, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { StatePill } from "../StatePill";
import type {
  CohortResult,
  MetricComparison,
  RegressionFinding,
  RouterActivation,
  RouterLineage,
  RouterLineageEntry,
  RouterModelVersion,
  RouterPromotionReport,
} from "../types";

/** The two workspace roles §10.3 lets administer a router family. */
const ADMINISTERING_ROLES = ["OWNER", "ADMIN"];

/**
 * SDD §17.3: where a router version came from, what it was judged on, and who may change it.
 *
 * ## The active version is the ledger head
 *
 * `active_version_id` on the lineage is the last activation entry's `router_version_id`,
 * and NOT "the row whose status column reads ACTIVE". Those are different answers, and the
 * ledger is the one routing reads (ADR-061). The version rows are append-only: promotion
 * mints a row that says ACTIVE and rollback mints two more — a restored copy of the target
 * and a tombstone for the withdrawn one — while every earlier row keeps whatever it said
 * when it was written. A workspace that has promoted once and rolled back therefore holds
 * several rows all claiming ACTIVE, so a page that read the status column would name one of
 * them and be right only by accident. This page renders the head, labels it as such, and
 * shows the status column beside it precisely so the two can be seen to differ.
 *
 * ## What it can and cannot reach
 *
 * The promotion reports it can open are the ones the family's activations cite, because
 * that is the whole of the read surface: `GET /api/v1/router-promotions/{report_id}` takes
 * an id and there is no list route. An evaluation that has never been acted on — the usual
 * state of a report that decided REQUIRE_REVIEW or REJECT — is therefore reachable only by
 * its id, which is why the report control is an input with the cited ids offered as
 * suggestions rather than a closed picker over them.
 *
 * ## Who may act
 *
 * Promotion changes which policy serves every project in the workspace and rollback
 * withdraws it during an incident, so §10.3 makes both a human act by someone accountable
 * for the workspace. The two controls render only for a membership whose role administers
 * it. That is a rendering decision and nothing more: the server re-checks the role on every
 * request, and a refusal is rendered here with the message it sent.
 */
export function RouterAdminPage() {
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  const membership = me.data?.memberships?.[0];
  const workspaceId = membership?.workspace_id;
  const administers = Boolean(membership && ADMINISTERING_ROLES.includes(membership.role));

  const versions = useQuery({
    queryKey: ["router-models", workspaceId],
    queryFn: () => api.routerModels(workspaceId!),
    enabled: Boolean(workspaceId),
    retry: false,
  });

  const [pickedVersionId, setPickedVersionId] = useState<string>();
  const [pickedReportId, setPickedReportId] = useState<string>();
  const [feedback, setFeedback] = useState<string>();
  const [refusal, setRefusal] = useState<string>();

  const rows = versions.data ?? [];
  // The newest row rather than the first: every member of a family shares one activation
  // ledger, so the lineage answers the same question from any of them, and the row an
  // operator has just trained is the one they are usually asking about.
  const versionId = pickedVersionId ?? rows.at(-1)?.contract_id;

  const lineage = useQuery({
    queryKey: ["router-lineage", versionId, workspaceId],
    queryFn: () => api.routerLineage(versionId!, workspaceId!),
    enabled: Boolean(versionId && workspaceId),
    retry: false,
  });

  // An activation cites the report that authorised it, and a rollback cites none, so the
  // same id repeats across a promote-and-withdraw pair. Deduplicated in ledger order.
  const citedReportIds = [...new Set(lineage.data?.promotion_report_ids ?? [])];
  const reportId = pickedReportId ?? citedReportIds.at(-1);

  const report = useQuery({
    queryKey: ["router-promotion", reportId, workspaceId],
    queryFn: () => api.routerPromotion(reportId!, workspaceId!),
    enabled: Boolean(reportId && workspaceId),
    retry: false,
  });

  async function invalidate() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["router-models", workspaceId] }),
      queryClient.invalidateQueries({ queryKey: ["router-lineage", versionId, workspaceId] }),
      queryClient.invalidateQueries({ queryKey: ["router-promotion", reportId, workspaceId] }),
    ]);
  }

  async function promote() {
    if (!reportId || !workspaceId) return;
    setRefusal(undefined);
    setFeedback(`Promoting the candidate that report ${reportId} authorises…`);
    try {
      const activation = await api.promoteRouter(reportId, workspaceId);
      setFeedback(activationText("Promoted", activation));
      await invalidate();
    } catch (error) {
      // The server's own text — "report rpr_… decided REQUIRE_REVIEW", "workspace owner or
      // admin role is required" — is the answer the operator came for. A generic string
      // would replace the one fact this surface exists to deliver.
      setRefusal(error instanceof Error ? error.message : "The promotion was refused.");
      setFeedback("The promotion was refused; nothing was activated.");
    }
  }

  async function rollback(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const cause = String(new FormData(form).get("cause") ?? "").trim();
    const head = lineage.data?.active_version_id;
    if (!cause || !head || !workspaceId) return;
    setRefusal(undefined);
    setFeedback(`Withdrawing the ledger head ${head}…`);
    try {
      const activation = await api.rollbackRouter(head, workspaceId, { cause, run_id: null });
      setFeedback(activationText("Rolled back", activation));
      form.reset();
      await invalidate();
    } catch (error) {
      setRefusal(error instanceof Error ? error.message : "The rollback was refused.");
      setFeedback("The rollback was refused; the ledger head is unchanged.");
    }
  }

  function loadReport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const asked = String(new FormData(event.currentTarget).get("report_id") ?? "").trim();
    setRefusal(undefined);
    setPickedReportId(asked || undefined);
  }

  return (
    <section className="page-panel">
      <header className="section-heading">
        <div>
          <p className="eyebrow">§17.3 router administration</p>
          <h1>Router administration</h1>
        </div>
        {lineage.data ? <StatePill state={lineage.data.scope} /> : null}
      </header>
      <p className="page-status" role="status">
        {feedback ?? statusLine(workspaceId, rows.length, versions.isError)}
      </p>

      {refusal ? (
        <section aria-label="Refused action" tabIndex={-1} className="registry-card" role="alert">
          <h2>Refused</h2>
          <p aria-label="Refusal reason">{refusal}</p>
        </section>
      ) : null}

      {workspaceId ? (
        <>
          <section aria-label="Router versions" tabIndex={0} className="registry-card">
            <h2>Router versions</h2>
            <p className="quiet">
              Workspace {workspaceId} · {rows.length} version(s) ·{" "}
              {administers ? `${membership?.role} may promote and roll back` : "read-only"}
            </p>
            <label htmlFor="router-version-picker">
              Inspect version
              <select
                id="router-version-picker"
                value={versionId ?? ""}
                onChange={(event) => {
                  setPickedVersionId(event.target.value);
                  setPickedReportId(undefined);
                }}
              >
                {rows.map((row) => (
                  <option key={row.contract_id} value={row.contract_id}>
                    {row.contract_id} · {row.status}
                  </option>
                ))}
              </select>
            </label>
            <VersionRows rows={rows} head={lineage.data?.active_version_id ?? null} />
            {rows.length === 0 ? (
              <p className="empty">
                {versions.isError
                  ? "The router versions of this workspace could not be read."
                  : "This workspace has trained no router version yet."}
              </p>
            ) : null}
          </section>

          {lineage.data ? (
            <>
              <ActiveVersion lineage={lineage.data} rows={rows} />
              <LineageChain chain={lineage.data.parent_chain ?? []} />
              <ActivationLedger activations={lineage.data.activations ?? []} />
            </>
          ) : (
            <section aria-label="Lineage" tabIndex={0} className="registry-card">
              <h2>Lineage</h2>
              <p className="empty">
                {lineage.isError
                  ? "The lineage of this version could not be read."
                  : "Select a router version to read its lineage."}
              </p>
            </section>
          )}

          <section aria-label="Promotion report" tabIndex={0} className="registry-card">
            <h2>Promotion report</h2>
            <form className="task-form" onSubmit={loadReport} aria-label="Open a promotion report">
              <label>
                Report id
                <input
                  name="report_id"
                  key={reportId ?? "none"}
                  defaultValue={reportId ?? ""}
                  list="router-report-ids"
                  placeholder="rpr_…"
                />
              </label>
              <datalist id="router-report-ids">
                {citedReportIds.map((cited) => (
                  <option key={cited} value={cited} />
                ))}
              </datalist>
              <div className="form-actions field-wide">
                <button className="secondary-button" type="submit">Open report</button>
              </div>
            </form>
            <p className="quiet" aria-label="Cited reports">
              {citedReportIds.length
                ? `Cited by this family's activations: ${citedReportIds.join(", ")}`
                : "No activation of this family cites a promotion report."}
            </p>
            {report.data ? (
              <PromotionReport report={report.data} />
            ) : (
              <p className="empty">
                {report.isError
                  ? `Promotion report ${reportId} could not be read.`
                  : "No promotion report is open."}
              </p>
            )}
          </section>

          {administers ? (
            <section
              aria-label="Promotion and rollback"
              tabIndex={0}
              className="registry-card"
            >
              <h2>Promotion and rollback</h2>
              <p className="quiet">
                Both acts are attributed to {membership?.principal_id} and recorded in the
                activation ledger. The server re-checks the role and the ledger head on every
                request, so a control offered here is still refused if either has moved.
              </p>
              <div className="router-evidence" role="group" aria-label="Promote a report">
                <div>
                  <h3>Promote</h3>
                  <p className="quiet" aria-label="Promotion authority">
                    {promotionAuthority(report.data, reportId)}
                  </p>
                  <button
                    className="primary-button"
                    type="button"
                    disabled={report.data?.decision !== "PROMOTE"}
                    onClick={promote}
                  >
                    Promote the candidate this report names
                  </button>
                </div>
              </div>
              <form className="task-form" onSubmit={rollback} aria-label="Roll back the ledger head">
                <label>
                  Cause
                  <input
                    name="cause"
                    required
                    placeholder="Why the active router is being withdrawn"
                  />
                </label>
                <div className="form-actions field-wide">
                  <button
                    className="secondary-button"
                    type="submit"
                    disabled={!lineage.data?.active_version_id}
                  >
                    Roll back {lineage.data?.active_version_id ?? "the ledger head"}
                  </button>
                </div>
              </form>
            </section>
          ) : (
            <p className="quiet" aria-label="Administration unavailable">
              Promotion and rollback need a workspace owner or admin role; this membership is{" "}
              {membership?.role ?? "absent"}.
            </p>
          )}
        </>
      ) : (
        <p className="empty" aria-label="No workspace">
          This principal is a member of no workspace, so no router family is addressable.
        </p>
      )}
    </section>
  );
}

/** One measured comparison, with its interval, so a delta is never read without its width. */
function metricLine(metric: MetricComparison): string {
  return (
    `${metric.metric_id}: ${metric.candidate_value} against ${metric.baseline_value} · ` +
    `delta ${metric.delta} [${metric.delta_lower_bound}, ${metric.delta_upper_bound}] · ` +
    (metric.passed ? "gate met" : "gate not met")
  );
}

function findingLine(finding: RegressionFinding): string {
  return (
    `${finding.severity} · ${finding.metric_id} · ${finding.description}` +
    (finding.disclosed_bound ? ` (${finding.disclosed_bound})` : "")
  );
}

function cohortLine(cohort: CohortResult): string {
  return (
    `${cohort.cohort_id} (${cohort.critical ? "critical" : "non-critical"}, n=` +
    `${cohort.sample_size}) · ${metricLine(cohort.comparison)} · ${cohort.description}`
  );
}

function activationText(verb: string, activation: RouterActivation): string {
  return (
    `${verb}: activation ${activation.contract_id} at sequence ${activation.sequence} ` +
    `made ${activation.router_version_id} the ledger head.`
  );
}

function statusLine(
  workspaceId: string | undefined,
  count: number,
  failed: boolean,
): string {
  if (!workspaceId) return "No workspace membership, so no router version can be read.";
  if (failed) return `The router versions of ${workspaceId} could not be read.`;
  return `${count} router version(s) in ${workspaceId}.`;
}

/** Why the promote control is or is not offered for the report that is open. */
function promotionAuthority(
  report: RouterPromotionReport | undefined,
  reportId: string | undefined,
): string {
  if (!report) {
    return reportId
      ? `Report ${reportId} is not open, so nothing authorises a promotion.`
      : "Open a promotion report to promote the candidate it names.";
  }
  if (report.decision !== "PROMOTE") {
    return (
      `Report ${report.contract_id} decided ${report.decision}, which authorises no ` +
      `promotion of ${report.candidate_version}.`
    );
  }
  return (
    `Report ${report.contract_id} decided PROMOTE for ${report.candidate_version} over ` +
    `${report.baseline_version}, restoring ${report.rollback_target} if it is withdrawn.`
  );
}

/**
 * Every version row, with the head marked.
 *
 * The status column is shown in full rather than reduced to "the active one" because it is
 * the column that cannot be trusted alone: these rows are append-only, so more than one may
 * read ACTIVE at once, and the marker beside them comes from the ledger instead.
 */
function VersionRows({
  rows,
  head,
}: {
  rows: readonly RouterModelVersion[];
  head: string | null;
}) {
  return (
    <table className="benchmark-table">
      <thead>
        <tr><th>Version</th><th>Status</th><th>Parent</th><th>Trained on</th><th>Ledger</th></tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.contract_id} aria-label={row.contract_id}>
            <td>{row.contract_id}</td>
            <td><StatePill state={row.status} /></td>
            <td>{row.parent_version_id ?? "no parent"}</td>
            <td>{row.training_snapshot_id}</td>
            <td>{row.contract_id === head ? "ledger head" : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** The head of the ledger and what withdrawing it would restore. */
function ActiveVersion({
  lineage,
  rows,
}: {
  lineage: RouterLineage;
  rows: readonly RouterModelVersion[];
}) {
  const head = lineage.active_version_id;
  const claiming = rows.filter((row) => row.status === "ACTIVE").length;
  return (
    <section aria-label="Active router version" tabIndex={0} className="registry-card">
      <h2>Active router version</h2>
      <dl className="registry-list">
        <dt>Ledger head</dt>
        <dd aria-label="Ledger head">{head ?? "this family has never been activated"}</dd>
        <dt>Rollback target</dt>
        <dd aria-label="Rollback target">
          {lineage.rollback_target_version_id ?? "no target recorded"}
        </dd>
        <dt>Family</dt>
        <dd aria-label="Family">{lineage.family_key} · {lineage.scope}</dd>
        <dt>Status rows claiming ACTIVE</dt>
        <dd aria-label="Status rows claiming ACTIVE">
          {claiming} of {rows.length}; the head above is read from the activation ledger and
          not from that column.
        </dd>
      </dl>
    </section>
  );
}

/** The parent chain, newest first, as the lineage route walks it. */
function LineageChain({ chain }: { chain: readonly RouterLineageEntry[] }) {
  return (
    <section aria-label="Lineage" tabIndex={0} className="registry-card">
      <h2>Lineage</h2>
      <table className="benchmark-table">
        <thead>
          <tr>
            <th>Version</th><th>Status</th><th>Algorithm</th>
            <th>Training snapshot</th><th>Artifact digest</th><th>Created</th>
          </tr>
        </thead>
        <tbody>
          {chain.map((entry) => (
            <tr key={entry.version_id} aria-label={`lineage ${entry.version_id}`}>
              <td>{entry.version_id}</td>
              <td>{entry.status}</td>
              <td>{entry.algorithm_id}</td>
              <td>{entry.training_snapshot_id}</td>
              <td>{entry.artifact_digest.slice(0, 12)}…</td>
              <td>{entry.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {chain.length === 0 ? (
        <p className="empty">This version records no ancestor.</p>
      ) : null}
    </section>
  );
}

/**
 * Every activation of the family in ledger order, not only those naming this version.
 *
 * "What happened to this router" includes the promotion that displaced it, and a history
 * filtered to entries mentioning one version would omit exactly that.
 */
function ActivationLedger({ activations }: { activations: readonly RouterActivation[] }) {
  return (
    <section aria-label="Activation history" tabIndex={0} className="registry-card">
      <h2>Activation history</h2>
      <table className="benchmark-table">
        <thead>
          <tr>
            <th>Sequence</th><th>Act</th><th>Version</th><th>Displaced</th>
            <th>Rollback target</th><th>Report</th><th>Approved by</th><th>Cause</th>
          </tr>
        </thead>
        <tbody>
          {activations.map((activation) => (
            <tr key={activation.contract_id} aria-label={`activation ${activation.sequence}`}>
              <td>{activation.sequence}</td>
              <td><StatePill state={activation.kind} /></td>
              <td>{activation.router_version_id}</td>
              <td>{activation.previous_version_id ?? "nothing"}</td>
              <td>{activation.rollback_target_version_id ?? "none"}</td>
              <td>{activation.promotion_report_id ?? "none cited"}</td>
              <td>{activation.approved_by.display_name ?? activation.approved_by.principal_id}</td>
              <td>{activation.cause ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {activations.length === 0 ? (
        <p className="empty">This family has never been activated.</p>
      ) : null}
    </section>
  );
}

/**
 * The sealed verdict: the primary metric, the three non-regressions it had to clear, the
 * cohorts it was measured over, and every regression the gate found.
 *
 * A REQUIRE_REVIEW or REJECT report renders in full rather than as a refusal. The
 * measurements are why the gate said no, and they are the reason an operator opens a report
 * that authorises nothing.
 */
function PromotionReport({ report }: { report: RouterPromotionReport }) {
  const criticals = report.critical_regressions ?? [];
  const tradeoffs = report.noncritical_tradeoffs ?? [];
  const cohorts = report.cohort_results ?? [];
  return (
    <div className="router-evidence">
      <div>
        <h3>{report.contract_id}</h3>
        <dl className="registry-list">
          <dt>Decision</dt>
          <dd aria-label="Decision"><StatePill state={report.decision} /></dd>
          <dt>Candidate and baseline</dt>
          <dd aria-label="Candidate and baseline">
            {report.candidate_version} over {report.baseline_version}
          </dd>
          <dt>Training snapshot</dt>
          <dd aria-label="Training snapshot">{report.training_snapshot_id}</dd>
          <dt>Holdout definition</dt>
          <dd aria-label="Holdout definition">{report.holdout_definition_id}</dd>
          <dt>Rollback target</dt>
          <dd aria-label="Report rollback target">{report.rollback_target}</dd>
          <dt>Primary metric</dt>
          <dd aria-label="Primary metric">{metricLine(report.primary_metric_result)}</dd>
          <dt>Verified-success non-regression</dt>
          <dd aria-label="Verified-success non-regression">
            {metricLine(report.verified_success_non_regression)}
          </dd>
          <dt>False-acceptance non-regression</dt>
          <dd aria-label="False-acceptance non-regression">
            {metricLine(report.false_acceptance_non_regression)}
          </dd>
          <dt>Calibration non-regression</dt>
          <dd aria-label="Calibration non-regression">
            {metricLine(report.calibration_result)}
          </dd>
        </dl>
        <h4>Cohort results</h4>
        <ul aria-label="Cohort results">
          {cohorts.map((cohort) => (
            <li key={cohort.cohort_id}>{cohortLine(cohort)}</li>
          ))}
          {cohorts.length === 0 ? <li>No cohort was measured separately.</li> : null}
        </ul>
        <h4>Critical regressions</h4>
        <ul aria-label="Critical regressions">
          {criticals.map((finding) => (
            <li key={finding.finding_id}>{findingLine(finding)}</li>
          ))}
          {criticals.length === 0 ? <li>None; no critical gate regressed.</li> : null}
        </ul>
        <h4>Disclosed tradeoffs</h4>
        <ul aria-label="Disclosed tradeoffs">
          {tradeoffs.map((finding) => (
            <li key={finding.finding_id}>{findingLine(finding)}</li>
          ))}
          {tradeoffs.length === 0 ? <li>None disclosed.</li> : null}
        </ul>
      </div>
    </div>
  );
}
