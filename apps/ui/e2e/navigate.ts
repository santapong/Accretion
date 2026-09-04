import { expect, type Page } from "@playwright/test";

import { RUN_ID_PLACEHOLDER, type RouteUnderTest } from "./routes";
import type { Seed } from "./global-setup";

/**
 * Navigate to a route and wait for it to stop moving.
 *
 * Extracted verbatim from `a11y.spec.ts`'s `open()`, which is the only settle logic in the
 * repository that has been shown to work against this app. `style-diff.spec.ts` measures
 * computed styles on the same seventeen routes and needs exactly the same barrier, and two
 * copies of a settle rule drift: the copy that is not maintained produces a gate that
 * fails intermittently, gets a retry bolted on, and stops being read.
 *
 * ## Why "loaded" is not "settled"
 *
 * The app polls - 2.5 s for runs and approvals, 5 s for runtimes - and the run page holds
 * an open `EventSource`. Auditing mid-render produces failures that vanish on re-run, which
 * is how a gate earns a reputation for flakiness. `networkidle` cannot be the barrier on
 * its own because the run page's stream never closes, so the explicit visibility waits are
 * the real barrier and `networkidle` is a best-effort extra.
 *
 * ## The origin argument
 *
 * `a11y.spec.ts` drives one server and relies on `baseURL`. The style diff drives two: the
 * branch build on :4173 and the pre-migration base on :4174, opened back to back so that a
 * poll landing between them is the only thing that can differ. Passing an origin rather
 * than reconfiguring `use.baseURL` per test keeps both origins reachable inside a single
 * test body, which is what makes "same route, same width, same moment" achievable at all.
 *
 * ## Why the barrier asserts the heading TEXT
 *
 * Waiting for "some level-1 heading" is not a content barrier: every route's shell renders
 * an `h1` whether the backend answered or not, so a page that fell back to its error or
 * empty state clears that wait and is measured as if it were the seeded content. That is
 * not hypothetical - it happened while this gate was being written, when Postgres died
 * mid-suite and sixteen of the seventeen route tests reported green over error placeholders
 * (`/` measured 52 elements instead of 117, `/benchmarks/acr-arch` 78 instead of 1046).
 * Only `/runs/:runId` failed, because it alone carried a barrier that looked at content.
 *
 * `route.heading` was asserted in `a11y.spec.ts` and nowhere else, so the barrier the two
 * gates share is where it belongs: after this returns, both callers know WHICH page they
 * are about to measure, not merely that a page rendered.
 */
export async function openRoute(
  page: Page,
  route: RouteUnderTest,
  seed: Seed,
  origin?: string,
): Promise<void> {
  const path = resolveRoutePath(route, seed);
  await page.goto(origin ? new URL(path, origin).toString() : path, {
    waitUntil: "domcontentloaded",
  });
  const heading = page.getByRole("heading", { level: 1 });
  await expect(heading, `${route.path} rendered no h1`).toBeVisible();
  await expect(
    heading,
    `${route.path} rendered an h1 that is not this route's; the page under the probe is ` +
      "not the page the test names",
  ).toHaveText(route.heading);
  if (route.settle === "run-events") {
    // The trace starts as a "Waiting for events" placeholder and fills from the audit
    // snapshot. The evidence measured this region with 1,349px of content in it.
    await expect(page.getByRole("log", { name: "Normalized event trace" })).toBeVisible();
    await expect(page.locator(".event-list .empty")).toHaveCount(0);
  }
  await page.waitForLoadState("networkidle").catch(() => {
    // The run page's EventSource never closes while the stream is open, so networkidle
    // can legitimately never arrive. The explicit waits above are the real barrier.
  });
}

/**
 * `:runId` substituted with the id the seeded showcase run reports.
 *
 * There is no stable id to hardcode: `accretion.ids.new_id` is a timestamp plus randomness,
 * so every seeding produces a new one.
 */
export function resolveRoutePath(route: RouteUnderTest, seed: Seed): string {
  return route.path.replace(RUN_ID_PLACEHOLDER, seed.run_id);
}
