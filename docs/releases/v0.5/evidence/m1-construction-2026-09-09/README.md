# Wave 1 construction validation

This bundle records observed engineering checks under the
[execution checkpoint](../../execution-2026-09-09.md). It does not complete a
composite AC5 criterion, activate an adapter or establish a scientific result.
The [manifest](manifest.json) records exact byte lengths and SHA-256 hashes of
all 31 original JSON records and logs. Each lane record links its own raw logs
and retains commands, timestamps, confirmed exit codes and source identity.

| Candidate | Observed validation |
|---|---|
| `ea9d0f6c485c63705d24cf5e263035014e3962d1` | [Backend](backend-ea9d0f6c.json): 4,219 passed, six intentional provider skips; fresh PostgreSQL upgrade → downgrade → upgrade |
| Same final source candidate | [Static](static-ea9d0f6c.json): lock, Ruff, mypy, docs and all 42 schema exports passed |
| Same final source candidate | [Inherited release](release-ea9d0f6c.json): acceptance harness and five inherited release conditions passed |
| `2fab581d6965e201794951f7cba96283680e6585` | [Frontend](frontend-2fab581d.json): 286 tests, checks, production build and API regeneration idempotence passed |
| Same preceding candidate | [Static](static-2fab581d.json): all checks passed |
| Same preceding candidate | [Retained migration refusal](backend-2fab581d.json): downgrade stopped before pytest on populated development history |

The final delta from `2fab581d` to `ea9d0f6c` is the scoped registry lineage
repair and its backend tests. Frontend code and API types are unchanged by that
delta. CI must validate the final protected PR head, including the evidence-only
documentation commit added after these checks. A lane marked `complete` means
the runner finished, not that every command succeeded; inspect each exit code.

The first downgrade correctly hit migration 0004's refusal to collapse multiple
loop executions into an older one-execution schema. It rolled back at head 0021.
The successful rerun used the fresh task-owned `accretion_v05_wave1_gate`
database; no historical rows were deleted and the guard was not weakened.
The test database uses disposable local credentials and contains no provider
secrets or production records.

The inherited release command does not establish v0.5 readiness. All thirty
composite AC5 criteria, real host/adapters, independent replay, Studio journeys
and prospective registered study evidence remain governed by the
[completion plan](../../completion-plan-2026-09-09.md).
