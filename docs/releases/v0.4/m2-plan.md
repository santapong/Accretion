# M2 implementation and evidence

Baseline: `42067cd` on `develop`. Implementation uses the isolated
`feature/v04-m2-integration` branch. The original checkout and older worktrees are
preserved. Three implementation workers cover freeze/dispatch, catalog/selection,
and API/evidence; the coordinator owns persistence, application wiring, integration,
and final checks. No learned model is activated and no paid provider is invoked.

## Delivered boundaries

The implementation follows four review areas: immutable freeze; candidate
construction/selection/dispatch; operator API; adversarial evidence and runbooks.
Worker commits are integrated locally before any remote review or merge. Local
verification is not a claim that GitHub CI has run or that `develop` contains M2.

The flag defaults off. Enabled routing is baseline-only and ships an explicit FAKE
catalog. See [the runbook](m2-runbook.md) for the intentionally blocked live/agent
tool cases and recovery from uncertain dispatch. Contracts and migrations are
unchanged. The store gains an explicit routing transaction and graph receipt reader;
these are required to serialize amendments and dispatch across API processes.

## Acceptance witnesses

The eleven M2 policy rows are deleted from `criteria.toml`; default verification is
now executable tests rather than `not_yet_due`. The source SDD is unchanged.

| Criterion | Executable evidence |
|---|---|
| AC4-M2-001 | Dispatch and real end-to-end graph tests: receipt precedes submit; events carry frozen node hash |
| AC4-M2-002 | `test_v04_m2_freeze.py`: verification spec persists before the node contract |
| AC4-M2-004 | `test_v04_m2_freeze.py`: graph revision produces a new immutable node contract |
| AC4-M2-009 | `test_v04_m2_candidates.py`: behavioral deduplication |
| AC4-M2-010 | Candidate and selector tests preserve audited fallback through bounded pruning |
| AC4-M2-011 | `test_v04_m2_service.py`: immutable request replay returns the same stored receipt |
| AC4-M2-012 | Service tests pin versions and distinguish registry, model, runtime, and node changes |
| AC4-M2-013 | Service routes a secret-bearing objective without copying it into receipt text |
| AC4-M2-014 | `test_v04_m2_end_to_end.py`: injected receipt-write failure produces zero submissions; dispatch tests also refuse a failed claim |
| AC4-M2-015 | Service tests reject ineligible overrides and preserve reason/principal attribution |
| AC4-M2-022 | Selector tests return fallback or human review under insufficient evidence |

Additional regressions exercise atomic PostgreSQL rollback/serialization, per-request
compatibility evidence across attempts, structural graph features, concurrent run
updates, selected tool implementation drift, and cross-workspace isolation. PostgreSQL
tests use a disposable database, never the development database. Six signed-in live
runtime tests are intentionally skipped in the offline lane; their existing manual
acceptance evidence is not replaced by fake execution.

The enabled real-stack graph test uses `MemoryStore`, `RunManager`,
`build_node_routing`, and `FakeRuntime`, reaches `SUCCEEDED`, and preserves all
mandatory git-diff, output-contract, and trajectory-policy verifier checks. It is
not a mocked routing-service test.

## Implementation decisions

- **Runtime seam:** the provider dispatch seam is already merged, so the obsolete
  freeze constraint `runtime.provider == run.provider` is omitted. Selected runtime
  identity, version, capability digest, and readiness are checked before dispatch.
- **OQ-403 / OQ-404:** versioned caps are AGENT 8, TOOL 8, VERIFIER 4; Pareto epsilon
  is 0.02. This is a deterministic baseline, not a claim of validation-set tuning.
- **OQ-416:** the fallback bundle is explicit and digest-pinned in routing identity
  and context. Unknown or unavailable exact catalog entries cannot become fallback.
- **Override chain:** an override creates a new request and receipt, preserving its
  predecessor. Cancel is also append-only. A graph-scoped read resolves the latest
  head, and the transactional dispatch claim prevents a second executable head.
- **OQ-417:** structured uppercase reason codes plus a nonempty explanation are
  required; a closed taxonomy and a new canonical override contract remain deferred.
- **Protocol extension:** `latest_receipt` and `claim_dispatch` are added to the
  calling protocol, with its digest updated explicitly in [M1's record](m1-plan.md).

## Reproduce

Use the project's Python environment with `PYTHONPATH=src` in a worktree. Point both
`ACCRETION_DATABASE_URL` and `ACCRETION_TEST_POSTGRES_URL` at a **disposable migrated
PostgreSQL database**: the inherited migration tests deliberately drop and restore
their own tables. Run the database suites serially.

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin -q
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python scripts/check_acceptance.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python scripts/check_acceptance.py --stage v0.4-M2
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python scripts/release_gate.py --json
ruff check .
mypy src/accretion
python scripts/check_docs.py
npm run api:generate
npm run check
npm run test
npm run build
```

The acceptance summary and full pytest exit status are separate checks: the harness
classifies criterion witnesses, while an unmarked regression can still fail pytest.
Both must pass before handoff.

## Local verification — 2026-09-06

- Full backend suite: **2,972 passed, 6 intentionally skipped**.
- Acceptance: **133 in scope, 127 proven, 0 unmet MUST** (three frontend and
  three current manual records account for the other six in-scope criteria).
- Five-part release gate: all conditions pass (acceptance, secret scans, policy
  bypass checks, connection isolation, inherited regression suites).
- Ruff passes; Mypy passes across 127 source files.
- Frontend: 210 tests pass; lint/type checks and production build pass, including
  bundle budgets. Regenerating the OpenAPI client produces no further diff.
- Documentation and whitespace checks pass.

This is local evidence on the integration branch. No remote push, merge, release,
deployment, paid call, or canonical contract/migration change is included. The
original `develop` checkout remains clean at the baseline. Continue with remote
review only through the repository's normal branch/PR workflow.
