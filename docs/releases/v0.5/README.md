# v0.5 — Robotics Simulation and Embodiment Foundation

Status: **planning proposed; implementation has not started**.

Start with the [multi-agent completion plan](completion-plan-2026-09-09.md).
It maps the ten SDD implementation steps and all thirty acceptance criteria into
bounded parallel work with a coordinator and at most three workers.

| Document | Purpose |
|---|---|
| [Completion plan](completion-plan-2026-09-09.md) | Product outcome, recommended simulator profile, M0 decisions, milestones, worktrees, UI, evaluation and release gates |
| [Contract audit](planning/contract-audit-2026-09-09.md) | Entry evidence, canonical contract conflicts and authority decisions |
| [Runtime audit](planning/runtime-audit-2026-09-09.md) | Existing code, missing simulation components, adapter choices and fault witnesses |
| [Acceptance audit](planning/acceptance-audit-2026-09-09.md) | Every source criterion, harness changes and research/release completion evidence |
| [Baseline checkpoint](planning/baseline-checkpoint-2026-09-09.json) | Inspected commit, source hashes, scientific access-log count and planning scope |
| [Planning validation](planning/plan-validation-2026-09-09.json) | Static link, criterion-mapping and preservation checks; not implementation or research execution |

The [forward SDD](../../sdd/future/v0.4-v1.0/02_SDDS/Accretion_SDD_v0.5.md)
remains preserved. An active v0.5 specification and registry overlay will be
created through M0 after approval. The [v0.4 handoff](../v0.4/research-handoff-2026-09-08.md)
records inherited technical entry evidence; it does not establish a v0.5
simulation result.

The recommended plan uses MuJoCo, UR5e with a Robotiq gripper, and an independent
Panda adapter, subject to the explicit M0 ADR. v0.5 is simulation-only. Engineering
readiness, research qualification and a tagged release remain distinct gates.
