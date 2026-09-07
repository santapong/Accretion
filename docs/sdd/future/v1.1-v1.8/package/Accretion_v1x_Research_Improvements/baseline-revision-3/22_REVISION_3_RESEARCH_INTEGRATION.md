# Accretion v1.x Technical SDD — Revision 3 Research Integration

**Document revision:** 3  
**Revised:** 2026-09-05  
**Status:** Normative design amendment; implementation and research evidence pending  
**Precedence:** This file amends revision-3 documents `00`–`13` and machine-readable artifacts `15`–`18`. It does not rewrite the historical revision-2 audit files `14`, `19`, or `21`.

## 1. Purpose

Revision 3 converts the supplied research synthesis into a staged Accretion research program for every planned v1.x minor release. It gives v1.1 the first executable study design while keeping every later study blocked by its predecessor and local readiness evidence.

No cited paper result is an Accretion result. No fixture establishes implementation conformance, scientific success, release readiness, deployment permission, model-training permission, paid-provider permission, GitHub write permission or physical-execution authority.

## 2. Corrections carried into the shared protocol

1. **Evaluation count is not total cost.** Report calls/evaluations, tokens, provider wait and quota delay, elapsed wall time, local compute, money, verifier work, selector work, failed candidates and human review separately.
2. **Verifier quality is a prerequisite.** A learned selector cannot optimize an unqualified success label. Scope-matched labeled correct, incorrect, corrupted and adversarial cases precede optimization.
3. **Adaptive sampling needs design-aware evidence.** Preserve positive inclusion probabilities, paired candidates, a uniform acquisition comparator, cohort floors and effective-sample diagnostics.
4. **Selection needs a common holdout.** Every compared candidate reaches the same untouched holdout after selection freezes; otherwise selection regret is not identifiable.
5. **Prediction is not intervention evidence.** v1.3 causal language requires actual descendant re-execution from a restorable snapshot and independent verification.
6. **Factual trust is not command authority.** Derived memory inherits least-source trust and always has no command authority.
7. **Calibration claims are conditional.** Formal guarantees are withdrawn when their registered assumptions fail under shift.
8. **Hosted API observations do not reveal serving internals.** Provider queue/latency may be measured, but GPU placement, KV scheduling and internal reasoning are outside the claim boundary.

## 3. Release amendments

| Release | Revision-3 first study | Frozen primary factor | Claim ceiling |
|---|---|---|---|
| v1.1 | Three-profile adaptive task-acquisition pilot | Fixed compute profile | Select one profile for a declared cohort; no conditional router claim |
| v1.2 | Frozen harness-by-compute factorial | Harness and profile candidates | Interaction value for tested regimes only |
| v1.3 | Observable intervention and replay | One typed component per episode | Bounded causal recovery only after deterministic re-execution |
| v1.4 | Memory write-path attack and dependency invalidation | Trust/derivation policy | Tested write/retrieval path and attacker only |
| v1.5 | Adaptive trace coreset | Trace acquisition with candidates frozen | Search-evaluation savings with common holdout |
| v1.6 | Label/verifier qualification and bounded adaptation | Qualified task family | Hosted observable behavior and supported open-weight lab only |
| v1.7 | Time-forward update strategy | Frozen/periodic/triggered schedule | Tested target domains; source remains a weak prior |
| v1.8 | Command-disabled workflow advisory simulation | Fixed graph/resource model | Simulation evidence only until independent v0.6 physical gates pass |

## 4. New shared contracts and events

Revision 3 adds `ResearchStudyPlan`, `VerifierQualificationReport`, `AdaptiveEvaluationStep`, `ObservableInterventionRecord`, `EvidenceDerivationRecord`, and `DriftEvaluationPlan` to `15_CONTRACT_KIT.json`. Their lifecycle events are registered once in `16_EVENT_CATALOG.json`; valid and adversarial examples are in `17_CONTRACT_LIFECYCLE_FIXTURES.json`.

The records carry evidence and replay lineage only. They cannot authorize execution, modify a frozen node, grant capabilities, consume approval, promote a candidate or issue a physical command.

## 5. Acceptance traceability

Revision-3 acceptance rows cover every added criterion in every release and shared criteria `ACX-P2-006` through `ACX-P2-011`. All rows remain `IMPLEMENTATION_PENDING` or `RESEARCH_PROTOCOL_PENDING` with `evidence_exists: false`. The revision-3 validator requires exact text equality between each SDD criterion and its traceability row, valid milestone ownership and resolvable design-fixture references.

## 6. Immediate next action

Only v1.1 readiness work is currently open: inventory existing traces, define the target population and three compatible profiles, qualify the verifier and freeze a profile-only pilot protocol. New paid calls, live routing, model training and physical trials remain outside this document package and require their own authorization and predecessor gates.

