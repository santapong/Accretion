# v0.2.0 release-candidate audit

> Audit date: 2026-08-24 (Asia/Bangkok)
>
> Candidate code: `feature/v02-release-closure` at
> `bbec630cd0a24c66ea30de55351ae5eff3b6e2b8`
>
> Decision: **NO-GO for release — integration into `develop` is allowed, but do
> not promote to `main` or create `v0.2.0` yet.**

The v0.2 implementation and deterministic research claims are complete. The
automated code, database, generated-contract, frontend, dependency, and frozen
research checks pass. Promotion remains blocked because the selected release
policy also requires signed-in Claude completion, balanced live calibration,
browser/accessibility evidence, and a one-time repair of the unrelated
`main`/`develop` histories. Those items did not pass or could not be executed in
the current environment.

<img src="assets/v02-release-gate.svg" alt="v0.2 release evidence flowing from the immutable v0.1 control through P5 dynamic, P6 search, P7 transfer, and the full clean-checkout gate before promotion to main" width="100%" />

The candidate may merge into `develop` after its normal CI passes; that
integration does not change the NO-GO release decision. No release tag, GitHub
release, or v0.2 baseline record is created while this decision is NO-GO.
Package metadata uses `0.2.0` to identify the candidate, but the immutable
`v0.1.0` tag remains the current release and static control.

## Candidate identity and environment

| Item | Audited value |
|---|---|
| Candidate commit | `bbec630cd0a24c66ea30de55351ae5eff3b6e2b8` |
| Candidate base | `origin/develop@4f249f4a94446ecad8c314224b23349ec2a6a8c7` |
| Stable branch before reconciliation | `origin/main@05ccc38703f3bdc685f324b895c4cd3e2eb1112a` |
| v0.1 tag object | `3280e117aadf9ee5f431804dd92bffd2fc80229f` |
| v0.1 peeled release commit | `6324c8fab1776f0bcc1535f6d6c44fe95588f0e2` |
| Python | `3.12.9` in the locked project environment |
| uv | `0.12.5` |
| Node.js / npm | `24.19.0` / `11.16.0` |
| PostgreSQL | `16.15` with pgvector, disposable release database |
| Codex CLI | `0.148.0` |
| Claude Code | `2.1.241` |

The v0.1 tag object and peeled commit match
[the frozen v0.1 baseline](V0_1_BASELINE.md); no v0.1 release document or tag was
rewritten during this work.

## Gate results

| Gate | Result | Evidence |
|---|---|---|
| Python lint and types | PASS | Ruff passed; strict mypy passed across 58 source files. |
| Backend and PostgreSQL tests | PASS | 218 collected: 215 passed on a newly recreated database; 3 signed-in live cases were skipped and audited separately. |
| PostgreSQL migrations | PASS | All nine migrations upgraded to head, downgraded to base, and upgraded to head on the disposable PostgreSQL 16 database. |
| Generated API contract | PASS | OpenAPI generation was idempotent; the TypeScript schema SHA-256 remained `65d4e6fb10c64e1425a1673690a513ac34fc0679a17e54341bb4a387c57d46ff`. |
| Frontend checks | PASS | ESLint and TypeScript passed. |
| Frontend tests and build | PASS | 22 Vitest cases passed and the production build completed. The 526.90 kB chunk advisory is tracked, non-blocking debt. |
| Production dependency audit | PASS | `pip-audit` found no known vulnerability in the exact exported production requirements. |
| Frontend dependency audit | PASS | `npm audit` reported 0 vulnerabilities across 328 dependencies. |
| Tracked credential-shape scan | PASS | No tracked file matched the release scan for common token or private-key shapes. |
| Open critical issue inventory | PASS | Authenticated GitHub searches found no open critical issue and no open Dependabot pull request. |
| v0.1 baseline integrity | PASS | The annotated tag object and peeled release commit match the frozen baseline record. |
| Codex signed-in runtime | PASS | The live test completed two independent Codex threads on one App Server. |
| Claude signed-in runtime | **BLOCKED** | Authentication and version checks passed, but a minimal prompt produced no terminal response in a separate 20-second probe. The full live suite did not complete and was stopped after its mixed-provider case stalled. |
| Balanced ACR-ARCH live sample | **NOT RUN / BLOCKING** | The 10-artifact Codex/Claude sample was not started after the prerequisite Claude prompt failed to complete. |
| Browser smoke and accessibility | **NOT RUN / BLOCKING** | The supported in-app browser runtime exposed no controllable browser instance. No visual, keyboard, or accessibility PASS is claimed. |
| Branch-ancestry reconciliation | **BLOCKED** | Reconciliation [PR #46](https://github.com/santapong/Accretion/pull/46) is clean and its CI passed, but branch rules reject merge commits while the available credential cannot make the required one-time settings change. |

## Frozen research evidence

| Suite | Result | Reproducible evidence |
|---|---|---|
| ACR-ARCH static architecture control | PASS | The inherited 30-task, 68-scenario replay and v0.1 fixture hashes remain unchanged. |
| P5 static versus dynamic | PASS · POSITIVE | 12 tasks and 24 paired traces; heterogeneous/uncertain utility uplift `+0.224631`, predictable uplift `+0.009195`, success `9/12 → 12/12`, no false accepts or risk events, and invalid-proposal fallback PASS. |
| P6 bounded search | PASS | Verified acceptance rises from `8/12` at N=1 to `12/12` at N=4; mean quality rises `0.472500 → 0.768333`, with null results preserved. |
| P7 verified experience | PASS | Replay quality `+0.070500`, tool calls reduced 20%, stale rejection 95%, negative transfer 3.33%, and no false-accept or success-rate regression. |

P5 fixture SHA-256 fingerprints:

- config: `55678342830491bc20ceea16332b6385c3f6afba3f8fd35fee6342d1260da8de`;
- tasks: `b411b0573d514a496b81b82e25ccee146b66af7fd990187ede6e7ea4c1c399db`;
- replay traces: `77645b41f35430bb886fae558a6ee684664d87b7adcb755c68a92c3db6dd3616`.

The P5 overall gate explicitly requires the benefit threshold as well as
predictable-task non-inferiority, safety, success non-regression, and static
fallback. A regression test proves a below-threshold treatment cannot report an
overall PASS.

## Promotion blockers

All four items below are release-blocking under the selected full gate:

1. Restore a responsive signed-in Claude session and rerun all three live-runtime
   tests to completion.
2. Run the balanced 10-artifact ACR-ARCH live calibration with five independently
   verified artifacts per provider.
3. Connect a supported browser instance and pass route smoke, responsive layout,
   keyboard operation, visible focus, and automated accessibility checks for all
   eleven routes.
4. Temporarily allow the two-parent history-reconciliation merge and disable the
   linear-history restriction, merge PR #46, then restore the protections. The
   reconciliation commit `1ad3bce048cfb81362318f3747595f55a5780c81` has the
   exact `origin/develop` tree and introduces no content change.

Issue [#45](https://github.com/santapong/Accretion/issues/45) tracks the ancestry
repair. Issue [#47](https://github.com/santapong/Accretion/issues/47) holds only
post-v0.2 work; no v0.3 feature belongs in this release.

## Procedure after the blockers clear

1. Merge the release-candidate pull request into `develop` after its normal CI
   passes. This is integration only and does not authorize release promotion.
2. After the blockers clear, rerun the complete gate from a clean checkout of
   the integrated candidate. Any code change creates a new candidate and requires
   a new audit.
3. Change this audit to GO only when every blocking row passes, then open the
   audited `develop` to `main` release pull request, require CI and
   resolved review conversations, and merge through the protected workflow.
4. Create annotated tag `v0.2.0` from the resulting `main` commit and publish the
   GitHub release using [the prepared release notes](V0_2_RELEASE_NOTES.md).
5. Create `V0_2_BASELINE.md` from the actual tag object, peeled commit, release
   URL, fixture hashes, and final audit. Never precompute or invent those values.

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
npm run check
npm run test
npm run build
ACCRETION_LIVE_PROVIDERS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -p pytest_asyncio.plugin -m live tests/test_live_runtimes.py
ACCRETION_LIVE_PROVIDERS=1 \
  uv run --no-sync python scripts/run_acr_arch_live_sample.py \
    --output artifacts/release/v0.2.0/acr-arch-live-sample.json
```
