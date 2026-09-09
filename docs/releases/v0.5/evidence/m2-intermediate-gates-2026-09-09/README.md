# Wave 2 intermediate gate evidence — 9 September 2026

These are actual intermediate runs on candidate
`1f4c6715f90104d71f17619e5a3af54027cfea4e`, before the final host timing,
transaction lifetime and durable launch changes. They do not qualify a later
candidate or close a composite AC5 criterion.

| Gate | Observed result |
|---|---|
| Backend, new empty `accretion_v05_wave2_gate2` database | Migration upgrade/downgrade/upgrade passed; 4,948 tests passed, 10 explicitly skipped, exit 0 |
| Static checks | Lock, Ruff, mypy, documentation and 42 schema checks passed |
| Frontend initial attempt | Failed: worktree dependencies were absent and a system ESLint 6.4 executable was selected |
| Frontend after locked `npm ci` | Check, 286 tests, build and generated-schema idempotence passed, exit 0 |
| Inherited acceptance attempt on old database | 54 failed, 4,894 passed, 10 skipped; exit 1. The subsequent release step did not run |

The acceptance runner mistakenly reused `accretion_v05_wave2_gate`. That older
database lacked migration 0023's authority tables and retained deliberately
corrupted binding fixtures from an earlier suite. Missing-table failures and
fail-closed app startup required separating two causes. Missing migration 0023
was an environment setup error. Leaving deliberately corrupted binding fixtures
also exposed a new test-isolation defect: CI runs several suites against the same
database. The negative tests now restore exact original columns in `finally`,
including after assertion failure. Production corruption refusal is unchanged.

The repair passed 12 focused Memory/PostgreSQL checks, then six PostgreSQL checks
in a second process against the same unchanged database. Actual unfiltered
reconciliation and fresh application startup succeeded after each corruption
case. The failed full attempt remains retained. Fresh isolated databases are
used for independent local gates; same-database repeat regression coverage and
protected CI verify that test cleanup is correct.

The confirmed backend run and frontend retry report unchanged source at their
completion. Final integration still requires its own checks and the joint
container/lease witness. All 30 v0.5 composite criteria remain pending.

[Hash inventory](files.json) covers each member of the
[original log archive](raw-gates.zip), including both failed attempts.
