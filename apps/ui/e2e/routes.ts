/**
 * Every route the accessibility gate sweeps, with the `h1` each must render.
 *
 * The authority is `ROUTES` in `src/routes.tsx`, which the shell turns into both the
 * navigation bar and the router. Seventeen declared paths plus the `*` fallback since M9c
 * added `/admin/router`; the "seventeen routes" the v0.3 release evidence claims was the
 * sixteen-plus-fallback this file swept before it.
 *
 * This is a SUPERSET of `src/accessibility.test.tsx`'s ROUTES, which covers ten. The seven
 * it cannot reach - the five `/admin/*` pages, `/runs/:runId`, and `h1` *uniqueness* on the
 * 404 - have had no heading assertion anywhere until now, even though
 * `browser-a11y-evidence.md` asserts one `h1` on all seventeen. That gap is a large part of
 * why this gate exists.
 */

export interface RouteUnderTest {
  readonly path: string;
  /** The accessible name of the route's single `h1`, or a matcher for a dynamic one. */
  readonly heading: string | RegExp;
  /** Set for routes needing settling beyond load, e.g. an open event stream. */
  readonly settle?: "run-events";
  /**
   * This PR deliberately changes this route's DOM; do not compare it to the merge-base.
   *
   * The computed-style diff aligns two builds by index over `body *`, so ANY added element
   * makes the two captures a different shape and the gate reports "the two builds rendered
   * different DOM" — which is true, intended, and not a finding. Before this field the only
   * ways past it were to skip the gate or to stop adding markup, and the first is how a
   * required check quietly stops being read.
   *
   * A waiver suspends the structural comparison for ONE route and nothing else. The element
   * floor, the focus pass and the whole a11y gate still run on it, so a waived route that
   * renders an error page still fails, and every other route is still compared byte for
   * byte — `styleDiff.test.ts` pins both halves of that.
   *
   * It is deliberately not durable. `pr` names the change that earned it and `reason` says
   * what was added, so the next PR that touches the route must delete this entry and write
   * its own — which is the only thing that stops one waiver from becoming a permanent
   * exemption for the busiest route in the app.
   *
   * `absentFromBase` is the stronger case a NEW route needs: the merge-base build has no
   * such route at all, so its SPA answers with the `*` fallback and `openRoute` fails the
   * base measurement on the heading assertion before any style is read. It suspends the
   * BASE MEASUREMENT and nothing else — `styleDiff.ts` owns that decision and
   * `styleDiff.test.ts` pins that the element floor survives it, which is what keeps a new
   * route from being merged unmeasured.
   */
  readonly structuralChange?: {
    readonly pr: string;
    readonly reason: string;
    readonly absentFromBase?: boolean;
  };
}

/** `:runId` is substituted with the id the seeded showcase run reports. */
export const RUN_ID_PLACEHOLDER = ":runId";

export const ROUTES: readonly RouteUnderTest[] = [
  { path: "/", heading: /One control plane\./ },
  { path: "/tasks/new", heading: "New task" },
  {
    path: `/runs/${RUN_ID_PLACEHOLDER}`,
    heading: /…/,
    settle: "run-events",
    structuralChange: {
      pr: "M9a",
      reason:
        "the §17.1 node routing panel is mounted in .execution-content beside the dynamic " +
        "workflow inspector, so the run page renders more elements than the merge-base",
    },
  },
  { path: "/runtimes", heading: "Runtime monitor" },
  { path: "/history", heading: "Run history / trace replay" },
  { path: "/approvals", heading: "Verifiers / approvals" },
  { path: "/capabilities", heading: "Capabilities, skills, and plugins" },
  { path: "/admin/connections", heading: "Connections" },
  { path: "/admin/plugins", heading: "Plugins" },
  { path: "/admin/mcp", heading: "MCP servers" },
  { path: "/admin/capabilities/inspect", heading: "Capability inspector" },
  { path: "/admin/identity", heading: "Identity and roles" },
  {
    path: "/admin/router",
    heading: "Router administration",
    structuralChange: {
      pr: "M9c",
      reason:
        "new route: the §17.3 router administration page did not exist in the merge-base " +
        "build, which answers /admin/router with the 404 page",
      absentFromBase: true,
    },
  },
  { path: "/benchmarks/acr-arch", heading: "ACR-ARCH" },
  { path: "/benchmarks/dynamic", heading: "Dynamic workflow gate" },
  { path: "/benchmarks/search", heading: "Quality vs compute" },
  { path: "/benchmarks/experience", heading: "Experience transfer gate" },
  // The catch-all. jsdom asserts this heading exists but never that it is the only one.
  { path: "/definitely-not-a-route", heading: "Page not found" },
];
