import { expect, test } from "vitest";
import { judge, WAIVERS, type Waiver } from "./allowlist";

/**
 * The waiver rules, tested here rather than through a browser: they are pure logic, and a
 * gate whose escape hatch is untested is an escape hatch.
 *
 * What matters is that this file cannot become a list of excuses. Rules 2 and 3 - expiry
 * and unused-entry detection - are the ones doing that work, so they carry the most tests.
 */

const TODAY = "2026-09-01";

function waiver(overrides: Partial<Waiver> = {}): Waiver {
  return {
    rule: "color-contrast",
    route: "/history",
    reason: "fixture",
    issue: "https://github.com/santapong/Accretion/issues/1",
    expires: "2026-12-31",
    ...overrides,
  };
}

test("a violation with no waiver fails the gate", () => {
  const verdict = judge([{ route: "/history", rule: "color-contrast" }], TODAY, []);
  expect(verdict.unwaived).toEqual(["/history: color-contrast"]);
});

test("a violation with a live waiver passes", () => {
  const verdict = judge([{ route: "/history", rule: "color-contrast" }], TODAY, [waiver()]);
  expect(verdict).toEqual({ unwaived: [], expired: [], unused: [] });
});

test("a waiver covers only its own rule on its own route", () => {
  // The pairing is the point: a waiver for one route must not silence the same rule
  // elsewhere, which is exactly how a narrow exception becomes a blanket one.
  const entries = [waiver()];
  expect(judge([{ route: "/approvals", rule: "color-contrast" }], TODAY, entries).unwaived).toEqual(
    ["/approvals: color-contrast"],
  );
  expect(judge([{ route: "/history", rule: "heading-order" }], TODAY, entries).unwaived).toEqual([
    "/history: heading-order",
  ]);
});

test("an expired waiver fails even though the violation is still genuinely waived", () => {
  const verdict = judge([{ route: "/history", rule: "color-contrast" }], TODAY, [
    waiver({ expires: "2026-08-31" }),
  ]);
  expect(verdict.unwaived).toEqual([]);
  expect(verdict.expired).toHaveLength(1);
  expect(verdict.expired[0]).toContain("expired 2026-08-31");
});

test("a waiver expires after its stated day, not on it", () => {
  const observed = [{ route: "/history", rule: "color-contrast" }];
  expect(judge(observed, TODAY, [waiver({ expires: TODAY })]).expired).toEqual([]);
  expect(judge(observed, "2026-09-02", [waiver({ expires: TODAY })]).expired).toHaveLength(1);
});

test("a waiver matching nothing fails, so a fixed defect cannot leave its excuse behind", () => {
  const verdict = judge([], TODAY, [waiver()]);
  expect(verdict.unused).toHaveLength(1);
  expect(verdict.unused[0]).toContain("no longer occurs");
});

test("an entry that is both unused and expired is reported once, as unused", () => {
  // Both are true, but deleting it is the action either way, and two messages for one
  // entry reads like two problems.
  const verdict = judge([], TODAY, [waiver({ expires: "2020-01-01" })]);
  expect(verdict.unused).toHaveLength(1);
  expect(verdict.expired).toEqual([]);
});

test("dates are compared as strings, so the verdict does not depend on the runner's timezone", () => {
  // `new Date("2026-09-01") < new Date()` is true from midnight UTC, which would expire a
  // waiver hours early for anyone west of Greenwich and make CI disagree with a laptop.
  const observed = [{ route: "/history", rule: "color-contrast" }];
  const entries = [waiver({ expires: "2026-09-01" })];
  for (const now of ["2026-09-01", "2026-09-01"]) {
    expect(judge(observed, now, entries).expired).toEqual([]);
  }
});

test("every committed waiver carries an issue and a real expiry date", () => {
  // Guards the file itself: an entry without a tracked issue is how a temporary waiver
  // becomes permanent. Vacuously true while the list is empty, and load-bearing the moment
  // it is not.
  for (const entry of WAIVERS) {
    expect(entry.issue, `${entry.route}/${entry.rule} has no issue`).toMatch(/^https?:\/\//);
    expect(entry.expires, `${entry.route}/${entry.rule} has a malformed expiry`).toMatch(
      /^\d{4}-\d{2}-\d{2}$/,
    );
    expect(entry.reason.length, `${entry.route}/${entry.rule} has no reason`).toBeGreaterThan(10);
  }
});

test("the committed allowlist is empty", () => {
  // Not a style rule - it is the claim the gate makes. If this ever has to change, the PR
  // that changes it should have to say so out loud.
  expect(WAIVERS).toEqual([]);
});
