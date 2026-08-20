# Accretion v0.4 Pre-Registered Research Protocol

## Evidence-Aware Node Configuration Routing

**Status:** Protocol draft; numerical thresholds require pilot power analysis  
**Date:** 2026-08-20  
**Related implementation:** `Accretion_SDD_v0.4.md`  
**Primary claim:** Lower constrained configuration regret on unseen Software/AI projects while preserving verified success

---

## 1. Purpose

This protocol defines the experiment required to decide whether Accretion v0.4's node-level configuration router is scientifically better than its strongest baseline.

It is deliberately separate from the SDD:

- The SDD defines how the system is built.
- This protocol defines how the claim is tested.

No superiority claim may be made from development-set results, unregistered metrics, selected success stories, or a p-value without effect size and safety non-regression.

---

## 2. Research question

> On previously unseen Software/AI projects using familiar capability types, does evidence-aware hierarchical node configuration routing reduce constrained configuration regret relative to the strongest eligible baseline while preserving the verified-success floor and critical safety behavior?

### 2.1 Unit of routing

One unit is one workflow graph node execution instance.

### 2.2 Routed action

\[
a_i=(Runtime, Model, Tools, Skills, VerifierImplementation, Environment)
\]

### 2.3 Experimental boundary

- Software engineering and AI research only;
- Digital, isolated, reproducible tasks;
- Familiar capability types but unseen projects;
- No learned graph planning;
- No physical Robotics;
- No end-to-end reinforcement learning.

---

## 3. Hypotheses

### H1 — Primary superiority

Accretion v0.4 has lower mean constrained configuration regret than the strongest baseline on the project-disjoint test set.

Let:

\[
R_{i,m}=U(a_i^{oracle})-U(a_{i,m})
\]

and paired difference:

\[
D_i=R_{i,Accretion}-R_{i,Baseline}
\]

Lower is better. The claim requires the confidence interval for the project-clustered mean difference to exceed the predeclared minimum meaningful improvement.

### H2 — Verified-success non-inferiority

Accretion's verified-success rate is not worse than the strongest baseline by more than the predeclared non-inferiority margin, and its lower confidence bound remains above the ObjectiveContract floor.

### H3 — False-acceptance safety

Accretion does not increase the critical false-acceptance rate.

### H4 — Resource tradeoff

At matched verified-success constraints, Accretion improves the quality/cost/latency Pareto frontier.

### H5 — Adaptation

Project-specific adaptation reduces regret after compatible verified project evidence accumulates, without increasing critical regression or miscalibration.

### H6 — Cold start

The workspace prior plus conservative fallback performs no worse than deterministic routing on unseen projects before local adaptation.

---

## 4. Estimands

### 4.1 Primary estimand

Project-clustered average treatment effect of full Accretion routing versus the strongest baseline on constrained configuration regret.

### 4.2 Safety estimands

- Difference in verified-success probability;
- Difference in false-acceptance probability;
- Difference in critical policy/risk violation rate;
- Difference in inconclusive-verification rate.

### 4.3 Secondary estimands

- Quality difference;
- Cost ratio and absolute cost difference;
- Latency ratio and absolute latency difference;
- Recovery overhead;
- Calibration error;
- Override rate;
- Configuration switching cost;
- Zero-shot versus adapted regret.

---

## 5. Operational definitions

### 5.1 Verified success

A node/run is successful only when all required claims in its frozen VerificationSpec pass with required evidence. Human approval without evidence is not verified success.

### 5.2 False acceptance

A false acceptance occurs when Accretion records `PASS`, but an independent adjudication protocol later establishes that a required critical claim was false or insufficiently supported.

### 5.3 Inconclusive

`INCONCLUSIVE` is neither success nor ordinary failure. It remains a separate outcome and is included in reporting.

### 5.4 Constrained configuration regret

Only policy-compatible configurations satisfying the same verification and risk requirements are included in the oracle set.

\[
Regret_i=U(a_i^{oracle})-U(a_i^{selected})
\]

If a method selects an invalid configuration, it receives the registered invalid-action penalty and is also counted in safety reporting.

### 5.5 Utility

Utility weights are taken from the frozen ObjectiveContract. Raw quality, cost, and latency are always reported separately.

---

## 6. Benchmark population

### 6.1 Project families

The benchmark SHOULD include both Software and AI research work:

#### Software engineering

- Bug diagnosis and repair;
- Test creation and regression repair;
- Refactoring under behavior preservation;
- Performance investigation and optimization;
- Dependency/API migration;
- Static-analysis or security remediation;
- Data pipeline implementation;
- Documentation/code consistency repair.

#### AI research

- Paper claim and evidence extraction;
- Reduced-scale reproduction;
- Dataset/evaluation pipeline creation;
- Baseline implementation;
- Ablation execution;
- Hyperparameter or method comparison;
- Statistical analysis;
- Reproducibility artifact creation.

### 6.2 Capability familiarity

Test projects may use capability types seen during training, including:

- Code read/edit;
- Tests and linters;
- Sandboxed command execution;
- Literature search and paper retrieval;
- Citation/evidence verification;
- Python analysis;
- Benchmark execution;
- Artifact generation.

The project objective, repository lineage, workflow composition, constraints, and artifacts MUST be unseen.

### 6.3 Exclusions

- Tasks without a credible VerificationSpec;
- Tasks requiring unavailable credentials or permissions;
- Physical or safety-critical execution;
- Tasks whose gold/adjudication artifacts leak into router context;
- Projects derived from the same upstream repository across split boundaries;
- Projects used to tune final model, thresholds, or utility normalization.

---

## 7. Split protocol

### 7.1 Grouping key

The split unit is the **project lineage**, not the node.

All forks, derived benchmarks, related paper extensions, repository versions, and generated node instances from a lineage stay in one split.

### 7.2 Required splits

- Training projects;
- Calibration/validation projects;
- Development benchmark projects;
- Locked project-disjoint test projects;
- Optional temporal/provider-drift holdout.

### 7.3 Leakage controls

- Hash and metadata similarity checks;
- Repository ancestry checks;
- Paper/benchmark lineage registry;
- Duplicate and near-duplicate task detection;
- Human review of ambiguous lineage;
- Test-set access log;
- Frozen retrieval corpus boundary where feasible.

---

## 8. Methods and baselines

### 8.1 Evaluated methods

| ID | Method |
|---|---|
| M0 | Strongest fixed full configuration |
| M1 | Cheapest valid full configuration |
| M2 | Deterministic v0.1 routing |
| M3 | Performance-aware pre-learning routing |
| M4 | Per-run configuration router |
| M5 | Node-level model-only router |
| M6 | Planning-LLM configuration selection |
| M7 | v0.4 offline ranker only |
| M8 | v0.4 workspace prior plus project adapter |
| M9 | Full v0.4 guarded router |
| ORACLE | Post-hoc best observed valid configuration |

### 8.2 Strongest baseline selection

The primary baseline is selected on validation projects only using the registered primary/safety criteria. Test results cannot determine which comparator is declared primary.

All baselines remain in the final report.

### 8.3 Oracle construction

The oracle requires executing a registered candidate subset under matched NodeContracts and conditions. It is a benchmark-only post-hoc upper bound and MUST NOT be presented as an available production policy.

---

## 9. Experimental design

### 9.1 Paired blocked design

Every eligible method is evaluated on matched project/node instances under equivalent:

- ObjectiveContract;
- NodeContract;
- Input artifact snapshot;
- Capability availability;
- Provider/model version window;
- Budget and timeout;
- VerificationSpec.

### 9.2 Repetition

Stochastic configurations are repeated across registered seeds/trial indices. Repetition counts are determined by pilot variance and power analysis.

### 9.3 Randomization

- Randomize method execution order within blocks;
- Balance provider time windows where possible;
- Record service outages and rate limits;
- Prevent one method from benefiting systematically from warmed caches unless cache state is part of the registered treatment.

### 9.4 Isolation

Each trial starts from an equivalent isolated workspace/container snapshot. Mutable artifacts never leak across methods.

### 9.5 Blinded adjudication

Where human adjudication is required, hide method identity and router explanation until the artifact-level judgment is recorded.

---

## 10. Sample size and power

The final sample size is not fixed before pilot variance estimation.

### 10.1 Pilot

Use development projects only to estimate:

- Within-project and between-project regret variance;
- Verified-success base rate;
- False-acceptance rarity;
- Intra-project correlation;
- Trial-to-trial stochasticity;
- Missing/outage rate.

### 10.2 Power analysis

Predeclare:

- Significance level \(\alpha\);
- Desired power \(1-\beta\);
- Minimum meaningful regret reduction \(\delta_{min}\);
- Verified-success non-inferiority margin \(\Delta_{NI}\);
- Cluster/intra-project correlation assumptions;
- Attrition allowance.

The unit count must be expressed in independent projects as well as nodes. A large node count inside very few projects does not demonstrate project generalization.

---

## 11. Metrics

### 11.1 Primary

- Project-clustered constrained configuration regret.

### 11.2 Safety gates

- Verified-success lower confidence bound;
- Verified-success non-inferiority difference;
- Critical false-acceptance difference;
- Policy/risk violation count;
- Critical cohort regression.

### 11.3 Secondary

- Claim-level quality;
- Monetary/token/compute cost;
- Wall-clock latency;
- Tool-call count;
- Recovery attempts;
- Graph revision count caused by downstream failures;
- Inconclusive rate;
- Expected calibration error and reliability curves;
- Cold-start performance;
- Adaptation sample efficiency;
- Override outcome;
- Pareto hypervolume or registered frontier summary.

### 11.4 Diagnostic

- Candidate pruning rate by reason;
- Fallback rate;
- Exploration rate;
- Experience retrieval coverage;
- Attribution confidence;
- Provider/model/tool cohort performance;
- Failure taxonomy distribution.

---

## 12. Statistical analysis

### 12.1 Primary analysis

Use a project-clustered paired estimator. The preferred initial analysis is a hierarchical bootstrap over projects, with paired nodes/trials sampled within each project. A mixed-effects sensitivity analysis SHOULD include project as a random effect.

The superiority claim requires:

1. Directional improvement in regret;
2. Confidence interval excluding no improvement;
3. Improvement meeting the registered minimum meaningful effect;
4. All safety gates passing.

### 12.2 Non-inferiority

Verified success is evaluated with a predeclared non-inferiority margin and confidence interval suitable for clustered binary outcomes.

### 12.3 False acceptance

Report exact counts and confidence intervals. Because false acceptance may be rare, absence of observed events is not automatically proof of equality. Include the upper confidence bound.

### 12.4 Multiple comparisons

The primary comparison is singular and predeclared. Secondary baseline and cohort comparisons use a registered multiplicity correction or are labeled exploratory.

### 12.5 Missingness and failures

- Timeouts, crashes, provider outages, and verifier inconclusive outcomes remain recorded;
- Method-caused failures count against the method;
- Externally caused outages are reported and handled by the registered missingness rule;
- No post-hoc exclusion based on unfavorable output;
- Sensitivity analyses treat ambiguous missingness conservatively.

### 12.6 Effect reporting

Report:

- Point estimate;
- Confidence interval;
- Standardized and natural-unit effect;
- Project-level distribution;
- Absolute verified-success/cost/latency values;
- Practical interpretation.

A p-value alone is insufficient.

---

## 13. Offline policy evaluation

When logged propensities are reliable, evaluate candidate policies using registered off-policy estimators such as inverse-propensity and doubly robust estimates.

Requirements:

- Positivity/coverage diagnostics;
- Effective sample size;
- Weight clipping rule;
- Sensitivity to estimator choice;
- No promotion based solely on unsupported action regions;
- Shadow evaluation before live influence.

Offline estimates support, but do not replace, paired benchmark and shadow evidence.

---

## 14. Required ablations

| Ablation | Removed component | Question answered |
|---|---|---|
| A1 | Hierarchical construction | Is factorized search necessary? |
| A2 | Compatibility pruning | Does typed pruning improve validity/efficiency? |
| A3 | Experience retrieval | Does verified history improve routing? |
| A4 | Project adapter | Is local adaptation beneficial? |
| A5 | Node-level feedback | Is local credit required? |
| A6 | Final-run feedback | Does global outcome correct local optimization? |
| A7 | Guarded exploration | Does online exploration add value beyond offline ranker? |
| A8 | Independent verification | Does evidence governance change learned behavior? |
| A9 | Uncertainty gate | Does conservative confidence control prevent unsafe choices? |
| A10 | EVI recovery stop | Does expected-value stopping reduce waste/thrashing? |

Each ablation uses the same project split and registered evaluation rules.

---

## 15. Cohort analysis

Predeclare cohorts for:

- Software versus AI research;
- Node type;
- Low versus high dependency depth;
- Cold-start versus adapted;
- Runtime/provider/model family;
- Tool/skill intensity;
- Verification type;
- Failure/contradiction presence;
- Cost/latency profile;
- Critical-path versus non-critical-path nodes.

Critical correctness, policy, secret-handling, verifier-conflict, or risk cohorts have hard non-regression gates. Other cohort results are descriptive unless powered and registered.

---

## 16. Human override analysis

Human override is analyzed separately because it changes the behavior policy.

Record:

- Router recommendation;
- Executed compatible configuration;
- Reason code;
- User role;
- Predicted tradeoff;
- Verified outcome;
- Whether the override improved regret post hoc.

An override is not treated as a positive label merely because a human chose it.

---

## 17. Promotion experiment

A workspace-router candidate can be promoted only if:

1. Primary regret criterion passes;
2. Verified-success floor/non-inferiority passes;
3. Critical false-acceptance gate passes;
4. Calibration remains within threshold;
5. No critical cohort regression occurs;
6. Shadow validation passes;
7. Rollback drill succeeds;
8. Promotion report is complete.

Bounded non-critical cost/latency tradeoffs require disclosure and explicit approval.

---

## 18. Reproducibility package

The release artifact MUST contain:

- Pre-registration and timestamp;
- Project lineage and split manifest;
- NodeContract and ObjectiveContract hashes;
- Configuration candidate catalog;
- Provider/model/tool/skill versions;
- Environment/container digests;
- Random seeds/trial indices;
- Routing receipts and propensities;
- Verification results and evidence references;
- Failure and missingness log;
- Analysis code and locked dependencies;
- Machine-readable aggregate results;
- Ablation configurations;
- Statistical report;
- Limitations and negative results.

Secrets and inaccessible project content are replaced by permission-safe manifests without weakening auditability.

---

## 19. Threats to validity

### Internal

- Provider drift during experiment;
- Cache or order effects;
- Verifier leakage;
- Unequal candidate coverage;
- Misclassified external outages;
- Human adjudicator bias.

### Construct

- Utility weights may hide important raw effects;
- Post-hoc oracle may be incomplete;
- Verifier pass may not capture all real-world correctness;
- Local credit may not equal causal contribution.

### External

- Limited project diversity;
- Familiar capability types do not prove unseen-domain generalization;
- Software/AI results do not imply Robotics transfer;
- Subscription/provider behavior may differ across deployments.

### Statistical

- Nodes are nested within projects;
- Repeated trials are correlated;
- False acceptance may be rare;
- Adaptive data collection can bias naive estimators;
- Multiple cohort comparisons inflate false discovery.

---

## 20. Decision rule

v0.4 may be described as outperforming the strongest baseline only when:

```text
primary constrained-regret superiority: PASS
AND minimum meaningful effect: PASS
AND verified-success floor/non-inferiority: PASS
AND critical false-acceptance non-regression: PASS
AND critical cohort non-regression: PASS
AND required ablations/reproducibility: COMPLETE
```

If regret improves but a safety gate fails, the result is **research evidence but not a successful v0.4 release claim**.

If confidence is insufficient, the result is **inconclusive**, not negative or positive.

---

## 21. Pre-registration fields still to freeze

The following require a development-only pilot before the locked test begins:

1. Exact project count and trial repetitions;
2. Primary baseline identity;
3. Utility normalization;
4. \(\delta_{min}\), \(\Delta_{NI}\), \(\alpha\), and power;
5. Confidence interval/bootstrap procedure;
6. Invalid-action penalty;
7. Candidate subset used for oracle construction;
8. Provider/model/tool version windows;
9. Cache and outage handling;
10. Critical cohort definitions and thresholds;
11. Calibration metric and maximum threshold;
12. Human adjudication rubric;
13. Multiplicity handling;
14. OPE estimators and clipping;
15. Promotion shadow duration/evidence requirement.

After these fields are frozen, test-set access begins and changes require a documented protocol amendment.
