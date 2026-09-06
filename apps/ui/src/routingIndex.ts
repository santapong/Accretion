import type { RunAudit } from "./types";

/**
 * Which §17.1 routing receipts belong to which node of the execution graph.
 *
 * There is no route that lists the routing receipts of a run, and this module is the
 * argument that none is needed: the run page already fetches the audit for its capability
 * badges, and the audit already carries both halves of the mapping.
 *
 * - `RUNTIME_CALL_STARTED` is stamped with `payload.routing_receipt_id` by
 *   `services/run_manager.py` whenever a routed configuration produced the call, and the
 *   run manager also stamps the event's `node_id` with the same projection node id the
 *   graph endpoint reports. One event therefore names a node AND a receipt.
 * - `ROUTING_DECISION_CREATED` is appended by `routing/service.py` with
 *   `payload.receipt_id` and `causation_id = receipt.contract_id`. A decision that was
 *   never dispatched — a `HUMAN_REVIEW_REQUIRED` waiting for an operator, or one cancelled
 *   before dispatch — has no runtime call and therefore no node, which is exactly the
 *   receipt an operator most needs to find.
 *
 * Like `runBadges.ts`, this module deliberately imports no API client. It is a projection
 * of persisted events and cannot request, override or cancel anything; the panel that owns
 * those controls asks the M2 routes for the receipt itself.
 */
export interface RoutingIndex {
  /** Projection node id → the receipts that node's runtime calls carried, in audit order. */
  readonly byNode: ReadonlyMap<string, readonly string[]>;
  /** Receipts the audit recorded but no runtime call ever claimed, in audit order. */
  readonly unassigned: readonly string[];
}

/** A non-empty trimmed string, or `undefined`. Payload values arrive as `unknown`. */
function identity(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed.length ? trimmed : undefined;
}

/**
 * Index the audit's routing receipts by the graph node that executed them.
 *
 * Order is the order the store recorded the events, per node and in `unassigned` alike, so
 * a re-render with a newer audit shows the newer decision last rather than a cached copy.
 * A receipt named by several runtime calls on the same node — every event of one call
 * carries the stamp, not just the first — is kept once.
 *
 * A receipt that a runtime call claims is never also `unassigned`, whichever event the
 * audit recorded first: assignment is resolved after the whole log has been read, so a
 * `ROUTING_DECISION_CREATED` that precedes its own dispatch (it always does) cannot leave
 * a duplicate behind in the unassigned list.
 */
export function routingIndex(audit: Pick<RunAudit, "events"> | undefined): RoutingIndex {
  const byNode = new Map<string, string[]>();
  const claimed = new Set<string>();
  const declared: string[] = [];

  for (const event of audit?.events ?? []) {
    if (event.normalized_type === "RUNTIME_CALL_STARTED") {
      const nodeId = identity(event.node_id);
      const receiptId = identity(event.payload?.routing_receipt_id);
      if (!nodeId || !receiptId) continue;
      claimed.add(receiptId);
      const existing = byNode.get(nodeId);
      if (!existing) byNode.set(nodeId, [receiptId]);
      else if (!existing.includes(receiptId)) existing.push(receiptId);
      continue;
    }
    if (event.normalized_type !== "ROUTING_DECISION_CREATED") continue;
    // `causation_id` is the fallback and not the primary read, because it is the service's
    // own key rather than a documented payload field. It matters anyway: `_receipt_event`
    // derives both from the same `receipt.contract_id`, so an event whose payload was
    // trimmed by a retention policy can still be resolved to its receipt.
    const receiptId = identity(event.payload?.receipt_id) ?? identity(event.causation_id);
    if (!receiptId || declared.includes(receiptId)) continue;
    declared.push(receiptId);
  }

  return { byNode, unassigned: declared.filter((receiptId) => !claimed.has(receiptId)) };
}
