import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ConnectionsPage } from "./ConnectionsPage";
import type { components } from "../api/schema";

type ConnectionSummary = components["schemas"]["ConnectionSummary"];
type ConnectorDefinition = components["schemas"]["ConnectorDefinition"];

/**
 * Every key the API promises. The page is asserted to read a subset of this, so
 * inventing a field on the client (or reading one the API never sends) fails here.
 */
const CONNECTION_SUMMARY_KEYS = [
  "connection_id",
  "connector_id",
  "created_at",
  "granted_scopes",
  "last_health_check",
  "principal_id",
  "scope",
  "status",
  "workspace_id",
  "workspace_shareable",
] as const;

const CONNECTOR: ConnectorDefinition = {
  schema_version: "1.0",
  connector_id: "conndef_github",
  name: "GitHub",
  kind: "REST",
  auth_type: "OAUTH2",
  connection_scope: "USER",
  authorization_server: "https://authorization.test",
  resource_server: "https://api.test",
  default_scopes: ["read:user"],
  optional_scopes: [],
  health_check_ref: null,
  plugin_id: null,
  created_at: "2026-08-20T00:00:00Z",
};

// `Required<...>` forces every optional field to be present, so `Object.keys(ACTIVE)`
// is the schema's full key set and the pin below cannot silently miss a new field.
const ACTIVE: Required<ConnectionSummary> = {
  connection_id: "conn_active",
  connector_id: "conndef_github",
  created_at: "2026-08-20T00:00:00Z",
  granted_scopes: ["read:user", "repo:status"],
  last_health_check: "2026-08-27T09:00:00Z",
  principal_id: "prin_alice",
  scope: "USER",
  status: "ACTIVE",
  workspace_id: "workspace_local",
  workspace_shareable: false,
};

const STALE: ConnectionSummary = {
  connection_id: "conn_stale",
  connector_id: "conndef_slack",
  created_at: "2026-08-21T00:00:00Z",
  granted_scopes: [],
  last_health_check: null,
  principal_id: "prin_bob",
  scope: "WORKSPACE",
  status: "REAUTH_REQUIRED",
  workspace_id: "workspace_local",
  workspace_shareable: true,
};

const AUTHORIZATION_URL =
  "https://authorization.test/login/oauth/authorize?state=xyz&code_challenge=abc";

/** Records every string key the page reads off a connection summary. */
function watched(summary: ConnectionSummary, seen: Set<string>): ConnectionSummary {
  return new Proxy(summary, {
    get(target, key, receiver) {
      if (typeof key === "string") seen.add(key);
      return Reflect.get(target, key, receiver);
    },
  });
}

interface Call {
  url: string;
  method: string;
}

function install(
  connections: ConnectionSummary[],
): { calls: Call[]; navigations: string[] } {
  const calls: Call[] = [];
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push({ url, method: init?.method ?? "GET" });
    const body = url.endsWith("/api/v1/connectors")
      ? [CONNECTOR]
      : url.endsWith("/api/v1/connections")
        ? connections
        : url.endsWith("/revoke")
          ? { ...STALE, status: "REVOKED", granted_scopes: [] }
          : url.endsWith("/health")
            ? { status: "ACTIVE", token_status: "ACTIVE" }
            : { authorization_url: AUTHORIZATION_URL };
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  const navigations: string[] = [];
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: { assign: (url: string) => navigations.push(url) },
  });
  return { calls, navigations };
}

function renderPage() {
  // Structural sharing would replace the fixture proxies with plain clones and make
  // the key-read assertion vacuous.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, structuralSharing: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/admin/connections"]}>
        <Routes><Route path="/admin/connections" element={<ConnectionsPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

vi.stubGlobal("fetch", vi.fn());

const realLocation = window.location;

beforeEach(() => {
  vi.mocked(fetch).mockReset();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: realLocation,
  });
});

test("the page is headed Connections and lists each connection's status, scopes, owner, and last health check", async () => {
  install([ACTIVE, STALE]);
  renderPage();

  expect(screen.getByRole("heading", { level: 1, name: "Connections" })).toBeInTheDocument();

  const active = within(await screen.findByRole("row", { name: "conn_active" }));
  expect(active.getByText("ACTIVE")).toBeInTheDocument();
  expect(active.getByText("read:user · repo:status")).toBeInTheDocument();
  expect(active.getByText("prin_alice")).toBeInTheDocument();
  expect(active.getByText("2026-08-27T09:00:00.000Z")).toBeInTheDocument();

  // The differential: the second row disagrees on every one of those four columns.
  const stale = within(await screen.findByRole("row", { name: "conn_stale" }));
  expect(stale.getByText("REAUTH_REQUIRED")).toBeInTheDocument();
  expect(stale.getByText("none granted")).toBeInTheDocument();
  expect(stale.getByText("prin_bob")).toBeInTheDocument();
  expect(stale.getByText("never checked")).toBeInTheDocument();

  expect(await screen.findByRole("button", { name: "Connect GitHub" })).toBeInTheDocument();
});

test("connect posts to the connector connect route and hands the browser to the authorization URL without rendering it", async () => {
  const { calls, navigations } = install([ACTIVE]);
  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "Connect GitHub" }));

  await waitFor(() =>
    expect(calls).toContainEqual({
      url: "/api/v1/connectors/conndef_github/connect",
      method: "POST",
    }),
  );
  await waitFor(() => expect(navigations).toEqual([AUTHORIZATION_URL]));
  // Never in the DOM: `state` and the PKCE challenge must not be readable off screen.
  expect(document.body.textContent).not.toContain(AUTHORIZATION_URL);
  expect(document.body.textContent).not.toContain("code_challenge");
});

test("reauthorize posts to the reauthorize route for the row it was pressed in", async () => {
  const { calls } = install([ACTIVE, STALE]);
  renderPage();

  const stale = within(await screen.findByRole("row", { name: "conn_stale" }));
  fireEvent.click(stale.getByRole("button", { name: "Reauthorize" }));

  await waitFor(() =>
    expect(calls).toContainEqual({
      url: "/api/v1/connections/conn_stale/reauthorize",
      method: "POST",
    }),
  );
  expect(calls.map((call) => call.url)).not.toContain(
    "/api/v1/connections/conn_active/reauthorize",
  );
});

test("revoke posts to the revoke route for the row it was pressed in and reports the new status", async () => {
  const { calls } = install([ACTIVE, STALE]);
  renderPage();

  const stale = within(await screen.findByRole("row", { name: "conn_stale" }));
  fireEvent.click(stale.getByRole("button", { name: "Revoke" }));

  await waitFor(() =>
    expect(calls).toContainEqual({
      url: "/api/v1/connections/conn_stale/revoke",
      method: "POST",
    }),
  );
  expect(await screen.findByRole("status")).toHaveTextContent("Connection is now REVOKED.");
  expect(calls.map((call) => call.url)).not.toContain("/api/v1/connections/conn_active/revoke");
});

test("the health action reads the connection health route and reports the token status", async () => {
  const { calls } = install([ACTIVE]);
  renderPage();

  const active = within(await screen.findByRole("row", { name: "conn_active" }));
  fireEvent.click(active.getByRole("button", { name: "Check health" }));

  await waitFor(() =>
    expect(calls).toContainEqual({
      url: "/api/v1/connections/conn_active/health",
      method: "GET",
    }),
  );
  expect(await screen.findByRole("status")).toHaveTextContent("Token status ACTIVE");
});

test("the page reads no key that ConnectionSummary does not declare", async () => {
  const seen = new Set<string>();
  install([watched(ACTIVE, seen), watched(STALE, seen)]);
  renderPage();

  await screen.findByRole("row", { name: "conn_active" });
  const stale = within(await screen.findByRole("row", { name: "conn_stale" }));
  fireEvent.click(stale.getByRole("button", { name: "Reauthorize" }));
  fireEvent.click(stale.getByRole("button", { name: "Revoke" }));
  fireEvent.click(stale.getByRole("button", { name: "Check health" }));
  await waitFor(() => expect(seen.has("connection_id")).toBe(true));

  expect([...CONNECTION_SUMMARY_KEYS]).toEqual(Object.keys(ACTIVE).sort());
  const declared = new Set<string>(CONNECTION_SUMMARY_KEYS);
  const undeclared = [...seen].filter((key) => !declared.has(key));
  expect(undeclared).toEqual([]);
  // Non-vacuous: the page really did read the columns the criterion names.
  for (const key of ["status", "granted_scopes", "principal_id", "last_health_check"]) {
    expect(seen.has(key)).toBe(true);
  }
});
