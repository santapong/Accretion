import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import type { components } from "../../src/api/schema";

/**
 * A complete, frozen run for the fixture-mocked pass of the computed-style diff.
 *
 * ## Why this exists at all
 *
 * `global-setup.ts` seeds the backend with `examples/showcase.py`, which creates one
 * successful `FAKE` run: no approval gate, no loop search, no candidate branches, no
 * verified-experience transfer, no graph revision. Roughly two thirds of `styles.css`
 * therefore never renders under the seed, and a rendering diff that only sweeps seeded
 * pages would report "no difference" for rules it never painted a pixel of.
 *
 * These fixtures are served to BOTH builds through one `page.route('**\/api/**')` handler,
 * so the branch and the base are given byte-identical responses and any difference between
 * the two pages is a stylesheet difference by construction.
 *
 * ## Why it is typed against the generated schema
 *
 * `components` comes from `src/api/schema.d.ts`, which `npm run api:generate` rebuilds from
 * the live FastAPI app and CI compares with `git diff --exit-code`. A backend field that is
 * renamed or made required therefore breaks `npm run check` here, in the same change,
 * rather than producing a fixture that no longer resembles a real response and a diff that
 * is measuring a page the app can never actually render.
 *
 * ## Determinism
 *
 * Every timestamp is a literal. The run is `SUCCEEDED`, which matters more than it looks:
 * `runState.useNow` stops ticking on a terminal run, `RunExecution`'s four polling queries
 * turn their `refetchInterval` off, and `runDuration.durationLabel` resolves to a constant
 * string instead of counting up between the branch measurement and the base measurement.
 * A `RUNNING` fixture would produce a real, correct, permanent diff on the elapsed label.
 */

const HERE = dirname(fileURLToPath(import.meta.url));

/**
 * The M6 capability audit, reused rather than rewritten.
 *
 * `tests/test_v03_m6_run_inspectors.py` generates this file from a real gateway execution
 * and byte-compares it there, and `RunExecution.test.tsx` already renders badges from it.
 * Reading the same bytes keeps the node badges in this pass identical to the ones the
 * component suite asserts, instead of inventing a second, weaker version of the same shape.
 *
 * Read with `readFileSync` rather than a JSON import so the module works unchanged under
 * Playwright's transpiler, which does not share Vitest's `resolveJsonModule` handling.
 */
const auditFixture = JSON.parse(
  readFileSync(resolve(HERE, "../../src/__fixtures__/run-audit.json"), "utf8"),
) as Pick<components["schemas"]["RunAudit"], "schema_version" | "capability_results">;

const graphDiffFixture = JSON.parse(
  readFileSync(resolve(HERE, "../../src/__fixtures__/graph-diff.json"), "utf8"),
) as components["schemas"]["GraphRevisionDiff"];

const runtimeDecisionsFixture = JSON.parse(
  readFileSync(resolve(HERE, "../../src/__fixtures__/runtime-decisions.json"), "utf8"),
) as components["schemas"]["RuntimeDecision"][];

export const RUN_ID = "run_style_diff_fixture";
export const TASK_ID = "tsk_style_diff_fixture";
export const PROJECT_ID = "prj_style_diff_fixture";
export const SEARCH_ID = "search_style_diff_fixture";
export const PROPOSAL_ID = "wfp_style_diff_fixture";

const schemaVersion = { schema_version: "1.0" } as const;

/**
 * The signed-in operator the shell's nav bar names.
 *
 * `OperatorShell` runs `api.me()` on every route, so both mocked passes need it or the
 * `.nav-status` label falls back to "Control plane" - a different string, a different text
 * width, and a legitimate but uninteresting difference on every element after it.
 */
export const me = {
  auth_mode: "LOCAL",
  principal: {
    schema_version: "1.0", principal_id: "prc_style_diff", subject: "operator@local",
    issuer: "local", display_name: "Style diff operator", status: "ACTIVE", type: "HUMAN",
  },
  memberships: [{
    schema_version: "1.0", membership_id: "mbr_style_diff", principal_id: "prc_style_diff",
    workspace_id: "wsp_style_diff", role: "OWNER", revision: 1,
  }],
} satisfies components["schemas"]["MeResponse"];

/**
 * Terminal, so nothing on the page is a function of the clock. See the module header.
 */
export const run: components["schemas"]["Run"] = {
  run_id: RUN_ID,
  task_id: TASK_ID,
  project_id: PROJECT_ID,
  provider: "FAKE",
  state: "SUCCEEDED",
  last_sequence: 6,
  revision: 2,
  // `Run` carries no started_at/completed_at - only these two. `runDuration.durationLabel`
  // measures a terminal run created_at -> updated_at, so this pair fixes the "Elapsed"
  // label at a constant 4m 12s instead of letting it count up between the two measurements.
  created_at: "2026-08-20T00:00:00Z",
  updated_at: "2026-08-20T00:04:12Z",
};

const event = (
  sequence: number,
  normalized_type: components["schemas"]["AgentEvent"]["normalized_type"],
  native_type: string,
): components["schemas"]["AgentEvent"] => ({
  event_id: `evt_style_diff_${String(sequence).padStart(3, "0")}`,
  run_id: RUN_ID,
  session_id: "ses_style_diff_fixture",
  correlation_id: "cor_style_diff_fixture",
  adapter_version: "fake-adapter-v1",
  sequence,
  provider: "FAKE",
  native_type,
  normalized_type,
  payload: {},
  timestamp: `2026-08-20T00:0${sequence}:00Z`,
});

/**
 * Six events, which is what the trace needs to be a scroll region rather than a placeholder.
 *
 * `navigate.openRoute` waits for `.event-list .empty` to disappear before measuring, so an
 * empty audit would hang the run-page pass rather than measuring an empty list.
 */
export const audit = {
  ...auditFixture,
  schema_version: "1.0",
  run,
  events: [
    event(1, "RUN_STARTED", "run.started"),
    event(2, "TOOL_STARTED", "tool.invoke"),
    event(3, "TOOL_COMPLETED", "tool.result"),
    event(4, "LOOP_ITERATION_COMPLETED", "loop.iteration"),
    event(5, "VERIFICATION_RESULT", "verifier.done"),
    event(6, "RUN_COMPLETED", "run.completed"),
  ],
} satisfies Pick<
  components["schemas"]["RunAudit"],
  "schema_version" | "run" | "events" | "capability_results"
>;

/**
 * A graph carrying one node of every kind the projection styles differently.
 *
 * `GATE` and `TERMINAL` reach `.projection-node-kind-gate` and
 * `.projection-node-kind-terminal`; the parented pair reaches `.projection-node-group` and
 * its nested `.projection-node-content`; the `LOOP_BACK` edge reaches
 * `.projection-loop-edge` and `.projection-edge-label`; the mixed statuses reach all four
 * `.projection-node-<status>` variants and their status dots.
 */
export const graph: components["schemas"]["GraphProjection"] = {
  schema_version: "1.0",
  version: "loop-projection-v1",
  run_id: RUN_ID,
  workflow_template_id: "hybrid-rd-v1",
  run_graph_version: 2,
  generated_at: "2026-08-20T00:04:00Z",
  nodes: [
    { ...schemaVersion, node_id: "run_m6_01:act", kind: "TASK", label: "Initialize", status: "SUCCEEDED", provider: "DETERMINISTIC", artifact_count: 0, risk: "LOW" },
    { ...schemaVersion, node_id: "run_m6_02:act", kind: "LOOP", label: "Experiment loop", status: "RUNNING", provider: "FAKE", iteration: 2, max_iterations: 4, artifact_count: 1, risk: "LOW" },
    { ...schemaVersion, node_id: "run_m6_03:act", parent_id: "run_m6_02:act", kind: "AGENT", label: "Act", status: "RUNNING", provider: "FAKE", artifact_count: 0, risk: "LOW" },
    { ...schemaVersion, node_id: "observe", parent_id: "run_m6_02:act", kind: "TOOL", label: "Observe", status: "PENDING", provider: "DETERMINISTIC", artifact_count: 1, risk: "LOW" },
    { ...schemaVersion, node_id: "gate", kind: "GATE", label: "Outcome approval", status: "WAITING", provider: null, artifact_count: 0, risk: "HIGH" },
    { ...schemaVersion, node_id: "verify", kind: "VERIFIER", label: "Verify", status: "FAILED", provider: "DETERMINISTIC", artifact_count: 0, verifier_state: "FAIL", risk: "MEDIUM" },
    { ...schemaVersion, node_id: "complete", kind: "TERMINAL", label: "Complete", status: "PENDING", provider: null, artifact_count: 0, risk: "LOW" },
  ],
  edges: [
    { ...schemaVersion, edge_id: "initialize-loop", source: "run_m6_01:act", target: "run_m6_02:act", kind: "NORMAL", active: false, traversal_count: 1 },
    { ...schemaVersion, edge_id: "act-observe", source: "run_m6_03:act", target: "observe", kind: "NORMAL", active: true, traversal_count: 2 },
    { ...schemaVersion, edge_id: "observe-act", source: "observe", target: "run_m6_03:act", kind: "LOOP_BACK", label: "repair", active: true, traversal_count: 2 },
    { ...schemaVersion, edge_id: "loop-gate", source: "run_m6_02:act", target: "gate", kind: "APPROVAL", label: "verified", active: true, traversal_count: 0 },
    { ...schemaVersion, edge_id: "gate-verify", source: "gate", target: "verify", kind: "CONDITION", label: "candidate", active: false, traversal_count: 1 },
    { ...schemaVersion, edge_id: "verify-complete", source: "verify", target: "complete", kind: "CONDITION", label: "accepted", active: false, traversal_count: 0 },
  ],
};

export const loop: components["schemas"]["LoopExecution"] = {
  schema_version: "1.0",
  loop_execution_id: "loop_style_diff_fixture",
  run_id: RUN_ID,
  node_key: "evaluate",
  attempt: 1,
  spec: {
    schema_version: "1.0",
    loop_id: "loop_style_diff_fixture",
    version: "loop-engine-v1",
    max_iterations: 4,
    max_wall_time_seconds: 300,
    max_tool_calls: 20,
    max_turns: 10,
    success_condition: "ACCEPTANCE_POLICY_PASS",
    no_progress_condition: "UNCHANGED_EVIDENCE_FINGERPRINT",
    no_progress_window: 2,
    repeated_failure_threshold: 2,
    provider_failure_threshold: 2,
    escalation_target: "HUMAN",
    verifier_refs: ["command-suite"],
  },
  state: {
    schema_version: "1.0",
    iteration: 2,
    latest_observation_ref: "artifact://iteration-002.patch",
    accumulated_evidence_refs: ["artifact://iteration-001.patch"],
    progress_score: 0.5,
    consecutive_no_progress: 0,
    repeated_failure_count: 1,
    provider_failure_count: 0,
    budget_remaining: { wall_time_seconds: 123, tool_calls: 11, turns: 7, iterations: 2 },
  },
  acceptance_policy_ref: "acceptance-policy-v1",
  status: "SUCCEEDED",
  stop_reason: "VERIFIED_SUCCESS",
  revision: 2,
  created_at: "2026-08-20T00:00:00Z",
  updated_at: "2026-08-20T00:04:00Z",
};

/**
 * One `ERROR` and one `WARNING` finding, because `.finding-error` and `.finding-warning`
 * are two separate rules and a fixture carrying only one of them measures only one.
 */
export const verifications: components["schemas"]["VerificationResult"][] = [{
  schema_version: "1.0",
  verification_id: "ver_style_diff_fixture",
  run_id: RUN_ID,
  iteration_id: "itr_002",
  verifier_id: "command-suite",
  verifier_version: "command-suite-v1",
  target_ref: "artifact://iteration-002.patch",
  status: "FAIL",
  score: 0.25,
  findings: [
    {
      ...schemaVersion,
      code: "TEST_FAILED",
      severity: "ERROR",
      message: "One acceptance test failed.",
      path: "tests/test_feature.py",
      line: 42,
      evidence_ref: "evidence://pytest-output",
      fingerprint: "finding_error_fixture",
    },
    {
      ...schemaVersion,
      code: "COVERAGE_DROPPED",
      severity: "WARNING",
      message: "Coverage fell below the recorded baseline.",
      path: "src/accretion/api/main.py",
      line: 118,
      evidence_ref: "evidence://coverage-report",
      fingerprint: "finding_warning_fixture",
    },
  ],
  evidence_refs: ["evidence://pytest-output", "evidence://coverage-report"],
  false_accept_risk_estimate: 0.8,
  executed_at: "2026-08-20T00:03:00Z",
  duration_ms: 812,
}];

/** One pending gate, which is the only way `.approval-panel` and its actions ever render. */
export const approvals = [{
  approval_id: "apr_style_diff_fixture",
  run_id: RUN_ID,
  node_id: "gate",
  native_request_id: "gate:approve-outcome",
  method: "accretion/gate",
  summary: "Approve the verified outcome before completion.",
  payload: {},
  status: "PENDING",
  decision: null,
  created_at: "2026-08-20T00:03:30Z",
  decided_at: null,
}] satisfies components["schemas"]["ApprovalRecord"][];

/**
 * A `REPLAY_BRANCH` plan rather than the simpler `BEST_OF_N`.
 *
 * Replay is the only mode that renders `.candidate-source-replay`,
 * `.candidate-replay-state`, the seed guidance list and the `.experience-lineage` section,
 * so choosing it here is the difference between measuring the P6/P7 rules and not.
 */
export const searches = [{
  schema_version: "2.0",
  revision: 5,
  status: "SUCCEEDED",
  selected_candidate_id: "candidate_replay",
  stop_reason: "ACCEPTED",
  budget_spent: { schema_version: "2.0", wall_time_seconds: 2, turns: 2, tool_calls: 2 },
  plan: {
    schema_version: "2.0",
    search_id: SEARCH_ID,
    run_id: RUN_ID,
    parent_node_id: "run_m6_03:act",
    graph_revision: 1,
    mode: "REPLAY_BRANCH",
    branch_count: 2,
    max_parallel: 1,
    candidate_directives: [],
    replay_seed_match_ids: ["match_positive"],
    negative_guidance_match_ids: ["match_negative"],
    per_branch_budget: { schema_version: "2.0", wall_time_seconds: 60, max_turns: 2, max_tool_calls: 5 },
    total_budget: { schema_version: "2.0", wall_time_seconds: 120, max_turns: 4, max_tool_calls: 10 },
    verifier_policy_ref: "policy",
    router_policy_version: "performance-router-v2",
    requested_by: "operator",
  },
}] satisfies components["schemas"]["SearchRecord"][];

export const candidates = [
  {
    schema_version: "2.0", candidate_id: "candidate_fresh", search_id: SEARCH_ID,
    run_id: RUN_ID, ordinal: 1, provider: "FAKE", runtime_id: "runtime_fake",
    runtime_model: "default", runtime_version: "fake-p2-v1", source_kind: "FRESH",
    status: "PRUNED", latency_ms: 100, terminal_reason: "control not selected",
    budget_spent: { schema_version: "2.0", wall_time_seconds: 1, turns: 1, tool_calls: 1 },
  },
  {
    schema_version: "2.0", candidate_id: "candidate_replay", search_id: SEARCH_ID,
    run_id: RUN_ID, ordinal: 2, provider: "FAKE", runtime_id: "runtime_fake",
    runtime_model: "default", runtime_version: "fake-p2-v1", source_kind: "REPLAY",
    replay_seed_id: "seed_style_diff", source_experience_id: "exp_positive",
    source_match_id: "match_positive", trajectory_segment_refs: ["segment_workflow"],
    seed_revalidation_status: "ELIGIBLE", seed_revalidation_reasons: [],
    status: "SELECTED", latency_ms: 90, terminal_reason: "selected by scorer",
    budget_spent: { schema_version: "2.0", wall_time_seconds: 1, turns: 1, tool_calls: 1 },
  },
] satisfies components["schemas"]["CandidateTrajectory"][];

export const scores = [
  {
    schema_version: "2.0", score_id: "score_fresh", search_id: SEARCH_ID,
    candidate_id: "candidate_fresh", verifier_policy_ref: "policy", verifier_status: "PASS",
    eligible: true, quality_score: 0.75, cost_proxy: 0.2, latency_proxy: 0.1, risk_score: 0,
    total_score: 0.7, explanation: "accepted", scorer_version: "candidate-scorer-v2",
  },
  {
    schema_version: "2.0", score_id: "score_replay", search_id: SEARCH_ID,
    candidate_id: "candidate_replay", verifier_policy_ref: "policy", verifier_status: "PASS",
    eligible: true, quality_score: 0.9, cost_proxy: 0.1, latency_proxy: 0.08, risk_score: 0,
    total_score: 0.86, explanation: "accepted", scorer_version: "candidate-scorer-v2",
  },
] satisfies components["schemas"]["CandidateScore"][];

export const replaySeeds = [{
  schema_version: "2.0", seed_id: "seed_style_diff", search_id: SEARCH_ID,
  candidate_id: "candidate_replay", match_id: "match_positive",
  experience_id: "exp_positive", segment_ids: ["segment_workflow"],
  procedural_guidance: ["Follow the verified workflow stages in order."],
  required_revalidations: ["Revalidate policy and repository."],
  validation_status: "ELIGIBLE",
}] satisfies components["schemas"]["TrajectorySeed"][];

/**
 * One accepted positive match and one negative-guidance match.
 *
 * Both polarities are needed: `.experience-match` styles them the same, but the lineage
 * inspector renders `NEGATIVE GUIDANCE` only for the second, and the `!important` colour on
 * `.experience-reasons,.experience-revalidations` (styles.css:236) is only reachable when a
 * match actually carries reasons - which is why the negative one does.
 */
export const experienceMatches = [
  {
    schema_version: "2.0", match_id: "match_positive", query_id: "query_style_diff",
    experience_id: "exp_positive", rank: 1, trust: "HIGH", polarity: "POSITIVE",
    assessment: {
      schema_version: "2.0", semantic_score: 0.94, environment_score: 1,
      version_score: 0.93, freshness_score: 1, final_score: 0.966, transfer_risk: 0.07,
      disposition: "ACCEPTED", replay_eligible: true, negative_guidance_eligible: false,
      reasons: [],
    },
  },
  {
    schema_version: "2.0", match_id: "match_negative", query_id: "query_style_diff",
    experience_id: "exp_negative", rank: 2, trust: "MEDIUM", polarity: "NEGATIVE",
    assessment: {
      schema_version: "2.0", semantic_score: 0.8, environment_score: 1,
      version_score: 0.9, freshness_score: 1, final_score: 0.92, transfer_risk: 0.1,
      disposition: "ACCEPTED", replay_eligible: false, negative_guidance_eligible: true,
      reasons: ["Prior attempt regressed the acceptance suite; avoid the same tool order."],
    },
  },
] satisfies components["schemas"]["ExperienceMatch"][];

export function experienceDetail(positive: boolean) {
  return {
    schema_version: "2.0",
    embedding_version: "deterministic-hybrid-384-v1",
    embedding_input_digest: "a".repeat(64),
    experience: {
      schema_version: "2.0",
      experience_id: positive ? "exp_positive" : "exp_negative",
      project_id: PROJECT_ID, repository_identity: "b".repeat(64), task_id: "source_task",
      task_type: "IMPLEMENT", task_family: "api", source_kind: "RUN",
      source_run_id: positive ? "run_verified_source" : "run_failed_source",
      source_commit: "1234567890abcdef", architecture_version: "2.0",
      manifest_digest: "c".repeat(64), policy_digest: "d".repeat(64),
      verifier_digest: "e".repeat(64), prompt_digest: "f".repeat(64),
      context_digest: "1".repeat(64), tool_profile_digest: "2".repeat(64),
      provider: "FAKE", runtime_model: "default", runtime_version: "fake-p2-v1",
      trust: positive ? "HIGH" : "MEDIUM", polarity: positive ? "POSITIVE" : "NEGATIVE",
      outcome: positive ? "VERIFIED_SUCCESS" : "FAILED", content_digest: "3".repeat(64),
      protected_side_effects: false, retracted: false, revision: 1,
    },
    segments: positive
      ? [{
          schema_version: "2.0", segment_id: "segment_workflow", experience_id: "exp_positive",
          ordinal: 1, kind: "WORKFLOW_PATH", content: { nodes: ["act"] },
          content_digest: "4".repeat(64),
        }]
      : [],
  } satisfies components["schemas"]["ExperienceDetail"];
}

const proposalNode = (
  local_id: string,
  kind: components["schemas"]["GraphNodeKind"],
  objective: string,
): components["schemas"]["DynamicWorkflowNodeSpec"] => ({
  schema_version: "2.0", local_id, kind, objective, checkpoint: true, max_attempts: 1,
  risk_level: "LOW", runtime_requirement: "ANY", timeout_seconds: 300,
});

export const proposals = [{
  proposal_id: PROPOSAL_ID, run_id: RUN_ID, planner_version: "fragment-planner-v2",
  confidence: 0.9,
  // Every node is fully specified rather than stubbed to `{ local_id }`: `PlanningReview`
  // filters proposal nodes by `kind === "AGENT"` with no `capability_refs` to decide which
  // ones the P6 search form may attach to, so a partial node would silently empty that
  // list and hide `.search-plan-form` from the mocked planning pass.
  nodes: [
    proposalNode("start", "AGENT", "Draft the change"),
    proposalNode("review", "AGENT", "Review the change"),
    proposalNode("complete", "TERMINAL", "Complete"),
  ],
  edges: [],
  objective: "Port the shell rules into the components layer.",
  planner_runtime: "DETERMINISTIC",
  repair_attempt: 0,
  schema_version: "2.0",
  task_id: TASK_ID,
  fragment_refs: ["single-act-verify@1.0.0"],
  assumptions: ["Budgets remain authoritative.", "No protected side effect is replayed."],
  required_capabilities: ["capability:repo.write", "capability:shell.exec"],
  rationale_summary: "Composed a reviewed workflow fragment.",
}] satisfies components["schemas"]["WorkflowProposal"][];

/**
 * A REJECTED validation, deliberately.
 *
 * An accepted one renders no findings, and `.finding-list li.finding-error>span` and
 * `.finding-list li.finding-warning>span` are exactly the rules this pass exists to reach.
 */
export const validations = [{
  schema_version: "2.0",
  validation_id: "gvl_style_diff", proposal_id: PROPOSAL_ID, status: "REJECT",
  errors: [{
    schema_version: "2.0", code: "PROTECTED_STATE_DROPPED", severity: "ERROR",
    message: "Revision drops a protected state reference.", path: "nodes/review",
    repairable: true,
  }],
  warnings: [{
    schema_version: "2.0", code: "UNVERIFIED_FRAGMENT", severity: "WARNING",
    message: "Fragment has no verifier attached.", path: "nodes/start",
    repairable: false,
  }],
  required_repairs: ["reattach-protected-state"], validator_version: "graph-validator-v2",
}] satisfies components["schemas"]["GraphValidationResult"][];

/** Two revisions, so the timeline, the revision selector and the diff panel all render. */
export const revisions = [
  {
    schema_version: "2.0", run_id: RUN_ID, proposal_id: PROPOSAL_ID, run_graph_id: "rgr_1",
    nodes: [], edges: [], revision_id: "grv_1", revision: 1, reason: "INITIAL",
    normalized_graph_hash: "a".repeat(64), protected_state_refs: [],
  },
  {
    schema_version: "2.0", run_id: RUN_ID, proposal_id: PROPOSAL_ID, run_graph_id: "rgr_2",
    nodes: [], edges: [], revision_id: "grv_2", revision: 2, reason: "HUMAN_REQUEST",
    normalized_graph_hash: "b".repeat(64), protected_state_refs: ["run_m6_01:start"],
  },
] satisfies components["schemas"]["RunGraphRevision"][];

export const replans = [{
  schema_version: "2.0", replan_request_id: "rpr_style_diff", run_id: RUN_ID,
  based_on_graph_revision: 1, reason: "HUMAN_REQUEST", status: "ACTIVATED",
  evidence_refs: ["evidence://pytest-output"], resulting_revision: 2,
  requested_by: "operator", created_at: "2026-08-20T00:03:45Z",
}] satisfies components["schemas"]["ReplanRequest"][];

/**
 * Route table for the mocked run page, in the shape the repository's vitest suites use:
 * URL matching with an explicit `undefined` fall-through.
 *
 * The caller answers `undefined` with a 404 rather than an empty object, deliberately. An
 * unmatched request that silently resolves to `{}` produces a half-rendered page that
 * differs from run to run and looks like a style regression; a 404 names the endpoint the
 * fixture forgot.
 */
export function runFixtureFor(url: string): unknown | undefined {
  if (url.endsWith("/api/v1/me")) return me;
  if (url.includes("/api/v1/runs?")) return [run];
  if (url.endsWith(`/api/v1/runs/${RUN_ID}`)) return run;
  if (url.endsWith("/audit")) return audit;
  if (url.endsWith("/loop")) return loop;
  if (url.endsWith("/graph")) return graph;
  if (url.endsWith("/verifications")) return verifications;
  if (url.includes("/api/v1/approvals")) return approvals;
  if (url.endsWith("/workflow/proposals")) return proposals;
  if (url.endsWith("/validations")) return validations;
  if (url.endsWith("/graph/revisions")) return revisions;
  if (url.includes("/graph/diff")) return graphDiffFixture;
  if (url.endsWith("/runtime-decisions")) return runtimeDecisionsFixture;
  if (url.endsWith("/replans")) return replans;
  if (url.endsWith("/searches")) return searches;
  if (url.endsWith(`/${SEARCH_ID}/candidates`)) return candidates;
  if (url.endsWith(`/${SEARCH_ID}/scores`)) return scores;
  if (url.endsWith(`/${SEARCH_ID}/replay-seeds`)) return replaySeeds;
  if (url.endsWith(`/tasks/${TASK_ID}/experience-matches`)) return experienceMatches;
  if (url.includes("/experiences/exp_")) return experienceDetail(url.endsWith("exp_positive"));
  if (url.endsWith("/api/v1/runtimes")) return [];
  return undefined;
}
