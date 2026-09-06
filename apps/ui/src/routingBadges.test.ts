import { expect, test } from "vitest";
import candidateFixtures from "./__fixtures__/routing/candidates.json";
import receiptFixture from "./__fixtures__/routing/receipt.json";
import { routingBadgeParts, routingBadges } from "./routingBadges";
import badgeSource from "./routingBadges.ts?raw";
import type {
  ConfigurationCandidate,
  GraphProjectionNode,
  RoutingDecisionReceipt,
  ShadowPair,
} from "./types";

/**
 * The §17.1 node badges, over the frozen v0.4 contract fixtures.
 *
 * `receipt.json` is the byte copy of `tests/fixtures/contracts/v0.4/routing_decision_receipt/
 * complete.json` that `RoutingPanel.test.tsx` reads, and `candidates.json` is its slate. Using
 * the same two documents as the panel is the point: the badge and the panel are two renderings
 * of ONE record, and a fixture of the badge's own would let them drift apart while both
 * stayed green.
 */
const receipt = receiptFixture as unknown as RoutingDecisionReceipt;
const candidates = candidateFixtures as unknown as ConfigurationCandidate[];

const nodes: GraphProjectionNode[] = [
  { schema_version: "1.0", node_id: "act", kind: "LOOP", label: "Act", status: "RUNNING", artifact_count: 1, risk: "LOW", verifier_state: "PASS" },
  { schema_version: "1.0", node_id: "verify", kind: "VERIFIER", label: "Verify", status: "WAITING", artifact_count: 0, risk: "LOW" },
];

const byNode = new Map([["act", [receipt.contract_id]]]);

function badgeFor(
  overrides: Partial<RoutingDecisionReceipt> = {},
  extras: {
    readonly candidates?: readonly ConfigurationCandidate[];
    readonly shadowPairs?: readonly ShadowPair[];
    readonly byNode?: ReadonlyMap<string, readonly string[]>;
  } = {},
) {
  return routingBadges({
    byNode: extras.byNode ?? byNode,
    receipts: [{ ...receipt, ...overrides }],
    candidates: extras.candidates ?? candidates,
    shadowPairs: extras.shadowPairs ?? [],
    nodes,
  }).get("act");
}

test("the decision word is the receipt's decision type and nothing else decides it", () => {
  const kinds = {
    EXPLOIT: "routed",
    EXPLORE: "explore",
    FALLBACK: "fallback",
    HUMAN_OVERRIDE: "overridden",
    HUMAN_REVIEW_REQUIRED: "human_review",
  } as const;
  for (const [type, word] of Object.entries(kinds)) {
    expect(badgeFor({ decision_type: type as RoutingDecisionReceipt["decision_type"] })?.decision).toBe(word);
  }
});

test("a shadow pair naming the receipt replaces routed, and never replaces the other four", () => {
  const pair = (executed: string): ShadowPair => ({
    schema_version: "1.0",
    executed_receipt_id: executed,
    shadow_receipt_id: "rcp_SHADOWFORKRECEIPT000000000",
    agreement: true,
    projected_utility_delta: 0.01,
    observed_delta: 0.02,
    control_result_id: null,
    shadow_result_id: null,
  });
  const shadowPairs = [pair(receipt.contract_id)];
  expect(badgeFor({ decision_type: "EXPLOIT" }, { shadowPairs })?.decision).toBe("shadowed");
  expect(badgeFor({ decision_type: "EXPLORE" }, { shadowPairs })?.decision).toBe("explore");
  expect(badgeFor({ decision_type: "FALLBACK" }, { shadowPairs })?.decision).toBe("fallback");

  // Either half of a pair counts: the branched rollout dispatches the shadow fork's own
  // receipt, and a run whose audit recorded THAT one was still shadowed.
  const asShadowHalf: ShadowPair = { ...pair("rcp_OTHERRUNRECEIPT00000000000"), shadow_receipt_id: receipt.contract_id };
  expect(badgeFor({ decision_type: "EXPLOIT" }, { shadowPairs: [asShadowHalf] })?.decision).toBe("shadowed");
  expect(badgeFor({ decision_type: "EXPLOIT" }, { shadowPairs: [pair("rcp_OTHERRUNRECEIPT00000000000")] })?.decision).toBe("routed");
});

test("runtime, model and tool count come from the configuration the receipt selected", () => {
  const selected = candidates.find(
    (candidate) => candidate.configuration.contract_id === receipt.selected_configuration_id,
  );
  expect(selected, "the fixture slate must contain the selected configuration").toBeDefined();
  const badge = badgeFor();
  expect(badge?.runtime).toBe(selected!.configuration.runtime.runtime_id);
  expect(badge?.model).toBe(selected!.configuration.model.model_id);
  expect(badge?.toolCount).toBe(selected!.configuration.tools?.length ?? 0);
});

test("a slate that has not been read yet reports no runtime, model or tool count", () => {
  const badge = badgeFor({}, { candidates: [] });
  expect(badge?.decision).toBe("explore");
  expect(badge?.runtime).toBeUndefined();
  expect(badge?.model).toBeUndefined();
  expect(badge?.toolCount).toBeUndefined();
  expect(routingBadgeParts(badge!).map(([kind]) => kind)).toEqual([
    "verification",
    "cost",
    "latency",
  ]);
});

test("the verification state is the node's own, from the graph projection", () => {
  expect(badgeFor()?.verification).toBe("PASS");
  expect(
    routingBadges({
      byNode: new Map([["verify", [receipt.contract_id]]]),
      receipts: [receipt],
      candidates,
      shadowPairs: [],
      nodes,
    }).get("verify")?.verification,
  ).toBeUndefined();
});

test("the revision is the receipt's decision_version, and is shown only once it moves", () => {
  expect(badgeFor()?.revisionCount).toBe(1);
  expect(routingBadgeParts(badgeFor()!).map(([kind]) => kind)).not.toContain("revision");

  const amended = badgeFor({ labels: { ...receipt.labels, decision_version: "3" } });
  expect(amended?.revisionCount).toBe(3);
  expect(routingBadgeParts(amended!)).toContainEqual(["revision", "3"]);

  // An unreadable or absent label reads as 1 — the same fallback `RoutingPanel.tsx` sends as
  // `expected_receipt_version`, so the badge and the compare-and-set cannot disagree.
  expect(badgeFor({ labels: { decision_version: "not-a-number" } })?.revisionCount).toBe(1);
  expect(badgeFor({ labels: {} })?.revisionCount).toBe(1);
});

test("cost and latency are the receipt's predicted means, with no unit invented", () => {
  const outcomes = receipt.predicted_outcomes;
  expect(outcomes, "the fixture receipt must carry predicted outcomes").toBeDefined();
  const badge = badgeFor();
  expect(badge?.cost).toBe(String(outcomes!.cost.mean));
  expect(badge?.latency).toBe(String(outcomes!.latency.mean));
  expect(badge?.latency).not.toContain("ms");
  expect(badge?.latency).not.toContain("s");

  const unpredicted = badgeFor({ predicted_outcomes: null });
  expect(unpredicted?.cost).toBeUndefined();
  expect(unpredicted?.latency).toBeUndefined();
});

test("the node's newest decision is the one badged, and an unread receipt badges nothing", () => {
  const older = { ...receipt, contract_id: "rcp_OLDERDECISION00000000000000", decision_type: "FALLBACK" as const };
  const both = new Map([["act", [older.contract_id, receipt.contract_id]]]);
  expect(
    routingBadges({ byNode: both, receipts: [older, receipt], candidates, shadowPairs: [], nodes })
      .get("act")?.receiptId,
  ).toBe(receipt.contract_id);

  // The panel has read only the older one: the node keeps no badge rather than showing a
  // decision that is not the one it ran under.
  expect(
    routingBadges({ byNode: both, receipts: [older], candidates, shadowPairs: [], nodes }).size,
  ).toBe(0);
  expect(
    routingBadges({ byNode, receipts: [], candidates, shadowPairs: [], nodes }).size,
  ).toBe(0);
  expect(
    routingBadges({ byNode: new Map(), receipts: [receipt], candidates, shadowPairs: [], nodes }).size,
  ).toBe(0);
});

test("the module imports no API client", () => {
  // The same guarantee `runBadges.ts` and `routingIndex.ts` carry, asserted rather than
  // promised: a badge that could request anything would be a control surface (AC4-M9-043).
  // Read through Vite's `?raw` rather than `node:fs`: `tsconfig.app.json` typechecks `src/`
  // against the browser lib and carries no `@types/node`.
  expect(badgeSource).not.toContain('from "./api"');
  expect(badgeSource).not.toContain("@tanstack/react-query");
  expect(badgeSource).not.toContain("fetch(");
});
