# v0.2.0 release audit

> Audit date: 2026-08-24 (Asia/Bangkok)
>
> Release-finalization base: `origin/develop` at
> `e48ea4ef2ff97ecfc8852972ca649b8e9dc7bfd5`
>
> Decision: **RELEASE AUTHORIZED WITH A DOCUMENTED MAINTAINER EXCEPTION.**

> Released: `v0.2.0` on 2026-08-24 from
> `de146cd9e1a3e651e066f8dde020c7938cbc1316`; see the
> [frozen baseline](baseline.md) and
> [published release](https://github.com/santapong/Accretion/releases/tag/v0.2.0).

The v0.2 implementation and deterministic research claims are complete. The
automated code, database, generated-contract, frontend, dependency, frozen
research, signed-in provider, and protected release-topology checks pass.
The supported browser-control surface still exposes no connected browser, so no
rendered visual, responsive, keyboard, focus, or accessibility PASS is claimed.
After that limitation was explicitly disclosed, the maintainer authorized the
2026-08-24 release with the exception tracked in
[#52](https://github.com/santapong/Accretion/issues/52).

<img src="../../assets/v02-release-gate.svg" alt="v0.2 release evidence flowing from the immutable v0.1 control through P5 dynamic, P6 search, P7 transfer, and the full clean-checkout gate before promotion to main" width="100%" />

The candidate is integrated into `develop`; package metadata uses `0.2.0`. The
immutable `v0.1.0` tag remains the static experimental control and is not moved
or rewritten by this release.

## Candidate identity and environment

| Item | Audited value |
|---|---|
| Audited code commit | `00220f7713943286b24535549b08ccbeb309637a` |
| Release-finalization base | `origin/develop@e48ea4ef2ff97ecfc8852972ca649b8e9dc7bfd5` |
| Stable branch before release | `origin/main@05ccc38703f3bdc685f324b895c4cd3e2eb1112a` |
| Authorized develop commit | `7cd9e0a9d90a2d93c1c60907490bd34e98ec5d68` |
| Release tree | `3828947d0b74f23125193b0553f0a4eb36239460` |
| v0.2 tag object | `2c455bac152c971ca85932262ac121c8d847274a` |
| v0.2 peeled release commit | `de146cd9e1a3e651e066f8dde020c7938cbc1316` |
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
| Open critical issue inventory | PASS | Authenticated GitHub searches found no open critical issue and no open Dependabot pull request. Release-topology issue #45 closed with the release; open issues #47 and #52 are respectively post-v0.2 planning and the documented browser exception. |
| v0.1 baseline integrity | PASS | The annotated tag object and peeled release commit match the frozen baseline record. |
| Codex signed-in runtime | PASS | The live test completed two independent Codex threads on one App Server. |
| Claude signed-in runtime | PASS | The adapter runs without ambient hooks/plugins, honors the typed `sonnet` model selection, and emitted normalized start, progress, and terminal events. All 3/3 live-runtime cases passed in 20.76 seconds, including mixed-provider isolation. |
| Balanced ACR-ARCH live sample | PASS | 10/10 exact artifacts passed deterministic verification: five Codex and five Claude, two tasks per category. The redacted report SHA-256 is `f378db0cd06fc1e95cfe5527496ea98e12c216a9ebb2d1d59bebdb389c2fe76c`. |
| Browser smoke and accessibility | **NOT RUN · MAINTAINER EXCEPTION** | The supported browser runtime exposed no controllable browser instance. No visual, responsive, keyboard, focus, or accessibility PASS is claimed. Issue [#52](https://github.com/santapong/Accretion/issues/52) tracks post-release evidence. |
| Protected release topology | PASS | Release-bridge [PR #46](https://github.com/santapong/Accretion/pull/46) descended from `main`, matched the exact authorized `develop` tree, passed CI run 164, and squash-merged under the normal linear-history policy. Post-merge `main` tree `3828947d0b74f23125193b0553f0a4eb36239460` exactly matched the authorized tree. |

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

## Documented release exception

The maintainer explicitly authorized release after the unavailable browser
surface and missing rendered evidence were disclosed. This authorization does
not convert the browser row to PASS, weaken any runtime safety invariant, or
alter a frozen research result. Issue
[#52](https://github.com/santapong/Accretion/issues/52) retains the exact
post-release route, responsive, keyboard, visible-focus, console, and automated
accessibility work.

The earlier two-parent ancestry-repair proposal is superseded by the protected
release bridge in [PR #46](https://github.com/santapong/Accretion/pull/46). The
bridge applies the exact audited `develop` tree on top of `main`, so the release
can remain squash-only and linear without changing repository settings. Issue
[#45](https://github.com/santapong/Accretion/issues/45) records that decision.
Issue [#47](https://github.com/santapong/Accretion/issues/47) holds only post-v0.2
work; no v0.3 feature belongs in this release.

## Completed promotion record

1. Release-bridge PR #46 was refreshed to authorized `develop`, passed backend
   and frontend CI, had no unresolved review threads, and squash-merged to
   protected `main`.
2. Post-merge verification proved exact tree equality before annotated tag
   `v0.2.0` was created at the resulting `main` commit.
3. The non-draft GitHub release was published from [the release notes](notes.md),
   and [the frozen baseline](baseline.md) records the observed tag object,
   peeled commit, release tree, URL, and evidence fingerprints.

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
