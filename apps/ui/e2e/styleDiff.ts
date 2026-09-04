/**
 * The pure half of the computed-style diff gate.
 *
 * `style-diff.spec.ts` opens the same route at the same width against two builds - the
 * branch on :4173 and the pre-migration base on :4174 - captures a computed-style record
 * per element from each, and asks this module whether the two agree. Everything that
 * decides "same or different" lives here rather than in the spec, for two reasons.
 *
 * First, a browser gate that only ever runs against a passing build proves nothing about
 * its own ability to fail. The mutation cases in `styleDiff.test.ts` are the only place
 * where this comparator is shown a difference and required to report it, so they are the
 * evidence that the gate is not vacuous. A Playwright run cannot supply that: it would
 * need a deliberately broken second build on every invocation.
 *
 * Second, the two decisions that are easy to get quietly wrong - which mismatches justify
 * a RETRY, and how two captures of different lengths are aligned - are exactly the ones
 * that turn a gate into a rubber stamp. Retrying on a style difference would let a real
 * regression pass on its second attempt. Aligning past the end of the shorter capture
 * would read `undefined` and report nothing. Both are one-line mistakes, and both are
 * pinned by a named test below.
 *
 * Nothing here touches the DOM, Playwright or the filesystem: the capture arrives as data
 * from `computedStyleProbe()` in `audit.ts`.
 */

/**
 * One element's contribution to a page's structural fingerprint.
 *
 * Tag name AND child count, not tag name alone. A layout change that moves an element
 * between parents leaves the flattened tag sequence intact while changing the tree, and
 * the resulting index misalignment would surface as a torrent of style differences
 * pointing at the wrong elements. Child counts make that case a fingerprint mismatch
 * instead, which is the one condition this gate is allowed to retry.
 */
export interface ElementFingerprint {
  readonly tag: string;
  readonly children: number;
}

/** The whole document's structure, in the order `document.querySelectorAll('body *')` walks it. */
export type StructuralFingerprint = readonly ElementFingerprint[];

/**
 * One element's measured styles.
 *
 * `styles` is keyed by property name for the element itself (`backgroundColor`) and by
 * `<pseudo>.<property>` for its generated boxes (`::before.content`). The probe flattens
 * pseudo-elements into the same map deliberately: a pseudo-element is invisible to
 * `body *`, so a rule like `.brand-mark::first-letter { transform: rotate(-45deg) }` has
 * no element of its own to be compared on and would otherwise be unmeasured.
 */
export interface ElementCapture {
  readonly tag: string;
  readonly children: number;
  readonly styles: Readonly<Record<string, string>>;
}

/** One property on one element that the two builds disagree about. */
export interface StyleDifference {
  /** Position in the `body *` walk. Both captures are aligned by this index. */
  readonly index: number;
  /** The tag at that index, so a reader can find the element without re-running the gate. */
  readonly tag: string;
  /** `backgroundColor`, or `::after.content` for a pseudo-element property. */
  readonly property: string;
  readonly base: string;
  readonly branch: string;
}

/**
 * What a property reads as when the other build measured it and this one did not.
 *
 * A missing key is a real difference and must never be silently skipped: the probe asks
 * for a pseudo-element's styles only when its `content` is not `none`, so a rule that
 * stops generating a `::before` box removes keys rather than changing values. Reporting
 * that as a value of `(absent)` keeps the one output format for every difference. The
 * parentheses make it unmistakable in a report full of `rgb(...)` values, since no CSS
 * computed value is ever the literal string `(absent)`.
 */
export const ABSENT = "(absent)";

/**
 * Structural equality of two captures.
 *
 * Length first, then tag and child count per index. Deliberately NOT a hash: when this
 * returns false the spec retries, and on the second failure it prints the first index
 * where the two disagree, which a digest cannot give.
 */
export function fingerprintsEqual(
  base: StructuralFingerprint,
  branch: StructuralFingerprint,
): boolean {
  if (base.length !== branch.length) return false;
  return base.every(
    (element, index) =>
      element.tag === branch[index].tag && element.children === branch[index].children,
  );
}

/**
 * How far two numeric values may drift and still count as the same rendering.
 *
 * A hundredth of a CSS pixel. It exists for one measured reason: React Flow writes the
 * canvas viewport transform inline from geometry it measures itself, and two runs of the
 * SAME build produce matrices that differ in the fourth decimal place -
 * `matrix(1, 0, 0, 1, 639.356, 66.3674)` against `matrix(1, 0, 0, 1, 639.356, 66.3676)`,
 * observed on `/runs/:runId` at 800 px while this gate was being built. That is 0.0002 px:
 * two ten-thousandths of one pixel, arithmetic noise from a `ResizeObserver` measurement,
 * and not something any stylesheet caused.
 *
 * Reporting it would make the gate fail at random on a correct branch, and a gate that
 * fails at random is a gate that gets muted. Ignoring it costs nothing that can be
 * expressed: every length `styles.css` declares is an integer pixel, a rem, a percentage or
 * a viewport unit, the smallest of which (`letter-spacing: .04em` at 8 px) is 0.32 px -
 * thirty-two times this threshold. Colour channels are integers, so any colour change is at
 * least 1.0 away.
 *
 * The comparison is numeric rather than a rounding, deliberately. Rounding both sides to
 * two decimals would report `66.3649` against `66.3651` as a difference because they fall
 * on opposite sides of a bucket boundary, which is the same flakiness in a new place.
 */
export const SUBPIXEL_EPSILON = 0.01;

/** Numbers, including exponents and signs, as they appear inside a computed value. */
const NUMBER = /-?\d+(?:\.\d+)?(?:e[-+]?\d+)?/gi;

/**
 * True when two computed values differ only by sub-pixel arithmetic noise.
 *
 * Both values are reduced to a "skeleton" with every number removed. Different skeletons
 * are different values, full stop - `none` against `matrix(...)`, or `1px solid` against
 * `2px dashed`, never compare numerically. Only when the skeletons match are the numbers
 * compared pairwise against `SUBPIXEL_EPSILON`.
 */
export function equivalentValues(base: string, branch: string): boolean {
  if (base === branch) return true;
  if (base.replace(NUMBER, "\u0000") !== branch.replace(NUMBER, "\u0000")) return false;
  const baseNumbers = base.match(NUMBER) ?? [];
  const branchNumbers = branch.match(NUMBER) ?? [];
  if (baseNumbers.length !== branchNumbers.length) return false;
  return baseNumbers.every(
    (value, index) =>
      Math.abs(Number.parseFloat(value) - Number.parseFloat(branchNumbers[index])) <
      SUBPIXEL_EPSILON,
  );
}

/**
 * Every property the two builds compute differently, in walk order then property order.
 *
 * Alignment is by index, which is only sound once `fingerprintsEqual` holds - the spec
 * checks that first and never reaches here otherwise. The loop is nonetheless bounded by
 * the SHORTER capture: a caller that skipped the fingerprint check would otherwise index
 * past the end of one array and compare against `undefined`, which throws on `.styles` in
 * the best case and reports nothing in the worst.
 *
 * Keys are unioned rather than taken from either side, so a property that exists only in
 * the branch is reported with `(absent)` for the base and vice versa. Taking the base's
 * keys alone is the natural way to write this and would make the gate blind to every new
 * declaration a port introduces.
 */
export function diffStyles(
  base: readonly ElementCapture[],
  branch: readonly ElementCapture[],
): StyleDifference[] {
  const differences: StyleDifference[] = [];
  const bound = Math.min(base.length, branch.length);

  for (let index = 0; index < bound; index += 1) {
    const baseElement = base[index];
    const branchElement = branch[index];
    const properties = [
      ...new Set([...Object.keys(baseElement.styles), ...Object.keys(branchElement.styles)]),
    ].sort();

    for (const property of properties) {
      const baseValue = baseElement.styles[property] ?? ABSENT;
      const branchValue = branchElement.styles[property] ?? ABSENT;
      if (equivalentValues(baseValue, branchValue)) continue;
      differences.push({
        index,
        // The branch is the build under review, so name its tag when the two disagree.
        // They cannot disagree in practice: the fingerprint gate ran first.
        tag: branchElement.tag,
        property,
        base: baseValue,
        branch: branchValue,
      });
    }
  }

  return differences;
}

/**
 * How many times a route/width pair may be re-measured, and how long to wait first.
 *
 * `attempts` counts total measurements, not extra ones: `attempts: 3` means one measure
 * and at most two retries. `delaysMs` therefore holds `attempts - 1` entries, and
 * `nextRetryDelayMs` enforces the relationship rather than trusting it.
 */
export interface RetryPolicy {
  readonly attempts: number;
  readonly delaysMs: readonly number[];
}

/**
 * The policy the spec uses.
 *
 * Three attempts, backing off 500 ms then 1500 ms. The app polls at 2.5 s (runs and
 * approvals) and 5 s (runtimes) and holds an open SSE stream on the run page, so a page
 * can legitimately change shape between the branch measurement and the base measurement
 * taken moments later. That is a fingerprint mismatch, it is not a regression, and
 * retrying it is the difference between a gate that is read and one that is muted.
 *
 * The retry exists ONLY for that case. `style-diff.spec.ts` calls this after a
 * `fingerprintsEqual` failure and never after a `diffStyles` result: a style difference is
 * the finding, and re-running until it disappears would be the purest form of a gate that
 * checks nothing.
 */
export const DEFAULT_RETRY_POLICY: RetryPolicy = {
  attempts: 3,
  delaysMs: [500, 1_500],
};

/**
 * The delay before the attempt after `attempt`, or `null` when the budget is spent.
 *
 * `attempt` is 1-based and names the measurement that just failed its fingerprint check,
 * so the first call is `nextRetryDelayMs(1, policy)`. The off-by-one this signature is
 * written to prevent is the loop that retries `attempts` times *after* the first
 * measurement, which quietly triples a sweep of 17 routes x 5 widths.
 */
export function nextRetryDelayMs(
  attempt: number,
  policy: RetryPolicy = DEFAULT_RETRY_POLICY,
): number | null {
  if (attempt < 1) throw new RangeError(`attempt is 1-based; received ${attempt}`);
  if (attempt >= policy.attempts) return null;
  const delay = policy.delaysMs[attempt - 1];
  if (delay === undefined) {
    throw new RangeError(
      `retry policy declares ${policy.attempts} attempts but only ${policy.delaysMs.length} ` +
        "delays; it needs one fewer delay than attempts",
    );
  }
  return delay;
}

/** One difference, formatted as `route @ width: #index tag property base -> branch`. */
export function formatDifference(
  route: string,
  width: number,
  difference: StyleDifference,
): string {
  return (
    `${route} @ ${width}: #${difference.index} ${difference.tag} ${difference.property} ` +
    `${difference.base} → ${difference.branch}`
  );
}
