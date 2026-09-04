import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";

import { COMPUTED_STYLE_PROPERTIES, FOCUSABLE_SELECTOR, HOVER_SELECTORS } from "./audit";

/**
 * The stylesheets of the port, read as bytes from disk.
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
 *
 * ## Why two of the four are optional
 *
 * The port is a stack of PRs and the set of files that exists CHANGES INSIDE IT. Before
 * M9 PR5c there is no `src/react-flow.css`; after PR5c there is no `src/styles.css`. A
 * `readFileSync` on either would throw at module scope in one of those two states, taking
 * the whole gate down rather than reporting anything - which is the shape R10 in the plan
 * names for `OperatorShell.test.tsx`, and there is no reason to reproduce it here.
 *
 * So each is read if present, and the invariants below are written over whichever exist.
 * That is not a weakening: "the canvas overrides live in exactly one unlayered sheet"
 * (below) is asserted in every state, and it is `styles.css` before PR5c and
 * `react-flow.css` after it. `theme.css` and the pin are NEVER optional - the union has no
 * meaning without them, so a typo in either path is a hard failure and not a skip.
 */
const HERE = dirname(fileURLToPath(import.meta.url));
const read = (path: string) => readFileSync(resolve(HERE, path), "utf8");
const readIfPresent = (path: string): string | null =>
  existsSync(resolve(HERE, path)) ? read(path) : null;

const pinnedSource = read("./fixtures/styles.pre-pr5.css");
const themeSource = read("../src/theme.css");

/**
 * The pre-migration sheet, until M9 PR5c deletes it. `null` from PR5c onward.
 *
 * Everything that reads it treats absence as "the port is finished", which is exactly what
 * it means: union equality then has to be satisfied by `theme.css` and `react-flow.css`
 * alone, and it is the completeness proof for the whole migration.
 */
const stylesSource = readIfPresent("../src/styles.css");

/**
 * The unlayered React Flow overrides, from M9 PR5c onward. `null` before it.
 *
 * This sheet exists for one reason: `@xyflow/react/dist/style.css` is unlayered (verified:
 * it declares no `@layer` and no `!important` at all), so a rule of ours that styles an
 * element INSIDE the canvas has to stay unlayered too or it loses to xyflow regardless of
 * specificity. It is imported from the component that imports the xyflow sheet, on the very
 * next line, and "the React Flow overrides sit beside the sheet they override" below is the
 * structural assertion behind that sentence.
 */
const reactFlowSource = readIfPresent("../src/react-flow.css");

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
const stylesTree = stylesSource === null ? [] : parse(stylesSource);
const reactFlowTree = reactFlowSource === null ? [] : parse(reactFlowSource);
const themeTree = parse(themeSource);

const pinned = rules(pinnedTree);
const live = rules(stylesTree);
const canvas = rules(reactFlowTree);
const ported = portedRules(themeTree);

/**
 * The live sheets, named, so every check below can iterate them instead of naming files.
 *
 * `unlayered` is the pair that beats `@layer components` outright. During the port that is
 * `styles.css` (everything not yet moved) and, from PR5c, `react-flow.css` (the canvas
 * overrides, which can never move). Keeping them in one list is what makes the ordering and
 * "no selector in two files" checks below hold across the whole stack rather than only in
 * the two-file state PR5a wrote them for.
 */
const UNLAYERED_SHEETS = [
  ["styles.css", live, stylesSource],
  ["react-flow.css", canvas, reactFlowSource],
] as const;

/** Every live sheet the union draws from, layered and unlayered alike. */
const LIVE_SHEETS = [...UNLAYERED_SHEETS, ["theme.css", ported, themeSource]] as const;

/** The rules the port currently spreads across those sheets. */
const liveRules = LIVE_SHEETS.flatMap(([, subset]) => [...subset]);

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
      ["styles.pre-pr5.css", pinnedSource as string | null],
      ["theme.css", themeSource],
      ...LIVE_SHEETS.map(([name, , source]) => [name, source] as const),
    ] as const) {
      if (source === null) continue;
      expect(source.length, `${name} was read as an empty string`).toBeGreaterThan(200);
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
  test("every pinned rule survives exactly once across the live stylesheets", () => {
    // The set of live sheets is whatever exists right now: `styles.css` + `theme.css`
    // during PR5a and PR5b, all three briefly inside PR5c, and `theme.css` +
    // `react-flow.css` once PR5c deletes the original. The invariant does not change with
    // the count - it is the same multiset comparison against the same pin - which is why
    // this reads `liveRules` rather than naming files.
    //
    // In the final state this case IS the completeness proof of the entire migration: with
    // `styles.css` gone, "every pinned rule exists exactly once" says that all 441 rules of
    // the pre-migration sheet are still declared, none twice, none edited on the way.
    const pinnedKeys = pinned.map(key);
    const liveKeys = liveRules.map(key);

    const counts = new Map<string, number>();
    for (const item of liveKeys) counts.set(item, (counts.get(item) ?? 0) + 1);

    const missing = pinnedKeys.filter((item) => !counts.has(item));
    const duplicated = pinnedKeys.filter((item) => (counts.get(item) ?? 0) > 1);
    const invented = liveKeys.filter((item) => !pinnedKeys.includes(item));

    expect(missing, `dropped or edited during the port:\n${missing.join("\n")}`).toEqual([]);
    expect(duplicated, `copied rather than moved:\n${duplicated.join("\n")}`).toEqual([]);
    expect(invented, `not present before the port:\n${invented.join("\n")}`).toEqual([]);
    expect(liveKeys.length).toBe(pinnedKeys.length);

    // Printed rather than asserted: the split between the sheets is a fact about which PR
    // this tree is on, not an invariant. Asserting it would mean editing this file every
    // time a rule moves, which is the opposite of what the union is for. It is logged so a
    // reviewer reading the CI output can see the port's shape without counting braces.
    console.log(
      `port: ${pinnedKeys.length} pinned rules across ` +
        LIVE_SHEETS.filter(([, subset]) => subset.length)
          .map(([name, subset]) => `${name} ${subset.length}`)
          .join(", "),
    );
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
    //
    // Generalised over the sheet list in PR5c, because there are three of them inside that
    // PR and the pairwise question is the same for every pair: `react-flow.css` is unlayered
    // too, so a canvas selector left behind in `styles.css` while its twin sits in
    // `react-flow.css` is settled by import order, and a canvas selector copied into
    // `theme.css` loses outright.
    const addressed = (rule: CssRule) => `${rule.context.join(" ")} || ${rule.selector}`;
    const homes = new Map<string, Set<string>>();
    for (const [name, subset] of LIVE_SHEETS) {
      for (const rule of subset) {
        const at = addressed(rule);
        if (!homes.has(at)) homes.set(at, new Set());
        homes.get(at)?.add(name);
      }
    }
    const shared = [...homes]
      .filter(([, sheets]) => sheets.size > 1)
      .map(([at, sheets]) => `${at}  (in ${[...sheets].sort().join(" and ")})`);
    expect(shared, `styled from more than one live sheet:\n${shared.join("\n")}`).toEqual([]);
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
      const unlayered = UNLAYERED_SHEETS.flatMap(([, subset]) => subset)
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
    console.log(`cascade order: ${split.length} selector(s) are both layered and unlayered`);
    expect(inverted, `cascade inverted by a partial move:\n${inverted.join("\n")}`).toEqual([]);
  });

  test("each live stylesheet keeps its rules in their pre-migration relative order", () => {
    // `styles.css` settles several same-specificity collisions by source order alone -
    // `.registry-card` at :72 and again at :95, `.pill` before its state variants, `h1`
    // before `.runtime-title h1`. Reordering during a move is invisible to a per-rule
    // comparison and visible on screen.
    const position = new Map(pinned.map((rule, index) => [key(rule), index]));
    for (const [name, subset] of LIVE_SHEETS) {
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
    // layer. They stayed in `styles.css` until PR5c moved them, unlayered, into
    // `react-flow.css` beside the xyflow import. Either way they are never here.
    expect(themeSource).not.toContain("react-flow__");

    // And they are somewhere: exactly one unlayered sheet carries them, and which one it is
    // says which side of PR5c this working tree is on. Without this half the assertion above
    // is satisfied by deleting the overrides.
    const carriers = UNLAYERED_SHEETS.filter(([, , source]) => source?.includes("react-flow__"));
    expect(
      carriers.map(([name]) => name),
      "the React Flow overrides must live in exactly one unlayered sheet",
    ).toHaveLength(1);
  });
});

/* ------------------------------------------------------------------------------------- */
/* The canvas partition, which is the whole of PR5c's cascade argument.                     */
/* ------------------------------------------------------------------------------------- */

/**
 * The classes that mark an element as living INSIDE the React Flow canvas.
 *
 * Every one of them is rendered under `<ReactFlow>` in `RunExecution.tsx` - as a node
 * (`.projection-node*`), as node content (`.projection-node-content`, `.projection-node-kind`,
 * `.projection-node-status`, `.projection-provider`, `.projection-node-badges`,
 * `.node-badge*`), as an edge (`.projection-loop-edge`) or as an edge label rendered into
 * the viewport through `EdgeLabelRenderer` (`.projection-edge-label`) - plus
 * `.projection-flow`, the wrapper xyflow mounts into.
 *
 * ## What this list is NOT
 *
 * It is not "every class that appears on the run page", and it is deliberately not every
 * class that appears inside the canvas either. `.iteration-badge`, `.gate-waiting-hint`,
 * `.pill` and the `.pill-*` states also render inside a node, and they are in
 * `@layer components` with the rest of the sheet. That is correct, because the property
 * that forces a rule to stay unlayered is not "renders inside the canvas" - it is COMPETES
 * WITH AN UNLAYERED xyflow RULE, and `@xyflow/react/dist/style.css` styles nothing but
 * `.react-flow`, `.react-flow.dark` and `.react-flow__*` (verified against the installed
 * package: those are the only selectors it declares, and it uses no `@layer` and no
 * `!important` anywhere). A rule on `.iteration-badge` has no xyflow counterpart to lose
 * to, so layering it changes nothing.
 *
 * What the classes below have in common is that each of their rules either names a
 * `.react-flow__*` class directly or sets a property xyflow also sets on the same element
 * (border, background, box-shadow, width, padding on `.react-flow__node-default`;
 * width/height/background on `.react-flow__handle`; stroke-dasharray on the edge path).
 * `.projection-node-kind-gate` is the sharpest case: at (0,1,0) it TIES xyflow's
 * `.react-flow__node-default` on specificity and wins only by coming later in an unlayered
 * cascade, which is precisely what `react-flow.css`'s import position preserves.
 *
 * `.projection-node-summary` and `.projection-routes` share the `projection-node` prefix and
 * are NOT here: they are the two lists rendered as siblings of `.projection-flow` inside
 * `.projection-card`, outside the canvas entirely. A prefix test rather than this explicit
 * list would have dragged them across, which is why the check below is written over class
 * tokens and not over substrings.
 */
const CANVAS_CLASSES: ReadonlySet<string> = new Set([
  "projection-flow",
  "projection-node",
  "projection-node-running",
  "projection-node-failed",
  "projection-node-succeeded",
  "projection-node-waiting",
  "projection-node-pending",
  "projection-node-content",
  "projection-node-kind",
  "projection-node-kind-gate",
  "projection-node-kind-terminal",
  "projection-node-status",
  "projection-node-group",
  "projection-node-badges",
  "projection-provider",
  "projection-loop-edge",
  "projection-edge-label",
  "node-badge",
  "node-badge-capability",
  "node-badge-part",
]);

/**
 * `button:disabled`, the one rule in the sheet that is unlayered for a reason no class name
 * records.
 *
 * M9 PR5a moved it into `@layer components` with the rest of `styles.css:3-25` and the
 * computed-style diff reported it immediately:
 *
 *   /runs/:runId @ 390: #148 button cursor not-allowed -> pointer
 *
 * xyflow sets `.react-flow__controls-button { cursor: pointer }`, unlayered. `button:disabled`
 * beats it on specificity ((0,1,1) against (0,1,0)) while both are unlayered and loses to it
 * outright from inside a layer, whatever the specificity - and the disabled zoom control on
 * the run page stopped saying it was disabled. It came back out in PR5a and lands here in
 * PR5c, in the sheet whose entire purpose is "unlayered, because xyflow is".
 */
const UNLAYERED_ELEMENT_RULES = ["button:disabled"] as const;

/** Every class token a selector list names, deduplicated. `.a.b>.c` gives `a`, `b`, `c`. */
function classTokens(selector: string): string[] {
  return [...new Set(selector.match(/\.-?[_a-zA-Z][\w-]*/g) ?? [])].map((token) => token.slice(1));
}

/** True for a rule that must stay unlayered beside xyflow's own sheet. */
function isCanvasRule(rule: CssRule): boolean {
  if (UNLAYERED_ELEMENT_RULES.includes(rule.selector as (typeof UNLAYERED_ELEMENT_RULES)[number])) {
    return true;
  }
  if (rule.selector.includes("react-flow__")) return true;
  return classTokens(rule.selector).some((token) => CANVAS_CLASSES.has(token));
}

/**
 * Where the canvas rules are supposed to be right now.
 *
 * Before PR5c that is `styles.css`, which is unlayered in its entirety; from PR5c it is
 * `react-flow.css`. Writing it as a lookup rather than as a constant is what lets the four
 * cases below assert the same partition on both sides of the PR that creates the file - and
 * it is not a loophole, because "exactly one unlayered sheet carries `react-flow__`" is
 * asserted above and the completeness case below pins every canvas rule to whichever sheet
 * this resolves to.
 */
const canvasHome = reactFlowSource === null ? "styles.css" : "react-flow.css";

describe("the React Flow overrides live outside every layer", () => {
  test("the pinned sheet's canvas rules all sit in the unlayered sheet that owns them", () => {
    // Completeness, derived from the pin rather than from a hand-kept list of what moved.
    // This is the case that fails when a canvas rule is "tidied" into `theme.css`: the rule
    // is still in the union exactly once, still byte-verbatim, still in relative order, and
    // silently loses to xyflow at runtime on every node in the graph.
    const pinnedCanvas = pinned.filter(isCanvasRule);
    expect(
      pinnedCanvas.length,
      "no canvas rule was classified, so this check proves nothing",
    ).toBeGreaterThan(20);

    const home = new Set(
      LIVE_SHEETS.filter(([name]) => name === canvasHome).flatMap(([, subset]) =>
        subset.map(key),
      ),
    );
    const misplaced = pinnedCanvas.map(key).filter((item) => !home.has(item));
    expect(
      misplaced,
      `these rules style an element inside the React Flow canvas and must be unlayered in ` +
        `${canvasHome}:\n${misplaced.join("\n")}`,
    ).toEqual([]);
  });

  test("neither the layer nor the sheet beside it holds a canvas rule", () => {
    // The converse direction. `theme.css` is checked in every state; `styles.css` is checked
    // from the moment `react-flow.css` exists, which is the window inside PR5c where a rule
    // could be left behind rather than moved.
    for (const [name, subset] of LIVE_SHEETS) {
      if (name === canvasHome) continue;
      const strays = subset.filter(isCanvasRule).map(key);
      expect(
        strays,
        `${name} styles an element inside the React Flow canvas; those rules belong in ` +
          `${canvasHome}, unlayered:\n${strays.join("\n")}`,
      ).toEqual([]);
    }
  });

  test("react-flow.css declares every rule it holds, and holds nothing else", () => {
    if (reactFlowSource === null) return;

    // A layer in this file would be self-defeating: the whole sheet exists to sit outside
    // one. `@layer` is checked as text rather than through the parser so that a layer
    // STATEMENT (`@layer components;`, which the parser records as a statement node and the
    // rule walker skips entirely) is caught too - but over the source with comments
    // stripped, because the file's header comment explains at length why it contains no
    // layer and says the word four times doing it. Measured: the first run of this case
    // failed on its own documentation.
    expect(
      stripComments(reactFlowSource),
      "react-flow.css must contain no @layer at all: an unlayered sheet is the only thing " +
        "that can beat @xyflow/react/dist/style.css, which is itself unlayered",
    ).not.toContain("@layer");

    const strays = canvas.filter((rule) => !isCanvasRule(rule)).map(key);
    expect(
      strays,
      "react-flow.css holds a rule that competes with nothing in xyflow's stylesheet; it " +
        `belongs in @layer components in theme.css:\n${strays.join("\n")}`,
    ).toEqual([]);

    expect(canvas.length, "react-flow.css was read as an empty sheet").toBeGreaterThan(20);
  });

  test("the classification tells the canvas lists apart from the canvas itself", () => {
    // The predicate is the whole check, and its one interesting failure mode is a prefix
    // test: `.projection-node-summary` renders BESIDE the canvas and shares eleven
    // characters with `.projection-node`, which does not.
    const canvasRule = (selector: string) => isCanvasRule({ context: [], selector, declarations: [] });
    expect(canvasRule(".projection-node.react-flow__node-default")).toBe(true);
    expect(canvasRule(".projection-node-content .pill")).toBe(true);
    expect(canvasRule(".projection-flow .react-flow__controls-button:hover")).toBe(true);
    expect(canvasRule("button:disabled")).toBe(true);
    expect(canvasRule(".projection-node-summary,.projection-routes")).toBe(false);
    expect(canvasRule(".projection-node-summary li")).toBe(false);
    expect(canvasRule(".projection-card")).toBe(false);
    expect(canvasRule(".iteration-badge")).toBe(false);
    expect(classTokens(".a.b>.c-d")).toEqual(["a", "b", "c-d"]);
  });

  test("xyflow's own stylesheet still has the three properties the partition relies on", () => {
    // The partition argument - the react-flow.css header, the canvas cases above - rests on
    // three facts about a third-party file: it is unlayered, it uses no !important, and every
    // selector it declares is scoped under `.react-flow`. A lockfile bump that broke any of
    // them would invalidate the cascade argument with every other gate green, because the
    // computed-style diff compares two builds that would BOTH resolve against the new file.
    // So the facts are read from the installed package (hoisted to the workspace root), not
    // remembered in prose.
    const xyflow = read("../../../node_modules/@xyflow/react/dist/style.css").replace(
      /\/\*[\s\S]*?\*\//g,
      "",
    );
    expect(xyflow.length, "xyflow's stylesheet was read as an empty string").toBeGreaterThan(1000);
    expect(xyflow, "xyflow now uses @layer; the partition must be re-derived").not.toContain("@layer");
    expect(xyflow, "xyflow now uses !important; the partition must be re-derived").not.toContain(
      "!important",
    );
    const declared = rules(parse(xyflow)).filter(
      (rule) => !rule.context.some((frame) => frame.startsWith("@keyframes")),
    );
    expect(declared.length, "xyflow's stylesheet parsed into too few rules").toBeGreaterThan(50);
    const unscoped = declared
      .map((rule) => rule.selector)
      .filter((selector) => !selector.split(",").every((part) => part.includes(".react-flow")));
    expect(
      unscoped,
      "xyflow declares a selector outside .react-flow; the canvas partition must be re-derived",
    ).toEqual([]);
  });
});

/* ------------------------------------------------------------------------------------- */
/* Cascade inversions: the winner the layer silently took away.                             */
/* ------------------------------------------------------------------------------------- */

/**
 * Specificity as (a, b, c), computed rather than eyeballed.
 *
 * Needed because the check below asks a question no other case in this file asks: not "is
 * this rule in the right file" but "does moving it into a layer change which of two
 * DIFFERENT selectors wins on an element they both match". That question is decided by
 * specificity before the port and by layering after it, so both have to be known.
 *
 * The three functional pseudo-classes the pinned sheet uses are handled by the spec:
 * `:not()` and `:is()` take the specificity of their most specific argument, `:where()`
 * takes none, and `:nth-child()` counts as one pseudo-class with its argument contributing
 * nothing. `*` contributes nothing and falls through the tokenizer.
 */
function specificity(selector: string): [number, number, number] {
  const counts: [number, number, number] = [0, 0, 0];
  let index = 0;
  while (index < selector.length) {
    const rest = selector.slice(index);
    const id = /^#[-\w]+/.exec(rest);
    if (id) {
      counts[0] += 1;
      index += id[0].length;
      continue;
    }
    const className = /^\.[-\w]+/.exec(rest);
    if (className) {
      counts[1] += 1;
      index += className[0].length;
      continue;
    }
    if (rest.startsWith("[")) {
      const end = selector.indexOf("]", index);
      counts[1] += 1;
      index = end === -1 ? selector.length : end + 1;
      continue;
    }
    const pseudoElement = /^::[-\w]+/.exec(rest);
    if (pseudoElement) {
      counts[2] += 1;
      index += pseudoElement[0].length;
      continue;
    }
    const pseudoClass = /^:[-\w]+/.exec(rest);
    if (pseudoClass) {
      const name = pseudoClass[0].slice(1).toLowerCase();
      index += pseudoClass[0].length;
      if (selector[index] !== "(") {
        counts[1] += 1;
        continue;
      }
      const close = matchParen(selector, index);
      const argument = selector.slice(index + 1, close);
      index = close + 1;
      if (name === "where") continue;
      if (name === "not" || name === "is" || name === "has") {
        const worst = splitSelectorList(argument)
          .map(specificity)
          .sort(compareSpecificity)
          .pop() ?? [0, 0, 0];
        counts[0] += worst[0];
        counts[1] += worst[1];
        counts[2] += worst[2];
        continue;
      }
      counts[1] += 1;
      continue;
    }
    const element = /^[a-zA-Z][-\w]*/.exec(rest);
    if (element) {
      counts[2] += 1;
      index += element[0].length;
      continue;
    }
    index += 1;
  }
  return counts;
}

/** The index of the `)` closing the parenthesis at `from`. */
function matchParen(selector: string, from: number): number {
  let depth = 0;
  for (let index = from; index < selector.length; index += 1) {
    if (selector[index] === "(") depth += 1;
    else if (selector[index] === ")" && (depth -= 1) === 0) return index;
  }
  return selector.length;
}

const compareSpecificity = (
  left: readonly number[],
  right: readonly number[],
): number => left[0] - right[0] || left[1] - right[1] || left[2] - right[2];

/** Split one selector part into its compound selectors, dropping the combinators. */
function compounds(part: string): string[] {
  const out: string[] = [];
  const state: ScanState = { quote: null, depth: 0 };
  let current = "";
  for (const character of part) {
    if (!state.quote && state.depth === 0 && /[\s>+~]/.test(character)) {
      if (current) out.push(current);
      current = "";
      continue;
    }
    current += character;
    advance(state, character);
  }
  if (current) out.push(current);
  return out;
}

/**
 * The simple selectors a compound is built from, as comparable tokens.
 *
 * `button:disabled` gives `el:button` and `:disabled`; `.a.b` gives `.a` and `.b`. The
 * element type is prefixed so it can never collide with a class of the same name.
 */
function simpleSelectors(compound: string): Set<string> {
  const out = new Set<string>();
  let index = 0;
  const element = /^[a-zA-Z][-\w]*/.exec(compound);
  if (element) {
    out.add(`el:${element[0].toLowerCase()}`);
    index = element[0].length;
  }
  while (index < compound.length) {
    const rest = compound.slice(index);
    if (rest.startsWith("[")) {
      const end = compound.indexOf("]", index);
      out.add(compound.slice(index, end === -1 ? undefined : end + 1));
      index = end === -1 ? compound.length : end + 1;
      continue;
    }
    const token = /^(::?[-\w]+|\.[-\w]+|#[-\w]+)/.exec(rest);
    if (!token) {
      index += 1;
      continue;
    }
    index += token[0].length;
    if (compound[index] === "(") {
      const close = matchParen(compound, index);
      out.add(compound.slice(index - token[0].length, close + 1));
      index = close + 1;
      continue;
    }
    out.add(token[0]);
  }
  return out;
}

const isSubset = (left: ReadonlySet<string>, right: ReadonlySet<string>) =>
  [...left].every((item) => right.has(item));

/** The simple selectors a part constrains on ancestors, i.e. everything but its key compound. */
function ancestorSelectors(part: string): Set<string> {
  const out = new Set<string>();
  for (const compound of compounds(part).slice(0, -1)) {
    for (const simple of simpleSelectors(compound)) out.add(simple);
  }
  return out;
}

/** One (unlayered rule, layered rule, property) triple the pre-migration sheet resolved. */
interface Competition {
  readonly unlayered: string;
  readonly layered: string;
  readonly property: string;
}

const competitionId = (pair: Competition) =>
  `${pair.layered} || ${pair.unlayered} || ${pair.property}`;

/**
 * The inversions that have been looked at and are not defects, each with the reason.
 *
 * An empty allow-list would be the ideal, and this one is not empty. The entry below is a
 * real inversion that the port does not introduce and cannot fix: on `develop` at the
 * merge-base `button:disabled` was ALREADY unlayered in `styles.css:15` (PR5a took it back
 * out of the layer, for the reason `react-flow.css`'s header records) while
 * `.benchmark-table td button` was ALREADY inside `theme.css`'s components layer. PR5c moves
 * both rules' text and changes neither's layer, so the computed-style diff against the
 * merge-base is structurally blind to it: both builds render the same wrong cursor.
 *
 * It is left standing rather than repaired here because every rule this PR touches moves
 * BYTE-VERBATIM, and the repair - `cursor: pointer` on the disabled table button, or
 * qualifying `button:disabled` so it stops reaching outside the canvas - is a declaration
 * change. It is recorded in the PR body and belongs to PR5d, which is the slice that is
 * allowed to change declarations.
 */
const REVIEWED_INVERSIONS: ReadonlyMap<string, string> = new Map([
  [
    ".benchmark-table td button || button:disabled || cursor",
    "Live, pre-existing, and not introduced by the port: `button:disabled` (0,1,1) is " +
      "unlayered because it has to beat xyflow's `.react-flow__controls-button " +
      "{cursor:pointer}` on the run page, and `.benchmark-table td button` (0,1,2) is " +
      "layered like the rest of the benchmark table. Before Tailwind both were unlayered " +
      "and the (0,1,2) rule won, so a disabled button in a benchmark-table cell showed " +
      "`cursor:pointer`; it now shows `not-allowed`. The element is real - " +
      "`pages/McpServersPage.tsx:152` renders `<table className=\"benchmark-table\">` and " +
      ":174 a `<button disabled>` inside one of its cells - and arguably `not-allowed` is " +
      "the correct rendering for a disabled control. Both halves of the pair predate this " +
      "PR on `develop` (00765e5), so fixing it is a declaration change and belongs to PR5d.",
  ],
]);

describe("layering never takes a fight the pre-migration sheet had already settled", () => {
  test("no layered rule lost a competition it used to win", () => {
    // The hole this closes. Every other case in this file asks whether a rule is in the
    // right FILE. None of them asks what happens between two DIFFERENT selectors that match
    // the same element: "no selector list is styled from both files" compares identical
    // selectors, and the `@media` case compares a selector against itself. Before the port
    // every rule was unlayered and a competition between two of them was decided by
    // specificity, then by source order. After it, an unlayered rule beats a layered one
    // whatever the specificity - so any pair where the LAYERED rule used to win has been
    // silently inverted, with every rule still byte-verbatim, still in the union exactly
    // once, and still in relative order.
    //
    // Derived from the pin rather than from a list of known pairs, because a hand-kept list
    // is exactly what stops being kept.
    //
    // ## The filters, and why each one is sound rather than convenient
    //
    // A pair is only examined when all four hold:
    //
    //  - the two rules can apply at the same width: identical `@media` context, or at least
    //    one of them unconditional;
    //  - neither declaration is `!important`. Importance is resolved before layers and
    //    REVERSES their order, so an important declaration's winner is not decided by the
    //    rule below. There are none in the current pairing and the exclusion is counted and
    //    printed so that stays visible;
    //  - they declare a common property with DIFFERENT values. Same value, nothing to
    //    invert - this is not a heuristic, it is the absence of an observable;
    //  - their KEY compounds are compatible: the simple selectors of one are a subset of the
    //    other's, so an element matching the narrower one necessarily matches the wider.
    //    `button` against `button:disabled` passes; `.projection-flow` against `.run-list`
    //    cannot describe one element and does not.
    //
    // What is deliberately NOT filtered out is the ancestor chain, because "these two
    // components never nest" is a claim about the JSX that this file cannot check. Pairs
    // whose ancestor constraints are disjoint are reported as UNREACHABLE below, with their
    // count and their text printed rather than silently dropped.
    const declarationsOf = (rule: CssRule) => {
      const map = new Map<string, string>();
      for (const declaration of rule.declarations) {
        const colon = declaration.indexOf(":");
        if (colon > 0) map.set(declaration.slice(0, colon).trim().toLowerCase(), declaration.slice(colon + 1).trim());
      }
      return map;
    };
    const position = new Map(pinned.map((rule, index) => [key(rule), index]));

    let examined = 0;
    let important = 0;
    const unreachable: string[] = [];
    const inversions: string[] = [];

    for (const [, subset] of UNLAYERED_SHEETS) {
      for (const unlayered of subset) {
        const unlayeredAt = position.get(key(unlayered));
        if (unlayeredAt === undefined) continue;
        const unlayeredDeclarations = declarationsOf(unlayered);
        for (const layered of ported) {
          const layeredAt = position.get(key(layered));
          if (layeredAt === undefined) continue;
          const unlayeredContext = unlayered.context.join(" ");
          const layeredContext = layered.context.join(" ");
          if (unlayeredContext && layeredContext && unlayeredContext !== layeredContext) continue;

          const layeredDeclarations = declarationsOf(layered);
          for (const [property, layeredValue] of layeredDeclarations) {
            const unlayeredValue = unlayeredDeclarations.get(property);
            if (unlayeredValue === undefined || unlayeredValue === layeredValue) continue;
            for (const unlayeredPart of splitSelectorList(unlayered.selector)) {
              for (const layeredPart of splitSelectorList(layered.selector)) {
                const unlayeredKey = simpleSelectors(compounds(unlayeredPart).at(-1) ?? "");
                const layeredKey = simpleSelectors(compounds(layeredPart).at(-1) ?? "");
                if (!isSubset(unlayeredKey, layeredKey) && !isSubset(layeredKey, unlayeredKey)) {
                  continue;
                }
                // Counted only once the pair is otherwise a real competition, so the number
                // printed below is the number of `!important` declarations this scan cannot
                // reason about rather than an artefact of loop nesting.
                if (
                  layeredValue.includes("!important") ||
                  unlayeredValue.includes("!important")
                ) {
                  important += 1;
                  continue;
                }
                examined += 1;

                // Who won before the port: higher specificity, then later in the sheet.
                const order = compareSpecificity(
                  specificity(layeredPart),
                  specificity(unlayeredPart),
                );
                if (order < 0 || (order === 0 && layeredAt < unlayeredAt)) continue;

                const pair: Competition = {
                  unlayered: unlayeredPart,
                  layered: layeredPart,
                  property,
                };
                const report =
                  `${layeredPart} (layered, pinned rule ${layeredAt}) used to beat ` +
                  `${unlayeredPart} (unlayered, pinned rule ${unlayeredAt}) on ` +
                  `${property}: ${layeredValue} -> ${unlayeredValue}`;

                const unlayeredAncestors = ancestorSelectors(unlayeredPart);
                const layeredAncestors = ancestorSelectors(layeredPart);
                if (
                  unlayeredAncestors.size &&
                  layeredAncestors.size &&
                  ![...unlayeredAncestors].some((simple) => layeredAncestors.has(simple))
                ) {
                  unreachable.push(report);
                  continue;
                }

                const reviewed = REVIEWED_INVERSIONS.get(competitionId(pair));
                if (reviewed) continue;
                inversions.push(report);
              }
            }
          }
        }
      }
    }

    // A floor, not a count: the pairing shrinks as rules move and it must never reach zero
    // silently, which is how this case would start passing over nothing at all.
    expect(
      examined,
      "the pair scan examined no competing selectors, so it proves nothing; either the " +
        "unlayered sheet is empty or the compatibility filter matches nothing",
    ).toBeGreaterThan(0);

    console.log(
      `cascade inversions: ${examined} competing pair(s) examined, ` +
        `${REVIEWED_INVERSIONS.size} reviewed, ${unreachable.length} unreachable ` +
        `(disjoint ancestor components), ${important} skipped as !important` +
        (unreachable.length ? `\n  unreachable: ${unreachable.join("\n  unreachable: ")}` : ""),
    );

    expect(
      inversions,
      "layering inverted a pre-migration winner:\n" +
        `${inversions.join("\n")}\n\nBefore the port both rules were unlayered and the one ` +
        "named first won on specificity or source order. It is now in @layer components " +
        "and loses to the unlayered one whatever its specificity. Either keep both rules " +
        "on the same side of the layer boundary, or add the pair to REVIEWED_INVERSIONS " +
        "with the reason it is not a defect.",
    ).toEqual([]);
  });

  test("every reviewed inversion is still a competition the scan finds", () => {
    // An allow-list that outlives the pair it exempts is an allow-list that hides the next
    // one. Each entry is addressed by selector text, so the moment either rule is edited or
    // moved across the layer boundary the entry stops matching anything - and this case
    // says so instead of leaving a dead exemption in place.
    const live = new Set<string>();
    for (const [, subset] of UNLAYERED_SHEETS) {
      for (const rule of subset) for (const part of splitSelectorList(rule.selector)) live.add(part);
    }
    const layeredParts = new Set(
      ported.flatMap((rule) => splitSelectorList(rule.selector)),
    );
    const stale = [...REVIEWED_INVERSIONS.keys()].filter((id) => {
      const [layered, unlayered] = id.split(" || ");
      return !layeredParts.has(layered) || !live.has(unlayered);
    });
    expect(
      stale,
      "REVIEWED_INVERSIONS exempts a pair that no longer exists in the stylesheets; " +
        `delete the entry:\n${stale.join("\n")}`,
    ).toEqual([]);
  });

  test("specificity and compound decomposition behave the way the scan assumes", () => {
    // The scan is only as good as these two, and both fail silently: a specificity function
    // that returns [0,0,0] for everything reports no inversion at all, and a compound
    // splitter that keeps the whole selector as one token makes every pair incompatible.
    expect(specificity("button:disabled")).toEqual([0, 1, 1]);
    expect(specificity(".benchmark-table td button")).toEqual([0, 1, 2]);
    expect(specificity(".projection-node.react-flow__node-default")).toEqual([0, 2, 0]);
    expect(specificity(".primary-button:hover:not(:disabled)")).toEqual([0, 3, 0]);
    expect(specificity(".loop-details>div:nth-child(2n)")).toEqual([0, 2, 1]);
    expect(specificity("#id .c el")).toEqual([1, 1, 1]);
    expect(specificity("*")).toEqual([0, 0, 0]);

    expect(compounds(".a .b>c")).toEqual([".a", ".b", "c"]);
    expect([...simpleSelectors("button:disabled")]).toEqual(["el:button", ":disabled"]);
    expect([...ancestorSelectors(".benchmark-table td button")]).toEqual([
      ".benchmark-table",
      "el:td",
    ]);
    expect([...ancestorSelectors("button:disabled")]).toEqual([]);
    expect(isSubset(simpleSelectors("button"), simpleSelectors("button:disabled"))).toBe(true);
    expect(isSubset(simpleSelectors(".projection-flow"), simpleSelectors(".run-list"))).toBe(false);
  });
});

/* ------------------------------------------------------------------------------------- */
/* Where the unlayered sheet is imported, which is the other half of the same argument.     */
/* ------------------------------------------------------------------------------------- */

/**
 * The `src/*.tsx` files that pull in xyflow's own stylesheet.
 *
 * Matched as an import STATEMENT rather than as a mention of the path. Measured: a plain
 * `includes()` reported three importers the moment `App.tsx` and `OperatorShell.test.tsx`
 * started explaining the cascade in prose, and the prose is exactly what this file's
 * neighbours are supposed to carry.
 */
const XYFLOW_STYLESHEET = "@xyflow/react/dist/style.css";
const XYFLOW_IMPORT = /^\s*import\s+["']@xyflow\/react\/dist\/style\.css["'];?\s*$/m;
const xyflowImporters = readdirSync(resolve(HERE, "../src"))
  .filter((entry) => entry.endsWith(".tsx"))
  .filter((entry) => XYFLOW_IMPORT.test(read(`../src/${entry}`)));

describe("the React Flow overrides sit beside the sheet they override", () => {
  test("exactly one component imports xyflow's stylesheet", () => {
    // Two importers would mean two places to keep the override next to, and the second one
    // would silently not have it. The count is the reason the case below can address "the"
    // importer at all.
    expect(xyflowImporters, "src/*.tsx importing xyflow's stylesheet").toHaveLength(1);
  });

  test("react-flow.css is imported on the statement immediately after it", () => {
    if (reactFlowSource === null) return;

    // Order is the only thing settling the ties: `.projection-node-kind-gate` and xyflow's
    // `.react-flow__node-default` are both (0,1,0) and both unlayered, so whichever sheet
    // is imported second wins the border colour of every gate node. Adjacency is what keeps
    // that from being decided by an unrelated import being added between them.
    //
    // Read as SOURCE TEXT, not as a module graph: this is the line a reviewer sees and the
    // line an editor would move. `style-diff.spec.ts` asserts the same thing about the
    // BUILT stylesheet, in a browser, which is where it actually has to hold.
    const source = read(`../src/${xyflowImporters[0]}`);
    const lines = source.split("\n");
    const at = lines.findIndex((line) => XYFLOW_IMPORT.test(line));
    expect(at, `${xyflowImporters[0]} does not import ${XYFLOW_STYLESHEET}`).toBeGreaterThan(-1);

    // The literal next line, not the next import: a non-import statement slipped between
    // the two would otherwise pass while the sentence below still claimed "nothing between".
    const next = lines[at + 1];
    expect(
      next?.trim(),
      `${xyflowImporters[0]} must import "./react-flow.css" on the import statement ` +
        "immediately after xyflow's own stylesheet, with nothing between them",
    ).toBe('import "./react-flow.css";');
  });
});

describe("the web font import", () => {
  test("exists exactly once across the two live stylesheets", () => {
    // Computed `font-family` reads back the declared stack whether or not the font ever
    // loaded, so the rendering diff is blind to a dropped `@import`. This guard and
    // `document.fonts.check` in `style-diff.spec.ts` are the two things that are not.
    const imports = [...stylesTree, ...reactFlowTree, ...themeTree].filter(
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
      ["react-flow.css", reactFlowTree],
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

/* ------------------------------------------------------------------------------------- */
/* The property list, which is the operational definition of "pixel-preserving".          */
/* ------------------------------------------------------------------------------------- */

const camel = (property: string) => property.replace(/-([a-z])/g, (_, c: string) => c.toUpperCase());

const SIDES = ["Top", "Right", "Bottom", "Left"] as const;
const CORNERS = ["TopLeft", "TopRight", "BottomRight", "BottomLeft"] as const;
const sides = (prefix: string, suffix = "") => SIDES.map((side) => `${prefix}${side}${suffix}`);

/**
 * What the probe must read for a declared shorthand to be measured. A shorthand not listed
 * here is expected under its own camelCase name; a longhand that IS its own name needs no
 * entry.
 */
const LONGHANDS: Readonly<Record<string, readonly string[]>> = {
  border: [...sides("border", "Width"), ...sides("border", "Style"), ...sides("border", "Color")],
  "border-width": sides("border", "Width"),
  "border-style": sides("border", "Style"),
  "border-color": sides("border", "Color"),
  "border-top": ["borderTopWidth", "borderTopStyle", "borderTopColor"],
  "border-right": ["borderRightWidth", "borderRightStyle", "borderRightColor"],
  "border-bottom": ["borderBottomWidth", "borderBottomStyle", "borderBottomColor"],
  "border-left": ["borderLeftWidth", "borderLeftStyle", "borderLeftColor"],
  "border-block": ["borderTopWidth", "borderTopStyle", "borderTopColor", "borderBottomWidth", "borderBottomStyle", "borderBottomColor"],
  "border-inline": ["borderLeftWidth", "borderLeftStyle", "borderLeftColor", "borderRightWidth", "borderRightStyle", "borderRightColor"],
  "border-radius": CORNERS.map((corner) => `border${corner}Radius`),
  margin: sides("margin"),
  "margin-block": ["marginTop", "marginBottom"],
  "margin-inline": ["marginLeft", "marginRight"],
  padding: sides("padding"),
  "padding-block": ["paddingTop", "paddingBottom"],
  "padding-inline": ["paddingLeft", "paddingRight"],
  inset: ["top", "right", "bottom", "left"],
  gap: ["rowGap", "columnGap"],
  "place-items": ["alignItems", "justifyItems"],
  "place-content": ["alignContent", "justifyContent"],
  background: ["backgroundColor", "backgroundImage", "backgroundPosition", "backgroundSize", "backgroundRepeat", "backgroundOrigin", "backgroundClip", "backgroundAttachment"],
  font: ["fontFamily", "fontSize", "fontStyle", "fontWeight", "fontStretch", "lineHeight"],
  flex: ["flexGrow", "flexShrink", "flexBasis"],
  "flex-flow": ["flexDirection", "flexWrap"],
  outline: ["outlineWidth", "outlineStyle", "outlineColor"],
  overflow: ["overflowX", "overflowY"],
  "grid-column": ["gridColumnStart", "gridColumnEnd"],
  "grid-row": ["gridRowStart", "gridRowEnd"],
  "grid-template": ["gridTemplateColumns", "gridTemplateRows"],
  "list-style": ["listStyleType", "listStylePosition", "listStyleImage"],
  "text-decoration": ["textDecorationLine", "textDecorationColor", "textDecorationStyle"],
};

/** Every property name the pinned sheet declares, custom properties excluded. */
const PINNED_PROPERTIES = [
  ...new Set(
    pinned.flatMap((rule) =>
      rule.declarations
        .map((declaration) => declaration.split(":")[0].trim().toLowerCase())
        .filter((property) => property && !property.startsWith("--")),
    ),
  ),
].sort();

describe("the computed-style probe reads every property the pinned sheet declares", () => {
  test("COMPUTED_STYLE_PROPERTIES covers each declared property or all of its longhands", () => {
    // `COMPUTED_STYLE_PROPERTIES` is what "pixel-preserving" means in practice: a property
    // the probe does not read is a property the diff cannot see. The list was complete when
    // it was written and nothing held it there - deleting `backgroundImage`, `boxShadow`,
    // `backdropFilter`, `transform` and `content` left every test green while the gate
    // stopped seeing every gradient, shadow, blur, rotation and generated box in the app.
    // So the list is derived from the stylesheet here, the way `HOVER_SELECTORS` is.
    expect(
      PINNED_PROPERTIES.length,
      "no declaration was found in the pinned sheet, so this check proves nothing",
    ).toBeGreaterThan(40);

    const listed = new Set(COMPUTED_STYLE_PROPERTIES);
    const missing = PINNED_PROPERTIES.flatMap((property) => {
      const required = LONGHANDS[property] ?? [camel(property)];
      const absent = required.filter((name) => !listed.has(name));
      return absent.length ? [`${property} -> ${absent.join(", ")}`] : [];
    });
    expect(
      missing,
      "the pinned sheet declares a property the computed-style probe never reads; add it " +
        `to COMPUTED_STYLE_PROPERTIES in e2e/audit.ts:\n${missing.join("\n")}`,
    ).toEqual([]);
  });

  test("the probe's property list carries no duplicate", () => {
    expect(new Set(COMPUTED_STYLE_PROPERTIES).size).toBe(COMPUTED_STYLE_PROPERTIES.length);
  });
});

/* ------------------------------------------------------------------------------------- */
/* The focus half of the interaction pass.                                                 */
/* ------------------------------------------------------------------------------------- */

/** The element a `:focus`/`:focus-visible` rule needs focus on, or `null` if the part has none. */
function focusTarget(selector: string): string | null {
  const index = selector.search(/:focus(-visible|-within)?\b/);
  if (index < 0) return null;
  return normaliseSelector(selector.slice(0, index));
}

/** Every element the pinned sheet styles on focus, derived from the sheet itself. */
const PINNED_FOCUS_TARGETS = [
  ...new Set(
    pinned.flatMap((rule) =>
      splitSelectorList(rule.selector)
        .map(focusTarget)
        .filter((target): target is string => target !== null && target !== ""),
    ),
  ),
].sort();

describe("the interaction pass can reach every focus rule", () => {
  test("FOCUSABLE_SELECTOR names each focused element type, and [tabindex] for the rest", () => {
    // The focus half of the pass reports green over zero captures if its selector matches
    // nothing - measured: with FOCUSABLE_SELECTOR set to `.no-such-element-anywhere`, "focus
    // and hover states render identically" still passed on all seventeen routes. The runtime
    // floor in `style-diff.spec.ts` (`NAV_FOCUS_FLOOR`) catches that at measurement time;
    // this is the text-level twin, derived from the sheet like the hover case above.
    //
    // A focus rule on an element type (`input:focus`) needs that type in the selector. A
    // focus rule on a class (`.registry-card:focus-visible`) styles a `div`/`ul` that is only
    // focusable because the markup gives it `tabIndex={0}` (the F4 fix), so the selector
    // has to reach it through `[tabindex]`; the pass logs how many such elements it found.
    expect(
      PINNED_FOCUS_TARGETS.length,
      "no :focus rule was found in the pinned sheet, so this check proves nothing",
    ).toBeGreaterThan(0);

    const parts = FOCUSABLE_SELECTOR.split(",").map(normaliseSelector);
    const elementTargets = PINNED_FOCUS_TARGETS.filter((target) => /^[a-z]/.test(target));
    const classTargets = PINNED_FOCUS_TARGETS.filter((target) => !/^[a-z]/.test(target));

    const missing = elementTargets.filter((target) => !parts.includes(target));
    expect(
      missing,
      "the pinned sheet styles an element type on focus that FOCUSABLE_SELECTOR never " +
        `focuses; add it in e2e/audit.ts:\n${missing.join("\n")}`,
    ).toEqual([]);
    expect(
      parts.includes("[tabindex]"),
      "the pinned sheet styles focusable regions by class " +
        `(${classTargets.join(", ")}); they are only reachable through [tabindex]`,
    ).toBe(classTargets.length === 0 || parts.includes("[tabindex]"));
    expect(parts.includes("[tabindex]") || classTargets.length === 0).toBe(true);
  });

  test("extracts the focused element from a rule's selector list", () => {
    expect(focusTarget("input:focus")).toBe("input");
    expect(focusTarget(".registry-card:focus-visible")).toBe(".registry-card");
    expect(focusTarget(".run.selected")).toBeNull();
    expect(splitSelectorList("input:focus,textarea:focus,select:focus").map(focusTarget)).toEqual([
      "input",
      "textarea",
      "select",
    ]);
  });
});

/* ------------------------------------------------------------------------------------- */
/* Byte-verbatim, checked rather than claimed.                                             */
/* ------------------------------------------------------------------------------------- */

/**
 * The rule texts inside a block, as written: each top-level `selector{...}` segment,
 * descending into `@media` containers so their entries are checked one by one (a container
 * in `theme.css` holds only the entries that moved, so the container itself is never a
 * substring of the pinned sheet - its entries are).
 */
function sliceRuleTexts(block: string): string[] {
  const texts: string[] = [];
  let depth = 0;
  let start = 0;
  for (let index = 0; index < block.length; index++) {
    const character = block[index];
    if (character === "{") {
      depth += 1;
      continue;
    }
    if (character !== "}") continue;
    depth -= 1;
    if (depth !== 0) continue;
    const text = block.slice(start, index + 1).trim();
    start = index + 1;
    if (!text) continue;
    if (text.startsWith("@media")) {
      const open = text.indexOf("{");
      texts.push(...sliceRuleTexts(text.slice(open + 1, -1)));
    } else {
      texts.push(text);
    }
  }
  return texts;
}

describe("every ported rule is a byte-verbatim slice of the pinned sheet", () => {
  test("each rule text inside @layer components occurs verbatim in styles.pre-pr5.css", () => {
    // The union above compares NORMALISED rules, so a reformatted move would pass it. The
    // prose in theme.css, the CHANGELOG and the frontend guide says "byte-verbatim"; this is
    // the assertion behind the word. A reviewer can diff the moved text against the pin.
    const source = stripComments(themeSource);
    const open = source.indexOf(`${PORT_LAYER} {`);
    expect(open, "theme.css has no @layer components block").toBeGreaterThanOrEqual(0);
    const bodyStart = source.indexOf("{", open) + 1;
    const bodyEnd = matchBrace(source, bodyStart);
    const texts = sliceRuleTexts(source.slice(bodyStart, bodyEnd));
    expect(texts.length, "no rule was sliced out of the components layer").toBeGreaterThan(50);
    const pinnedText = stripComments(pinnedSource);
    const notVerbatim = texts.filter((text) => !pinnedText.includes(text));
    expect(
      notVerbatim,
      "a rule in @layer components is not a byte-verbatim slice of the pinned sheet:\n" +
        notVerbatim.join("\n"),
    ).toEqual([]);
  });

  test("so does each rule in react-flow.css", () => {
    // The same claim about the other destination, added in PR5c. `react-flow.css` has no
    // wrapping layer, so the whole file is the block: `sliceRuleTexts` takes it directly and
    // still descends into the one `@media` container it holds, so that container's entry is
    // compared against the pin on its own rather than as part of a wrapper the pin never
    // wrote that way.
    if (reactFlowSource === null) return;
    const texts = sliceRuleTexts(stripComments(reactFlowSource));
    expect(texts.length, "no rule was sliced out of react-flow.css").toBeGreaterThan(20);
    const pinnedText = stripComments(pinnedSource);
    const notVerbatim = texts.filter((text) => !pinnedText.includes(text));
    expect(
      notVerbatim,
      "a rule in react-flow.css is not a byte-verbatim slice of the pinned sheet:\n" +
        notVerbatim.join("\n"),
    ).toEqual([]);
  });
});

