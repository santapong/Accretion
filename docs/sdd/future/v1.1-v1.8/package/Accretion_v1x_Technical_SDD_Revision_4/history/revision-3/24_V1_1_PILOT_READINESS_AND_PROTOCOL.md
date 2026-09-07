# Accretion v1.1 Profile-Only Pilot Readiness and Protocol

**Document revision:** 3  
**Revised:** 2026-09-05  
**Status:** Protocol draft; `NOT_READY_TO_EXECUTE`  
**Claim boundary:** Feasibility of selecting one fixed compute profile for one declared digital cohort

## 1. Readiness decision

The repository snapshot reviewed for this document is `develop@e66a7b996eeb27a588631a52fb3811c8a3e447f3` (2026-09-05). Repository documentation reports v0.3.0 as the current release and v0.4 work in progress, including a freeze delta for shadow rollout and router activation. That snapshot was used only to align names and prerequisites; its reported test count was not independently reproduced for this SDD review.

| Gate | Evidence needed | State |
|---|---|---|
| Current v0.4 SDD work remains untouched | No revision-3 package file targets the v0.4 repository SDD | SATISFIED_FOR_DOCUMENT_SCOPE |
| Eligible historical trace inventory | Counts by task family, project lineage, verifier, profile, missingness and cost quality | MISSING |
| Three compatible profile definitions | Immutable manifests and final compatibility evidence | MISSING |
| Scope-matched verifier qualification | Labeled correct/incorrect/corrupt/adversarial set and false-acceptance gate | MISSING |
| Target population and cohort floor | Inclusion/exclusion rules and project-disjoint grouping | MISSING |
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

Group by project lineage before splitting. Development and holdout projects do not overlap. Preserve declared floors for critical task families; adaptive sampling cannot remove a cohort from support. Final hidden tasks are opened once after candidate identity and analysis code freeze.

## 4. Candidate profiles

Exactly three profiles enter:

1. `P0_BASELINE`: current strongest governed compatible profile;
2. `P1_ECONOMY`: lower expected total cost, unchanged verifier and authority;
3. `P2_CAPABILITY`: higher expected success on difficult cases, unchanged verifier and authority.

Every manifest binds runtime/model/connection handle/context/tool/harness/retry/escalation/reasoning limits and adapter support. Final compatibility is rechecked after complete assembly. The primary pilot never drops a candidate midstream.

## 5. Staged protocol

### Stage 0 — Verifier qualification

Build a task-family labeled set containing correct, incomplete, subtly incorrect, corrupted-artifact and adversarial outputs. Freeze false-acceptance and false-rejection thresholds. A `PASS` requires the upper false-acceptance bound to meet the ceiling for the declared scope. Model agreement is diagnostic only.

### Stage 1 — Trace inventory

Produce counts and missingness by project, family, profile, verifier and terminal outcome. Report latency clock definitions, overlapping work, provider wait/quota visibility, token/cost quality and replay eligibility. Use these observations to replace the illustrative sample quantities below.

### Stage 2 — Offline replay

Compare full panel, uniform rotating and frozen adaptive acquisition over existing eligible traces. Log the eligible pool, selected task, probability for every eligible task, history and predicted cost basis at every step. Diagnose effective sample size, weight concentration, cohort coverage and rank/selection stability.

### Stage 3 — Bounded development pilot

Randomize acquisition-arm order or run matched independent pools to avoid shared-history leakage. Every acquired task evaluates all three profiles. Freeze a 20–24 profile-trial cap only after inventory-derived precision and budget analysis. Stop for critical verifier, authority, policy or isolation failure; otherwise budget exhaustion is an `INCONCLUSIVE` possibility, not permission to add trials post hoc.

### Stage 4 — Common holdout

Freeze the selected profile and analysis code. Evaluate all three profiles on every common holdout task. The illustrative design uses six tasks across at least six project lineages, producing 18 profile trials. Do not treat this number as powered until baseline variability and intra-project correlation are known.

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
| ACX-P2-011 | Complete separated research cost ledger |

## 9. Decisions still required before execution

Name the cohort and owners; inventory eligible traces; select and content-bind the three profiles; freeze verifier gates; estimate variance and correlation; choose development/holdout sizes; freeze cost sources, propensity floor, stop rules and analysis code; then request separate authority for any new calls. Until those items exist, the correct state is `NOT_READY_TO_EXECUTE`.

