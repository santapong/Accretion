import type { components } from "../../src/api/schema";

import { experienceDetail, experienceMatches, me, PROJECT_ID, TASK_ID } from "./run";

/**
 * The deterministic planning review, for the second fixture-mocked pass.
 *
 * `/tasks/new` renders `PlanningReview` only after a task is created: `planning` is
 * component state, not a query, so no amount of request interception alone will show it.
 * `style-diff.spec.ts` therefore fills the objective and submits the form on both builds -
 * a scripted interaction, driven identically against two servers - and these fixtures are
 * what the two requests behind that submission return.
 *
 * The shapes are the ones `App.test.tsx` builds inline for the same screen, extended where
 * the jsdom suite had no reason to go:
 *
 * - `unknown_features` is non-empty, so `.notice` and its amber left border render at all.
 * - `observed_features` carries four rows, so `.evidence-list` renders as the two-column
 *   grid its rule describes rather than as an empty `<details>` body.
 * - `context_bundle.version` is `context-bundle-v2`, which is the only value that makes
 *   `PlanningReview` load selected experience matches on mount - and therefore the only
 *   way `.experience-match`, `.experience-score-row` and the `!important` colour on
 *   `.experience-reasons` (styles.css:236) are ever painted on this route.
 *
 * Typed against the generated OpenAPI schema for the reason given in `run.ts`: a contract
 * change has to break `npm run check` rather than quietly produce a page the app cannot
 * render.
 */

export const CONTEXT_BUNDLE_ID = "ctx_style_diff_fixture";

export const project: components["schemas"]["Project"] = {
  project_id: PROJECT_ID,
  name: "Style diff fixture",
  repository_path: "/workspace/style-diff",
  created_at: "2026-08-20T00:00:00Z",
} satisfies components["schemas"]["Project"];

export const task = {
  envelope: {
    task_id: TASK_ID,
    project_id: PROJECT_ID,
    objective: "Port the shell rules into the components layer without changing a pixel.",
    task_type: "IMPLEMENT",
    risk_level: "LOW",
    context_policy_ref: "p0-local-worktree-v1",
    prompt_contract_ref: "p0-runtime-v1",
    verifier_policy_ref: "p0-observe-only-v1",
    // Read by `PlanningReview` to size the P6 search form's default budgets. Fixed values
    // keep those `<input>` defaults constant between the two builds.
    budgets: {
      wall_time_seconds: 1800, max_turns: 20, max_tool_calls: 100,
      max_loop_iterations: 1, max_parallel_runs: 2,
    },
  },
  created_at: "2026-08-20T00:00:00Z",
} satisfies components["schemas"]["Task"];

/**
 * Five templates over four modes, so the two `<select>` controls in the override form are
 * populated. An empty template list disables the submit button, which would leave
 * `.primary-button:disabled` measured instead of `.primary-button`.
 */
export const templates = [
  { template_id: "direct-v1", version: "1.0.0", mode: "DIRECT", status: "VALIDATED", checksum: "0".repeat(64) },
  { template_id: "feedback-loop-v1", version: "1.0.0", mode: "LOOP", status: "VALIDATED", checksum: "1".repeat(64) },
  { template_id: "fixed-graph-v1", version: "1.0.0", mode: "GRAPH", status: "VALIDATED", checksum: "2".repeat(64) },
  { template_id: "hybrid-rd-v1", version: "1.0.0", mode: "HYBRID", status: "VALIDATED", checksum: "3".repeat(64) },
  { template_id: "safe-unknown-v1", version: "1.0.0", mode: "HYBRID", status: "VALIDATED", checksum: "4".repeat(64) },
] satisfies components["schemas"]["WorkflowTemplateSummary"][];

const observedFeature = (
  feature: string,
  available: boolean,
  value: components["schemas"]["FeatureEvidence"]["value"],
  rationale: string,
): components["schemas"]["FeatureEvidence"] =>
  ({ feature, available, value, source: "REPOSITORY", rationale });

export const planning = {
  task_id: TASK_ID,
  prompt_contract: {
    schema_version: "1.0", version: "p1-task-execution-v1",
    prompt_contract_id: "pct_style_diff", task_id: TASK_ID, role: "implementer",
    objective: "Port the shell rules into the components layer without changing a pixel.",
  },
  // `context-bundle-v2` is the whole point of this fixture: it is the only version for
  // which `PlanningReview` loads the frozen experience selection on mount.
  context_bundle: {
    schema_version: "2.0",
    context_bundle_id: CONTEXT_BUNDLE_ID,
    version: "context-bundle-v2",
    phase: "TASK_EXECUTION",
    task_ref: TASK_ID,
    project_summary: "Accretion operator UI, mid-Tailwind-port.",
    token_budget: 8_000,
    experience_match_refs: ["match_positive", "match_negative"],
  },
  profile_history: [],
  decision_history: [],
  override_history: [],
  current_profile: {
    schema_version: "1.0", profiler_version: "deterministic-profiler-v1",
    profile_id: "prf_style_diff", task_id: TASK_ID, complexity: 0.68,
    expected_horizon: "MEDIUM", irreversible_actions: false,
    semantic_rationale: "Feedback-dependent implementation against an existing verifier.",
    structure_certainty: 0.5, feedback_dependency: 0.65, dependency_complexity: 0.1,
    parallelism_potential: 0.1, uncertainty: 0.4, verifier_strength: 0.5,
    profile_confidence: 1,
    unknown_features: ["repository.test_command", "repository.lint_command"],
    observed_features: [
      observedFeature("repository.language", true, "python", "Detected from pyproject.toml."),
      observedFeature("repository.has_tests", true, true, "tests/ contains 41 modules."),
      observedFeature("repository.ci", true, "github-actions", "Read from .github/workflows."),
      observedFeature("repository.test_command", false, null, "No command could be derived."),
    ],
    risk: "LOW",
  },
  current_decision: {
    schema_version: "1.0", policy_version: "selector-v1",
    decision_id: "dec_style_diff", selected_mode: "LOOP",
    operator_override_allowed: true,
    selected_template_id: "feedback-loop-v1",
    task_id: TASK_ID, task_profile_ref: "prf_style_diff",
    matched_rules: ["threshold:loop", "verifier:command-suite"],
    alternatives: ["DIRECT", "GRAPH", "HYBRID"],
    rationale: "LOOP is the deterministic selection for a feedback-dependent implement task.",
    requires_approval: false, requires_independent_verifier: true,
  },
} satisfies components["schemas"]["TaskPlanning"];

/**
 * Route table for the mocked planning pass, with the same explicit `undefined`
 * fall-through as `run.ts`.
 *
 * `init` is consulted because two endpoints differ only by method: `GET /api/v1/projects`
 * lists, `POST /api/v1/tasks` creates. Matching on the path alone would answer the task
 * creation with a project list and leave the review unrendered.
 */
export function planningFixtureFor(url: string, method: string): unknown | undefined {
  if (url.endsWith("/api/v1/me")) return me;
  if (url.endsWith("/api/v1/runtimes")) return [];
  if (url.includes("/api/v1/runs?")) return [];
  if (url.endsWith("/api/v1/projects") && method === "GET") return [project];
  if (url.endsWith("/api/v1/tasks") && method === "POST") return task;
  if (url.includes("/api/v1/templates")) return templates;
  if (url.endsWith("/planning")) return planning;
  // The frozen selection and its details, shared with the run-page pass so the two mocked
  // routes paint the same `.experience-match` cards from the same bytes.
  if (url.endsWith(`/tasks/${TASK_ID}/experience-matches`)) return experienceMatches;
  if (url.includes("/experiences/exp_")) return experienceDetail(url.endsWith("exp_positive"));
  return undefined;
}
