# Project baseline and integration bridge

## Evidence identities

- Supplied archive: `/home/santapong/Downloads/Accretion_v1x_Technical_SDD_Package (1).zip`.
- Archive SHA-256: `14a41b5ba6748658f82930dd7f91e6a90c6d1cc17dfe2187ed1c9b71bf9dd883`.
- Extracted baseline: 29 files, all eight release SDDs marked document revision 3; manifest has 28 payload entries.
- Initial repository inspection: `develop@6fdd488`, clean and equal to the locally recorded `origin/develop`, on 2026-09-06. No fetch or release-readiness audit was performed.
- Latest read Claude memory section: #148 merged; subsequent M9c/M7/M10 work in progress. Those changing workstreams remain outside this review.
- Packaging recheck: clean `develop@2d2f81c91fe279b245f0b16785c7bff36c07b97e`. M9c (#149) landed during the review. Its UI, documentation and test changes do not alter the backend owners inspected below. This was a bounded difference check, not a fresh runtime or release audit.

The package's original repository snapshot is historical. Its `DOCUMENT_DESIGN_CONFORMANCE_ONLY` PASS does not prove runtime integration, research success or release readiness. This report does not promote its prototype contracts to shipped implementation.

## Reuse these owners

| Research requirement | Existing evidence/owner inspected | Integration implication |
|---|---|---|
| Independent outcomes and contradictions | `src/accretion/feedback/verification.py`, `IndependentVerificationRecorder` | Add qualification evidence around the existing verdict process |
| Eligible reusable experience | `src/accretion/feedback/evidence.py`, `StoreEvidenceRetriever.retrieve` | Reuse visibility/compatibility/contradiction filtering; do not create a competing memory store |
| Lineage grouping and data roles | `src/accretion/routing/split.py`, `SplitName`, `assign`, `assert_disjoint` | Reuse structural lineage separation; verify actual persisted access and temporal enforcement separately |
| Controlled candidate evaluation | `src/accretion/routing/rollout.py`, `_run_arm` | Existing fresh execution arms are a starting point, not proof of exact snapshot counterfactual replay |
| Promotion and rollback | `src/accretion/routing/promotion.py` | Proposed research artifacts remain subject to existing activation and rollback owners |
| Generated-code containment | `src/accretion/verifiers/process.py`, `run_bounded_process` | A trusted subprocess time/output bound is not an outer security sandbox |
| Capability proposals with frozen verification | `docs/sdd/future/v0.4-v1.0/02_SDDS/Accretion_SDD_v0.10.md` | Harness implementation proposals stay with v0.10; changing correctness semantics is a separate design decision |

At the inspected commit, `routing/split.py` explicitly describes pure lineage assignment and says persisted read-time enforcement and real temporal/provider-era separation are later work. A partition named DRIFT is not itself proof of chronological evaluation. Recheck that statement after the active M10 work lands; do not turn this dated observation into a permanent defect claim.

## What a future Revision 4 integration must do

This package contains proposed prose and study design. It does not yet amend the original release SDDs, assign new acceptance IDs, revise the JSON schemas or run a new design-package validator. Integrating it requires:

1. Review each proposed delta against current project state and retain only useful, non-duplicative changes.
2. Resolve shared selection/calibration semantics and any needed ADRs.
3. Update affected scope, algorithms, contracts, failure handling, tests, benchmark, milestones, gates and handoffs together.
4. Reconcile documents 01–03, 13, 15–18 and research amendments; preserve historical reports as historical evidence.
5. Validate references, schemas, positive/negative fixtures, traceability and hashes, then issue a new report whose scope is explicit.

User review can approve a concrete design revision. Implementation, experiment execution and release actions remain governed by their actual authorized scopes and prerequisites. No application repository changes were made by this research task.

## Relation to earlier research

The earlier five-idea comparison and post-v1.8 adaptation brief preceded inspection of this archive. They remain historical exploratory work. This package takes priority for the present v1.1–v1.8 research question; it does not claim that the prior post-v1.8 ranking has been comprehensively reconciled with every future design.
