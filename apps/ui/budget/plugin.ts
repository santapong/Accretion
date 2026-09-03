import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { gzipSync } from "node:zlib";
import type { Plugin } from "vite";
import { BUDGET } from "./budget.js";
import {
  evaluateBudget,
  groupDigits,
  type Budget,
  type BundleChunk,
  type BundleGraph,
  type BudgetReport,
  type BundleStylesheet,
} from "./evaluate.js";

/**
 * The bundle budget, wired into `vite build`.
 *
 * ## Why a Vite plugin and not a script
 *
 * Every CI job that builds the UI — `frontend`, `clean-checkout` and `browser` — runs
 * `vite build`, so the gate enforces itself in all three with no new CI step, and a local
 * `npm run build` fails in exactly the same way with exactly the same output. A separate
 * `scripts/` entry point would have to be remembered, wired into each job, and kept in
 * step with them; the failure mode of that design is a gate that stops running and says
 * nothing, which is worse than not having one.
 *
 * ## The one non-obvious constraint
 *
 * Rolldown's `OutputChunk` is an `ExternalMemoryHandle`: the `code` string is backed by
 * Rust-side memory that is freed once the hook returns. Everything this gate needs is
 * therefore extracted **synchronously**, at the top of `generateBundle`, before any
 * `await` can yield. `normalizeBundle` is a pure synchronous function for that reason and
 * must stay one; making it async would produce a gate that works on small builds and
 * reads freed memory on large ones.
 *
 * ## Measured at the hook, not on disk, and it is 14 bytes off
 *
 * `enforce: "post"` puts this plugin after every *user* plugin, but Vite's own
 * `vite:build-import-analysis` runs its `generateBundle` later still, and one of the
 * things it does there is replace the literal `__VITE_PRELOAD__` marker with the real
 * preload array. On the measured M9 PR3 build that happens exactly once — inside
 * react-router's lazy route loader, in `vendor-data` — turning 16 characters into 2:
 *
 *     hook: await tt(() => import(e.module), __VITE_PRELOAD__)
 *     disk: await tt(() => import(e.module), [])
 *
 * so the gate reads 561,138 B where `dist/` holds 561,124 B. Fourteen bytes, 0.0025%, and
 * it is deliberately left alone. Measuring in `writeBundle` instead would read the final
 * text but could only fail a build that had ALREADY written `dist/`, which is a worse
 * shape for a gate. The over-count is in the conservative direction, and it does not
 * distort anything, because the caps in `budget.ts` were read off this same hook: the
 * measurement and the enforcement are the same measurement.
 */

/** The gate's own name, used as the plugin id and as the prefix on every printed line. */
const PLUGIN_NAME = "accretion:bundle-budget";

/**
 * The subset of Rolldown's `OutputChunk` this gate reads.
 *
 * Declared structurally rather than imported so `plugin.test.ts` can build a bundle by
 * hand without constructing an `ExternalMemoryHandle`. Rolldown's real type is assignable
 * to it, which is what keeps the two in step: if a field is renamed upstream, the call in
 * `generateBundle` stops typechecking.
 */
export interface EmittedChunk {
  readonly type: "chunk";
  readonly fileName: string;
  readonly name: string;
  readonly isEntry: boolean;
  readonly isDynamicEntry: boolean;
  readonly code: string;
  readonly imports: readonly string[];
  readonly dynamicImports: readonly string[];
  readonly moduleIds: readonly string[];
  /**
   * Populated by Vite's CSS plugin, not by Rolldown. This is the only link between a
   * JavaScript chunk and the stylesheets its modules imported: `imports` never mentions
   * them, because by the time the bundle exists the CSS has been extracted into separate
   * assets. Hence `enforce: "post"` on the plugin below.
   */
  readonly viteMetadata?: { readonly importedCss: ReadonlySet<string> };
}

/** The subset of Rolldown's `OutputAsset` this gate reads. */
export interface EmittedAsset {
  readonly type: "asset";
  readonly fileName: string;
  readonly source: string | Uint8Array;
}

export type EmittedBundle = Readonly<Record<string, EmittedChunk | EmittedAsset>>;

/**
 * Bytes on the wire, not characters in a string.
 *
 * `code.length` counts UTF-16 code units, which undercounts every non-ASCII byte in the
 * bundle — and the bundle contains plenty, from the arrows and box-drawing characters in
 * this app's own copy to whatever a dependency embeds. Undercounting is the dangerous
 * direction: it makes a budget look satisfied by bytes that were never measured.
 */
function rawBytes(source: string | Uint8Array): number {
  return typeof source === "string" ? Buffer.byteLength(source, "utf8") : source.byteLength;
}

/**
 * Compressed bytes, at `node:zlib`'s default level.
 *
 * This deliberately does not try to reproduce Vite's own `gzip:` column, which comes from
 * a native Rust reporter with its own settings. Two measurements of the same file that
 * disagree by a few hundred bytes are fine as long as caps and enforcement come from the
 * SAME one, and they do: every number in `budget.ts` is a number this function produced.
 */
function gzipBytes(source: string | Uint8Array): number {
  return gzipSync(source).byteLength;
}

/**
 * Turn one emitted bundle into the pure graph the evaluator understands.
 *
 * Synchronous from top to bottom — see the memory note in the file header.
 */
export function normalizeBundle(bundle: EmittedBundle): BundleGraph {
  const stylesheets = new Map<string, BundleStylesheet>();
  const chunks: BundleChunk[] = [];

  // Assets first, so every chunk can resolve its stylesheets in one pass afterwards.
  for (const emitted of Object.values(bundle)) {
    if (emitted.type !== "asset") continue;
    if (!emitted.fileName.endsWith(".css")) continue;
    stylesheets.set(emitted.fileName, {
      fileName: emitted.fileName,
      bytes: rawBytes(emitted.source),
      gzipBytes: gzipBytes(emitted.source),
    });
  }

  for (const emitted of Object.values(bundle)) {
    if (emitted.type !== "chunk") continue;

    // An `importedCss` entry with no matching asset would mean Vite named a stylesheet the
    // bundle does not contain. Silently dropping it would understate the CSS total, so the
    // unresolved name is kept with zero bytes and shows up in the printed closure instead.
    const css = [...(emitted.viteMetadata?.importedCss ?? [])].map(
      (fileName) => stylesheets.get(fileName) ?? { fileName, bytes: 0, gzipBytes: 0 },
    );

    chunks.push({
      fileName: emitted.fileName,
      name: emitted.name,
      isEntry: emitted.isEntry,
      isDynamicEntry: emitted.isDynamicEntry,
      bytes: rawBytes(emitted.code),
      gzipBytes: gzipBytes(emitted.code),
      imports: [...emitted.imports],
      dynamicImports: [...emitted.dynamicImports],
      // Copied, not aliased: the source array belongs to memory that is about to be freed.
      moduleIds: [...emitted.moduleIds],
      css,
    });
  }

  return { chunks };
}

function padLeft(value: string, width: number): string {
  return value.length >= width ? value : " ".repeat(width - value.length) + value;
}

function padRight(value: string, width: number): string {
  return value.length >= width ? value : value + " ".repeat(width - value.length);
}

/**
 * The table.
 *
 * Printed on every build, passing or failing, and printing it on a PASS is the point: a
 * gate that is only visible when it fails teaches everyone that it does not exist, and the
 * numbers in `budget.ts` came from reading exactly this output. Chunks are listed in file
 * name order and rules in evaluation order, both deterministic, so two builds of the same
 * tree produce byte-identical text and a diff of two build logs means something.
 */
export function formatReport(graph: BundleGraph, report: BudgetReport): string {
  const lines: string[] = [];
  const initial = new Set(report.initial);

  const chunks = [...graph.chunks].sort((a, b) => (a.fileName < b.fileName ? -1 : 1));
  const nameWidth = Math.max(12, ...chunks.map((chunk) => chunk.fileName.length));

  lines.push("bundle budget");
  lines.push("");
  lines.push(
    `  ${padRight("chunk", nameWidth)}  ${padLeft("raw B", 10)}  ${padLeft("gzip B", 10)}  load`,
  );
  for (const chunk of chunks) {
    lines.push(
      `  ${padRight(chunk.fileName, nameWidth)}  ${padLeft(groupDigits(chunk.bytes), 10)}  ` +
        `${padLeft(groupDigits(chunk.gzipBytes), 10)}  ${initial.has(chunk.fileName) ? "initial" : "lazy"}`,
    );
  }

  // Deduplicated the same way the evaluator deduplicates, and only for the initial
  // closure, so the printed stylesheet list adds up to the printed CSS totals.
  const sheets = new Map<string, BundleStylesheet>();
  for (const chunk of chunks) {
    if (!initial.has(chunk.fileName)) continue;
    for (const sheet of chunk.css) if (!sheets.has(sheet.fileName)) sheets.set(sheet.fileName, sheet);
  }
  for (const sheet of [...sheets.values()].sort((a, b) => (a.fileName < b.fileName ? -1 : 1))) {
    lines.push(
      `  ${padRight(sheet.fileName, nameWidth)}  ${padLeft(groupDigits(sheet.bytes), 10)}  ` +
        `${padLeft(groupDigits(sheet.gzipBytes), 10)}  initial`,
    );
  }

  lines.push("");
  lines.push(
    `  initial JS  ${groupDigits(report.totals.initialJsRaw)} B raw / ` +
      `${groupDigits(report.totals.initialJsGzip)} B gzip` +
      `   initial CSS  ${groupDigits(report.totals.initialCssRaw)} B raw / ` +
      `${groupDigits(report.totals.initialCssGzip)} B gzip`,
  );
  lines.push("");

  const ruleWidth = Math.max(12, ...report.rules.map((rule) => rule.id.length));
  for (const rule of report.rules) {
    lines.push(`  ${rule.pass ? "PASS" : "FAIL"}  ${padRight(rule.id, ruleWidth)}  ${rule.detail}`);
  }

  lines.push("");
  lines.push(
    report.pass
      ? "  budget satisfied"
      : `  budget exceeded: ${report.rules.filter((rule) => !rule.pass).length} rule(s) failed`,
  );

  return lines.join("\n");
}

/**
 * Fail the build over the bundle budget, and print the numbers either way.
 *
 * `enforce: "post"` puts this after Vite's CSS plugin, which is what populates
 * `viteMetadata.importedCss`; without it the stylesheet totals would be zero and the two
 * CSS rules would pass vacuously — a gate reporting success while measuring nothing.
 *
 * `apply: "build"` because there is no bundle to measure in dev.
 */
export function bundleBudget(budget: Budget = BUDGET): Plugin {
  let outcome: { graph: BundleGraph; report: BudgetReport } | null = null;

  return {
    name: PLUGIN_NAME,
    enforce: "post",
    apply: "build",

    generateBundle(_options, bundle) {
      // Synchronous extraction, first statement, before anything can await. The chunk
      // objects are handles onto Rust-owned memory freed when this hook returns.
      const graph = normalizeBundle(bundle);
      const report = evaluateBudget(graph, budget);
      outcome = { graph, report };

      const table = formatReport(graph, report);
      if (report.pass) {
        this.info(`\n${table}\n`);
        return;
      }

      // `this.error` throws, which aborts the build with the plugin name attached and
      // stops `writeBundle` from ever running. The whole table goes into the message so a
      // CI log shows what was measured, not only what broke.
      this.error(
        `${table}\n\n` +
          `The bundle budget in apps/ui/budget/budget.ts was exceeded. If the growth is\n` +
          `intended, raise the cap IN THIS COMMIT and quote the new measured number in the\n` +
          `comment above it. Do not raise it in a separate commit to make CI green.`,
      );
    },

    async writeBundle(options) {
      // Only reached on a passing build, since `this.error` above throws. The JSON is the
      // machine-readable twin of the printed table, for evidence collection and for a
      // future PR that wants to diff two builds; it is written into the gitignored `dist/`
      // and is never an input to the gate's own decision.
      if (outcome === null) return;
      const directory = options.dir ?? "dist";
      await writeFile(
        join(directory, "bundle-report.json"),
        `${JSON.stringify({ ...outcome.report, chunks: outcome.graph.chunks }, null, 2)}\n`,
        "utf8",
      );
    },
  };
}
