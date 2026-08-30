# Acceptance baseline

> Computed: 2026-08-25 · Base: `develop` at `519ab2b`
> Numbers refreshed: 2026-08-29 · `develop` at `3e7f9c1` plus M6 (frontend and administration)
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
| Not yet due | 0 |
| **In scope** | **110** |
| Proven by a passing claiming test | 96 |
| Proven by the frontend suite | 3 |
| Uncovered | 11 |

Coverage is 90% of in-scope criteria (99 of 110). The first computed figure was 37;
the annotation sweep raised it to 59 without changing any runtime behaviour, because
the inherited gap was traceability rather than implementation. Putting the token broker
into the execution path then closed five more (#75); completing M2 closed six (#77);
closing the Claude egress asymmetry closed `V01-P4-001` (#78); M3 brought seven
MCP criteria into scope and proved all of them (#79); M4 brought the six AC3-PLG
plugin-manager criteria into scope and proved all of them; M5 brought the four
AC3-RES research criteria into scope and proved all of them; and M6 brought the five
AC3-UI criteria into scope, proved all of them, and additionally closed the six
inherited `V02-UI-001..006` run-page criteria. **Nothing is `not_yet_due` any more** —
every criterion in the three SDDs is now in scope. All 11 uncovered criteria are
inherited v0.1/v0.2 items.

Reproduce this table with `make acceptance`; the run behind these numbers reported
`PROVEN: 96`, `FRONTEND: 3`, `UNCOVERED: 11`, no `NOT_YET_DUE` bucket at all, and
`in scope: 110   proven: 96   unmet MUST: 10`. The full harness still exits `FAIL`
because those 10 unmet MUSTs are the inherited v0.1/v0.2 items below; the per-stage
gates `--stage M1` through `--stage M6` plus `--stage v0.2-ui` all pass and are what CI
enforces today, each reporting `unmet MUST: 0` over 5, 11, 8, 6, 4, 5 and 6 in-scope
criteria respectively.

### What M6's numbers do and do not measure

`FRONTEND` stayed at 3 while five AC3-UI and six V02-UI criteria were proven, which
looks wrong and is not. `FRONTEND` counts criteria whose *only* proof is vitest. M6's
criteria are each claimed by a marked pytest test that proves the API half in-process
against the real managers — a really-installed package's version and state, a
really-opened circuit breaker, a resolution checked against the store, a capability
really executed through `CapabilityGateway` — and carry the vitest test proving the
rendering half as a `frontend_evidence` pointer that `acceptance.py` checks by path,
line and test title. They are therefore counted under `PROVEN`. The three that remain
`FRONTEND` are the pre-existing `V01-P4-004`, `V02-P6-008` and `V02-P7-007`. See
ADR3-M6-001 in the
[frontend and administration runbook](../../runbooks/v03-frontend-admin.md).

What the numbers do **not** establish is anything about layout or contrast. jsdom has no
layout engine and `apps/ui/src/test/setup.ts` fakes `offsetWidth` and
`getBoundingClientRect` with constants, so F1 and F2 below remain open against browser
and axe evidence rather than against this suite.

### What M5's numbers do and do not measure

The four AC3-RES criteria are proven against `tests/fake_research_api.py`, an
in-process fake literature service. Everything between that fake and each assertion is
the real thing — the real MCP client over `ASGITransport`, M3's real `RemoteMcpManager`
behind its real endpoint policy, the real `PluginManager` installing the bundled
package from disk, and the real `CapabilityGateway` — and no test in the suite touches
the network. What the numbers therefore establish is that the *pipeline* behaves
correctly, not that any real literature source has been queried. This is also why M5
shipped no research benchmark: a benchmark over these connectors would report the
quality of the fixture in the register of a system measurement, which is the exact
failure this document exists to prevent.

### Movement since the baseline

Findings below that were open at `519ab2b` and are now closed, so a reader does not
act on them twice: `V01-P4-001` (#78); `AC3-CON-06` and `AC3-SEC-03` (#75, #77); and the
two specification mismatches `V02-UI-003` and `V02-UI-006`, closed by M6 — the graph diff
now names every added, removed and changed node *and edge* rather than rendering counts,
and the router inspector renders `fallback_order` and `observed_features`. Both are proven
against the real planner, the real replan path and the production runtime router, whose
output is committed as the vitest fixture. The remaining findings stand, including the
`command_result` child-environment inheritance.

Three criteria are proven by **vitest** rather than pytest. This gate reads pytest
markers, so those record a pointer to the test that proves them; `npm run test` runs
that suite in CI on every pull request. They are reported as `FRONTEND` rather than
`PROVEN` so a reader can tell which runner actually executed them.

## What "uncovered" does and does not mean

**It mostly does not mean broken.** Three audits ran against this tree:

| Release | MET | PARTIAL | UNMET |
|---|---:|---:|---:|
| v0.1 (P0–P4, BENCH) | 33 | 5 | 0 |
| v0.2 (P5–P7, UI) | 18 | 15 | 0 |
| v0.3 (M0–M2) | 5 | 6 | 4 |

Those audits predate M3–M5; the v0.3 row is a historical record of the tree at
`519ab2b`, not a current status. The table above is the current status.

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

- ~~`V02-UI-003` — the diff renders counts only, never identities, and no edge diff at
  all.~~ Closed by M6: the diff names every added, removed and changed node and edge, and
  a rollback diff renders its own identities rather than the previous diff's.
- ~~`V02-UI-006` — neither `fallback_order` nor `observed_features` is rendered
  anywhere.~~ Closed by M6: the router inspector lists the fallback order and every
  observed feature, and renders only the fields the decision declares — never a credential.
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

CI gates the milestone stages rather than the full harness: the backend job runs
`--stage M1` through `--stage M6` and `--stage v0.2-ui`, each of which passes today. The
full `make acceptance` gate stays M8's job, because it cannot pass until the 10
inherited unmet MUSTs above are closed.

Re-run at any time:

```bash
make acceptance                 # full: runs the suite, reports per criterion
uv run python scripts/check_acceptance.py --stage M6
uv run python scripts/check_acceptance.py --no-tests --stage M2
```
