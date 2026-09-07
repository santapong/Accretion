# Accretion v1.x Semantic Versioning and Change Control

**Document revision:** 4  
**Supersedes document revision:** 3  
**Revised:** 2026-09-06

**Status:** Normative for the post-v1.0 product line  
**SemVer format:** `MAJOR.MINOR.PATCH`  
**Reference:** <https://semver.org/>

---

## 1. Direct answer

Version positions have the following compatibility meanings:

| Position | Meaning | Accretion example |
|---|---|---|
| `MAJOR` | Incompatible public contract, API, event, persistence, authority, or safety change | `1.x.x → 2.0.0` |
| `MINOR` | New backward-compatible product capability | `1.1.0 → 1.2.0` |
| `PATCH` | Backward-compatible defect, security, documentation, or implementation-level performance correction | `1.2.0 → 1.2.1` |

The first number does not mean merely “a large feature.” It marks a compatibility line. A substantial but backward-compatible feature can still be a minor release.

## 2. Accretion product version rules

### 2.1 Major release

Increment `MAJOR` when a released change requires an incompatible consumer migration or intentionally changes a public semantic contract. Examples include:

- removing or renaming a public API field without a compatibility layer;
- changing a persisted contract's meaning;
- changing event semantics so an existing consumer cannot safely process them;
- requiring a new incompatible plugin/runtime adapter contract;
- changing evidence, verification, identity, unit, or safety semantics;
- replacing the v1 authority model with an incompatible model.

An authority or safety change also requires explicit Golden Direction impact analysis. SemVer alone cannot authorize it.

### 2.2 Minor release

Increment `MINOR` for a backward-compatible capability, including:

- a new optional API or event;
- a new optimizer or execution mode;
- a new supported domain or capability profile;
- a new Studio surface;
- a new learned decision type;
- a backward-compatible contract extension with safe defaults;
- a materially new research claim that ships as product behavior.

Examples in this package are `1.1.0` Verified Compute Optimization and `1.2.0` Specialized Harness Portfolios.

### 2.3 Patch release

Increment `PATCH` only after the owning minor version has been released. Valid patch changes include:

- correcting a defect;
- correcting an evaluation or metric calculation;
- closing a security vulnerability without breaking the supported interface;
- improving latency, memory, CPU use, or query plans while preserving observable semantics;
- improving calibration with a drop-in policy artifact when the release contract explicitly allows artifact replacement;
- updating documentation to match already released behavior;
- adding non-semantic diagnostics or tests.

A performance change is a patch only when it preserves the same capability, contract, authority, safety behavior, and compatibility guarantees. A new optimizer, new action, new authority, or changed decision semantics is a minor or major release.

## 3. Concrete examples

| Proposed change | Correct version action | Reason |
|---|---|---|
| Fix p95 TTVS aggregation bug in v1.2 | `1.2.1` | Defect correction |
| Make harness lookup faster with the same output | `1.2.1` | Internal optimization |
| Add a new harness-portfolio router | `1.3.0` or owning new minor | New capability |
| Add an optional response field with a new supported semantic | Next `1.y.0` | Backward-compatible public capability |
| Add an explicitly permitted non-semantic diagnostic field | Patch candidate | Only when old clients safely ignore it and behavior is unchanged |
| Change `INCONCLUSIVE` to count as success | Prohibited | Violates permanent invariant |
| Rename a required API field and remove the old field | `2.0.0` | Breaking public contract |
| Add more design detail before v1.2 ships | SDD document revision | No released software changed |
| Retrain the v1.2 router on a new snapshot | New `PolicyArtifact` version | Product version changes only if code/contract/support policy changes |
| Publish a new specialized harness | New `HarnessBundle` artifact version | Artifact lifecycle, not automatically product SemVer |
| Fix a critical vulnerability with no compatible remediation | Major or exceptional breaking security release | Safety wins over compatibility |

## 4. Separate version namespaces

Accretion must not overload the product version for every changing artifact.

| Namespace | Example | Purpose |
|---|---|---|
| Product | `1.3.2` | Released Accretion software |
| API | `/api/v1` plus endpoint contract version | Public HTTP compatibility line |
| Contract schema | `ComputeProfile 1.1.0` | Typed data compatibility |
| Event schema | `compute.decision.recorded 1.0.0` | Consumer compatibility |
| Database migration | Monotonic migration ID | Persistence evolution |
| Policy/model artifact | Immutable ID + digest + training snapshot | Learned-policy lineage |
| Harness artifact | Harness ID + SemVer + digest | Harness compatibility and promotion |
| SDD document | `target_release: 1.2.0`, `document_revision: 3` | Design evolution before/after release |
| Research protocol | Protocol ID + revision + frozen hash | Scientific preregistration |

Updating one namespace does not automatically increment every other namespace.

Revision 4 keeps product targets v1.1.0–v1.8.0. The six changed research projection schemas move from prototype 1.0.0 to 2.0.0 because required fields or meaning change; new evidence projections begin at 1.0.0. These are unreleased design payload versions, not Accretion 2.0.0. Old immutable records remain readable through their original schemas. No upcaster may fabricate missing activation, data-role, replay, denominator or label-timing evidence.

## 5. SDD revision policy

Future research may add detail or invalidate an assumption before implementation. Use:

```yaml
target_release: 1.2.0
document_revision: 2
status: FORWARD_DESIGN_BASELINE
supersedes_document_revision: 1
change_reason: new research evidence
impact: no released product change
```

Use a product patch such as `1.2.1` only after `1.2.0` is released and the software itself changes.

Material SDD changes require:

1. Evidence source and evidence class;
2. Semantic diff;
3. Affected contracts and releases;
4. Authority, verification, security, safety, migration, and benchmark impact;
5. Updated risks and acceptance gates;
6. ADR when ownership or architecture changes;
7. Human approval before the revised SDD becomes normative.

## 6. Pre-release identifiers

Use pre-release versions for incomplete release candidates:

```text
1.2.0-alpha.1  internal contract/prototype validation
1.2.0-beta.1   feature-complete, evaluation incomplete
1.2.0-rc.1     release candidate with frozen behavior
1.2.0          released after all gates
```

Pre-release ordering does not bypass evidence gates. Do not describe an alpha, beta, or RC as stable.

## 7. Build metadata

Build metadata may identify a commit or build without changing precedence:

```text
1.2.0-rc.1+git.85e0e358
```

Never use build metadata as the only provenance record. The run must still pin the full commit digest, dependencies, policy artifacts, harnesses, environments, and verifiers.

## 8. Deprecation and compatibility window

- v1.x APIs retain the current and previous supported minor schema where feasible.
- Deprecation must be announced in metadata and Studio/API diagnostics before removal.
- Removal waits for a major release unless the behavior is unsafe.
- Unknown authority/safety enum values fail closed.
- Raw historical artifacts may preserve unknown bytes for audit; typed execution readers reject unknown fields until an explicit versioned adapter validates them. Optional fields do not bypass the registry's extra-forbid policy.
- Historical evidence remains readable through versioned projections/upcasters; it is never rewritten in place.

## 9. Patch release constraints

A patch release must not:

- add a new learned action class;
- expand an online-exploration cohort;
- add physical execution authority;
- weaken an acceptance or verifier threshold;
- change failure ownership;
- make a previously denied operation allowed;
- silently change the scientific estimand;
- reset historical evidence or policy lineage.

If a proposed “optimization” does any of those, it is not a patch.

## 10. Patch release evidence

Every patch requires, as applicable:

- reproducing test for the defect or performance problem;
- compatibility and migration tests;
- before/after benchmark with workload and environment pinned;
- correctness and critical-cohort non-regression;
- security review for security-sensitive changes;
- deterministic replay comparison;
- canary and rollback plan for runtime/policy changes;
- updated release notes and artifact digests.

## 11. Decision rule

Use the following order:

1. Did released public meaning or compatibility break? → `MAJOR`.
2. Was a new backward-compatible capability added? → `MINOR`.
3. Was released behavior corrected or internally optimized without semantic expansion? → `PATCH`.
4. Did only a forward SDD or research note change? → document revision, not product SemVer.
5. Did only a model, policy, dataset, or harness artifact change inside an allowed lifecycle? → artifact version; increment product only when the supported product behavior or contract changes.

## 12. Examples for the planned line

```text
1.1.0  ships Verified Compute Optimizer
1.1.1  fixes incorrect retry-time attribution
1.1.2  improves compatible portfolio lookup latency
1.2.0  ships Specialized Harness Portfolios
1.2.1  fixes harness cache invalidation
1.3.0  ships Verified Recovery Optimizer
```

This is the recommended interpretation of `v1.x.x` for Accretion.
