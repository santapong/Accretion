# v0.2.0 release-candidate audit

> Audit date: 2026-08-24 (Asia/Bangkok)
>
> Integrated candidate: `origin/develop` at
> `6ed3584fb2a4dea2d1c7add67ce2e68fb230a553`
>
> Decision: **NO-GO for release — do not promote to `main` or create `v0.2.0`
> until the remaining browser gate passes.**

The v0.2 implementation and deterministic research claims are complete. The
automated code, database, generated-contract, frontend, dependency, frozen
research, signed-in provider, and protected release-topology checks pass.
Promotion remains blocked only because the selected release policy also
requires browser/accessibility evidence and the supported in-app browser has no
connected browser instance.

<img src="../../assets/v02-release-gate.svg" alt="v0.2 release evidence flowing from the immutable v0.1 control through P5 dynamic, P6 search, P7 transfer, and the full clean-checkout gate before promotion to main" width="100%" />

The candidate is integrated into `develop`. No release tag, GitHub release, or
v0.2 baseline record is created while this decision is NO-GO. Package metadata
uses `0.2.0` to identify the candidate, but the immutable `v0.1.0` tag remains
the current release and static control.

## Candidate identity and environment

| Item | Audited value |
|---|---|
| Audited code commit | `00220f7713943286b24535549b08ccbeb309637a` |
| Integrated candidate | `origin/develop@6ed3584fb2a4dea2d1c7add67ce2e68fb230a553` |
| Stable branch before release | `origin/main@05ccc38703f3bdc685f324b895c4cd3e2eb1112a` |
| v0.1 tag object | `3280e117aadf9ee5f431804dd92bffd2fc80229f` |
| v0.1 peeled release commit | `6324c8fab1776f0bcc1535f6d6c44fe95588f0e2` |
| Python | `3.12.9` in the locked project environment |
| uv | `0.12.5` |
| Node.js / npm | `24.19.0` / `11.16.0` |
| PostgreSQL | `16.15` with pgvector, disposable release database |
| Codex CLI | `0.148.0` |
| Claude Code | `2.1.241` |

The v0.1 tag object and peeled commit match
[the frozen v0.1 baseline](../v0.1/baseline.md); no v0.1 release document or tag was
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
| Documentation integrity | PASS | The repository docs check resolved all local links, enforced the managed folder layout, and validated accessible XML metadata across 58 Markdown files and 18 SVGs. |
| Open critical issue inventory | PASS | Authenticated GitHub searches found no open critical issue and no open Dependabot pull request. |
| v0.1 baseline integrity | PASS | The annotated tag object and peeled release commit match the frozen baseline record. |
| Codex signed-in runtime | PASS | The live test completed two independent Codex threads on one App Server. |
| Claude signed-in runtime | PASS | The adapter runs without ambient hooks/plugins, honors the typed `sonnet` model selection, and emitted normalized start, progress, and terminal events. All 3/3 live-runtime cases passed in 20.76 seconds, including mixed-provider isolation. |
| Balanced ACR-ARCH live sample | PASS | 10/10 exact artifacts passed deterministic verification: five Codex and five Claude, two tasks per category. The redacted report SHA-256 is `f378db0cd06fc1e95cfe5527496ea98e12c216a9ebb2d1d59bebdb389c2fe76c`. |
| Browser smoke and accessibility | **NOT RUN / BLOCKING** | The supported in-app browser runtime exposed no controllable browser instance. No visual, keyboard, or accessibility PASS is claimed. |
| Protected release topology | PASS | Release-bridge [PR #46](https://github.com/santapong/Accretion/pull/46) descends from `main`, is refreshed to the exact audited `develop` tree, and uses the normal squash-only linear-history policy. No protection exception or history rewrite is required. Its final head must be re-verified after this audit update before promotion. |

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

## Promotion blocker

Connect a supported browser instance and pass route smoke, responsive layout,
keyboard operation, visible focus, and automated accessibility checks for all
eleven routes.

The earlier two-parent ancestry-repair proposal is superseded by the protected
release bridge in [PR #46](https://github.com/santapong/Accretion/pull/46). The
bridge applies the exact audited `develop` tree on top of `main`, so the release
can remain squash-only and linear without changing repository settings. Issue
[#45](https://github.com/santapong/Accretion/issues/45) records that decision.
Issue [#47](https://github.com/santapong/Accretion/issues/47) holds only post-v0.2
work; no v0.3 feature belongs in this release.

## Procedure after the blockers clear

1. After the browser blocker clears, rerun any gate affected by a resulting code
   change. Any code change creates a new candidate and requires a new audit.
2. Change this audit to GO only when every blocking row passes.
3. Refresh release-bridge PR #46 from `main` so its tree exactly equals the
   audited `develop` commit, require green CI and resolved conversations, verify
   both commit IDs and tree equality, then squash-merge through the protected
   workflow.
4. Create annotated tag `v0.2.0` from the resulting `main` commit and publish the
   GitHub release using [the prepared release notes](notes.md).
5. Create `docs/releases/v0.2/baseline.md` from the actual tag object, peeled commit, release
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
make docs-check
ACCRETION_LIVE_PROVIDERS=1 ACCRETION_CLAUDE_LIVE_MODEL=sonnet \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --no-sync pytest -p pytest_asyncio.plugin -m live tests/test_live_runtimes.py
ACCRETION_LIVE_PROVIDERS=1 ACCRETION_CLAUDE_LIVE_MODEL=sonnet \
  uv run --no-sync python scripts/run_acr_arch_live_sample.py \
    --output artifacts/release/v0.2.0/acr-arch-live-sample.json
```
