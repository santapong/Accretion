# v0.4 — Evidence-Aware Node Configuration Routing

Status: **In release preparation (2026-09-06).** M0 through M8 are delivered on `develop`,
along with M9's routing panel, shadow comparison and router administration page and M10's
statistics, lineage splits, baselines, corpus and development pilot. M9d and M10c–M10d close
the last nine acceptance rows, after which the release PR bumps the version and cuts the tag.
The harness target the release PR must confirm is `in scope: 167   proven: 161   unmet
MUST: 0`; see the [draft release notes](notes.md) and [draft release audit](audit.md).

| Document | Purpose |
|---|---|
| [SDD v0.4](../../sdd/Accretion_SDD_v0.4.md) | The normative design: contracts (§7), lifecycle (§8), algorithms (§9), persistence (§13), milestones (§19), acceptance criteria (§20), decisions (§21) |
| [M0 plan](m0-plan.md) | How the contract freeze is built and proven |
| [M1 plan](m1-plan.md) | The compatibility engine, the gates, and the identity a routing attempt derives |
| [M2 implementation brief](m2-implementation-brief.md) | Work boundaries and the staged integration plan |
| [M2 plan and evidence](m2-plan.md) | Acceptance witnesses, implementation decisions, and repeatable gates |
| [M2 runbook](m2-runbook.md) | Opt-in baseline routing, operator controls, and uncertain-dispatch recovery |
| [M3 plan and evidence](m3-plan.md) | The feedback pipeline's three run-manager hooks, the store-backed evidence retriever, and §11.2 |
| [M3 feedback runbook](../../runbooks/v04-feedback.md) | Material conflicts, recovery authority, retrieval, and the §11.2 endpoints |
| [M4 plan](m4-plan.md) | The offline ranker, the calibration report, and why nothing learned loads without a holdout evaluation |
| [M5 plan](m5-plan.md) | Cold start, the capped cross-domain prior, and the five routing-stage collaborators M6-M8 plug into |
| [M6 plan](m6-plan.md) | Shadow evaluation by branched live rollout: what a shadow decision is, why it is never a replay, and the gates a fork passes |
| [M7 plan](m7-plan.md) | The guarded bandit: the nine gates exploration passes, inverse-gap weighting under a conformal safety clip, and the cost ledger reconstructed from receipts |
| [M8 plan](m8-plan.md) | The activation ledger, the rollback drill, and the CSPI-MT promotion gate over the Logarithmic-Smoothing estimator |
| [Release notes (draft)](notes.md) | The v0.4.0 highlights, the acceptance target, the honest limitations, the eighteen-route accessibility position and the upgrade path |
| [Release audit (draft)](audit.md) | Candidate identity, the SDD §24.8 gate, the automated checks, acceptance, the research evidence and the release procedure |
| [Backlog](backlog.md) | Milestone order and the v0.3 deferrals carried into v0.4 |
| [Forward package](../../sdd/future/v0.4-v1.0/00_READ_ME_FIRST.md) | Golden Direction, the cross-release contract registry, the research protocol, and the v0.5-v1.0 designs |

The acceptance harness reads the fifty v0.4 criteria from the SDD as `AC4-M<owner>-0NN` rows
and gates each milestone with `scripts/check_acceptance.py --stage v0.4-M<n>`; the rows start
`not_yet_due` in [`docs/acceptance/criteria.toml`](../../acceptance/criteria.toml) and flip to
`test` as their milestone's claiming tests land.
