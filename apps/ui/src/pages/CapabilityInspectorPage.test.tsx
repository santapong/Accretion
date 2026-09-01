import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { CapabilityInspectorPage } from "./CapabilityInspectorPage";
import type { components } from "../api/schema";

type ResolvedCapability = components["schemas"]["ResolvedCapability"];
type Capability = components["schemas"]["Capability"];
type MeResponse = components["schemas"]["MeResponse"];

const ME: MeResponse = {
  auth_mode: "LOCAL_PRINCIPAL",
  principal: {
    schema_version: "1.0",
    principal_id: "prin_alice",
    type: "HUMAN",
    issuer: "accretion-local",
    subject: "alice",
    email: null,
    display_name: null,
    status: "ACTIVE",
    created_at: "2026-08-20T00:00:00Z",
  },
  memberships: [
    {
      schema_version: "1.0",
      membership_id: "wsm_1",
      workspace_id: "workspace_local",
      principal_id: "prin_alice",
      role: "OWNER",
      revision: 1,
      created_at: "2026-08-20T00:00:00Z",
    },
  ],
};

function capability(id: string, risk: Capability["risk"], backend: Capability["backend"]): Capability {
  return {
    schema_version: "1.0",
    capability_id: id,
    kind: "TOOL",
    version: "1.0.0",
    description: "",
    input_schema: {},
    output_schema: {},
    risk,
    side_effects: [],
    required_permissions: [],
    credential_refs: [],
    idempotency: "NONE",
    backend,
    provider_projections: {},
    verifier_policy_ref: null,
    enabled: true,
    created_at: "2026-08-20T00:00:00Z",
  };
}

/** Bound to an OAuth connector over HTTP, and connected. */
const OAUTH: ResolvedCapability = {
  capability: capability("inspector.oauth", "MEDIUM", "HTTP"),
  outcome: "OK",
  binding: {
    schema_version: "1.0",
    binding_id: "cbd_oauth",
    capability_id: "inspector.oauth",
    connector_id: "conndef_github",
    backend: {
      type: "HTTP",
      server_ref: null,
      method: "GET /repos/{owner}/{repo}/status",
      tool_name: null,
    },
    input_transform_ref: null,
    output_transform_ref: null,
    policy_ref: "policy.http.read",
    enabled: true,
    created_at: "2026-08-20T00:00:00Z",
  },
  connection: {
    connection_id: "conn_oauth",
    connector_id: "conndef_github",
    status: "ACTIVE",
  },
  reason: "resolved",
};

/** The differential: a different backend, binding, connection, risk and policy. */
const MCP: ResolvedCapability = {
  capability: capability("inspector.mcp", "HIGH", "MCP"),
  outcome: "OK",
  binding: {
    schema_version: "1.0",
    binding_id: "cbd_mcp",
    capability_id: "inspector.mcp",
    connector_id: "conndef_remote",
    backend: {
      type: "MCP",
      server_ref: "mcp_server_probe",
      method: null,
      tool_name: "probe",
    },
    input_transform_ref: null,
    output_transform_ref: null,
    policy_ref: "policy.mcp.probe",
    enabled: true,
    created_at: "2026-08-20T00:00:00Z",
  },
  connection: {
    connection_id: "conn_remote",
    connector_id: "conndef_remote",
    status: "ACTIVE",
  },
  reason: "resolved",
};

/** Bound, but this principal holds no connection. */
const UNBOUND: ResolvedCapability = {
  capability: capability("inspector.oauth", "MEDIUM", "HTTP"),
  outcome: "NO_CONNECTION",
  binding: OAUTH.binding,
  connection: null,
  reason: "no usable connection for connector conndef_github",
};

interface Call {
  url: string;
  body: unknown;
}

function install(answers: ResolvedCapability[], failure?: { status: number; message: string }) {
  const calls: Call[] = [];
  const queue = [...answers];
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/api/v1/capabilities")) {
      return {
        ok: true,
        status: 200,
        json: async () => [capability("inspector.oauth", "MEDIUM", "HTTP")],
      } as Response;
    }
    if (url.endsWith("/api/v1/me")) {
      return { ok: true, status: 200, json: async () => ME } as Response;
    }
    calls.push({ url, body: JSON.parse(String(init?.body ?? "null")) });
    if (failure) {
      return {
        ok: false,
        status: failure.status,
        json: async () => ({
          code: "FORBIDDEN",
          message: failure.message,
          correlation_id: "corr_1",
          retryable: false,
        }),
      } as Response;
    }
    return { ok: true, status: 200, json: async () => queue.shift() } as Response;
  });
  return calls;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/admin/capabilities/inspect"]}>
        <Routes>
          <Route
            path="/admin/capabilities/inspect"
            element={<CapabilityInspectorPage />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function resolve(capabilityId: string) {
  fireEvent.change(await screen.findByLabelText(/Capability id/), {
    target: { value: capabilityId },
  });
  fireEvent.submit(screen.getByRole("form", { name: "Resolve a capability" }));
}

vi.stubGlobal("fetch", vi.fn());

beforeEach(() => {
  vi.mocked(fetch).mockReset();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test("the page is headed Capability inspector and resolves a capability onto its binding, connection, risk and policy", async () => {
  const calls = install([OAUTH]);
  renderPage();

  expect(
    screen.getByRole("heading", { level: 1, name: "Capability inspector" }),
  ).toBeInTheDocument();

  await resolve("inspector.oauth");

  await waitFor(() => expect(calls).toHaveLength(1));
  expect(calls[0].url).toBe("/api/v1/capabilities/resolve");
  expect(calls[0].body).toMatchObject({ capability_id: "inspector.oauth" });

  const panel = within(await screen.findByRole("region", { name: "Resolution" }));
  expect(panel.getByLabelText("Outcome")).toHaveTextContent("OK");
  expect(panel.getByLabelText("Binding")).toHaveTextContent("cbd_oauth");
  expect(panel.getByLabelText("Binding")).toHaveTextContent("conndef_github");
  expect(panel.getByLabelText("Backend")).toHaveTextContent("HTTP");
  expect(panel.getByLabelText("Connection")).toHaveTextContent("conn_oauth");
  expect(panel.getByLabelText("Risk")).toHaveTextContent("MEDIUM");
  expect(panel.getByLabelText("Policy")).toHaveTextContent("policy.http.read");
  expect(panel.getByLabelText("Reason")).toHaveTextContent("resolved");
});

test("two capabilities bound to different backends do not read alike", async () => {
  install([OAUTH, MCP]);
  renderPage();

  await resolve("inspector.oauth");
  const first = within(await screen.findByRole("region", { name: "Resolution" }));
  const oauth = {
    binding: first.getByLabelText("Binding").textContent,
    backend: first.getByLabelText("Backend").textContent,
    connection: first.getByLabelText("Connection").textContent,
    risk: first.getByLabelText("Risk").textContent,
    policy: first.getByLabelText("Policy").textContent,
  };

  await resolve("inspector.mcp");
  await waitFor(() =>
    expect(screen.getByLabelText("Binding")).toHaveTextContent("cbd_mcp"),
  );
  const second = within(screen.getByRole("region", { name: "Resolution" }));
  expect(second.getByLabelText("Backend").textContent).not.toBe(oauth.backend);
  expect(second.getByLabelText("Binding").textContent).not.toBe(oauth.binding);
  expect(second.getByLabelText("Connection").textContent).not.toBe(oauth.connection);
  expect(second.getByLabelText("Risk").textContent).not.toBe(oauth.risk);
  expect(second.getByLabelText("Policy").textContent).not.toBe(oauth.policy);
  // The MCP-specific facts an operator needs to find the server behind the capability.
  expect(second.getByLabelText("Backend")).toHaveTextContent("mcp_server_probe");
  expect(second.getByLabelText("Backend")).toHaveTextContent("probe");
});

test("an unresolved capability states the denial reason instead of an empty panel", async () => {
  install([UNBOUND]);
  renderPage();

  await resolve("inspector.oauth");

  const panel = within(await screen.findByRole("region", { name: "Resolution" }));
  expect(panel.getByLabelText("Outcome")).toHaveTextContent("NO_CONNECTION");
  expect(panel.getByLabelText("Reason")).toHaveTextContent(
    "no usable connection for connector conndef_github",
  );
  // The binding is still named: the capability is bound, this principal is not connected.
  expect(panel.getByLabelText("Binding")).toHaveTextContent("cbd_oauth");
  expect(panel.getByLabelText("Connection")).toHaveTextContent("no connection selected");
  expect(panel.getByLabelText("Usability")).toHaveTextContent(
    "A run requesting this capability will be refused.",
  );
});

test("a refused resolution renders the API's own message rather than a blank panel", async () => {
  install([], { status: 403, message: "cannot resolve capabilities for another principal" });
  renderPage();

  await resolve("inspector.oauth");

  const failure = within(await screen.findByRole("alert"));
  expect(failure.getByLabelText("Failure reason")).toHaveTextContent(
    "cannot resolve capabilities for another principal",
  );
  // No stale or invented resolution is left on screen beside the refusal.
  expect(screen.queryByRole("region", { name: "Resolution" })).not.toBeInTheDocument();
});
