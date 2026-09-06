/**
 * The bundle budget, evaluated. Pure logic: no `node:` imports, no I/O, no Vite types.
 *
 * Everything that makes the gate hard to trust lives here rather than in the plugin, and
 * it lives here precisely so it can be tested on synthetic graphs. `plugin.ts` is the
 * adapter — it turns an `OutputBundle` into a `BundleGraph` and prints the result — and it
 * makes no decisions. That split is deliberate: a gate whose judgement can only be
 * exercised by running a real production build is a gate nobody exercises.
 *
 * The five rules and why each one exists:
 *
 *   `per-chunk-raw`   Vite's own 500 kB advisory, promoted from a printed warning to a
 *                     failure. Applied to EVERY chunk, initial or lazy — a 2 MB chunk
 *                     behind a dynamic import is still 2 MB somebody eventually waits for.
 *   `initial-js-*`    The bytes a first-time visitor must download before the app runs.
 *                     Splitting one chunk into five does not shrink this by a byte, which
 *                     is exactly why the per-chunk rule alone would be a false comfort.
 *   `initial-css-*`   The same, for stylesheets, deduplicated: two initial chunks that
 *                     both pull in the same sheet cost that sheet once.
 *   `chunk-of`        Grouping identity. Caps say how much; this says where. Without it a
 *                     dependency could migrate from `vendor-react` into the app chunk and
 *                     every byte total would stay green while the caching story quietly
 *                     died.
 *   `lazy-only`       Modules that must never be reachable without a dynamic import. This
 *                     is the rule PR7's three.js scene is being held to before it exists.
 *
 * The initial closure is BFS from every entry chunk over `imports` ONLY. `dynamicImports`
 * is never walked — that edge is the whole point of the distinction, and a closure that
 * followed it would make `lazy-only` unsatisfiable and the initial caps meaningless.
 */

/** One emitted stylesheet, attributed to the chunks that import it. */
export interface BundleStylesheet {
  readonly fileName: string;
  readonly bytes: number;
  readonly gzipBytes: number;
}

/**
 * One emitted JavaScript chunk.
 *
 * `imports` and `dynamicImports` hold the file names of other chunks (Rolldown also lists
 * unresolved external ids there; the closure walk simply ignores names it cannot resolve).
 * `moduleIds` holds absolute source paths, which is what the grouping and lazy-only rules
 * match against — never the chunk name, which is the thing being verified.
 */
export interface BundleChunk {
  readonly fileName: string;
  readonly name: string;
  readonly isEntry: boolean;
  readonly isDynamicEntry: boolean;
  readonly bytes: number;
  readonly gzipBytes: number;
  readonly imports: readonly string[];
  readonly dynamicImports: readonly string[];
  readonly moduleIds: readonly string[];
  readonly css: readonly BundleStylesheet[];
}

export interface BundleGraph {
  readonly chunks: readonly BundleChunk[];
}

/**
 * "Every module matching `pattern` must end up in the chunk named `chunkName`."
 *
 * Matched on `name`, never on `fileName`: file names carry content hashes, so a rule
 * written against them would either never match or would have to be re-written on every
 * dependency bump.
 */
export interface GroupingExpectation {
  readonly pattern: RegExp;
  readonly chunkName: string;
}

export interface Budget {
  /** Applied to every chunk individually. */
  readonly perChunkRawBytes: number;
  readonly initialJsRawBytes: number;
  readonly initialJsGzipBytes: number;
  readonly initialCssRawBytes: number;
  readonly initialCssGzipBytes: number;
  /** Modules that may only ever be reached through a dynamic import. */
  readonly lazyOnlyModules: RegExp;
  /** The chunk name those modules must land in. */
  readonly lazyOnlyChunkName: string;
  readonly grouping: readonly GroupingExpectation[];
}

export interface BudgetRule {
  /** Stable identifier, e.g. `initial-js-raw` or `per-chunk-raw:assets/index-abc.js`. */
  readonly id: string;
  readonly pass: boolean;
  /** Human-readable explanation, printed for passing rules too. */
  readonly detail: string;
  /** Measured value in bytes where the rule is a size comparison, else `null`. */
  readonly measured: number | null;
  /** Cap in bytes where the rule is a size comparison, else `null`. */
  readonly cap: number | null;
}

export interface BudgetTotals {
  readonly initialJsRaw: number;
  readonly initialJsGzip: number;
  readonly initialCssRaw: number;
  readonly initialCssGzip: number;
}

export interface BudgetReport {
  readonly pass: boolean;
  readonly rules: readonly BudgetRule[];
  /** File names in the initial closure, sorted — printed so the closure itself is auditable. */
  readonly initial: readonly string[];
  readonly totals: BudgetTotals;
}

/**
 * `Number.toLocaleString` is locale-dependent, and CI logs that change shape with the
 * runner's environment are logs nobody can diff. Group digits by hand instead.
 */
export function groupDigits(value: number): string {
  return value.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/**
 * Module ids that are stylesheets rather than JavaScript.
 *
 * Both id-matching rules below scan `moduleIds`, and `moduleIds` contains CSS. That is not
 * a detail: `RunExecution.tsx` imports `@xyflow/react/dist/style.css`, whose id matches the
 * `@xyflow/react` grouping probe exactly — so without this filter the grouping rule
 * demanded a STYLESHEET be placed in the `vendor-flow` JavaScript chunk, and failed the
 * build when it was not.
 *
 * It must not be. The chunk groups in `vite.config.ts` skip CSS ids on purpose, so that
 * splitting the JavaScript cannot reorder the stylesheet cascade, and this filter is the
 * evaluator's half of that same decision. Stylesheets are not unmeasured as a result: they
 * are attributed to the chunks that import them through `viteMetadata.importedCss` and
 * bounded by `initial-css-raw` and `initial-css-gzip`.
 */
const STYLESHEET_ID = /\.css(\?|$)/;

/** A chunk's JavaScript module ids: what "which chunk is this package in" is a question about. */
function scriptModuleIds(chunk: BundleChunk): readonly string[] {
  return chunk.moduleIds.filter((id) => !STYLESHEET_ID.test(id));
}

/**
 * `RegExp.test` advances `lastIndex` on a `/g/` or `/y/` pattern, so the same pattern
 * applied to a list of module ids would skip roughly every other one — silently, and only
 * for patterns that happen to carry the flag. Reset before each use rather than trusting
 * every future author to remember not to write `/g/`.
 */
function matches(pattern: RegExp, value: string): boolean {
  pattern.lastIndex = 0;
  return pattern.test(value);
}

/**
 * Every chunk a first-time visitor loads: BFS from the entry chunks across static imports.
 *
 * Keyed by `fileName` because that is the identity Rolldown uses in `imports`. Returned as
 * a Map so callers can both test membership and iterate the chunks without a second pass.
 */
function initialClosure(chunks: readonly BundleChunk[]): Map<string, BundleChunk> {
  const byFileName = new Map(chunks.map((chunk) => [chunk.fileName, chunk]));
  const reached = new Map<string, BundleChunk>();
  const queue = chunks.filter((chunk) => chunk.isEntry);

  while (queue.length > 0) {
    const chunk = queue.shift() as BundleChunk;
    if (reached.has(chunk.fileName)) continue;
    reached.set(chunk.fileName, chunk);
    // `imports` only. Following `dynamicImports` here would quietly delete the difference
    // between "shipped on first paint" and "fetched when the user opens that screen".
    for (const importedFileName of chunk.imports) {
      const next = byFileName.get(importedFileName);
      if (next !== undefined && !reached.has(next.fileName)) queue.push(next);
    }
  }

  return reached;
}

function sizeRule(id: string, measured: number, cap: number, unit: string): BudgetRule {
  const pass = measured <= cap;
  const comparison = pass ? "<=" : ">";
  return {
    id,
    pass,
    detail: `${groupDigits(measured)} B ${comparison} ${groupDigits(cap)} B cap (${unit})`,
    measured,
    cap,
  };
}

/**
 * Evaluate one bundle against one budget.
 *
 * Deterministic in rule order — per-chunk rules sorted by file name, then the four totals,
 * then grouping in budget order, then lazy-only — so two builds of the same tree print the
 * same table and a diff of two gate outputs is meaningful.
 */
export function evaluateBudget(graph: BundleGraph, budget: Budget): BudgetReport {
  const rules: BudgetRule[] = [];
  const closure = initialClosure(graph.chunks);

  // Every chunk, not just the initial ones. A lazy chunk over the cap is a real defect.
  const byFileName = [...graph.chunks].sort((a, b) => (a.fileName < b.fileName ? -1 : 1));
  for (const chunk of byFileName) {
    rules.push(
      sizeRule(`per-chunk-raw:${chunk.fileName}`, chunk.bytes, budget.perChunkRawBytes, "raw"),
    );
  }

  let initialJsRaw = 0;
  let initialJsGzip = 0;
  for (const chunk of closure.values()) {
    initialJsRaw += chunk.bytes;
    initialJsGzip += chunk.gzipBytes;
  }

  // Deduplicated by file name: React Flow's stylesheet imported by two initial chunks is
  // downloaded once, so counting it twice would invent a regression that does not exist.
  const stylesheets = new Map<string, BundleStylesheet>();
  for (const chunk of closure.values()) {
    for (const sheet of chunk.css) {
      if (!stylesheets.has(sheet.fileName)) stylesheets.set(sheet.fileName, sheet);
    }
  }
  let initialCssRaw = 0;
  let initialCssGzip = 0;
  for (const sheet of stylesheets.values()) {
    initialCssRaw += sheet.bytes;
    initialCssGzip += sheet.gzipBytes;
  }

  // Four independent rules, never combined. Raw catches parse cost and gzip catches
  // transfer cost, and a change can move one without the other — a dependency that
  // compresses badly shows up in gzip alone.
  rules.push(sizeRule("initial-js-raw", initialJsRaw, budget.initialJsRawBytes, "raw"));
  rules.push(sizeRule("initial-js-gzip", initialJsGzip, budget.initialJsGzipBytes, "gzip"));
  rules.push(sizeRule("initial-css-raw", initialCssRaw, budget.initialCssRawBytes, "raw"));
  rules.push(sizeRule("initial-css-gzip", initialCssGzip, budget.initialCssGzipBytes, "gzip"));

  for (const expectation of budget.grouping) {
    const owners = graph.chunks.filter((chunk) =>
      scriptModuleIds(chunk).some((id) => matches(expectation.pattern, id)),
    );
    const id = `chunk-of:${expectation.pattern.source}`;

    if (owners.length === 0) {
      // A check that can go vacuous is not a check. If the pattern stops matching — the
      // dependency was removed, renamed, or the path layout changed — the honest report is
      // that the expectation is no longer verifying anything, not a silent pass.
      rules.push({
        id,
        pass: false,
        detail: `no module matches; expectation verifies nothing (expected chunk "${expectation.chunkName}")`,
        measured: null,
        cap: null,
      });
      continue;
    }

    const misplaced = owners.filter((chunk) => chunk.name !== expectation.chunkName);
    rules.push({
      id,
      pass: misplaced.length === 0,
      detail:
        misplaced.length === 0
          ? `all ${owners.length} matching module(s) in chunk "${expectation.chunkName}"`
          : `expected chunk "${expectation.chunkName}", found in ${misplaced
              .map((chunk) => `"${chunk.name}"`)
              .join(", ")}`,
      measured: null,
      cap: null,
    });
  }

  // Selected by MODULE ID, never by chunk name: the failure this rule exists to catch is a
  // lazy-only module landing in a chunk that is not `cosmic`, and looking only inside the
  // chunk named `cosmic` would be constitutionally unable to see it.
  const lazyOwners = graph.chunks.filter((chunk) =>
    scriptModuleIds(chunk).some((id) => matches(budget.lazyOnlyModules, id)),
  );

  if (lazyOwners.length === 0) {
    // An explicit row, never silence. The rule has nothing real to check until PR7 adds
    // the cosmic scene, and a reader of this table is entitled to know that.
    rules.push({
      id: "lazy-only",
      pass: true,
      detail: "no matching modules (vacuous until PR7)",
      measured: null,
      cap: null,
    });
  } else {
    // Three conditions, all required. `isDynamicEntry` alone is not enough: a chunk can be
    // a dynamic entry AND be statically imported by the app chunk, in which case it ships
    // on first paint regardless of the dynamic import that also exists.
    const offenders = lazyOwners
      .map((chunk) => {
        const reasons: string[] = [];
        if (chunk.name !== budget.lazyOnlyChunkName) {
          reasons.push(`named "${chunk.name}" not "${budget.lazyOnlyChunkName}"`);
        }
        if (closure.has(chunk.fileName)) reasons.push("in the initial closure");
        if (!chunk.isDynamicEntry) reasons.push("not a dynamic entry");
        return { chunk, reasons };
      })
      .filter((entry) => entry.reasons.length > 0);

    rules.push({
      id: "lazy-only",
      pass: offenders.length === 0,
      detail:
        offenders.length === 0
          ? `${lazyOwners.length} chunk(s) carry lazy-only modules, all dynamic-only`
          : offenders
              .map((entry) => `${entry.chunk.fileName}: ${entry.reasons.join("; ")}`)
              .join(" | "),
      measured: null,
      cap: null,
    });
  }

  return {
    // `every`, not `some`. One failing rule fails the build.
    pass: rules.every((rule) => rule.pass),
    rules,
    initial: [...closure.keys()].sort(),
    totals: { initialJsRaw, initialJsGzip, initialCssRaw, initialCssGzip },
  };
}
