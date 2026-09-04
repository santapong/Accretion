import { describe, expect, test } from "vitest";

import {
  ABSENT,
  DEFAULT_RETRY_POLICY,
  diffStyles,
  equivalentValues,
  fingerprintsEqual,
  formatDifference,
  nextRetryDelayMs,
  SUBPIXEL_EPSILON,
  type ElementCapture,
  type StructuralFingerprint,
} from "./styleDiff";

/**
 * The mutation table for the computed-style diff.
 *
 * `style-diff.spec.ts` runs against two builds that are supposed to agree, so on a healthy
 * branch it never sees a difference and never exercises the code that reports one. These
 * cases are where the comparator is handed a difference and required to name it. Each one
 * is a failure mode that would leave the browser gate green while the port broke something:
 * a property whose value changed, a declaration that appeared, a declaration that vanished,
 * a pseudo-element box that stopped being generated, a capture that got shorter, a retry
 * that fired on the wrong condition.
 */

/** A two-element page: a `<nav>` with three children and a `<span>` leaf inside it. */
function capture(overrides: Partial<Record<string, string>> = {}): ElementCapture[] {
  return [
    {
      tag: "nav",
      children: 3,
      styles: { backgroundColor: "rgb(12, 16, 13)", height: "72px" },
    },
    {
      tag: "span",
      children: 0,
      styles: { color: "rgb(137, 147, 139)", fontSize: "11px", ...overrides },
    },
  ];
}

describe("structural fingerprints", () => {
  test("two captures of the same tree are equal", () => {
    const fingerprint: StructuralFingerprint = [
      { tag: "nav", children: 3 },
      { tag: "span", children: 0 },
    ];
    expect(fingerprintsEqual(fingerprint, [...fingerprint])).toBe(true);
  });

  test("the same tag sequence with a different child count is not equal", () => {
    // The case tag names alone cannot see: an element moved between parents leaves the
    // flattened tag order intact. Without child counts the diff would align every
    // subsequent index against the wrong element and report a page of false differences.
    const base: StructuralFingerprint = [
      { tag: "nav", children: 3 },
      { tag: "span", children: 0 },
    ];
    const branch: StructuralFingerprint = [
      { tag: "nav", children: 4 },
      { tag: "span", children: 0 },
    ];
    expect(fingerprintsEqual(base, branch)).toBe(false);
  });

  test("captures of different lengths are not equal", () => {
    expect(fingerprintsEqual([{ tag: "nav", children: 3 }], [])).toBe(false);
  });
});

describe("computed-style differences", () => {
  test("identical captures produce no differences", () => {
    expect(diffStyles(capture(), capture())).toEqual([]);
  });

  test("a changed property is reported with its index, tag and both values", () => {
    const differences = diffStyles(capture(), capture({ color: "rgb(255, 0, 0)" }));
    expect(differences).toEqual([
      {
        index: 1,
        tag: "span",
        property: "color",
        base: "rgb(137, 147, 139)",
        branch: "rgb(255, 0, 0)",
      },
    ]);
    expect(formatDifference("/runtimes", 1440, differences[0])).toBe(
      "/runtimes @ 1440: #1 span color rgb(137, 147, 139) → rgb(255, 0, 0)",
    );
  });

  test("a property only the branch measured is reported with (absent) as the base value", () => {
    // A port that adds a declaration is as much a rendering change as one that removes it.
    // Reading the key set from the base alone would make this invisible.
    const differences = diffStyles(capture(), capture({ letterSpacing: "0.05em" }));
    expect(differences).toEqual([
      { index: 1, tag: "span", property: "letterSpacing", base: ABSENT, branch: "0.05em" },
    ]);
  });

  test("a property only the base measured is reported with (absent) as the branch value", () => {
    const differences = diffStyles(capture({ letterSpacing: "0.05em" }), capture());
    expect(differences).toEqual([
      { index: 1, tag: "span", property: "letterSpacing", base: "0.05em", branch: ABSENT },
    ]);
  });

  test("a pseudo-element that stops being generated is reported under its ::before key", () => {
    // The probe captures a pseudo-element only when its `content` resolves to something
    // other than `none`, so deleting `.candidate-tree::before { content: "" }` removes the
    // whole key group rather than changing a value. `styles.css` generates four such boxes
    // (:27-28, :63, :202, :204) and none of them owns an element in the `body *` walk.
    const base = capture();
    base[1] = {
      ...base[1],
      styles: {
        ...base[1].styles,
        "::before.content": '""',
        "::before.borderTopColor": "rgb(59, 83, 66)",
      },
    };
    expect(diffStyles(base, capture())).toEqual([
      {
        index: 1,
        tag: "span",
        property: "::before.borderTopColor",
        base: "rgb(59, 83, 66)",
        branch: ABSENT,
      },
      { index: 1, tag: "span", property: "::before.content", base: '""', branch: ABSENT },
    ]);
  });

  test("sub-pixel arithmetic noise is not a difference, but anything visible is", () => {
    // The tolerance that keeps React Flow's inline viewport transform from failing the gate
    // at random. Both halves are asserted, because a tolerance with no upper bound is just
    // a disabled comparison: 0.0002px is noise, 0.02px is twice the threshold and reported,
    // one colour channel is 1.0 away and reported, and a value whose SHAPE changed is never
    // compared numerically at all.
    expect(SUBPIXEL_EPSILON).toBe(0.01);
    const matrix = (y: string) => `matrix(1, 0, 0, 1, 639.356, ${y})`;
    expect(equivalentValues(matrix("66.3674"), matrix("66.3676"))).toBe(true);
    expect(equivalentValues(matrix("66.36"), matrix("66.38"))).toBe(false);
    expect(equivalentValues("rgb(137, 147, 139)", "rgb(137, 147, 140)")).toBe(false);
    expect(equivalentValues("none", "matrix(1, 0, 0, 1, 0, 0)")).toBe(false);
    expect(equivalentValues("1px solid rgb(41, 50, 44)", "1px dashed rgb(41, 50, 44)")).toBe(false);

    const differences = diffStyles(
      capture({ letterSpacing: "0.320px" }),
      capture({ letterSpacing: "0.3202px" }),
    );
    expect(differences).toEqual([]);
  });

  test("the diff stops at the shorter capture instead of reading past its end", () => {
    // Defence in depth: the spec checks the fingerprint first and only diffs equal-length
    // captures. If that order is ever inverted, an unbounded loop would dereference
    // `.styles` on `undefined` - or, with an optional chain bolted on to "fix" the crash,
    // would report nothing at all and pass.
    const short = capture().slice(0, 1);
    expect(() => diffStyles(short, capture({ color: "rgb(255, 0, 0)" }))).not.toThrow();
    expect(diffStyles(short, capture({ color: "rgb(255, 0, 0)" }))).toEqual([]);
    expect(diffStyles(capture({ color: "rgb(255, 0, 0)" }), short)).toEqual([]);
  });
});

describe("the retry decision", () => {
  test("the default policy allows a retry, and the final attempt gets none", () => {
    // A policy with no retry would make every poll-induced fingerprint mismatch a failure
    // and the gate would be muted within a week; a policy that never stops would hang the
    // sweep. Both ends are asserted here rather than read off the constant.
    expect(DEFAULT_RETRY_POLICY.attempts).toBeGreaterThan(1);
    expect(nextRetryDelayMs(1)).toBe(500);
    expect(nextRetryDelayMs(DEFAULT_RETRY_POLICY.attempts)).toBeNull();
    expect(nextRetryDelayMs(DEFAULT_RETRY_POLICY.attempts - 1)).not.toBeNull();
  });

  test("successive retries back off in the order the policy declares", () => {
    const policy = { attempts: 4, delaysMs: [100, 200, 400] } as const;
    expect([1, 2, 3, 4].map((attempt) => nextRetryDelayMs(attempt, policy))).toEqual([
      100,
      200,
      400,
      null,
    ]);
    // `attempts - 1` delays, not `attempts`: a policy that declares more attempts than it
    // has delays for is a typo, and returning `undefined` from it would be read as "wait
    // undefined milliseconds", which `setTimeout` treats as zero.
    expect(() => nextRetryDelayMs(2, { attempts: 5, delaysMs: [100] })).toThrow(/one fewer delay/);
    expect(() => nextRetryDelayMs(0)).toThrow(/1-based/);
  });
});
