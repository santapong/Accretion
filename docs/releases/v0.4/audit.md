# v0.4.0 release audit

> **Draft.** This file exists so the release PR fills values rather than invents structure.
> Every cell marked *filled by the release PR* is a measurement nobody has taken yet; every
> other statement is sourced from the repository at the head of `develop` on 2026-09-06.
> **No release decision may be read out of this draft.**

> Audit date: *filled by the release PR* (Asia/Bangkok)
>
> Release-finalization base: *filled by the release PR*
>
> Decision: *filled by the release PR — this draft authorizes nothing.*

The v0.1.0, v0.2.0 and v0.3.0 tags remain immutable and are neither moved nor rewritten. No
`v0.3.1` tag exists and none is created: the v0.3.1 operator-UI ladder is parked after its
stylesheet port, and its merged work ships inside 0.4.0 under its own `CHANGELOG.md`
sub-heading.

## Candidate identity and environment

| Item | Audited value |
|---|---|
| Audited code commit | *filled by the release PR* |
| Audited tree | *filled by the release PR* |
| Integration branch before release | *filled by the release PR* |
| Stable branch before release | `main@bf5b774eb964252d448b44ec3ea9d6b7b7511213` (the v0.3.0 tag's peeled commit; unchanged since 2026-09-01) |
| Package metadata | `0.4.0` in `pyproject.toml`, the root and UI `package.json`, and `accretion.__version__` — **the release PR makes this true; no v0.4 milestone PR bumps a version** |
| Python | *filled by the release PR* |
| uv | *filled by the release PR* |
| Node.js / npm | *filled by the release PR* |
| PostgreSQL | *filled by the release PR* |
| Codex CLI | *filled by the release PR* |
| Claude Code | *filled by the release PR* |
| Browser | *filled by the release PR* |

The release PR promotes the audited work to `main` per
[branch-policy.md](../../governance/branch-policy.md); the tag is cut from `main` after merge.
If `main` and `develop` again have no usable merge ancestry, the protected release bridge from
that policy applies, exactly as it did at v0.2.0 and v0.3.0.

## SDD §24.8 release gate

The gate expression is unchanged from v0.3 and gains no condition. Its five expressions are
pinned to the SDD prose by `tests/test_v03_m8_release_gate.py`, and `docs/sdd/` is frozen;
v0.4 adds no credential surface, so its evidence belongs in the acceptance line and in
`docs/research/v0.4/results.md` rather than in a sixth condition.

```
release_v0_4 =
    all(MUST acceptance criteria pass)
    AND secret_exposure_incidents == 0
    AND capability_policy_bypass == 0
    AND connection_isolation_tests == PASS
    AND v0.1/v0.2 regression suite == PASS
```

`make release-gate`, run against a clean PostgreSQL container:

| Condition | Result | Evidence |
|---|---|---|
| all(MUST acceptance criteria pass) | *filled by the release PR* | target `in scope: 167   proven: 159   unmet MUST: 0` |
| `secret_exposure_incidents == 0` | *filled by the release PR* | |
| `capability_policy_bypass == 0` | *filled by the release PR* | |
| `connection_isolation_tests == PASS` | *filled by the release PR* | |
| v0.1/v0.2 regression suite == PASS | *filled by the release PR* | |

Two of these conditions remain derived from evidence rather than telemetry — the deliberate
decision recorded as ADR3-M8-002 in the
[release-hardening runbook](../../runbooks/v03-release-hardening.md). SDD §21's fourteen
metrics are still unimplemented, and v0.4 did not implement them.

## Automated checks

| Check | Result |
|---|---|
| `ruff check .` | *filled by the release PR* |
| `mypy src` | *filled by the release PR* |
| `scripts/check_docs.py` | *filled by the release PR* |
| `scripts/export_contract_schemas.py --check` | *filled by the release PR* — 21 contracts |
| `alembic upgrade head` → `downgrade base` → `upgrade head` | *filled by the release PR* — must be run on a clean database |
| `pytest` (with PostgreSQL) | *filled by the release PR* |
| `make acceptance` | *filled by the release PR* |
| `make release-gate` | *filled by the release PR* |
| `npm run check` | *filled by the release PR* |
| `npm run test` | *filled by the release PR* |
| `npm run build` | *filled by the release PR* — including the five bundle-budget rules |
| `npm run api:generate` + `git diff --exit-code` | *filled by the release PR* |
| `uv lock --check` | *filled by the release PR* — two CI jobs run it |

Two notes the release PR must not lose:

1. **The migration reversibility check must be run against a clean database.** Migration 0004
   refuses to downgrade a database holding `agent_events.node_id` values longer than 40
   characters, and migration 0020 refuses to downgrade while any `experience_records` row has
   `experience_id != id`. Both refusals are correct on a database with real data; CI uses a
   fresh container per run. The v0.4 revision chain is `0017` → `0018` → `0020` → `0019`, and
   `0019` is the single alembic head.
2. **Two `tests/test_acceptance_harness.py` node-id tests fail in a git worktree** because
   they read the checkout path. They pass in a normal checkout and in CI. Any run recorded
   here must be a normal checkout, not a worktree.

## Acceptance

| | Count |
|---|---:|
| Criteria in the four SDDs | 167 |
| Not yet due | 0 |
| **In scope** | **167** |
| Proven by a passing claiming test | 159 |
| Proven by the frontend suite | 5 |
| Proven by a recorded live-provider run (`manual`) | 3 |
| Uncovered | 0 |
| **Unmet MUST** | **0** |

**Target, not measurement.** 167 = the 117 criteria of the v0.1–v0.3 SDDs plus the fifty
`AC4-M<owner>-0NN` rows of SDD v0.4 §20; 159 = 167 less the **five** frontend and the three
manual criteria. Every row still recorded `not_yet_due` in
[`docs/acceptance/criteria.toml`](../../acceptance/criteria.toml) subtracts one from each
number; none is, since M10d.

This target was `161` in the drafts written before M9 landed, and that number was wrong: it
counted three `frontend` rows when the policy file carries five — `V01-P4-004`, `V02-P6-008`,
`V02-P7-007` and, since M9d, `AC4-M9-040` and `AC4-M9-043`. M9's two were subtracted from
neither total, so the target double-counted them as proven by pytest as well. The harness has
never been able to report 161; the corrected figure is 159 and the correction is recorded here
rather than absorbed. When the first draft was written the harness reported
`in scope: 155   proven: 149   unmet MUST: 0` with twelve rows outstanding; M7 closed three of
them the same day, M9d flipped `AC4-M9-040`, `-043` and `-044`, and M10d deleted
`AC4-M10-045`..`-050`. The release PR records the measured line and, if it is not
`167 / 159 / 0`, records why instead of adjusting the target.

### The three `manual` criteria

`V01-P0-002`, `V01-P0-004` and `V01-P4-008` describe signed-in vendor CLIs and cannot execute
in CI, which never sets `ACCRETION_LIVE_PROVIDERS=1`. They are proven by
[live-acceptance-2026-09-01.md](../v0.3/evidence/live-acceptance-2026-09-01.md) and **expire
on 2027-02-28**. That date is inside the supported life of 0.4.0, so the release PR must state
explicitly that it is not re-running them and that `make acceptance` will begin to fail on
that date until `scripts/live_acceptance.py` is re-run and `last_verified` is moved.

## Research evidence

v0.4 is the first release that makes a research claim, so its evidence is audited separately
from its engineering.

| Item | Audited value |
|---|---|
| [`docs/research/v0.4/results.md`](../../research/v0.4/results.md) | present since M10d. Every table on it is generated by `scripts/router_locked_test.py` and diffed against the page by `tests/test_v04_m10_locked_test.py`, so a number nobody can reproduce cannot survive the suite |
| Pre-registration sha256 (`docs/research/v0.4/preregistration.md`) | `6b45998b3a9847298ba878a6414211ce38d64c775e5addd587509fdd70cf04ea`, equal to the `preregistration_sha256` in `evals/router/config.v1.json` and in both locked corpora's configurations; `tests/test_v04_m10_preregistration.py` proves the pin |
| Pre-registration status | **FROZEN 2026-09-06.** All fifteen SDD §21 fields carry values; the locked-test runner refuses to start if the file on disk no longer hashes to the pin, and names both digests when it does |
| `TestSetAccessLog` row count at the audit | **2**, in [`access-log.jsonl`](../../research/v0.4/access-log.jsonl): one read of `evals/router/locked` and one of `evals/router/drift`. The drift holdout is a locked corpus and reading it is a read (ADR4-M10-001), so the audited value is two and not one; any value above 2 is a finding, not a footnote |
| Locked-test guard | present since M10d: `ACCRETION_ROUTER_LOCKED_TEST=1` through `Settings.router_locked_test`, beside the pre-registration digest check and a refusal of any `RouterTrainingSnapshot` naming a locked project in any of its three groups |
| Corpora the numbers come from | `evals/router/locked` (seed 20260906) and `evals/router/drift` (seed 20260907), 18 trials per cell and 3,888 traces each, byte-reproducible from `tests/router_corpus_generator.py` and sharing no lineage with `evals/router` or with each other |
| Estimands reported | G_out, G_Z and G_learn with Clopper–Pearson intervals at α/(K + L) = 0.05/9, K = 6 configurations and L = 3 policies. `g_learn` = 0.166667 with an interval spanning zero: **no binary superiority is claimed** |
| Primary analysis | the project-clustered paired regret contrast, M9 against the best fixed configuration: `[0.009603, 0.939517]` on the locked corpus and `[0.039887, 0.320070]` on the drift holdout, both excluding zero. This is the release's one superiority statement |
| Recovered fraction | **not reported.** The opportunity gap's lower limit is not positive, so the share recovered is not a number; the prior expectation from the confirmatory audit was 7.5–14.4% and this corpus cannot distinguish that from zero at the adjusted level |
| Safety gates | **both fail for every policy on both corpora**, as a property of the corpus's conservative trial pooling at the frozen size rather than of any router. Recorded as `ADR4-M10-005`, reported and not repaired |
| Priced real-provider run | **not run**, recorded as such on the results page rather than omitted |

## Disclosed limitations

Recorded here so the release is not read as claiming more than it proved. The operator-facing
version of this list is in [notes.md](notes.md).

1. **The priced real-provider routing run was not performed.** Every v0.4 measurement is a
   replay: over the seeded synthetic development corpus in `evals/router/` during construction,
   and over `evals/router/locked` and `evals/router/drift` for the numbers the release quotes.
   `results.md` records the priced run as **not run** rather than omitting the question.
2. **The benchmark is replay-only.** `RouterBenchmarkRunner` refuses any source but `REPLAY`
   at the route and again inside the runner, and ORACLE refuses outside `REPLAY`.
3. **The M7 exploration cost ledger is in-memory.** `routing/ledger.py` touches no store, no
   clock and no network; a restart forgets prior spend, and the absolute `ExplorationCaps`
   are what bound a long-lived deployment.
4. **Routed AGENT tool configurations fail closed** until the MCP boundary can honour an exact
   binding pin, and a crash after a durable dispatch claim is uncertain execution to be
   reconciled before retry — v0.4 does not claim exactly-once external execution.
5. **`exploration_policy` moved every pre-delta `ObjectiveContract` content hash.** ADR-056's
   canonical form keeps nulls, so an additive optional field is Minor by field list but not
   digest-neutral. No release contains migration 0017 (v0.3.0 ends at 0016) and nothing
   outside the test suite writes an `objective_contracts` row, so no stored document existed
   to break — but the fact is recorded rather than absorbed.
6. **`DRIFT` reads no temporal or provider key yet.** The drift split is allocated by the same
   seeded root hash as every other split; era-aware separation is M10d's.
7. **OQ-419 is deferred** to protocol publication preparation, so the benchmark ships under
   its internal name only.
8. **The four v0.3 deferrals are still deferred** and enumerated in [backlog.md](backlog.md):
   workspace-shared and `SERVICE_ACCOUNT` enterprise authorization, session enumeration in the
   identity page, real identity-provider interoperability as an expiring manual criterion, and
   the token-exchange egress allowlist.
9. **The v0.3.1 operator-UI ladder is parked** after its stylesheet port; its remaining steps
   are listed in [backlog.md](backlog.md) rather than implied to be shipped.

## Release procedure

1. Merge the remaining v0.4 milestone PRs into `develop` (M9d, then M10c and M10d).
2. Open the release PR against `develop`: bump `pyproject.toml`, the root `package.json` and
   `apps/ui/package.json` to `0.4.0`, run `npm install --package-lock-only` and `uv lock`,
   convert `## [Unreleased]` to `## [0.4.0] - <date>` in the v0.3 header shape — theme line,
   links to these notes and this audit, the counts line and the five-of-five gate — and fill
   every *filled by the release PR* cell above.
3. Open `develop` → `main` and squash-merge, using the protected release bridge from
   [branch-policy.md](../../governance/branch-policy.md) if the two branches have no usable
   merge ancestry.
4. Tag `v0.4.0` on `main`, then verify the tag's peeled commit matches the merged `main` tip
   and that `git diff --exit-code origin/develop origin/main` passes.
5. Write `docs/releases/v0.4/baseline.md` recording the frozen identity.

Steps 2 through 5 are the release PR's, not this draft's. Nothing in this file is a release
authorization.
