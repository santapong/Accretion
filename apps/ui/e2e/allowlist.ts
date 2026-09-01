/**
 * Accessibility violations this gate knowingly tolerates, and until when.
 *
 * The published evidence was measured with axe-core 4.10.2. This gate runs 4.13.0, which
 * carries rules that did not exist then, so it can legitimately surface defects the
 * original sweep never checked for. Cheap ones get fixed. Anything larger gets an entry
 * here rather than a weakened assertion, because a gate that quietly narrows its ruleset
 * to stay green is worse than no gate.
 *
 * This is the same waiver-with-expiry idiom `docs/acceptance/criteria.toml` uses, and it
 * fails closed for the same reason: `accretion.acceptance._expired()` treats an
 * out-of-date waiver as a failure, not as permission.
 *
 * THREE RULES, and the second and third are what stop this file rotting into a list of
 * excuses:
 *
 *   1. a violation with no entry fails the gate;
 *   2. an entry past `expires` fails the gate, even if the violation is still real - the
 *      point of an expiry is to force the decision back into the open;
 *   3. an entry matching nothing fails the gate - a waiver for a defect that no longer
 *      occurs is a false statement about the application, and deleting it is the whole
 *      point of having fixed the defect.
 *
 * Every entry needs an issue. "We'll get to it" without a tracked issue is how a temporary
 * waiver becomes permanent.
 */

export interface Waiver {
  /** axe rule id, e.g. "color-contrast". */
  readonly rule: string;
  /** The route path exactly as it appears in `routes.ts`. */
  readonly route: string;
  /** Why this is waived rather than fixed. */
  readonly reason: string;
  /** Tracking issue. Required. */
  readonly issue: string;
  /** ISO date. Past this, the gate fails. */
  readonly expires: string;
}

export const WAIVERS: readonly Waiver[] = [
  // Empty, and that is the claim: axe 4.13.0's full default ruleset reports zero
  // violations across all seventeen routes. Adding the first entry here should feel like
  // a decision, not a formality.
];

export interface WaiverVerdict {
  readonly unwaived: readonly string[];
  readonly expired: readonly string[];
  readonly unused: readonly string[];
}

/**
 * Apply the three rules above to one sweep's results.
 *
 * `today` is a parameter so the expiry logic is testable without waiting for a date to
 * pass, and dates are compared as ISO `YYYY-MM-DD` strings rather than `Date` objects:
 * `new Date("2026-12-01")` is midnight UTC, so a `Date` comparison would call a waiver
 * expired several hours early for anyone west of Greenwich. A waiver expires *after* its
 * stated day.
 *
 * Each waiver yields at most one message. An entry that is both unused and expired is
 * reported as unused, because deleting it is the action either way.
 */
export function judge(
  observed: readonly { route: string; rule: string }[],
  today: string = new Date().toISOString().slice(0, 10),
  waivers: readonly Waiver[] = WAIVERS,
): WaiverVerdict {
  const matched = new Set<string>();
  const unwaived: string[] = [];
  const key = (w: { rule: string; route: string }) => `${w.rule}@${w.route}`;

  for (const violation of observed) {
    const waiver = waivers.find((w) => w.rule === violation.rule && w.route === violation.route);
    if (!waiver) {
      unwaived.push(`${violation.route}: ${violation.rule}`);
      continue;
    }
    matched.add(key(waiver));
  }

  const unused = waivers
    .filter((w) => !matched.has(key(w)))
    .map((w) => `${w.route}: ${w.rule} no longer occurs; delete it (${w.issue})`);

  const expired = waivers
    .filter((w) => matched.has(key(w)) && w.expires < today)
    .map((w) => `${w.route}: ${w.rule} expired ${w.expires} (${w.issue})`);

  return { unwaived, expired, unused };
}
