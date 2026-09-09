# v0.5 — Robotics Simulation and Embodiment Foundation

Status: **plan approved; M0 implementation in progress** (9 September 2026).

Start with the [multi-agent completion plan](completion-plan-2026-09-09.md).
It maps the ten SDD implementation steps and all thirty acceptance criteria into
bounded parallel work with a coordinator and at most three workers.

| Document | Purpose |
|---|---|
| [Completion plan](completion-plan-2026-09-09.md) | Product outcome, recommended simulator profile, M0 decisions, milestones, worktrees, UI, evaluation and release gates |
| [Execution checkpoint](execution-2026-09-09.md) | Current implementation disposition and observed validation |
| [M0 decisions](m0-decisions-2026-09-09.md) | Adopted contract, authority, simulation and evidence directions |
| [M0 API contract](m0-api-contract.md) | Scoped operations, idempotency, concurrency, state and Studio integration |
| [M0 simulator feasibility](feasibility-2026-09-09.md) | Executed CPU-only UR5e/gripper and Panda reset, stepping, observations and contact evidence |
| [Active SDD](../../sdd/Accretion_SDD_v0.5.md) | Active requirements with stable acceptance IDs and separate evidence obligations |
| [Prospective protocol draft](../../research/v0.5/protocol-draft.md) | Study design decisions and implemented composite-evidence format; not a registered study |
| [Contract audit](planning/contract-audit-2026-09-09.md) | Entry evidence, canonical contract conflicts and authority decisions |
| [Runtime audit](planning/runtime-audit-2026-09-09.md) | Existing code, missing simulation components, adapter choices and fault witnesses |
| [Acceptance audit](planning/acceptance-audit-2026-09-09.md) | Every source criterion, harness changes and research/release completion evidence |
| [Baseline checkpoint](planning/baseline-checkpoint-2026-09-09.json) | Inspected commit, source hashes, scientific access-log count and planning scope |
| [Planning validation](planning/plan-validation-2026-09-09.json) | Static link, criterion-mapping and preservation checks; not implementation or research execution |

The [forward SDD](../../sdd/future/v0.4-v1.0/02_SDDS/Accretion_SDD_v0.5.md)
remains preserved. Active specification and registry reconciliation proceed
through the approved M0 work. The [v0.4 handoff](../v0.4/research-handoff-2026-09-08.md)
records inherited technical entry evidence; it does not establish a v0.5
simulation result.

The recommended plan uses MuJoCo, UR5e with a Robotiq gripper, and an independent
Panda adapter, subject to the explicit M0 ADR. v0.5 is simulation-only. Engineering
readiness, research qualification and a tagged release remain distinct gates.
