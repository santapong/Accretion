# Accretion v1.x Cross-Release Contract Registry

**Document revision:** 4  
**Supersedes document revision:** 3  
**Revised:** 2026-09-06

**Status:** Normative extension registry  
**Applies to:** v1.1.0-v1.8.0  
**Predecessor:** Existing `Accretion_Cross_Release_Contract_Registry_v0.4_to_v1.0.md`  
**Rule:** This document extends existing contracts; it does not rename, copy, or weaken them

---

## 1. Precedence

When requirements conflict, apply this order:

1. Applicable law, approved physical safety case, and mandatory facility/manufacturer rules;
2. Accretion Golden Direction and permanent invariants;
3. Approved `ObjectiveContract` and project policy;
4. Existing v0.4-v1.0 cross-release contract registry;
5. This v1.x extension registry;
6. Currently unlocked and approved release SDD;
7. Earlier released SDDs and ADRs;
8. Locked forward SDDs;
9. Research protocols, examples, and UI copy.

A locked forward document cannot authorize implementation or expand authority.

## 2. Reused canonical foundation

All v1.x records use the existing canonical header, canonical JSON/hash rules, typed immutable references, event envelope, verification states, evidence classes, risk classes, and failure ownership taxonomy.

The following existing contracts remain authoritative and MUST NOT be recreated:

- `ObjectiveContract`;
- `NodeContract`;
- `VerificationSpec` and the v0.4 semantic `VerificationResult`, implemented in the current repository as `IndependentVerificationResult` and distinct from the v0.1 API `VerificationResult`;
- `ExecutionConfiguration` and `ConfigurationCandidate`;
- `CompatibilityDecision` and `RoutingDecisionReceipt`;
- `ExperienceRecord` and `FailureEvent`;
- `WorkflowPlanState`, `GraphCandidateSet`, and `PlannerDecisionReceipt`;
- `JointPolicySnapshot` and `HierarchicalOutcomeReceipt`;
- `CapabilityGapReport`, `CapabilityChangeProposal`, and `PromotionDecision`;
- `EmbodimentDescriptor`, `EmbodimentSignature`, and transfer contracts;
- `PhysicalTrialContract`, `PhysicalTrialApproval`, `ArmedTrialLease`, and safety contracts;
- `EventEnvelope`, `PrincipalRef`, `ConnectionRef`, `PolicyRef`, `EvidenceRef`, `ArtifactRef`, and `ApprovalArtifactRef`.

New v1.x contracts reference or extend these owners. They never create a parallel acceptance, policy, approval, evidence, or capability system.

## 3. Canonical extension header

Every new persisted v1.x contract embeds the existing canonical contract header and, when it represents a learned decision, also includes:

```yaml
decision_context_hash: sha256
policy_artifact_ref: ArtifactRef | null
candidate_set_hash: sha256 | null
compatibility_receipt_refs: [ArtifactRef]
objective_contract_ref: ObjectiveContractRef
evidence_lineage_root_ref: EvidenceRef
```

Unknown major schema versions and unknown fields fail closed under the current extra-forbid policy. An explicit versioned upcaster is required before accepting a new field; optional does not mean silently accepted. Unknown authority, safety, physical, or verification enums cannot be coerced to a permissive value.

In illustrative schemas, `uuid` means a globally unique opaque identifier. Implementation follows the repository's noncolliding prefixed-ID convention and freezes a unique prefix before adding a contract; it does not assume a literal UUID wire format. The existing v0.2 `CompatibilityAssessment` and v0.4 `CompatibilityDecision` remain distinct. `RiskClass` uses the existing total mapping to the v0.1 `RiskLevel`; it cannot bypass that approval ladder.

## 4. Product and artifact identities

| Item | Identity rule |
|---|---|
| Product release | Accretion SemVer |
| Contract | `contract_type`, schema SemVer, UUID, content hash |
| Compute profile | Immutable profile ID + version + digest |
| Compute portfolio | Immutable portfolio ID + compiler/evidence snapshot |
| Learned policy | Immutable artifact ID + digest + training/evaluation lineage |
| Harness bundle | Harness ID + SemVer + content/package digest |
| Recovery policy | Policy artifact + eligible failure/risk cohort |
| State coordinator | Policy artifact + allowed state-action manifest |
| Adapted model | Base model digest + adapter digest + training snapshot |
| Research protocol | Protocol ID + revision + frozen content hash |
| Physical advisory | Advisory ID + exact input/preflight snapshot hashes |

Aliases resolve to immutable references before any decision or execution.

## 5. v1.1.0 contract ownership — Verified Compute Optimizer

| Contract | Stable semantic |
|---|---|
| `ComputeProfile` | Backward-compatible enrichment of one allowed execution configuration with explicit compute, context, reasoning, and escalation limits |
| `ComputePortfolio` | Pareto-filtered, versioned set of compatible compute profiles for a cohort |
| `ComputePrediction` | Calibrated predicted verified pass, latency, cost, retries, uncertainty, and TTVS for one candidate |
| `ComputeDecisionReceipt` | Candidate portfolio, pruning, predictions, selected tier/profile, abstention/fallback, and policy snapshot |
| `ComputeDispatchBinding` | Immutable extension binding the complete resolved profile/configuration to the single existing routing decision and final compatibility receipt; never a second dispatch authority |
| `EffectiveComputeConfiguration` | Immutable materialized assembly payload referencing the existing ExecutionConfiguration and all resolved profile fields; consumed by a compatible adapter, never an alternative ExecutionConfiguration authority |
| `ComputeExecutorAdapterManifest` | Pinned field map, supported schema versions and dependency digest for the existing executor's additive profile adapter |
| `TTVSOutcome` | Observed queue, route, execute, verify, repair/escalation, and terminal-result timing |
| `ComputeCompilerRun` | Frozen profiling dataset, proxy/compiler configuration, output portfolio, and validation result |

### 5.1 `ComputeProfile`

```yaml
ComputeProfile:
  profile_id: uuid
  profile_version: semver
  execution_configuration_ref: ArtifactRef
  tier: DETERMINISTIC | SPECIALIZED_SMALL | GENERAL_MID | FRONTIER
  reasoning_budget:
    max_output_tokens: integer | null
    max_steps: integer
    max_wall_time_ms: integer
  context_policy_ref: ArtifactRef
  tool_subset_refs: [ToolRef]
  harness_bundle_ref: ArtifactRef | null
  retry_policy_ref: PolicyRef
  escalation_policy_ref: PolicyRef
  eligible_risk_classes: [RiskClass]
  capability_manifest_hash: sha256
  content_hash: sha256
```

The referenced `ExecutionConfiguration` remains the execution authority owner. `ComputeProfile` cannot make an incompatible configuration admissible.

### 5.2 `ComputePrediction`

```yaml
ComputePrediction:
  profile_ref: ArtifactRef
  p_verified_pass: number  # independently verified PASS by horizon_ms
  p_verified_pass_lcb: number
  uncertainty_statement_ref: ArtifactRef  # same population and inference scope
  latency_ms: {p50: number, p95: number}
  verification_latency_ms: {p50: number, p95: number}
  expected_attempts: number
  expected_ttvs_ms: number  # expected capped TTVS_H, same horizon as protocol
  horizon_ms: positive_integer
  expected_cost: number
  frontier_call_probability: number
  uncertainty_score: number
  ood_score: number
  prediction_model_ref: ArtifactRef
```

### 5.3 TTVS terminal semantics

`TTVSOutcome.terminal_status` uses the canonical verification state. Only independent PASS committed by the frozen horizon counts as success. Store observed time-to-terminal separately from capped TTVS_H; all no-PASS-by-H outcomes contribute H. Timing can be UNKNOWN, never fabricated as zero. The shared protocol owns estimation, missingness, and critical-cohort gates.

A `ComputePrediction` using a success lower bound must bind its uncertainty statement. The bound applies only to its declared population/conditioning assumptions; marginal prediction coverage cannot populate a per-task probability LCB. Unsupported scope abstains. `EffectiveSettingObservation` is shared research evidence about a complete profile/harness activation and grants no dispatch authority.

## 6. v1.2.0 contract ownership — Specialized Harness Portfolios

| Contract | Stable semantic |
|---|---|
| `HarnessBundle` | Signed/content-addressed package of context templates, tool schemas, hooks, memory/context policy, loop policy, and allowed runtime bindings |
| `HarnessVariant` | Immutable candidate derived from a parent bundle with typed change set |
| `HarnessRegime` | Validated task/cohort description for specialization; not a permission category |
| `HarnessPortfolio` | Versioned set of promoted bundles and routing metadata |
| `HarnessCompatibilityReceipt` | Deterministic proof that a bundle fits node, runtime, tool, policy, verifier, and environment requirements |
| `HarnessSelectionReceipt` | Eligible bundle set, regime prediction, uncertainty, selected bundle, and fallback |

### 6.1 `HarnessBundle`

```yaml
HarnessBundle:
  harness_id: uuid
  harness_version: semver
  package_digest: sha256
  prompt_template_refs: [ArtifactRef]
  context_policy_ref: ArtifactRef
  tool_schema_adapter_refs: [ArtifactRef]
  deterministic_hook_refs: [ArtifactRef]
  loop_policy_ref: PolicyRef
  state_view_policy_ref: PolicyRef | null
  compatible_runtime_refs: [RuntimeRef]
  required_capabilities: [CapabilityRef]
  forbidden_capabilities: [CapabilityRef]
  verifier_compatibility_refs: [VerifierRef]
  supply_chain_attestation_ref: ArtifactRef
```

A harness may shape model-visible context and tool affordances. It cannot alter the frozen `NodeContract`, `VerificationSpec`, policy result, secret handling, or physical safety envelope.

Bundle content is immutable before evaluation and contains no future promotion pointer. A separate append-only `HarnessLifecycleRecord` references the bundle digest and evidence; PROMOTED requires the existing v0.10 PromotionDecision. Candidate/packaged records require no nonexistent promotion. Live eligibility resolves the latest authorized non-revoked lifecycle projection atomically; content identity never changes on promotion or revocation. `HarnessSelectionReceipt.selected_harness_ref` is null exactly on ABSTAIN, with a reason and separate fallback reference. It is a proposal feeding the v1.1 joint assembly; it cannot dispatch. A later binding links both receipts without a circular forward reference.

## 7. v1.3.0 contract ownership — Verified Recovery Optimizer

| Contract | Stable semantic |
|---|---|
| `RecoveryContext` | Immutable snapshot of failure, attempt chain, remaining budget, permitted actions, and owning controller |
| `RecoveryCandidate` | One policy-compatible recovery action with predicted outcome and cost |
| `RecoveryDecisionReceipt` | Candidate set, predicted value, chosen action or stop, caps, and explanation |
| `AttemptChain` | Append-only causal chain of attempts, changes, evidence, verification, and terminal state |
| `RecoveryOutcome` | Observed resolution, incremental TTVS/cost, recurrence, and final verification |

Canonical recovery actions:

```text
RETRY_TRANSIENT
REBUILD_CONTEXT
ACQUIRE_EVIDENCE
SWAP_TOOL_BINDING
SWAP_HARNESS
INCREASE_REASONING_BUDGET
ESCALATE_MODEL_TIER
REQUEST_REPLAN
PAUSE_HUMAN_REVIEW
STOP_BUDGET
STOP_SAFETY
STOP_AUTHORITY
```

The failure ownership taxonomy determines which actions may be considered. A recovery policy cannot turn a configuration failure into an unauthorized structural edit. `SAFETY`, `AUTHORITY`, and unresolved `UNKNOWN` do not enter learned automatic recovery.

## 8. v1.4.0 contract ownership — Runtime State Coordination

| Contract | Stable semantic |
|---|---|
| `RuntimeStateView` | Read-only, least-privilege projection of Belief, Progress, and eligible Experience |
| `StateAccessAction` | Bounded inspect/retrieve/summarize request against an allowed view |
| `StateCommitProposal` | Proposed structured progress/belief/experience update; never direct authoritative mutation |
| `StateValidationReceipt` | Deterministic schema, provenance, contradiction, policy, and monotonicity checks |
| `StateCoordinationReceipt` | Chosen action, policy snapshot, view digest, cost, and resulting validated state reference |
| `StateCoordinationOutcome` | Effect on task result, state-call count, context, latency, and verifier outcome |

Canonical state partitions:

```text
BELIEF     claims, evidence links, uncertainty, contradictions
PROGRESS   subgoals, statuses, blockers, attempt/evidence references
EXPERIENCE eligible verified reusable records
```

The state coordinator never stores hidden chain-of-thought as a required product artifact. It uses structured summaries, claims, decisions, evidence references, and outcomes. “Forget” may evict derived caches; it cannot delete authoritative evidence or audit history.

`StateConsumptionReceipt` is owned by the existing State Validator/Committer. It records a dependency-bound use decision at the serialized eligibility/revocation boundary. This adds no memory service or new authority. The receipt retains view/dependency references, epoch comparison and rejected-stale versus consumed state; future reuse always rechecks eligibility.

## 9. v1.5.0 contract ownership — Retrospective Harness Optimization

v1.5 reuses v0.10 `CapabilityChangeProposal`, `CandidateManifest`, `CandidateEvaluationPlan`, `CandidateEvaluationReport`, `PromotionDecision`, and `CapabilityLineageRecord`.

| New contract | Stable semantic |
|---|---|
| `TraceCohort` | Frozen, redacted, diversity/coverage-audited set of eligible historical trajectories |
| `HarnessOptimizationRun` | Proposer/search configuration, baseline harness, budget, candidate lineage, and accounting |
| `HarnessDiagnostic` | Self-validation, cross-rollout consistency, verifier, and failure-pattern evidence; never acceptance by itself |
| `HarnessComparisonReport` | Paired held-in/hidden-held-out quality, TTVS, cost, security, and cohort comparison |

Every proposed harness change is represented as a v0.10 capability proposal subtype. v1.5 creates no alternate promotion path.

## 10. v1.6.0 contract ownership — Open-Weight Policy Adaptation Lab

| Contract | Stable semantic |
|---|---|
| `PolicyAdaptationExperiment` | Frozen offline experiment over an eligible repeated task family and open-weight model |
| `RolloutEquivalenceSpec` | Behavior/outcome equivalence and clustering rules established before rollouts |
| `PseudoLabelCohort` | Consensus and verifier-supported rollout grouping with uncertainty and exclusions |
| `AdapterArtifact` | Parameter-efficient adapter tied to base-model digest, data lineage, license, and evaluation |
| `AdaptationEvaluationReport` | Held-out correctness, calibration, safety, TTVS, compute cost, and amortization evidence |
| `AdaptationPromotionReceipt` | Human-authorized eligibility for a named low-risk digital profile or rejection |

An adapter cannot alter policy, verifier, identity, capability, or safety contracts. Closed-weight models are out of scope unless a provider exposes an authorized compatible adaptation API.

## 11. v1.7.0 contract ownership — Cross-Domain Efficiency Transfer

v1.7 reuses v0.7 transfer and embodiment contracts.

| New contract | Stable semantic |
|---|---|
| `EfficiencyDomainSignature` | Typed representation of task, workflow, compute, harness, verifier, environment, and embodiment properties |
| `EfficiencyTransferCandidate` | Source compute/harness/recovery/state prior plus explicitly transferable fields |
| `EfficiencyTransferDecision` | Hard compatibility gates, weak-prior weight, target shadow plan, and fallback |
| `EfficiencyTransferOutcome` | Target-simulation TTVS, verified success, adaptation samples, and negative-transfer evidence |

Source performance never establishes target acceptance. A target-specific verifier and target simulation evidence are mandatory.

## 12. v1.8.0 contract ownership — Governed Physical Efficiency

v1.8 reuses every v0.6 physical contract without semantic change.

| New contract | Stable semantic |
|---|---|
| `PhysicalOptimizationAdvisory` | Non-authoritative recommendation for digital planning, perception, harness, or compute choices before trial freeze |
| `PhysicalExecutionFreezeReceipt` | Digest binding selected advisory-derived configuration to the exact preflight/trial package before approval |
| `PhysicalEfficiencyOutcome` | Preparation, simulation, planning, verification, and approved-trial resource evidence kept separate from safety outcome |

The advisory cannot arm, execute, retry, approve, or change a physical trial. Any post-freeze configuration change invalidates the freeze receipt and requires a new preflight and exact human approval.

## 13. Event ownership

All events use the existing `EventEnvelope`. Canonical v1.x event families include:

The complete authoritative catalog is `16_EVENT_CATALOG.json`; §12 of each release is its human-readable owner slice. Every entry identifies an owning release, subject contract, and strict `EventPayload` schema in `15_CONTRACT_KIT.json`. The envelope is inherited unchanged; the new payload contains a typed immutable subject reference and subject digest. Runtime integration must validate that resolved subject against its owning full schema and use the inherited tenant, causation, event-id, and ordering fields. Fixtures validate the extension payload, not a replica of predecessor schemas. Different lifecycle events are not aliases. Consumers must reject unknown event names/versions or quarantine for a registered upcaster, never guess their meaning.

```text
compute.profile.registered
compute.portfolio.compiled
compute.decision.recorded
compute.outcome.recorded
harness.bundle.registered
harness.portfolio.promoted
harness.selection.recorded
recovery.decision.recorded
recovery.attempt.completed
state.action.requested
state.proposal.validated
state.coordination.completed
harness_optimization.run.completed
policy_adaptation.experiment.completed
efficiency_transfer.outcome.recorded
physical_optimization.advisory.created
physical_execution.configuration.frozen
```

Every consequential command has an idempotency key. Consumers are idempotent and preserve causation/correlation across original execution, verification, recovery, training, promotion, and rollback.

## 14. API compatibility

The v1.x product uses `/api/v1` while public compatibility remains within the v1 line. New minor releases add endpoints or optional fields; they do not repurpose existing fields.

API rules:

- commands return authoritative resource/operation references;
- mutating commands require idempotency keys;
- mutable aggregates use optimistic concurrency/version preconditions;
- large traces and artifacts are referenced, not embedded;
- model-visible responses never include raw credentials;
- policy denial is explicit and cannot be retried with weaker parameters;
- unknown contract major versions fail closed;
- every routing/recovery/state decision is retrievable as a receipt.

## 15. Persistence ownership

Recommended logical aggregates:

```text
compute_profiles / compute_portfolios / compiler_runs
compute_predictions / compute_decisions / ttvs_outcomes
harness_bundles / harness_variants / harness_portfolios
recovery_decisions / attempt_chains / recovery_outcomes
runtime_state_views / state_proposals / state_receipts
harness_optimization_runs / trace_cohorts / comparisons
adaptation_experiments / pseudo_label_cohorts / adapters
efficiency_transfer_studies / transfer_outcomes
physical_advisories / freeze_receipts / efficiency_outcomes
```

Training/projection tables are derived from immutable evidence. Historical decisions and outcomes are never updated in place. Quarantine propagates through lineage to derived features, policies, harnesses, and adapters.

## 16. Decision authority matrix

| Component | May propose/rank | May execute | May verify | May promote/approve |
|---|---:|---:|---:|---:|
| Compute router | Compatible compute profiles | No direct capability | No | No |
| Harness router | Promoted compatible bundles | No direct capability | No | No |
| Recovery optimizer | Allowed recovery actions | Through existing controller | No | No |
| State coordinator | Allowed state actions/updates | Read/propose only | No | No |
| Harness optimizer | Candidate harness changes | Sandbox only | No sole evaluation | No |
| Adaptation trainer | Adapter candidates | Training sandbox only | No sole evaluation | No |
| Transfer estimator | Weak-prior recommendations | Simulation/shadow through policy | No | No |
| Physical optimizer | Advisory configuration | No physical execution | No | No |
| Independent verifier | Evidence requests | Verification capability | Yes | No promotion |
| Human authority | Review/override compatible option | Through governed commands | Human review | Yes by role |
| Policy/safety systems | Allow/deny/stop | Enforcement only | Policy/safety evidence | No research acceptance |

## 17. Migration rules

Every v1.x migration requires:

1. Owning contract and affected-release list;
2. Semantic and authority diff;
3. Forward/backward schema tests;
4. Golden fixtures and canonical hash vectors;
5. Historical replay including failures and contradictions;
6. Event consumer compatibility;
7. Feature/training lineage impact;
8. Quarantine propagation test;
9. Rollback/downcast plan where supported;
10. Human approval for objective, evidence, verifier, authority, identity, safety, or physical changes.

No migration may reclassify simulation as physical evidence, restore consumed physical approval, or broaden learned authority.

## 18. Protected surfaces

The following are never writable targets for a v1.x learned proposal:

- Policy Engine and authorization semantics;
- Token Broker and secret resolution;
- audit/event integrity;
- canonical verification outcomes;
- required verifier independence;
- evidence-class semantics;
- approval issuance/consumption;
- physical safety envelope, supervisor, watchdog, stop, controller, or driver;
- release/promotion signatures;
- retention holds for incidents/publications;
- Golden Direction or this registry.

## 19. Cross-release gate

Before a release owns a new contract:

- [ ] No existing canonical owner already covers the semantic.
- [ ] Contract has a machine-readable schema and content hash.
- [ ] Unknown major and unsafe enum behavior fail closed.
- [ ] Backend and frontend types are generated or conformance-tested.
- [ ] API and event forms reference the same contract.
- [ ] Minimal, full, invalid, adversarial, and historical fixtures pass.
- [ ] Authority and verifier separation tests pass.
- [ ] Migration and rollback tests pass.
- [ ] Evidence and model/harness/policy lineage is complete.
- [ ] Critical safety/security/correctness invariants remain unchanged.

## 20. Final registry rule

If implementation discovers the same concept under two names, stop and reconcile ownership through an ADR and registry revision. Compatibility adapters may bridge historical schemas, but two authoritative sources of truth are forbidden.

## 21. Revision-4 shared research contract ownership

The shared protocol owns these research records across v1.1-v1.8. A release may add typed payload fields through the normal compatibility process, but it may not redefine their evidence meaning.

| Contract | Canonical owner | Immutable purpose | Prohibited reinterpretation |
|---|---|---|---|
| `ResearchStudyPlan` | Shared research protocol | Freeze stage, population, sampling, candidates, verifier, holdout and drift plan | A plan is not a result or promotion |
| `VerifierQualificationReport` | Independent evaluation | Record labeled denominator, false acceptance/rejection and supported scope | Model agreement is not independent verification |
| `AdaptiveEvaluationStep` | Research acquisition controller | Record eligible units, selected unit, positive propensities and frozen candidates | Selection score is not verified success |
| `ObservableInterventionRecord` | v1.3 recovery research | Distinguish actual observed re-execution, no-op comparisons and localization from prediction-only counterfactuals | Predicted outcome is not causal proof |
| `EvidenceDerivationRecord` | v1.4 state/provenance | Bind sources, least trust, derivation and invalidation dependencies | Derived text cannot gain command authority |
| `DriftEvaluationPlan` | v1.7 transfer research | Freeze time-forward update strategy, budgets, shift test and fallback | A guarantee cannot remain asserted after its assumptions fail |

These contracts are research evidence. They do not grant dispatch, promotion, approval, training, secret access, provider scheduling, physical control or safety authority. Every locator resolves through the existing canonical artifact registry, and every lifecycle event uses the shared event envelope and outbox/replay rules.

### Revision-4 evidence extensions

| Projection | Owner | Required content and limit |
|---|---|---|
| `ResearchDataRolePlan` | Shared research protocol | Independent-unit and lineage membership, access order and selection/calibration/final boundaries |
| `UncertaintyStatement` | Independent evaluation | Statistical target, assumptions, labels, calibration rank and bounded guarantee; never approval |
| `EffectiveSettingObservation` | Existing profiler/evaluator | Requested, accepted and observed effective configuration with unavailable signals explicit |
| `ResearchComparisonPlan` | Owning release's research evaluator | Study stage, mandatory controls, candidates, hypothesis manifest and full budget |
| `StateConsumptionReceipt` | Existing v1.4 state owner | Atomic dependency/epoch use decision, not a replacement state store |

`ResearchStudyPlan` references the first, second and fourth projections. Its candidate count must match immutable candidate references. Verifier reports retain explicit accepted/incorrect/correct/uncertain denominators and analysis provenance. Acquisition steps bind the frozen candidate list and observed-setting evidence. Intervention records retain actual/no-op repeat and comparison evidence. Derivations pin dependency snapshots. Drift plans bind uncertainty statements and label arrival/update policy. No event adds authority; each resolves a versioned owner payload through existing artifact/header adapters.

The full field definitions and executable cross-field rules are in `15_CONTRACT_KIT.json` and `29_validate_revision4.py`; document 28 records migration, alternatives and rejected transfers. Old prototype bytes must not pass as schema 2 merely by changing a version number.

