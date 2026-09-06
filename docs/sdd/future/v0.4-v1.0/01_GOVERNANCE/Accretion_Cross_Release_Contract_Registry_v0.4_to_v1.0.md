# Accretion Cross-Release Contract Registry

## v0.4 to v1.0

**Status:** Normative package governance  
**Purpose:** Prevent contract redefinition, semantic drift, unsafe migration, and out-of-order implementation  
**Applies to:** All v0.4-v1.0 SDDs and every implementation derived from them  
**Precedence:** Golden Direction and permanent safety/authority invariants override this registry; this registry overrides duplicate illustrative schemas inside later SDDs

---

## 1. Why this registry exists

The forward SDDs intentionally span node routing, Robotics, graph planning, joint orchestration, capability evolution, and the v1.0 integrated product. Several releases refer to the same concepts. Without central ownership, an implementation agent could create multiple incompatible meanings for `VerificationResult`, `ExperienceRecord`, `NodeContract`, evidence types, risk classes, or policy receipts.

This registry establishes:

- one canonical owner for every cross-release contract;
- extension and migration rules;
- stable identity, hashing, and event conventions;
- explicit release dependencies;
- fields later releases may add but may not reinterpret;
- authority boundaries that no schema migration may weaken.

## 2. Document precedence

For a conflict, apply this order:

1. applicable law, approved physical safety case, and mandatory facility/manufacturer rules;
2. Accretion permanent invariants and Golden Direction;
3. approved `ObjectiveContract` and project policy;
4. this cross-release registry;
5. the currently unlocked, approved release SDD;
6. preceding released SDDs and ADRs;
7. locked forward SDDs;
8. research protocols and charters;
9. examples and UI copy.

A locked forward SDD never authorizes implementation or changes active authority.

## 3. Canonical contract header

Every persisted contract introduced at or after v0.4 MUST embed or inherit:

```yaml
contract_type: canonical-string
schema_version: semver
contract_id: uuid
content_hash: sha256
created_at: rfc3339
created_by: principal-ref
workspace_id: uuid
project_id: uuid
```

Optional shared fields:

```yaml
supersedes_contract_id: uuid-or-null
objective_contract_ref: {contract_id: uuid, revision: integer, content_hash: sha256}
labels: {string: string}
retention_class: canonical-enum
```

### 3.1 Canonical serialization

- Hash input is canonical UTF-8 JSON using sorted keys and normalized numbers.
- The `content_hash` field itself is omitted when calculating the hash.
- Timestamps use UTC RFC 3339 with explicit `Z` or offset.
- Units use canonical UCUM-compatible identifiers where practical.
- Frames, clocks, environments, artifacts, runtimes, models, tools, skills, capabilities, adapters, verifiers, and policies use immutable typed references.
- Floating-point comparison tolerances belong to verifier or experiment contracts, never global serialization.

### 3.2 Version compatibility

| Change | Version effect | Reader behavior |
|---|---|---|
| Add optional field with defined default | Minor | Preserve unknown field; may process |
| Add enum value | Minor only if readers fail safely | Unknown value cannot be coerced |
| Clarify documentation without semantic change | Patch | Process normally |
| Remove/rename field | Major | Reject unknown major |
| Change authority, verification, safety, unit, or identity semantics | Major plus migration review | Fail closed |
| Make optional field required | Major | Explicit migration required |

No migration may make a denied action allowed, convert `FAIL`/`INCONCLUSIVE` to `PASS`, or convert simulation evidence to physical evidence.

## 4. Stable identifiers and references

The following references are stable across releases:

| Reference | Required identity |
|---|---|
| `PrincipalRef` | issuer, subject, workspace/tenant, authentication context |
| `RuntimeRef` | runtime ID, adapter version, provider/model capability profile |
| `CapabilityRef` | canonical capability ID and schema version |
| `ToolRef` | normalized tool ID, implementation digest |
| `SkillRef` | skill ID, version, package digest |
| `PluginRef` | plugin ID, version, manifest digest |
| `ConnectionRef` | opaque connection ID, owner scope, provider; never token |
| `EnvironmentRef` | environment ID, version/image digest, policy profile |
| `VerifierRef` | verifier contract ID plus implementation digest |
| `EvidenceRef` | evidence ID, class, content digest |
| `PolicyRef` | policy ID, version, content digest |
| `ArtifactRef` | content-addressed URI/digest, media type, retention class |

Aliases must resolve to immutable references before routing, planning, approval, or execution.

## 5. Stable enums

### 5.1 Verification state

```text
PENDING | PASS | FAIL | INCONCLUSIVE | ERROR | QUARANTINED
```

Rules:

- `ERROR` is not `INCONCLUSIVE` and neither is `PASS`.
- A required verifier `FAIL`, `ERROR`, or unresolved `INCONCLUSIVE` blocks acceptance.
- `QUARANTINED` is append-only governance state applied after a material concern.

### 5.2 Evidence class

```text
DIGITAL | SIMULATION | PHYSICAL | HUMAN_ATTESTATION | EXTERNAL_SOURCE
```

Classes are not interchangeable. Derived evidence retains all parent classes and provenance.

### 5.3 Risk class

```text
LOW_DIGITAL | MEDIUM_DIGITAL | HIGH_DIGITAL | SIMULATION | PHYSICAL_HIGH | PROHIBITED
```

Project policy may make a class stricter. It may not reduce `PHYSICAL_HIGH` through a plugin, learned policy, or runtime request.

The repository's v0.1 `RiskLevel` (`LOW|MEDIUM|HIGH|CRITICAL`, the human-approval ladder) stays; `RiskClass` maps onto it totally and `PROHIBITED` maps to nothing (SDD v0.4 ADR-054).

### 5.4 Failure ownership

```text
CONFIGURATION | STRUCTURAL | CAPABILITY | VERIFICATION | ENVIRONMENT | SAFETY | AUTHORITY | RESOURCE | UNKNOWN
```

- Router handles `CONFIGURATION` when the graph and contract remain valid.
- Planner handles `STRUCTURAL`.
- Capability manager handles missing/broken allowed capability implementations.
- `SAFETY`, `AUTHORITY`, and unresolved `UNKNOWN` stop automatic recovery.

## 6. Inherited foundation contracts

These originate in the repository v0.1-v0.3 SDDs and MUST be reused rather than recreated.

| Contract/interface | Canonical owner | v0.4+ rule |
|---|---|---|
| `TaskEnvelope` | v0.1 | Rough task input; not authoritative objective approval |
| `PromptContract` | v0.1 | Runtime prompt/config boundary; not `NodeContract` |
| `ContextBundle` | v0.1 | Immutable/materialized context refs; secrets excluded |
| `TaskProfile` | v0.1 | Deterministic strategy profiling input |
| `StrategyDecision` | v0.1 | `DIRECT/LOOP/GRAPH/HYBRID`; later learned planning does not mutate historical decisions |
| `AgentRuntime` | v0.1 | Replaceable worker boundary; Robotics adapters are not agent runtimes |
| `WorkflowTemplate` | v0.1 | Static validated topology |
| `RunGraph` | v0.1/v0.2 extension | Backend-authoritative instantiated graph |
| `GraphProjection` | v0.1 | Read-only UI projection |
| `WorkflowProposal` | v0.2 | Proposed dynamic graph, not authority |
| `GraphRevision` | v0.2 | Immutable revision; v0.8 extends reason/learning metadata only |
| `CapabilityRequest` | v0.1/v0.3 extension | Requests normalized capability; no authority grant |
| `ConnectionRef` | v0.3 | Opaque identity/connection handle |
| `TokenHandle` | v0.3 | Broker-only resolution; never visible to model |
| `MetaPlugin`/manifest | v0.3 | Package declaration; installation does not authorize |

Implementations MUST inspect the repository's exact current schemas before adding migrations.

## 7. v0.4 canonical contracts

v0.4 owns node-level objective, routing, verification-feedback, and learned-router contracts.

| Contract | Owner | Stable semantic |
|---|---|---|
| `ObjectiveContract` | v0.4 governance | Human-approved goals, constraints, success floor, utility, budget, risk |
| `ObjectiveContractRef` | v0.4 | Exact revision/hash reference |
| `NodeContract` | v0.4 | One routable workflow graph node; requirements precede routing |
| `VerificationSpec` | v0.4 | Required verifier contracts and inconclusive policy |
| `RoutingContext` | v0.4 | Immutable decision snapshot |
| `ExecutionConfiguration` | v0.4 | Runtime/model/tool/skill/verifier implementation/environment tuple |
| `ConfigurationCandidate` | v0.4 | Policy-compatible candidate with predicted outcomes |
| `CompatibilityDecision` | v0.4 | Deterministic admissibility receipt for node configuration. Distinct from the v0.2 `CompatibilityAssessment` of a past experience (SDD v0.4 ADR-054) |
| `RoutingDecisionReceipt` | v0.4 | Candidate set, selected option, propensity, explanation, policy snapshot |
| `VerificationResult` | v0.4 | Independent verification outcome tied to spec and evidence. Code name `IndependentVerificationResult` (SDD v0.4 ADR-054): the v0.1 `VerificationResult` is API-exposed and keeps its name |
| `ExperienceRecord` | v0.4 | Verified, compatibility-scoped reusable experience. A projection keyed by the v0.2 P7 `experience_id`; no second `Experience` schema (SDD v0.4 ADR-054) |
| `FailureEvent` | v0.4 | Typed failure and owning recovery layer |
| `RouterModelVersion` | v0.4 | Immutable router artifact/data/config snapshot |
| `RouterPromotionReport` | v0.4 | Holdout, cohort, safety, rollback, and human promotion record |

### 7.1 `ObjectiveContract` minimum fields

```yaml
goal: string
scope_in: [string]
scope_out: [string]
verified_success_floor: number
false_acceptance_ceiling: number
utility_weights: {quality: number, cost: number, latency: number}
risk_policy_ref: PolicyRef
resource_budget: object
required_human_approvals: [object]
revision: integer
approval_receipt_ref: ArtifactRef
```

Changing an active objective creates a new revision with impact analysis and human approval. It affects only new runs/nodes unless an explicit safe migration says otherwise.

### 7.2 `NodeContract` minimum fields

```yaml
node_id: uuid
graph_revision_id: uuid
node_kind: canonical-enum
input_contracts: [schema-ref]
output_contracts: [schema-ref]
required_capabilities: [CapabilityRef]
allowed_risk_class: RiskClass
verification_spec_ref: {id: uuid, hash: sha256}
resource_cap: object
environment_constraints: object
failure_policy_ref: PolicyRef
```

Later releases may add embodiment or planner metadata but may not weaken these fields after routing.

### 7.3 `ExecutionConfiguration`

The hierarchy is:

```text
environment
→ runtime
→ model
→ tools/capabilities
→ skills/plugin implementations
→ independent verifier implementation
```

Compatibility pruning occurs before prediction/ranking. A learned policy never considers a policy-incompatible candidate.

## 8. v0.5 canonical contracts

| Contract | Owner | Extension rule |
|---|---|---|
| `EmbodimentDescriptor` | v0.5 | Later signatures derive from it; never mutate historical version |
| `ObservationSpec` | v0.5 | Add optional modalities by minor version; unit/frame changes are major |
| `ActionIntent` | v0.5 | High-level bounded intent; physical reuse requires v0.6 admission |
| `SafetyEnvelope` | v0.5 | Physical profile may be stricter; never learned/relaxed automatically |
| `RobotAdapterManifest` | v0.5 | Simulation/physical transport profiles are explicit |
| `SimulationExperimentContract` | v0.5 | Frozen simulation design and budgets |
| `SimulationEnvironmentSnapshot` | v0.5 | Content-addressed replay environment |
| `EpisodeRecord` | v0.5 | Simulation evidence only |
| `EmbodiedVerificationSpec` | v0.5 | Target/physical releases extend implementation references only |
| `AdapterConformanceReport` | v0.5 | Invalidated by adapter/environment dependency change |

`RobotAdapter` is not `AgentRuntime`. It translates validated embodied intents to a simulator/controller interface.

## 9. v0.6 canonical contracts

| Contract | Owner | Stable semantic |
|---|---|---|
| `PhysicalCellDescriptor` | v0.6 | Approved physical asset/cell profile |
| `PhysicalTrialContract` | v0.6 | Exact single physical attempt |
| `PhysicalTrialApproval` | v0.6 | Human approval bound to exact trial/preflight hash |
| `CalibrationBundle` | v0.6 | Versioned calibration and validity |
| `PhysicalEnvironmentSnapshot` | v0.6 | Human/automatic observed cell state |
| `SafetySupervisorContract` | v0.6 | Deterministic monitors and stop behavior |
| `ArmedTrialLease` | v0.6 | Short-lived single-trial gateway authority after approval consumption |
| `PhysicalEpisodeRecord` | v0.6 | Physical evidence; distinct from v0.5 record |
| `PhysicalIncidentRecord` | v0.6 | Append-only incident/lockout record |
| `SimToRealDiscrepancyReport` | v0.6 | Matched simulation/physical comparison |

### 9.1 Approval immutability

An approval hash includes the trial contract and successful preflight receipt. Approval is single-use. A failed arm consumes it. No automatic retry or schema migration can restore it.

### 9.2 Safety immutability

No later release may:

- learn, relax, or bypass a safety envelope;
- approve or arm a physical trial;
- turn a shadow recommendation into physical action;
- treat source/simulation evidence as physical acceptance;
- remove the independent stop/lockout path.

## 10. v0.7 canonical contracts

| Contract | Owner | Stable semantic |
|---|---|---|
| `EmbodiedTaskContract` | v0.7 | High-level task semantics plus target slots |
| `EmbodimentSignature` | v0.7 | Derived comparison representation |
| `EmbodimentCompatibilityDecision` | v0.7 | Hard gates plus bounded soft prior |
| `TransferEvidenceSet` | v0.7 | Frozen source evidence snapshot including failures/contradictions |
| `TransferCandidate` | v0.7 | Explicit transferable/non-transferable layers |
| `TargetAdaptationPlan` | v0.7 | Bounded target stages and fallback |
| `TransferOutcome` | v0.7 | Matched target/scratch result |
| `NegativeTransferEvent` | v0.7 | Append-only harm/fallback record |
| `TargetVerificationReceipt` | v0.7 | Target-specific acceptance evidence |

`EmbodimentCompatibilityDecision` is distinct from v0.4 `CompatibilityDecision`: the former evaluates source-target transfer; the latter evaluates node execution configuration. Implementations MUST use the full names internally.

## 11. v0.8 canonical contracts

| Contract | Owner | Stable semantic |
|---|---|---|
| `WorkflowPlanState` | v0.8 | Frozen planner observation |
| `GraphCandidateSet` | v0.8 | Validated graph options and behavior propensities |
| `PlannerDecisionReceipt` | v0.8 | Selected graph/revision and explanation |
| `GraphRevision` learning extension | v0.8 over v0.2 | Adds policy/evidence attribution; does not replace v0.2 identity |
| `PlannerOutcome` | v0.8 | Graph-level verified outcome and credit |

The graph grammar, node/edge types, conditions, loop bounds, and verifier placement constraints are validated by the deterministic `GraphValidator` owned by v0.2 and extended through versioned rules.

## 12. v0.9 canonical contracts

| Contract | Owner | Stable semantic |
|---|---|---|
| `HierarchicalOrchestrationState` | v0.9 | Joint but authority-separated planner/router snapshot |
| `ContractBridgeReceipt` | v0.9 | Validated bridge from graph/node requirements to configuration domain |
| `JointCandidateSet` | v0.9 | Graph/configuration options and behavior propensities |
| `JointDecisionReceipt` | v0.9 | Linked planner and router selections with explanations |
| `HierarchicalOutcomeReceipt` | v0.9 | Layer-specific plus end-to-end credit and verified outcome |
| `JointPolicySnapshot` | v0.9 | Separately versioned planner/router/coordinator artifacts and compatibility manifest |

Bundling does not merge authority. Planner, router, compatibility engine, policy engine, and verifier remain independently addressable and rollbackable.

## 13. v0.10 canonical contracts

| Contract | Owner | Stable semantic |
|---|---|---|
| `CapabilityGapReport` | v0.10 | Verified inability not owned by planning/routing/configuration |
| `CapabilityChangeProposal` | v0.10 | Bounded writable surface, rationale, and expected effect |
| `CandidateManifest` | v0.10 | Sandboxed build output with provenance/SBOM/digest |
| `CandidateEvaluationPlan` | v0.10 | Frozen held-in/out, critical, security, and conformance suites |
| `CandidateEvaluationReport` | v0.10 | Independent evidence; candidate cannot self-accept |
| `PromotionDecision` | v0.10 | Human-reviewed canary/release/reject decision and rollback metadata |
| `CapabilityLineageRecord` | v0.10 | Gap-to-proposal-to-evidence-to-release graph |

Capabilities may evolve; policies, approval semantics, Token Broker, immutable audit, and physical safety/stop surfaces are prohibited candidate targets.

## 14. v1.0 integration rule

v1.0 integrates the contracts above. It does not create competing v1.0 versions solely for naming consistency. When the v1.0 SDD shows an abbreviated schema, that schema is illustrative and the owning release contract plus this registry remains normative.

v1.0 may freeze a compatibility profile:

```yaml
profile_id: accretion.v1.0.contract-profile
required_contract_major_versions:
  ObjectiveContract: 1
  NodeContract: 1
  VerificationResult: 1
  EmbodimentDescriptor: 1
  PhysicalTrialContract: 1
  EmbodimentSignature: 1
  WorkflowPlanState: 1
  JointPolicySnapshot: 1
  CapabilityChangeProposal: 1
```

The actual major versions are selected at release freeze after migration testing.

## 15. Event envelope

All releases use:

```yaml
event_id: uuid
event_type: canonical-string
schema_version: semver
occurred_at: rfc3339
workspace_id: uuid
project_id: uuid
run_id: uuid-or-null
node_id: uuid-or-null
correlation_id: uuid
causation_id: uuid-or-null
producer: service-identity
payload: object-or-artifact-reference
payload_hash: sha256
```

Requirements:

- event IDs are globally unique;
- consumers are idempotent;
- causation/correlation is preserved across runtime, capability, Robotics, verification, and promotion planes;
- large/binary/sensitive payloads use artifact references;
- tokens and secrets never enter events;
- state is reconstructed from authoritative persistence plus events, not UI state.

## 16. Evidence and provenance

Every accepted claim MUST trace:

```text
ObjectiveContract
→ workflow/graph revision
→ NodeContract
→ routing/planning receipts
→ runtime/tool/adapter/environment artifacts
→ raw and derived evidence
→ verifier implementations/results
→ acceptance or human-review decision
```

Learned policies and evolved capabilities add their training/evaluation/promotion lineage. Cross-embodiment results add source-to-target lineage. Physical results add approval, preflight, armed lease, safety trace, and incident state.

## 17. Migration process

Every contract migration requires:

1. owner and affected-release list;
2. semantic diff and authority/safety impact analysis;
3. forward and backward compatibility tests;
4. fixture migration and round-trip tests;
5. event consumer compatibility test;
6. replay against historical accepted and failed records;
7. policy/training snapshot impact analysis;
8. rollback plan;
9. human approval for authority, objective, verifier, evidence-class, or physical changes.

Historical records are never rewritten in place. A migrated projection references the original record and migration receipt.

## 18. Implementation placement

Recommended repository ownership:

```text
src/accretion/contracts/core/
src/accretion/contracts/routing/
src/accretion/contracts/robotics/
src/accretion/contracts/planning/
src/accretion/contracts/evolution/
src/accretion/events/
src/accretion/migrations/
tests/contracts/
tests/fixtures/contracts/
```

The exact layout may adapt to the existing codebase, but one schema must not be independently copied into multiple modules.

## 19. Contract release gates

- [ ] Every contract has one owner and schema version.
- [ ] JSON Schema or equivalent machine-readable validation exists.
- [ ] Golden fixtures cover minimal, complete, invalid, and unknown-version cases.
- [ ] Canonical hash vectors are shared across backend/frontend languages.
- [ ] Events and APIs reference the same canonical contract.
- [ ] Migration tests preserve historical evidence and acceptance state.
- [ ] No migration expands authority or weakens safety/verification.
- [ ] Simulation and physical evidence remain type-distinct.
- [ ] Planner/router contracts remain authority-separated.
- [ ] Capability candidates cannot modify protected contracts.
- [ ] v1.0 conformance profile passes before release.

## 20. Open registry decisions

| # | Decision | Proposed default | Owner/deadline |
|---:|---|---|---|
| 1 | Schema language | JSON Schema 2020-12 plus generated Python/TypeScript types | v0.4 entry |
| 2 | Canonical JSON | RFC 8785-compatible implementation or documented equivalent | v0.4 entry |
| 3 | Units | UCUM strings plus domain validation | v0.5 entry |
| 4 | Frame semantics | Explicit ROS-compatible frame IDs and transform provenance | v0.5 entry |
| 5 | Event compatibility | Upcasters at read boundary; never mutate stored event | v0.4 entry |
| 6 | Contract registry service | Library/module first; network service only if justified | v0.4 architecture review |
| 7 | Digital signatures | Required for promotion and physical approval; content hashes elsewhere | v0.6 entry |
| 8 | Evidence graph storage | Relational edges plus content-addressed artifacts first | v0.4 entry |
| 9 | Enum extension | Unknown values fail closed on authority/safety paths | Permanent |
| 10 | v1.0 profile | Freeze only after all migration/replay suites pass | v1.0 RC |

## 21. Final registry rule

If Codex finds a schema with the same concept but a different name or meaning, it MUST stop and reconcile ownership through an ADR and registry update before implementing both. Compatibility code may bridge historical versions; duplicate sources of truth are not acceptable.
