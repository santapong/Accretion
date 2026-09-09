# v0.5 execution checkpoint — 9 September 2026

Santapong approved [the completion plan](completion-plan-2026-09-09.md) and
instructed implementation. This is the active execution record; the dated
planning validation remains historical static evidence.

Base: freshly fetched `develop@01e2268b3b602a12eeaf81f55fb4670a1fe7636f`, plus
the reviewed planning commits through `5d589c53417c97b86f779ce0a86c4611913d5cdb`.
That starting checkpoint is historical. M0 was squash-merged through protected
[PR #182](https://github.com/santapong/Accretion/pull/182) as
`e9cd6503ba139770de61f627d8b2aa0cb063c094`. All eight checks passed on reviewed
head `31c538a065e423568c9cb86d57bef2dc87624871`; the fetched merge tree equals
that reviewed tree. The clean canonical `develop` checkout was fast-forwarded
to the merge. The released `main`/v0.4.1 line is unchanged.

| Milestone | Current disposition |
|---|---|
| M0 | Merged through protected PR #182; local and CI construction checks passed. See the [validation record](evidence/m0-construction-2026-09-09/validation.json) |
| M1 / pure M3 | Wave 1 combined local construction checks passed; protected PR review pending. Registry/persistence, SDK/fault protocol, safety evaluator and API/artifact integration are implemented |
| M2 / M5 / M6 | Isolated host, durable authority, pure verifier logic and real UR5e adapter construction active in separate worktrees; runtime acceptance pending |
| M4 / M7 / M8 | Pending the implementation wave dependency gates |
| M9 | Pending engineering completion and concrete prospective study/release gates |

Active worktrees: `/mnt/data/accretion-v05-execution-2026-09-09/{integration-wave1,host,authority,ur5e,verifiers}`.
M0 integration and worker worktrees are retained as historical source evidence.
One coordinator and three workers; no nested delegation. Source-only integration
uses plain Git. Simulator feasibility uses one bounded CPU process and no
physical endpoint or provider calls. Validation/results will be appended only
after observing their actual completion and tested candidate identity.

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
does not change frontend code or API types. Protected CI must check the actual
PR head before integration. Local frontend validation used Node 26.8.1.

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

All thirty composite AC5 criteria remain pending. Wave 2 implementation is
continuing under the approved plan; no registered campaign or release is claimed.
