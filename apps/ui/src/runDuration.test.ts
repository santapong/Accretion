import { expect, test } from "vitest";
import { durationLabel, formatDuration, runDuration } from "./runDuration";
import type { Run } from "./types";

const CREATED = "2026-09-01T10:00:00Z";
const NOW = Date.parse("2026-09-01T10:05:00Z");

function run(state: string, updated?: string): Pick<Run, "state" | "created_at" | "updated_at"> {
  return { state, created_at: CREATED, updated_at: updated } as Pick<
    Run,
    "state" | "created_at" | "updated_at"
  >;
}

test("a terminal run is measured to its last write, not to the wall clock", () => {
  const duration = runDuration(run("SUCCEEDED", "2026-09-01T10:00:42Z"), NOW);
  expect(duration).toEqual({ elapsedMs: 42_000, final: true });
});

test("a live run keeps counting against the clock, ignoring a stale updated_at", () => {
  // The distinction that matters: a provider that has gone quiet for four minutes must
  // still show four minutes elapsed, not the 42s of its last event.
  const duration = runDuration(run("RUNNING", "2026-09-01T10:00:42Z"), NOW);
  expect(duration).toEqual({ elapsedMs: 300_000, final: false });
});

test.each(["SUCCEEDED", "FAILED", "CANCELLED"])("%s is final", (state) => {
  expect(runDuration(run(state, "2026-09-01T10:00:10Z"), NOW)!.final).toBe(true);
});

test.each(["PENDING", "RUNNING", "AWAITING_APPROVAL"])("%s is not final", (state) => {
  expect(runDuration(run(state, "2026-09-01T10:00:10Z"), NOW)!.final).toBe(false);
});

test("an unknown duration is undefined rather than zero", () => {
  // Zero would render as "0.0s", which claims an instantaneous run rather than an
  // unmeasurable one. These are different facts and must not share a rendering.
  expect(runDuration(undefined, NOW)).toBeUndefined();
  expect(runDuration({ state: "RUNNING" } as Run, NOW)).toBeUndefined();
  expect(runDuration({ state: "RUNNING", created_at: "not a date" } as Run, NOW)).toBeUndefined();
  expect(durationLabel(undefined, NOW)).toBeUndefined();
});

test("a terminal run with no updated_at falls back to the clock instead of vanishing", () => {
  expect(runDuration(run("SUCCEEDED"), NOW)).toEqual({ elapsedMs: 300_000, final: true });
});

test("clock skew is clamped to zero rather than shown as negative", () => {
  const skewed = runDuration(run("SUCCEEDED", "2026-09-01T09:59:00Z"), NOW);
  expect(skewed).toEqual({ elapsedMs: 0, final: true });
});

test.each([
  [0, "0.0s"],
  [1_500, "1.5s"],
  [9_940, "9.9s"],
  [10_400, "10s"],
  [59_400, "59s"],
  [60_000, "1m 00s"],
  [201_000, "3m 21s"],
  [3_600_000, "1h 00m"],
  // 2h 10.75m: minutes floor rather than round, so an hours-and-minutes
  // reading never claims more elapsed time than there was.
  [7_845_000, "2h 10m"],
])("formats %ims as %s", (ms, expected) => {
  expect(formatDuration(ms)).toBe(expected);
});

test("sub-minute precision survives the second boundary in both directions", () => {
  // 59.5s must not round up into a bare "60s" that reads as a minute in the wrong unit.
  expect(formatDuration(59_500)).toBe("60s");
  expect(formatDuration(59_999)).toBe("60s");
  expect(formatDuration(60_001)).toBe("1m 00s");
});

test("a live label is marked as still counting so it is not read as a final figure", () => {
  expect(durationLabel(run("RUNNING", undefined), NOW)).toBe("5m 00s…");
  expect(durationLabel(run("SUCCEEDED", "2026-09-01T10:05:00Z"), NOW)).toBe("5m 00s");
});
