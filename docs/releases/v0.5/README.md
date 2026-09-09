# v0.5 — Robotics Simulation and Embodiment Foundation

Status: **Wave 2 construction witnesses verified; Wave 3 parked**
(9 September 2026).

Start with the [multi-agent completion plan](completion-plan-2026-09-09.md).
It maps the ten SDD implementation steps and all thirty acceptance criteria into
bounded parallel work with a coordinator and at most three workers.

| Document | Purpose |
|---|---|
| [Wave 2 adapter/verifier construction](m2-modules-construction-2026-09-09.md) | Bounded UR5e source and pure evidence checks; full episode qualification and numerical evidence integration remain pending |
| [UR5e development evidence](m6-ur5e-development-2026-09-09.md) | Historical real movement and observation witnesses; later source needs new qualification |
| [Verifier construction boundary](../../../src/accretion/robotics/verification/README.md) | Pure evidence validation without production acceptance authority |
| [Completion plan](completion-plan-2026-09-09.md) | Product outcome, recommended simulator profile, M0 decisions, milestones, worktrees, UI, evaluation and release gates |
| [Execution checkpoint](execution-2026-09-09.md) | Current implementation disposition and observed validation |
| [M0 decisions](m0-decisions-2026-09-09.md) | Adopted contract, authority, simulation and evidence directions |
| [M0 API contract](m0-api-contract.md) | Scoped operations, idempotency, concurrency, state and Studio integration |
| [M1 foundation boundaries](m1-foundations.md) | Scoped registry API, explicit local project binding, bounded artifacts and pending host authority |
| [M1 persistence boundary](m1-registry-2026-09-09.md) | Additive migration, canonical reference resolution, transactional events and trusted conformance joins |
| [M2 final host gates](evidence/m2-host-gates-2026-09-09/README.md) | 5,250 backend passes, CI-order shared-database gates and retained failures |
| [M2 combined host/lease probe](m2-host-lease-probe-2026-09-09.md) | Four actual nonrobot construction cases, original attempts and measured limits |
| [M2 runtime boundaries](m2-runtime-boundaries.md) | Fenced authority, scoped IPC, staged worker and inspected host construction |
| [M2 current authority inventory](m2-authority-inventory-boundaries.md) | Durable policy/conformance grants, current transactional checks and commit-time expiry rollback |
| [M2 durable host journal](host-journal-boundary.md) | Persisted creation identity and cleanup-only restart authority |
| [M2 worker image](m2-worker-image.md) | Allowlisted build context, immutable dependency/model inputs and fixed staged worker entrypoint |
| [M2 intermediate gate evidence](evidence/m2-intermediate-gates-2026-09-09/README.md) | Confirmed intermediate backend/frontend results and preserved environment failures; final candidate still requires checks |
| [M2 initial integration gate](evidence/m2-initial-integration-gate-2026-09-09/README.md) | Retained full-suite failure, raw log hashes and fixture/schema repairs |
| [M4 lifecycle boundaries](m4-lifecycle-boundaries.md) | Generic-run ownership refusal, exact normal termination and entry into verification without producer acceptance |
| [M5 verifier construction](../../../src/accretion/robotics/verification/README.md) | Independently pinned evidence correlation and explicit incomplete findings |
| [M6 UR5e development](m6-ur5e-development-2026-09-09.md) | Recorded real development run, exact source identity and unresolved dynamics limitations |
| [Canonical contract inventory](../../contracts/v0.5/README.md) | Twenty-one simulation records, writer provenance and synthetic fixtures |
| [M0 construction validation](evidence/m0-construction-2026-09-09/validation.json) | Tested candidates, confirmed local exits and review-repair evidence |
| [M1 construction validation](evidence/m1-construction-2026-09-09/README.md) | Combined tests, exact source identities, immutable logs and migration recovery |
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
