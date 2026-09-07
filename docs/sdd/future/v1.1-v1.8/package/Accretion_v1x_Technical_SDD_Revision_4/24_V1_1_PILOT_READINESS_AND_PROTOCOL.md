# Accretion v1.1 Profile-Only Pilot Readiness and Protocol

**Document revision:** 4  
**Supersedes document revision:** 3  
**Revised:** 2026-09-06  
**Status:** Protocol draft; `NOT_READY_TO_EXECUTE`  
**Claim boundary:** Feasibility of selecting one fixed compute profile for one declared digital cohort

## 1. Readiness decision

The current read-only design snapshot is clean `develop@2d2f81c91fe279b245f0b16785c7bff36c07b97e` on 2026-09-06, matching the locally recorded origin reference without a fetch. This verifies names and relevant owner locations, not v1.x readiness or the existence of a complete trial panel. Existing v0.4-v1.0 implementation gates remain authoritative.

During this update M10b (#150) landed at clean `develop@fa8d865453e2c84d2c6733c985bf4d3f6b397b64`. Its `src/accretion/routing/pilot.py`, `scripts/router_pilot.py` and replay-only benchmark API provide useful variance, repetition and denominator inspection paths. Its `docs/research/v0.4/preregistration.md` still marks all fifteen fields TBD. Reuse those components only after checking their estimand and data eligibility: the v0.4 utility objective, trial-based false-acceptance rate, synthetic corpus and proposed repetition floors are not automatically v1.1 capped-TTVS or verifier-qualification evidence. No pilot was run for this SDD update.

| Gate | Evidence needed | State |
|---|---|---|
| Current v0.4 SDD work remains untouched | No revision-4 package file targets the v0.4 repository SDD | SATISFIED_FOR_DOCUMENT_SCOPE |
| Eligible historical trace inventory | Counts by task family, project lineage, verifier, profile, missingness and cost quality | MISSING |
| Three compatible profile definitions | Immutable manifests and final compatibility evidence | MISSING |
| Scope-matched verifier qualification | Labeled correct/incorrect/corrupt/adversarial set and false-acceptance gate | MISSING |
| Target population and cohort floor | Inclusion/exclusion rules and project-disjoint grouping | MISSING |
| Effective-setting observability | Requested/accepted/observed setting fields, adapter support, cache and order policy | MISSING |
| Complete acquisition/holdout budget | Separate adaptive/uniform batches, qualification, repeats, retries and holdout | MISSING |
| Frozen cost basis | Provider/runtime/pricing snapshot plus wait/quota accounting | MISSING |
| Protocol owner and independent evaluator | Named people and conflict separation | MISSING |
| Paid/new execution authority | Separate explicit authorization after reviewable protocol | NOT_REQUESTED |

Read-only trace inventory and protocol construction may proceed. New evaluations, paid provider calls and live activation must wait.

## 2. Primary question and estimands

**Question:** Under a frozen total development budget, can adaptive task acquisition choose the same useful fixed profile as a full or uniform panel while reducing total research cost?

Primary endpoints:

1. independently verified success non-inferiority on the common holdout;
2. capped TTVS difference on the common holdout;
3. selection regret relative to the best of the three profiles on that holdout;
4. total acquisition plus evaluation cost, with evaluation count, tokens, provider wait/quota time, wall time, local compute, money, verifier, selector and human cost separated.

The pilot does not estimate conditional routing benefit. It selects at most one fixed profile for later study.

## 3. Population and split

Eligible units are repeatable low-risk digital tasks with immutable inputs, project lineage, independent executable verification, replay permission, complete terminal status and usable timing/cost evidence. Exclude physical/irreversible actions, secret-bearing traces without approved redaction, tasks whose verifier is the producing model alone, contaminated tasks and episodes without a frozen horizon.

Group by project lineage before splitting. Development and holdout projects do not overlap. If a statistical calibration artifact is needed, reserve an independent calibration group after development selection; the fixed-profile pilot does not require adding conformal calibration without a useful estimand. Preserve declared floors for critical task families; adaptive sampling cannot remove a cohort from support. Final hidden tasks are opened once after candidate identity and analysis code freeze.

## 4. Candidate profiles

Exactly three profiles enter:

1. `P0_BASELINE`: current strongest governed compatible profile;
2. `P1_ECONOMY`: lower expected total cost, unchanged verifier and authority;
3. `P2_CAPABILITY`: higher expected success on difficult cases, unchanged verifier and authority.

Every manifest binds runtime/model/connection handle/context/tool/harness/retry/escalation/reasoning limits and adapter support. Final compatibility is rechecked after complete assembly. The primary pilot never drops a candidate midstream. Each trial records `EffectiveSettingObservation`: requested/accepted/observed fields, immutable effective configuration, cache condition, order and accounting snapshot. Do not collect hidden reasoning. Unsupported/ignored settings block intended-profile readiness, and any allocated failed trial remains in intention-to-treat outcomes.

## 5. Staged protocol

### Stage 0 — Verifier qualification

Build a task-family labeled set containing correct, incomplete, subtly incorrect, corrupted-artifact and adversarial outputs. Freeze false-acceptance and false-rejection thresholds. A `PASS` requires the upper false-acceptance bound to meet the ceiling for the declared scope. State the independent unit, confidence level and interval method. The projection fixture uses an independently labeled fixed-sample binomial bound; clustered or adaptive qualification needs its own reviewed analysis. Report both errors among confirmed-incorrect outputs and confirmed errors among all accepted outputs, retaining accepted outputs with uncertain labels as a separate possible-error range. Model agreement is diagnostic only.

### Stage 1 — Trace inventory

Produce counts and missingness by project, family, profile, verifier and terminal outcome. Report latency clock definitions, overlapping work, provider wait/quota visibility, token/cost quality and replay eligibility. Use these observations to replace the illustrative sample quantities below.

### Stage 2 — Offline replay

Compare full panel, uniform rotating and frozen adaptive acquisition over existing eligible traces. Use complete profile-by-task observations; absent cells cannot be imputed as verified outcomes. Each acquisition algorithm has its own revealed history. Charge historical collection once as dataset construction and separately report each replay computation; do not claim those historical calls were actually avoided. Log the eligible pool, selected task, probability for every eligible task, history and predicted cost basis at every step. Diagnose effective sample size, weight concentration, cohort coverage and rank/selection stability.

### Stage 3 — Bounded development pilot

Randomize acquisition-arm order or run matched independent pools to avoid shared-history leakage. Every acquired task evaluates all three profiles. Freeze separate complete-batch caps for both arms only after inventory-derived precision and budget analysis. A 20-trial cap permits six complete three-profile tasks (18 trials), and a 24-trial cap permits eight. Freeze repeated-run support, difficulty/family strata, execution ordering and cache controls. Stop for critical verifier, authority, policy or isolation failure; otherwise budget exhaustion is an `INCONCLUSIVE` possibility, not permission to add trials post hoc.

### Stage 4 — Common holdout

Freeze the selected profile and analysis code. Evaluate all three profiles on every common holdout task. As arithmetic only, six tasks across at least six project lineages with one run/profile produce 18 profile trials. This holdout cost is additional to both acquisition arms and qualification. Do not treat this number as powered until baseline variability and intra-project correlation are known.

### 5.1 Complete trial and cost ledger

For exactly three profiles, define:

`total_profile_trials = 3 * sum(tasks_arm * repeats_arm) + 3 * holdout_tasks * holdout_repeats + qualification_profile_trials + extra_profile_trials`.

The arm sum includes adaptive and uniform acquisition. A development full-panel baseline is either separately collected and counted or reused from an existing complete panel with provenance; never count the same call twice or omit its acquisition cost. Repeated verifier invocations, provider retries, failed attempts, local processing and human review additionally appear in their own units and the full cost ledger. The `extra_profile_trials` term includes additional complete-profile executions only; it is not a replacement for token/money/time accounting.

Example: two arms each using eight tasks at one run/profile consume 48 profile trials; six holdout tasks consume 18 more, totaling 66 before qualification and extras. With four qualification and two extra profile trials, the fixture total is 72. These examples are neither quotas nor recommended sample sizes. Unused budget under an incomplete final batch remains unused. No post-hoc sample expansion rescues an inconclusive result.

## 6. Adaptive acquisition rule

At step `t`, the rule may use only frozen task metadata and observations available before step `t`. It scores expected discrimination divided by predicted total evaluation cost, then mixes with a uniform floor so every eligible task has non-zero probability. The exact mixture, clipping, batch size and stopping rule freeze before Stage 3.

The selector may choose the next task; it may not choose which profiles run on that task. A separately budgeted uniform rotating arm is mandatory. If predicted-cost error or propensity concentration crosses its frozen limit, acquisition falls back to uniform and the event is recorded.

## 7. Analysis

- Use paired task-level profile contrasts and project-aware uncertainty.
- Report design-weighted development estimates only with the frozen estimator and propensities.
- Make the release decision from the common holdout, not the adaptively sampled development estimate.
- Report selection regret and the probability/uncertainty that the selected candidate differs from the observed best.
- Report all failures, timeouts, unresolved verification and missing measurements; non-PASS by horizon contributes the full horizon.
- Run sensitivity for workload/amortization horizons instead of assuming future production volume.

## 8. Acceptance mapping

| Criterion | Required artifact |
|---|---|
| ACX-P2-006 | `VerifierQualificationReport` and labeled-set manifest |
| ACX-P2-007 / AC11-C7-020 | `AdaptiveEvaluationStep` stream, uniform arm and propensity/ESS report |
| ACX-P2-008 / AC11-C7-021 | Frozen candidate decision and three-profile common-holdout matrix |
| ACX-P2-009 | One-use final-holdout access receipt |
| ACX-P2-011 / AC11-C7-023 | Complete separated research cost ledger and batch arithmetic |
| AC11-C1-022 | Effective-setting, cache/order and accounting observations |
| ACX-P3-012–015 | Data-role, uncertainty and comparison-plan validity |

## 9. Decisions still required before execution

Name the cohort and owners; inventory eligible traces; select and content-bind the three profiles; freeze verifier gates; estimate variance and correlation; choose development/holdout sizes; freeze cost sources, propensity floor, stop rules and analysis code; then request separate authority for any new calls. Until those items exist, the correct state is `NOT_READY_TO_EXECUTE`.
