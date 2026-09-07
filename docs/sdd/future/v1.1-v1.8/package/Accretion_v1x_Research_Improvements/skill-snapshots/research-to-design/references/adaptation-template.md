# Evidence and adaptation record

Use the project's established schema when one exists. The fields below are a compact fallback, not a requirement to introduce another contract system.

## Source card

- Identity: title, authors, primary URL, exact version, access date.
- Reading: inspected sections/pages; evidence or code files; unread material.
- Mechanism: decision unit, inputs, controlled action, update rule and stopping condition.
- Evaluation: task/domain, model/runtime, data lineage and splits, sample and repeat counts, outcome/verifier, baselines, ablations and uncertainty.
- Cost: collection, proposal/search, rejected candidates, verification, training, serving and human work; record missing categories.
- Evidence: narrowly supported claims; author interpretation; reviewer inference; conflicts and unknowns.
- Reproduction: official artifacts, inspected commit/functions, missing inputs, reproduction status.
- Relevance: target owner and question this source helps resolve.

## Adaptation card

| Field | Required content |
|---|---|
| Existing baseline | File/section, document revision or commit; what is already covered |
| Gap | Observable missing behavior or unresolved research question |
| Evidence | Source version and precise supporting sections; counter-evidence |
| Keep | Source mechanism that is useful and project behavior to preserve |
| Change | Project-specific inputs, state, outputs and integration owner |
| Reject | Source assumptions or operations that do not transfer |
| Test | Hypothesis, unit, population, controls, independent outcome, cost and uncertainty |
| Decision rule | What qualifies success, falsifies the proposal, or requires abstention/deferment |
| Impact | Algorithm, contract, API/event, storage, security, verification, migration, UI or authority changes |
| Status | Proposal/clarification/deferred/rejected; required evidence and next owner |

For a source-derived design, “keep/change/reject” is often more informative than an estimated usefulness score. Avoid arbitrary numerical rankings that imply measurement.

## Bounded experiment plan

State the primary question and estimand first. Specify the target population and grouping unit; disjoint data roles; candidate freeze; observation availability; controls and ablations; full resource ledger; horizon and treatment of failures; statistical method; minimum meaningful effect; stopping and incident rules; reproducibility artifacts; and the permitted scope of a passing result.

Choose counts from inventory, variability, correlation and budget. Label illustrative counts and margins as planning assumptions. Do not convert a tiny feasibility pilot into evidence of rare-event safety or general superiority. A failed or inconclusive study is a valid decision output.

## SDD amendment proposal

Identify the current revision and exact target sections, affected owners and dependent documents. Name proposed gate changes and any ADR needed. Keep existing acceptance IDs intact until the project's change-control process assigns new ones. If schemas, fixtures and traceability have not been reconciled, label the output an amendment proposal rather than a validated replacement package.

## Final synthesis

Lead with the decision and why the evidence changes it. Compare releases/components in a compact matrix, then put detailed cards in the supporting report. Include rejected transfers and the smallest next evidence-producing action. Link actual outputs and primary sources.
