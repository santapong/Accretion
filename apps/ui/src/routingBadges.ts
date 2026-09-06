import type {
  ConfigurationCandidate,
  GraphProjectionNode,
  RoutingDecisionReceipt,
  ShadowPair,
} from "./types";

/**
 * What the router did with one node, in six words an operator can read off the canvas.
 *
 * The first four are properties of the DECISION and come from `decision_type` alone:
 * `explore` and `fallback` are §9.5's exploration arm and §9.2's degraded arm, `overridden`
 * is §8.4's attributed human amendment, and `human_review` is the decision that selected
 * nothing and is waiting for a person. `routed` is the ordinary exploit.
 *
 * `shadowed` is not one of those. It is a property of what happened BESIDE the decision — a
 * §17.2 shadow stage scored it — so it replaces `routed` and never displaces one of the
 * four, because "this node explored" and "a shadow policy was scored against this node" are
 * different facts and the first one is the one that explains what ran.
 */
export type RoutingDecisionKind =
  | "routed"
  | "shadowed"
  | "overridden"
  | "explore"
  | "fallback"
  | "human_review";

/**
 * The §17.1 routing provenance of one graph node, as a projection of records already read.
 *
 * Every field is derived from a receipt, a candidate slate, a shadow pair or the graph
 * projection — the documents the panels on the run page already hold — and this module
 * imports no API client, exactly as `runBadges.ts` and `routingIndex.ts` do not. A badge
 * cannot re-route, override or cancel anything; it is what the audit already says, rendered
 * where the node is.
 *
 * The optional fields are optional because their source is optional. A slate that has not
 * been read yet has no runtime, model or tool count to report, and a badge that invented a
 * placeholder for them would be asserting something no record says. Absent is rendered as
 * absent.
 */
export interface RoutingBadge {
  /** The projection node this decision routed. */
  readonly nodeId: string;
  /** The receipt the node's newest runtime call carried. */
  readonly receiptId: string;
  readonly decision: RoutingDecisionKind;
  /** `runtime_id` of the selected configuration, when the slate has been read. */
  readonly runtime?: string;
  /** `model_id` of the selected configuration, when the slate has been read. */
  readonly model?: string;
  /** How many tools the selected configuration binds, when the slate has been read. */
  readonly toolCount?: number;
  /** The node's own verifier verdict, from the graph projection. */
  readonly verification?: string;
  /** The receipt's `decision_version`: 1 until an override or a cancellation amends it. */
  readonly revisionCount: number;
  /** The mean predicted cost, as the receipt recorded it. */
  readonly cost?: string;
  /** The mean predicted latency, as the receipt recorded it. */
  readonly latency?: string;
}

/** Projection node id → the badge for the decision that node ran under. */
export type RoutingBadgeIndex = ReadonlyMap<string, RoutingBadge>;

/**
 * Everything the badges are derived from, and nothing else.
 *
 * `byNode` is `routingIndex.ts`'s half of the audit — which receipts a node's runtime calls
 * carried, in audit order. `receipts` and `candidates` are whichever documents the §17.1
 * panel has already read; `shadowPairs` are whichever the §17.2 comparison has. `nodes` is
 * the graph projection the canvas is drawn from, and is where the verification state comes
 * from: `VerificationResult.target_ref` names an artifact, an iteration or the run
 * (`services/run_manager.py:3601`) and never a projection node, so a per-node verdict cannot
 * be joined from the verification list and is read from the node the graph endpoint reports.
 */
export interface RoutingBadgeSources {
  readonly byNode: ReadonlyMap<string, readonly string[]>;
  readonly receipts: readonly RoutingDecisionReceipt[];
  readonly candidates: readonly ConfigurationCandidate[];
  readonly shadowPairs: readonly ShadowPair[];
  readonly nodes: readonly GraphProjectionNode[];
}

/** The five §8.1 decision types, mapped onto the words the badge shows. */
const DECISION_KINDS: Readonly<Record<string, RoutingDecisionKind>> = {
  EXPLOIT: "routed",
  EXPLORE: "explore",
  FALLBACK: "fallback",
  HUMAN_OVERRIDE: "overridden",
  HUMAN_REVIEW_REQUIRED: "human_review",
};

/** A non-empty trimmed string, or `undefined`. */
function identity(value: string | null | undefined): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed.length ? trimmed : undefined;
}

/**
 * A predicted mean, rendered as the receipt wrote it.
 *
 * No unit is appended and no scaling is applied: `DistributionEstimate` declares none, and a
 * badge that decided the latency estimate was milliseconds would be reading a unit into a
 * contract that does not state one. Trailing zeros are dropped so that `41000.0` reads as
 * `41000` rather than implying a precision the estimate does not claim.
 */
function measure(value: number | undefined): string | undefined {
  if (value === undefined || !Number.isFinite(value)) return undefined;
  return String(Number(value.toFixed(3)));
}

/**
 * The receipt's own revision, from the `decision_version` label the routing service keeps.
 *
 * `routing/service.py` writes it on every receipt and increments it on every amendment, so
 * `revision 2` on a badge is the visible half of "somebody overrode this decision". A
 * receipt written before the label existed, or one whose label a retention policy trimmed,
 * reads as 1 — the same fallback `RoutingPanel.tsx` uses for the compare-and-set version, so
 * the two cannot disagree about what version an operator is looking at.
 */
function revisionOf(receipt: RoutingDecisionReceipt): number {
  const parsed = Number.parseInt(receipt.labels?.decision_version ?? "", 10);
  return Number.isFinite(parsed) && parsed >= 1 ? parsed : 1;
}

/**
 * The receipts a §17.2 shadow stage scored, read from the pairs the comparison holds.
 *
 * Both halves of a pair count. `executed_receipt_id` is the decision that ran and
 * `shadow_receipt_id` is the recommendation it was compared with; a run that dispatched the
 * shadow fork's own receipt would otherwise show it as an ordinary `routed` node while the
 * panel below said it was a shadow.
 */
function shadowedReceiptIds(pairs: readonly ShadowPair[]): ReadonlySet<string> {
  const shadowed = new Set<string>();
  for (const pair of pairs) {
    const executed = identity(pair.executed_receipt_id);
    const shadow = identity(pair.shadow_receipt_id);
    if (executed) shadowed.add(executed);
    if (shadow) shadowed.add(shadow);
  }
  return shadowed;
}

/**
 * The badge for every routed node whose receipt is in hand, keyed by projection node id.
 *
 * One badge per node and not one per receipt: a node that was re-routed after a failure
 * carries several receipts, and the one that explains what ran is the LAST — the decision
 * the newest runtime call of that node was dispatched under. The earlier ones are still on
 * the page, in the §17.1 panel's picker, which is where a history belongs.
 *
 * A node whose receipt no panel has read yet gets no badge at all rather than a placeholder
 * one. That is what keeps this module honest about the run page's design (ADR4-M9-002): the
 * receipts arrive because an operator opened one, not because the canvas asked for them.
 */
export function routingBadges(sources: RoutingBadgeSources): RoutingBadgeIndex {
  const receipts = new Map(
    sources.receipts.map((receipt) => [receipt.contract_id, receipt] as const),
  );
  const configurations = new Map(
    sources.candidates.map(
      (candidate) => [candidate.configuration.contract_id, candidate] as const,
    ),
  );
  const verifierStates = new Map(
    sources.nodes.map((node) => [node.node_id, node.verifier_state ?? undefined] as const),
  );
  const shadowed = shadowedReceiptIds(sources.shadowPairs);

  const badges = new Map<string, RoutingBadge>();
  for (const [nodeId, receiptIds] of sources.byNode) {
    const receiptId = receiptIds[receiptIds.length - 1];
    const receipt = receiptId ? receipts.get(receiptId) : undefined;
    if (!receipt) continue;
    const kind = DECISION_KINDS[receipt.decision_type];
    const decision: RoutingDecisionKind =
      kind === "routed" && shadowed.has(receipt.contract_id) ? "shadowed" : (kind ?? "routed");
    const selected = receipt.selected_configuration_id
      ? configurations.get(receipt.selected_configuration_id)
      : undefined;
    const outcomes = receipt.predicted_outcomes;
    badges.set(nodeId, {
      nodeId,
      receiptId: receipt.contract_id,
      decision,
      ...(selected ? { runtime: selected.configuration.runtime.runtime_id } : {}),
      ...(selected ? { model: selected.configuration.model.model_id } : {}),
      ...(selected ? { toolCount: selected.configuration.tools?.length ?? 0 } : {}),
      ...(verifierStates.get(nodeId) ? { verification: verifierStates.get(nodeId) } : {}),
      revisionCount: revisionOf(receipt),
      ...(measure(outcomes?.cost.mean) ? { cost: measure(outcomes?.cost.mean) } : {}),
      ...(measure(outcomes?.latency.mean) ? { latency: measure(outcomes?.latency.mean) } : {}),
    });
  }
  return badges;
}

/**
 * The badge's parts, in render order, skipping every fact the records do not carry.
 *
 * `revision` is shown only once a decision has been amended: every unamended receipt reads
 * `revision 1`, and a part that is identical on every badge is noise rather than provenance.
 */
export function routingBadgeParts(badge: RoutingBadge): readonly (readonly [string, string])[] {
  const parts: (readonly [string, string])[] = [];
  if (badge.runtime) parts.push(["runtime", badge.runtime]);
  if (badge.model) parts.push(["model", badge.model]);
  if (badge.toolCount !== undefined) parts.push(["tools", String(badge.toolCount)]);
  if (badge.verification) parts.push(["verification", badge.verification]);
  if (badge.revisionCount > 1) parts.push(["revision", String(badge.revisionCount)]);
  if (badge.cost) parts.push(["cost", badge.cost]);
  if (badge.latency) parts.push(["latency", badge.latency]);
  return parts;
}
