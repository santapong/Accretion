# Accretion v1.x — Document revision 2 technical addendum

**Document revision:** 2  
**Date:** 2026-09-05  
**Status:** Forward design; implementation and research acceptance pending  
**Priority:** Shared evaluation protocol and v1.1, then cross-release consistency

## 1. Scope and review disposition

This is a document revision, not Accretion v1.2.1 or a product release. Existing v0.4–v1.0 documents and code are unchanged. Revision 2 corrects the following findings in the owning documents and adds executable design examples. The package does not claim measured ML savings or completed platform integration.

| Finding | Correction | Owning source | Design evidence |
|---|---|---|---|
| R2-01 Failure can look fast | Fixed-horizon capped time to PASS; separate time-to-terminal; all assigned tasks retained | Shared protocol §§2, 3, 10, 19 | TTVS_* fixtures and metric regression |
| R2-02 Competing/incomplete dispatch authority | Complete assembly, final validation, immutable binding to one existing routing receipt, transactional outbox and epoch check | v1.1 §§8–10 | COMPUTE_*, DISPATCH_* fixtures |
| R2-03 Inconclusive recovery conflict | Deterministic status matrix; pause before evidence collection; explicit human resolution | v1.3 §10.5 | RECOVERY_* fixtures |
| R2-04 Approval misses freeze configuration | Acyclic manifest→trial/preflight→freeze wrapper; unchanged v0.6 signed fields; atomic invalidation/arming boundary | v1.8 §10.4 | PHYSICAL_BINDING_* and ARM_* fixtures |
| R2-05 Impossible lifecycle records | Conditional references, separate promotion/invalidation records, truthful unknown timings and retry observations | Registry §§5–6; v1.2 §8; v1.8 §§8–9 | HARNESS_*, PHYSICAL_*, FREEZE_* fixtures |
| R2-06 Optional experiment blocks roadmap | Separate platform readiness, research closure and artifact promotion | Shared protocol §11; README §10; v1.6/v1.7 §3 | LAB_* fixtures |
| R2-07 Event-name drift | One catalog, owner slices, strict payload schema, terminal experiment event | Registry §13 and each SDD §12 | Catalog parity and EVENT_* fixtures |
| R2-08 Acceptance not traceable | Stable criterion IDs, milestone and accountable role, evidence target, fixture link and explicit pending status | Each SDD §21 and traceability JSON | Traceability validation |

## 2. How to read and run the kit

Read the shared protocol, v1.1, then this addendum. `15_CONTRACT_KIT.json` contains JSON Schema Draft 2020-12 schemas for **new extension payloads and normalized lifecycle projections**. It deliberately does not reproduce the inherited canonical header, ArtifactRef, EventEnvelope, RoutingDecisionReceipt, physical approval or lease schemas. `16_EVENT_CATALOG.json` is the authoritative name/owner/payload map. `17_CONTRACT_LIFECYCLE_FIXTURES.json` contains synthetic valid/invalid cases. `18_ACCEPTANCE_TRACEABILITY.json` maps every release §21 criterion and the shared criteria below. `19_validate_revision2.py` validates schema, semantic rules, lifecycle cases, catalog parity and acceptance coverage.

```bash
python3 -m pip install -r 20_validation_requirements.txt
python3 19_validate_revision2.py
```

Run from this package directory. No network, repository, model call, deployment, approval, or physical action occurs during validation. Installing the declared validator is the only dependency step. Schema validation proves payload shape; reference resolution, signatures, isolation and transaction semantics require the owning implementation tests. Negative fixtures intentionally fail a named validator check; that is a successful test, not an operational incident.

The new `Locator` is a digest-bearing lookup value inside these extension payloads: contract_type, record_id, content_hash. It is not a replacement for ArtifactRef or ApprovalArtifactRef. A production adapter must resolve it to the complete owner-defined typed reference/header, enforce tenant/run/approval scope, compare its canonical content hash and schema version, then validate the target owner schema. It must preserve ArtifactRef.run_id where required. Off-run assets cannot fabricate run_id; they require an existing suitable registered-artifact owner or a reviewed new subtype. All schemas in this kit reject extras; unknown major versions reject. Golden hashes cover a deliberately restricted ASCII/integer canonical JSON fixture domain; full production interoperability must pass predecessor canonicalization vectors.

## 3. v1.1 binding contract and field mapping

`EffectiveComputeConfiguration` is a materialized sidecar, with the existing execution-configuration reference plus pinned runtime, model, connection handle, context, tools, optional harness, retry/escalation policies, explicit reasoning/resource caps and adapter reference. `ComputeExecutorAdapterManifest` declares supported fields/schema versions and its executable/package digest. Both payloads have strict machine-readable schemas and fixtures. They cannot replace ExecutionConfiguration ownership or turn a policy-denied assembly into an admissible one. Connection values are opaque handles, never credentials.

Normalized selection fixture fields map as follows: selected_ref → ComputeDecisionReceipt.selected_profile_ref or HarnessSelectionReceipt.selected_harness_ref; fallback_ref → the respective fallback profile/harness ref; node_ref → node_contract_ref; mode → decision_mode for compute and mode for harness. Persisted adapters must make this mapping explicit and preserve the inherited owner envelope. Projection field names are not an alternate public API.

`ComputeDispatchBinding` payload is immutable and includes the decision/proposal, authoritative routing receipt, final compatibility receipt, selected or fallback profile, resolved effective configuration, frozen node/verifier/objective/joint policy/graph digests, optional harness selection, budget reservation, adapter manifest, candidate-set digest, idempotency key, expected epoch, expiration and dependency digests. Its `content_hash` is the SHA-256 of canonical payload bytes excluding only its own content_hash. Owner headers and typed reference validation remain required at persistence.

| Source | Assembly rule | Rejection condition |
|---|---|---|
| ExecutionConfiguration | Resolve immutable owner-defined runtime/model/provider/tool configuration | Alias unresolved, retired, unsupported schema |
| ComputeProfile reasoning limits | Materialize explicit supported adapter fields; min with stricter node/objective/policy limits | Any cap relaxed or field ignored |
| Context policy | Pin the actual template/selection/compaction assets | Mandatory context or verifier requirements removed |
| Tool subset | Intersect with existing compatible tools and permissions | New authority implied by profile |
| Harness bundle | Pin general baseline or promoted compatible bundle | Unpromoted/revoked bundle, incompatible joint pair |
| Retry/escalation policy | Reference existing permitted policy; no automatic execution from extension | Hidden retry, high-risk/physical retry, unreserved attempt |
| VerificationSpec | Frozen before selection; exact same digest through validation and dispatch | Producer substitutes or weakens verifier |
| JointPolicySnapshot/graph | Bind existing separated planner/router snapshot and graph revision | Concurrent replan, revoked policy, stale epoch |

The single canonical RoutingDecisionReceipt is created by its existing owner using the complete candidate. The optimizer receipt is upstream evidence. A later dispatch binding references both, avoiding a cycle. No second independent selector runs between commit and execution. A post-selection dependency change causes rejection and a new decision; it cannot mutate the committed object.

Transaction boundary: compare expected epoch → final authoritative validation/budget reservation → persist receipts and outbox atomically. Dispatcher boundary: compare current epoch/revocation/expiry/digests → claim unique attempt → invoke existing executor. If the executor is remote, propagate an owner-enforced idempotency token; unknown invocation outcome pauses/reconciles. An outbox alone does not guarantee exactly-once external side effects. The fixture state machine checks the specified decisions; it does not prove distributed atomicity.

## 4. Lifecycle and reference presence

Selection payloads have one state discriminator. ABSTAIN requires selected=null and a nonempty reason; other modes require a selected candidate in the recorded candidate set. An absent admissible fallback yields PAUSE. A fallback field does not authorize it. SHADOW never creates an optimizer dispatch; baseline execution has its own existing authority path.

Harness candidate content has no future promotion reference. A lifecycle event references the immutable bundle; PROMOTED requires the existing promotion decision and earlier successful evaluation. Revocation appends evidence; it does not mutate the bundle or erase promotion history. Selection and dispatch recheck current eligibility.

| Physical outcome stage | Freeze | Granted approval | Lease | Task verification | Safety outcome |
|---|---|---|---|---|---|
| PRE_FREEZE | null | null | null | null | null |
| FROZEN_UNAPPROVED | required | null | null | null | null |
| APPROVED_UNARMED | required | required | null | null | null |
| ARMED_NO_EXECUTION | required | required | required | null | optional if actually emitted |
| EXECUTED_UNVERIFIED | required | required | required | null | optional; missing must be visible |
| VERIFIED | required | required | required | required, possibly FAIL/INCONCLUSIVE | required for complete verified-stage record |

Stage is the furthest reached fact, not an efficiency/safety PASS. A partial verification or missing safety record stays EXECUTED_UNVERIFIED and references available evidence separately; it cannot fabricate a complete VERIFIED result. Incidents/evidence refs remain available in every stage. Preflight rejection, ineligibility and simulation failure use PRE_FREEZE; human denial uses FROZEN_UNAPPROVED; a granted approval expiring before arm uses APPROVED_UNARMED. Lease expiry without invocation uses ARMED_NO_EXECUTION. A pre-command stop can still have real safety evidence.

Prohibited retry requests and actual reexecutions are nonnegative observed counters. Requests are always denied and reviewed; an actual reexecution blocks the hard gate. Schema validity must allow recording an incident. It must not imply safe acceptance. Missing/unperformed measured phases use null plus quality/reason, not invented zero. No-PASS-by-H always contributes H independently of missing component spans.

## 5. Physical freeze integration and race contract

The exact field mapping in v1.8 §10.4 is normative. The configuration manifest is hashed first; task_parameters_ref pins the manifest through a compatible task schema. Trial and preflight then pin those inputs. The freeze is a later audit wrapper referencing their existing hashes. The unchanged approval signs trial_contract_hash and preflight_receipt_hash; it never needs to sign a future wrapper or its own consumption record.

Before approval consumption, the v0.6 owner checks that actual loaded input digests match the signed chain and no invalidation/revocation won the serialized epoch check. The fixture model permits one arm on a valid unconsumed approval, rejects duplicate consumption, and rejects stale or mutated configuration. Invalidation after arm invokes the existing safety transition and cannot edit the active configuration. These are integration requirements; P5/P7 must demonstrate them against the actual v0.6 owner. A reference projection or UI warning cannot enforce this boundary.

## 6. Dependency and acceptance gate semantics

The README sequence is roadmap order. Each release separately records platform readiness, research closure and artifact promotion. v1.1–v1.5 retain their explicit predecessor entry gates. v1.6 lab functionality must satisfy operational acceptance; a successful adapter is optional. v1.7 requires the stable v1.1–v1.5 interfaces and v0.7 target-only transfer baseline, plus an explicit v1.6 closure. v1.8 retains all target-simulation and v0.6 physical entry gates; optional adapter evidence cannot substitute for them.

v1.6 research-only criteria (family selection, observed research success/improvement and matched-budget study) apply to a launched experiment and promotion claim. With DEFERRED, store a signed applicability decision and no performance claim; mark those research criteria DEFERRED_BY_DECISION at implementation time. Operational criteria are still mandatory and tested with synthetic isolated lab inputs. With FAIL/INCONCLUSIVE, preserve actual results; no adapter promotion. A research FAIL is not relabeled acceptance PASS. The closure must name an accountable human role, rationale, dependency impacts and revisit condition. This package leaves all runtime evidence pending.

## 7. Acceptance traceability rules

`18_ACCEPTANCE_TRACEABILITY.json` is the complete mapping. Each ID is stable within the v1.x namespace and never changes predecessor AC4 identifiers. Each row includes exact source requirement text, release, milestone owner, accountable role, evidence kind, concrete proposed evidence path, fixture IDs when provided, and status. Role owners must be bound to named people before milestone kickoff. A proposed path is an evidence obligation, not an existing implementation test.

All criteria begin IMPLEMENTATION_PENDING or RESEARCH_PROTOCOL_PENDING. Passing a design fixture only fills design-validation evidence. It cannot check the SDD checkbox, authorize implementation, approve a research claim, or grant physical approval. Runtime evidence must identify source commit, full contract versions, environment, raw reports and reviewer. Critical criteria have no automatic waiver. New research updates preserve IDs or mark supersession explicitly; a patch number is never used merely to revise this document.

Shared criteria:

- [ ] **ACX-P1-001** Every study freezes a positive horizon and complete numerical gates before training/search/evaluation; invalid or missing values block freeze.
- [ ] **ACX-P1-002** All allocated non-PASS-by-H outcomes contribute H and remain in the success denominator; missing durations are never zero-filled.
- [ ] **ACX-P1-003** Event owner slices match the single catalog and every payload resolves a valid immutable subject under its owning schema.
- [ ] **ACX-P1-004** Every release criterion has an accountable milestone, evidence target and explicit implementation/research status; design fixtures cannot establish release PASS.
- [ ] **ACX-P1-005** Full inherited canonicalization, schema, typed-reference, replay and transaction conformance passes at the owning implementation milestone.

## 8. Remaining freeze decisions and integration obligations

| Decision | Accountable role | Due | Blocking effect |
|---|---|---|---|
| Numerical horizon, success margin/floor, effect, missingness, power and cohort gates | Research owner + independent evaluation owner | Each protocol freeze | No training/search/final study or promotion claim |
| Concrete schema prefix/version and generated owner-header/reference adapters | Contract maintainer | C1; each later contract milestone | No production persistence/dispatch from fixture projections |
| Supported effective-configuration adapter field map and complete compatibility test | Routing maintainer | C1/C5 | No v1.1 canary or dispatch |
| Actual database/outbox and remote invocation reconciliation implementation | Runtime maintainer | C5 | No v1.1 activation |
| Task-parameters schema binding and serialized invalidation/arm boundary | v0.6 owner + physical integration maintainer | P5/P7 | No physical trial through v1.8 advisory |
| Named milestone assignees and evidence report locations | Release maintainer | Milestone kickoff | Milestone cannot be signed off |

These are explicit implementation/protocol gates, not claims that the documents already prove implementation. Later-release full wire schemas beyond the supplied targeted payloads remain owned by each contract milestone. Revision 2 resolves the review's design contradictions while exposing those obligations for implementation review.
