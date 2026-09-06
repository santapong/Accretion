import type { Run } from "./types";

/**
 * How long a run has been going, derived from the two timestamps `Run` actually carries.
 *
 * There is no `started_at` or `completed_at` on `Run` — only `created_at` and
 * `updated_at` (`src/accretion/contracts.py`, projected into `api/schema.d.ts`). So this
 * module reads a duration out of what exists rather than inventing a field, and states
 * plainly where that reading is approximate:
 *
 * - A **terminal** run is measured `created_at` → `updated_at`. `updated_at` is the last
 *   write to the row, and for a finished run that write is the transition into the
 *   terminal state, so the number is the real end-to-end duration. It would drift only
 *   if something wrote the row again afterwards; nothing in the control plane does today.
 * - A **live** run is measured `created_at` → now, so it ticks with the poll. Using
 *   `updated_at` here would show time-since-last-event and freeze whenever a provider
 *   went quiet — the opposite of what an operator watching a slow run needs to see.
 *
 * Nothing here fetches; it is a pure projection of a run the caller already has.
 */

const TERMINAL_STATES: ReadonlySet<string> = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);

export interface RunDuration {
  /** Elapsed milliseconds, never negative. */
  readonly elapsedMs: number;
  /** False while the run is live, so the caller can label it as still counting. */
  readonly final: boolean;
}

function instant(value: string | null | undefined): number | undefined {
  if (typeof value !== "string" || !value.length) return undefined;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/**
 * The elapsed time of one run, or `undefined` when it cannot be established.
 *
 * Returns `undefined` rather than zero when `created_at` is absent or unparseable: an
 * unknown duration and an instantaneous run are different facts, and rendering the
 * second for the first would be a quiet lie. A terminal run whose `updated_at` is
 * missing falls back to the wall clock, which is the same reading a live run gets.
 *
 * `now` is a parameter so tests are deterministic and a caller can render a whole list
 * against a single instant instead of a slightly different one per row.
 */
export function runDuration(
  run: Pick<Run, "state" | "created_at" | "updated_at"> | undefined,
  now: number = Date.now(),
): RunDuration | undefined {
  const created = instant(run?.created_at);
  if (created === undefined) return undefined;
  const final = TERMINAL_STATES.has(run!.state);
  const end = (final ? instant(run!.updated_at) : undefined) ?? now;
  // Clock skew between the control plane and the browser can put `end` behind `created`.
  // Clamping keeps a nonsensical negative off the screen without hiding the row.
  return { elapsedMs: Math.max(0, end - created), final };
}

/**
 * A duration an operator can read at a glance, at the precision that unit deserves.
 *
 * Sub-minute runs get a decimal because the difference between 2.1s and 2.9s is the
 * whole signal when you are comparing provider calls; past a minute it is noise, so the
 * larger units are whole and the seconds are padded to keep a column of rows aligned.
 */
export function formatDuration(elapsedMs: number): string {
  const seconds = elapsedMs / 1000;
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`;
  const whole = Math.round(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const remainder = whole % 60;
  if (hours) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  return `${minutes}m ${String(remainder).padStart(2, "0")}s`;
}

/** The label for one run: `undefined` when there is no duration worth showing. */
export function durationLabel(
  run: Pick<Run, "state" | "created_at" | "updated_at"> | undefined,
  now: number = Date.now(),
): string | undefined {
  const duration = runDuration(run, now);
  if (!duration) return undefined;
  return duration.final ? formatDuration(duration.elapsedMs) : `${formatDuration(duration.elapsedMs)}…`;
}
