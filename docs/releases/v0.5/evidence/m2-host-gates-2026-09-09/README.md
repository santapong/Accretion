# Wave 2 host gate evidence

The [initial log bundle](initial-gates.zip) and [inventory](initial-files.json)
retain candidate `4fac4ad46b3cea3cfe3f90b71e2cecc7cbdafc27` without overwriting
its failures. Migration round trip, static checks and 286 frontend tests passed.
The backend finished with **5,078 passed, four failed and nine explicit skips**.

All four failures were PostgreSQL order regressions in the new corruption
restoration/lifespan test. A preceding provider test deliberately changed an
indexed orchestrator identity without restoring it; unfiltered reconciliation
correctly refused the mismatch against the sealed original. The production
integrity guard must remain intact. Commit `592356e95a61318651c9115e29e71e0b51680f92` repairs the fixture by
restoring the exact original in `finally`, including assertion failure. The
[repair bundle](suite-order-repair.zip) retains the reproduced failure, 38 passing
ordered checks, then eight PostgreSQL checks passing in a fresh process against
the same unchanged database. The final candidate subsequently passed the complete CI-order sequence below.

The startup-freshness and dispatch-deadline repairs and combined container probe
postdate this initial candidate. Earlier passes cannot be transferred to those
changes; final source and actual-host results must be recorded separately.


## Final integrated local gate

Candidate `45156481b22d2c9501fbae55950284c7b018d84d` passed every step of the
[recorded final sequence](final-gates.zip), with source unchanged and confirmed
exit zero. The [inventory](final-files.json) covers all original logs, result
record, exact runner and the three independent bounded reviews.

A single fresh disposable PostgreSQL database first passed the empty migration
upgrade/downgrade/upgrade cycle through 0024. **Full pytest, acceptance and the
inherited release gate then used that same database without replacement or
reset.** This verifies the repaired corruption fixtures in CI order.

- Full backend: **5,250 passed, 10 explicitly skipped**, in 387.87 seconds.
  Skips: six signed-in provider cases, one optional simulator development test,
  the separately executed actual-host opt-in test, and two inapplicable RESET
  signed-issuance-clock cases.
- Acceptance: **159 proven of 167 in scope; zero unmet MUST requirements**.
  All thirty composite AC5 criteria remain explicitly not yet due.
- All five inherited release conditions passed. These are engineering
  regressions; they do not declare a v0.5 release.
- Lock/frozen dependency sync, Ruff, mypy, documentation, all 42 generated
  schemas and all 187 frozen imported SDD files passed.
- The initial focused candidate's 286 frontend tests, checks, build and API
  idempotence remain recorded above. Later implementation changes affect only
  backend/tests; protected CI checks the submitted head's frontend and browser.

The [four-case actual host/lease witness](../../m2-host-lease-probe-2026-09-09.md)
ran separately on the same executable source closure before these report-only
commits. Both failed and successful actual attempts are retained. Final report
additions preserve that code closure. Protected develop integration still uses
its current required CI and exact reviewed-tree comparison.
