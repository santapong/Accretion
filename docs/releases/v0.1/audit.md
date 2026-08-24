# v0.1 release readiness audit

> Audit date: 2026-08-22 (Asia/Bangkok)
> Candidate code: `origin/develop` at `934ea75cf06be820ac6fdd0946e3203982c779c4`
> Decision: **GO — eligible for promotion to `main` and tag `v0.1.0`.**

The candidate passes every MUST criterion in SDD Section 20, has no open critical
security issue in the authenticated issue inventory, and reproduces the frozen
ACR-ARCH dataset from a clean checkout and clean PostgreSQL 16 database.

<img src="../../assets/benchmark-pipeline.svg" alt="Release-evidence pipeline from frozen benchmark inputs through schema validation, deterministic replay, raw dimensions, utility and regret, and durable operator reports" width="100%" />

> Post-release note: the audited candidate was promoted and the immutable
> `v0.1.0` tag now records the shipped release. The procedure below remains the
> historical evidence used for that promotion.

## Clean-checkout evidence

The complete gate was rerun from a new clone of the exact candidate with newly
installed Python/npm dependencies and a new disposable PostgreSQL 16 instance.

| Gate | Result | Evidence |
|---|---|---|
| Python lint and types | PASS | Ruff passed; strict mypy passed across 42 source files. |
| Backend tests | PASS | 158 passed against PostgreSQL; 3 signed-in live cases were isolated and then passed separately. |
| PostgreSQL migrations | PASS | All six migrations upgraded to head, downgraded to base, and upgraded to head on a clean database. |
| Generated API contract | PASS | OpenAPI regeneration produced no tracked TypeScript schema difference. |
| Frontend checks | PASS | ESLint and TypeScript checks passed. |
| Frontend tests and build | PASS | 15 Vitest cases passed; the Vite production build completed. |
| Dependency install | PASS | `uv sync --all-groups` and `npm ci` completed from the lockfiles. |
| Dependency security | PASS | npm audit reported 0 vulnerabilities; pip-audit reported no known production dependency vulnerabilities. |
| Tracked credential-shape scan | PASS | No tracked file matched the release scan for common token/private-key formats. |
| Codex live runtime | PASS | Codex CLI `0.148.0` launched one App Server and completed two independent threads. |
| Claude live runtime | PASS | Claude Code `2.1.239` emitted normalized start, progress, and terminal events. |
| Mixed-provider isolation | PASS | Claude and Codex completed concurrently in separate Git worktrees. |
| ACR-ARCH live calibration | PASS | 10/10 exact provider-written artifacts passed deterministic checks: 2 tasks per category and 5 calls per provider. |
| Open critical issue inventory | PASS | Authenticated GitHub searches returned no open issue matching `severity:critical`, `critical`, or critical issue text. |
| Visual browser pass | NOT RUN | The supported browser runtime exposed no controllable browser instance. No visual result is claimed; this is not a Section 20 MUST criterion. |

## Acceptance status

| Release area | Status | Evidence |
|---|---|---|
| P0 — Runtime feasibility | PASS | Runtime health/auth classification, crash/reconcile/idempotency tests, 3/3 signed-in provider cases, two Codex threads, and mixed-worktree concurrency. |
| P1 — Deterministic planning | PASS | Schema/reference persistence, typed unknown handling, deterministic selection, rationale/alternatives, hard-risk policy, and audited override tests. |
| P2 — Feedback loops | PASS | Terminal-condition, bad-patch rejection, durable recovery, ceiling, inconclusive-policy, and loop projection tests. |
| P3 — Static graphs | PASS | Validated-template admission, no arbitrary-topology API, checkpoint/resume, immutable replay, projection identity, and bounded HYBRID tests. |
| P4 — Harness, policy, frontend | PASS | Denied native escape tests, credential redaction, intent-before-effect/result-after-effect, all required routes, snapshot-first SSE recovery, read-only layout, and complete `RunAudit` linkage. |
| ACR-ARCH | PASS | Frozen 30-task corpus, 68 raw scenario metrics, per-task regret, five UI filters, and independently versioned configuration/environments. |
| Security issue gate | PASS | No open critical issue found; npm/pip advisory checks and tracked credential-shape scan were clean. |

P4-008 is a SHOULD rather than a release-blocking MUST, but it also passes: the
balanced live calibration independently verified artifacts produced by each provider
with deterministic checks.

## ACR-ARCH reproduction

The clean-checkout replay produced:

```text
benchmark run: bnr_94c78f2ab6bd9adbf47be53301
tasks: 30
scenarios: 68
providers: Claude 34, Codex 34
tasks with non-zero selector regret: 4
task corpus SHA-256: 9251bb918912e73a2dade20189f93cc26cd7bc217a0dea03713ef252843b9dd7
replay trace SHA-256: 2f62f87eaf079914d41f47bea57a4dd04ce469d0e54f1d6a38faa6de0dd6f051
```

Every task declares a versioned environment, verifier, budgets, success criteria,
and at least two applicable modes. Live calibration remains separate and cannot
change these frozen release metrics.

## Release procedure

1. Merge this documentation-only evidence update into `develop` after CI passes.
2. Open the planned release pull request from `develop` to protected `main`.
3. Require backend/frontend CI success and zero unresolved review conversations.
4. Merge through GitHub using squash merge.
5. Create annotated tag `v0.1.0` from the resulting `main` commit and publish the
   GitHub release using `docs/releases/v0.1/notes.md`.

No code change is permitted between the audited candidate and the release PR. A
code change requires a new clean-checkout audit; documentation-only release evidence
does not invalidate the candidate code result.

## Reproduction commands

```bash
uv sync --all-groups
npm ci
uv run --no-sync ruff check .
uv run --no-sync mypy src
ACCRETION_DATABASE_URL=postgresql+asyncpg://accretion:accretion@localhost:5432/accretion \
  uv run --no-sync alembic upgrade head
ACCRETION_DATABASE_URL=postgresql+asyncpg://accretion:accretion@localhost:5432/accretion \
  uv run --no-sync alembic downgrade base
ACCRETION_DATABASE_URL=postgresql+asyncpg://accretion:accretion@localhost:5432/accretion \
  uv run --no-sync alembic upgrade head
ACCRETION_TEST_POSTGRES_URL=postgresql+asyncpg://accretion:accretion@localhost:5432/accretion \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -p pytest_asyncio.plugin
npm run api:generate
git diff --exit-code -- apps/ui/src/api/schema.d.ts
npm run check
npm run test
npm run build
ACCRETION_LIVE_PROVIDERS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -p pytest_asyncio.plugin -m live tests/test_live_runtimes.py
ACCRETION_LIVE_PROVIDERS=1 \
  uv run --no-sync python scripts/run_acr_arch_live_sample.py
```
