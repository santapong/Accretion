# v0.5 execution checkpoint — 9 September 2026

Santapong approved [the completion plan](completion-plan-2026-09-09.md) and
instructed implementation. This is the active execution record; the dated
planning validation remains historical static evidence.

Base: freshly fetched `develop@01e2268b3b602a12eeaf81f55fb4670a1fe7636f`, plus
the reviewed planning commits through `5d589c53417c97b86f779ce0a86c4611913d5cdb`.
Canonical develop is preserved while isolated work proceeds.

| Milestone | Current disposition |
|---|---|
| M0 | Construction locally verified; protected review/CI pending. See the [validation record](evidence/m0-construction-2026-09-09/validation.json) |
| M1–M8 | Next: parallel registry/persistence, SDK/fault protocol and pure safety work after M0 integration; remaining dependency gates apply |
| M9 | Pending engineering completion and concrete prospective study/release gates |

Worktrees: `/mnt/data/accretion-v05-execution-2026-09-09/{integration,contracts,runtime,evidence}`.
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
The final protected PR must run required CI on its own head. Retained logs and
source identities are in the [construction validation bundle](evidence/m0-construction-2026-09-09/validation.json).

All thirty composite AC5 criteria remain explicitly pending. A full v0.5 release
probe correctly exits 1 without their evidence; an all-deferred M0 stage exits
2, NOT READY. These are intentional refusals. Construction tests and development
feasibility do not establish a working episode path, independent replay or a
registered scientific result.
