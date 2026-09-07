# Accretion v1.x Technical SDD Package — Revision 4

**Document revision:** 4  
**Supersedes document revision:** 3  
**Revised:** 2026-09-06  
**Product targets:** v1.1.0–v1.8.0, unchanged  
**Status:** Complete amended forward design; implementation locked; independent design review pending

Revision 4 integrates the 2026-09-06 alphaXiv research into all eight owning SDDs and the shared contracts, research protocol, milestones, tests and acceptance traceability. It strengthens evaluation validity before increasing optimizer complexity. All empirical Accretion benefits remain unproven.

## 1. What changed

| Release | Integrated design improvement |
|---|---|
| v1.1 | Requested/effective profile observations, cache/order controls and complete three-profile per-arm budgets |
| v1.2 | Mature general-harness baseline, optimizer/target identity separation and install/load/trigger evidence |
| v1.3 | Separate localization, observed intervention and recovery value; repeated no-op/full-retry controls |
| v1.4 | Delayed/cross-session poisoning and serialized dependency-revocation/consumption checks |
| v1.5 | Frozen-candidate acquisition first; separate behavior-linked repair/preservation/boundary screening later |
| v1.6 | Optional four-arm harness/adapter study with development-only selection and total-budget accounting |
| v1.7 | Context-only transfer first, immutable source/target content and explicit label-arrival handling |
| v1.8 | Deterministic workflow comparator; separate scenario-calibration protocol with bounded evidence scope |

Shared changes separate development selection, statistical calibration and final evaluation; name the uncertainty target; handle small calibration samples conservatively; and prevent a passed drift diagnostic from being presented as proof of a statistical guarantee.

## 2. Read in this order

1. [Revision-4 integration decisions and ADRs](28_REVISION_4_INTEGRATION_AND_ADRS.md).
2. [Shared research/evaluation protocol](03_SHARED_RESEARCH_EVALUATION_PROTOCOL.md).
3. [Cross-release ownership registry](02_CROSS_RELEASE_CONTRACT_REGISTRY_v1.x.md).
4. [Research program and stage gates](23_V1X_RESEARCH_PROGRAM_MATRIX.md).
5. [v1.1 readiness and first-study protocol](24_V1_1_PILOT_READINESS_AND_PROTOCOL.md).
6. The relevant full release SDD below.
7. [Source review and limitations](25_RESEARCH_SOURCE_REGISTER.md) and [current validation report](30_REVISION_4_VALIDATION_REPORT.md).

| Full SDD | Full SDD |
|---|---|
| [v1.1 Verified Compute Optimizer](04_Accretion_SDD_v1.1.0.md) | [v1.5 Retrospective Harness Optimization](08_Accretion_SDD_v1.5.0.md) |
| [v1.2 Specialized Harness Portfolios](05_Accretion_SDD_v1.2.0.md) | [v1.6 Open-Weight Adaptation Lab](09_Accretion_SDD_v1.6.0.md) |
| [v1.3 Verified Recovery Optimizer](06_Accretion_SDD_v1.3.0.md) | [v1.7 Cross-Domain Efficiency Transfer](10_Accretion_SDD_v1.7.0.md) |
| [v1.4 Runtime State Coordination](07_Accretion_SDD_v1.4.0.md) | [v1.8 Governed Physical Efficiency Advisory](11_Accretion_SDD_v1.8.0.md) |

The unchanged predecessor Golden Direction, v0.4–v1.0 registry and v1.0 SDD remain higher authority. This package does not unlock or rewrite those releases. The original Revision 3 package is retained byte-for-byte under `history/revision-3/` with its original manifest.

## 3. Executable design validation

Use Python 3.11 or later in an isolated environment:

```bash
python3 -m venv .validation-venv
.validation-venv/bin/python -m pip install -r 20_validation_requirements.txt
.validation-venv/bin/python 29_validate_revision4.py
```

The current validator checks strict schemas, cross-field constraints, synthetic positive/negative cases, normalized lifecycle ordering, event ownership, exact acceptance-text coverage and local links. It makes no model/provider/robot calls. The inherited revision-2 and revision-3 validators and reports describe historical scope; run the old validator inside `history/revision-3/` to reproduce that baseline. Use `29_validate_revision4.py` for the current files.

Machine-readable artifacts are `15_CONTRACT_KIT.json`, `16_EVENT_CATALOG.json`, `17_CONTRACT_LIFECYCLE_FIXTURES.json` and `18_ACCEPTANCE_TRACEABILITY.json`. [The field guide](32_REVISION_4_CONTRACT_REFERENCE.md) explains the changed projections. Payload fixtures are targeted design specifications, not replicas of every production wire schema or implementation tests.

## 4. Status and next gate

The initial read-only snapshot was clean `develop@2d2f81c91fe279b245f0b16785c7bff36c07b97e`. During integration, M10b (#150) landed; the final bounded recheck observed clean `develop@fa8d865453e2c84d2c6733c985bf4d3f6b397b64`, equal to its locally recorded origin reference, on 2026-09-06. No fetch or release audit was performed. The new development-pilot statistics and replay-only benchmark route are useful future inventory inputs, not v1.1 evidence or a frozen study. Actual runtime state may change independently of this package.

The v1.1 readiness inventory is the next useful research work product. Its study remains `NOT_READY_TO_EXECUTE` until predecessor, population, profile, verifier, owner, numerical and budget inputs are resolved. The fixed-profile pilot does not establish conditional routing. Later releases retain their entry gates. The v1.6 optional-lab negative/deferred closure exception remains intact; it promotes no adapter and waives no operational gate.

No implementation or empirical criterion is checked off by this update. Document validation, independent design review, runtime acceptance and research success are separate statuses. This document amendment creates no new execution, publication, deployment, training or physical authority.

## 5. Supporting material and provenance

The source register records selective primary reading of 12 papers from the latest review, exact versions, inspected sections, limitations and conflicts. Original inspirations retain their earlier reading status. No paper was independently reproduced in Accretion.

`research/` includes the focused first-paper outline, source metadata and a small exact calibration-rank calculation. Those are supporting research notes; the revised numbered SDDs, protocol and contracts are the current design. Numerical examples are arithmetic illustrations, not powered study defaults. `MANIFEST.sha256` covers all distributed payload files except itself.
