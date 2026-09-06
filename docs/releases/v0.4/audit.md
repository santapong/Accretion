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
| all(MUST acceptance criteria pass) | *filled by the release PR* | target `in scope: 167   proven: 161   unmet MUST: 0` |
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
| Proven by a passing claiming test | 161 |
| Proven by the frontend suite | 3 |
| Proven by a recorded live-provider run (`manual`) | 3 |
| Uncovered | 0 |
| **Unmet MUST** | **0** |

**Target, not measurement.** 167 = the 117 criteria of the v0.1–v0.3 SDDs plus the fifty
`AC4-M<owner>-0NN` rows of SDD v0.4 §20; 161 = 167 less the three frontend and the three
manual criteria. Every row still recorded `not_yet_due` in
[`docs/acceptance/criteria.toml`](../../acceptance/criteria.toml) subtracts one from each
number. When this draft was written the harness reported
`in scope: 155   proven: 149   unmet MUST: 0` with twelve rows outstanding; M7 closed three of
them the same day. M9d flips `AC4-M9-040`, `-043` and `-044`; M10d deletes
`AC4-M10-045`..`-050`. The release PR records the measured line and, if it is not
`167 / 161 / 0`, records why instead of adjusting the target.

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
| `docs/research/v0.4/results.md` | *filled by the release PR* — the file is created by M10d and does not exist in this draft |
| Pre-registration sha256 (`docs/research/v0.4/preregistration.md`) | *filled by the release PR*, and it must equal the `preregistration_sha256` recorded in the benchmark configuration |
| Pre-registration status | **PENDING FREEZE** in this draft: all fifteen SDD §21 fields carry proposals and remain `TBD` |
| `TestSetAccessLog` row count at the audit | *filled by the release PR* — under ADR-064 the locked test set is readable **once**, so any value above 1 is a finding, not a footnote |
| Locked-test guard | `ACCRETION_ROUTER_LOCKED_TEST` does not exist in this draft; M10d adds it beside the pre-registration digest check |
| Estimands reported | G_out, G_Z and G_learn with Clopper–Pearson intervals and Bonferroni across the K fixed configurations and L router policies |
| Recovered fraction | reported only when the lower limit of the opportunity gap is positive; the honest prior expectation is **7.5–14.4%** |

## Disclosed limitations

Recorded here so the release is not read as claiming more than it proved. The operator-facing
version of this list is in [notes.md](notes.md).

1. **No priced real-provider routing run is recorded.** Every v0.4 measurement is a replay
   over the seeded synthetic development corpus in `evals/router/`. The release PR records the
   priced run or records that it was not run; it does not omit the question.
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
