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
    {
      command: "npm run preview",
      url: "http://localhost:4173/",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
