# v0.5 execution checkpoint — 9 September 2026

The authorized stopping point is **finish Wave 2, then park before Wave 3**.
The [approved plan](completion-plan-2026-09-09.md) separates the Wave 2
execution-boundary witnesses from a complete recorded episode, Studio,
independent robot qualification and the study/release program.

| Slice | Verified state |
|---|---|
| Wave 0 / M0 | [PR #182](https://github.com/santapong/Accretion/pull/182), merge `e9cd6503ba139770de61f627d8b2aa0cb063c094`; all eight CI checks passed |
| Wave 1 / M1 and pure M3 | [PR #183](https://github.com/santapong/Accretion/pull/183), merge `da7e806f7ab3a4eef0b6844b5777d9cb66370ccd`; all eight CI checks passed |
| Wave 2 / UR5e and verifier modules | [PR #184](https://github.com/santapong/Accretion/pull/184), merge `f380bf08fd9e45a44bc749723f21bafc7644c56d`; all eight CI checks passed |
| Wave 2 / host, gateway, leases and preflight foundation | Implementation and scoped checks complete; final combined host/lease probe and protected integration pending |
| Wave 3 onward | Parked; no complete episode, Studio, independent replay, task qualification or study/release claim |

Each fetched merge tree matched its reviewed PR head. Canonical `develop` was
clean and synchronized at `f380bf08fd9e45a44bc749723f21bafc7644c56d` after PR #184.
Released `main` and peeled v0.4.1 remain
`f60c7faae68a4ebae7ad4e15696746957690ce7b`.

The host candidate is prepared in
`/mnt/data/accretion-v05-execution-2026-09-09/wave2-host`. One coordinator and
three reused agents implement and independently review bounded work. The wider
`integration-wave2` branch retains early Panda/recorder construction and its
historical evidence; those files are excluded from this host slice. Episode API
and numerical-evidence drafts remain in separate parked worktrees.

## Wave 2 implementation and remaining exit

Additive migrations 0022–0024 provide atomic attributed Task/Run ownership,
fenced leases and reservations, scoped policy/conformance inventory and a
durable host creation journal. The generic coding Run manager refuses ownership
of simulation runs. Current human/service identity, exact approvals, signed
safety issuance, deadlines, state and budgets are checked transactionally.
Missing trusted collaborators fail closed. Uncertain dispatch never authorizes
resending an action.

The host uses bounded private authority/artifact Unix channels, single-flight
transport, immutable image/bootstrap identity, inspected Docker isolation and
bounded stop/remove cleanup. The journal commits before container creation;
a historical receipt cannot authorize a second launch. Restart can fence and
clean the exact persisted attempt, with the resource quarantined until authentic
cleanup proof is durably accepted. An observed cleanup proof retained across a
storage outage can be redelivered; absence after a process restart is insufficient.

The preflight builder accounts for ten named checks, exact plan bindings,
freshness and bounded hash-verified evidence. Missing or invalid proof produces
an explicit refusal. Its assessment always has `activation_eligible=False`.
Actual trusted production checkers, receipt commitment and full episode
composition remain future integration obligations. This Wave 2 foundation is
not an operational preflight PASS or a claim that all of M2 is complete.

The isolated non-robot [host probe](evidence/m2-host-construction-2026-09-09/README.md)
and [watchdog probe](evidence/m2-watchdog-construction-2026-09-09/README.md) retain
actual inspection, normal exit, wall expiry and cumulative descendant CPU
observations. All those containers were removed. Their component evidence alone
does not close the final combined persisted-authority/host witness, which remains
pending here. No simulator or physical endpoint is involved in that final probe.

PR #184's reviewed candidate `22ac4886e25afa261566da0f432ee339fe19cbdb`
passed 4,345 backend tests with seven explicit skips, a fresh-database migration
round trip, 126 focused adapter/verifier tests, Ruff/mypy, documentation and all
42 schemas. The inherited acceptance gate had zero unmet MUST requirements.
All eight protected/relevant CI checks passed and independent review found no
blocker. Its 29 historical evidence files match their recorded hashes/lengths.
The earlier real UR5e development run does not qualify changed adapter source.

The pure verifier cannot accept sampled traces as the stronger internal
actual-interval schema. A routine internal numerical-evidence correction is
parked for the episode integration wave; command-path checks and all original
acceptance requirements remain unchanged. Neither pure findings nor inherited
green gates close any of the thirty composite AC5 criteria.

## Integration repairs and retained evidence

Review repaired late success after cancellation/authority expiry, partial or
lost acknowledgements, socket-shutdown races and watchdog backpressure.
Memory transaction isolation, defensive copies and owner/lifetime checks prevent
rollback from erasing unrelated work and prevent escaped handles from operating.
Nested units of work against the same backing store now fail before mutation.

The [initial integration bundle](evidence/m2-initial-integration-gate-2026-09-09/README.md)
retains 20 failures on an earlier broader candidate: 19 fixtures inserted Runs
before their real referenced Principals; one compared physical column order
across drop/re-add. Repairs preserve the ownership foreign key and compare
semantic schema constraints; all 36 affected checks subsequently passed.

The [intermediate bundle](evidence/m2-intermediate-gates-2026-09-09/README.md)
retains the wider candidate's 4,948 backend passes, ten explicit skips, static
checks and 286 frontend passes. It also preserves initial frontend setup and
stale-database failures. These historical counts include parked construction
components and are not transferred to the final host slice.

A separate CI-sequence review found two authority corruption tests leaving
modified rows behind. They now restore exact originals in `finally`, including
after assertion failure. Twelve focused tests passed, then six PostgreSQL cases
passed in another process against the same unchanged database, including real
application startup and unfiltered reconciliation. CI and production integrity
checks remain intact. Migration round trips use a fresh empty database; populated
historical databases are never downgraded.

## M0 construction evidence

The integrated foundation contains the active SDD with all thirty stable AC5
IDs, twelve scoped decisions, API/state contract, twenty-one canonical robotics
records, schema exports and eighty-four synthetic fixtures. Original-writer
envelopes preserve provenance; typed preconditions reject ambiguous retries;
detached Ed25519 safety signatures bind the evaluator to the exact decision.
Simulation dependencies remain optional. No episode service or simulation API
is activated by this milestone.

The approved CPU MuJoCo profile passed [bounded feasibility](feasibility-2026-09-09.md)
for UR5e/Robotiq and Panda. Package, model, scene/controller and observed render
identities are recorded there. Enforced worker-image isolation and real adapter
conformance remain M2/M6/M7 obligations.

On `9549a466fa3ed2349d8cba392364225372e76fde`, local validation confirmed
3,871 backend tests passing, six intentional signed-in-provider skips, 286 UI
tests passing, frontend checks/build/OpenAPI idempotence, the PostgreSQL
downgrade/upgrade cycle and all five inherited release conditions. The initial
backend runner stopped without recording its final pytest exit; the unrecorded
step was rerun successfully and both records are retained. Local frontend
validation used Node 26.8.1; the protected CI uses Node 24.

Independent review then found and fixed two negative cases: future-minor event
projection could replace an invalid original payload pin, and the explicit v0.5
release command could overlook an unmarked failing test. The repaired candidate
`77d99396e6bbfc8fd9b32fbc2e2204cfafbcf995` passed 548 relevant tests, full Ruff
and mypy, and all 42 schema-export checks. The fixtures reproduce byte for byte.
The final protected PR passed its required CI on its own head. Retained local logs and
source identities are in the [construction validation bundle](evidence/m0-construction-2026-09-09/validation.json).

All thirty composite AC5 criteria remain explicitly pending. A full v0.5 release
probe correctly exits 1 without their evidence; an all-deferred M0 stage exits
2, NOT READY. These are intentional refusals. Construction tests and development
feasibility do not establish a working episode path, independent replay or a
registered scientific result.

## Wave 1 construction evidence

Candidate `ea9d0f6c485c63705d24cf5e263035014e3962d1` passed **4,219 backend
tests**, with six intentionally skipped signed-in-provider cases, on a fresh
disposable PostgreSQL database. The migration upgrade/downgrade/upgrade cycle,
Ruff, mypy, lock and schema checks passed. The inherited acceptance harness and
all five inherited release conditions passed. The latter remain inherited
engineering regression checks, not a v0.5 release decision.

The immediately preceding candidate `2fab581d6965e201794951f7cba96283680e6585`
passed all **286 frontend tests**, checks, production build and generated API
idempotence. The final delta fixes registry lineage and adds backend tests; it
does not change frontend code or API types. Protected CI subsequently checked
the actual PR head before integration. Local frontend validation used Node 26.8.1.

Review repaired four substantive boundaries: duplicate artifact publication at
full quota, directory durability on reuse after an interrupted publish, contact
force aggregation by unordered body pair, and immutable scoped registry
predecessor/conformance lineage. The protocol also refuses an OBSERVE response
that advances state. All were included in the combined backend validation.

The first migration round trip used an earlier populated development database.
Its downgrade was correctly refused by inherited migration 0004 because multiple
loop executions cannot fit the older schema. The transaction rolled back;
historical rows and the guard were preserved. The successful round trip used a
new empty database. Both attempts, exact commands, source identities, confirmed
exits and raw log hashes are retained in the
[construction evidence bundle](evidence/m1-construction-2026-09-09/README.md).

All thirty composite AC5 criteria remain pending. Wave 2 host validation is
continuing within the approved stopping point; no registered campaign or release is claimed.
