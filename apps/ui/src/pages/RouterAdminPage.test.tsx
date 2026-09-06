import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "../App";
import activationFixtures from "../__fixtures__/router/activations.json";
import lineageFixture from "../__fixtures__/router/lineage.json";
import reportFixtures from "../__fixtures__/router/promotion-report.json";
import versionFixtures from "../__fixtures__/router/versions.json";
import { ROUTES } from "../routes";
import type {
  MeResponse,
  RouterActivation,
  RouterLineage,
  RouterModelVersion,
  RouterPromotionReport,
} from "../types";

/**
 * The §17.3 router administration page, over fixtures derived from the v0.4 goldens.
 *
 * `versions.json` is the append-only shape a promoted-then-withdrawn family really leaves
 * behind: the fitted baseline, the candidate row minted by the promotion, the tombstone and
 * the restored copy the rollback minted, and one shadow candidate. THREE of those five rows
 * say `ACTIVE`, which is the point — the ledger head is a different question from the status
 * column, and `lineage.json` answers it with `active_version_id`.
 *
 * `promotion-report.json` holds two reports: the `PROMOTE` one the promotion cited, and the
 * golden `REQUIRE_REVIEW` report for the shadow candidate, which authorises nothing and
 * carries the critical regression that says why. `activations.json` holds the ledger entries
 * the two POST routes return.
 *
 * The whole app is rendered rather than the page alone, because two of the claims are about
 * the route table: that `/admin/router` resolves to this page and that the nav offers it.
 */

const WORKSPACE = "wks_8G33T24F686H6EJPBHRSFYCC3C";
const HEAD_VERSION = "rmv_6HND5PVA2Y7KQ3XT9RJZ0CWMEB";
const ROLLBACK_TARGET = "rmv_73BPF68NW342QGTDNRRFYDCH6B";
const SHADOW_CANDIDATE = "rmv_7CJEP8EAB33CNS3R0N4CRPN18G";
const PROMOTED_CANDIDATE = "rmv_2KPQMJ4VCETFFK2Z973NZSM3NV";
const PROMOTE_REPORT = "rpr_P5ZJB9X8T0JM655SQFCBNJQ4T7";
const REVIEW_REPORT = "rpr_N99A206Y89ZZTCGZNFH0W4W2XD";

const versions = versionFixtures as unknown as RouterModelVersion[];
const lineage = lineageFixture as unknown as RouterLineage;
const reports = reportFixtures as unknown as RouterPromotionReport[];
const activations = activationFixtures as unknown as RouterActivation[];

function me(role: string): MeResponse {
  return {
    auth_mode: "LOCAL_PRINCIPAL",
    principal: {
      schema_version: "1.0",
      principal_id: "prin_priya",
      type: "HUMAN",
      issuer: "accretion-local",
      subject: "priya",
      email: null,
      display_name: "Priya Raman",
      status: "ACTIVE",
      created_at: "2026-08-20T00:00:00Z",
    },
    memberships: [
      {
        schema_version: "1.0",
        membership_id: "wsm_1",
        workspace_id: WORKSPACE,
        principal_id: "prin_priya",
        role: role as MeResponse["memberships"][number]["role"],
        revision: 1,
        created_at: "2026-08-20T00:00:00Z",
      },
    ],
  };
}

interface Call {
  readonly url: string;
  readonly method: string;
  readonly headers: Record<string, string>;
  readonly body: unknown;
}

function ok(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response;
}

/**
 * Answer the routes this page calls and record every request.
 *
 * `refuse` arms the next POST with the envelope the API really sends, so the refusal case
 * renders the server's own message rather than one this test invented.
 */
function install(role: string, refuse?: { status: number; message: string }): Call[] {
  const calls: Call[] = [];
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    const method = String(init?.method ?? "GET");
    calls.push({
      url,
      method,
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    if (url.endsWith("/api/v1/me")) return ok(me(role));
    if (method === "POST" && refuse) {
      return {
        ok: false,
        status: refuse.status,
        json: async () => ({
          code: "ROUTER_ACTIVATION_CONFLICT",
          message: refuse.message,
          correlation_id: "corr_1",
          retryable: false,
        }),
      } as Response;
    }
    if (url.includes("/promote")) return ok(activations[0]);
    if (url.includes("/rollback")) return ok(activations[1]);
    if (url.includes("/lineage")) return ok(lineage);
    if (url.includes("/api/v1/router-promotions/")) {
      const reportId = url.split("/api/v1/router-promotions/")[1].split("?")[0];
      const found = reports.find((report) => report.contract_id === reportId);
      return found
        ? ok(found)
        : ({ ok: false, status: 404, json: async () => ({ message: "no such report" }) } as Response);
    }
    if (url.includes("/api/v1/router-models")) return ok(versions);
    if (url.includes("/api/v1/runtimes")) return ok([]);
    // Deliberate: an unmocked request must be an obvious failure, not a hang.
    return { ok: false, status: 404, json: async () => ({ detail: "not found" }) } as Response;
  });
  return calls;
}

function renderApp(path = "/admin/router") {
  window.history.pushState({}, "", path);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
}

class EventSourceStub {
  static readonly OPEN = 1;
  addEventListener = vi.fn();
  close = vi.fn();
}

vi.stubGlobal("EventSource", EventSourceStub);
vi.stubGlobal("fetch", vi.fn());

beforeEach(() => {
  window.history.pushState({}, "", "/");
  vi.mocked(fetch).mockReset();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test("router admin lists lineage, activation history, report ids, the active version and its rollback target", async () => {
  install("OWNER");
  renderApp();

  expect(
    await screen.findByRole("heading", { level: 1, name: "Router administration" }),
  ).toBeInTheDocument();

  // The parent chain the lineage route walked, newest ancestor first.
  await screen.findByLabelText(`lineage ${SHADOW_CANDIDATE}`);
  const chain = within(screen.getByRole("region", { name: "Lineage" }));
  expect(chain.getByLabelText(`lineage ${SHADOW_CANDIDATE}`)).toHaveTextContent(
    "gradient-boosted-ranker",
  );
  expect(chain.getByLabelText(`lineage ${SHADOW_CANDIDATE}`)).toHaveTextContent(
    "rts_TR41NDVX8Q0KM7ZJ5HB2WCPY63",
  );
  expect(chain.getByLabelText(`lineage ${PROMOTED_CANDIDATE}`)).toBeInTheDocument();
  expect(chain.getByLabelText(`lineage ${ROLLBACK_TARGET}`)).toBeInTheDocument();

  // Both activations, in ledger order, with what each displaced and what it cited.
  const ledger = within(screen.getByRole("region", { name: "Activation history" }));
  const promotion = ledger.getByLabelText("activation 3");
  expect(promotion).toHaveTextContent("PROMOTE");
  expect(promotion).toHaveTextContent(PROMOTED_CANDIDATE);
  expect(promotion).toHaveTextContent(PROMOTE_REPORT);
  const withdrawal = ledger.getByLabelText("activation 4");
  expect(withdrawal).toHaveTextContent("ROLLBACK");
  expect(withdrawal).toHaveTextContent(HEAD_VERSION);
  expect(withdrawal).toHaveTextContent("Critical cohort regression on secrets handling");

  // The report ids the family's activations cite, which are the only ones the API can be
  // asked for without an id from somewhere else.
  expect(screen.getByLabelText("Cited reports")).toHaveTextContent(PROMOTE_REPORT);

  const active = within(screen.getByRole("region", { name: "Active router version" }));
  expect(active.getByLabelText("Ledger head")).toHaveTextContent(HEAD_VERSION);
  expect(active.getByLabelText("Rollback target")).toHaveTextContent(ROLLBACK_TARGET);
});

test("the active version is the ledger head, not the row whose status is ACTIVE", async () => {
  install("OWNER");
  renderApp();

  const active = within(await screen.findByRole("region", { name: "Active router version" }));
  expect(active.getByLabelText("Ledger head")).toHaveTextContent(HEAD_VERSION);

  // Three rows in this workspace say ACTIVE, and the head is neither the first nor the row
  // the promotion minted: reading the status column would answer this question wrongly, and
  // the fixture is built so that it does.
  const table = within(screen.getByRole("region", { name: "Router versions" }));
  const claiming = versions.filter((row) => row.status === "ACTIVE");
  expect(claiming.length).toBeGreaterThan(1);
  expect(claiming[0].contract_id).not.toBe(HEAD_VERSION);
  expect(active.getByLabelText("Status rows claiming ACTIVE")).toHaveTextContent(
    `${claiming.length} of ${versions.length}`,
  );
  for (const row of claiming) {
    expect(table.getByLabelText(row.contract_id)).toHaveTextContent("ACTIVE");
  }
  // Exactly one row is marked as the head, and it is the one the ledger names.
  expect(table.getByLabelText(HEAD_VERSION)).toHaveTextContent("ledger head");
  expect(table.getByLabelText(PROMOTED_CANDIDATE)).not.toHaveTextContent("ledger head");
});

test("a member without administer sees no promote control and nothing posts", async () => {
  const calls = install("VIEWER");
  renderApp();

  await screen.findByLabelText("activation 4");
  expect(screen.queryByRole("region", { name: "Promotion and rollback" })).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /Promote the candidate/ }),
  ).not.toBeInTheDocument();
  expect(screen.queryByRole("form", { name: "Roll back the ledger head" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Administration unavailable")).toHaveTextContent("VIEWER");

  // The evidence is still readable: a viewer is refused the acts, not the audit of them.
  const active = within(screen.getByRole("region", { name: "Active router version" }));
  expect(active.getByLabelText("Ledger head")).toHaveTextContent(HEAD_VERSION);
  expect(calls.every((call) => call.method === "GET")).toBe(true);
});

test("a refused promotion renders the server's message rather than an empty panel", async () => {
  const calls = install("ADMIN", {
    status: 409,
    message: `router version ${PROMOTED_CANDIDATE} is not the head of its family any more`,
  });
  renderApp();

  const promote = await screen.findByRole("button", { name: /Promote the candidate/ });
  await waitFor(() => expect(promote).toBeEnabled());
  fireEvent.click(promote);

  const refusal = within(await screen.findByRole("alert"));
  expect(refusal.getByLabelText("Refusal reason")).toHaveTextContent(
    `router version ${PROMOTED_CANDIDATE} is not the head of its family any more`,
  );
  expect(screen.getByRole("status")).toHaveTextContent("nothing was activated");

  // The request the refusal answers: the promote route for the open report, in this
  // workspace, under the idempotency key SDD §11 requires of it.
  const posted = calls.filter((call) => call.method === "POST");
  expect(posted).toHaveLength(1);
  expect(posted[0].url).toBe(
    `/api/v1/router-promotions/${PROMOTE_REPORT}/promote?workspace_id=${WORKSPACE}`,
  );
  expect(posted[0].headers["Idempotency-Key"]).toBe(`promote-${PROMOTE_REPORT}`);
});

test("the route is registered, has one h1 and appears in the nav", async () => {
  install("OWNER");
  renderApp();

  await screen.findByRole("heading", { level: 1, name: "Router administration" });
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);

  const nav = within(screen.getByRole("navigation"));
  const link = nav.getByRole("link", { name: "Router" });
  expect(link).toHaveAttribute("href", "/admin/router");
  expect(ROUTES.find((route) => route.path === "/admin/router")?.label).toBe("Router");
});

test("a report that decided REQUIRE_REVIEW renders its regressions and authorises no promotion", async () => {
  install("OWNER");
  renderApp();

  // The cited report is opened by default; the review report is reachable only by its id,
  // because the API has no route that lists reports.
  await screen.findByLabelText("Decision");
  const panel = screen.getByRole("region", { name: "Promotion report" });
  expect(within(panel).getByLabelText("Decision")).toHaveTextContent("PROMOTE");

  fireEvent.change(screen.getByLabelText(/Report id/), { target: { value: REVIEW_REPORT } });
  fireEvent.submit(screen.getByRole("form", { name: "Open a promotion report" }));

  await waitFor(() =>
    expect(within(panel).getByLabelText("Decision")).toHaveTextContent("REQUIRE REVIEW"),
  );
  expect(within(panel).getByLabelText("Critical regressions")).toHaveTextContent(
    "False acceptance rose on the verifier-conflict cohort.",
  );
  expect(within(panel).getByLabelText("Primary metric")).toHaveTextContent("constrained-regret");
  expect(within(panel).getByLabelText("Holdout definition")).toHaveTextContent(
    "rts_X3K3EJ2ZS40W7BHS4CK2X173ZQ",
  );
  expect(within(panel).getByLabelText("Cohort results")).toHaveTextContent("high-risk");
  expect(screen.getByLabelText("Promotion authority")).toHaveTextContent(
    "decided REQUIRE_REVIEW, which authorises no promotion",
  );
  expect(screen.getByRole("button", { name: /Promote the candidate/ })).toBeDisabled();
});

test("rolling back names the ledger head, carries the operator's cause and its own key", async () => {
  const calls = install("OWNER");
  renderApp();

  const form = await screen.findByRole("form", { name: "Roll back the ledger head" });
  const submit = within(form).getByRole("button");
  await waitFor(() => expect(submit).toBeEnabled());
  fireEvent.change(within(form).getByLabelText(/Cause/), {
    target: { value: "False acceptance rose in production" },
  });
  fireEvent.submit(form);

  await waitFor(() => expect(calls.some((call) => call.method === "POST")).toBe(true));
  const posted = calls.filter((call) => call.method === "POST");
  expect(posted).toHaveLength(1);
  expect(posted[0].url).toBe(
    `/api/v1/router-models/${HEAD_VERSION}/rollback?workspace_id=${WORKSPACE}`,
  );
  expect(posted[0].body).toEqual({
    cause: "False acceptance rose in production",
    run_id: null,
  });
  expect(posted[0].headers["Idempotency-Key"]).toBe(`rollback-${HEAD_VERSION}`);
  expect(await screen.findByRole("status")).toHaveTextContent(
    `made ${activations[1].router_version_id} the ledger head`,
  );
});
