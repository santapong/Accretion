# Accretion v1.x — Revision 2 validation report

**Document revision:** 2  
**Date:** 2026-09-05  
**Result:** PASS for document design conformance  
**Implementation acceptance:** Pending  
**Research acceptance:** Not evaluated

## 1. Delivered changes

The eight review findings are addressed in their owning documents and indexed in `14_REVISION_2_TECHNICAL_ADDENDUM.md`. Priority changes cover the shared fixed-horizon evaluation protocol and v1.1 complete configuration assembly, immutable dispatch binding, final compatibility check and existing routing authority. Later releases reconcile abstention, harness promotion lifecycle, inconclusive recovery, optional-lab dependency closure, physical freeze/signature binding, stage-specific outcomes and event names.

Document revision 2 does not change any product release number. The v0.4–v1.0 SDDs, repository code and existing implementation plan were not modified.

## 2. Executed validation

Command: `python3 19_validate_revision2.py` with the declared JSON Schema validator dependency available.

| Check | Result | Scope |
|---|---|---|
| JSON Schema definitions | 13 valid | New extension payloads and normalized projections |
| Contract cases | 53 passed | Valid and intentionally invalid payloads, digests, references, assembly, lifecycle prerequisites and event subjects |
| Lifecycle cases | 25 passed | Commit-before-dispatch, deduplication, stale epoch, expiry, uncertain invocation, conflict pause, human/independent resolution and serialized arm/invalidation examples |
| Metric cases | 8 passed | PASS, early FAIL, INCONCLUSIVE, timeout, late PASS, missing time, quarantine and denial |
| Early-abandonment regression | Passed | Replacing a PASS with a quick FAIL cannot improve capped TTVS |
| Parallel-span example | Passed | Workflow elapsed time remains distinct from summed compute work |
| Restricted canonical JSON golden vector | Passed | Synthetic ASCII/integer domain; full predecessor vectors remain an integration gate |
| Event catalog parity | 90 event names checked | Exact equality with all eight release §12 owner slices |
| Acceptance traceability | 137 criteria checked | Exact source text, unique stable IDs, valid milestone owners, proposed evidence targets and valid fixture links |

All 86 fixture cases passed their expected outcome. An invalid fixture passes its test by being rejected with the specified error. A valid incident record can describe an unsafe observation; the recorded physical reexecution fixture correctly requires hard-gate FAIL. Schema validity never implies release acceptance.

## 3. Interpretation and limits

These tests execute the design's payload validators and small lifecycle specifications. They do not run Accretion source code, evaluate an ML model, verify production signatures, test tenant isolation, establish distributed atomicity, prove safety, consume real approvals or invoke hardware.

Every acceptance row retains IMPLEMENTATION_PENDING or RESEARCH_PROTOCOL_PENDING. Proposed evidence paths are obligations for the future implementation, not reports created by these tests. The highest-priority remaining integration gates are full predecessor canonicalization/typed-reference conformance, generated runtime schema adapters, transactional dispatch and uncertain invocation reconciliation, and the v0.6 manifest/preflight/approval/arming boundary. Concrete numerical research parameters must be frozen by the research and independent evaluation owners before a study starts. Fixture values are synthetic.

## 4. Package integrity

`MANIFEST.sha256` covers every deliverable except itself. The ZIP contains the package files under `Accretion_v1x_Technical_SDD_Package/`; the ZIP is not recursively included in its own manifest. The delivery workflow verifies all manifest digests and archive member bytes after packaging. Revision-1 files are retained through document history.
