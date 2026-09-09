# v0.5 — Robotics Simulation and Embodiment Foundation

Status: **M0 merged through PR #182; wave 1 implementation active**
(9 September 2026).

Start with the [multi-agent completion plan](completion-plan-2026-09-09.md).
It maps the ten SDD implementation steps and all thirty acceptance criteria into
bounded parallel work with a coordinator and at most three workers.

| Document | Purpose |
|---|---|
| [Completion plan](completion-plan-2026-09-09.md) | Product outcome, recommended simulator profile, M0 decisions, milestones, worktrees, UI, evaluation and release gates |
| [Execution checkpoint](execution-2026-09-09.md) | Current implementation disposition and observed validation |
| [M0 decisions](m0-decisions-2026-09-09.md) | Adopted contract, authority, simulation and evidence directions |
| [M0 API contract](m0-api-contract.md) | Scoped operations, idempotency, concurrency, state and Studio integration |
| [M1 foundation boundaries](m1-foundations.md) | Scoped registry API, explicit local project binding, bounded artifacts and pending host authority |
| [M1 persistence boundary](m1-registry-2026-09-09.md) | Additive migration, canonical reference resolution, transactional events and trusted conformance joins |
| [Canonical contract inventory](../../contracts/v0.5/README.md) | Twenty-one simulation records, writer provenance and synthetic fixtures |
| [M0 construction validation](evidence/m0-construction-2026-09-09/validation.json) | Tested candidates, confirmed local exits and review-repair evidence |
| [M0 simulator feasibility](feasibility-2026-09-09.md) | Executed CPU-only UR5e/gripper and Panda reset, stepping, observations and contact evidence |
| [Active SDD](../../sdd/Accretion_SDD_v0.5.md) | Active requirements with stable acceptance IDs and separate evidence obligations |
| [Prospective protocol draft](../../research/v0.5/protocol-draft.md) | Study design decisions and implemented composite-evidence format; not a registered study |
| [Contract audit](planning/contract-audit-2026-09-09.md) | Entry evidence, canonical contract conflicts and authority decisions |
| [Runtime audit](planning/runtime-audit-2026-09-09.md) | Existing code, missing simulation components, adapter choices and fault witnesses |
| [Acceptance audit](planning/acceptance-audit-2026-09-09.md) | Every source criterion, harness changes and research/release completion evidence |
| [Baseline checkpoint](planning/baseline-checkpoint-2026-09-09.json) | Inspected commit, source hashes, scientific access-log count and planning scope |
| [Planning validation](planning/plan-validation-2026-09-09.json) | Static link, criterion-mapping and preservation checks; not implementation or research execution |

The [forward SDD](../../sdd/future/v0.4-v1.0/02_SDDS/Accretion_SDD_v0.5.md)
remains preserved. The active specification and M0 contract overlay are now
implemented. The [v0.4 handoff](../v0.4/research-handoff-2026-09-08.md)
records inherited technical entry evidence; it does not establish a v0.5
simulation result.

The adopted profile uses MuJoCo, UR5e with a Robotiq gripper, and an independent
Panda adapter under the M0 ADR. v0.5 is simulation-only. Engineering
readiness, research qualification and a tagged release remain distinct gates.
