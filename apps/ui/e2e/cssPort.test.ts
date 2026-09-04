import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";

import { HOVER_SELECTORS } from "./audit";

/**
 * The three stylesheets, read as bytes from disk.
 *
 * NOT `import "…css?raw"`, which is the obvious way to write this and is silently useless
 * here: `vite.config.ts` sets no `test.css`, so Vitest replaces every CSS module with an
 * empty one, and it does so BEFORE the `?raw` suffix is honoured. Measured while writing
 * this file - all three imports returned `""`, and every `toContain`/`not.toContain` guard
 * below passed against the empty string. A stylesheet gate that reads nothing is worse than
 * no gate, so the bytes are read directly. `readFileSync` also removes the last plugin from
 * the path: what is compared is unambiguously the source text a reviewer would diff.
 *
 * The non-emptiness assertion in "the stylesheet reader" is what keeps that mistake from
 * coming back in another form.
 */
const HERE = dirname(fileURLToPath(import.meta.url));
const read = (path: string) => readFileSync(resolve(HERE, path), "utf8");

const pinnedSource = read("./fixtures/styles.pre-pr5.css");
const stylesSource = read("../src/styles.css");
const themeSource = read("../src/theme.css");

/**
 * Union-equality: the text-level proof that the Tailwind port moves rules and changes none.
 *
 * The rendering diff in `style-diff.spec.ts` is the stronger evidence, but it can only
 * measure what the seeded backend renders. `examples/showcase.py` creates one successful
 * FAKE run with no gate, no loop search and no experience transfer, so roughly two thirds
 * of `styles.css` never reaches a pixel under it. The fixture-mocked passes narrow that gap
 * and do not close it: several rules exist for states no fixture reproduces at all.
 *
 * So this test compares TEXT. `apps/ui/e2e/fixtures/styles.pre-pr5.css` is a byte-identical
 * copy of `styles.css` as it stood before the first rule moved, and the invariant is that
 * the UNION of the two live stylesheets still contains exactly that set of rules - every
 * one of them, exactly once, with identical declarations, in the same relative order within
 * whichever file now holds it.
 *
 * ## Why the union, rather than "theme.css contains what styles.css lost"
 *
 * The port runs as a stack of PRs. At every point between the first and the last, some
 * rules live in `styles.css` and some in `theme.css`, and the only property that holds
 * throughout is a property of the pair. Checking either file alone would either pass
 * vacuously (nothing moved yet) or fail on every intermediate state.
 *
 * ## What each assertion is defending against
 *
 * - A declaration edited during the move ("while I am here, that hex should be the token")
 *   changes the triple and fails the multiset comparison.
 * - A comma-joined selector list split into two rules produces two selectors the pinned
 *   file has never seen, and loses the one it had. Both halves of the multiset comparison
 *   report it. This is the failure mode a naive per-selector union misses entirely, and it
 *   silently inverts specificity against any rule that sits between the two halves.
 * - A `@media` entry migrated without its context lands under a different key.
 * - A rule copied rather than moved appears twice in the union.
 * - A rule reordered within a file breaks the increasing-index check, which matters because
 *   `styles.css` resolves several same-specificity collisions by source order alone.
 */

/* ------------------------------------------------------------------------------------- */
/* A small CSS reader.                                                                     */
/* ------------------------------------------------------------------------------------- */

/** A block that holds declarations: a normal rule, or an at-rule like `@theme static`. */
interface DeclarationBlock {
  readonly kind: "declarations";
  /** The selector list, or the at-rule prelude. Normalised, never re-ordered. */
  readonly prelude: string;
  readonly declarations: readonly string[];
}

/** An at-rule that holds other rules: `@media`, `@layer components`. */
interface NestedBlock {
  readonly kind: "block";
  readonly prelude: string;
  readonly children: readonly CssNode[];
}

/** A semicolon-terminated at-rule: `@import`, `@layer a, b;`, `@source`. */
interface StatementNode {
  readonly kind: "statement";
  readonly prelude: string;
}

type CssNode = DeclarationBlock | NestedBlock | StatementNode;

/** One rule, addressed the way the union compares it. */
interface CssRule {
  /** The chain of enclosing at-rule preludes, outermost first. Empty at top level. */
  readonly context: readonly string[];
  readonly selector: string;
  readonly declarations: readonly string[];
}

/**
 * Collapse insignificant whitespace without touching anything that carries meaning.
 *
 * Runs of whitespace become one space; that is all. `>` and `,` and `:nth-child(3n)`
 * survive untouched here and are normalised separately, per construct, below - a blanket
 * "strip all spaces" would turn the descendant combinator in `.registry-card > .benchmark-
 * table` into the compound selector `.registry-card>.benchmark-table` only by luck, and
 * would turn `label small` into `labelsmall`.
 */
const collapse = (value: string) => value.replace(/\s+/g, " ").trim();

/**
 * Selector lists, compared as ONE string.
 *
 * Spaces around the combinators `>`, `+` and `~` and around `,` are insignificant and are
 * removed so a reformat is not reported as a port error. The comma-joined list is never
 * split and never sorted: `.a,.b` and `.b,.a` are different text, and more importantly
 * `.a,.b{x}` and `.a{x} .b{x}` are different rules whose difference is exactly what a
 * careless port produces.
 */
const normaliseSelector = (value: string) =>
  collapse(value).replace(/\s*([>+~,])\s*/g, "$1");

/**
 * At-rule preludes, compared with their internal spacing removed.
 *
 * `styles.css` writes the same breakpoint two ways - `@media(max-width:900px)` at :98 and
 * `@media (max-width: 900px)` at :275 - and they are the same media query. Treating them as
 * two contexts would be harmless here (the two blocks share no selector) but would make the
 * union brittle for no gain later, when the duplicated blocks are merged.
 */
const normaliseContext = (value: string) =>
  collapse(value).replace(/\s*([(),:])\s*/g, "$1");

/** `prop: value` compared as `prop:value`, with the value's own spacing collapsed. */
function normaliseDeclaration(value: string): string {
  const text = collapse(value);
  const colon = text.indexOf(":");
  if (colon === -1) return text;
  return `${text.slice(0, colon).trim()}:${text.slice(colon + 1).trim()}`;
}

/**
 * Remove `/* … *\/` comments.
 *
 * Done before brace matching rather than during it, so a comment containing a brace or a
 * semicolon cannot confuse the reader. Both stylesheets are hand-written and neither has a
 * comment inside a string, which is the one case this simplification would get wrong.
 */
const stripComments = (source: string) => source.replace(/\/\*[\s\S]*?\*\//g, "");

/** True at a position that is inside a quoted string or a parenthesised value. */
interface ScanState {
  quote: string | null;
  depth: number;
}

function advance(state: ScanState, character: string): void {
  if (state.quote) {
    if (character === state.quote) state.quote = null;
    return;
  }
  if (character === '"' || character === "'") state.quote = character;
  else if (character === "(") state.depth += 1;
  else if (character === ")") state.depth -= 1;
}

/**
 * Split a declaration body on top-level semicolons.
 *
 * Quote- and paren-aware because a value may legitimately contain either - the Google Fonts
 * URL carries three semicolons inside its query string, and `radial-gradient(...)` carries
 * commas inside parentheses.
 */
function splitDeclarations(body: string): string[] {
  const parts: string[] = [];
  const state: ScanState = { quote: null, depth: 0 };
  let start = 0;
  for (let index = 0; index < body.length; index += 1) {
    const character = body[index];
    if (!state.quote && state.depth === 0 && character === ";") {
      parts.push(body.slice(start, index));
      start = index + 1;
      continue;
    }
    advance(state, character);
  }
  parts.push(body.slice(start));
  return parts.map(normaliseDeclaration).filter((part) => part.length > 0);
}

/**
 * Split a selector list on top-level commas.
 *
 * The union compares a comma-joined list as one string on purpose (splitting it is the port
 * error it exists to catch), so this is deliberately NOT used there. It is used by the hover
 * coverage check below, which asks a different question: which individual compound selectors
 * carry a `:hover`. Paren-aware because `:not(.a, .b)` and `:is(.a, .b)` are legal and would
 * otherwise be torn in half.
 */
function splitSelectorList(selector: string): string[] {
  const parts: string[] = [];
  const state: ScanState = { quote: null, depth: 0 };
  let start = 0;
  for (let index = 0; index < selector.length; index += 1) {
    const character = selector[index];
    if (!state.quote && state.depth === 0 && character === ",") {
      parts.push(selector.slice(start, index));
      start = index + 1;
      continue;
    }
    advance(state, character);
  }
  parts.push(selector.slice(start));
  return parts.map((part) => part.trim()).filter((part) => part.length > 0);
}

/**
 * Read a stylesheet into a tree of statements, rules and at-rule blocks.
 *
 * A recursive brace matcher rather than a regular expression: `@media` blocks nest rules,
 * and a regex over `{...}` pairs cannot tell an inner rule from an outer one, which is
 * precisely how a `@media` entry would end up compared as though it were unconditional.
 */
function parse(source: string): CssNode[] {
  const text = stripComments(source);
  const nodes: CssNode[] = [];
  const state: ScanState = { quote: null, depth: 0 };
  let start = 0;

  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (state.quote || state.depth > 0) {
      advance(state, character);
      continue;
    }

    if (character === ";") {
      const prelude = collapse(text.slice(start, index));
      if (prelude) nodes.push({ kind: "statement", prelude });
      start = index + 1;
      continue;
    }

    if (character === "{") {
      const prelude = text.slice(start, index);
      const bodyStart = index + 1;
      const bodyEnd = matchBrace(text, bodyStart);
      const body = text.slice(bodyStart, bodyEnd);
      nodes.push(block(prelude, body));
      index = bodyEnd;
      start = bodyEnd + 1;
      continue;
    }

    advance(state, character);
  }

  return nodes;
}

/** The index of the `}` closing the block whose body starts at `from`. */
function matchBrace(text: string, from: number): number {
  const state: ScanState = { quote: null, depth: 0 };
  let braces = 1;
  for (let index = from; index < text.length; index += 1) {
    const character = text[index];
    if (!state.quote && state.depth === 0) {
      if (character === "{") braces += 1;
      else if (character === "}") {
        braces -= 1;
        if (braces === 0) return index;
      }
    }
    advance(state, character);
  }
  throw new Error(`unbalanced brace starting at offset ${from}`);
}

/**
 * A block is a container of rules if its body contains a brace, and a bag of declarations
 * otherwise.
 *
 * That one test separates `@media (…) { .a { … } }` from `@theme static { --x: 1 }` without
 * a list of container at-rule names to keep up to date. No declaration value in either
 * stylesheet contains a brace, so the test cannot be fooled by one.
 */
function block(prelude: string, body: string): CssNode {
  if (body.includes("{")) {
    return { kind: "block", prelude: normaliseContext(prelude), children: parse(body) };
  }
  const trimmed = collapse(prelude);
  return {
    kind: "declarations",
    prelude: trimmed.startsWith("@") ? normaliseContext(trimmed) : normaliseSelector(trimmed),
    declarations: splitDeclarations(body),
  };
}

/** Flatten a parsed tree into rules, carrying each rule's enclosing at-rule chain. */
function rules(nodes: readonly CssNode[], context: readonly string[] = []): CssRule[] {
  const flat: CssRule[] = [];
  for (const node of nodes) {
    if (node.kind === "statement") continue;
    if (node.kind === "block") {
      flat.push(...rules(node.children, [...context, node.prelude]));
      continue;
    }
    // `@theme static { … }` is a declaration block whose prelude is an at-rule. It declares
    // custom properties for Tailwind rather than styling a selector, so it is not a rule of
    // the kind this union compares.
    if (node.prelude.startsWith("@")) continue;
    flat.push({ context, selector: node.prelude, declarations: node.declarations });
  }
  return flat;
}

/** The comparison key: at-rule context, selector list and declarations, in order. */
const key = (rule: CssRule) =>
  `${rule.context.join(" ")} || ${rule.selector} || ${rule.declarations.join("; ")}`;

/** The layer every ported rule lands in, and the only part of `theme.css` in the union. */
const PORT_LAYER = "@layer components";

/**
 * The rules `theme.css` contributes: those inside `@layer components`, with the layer
 * stripped from their context so they compare against the unlayered originals.
 *
 * Everything else in `theme.css` - the `@theme static` tokens, the `@import`s, the
 * `@source` scoping - is not part of the port and is checked structurally instead.
 */
function portedRules(nodes: readonly CssNode[]): CssRule[] {
  return rules(nodes)
    .filter((rule) => rule.context[0] === PORT_LAYER)
    .map((rule) => ({ ...rule, context: rule.context.slice(1) }));
}

const pinnedTree = parse(pinnedSource);
const stylesTree = parse(stylesSource);
const themeTree = parse(themeSource);

const pinned = rules(pinnedTree);
const live = rules(stylesTree);
const ported = portedRules(themeTree);

const FONTS_IMPORT = /^@import\s+url\(["']?https:\/\/fonts\.googleapis\.com\//;

/* ------------------------------------------------------------------------------------- */
/* The reader has to be shown working before its verdict means anything.                   */
/* ------------------------------------------------------------------------------------- */

describe("the stylesheet reader", () => {
  test("keeps combinators, comma lists and structural pseudo-classes intact", () => {
    const parsed = rules(
      parse(`
        /* a comment { with a brace } and a ; semicolon */
        .loop-details > div:nth-child(2n) { border-right: 0 }
        .registry-card:focus-visible, .benchmark-table-wrap:focus-visible {
          outline: 2px solid #75db91;
          outline-offset: -2px;
        }
        @media (max-width: 900px) { .nav-status { display: none } }
      `),
    );

    expect(parsed).toEqual([
      {
        context: [],
        selector: ".loop-details>div:nth-child(2n)",
        declarations: ["border-right:0"],
      },
      {
        context: [],
        selector: ".registry-card:focus-visible,.benchmark-table-wrap:focus-visible",
        declarations: ["outline:2px solid #75db91", "outline-offset:-2px"],
      },
      {
        context: ["@media(max-width:900px)"],
        selector: ".nav-status",
        declarations: ["display:none"],
      },
    ]);
  });

  test("reads the pinned stylesheet as a large set of distinct rules", () => {
    // Three claims, all load-bearing. Non-empty sources are asserted first because the
    // obvious `?raw` spelling of this file's imports hands back `""` under Vitest and every
    // other assertion here passes against an empty stylesheet. The floor says the reader
    // did not quietly stop at the first construct it could not parse - a silently truncated
    // parse would make the union comparison trivially satisfiable. Uniqueness is what lets
    // the order check below address a rule by a single index.
    for (const [name, source] of [
      ["styles.pre-pr5.css", pinnedSource],
      ["styles.css", stylesSource],
      ["theme.css", themeSource],
    ] as const) {
      expect(source.length, `${name} was read as an empty string`).toBeGreaterThan(1_000);
    }
    expect(pinned.length).toBeGreaterThan(300);
    const keys = pinned.map(key);
    expect(new Set(keys).size, "the pinned sheet has no duplicate rule").toBe(keys.length);
  });
});

/* ------------------------------------------------------------------------------------- */
/* The union.                                                                              */
/* ------------------------------------------------------------------------------------- */

describe("union equality against the pre-migration stylesheet", () => {
  test("every pinned rule survives exactly once across the two live stylesheets", () => {
    const pinnedKeys = pinned.map(key);
    const liveKeys = [...live, ...ported].map(key);

    const counts = new Map<string, number>();
    for (const item of liveKeys) counts.set(item, (counts.get(item) ?? 0) + 1);

    const missing = pinnedKeys.filter((item) => !counts.has(item));
    const duplicated = pinnedKeys.filter((item) => (counts.get(item) ?? 0) > 1);
    const invented = liveKeys.filter((item) => !pinnedKeys.includes(item));

    expect(missing, `dropped or edited during the port:\n${missing.join("\n")}`).toEqual([]);
    expect(duplicated, `copied rather than moved:\n${duplicated.join("\n")}`).toEqual([]);
    expect(invented, `not present before the port:\n${invented.join("\n")}`).toEqual([]);
    expect(liveKeys.length).toBe(pinnedKeys.length);
  });

  test("no selector list is styled from both files under the same conditions", () => {
    // A selector with an unconditional rule in each file has two rules whose relative
    // precedence now depends on layering rather than on source order, and unlayered always
    // wins. That is the one way this port can change rendering while every rule is still
    // individually verbatim - `.registry-card` is defined twice in the pinned sheet, at :72
    // and again at :95, and moving only the first would silently invert them.
    //
    // The at-rule context is part of the identity, because a base rule and its `@media`
    // entry legitimately live in different files DURING a slice: the base moves first and
    // the entry follows in the same PR. The next test is what keeps that direction honest.
    const inStyles = new Set(live.map((rule) => `${rule.context.join(" ")} || ${rule.selector}`));
    const shared = [
      ...new Set(ported.map((rule) => `${rule.context.join(" ")} || ${rule.selector}`)),
    ].filter((addressed) => inStyles.has(addressed));
    expect(shared, `styled from both styles.css and theme.css:\n${shared.join("\n")}`).toEqual([]);
  });

  test("a selector's base rule never lags behind its own @media entries", () => {
    // The port's ordering rule, asserted rather than trusted: per selector, the base rule
    // moves no later than the `@media` entries that override it.
    //
    // Why it matters. While a rule sits in `theme.css` it is LAYERED and while it sits in
    // `styles.css` it is UNLAYERED, and unlayered wins outright. So for any selector still
    // present in both files, every occurrence left behind must be one that already won under
    // source order - that is, one that came LATER in the pinned sheet. Move a `@media` entry
    // first and its unmoved base rule starts beating it at every width, which is a rendering
    // change that every other check here would pass.
    const position = new Map(pinned.map((rule, index) => [key(rule), index]));
    const inverted: string[] = [];
    const split: string[] = [];
    for (const selector of new Set(ported.map((rule) => rule.selector))) {
      const layered = ported
        .filter((rule) => rule.selector === selector)
        .map((rule) => position.get(key(rule)) ?? -1);
      const unlayered = live
        .filter((rule) => rule.selector === selector)
        .map((rule) => position.get(key(rule)) ?? -1);
      if (!unlayered.length) continue;
      split.push(selector);
      if (Math.min(...unlayered) < Math.max(...layered)) {
        inverted.push(
          `${selector}: still unlayered at pinned rule ${Math.min(...unlayered)} but already ` +
            `layered at ${Math.max(...layered)}`,
        );
      }
    }
    // Named rather than asserted: the set of split selectors is empty at the end of a
    // completed slice, and will be empty for good once `styles.css` is deleted in PR5c. This
    // check is genuinely vacuous then, which is the correct end state and not a defect - but
    // a reader who sees "0 split selectors" should know that is what they are looking at
    // rather than assume the invariant was exercised.
    console.log(`cascade order: ${split.length} selector(s) live in both files`);
    expect(inverted, `cascade inverted by a partial move:\n${inverted.join("\n")}`).toEqual([]);
  });

  test("each live stylesheet keeps its rules in their pre-migration relative order", () => {
    // `styles.css` settles several same-specificity collisions by source order alone -
    // `.registry-card` at :72 and again at :95, `.pill` before its state variants, `h1`
    // before `.runtime-title h1`. Reordering during a move is invisible to a per-rule
    // comparison and visible on screen.
    const position = new Map(pinned.map((rule, index) => [key(rule), index]));
    for (const [name, subset] of [
      ["styles.css", live],
      ["theme.css", ported],
    ] as const) {
      const indices = subset.map((rule) => position.get(key(rule)) ?? -1);
      const sorted = [...indices].sort((a, b) => a - b);
      expect(indices, `${name} reordered its rules`).toEqual(sorted);
    }
  });
});

/* ------------------------------------------------------------------------------------- */
/* Structure of the two files, which the union alone cannot see.                            */
/* ------------------------------------------------------------------------------------- */

describe("the shape of theme.css", () => {
  test("declares nothing outside a layer except its own at-rule statements", () => {
    // The port's whole safety argument is that ported rules sit in `@layer components`,
    // underneath everything `styles.css` still declares. A rule that landed at the top level
    // of `theme.css` by accident would be unlayered, would beat the sheet it is supposed to
    // lose to, and would still satisfy union equality.
    const stray = themeTree
      .filter((node) => {
        if (node.kind === "statement") return !node.prelude.startsWith("@");
        return !(node.prelude.startsWith("@theme") || node.prelude.startsWith("@layer"));
      })
      .map((node) => node.prelude);
    expect(stray, `unlayered in theme.css:\n${stray.join("\n")}`).toEqual([]);
  });

  test("opens exactly one layer block, and it is the layer the union compares", () => {
    // The sibling case to the one above, and the one it does not cover. `portedRules` only
    // collects rules whose outermost context is `@layer components`; the stray check above
    // accepts ANY block whose prelude starts with `@layer`. Together those leave a hole:
    // rules written into a different layer block are invisible to the union - not "in the
    // wrong place and reported", genuinely not compared at all. Measured live while this
    // case was written: pasting `@layer base { .shell { padding: 0 } }` into `theme.css`
    // left the whole file green.
    //
    // And it is not merely unreported. `@layer utilities` comes AFTER `components` in the
    // order `theme.css:65` declares, so a rule there beats every ported rule regardless of
    // specificity and changes rendering, while the text-level proof - the layer that exists
    // precisely to cover the rules no browser pass renders - reports nothing.
    //
    // PR5d legitimately adds `@layer base` for the Preflight ledger. The expected list is
    // widened there, in the same commit that adds the ledger, so the widening is reviewed
    // beside the rules it admits rather than discovered afterwards.
    const layerBlocks = themeTree
      .filter((node) => node.kind !== "statement" && node.prelude.startsWith("@layer"))
      .map((node) => node.prelude);
    expect(
      layerBlocks,
      "theme.css declares a layer block other than @layer components; only that layer is " +
        "compared against the pinned sheet",
    ).toEqual([PORT_LAYER]);
  });

  test("references no React Flow class", () => {
    // xyflow's own stylesheet is unlayered, so the `.projection-flow .react-flow__*`
    // overrides win today by order and specificity and would lose the moment they entered a
    // layer. They stay in `styles.css` until PR5c moves them, unlayered, into
    // `react-flow.css` beside the xyflow import.
    expect(themeSource).not.toContain("react-flow__");
    expect(stylesSource).toContain("react-flow__");
  });
});

describe("the web font import", () => {
  test("exists exactly once across the two live stylesheets", () => {
    // Computed `font-family` reads back the declared stack whether or not the font ever
    // loaded, so the rendering diff is blind to a dropped `@import`. This guard and
    // `document.fonts.check` in `style-diff.spec.ts` are the two things that are not.
    const imports = [...stylesTree, ...themeTree].filter(
      (node) => node.kind === "statement" && FONTS_IMPORT.test(node.prelude),
    );
    expect(imports.length, "the Google Fonts @import must exist exactly once").toBe(1);
  });

  test("precedes every construct that is not an @import or a @layer statement", () => {
    // A CSS requirement, not a style preference: `@import` is ignored by the browser unless
    // it precedes every rule except `@charset` and `@layer` statements. Placed one line too
    // low it fails silently, and every heading falls back to the system sans stack.
    for (const [name, tree] of [
      ["styles.css", stylesTree],
      ["theme.css", themeTree],
    ] as const) {
      const at = tree.findIndex(
        (node) => node.kind === "statement" && FONTS_IMPORT.test(node.prelude),
      );
      if (at === -1) continue;
      const before = tree.slice(0, at).filter(
        (node) =>
          !(
            node.kind === "statement" &&
            (node.prelude.startsWith("@import") || node.prelude.startsWith("@layer"))
          ),
      );
      expect(before.map((node) => node.prelude), `${name} declares before its @import`).toEqual([]);
    }
  });
});

/* ------------------------------------------------------------------------------------- */
/* What the rendering diff's interaction pass is pointed at.                                */
/* ------------------------------------------------------------------------------------- */

/** The element that has to be hovered for `selector` to match, or `null` if it is not a hover rule. */
function hoverTarget(selector: string): string | null {
  const at = selector.indexOf(":hover");
  return at === -1 ? null : selector.slice(0, at);
}

/**
 * Every element the pinned sheet requires a pointer over, derived from the sheet itself.
 *
 * A comma-joined list is split here (and only here): `.run:hover,.run.selected` needs a
 * pointer over `.run` and nothing over `.run.selected`. Each hovering part is truncated at
 * the pseudo-class, so `.primary-button:hover:not(:disabled)` asks for `.primary-button` -
 * the element to put the pointer on, not the full condition under which the rule applies.
 */
const PINNED_HOVER_TARGETS = [
  ...new Set(
    pinned.flatMap((rule) =>
      splitSelectorList(rule.selector)
        .map(hoverTarget)
        .filter((target): target is string => target !== null),
    ),
  ),
].sort();

describe("the interaction pass covers every hover rule", () => {
  test("HOVER_SELECTORS names a target for each :hover rule and for nothing else", () => {
    // `style-diff.spec.ts` only measures hover states for the selectors `audit.ts` lists,
    // and a rule with no entry there is not reported as unmeasured - it is simply never
    // triggered, and the pass returns "0 differences" over it. That is the shape
    // `docs/releases/v0.3/acceptance-baseline.md` records as a gate proving less than it claims,
    // so the list is derived from the stylesheet here rather than maintained by hand.
    //
    // The pinned copy is the right source even after `styles.css` shrinks: it is the
    // complete pre-migration sheet, so this stays a statement about the whole port and not
    // about whichever slice has already moved.
    //
    // Both directions are asserted. A missing entry is the coverage hole. An extra entry is
    // a target with no rule behind it - a hover that measures nothing, which reads in the
    // log as coverage it is not.
    expect(
      PINNED_HOVER_TARGETS.length,
      "no :hover rule was found in the pinned sheet, so this check proves nothing",
    ).toBeGreaterThan(0);

    const listed = HOVER_SELECTORS.map(normaliseSelector);
    const missing = PINNED_HOVER_TARGETS.filter((target) => !listed.includes(target));
    const unused = listed.filter((target) => !PINNED_HOVER_TARGETS.includes(target));

    expect(
      missing,
      "the pinned sheet declares a :hover rule the interaction pass never triggers; add " +
        `it to HOVER_SELECTORS in e2e/audit.ts:\n${missing.join("\n")}`,
    ).toEqual([]);
    expect(
      unused,
      "HOVER_SELECTORS hovers an element no :hover rule in the pinned sheet styles:\n" +
        unused.join("\n"),
    ).toEqual([]);
  });

  test("splits a hover rule's selector list without splitting its parenthesised parts", () => {
    // The extractor above is the whole check; a truncation bug in it would empty the
    // expected set and turn the case green. These are the three shapes the sheet uses.
    expect(splitSelectorList(".run:hover,.run.selected").map(hoverTarget)).toEqual([
      ".run",
      null,
    ]);
    expect(hoverTarget(".primary-button:hover:not(:disabled)")).toBe(".primary-button");
    expect(splitSelectorList(":is(.a,.b):hover,.c")).toEqual([":is(.a,.b):hover", ".c"]);
  });
});
