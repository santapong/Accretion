/**
 * The one list the navigation bar and the router both read.
 *
 * Until v0.3.1 these were two hand-synced lists in `App.tsx`: a `navigation` array of
 * `[path, label]` tuples and a `<Routes>` element holding sixteen `<Route>` children
 * plus the `*` fallback. Nothing checked that they agreed, so a route added without a
 * nav entry was unreachable and a nav entry added without a route led to the 404 page —
 * both green in every gate. `OperatorShell` now derives the nav from
 * `ROUTES.filter((route) => route.label)` and its `<Route>` children from all of
 * `ROUTES`, so the two cannot drift apart.
 *
 * A `label` is the navigation text, which deliberately differs from the page's `h1`
 * ("Runtimes" vs "Runtime monitor"), so it is stored rather than derived. A row without
 * one is routable but not offered in the nav: `/runs/:runId` is reached from a run link,
 * and `*` is not a destination.
 *
 * `end` is NOT stored here; `OperatorShell` derives it. Only `/` could ever need it, and
 * a per-row flag would be wrong for fourteen of fifteen rows with nothing able to tell.
 *
 * **No page may import this module.** Every element below is constructed here, so a page
 * importing `ROUTES` would be a circular value import. The two "back to" links in the
 * app (`DashboardPage`'s "New task" and `HistoryPage`'s "Open live run") stay string
 * literals for that reason.
 */

// React 19 removed the global `JSX` namespace from @types/react, so `JSX.Element` has to
// be imported rather than assumed ambient.
import type { JSX } from "react";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { BenchmarkPage } from "./pages/BenchmarkPage";
import { CapabilitiesPage } from "./pages/CapabilitiesPage";
import { CapabilityInspectorPage } from "./pages/CapabilityInspectorPage";
import { ConnectionsPage } from "./pages/ConnectionsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DynamicBenchmarkPage } from "./pages/DynamicBenchmarkPage";
import { ExperienceBenchmarkPage } from "./pages/ExperienceBenchmarkPage";
import { HistoryPage } from "./pages/HistoryPage";
import { IdentityPage } from "./pages/IdentityPage";
import { LiveRunPage } from "./pages/LiveRunPage";
import { McpServersPage } from "./pages/McpServersPage";
import { NewTaskPage } from "./pages/NewTaskPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { PluginsPage } from "./pages/PluginsPage";
import { RuntimeMonitorPage } from "./pages/RuntimeMonitorPage";
import { SearchBenchmarkPage } from "./pages/SearchBenchmarkPage";

export interface RouteEntry {
  /** The `<Route path>`, including the `*` fallback. */
  readonly path: string;
  /** The navigation label, or absent for a route the nav bar must not offer. */
  readonly label?: string;
  readonly element: JSX.Element;
}

export const ROUTES: readonly RouteEntry[] = [
  { path: "/", label: "Dashboard", element: <DashboardPage /> },
  { path: "/tasks/new", label: "New task", element: <NewTaskPage /> },
  { path: "/runs/:runId", element: <LiveRunPage /> },
  { path: "/runtimes", label: "Runtimes", element: <RuntimeMonitorPage /> },
  { path: "/history", label: "History", element: <HistoryPage /> },
  { path: "/approvals", label: "Approvals", element: <ApprovalsPage /> },
  { path: "/capabilities", label: "Capabilities", element: <CapabilitiesPage /> },
  { path: "/admin/connections", label: "Connections", element: <ConnectionsPage /> },
  { path: "/admin/plugins", label: "Plugins", element: <PluginsPage /> },
  { path: "/admin/mcp", label: "MCP servers", element: <McpServersPage /> },
  { path: "/admin/capabilities/inspect", label: "Capability inspector", element: <CapabilityInspectorPage /> },
  { path: "/admin/identity", label: "Identity", element: <IdentityPage /> },
  { path: "/benchmarks/acr-arch", label: "ACR-ARCH", element: <BenchmarkPage /> },
  { path: "/benchmarks/dynamic", label: "P5 Dynamic", element: <DynamicBenchmarkPage /> },
  { path: "/benchmarks/search", label: "P6 Search", element: <SearchBenchmarkPage /> },
  { path: "/benchmarks/experience", label: "P7 Experience", element: <ExperienceBenchmarkPage /> },
  { path: "*", element: <NotFoundPage /> },
];
