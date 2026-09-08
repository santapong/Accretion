# Exploration accounting after the v0.4 closure repair

Prepared 2026-09-09. Applies to the `develop` candidate containing the
[closure repair](../releases/v0.4/closure-execution-2026-09-08.md); the existing
v0.4.1 tag retains its original implementation. This work does not activate
routing or change a running deployment.

## What is enforced

AUTO routing acquires a PostgreSQL transaction lock for the shared
`(workspace_id, node_kind)` budget, then the run lock. Accounting replay,
admission and the new receipt commit use that transaction. Separate API
processes using this implementation therefore cannot both reserve the same
remaining allowance. MemoryStore uses its existing transaction lock.

The account is rebuilt from immutable EXPLORE receipts and matching durable
ExperienceRecords. Each observation must match the frozen node's execution,
project and selected configuration. Missing outcomes retain the reservation.
When revisions disagree about measured cost, the largest value is retained;
a later lower report cannot erase previously recorded spend. Measured overruns
remain visible, including normalized values above one.

Unreadable charges, inconsistent scope, conflicting duplicate receipts and
unresolvable observation provenance make exploration unavailable. The receipt
records `EXPLORATION_ACCOUNTING_UNAVAILABLE` with a deterministic selection;
the account is never treated as empty to make the request proceed.

## Scope and limits

The account is cumulative per workspace/node kind. It is not a separate run
or calendar-day account and has no daily reset. Counts and normalized cost
must not be presented as monetary allowances. This preserves the implemented
scope; distinct per-run/day allocation semantics in the SDD remain a separately
scoped design decision.

Observed cost is divided by the frozen node's cost cap. A positive observation
against a zero cap has no valid denominator and makes accounting unavailable.
The repair can deny later exploration once an overrun has been durably recorded;
it cannot retroactively prevent an unobserved external charge. A priced pilot
still needs explicit provider accounting and independent money/call/time limits.

No totals table, contract schema change or migration is introduced. This does
not make old and new AUTO writers interoperable: old binaries neither acquire
the shared lock nor reconstruct durable observations by these rules.

## Rollout and rollback

Keep `ACCRETION_ENABLE_NODE_ROUTING=false` or
`ACCRETION_NODE_ROUTING_MODE=BASELINE_ONLY` during deployment preparation.
Drain or disable AUTO on every older writer before admitting work through an
upgraded writer. A mixed fleet cannot claim the corrected admission invariant.
Do not reactivate AUTO merely because deployment or migration checks pass;
the existing promotion, calibration, safety and approval gates still apply.

Rollback also requires AUTO to remain disabled on older binaries. Preserve
receipts and experience observations; do not delete accounting history to
restore an allowance. A completed node is not retried because its settlement
hook failed, and an uncertain external dispatch still requires reconciliation.

Regression evidence is in [budget admission](../../tests/test_v04_budget_admission.py),
[ledger arithmetic](../../tests/test_v04_m7_ledger.py), and
[both-store replay](../../tests/test_v04_m7_postgres_store.py). The closure record
states which checks actually ran and on which candidate.
