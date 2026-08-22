# v0.1 release readiness audit

> Audit date: 2026-08-22 (Asia/Bangkok)  
> Candidate code: `origin/develop` at `401d6ccb786127a04b179cc4a7e54f7ab53d0f52`  
> Decision: **NO-GO — do not merge to `main` or create `v0.1.0`.**

The v0.1 SDD permits a release only when every MUST criterion in P0 through P4
passes, no critical security issue remains open, and the ACR-ARCH benchmark is
reproducible from a clean checkout. The candidate has a healthy P0–P3 automated
baseline, but it does not meet that complete release contract.

## Verification evidence

| Gate | Result | Evidence |
|---|---|---|
| Python lint and types | PASS | Ruff passed; strict mypy passed across 38 source files. |
| Backend tests | PASS | 144 passed with PostgreSQL integration enabled; the 3 opt-in live cases were excluded from this run. |
| PostgreSQL migrations | PASS | On a clean PostgreSQL 16 database, upgrade to head, downgrade to base, and upgrade to head all completed. A downgrade on populated P3 test history was separately and correctly refused because the P2 schema cannot represent multiple loop executions per run. |
| Generated API contract | PASS | OpenAPI regeneration produced no tracked schema difference. |
| Frontend static checks | PASS | ESLint and TypeScript checks passed. |
| Frontend tests and build | PASS | 12 Vitest cases passed and the Vite production build completed. |
| Dependency install audit | PASS | `uv sync --all-groups` and `npm ci` completed; npm reported zero vulnerabilities. |
| Claude live runtime | PASS | Normalized start/progress/terminal acceptance passed with Claude Code `2.1.239`. |
| Mixed-provider live runtime | PASS | Claude and Codex completed concurrently in separate Git worktrees. |
| Codex two-thread live runtime | **FAIL** | Codex health was `DEGRADED`: installed `0.147.0` is outside the validated `>=0.148,<0.149` range, so V01-P0-002 was not demonstrated. |
| Browser acceptance | NOT RUN | No controllable browser was available in the audit environment. |
| Open critical-security issue check | NOT VERIFIED | Git transport could authenticate, but GitHub CLI/API issue access was not authenticated. This condition must be checked before release. |

## Acceptance status

| Release area | Status | Release evidence or blocker |
|---|---|---|
| P0 — Runtime feasibility | **BLOCKED** | The live two-thread Codex MUST gate failed in the current environment because the installed CLI is outside the validated range. |
| P1 — Deterministic planning | PASS in current automated suite | Planning, selector, persistence, API, and UI tests passed. |
| P2 — Feedback loops | PASS in current automated suite | Bounded-loop, verifier, recovery, control, projection, and PostgreSQL tests passed. |
| P3 — Static graphs | PASS in current automated suite | Template, execution, checkpoint, replay, approval, projection, and PostgreSQL tests passed. |
| P4 — Harness, capability, policy, frontend | **NOT COMPLETE** | The project status declares P4 planned. The capability registry, MCP gateway, complete operator surfaces, and full P4 acceptance evidence are not present. |
| ACR-ARCH | **NOT COMPLETE** | The required benchmark implementation, 30 versioned tasks, metrics/regret data, filtering UI, and independently versioned environments are absent. |
| Security issue gate | **UNKNOWN** | The open-issue inventory was not available to this unauthenticated audit session. |

## Remaining v0.1 work

1. Complete and prove the P4 security boundary:
   capability registry and policy enforcement, native-provider escape denial,
   credential redaction, and durable side-effect intent/result evidence
   (`V01-P4-001` through `V01-P4-003`).
2. Complete the operator experience and its acceptance tests: dashboard, task
   profiler, live run, runtime monitor, trace history, approvals, capabilities,
   ACR-ARCH, reconnect snapshot recovery, read-only graph layout, and complete
   audit linkage (`V01-P4-004` through `V01-P4-007`).
3. Implement the ACR-ARCH release gate: at least 30 reproducible versioned tasks,
   raw metrics, architecture-regret computation, filtering UI, and independently
   versioned benchmark configuration/environments (`V01-BENCH-001` through
   `V01-BENCH-005`).
4. Install a validated Codex CLI `0.148.x`, or deliberately validate and document
   a broader compatibility range, then rerun all three live-provider cases.
5. Run the P4 browser acceptance scenarios and review the authenticated GitHub
   issue/security inventory for open critical findings.
6. Rerun the complete gate from a clean checkout. Only after every MUST item is
   evidenced should `develop` be promoted through a release pull request and
   `v0.1.0` be created from the resulting `main` commit.

## Reproduction commands

```bash
uv sync --all-groups
npm ci
uv run ruff check .
uv run mypy src
ACCRETION_TEST_POSTGRES_URL=postgresql+asyncpg://accretion:accretion@localhost:5432/accretion \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -p pytest_asyncio.plugin
npm run api:generate
git diff --exit-code -- apps/ui/src/api/schema.d.ts openapi.json
npm run check
npm run test
npm run build
ACCRETION_LIVE_PROVIDERS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -p pytest_asyncio.plugin -m live tests/test_live_runtimes.py
```

The release decision should be re-recorded against the exact promoted commit;
passing CI alone is not equivalent to passing the v0.1 SDD release gate.
