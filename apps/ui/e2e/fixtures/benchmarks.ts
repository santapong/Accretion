import type { components } from "../../src/api/schema";

import { me } from "./run";

/**
 * The four frozen benchmark reports, for the fixture-mocked benchmark passes of the
 * computed-style diff.
 *
 * ## What the seed actually renders, and why these fixtures still exist
 *
 * The plan for M9 PR5b predicted that `/benchmarks/*` would render their EMPTY state under
 * `examples/showcase.py`, the way `/tasks/new` renders a blank form. Measured on the seeded
 * backend while this file was written, that is NOT true: the four benchmark endpoints do
 * not read the seeder's rows at all. They serve frozen research corpora that ship with the
 * backend, so a seeded sweep already paints 1,046 elements on `/benchmarks/acr-arch`, 204
 * on `/benchmarks/search`, 136 on `/benchmarks/experience` and 118 on `/benchmarks/dynamic`
 * - tables, gate grids, the quality curve and the provider cards included.
 *
 * That correction is recorded here rather than quietly acted on, because it changes what
 * these fixtures are FOR. They are not the only way the benchmark rules reach a pixel; they
 * are what makes the measurement independent of a corpus the UI does not own, and what
 * reaches the three regions the seeded sweep cannot reach at all:
 *
 * - `.benchmark-detail` and its four child rules, which render only after an operator
 *   clicks a task id in the ACR-ARCH table;
 * - `.benchmark-status`, which renders only after a replay button is pressed;
 * - `.benchmark-table tr.null-result` on more than one row, which needs a corpus whose
 *   null-gain list has more than one entry.
 *
 * So every list below is sized to MATCH the frozen corpus the backend serves - 68 ACR-ARCH
 * scenarios over 30 tasks, three curve points, two providers, twelve P6 tasks, two dynamic
 * treatments over three cohorts, four experience treatments - and the passes then script
 * the two interactions on top. The mocked page is therefore a strict superset of the seeded
 * one, which is exactly what `style-diff.spec.ts` asserts before it trusts a clean diff. A
 * fixture that rendered LESS than the seed would be a weaker measurement wearing a stronger
 * one's name.
 *
 * ## Why it is typed against the generated schema
 *
 * `components` comes from `src/api/schema.d.ts`, which `npm run api:generate` rebuilds from
 * the live FastAPI app and CI compares with `git diff --exit-code`. A renamed or newly
 * required field breaks `npm run check` here, in the same change, rather than producing a
 * fixture that no longer resembles a real response. `stale_rejection_source` is a case in
 * point: `App.test.tsx`'s inline P7 report omits it and nothing complains, because that
 * object is never checked against the schema. This one is.
 *
 * ## Determinism
 *
 * Every value is a literal or a pure function of a row index. Nothing reads a clock, and no
 * rendered string carries an id the backend would randomise per seeding - `benchmark_run_id`
 * and `frozen_at` are present because the contract requires them and are painted by no
 * component. Two measurements taken a second apart against two origins therefore produce
 * byte-identical text, and any difference between them is the stylesheet.
 */

/* ------------------------------------------------------------------------------------- */
/* ACR-ARCH (v1): the architecture comparison suite.                                       */
/* ------------------------------------------------------------------------------------- */

/** The task whose detail card the ACR-ARCH pass opens, and the first row of the table. */
export const ACR_ARCH_TASK_ID = "acr-001";

/** Matches the frozen suite: 68 mode/provider scenarios spread over 30 versioned tasks. */
const ACR_ARCH_SCENARIO_COUNT = 68;
const ACR_ARCH_TASK_COUNT = 30;

/**
 * The filter vocabularies, copied from the frozen corpus.
 *
 * `BenchmarkPage` renders one `<option>` per value in five `<select>`s, so these decide the
 * element count of `.benchmark-filters` as much as they decide what can be filtered. They
 * are the corpus's real lists rather than a shortened sample, for the reason in the header.
 */
const MODES = ["DIRECT", "GRAPH", "HYBRID", "LOOP"] as const;
const PROVIDERS = ["CLAUDE", "CODEX"] as const;
const TASK_TYPES = ["ANALYSIS", "EXPERIMENT", "IMPLEMENT", "REVIEW"] as const;
const VERIFIERS = [
  "command-suite",
  "independent-cross-provider",
  "output-contract",
  "trace-policy",
  "trajectory-policy",
] as const;
const CATEGORIES = [
  "DIRECT_SIMPLE",
  "FEEDBACK_REFINEMENT",
  "PREDICTABLE_GRAPH",
  "HYBRID_ENGINEERING",
  "SAFETY_RECOVERY",
] as const;

/** The replay run every ACR-ARCH metric is attributed to, and the answer to a replay. */
export const ACR_ARCH_RUN_ID = "bnr_style_diff_fixture";

/** `acr-001` … `acr-030`, cycled so 68 scenarios cover 30 tasks the way the corpus does. */
const acrTaskId = (index: number) =>
  `acr-${String((index % ACR_ARCH_TASK_COUNT) + 1).padStart(3, "0")}`;

/**
 * One scenario row, derived entirely from its index.
 *
 * Modular arithmetic rather than random-looking literals: 68 hand-written rows would be 68
 * chances to typo a number that the diff would then report as a real finding, and a reader
 * checking this fixture can verify the whole table from the six lines below.
 *
 * `success` is false on every seventh row so both `StatePill` states - and therefore both
 * `.pill-pass` and `.pill-fail` - are painted, as they are under the seeded corpus.
 */
function acrMetric(index: number): components["schemas"]["ArchitectureMetric"] {
  return {
    schema_version: "1.0",
    metric_id: `acm_style_diff_${String(index).padStart(3, "0")}`,
    benchmark_run_id: ACR_ARCH_RUN_ID,
    benchmark_task_id: acrTaskId(index),
    task_version: "1.0.0",
    category: CATEGORIES[index % CATEGORIES.length],
    task_type: TASK_TYPES[index % TASK_TYPES.length],
    mode: MODES[index % MODES.length],
    provider: PROVIDERS[index % PROVIDERS.length],
    execution_source: "REPLAY",
    verifier_id: VERIFIERS[index % VERIFIERS.length],
    selector_version: "selector-v1",
    success: index % 7 !== 0,
    quality: (index % 40) / 40,
    cost: ((index * 3) % 50) / 100,
    latency: ((index * 7) % 90) / 1000,
    risk: index % 11 === 0 ? 0.125 : 0,
    human_burden: index % 13 === 0 ? 0.25 : 0,
    utility: ((index * 11) % 70) / 100,
    architecture_regret: index % 5 === 0 ? 0.05 : 0,
    duration_ms: 40_000 + index * 137,
    turns: 2 + (index % 7),
    tool_calls: 3 + (index % 11),
    approvals: index % 9 === 0 ? 1 : 0,
    trace_ref: `evals/acr_arch/style-diff-traces.json#${acrTaskId(index)}`,
    environment_ref: "acr-env-direct-simple",
    environment_version: "1.0.0",
  };
}

export const acrArchSummary = {
  schema_version: "1.0",
  suite: "ACR-ARCH",
  suite_version: "1.0.0",
  configuration_version: "1.0.0",
  task_count: ACR_ARCH_TASK_COUNT,
  scenario_count: ACR_ARCH_SCENARIO_COUNT,
  metrics: Array.from({ length: ACR_ARCH_SCENARIO_COUNT }, (_, index) => acrMetric(index)),
  filters: {
    mode: [...MODES],
    provider: [...PROVIDERS],
    task_type: [...TASK_TYPES],
    verifier: [...VERIFIERS],
    selector_version: ["selector-v1"],
  },
} satisfies components["schemas"]["AcrArchSummary"];

/**
 * The versioned task behind `acr-001`.
 *
 * Three success criteria rather than one: `.evidence-list`-style `<ul>` children are the
 * only repeated element inside `.benchmark-detail`, and a single-item list would leave the
 * grid rule measured over a degenerate case.
 */
export const acrArchTaskDetail = {
  task: {
    schema_version: "1.0",
    benchmark_task_id: ACR_ARCH_TASK_ID,
    version: "1.0.0",
    title: "Review a deterministic serializer contract",
    category: "DIRECT_SIMPLE",
    task_type: "REVIEW",
    environment_ref: "acr-env-direct-simple",
    environment_version: "1.0.0",
    verifier_id: "output-contract",
    verifier_version: "1.0.0",
    success_criteria: [
      "The produced artifact satisfies the output contract.",
      "No irreversible action is proposed.",
      "The verifier runs without operator intervention.",
    ],
    budgets: {
      wall_time_seconds: 900,
      max_turns: 12,
      max_tool_calls: 40,
      max_loop_iterations: 1,
      max_parallel_runs: 1,
    },
    applicable_modes: ["DIRECT", "LOOP"],
    selector_mode: "DIRECT",
    selector_version: "selector-v1",
  },
  metrics: [acrMetric(0)],
} satisfies components["schemas"]["BenchmarkTaskDetail"];

/** The answer to "Reproduce replay", which is what puts `.benchmark-status` on the page. */
export const acrArchRun = {
  schema_version: "1.0",
  benchmark_run_id: ACR_ARCH_RUN_ID,
  suite_version: "1.0.0",
  configuration_version: "1.0.0",
  execution_source: "REPLAY",
  status: "COMPLETED",
  corpus_sha256: "a".repeat(64),
  trace_sha256: "b".repeat(64),
  scenario_count: ACR_ARCH_SCENARIO_COUNT,
  started_at: "2026-08-24T00:00:00Z",
  completed_at: "2026-08-24T00:04:00Z",
} satisfies components["schemas"]["BenchmarkRun"];

/* ------------------------------------------------------------------------------------- */
/* P6 (v2): the bounded-search quality-vs-compute curve.                                   */
/* ------------------------------------------------------------------------------------- */

const searchCurvePoint = (
  candidateCount: number,
  acceptedTasks: number,
  meanQuality: number,
  marginalQualityGain: number,
  meanTurns: number,
  meanToolCalls: number,
  meanLatencyMs: number,
): components["schemas"]["SearchBenchmarkCurvePoint"] => ({
  schema_version: "2.0",
  candidate_count: candidateCount,
  task_count: 12,
  accepted_tasks: acceptedTasks,
  acceptance_rate: acceptedTasks / 12,
  mean_quality: meanQuality,
  marginal_quality_gain: marginalQualityGain,
  mean_turns: meanTurns,
  mean_tool_calls: meanToolCalls,
  mean_latency_ms: meanLatencyMs,
});

const searchTask = (
  index: number,
  family: string,
  title: string,
  atOne: number,
  atTwo: number,
  atFour: number,
): components["schemas"]["SearchBenchmarkTaskResult"] => ({
  schema_version: "2.0",
  task_id: `p6-${String(index).padStart(3, "0")}`,
  family,
  title,
  quality_by_candidate_count: { "1": atOne, "2": atTwo, "4": atFour },
  accepted_by_candidate_count: { "1": atOne > 0, "2": atTwo > 0, "4": atFour > 0 },
  selected_provider_at_four: "CLAUDE",
  gain_from_two_to_four: Number((atFour - atTwo).toFixed(3)),
});

/**
 * Twelve held-out tasks, of which TWO have no gain from N=2 to N=4.
 *
 * The second null-gain task is the one deliberate difference from the frozen corpus, which
 * declares one. `.benchmark-table tr.null-result` (styles.css:274) is a PR5b rule, and a
 * single matching row cannot distinguish "the rule applies" from "the rule applies to the
 * first row only" - a real class of CSS mistake, and one a table with one highlighted row
 * would never surface. Nothing else about the report changes.
 */
export const searchBenchmark = {
  schema_version: "2.0",
  benchmark_run_id: "sbr_style_diff_fixture",
  suite_version: "1.0.0",
  configuration_version: "1.0.0",
  selector_version: "verified-best-candidate-v2",
  execution_source: "REPLAY",
  task_count: 12,
  candidate_counts: [1, 2, 4],
  corpus_sha256: "a".repeat(64),
  trace_sha256: "b".repeat(64),
  config_sha256: "c".repeat(64),
  frozen_at: "2026-08-24T00:00:00Z",
  null_gain_task_ids: ["p6-007", "p6-011"],
  curve: [
    searchCurvePoint(1, 8, 0.4725, 0.4725, 1, 1.833, 866.667),
    searchCurvePoint(2, 10, 0.608333, 0.135833, 2, 3.75, 937.5),
    searchCurvePoint(4, 12, 0.768333, 0.16, 4, 8.75, 1091.667),
  ],
  provider_comparison: [
    {
      schema_version: "2.0", provider: "CLAUDE", task_count: 12, accepted_tasks: 12,
      acceptance_rate: 1, mean_best_quality: 0.768333,
    },
    {
      schema_version: "2.0", provider: "CODEX", task_count: 12, accepted_tasks: 11,
      acceptance_rate: 0.916667, mean_best_quality: 0.683333,
    },
  ],
  tasks: [
    searchTask(1, "IMPLEMENT", "Add a typed configuration field", 0.62, 0.78, 0.82),
    searchTask(2, "REVIEW", "Find a boundary-condition regression", 0, 0.7, 0.76),
    searchTask(3, "ANALYSIS", "Explain an orchestration failure", 0.72, 0.72, 0.83),
    searchTask(4, "IMPLEMENT", "Repair a deterministic serializer", 0.8, 0.8, 0.84),
    searchTask(5, "RESEARCH", "Compare two compatible runtime approaches", 0, 0, 0.76),
    searchTask(6, "REVIEW", "Audit a permission boundary", 0.66, 0.69, 0.71),
    searchTask(7, "IMPLEMENT", "Add a fail-closed API control", 0.75, 0.77, 0.77),
    searchTask(8, "ANALYSIS", "Classify a restart inconsistency", 0, 0.65, 0.7),
    searchTask(9, "IMPLEMENT", "Preserve an immutable evidence link", 0.82, 0.82, 0.84),
    searchTask(10, "RESEARCH", "Evaluate a bounded search policy", 0.6, 0.64, 0.69),
    searchTask(11, "REVIEW", "Detect a shared-budget accounting error", 0.73, 0.73, 0.73),
    searchTask(12, "ANALYSIS", "Resolve ambiguous verifier evidence", 0, 0, 0.74),
  ],
} satisfies components["schemas"]["SearchBenchmarkSummary"];

/* ------------------------------------------------------------------------------------- */
/* P5 (v2): the dynamic workflow release gate.                                             */
/* ------------------------------------------------------------------------------------- */

const dynamicTreatment = (
  treatment: components["schemas"]["DynamicTreatment"],
  successfulTasks: number,
  meanQuality: number,
  meanUtility: number,
  meanTurns: number,
  meanToolCalls: number,
  invalidProposalRate: number,
  replanRate: number,
  meanGraphNodes: number,
  meanGraphDepth: number,
  structuralVariationRate: number,
): components["schemas"]["DynamicTreatmentSummary"] => ({
  schema_version: "2.0",
  treatment,
  task_count: 12,
  successful_tasks: successfulTasks,
  success_rate: successfulTasks / 12,
  mean_quality: meanQuality,
  mean_utility: meanUtility,
  mean_turns: meanTurns,
  mean_tool_calls: meanToolCalls,
  mean_latency_ms: 2400,
  invalid_proposal_rate: invalidProposalRate,
  replan_rate: replanRate,
  human_intervention_rate: 0.083333,
  mean_graph_nodes: meanGraphNodes,
  mean_graph_depth: meanGraphDepth,
  structural_variation_rate: structuralVariationRate,
});

/**
 * Both treatments the contract allows.
 *
 * `DynamicTreatment` is `"STATIC" | "DYNAMIC"` and nothing else, so this list cannot be
 * made longer than the corpus's without inventing a value the backend can never send.
 */
export const dynamicBenchmark = {
  schema_version: "2.0",
  benchmark_run_id: "dbr_style_diff_fixture",
  suite_version: "p5-dynamic-workflow-v1",
  configuration_version: "p5-release-gate-v1",
  selector_version: "fragment-planner-v2",
  execution_source: "REPLAY",
  task_count: 12,
  trace_count: 24,
  corpus_sha256: "a".repeat(64),
  trace_sha256: "b".repeat(64),
  config_sha256: "c".repeat(64),
  frozen_at: "2026-08-24T00:00:00Z",
  gate: {
    schema_version: "2.0",
    passed: true,
    research_classification: "POSITIVE",
    benefit_passed: true,
    predictable_non_inferiority_passed: true,
    success_rate_not_regressed: true,
    safety_invariants_passed: true,
    static_fallback_operational: true,
    heterogeneous_uncertain_uplift: 0.16,
    predictable_uplift: 0.005,
    thresholds: { heterogeneous_uplift: 0.05, predictable_non_inferiority: -0.02 },
  },
  treatments: [
    dynamicTreatment("STATIC", 11, 0.681, 0.598, 8.1, 15.4, 0, 0, 5.2, 3.4, 0),
    dynamicTreatment("DYNAMIC", 12, 0.724, 0.652, 9.5, 18.3, 0.083333, 0.416667, 6.6, 4.1, 1),
  ],
  cohorts: [
    {
      schema_version: "2.0", cohort: "PREDICTABLE", task_count: 4,
      static_mean_utility: 0.71, dynamic_mean_utility: 0.715, utility_uplift: 0.005,
      static_success_rate: 1, dynamic_success_rate: 1,
    },
    {
      schema_version: "2.0", cohort: "HETEROGENEOUS", task_count: 4,
      static_mean_utility: 0.45, dynamic_mean_utility: 0.61, utility_uplift: 0.16,
      static_success_rate: 0.75, dynamic_success_rate: 1,
    },
    {
      schema_version: "2.0", cohort: "UNCERTAIN", task_count: 4,
      static_mean_utility: 0.63, dynamic_mean_utility: 0.71, utility_uplift: 0.08,
      static_success_rate: 0.75, dynamic_success_rate: 1,
    },
  ],
  // `DynamicBenchmarkPage` renders the treatments and the cohorts and never this list, so
  // an empty one costs no coverage and keeps the fixture readable.
  tasks: [],
} satisfies components["schemas"]["DynamicWorkflowBenchmarkSummary"];

/* ------------------------------------------------------------------------------------- */
/* P7 (v2): the verified-experience transfer gate.                                         */
/* ------------------------------------------------------------------------------------- */

const experienceTreatment = (
  treatment: components["schemas"]["ExperienceTreatment"],
  successfulTasks: number,
  meanQuality: number,
  qualityUplift: number,
  meanTurns: number,
  meanToolCalls: number,
  toolCallReduction: number,
  falseAccepts: number,
  negativeTransfers: number,
  useRate: number,
  rejectionRate: number,
  nullRate: number,
): components["schemas"]["ExperienceTreatmentSummary"] => ({
  schema_version: "2.0",
  treatment,
  task_count: 20,
  successful_tasks: successfulTasks,
  success_rate: successfulTasks / 20,
  mean_quality: meanQuality,
  mean_turns: meanTurns,
  mean_tool_calls: meanToolCalls,
  mean_latency_ms: 945,
  mean_compute: 12,
  quality_uplift: qualityUplift,
  tool_call_reduction: toolCallReduction,
  false_accepts: falseAccepts,
  negative_transfers: negativeTransfers,
  experience_use_rate: useRate,
  experience_rejection_rate: rejectionRate,
  experience_null_rate: nullRate,
});

const experienceTask = (
  index: number,
  taskType: components["schemas"]["TaskType"],
  title: string,
  negative: components["schemas"]["ExperienceTreatment"][],
): components["schemas"]["ExperienceBenchmarkTaskResult"] => ({
  schema_version: "2.0",
  task_id: `p7-${String(index).padStart(3, "0")}`,
  task_type: taskType,
  family: "transfer",
  title,
  quality_by_treatment: { FRESH: 0.79, SUCCESS_ONLY: 0.75, SUCCESS_FAILURE: 0.77, REPLAY: 0.73 },
  success_by_treatment: { FRESH: true, SUCCESS_ONLY: true, SUCCESS_FAILURE: true, REPLAY: true },
  negative_transfer_treatments: negative,
});

/**
 * All four treatments, and the two negative-transfer results the page reports.
 *
 * `ExperienceBenchmarkPage` lists only tasks whose `negative_transfer_treatments` is
 * non-empty, so the two entries below are what `.experience-benchmark-evidence`'s left
 * column paints; the two clean tasks are carried so the report is a plausible one rather
 * than a corpus in which every task failed.
 */
export const experienceBenchmark = {
  schema_version: "2.0",
  benchmark_run_id: "ebr_style_diff_fixture",
  suite_version: "p7-experience-v1",
  configuration_version: "p7-gate-v1",
  selector_version: "verified-experience-selector-v1",
  execution_source: "REPLAY",
  task_count: 20,
  source_count: 50,
  trace_count: 80,
  source_counts: { POSITIVE: 20, NEGATIVE: 10, STALE_INCOMPATIBLE: 20 },
  corpus_sha256: "a".repeat(64),
  source_sha256: "b".repeat(64),
  trace_sha256: "c".repeat(64),
  config_sha256: "d".repeat(64),
  frozen_at: "2026-08-24T00:00:00Z",
  gate: {
    schema_version: "2.0",
    passed: true,
    false_accepts_not_increased: true,
    stale_rejection_passed: true,
    negative_transfer_passed: true,
    benefit_passed: true,
    success_rate_not_regressed: true,
    stale_rejection_rate: 0.95,
    stale_rejection_source: "DECLARED",
    negative_transfer_rate: 0.033333,
    replay_quality_uplift: 0.0705,
    replay_tool_call_reduction: 0.2,
    thresholds: { stale_rejection: 0.9, negative_transfer: 0.05 },
  },
  treatments: [
    experienceTreatment("FRESH", 19, 0.711, 0, 5, 10, 0, 0, 0, 0, 0, 1),
    experienceTreatment("SUCCESS_ONLY", 19, 0.7455, 0.0345, 4.5, 9, 0.1, 1, 0, 1, 0, 0),
    experienceTreatment("SUCCESS_FAILURE", 19, 0.762, 0.051, 4.2, 8.4, 0.16, 1, 1, 1, 0.5, 0),
    experienceTreatment("REPLAY", 19, 0.7815, 0.0705, 4, 8, 0.2, 1, 1, 1, 1, 0),
  ],
  tasks: [
    experienceTask(10, "IMPLEMENT", "Reuse a stale migration recipe", ["SUCCESS_FAILURE"]),
    experienceTask(11, "REVIEW", "Confirm a verifier contract still holds", []),
    experienceTask(19, "ANALYSIS", "Explain a diverging replay trace", []),
    experienceTask(20, "RESEARCH", "Measure a negative transfer case", ["REPLAY"]),
  ],
} satisfies components["schemas"]["ExperienceBenchmarkSummary"];

/* ------------------------------------------------------------------------------------- */
/* The route table.                                                                        */
/* ------------------------------------------------------------------------------------- */

/**
 * URL matching with an explicit `undefined` fall-through, in the shape `run.ts` and
 * `planning.ts` established.
 *
 * `method` is consulted because three ACR-ARCH endpoints share a prefix and two of them
 * differ only by verb, and because answering a replay POST with a summary would leave
 * `.benchmark-status` reporting a `TypeError` instead of a scenario count.
 *
 * ## The trailing `?` that is not there
 *
 * `api.acrArch({})` requests `/api/v1/benchmarks/acr-arch?` with an EMPTY query string, and
 * `mockApi` rebuilds the path it passes here as `pathname + search` - where `search` is `""`
 * for a bare `?`. Matching `.includes("/api/v1/benchmarks/acr-arch?")`, which is what
 * `App.test.tsx` does against the raw fetch URL, therefore matches nothing here and the
 * page would render its empty state against a 404. The path is compared with its query
 * stripped instead.
 */
export function benchmarkFixtureFor(url: string, method: string): unknown | undefined {
  if (url.endsWith("/api/v1/me")) return me;

  if (url.endsWith("/api/v1/benchmarks/acr-arch/run") && method === "POST") return acrArchRun;
  if (url.includes("/api/v1/benchmarks/acr-arch/tasks/")) return acrArchTaskDetail;
  if (url.split("?")[0].endsWith("/api/v1/benchmarks/acr-arch")) return acrArchSummary;

  if (url.endsWith("/api/v2/benchmarks/search/run") && method === "POST") return searchBenchmark;
  if (url.endsWith("/api/v2/benchmarks/search")) return searchBenchmark;

  if (url.endsWith("/api/v2/benchmarks/dynamic/run") && method === "POST") return dynamicBenchmark;
  if (url.endsWith("/api/v2/benchmarks/dynamic")) return dynamicBenchmark;

  if (url.endsWith("/api/v2/benchmarks/experience/run") && method === "POST") {
    return experienceBenchmark;
  }
  if (url.endsWith("/api/v2/benchmarks/experience")) return experienceBenchmark;

  return undefined;
}
