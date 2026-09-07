# Accretion v1.x Revision-4 validation report

**Document revision:** 4  
**Validated:** 2026-09-06 09:46 UTC  
**Result:** PASS — DOCUMENT_DESIGN_CONFORMANCE_ONLY  
**Environment:** Python 3.14.6, jsonschema 4.23.0, isolated validation environment

## 1. Executed checks

| Check | Result |
|---|---|
| Current schema structure | 24 targeted projection/payload schemas passed |
| Current synthetic fixtures | 183 passed: 139 contract cases, 36 lifecycle cases, 8 metric cases |
| Event ownership and owner-slice parity | 101 event names passed |
| Acceptance-text and milestone traceability | 174 rows passed; all implementation/research evidence remains pending |
| Full release document structure | All 8 SDDs retain sections 1–23 and document revision 4 |
| Local Markdown links | 16 current root-document links resolved |
| Historical Revision 3 validator | PASS: 19 schemas, 100 fixtures, 96 events, 153 acceptance rows |
| Exact calibration-rank check | PASS at n=6, 15, 19 and 99; mathematical scope only |
| Markdown structure | Code fences and all eight open-question tables checked |

The current validator is `29_validate_revision4.py`; exact output and dependency versions are in `31_REVISION_4_VALIDATION_RESULT.json`. The historical validator was run against the unchanged `history/revision-3/` files under the same declared jsonschema version. Earlier validation reports remain explicitly historical.

## 2. Important rejection cases

The suite rejects selection/calibration lineage overlap; final feedback into search; final evaluation before selection or required calibration; reuse of a final campaign; unsupported uncertainty or label-process claims; clamped small-sample ranks; unobserved effective settings; incomplete profile batches and omitted uniform-arm cost; missing frozen candidates; fabricated verifier bounds; predicted-only causal evidence or absent repeated no-op comparison; stale state consumption; missing study controls or costs; reused behavior-confirmation cases; mismatched rollout/training pairs; source-bound content leakage; and workflow simulation presented as physical evidence.

Inherited tests continue to cover dispatch hashes/epochs and idempotency, inconclusive recovery handling, immutable harness lifecycle, truthful physical outcomes, and exact approval/arming order. Negative fixtures pass by producing their expected rejection; they are not incidents observed in a running application.

## 3. Interpretation limits

These are executable design specifications using synthetic artifacts and normalized lifecycle models. Validating a locator or assumption-reference field does not prove the referenced evidence exists, is independent or supports the claim. The implementation must resolve full owner schemas, identities, scopes, signatures, provenance and data-role bindings. Synthetic state ordering is not proof of distributed atomicity. The canonical hash examples do not replace predecessor production canonicalization vectors.

The fixed-sample verifier-bound check does not validate clustered/adaptive qualification. The calibration-rank check is arithmetic under its stated assumptions, not a reproduction of SCAPE or an Accretion benchmark. Passing these checks establishes neither performance gain nor safety, scientific validity, runtime readiness or release readiness.

No application code was changed and no application test suite, pilot, paid model call, training job or physical experiment was run. The concurrent application update from `2d2f81c` to `fa8d865` is recorded as a read-only integration checkpoint. Independent methods, architecture and security review remains pending; no reviewer approval is fabricated.

## 4. Package and handoff

The original archive's 29 files are preserved under `history/revision-3/`, including all 28 payloads covered by its original manifest. `33_REVISION_4_CHANGE_MAP.json` records the changed sections, schema versions, event additions and acceptance IDs. The new package manifest and final ZIP integrity are checked during packaging; hashes identify the delivered bytes, not the correctness of the research.

The document revision is complete. All eight releases retain their predecessor and release gates. v1.1 remains `NOT_READY_TO_EXECUTE` pending its actual data, profile, verifier, numerical, budget and owner inputs. Optional later studies retain declared applicability and no-result/deferred outcomes. The next review concerns this concrete amended package.
