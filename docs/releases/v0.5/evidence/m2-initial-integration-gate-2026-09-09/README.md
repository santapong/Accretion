# Initial combined runtime gate: retained failure

Candidate `6261ff2e04787213e2c1e0ea64433f39f8450924` passed its complete static
lane and fresh empty PostgreSQL upgrade/downgrade/upgrade cycle. The full backend
run failed: **4,650 passed, 20 failed, 9 skipped**. Its JSON record deliberately
retains `complete: false` and the actual failing exit. This is not a passing
integration gate.

[original-logs.zip](original-logs.zip) retains all original static/backend logs
and runner records, including the full failures. [files.json](files.json) pins
each member's original hash and length; every ZIP member was checked after
packaging. The task database and test identities were disposable construction
fixtures. No signed-in provider or simulation campaign ran.

Nineteen failures referenced fixture principals before creating those principals;
the repaired setup registers them first. The other compared PostgreSQL physical
column order after dropping and restoring a named column. Its repair compares
semantic columns, foreign keys, unique constraints, indexes and primary keys.
The actual Run ownership foreign key remains enforced. All 36 affected checks
passed after repair `764ba6d`; a new full-candidate gate is still required.

See the [execution checkpoint](../../execution-2026-09-09.md) for later source
changes and gate disposition. Failed evidence is never overwritten by a later
passing run.
