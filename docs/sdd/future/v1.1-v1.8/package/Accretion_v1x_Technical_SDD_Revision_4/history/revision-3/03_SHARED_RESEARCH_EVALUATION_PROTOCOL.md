# Accretion v1.x Shared Research and Evaluation Protocol

**Document revision:** 3  
**Revised:** 2026-09-05

**Status:** Normative minimum protocol for every v1.x learning or optimization claim  
**Applies to:** v1.1.0-v1.8.0  
**Scientific priority:** Verified correctness first, TTVS second, cost third

---

## 1. Purpose

This protocol prevents a v1.x release from claiming improvement because it made fewer LLM calls while hiding retries, verifier failures, lower correctness, task leakage, offline-search cost, or physical risk.

Each release must freeze a release-specific `ResearchProtocol` before training, search, or final evaluation. This shared document defines the minimum content; individual SDDs add release-specific baselines, cohorts, and ablations.

## 2. Primary estimand

Freeze one observation horizon \(H>0\), the task/node unit, and READY boundary per cohort before allocation. Let \(T_{pass}\) be elapsed wall time to committed independent PASS; set it to infinity when no PASS is established by H. The primary estimand is **capped time to verified success**, not time to any terminal state:

\[
TTVS_H(x,\pi)=\min(T_{pass},H)
\]

It includes:

- queue and admission time when controlled by the evaluated system;
- routing/planning/compilation lookup time on the live path;
- model inference and tool execution;
- context construction and state/harness access;
- independent verification;
- failed attempts;
- repair, reroute, and escalation;
- terminal evidence commit.

Report service wait caused by uncontrolled external provider outage separately, but do not silently delete it. Predeclare whether provider queue time is part of the product estimand.

The primary efficiency comparison is:

\[
\Delta_{TTVS}=E[TTVS_{candidate}-TTVS_{baseline}]
\]

Evaluate the paired difference using every allocated task (intention to treat). FAIL, timeout, budget stop, denial, unresolved INCONCLUSIVE, ERROR, QUARANTINED, withdrawal, and PASS after H contribute H; none is a fast success or an independently censored observation. Also report verified-success probability by H and the observed time-to-terminal (TTT) separately. Successful-only latency is descriptive, never the primary gate. An unresolved/missing clock is UNKNOWN, never zero; missing PASS timing conservatively contributes H and blocks a claim when the frozen missing-data threshold is exceeded. A candidate must independently pass success-floor and non-inferiority gates globally and in each critical cohort.

Node and workflow clocks are distinct estimands. A workflow uses READY to committed workflow PASS; overlapping node spans are not summed into workflow wall time. Attribute spans using union/critical-path accounting and report total compute separately. Report raw segments, TTT, TTVS_H, horizon, terminal reason, independent result reference, and measurement quality. A human pause remains on the wall clock. No outcome is dropped because a provider failed or a human declined approval. Service-time-only analysis is secondary.

For example at H=100 ms, an early FAIL at 1 ms contributes 100 ms, a PASS at 60 ms contributes 60 ms, and an unresolved task contributes 100 ms. Capping the raw terminal duration would incorrectly reward early abandonment.

## 3. Verification denominator

Canonical terminal classes are:

```text
PASS | FAIL | INCONCLUSIVE | ERROR | QUARANTINED
```

- Only `PASS` is verified success.
- `INCONCLUSIVE` is not a negative label for training unless resolved, but it is not a success in release reporting.
- `ERROR` remains visible as system/recovery evidence.
- `QUARANTINED` is excluded from eligible learning data but included in safety/incident reporting.
- Timeouts and budget stops remain in the intention-to-treat denominator.

### 3.1 Verifier qualification before optimization

A release may optimize only against verifier labels covered by a frozen `VerifierQualificationReport`. Qualification crosses known-correct and known-incorrect artifacts with complete, partial, stale, wrong-commit, fabricated and adversarial evidence. Deterministic mutation provenance is preferred; semantic gold labels require independent blinded adjudication with unresolved cases retained as `UNCERTAIN`.

Report both denominators:

\[
FAR_{incorrect}=\frac{incorrect\ accepted}{all\ confirmed\ incorrect},\qquad
ErrorAmongAccepted=\frac{incorrect\ accepted}{all\ accepted}
\]

Also report false rejection among confirmed-correct artifacts, `INCONCLUSIVE` coverage, critical-claim false acceptance, producer/verifier family, evidence provenance, confidence intervals and a zero-event upper bound. Model-family diversity is a study factor, not proof of independence. A changed verifier, evidence schema, task family or material runtime invalidates the applicable qualification scope until re-evaluated. Failure to meet the frozen false-acceptance ceiling blocks learning, promotion and any efficiency claim that uses those labels.

## 4. Hard gates

Average efficiency cannot compensate for any of the following:

- critical false acceptance;
- verified-success below the preregistered floor or non-inferiority boundary;
- policy or authority violation;
- secret exposure to a model-visible surface;
- workspace/tenant isolation failure;
- unapproved physical execution;
- physical safety regression or unavailable stop path;
- lost/rewritten evidence or contradiction;
- missing decision or promotion lineage;
- inability to roll back to the predecessor baseline.

A hard-gate event produces `FAIL` for promotion even when mean TTVS improves.

## 5. Secondary metrics

Every applicable release reports:

| Metric | Definition requirement |
|---|---|
| Verified success rate | Independent verifier PASS / intention-to-treat tasks |
| Critical false acceptance | Accepted output later confirmed materially incorrect |
| Compute per verified success | Total controlled compute divided by verified PASS count |
| Cost per verified success | Total direct and allocated optimization cost / verified PASS count |
| Frontier-call rate | Frontier invocations / eligible tasks and / verified PASS |
| Frontier-call avoidance | Baseline frontier calls avoided without later frontier escalation |
| Escalation rate | Tasks moving to a more capable/costly tier |
| Retry/repair count | All attempts, not only final attempt |
| p50/p95 capped TTVS_H | Intention-to-treat and cohort-stratified; a percentile equal to H means the success horizon was reached, not an observed success time |
| Verification burden | Verifier time, calls, compute, and human review |
| Calibration | Brier/log loss, reliability curves, expected calibration error with limitations |
| Abstention quality | Risk/accuracy of handled versus abstained cohorts |
| OOD behavior | Coverage, detection, fallback, and outcomes |
| Reproducibility | Replay success within preregistered tolerance |
| Human burden | Review/override/preparation time when applicable |

“90% fewer frontier calls” is invalid if the system replaces them with enough retries to increase TTVS or reduce verified success.

## 6. Total cost accounting

Separate and report:

```text
online execution cost
online verification cost
repair/escalation cost
offline profiling cost
offline search/training cost
human labeling/review cost
infrastructure amortization
provider waiting and quota consumption
selector/acquisition computation
discarded, screened and failed candidates
```

For an optimizer requiring offline work, report both:

1. Online steady-state performance;
2. Amortized performance at predefined workload horizons.

The break-even task count is:

\[
N_{break-even}=\frac{C_{offline}}{C_{baseline/task}-C_{candidate/task}}
\]

when the denominator is positive. If it is non-positive, there is no cost break-even under the evaluated conditions.

Evaluation count, token count, elapsed wall time, controlled compute and money are separate outcomes. Parallel work uses elapsed-union or critical-path accounting for wall time and sums resource use for compute. A method that samples longer tasks can reduce evaluation count without a proportional time or cost reduction. Historical matrix collection is reported as dataset acquisition cost even when a replay simulator later chooses fewer cells.

## 7. Dataset and cohort design

### 7.1 Required splits

- Training/development;
- Held-in validation for search and calibration;
- Project-disjoint hidden holdout;
- Time-forward holdout for drift;
- Critical cohort suite;
- OOD/adversarial cohort;
- Domain/embodiment-disjoint split when transfer is claimed.

Repositories, project lineage, near-duplicate tasks, generated variants, solution artifacts, and verifier fixtures must not leak across project-disjoint boundaries.

### 7.3 Holdout access and selection regret

Development and calibration results may choose the method, thresholds and candidate. Pilot holdout estimates feasibility and selection error; final hidden holdout is opened once after the confirmatory protocol, candidate identity and analysis code are frozen. If the claim concerns selecting the best of several profiles or harnesses, the holdout must evaluate every compared candidate on the same tasks, or the report must state that selection regret is not identifiable. Evaluating only the selected candidate establishes its observed performance, not that selection was correct.

All forks, versions, generated mutations and upstream-derived repositories share one lineage group. Any change after final-hidden access requires a new protocol revision and a new untouched final holdout.

### 7.2 Cohort dimensions

At minimum, stratify by:

- domain and node kind;
- task novelty/difficulty;
- runtime/model/harness family;
- required tools and tool-schema complexity;
- context size;
- verifier type and strength;
- risk class;
- project/workspace adaptation level;
- failure ownership;
- supported, experimental, or unsupported capability profile.

## 8. Baseline policy

Every experiment includes:

1. Predecessor released Accretion version with its strongest governed configuration;
2. Fixed frontier configuration;
3. Cheapest compatible configuration;
4. Simple deterministic heuristic;
5. The strongest directly relevant non-learned/learned baseline feasible at the registered scale;
6. Candidate method;
7. Post-hoc oracle or empirical upper bound when ethically and computationally feasible.

The post-hoc oracle cannot be used by the live candidate.

## 9. Experimental design

- Use paired task instances where possible.
- Randomize method order/provider timing when order effects matter.
- Repeat stochastic executions according to power analysis.
- Pin prompts, tool/harness versions, model identifiers, reasoning settings, environments, verifiers, and code.
- Record provider/platform drift and rerun sensitivity cohorts when material.
- Use identical hard budgets unless the estimand explicitly compares budget policies.
- Preserve all failed, negative, and inconclusive runs.
- Analyze intention-to-treat first; per-protocol results are secondary.

### 9.1 Adaptive evaluation and acquisition

Adaptive task, trace or rollout acquisition is allowed only in development/calibration. Before the first adaptive choice, freeze the target population, eligible units, sampling unit, with/without-replacement design, minimum inclusion support, cohort floors, cost-estimation inputs, estimator, clipping rule, effective-sample-size gate and stopping rule.

Each selection records the complete eligible-set digest, history digest, exact inclusion probability when analytically available, any estimated-propensity method and error, selected unit, cohort-floor reason, predicted cost using information available at selection time, all candidates evaluated on the selected snapshot, and realized outcomes/costs. Every target unit represented in a population claim retains positive inclusion support. Critical-cohort floors do not replace population-wide positivity.

The primary v1.1 pilot evaluates all three fixed surviving profiles on each selected task to preserve paired comparisons. Sequential candidate elimination is a separate preregistered ablation after the profile-only pilot; it requires anytime-valid inference or frozen looks. Adaptive raw means are never reported as population estimates. Report maximum weight, effective sample size, coverage, estimator sensitivity and the result of a uniformly rotating-panel baseline.

## 10. Statistical gate

Before evaluation, register:

- primary hypothesis and estimand;
- verified-success floor/non-inferiority margin;
- minimum meaningful TTVS effect;
- sample-size/power method;
- confidence interval method;
- handling of stochastic repeats and clustered projects;
- fixed observation horizon and H assignment for timeout, failure, withdrawal, and unresolved outcomes; survival analysis, if used, is secondary and treats terminal failures as competing outcomes;
- missing/error policy;
- outlier policy;
- multiple-comparison correction;
- stopping rule;
- cohort and safety gates.

Use cluster-aware or hierarchical analysis when multiple tasks/runs share a project. Report effect sizes and confidence intervals. A p-value alone is insufficient.

Formal calibration or conformal language requires its assumptions to be recorded and tested. When exchangeability, monotonicity or signal availability is implausible under provider, time or domain shift, withdraw the formal guarantee and report empirical time-forward coverage, alarm delay, false alarms, abstention and fallback cost. Intermediate-token or hidden-state methods are out of scope for a hosted runtime that exposes only final outputs unless the supported interface is independently verified.

## 11. Promotion decision

Three separate decisions are required: **platform readiness** (operational/invariant gates), **research closure** (PASS/FAIL/INCONCLUSIVE or explicit DEFERRED before running), and **artifact promotion** (only after a PASS claim and human approval). Research FAIL never means platform PASS automatically; platform tests must pass independently. Document revision 3 and its fixtures do not establish any of these gates. The v1.6 optional-lab exception and dependency rules remain defined in the revision-2 addendum; revision 3 adds research prerequisites without weakening them.

Release claim outcomes:

```text
PASS
FAIL
INCONCLUSIVE
```

`PASS` requires:

- hard gates pass;
- verified-success constraint passes;
- primary efficiency effect meets the preregistered minimum and uncertainty criterion;
- critical cohorts do not regress;
- required ablations and replay artifacts are complete;
- rollback is proven;
- human promotion approval is recorded.

`INCONCLUSIVE` applies when evidence is underpowered, materially conflicted, contaminated, or non-reproducible. It does not become `PASS` through narrative judgment.

## 12. Shadow, canary, and online stages

```text
OFFLINE_REPLAY
→ SHADOW
→ CANARY_LOW_RISK_DIGITAL
→ ACTIVE_ELIGIBLE_COHORT
```

- Shadow decisions never change execution.
- Canary scope, budget, rollback trigger, and observation window are frozen.
- Online exploration is allowed only for approved low-risk digital cohorts.
- Physical/high-risk execution never enters online exploration.
- Cohort-specific regressions trigger cohort rollback even when global averages improve.

## 13. Required shared ablations

When applicable, remove or replace one element at a time:

- compatibility pruning;
- uncertainty/OOD abstention;
- independent verification feedback;
- local versus final-run feedback;
- offline portfolio compilation;
- specialized harness selection;
- targeted recovery;
- state coordination;
- project adaptation;
- cross-domain weak prior;
- cost-aware objective;
- human override path.
- uniform rotating acquisition instead of learned acquisition;
- verifier qualification/provenance packet;
- frozen versus periodic versus drift-triggered recalibration;
- observable replay versus full retry;
- trust-preserving derivation/invalidation.

Each release SDD adds method-specific ablations.

## 14. Leakage and reward-hacking controls

- Hidden holdout is inaccessible to proposer/trainer/router.
- Verifier implementation and hidden fixtures are not exposed in model context unless the protocol explicitly tests white-box verification.
- Candidate cannot edit the evaluator, policy, audit, or test oracle.
- Detect suspicious output/test coupling, fixture memorization, reward tampering, and evaluator-specific artifacts.
- Run alternate/cross-verifier checks on sampled accepted outputs.
- Quarantine any implicated experiences, features, policies, harnesses, and adapters by lineage.

## 15. Reproducibility bundle

Every claim exports:

- frozen protocol and analysis plan;
- task and split manifests with leakage audit;
- source commit and dependency lock;
- runtime/model/provider identifiers;
- configuration, harness, policy, and verifier digests;
- environments/container/simulator manifests;
- raw result/evidence references;
- decision receipts and propensities;
- verifier qualification and gold-label adjudication;
- adaptive eligible-set, inclusion and cost-estimate ledgers;
- holdout access log and frozen analysis digest;
- drift/assumption assessment and guarantee status;
- statistical analysis and plots;
- negative/inconclusive results;
- cost and compute accounting;
- limitations and known non-reproducible external dependencies.

## 16. Threats to validity

Every release analyzes at least:

- benchmark representativeness;
- provider/model drift;
- verifier validity and independence;
- task leakage and generated-task artifacts;
- repeated-measure/project dependence;
- optimizer overfitting;
- survivor and selection bias;
- cost-price instability;
- local versus hosted latency differences;
- human-review inconsistency;
- transfer validity;
- physical measurement/calibration limits when applicable.

## 17. Negative-result rule

A technically correct implementation can fail its research claim. In that case:

1. Preserve and publish/export the negative evidence according to project policy;
2. Do not promote the candidate as the default;
3. Keep the predecessor baseline active;
4. Identify whether the failure is method, data, verification, implementation, or scope;
5. Amend a future SDD only through the research-intake process;
6. Do not lower the success floor after seeing results.

## 18. Release-specific primary comparisons

| Release | Candidate | Required predecessor comparison |
|---|---|---|
| v1.1 | TTVS-aware compute router/cascade | v1.0.0 strongest governed routing |
| v1.2 | Specialized harness portfolio | v1.1 general/single harness |
| v1.3 | Learned targeted recovery | v1.2 deterministic recovery/escalation |
| v1.4 | Learned state coordination | v1.3 static/always-on state access |
| v1.5 | Retrospective harness optimizer | v1.4 human-maintained and bounded optimizer baselines |
| v1.6 | Adapted open-weight policy/adapter | v1.5 frozen base model/harness and training baselines |
| v1.7 | Cross-domain efficiency weak prior | Target-only scratch adaptation |
| v1.8 | Frozen physical efficiency advisory | v1.0.0/v1.7 governed manual/static physical preparation |

Each release also owns the following focused research question:

| Release | Focused study | Required falsifier |
|---|---|---|
| v1.1 | Cost-aware acquisition for three fixed compatible profiles, followed later by per-node routing | Uniform rotating panels match the candidate within the frozen meaningful effect |
| v1.2 | Factorial harness effect and joint harness/compute selection | Apparent harness benefit disappears after compute-profile control or maintenance cost |
| v1.3 | State-restorable observable-component intervention and value-of-replay | Full retry is cheaper/more reliable or restoration/side effects are unsafe |
| v1.4 | Trust-preserving state derivation, poisoning resistance and selective invalidation | Summaries elevate authority, contamination propagates, or recompute-all is preferable |
| v1.5 | Adaptive trace acquisition for bounded retrospective harness search | Uniform/diversity sampling yields equivalent finalists at lower total cost |
| v1.6 | Verifier-qualified pseudo-label and adaptation calibration | Consensus/reward signal is unreliable or amortized benefit does not break even |
| v1.7 | Matched-budget recalibration and weak-prior transfer under time/domain shift | Target-only scratch wins or negative-transfer boundaries trigger |
| v1.8 | Fixed-graph workflow allocation in simulation plus target-only physical advisory evidence | Opaque queues erase value or any authority/safety/preflight gate regresses |

## 19. Final scientific rule

### Protocol freeze contract

The revision-3 contract kit defines executable `EvaluationProtocol`, `ResearchStudyPlan`, `VerifierQualificationReport`, `AdaptiveEvaluationStep`, `ObservableInterventionRecord`, `EvidenceDerivationRecord` and `DriftEvaluationPlan` payloads. Each release must fill and sign concrete horizon_ms, success floor, non-inferiority margin, minimum effect, confidence, missingness, cohorts, budgets, verifier qualification, target population, sampling/holdout/drift plans, sample-size and cluster-aware analysis, stopping rule, baseline digests and split-manifest digests before training/search/final evaluation. Constraints are checked before freeze; no placeholder or fixture value authorizes a study. Numerical values in fixtures are synthetic test inputs, not recommended release thresholds. Until accountable owners resolve these values, the research gate is `BLOCKED_PROTOCOL_FREEZE`. A changed value creates a new protocol revision and new untouched holdout; it cannot rescue a failed result.

For primary effect delta=candidate minus baseline, require the upper confidence bound <= -minimum_effect_ms; for success delta require its lower bound >= -noninferiority_margin and the candidate success lower bound >= success_floor. Apply frozen multiplicity and cluster handling, plus every hard/cohort gate. No universal margin or performance benefit is asserted by this SDD.

> **No efficiency claim is valid until the complete path to an independently verified terminal outcome is measured and the verified-success, safety, authority, and reproducibility gates pass.**

## 20. Shared research events

```text
research.study.frozen
research.verifier.qualified
research.adaptive_observation.recorded
research.intervention.recorded
research.evidence_derivation.recorded
research.drift.assessed
```

These events are append-only observations under the existing EventEnvelope. They never authorize execution, change a verifier, promote an artifact or grant physical approval.

## 21. Revision-3 shared acceptance criteria

- [ ] **ACX-P2-006** Every learned or optimized label source has a scope-matched verifier qualification with both false-acceptance denominators and frozen gates.
- [ ] **ACX-P2-007** Adaptive acquisition preserves population positivity, exact or qualified propensities, paired candidate observations, cohort floors and effective-sample diagnostics.
- [ ] **ACX-P2-008** A selection-regret claim evaluates every compared candidate on common untouched holdout tasks after selection is frozen.
- [ ] **ACX-P2-009** Final hidden data is accessed once under a frozen candidate and analysis plan; a changed plan requires a new untouched holdout.
- [ ] **ACX-P2-010** Formal calibration guarantees are withdrawn when their registered assumptions fail, while empirical time-forward coverage and fallback cost remain reported.
- [ ] **ACX-P2-011** All research acquisition, verification, selector, failed-candidate, human and amortized costs remain visible and separate from runtime savings.
