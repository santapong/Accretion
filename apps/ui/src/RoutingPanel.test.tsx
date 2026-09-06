import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import auditEvents from "./__fixtures__/routing/audit-events.json";
import candidateFixtures from "./__fixtures__/routing/candidates.json";
import receiptFixture from "./__fixtures__/routing/receipt.json";
import { RoutingPanel } from "./RoutingPanel";
import type {
  ConfigurationCandidate,
  GraphProjection,
  RoutingDecisionReceipt,
  Run,
  RunAudit,
} from "./types";

/**
 * The §17.1 node routing panel, over the frozen v0.4 contract fixtures.
 *
 * `receipt.json` is a byte copy of `tests/fixtures/contracts/v0.4/routing_decision_receipt/
 * complete.json` — the golden `tests/test_v04_m2_api.py` asserts the routes against — and
 * `candidates.json` is built from the `configuration_candidate` golden with its ids aligned
 * to that receipt, so a slate has one selected member, one hard-eligible alternative, one
 * hard-eligible-but-dominated member and one that failed a hard gate. The four are what
 * separate "shows the alternatives" from "shows the slate".
 */
const RECEIPT_ID = "rcp_ZV1DZZBTPY9GD9V6NVWWXX40ZS";
const REVIEW_ID = "rcp_RXG4QHXAH6CV4SA907AQV988VW";
const SELECTED_CANDIDATE = "ccd_WZ2VXFZVD3CT64PQPKGVGNVWN9";
const ALTERNATIVE_CANDIDATE = "ccd_NYYV9T7AQFB662TX02BH7CT9Q6";
const DOMINATED_CANDIDATE = "ccd_HZT804NS1J2WHEM8GJ6Z1NPXC6";
const REJECTED_CANDIDATE = "ccd_7731SP5ZT9RP3PB8VJNQWRBEJD";

const candidates = candidateFixtures as unknown as ConfigurationCandidate[];

/**
 * The receipt with the `decision_version` label the routing service maintains.
 *
 * The golden carries only its two descriptive labels, and `routing/service.py` writes
 * `decision_version` on every receipt it persists — `_amend` compares
 * `expected_receipt_version` against exactly that value. Pinning it to 3 rather than 1 is
 * what makes the override body assertion below distinguish "read from the receipt" from
 * "hard-coded to the first version".
 */
const receipt = {
  ...(receiptFixture as unknown as RoutingDecisionReceipt),
  labels: { ...(receiptFixture as unknown as RoutingDecisionReceipt).labels, decision_version: "3" },
};

/** A decision that selected nothing, which §8.1 allows only for `HUMAN_REVIEW_REQUIRED`. */
const reviewReceipt: RoutingDecisionReceipt = {
  ...receipt,
  contract_id: REVIEW_ID,
  decision_type: "HUMAN_REVIEW_REQUIRED",
  selected_configuration_id: null,
  selected_configuration_hash: null,
  selection_propensity: null,
};

const run: Run = {
  run_id: "run_routing_fixture",
  task_id: "tsk_routing_fixture",
  project_id: "prj_8W5DH3HW6DPAFFPBHQ47R21DK9",
  provider: "FAKE",
  state: "RUNNING",
  last_sequence: 4,
  revision: 1,
};

const audit = { events: auditEvents } as unknown as RunAudit;

const projection = {
  schema_version: "1.0",
  run_id: run.run_id,
  revision: 1,
  nodes: [
    { schema_version: "1.0", node_id: "n:act", kind: "AGENT", label: "Act", status: "RUNNING" },
  ],
  edges: [],
} as unknown as GraphProjection;

function response(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

/**
 * The URL router, with a 404 fallthrough.
 *
 * An unmocked request must be an obvious failure and never a hang: the panel's whole claim
 * is about which requests it makes, and a permissive fallthrough would let a fourth request
 * pass unnoticed by the case that exists to count them.
 */
function stubRoutes(receiptBody: RoutingDecisionReceipt, slate = candidates) {
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/candidates")) return response(slate);
    if (url.includes("/api/v1/routing-decisions/")) return response(receiptBody);
    return response({ message: `unmocked ${url}` }, 404);
  });
}

function renderPanel(auditProp: RunAudit | undefined = audit) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RoutingPanel run={run} audit={auditProp} projection={projection} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// AC4-M9-040.
test("node panel shows selected configuration, uncertainty, alternatives and rejection reasons", async () => {
  stubRoutes(receipt);
  renderPanel();

  const panel = await screen.findByRole("region", { name: "Node routing" });

  // The selected configuration is the candidate whose configuration the receipt pinned,
  // named by the runtime, model and frozen verifier an operator would reproduce it with.
  expect(await within(panel).findByText(/accretion-claude-v1/)).toBeInTheDocument();
  expect(within(panel).getByText("CLAUDE · claude-opus-4")).toBeInTheDocument();
  expect(within(panel).getByText(/diff-and-suite v4\.2\.0/)).toBeInTheDocument();

  // Uncertainty: the lower confidence bound the §9.5 gate is defined over, the epistemic
  // term, and the calibration that produced them.
  expect(within(panel).getByText("0.81")).toBeInTheDocument();
  expect(within(panel).getByText("0.19")).toBeInTheDocument();
  expect(within(panel).getByText("conformal-v1")).toBeInTheDocument();

  // Predicted outcomes are intervals labelled with the method, never bare means.
  expect(
    within(panel).getByText("0.88 [0.81, 0.95] @ 0.9 · conformal-v1"),
  ).toBeInTheDocument();

  // Alternatives are the hard-eligible, non-dominated candidates, and nothing else.
  const alternatives = within(panel).getByRole("list", { name: "Alternatives considered" });
  const offered = within(alternatives).getAllByRole("listitem");
  expect(offered).toHaveLength(1);
  expect(offered[0]).toHaveTextContent("claude-sonnet-4");
  expect(alternatives).not.toHaveTextContent("codex-large");
  expect(alternatives).not.toHaveTextContent("gemini-pro");

  // Rejections are reason codes and the gate that raised them — never the prose detail,
  // which is the candidate's own explanation and not a fact about this decision.
  const rejected = within(panel).getByRole("list", { name: "Rejected candidates" });
  expect(rejected).toHaveTextContent("BELOW_SUCCESS_FLOOR");
  expect(rejected).toHaveTextContent("SUCCESS GATE");

  expect(within(panel).getByText("exp_9GGR1WTFAY2TCVCVC0NK0E24MR")).toBeInTheDocument();
});

test("override offers only hard-eligible candidates and posts candidate_id, reason_code, reason and expected_receipt_version", async () => {
  stubRoutes(receipt);
  renderPanel();

  const override = await screen.findByRole("group", {
    name: "Override the routed configuration",
  });
  const picker = within(override).getByLabelText("Replacement candidate");
  const values = within(picker as HTMLSelectElement)
    .getAllByRole("option")
    .map((option) => (option as HTMLOptionElement).value);
  // The three that cleared every hard gate — including the dominated one, which is a worse
  // choice and still a legal one — and never the candidate the success gate rejected: the
  // service answers CANDIDATE_NOT_ELIGIBLE for that id, so offering it offers a dead button.
  expect(values).toEqual(["", SELECTED_CANDIDATE, ALTERNATIVE_CANDIDATE, DOMINATED_CANDIDATE]);
  expect(values).not.toContain(REJECTED_CANDIDATE);

  fireEvent.change(picker, { target: { value: ALTERNATIVE_CANDIDATE } });
  fireEvent.change(within(override).getByLabelText("Reason code"), { target: { value: "COST" } });
  fireEvent.change(within(override).getByLabelText("Reason"), {
    target: { value: "  The cheaper tuple clears the floor.  " },
  });
  fireEvent.click(within(override).getByRole("button", { name: "Record override" }));

  await waitFor(() =>
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      `/api/v1/routing-decisions/${RECEIPT_ID}/override`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          candidate_id: ALTERNATIVE_CANDIDATE,
          reason_code: "COST",
          reason: "The cheaper tuple clears the floor.",
          expected_receipt_version: 3,
        }),
      }),
    ),
  );
});

test("HUMAN_REVIEW_REQUIRED shows no selection and offers cancel", async () => {
  stubRoutes(reviewReceipt);
  renderPanel();

  // The undispatched receipt has no runtime call and therefore no node, so it is reachable
  // only through the picker's own entry for it. A panel keyed on graph nodes alone would
  // make exactly the decision that is waiting for a person the one an operator cannot open.
  fireEvent.change(await screen.findByLabelText("Routed node"), {
    target: { value: REVIEW_ID },
  });

  const review = await screen.findByRole("group", { name: "Human review required" });
  expect(
    screen.getByText("This decision selected no configuration."),
  ).toBeInTheDocument();
  // Nothing was selected, so there is nothing to replace: the override form is absent
  // rather than disabled.
  expect(
    screen.queryByRole("group", { name: "Override the routed configuration" }),
  ).toBeNull();

  fireEvent.click(within(review).getByRole("button", { name: "Cancel routing decision" }));
  await waitFor(() =>
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      `/api/v1/routing-decisions/${REVIEW_ID}/cancel`,
      expect.objectContaining({ method: "POST", body: "{}" }),
    ),
  );
});

test("the panel fetches only /audit, /routing-decisions/{id} and /candidates", async () => {
  stubRoutes(receipt);
  renderPanel();
  await screen.findByRole("region", { name: "Node routing" });
  await screen.findByText(/accretion-claude-v1/);

  // `/audit` is the run page's own query, handed down as a prop — so the panel's own
  // requests are the two M2 reads and nothing else. A third URL here would mean a second
  // audit fetch, a receipts-of-a-run route that does not exist, or a poll.
  const requested = new Set(vi.mocked(fetch).mock.calls.map((call) => String(call[0])));
  expect([...requested].sort()).toEqual([
    `/api/v1/routing-decisions/${RECEIPT_ID}`,
    `/api/v1/routing-decisions/${RECEIPT_ID}/candidates`,
  ]);
});

test("the version pins render", async () => {
  stubRoutes(receipt);
  renderPanel();

  const pins = await screen.findByRole("region", { name: "Node routing" });
  await within(pins).findByText("policy snapshot");
  for (const value of [
    "rmv_D0Q67B7EW89E1W1KJJFEC3XVN5",
    "rmv_WZP5BDHRP7SAK042XKRA617R1T",
    "mcp_SMCJYH1VRSCC2HHMXFHYPXNP90",
    "pol_SHHZA9CFZ1EWF7Y7R6HDRH9K66",
  ]) {
    expect(within(pins).getByText(value)).toBeInTheDocument();
  }
  expect(within(pins).getByText("policy snapshot")).toBeInTheDocument();
  expect(within(pins).getByText("objective contract version")).toBeInTheDocument();
});

test("a run with no routing receipt names the flag that records one, and asks for nothing", async () => {
  stubRoutes(receipt);
  renderPanel({ events: [] } as unknown as RunAudit);

  const panel = await screen.findByRole("region", { name: "Node routing" });
  expect(panel).toHaveTextContent("ACCRETION_ENABLE_NODE_ROUTING=true");
  expect(vi.mocked(fetch)).not.toHaveBeenCalled();
});
