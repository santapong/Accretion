import { expect, test, vi } from "vitest";
import type { Plugin } from "vite";
import { bundleBudget, normalizeBundle, type EmittedBundle, type EmittedChunk } from "./plugin";
import type { Budget } from "./evaluate";

/**
 * The adapter between Rolldown's `OutputBundle` and the pure graph the evaluator judges.
 *
 * It makes no decisions, which is the point of it existing separately — but it does make
 * three measurements, and each of them has a plausible wrong answer that would leave every
 * rule in `evaluate.ts` correct and the gate still useless:
 *
 *   - `code.length` instead of UTF-8 bytes silently undercounts every non-ASCII character;
 *   - a gzip size that is not actually compressed makes the two gzip caps duplicates of
 *     the raw ones;
 *   - ignoring `viteMetadata` makes both CSS totals zero, so the CSS caps pass vacuously.
 *
 * All three fail in the safe-looking direction: the build stays green.
 */

function emittedChunk(overrides: Partial<EmittedChunk> = {}): EmittedChunk {
  return {
    type: "chunk",
    fileName: "assets/index-AAAAAAAA.js",
    name: "index",
    isEntry: true,
    isDynamicEntry: false,
    code: "const a = 1;",
    imports: [],
    dynamicImports: [],
    moduleIds: ["/repo/apps/ui/src/main.tsx"],
    ...overrides,
  };
}

test("chunk bytes are UTF-8 bytes, not string length", () => {
  // Real characters from this app's own copy: an em dash and a right arrow, three UTF-8
  // bytes each where `String.length` counts one. Twenty-four characters, twenty-eight bytes.
  const code = 'const label = "a — b →";';
  expect(code.length).toBe(24);

  const graph = normalizeBundle({ "assets/index-AAAAAAAA.js": emittedChunk({ code }) });

  expect(graph.chunks[0].bytes).toBe(Buffer.byteLength(code, "utf8"));
  expect(graph.chunks[0].bytes).toBe(28);
  expect(graph.chunks[0].bytes).toBeGreaterThan(code.length);
});

test("gzip bytes are smaller than raw for repetitive source", () => {
  // Minified bundles are extremely repetitive, so the compressed figure is roughly a third
  // of the raw one in practice. Anything that returned the raw size here would make
  // `initial-js-gzip` a second copy of `initial-js-raw` rather than an independent bound.
  const code = "const value = 1;".repeat(2_000);
  const graph = normalizeBundle({ "assets/index-AAAAAAAA.js": emittedChunk({ code }) });

  expect(graph.chunks[0].bytes).toBe(32_000);
  expect(graph.chunks[0].gzipBytes).toBeGreaterThan(0);
  expect(graph.chunks[0].gzipBytes).toBeLessThan(graph.chunks[0].bytes / 10);
});

test("stylesheets are attributed through viteMetadata.importedCss", () => {
  const styles = ".panel{color:#fff}";
  const graph = normalizeBundle({
    "assets/index-AAAAAAAA.js": emittedChunk({
      viteMetadata: { importedCss: new Set(["assets/index-BBBBBBBB.css"]) },
    }),
    "assets/index-BBBBBBBB.css": {
      type: "asset",
      fileName: "assets/index-BBBBBBBB.css",
      source: styles,
    },
    // An asset that is not CSS and is not imported by anything: it must not be counted.
    // Fonts and images are not part of the stylesheet budget.
    "assets/logo-CCCCCCCC.svg": {
      type: "asset",
      fileName: "assets/logo-CCCCCCCC.svg",
      source: "<svg/>",
    },
  });

  // `imports` never mentions a stylesheet: by the time the bundle exists, Vite has
  // extracted the CSS into its own asset and recorded the link only here. Ignoring
  // `viteMetadata` leaves both CSS rules measuring zero bytes and passing forever.
  expect(graph.chunks[0].css).toEqual([
    {
      fileName: "assets/index-BBBBBBBB.css",
      bytes: Buffer.byteLength(styles, "utf8"),
      gzipBytes: expect.any(Number),
    },
  ]);
  expect(graph.chunks[0].css[0].bytes).toBe(18);
});

test("module ids and import edges survive normalization", () => {
  // The grouping and lazy-only rules match on module ids, and the initial closure is built
  // from `imports`. If either were dropped in translation, both rules would go quietly
  // vacuous against a real bundle while every synthetic test in `evaluate.test.ts`
  // continued to pass.
  const graph = normalizeBundle({
    "assets/index-AAAAAAAA.js": emittedChunk({
      imports: ["assets/vendor-react-DDDDDDDD.js"],
      dynamicImports: ["assets/cosmic-EEEEEEEE.js"],
    }),
    "assets/vendor-react-DDDDDDDD.js": emittedChunk({
      fileName: "assets/vendor-react-DDDDDDDD.js",
      name: "vendor-react",
      isEntry: false,
      moduleIds: ["/repo/node_modules/react-dom/client.js"],
    }),
  });

  const entry = graph.chunks.find((chunk) => chunk.name === "index");
  const vendor = graph.chunks.find((chunk) => chunk.name === "vendor-react");

  expect(entry?.imports).toEqual(["assets/vendor-react-DDDDDDDD.js"]);
  expect(entry?.dynamicImports).toEqual(["assets/cosmic-EEEEEEEE.js"]);
  expect(vendor?.moduleIds).toEqual(["/repo/node_modules/react-dom/client.js"]);
  expect(vendor?.isEntry).toBe(false);
});

/**
 * The enforcement action itself.
 *
 * Everything above this line tests measurement, and everything in `evaluate.test.ts` tests
 * judgement — but a gate that measures correctly, judges correctly and then does nothing
 * about it is the exact failure the header of `plugin.ts` warns against: "a gate that stops
 * running and says nothing". Changing `this.error` to `this.info` on one line converts this
 * gate from enforcing to advisory, and without the two tests below that edit passes
 * `npm run check`, passes every vitest case, and lets `npm run build` print
 * `budget exceeded` and then exit 0 having written `dist/`.
 *
 * So these call the real hook, on the real plugin, against a stub Rollup plugin context,
 * and assert the two things a stub context can see: which method was called, and whether
 * control returned.
 */

/** The two `PluginContext` methods the gate uses. `error` throws in Rollup; the stub does too. */
interface StubContext {
  info: ReturnType<typeof vi.fn>;
  error: ReturnType<typeof vi.fn>;
}

function stubContext(): StubContext {
  return {
    info: vi.fn(),
    // Rollup's `PluginContext.error` is declared `never`-returning and aborts the build by
    // throwing. Reproduced here, because "did it throw" is how a caller distinguishes
    // `this.error` from `this.info` — the two are otherwise the same shape.
    error: vi.fn((message: unknown) => {
      throw new Error(typeof message === "string" ? message : String(message));
    }),
  };
}

/** Invoke `generateBundle` the way Rolldown does: as a method, with the plugin context as `this`. */
function runGenerateBundle(plugin: Plugin, ctx: StubContext, bundle: EmittedBundle): void {
  const hook = plugin.generateBundle;
  // An object hook (`{ handler }`) would still run in Vite but would slip past this test,
  // so the shape is asserted rather than pattern-matched away.
  if (typeof hook !== "function") throw new Error("generateBundle is not a plain function hook");
  const invoke = hook as unknown as (
    this: StubContext,
    options: unknown,
    bundle: EmittedBundle,
    isWrite: boolean,
  ) => void;
  invoke.call(ctx, {}, bundle, true);
}

/**
 * Two chunks, 48,000 raw bytes between them, `react-dom` correctly placed in
 * `vendor-react`: a miniature of the real build, so the only thing that decides pass or
 * fail below is the budget it is measured against.
 */
function twoChunkBundle(): EmittedBundle {
  return {
    "assets/index-AAAAAAAA.js": emittedChunk({
      code: "const value = 1;".repeat(2_000),
      imports: ["assets/vendor-react-DDDDDDDD.js"],
    }),
    "assets/vendor-react-DDDDDDDD.js": emittedChunk({
      fileName: "assets/vendor-react-DDDDDDDD.js",
      name: "vendor-react",
      isEntry: false,
      code: "const react = 1;".repeat(1_000),
      moduleIds: ["/repo/node_modules/react-dom/client.js"],
    }),
  };
}

const GENEROUS_BUDGET: Budget = {
  perChunkRawBytes: 500_000,
  initialJsRawBytes: 500_000,
  initialJsGzipBytes: 500_000,
  initialCssRawBytes: 500_000,
  initialCssGzipBytes: 500_000,
  lazyOnlyModules: /node_modules[\\/]three[\\/]/,
  lazyOnlyChunkName: "cosmic",
  grouping: [{ pattern: /node_modules[\\/]react-dom[\\/]/, chunkName: "vendor-react" }],
};

test("a failing report aborts the build through this.error", () => {
  // Exactly one cap is lowered, so the assertion on the count is meaningful: if some
  // second rule started failing for an unrelated reason, this test says so rather than
  // passing on the wrong failure.
  const plugin = bundleBudget({ ...GENEROUS_BUDGET, initialJsRawBytes: 1_000 });
  const ctx = stubContext();

  expect(() => runGenerateBundle(plugin, ctx, twoChunkBundle())).toThrow(
    /budget exceeded: 1 rule\(s\) failed/,
  );

  // The gate must not have taken the informational path. `this.info` does not throw, so
  // without this line the test would still pass if the message were routed to it and the
  // throw came from somewhere else.
  expect(ctx.info).not.toHaveBeenCalled();
  expect(ctx.error).toHaveBeenCalledTimes(1);

  // The whole table travels in the failure message, so a CI log shows what was measured
  // and not only what broke.
  const message = String(ctx.error.mock.calls[0][0]);
  expect(message).toContain("FAIL  initial-js-raw");
  expect(message).toContain("48,000 B > 1,000 B cap (raw)");
  expect(message).toContain("PASS  initial-js-gzip");
  expect(message).toContain("apps/ui/budget/budget.ts was exceeded");
});

test("a passing report prints the table and does not throw", () => {
  const plugin = bundleBudget(GENEROUS_BUDGET);
  const ctx = stubContext();

  expect(() => runGenerateBundle(plugin, ctx, twoChunkBundle())).not.toThrow();

  expect(ctx.error).not.toHaveBeenCalled();
  expect(ctx.info).toHaveBeenCalledTimes(1);

  // Printed on a PASS too, and that is deliberate: a gate only visible when it fails
  // teaches everyone that it does not exist, and every cap in `budget.ts` was read off
  // exactly this output.
  const printed = String(ctx.info.mock.calls[0][0]);
  expect(printed).toContain("budget satisfied");
  expect(printed).not.toContain("budget exceeded");
  expect(printed).toContain("PASS  initial-js-raw");
  // The grouping rule reported on a real placement, not on an empty expectation list.
  expect(printed).toContain('all 1 matching module(s) in chunk "vendor-react"');
  // The vacuous lazy-only row: an explicit line, never silence.
  expect(printed).toContain("PASS  lazy-only");
});

test("every failing rule is counted in the abort message, not just the first", () => {
  // A one-rule failure and an n-rule failure take the same code path, so a plugin that
  // hardcoded "1 rule(s) failed" would pass the first test above. Two caps under measured
  // here: raw and gzip.
  const plugin = bundleBudget({
    ...GENEROUS_BUDGET,
    initialJsRawBytes: 1_000,
    initialJsGzipBytes: 100,
  });
  const ctx = stubContext();

  expect(() => runGenerateBundle(plugin, ctx, twoChunkBundle())).toThrow(
    /budget exceeded: 2 rule\(s\) failed/,
  );
  expect(ctx.info).not.toHaveBeenCalled();
});

test("a grouping expectation that matches nothing aborts the build", () => {
  // The gate's own anti-vacuity rule, exercised end to end rather than only on a synthetic
  // graph: if `@xyflow/react` were removed or its paths changed, the honest outcome is a
  // failed build saying the expectation verifies nothing — not a green one.
  const plugin = bundleBudget({
    ...GENEROUS_BUDGET,
    grouping: [{ pattern: /node_modules[\\/]@xyflow[\\/]react[\\/]/, chunkName: "vendor-flow" }],
  });
  const ctx = stubContext();

  expect(() => runGenerateBundle(plugin, ctx, twoChunkBundle())).toThrow(
    /expectation verifies nothing/,
  );
  expect(ctx.info).not.toHaveBeenCalled();
});
