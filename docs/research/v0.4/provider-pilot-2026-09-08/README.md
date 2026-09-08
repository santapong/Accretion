# Provider routing pilot preparation

Study ID: `ACR-ROUTER-DEV-20260908`. Status: **PREPARATION ONLY**.
Live execution: **NOT_AUTHORIZED**. Protocol: **DRAFT, NOT FROZEN**.

This new study asks whether a bounded digital routing comparison can collect
attributable independent outcomes and complete provider cost measurements.
It prepares a development pilot, not a confirmatory superiority claim. The
[v0.4 handoff](../../../releases/v0.4/research-handoff-2026-09-08.md) has already
recorded the scoped no-go on the previous full claim.

| Artifact | Purpose |
|---|---|
| [Protocol](protocol.md) | Question, data roles, candidate eligibility, verification, units, accounting and execution gates |
| [Readiness](readiness.json) | Machine-readable current gaps; unprovided owners, prices and spending limits stay null |
| [Fake instrumentation plan](instrumentation-plan.md) | Proposed disjoint implementation using BASELINE_ONLY and Provider.FAKE, with no selected AGENT tools |
| [Fake instrumentation manifest](fake-instrumentation-manifest.json) | Exact allowed mode and zero-provider boundary for the first implementation |
| [Trial record schema](trial-record.schema.json) | Draft export shape for FAKE_INSTRUMENTATION only; not a production contract |
| [Schema fixtures](fixtures/cases.json) | Explicitly illustrative records and rejection mutations; these are not measured run evidence |

User approval of the completion plan authorizes document preparation and bounded
fake/local instrumentation. Hosted providers, new scientific locked-data access,
promotion and physical execution remain outside that approval. The fake path
is designed here and awaits implementation/review; a schema check alone does
not establish that its runtime guard works.

Do not use the existing replay benchmark endpoint or `scripts/router_pilot.py`
as a live runner. No provider, account, model, price, monetary ceiling, scientific
sample size or independent human evaluator has been chosen by this package.
