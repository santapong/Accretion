import { expect, test } from "vitest";
import {
  BUDGET,
  INITIAL_CSS_GZIP_BYTES,
  INITIAL_CSS_RAW_BYTES,
  INITIAL_JS_GZIP_BYTES,
  INITIAL_JS_RAW_BYTES,
  PER_CHUNK_RAW_BYTES,
} from "./budget";
import { evaluateBudget, type Budget, type BundleChunk, type BundleGraph } from "./evaluate";

/**
 * The budget rules, tested on synthetic graphs.
 *
 * A production build takes about eight seconds and produces exactly one shape, so it can
 * demonstrate that the gate passes today and nothing else. Every interesting case — a
 * dependency in the wrong chunk, a lazy-only module that is secretly static, a stylesheet
 * counted twice — is a shape this repository does not currently have and, if the gate
 * works, never will. Those cases are built by hand here.
 *
 * Each test below is paired in the PR plan with the one-line mutation of `evaluate.ts`
 * that must make it fail. Two of them look redundant and are not: the closure tests
 * (dynamic import outside, static non-entry inside) are the two halves of the definition
 * of "initial", and an implementation can get either one right while getting the other
 * wrong. `isEntry`-only summation is the specific free pass a reviewer flagged: it makes
 * the initial caps look enforced while measuring roughly a fifth of what ships.
 */

/** Caps far above anything the fixtures emit, so a fixture only fails what it means to. */
function fixtureBudget(overrides: Partial<Budget> = {}): Budget {
  return {
    perChunkRawBytes: 500_000,
    initialJsRawBytes: 1_000_000,
    initialJsGzipBytes: 1_000_000,
    initialCssRawBytes: 1_000_000,
    initialCssGzipBytes: 1_000_000,
    lazyOnlyModules: /node_modules[\\/]three[\\/]/,
    lazyOnlyChunkName: "cosmic",
    grouping: [],
    ...overrides,
  };
}

function chunk(overrides: Partial<BundleChunk> = {}): BundleChunk {
  return {
    fileName: "assets/index-AAAAAAAA.js",
    name: "index",
    isEntry: true,
    isDynamicEntry: false,
    bytes: 100_000,
    gzipBytes: 30_000,
    imports: [],
    dynamicImports: [],
    moduleIds: ["/repo/apps/ui/src/main.tsx"],
    css: [],
    ...overrides,
  };
}

function ruleById(graph: BundleGraph, budget: Budget, id: string) {
  const rule = evaluateBudget(graph, budget).rules.find((candidate) => candidate.id === id);
  if (rule === undefined) throw new Error(`no rule ${id} in report`);
  return rule;
}

function failingIds(graph: BundleGraph, budget: Budget): string[] {
  return evaluateBudget(graph, budget)
    .rules.filter((rule) => !rule.pass)
    .map((rule) => rule.id);
}

test("a single entry chunk under every cap passes with no failing rule", () => {
  const report = evaluateBudget({ chunks: [chunk()] }, fixtureBudget());
  expect(report.rules.filter((rule) => !rule.pass)).toEqual([]);
  expect(report.pass).toBe(true);
  // The closure is reported, not just its total, so the table can be audited rather than
  // trusted: a reader can see which files the gate believed ship on first paint.
  expect(report.initial).toEqual(["assets/index-AAAAAAAA.js"]);
});

test("a chunk over the per-chunk raw cap fails only that chunk's rule", () => {
  const entry = chunk({ bytes: 10_000, imports: ["assets/vendor-BBBBBBBB.js"] });
  const vendor = chunk({
    fileName: "assets/vendor-BBBBBBBB.js",
    name: "vendor",
    isEntry: false,
    bytes: 500_001,
    gzipBytes: 120_000,
    moduleIds: ["/repo/node_modules/lodash/lodash.js"],
  });

  // Not an entry chunk, and the initial totals are far under their caps: the ONLY thing
  // wrong with this bundle is that one chunk is a byte over the per-chunk cap.
  expect(failingIds({ chunks: [entry, vendor] }, fixtureBudget())).toEqual([
    "per-chunk-raw:assets/vendor-BBBBBBBB.js",
  ]);
});

test("today's real shape, one chunk of 561,241 bytes, fails the per-chunk rule", () => {
  // The measured pre-split build of `a6975e3`, checked against the COMMITTED budget rather
  // than a fixture. This is what stops `PER_CHUNK_RAW_BYTES` being quietly raised to fit
  // whatever the app happens to weigh: raise it above 561,241 and this test says so.
  const today = chunk({
    bytes: 561_241,
    gzipBytes: 165_471,
    moduleIds: [
      "/repo/apps/ui/src/main.tsx",
      "/repo/node_modules/react-dom/client.js",
      "/repo/node_modules/@xyflow/react/dist/esm/index.js",
    ],
    css: [{ fileName: "assets/index-CCCCCCCC.css", bytes: 51_148, gzipBytes: 10_232 }],
  });

  const report = evaluateBudget({ chunks: [today] }, BUDGET);
  const perChunk = report.rules.find((rule) => rule.id.startsWith("per-chunk-raw:"));

  expect(perChunk?.pass).toBe(false);
  expect(perChunk?.measured).toBe(561_241);
  expect(perChunk?.cap).toBe(PER_CHUNK_RAW_BYTES);
  expect(report.pass).toBe(false);
});

test("a lazy chunk over the per-chunk cap fails even though it is outside the initial closure", () => {
  const entry = chunk({ bytes: 10_000, dynamicImports: ["assets/cosmic-QQQQQQQQ.js"] });
  const lazy = chunk({
    fileName: "assets/cosmic-QQQQQQQQ.js",
    name: "cosmic",
    isEntry: false,
    isDynamicEntry: true,
    bytes: 500_001,
    gzipBytes: 150_000,
    moduleIds: ["/repo/apps/ui/src/cosmic/scene.tsx"],
  });
  const budget = fixtureBudget({ lazyOnlyModules: /[\\/]src[\\/]cosmic[\\/]/ });
  const graph = { chunks: [entry, lazy] };

  // The chunk is genuinely lazy — correctly named, a real dynamic entry, outside the
  // closure — so `lazy-only` and both initial totals are satisfied. It is still half a
  // megabyte that somebody eventually waits for on a slow connection, and the per-chunk
  // rule is the only one in the table that can see it. Restricting that rule to the
  // initial closure would leave every assertion in this file green.
  const report = evaluateBudget(graph, budget);
  expect(report.initial).toEqual(["assets/index-AAAAAAAA.js"]);
  expect(report.rules.find((rule) => rule.id === "lazy-only")?.pass).toBe(true);
  expect(failingIds(graph, budget)).toEqual(["per-chunk-raw:assets/cosmic-QQQQQQQQ.js"]);
});

test("a chunk reachable only through a dynamic import is outside the initial closure", () => {
  const entry = chunk({ bytes: 100_000, dynamicImports: ["assets/cosmic-DDDDDDDD.js"] });
  const lazy = chunk({
    fileName: "assets/cosmic-DDDDDDDD.js",
    name: "cosmic",
    isEntry: false,
    isDynamicEntry: true,
    bytes: 400_000,
    gzipBytes: 120_000,
    moduleIds: ["/repo/apps/ui/src/cosmic/scene.tsx"],
  });

  const budget = fixtureBudget({ initialJsRawBytes: 200_000 });
  const report = evaluateBudget({ chunks: [entry, lazy] }, budget);

  expect(report.totals.initialJsRaw).toBe(100_000);
  expect(report.initial).toEqual(["assets/index-AAAAAAAA.js"]);
  expect(report.pass).toBe(true);
});

test("a chunk statically imported by the entry counts toward initial JS even though it is not an entry", () => {
  const entry = chunk({ bytes: 100_000, imports: ["assets/vendor-react-EEEEEEEE.js"] });
  const vendor = chunk({
    fileName: "assets/vendor-react-EEEEEEEE.js",
    name: "vendor-react",
    isEntry: false,
    bytes: 200_000,
    gzipBytes: 60_000,
    moduleIds: ["/repo/node_modules/react-dom/client.js"],
  });

  const budget = fixtureBudget({ initialJsRawBytes: 250_000 });
  const report = evaluateBudget({ chunks: [entry, vendor] }, budget);

  // 300,000 B ship on first paint. Summing only `isEntry` chunks would report 100,000 and
  // pass — the caps would appear enforced while ignoring every vendor chunk this PR
  // creates, which is to say while ignoring most of the bundle.
  expect(report.totals.initialJsRaw).toBe(300_000);
  expect(failingIds({ chunks: [entry, vendor] }, budget)).toEqual(["initial-js-raw"]);
});

test("initial JS gzip binds independently of raw", () => {
  const entry = chunk({ bytes: 100_000, gzipBytes: 90_000 });
  const budget = fixtureBudget({ initialJsRawBytes: 200_000, initialJsGzipBytes: 50_000 });

  // Raw is comfortably under; only the compressed size is over. A dependency that
  // compresses badly moves exactly one of these two numbers.
  expect(failingIds({ chunks: [entry] }, budget)).toEqual(["initial-js-gzip"]);
});

test("CSS shared by two initial chunks is counted once", () => {
  const shared = { fileName: "assets/index-FFFFFFFF.css", bytes: 30_000, gzipBytes: 8_000 };
  const entry = chunk({ imports: ["assets/vendor-flow-GGGGGGGG.js"], css: [shared] });
  const vendor = chunk({
    fileName: "assets/vendor-flow-GGGGGGGG.js",
    name: "vendor-flow",
    isEntry: false,
    moduleIds: ["/repo/node_modules/@xyflow/react/dist/esm/index.js"],
    // The same emitted stylesheet, referenced by a second chunk. This is the ordinary
    // shape once React Flow's sheet is imported by code in more than one chunk.
    css: [shared],
  });

  const budget = fixtureBudget({ initialCssRawBytes: 40_000, initialCssGzipBytes: 10_000 });
  const report = evaluateBudget({ chunks: [entry, vendor] }, budget);

  // The browser downloads it once, so counting it twice would invent a 60,000 B
  // regression and fail a build over bytes nobody transfers.
  expect(report.totals.initialCssRaw).toBe(30_000);
  expect(report.totals.initialCssGzip).toBe(8_000);
  expect(report.pass).toBe(true);
});

test("CSS over cap fails even when JS is under", () => {
  const entry = chunk({
    bytes: 10_000,
    gzipBytes: 4_000,
    css: [{ fileName: "assets/index-HHHHHHHH.css", bytes: 90_000, gzipBytes: 20_000 }],
  });
  const budget = fixtureBudget({ initialCssRawBytes: 50_000 });
  const report = evaluateBudget({ chunks: [entry] }, budget);

  // Four separate rules, never folded into one verdict: the report has to name the
  // stylesheet budget as the thing that broke, or the next author reads "initial size"
  // and starts deleting JavaScript.
  expect(ruleById({ chunks: [entry] }, budget, "initial-js-raw").pass).toBe(true);
  expect(ruleById({ chunks: [entry] }, budget, "initial-js-gzip").pass).toBe(true);
  expect(failingIds({ chunks: [entry] }, budget)).toEqual(["initial-css-raw"]);
  expect(report.pass).toBe(false);
});

test("a lazy-only module statically reachable from the entry fails even when its chunk is also a dynamic entry", () => {
  const entry = chunk({
    imports: ["assets/cosmic-IIIIIIII.js"],
    dynamicImports: ["assets/cosmic-IIIIIIII.js"],
  });
  const cosmic = chunk({
    fileName: "assets/cosmic-IIIIIIII.js",
    name: "cosmic",
    isEntry: false,
    // Correctly named, and genuinely a dynamic entry — and still shipped on first paint,
    // because something also imports it statically. This is the realistic regression: a
    // stray `import { Scene } from "./cosmic"` for a type or a constant re-attaches the
    // whole chunk to the initial closure while every other signal still looks right.
    isDynamicEntry: true,
    bytes: 300_000,
    gzipBytes: 90_000,
    moduleIds: ["/repo/node_modules/three/build/three.module.js"],
  });

  const rule = ruleById({ chunks: [entry, cosmic] }, fixtureBudget(), "lazy-only");
  expect(rule.pass).toBe(false);
  expect(rule.detail).toContain("in the initial closure");
});

test("a lazy-only module in a chunk not named cosmic fails", () => {
  const entry = chunk({ dynamicImports: ["assets/vendor-JJJJJJJJ.js"] });
  const misnamed = chunk({
    fileName: "assets/vendor-JJJJJJJJ.js",
    name: "vendor",
    isEntry: false,
    isDynamicEntry: true,
    bytes: 300_000,
    moduleIds: ["/repo/node_modules/three/build/three.module.js"],
  });

  // Selection is by module id. A rule that looked inside the chunk named `cosmic` would
  // find no such chunk here, conclude there is nothing to check, and report a vacuous
  // pass over a bundle that has three.js sitting in the shared vendor chunk.
  const rule = ruleById({ chunks: [entry, misnamed] }, fixtureBudget(), "lazy-only");
  expect(rule.pass).toBe(false);
  expect(rule.detail).toContain('named "vendor" not "cosmic"');
});

test("no lazy-only module present is reported as vacuous, not as a pass with no row", () => {
  const report = evaluateBudget({ chunks: [chunk()] }, fixtureBudget());
  const rule = report.rules.find((candidate) => candidate.id === "lazy-only");

  // Today there is no three.js, so this rule checks nothing. It says so, in the table, on
  // every build — because "no row" and "row that passed" are indistinguishable to a
  // reader, and this one will be checking nothing until PR7 lands the cosmic scene.
  expect(rule).toBeDefined();
  expect(rule?.pass).toBe(true);
  expect(rule?.detail).toBe("no matching modules (vacuous until PR7)");
});

test("a dependency landing in the wrong named chunk fails the grouping rule", () => {
  const expectation = {
    pattern: /node_modules[\\/]react-dom[\\/]/,
    chunkName: "vendor-react",
  };
  const budget = fixtureBudget({ grouping: [expectation] });
  const id = `chunk-of:${expectation.pattern.source}`;

  const placed = {
    chunks: [
      chunk({ imports: ["assets/vendor-react-KKKKKKKK.js"] }),
      chunk({
        fileName: "assets/vendor-react-KKKKKKKK.js",
        name: "vendor-react",
        isEntry: false,
        moduleIds: ["/repo/node_modules/react-dom/client.js"],
      }),
    ],
  };
  // The control, and it is the half that matters most: file names carry a content hash, so
  // a rule comparing `fileName` to "vendor-react" could never match and would report this
  // correct bundle as misplaced. Identity is checked on `name`.
  expect(ruleById(placed, budget, id).pass).toBe(true);

  const migrated = {
    chunks: [chunk({ moduleIds: ["/repo/node_modules/react-dom/client.js"] })],
  };
  const rule = ruleById(migrated, budget, id);
  expect(rule.pass).toBe(false);
  expect(rule.detail).toContain('expected chunk "vendor-react", found in "index"');
});

test("a package's stylesheet is not one of its modules for grouping or lazy-only purposes", () => {
  // Not a hypothetical: `RunExecution.tsx` imports `@xyflow/react/dist/style.css`, and that
  // module id matches the `@xyflow/react` grouping probe character for character. The chunk
  // groups in `vite.config.ts` deliberately leave CSS ids in the app chunk so that splitting
  // the JavaScript cannot reorder the stylesheet cascade — so a grouping rule that counted
  // the stylesheet would demand the opposite of what the config was written to do, and fail
  // every build. The gate did exactly that on its first real run.
  const expectation = {
    pattern: /node_modules[\\/]@xyflow[\\/]react[\\/]/,
    chunkName: "vendor-flow",
  };
  const budget = fixtureBudget({
    grouping: [expectation],
    lazyOnlyModules: /node_modules[\\/]three[\\/]/,
  });

  const graph = {
    chunks: [
      chunk({
        imports: ["assets/vendor-flow-LLLLLLLL.js"],
        moduleIds: [
          "/repo/apps/ui/src/RunExecution.tsx",
          // The stylesheet, in the app chunk, exactly where the config puts it.
          "/repo/node_modules/@xyflow/react/dist/style.css",
          "/repo/node_modules/three/build/three.css",
        ],
      }),
      chunk({
        fileName: "assets/vendor-flow-LLLLLLLL.js",
        name: "vendor-flow",
        isEntry: false,
        moduleIds: ["/repo/node_modules/@xyflow/react/dist/esm/index.js"],
      }),
    ],
  };

  expect(ruleById(graph, budget, `chunk-of:${expectation.pattern.source}`).pass).toBe(true);
  // Same filter on the lazy-only scan, for the same reason and by the same code path.
  expect(ruleById(graph, budget, "lazy-only").detail).toBe(
    "no matching modules (vacuous until PR7)",
  );
});

test("a grouping expectation that matches no module at all fails", () => {
  const expectation = {
    pattern: /node_modules[\\/]react-dom[\\/]/,
    chunkName: "vendor-react",
  };
  const budget = fixtureBudget({ grouping: [expectation] });
  const rule = ruleById({ chunks: [chunk()] }, budget, `chunk-of:${expectation.pattern.source}`);

  // The dependency was removed, or renamed, or the path layout changed. Treating that as a
  // pass leaves a rule in the table that reads like verification and performs none — the
  // same failure mode as an accessibility waiver that no longer matches anything.
  expect(rule.pass).toBe(false);
  expect(rule.detail).toContain("verifies nothing");
});

test("one failing rule fails the report", () => {
  const entry = chunk({ bytes: 500_001 });
  const report = evaluateBudget({ chunks: [entry] }, fixtureBudget());

  // Most rules pass. The verdict is a conjunction, not a disjunction: a gate that reported
  // success because something passed would be green on every build ever made.
  expect(report.rules.filter((rule) => rule.pass).length).toBeGreaterThan(0);
  expect(report.rules.filter((rule) => !rule.pass)).toHaveLength(1);
  expect(report.pass).toBe(false);
});

/**
 * The committed constants, checked through the evaluator rather than by reading them back.
 *
 * Everything above this line proves the RULES work, on budgets invented by the fixture. It
 * proves nothing about the budget this repository actually enforces, and that is where the
 * cheapest way to defeat the gate lives: raise a cap, blank a pattern, empty the grouping
 * list. Each of those leaves `evaluate.ts` untouched and correct, every test above green,
 * and the build passing over a bundle nobody is measuring any more.
 */

/**
 * The measurements behind the four committed caps, restated here as literals so that
 * raising a cap means restating its measurement in the same edit.
 *
 * The two JS numbers are M9 PR3's, from the first green build after the vendor split. The
 * two CSS numbers are M9 PR5a's, because that PR moved 113 rules into `@layer components`
 * and added thirteen `@theme static` tokens, and leaving PR3's 51,148 quoted here would
 * have described a stylesheet that no longer exists. The reasoning for each is in
 * `budget.ts` beside the cap it derives.
 */
const MEASURED = {
  initialJsRaw: 561_138,
  initialJsGzip: 166_998,
  initialCssRaw: 51_581,
  initialCssGzip: 10_372,
} as const;

/** The three named vendor chunks of that same build, raw and gzip as the gate printed them. */
const VENDOR_RAW = 189_604 + 177_375 + 73_760;
const VENDOR_GZIP = 58_953 + 56_740 + 23_635;

/**
 * The real post-split shape: an app entry statically importing three named vendor chunks,
 * with the one stylesheet attributed to the entry. Totals are parameters because these
 * tests are about where the committed caps sit relative to the measurement.
 */
function realBuildShape(js: { raw: number; gzip: number }, css: { raw: number; gzip: number }) {
  const sheet = { fileName: "assets/index-MMMMMMMM.css", bytes: css.raw, gzipBytes: css.gzip };
  return {
    chunks: [
      chunk({
        imports: [
          "assets/vendor-react-NNNNNNNN.js",
          "assets/vendor-flow-OOOOOOOO.js",
          "assets/vendor-data-PPPPPPPP.js",
        ],
        bytes: js.raw - VENDOR_RAW,
        gzipBytes: js.gzip - VENDOR_GZIP,
        moduleIds: [
          "/repo/apps/ui/src/main.tsx",
          // Where the config deliberately leaves React Flow's stylesheet.
          "/repo/node_modules/@xyflow/react/dist/style.css",
        ],
        css: [sheet],
      }),
      chunk({
        fileName: "assets/vendor-react-NNNNNNNN.js",
        name: "vendor-react",
        isEntry: false,
        bytes: 189_604,
        gzipBytes: 58_953,
        moduleIds: ["/repo/node_modules/react-dom/client.js"],
      }),
      chunk({
        fileName: "assets/vendor-flow-OOOOOOOO.js",
        name: "vendor-flow",
        isEntry: false,
        bytes: 177_375,
        gzipBytes: 56_740,
        moduleIds: ["/repo/node_modules/@xyflow/react/dist/esm/index.js"],
      }),
      chunk({
        fileName: "assets/vendor-data-PPPPPPPP.js",
        name: "vendor-data",
        isEntry: false,
        bytes: 73_760,
        gzipBytes: 23_635,
        moduleIds: ["/repo/node_modules/@tanstack/react-query/build/index.js"],
      }),
    ],
  };
}

test("each committed initial cap is the measured baseline plus five percent, and each one binds", () => {
  // The arithmetic half of the honesty rule `budget.ts` states in prose. A cap raised to
  // make a build go green stops matching its measurement, and this is the line that says
  // so — re-measuring means restating MEASURED here, in the same edit, deliberately.
  expect(INITIAL_JS_RAW_BYTES).toBe(Math.ceil(MEASURED.initialJsRaw * 1.05));
  expect(INITIAL_JS_GZIP_BYTES).toBe(Math.ceil(MEASURED.initialJsGzip * 1.05));
  expect(INITIAL_CSS_RAW_BYTES).toBe(Math.ceil(MEASURED.initialCssRaw * 1.05));
  expect(INITIAL_CSS_GZIP_BYTES).toBe(Math.ceil(MEASURED.initialCssGzip * 1.05));

  // The per-chunk cap is the one constant that is NOT measured-plus-headroom: it is Vite's
  // own advisory threshold, promoted to a failure. Nothing derives it, so unlike the four
  // caps above it has no legitimate reason to move, and a quiet loosening to 550,000 would
  // otherwise pass every test in this file (the real-shape case only needs it below
  // 561,241).
  expect(PER_CHUNK_RAW_BYTES).toBe(500_000);

  const js = { raw: MEASURED.initialJsRaw, gzip: MEASURED.initialJsGzip };
  const css = { raw: MEASURED.initialCssRaw, gzip: MEASURED.initialCssGzip };

  // At the measurement, the committed budget passes: the ratchet has headroom today.
  expect(evaluateBudget(realBuildShape(js, css), BUDGET).pass).toBe(true);

  // One byte past each cap in turn, everything else at the measurement. Each of the four
  // fails alone, which is what makes them four bounds rather than one. A cap set to a
  // number no bundle could reach would leave all four of these lists empty.
  expect(failingIds(realBuildShape({ ...js, raw: INITIAL_JS_RAW_BYTES + 1 }, css), BUDGET)).toEqual(
    ["initial-js-raw"],
  );
  expect(
    failingIds(realBuildShape({ ...js, gzip: INITIAL_JS_GZIP_BYTES + 1 }, css), BUDGET),
  ).toEqual(["initial-js-gzip"]);
  expect(failingIds(realBuildShape(js, { ...css, raw: INITIAL_CSS_RAW_BYTES + 1 }), BUDGET)).toEqual(
    ["initial-css-raw"],
  );
  expect(
    failingIds(realBuildShape(js, { ...css, gzip: INITIAL_CSS_GZIP_BYTES + 1 }), BUDGET),
  ).toEqual(["initial-css-gzip"]);
});

test("the committed lazy-only pattern catches three.js, @react-three and src/cosmic, on both path separators", () => {
  // Read through `evaluateBudget` with the COMMITTED budget, never by inspecting the RegExp:
  // a pattern narrowed to match nothing would still be a RegExp, the printed row would still
  // read "no matching modules (vacuous until PR7)", and the build would still pass. Vacuous
  // today and vacuous forever are the same text, so the pattern is exercised against ids.
  const ids = [
    "/repo/node_modules/three/build/three.module.js",
    "/repo/node_modules/@react-three/fiber/dist/index.js",
    "/repo/apps/ui/src/cosmic/scene.tsx",
    // Windows separators, which is the whole reason every pattern is written `[\\/]`. A
    // `/`-only pattern matches nothing here and the gate reports success while checking
    // nothing — on the one platform nobody in CI would notice.
    "C:\\repo\\node_modules\\three\\build\\three.module.js",
  ];

  for (const id of ids) {
    const graph = { chunks: [chunk({ moduleIds: ["/repo/apps/ui/src/main.tsx", id] })] };
    const rule = ruleById(graph, BUDGET, "lazy-only");
    expect(rule.pass, id).toBe(false);
    // All three conditions named, including the committed chunk name: this app chunk is
    // not called cosmic, ships on first paint, and is not a dynamic entry.
    expect(rule.detail, id).toContain('named "index" not "cosmic"');
    expect(rule.detail, id).toContain("in the initial closure");
    expect(rule.detail, id).toContain("not a dynamic entry");
  }

  // The control: an ordinary app module is not lazy-only, so the rule is vacuous — which is
  // what it reports on every build today, and the reason the row above cannot be trusted
  // without the four ids above.
  const ordinary = { chunks: [chunk({ moduleIds: ["/repo/apps/ui/src/RunExecution.tsx"] })] };
  expect(ruleById(ordinary, BUDGET, "lazy-only").detail).toBe(
    "no matching modules (vacuous until PR7)",
  );
});

test("the committed grouping list still pins react-dom and @xyflow/react to their vendor chunks", () => {
  // `evaluate.ts` refuses to let one expectation go vacuous. Nothing stops the LIST going
  // empty, and an empty list emits no `chunk-of` row at all: the build passes, the table
  // looks shorter, and every vendor could migrate into the app chunk unremarked. Two rows,
  // by count and by content.
  const migrated = {
    chunks: [
      chunk({
        moduleIds: [
          "/repo/apps/ui/src/main.tsx",
          "/repo/node_modules/react-dom/client.js",
          "/repo/node_modules/@xyflow/react/dist/esm/index.js",
        ],
      }),
    ],
  };
  const rules = evaluateBudget(migrated, BUDGET).rules.filter((rule) =>
    rule.id.startsWith("chunk-of:"),
  );

  expect(rules).toHaveLength(2);
  expect(rules.every((rule) => !rule.pass)).toBe(true);
  expect(rules.find((rule) => rule.id.includes("react-dom"))?.detail).toContain(
    'expected chunk "vendor-react", found in "index"',
  );
  expect(rules.find((rule) => rule.id.includes("@xyflow"))?.detail).toContain(
    'expected chunk "vendor-flow", found in "index"',
  );
});
