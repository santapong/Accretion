# v0.4 — Evidence-Aware Node Configuration Routing

Status: **M0, M1, and M4 delivered; M2 integration in progress (2026-09-06).**

| Document | Purpose |
|---|---|
| [SDD v0.4](../../sdd/Accretion_SDD_v0.4.md) | The normative design: contracts (§7), lifecycle (§8), algorithms (§9), persistence (§13), milestones (§19), acceptance criteria (§20), decisions (§21) |
| [M0 plan](m0-plan.md) | How the contract freeze is built and proven |
| [M1 plan](m1-plan.md) | The compatibility engine, the gates, and the identity a routing attempt derives |
| [M2 implementation brief](m2-implementation-brief.md) | Work boundaries and the staged integration plan |
| [M2 runbook](m2-runbook.md) | Opt-in baseline routing, operator controls, and uncertain-dispatch recovery |
| [M4 plan](m4-plan.md) | The offline ranker, the calibration report, and why nothing learned loads without a holdout evaluation |
| [Backlog](backlog.md) | Milestone order and the v0.3 deferrals carried into v0.4 |
| [Forward package](../../sdd/future/v0.4-v1.0/00_READ_ME_FIRST.md) | Golden Direction, the cross-release contract registry, the research protocol, and the v0.5-v1.0 designs |

The acceptance harness reads the fifty v0.4 criteria from the SDD as `AC4-M<owner>-0NN` rows
and gates each milestone with `scripts/check_acceptance.py --stage v0.4-M<n>`; the rows start
`not_yet_due` in [`docs/acceptance/criteria.toml`](../../acceptance/criteria.toml) and flip to
`test` as their milestone's claiming tests land.
