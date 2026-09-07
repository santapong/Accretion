# Accretion v1.x Patch Release SDD Template

**Document revision:** 4  
**Supersedes document revision:** 3  
**Revised:** 2026-09-06

**Template status:** Normative minimum for `1.y.z`, where `z > 0`  
**Use only after:** The owning `1.y.0` release exists  
**Primary rule:** A patch preserves public semantics, authority, safety, and backward compatibility

---

## 1. Patch identity

```yaml
product: Accretion
target_release: 1.y.z
base_release: 1.y.previous
document_revision: 1
status: PROPOSED
change_class: BUG_FIX | SECURITY_FIX | INTERNAL_PERFORMANCE | DOCUMENTATION | NON_SEMANTIC_DIAGNOSTIC
owner: team-or-person
created_at: timestamp
target_branch: string
base_commit: full-git-sha
tracking_issue_refs: [url-or-id]
adr_refs: [url-or-id]
```

Do not assign `1.y.z` merely because more detail was added to the future `1.y.0` design. Before `1.y.0` ships, update its `document_revision` instead.

For unreleased v1.1–v1.8 design additions, use document revision 4 and the amendment process, not this software patch template. Any later patch touching research evidence must preserve uncertainty scope, data-role lineage and historical results defined by the shared protocol.

## 2. Executive decision

Complete one sentence:

> This is a patch because it [corrects/improves] already released behavior while preserving [named contracts, API/event semantics, authority, safety, verification, and supported capability scope].

If that sentence is not true, stop and classify the change as a minor or major candidate.

## 3. Change-class gate

Check exactly one primary class and all applicable secondary classes:

- [ ] Bug correction;
- [ ] Compatible security correction;
- [ ] Internal latency/CPU/memory/query/compute optimization with identical observable decisions;
- [ ] Documentation correction matching released behavior;
- [ ] Non-semantic diagnostics/tests/observability;
- [ ] Drop-in policy/model/harness artifact correction explicitly allowed by the released contract.

The change must **not**:

- add a new user/product capability;
- add a new learned action or candidate class;
- expand an eligible, exploration, risk, or physical cohort;
- alter public API/event/persistence meaning;
- weaken verifier, evidence, correctness, success, policy, identity, security, or safety requirements;
- change failure ownership or authority separation;
- make a denied operation allowed;
- modify a physical controller, safety envelope, stop path, approval semantic, or retry rule;
- silently change the research estimand or acceptance gate.

If any prohibited item applies, this template rejects the patch classification.

## 4. Problem statement and evidence

Document:

1. Released behavior expected;
2. Actual observed behavior;
3. Affected versions, contracts, cohorts, configurations, and environments;
4. First-known occurrence and detection path;
5. Severity, frequency, user/research impact, and worst credible consequence;
6. Reproduction steps or a pinned before-benchmark;
7. Evidence and incident references;
8. Whether historical decisions, results, metrics, or claims may be invalid.

For a security patch, use restricted references for exploit details and follow the security disclosure process.

## 5. Scope and exclusions

### Included

- [Exact files/services/modules/contracts/artifacts being corrected]
- [Exact affected cohorts]
- [Tests, migration, telemetry, documentation, release notes]

### Excluded

- [Adjacent feature requests]
- [New candidate/action types]
- [Schema/authority/safety redesign]
- [Unrelated refactors]

## 6. Compatibility and SemVer proof

| Surface | Before | After | Compatible? | Evidence |
|---|---|---|---:|---|
| Public API |  |  |  |  |
| Event semantics |  |  |  |  |
| Contract schema |  |  |  |  |
| Persistence/history |  |  |  |  |
| CLI/SDK/config |  |  |  |  |
| Plugin/runtime adapter |  |  |  |  |
| Learned decision/action set |  |  |  |  |
| Authority/policy |  |  |  |  |
| Verification/evidence |  |  |  |  |
| Security/secrets/tenancy |  |  |  |  |
| Physical safety/approval |  |  |  |  |

State the supported backward/forward read behavior and why existing clients, events, stored evidence, replays, and conservative fallback remain valid.

## 7. Authority, safety, and verification impact

Answer each explicitly:

- Does any principal gain a permission? `NO` is required for a patch unless it only restores already-documented released permission.
- Does any learned component gain an action or cohort? `NO` is required.
- Does a verifier, evidence class, acceptance rule, or terminal state change? `NO` is required except correction to match the released normative contract.
- Does a physical safety, controller, approval, lease, stop, or retry semantic change? `NO` is required for normal patch classification.
- Can the producer accept or promote its own output after this change? `NO` is required.
- Are contradictions, failures, incidents, and history still append-only? `YES` is required.

Any nontrivial ambiguity triggers architecture/security/safety review and may require a new minor, major, or exceptional security process.

## 8. Technical design

Describe the smallest implementation that corrects the issue:

- owning component and control flow;
- current root cause;
- corrected algorithm or logic;
- input/output invariants;
- concurrency, idempotency, timeout, and retry behavior;
- caches/indexes/query plans and invalidation, if applicable;
- model/policy/harness artifact handling, if applicable;
- error and fallback behavior;
- why unrelated behavior cannot change;
- feature flag or kill switch;
- predecessor/conservative fallback.

For performance patches, identify the exact equivalence predicate used to show that before/after decisions and evidence are semantically identical.

## 9. Contract, API, event, and persistence delta

The expected semantic delta is `NONE`.

```yaml
public_contract_semantic_delta: NONE
api_semantic_delta: NONE
event_semantic_delta: NONE
authority_delta: NONE
safety_delta: NONE
supported_capability_delta: NONE
```

List any allowed implementation-only changes such as an index, optional diagnostic, corrected internal calculation, or bug-compatible reader. If a field is added, prove it is optional, safely defaulted, ignored by old readers, preserved where required, and does not change authorization or execution.

## 10. Historical data and migration

Specify one:

```text
NO_MIGRATION
DERIVED_PROJECTION_REBUILD
COMPATIBLE_FORWARD_MIGRATION
INCIDENT_QUARANTINE_AND_RECOMPUTE
```

Document:

- affected rows/events/artifacts/evidence;
- immutable-history treatment;
- upcast/downcast behavior;
- canonical hash impact;
- quarantine propagation;
- projection/index rebuild;
- restart/resume behavior;
- rollback behavior;
- proof that historical evidence is not rewritten in place.

## 11. Reproduction and regression test

Provide a test that fails on the base release and passes on the patch:

```yaml
fixture_ref: immutable-ref
base_release_result: FAIL
patched_release_result: PASS
oracle_ref: independent-or-deterministic-ref
environment_ref: pinned-environment
```

When the patch corrects a false result or metric, identify dependent decisions, publications, dashboards, policy artifacts, harnesses, and research claims requiring review.

## 12. Test strategy

At minimum, run applicable:

- unit and property tests for the corrected invariant;
- golden contract/hash fixtures;
- API/event consumer compatibility;
- migration and rollback tests;
- deterministic replay before/after comparison;
- concurrency, idempotency, retry, timeout, and crash recovery;
- verifier/producer separation;
- authorization, tenant isolation, and secret non-exposure;
- OOD, uncertainty, fallback, and critical cohorts;
- simulator/physical command-denial and freeze-binding tests when adjacent to physical code;
- incident/quarantine lineage tests;
- full owning-minor regression suite.

## 13. Performance and research evidence

For an optimization patch, freeze:

- workload, task/cohort manifest, and environment;
- base and candidate commits/artifact digests;
- repetitions and statistical method;
- correctness equivalence/non-inferiority gate;
- p50/p95 latency and TTVS;
- compute, memory, CPU, query, network, and cost measures as applicable;
- verifier, retry, escalation, failure, and frontier-call counts;
- offline/search/training and amortization cost;
- minimum meaningful improvement.

The patch fails if the speed/cost gain changes observable decisions beyond the registered equivalence tolerance, lowers verified success, hides retries, or violates a hard gate.

## 14. Security and privacy review

Record:

- threat-model delta;
- dependency/supply-chain impact;
- input validation and injection exposure;
- authorization/identity/tenant boundaries;
- secret handling and model-visible data;
- logging/redaction/retention changes;
- disclosure/rotation/remediation steps for a vulnerability;
- reviewer and decision.

## 15. Rollout

Define:

```text
OFFLINE_VALIDATION → SHADOW → CANARY → ELIGIBLE_COHORT → GENERAL
```

For low-risk internal/documentation-only patches, justify skipped stages. For runtime, model, policy, harness, migration, security, or physical-adjacent patches, specify:

- feature flag and default;
- exact canary cohort and duration;
- metrics and hard-stop thresholds;
- operator alerts;
- artifact/config freeze;
- compatibility window;
- release notes and client/operator communication.

## 16. Rollback and recovery

State:

- rollback trigger;
- responsible role;
- command/process and estimated recovery time;
- database/event/artifact compatibility during rollback;
- treatment of decisions/outcomes created under the patch;
- whether evidence must be quarantined;
- proof that the base release remains deployable and readable;
- physical lockout/stop behavior if applicable.

Rollback preserves audit history; it never erases the patch's decisions or incidents.

## 17. Acceptance checklist

- [ ] Patch classification passes Sections 2, 3, and 6.
- [ ] Base release and exact affected scope are pinned.
- [ ] Reproduction or before-benchmark is preserved.
- [ ] Root cause and minimal correction are documented.
- [ ] Public semantic, capability, authority, safety, and verifier deltas are `NONE`.
- [ ] Existing clients, events, schemas, history, replay, and fallback remain compatible.
- [ ] Regression and full owning-minor suites pass.
- [ ] Verified-success and critical-cohort gates pass.
- [ ] No critical correctness, policy, security, secret, isolation, evidence, or physical-safety regression occurs.
- [ ] Performance claim, if any, meets the frozen effect/equivalence gate.
- [ ] Migration/quarantine impact is resolved.
- [ ] Canary, rollback, observability, and operator procedures pass.
- [ ] Required engineering, research, security, safety, and human approvals are recorded.

## 18. Release decision

```yaml
decision: PASS | FAIL | INCONCLUSIVE | RECLASSIFY_MINOR | RECLASSIFY_MAJOR
decision_reason: string
approved_scope: string
known_limitations: [string]
rollback_ref: ArtifactRef
evidence_bundle_ref: EvidenceRef
approver_refs: [PrincipalRef]
decided_at: timestamp
```

`INCONCLUSIVE` does not become `PASS` through schedule pressure. `RECLASSIFY_MINOR` or `RECLASSIFY_MAJOR` returns the proposal to the research/change-control process and requires the appropriate full SDD.

## 19. Worked classification examples

### Example A — `1.2.1` harness-cache invalidation fix

A released v1.2 cache sometimes returns an older promoted `HarnessBundle` after an explicit portfolio update. The patch corrects cache-key composition and invalidation, adds a reproducing concurrency test, rebuilds only derived cache state, preserves bundle/selection contract semantics, and replays affected decisions. This is a patch because it restores the released v1.2 behavior.

### Example B — `1.2.2` faster portfolio lookup

An index reduces compatible harness lookup p95 without changing the eligible set, ordering, selected bundle, receipt, or fallback for the frozen replay corpus and critical cohorts. This may be a patch after equivalence, migration, and rollback gates pass.

### Example C — not `1.2.1`

Adding a new learned harness-repair action, a new auto-generated harness class, or a new supported runtime is a backward-compatible capability and needs a later `1.y.0` SDD. Changing approval, safety, verification, or event meaning may require `2.0.0` or may be prohibited entirely.
