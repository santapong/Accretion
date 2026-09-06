import { expect, test } from "vitest";
import auditEvents from "./__fixtures__/routing/audit-events.json";
import { routingIndex } from "./routingIndex";
import type { RunAudit } from "./types";

/**
 * The mapping the §17.1 panel is built on, tested where it is pure.
 *
 * `RoutingPanel.test.tsx` renders the result; these cases are where the projection is shown
 * a log it must read exactly. Each is a shape the real audit produces: several events of one
 * runtime call carrying the same stamp, a decision recorded before the call that dispatched
 * it, and a decision that was never dispatched at all.
 */
const events = auditEvents as unknown as NonNullable<RunAudit["events"]>;

const RECEIPT = "rcp_ZV1DZZBTPY9GD9V6NVWWXX40ZS";
const REVIEW = "rcp_RXG4QHXAH6CV4SA907AQV988VW";

test("a runtime call's routing stamp maps its node to the receipt that produced it", () => {
  const index = routingIndex({ events });
  expect([...index.byNode.keys()]).toEqual(["n:act"]);
  expect(index.byNode.get("n:act")).toEqual([RECEIPT]);
});

test("a receipt stamped on several events of the same call is kept once", () => {
  // The run manager stamps EVERY event of a routed call, not only the first, so a naive
  // push produces one entry per event and the panel would offer the same receipt four times.
  const index = routingIndex({ events });
  expect(index.byNode.get("n:act")).toHaveLength(1);
});

test("a receipt no runtime call claimed is unassigned, and a dispatched one is not", () => {
  const index = routingIndex({ events });
  expect(index.unassigned).toEqual([REVIEW]);
});

test("a decision event is found through causation_id when its payload carries no receipt_id", () => {
  // `_receipt_event` derives the payload key and `causation_id` from the same
  // `receipt.contract_id`. Reading only the payload would lose a decision whose payload a
  // retention policy trimmed, and losing it means the panel cannot offer its cancel control.
  const trimmed = events.map((event) =>
    event.event_id === "evt_review_created" ? { ...event, payload: { action: "created" } } : event,
  );
  expect(routingIndex({ events: trimmed }).unassigned).toEqual([REVIEW]);
});

test("receipts keep the order the audit recorded them", () => {
  const second = "rcp_2AAAAAAAAAAAAAAAAAAAAAAAAA";
  const later = [
    ...events,
    { ...events[0], event_id: "evt_second_created", causation_id: second, payload: { receipt_id: second } },
  ];
  expect(routingIndex({ events: later }).unassigned).toEqual([REVIEW, second]);
});

test("an audit with no events and an absent audit both index nothing", () => {
  for (const audit of [undefined, { events: [] }]) {
    const index = routingIndex(audit);
    expect(index.byNode.size).toBe(0);
    expect(index.unassigned).toEqual([]);
  }
});

test("a runtime call with no node id or no routing stamp contributes nothing", () => {
  const unusable = [
    { ...events[2], event_id: "evt_no_node", node_id: null },
    { ...events[2], event_id: "evt_no_stamp", payload: { runtime_call_id: "rtc_x" } },
  ];
  const index = routingIndex({ events: unusable });
  expect(index.byNode.size).toBe(0);
});
