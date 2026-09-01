/**
 * Every route the accessibility gate sweeps, with the `h1` each must render.
 *
 * The authority is the `<Routes>` block in `src/App.tsx`. Sixteen declared paths plus the
 * `*` fallback is the "seventeen routes" the release evidence and the README both claim.
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
}

/** `:runId` is substituted with the id the seeded showcase run reports. */
export const RUN_ID_PLACEHOLDER = ":runId";

export const ROUTES: readonly RouteUnderTest[] = [
  { path: "/", heading: /One control plane\./ },
  { path: "/tasks/new", heading: "New task" },
  { path: `/runs/${RUN_ID_PLACEHOLDER}`, heading: /…/, settle: "run-events" },
  { path: "/runtimes", heading: "Runtime monitor" },
  { path: "/history", heading: "Run history / trace replay" },
  { path: "/approvals", heading: "Verifiers / approvals" },
  { path: "/capabilities", heading: "Capabilities, skills, and plugins" },
  { path: "/admin/connections", heading: "Connections" },
  { path: "/admin/plugins", heading: "Plugins" },
  { path: "/admin/mcp", heading: "MCP servers" },
  { path: "/admin/capabilities/inspect", heading: "Capability inspector" },
  { path: "/admin/identity", heading: "Identity and roles" },
  { path: "/benchmarks/acr-arch", heading: "ACR-ARCH" },
  { path: "/benchmarks/dynamic", heading: "Dynamic workflow gate" },
  { path: "/benchmarks/search", heading: "Quality vs compute" },
  { path: "/benchmarks/experience", heading: "Experience transfer gate" },
  // The catch-all. jsdom asserts this heading exists but never that it is the only one.
  { path: "/definitely-not-a-route", heading: "Page not found" },
];
