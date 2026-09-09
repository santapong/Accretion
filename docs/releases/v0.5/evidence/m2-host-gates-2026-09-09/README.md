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
the same unchanged database. The final full candidate gate remains required.

The startup-freshness and dispatch-deadline repairs and combined container probe
postdate this initial candidate. Earlier passes cannot be transferred to those
changes; final source and actual-host results must be recorded separately.
