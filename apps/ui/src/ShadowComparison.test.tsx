import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import reportFixture from "./__fixtures__/shadow/report.json";
import versionFixtures from "./__fixtures__/shadow/versions.json";
import { ShadowComparison } from "./ShadowComparison";
import type { MeResponse, RouterModelVersion, Run, ShadowReport } from "./types";

/**
 * The §17.2 shadow comparison, over the frozen v0.4 contract fixtures.
 *
 * `versions.json` is the `router_model_version` golden — one copy in `SHADOW`, one edited to
 * `ACTIVE` — so the SHADOW filter has something to reject rather than an empty list to pass
 * over. `report.json` has the shape `shadow_report` builds from a `shadow_decision` and its
 * `shadow_rollout_result` rows: three pairs from three decisions, two complete and one whose
 * forks produced no result, and both promotion gates unmet. Two of the three came from the
 * run under test and the third did not, which is what separates "shows the report" from
 * "shows this run's part of the report".
 */
const WORKSPACE = "wks_8G33T24F686H6EJPBHRSFYCC3C";
const SHADOW_VERSION = "rmv_7CJEP8EAB33CNS3R0N4CRPN18G";
const ACTIVE_VERSION = "rmv_73BPF68NW342QGTDNRRFYDCH6B";
const EXECUTED_PAIRED = "rcp_ZV1DZZBTPY9GD9V6NVWWXX40ZS";
const SHADOW_PAIRED = "rcp_FQ639X9EJVHQ0BQDB279RN8DXX";
const EXECUTED_PENDING = "rcp_YPW2A7XZVADFSDVJS4DGZHEK47";
const SHADOW_PENDING = "rcp_9BM7WKQZ4TDPXC0R2VHJE6SNF5";
const OTHER_RUN_EXECUTED = "rcp_3KFQ7DZV1M8XR0PB5NWHTJ2C6A";
const OTHER_RUN_SHADOW = "rcp_HX5T2QBM9VNZ0C4KDWJ7RP63SE";

/** The receipts this run's audit named; the other run's pair names neither of them. */
const RUN_RECEIPTS = [EXECUTED_PAIRED, EXECUTED_PENDING];

const report = reportFixture as unknown as ShadowReport;
const versions = versionFixtures as unknown as RouterModelVersion[];

const me = {
  auth_mode: "dev",
  principal: {
    principal_id: "usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    issuer: "accretion-local",
    subject: "alice",
    email: null,
    display_name: "Alice",
    status: "ACTIVE",
    created_at: "2026-03-01T09:00:00Z",
  },
  memberships: [
    {
      schema_version: "1.0",
      membership_id: "wsm_1",
      workspace_id: WORKSPACE,
      principal_id: "usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
      role: "OWNER",
      revision: 1,
      created_at: "2026-03-01T09:00:00Z",
    },
  ],
} as unknown as MeResponse;

const run: Run = {
  run_id: "run_shadow_fixture",
  task_id: "tsk_shadow_fixture",
  project_id: "prj_8W5DH3HW6DPAFFPBHQ47R21DK9",
  provider: "FAKE",
  state: "RUNNING",
  last_sequence: 4,
  revision: 1,
};

function response(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

/**
 * The URL router, with a 404 fallthrough.
 *
 * The fallthrough is what makes "never calls the report route" assertable: an unmocked
 * request has to be an obvious failure rather than a hang, because two of the claims below
 * are about which requests this panel makes and not only about what it renders.
 */
function stubRoutes(available: RouterModelVersion[] = versions) {
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/v1/me") return response(me);
    if (url.startsWith("/api/v1/router-models")) return response(available);
    if (url === `/api/v1/shadow-policies/${SHADOW_VERSION}/report`) return response(report);
    return response({ message: `unmocked ${url}` }, 404);
  });
}

function renderComparison(receipts: readonly string[] = RUN_RECEIPTS) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ShadowComparison run={run} receipts={receipts} />
    </QueryClientProvider>,
  );
}

/** One `<dd>` of a pair, found through the `<dt>` beside it rather than by position. */
function cell(pair: HTMLElement, label: string): HTMLElement {
  const term = within(pair).getByText(label);
  const value = term.parentElement?.querySelector("dd");
  if (!value) throw new Error(`the pair has no value for ${label}`);
  return value as HTMLElement;
}

async function pairItems(): Promise<HTMLElement[]> {
  const list = await screen.findByRole("list", { name: "Executed vs shadow" });
  return within(list).getAllByRole("listitem");
}

const requestedUrls = () =>
  [...new Set(vi.mocked(fetch).mock.calls.map((call) => String(call[0])))].sort();

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// AC4-M6-041, second witness: the UI half of "the shadow view compares recommendations with
// executed outcomes". `tests/test_v04_m6_report_api.py` proves the route pairs them; this
// proves an operator is shown both halves rather than the projection alone.
test("the shadow view compares recommendations with executed outcomes", async () => {
  stubRoutes();
  renderComparison();

  const panel = await screen.findByRole("region", { name: "Shadow comparison" });
  const pairs = await pairItems();
  expect(pairs).toHaveLength(2);

  // The executed baseline and the shadow recommendation, named by the receipts a reader can
  // open in the §17.1 panel — never one id standing for the comparison.
  expect(cell(pairs[0], "executed")).toHaveTextContent(EXECUTED_PAIRED);
  expect(cell(pairs[0], "shadow")).toHaveTextContent(SHADOW_PAIRED);
  expect(cell(pairs[1], "executed")).toHaveTextContent(EXECUTED_PENDING);
  expect(cell(pairs[1], "shadow")).toHaveTextContent(SHADOW_PENDING);

  // Predicted against observed, both signed: the projection the router made when it chose,
  // and what the CONTROL and shadow forks actually measured.
  expect(cell(pairs[0], "predicted delta")).toHaveTextContent("+0.018");
  expect(cell(pairs[0], "observed delta")).toHaveTextContent("+0.021");
  expect(pairs[0]).toHaveTextContent("agreed");
  expect(pairs[1]).toHaveTextContent("differed");

  // The accumulated evidence behind the verdict, and the verdict itself.
  expect(within(panel).getByText(/complete pairs in the stage/)).toHaveTextContent(
    "2 complete pairs in the stage",
  );
  expect(within(panel).getByText("66.7%")).toBeInTheDocument();
  expect(within(panel).getByText("+0.0085")).toBeInTheDocument();
  expect(within(panel).getByText("-0.012")).toBeInTheDocument();
  expect(within(panel).getByText("INCONCLUSIVE")).toBeInTheDocument();
});

test("a run with no SHADOW version renders the disabled state and never calls the report route", async () => {
  // The workspace has a router version; none of them is shadowing anything.
  stubRoutes(versions.filter((version) => version.contract_id === ACTIVE_VERSION));
  renderComparison();

  const panel = await screen.findByRole("region", { name: "Shadow comparison" });
  expect(await within(panel).findByText(/No router version is in SHADOW/)).toBeInTheDocument();
  expect(panel).toHaveTextContent("POST /api/v1/shadow-policies");
  expect(within(panel).queryByRole("list", { name: "Executed vs shadow" })).toBeNull();

  // There is no version id to ask about, so the report route is never requested: a report
  // for a stage that does not exist is a 404 an operator would read as a broken panel.
  expect(requestedUrls()).toEqual([
    "/api/v1/me",
    `/api/v1/router-models?workspace_id=${WORKSPACE}`,
  ]);
});

test("each unmet gate is named", async () => {
  stubRoutes();
  renderComparison();

  const gates = await screen.findByRole("list", { name: "Promotion gates" });
  const listed = within(gates).getAllByRole("listitem");
  expect(listed).toHaveLength(2);

  // Both gates, each with the arithmetic that decided it: a count against the required
  // count, and a lower bound against the non-inferiority floor.
  expect(listed[0]).toHaveTextContent("paired runs");
  expect(listed[0]).toHaveTextContent("2 complete pairs of the 30 required");
  expect(listed[1]).toHaveTextContent("non inferiority");
  expect(listed[1]).toHaveTextContent("delta_lcb -0.012 against a floor of 0.0");
  expect(within(listed[0]).getByText("PENDING")).toBeInTheDocument();
  expect(within(listed[1]).getByText("PENDING")).toBeInTheDocument();

  expect(
    screen.getByText("Promotion is blocked on 2 unmet gate(s): paired runs, non inferiority."),
  ).toBeInTheDocument();
});

test("pairs outside this run are not shown", async () => {
  stubRoutes();
  renderComparison();

  const panel = await screen.findByRole("region", { name: "Shadow comparison" });
  const pairs = await pairItems();
  expect(pairs).toHaveLength(2);

  // The report is the whole stage's, over every run it scored. The third pair belongs to a
  // different run and naming it here would attribute another run's evidence to this one.
  expect(panel).not.toHaveTextContent(OTHER_RUN_EXECUTED);
  expect(panel).not.toHaveTextContent(OTHER_RUN_SHADOW);
  // …and the aggregate is still reported as the stage's, not recomputed from these two.
  expect(panel).toHaveTextContent("the 2 of 3 that came from this run");
});

test("an incomplete pair shows observed as pending, not zero", async () => {
  stubRoutes();
  renderComparison();

  const pairs = await pairItems();
  const observed = cell(pairs[1], "observed delta");

  // `observed_delta` is null exactly when a fork produced no result. Zero is the value that
  // says the shadow changed nothing, so rendering it here would turn a missing measurement
  // into the verdict the stage exists to reach.
  expect(observed).toHaveTextContent("pending");
  expect(observed.textContent).not.toMatch(/\d/);
  // The prediction is still shown: it is what the router claimed before anything ran.
  expect(cell(pairs[1], "predicted delta")).toHaveTextContent("+0.042");
});

test("a run that recorded no routing receipt asks the shadow routes for nothing", async () => {
  stubRoutes();
  renderComparison([]);

  const panel = await screen.findByRole("region", { name: "Shadow comparison" });
  expect(panel).toHaveTextContent("recorded no routing receipt");
  // Routing is opt-in and most runs are not routed. A panel that listed the workspace's
  // router versions on every run page would cost two requests per run to say "nothing".
  expect(vi.mocked(fetch)).not.toHaveBeenCalled();
});
