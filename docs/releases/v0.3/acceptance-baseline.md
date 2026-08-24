# Acceptance baseline

> Computed: 2026-08-25 · Base: `develop` at `519ab2b`
>
> Produced by `make acceptance`. This document records a **starting position**, not a
> release decision. It supersedes hand-written status claims.

## Why this exists

Until now a criterion was "met" because a document said so. The v0.1 and v0.2 release
audits were careful and honest, but they are point-in-time human claims, and three
independent audits of this tree found that several had drifted from what the tests
actually prove.

`scripts/check_acceptance.py` computes the status instead. The three SDDs remain the
source of truth for *what* the criteria are; `docs/acceptance/criteria.toml` records
only *how* each is verified; and a test claims a criterion with
`@pytest.mark.acceptance("<id>")`. Nothing is met because someone believes it.

## The numbers

| | Count |
|---|---:|
| Criteria in the three SDDs | 110 |
| Not yet due (M3–M6) | 22 |
| **In scope** | **88** |
| Proven by a passing claiming test | 37 |
| Uncovered | 51 |

Coverage is 42% of in-scope criteria. That is the honest figure; it was previously
unknown rather than better.

## What "uncovered" does and does not mean

**It mostly does not mean broken.** Three audits ran against this tree:

| Release | MET | PARTIAL | UNMET |
|---|---:|---:|---:|
| v0.1 (P0–P4, BENCH) | 33 | 5 | 0 |
| v0.2 (P5–P7, UI) | 18 | 15 | 0 |
| v0.3 (M0–M2) | 5 | 6 | 4 |

**No inherited criterion is unimplemented.** The v0.1 suite is intact — no v0.1-era
test or source file was deleted between `v0.1.0` and HEAD — and the v0.2 P6 isolation,
budget, promotion, and P7 replay-safety suites are genuinely adversarial. The gap in
P0–P7 is almost entirely *traceability*: passing tests exist but nothing tied them to
a criterion, so a regression would not have named what it broke.

Only criteria the audits scored **MET** were annotated. A PARTIAL criterion is left
uncovered on purpose — marking it would make this report claim PROVEN, which is the
failure mode the harness exists to prevent.

## Findings that are not merely missing coverage

These need work, not annotation.

**Security**

- `V01-P4-001` — sandbox asymmetry. Codex runs network-denied
  (`sandbox_workspace_write.network_access = False`); Claude does not, and its
  `--allowedTools` list includes `Bash(uv run*)` and `Bash(npm run*)` under
  `--permission-mode dontAsk`, so `uv run python -c "<network call>"` matches the
  prefix. No test attempts a denied capability through a native shell escape.
- `AC3-CON-06` — the audience guard is fail-open. `if audience and handle.audience`
  skips validation entirely when either side is empty, and `handle.audience` is empty
  whenever a connector has no `resource_server`. `TokenHandle.issuer` is written and
  read by nothing, so the issuer half is unimplemented.
- `AC3-SEC-03` — the token broker is unwired. `EncryptedTokenBroker` has zero call
  sites in `src/`; `CapabilityGateway.execute` still resolves credentials through the
  v0.2 env-var `CredentialBroker`, and the resolved connection is discarded.
- `command_result` passes no child environment, so every runtime health probe inherits
  the full control-plane environment including the database URL and OIDC client secret.

**Evidence integrity**

- `V01-BENCH-005` — `config.v1.json` and `environments.v1.json` are unhashed and
  untested, while P5/P6/P7 pin theirs. Silent edits would change published utility and
  regret with no test failure.
- The P5 and P7 fixture pins are tautological: they assert
  `first.corpus_sha256 == digest(runner.tasks_path)`, hashing the file just read. Only
  P6 asserts literal digests. The values printed in the acceptance documents have no
  automated guard.
- `V02-P7-003` and `V02-P7-008` — the benchmarks replay hand-authored fixtures rather
  than system output. The headline "19/20 stale sources rejected" counts a literal
  field in `sources.v1.json`; `ExperienceService.assess()` is never invoked, and 16 of
  its 19 rejection codes have no test. The replay uplift is arithmetic over typed
  numbers. The documents do disclaim this, but the acceptance tables read as
  measurement.

**Specification mismatches**

- `V02-UI-003` — the diff renders counts only, never identities, and no edge diff at
  all. The criterion says "accurately shows added/removed/replaced nodes/edges".
- `V02-UI-006` — neither `fallback_order` nor `observed_features` is rendered
  anywhere, though the criterion and the P5 acceptance document both claim the UI
  exposes them.
- `V02-P5-005` — the test named for privilege expansion asserts only
  `UNKNOWN_CAPABILITY` and short-circuits before the privilege branch. No test anywhere
  asserts `PRIVILEGE_EXPANSION`, `DENIED_CAPABILITY`, or `RISK_EXPANSION`.

**Test hygiene**

- `tests/test_p5_postgres_store.py::test_p5_postgres_records_round_trip_and_remain_immutable`
  is not isolated and fails on a second run against the same database. CI is green only
  because it uses a fresh container each run.
- No live-provider criterion is enforced by CI: `tests/test_live_runtimes.py` is gated
  on `ACCRETION_LIVE_PROVIDERS=1`, which the workflow never sets. `V01-P0-002` and
  `V01-P0-004` rest on manual runs recorded against earlier commits.
- All three P5–P7 feature flags default to `False`, so in a stock deployment none of
  the behaviour those 23 criteria describe is reachable.

## What happens next

Phase 2 closes the v0.3 M0–M2 gaps, Phase 3 the inherited ones, Phase 4 gates
`make acceptance` in CI. Each criterion closed gains a claiming test, so this table
moves on evidence rather than assertion.

Re-run at any time:

```bash
make acceptance                 # full: runs the suite, reports per criterion
uv run python scripts/check_acceptance.py --no-tests --stage M2
```
