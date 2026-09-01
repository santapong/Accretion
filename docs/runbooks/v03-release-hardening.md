# v0.3 release-hardening runbook (M8)

How v0.3 M8 turns the release gate from prose into something executable, and how
it closed the ten inherited unmet MUSTs that stood between `develop` and a
tagged v0.3.0. The normative contract is
[Accretion SDD v0.3](../sdd/Accretion_SDD_v0.3.md) §24.8 and §27 M8.

M8 has **no acceptance criteria of its own**, and deliberately gets no
`--stage M8`: a stage gate over an empty scope prints PASS, which is exactly the
failure mode this milestone exists to remove. Its exit condition is instead:

- `make acceptance` reports `unmet MUST: 0` and exits PASS;
- `make release-gate` evaluates all five §24.8 conditions and exits PASS;
- CI gates the **full** harness rather than eight stage-scoped subsets;
- the result reproduces from a clean checkout.

All four hold as of `fa0a63e`.

## What the milestone actually found

Ten inherited MUSTs were unmet at the start of M8. **Seven of them needed a
claiming test, not a behaviour change.** The graph validator's cycle, fan-out,
denied-capability, privilege-expansion and risk-expansion branches; the six
search stop reasons; the N=1,2,4 quality curve; and the benchmark version axes
were all implemented already and simply unclaimed, so a regression in any of
them would not have named what it broke.

That ratio is the milestone's main finding. The inherited gap was traceability,
not capability — with one exception, `V02-P7-003`, which was the only criterion
in the whole set that required a `src/` change.

## The numbers

| | Before M8 | After M8 |
|---|---:|---:|
| In scope | 117 | 117 |
| Proven by a claiming test | 103 | 111 |
| Proven by the frontend suite | 3 | 3 |
| Proven by a recorded live run | 0 | 3 |
| Uncovered | 11 | **0** |
| Unmet MUST | 10 | **0** |
| Coverage | 91% | **100%** |

## Decisions

### ADR3-M8-001 — the harness must fail closed before it is trusted to gate

**Context.** M8 makes CI depend on `check_acceptance.py` alone. Before widening
its authority, the harness's own escape hatches were audited and two were found
to fail *open*: `_expired()` returned `False` for a value that is not a date, so
`expires = "v0.4.0"` was a permanent waiver; and a `manual` or `waived`
criterion whose claiming test failed still reported its recorded verdict rather
than the failure.

**Decision.** The harness fails closed. An unreadable end date counts as already
expired; a waiver needs an ISO date no more than 180 days out; a failing claimed
test outranks any recorded belief; and a criterion whose test reports no outcome
at all — a fixture that raised during setup, a node collected but never run —
classifies `FAILING` rather than `PROVEN`.

**Consequences.** Shipped as PR0, before any criterion was claimed, so every
later claim in this milestone was measured by the stricter harness.

### ADR3-M8-002 — the §24.8 counters are derived from evidence, not telemetry

**Context.** §24.8 names `secret_exposure_incidents == 0` and
`capability_policy_bypass == 0`. SDD §21 declares fourteen metrics and
implements none. Building them would introduce OpenTelemetry, which would then
have to be added to the secret-scan surface list that AC3-EMA-05 and AC3-SEC-05
enumerate — new exposure surface during a release freeze, to measure a number
that is supposed to be zero.

**Decision.** Derive both from evidence that already exists.
`secret_exposure_incidents` is the failure count of the secret-scan suites,
which walk every surface a credential could reach. `capability_policy_bypass` is
computed by `capability_policy_bypasses()` in `scripts/release_gate.py` from
`CapabilityGateway` audit rows: a bypass is a call whose authorization refused
it — `DENY`, or `REQUIRE_APPROVAL` with no approval recorded — that nonetheless
reached the backend, or an execution with no policy recorded at all. `FAILED`
and `UNKNOWN` count as executed, because a call that may have run and cannot be
shown not to have is not evidence of no bypass.

**Consequences.** Both conditions are independently failable today. Real
telemetry stays a v0.4 question.

### ADR3-M8-003 — the P5/P6/P7 benchmarks are replay, and say so

**Context.** The benchmark documents read as measurement. They are replays of
frozen traces, and the acceptance baseline recorded the P5 and P7 fixture pins
as tautological: `first.corpus_sha256 == digest(runner.tasks_path)` hashes the
file it just read.

**Decision.** Keep replay — a live experiment is not reproducible in CI — but
make the pins literal and prove derivation separately. Every published point is
asserted against a literal value, and each is then shown to be *computed* by
perturbing one trace in a `tmp_path` copy of the corpus and asserting that the
expected number moves while the others hold still.

**Consequences.** `V02-P6-009` and `V02-P7-008` are proven without either
document claiming to be an experiment it is not.

### ADR3-M8-004 — benchmark fixture digests are pinned in tests, not on the row

**Context.** `V01-BENCH-005` requires configuration and task environments to be
versioned independently. `config.v1.json` and `environments.v1.json` were
unhashed and untested while the task and trace corpora were pinned. The natural
fix — surfacing both digests on `BenchmarkRun` — turns out to be expensive:
`BenchmarkRun` is an immutable persisted `StrictModel` seeded on every API
start, so new fields mean a migration, an id-derivation change and an
immutability-comparison change.

**Decision.** Tests only for v0.3. All four fixtures carry literal digests, and
each of the three version axes is bumped alone in a `tmp_path` corpus copy to
demonstrate independence, with the task/environment cross-check pinned in both
directions so independence cannot become undetected drift.

**Consequences.** Surfacing the digests on the run is a v0.4 item.

### ADR3-M8-005 — the P7 stale-rejection figure gains an assessed path, not a rewrite

**Context.** The headline "19/20 stale sources rejected" counted a literal
`retrieval_outcome` field in `sources.v1.json`; `ExperienceService.assess()` was
never invoked, and 16 of its 19 reason codes had no test. Making the benchmark
genuinely call `assess()` requires an async runner holding a store, a verifier
registry and a git repository, plus extending `sources.v1.json` with the data an
assessment needs — contracts, fixtures and two API routes, during a freeze.

**Decision.** Split the criterion from the document. `V02-P7-003` is a claim
about the **system**, so it is proven directly against the real `assess()`: all
19 reason codes provoked by distinct single-variable perturbations of one
compatible pair, plus a guard test that fails if a code is added without
coverage. Separately, `ExperienceBenchmarkRunner.run()` gains an optional
`stale_assessor`, and the gate reports `stale_rejection_source` as `DECLARED` or
`ASSESSED`, treating the declared outcome as a pin that raises on disagreement.

**Consequences.** The criterion is honestly proven; the *document* is not yet
fully derived, because the two API routes still take the `DECLARED` path and now
say so on the gate. Tracked for v0.4 under "P7 experience benchmark provenance"
in [backlog.md](../releases/v0.3/backlog.md).

A limit worth stating plainly: while the declared outcome is a hard pin, the
assessed count and the declared count are necessarily equal, so no passing test
can distinguish counting one from counting the other — they are equivalent
implementations. What is falsifiable, and is proven, is that every stale source
reaches the assessor and that disagreement is fatal.

### ADR3-M8-006 — three criteria are `manual`, and no fake binary may claim them

**Context.** `V01-P0-002`, `V01-P0-004` and `V01-P4-008` are claims about real
vendor CLIs. CI never sets `ACCRETION_LIVE_PROVIDERS=1`, so a test claiming them
would report a skip — and per ADR3-M8-001 a claimed test with no outcome now
classifies `FAILING`, which is correct.

**Decision.** Record them as `manual`, pointing at
[live-acceptance-2026-09-01.md](../releases/v0.3/evidence/live-acceptance-2026-09-01.md),
produced by `scripts/live_acceptance.py` against signed-in CLIs. The offline
protocol guards in `tests/test_v03_m8_live_protocol.py` carry **no** acceptance
marker: a stub can show the runtime speaks its protocol, but marking it would
let a fake binary impersonate a vendor.

**Consequences.** The records expire after 180 days. Re-run the script and move
`last_verified` before **2027-02-28**.

## Running the gates

```bash
make acceptance        # every criterion; PASS requires unmet MUST: 0
make release-gate      # the five SDD 24.8 conditions, each separately failable
```

`scripts/release_gate.py --json` emits the same verdict machine-readably.

Both need a database. Use a **clean** one: `alembic downgrade base` refuses to
run against a database holding real run data, because migration 0004 guards
`agent_events.node_id` values longer than 40 characters. That guard is correct;
CI uses a fresh container per run, and so should any local reproduction.

```bash
docker run -d --name accretion-verify \
  -e POSTGRES_DB=accretion -e POSTGRES_USER=accretion -e POSTGRES_PASSWORD=accretion \
  -p 127.0.0.1:5437:5432 pgvector/pgvector:0.8.6-pg16
export ACCRETION_DATABASE_URL=postgresql+asyncpg://accretion:accretion@localhost:5437/accretion
export ACCRETION_TEST_POSTGRES_URL=$ACCRETION_DATABASE_URL
uv run --no-sync alembic upgrade head
make acceptance && make release-gate
```

With Postgres configured the suite reports 634 passed / 6 skipped. Without it,
605 passed / 32 skipped — 25 integration tests skip silently, which is why the
release gate is run against a real database rather than a bare checkout.

## What CI enforces after M8

The eight `check_acceptance.py --stage` lines are gone. Every criterion they
covered is in scope for the single unscoped run that replaces them, which is
both broader and stricter: a stage gate prints PASS over an empty scope
(`in scope: 0`), and the unscoped harness cannot. Each stage line also re-ran
the whole suite, so the cutover reduces CI work substantially.

The standalone `pytest` step is kept on purpose. The harness decides criteria
and does not fail on a test that claims none, so without it a regression in
unclaimed code would go unreported behind a green gate.

A `clean-checkout` job shares nothing with `backend` — caching disabled, fresh
database, API surface regenerated from source and diffed — so anything that only
works on a developer's machine fails before a tag is cut rather than after.

`--stage` itself still exists and remains a useful local diagnostic; it is
simply no longer what gates the repository.

## Accessibility

The four inherited findings F1–F4 are closed, with axe-core reporting zero
violations across all seventeen routes. Two of the findings were partly
misdiagnosed, and the corrections are recorded in
[browser-a11y-evidence.md](../releases/v0.3/browser-a11y-evidence.md) rather
than quietly fixed: the status-pill palette always passed AA, and the five
`/admin/*` pages already had an `h1`. A new finding — the M6 admin pages
scrolling the document sideways at 390 px — was found and fixed in the same
pass.
