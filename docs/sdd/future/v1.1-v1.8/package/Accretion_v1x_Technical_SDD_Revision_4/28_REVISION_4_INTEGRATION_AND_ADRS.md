# Revision 4 — integrated research decisions and ADRs

**Document revision:** 4  
**Supersedes document revision:** 3  
**Date:** 2026-09-06  
**Classification:** SDD_AMENDMENT, product targets v1.1.0–v1.8.0 unchanged  
**State:** DOCUMENT_AMENDED / IMPLEMENTATION_LOCKED  
**Authority delta:** NONE  
**Independent methods, architecture and security review:** PENDING

Santapong authorized the complete SDD update after reviewing the research package. This records authorization to amend the documents; it does not invent independent review, signed promotion, protocol freeze or experiment approval. The owning SDD sections, shared registry/protocol, schemas, event catalog, synthetic fixtures and acceptance map are updated together. These are the current design requirements within this forward package; they remain subordinate to the unchanged predecessor contracts and entry gates.

## 1. Baseline and preserved constraints

The original Revision 3 archive has SHA-256 `14a41b5ba6748658f82930dd7f91e6a90c6d1cc17dfe2187ed1c9b71bf9dd883`. All 29 payload files are retained under `history/revision-3/`; its manifest covers 28 files excluding itself. Baseline validation passed 19 schemas, 100 synthetic cases, 96 event names and 153 acceptance rows in this task. This historical PASS is design conformance only.

The application repository was inspected read-only at clean `develop@2d2f81c91fe279b245f0b16785c7bff36c07b97e` on 2026-09-06, matching the locally recorded origin ref without a fetch. Relevant owners include independent verification, eligible experience retrieval, lineage splits, rollout, promotion and bounded subprocess execution. A split-assignment helper is not proof of persisted read-time enforcement; fresh execution arms are not exact counterfactual replay; a time/output-limited subprocess is not an operating-system sandbox. These remain implementation review obligations, not new competing services.

Preserved rules include the single v0.4 routing/dispatch owner, immutable evidence and decisions, independent verification, capped TTVS for all allocated tasks, least-trust derivation, v0.10 proposal/promotion ownership, the optional v1.6 closure exception, target-only transfer fallback and unchanged v0.6 physical approval/arming/safety authority.

Final bounded repository recheck observed clean `develop@fa8d865453e2c84d2c6733c985bf4d3f6b397b64` after M10b (#150) landed concurrently. Its nine changed files add development-only pilot statistics, a preregistration draft and replay-only benchmark API; they do not alter the inspected core split, verification, retrieval, rollout or promotion owners. Document 24 names these reusable inputs and their v0.4-specific limits. The draft still has fifteen unfrozen fields, and this task did not run that pilot or edit the repository.

## 2. ADR-R4-001 — independent data roles and typed uncertainty

**Problem.** Earlier text grouped search and calibration, and could be read as allowing a passed exchangeability diagnostic to establish a guarantee. It also suggested conformal prediction coverage could supply an unspecified per-task success bound.

**Decision.** Separate FIT, DEVELOPMENT, CALIBRATION and FINAL_TEST membership and access. Freeze candidate/model/score selection before independent statistical calibration. Final data is used in one predeclared campaign with fixed candidates and repeats. Changing the method after final access needs a new protocol and untouched final cohort. Ordinary independent-role validation rejects shared lineage groups. A method supporting reuse requires a distinct reviewed extension with applicable assumptions.

`UncertaintyStatement` names the target, population, independent unit, method, assumptions, label arrival and guarantee status. Mean confidence, new-outcome prediction, marginal error, time-average error and conditional claims cannot be exchanged. Split-conformal overflow returns a conservative full-support set; insufficient usefulness remains visible. A passed diagnostic challenges neither proof obligations nor label-timing requirements.

**Evidence and alternatives.** [Adaptive Conformal Inference, 2106.00170v3](https://www.alphaxiv.org/abs/2106.00170v3) distinguishes a time-average error result from stronger assumptions and identifies delayed/batched outcomes as an open extension. [SCAPE, 2608.19425v1](https://www.alphaxiv.org/abs/2608.19425v1) motivates scenario correction while its appendix calibration reuse and rank clamping require care. This decision adopts mechanisms under narrower conditions, not reported physical performance. Reusing one development/calibration set by default and treating a diagnostic PASS as proof were rejected.

**Consequences.** Independent cohorts can increase data cost and produce broad or vacuous intervals. An inconclusive result is acceptable; narrowing a bound after observing outcomes is not. The per-task selector keeps its conservative fallback if the required inference scope cannot be supported.

**Ownership and migration.** Shared protocol/evaluator own data-role and uncertainty evidence; v1.7 still owns drift studies. New projections are schema 1.0.0. Existing study/drift projections are schema 2.0.0; old records remain historical and cannot gain missing assumptions by an automatic upcast. Revisit after an applicable data-reuse or delayed-label method is reviewed and implemented.

## 3. ADR-R4-002 — observe interventions and compare mature baselines

**Problem.** A requested profile setting or installed harness may not take effect. Weak starting harnesses, missing cost categories and stochastic retries can exaggerate improvement.

**Decision.** Add `EffectiveSettingObservation` to profiler/evaluator evidence. Bind requested, accepted and observed settings, immutable candidate/configuration identity, supported fields, cache/order conditions and accounting snapshot. UNKNOWN does not establish activation. All allocated trials remain in the primary denominator. Use the strongest governed general harness and record optimizer versus target harness identity. Budget both acquisition arms as complete three-profile batches.

Recovery diagnosis, observed intervention and net recovery value have separate evidence. `OBSERVED_REEXECUTION` replaces the misleading deterministic-execution label in the amended projection. A causal comparison includes repeated no-op and full-retry arms, isolated intervention, restoration and independent outcome evidence. Ambiguous localization remains representable; a single successful rerun is insufficient.

**Evidence and alternatives.** [Prompt-Induced Waste, 2608.01347v5](https://www.alphaxiv.org/abs/2608.01347v5) motivates interaction and activation controls. [HarnessOpt-Bench, 2608.06301v1](https://www.alphaxiv.org/abs/2608.06301v1) makes weak-seed and task-dependent harness effects visible. [Who & When, 2505.00212v3](https://www.alphaxiv.org/abs/2505.00212v3) supports localization as a separate evaluation target. [CausalFlow, 2605.25338v1](https://www.alphaxiv.org/abs/2605.25338v1) contains execution/prediction and gold-access limits; the conflicting GSM8K descriptions remain unresolved. Manifest-only activation, weak-seed-only gains and verbal causal attribution were rejected.

**Consequences.** Observation may be unavailable on some providers; those profiles cannot earn the corresponding mechanism claim. The three-profile 20-trial cap permits six complete tasks, not seven. Two 24-trial acquisition arms plus an 18-trial common holdout total 66 before qualification and extras. This arithmetic does not prescribe a statistically adequate sample size.

**Ownership and migration.** Existing profiler, harness registry and recovery owner remain responsible. Amended acquisition/intervention schema 2 records cannot be populated by fabricating historical observations or no-op trials. Revisit after real trace inventory and runtime support are known.

## 4. ADR-R4-003 — serialize state consumption with revocation

**Problem.** Eligibility checked when materializing a view can become stale before consumption. Persistent and delayed attacks can survive an immediate-session injection test.

**Decision.** Add an immutable dependency snapshot to derivation and a `StateConsumptionReceipt` at the existing state owner. Eligibility, dependency epoch and consumption commit share a transaction or owner-enforced compare-and-swap. Revocation first rejects consumption. Consumption first preserves the already-delivered history; later revocation invalidates descendants and blocks further reuse. Each consequential downstream action still applies its own fresh authority gate. Epochs cannot be reused after rollback.

Test same/later sessions, summary-mediated attacks, source revocation after caching and concurrent invalidation. Separate attack/all-attempts, malicious-write/all-attempts, exposure/committed-malicious-state and benign-utility denominators. Zero eligible denominator is not an observed zero attack rate. Include recommendation-only manipulation and false-positive quarantine cost.

**Evidence and alternatives.** [AgentDojo, 2406.13352v3](https://www.alphaxiv.org/abs/2406.13352v3) motivates utility/security separation and exposes limits of tool filters. The persistent-memory and concurrency extension is an Accretion design hypothesis, beyond that benchmark's same-session results. Two independent service reads, automatic trust elevation and a second memory store were rejected.

**Consequences.** Serialization, invalidation tracking and re-verification add measurable cost. If dependency completeness or the transaction boundary is unavailable, use recompute-all/static fallback or pause. A synthetic ordering model does not prove a distributed implementation.

**Ownership and migration.** Existing State Validator/Committer and experience eligibility owners retain authority. Derivation is schema 2; consumption is a new schema-1 evidence projection. Legacy views require revalidation before fresh use; immutable source history remains readable. Revisit when storage/dispatch integration demonstrates the actual race behavior.

## 5. ADR-R4-004 — stage advanced studies without expanding authority

**Decision.** v1.5 preserves frozen-candidate acquisition first; behavior-linked search is a later independent protocol with repair, preservation, boundary and fresh development confirmation sets. v1.6 may compare fixed, harness-only, adapter-only and alternating arms under a total budget, with development-only selection and joint requalification. v1.7 begins with context-only transfer, separates source-bound content from target descendants and handles empty priors and actual label arrival. v1.8 separates deterministic workflow allocation from scenario calibration; physical benefit remains a separate existing-authority claim.

**Evidence.** [HarnessLens, 2608.27311v1](https://www.alphaxiv.org/abs/2608.27311v1) and [AutoSaddler, 2608.23041v1](https://www.alphaxiv.org/abs/2608.23041v1) motivate behavior-linked screening and fix/regression history. Selected batches are development evidence. [WHALE, 2609.00196v1](https://www.alphaxiv.org/abs/2609.00196v1) motivates coupled harness/weight experiments; its test-based pair selection is rejected. The inspected release notes state paper runs used eight H200s and cleaned launchers were only stub-tested, so no cheap reproduction or runtime readiness is assumed. [CoAdapt-GUI, 2608.11588v1](https://www.alphaxiv.org/abs/2608.11588v1) motivates portable context and empty priors, not robot transfer claims. [Dyserve, 2607.02942v1](https://www.alphaxiv.org/abs/2607.02942v1) motivates workflow controls while its owned serving fleet, optional quality policy and runtime suffix changes are outside this adaptation. SCAPE's scenario mechanism remains a separate target-data hypothesis.

**Rejected transfers.** Final-test checkpoint selection; selected repair batches as population estimates; duplicated learning-memory or training ownership; assumed large GPU experiments; hosted GPU/KV control; mandatory-verifier substitution; marginal intervals as scenario safety; post-freeze physical mutation.

**Consequences and reconsideration.** Optional follow-ups require a trigger, scoped protocol and explicit applicability before observing results. They may be deferred with reason without marking their acceptance PASS. Simpler deterministic and target-only baselines remain valid outcomes. Reassess after the first study establishes whether the next stage is useful and affordable.

## 6. Contract and event compatibility disposition

| Amended projection | New schema | Reason old bytes cannot be silently upgraded |
|---|---|---|
| ResearchStudyPlan | 2.0.0 | Missing data roles, uncertainty/comparison references and candidate identities |
| VerifierQualificationReport | 2.0.0 | Missing accepted/uncertain denominators and explicit bound analysis |
| AdaptiveEvaluationStep | 2.0.0 | Missing frozen-candidate set and conditional single-draw probability semantics |
| ObservableInterventionRecord | 2.0.0 | Actual versus deterministic wording and missing no-op/comparison evidence |
| EvidenceDerivationRecord | 2.0.0 | Missing immutable dependency snapshot |
| DriftEvaluationPlan | 2.0.0 | Missing applicable-assumption, uncertainty and label-arrival evidence |

New schema-1 projections are `ResearchDataRolePlan`, `UncertaintyStatement`, `EffectiveSettingObservation`, `ResearchComparisonPlan` and `StateConsumptionReceipt`. These are evidence attachments under existing owners. Product SemVer and predecessor wire contracts do not change. The prototype qualifier supports fixed-sample binomial examples; other valid inferential methods require their own reviewed extension rather than pretending this validator implements them.

Existing event names keep their subjects and authority. Five new observation-only events record data-role freeze, uncertainty, comparison freeze, effective-setting observation and state-consumption decisions. Consumers resolve the referenced versioned payload and reject unsupported versions. An event or valid locator does not itself prove that the artifact exists, is trusted, is authorized or fits the current cohort.

Migration is explicit: preserve original bytes and schema; read them for history; require new evidence before creating a current qualified record; link the original as provenance; never rewrite old digests. The original 100 synthetic fixtures remain independently reproducible in the historical package. Current schema-2 fixture examples are newly constructed synthetic cases, not migrations of real research results.

## 7. Coverage and review responsibilities

All eight SDDs update scope, component responsibilities, contracts, algorithms, persistence, privacy/authority handling, failures, verification, observability, tests, benchmark, milestone evidence, acceptance, open questions and handoffs. Existing API ownership and physical state machines remain intact. v1.4 adds the consumption event and serialized-use semantics; the shared protocol adds data-role access order.

The acceptance map preserves every earlier stable ID and adds 21 obligations. Every row retains an accountable role, milestone, expected evidence path, pending status and applicable synthetic fixture references. A fixture validates only its named rule; it cannot discharge the complete criterion or prove a methods review occurred.

Independent reviewers still need to assess the statistical method and population, architecture/reference adapters, v1.4 transaction boundary, privacy/security and any later physical safety applicability. Named people, sample sizes, margins, budgets and actual protocol approvals remain data-dependent freeze inputs. This package is complete as a forward design revision; it is not an implementation-ready claim for currently locked releases.

## 8. Validation scope

The current report is `30_REVISION_4_VALIDATION_REPORT.md`; machine-readable results are `31_REVISION_4_VALIDATION_RESULT.json`. Validation covers targeted schemas, semantic rejection cases, normalized lifecycle ordering, inherited regressions, event parity, acceptance-text coverage, links and package integrity. No model training, provider pilot, runtime migration, live activation or physical experiment is performed.
