import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  computedStyleProbe,
  focusStateProbe,
  hoveredStyleProbe,
  HOVER_SELECTORS,
  layoutSettledProbe,
} from "./audit";
import { ACR_ARCH_TASK_ID, benchmarkFixtureFor } from "./fixtures/benchmarks";
import { planningFixtureFor } from "./fixtures/planning";
import { RUN_ID as FIXTURE_RUN_ID, runFixtureFor } from "./fixtures/run";
import { SEED_FILE, type Seed } from "./global-setup";
import { openRoute } from "./navigate";
import { ROUTES, RUN_ID_PLACEHOLDER, type RouteUnderTest } from "./routes";
import {
  diffStyles,
  fingerprintsEqual,
  formatDifference,
  nextRetryDelayMs,
  type ElementCapture,
} from "./styleDiff";

/**
 * The computed-style diff: proof that a stylesheet migration changed no rendering.
 *
 * PR3 and PR4 proved "zero visual change" with a sha256 of the built stylesheet. The
 * Tailwind port changes that file's text by design, so that proof retires here and this one
 * replaces it: open the same route at the same width against the BRANCH build on :4173 and
 * the PRE-MIGRATION BASE build on :4174, measure every element's computed styles in both,
 * and require the two to be identical.
 *
 * One backend and one seed serve both origins, so the only variable between the two
 * measurements is the CSS. `styleDiff.ts` owns every comparison decision and is unit-tested
 * against a mutation table in `styleDiff.test.ts`; nothing here decides what counts as a
 * difference.
 *
 * ## Widths
 *
 * `styles.css` carries six `@media` blocks at 1100, 900, 720 and 620 px. The five widths
 * below put one measurement inside every interval those breakpoints carve - >1100,
 * 900-1100, 720-900, 620-720, <620 - so no rule is measured only on the side of a
 * breakpoint where it does not apply. 1440 and 390 are the widths the v0.3 contrast and
 * overflow evidence used, kept so the two documents describe the same viewports.
 *
 * ## Retry
 *
 * Only on a STRUCTURAL mismatch, never on a style difference. The app polls and holds an
 * open event stream, so the two builds can legitimately be measured either side of a
 * refetch; that is a fingerprint mismatch and it is worth re-measuring. A style difference
 * is the finding, and re-running until it disappears would be the purest form of a gate
 * that checks nothing.
 */

const seed = JSON.parse(readFileSync(SEED_FILE, "utf8")) as Seed;

const BRANCH_ORIGIN = "http://localhost:4173";
const BASE_ORIGIN = "http://localhost:4174";
const WIDTHS = [1440, 1000, 800, 660, 390] as const;
const VIEWPORT_HEIGHT = 1000;

/**
 * The fewest elements the focus half of the interaction pass may capture on a route.
 *
 * Every route renders the operator nav: the brand link plus one link per labelled route,
 * sixteen `a[href]` today, all matched by `FOCUSABLE_SELECTOR`. A capture below that means
 * the selector matched nothing - measured once while this gate was audited: with
 * `FOCUSABLE_SELECTOR` set to a selector no element carries, the pass still reported
 * "0 differences" over zero captures on all seventeen routes. PR9b adds a nav entry and
 * bumps this to seventeen.
 */
const NAV_FOCUS_FLOOR = 16;

const BASE_DIST = process.env.STYLE_DIFF_BASE_DIST;
/** Where `npm run preview` serves :4173 from - Vite's default `outDir` for this workspace. */
const BRANCH_DIST = resolve(dirname(fileURLToPath(import.meta.url)), "../dist");

/* ------------------------------------------------------------------------------------- */
/* Element floors: how many elements each route must actually have rendered.                */
/* ------------------------------------------------------------------------------------- */

/**
 * The smallest number of elements a route may render before the measurement is worthless.
 *
 * `differences.toEqual([])` on its own is satisfied by two equally empty pages. Demonstrated:
 * Postgres died mid-suite while this gate was being written and sixteen of the seventeen
 * route tests reported green over error placeholders - `/` measured 52 elements instead of
 * 117, `/benchmarks/acr-arch` 78 instead of 1046, `/runtimes` 33 instead of 66. Both builds
 * failed the same way at the same moment, so the diff between them really was empty. Only
 * `/runs/:runId` failed, and only because it alone carried a content barrier.
 *
 * `openRoute` now asserts the route's own `h1`, which catches a page that rendered the wrong
 * ROUTE. This catches the other half: the right route with none of its content in it. The
 * two together are why a green run means something.
 *
 * ## Where the numbers come from, and why a floor rather than an equality
 *
 * Every value below is a MEASURED count from a passing sweep, and the check is
 * `>=` because these pages are allowed to grow: the port under review adds no markup, but
 * PR7 and PR9 will, and a gate that has to be re-pinned on every markup change is a gate
 * that gets re-pinned without being read. Too low to notice a page losing half its content
 * is the failure mode this guards against, so the floors are the measurement itself for
 * routes whose content does not depend on how many times the seeder has run.
 *
 * ## The three routes that are not pinned to their measurement
 *
 * `examples/showcase.py` is ADDITIVE (`global-setup.ts` says so): every invocation creates
 * another project, task, run and runtime session. Routes that list those rows therefore
 * measure larger on a developer's database than on CI's throwaway one - `/` and `/history`
 * list runs, `/tasks/new` lists projects in its select. Measured both ways while pinning
 * these: on a database seeded ONCE, as CI's is, `/` is 117, `/tasks/new` 87 and `/history`
 * 60; on a local database seeded seven times the same three are 153, 93 and 90. Pinning them
 * to either number is wrong, so their floors are set under the single-seeding count.
 *
 * `/runtimes` was the fourth, because it lists runtime sessions. It is not any more: the
 * sessions it lists now come from `RUNTIME_SESSIONS_FIXTURE` on both builds, which is what
 * makes its floor an equality-strength measurement rather than a margin under one.
 *
 * Two of them USED to get a further margin because they render live runtime health, which
 * differs between a developer's machine and CI. `stubRuntimeHealth` below now answers both
 * runtime endpoints from a fixture on every route, so `/` and `/runtimes` no longer depend
 * on which vendor CLIs are installed - or on whether a probe answered in time. `/runtimes`
 * loses its seeder dependency entirely with it, because the sessions it lists are the
 * fixture's; `/` keeps one, because it also lists runs.
 *
 * `/tasks/new` and `/history` have no health dependency and never did - the task form is
 * static apart from one `<option>` per project, and a history row reads the run's stored
 * audit, not a probe - so those two are floored at "the chrome plus the first row", which is
 * the tightest statement that stays true on a one-run database.
 */
const ROUTE_ELEMENT_FLOOR: Record<string, number> = {
  // Additive (the run list) but no longer health-dependent: conservative, well under the 117
  // above, still well over the 52 the collapsed-backend run produced.
  "/": 90,
  // Seed-independent since `stubRuntimeHealth`: four fixture runtimes, each with a fixed
  // session list, so this is the measurement itself rather than a margin under it. Measured
  // 70 at all five widths on the sweep that took this PR green, against 66 for the live
  // probe before it.
  "/runtimes": 70,
  // Additive only, and exact on a single-seeding database: the task form is 86 static
  // elements plus one `<option>` per project, and the history panel is 55 static elements
  // plus 5 per run row. Nothing can legitimately make either SMALLER, so there is no
  // downward margin to leave - and without the last element these would pass with a failed
  // project fetch or an empty run list, which is the state worth catching.
  "/tasks/new": 87,
  "/history": 60,
  // Seed-independent: the floor is the measured count.
  [`/runs/${RUN_ID_PLACEHOLDER}`]: 399,
  "/approvals": 34,
  "/capabilities": 56,
  "/admin/connections": 64,
  "/admin/plugins": 52,
  "/admin/mcp": 47,
  "/admin/capabilities/inspect": 48,
  "/admin/identity": 81,
  "/benchmarks/acr-arch": 1046,
  "/benchmarks/dynamic": 118,
  "/benchmarks/search": 204,
  "/benchmarks/experience": 136,
  "/definitely-not-a-route": 30,
};

/** The mocked run page, whose content comes from `fixtures/run.ts` and never from the seed. */
const MOCKED_RUN_ELEMENT_FLOOR = 620;
/** The mocked planning review, rendered only after the task form is submitted. */
const MOCKED_PLANNING_ELEMENT_FLOOR = 212;

/**
 * A route with no floor is a hole, not a pass.
 *
 * Adding a row to `routes.ts` without a measurement here would otherwise give the new route
 * a silent exemption from the only check that notices an empty page.
 */
function elementFloor(path: string): number {
  const floor = ROUTE_ELEMENT_FLOOR[path];
  if (floor === undefined) {
    throw new Error(
      `${path} is in routes.ts but has no entry in ROUTE_ELEMENT_FLOOR. Measure it on a ` +
        "passing sweep and add it; without a floor the route can render nothing and still " +
        "diff clean against a base that renders nothing.",
    );
  }
  return floor;
}

/** The assertion itself, shared by the sweep and both fixture-mocked passes. */
function assertAboveFloor(label: string, width: number, elements: number, floor: number): void {
  expect(
    elements,
    `${label} @ ${width} rendered ${elements} elements, below the floor of ${floor}; the ` +
      "page is an error or empty state, not the seeded content, and a diff over it proves " +
      "nothing",
  ).toBeGreaterThanOrEqual(floor);
}

const RECIPE = [
  "STYLE_DIFF_BASE_DIST is not set, so there is no base build to compare against.",
  "",
  "  make style-diff-base           # builds the merge-base into .style-diff-base-dist",
  "  export STYLE_DIFF_BASE_DIST=<the path it prints>",
  "  cd apps/ui && npx playwright test e2e/style-diff.spec.ts",
].join("\n");

/**
 * Env-gated specs become gates that never run. This one cannot.
 *
 * In CI the variable is set by the `browser` job, which builds the merge-base before the
 * Playwright step. If it is ever missing there - a renamed step, a reordered job, a
 * `continue-on-error` - these tests FAIL rather than skip, because a skipped required check
 * still reports green and this is the only evidence that the port preserved rendering.
 *
 * Locally it skips and prints the recipe, so a developer running the a11y gate is not
 * forced to build two copies of the app first.
 */
test.beforeEach(async ({ page }) => {
  // A release bridge (a PR whose base is `main`, or `main` itself) has no meaningful
  // merge-base with develop - `git merge-base main develop` is the repository's initial
  // commit - and nothing new to prove: it is content promotion. CI sets STYLE_DIFF_SKIP
  // there and only there; the a11y gate still runs.
  if (process.env.STYLE_DIFF_SKIP) {
    test.skip(true, `computed-style diff skipped: ${process.env.STYLE_DIFF_SKIP}`);
  }
  if (!BASE_DIST) {
    if (process.env.CI) {
      throw new Error(
        "STYLE_DIFF_BASE_DIST is required in CI: the computed-style diff is the only " +
          "evidence that the Tailwind port changed no rendering, and skipping it reports " +
          "green while measuring nothing.\n\n" +
          RECIPE,
      );
    }
    test.skip(true, RECIPE);
  }
  await stubWebFonts(page);
  await stubRuntimeHealth(page);
});

/* ------------------------------------------------------------------------------------- */
/* The origin/build binding: proof that each port serves the build it is named after.      */
/* ------------------------------------------------------------------------------------- */

/**
 * The stylesheet filename a built `index.html` points at.
 *
 * Vite content-hashes it, so `assets/index-<hash>.css` is a fingerprint of the CSS that
 * build emitted: one changed byte gives a different name, and two builds with identical
 * stylesheets give the same one. That makes it the right thing to compare a served page
 * against, and the wrong thing to compare the two BUILDS against - on `develop` the
 * merge-base is `HEAD`, the two builds are legitimately identical, and their hashes match.
 */
function builtStylesheet(html: string, source: string): string {
  const match = html.match(/<link[^>]*rel="stylesheet"[^>]*href="([^"]*\.css)"/);
  if (!match) {
    throw new Error(
      `${source} references no stylesheet at all. Either the build did not run or it ` +
        "stopped emitting CSS; in both cases the computed-style diff would measure an " +
        "unstyled page and report it as identical.",
    );
  }
  return match[1];
}

/**
 * Bind one origin to one directory, or fail before a single style is measured.
 *
 * Without this the spec trusts a port number. `reuseExistingServer: !process.env.CI` accepts
 * whatever answers on :4173 and :4174, and a second concurrent run - or a stale
 * `vite preview` left behind by an interrupted one - can be holding either. Demonstrated
 * while this gate was under review: a foreign preview of the BRANCH build was started on
 * :4174 while `STYLE_DIFF_BASE_DIST` still pointed at a genuine base build that was never
 * served, and the suite reported `2 passed`. It had compared the branch with itself.
 *
 * The read is from disk on one side and over HTTP on the other, so it is exactly the
 * question that matters: is the bytes-on-disk build the one answering on this port?
 *
 * It stays honest on `develop`, where the merge-base is `HEAD` and the two builds emit the
 * same hash: each origin is still checked against its OWN directory, so a base-build step
 * that silently produced nothing, or produced the wrong tree, fails here rather than
 * passing as a trivially empty diff.
 */
async function assertOriginServes(origin: string, dist: string, label: string): Promise<void> {
  const onDisk = builtStylesheet(readFileSync(resolve(dist, "index.html"), "utf8"), dist);

  const response = await fetch(new URL("/", origin));
  expect(response.ok, `${origin} answered ${response.status} for /`).toBe(true);
  const served = builtStylesheet(await response.text(), `the page served by ${origin}`);

  expect(
    served,
    `${origin} is not serving ${label} (${dist}): that build emitted ${onDisk} but the ` +
      `origin serves ${served}. A stale or foreign preview is holding the port, so the ` +
      "diff would compare a build with itself and report zero differences having measured " +
      "nothing.",
  ).toBe(onDisk);

  console.log(`style-diff origin ${origin} serves ${label} (${served})`);
}

/**
 * Run before every test in the file, including the fixture-mocked passes.
 *
 * A `beforeAll` rather than a test of its own on purpose: a failing test still lets the rest
 * of the file run and report green, and the whole point is that nothing here may measure
 * anything until both origins are known to be the builds they claim to be.
 */
test.beforeAll(async () => {
  if (!BASE_DIST || process.env.STYLE_DIFF_SKIP) return;
  if (!BASE_DIST) return;
  await assertOriginServes(BRANCH_ORIGIN, BRANCH_DIST, "the branch build (apps/ui/dist)");
  await assertOriginServes(BASE_ORIGIN, BASE_DIST, "STYLE_DIFF_BASE_DIST");
});

/**
 * Answer the Google Fonts request locally, identically on both origins.
 *
 * Measured before this was added: `waitForLoadState("networkidle")` took 15-19 SECONDS per
 * navigation because the request to `fonts.googleapis.com` sat pending until the browser
 * gave up on it. At ten navigations per route that is fifty minutes of sweep, and worse, it
 * makes every measurement depend on when a third-party CDN happens to answer - the branch
 * build could be measured with the web font applied and the base build with the fallback
 * stack, and the resulting page of text-metric differences would be real, reproducible and
 * entirely about the network.
 *
 * So both builds are given the same empty stylesheet and both render with the fallback
 * stack. What this costs is stated plainly: the sweep measures the app WITHOUT DM Mono and
 * Manrope resolved. It is not blind to a font change - `fontFamily` is captured on every
 * element, so an altered stack is reported like any other property - but it does not
 * compare glyph metrics. The declaration itself is checked structurally by the `@import`
 * test below and textually by `cssPort.test.ts`.
 *
 * Aborting instead of fulfilling would work equally well for speed and was rejected: the
 * `@import` in the built sheet still parses into a `CSSImportRule` either way, but a 200
 * keeps the page's network log free of failures that a reader would have to triage.
 */
async function stubWebFonts(page: Page): Promise<void> {
  for (const pattern of ["**://fonts.googleapis.com/**", "**://fonts.gstatic.com/**"]) {
    await page.route(pattern, (route) =>
      route.fulfill({ status: 200, contentType: "text/css", body: "" }),
    );
  }
}

/* ------------------------------------------------------------------------------------- */
/* Runtime health, which is the one thing on these pages the backend cannot answer twice.  */
/* ------------------------------------------------------------------------------------- */

/**
 * The four runtimes `/` and `/runtimes` render, one per pill colour the sheet declares.
 *
 * `RuntimeStatus` has six members and `styles.css` groups them into four rules: READY is
 * green (`:68`), UNAVAILABLE and AUTH_REQUIRED red, BUSY amber-yellow, DEGRADED orange.
 * RATE_LIMITED has no rule of its own and falls back to the base `.pill`. The four below
 * therefore put every `.pill-*` group that a runtime can produce in front of the probe at
 * every width - which the live probe never did, because it reported whatever the machine's
 * CLIs happened to say.
 */
const RUNTIME_HEALTH_FIXTURE = [
  {
    runtime_id: "runtime_fake",
    provider: "FAKE",
    status: "READY",
    auth_mode: "LOCAL",
    runtime_version: "fake-1.0.0",
    capabilities: ["structured-events", "repeatable-calls", "interrupt"],
    active_sessions: 2,
    active_runs: 1,
    observed_usage_pressure: "LOW",
    last_error: null,
  },
  {
    runtime_id: "runtime_codex",
    provider: "CODEX",
    status: "BUSY",
    auth_mode: "SUBSCRIPTION",
    runtime_version: "codex-cli 0.148.0",
    capabilities: ["app-server", "session-resume"],
    active_sessions: 1,
    active_runs: 3,
    observed_usage_pressure: "MEDIUM",
    last_error: null,
  },
  {
    runtime_id: "runtime_claude",
    provider: "CLAUDE",
    status: "DEGRADED",
    auth_mode: "SUBSCRIPTION",
    runtime_version: "2.1.260 (Claude Code)",
    capabilities: ["stream-json", "session-resume"],
    active_sessions: 0,
    active_runs: 0,
    observed_usage_pressure: "HIGH",
    last_error: {
      code: "CLAUDE_DEGRADED",
      message: "version outside the supported window",
      retryable: false,
    },
  },
  {
    runtime_id: "runtime_opencode",
    provider: "OPENCODE",
    status: "UNAVAILABLE",
    auth_mode: "API",
    runtime_version: "unknown",
    capabilities: [],
    active_sessions: 0,
    active_runs: 0,
    observed_usage_pressure: "UNKNOWN",
    last_error: { code: "OPENCODE_UNAVAILABLE", message: "command not found", retryable: false },
  },
] as const;

/**
 * The sessions each fixture runtime lists, covering both branches of the list markup.
 *
 * `RuntimeMonitorPage` renders one `<li>` per session and a single "No persisted sessions."
 * `<li>` when there are none, and a session with no `native_session_id` renders "pending
 * native session" instead of a short id. All three shapes are here, so the sweep measures
 * markup the seeded database does not reliably produce.
 */
const RUNTIME_SESSIONS_FIXTURE: Record<string, readonly unknown[]> = {
  runtime_fake: [
    {
      session_id: "session_20260101T000000_aaaaaaaa",
      run_id: "run_20260101T000000_bbbbbbbb",
      provider: "FAKE",
      native_session_id: "fake-run_20260101T000000_bbbbbbbb",
      workspace: "/tmp/accretion/style-diff/fake",
    },
    {
      session_id: "session_20260101T000001_cccccccc",
      run_id: "run_20260101T000001_dddddddd",
      provider: "FAKE",
      native_session_id: null,
      workspace: "/tmp/accretion/style-diff/fake",
    },
  ],
  runtime_codex: [
    {
      session_id: "session_20260101T000002_eeeeeeee",
      run_id: "run_20260101T000002_ffffffff",
      provider: "CODEX",
      native_session_id: "codex-0f0f0f0f",
      workspace: "/tmp/accretion/style-diff/codex",
    },
  ],
  runtime_claude: [],
  runtime_opencode: [],
};

/**
 * Answer both runtime endpoints from the fixtures above, on every route, on both origins.
 *
 * The reason is a finding, not a convenience. `/` and `/runtimes` render `<StatePill>` from
 * a LIVE probe: the backend shells out to `codex`, `claude` and `opencode` on every request
 * to `/api/v1/runtimes`, and `runtime_sessions` used to re-probe every runtime just to
 * resolve an id. On this machine `opencode --version` takes 1.9-2.6 s, so under the load of
 * the sweep itself those probes hit their own five-second deadline and reported UNAVAILABLE
 * or DEGRADED for a CLI that was merely slow. The diff measured the branch build and the
 * base build seconds apart and correctly reported the difference:
 *
 *   /runtimes @ 1440: #56 span color rgb(143,225,166) -> rgb(232,186,119), width 75.7 -> 63.2
 *   /          @ 660: #93 span color rgb(242,168,160) -> rgb(232,186,119)
 *
 * That is a real rendering difference and a completely false finding about the stylesheet,
 * which is the worst thing a gate can produce. `src/accretion/runtimes/common.py` now caches
 * a probe for thirty seconds and no longer crashes when it loses the kill race, and that
 * fixes the latency; it cannot make the answer identical across two measurements, because a
 * cache boundary can still fall between them. Only a fixture can.
 *
 * What it costs is stated plainly: the sweep no longer measures whatever status the local
 * machine's CLIs report. It measures four fixed ones covering every pill colour a runtime
 * can produce, which is strictly more of the stylesheet than the live probe ever reached -
 * and `/runtimes` becomes seed-independent, so its element floor is now the measurement
 * rather than a margin under it.
 *
 * Registered in `beforeEach`, so `mockApi`'s `**\/api\/**` handler - added later, inside the
 * fixture-mocked tests - still takes precedence there and those pages stay entirely under
 * their own fixtures.
 */
async function stubRuntimeHealth(page: Page): Promise<void> {
  // Checked here rather than in a test of its own, so it fails on the first route of the
  // sweep instead of after it. A runtime with no session list renders "No persisted
  // sessions." on both builds and diffs clean over content that was never fetched, and a
  // fixture that dropped a status would quietly stop measuring one of the four pill rules.
  expect(
    RUNTIME_HEALTH_FIXTURE.map((runtime) => runtime.runtime_id).filter(
      (id) => !(id in RUNTIME_SESSIONS_FIXTURE),
    ),
    "every runtime in RUNTIME_HEALTH_FIXTURE needs an entry in RUNTIME_SESSIONS_FIXTURE",
  ).toEqual([]);
  expect(
    new Set(RUNTIME_HEALTH_FIXTURE.map((runtime) => runtime.status)).size,
    "the runtime fixture must carry one status per `.pill-*` group the sheet declares - " +
      "READY (green), BUSY (yellow), DEGRADED (orange) and UNAVAILABLE (red) - or the " +
      "sweep stops measuring one of them",
  ).toBe(RUNTIME_HEALTH_FIXTURE.length);

  await page.route("**/api/v1/runtimes", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(RUNTIME_HEALTH_FIXTURE),
    }),
  );
  await page.route("**/api/v1/runtimes/*/sessions", (route) => {
    const runtimeId = new URL(route.request().url()).pathname.split("/").at(-2) ?? "";
    const sessions = RUNTIME_SESSIONS_FIXTURE[runtimeId];
    if (sessions === undefined) {
      // A runtime id the fixture does not know means the health fixture and this one have
      // drifted apart, and the page would render an empty list that still diffs clean.
      return route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ message: `no session fixture for ${runtimeId}` }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(sessions),
    });
  });
}

/** Set the viewport, open the route on one origin, and capture every element's styles. */
async function measure(
  page: Page,
  route: RouteUnderTest,
  width: number,
  origin: string,
): Promise<ElementCapture[]> {
  await page.setViewportSize({ width, height: VIEWPORT_HEIGHT });
  await openRoute(page, route, seed, origin);
  // Belt and braces alongside `stubWebFonts`: the stub means no face is ever loading, so
  // this resolves immediately, but it costs nothing and it is the barrier that keeps the
  // measurement honest if the stub is ever removed.
  await page.evaluate(() => document.fonts.ready);
  await settle(page, route, width, origin);
  return (await page.evaluate(computedStyleProbe())) as ElementCapture[];
}

/**
 * Wait for the layout to stop moving, and say so out loud if it never does.
 *
 * An unsettled page is measurable and produces differences that reverse direction between
 * runs, which is worse than a failure because it looks like a finding. Reaching the frame
 * cap is therefore reported rather than swallowed: the measurement still happens, and the
 * log names the route so a reader can tell a real difference from a page that was still
 * moving when it was read.
 */
async function settle(page: Page, route: RouteUnderTest, width: number, origin: string) {
  const result = (await page.evaluate(layoutSettledProbe())) as {
    frames: number;
    settled: boolean;
  };
  if (!result.settled) {
    console.log(
      `style-diff WARNING ${route.path} @ ${width} on ${origin}: layout still moving after ` +
        `${result.frames} frames; the capture below may be mid-reflow`,
    );
  }
}

/**
 * Measure one route/width against both builds and return the differences.
 *
 * Branch first, then base, back to back, so a poll landing between them lands in the
 * smallest possible window. Throws only when the structure still disagrees after the retry
 * budget: that is not a style finding and must not be reported as one.
 */
async function compare(
  page: Page,
  route: RouteUnderTest,
  width: number,
): Promise<{ differences: string[]; elements: number }> {
  for (let attempt = 1; ; attempt += 1) {
    const branch = await measure(page, route, width, BRANCH_ORIGIN);
    const base = await measure(page, route, width, BASE_ORIGIN);

    if (fingerprintsEqual(base, branch)) {
      return {
        differences: diffStyles(base, branch).map((difference) =>
          formatDifference(route.path, width, difference),
        ),
        elements: branch.length,
      };
    }

    const delay = nextRetryDelayMs(attempt);
    if (delay === null) {
      const at = base.findIndex(
        (element, index) =>
          element.tag !== branch[index]?.tag || element.children !== branch[index]?.children,
      );
      throw new Error(
        `${route.path} @ ${width}: the two builds rendered different DOM after ${attempt} ` +
          `attempts (base ${base.length} elements, branch ${branch.length}; first ` +
          `divergence at #${at}). The style diff cannot align captures of different shape, ` +
          "and this is a rendering difference in its own right.",
      );
    }
    await page.waitForTimeout(delay);
  }
}

/* ------------------------------------------------------------------------------------- */
/* The sweep: every route, every width.                                                    */
/* ------------------------------------------------------------------------------------- */

test.describe("computed-style diff against the pre-migration build", () => {
  // Ten navigations and ten full-page probes per test. The 120 s default in
  // `playwright.config.ts` is sized for one audit per route and is not enough here.
  test.describe.configure({ timeout: 600_000 });

  for (const route of ROUTES) {
    test(`${route.path} renders identically at 1440, 1000, 800, 660 and 390px`, async ({
      page,
    }) => {
      const floor = elementFloor(route.path);
      const differences: string[] = [];
      const counts: string[] = [];
      const shortfalls: (() => void)[] = [];
      for (const width of WIDTHS) {
        const result = await compare(page, route, width);
        differences.push(...result.differences);
        counts.push(`${width}px: ${result.elements}`);
        // Collected rather than thrown mid-loop so the count line below is printed for
        // every width even when one of them is short - the shape of the shortfall (all
        // five widths, or one) is what tells a reader whether the backend died or a
        // breakpoint dropped content.
        shortfalls.push(() => assertAboveFloor(route.path, width, result.elements, floor));
      }
      // Printed on success as well as failure: "0 diffs" is only meaningful next to the
      // number of elements it was 0 out of.
      console.log(`style-diff ${route.path} — ${counts.join(", ")} elements (floor ${floor})`);
      for (const assertShortfall of shortfalls) assertShortfall();
      expect(differences, differences.join("\n")).toEqual([]);
    });
  }

  /**
   * Focus and hover, which a load-and-measure sweep never reaches.
   *
   * Seven rules in `styles.css` only apply under interaction (:21, :25, :31, :42-43, :76,
   * :94, :304), and four of them are the focus rings the v0.2 accessibility findings turned
   * into real fixes. None has a `@media` entry, so this runs at one width.
   *
   * `HOVER_SELECTORS` names a target for every `:hover` rule the pinned sheet declares -
   * `cssPort.test.ts` fails if it does not. A target no route renders is skipped below, so
   * the selectors this sweep never reached are PRINTED at the end: "0 diffs" over a hover
   * rule that was never triggered is not evidence, and a reader has to be able to tell the
   * two apart.
   */
  test("focus and hover states render identically", async ({ page }) => {
    const differences: string[] = [];
    const reached = new Set<string>();
    let focusedTotal = 0;
    let hoveredTotal = 0;

    for (const route of ROUTES) {
      const captures: Record<string, ElementCapture[]> = {};
      for (const origin of [BRANCH_ORIGIN, BASE_ORIGIN]) {
        await page.setViewportSize({ width: 1440, height: VIEWPORT_HEIGHT });
        await openRoute(page, route, seed, origin);
        await page.evaluate(() => document.fonts.ready);
        await settle(page, route, 1440, origin);

        const focused = (await page.evaluate(focusStateProbe())) as ElementCapture[];
        expect(
          focused.length,
          `${route.path} (${origin}): the focus pass captured ${focused.length} elements, ` +
            `below the ${NAV_FOCUS_FLOOR} the nav alone provides - FOCUSABLE_SELECTOR ` +
            "matched nothing and the pass would be green over zero measurements",
        ).toBeGreaterThanOrEqual(NAV_FOCUS_FLOOR);
        focusedTotal += focused.length;
        const hovered: ElementCapture[] = [];
        for (const selector of HOVER_SELECTORS) {
          const target = page.locator(selector).first();
          if ((await target.count()) === 0) continue;
          reached.add(selector);
          // `:hover` needs a real pointer; it cannot be simulated from inside the page.
          await target.hover({ trial: false }).catch(() => undefined);
          const capture = (await page.evaluate(
            hoveredStyleProbe(selector),
          )) as ElementCapture | null;
          if (capture) hovered.push(capture);
        }
        hoveredTotal += hovered.length;
        captures[origin] = [...focused, ...hovered];
      }

      const base = captures[BASE_ORIGIN];
      const branch = captures[BRANCH_ORIGIN];
      if (!fingerprintsEqual(base, branch)) {
        differences.push(
          `${route.path}: the interaction pass found ${base.length} targets on the base ` +
            `build and ${branch.length} on the branch`,
        );
        continue;
      }
      differences.push(
        ...diffStyles(base, branch).map((difference) =>
          formatDifference(`${route.path} (interaction)`, 1440, difference),
        ),
      );
    }

    const unreached = HOVER_SELECTORS.filter((selector) => !reached.has(selector));
    console.log(
      `style-diff interaction pass — focused ${focusedTotal} elements and hovered ` +
        `${hoveredTotal} across both builds; hovered ${reached.size}/${HOVER_SELECTORS.length} ` +
        `targets across ${ROUTES.length} routes` +
        (unreached.length ? `; rendered nowhere: ${unreached.join(", ")}` : ""),
    );
    expect(differences, differences.join("\n")).toEqual([]);
  });

  /**
   * The web font import, which computed styles cannot see.
   *
   * `font-family` reads back the DECLARED stack whether or not a face ever loaded, so
   * deleting the Google Fonts `@import` changes every rendered glyph and not one value this
   * probe records. The real guard is the parsed stylesheet: `@import` is dropped by the CSS
   * parser unless it precedes every rule other than `@charset` and `@layer` statements, so
   * a `CSSImportRule` at index 0 of the built sheet proves both that the declaration
   * survived the port AND that the bundler kept it somewhere the browser honours.
   *
   * `document.fonts.check` is reported beside it and deliberately NOT the guard. Measured
   * while writing this: it returns `true` for `12px "DM Mono"` on a page with no font
   * import at all, because the spec's algorithm succeeds when no face matches. An assertion
   * on it alone would be exactly the kind of gate that passes while checking nothing.
   */
  test("both builds ship the web font import the sheet depends on", async ({ page }) => {
    for (const origin of [BRANCH_ORIGIN, BASE_ORIGIN]) {
      await openRoute(page, ROUTES[0], seed, origin);
      await page.evaluate(() => document.fonts.ready);

      const report = await page.evaluate(() => {
        const imports: { index: number; href: string | null }[] = [];
        for (const sheet of document.styleSheets) {
          let rules: CSSRuleList;
          try {
            rules = sheet.cssRules;
          } catch {
            // A cross-origin sheet (the imported Google one) refuses `cssRules`. The rule
            // this test is looking for lives in the app's own same-origin stylesheet.
            continue;
          }
          for (let index = 0; index < rules.length; index += 1) {
            const rule = rules[index];
            if (rule instanceof CSSImportRule) imports.push({ index, href: rule.href });
          }
        }
        return {
          imports,
          check: {
            mono: document.fonts.check('12px "DM Mono"'),
            sans: document.fonts.check("12px Manrope"),
          },
          loaded: [...document.fonts].map((face) => `${face.family}:${face.status}`),
        };
      });

      const fonts = report.imports.filter((rule) =>
        (rule.href ?? "").includes("fonts.googleapis.com"),
      );
      expect(fonts, `${origin} dropped the Google Fonts @import`).toHaveLength(1);
      expect(fonts[0].href).toContain("family=DM+Mono");
      expect(fonts[0].href).toContain("family=Manrope");
      expect(
        fonts[0].index,
        `${origin} emitted the @import after another rule, so the browser ignored it`,
      ).toBe(0);

      // `faces` is empty and `check` is true by construction here: `stubWebFonts` answers
      // the CDN with an empty stylesheet, so no face is ever registered. Both are logged
      // rather than asserted, for exactly that reason - the assertion above is the guard.
      console.log(
        `fonts ${origin}: @import at index ${fonts[0].index}, ` +
          `check(DM Mono)=${report.check.mono} check(Manrope)=${report.check.sans}, ` +
          `faces=[${report.loaded.join(", ")}]`,
      );
    }
  });

  /**
   * The cascade `react-flow.css` depends on, read out of the BUILT stylesheet.
   *
   * M9 PR5c moved the React Flow overrides out of `styles.css` into `src/react-flow.css`,
   * unlayered, imported from `RunExecution.tsx` on the line after `@xyflow/react/dist/style.css`.
   * Two properties have to hold in the bundle for that to mean anything, and neither is
   * visible to `cssPort.test.ts`, which reads source files and knows nothing about how
   * Vite orders CSS it collects from a module graph:
   *
   *  1. Our overrides come AFTER xyflow's sheet. Unlayered beats layered whatever the
   *     order, but two unlayered rules of EQUAL specificity are settled by order alone, and
   *     `.projection-node-kind-gate` is (0,1,0) against xyflow's `.react-flow__node-default`,
   *     also (0,1,0), for the border of every gate node.
   *  2. Our overrides are outside every `@layer`. Inside one they would lose to xyflow
   *     outright, at any specificity - which is the failure PR5a measured on
   *     `button:disabled` and the reason that rule is in `react-flow.css` too.
   *
   * The computed-style diff cannot substitute for this. It compares the branch against the
   * merge-base, and if a future change put the overrides in a layer on BOTH sides it would
   * report nothing while the canvas rendered as xyflow's default. So this reads the
   * stylesheet the branch build actually serves and asserts the two orderings directly.
   *
   * Only the branch is measured. The base build is whatever the merge-base was, and pinning
   * ITS cascade is neither this PR's business nor achievable across the PR that introduces
   * the file.
   */
  test("the built stylesheet puts the React Flow overrides after xyflow's sheet and outside every layer", async () => {
    const index = await (await fetch(new URL("/", BRANCH_ORIGIN))).text();
    const href = builtStylesheet(index, `the page served by ${BRANCH_ORIGIN}`);
    const css = await (await fetch(new URL(href, BRANCH_ORIGIN))).text();
    expect(css.length, `${href} was served empty`).toBeGreaterThan(10_000);

    // `.react-flow__node-default` occurs in xyflow's own rule and inside ours
    // (`.projection-node.react-flow__node-default`). The lookbehind takes the first
    // occurrence that is NOT preceded by another compound selector, which is xyflow's.
    const xyflow = css.search(/(?<![\w-])\.react-flow__node-default/);
    const override = css.indexOf(".projection-node.react-flow__node-default");
    const disabled = css.indexOf("button:disabled{");

    expect(xyflow, "xyflow's .react-flow__node-default rule is not in the built stylesheet")
      .toBeGreaterThan(-1);
    expect(override, "our .projection-node.react-flow__node-default rule is not in the built stylesheet")
      .toBeGreaterThan(-1);
    expect(disabled, "button:disabled is not in the built stylesheet").toBeGreaterThan(-1);

    expect(
      override,
      "react-flow.css was emitted BEFORE @xyflow/react/dist/style.css. Every " +
        "same-specificity tie on the canvas now goes to xyflow: gate nodes lose their amber " +
        "border, terminal nodes their second pixel of border width. Check the import order " +
        "in the component that imports both.",
    ).toBeGreaterThan(xyflow);

    // Layer extents, matched with a brace counter rather than a regex: `@layer components`
    // is 30 kB of nested rules and `@media` blocks.
    const layers: [number, number][] = [];
    for (const match of css.matchAll(/@layer[^{;]*\{/g)) {
      let depth = 0;
      let at = match.index + match[0].length - 1;
      for (; at < css.length; at += 1) {
        if (css[at] === "{") depth += 1;
        else if (css[at] === "}" && (depth -= 1) === 0) break;
      }
      layers.push([match.index, at]);
    }
    expect(layers.length, "the built stylesheet declares no layer at all").toBeGreaterThan(0);

    const layered = (at: number) => layers.some(([from, to]) => at >= from && at <= to);
    for (const [name, at] of [
      [".projection-node.react-flow__node-default", override],
      ["button:disabled", disabled],
    ] as const) {
      expect(
        layered(at),
        `${name} was emitted inside an @layer block. A layered rule loses to xyflow's ` +
          "unlayered stylesheet regardless of specificity; this is the exact failure M9 " +
          "PR5a measured as `#148 button cursor not-allowed -> pointer`.",
      ).toBe(false);
    }

    console.log(
      `built cascade: xyflow @${xyflow} < react-flow.css @${override} ` +
        `(button:disabled @${disabled}), ${layers.length} layer block(s) starting @${layers[0][0]}`,
    );
  });
});

/* ------------------------------------------------------------------------------------- */
/* The fixture-mocked passes.                                                              */
/* ------------------------------------------------------------------------------------- */

/**
 * Serve `e2e/fixtures/` to both builds instead of the backend.
 *
 * `examples/showcase.py` seeds one successful FAKE run with no gate, no loop search, no
 * candidate branches and no experience transfer, so the sweep above never renders roughly
 * two thirds of `styles.css`. These two routes are where the rest of it lives.
 *
 * Unmatched requests are answered 404 and RECORDED. Answering them with `{}` would produce
 * a half-rendered page that still diffs clean - the same failure mode the repository's
 * vitest suites avoid with their explicit 404 fall-through - so the caller asserts the
 * recorded list is empty and a forgotten endpoint names itself.
 */
async function mockApi(
  page: Page,
  resolve: (url: string, method: string) => unknown | undefined,
): Promise<string[]> {
  const unmatched: string[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const parsed = new URL(request.url());
    const path = `${parsed.pathname}${parsed.search}`;

    // The run page's `EventSource` never completes, and `route.fulfill` can only send a
    // finished response. Aborting makes the stream fail immediately and deterministically:
    // the fixture run is terminal, so `EventStream` settles on `connection-complete` and
    // stays there rather than cycling through reconnect states mid-measurement.
    if (path.includes("/events?")) {
      await route.abort();
      return;
    }

    const body = resolve(path, request.method());
    if (body === undefined) {
      unmatched.push(`${request.method()} ${path}`);
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ message: `no fixture for ${request.method()} ${path}` }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  return unmatched;
}

/** The mocked run, addressed by the fixture's own id rather than the seeded one. */
const MOCKED_RUN_ROUTE: RouteUnderTest = {
  path: `/runs/${FIXTURE_RUN_ID}`,
  heading: /…/,
  settle: "run-events",
};

const MOCKED_TASK_ROUTE: RouteUnderTest = { path: "/tasks/new", heading: "New task" };

/* ------------------------------------------------------------------------------------- */
/* The four mocked benchmark routes.                                                       */
/* ------------------------------------------------------------------------------------- */

/**
 * One mocked benchmark route: which page, how it is driven, and how much it must render.
 *
 * `seeded` is the element count the sweep above measures on the same route against the
 * seeded backend. It is not a floor - it is the number the mocked pass has to BEAT, and the
 * reason is a correction to the M9 PR5b plan that is worth stating where it is acted on.
 *
 * The plan assumed `/benchmarks/*` render an empty state under `examples/showcase.py`, so
 * that these fixtures would be the only way the benchmark rules ever reach a pixel. They do
 * not: the four benchmark endpoints serve frozen research corpora that ship with the
 * backend and ignore the seeder entirely, so the sweep already paints the tables, the gate
 * grids, the quality curve and the provider cards. What it cannot paint is anything behind
 * an interaction - `.benchmark-detail` needs a task id clicked, `.benchmark-status` needs a
 * replay pressed - and it cannot control how many rows carry `.null-result`.
 *
 * So each `prepare` below scripts exactly those interactions, identically against both
 * origins, and the pass asserts `elements > seeded`. That inequality is the guard that keeps
 * these passes from silently becoming WEAKER than the sweep they sit beside: a fixture that
 * lost half the corpus would still diff clean against a base fixed the same way, and would
 * look like evidence.
 */
interface MockedBenchmark {
  readonly route: RouteUnderTest;
  /**
   * The measured mocked count, as a floor. Same contract as `ROUTE_ELEMENT_FLOOR`: `>=`
   * because these pages may grow, pinned to the measurement because nothing may make them
   * smaller. Measured identical at all five widths, which is expected - none of these
   * screens adds or drops markup at a breakpoint, they only reflow.
   */
  readonly floor: number;
  /** The scripted interaction that puts the seed-unreachable regions on the page. */
  readonly prepare: (page: Page) => Promise<void>;
}

/**
 * Press a page's replay button and wait for the report to land again.
 *
 * Two barriers, because the button does two things. `.benchmark-status` is written
 * synchronously with a "Replaying…" placeholder and rewritten when the POST resolves, so
 * waiting on the final sentence is what stops the probe reading the placeholder on one
 * origin and the result on the other. And the handler then invalidates the summary query,
 * which refetches and re-renders the table; waiting for the row count settles that too.
 */
async function replayBenchmark(
  page: Page,
  button: string,
  status: RegExp,
  rows: number,
): Promise<void> {
  await page.getByRole("button", { name: button }).click();
  await expect(page.getByRole("status")).toHaveText(status);
  await expect(page.locator(".benchmark-table tbody tr")).toHaveCount(rows);
}

const MOCKED_BENCHMARKS: readonly MockedBenchmark[] = [
  {
    route: { path: "/benchmarks/acr-arch", heading: "ACR-ARCH" },
    floor: 1057,
    prepare: async (page) => {
      // 68 scenarios over 30 tasks means `acr-001` labels three buttons; the first is the
      // one an operator's eye lands on, and `.first()` is what keeps strict mode happy.
      await page.getByRole("button", { name: ACR_ARCH_TASK_ID }).first().click();
      await expect(
        page.getByRole("heading", { name: "Review a deterministic serializer contract" }),
      ).toBeVisible();
      await replayBenchmark(page, "Reproduce replay", /Reproduced 68 scenarios/, 68);
    },
  },
  {
    route: { path: "/benchmarks/dynamic", heading: "Dynamic workflow gate" },
    floor: 119,
    prepare: (page) =>
      replayBenchmark(page, "Reproduce static vs dynamic", /Reproduced 24 traces; gate passed/, 2),
  },
  {
    route: { path: "/benchmarks/search", heading: "Quality vs compute" },
    floor: 205,
    prepare: (page) =>
      replayBenchmark(page, "Reproduce N=1/2/4", /Reproduced 12 held-out tasks/, 12),
  },
  {
    route: { path: "/benchmarks/experience", heading: "Experience transfer gate" },
    floor: 137,
    prepare: (page) =>
      replayBenchmark(page, "Reproduce P7 gate", /Reproduced 80 traces; gate passed/, 4),
  },
];

test.describe("computed-style diff over fixture-mocked pages", () => {
  test.describe.configure({ timeout: 600_000 });

  test("the run page renders identically with gate, loop, search and experience state", async ({
    page,
  }) => {
    const unmatched = await mockApi(page, (url) => runFixtureFor(url));
    const differences: string[] = [];
    const counts: string[] = [];
    const shortfalls: (() => void)[] = [];

    for (const width of WIDTHS) {
      const result = await compare(page, MOCKED_RUN_ROUTE, width);
      differences.push(...result.differences);
      counts.push(`${width}px: ${result.elements}`);
      shortfalls.push(() =>
        assertAboveFloor("mocked run page", width, result.elements, MOCKED_RUN_ELEMENT_FLOOR),
      );
    }

    console.log(
      `style-diff mocked run page — ${counts.join(", ")} elements ` +
        `(floor ${MOCKED_RUN_ELEMENT_FLOOR})`,
    );
    for (const assertShortfall of shortfalls) assertShortfall();
    expect(unmatched, `endpoints with no fixture:\n${unmatched.join("\n")}`).toEqual([]);
    expect(differences, differences.join("\n")).toEqual([]);
  });

  /**
   * The planning review, which no amount of request interception alone can reach.
   *
   * `NewTaskPage` holds the planning result in component state, so the review renders only
   * after the task form is submitted. The submission is scripted identically against both
   * origins and answered from the same fixtures, which is what makes the two pages
   * comparable - and what puts `.score-grid`, `.notice`, `.decision-grid`,
   * `.evidence-list`, `.experience-match` and the `!important` on `.experience-reasons`
   * (styles.css:236) in front of the probe at all.
   */
  test("the planning review renders identically after the task form is submitted", async ({
    page,
  }) => {
    const unmatched = await mockApi(page, (url, method) => planningFixtureFor(url, method));
    const differences: string[] = [];
    const counts: string[] = [];
    const shortfalls: (() => void)[] = [];

    for (const width of WIDTHS) {
      const captures: Record<string, ElementCapture[]> = {};
      for (const origin of [BRANCH_ORIGIN, BASE_ORIGIN]) {
        await page.setViewportSize({ width, height: VIEWPORT_HEIGHT });
        await openRoute(page, MOCKED_TASK_ROUTE, seed, origin);
        await submitTaskForm(page);
        await page.evaluate(() => document.fonts.ready);
        await settle(page, MOCKED_TASK_ROUTE, width, origin);
        captures[origin] = (await page.evaluate(computedStyleProbe())) as ElementCapture[];
      }

      const base = captures[BASE_ORIGIN];
      const branch = captures[BRANCH_ORIGIN];
      // Recorded before the fingerprint check, so a structural mismatch still reports how
      // much of the review each build managed to render.
      counts.push(`${width}px: ${branch.length}`);
      shortfalls.push(() =>
        assertAboveFloor(
          "mocked planning review",
          width,
          branch.length,
          MOCKED_PLANNING_ELEMENT_FLOOR,
        ),
      );
      if (!fingerprintsEqual(base, branch)) {
        differences.push(
          `/tasks/new @ ${width}: base rendered ${base.length} elements, branch ` +
            `${branch.length}`,
        );
        continue;
      }
      differences.push(
        ...diffStyles(base, branch).map((difference) =>
          formatDifference("/tasks/new (planned)", width, difference),
        ),
      );
    }

    console.log(
      `style-diff mocked planning review — ${counts.join(", ")} elements ` +
        `(floor ${MOCKED_PLANNING_ELEMENT_FLOOR})`,
    );
    for (const assertShortfall of shortfalls) assertShortfall();
    expect(unmatched, `endpoints with no fixture:\n${unmatched.join("\n")}`).toEqual([]);
    expect(differences, differences.join("\n")).toEqual([]);
  });

  /**
   * The four benchmark screens, driven from `fixtures/benchmarks.ts`.
   *
   * M9 PR5b moves `styles.css:96` (`.benchmark-summary`, `.benchmark-filters`,
   * `.benchmark-status`, `.benchmark-table*`, `.benchmark-detail*`) and `:245-274`
   * (`.search-research-grid`, `.search-curve*`, `.curve-*`, `.experience-gate-grid*`,
   * `.experience-benchmark-evidence*`, `.provider-*`, `.benchmark-table tr.null-result`)
   * into the components layer, plus the 900 px block at `:275` that reshapes three of them.
   * These four passes are what puts that text in front of the probe under fixtures the UI
   * owns, at every width, with the two interaction-only regions rendered.
   *
   * One test per route rather than one loop inside a single test: a failure names the
   * screen, and a route whose fixture is wrong cannot hide behind three that are right.
   */
  for (const { route, floor, prepare } of MOCKED_BENCHMARKS) {
    // The seeded count this pass must exceed is the sweep's own floor for the route, derived
    // rather than restated: two literals a few lines apart would drift the moment the frozen
    // corpus grows, and the assertion below would then compare against a stale, too-low
    // number - the exact decay it exists to prevent. The margins are small and deliberately
    // so, because the fixtures match the corpus rather than pad it: `/benchmarks/acr-arch`
    // measures 1,057 against 1,046 (the detail card an operator opens, plus the replay
    // status line), and the other three measure exactly one more than the seed - that one
    // element is `.benchmark-status`, which no unattended sweep can ever render.
    const seeded = elementFloor(route.path);
    test(`${route.path} renders identically with its frozen report and detail open`, async ({
      page,
    }) => {
      const unmatched = await mockApi(page, benchmarkFixtureFor);
      const differences: string[] = [];
      const counts: string[] = [];
      const shortfalls: (() => void)[] = [];

      for (const width of WIDTHS) {
        const captures: Record<string, ElementCapture[]> = {};
        for (const origin of [BRANCH_ORIGIN, BASE_ORIGIN]) {
          await page.setViewportSize({ width, height: VIEWPORT_HEIGHT });
          await openRoute(page, route, seed, origin);
          await prepare(page);
          await page.evaluate(() => document.fonts.ready);
          await settle(page, route, width, origin);
          captures[origin] = (await page.evaluate(computedStyleProbe())) as ElementCapture[];
        }

        const base = captures[BASE_ORIGIN];
        const branch = captures[BRANCH_ORIGIN];
        // Recorded before the fingerprint check, so a structural mismatch still reports how
        // much of the report each build managed to render.
        counts.push(`${width}px: ${branch.length}`);
        shortfalls.push(() => {
          assertAboveFloor(`mocked ${route.path}`, width, branch.length, floor);
          expect(
            branch.length,
            `mocked ${route.path} @ ${width} rendered ${branch.length} elements, no more ` +
              `than the ${seeded} the seeded sweep already measures on the same route. The ` +
              "fixture is smaller than the frozen corpus the backend serves, so this pass " +
              "is a WEAKER measurement than the sweep beside it and would still diff clean.",
          ).toBeGreaterThan(seeded);
        });
        if (!fingerprintsEqual(base, branch)) {
          differences.push(
            `${route.path} @ ${width}: base rendered ${base.length} elements, branch ` +
              `${branch.length}`,
          );
          continue;
        }
        differences.push(
          ...diffStyles(base, branch).map((difference) =>
            formatDifference(`${route.path} (mocked)`, width, difference),
          ),
        );
      }

      console.log(
        `style-diff mocked ${route.path} — ${counts.join(", ")} elements ` +
          `(floor ${floor}, seeded ${seeded})`,
      );
      for (const assertShortfall of shortfalls) assertShortfall();
      expect(unmatched, `endpoints with no fixture:\n${unmatched.join("\n")}`).toEqual([]);
      expect(differences, differences.join("\n")).toEqual([]);
    });
  }
});

/**
 * Fill the objective and submit, then wait for the review to settle.
 *
 * The objective is the only required field without a default, and the project select is
 * populated by the fixture, so this is the shortest path from a blank form to a rendered
 * `PlanningReview`. The wait is on the experience card rather than on the review region:
 * the region appears as soon as the planning response lands, while the frozen experience
 * matches arrive one request later, and measuring in between would compare a page that has
 * them against one that does not.
 */
async function submitTaskForm(page: Page): Promise<void> {
  await expect(page.getByRole("option", { name: "Style diff fixture" })).toBeAttached();
  await page
    .getByPlaceholder("Describe the outcome without routing instructions.")
    .fill("Port the shell rules into the components layer.");
  await page.getByRole("button", { name: "Create and profile task" }).click();
  await expect(page.getByRole("region", { name: "Task planning review" })).toBeVisible();
  await expect(page.locator(".experience-match")).toHaveCount(2);
  await expect(page.locator(".experience-match small")).toHaveCount(2);
}
