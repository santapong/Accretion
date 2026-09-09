# Dependency review and v0.4 integration — 2026-09-09

The approved v0.4 closure is merged into `develop` through
[PR #179](https://github.com/santapong/Accretion/pull/179), squash commit
`abd3e0d8d0bd36b86ab28b47d1b789d87f7a7e30`. Its reviewed head and merged tree
are identical. M0–M10 and the approved C0/C1/C2/R0/R1 work are complete;
[the execution record](closure-execution-2026-09-08.md) contains the boundaries
and protected-check evidence. This maintenance change integrates all ten
Dependabot requests open at inspection, with one combined validation on top
of that closure. GitHub records the individual requests' final dispositions.

## Requests covered

| PR | Dependency | Accepted requirement / locked result |
|---|---|---|
| [#169](https://github.com/santapong/Accretion/pull/169) | `@types/react-dom` | `^19.2.7` / 19.2.7 |
| [#170](https://github.com/santapong/Accretion/pull/170) | `pydantic-settings` | `>=2.15.0,<3` / 2.15.0 (already locked; minimum synchronized) |
| [#171](https://github.com/santapong/Accretion/pull/171) | `pydantic` | `>=2.13.5,<3` / 2.13.5; matching core 2.46.5 |
| [#172](https://github.com/santapong/Accretion/pull/172) | `pytest-cov` | `>=7.1.0,<8` / 7.1.0 |
| [#173](https://github.com/santapong/Accretion/pull/173) | `@vitejs/plugin-react` | `^6.1.1` / 6.1.1 |
| [#174](https://github.com/santapong/Accretion/pull/174) | `alembic` | `>=1.19.2,<2` / 1.19.2 |
| [#175](https://github.com/santapong/Accretion/pull/175) | `cryptography` | `>=50.0.1,<51` / 50.0.1 |
| [#176](https://github.com/santapong/Accretion/pull/176) | `@xyflow/react` | `^12.11.6` / 12.11.6; system 0.0.82 |
| [#177](https://github.com/santapong/Accretion/pull/177) | `vitest` | `^5.0.0` / 5.0.0 |
| [#178](https://github.com/santapong/Accretion/pull/178) | `globals` | `^17.12.0` / 17.12.0 |

The five Python PRs changed only `pyproject.toml`; their backend and
clean-checkout jobs failed. Applying those requirements without changing the
lock reproduced `uv lock --check` exit 1 (lockfile needs updating). A targeted
`uv lock --upgrade-package` for the five requested packages regenerated the
lock; `uv lock --check` and `uv sync --locked --all-groups` then passed.
The npm manifest requests were applied together and the workspace lock rebuilt.

Dependabot now uses `package-ecosystem: uv` for the Python project. GitHub
[documents uv as a supported ecosystem](https://docs.github.com/en/code-security/reference/supply-chain-security/supported-ecosystems-and-repositories).
This lets the updater own the actual project format and lockfile; the existing
lock check remains required. The weekly schedule, PR limits, target branch and
other ecosystems stay the same. A future scheduled bot execution is not yet
part of this validation.

## Security and major-version review

The first npm audit reported two high-severity entries caused by the same
`js-yaml` 4.3.1 dependency of `@redocly/openapi-core` 1.34.19, used by API type
generation. [GHSA-2883-xcg3-v3hh](https://github.com/advisories/GHSA-2883-xcg3-v3hh)
is patched in js-yaml 4.3.2. The latest available Redocly 1.x version at review
still pins 4.3.1, so a narrowly scoped root override selects 4.3.2 only under
`@redocly/openapi-core@1.34.19`. Explicitly updating that lock entry and running
`npm ci` installs 4.3.2; the final `npm audit` reports **zero vulnerabilities**.
Remove the override when an upstream update uses a patched parser. No broad
Redocly downgrade or major API-generator upgrade is included.

[Vitest 5](https://vitest.dev/blog/vitest-5.html) requires Node 22.12+ and Vite
6.4+. The locked version's engine range accepts the local Node 26.8.1 and CI's
Node 24; Vite is 8.2.1. The existing full UI suite passes with version 5 without
relaxing assertions or changing tests. Its related lock changes are confined
to the selected dependency trees.

[pytest-cov 7](https://pytest-cov.readthedocs.io/en/latest/changelog.html)
removed its implicit subprocess coverage hook. The normal repository tests
explicitly disable plugin autoload and do not claim subprocess coverage.
An additional smoke run explicitly loads `pytest_cov.plugin` against the
existing fake-pilot tests, with coverage 7.15.4. No coverage threshold is
weakened and no subprocess-coverage claim is introduced.

## Executed validation

The clean, rebased source candidate is
`1f029fb150e5fdb59ce01e7cab9c8013cc769318`. It was tested in its own locked
Python environment, with its import path verified, and an isolated disposable
PostgreSQL 16 database on loopback port 55442. Live provider tests were disabled.
The subsequent changes only record the merge and this validation.

| Check | Result |
|---|---|
| Alembic upgrade → downgrade to base → upgrade | All pass; head `0019_v04_m8_activation` |
| `make check` | Lock, Ruff, mypy (150 sources), docs, 21 schemas, ESLint and TypeScript pass |
| `make test` | 3,544 backend tests pass, six signed-in provider tests skip; 286 UI tests pass |
| `make acceptance` | 167 in scope, 159 Python-proven, zero unmet MUST; five frontend and three recorded manual criteria accounted for |
| `make release-gate` | All five conditions pass |
| `make future-sdd-check` | All 187 imported files unchanged; synthetic design conformance passes |
| Fake pilot dry run | PASS/FAIL/INCONCLUSIVE captured; zero provider calls |
| API generation and production build | Pass; generated TypeScript schema unchanged |
| Explicit pytest-cov smoke | 39 existing fake-pilot tests pass with the coverage plugin loaded |
| `npm audit` | Zero vulnerabilities at this inspection |

Original command output, timestamps and per-step exit codes are retained in
`/mnt/data/accretion-dependency-review-2026-09-09/evidence/20260909T024726Z/`.
Protected backend, frontend, browser and clean-checkout checks are required on
the final PR before integration; their GitHub records establish remote results.
Local tests are not substituted for those checks. The original coordinator
ended with exit 143 after the first twelve successful steps and before saving
the coverage step exit. That final step was rerun alone on the same clean
source: 39 tests passed, 90% coverage of the selected module, exit 0. The
interrupted record and confirmed rerun are both retained; the signal cause
was not established.

## Version and research disposition

The released version remains **v0.4.1**; this change does not promote `main` or
create a release tag. Contract schemas, migrations, acceptance criteria, routing
defaults, the frozen research protocol/amendment, its four-row access log and
the imported future SDD package are unchanged. Engineering completion does not
establish the full routing-benefit research claim: its scoped NO-GO and PARTIAL
paired synthetic evidence remain recorded. A priced live-provider study and
selected-tool AGENT capability extension retain their separate scope and gates.
