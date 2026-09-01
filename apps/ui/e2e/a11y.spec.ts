import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

import { judge } from "./allowlist";
import { contrastProbe, overflowProbe, type ContrastReport, type OverflowReport } from "./audit";
import { ROUTES, RUN_ID_PLACEHOLDER, type RouteUnderTest } from "./routes";
import { SEED_FILE, type Seed } from "./global-setup";

/**
 * The browser half of the accessibility gate.
 *
 * `src/accessibility.test.tsx` covers findings F3 and F4 in jsdom and says in its own
 * header why it cannot cover F1 and F2: there is no layout engine, and `src/test/setup.ts`
 * fakes `getBoundingClientRect` to fixed per-class constants, so an overflow assertion
 * would be measuring the stub and a contrast assertion would be measuring nothing.
 *
 * Everything here needs a real browser. It re-measures F1 and F2, runs axe over all
 * seventeen routes rather than the ten jsdom reaches, and checks the one thing the vitest
 * suite explicitly declines to assert - that focus actually lands on the trace region.
 */

const seed = JSON.parse(readFileSync(SEED_FILE, "utf8")) as Seed;

const resolvePath = (route: RouteUnderTest) => route.path.replace(RUN_ID_PLACEHOLDER, seed.run_id);

/**
 * Navigate and wait for the route to stop moving.
 *
 * The app polls (2.5s for runs and approvals, 5s for runtimes) and the run page holds an
 * open SSE stream, so "loaded" is not the same as "settled". Auditing mid-render produces
 * failures that vanish on re-run, which is how an accessibility gate earns a reputation
 * for flakiness and stops being read.
 */
async function open(page: Page, route: RouteUnderTest): Promise<void> {
  await page.goto(resolvePath(route), { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
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

test.describe("accessibility", () => {
  test("axe reports no violation outside the allowlist, across every route", async ({ page }) => {
    const observed: { route: string; rule: string }[] = [];
    const detail: string[] = [];

    for (const route of ROUTES) {
      await open(page, route);
      // DEFAULT RULESET, NO TAG FILTER. Adding `.withTags(["wcag2a", "wcag2aa"])` would
      // run FEWER rules than the 4.10.2 sweep this replaces and would silently drop the
      // best-practice rules that were passing, including heading-order.
      const results = await new AxeBuilder({ page }).analyze();
      for (const violation of results.violations) {
        observed.push({ route: route.path, rule: violation.id });
        // Name the offending elements, not just the rule: a failure reading only
        // "scrollable-region-focusable on /admin/mcp" leaves the reader to find it.
        detail.push(
          `${route.path}  ${violation.id} (${violation.impact}): ${violation.help}\n` +
            violation.nodes.map((node) => `      ${node.target.join(" ")}`).join("\n"),
        );
      }
    }

    const verdict = judge(observed);
    expect(verdict.unwaived, detail.join("\n")).toEqual([]);
    expect(verdict.expired, "an expired waiver is not permission").toEqual([]);
    expect(verdict.unused, "a waiver for a defect that no longer occurs is a false claim").toEqual(
      [],
    );
  });

  test("every route renders exactly one h1", async ({ page }) => {
    // The evidence asserts one h1 on all seventeen routes; jsdom reaches ten. The five
    // /admin/* pages, /runs/:runId, and uniqueness on the 404 have had no such assertion
    // anywhere until now.
    for (const route of ROUTES) {
      await open(page, route);
      const headings = page.getByRole("heading", { level: 1 });
      await expect(headings, `${route.path} must have exactly one h1`).toHaveCount(1);
      await expect(headings.first(), `${route.path} h1 text`).toHaveText(route.heading);
    }
  });

  test("no route scrolls horizontally at 390px, and nothing overflows unclipped", async ({
    page,
  }) => {
    // TWO assertions, deliberately. `scrollWidth <= clientWidth` alone is satisfiable with
    // `overflow: hidden`, which hides the symptom while leaving the content unreachable.
    // The offender enumeration ignores anything with a scrollable ancestor, which is what
    // made `.registry-card { overflow-x: auto }` a real fix rather than a cover-up.
    const failures: string[] = [];
    for (const route of ROUTES) {
      await open(page, route);
      const report = (await page.evaluate(overflowProbe())) as OverflowReport;
      if (report.scrollWidth > report.clientWidth) {
        failures.push(
          `${route.path}: scrollWidth ${report.scrollWidth} > clientWidth ${report.clientWidth}`,
        );
      }
      for (const offender of report.offenders) {
        failures.push(
          `${route.path}: ${offender.selector} right edge ${offender.right} exceeds ` +
            `${report.clientWidth} with no scrollable ancestor`,
        );
      }
    }
    expect(failures, failures.join("\n")).toEqual([]);
  });

  test("every text node clears WCAG AA contrast", async ({ page }) => {
    // Not axe's color-contrast rule. axe files nodes whose background it cannot resolve as
    // `incomplete`, and the evidence ran with `resultTypes: ["violations"]`, which hides
    // those - so relying on axe alone would stop checking the hardest cases, including
    // text over the panel gradient. `skipped` is asserted for the same reason: an
    // unmeasured node is not a passing one.
    await page.setViewportSize({ width: 1440, height: 1000 });
    const failures: string[] = [];
    let measured = 0;
    let skipped = 0;

    for (const route of ROUTES) {
      await open(page, route);
      const report = (await page.evaluate(contrastProbe())) as ContrastReport;
      measured += report.measured;
      skipped += report.skipped;
      for (const failure of report.failures) {
        failures.push(
          `${route.path}: ${failure.selector} ${failure.ratio}:1 < ${failure.required}:1 ` +
            `(${failure.color} on ${failure.background}, ${failure.fontSize}px` +
            `${failure.bold ? " bold" : ""}) "${failure.text}"`,
        );
      }
    }

    // Printed so the PR body can quote a real number rather than repeating the old one.
    // The v0.3 sweep measured 1,421 nodes over 16 routes; this covers 17, so the count
    // legitimately differs and should not be forced to match.
    console.log(`contrast: ${measured} text nodes measured, ${skipped} skipped`);
    expect(failures, failures.join("\n")).toEqual([]);
    expect(skipped, "a node whose background could not be resolved is unmeasured").toBe(0);
    expect(measured).toBeGreaterThan(500);
  });

  test("the trace region really takes focus and scrolls from the keyboard", async ({ page }) => {
    // `accessibility.test.tsx:143-147` declines to assert this: jsdom does not move focus
    // onto a tabindex-bearing div the way a browser does, so a passing assertion there
    // would be an artefact of the fake environment. Here it is real.
    await page.setViewportSize({ width: 1440, height: 1000 });
    await open(page, ROUTES.find((route) => route.settle === "run-events")!);

    // By role and name, not by class: `.event-list` is NOT unique - EnterpriseAuthPanel
    // carries it too, on /admin/identity.
    const region = page.getByRole("log", { name: "Normalized event trace" });
    await expect(region).toHaveAttribute("tabindex", "0");
    await expect(region).toHaveAttribute("aria-live", "polite");

    await region.focus();
    await expect(region).toBeFocused();

    const scrollable = await region.evaluate((el) => el.scrollHeight > el.clientHeight);
    if (scrollable) {
      const before = await region.evaluate((el) => el.scrollTop);
      await page.keyboard.press("End");
      await expect
        .poll(async () => region.evaluate((el) => el.scrollTop))
        .toBeGreaterThan(before);
    }
  });
});
