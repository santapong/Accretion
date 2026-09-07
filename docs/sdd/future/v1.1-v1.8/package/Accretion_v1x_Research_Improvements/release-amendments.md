# Proposed research amendments to Accretion v1.1–v1.8

Baseline: the supplied Revision 3 package. These are reviewable proposed deltas, not normative replacements. All new benefits remain unmeasured in Accretion. References identify primary paper versions; interpretation and adaptation below are this review's proposals.

## Shared protocol: strengthen the evidence boundary

Revision 3 already requires verifier qualification, total cost, positive acquisition support, common holdouts, honest failure accounting and authority separation. Preserve them. The new proposals target remaining ambiguity in `03_SHARED_RESEARCH_EVALUATION_PROTOCOL.md` §§7, 9–10 and the release-specific protocols.

**Separate selection from statistical calibration.** The current shared §7.1 groups held-in validation for search and calibration. Where split-conformal or another independence-dependent guarantee is claimed, freeze model/harness/score selection on development data, then calibrate on a separate untouched cohort, then evaluate once on a final cohort. If a method explicitly supports data reuse, record its applicable theorem and implement its assumptions instead of assuming ordinary split-conformal validity. Critical cohort membership and project/scene lineage must be explicit in all splits.

**Define the claimed uncertainty object.** Record whether an artifact estimates mean performance, predicts a new outcome, controls marginal error, controls a time-average error, or supports a conditional claim. Include population, independent unit, calibration size, label-arrival process and applicable assumptions. Shift diagnostics can falsify plausibility; they cannot certify exchangeability. A prediction interval must not be relabeled as a lower confidence bound on each task's success probability. [Adaptive Conformal Inference](https://www.alphaxiv.org/abs/2106.00170v3), §4, is a useful example of how a time-average guarantee differs from a per-decision claim.

**Handle small samples honestly.** For split-conformal rank `ceil((n+1)(1-alpha))`, a rank beyond `n` needs the method's valid conservative handling, such as an unbounded/full-support prediction set. Silently replacing it with the largest observed residual cannot generally retain the requested coverage. Record insufficient precision as inconclusive; do not shrink an interval merely to produce a recommendation. The included rank calculation is a mathematical check, not an Accretion experiment.

**Treat activation as measured evidence.** A change should have a receipt showing what was requested, what the runtime accepted, and what observable effect establishes that it ran. Unavailable signals remain unavailable. Preserve intended-treatment outcomes when a configuration fails; do not discard inconvenient trials after observing results.

Integration should extend the existing `ResearchStudyPlan`, `VerifierQualificationReport`, `AdaptiveEvaluationStep` and provenance attachments through the package's change-control process. It should not create a second research/evidence authority. New fields, events and acceptance rows require joint schema, fixture and traceability updates before calling a revised package validated.

## v1.1 — Verified Compute Optimizer

**Already covered:** three fixed compatible profiles, all-profile pairing, uniform acquisition comparator, capped TTVS, full cost and common holdout (§19.1; document 24). These are retained.

**Proposed delta:** add a profile-observation audit to §§7.6, 8.1 and 19.1. Bind prompt semantics, harness/tool schemas, reasoning setting, context/compaction, gateway behavior and provider/accounting version. Record requested versus effective supported settings. Check a trace for the observable mechanism; do not require inaccessible reasoning text. Distinguish cold/warm cache conditions and randomized or counterbalanced execution order where they could alter cost.

[Prompt-Induced Waste](https://www.alphaxiv.org/abs/2608.01347v5), §§2, 7, 11, motivates checking interactions and actual activation. Its small, high-success tasks limit direct transfer of reported savings.

**Test:** retain the primary fixed-profile pilot. Add predeclared difficulty/family strata, repeated paired runs only where inventory supports estimating variance, and an inert-change control when practical. Test whether an apparent saving persists with unchanged success and complete accounting. A gateway mismatch is a failed readiness/configuration check, not evidence that the intended profile is cheap.

**Budget clarification:** all-profile batches cost multiples of three profile trials. A cap of 20 cannot fund seven complete batches. Separate the two acquisition arms, reused evaluation observations, repeats, qualification and holdout costs; the current illustrative total does not fully resolve these boundaries. See the next-study plan.

**Owner/impact:** v1.1 profiling and shared protocol; no new learned action. Proposed SDD amendment. First work: read-only field/trace inventory. Conditional routing still requires a later protocol.

## v1.2 — Specialized Harness Portfolio Compiler

**Already covered:** frozen harness-by-compute factorial, general and hand-authored baselines, complexity penalty, compatible tool/context changes, independent verification and fallback (§§10, 19.1).

**Proposed delta:** add a baseline-strength and portability ladder. Start with the current mature general harness; use a minimal seed only as a diagnostic arm. Evaluate a bounded specialist and general baseline across the same three profiles and project families. Record the optimizer's harness separately from the target harness if a proposer generated candidates. Add install/load/trigger checks so an unused skill file cannot count as a successful specialization.

[HarnessOpt-Bench](https://www.alphaxiv.org/abs/2608.06301v1), §§3–5 and limitations, supports this concern: its deliberately untuned seeds and task-dependent harness effects do not establish gains over a mature system.

**Test:** estimate the incremental gain of specialization over the mature general harness, not just the best cell's absolute score. Keep every frozen factorial cell on the common holdout. Report fixed overhead, bundle count, context growth, failed activations, maintenance/requalification cost and the regions where the fallback wins. Compare a static regime map before a learned regime model. Avoid a large factorial until inventory indicates sufficient support.

**Owner/impact:** v1.2 Harness Registry/Portfolio Compiler and v1.1 selection inputs; §19 benchmark and §21 proposed gate extension. v0.10 still governs executable harness proposals. Reject borrowing unrestricted source-code search as runtime permission.

## v1.3 — Verified Recovery and Escalation Optimizer

**Already covered:** actual descendant re-execution, one typed intervention, snapshot restore, independent verifier, no gold access and full-retry control (§19.1). Do not present these as new.

**Proposed delta:** separate three evaluation stages: localization quality, causal intervention evidence, and economical recovery. A model may rank suspect components or abstain. Qualification must independently show that the chosen intervention changes outcomes beyond ordinary stochastic reruns. Count ambiguous multi-cause episodes rather than forcing one blame label.

[Who & When](https://www.alphaxiv.org/abs/2505.00212v3), §§3–4, measures difficult attribution against human annotations. [CausalFlow](https://www.alphaxiv.org/abs/2605.25338v1), §4 and Appendices A.5/A.10, mixes execution and predicted validation and contains a GSM8K description conflict. Neither licenses verbal blame as a repair gate.

**Test:** freeze full retry, no-op restore/replay, rule-guided intervention and proposed intervention arms on the same restorable episodes. Select the intervention using development evidence, then repeat a predeclared set of matched replay trials. Compare independent PASS and saved descendant work after restore, instrumentation and verification cost. Same random seeds help pairing only where the environment honors them; hosted LLM outputs remain stochastic.

**Reject/defer:** predicted-only recovery, interventions that expose the answer, and causal claims from a single lucky success. If exact state restoration or side-effect isolation is unavailable, retain diagnostic ranking and defer the causal claim.

**Owner/impact:** v1.3 failure/recovery owner and `ObservableInterventionRecord`; refine §§18–19 and uncertainty evidence without changing planner or verifier ownership.

## v1.4 — Evidence-Grounded Runtime State Coordination

**Already covered:** the complete write/retrieval path, separate attack denominators, least-source trust, no command authority and exact dependency invalidation (§19.1).

**Proposed delta:** extend the attack population across time and sessions. Include delayed activation, summary-mediated poisoning, source revocation after caching, and concurrent retrieval during invalidation. Use immutable dependency versions and a read/commit epoch or equivalent serialization rule so stale state cannot slip through between eligibility check and consumption. This is a proposed consistency requirement; existing retrieval checks alone do not prove it.

[AgentDojo](https://www.alphaxiv.org/abs/2406.13352v3), §§3–4, supplies resettable environments and separate utility/security measures. Its own tool-filter discussion identifies limits when the same tools serve both legitimate and malicious goals or an attacker waits for a future task.

**Test:** use a fixture matrix of clean versus injected observations, accepted versus rejected writes, fresh versus revoked sources, and same-session versus later-session use. Report harmful outcomes per all attempts, malicious-write rate, exposure conditional on committed malicious state, benign utility and false-positive quarantine cost. Add an attack that changes recommendations without making a forbidden tool call. Compare recompute-all, validated selective invalidation and command-disabled unsafe reuse.

**Owner/impact:** v1.4 State Validator/Committer and Experience Retriever; reuse v0.4 visibility and v0.10 promotion boundaries. Proposed change touches concurrency and derivation contracts, so an architecture review is needed when integrated. No new authority is created by a memory entry.

## v1.5 — Retrospective Harness Optimization

**Already covered:** the first study freezes candidates and changes only trace acquisition; all candidates receive common-holdout evaluation (§19.1). Keep that first stage intact.

**Proposed delta:** after that study closes, specify a separate behavior-linked screening stage. Each patch hypothesis names a target behavior, supporting source traces, affected components and plausible collateral damage. Build repair cases, successful preservation cases, boundary cases and a fresh confirmation set. Keep unsuccessful patches and rejected lessons in the existing search history with a non-promoted status.

[HarnessLens](https://www.alphaxiv.org/abs/2608.27311v1), §4, motivates targeted paired screening and fresh confirmation. [AutoSaddler](https://www.alphaxiv.org/abs/2608.23041v1), §§4–5, motivates before/after fix and regression records. These methods do not establish unbiased population improvement from a selected behavior batch.

**Test:** compare score-only screening, frozen diversity coverage and behavior-linked screening under one full resource budget. Measure fixed, still failing, preserved, regressed and mixed outcomes separately; evaluate the finalist on untouched project lineages. Batch relevance and fresh confirmation are development tools, never permission to skip required verification or use selected cases as the final success estimate. An LLM's attribution stays a hypothesis even when the source calls it attributable evidence.

**Owner/impact:** v1.5 proposer/screener plus v0.10 independent evaluator and promotion; v1.4 owns reusable experience. No duplicate “learning memory” service. Proposed §19.2 follow-up, with a distinct protocol for adaptive candidate generation.

## v1.6 — Open-Weight Policy Adaptation Lab

**Already covered:** verifier-qualified labels, verified SFT and inference-sampling baselines, frozen verifier, time/project holdout, bounded updates and realistic amortization (§§10, 19).

**Proposed delta:** add an optional interaction experiment before assuming a one-way sequence from harness optimization to weights. Compare fixed baseline, harness-only, adapter-only and a small alternating arm. Requalify the assembled harness/adapter pair after either changes; training data must bind both identities.

[WHALE](https://www.alphaxiv.org/abs/2609.00196v1), §§3–6, motivates alternating updates. Its Algorithm 1 line 17 selects by test accuracy; Accretion should instead nominate using development data and lock the pair before final evaluation. Its rollout scale and official reproduction notes make direct replication unsuitable as an assumed low-cost step.

**Test:** use one resettable, repeatedly useful task family, a small supported open-weight model and verified-success SFT as the first weight method. Allocate a total budget across both phases, including the proposer, rollouts, failed candidates, training, evaluation and serving. Choose the phase schedule on development data. Report the four arms under a joint budget, not only matching each component separately. Compare real break-even volumes and stop if the workload cannot repay the extra cost.

**Reject/defer:** test-chosen checkpoints, consensus-only positive labels, autonomous promotion, an eight-H200 reproduction by assumption, or expanding v1.7 into a second training owner. Keep v1.6 optional; negative or deferred research closure remains valid.

## v1.7 — Cross-Domain Efficiency Transfer in Simulation

**Already covered:** hard compatibility, weak source priors, target-only comparison, negative-transfer stop and frozen/periodic/triggered forward updates (§§10, 19.1).

**Proposed delta:** make transfer content structural. Divide source-bound identifiers, frame/unit/calibration details and runtime assumptions from reusable procedures, failure conditions and completion checks. Freeze the source record and keep target adaptations as separate descendants. A missing compatible category yields an empty prior. A linter may reject obvious source-specific data, but cannot prove semantic transferability.

[CoAdapt-GUI](https://www.alphaxiv.org/abs/2608.11588v1), §3 and §4, offers a useful separation of transferable context and target-grounded state. Its GUI results are an analogy for efficiency priors, not evidence of robot transfer.

**Test:** begin with target-only, static eligible prior and target-updated context-only arms, all at the same target-label and compute budget. Keep optional weight adaptation in v1.6. Separate new task instances from new task templates and genuinely new domains/embodiments. Evaluate incompatible-source and empty-prior controls. Record whether labels arrive late or never; missing verifier labels cannot become successful outcomes or automatic conformal updates.

Use time-average calibration as a diagnostic only when its assumptions and label timing fit. The original [Adaptive Conformal Inference](https://www.alphaxiv.org/abs/2106.00170v3), §7, explicitly leaves delayed/batched response settings open. It does not establish the per-cohort success floor needed for activation.

**Owner/impact:** v1.7 transferable-field policy and source/target lineage; v1.4 state, v1.6 adapters and v0.7 embodiment compatibility remain owners. Proposed amendments to §§8, 10 and 19.

## v1.8 — Governed Physical Efficiency Advisory

**Already covered:** fixed-graph command-disabled workflow simulation, pre-freeze advisory, target-specific evidence and unchanged v0.6 approval/arming/safety ownership (§§7–10, 19.1).

**Proposed delta A:** compare a transparent deterministic workflow allocator before a learned allocator. With a fixed graph, finite permitted digital choices and simulated resource constraints, a simple constrained solver is a useful control. Measure sensitivity to latency estimates and queue uncertainty. Treat hosted-provider latency as an observed external process, not accessible fleet internals.

[Dyserve](https://www.alphaxiv.org/abs/2607.02942v1), §§4–7, motivates joint workflow allocation but assumes a self-hosted serving fleet, optional quality policies and runtime suffix changes. Accretion should not import verifier substitution, device-control claims or post-freeze mutations.

**Proposed delta B:** keep scenario prediction as a separate, later calibration study. [SCAPE](https://www.alphaxiv.org/abs/2608.19425v1), §§3–4, motivates correcting biased simulator outcomes with paired target observations. Begin with sim-to-sim evidence; physical applicability needs exact-task paired real data under existing v0.6 authority. A quadruped velocity study is not a UR5e manipulation calibration set.

**Test:** compare simulator-only, target-only, simple residual correction and a scenario-conditioned model at matched paired-data budgets. Split by independent scene/configuration where generalization is claimed; reserve separate selection, calibration and final test roles. Measure prediction error, task success, interval usefulness and out-of-scope abstention. Preserve task and safety outcomes separately.

**Reject:** guaranteed per-scenario safety from marginal intervals, clamped small-sample quantiles without valid coverage treatment, pooled real/sim outcomes, or physical-efficiency claims from command-disabled workflow simulations. If the target data do not exist, close with simulation-only scope.

## Integration decision

All eight entries are proposed SDD amendments or staged study additions, with `authority_delta: NONE`. They have not changed product versions or unlocked releases. Shared data-role clarification is the first design priority; v1.1 trace readiness is the first operationally useful research product. A future Revision 4 integration must reconcile all affected SDD sections, shared registry, contract kit, events, fixtures, traceability and validation report together.
