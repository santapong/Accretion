import { defineConfig, devices } from "@playwright/test";

/**
 * The accessibility gate.
 *
 * Drives the PRODUCTION BUILD, not the dev server. The evidence this replaces was taken
 * against `npm run dev`, but the artifact users receive is the built bundle: minified CSS
 * in its real cascade order, real chunking, no HMR client. That distinction is the whole
 * point from PR5 onward, where the risk is precisely CSS ordering and deletion.
 *
 * `vite preview` does NOT read `server.proxy` - it reads `preview.proxy`, which
 * `vite.config.ts` now sets. Without it every `/api` call from the previewed app 404s and
 * the failure looks like a dead backend rather than a missing proxy.
 *
 * Specs are `*.spec.ts`; vitest owns `*.test.ts`. The split is by filename because both
 * runners are configured from files in this directory and a path-based split drifts.
 */
/**
 * The pre-migration build the computed-style diff compares against, or undefined.
 *
 * Set by `make style-diff-base` locally and by the `browser` CI job, both of which build the
 * merge-base with `develop` into a directory of their own. When it is absent the third
 * server below is not started and `style-diff.spec.ts` skips - except under CI, where it
 * fails loudly rather than reporting green over a check that never ran.
 */
const STYLE_DIFF_BASE_DIST = process.env.STYLE_DIFF_BASE_DIST;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  // The sweep walks seventeen routes running two full-page audits on each.
  timeout: 120_000,
  expect: { timeout: 15_000 },
  // A flaky accessibility gate would be ignored within a week, so failures must be real:
  // no retries, and one worker so the seeded backend is never audited concurrently.
  retries: 0,
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  globalSetup: "./e2e/global-setup.ts",

  use: {
    // Spread first: the device preset carries its own viewport, and anything set before it
    // would be silently overwritten.
    ...devices["Desktop Chrome"],
    baseURL: "http://localhost:4173",
    // 390px is the narrow viewport the F1 finding was measured at. Note the evidence saw
    // clientWidth 375 because it used a 390px iframe with a 15px classic scrollbar gutter;
    // a headless viewport reports 390. Assert the relationship, never the literal.
    viewport: { width: 390, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },

  webServer: [
    {
      command: "uv run --no-sync uvicorn accretion.api.main:app --port 8000",
      // /healthz is exempt from the session middleware, so it is a readiness probe that
      // works regardless of auth mode.
      url: "http://localhost:8000/healthz",
      cwd: "../..",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    // `--strictPort` rather than a bare `npm run preview`: Vite's preview server AUTO-
    // INCREMENTS off a port already in use, so a second concurrent run - or a stale preview
    // left behind by an interrupted one - would move the BRANCH build onto :4174 and the
    // style diff would compare it with itself and report zero differences. Refusing to bind
    // is the honest outcome. `style-diff.spec.ts` checks the same invariant from the other
    // end by build fingerprint, because `reuseExistingServer` accepts whatever answers.
    {
      command: "npx vite preview --port 4173 --strictPort",
      url: "http://localhost:4173/",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    // The base build, served from a second port so both can be open in one test body. It is
    // previewed from THIS config, so it inherits `preview.proxy` and its /api calls reach
    // the same backend and the same seeded run as the branch build - which is the whole
    // reason a difference between the two pages can only be the stylesheet.
    ...(STYLE_DIFF_BASE_DIST
      ? [{
          command: `npx vite preview --outDir ${STYLE_DIFF_BASE_DIST} --port 4174 --strictPort`,
          url: "http://localhost:4174/",
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
        }]
      : []),
  ],
});
