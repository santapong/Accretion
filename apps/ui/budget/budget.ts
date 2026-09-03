import type { Budget, GroupingExpectation } from "./evaluate.js";

/**
 * The committed bundle budget. One file, read by both `vite.config.ts` (which needs the
 * lazy-only pattern to build the `cosmic` group) and the gate (which needs all of it).
 *
 * ## The honesty rule
 *
 * Every number below carries a comment naming the PR that set it and quoting the measured
 * value it came from. A budget constant with no provenance is indistinguishable from a
 * number somebody raised to make a build go green, and the two have opposite meanings.
 *
 * Raising a cap is allowed and is sometimes correct — a real feature costs real bytes. It
 * is allowed *in the same commit as the change that needs it*, with the new measurement
 * quoted here. What is not allowed is raising it in a separate "fix the build" commit,
 * because that severs the number from its justification.
 *
 * ## Where the numbers come from
 *
 * All of them are the GATE's own measurements, printed by its own table on a passing
 * build — never Vite's CLI reporter. Vite's `gzip:` column comes from a native Rust
 * reporter whose settings are not ours; this gate uses `node:zlib` `gzipSync` at its
 * default level. The two disagree by a small margin, and mixing them would mean a cap set
 * against one number and enforced against the other.
 *
 * ## Headroom
 *
 * The four initial caps are `ceil(measured x 1.05)`. Five percent is a tight ratchet, and
 * that is the point: it catches an accidental dependency (a date library, a second icon
 * set) on the PR that adds it, while leaving room for the ordinary drift of a dependency
 * bump. It is deliberately too tight to absorb a feature silently.
 */

/**
 * Modules that may only ever be reached through a dynamic import.
 *
 * Empty of matches today, and that is the reason it is being written today. PR7 adds a
 * three.js "cosmic" background scene; a budget negotiated after that chunk exists would be
 * negotiated against whatever it happens to weigh. Committing the constraint first means
 * the scene is lazy by construction, and the gate says so out loud (`lazy-only: no
 * matching modules (vacuous until PR7)`) rather than passing in silence.
 *
 * `[\\/]` rather than `/` throughout: Rolldown's own documentation calls this out, because
 * module ids use backslashes on Windows and a `/`-only pattern silently matches nothing
 * there — the exact failure mode where a gate reports success while checking nothing.
 */
export const LAZY_ONLY_MODULES = /node_modules[\\/](three|@react-three)[\\/]|[\\/]src[\\/]cosmic[\\/]/;

/** The chunk `LAZY_ONLY_MODULES` must land in. Mirrored by the `cosmic` group in `vite.config.ts`. */
export const LAZY_ONLY_CHUNK_NAME = "cosmic";

/**
 * Grouping identity: not "how many bytes" but "which chunk".
 *
 * Byte caps cannot see a dependency migrating out of `vendor-react` and into the app
 * chunk — the total is unchanged, so every size rule stays green while long-term caching
 * quietly stops working, because the app chunk's hash changes on every commit and React's
 * used not to.
 *
 * Two probes, not five, and deliberately so: each names a package whose placement is load-
 * bearing and whose absence would be a real event. `react-dom` anchors `vendor-react`;
 * `@xyflow/react` anchors `vendor-flow`. `vendor-data` and `vendor` are catch-alls whose
 * membership is defined by exclusion, so an identity probe on them would assert a
 * tautology.
 */
export const GROUPING: readonly GroupingExpectation[] = [
  { pattern: /node_modules[\\/]react-dom[\\/]/, chunkName: "vendor-react" },
  { pattern: /node_modules[\\/]@xyflow[\\/]react[\\/]/, chunkName: "vendor-flow" },
];

/**
 * Vite's own chunk-size advisory threshold, promoted from a printed warning nobody reads
 * to a condition that fails the build.
 *
 * Flat, not measured-plus-headroom, because unlike the totals it is not a ratchet against
 * this app's own history — it is the industry advisory this repo had been printing and
 * ignoring since v0.2. M9 PR3: the pre-split build emitted a single 561,241 B chunk, so
 * this cap is the reason the split had to land in the same PR rather than being a number
 * chosen to fit what already existed.
 */
export const PER_CHUNK_RAW_BYTES = 500_000;

/**
 * M9 PR3. Gate-measured 561,138 B on the first green build after the vendor split — five
 * chunks, all in the initial closure. Cap = ceil(561,138 x 1.05).
 *
 * The pre-split build measured 561,255 B at this same hook in ONE chunk (561,241 B as
 * written to disk), so splitting moved 117 bytes, not 400 kB: this cap is the rule that
 * says so out loud. Code splitting fixes per-chunk size
 * and cache granularity and shrinks nothing, and a budget that only enforced the per-chunk
 * rule would let the initial payload double while every chunk stayed comfortably small.
 */
export const INITIAL_JS_RAW_BYTES = 589_195;

/**
 * M9 PR3. Gate-measured 166,998 B on the same build (node:zlib `gzipSync`, default level,
 * summed per chunk). Cap = ceil(166,998 x 1.05).
 *
 * Higher than the pre-split 165,490 B measured at this same hook (165,471 B for the file
 * on disk), and that is expected rather than a regression: gzip finds fewer cross-file
 * repetitions once one chunk becomes five. The 1,508 B is the price of cache granularity,
 * paid once and recorded here so nobody later reads it as drift.
 */
export const INITIAL_JS_GZIP_BYTES = 175_348;

/**
 * M9 PR3. Gate-measured 51,148 B on the same build: one stylesheet, byte-identical to the
 * pre-split build (same content hash, `index-yyjTOnqb.css`), because the chunk groups
 * deliberately skip CSS ids. Cap = ceil(51,148 x 1.05).
 */
export const INITIAL_CSS_RAW_BYTES = 53_706;

/** M9 PR3. Gate-measured 10,232 B gzip on the same build. Cap = ceil(10,232 x 1.05). */
export const INITIAL_CSS_GZIP_BYTES = 10_744;

export const BUDGET: Budget = {
  perChunkRawBytes: PER_CHUNK_RAW_BYTES,
  initialJsRawBytes: INITIAL_JS_RAW_BYTES,
  initialJsGzipBytes: INITIAL_JS_GZIP_BYTES,
  initialCssRawBytes: INITIAL_CSS_RAW_BYTES,
  initialCssGzipBytes: INITIAL_CSS_GZIP_BYTES,
  lazyOnlyModules: LAZY_ONLY_MODULES,
  lazyOnlyChunkName: LAZY_ONLY_CHUNK_NAME,
  grouping: GROUPING,
};
