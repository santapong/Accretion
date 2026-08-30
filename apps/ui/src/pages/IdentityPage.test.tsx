import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { IdentityPage } from "./IdentityPage";
import type { components } from "../api/schema";

type MeResponse = components["schemas"]["MeResponse"];
type WorkspaceEntity = components["schemas"]["WorkspaceEntity"];
type AuthProviderInfo = components["schemas"]["AuthProviderInfo"];

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

function install(): string[] {
  const urls: string[] = [];
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    urls.push(`${init?.method ?? "GET"} ${url}`);
    const body = url.endsWith("/api/v1/me")
      ? ME
      : url.endsWith("/api/v1/workspaces")
        ? WORKSPACES
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
    new Set(["GET /api/v1/me", "GET /api/v1/workspaces", "GET /api/v1/auth/providers"]),
  );
  expect(screen.queryAllByRole("button")).toEqual([]);
  expect(screen.queryAllByRole("textbox")).toEqual([]);
  expect(screen.queryAllByRole("combobox")).toEqual([]);
});
