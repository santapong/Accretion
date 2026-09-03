/**
 * The shell's two derived lists, and the import order the built cascade depends on.
 *
 * Per-route rendering, headings, the 404 and the nav link count are already covered by
 * `App.test.tsx` and `accessibility.test.tsx`, which are indifferent to which file
 * defines a page. What had no test before v0.3.1 is the wiring itself: that the nav and
 * the router read one table, that `end` is applied to `/` alone, and that the two
 * stylesheet imports stay behind the shell import.
 */

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import appSource from "./App.tsx?raw";
import shellSource from "./OperatorShell.tsx?raw";
import routesSource from "./routes.tsx?raw";
import { OperatorShell } from "./OperatorShell";
import { ROUTES } from "./routes";

class EventSourceStub {
  static readonly OPEN = 1;
  addEventListener = vi.fn();
  close = vi.fn();
}

function response(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

vi.stubGlobal("EventSource", EventSourceStub);
vi.stubGlobal("fetch", vi.fn());

beforeEach(() => {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const url = String(input);
    if (url.endsWith("/api/v1/me")) {
      return response({
        principal: { subject: "operator", display_name: "Operator" },
        memberships: [{ role: "ADMIN" }],
      });
    }
    if (url.includes("/api/v1/")) return response([]);
    // Deliberate: an unmocked request must be an obvious failure, not a hang.
    return response({ detail: "not found" }, 404);
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/** Render the shell at `path` and wait until its own identity query has settled. */
async function renderShell(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}><OperatorShell /></MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText("Operator · ADMIN");
  return within(screen.getByRole("navigation"));
}

/**
 * The index of an `import` STATEMENT for `specifier`, matched on a whole `^import` line.
 * A substring search would let a comment or a string mentioning "./theme.css" stand in
 * for the import, which is exactly the mistake the cascade-order test exists to catch.
 */
function importIndex(source: string, specifier: string) {
  const quoted = specifier.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`^import\\s[^\\n]*"${quoted}";$`, "m").exec(source);
  if (!match) throw new Error(`no import statement for ${specifier}`);
  return match.index;
}

test("the nav bar renders one link per labelled route, in table order, and no link for an unlabelled route", async () => {
  const nav = await renderShell("/");
  const links = nav.getAllByRole("link").filter((link) => !link.classList.contains("brand-link"));
  const labelled = ROUTES.filter((route) => route.label);
  const unlabelled = ROUTES.filter((route) => !route.label);

  expect(links.map((link) => link.textContent)).toEqual(labelled.map((route) => route.label));
  expect(links).toHaveLength(ROUTES.length - unlabelled.length);
  // `/runs/:runId` and `*` are routable but must never be offered as destinations.
  expect(unlabelled.map((route) => route.path)).toEqual(["/runs/:runId", "*"]);
  const hrefs = links.map((link) => link.getAttribute("href"));
  for (const route of unlabelled) expect(hrefs).not.toContain(route.path);
});

test("the shell contains no hardcoded route path — every path comes from the routes table", () => {
  expect(shellSource).not.toMatch(/path="/);
  // Exactly one <Route> element exists and it is the mapped one: a stray sibling written
  // as path={'/x'} or path={"/x"} escapes the regex above but not this count.
  expect(shellSource.match(/<Route\b/g)).toHaveLength(1);
  expect(shellSource).toMatch(/<Route key=\{route\.path\} path=\{route\.path\} element=\{route\.element\} \/>/);
  expect(importIndex(shellSource, "./routes")).toBeGreaterThanOrEqual(0);
});

test("the dashboard nav link is marked current only on \"/\", because end applies only to the root entry", async () => {
  // The rendered half pins the pairing: each nav link's `to` comes from the same row as
  // its label. It cannot pin `end`, which is inert under react-router 7 — that version's
  // prefix match already requires a separator after `to`, so `end={false}` renders exactly
  // the same DOM. Mutating `end` therefore has to be caught structurally, below, or R6
  // (a stored per-row `end` would be wrong for fourteen of fifteen rows) has no guard at
  // all and this test's title would assert something nothing could falsify.
  const away = await renderShell("/tasks/new");
  expect(away.getByRole("link", { name: "Dashboard" })).not.toHaveAttribute("aria-current");
  expect(away.getByRole("link", { name: "New task" })).toHaveAttribute("aria-current", "page");

  cleanup();
  const root = await renderShell("/");
  await waitFor(() => {
    expect(root.getByRole("link", { name: "Dashboard" })).toHaveAttribute("aria-current", "page");
  });
  expect(root.getByRole("link", { name: "New task" })).not.toHaveAttribute("aria-current");

  // `end` is derived at the call site from the row's own path, and stored nowhere: no
  // `RouteEntry` field, no row setting one. This is the assertion `end={false}` fails.
  expect(shellSource).toMatch(/<NavLink end=\{route\.path === "\/"\}/);
  expect(routesSource).not.toMatch(/\bend\s*[?:]/);
});

test("the stylesheets are imported after the shell, so React Flow's own sheet stays ahead of them in the cascade", () => {
  const shell = importIndex(appSource, "./OperatorShell");
  const theme = importIndex(appSource, "./theme.css");
  const styles = importIndex(appSource, "./styles.css");
  expect(shell).toBeLessThan(theme);
  expect(theme).toBeLessThan(styles);
});
