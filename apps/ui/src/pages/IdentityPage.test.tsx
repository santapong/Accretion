import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { IdentityPage } from "./IdentityPage";
import type { components } from "../api/schema";

type MeResponse = components["schemas"]["MeResponse"];
type WorkspaceEntity = components["schemas"]["WorkspaceEntity"];
type AuthProviderInfo = components["schemas"]["AuthProviderInfo"];
type EnterpriseAuthProfileResponse = components["schemas"]["EnterpriseAuthProfileResponse"];

const ME: MeResponse = {
  auth_mode: "OIDC",
  principal: {
    schema_version: "1.0",
    principal_id: "prin_alice",
    type: "HUMAN",
    issuer: "https://idp.test",
    subject: "alice-subject",
    email: "alice@example.test",
    display_name: "Alice",
    status: "ACTIVE",
    created_at: "2026-08-20T00:00:00Z",
  },
  memberships: [
    {
      schema_version: "1.0",
      membership_id: "wsm_owner",
      workspace_id: "wks_platform",
      principal_id: "prin_alice",
      role: "OWNER",
      revision: 1,
      created_at: "2026-08-20T00:00:00Z",
    },
    {
      schema_version: "1.0",
      membership_id: "wsm_viewer",
      workspace_id: "wks_research",
      principal_id: "prin_alice",
      role: "VIEWER",
      revision: 1,
      created_at: "2026-08-20T00:00:00Z",
    },
  ],
};

const WORKSPACES: WorkspaceEntity[] = [
  {
    schema_version: "1.0",
    workspace_id: "wks_platform",
    name: "Platform",
    created_at: "2026-08-20T00:00:00Z",
  },
  {
    schema_version: "1.0",
    workspace_id: "wks_research",
    name: "Research",
    created_at: "2026-08-20T00:00:00Z",
  },
];

const PROVIDERS: AuthProviderInfo[] = [{ mode: "OIDC", issuer: "https://idp.test" }];

// Two profiles that differ only in whether the deployment turned enterprise-managed
// authorization on, and — within the enabled one — whether this session holds a live
// assertion. Both are the response schema verbatim: the panel may render no other key.
const ENABLED_PROFILE: EnterpriseAuthProfileResponse = {
  enabled: true,
  token_exchange_configured: true,
  has_live_assertion: true,
  assertion_expires_at: "2026-08-30T12:00:00Z",
  audiences: { "research-openalex": "https://openalex.mcp.test" },
};
const DISABLED_PROFILE: EnterpriseAuthProfileResponse = {
  enabled: false,
  token_exchange_configured: false,
  has_live_assertion: false,
  assertion_expires_at: null,
  audiences: {},
};
const NO_ASSERTION_PROFILE: EnterpriseAuthProfileResponse = {
  ...ENABLED_PROFILE,
  has_live_assertion: false,
  assertion_expires_at: null,
};

function install(
  profile: EnterpriseAuthProfileResponse = ENABLED_PROFILE,
): string[] {
  const urls: string[] = [];
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    urls.push(`${init?.method ?? "GET"} ${url}`);
    const body = url.endsWith("/api/v1/me")
      ? ME
      : url.endsWith("/api/v1/workspaces")
        ? WORKSPACES
        : url.endsWith("/api/v1/enterprise-auth/profile")
          ? profile
          : PROVIDERS;
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  return urls;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/admin/identity"]}>
        <Routes><Route path="/admin/identity" element={<IdentityPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

vi.stubGlobal("fetch", vi.fn());

beforeEach(() => {
  vi.mocked(fetch).mockReset();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test("the page is headed Identity and roles and shows the principal's issuer, subject, and session", async () => {
  install();
  renderPage();

  expect(
    screen.getByRole("heading", { level: 1, name: "Identity and roles" }),
  ).toBeInTheDocument();

  await screen.findByLabelText("Principal id");
  const principal = within(screen.getByRole("region", { name: "Principal" }));
  expect(principal.getByLabelText("Principal id")).toHaveTextContent("prin_alice");
  // Identity is (issuer, subject); email alone never identifies a principal.
  expect(principal.getByLabelText("Issuer and subject")).toHaveTextContent("https://idp.test");
  expect(principal.getByLabelText("Issuer and subject")).toHaveTextContent("alice-subject");
  expect(principal.getByLabelText("Type and status")).toHaveTextContent("ACTIVE");

  const session = within(screen.getByRole("region", { name: "Current session" }));
  expect(session.getByLabelText("Auth mode")).toHaveTextContent("OIDC");
  expect(session.getByLabelText("Session subject")).toHaveTextContent("alice-subject");
  expect(within(session.getByRole("list", { name: "Auth providers" })).getByText(
    "OIDC · https://idp.test",
  )).toBeInTheDocument();
});

test("two memberships of the same principal are shown with the roles that differ", async () => {
  install();
  renderPage();

  const owner = within(await screen.findByRole("row", { name: "wks_platform" }));
  expect(owner.getByText("OWNER")).toBeInTheDocument();
  expect(owner.getByText("Platform")).toBeInTheDocument();

  const viewer = within(await screen.findByRole("row", { name: "wks_research" }));
  expect(viewer.getByText("VIEWER")).toBeInTheDocument();
  expect(viewer.getByText("Research")).toBeInTheDocument();
  expect(viewer.queryByText("OWNER")).not.toBeInTheDocument();
});

test("the identity page issues only reads and offers no way to change a role", async () => {
  const urls = install();
  renderPage();

  await screen.findByRole("row", { name: "wks_platform" });
  // Read-only by construction: role assignment and session revocation are M7 surfaces.
  expect(urls.every((entry) => entry.startsWith("GET "))).toBe(true);
  expect(new Set(urls)).toEqual(
    new Set([
      "GET /api/v1/me",
      "GET /api/v1/workspaces",
      "GET /api/v1/auth/providers",
      "GET /api/v1/enterprise-auth/profile",
    ]),
  );
  expect(screen.queryAllByRole("button")).toEqual([]);
  expect(screen.queryAllByRole("textbox")).toEqual([]);
  expect(screen.queryAllByRole("combobox")).toEqual([]);
});

test("the enterprise authorization panel reports an enabled profile with its audiences", async () => {
  install(ENABLED_PROFILE);
  renderPage();

  await screen.findByLabelText("Enterprise authorization enabled");
  const panel = within(
    screen.getByRole("region", { name: "Enterprise authorization" }),
  );
  expect(panel.getByLabelText("Enterprise authorization enabled")).toHaveTextContent("enabled");
  expect(panel.getByLabelText("Token exchange configured")).toHaveTextContent("configured");
  expect(panel.getByLabelText("Live assertion")).toHaveTextContent("held by this session");
  expect(panel.getByLabelText("Assertion expires at")).toHaveTextContent(
    "2026-08-30T12:00:00.000Z",
  );
  expect(panel.getByLabelText("Enterprise authorization readiness")).toHaveTextContent(
    "Enabled, configured, and this session holds a live identity assertion.",
  );
  expect(
    within(panel.getByRole("list", { name: "Enterprise authorization audiences" })).getByLabelText(
      "audience research-openalex",
    ),
  ).toHaveTextContent("https://openalex.mcp.test");
});

test("a disabled profile is stated as disabled rather than rendered as an empty panel", async () => {
  install(DISABLED_PROFILE);
  renderPage();

  await screen.findByLabelText("Enterprise authorization enabled");
  const panel = within(
    screen.getByRole("region", { name: "Enterprise authorization" }),
  );
  expect(panel.getByLabelText("Enterprise authorization enabled")).toHaveTextContent("disabled");
  expect(panel.getByLabelText("Token exchange configured")).toHaveTextContent("not configured");
  expect(panel.getByLabelText("Live assertion")).toHaveTextContent("none held");
  expect(panel.getByLabelText("Assertion expires at")).toHaveTextContent("no live assertion");
  // The two fixtures differ only in the profile, so the sentence must differ too.
  expect(panel.getByLabelText("Enterprise authorization readiness")).toHaveTextContent(
    "Enterprise-managed authorization is disabled. EMA connectors behave as unauthorized connectors and require standard OAuth.",
  );
  expect(
    within(panel.getByRole("list", { name: "Enterprise authorization audiences" })).getByText(
      "No connector is mapped to an enterprise audience.",
    ),
  ).toBeInTheDocument();
});

test("an enabled profile with no live assertion is distinguished from one that holds one", async () => {
  install(NO_ASSERTION_PROFILE);
  renderPage();

  await screen.findByLabelText("Enterprise authorization enabled");
  const panel = within(
    screen.getByRole("region", { name: "Enterprise authorization" }),
  );
  expect(panel.getByLabelText("Enterprise authorization enabled")).toHaveTextContent("enabled");
  expect(panel.getByLabelText("Live assertion")).toHaveTextContent("none held");
  expect(panel.getByLabelText("Enterprise authorization readiness")).toHaveTextContent(
    "Enabled and configured, but this session holds no live identity assertion.",
  );
});

test("the audience list is a focusable scroll region and the panel renders no unknown key", async () => {
  // A misbehaving deployment that answers with more than the schema declares: the panel
  // must render the five keys it knows and drop the rest on the floor. Rendering keys
  // generically (Object.entries over the response) would put every sentinel on screen.
  const OVERSHARING = {
    ...ENABLED_PROFILE,
    secret_store_key: "sentinel-secret-store-key",
    assertion_id: "sentinel-assertion-id",
    id_token: "sentinel-id-token",
    access_token: "sentinel-access-token",
  } as unknown as EnterpriseAuthProfileResponse;
  install(OVERSHARING);
  renderPage();

  await screen.findByLabelText("Enterprise authorization enabled");
  const region = screen.getByRole("region", { name: "Enterprise authorization" });
  const audiences = within(region).getByRole("list", {
    name: "Enterprise authorization audiences",
  });
  expect(audiences).toHaveAttribute("tabindex", "0");
  audiences.focus();
  expect(document.activeElement).toBe(audiences);

  // Nothing outside EnterpriseAuthProfileResponse reaches the DOM: a response carrying
  // assertion material would have to be rendered to be leaked, and it is not.
  const text = region.textContent ?? "";
  for (const forbidden of [
    "secret_store_key",
    "assertion_id",
    "id_token",
    "access_token",
    "sentinel-secret-store-key",
    "sentinel-assertion-id",
    "sentinel-id-token",
    "sentinel-access-token",
  ]) {
    expect(text).not.toContain(forbidden);
  }
  // Non-vacuity: the panel did render the profile it was handed.
  expect(text).toContain("https://openalex.mcp.test");
});
