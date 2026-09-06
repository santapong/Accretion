# v0.3.0 release audit

> Audit date: 2026-09-01 (Asia/Bangkok)
>
> Release-finalization base: `feat/v03-m8` at `fa0a63e415d2a1b785e7cd776fd22d536baef177`
>
> Decision: **RELEASE CANDIDATE AUTHORIZED — no maintainer exception required.**

> Released: `v0.3.0` on 2026-09-01 from
> `bf5b774eb964252d448b44ec3ea9d6b7b7511213` (annotated tag object
> `6d20bc6a3b4df4ba2f01920b3717b4cf3c69a2e0`); see the
> [frozen baseline](baseline.md) and
> [published release](https://github.com/santapong/Accretion/releases/tag/v0.3.0).

Unlike v0.2.0, this candidate carries no accessibility exception: a real browser
was available, and axe-core reports zero violations across all seventeen routes.
Issue [#52](https://github.com/santapong/Accretion/issues/52), which tracked the
v0.2 exception, is discharged by
[browser-a11y-evidence.md](browser-a11y-evidence.md).

The v0.1.0 and v0.2.0 tags remain immutable and are neither moved nor rewritten.

## Candidate identity and environment

| Item | Audited value |
|---|---|
| Audited code commit | `fa0a63e415d2a1b785e7cd776fd22d536baef177` |
| Audited tree | `7b0815b137a204364ec4760d77503554680f8dc6` |
| Integration branch before release | `develop@0cb5cbcf69d58458c0aa5e3519c66b006d4a6cd2` |
| Stable branch before release | `main@de146cd9e1a3e651e066f8dde020c7938cbc1316` |
| Package metadata | `0.3.0` (pyproject, root and UI `package.json`, `accretion.__version__`) |
| Python | `3.12.9` in the locked project environment |
| uv | `0.12.5` |
| Node.js / npm | `26.8.1` / `11.19.0` |
| PostgreSQL | pgvector `0.8.6-pg16`, disposable release database |
| Codex CLI | `codex-cli 0.148.0` (subscription auth) |
| Claude Code | `2.1.252` |
| Browser | Chromium 151, axe-core 4.10.2 |

The audited commit is the tip of `feat/v03-m8`, which carries the ten M8
checkpoint commits ahead of `develop`. The release PR promotes that work to
`develop` and then to `main`; the tag is cut from `main` after merge, per
[branch-policy.md](../../governance/branch-policy.md).

## SDD §24.8 release gate

```
release_v0_3 =
    all(MUST acceptance criteria pass)
    AND secret_exposure_incidents == 0
    AND capability_policy_bypass == 0
    AND connection_isolation_tests == PASS
    AND v0.1/v0.2 regression suite == PASS
```

`make release-gate`, run against a clean PostgreSQL container:

| Condition | Result | Evidence |
|---|---|---|
| all(MUST acceptance criteria pass) | **PASS** | `in scope: 117   proven: 111   unmet MUST: 0` |
| `secret_exposure_incidents == 0` | **PASS** | 2 suites, 6 passed |
| `capability_policy_bypass == 0` | **PASS** | 2 suites, 21 passed |
| `connection_isolation_tests == PASS` | **PASS** | 4 suites, 53 passed |
| v0.1/v0.2 regression suite == PASS | **PASS** | 8 suites, 87 passed |

Two of these conditions are derived from evidence rather than telemetry, which
is a deliberate decision recorded as ADR3-M8-002 in the
[release-hardening runbook](../../runbooks/v03-release-hardening.md) — SDD §21's
fourteen metrics are unimplemented, and building them would add OpenTelemetry to
the secret-scan surface list during a freeze.

## Automated checks

| Check | Result |
|---|---|
| `ruff check .` | PASS |
| `mypy src` | PASS — 87 source files |
| `scripts/check_docs.py` | PASS — 83 Markdown files, 18 SVGs |
| `alembic upgrade head` → `downgrade base` → `upgrade head` | PASS on a clean database |
| `pytest` (with PostgreSQL) | PASS — 634 passed, 6 skipped |
| `make acceptance` | PASS — `unmet MUST: 0` |
| `make release-gate` | PASS — five of five conditions |
| `npm run check` | PASS |
| `npm run test` | PASS — 97 tests |
| `npm run build` | PASS |
| `npm run api:generate` + `git diff --exit-code` | PASS — generated schema reproduces |

The migration reversibility check must be run against a **clean** database.
Migration 0004 refuses to downgrade a database holding `agent_events.node_id`
values longer than 40 characters, so a development database with real run data
correctly fails it. CI uses a fresh container per run.

## Acceptance

| | Count |
|---|---:|
| Criteria in the three SDDs | 117 |
| Not yet due | 0 |
| **In scope** | **117** |
| Proven by a passing claiming test | 111 |
| Proven by the frontend suite | 3 |
| Proven by a recorded live-provider run (`manual`) | 3 |
| Uncovered | 0 |
| **Unmet MUST** | **0** |

Full derivation and per-criterion status:
[acceptance-baseline.md](acceptance-baseline.md).

### The three `manual` criteria

`V01-P0-002`, `V01-P0-004` and `V01-P4-008` describe signed-in vendor CLIs and
cannot execute in CI, which never sets `ACCRETION_LIVE_PROVIDERS=1`. They are
proven by a real recorded run —
[live-acceptance-2026-09-01.md](evidence/live-acceptance-2026-09-01.md) — which
observed two Codex threads with distinct native run ids on one App Server,
Claude and Codex dispatched concurrently into disjoint worktrees sharing no
working-tree path, and an artifact written by Claude and independently verified
by Codex in a separate session.

**These records expire on 2027-02-28.** After that date `make acceptance` fails
until `scripts/live_acceptance.py` is re-run and `last_verified` is moved. That
is intentional: an audit records what someone believed on one day, and this file
makes every belief carry an end date.

## Disclosed limitations

Recorded here so the release is not read as claiming more than it proved.

1. **The research benchmarks are replays.** P5, P6 and P7 replay frozen traces.
   Figures are pinned literally and shown to be derived by perturbation, but no
   live experiment was run (ADR3-M8-003).
2. **The P7 stale-rejection figure served by the API is corpus-declared.** The
   criterion is proven against the real assessor across all 19 reason codes; the
   benchmark's two routes still report `stale_rejection_source: DECLARED` and
   say so on the gate (ADR3-M8-005). Tracked for v0.4.
3. **Benchmark fixture digests are pinned in tests, not surfaced on
   `BenchmarkRun`.** Adding them would require a migration and an
   id-derivation change during a freeze (ADR3-M8-004).
4. **Health probes inherit the parent environment.** `command_result` passes no
   child environment, so a runtime health probe inherits the control-plane
   environment. Pinned by a test and tracked for v0.4.
5. **Deferred features are enumerated, not implied.** Audit routes, plugin
   dry-run, bundle split, session-enumeration UI, egress allowlist,
   `consent_records` / `scope_grants`, and the plugin health scheduler are
   listed in [backlog.md](backlog.md).

## Release procedure

1. ~~Open `feat/v03-m8` → `develop` and squash-merge.~~ Done —
   [#102](https://github.com/santapong/Accretion/pull/102), `develop` at
   `0bf1d747eb9428efe13d8e71b13e3866e9cebb92`.
2. ~~Open `develop` → `main` and squash-merge.~~ Done —
   [#103](https://github.com/santapong/Accretion/pull/103). `main` and `develop`
   had no usable merge ancestry, as at v0.2.0, so the protected release bridge
   from [branch-policy.md](../../governance/branch-policy.md) was used: branch
   `release/v0.3.0` created from `main` at `b849ca0`, its complete tree replaced
   with the audited `develop` tree, and `git diff --exit-code origin/develop
   release/v0.3.0` verified to pass before the pull request was opened.
3. ~~Tag `v0.3.0` on `main`.~~ Done — annotated tag object
   `6d20bc6a3b4df4ba2f01920b3717b4cf3c69a2e0`.
4. ~~Verify the tag's peeled commit matches the merged `main` tip.~~ Verified —
   both are `bf5b774eb964252d448b44ec3ea9d6b7b7511213`, and
   `git diff --exit-code origin/develop origin/main` passes, so the promoted
   tree `902c5c75aa899ecb2306cf26696bbccb867fc797` is byte-identical to the
   audited one.
5. ~~Write `docs/releases/v0.3/baseline.md`.~~ Done — see
   [the frozen v0.3 baseline](baseline.md).

All required CI checks — `backend`, `frontend`, and the new `clean-checkout`
job — passed on both pull requests and again on `main` after the merge. The
`v0.1.0` and `v0.2.0` tag objects and peeled commits are unchanged from the
values recorded in their own baselines.
