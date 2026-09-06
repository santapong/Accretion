import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "./api";
import { routingIndex } from "./routingIndex";
import { StatePill } from "./StatePill";
import type {
  ConfigurationCandidate,
  DistributionEstimate,
  GraphProjection,
  RoutingDecisionReceipt,
  Run,
  RunAudit,
} from "./types";

/**
 * The SDD §17.1 node routing panel: why one node ran the configuration it ran.
 *
 * ## Where its data comes from
 *
 * The receipt ids are read out of the audit the run page ALREADY fetches for its capability
 * badges (`routingIndex.ts`), so this panel adds no request to a run that was never routed —
 * and routing is opt-in, so most runs are not. Only once an operator selects a receipt does
 * it ask the two M2 read routes for that receipt and its candidate slate. There is no
 * "receipts of a run" endpoint and this panel is the argument that none is needed.
 *
 * ## Why it owns a node picker
 *
 * The React Flow canvas on this page is deliberately passive: `nodesFocusable={false}` and
 * `elementsSelectable={false}`, because a projection of persisted state must not become a
 * control surface. So the panel carries its own `<select>` over the projection's nodes
 * rather than reading a canvas selection that does not exist, and nothing here changes how
 * the canvas behaves.
 *
 * ## What an operator may change
 *
 * Two things, both attributed and both compare-and-set. An override may name only a
 * `hard_eligible` candidate — the service re-checks that and answers `CANDIDATE_NOT_ELIGIBLE`
 * otherwise, so offering an ineligible one would be offering a button that cannot work — and
 * it carries the `expected_receipt_version` read from the receipt's own `decision_version`
 * label, which is what makes a second operator's concurrent amendment a conflict rather than
 * a silent overwrite. A decision that selected nothing (`HUMAN_REVIEW_REQUIRED`) can be
 * cancelled instead; it has no selection to replace.
 */
export function RoutingPanel({
  run,
  audit,
  projection,
}: {
  run: Run;
  audit: RunAudit | undefined;
  projection: GraphProjection | undefined;
}) {
  const queryClient = useQueryClient();
  const index = useMemo(() => routingIndex(audit), [audit]);
  const [selectedNodeId, setSelectedNodeId] = useState<string>();
  const [candidateId, setCandidateId] = useState("");
  const [reasonCode, setReasonCode] = useState<string>(OVERRIDE_REASON_CODES[0]);
  const [reason, setReason] = useState("");
  const [feedback, setFeedback] = useState<string>();

  const groups = routingGroups(index, projection);
  const activeGroup = groups.find((group) => group.key === selectedNodeId) ?? groups[0];
  const receiptId = activeGroup?.receiptIds[0];

  const receiptQuery = useQuery({
    queryKey: ["routing-decision", receiptId],
    queryFn: () => api.routingDecision(receiptId!),
    enabled: Boolean(receiptId),
    retry: false,
  });
  const candidatesQuery = useQuery({
    queryKey: ["routing-candidates", receiptId],
    queryFn: () => api.routingCandidates(receiptId!),
    enabled: Boolean(receiptId),
    retry: false,
  });

  const receipt = receiptQuery.data;
  const candidates = Array.isArray(candidatesQuery.data) ? candidatesQuery.data : [];
  const selectedCandidate = candidates.find(
    (candidate) => candidate.configuration.contract_id === receipt?.selected_configuration_id,
  );
  // §9.1's slate minus the two things that make a candidate un-offerable: one that failed a
  // hard gate cannot be selected at all, and one another candidate dominates on every
  // objective is not an alternative, it is a worse copy of one already listed.
  const alternatives = candidates.filter(
    (candidate) =>
      candidate.hard_eligible &&
      !candidate.pareto_dominated &&
      candidate.contract_id !== selectedCandidate?.contract_id,
  );
  const eligible = candidates.filter((candidate) => candidate.hard_eligible);
  const awaitingReview = receipt?.decision_type === "HUMAN_REVIEW_REQUIRED";
  const expectedVersion = receiptVersion(receipt);

  async function applyOverride() {
    if (!receiptId || !candidateId || !reason.trim()) return;
    setFeedback("Recording an attributed override against the frozen receipt…");
    try {
      const amended = await api.overrideRoutingDecision(receiptId, {
        candidate_id: candidateId,
        reason_code: reasonCode,
        reason: reason.trim(),
        expected_receipt_version: expectedVersion,
      });
      setFeedback(`Override recorded as receipt ${amended.contract_id}.`);
      setReason("");
      await invalidate();
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "The override was refused.");
    }
  }

  async function cancelDecision() {
    if (!receiptId) return;
    setFeedback("Cancelling the routing decision before dispatch…");
    try {
      const amended = await api.cancelRoutingDecision(receiptId);
      setFeedback(`Decision cancelled as receipt ${amended.contract_id}.`);
      await invalidate();
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "The cancellation was refused.");
    }
  }

  async function invalidate() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["routing-decision", receiptId] }),
      queryClient.invalidateQueries({ queryKey: ["routing-candidates", receiptId] }),
      queryClient.invalidateQueries({ queryKey: ["run-audit-badges", run.run_id] }),
    ]);
  }

  return (
    <section className="dynamic-inspector" aria-labelledby="node-routing-heading">
      <header>
        <div>
          <p className="eyebrow">§17.1 node routing</p>
          <h3 id="node-routing-heading">Node routing</h3>
        </div>
        {receipt ? <StatePill state={receipt.decision_type} /> : null}
      </header>

      {groups.length ? (
        <>
          <div className="replan-control">
            <label htmlFor="routing-node-picker">
              Routed node
              <select
                id="routing-node-picker"
                value={activeGroup?.key ?? ""}
                onChange={(event) => setSelectedNodeId(event.target.value)}
              >
                {groups.map((group) => (
                  <option key={group.key} value={group.key}>
                    {group.label}
                  </option>
                ))}
              </select>
            </label>
            <p className="quiet">
              {groups.length} routed node(s); {index.unassigned.length} receipt(s) never
              dispatched.
            </p>
          </div>
          {receiptQuery.isError ? (
            <p className="quiet">The routing receipt for this node could not be read.</p>
          ) : null}
          {receipt ? (
            <ReceiptDetail
              receipt={receipt}
              selected={selectedCandidate}
              alternatives={alternatives}
            />
          ) : null}
          {receipt && !awaitingReview ? (
            <div
              className="router-evidence"
              role="group"
              aria-label="Override the routed configuration"
            >
              <div>
              <h4>Override</h4>
              <p className="quiet">
                Only hard-eligible candidates may be selected. The override is written against
                receipt version {expectedVersion}; a concurrent amendment is refused rather
                than overwritten.
              </p>
              <label htmlFor="routing-override-candidate">
                Replacement candidate
                <select
                  id="routing-override-candidate"
                  value={candidateId}
                  onChange={(event) => setCandidateId(event.target.value)}
                >
                  <option value="">Select an eligible candidate</option>
                  {eligible.map((candidate) => (
                    <option key={candidate.contract_id} value={candidate.contract_id}>
                      {candidate.configuration.runtime.runtime_id} ·{" "}
                      {candidate.configuration.model.model_id} · {candidate.contract_id}
                    </option>
                  ))}
                </select>
              </label>
              <label htmlFor="routing-override-reason-code">
                Reason code
                <select
                  id="routing-override-reason-code"
                  value={reasonCode}
                  onChange={(event) => setReasonCode(event.target.value)}
                >
                  {OVERRIDE_REASON_CODES.map((code) => (
                    <option key={code} value={code}>
                      {code.replaceAll("_", " ")}
                    </option>
                  ))}
                </select>
              </label>
              <label htmlFor="routing-override-reason">
                Reason
                <textarea
                  id="routing-override-reason"
                  rows={2}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="Why this configuration replaces the routed one"
                />
              </label>
              <button
                className="secondary-button"
                type="button"
                disabled={!candidateId || !reason.trim()}
                onClick={applyOverride}
              >
                Record override
              </button>
              </div>
            </div>
          ) : null}
          {awaitingReview ? (
            <div
              className="router-evidence"
              role="group"
              aria-label="Human review required"
            >
              <div>
              <h4>Human review required</h4>
              <p className="quiet">
                The router selected no configuration, so there is nothing to override. Cancel
                the decision to release the node before it is dispatched.
              </p>
              <button className="secondary-button" type="button" onClick={cancelDecision}>
                Cancel routing decision
              </button>
              </div>
            </div>
          ) : null}
          {feedback ? (
            <p className="form-status" role="status">
              {feedback}
            </p>
          ) : null}
        </>
      ) : (
        <p className="quiet">
          No routing receipt was recorded for this run. Deterministic node routing is opt-in:
          start the API with ACCRETION_ENABLE_NODE_ROUTING=true and re-run to record one.
        </p>
      )}
    </section>
  );
}

/**
 * The six structured reasons §8.4 lets an operator give, in the order the panel offers them.
 *
 * `OTHER` is last and is not a free-text escape hatch: the reason prose is required beside
 * whichever code is chosen, so the code stays groupable while the prose stays specific.
 */
const OVERRIDE_REASON_CODES = [
  "OPERATOR_PREFERENCE",
  "COST",
  "LATENCY",
  "RISK",
  "VERIFIER_MISMATCH",
  "OTHER",
] as const;

/** One entry of the node picker: a graph node, or a receipt no runtime call ever claimed. */
interface RoutingGroup {
  readonly key: string;
  readonly label: string;
  readonly receiptIds: readonly string[];
}

/**
 * The picker's options: every routed node first, then every undispatched receipt.
 *
 * A receipt with no runtime call is listed under its own id rather than dropped. Those are
 * the decisions an operator is most likely to be looking for — a `HUMAN_REVIEW_REQUIRED`
 * waiting for a person is by definition one that never dispatched — and hiding them would
 * make the panel useful only for decisions that already worked.
 */
function routingGroups(
  index: ReturnType<typeof routingIndex>,
  projection: GraphProjection | undefined,
): RoutingGroup[] {
  const labels = new Map(
    (projection?.nodes ?? []).map((node) => [node.node_id, node.label ?? node.node_id]),
  );
  const groups: RoutingGroup[] = [];
  for (const [nodeId, receiptIds] of index.byNode) {
    groups.push({ key: nodeId, label: labels.get(nodeId) ?? nodeId, receiptIds });
  }
  for (const receiptId of index.unassigned) {
    groups.push({
      key: receiptId,
      label: `${receiptId} (never dispatched)`,
      receiptIds: [receiptId],
    });
  }
  return groups;
}

/**
 * The receipt's version, from the `decision_version` label the routing service maintains.
 *
 * `routing/service.py` writes that label on every receipt it persists and increments it on
 * every amendment, and `_amend` compares `expected_receipt_version` against it. There is no
 * other field carrying it, so a panel that sent a literal `1` would send a stale version on
 * every already-amended decision — which reads as a conflict rather than as a bug, and is
 * therefore worth deriving rather than assuming.
 */
function receiptVersion(receipt: RoutingDecisionReceipt | undefined): number {
  const label = receipt?.labels?.decision_version;
  const version = label === undefined ? Number.NaN : Number.parseInt(label, 10);
  return Number.isFinite(version) && version >= 1 ? version : 1;
}

/** The frozen record itself: selection, calibration, alternatives, rejections and pins. */
function ReceiptDetail({
  receipt,
  selected,
  alternatives,
}: {
  receipt: RoutingDecisionReceipt;
  selected: ConfigurationCandidate | undefined;
  alternatives: readonly ConfigurationCandidate[];
}) {
  const outcomes = receipt.predicted_outcomes;
  const rejections = receipt.rejected_candidate_reasons ?? [];
  const experiences = receipt.experience_refs ?? [];
  return (
    <article className="proposal-inspector">
      <div className="dynamic-metrics">
        <span>
          <strong>{receipt.decision_type.replaceAll("_", " ")}</strong> decision
        </span>
        <span>
          <strong>{receipt.uncertainty.lower_confidence_success.toFixed(2)}</strong> lower
          confidence success
        </span>
        <span>
          <strong>{receipt.uncertainty.epistemic_uncertainty.toFixed(2)}</strong> epistemic
          uncertainty
        </span>
        <span>
          <strong>{receipt.uncertainty.calibration_version}</strong> calibration
        </span>
      </div>

      <div className="router-evidence">
        <div>
          <h4 id="routing-selected-heading">Selected configuration</h4>
          {selected ? (
            <dl className="router-features" aria-labelledby="routing-selected-heading">
              <div>
                <dt>runtime</dt>
                <dd>
                  {selected.configuration.runtime.runtime_id} ·{" "}
                  {selected.configuration.runtime.adapter_version}
                </dd>
              </div>
              <div>
                <dt>model</dt>
                <dd>
                  {selected.configuration.model.provider} ·{" "}
                  {selected.configuration.model.model_id}
                </dd>
              </div>
              <div>
                <dt>configuration</dt>
                <dd>{selected.configuration.contract_id}</dd>
              </div>
              <div>
                <dt>frozen verifier</dt>
                <dd>
                  {selected.configuration.verifier.verifier.verifier_contract_id} v
                  {selected.configuration.verifier.version}
                </dd>
              </div>
              <div>
                <dt>verification spec hash</dt>
                <dd>{selected.configuration.verifier.verification_spec_hash}</dd>
              </div>
            </dl>
          ) : (
            <p className="quiet">
              {receipt.selected_configuration_id
                ? `The slate no longer carries ${receipt.selected_configuration_id}.`
                : "This decision selected no configuration."}
            </p>
          )}
        </div>
        <div>
          <h4 id="routing-outcomes-heading">Predicted outcomes</h4>
          {outcomes ? (
            <dl className="router-features" aria-labelledby="routing-outcomes-heading">
              {PREDICTED_KEYS.map((objective) => (
                <div key={objective}>
                  <dt>{objective.replaceAll("_", " ")}</dt>
                  <dd>{formatInterval(outcomes[objective])}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="quiet">The router recorded no predicted outcomes.</p>
          )}
        </div>
        <div>
          <h4 id="routing-alternatives-heading">Alternatives considered</h4>
          {alternatives.length ? (
            <ul className="router-fallback" aria-labelledby="routing-alternatives-heading">
              {alternatives.map((candidate) => (
                <li key={candidate.contract_id}>
                  <span>{candidate.configuration.runtime.runtime_id}</span>
                  <strong>{candidate.configuration.model.model_id}</strong>
                  <small>{candidate.lower_confidence_success.toFixed(2)} LCB</small>
                </li>
              ))}
            </ul>
          ) : (
            <p className="quiet">No other candidate cleared every hard gate.</p>
          )}
        </div>
        <div>
          <h4 id="routing-rejected-heading">Rejected candidates</h4>
          {rejections.length ? (
            <ul className="router-fallback" aria-labelledby="routing-rejected-heading">
              {rejections.map((rejection) => (
                <li key={`${rejection.candidate_id}-${rejection.reason_code}`}>
                  <span>{rejection.reason_code}</span>
                  <strong>{rejection.stage.replaceAll("_", " ")}</strong>
                </li>
              ))}
            </ul>
          ) : (
            <p className="quiet">No candidate was rejected by a gate.</p>
          )}
        </div>
        <div>
          <h4 id="routing-experience-heading">Experience evidence</h4>
          {experiences.length ? (
            <ul className="proposal-capabilities" aria-labelledby="routing-experience-heading">
              {experiences.map((reference) => (
                <li key={reference}>
                  <code>{reference}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p className="quiet">The decision cited no prior experience.</p>
          )}
        </div>
      </div>

      <div className="router-evidence">
        <div>
          <h4 id="routing-pins-heading">Version pins</h4>
          <dl className="graph-diff-identities" aria-labelledby="routing-pins-heading">
            {versionPins(receipt).map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>
                  <code>{value}</code>
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </article>
  );
}

/** The five §7.6 objectives, in the order the panel reports them. */
const PREDICTED_KEYS = [
  "node_verified_success",
  "run_verified_success",
  "quality",
  "cost",
  "latency",
] as const;

/**
 * One predicted interval, labelled with the method that produced it.
 *
 * The method is rendered and not dropped because OQ-405 is undecided: a `bootstrap-1000`
 * interval and a `conformal-v1` interval are not the same claim, and a reader comparing two
 * receipts has no way to tell them apart from the numbers alone.
 */
function formatInterval(estimate: DistributionEstimate): string {
  return (
    `${estimate.mean} [${estimate.lower_bound}, ${estimate.upper_bound}] ` +
    `@ ${estimate.confidence} · ${estimate.method}`
  );
}

/**
 * The five §8.3 pins that make a decision reproducible, as label/value pairs.
 *
 * All five, always, including the ones that can be null: an adapter version the receipt does
 * not carry is a fact about the decision (§8.3 lets a degraded attribution drop it) and
 * rendering the row as `none` says so, where omitting the row would read as "not pinned".
 */
function versionPins(receipt: RoutingDecisionReceipt): readonly (readonly [string, string])[] {
  return [
    ["workspace router version", receipt.workspace_router_version],
    ["project adapter version", receipt.project_adapter_version ?? "none"],
    ["capability registry snapshot", receipt.capability_registry_snapshot_id],
    ["policy snapshot", receipt.policy_snapshot_id],
    ["objective contract version", String(receipt.objective_contract_version)],
  ];
}
