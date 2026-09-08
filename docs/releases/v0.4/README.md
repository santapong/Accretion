# v0.4 — Evidence-Aware Node Configuration Routing

Status: **v0.4.1 released on 2026-09-07; M0–M10 delivered.** Status reconciled
on 2026-09-08 from the [frozen baseline](baseline.md), [acceptance baseline](acceptance-baseline.md)
and [release audit](audit.md). The v0.4.0 and v0.4.1 tags retain their recorded identities.

The release acceptance line is `in scope: 167   proven: 159   unmet MUST: 0`:
159 Python-test criteria, five frontend criteria and three recorded manual
witnesses. Of the fifty v0.4 criteria, 48 are claimed by Python tests and two
by frontend evidence pointers in [the verification policy](../../acceptance/criteria.toml).
No v0.4 criterion remains `not_yet_due`. These are dated release results, not a
new acceptance run.

M10 closed the research instrument with a limited synthetic paired result.
Amendment 1 was frozen and read once on 2026-09-07; [the access log](../../research/v0.4/access-log.jsonl)
has four rows in total, two from the original read and two from the amendment.
The [results](../../research/v0.4/results.md) retain both readings. No priced
real-provider routing pilot is claimed.

The [approved completion plan](completion-plan-2026-09-08.md) assigns the
post-release work and its validation gates. The [bounded follow-up backlog](backlog.md#remaining-bounded-work)
separates correctness checks, research decisions and deferred capabilities.
The [dated research handoff](research-handoff-2026-09-08.md) records the scoped
claim decision and the v0.5 entry-condition dispositions.
The [execution record](closure-execution-2026-09-08.md) tracks the accounting and
writer-identity repairs, their actual checks and the protected integration gate.
[Fake pilot preparation](../../research/v0.4/provider-pilot-2026-09-08/instrumentation-report-2026-09-09.md)
has verified PASS/FAIL/INCONCLUSIVE evidence with zero provider calls; live
execution remains separately gated. These development changes do not alter the
existing v0.4.1 release.

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
| [M9 plan](m9-plan.md) | The Experiment Studio: the §17.1 panel, the §17.2 comparison, the §17.3 administration page, the badges the canvas derives, the TypeScript canonical twin, and the §16.2 correlation chain |
| [M10 plan](m10-plan.md) | The research instrument: the locked corpus and the drift holdout, the door they are read through, the committed access log, and what the one locked read found |
| [Router benchmark results](../../research/v0.4/results.md) | The locked read itself: three estimands with exact intervals, the gates apart from utility, ten ablations, the drift holdout, and the priced run recorded as not run |
| [Release notes and v0.4.1 addendum](notes.md) | The v0.4.0 highlights, v0.4.1 hardening, dated acceptance, the honest limitations, the eighteen-route accessibility position and the upgrade path |
| [Release audit](audit.md) | Released candidate identities, the SDD §24.8 gate, the automated checks, acceptance, the research evidence and the release procedure |
| [Backlog](backlog.md) | Delivered milestones, remaining bounded work and explicit deferrals |
| [Forward package](../../sdd/future/v0.4-v1.0/00_READ_ME_FIRST.md) | Golden Direction, the cross-release contract registry, the research protocol, and the v0.5-v1.0 designs |

The acceptance harness reads the fifty v0.4 criteria from the SDD as
`AC4-M<owner>-0NN` rows. `scripts/check_acceptance.py --stage v0.4-M<n>` is a
milestone diagnostic; the full `make acceptance` result is the repository gate.
The empty v0.4 deferral section in the policy does not remove its two frontend
rows, which remain above it with checked evidence pointers.
