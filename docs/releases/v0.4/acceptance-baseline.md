# Acceptance baseline

> Computed: 2026-09-07 · Base: `develop` at `08a695b` (the v0.4.0 release commit)
> Produced by `make acceptance` (`scripts/check_acceptance.py`) on the audited tree; the
> release decision itself is in [audit.md](audit.md).

## Why this exists

A criterion is met because a test claims it with `@pytest.mark.acceptance("<id>")` and
passes, because the vitest suite CI runs proves the `frontend` row it points at, or because a
recorded live-provider run backs a `manual` row that has not expired. The four SDDs remain the
source of truth for *what* the criteria are; `docs/acceptance/criteria.toml` records only *how*
each is verified. Nothing is met because someone believes it.

## The numbers

| | Count |
|---|---:|
| Criteria in the four SDDs (v0.1 38, v0.2 33, v0.3 46, v0.4 50) | 167 |
| Not yet due | 0 |
| **In scope** | **167** |
| Proven by a passing claiming test | 159 |
| Proven by the frontend suite | 5 |
| Proven by a recorded live-provider run | 3 |
| Uncovered | 0 |
| **Unmet MUST** | **0** |

Coverage is 100% of in-scope criteria (167 of 167):

```text
in scope: 167   proven: 159   unmet MUST: 0
PASS
```

## The v0.4 criteria by milestone

| Milestone | Criteria | Proven by | Closed in |
|---|---|---|---|
| M1 compatibility engine | AC4-M1-005 … 008 | tests | #128, #136 |
| M2 deterministic routing | AC4-M2-001, 002, 004, 009 … 015, 022 | tests | #139 |
| M3 experience and feedback | AC4-M3-003, 023 … 034 | tests | #146 |
| M4 offline ranker | AC4-M4-016 | tests | #138 |
| M5 cold start and adapter | AC4-M5-021 | tests | #141 |
| M6 shadow evaluation | AC4-M6-017, 041 | tests | #145 |
| M7 guarded bandit | AC4-M7-018 … 020 | tests | #151 |
| M8 promotion and rollback | AC4-M8-035 … 039, 042 | tests | #147 |
| M9 Experiment Studio | AC4-M9-040, 043 (`frontend`), 044 (test) | vitest pointers, a test | #153 |
| M10 research instrument | AC4-M10-045 … 050 | tests | #156 |

The five `frontend` rows are `V01-P4-004`, `V02-P6-008`, `V02-P7-007`, `AC4-M9-040` and
`AC4-M9-043`; the three `manual` rows are `V01-P0-002`, `V01-P0-004` and `V01-P4-008`, backed by
[live-acceptance-2026-09-01.md](../v0.3/evidence/live-acceptance-2026-09-01.md) and expiring on
2027-02-28, after which `make acceptance` fails against this tree until the live run is repeated.

## Reproduce

```bash
make acceptance          # the counts line above, with PostgreSQL available
make release-gate        # the five SDD §24.8 conditions
```
