# Accretion v0.4.0 release notes

> **Released historical record.** The v0.4.0 measurements refer to `develop`
> `cea73eb1eeb6e1cdeb7513de5acff3b085c16542` (#156, M10 closed), audited on
> 2026-09-07. The v0.4.1 addendum below has its own dated evidence. Both releases
> and their actual tag identities are recorded in the [frozen baseline](baseline.md).
> Documentation clarifications dated 2026-09-08 do not claim a new test or provider run.

Version: `v0.4.0`. Theme: **Evidence-aware node configuration routing**.

v0.4.0 turns the v0.3 integration platform into a control plane that *chooses* how each node
runs and then has to defend the choice. A node is frozen into an immutable contract before it
executes; the runtime, model, verifier and governed-tool identities it may use are resolved by
a deterministic compatibility engine; the decision is sealed as a receipt before any effect
happens; the outcome is verified independently and projected back as experience; and a learned
router may only replace the deterministic one after an offline holdout, a shadow evaluation on
forked live runs, and a calibrated safe-improvement gate over a project-disjoint holdout.

Routing is **off by default**. `ACCRETION_ENABLE_NODE_ROUTING` defaults to false, and with it
off the execution path emits no new event and no new payload key. With it on,
`ACCRETION_NODE_ROUTING_MODE` still defaults to `BASELINE_ONLY`: `SHADOW` and `AUTO` are
regimes a workspace earns by passing evaluation, not defaults a deployment inherits by
accident. Nothing in this release can expand permissions, bypass approvals or verifiers,
expose credentials, or erase durable execution history.

## Highlights

- **M0 contract and feature freeze** — nineteen canonical routing contracts at the freeze and
  twenty-one after its single delta, all sealing an ADR-056 canonical digest, with four golden
  fixtures each (minimal, complete, invalid, unknown-version), twenty-one committed JSON
  Schema 2020-12 documents under `docs/contracts/v0.4/` behind an export-and-`--check` script,
  seventeen additive tables across migrations `0017` and `0018`, and append-only stores with
  no `update_` or `delete_` anywhere.
- **M1 compatibility engine** — a versioned `ReasonCode` catalogue (`compat-rules/1`),
  registry snapshots over narrow `(id, status, version)` projections that never contain a
  token, one `CompatibilityDecision` per registry layer plus the stage-7 joint decision, and
  policy, risk and permission gates that run before any scoring. The engine is pure, decision
  ids are derived, and `UNKNOWN` is never compatible.
- **M2 deterministic, receipt-first routing** — bounded deduplicated candidates beside a
  digest-pinned audited fallback, the context, evidence, candidates, receipt, amendments and
  audit events persisted atomically, and no AGENT, TOOL or VERIFIER effect without the latest
  receipt and a durable dispatch claim. Route, read, candidate, override and cancel APIs with
  replay and version-conflict control.
- **M3 evidence: verification, failure recovery and experience** — claim-level independent
  verification against the frozen spec with a producer ≠ verifier check, an ordered failure
  taxonomy with a fixed authority scope per owner, §9.7 recovery under a hard attempt cap and
  a Wilson lower bound on expected value of information, append-only attribution, and one
  experience record projected per routed node. A material verifier conflict leaves the node
  `WAITING` and pauses the run instead of accepting the failing side.
- **M4/M5 learning: the offline ranker and cold start** — a project-disjoint training snapshot,
  a five-head ranker calibrated on held-out projects and scored on projects it never saw, and
  `LearnedPredictorLoader` as the only way a learned predictor enters routing: no version
  without a holdout evaluation and a calibration report on record ever loads. The project
  adapter is a regularised residual on the workspace prior, so a brand-new project routes
  exactly like its workspace, and cross-domain evidence may move the mean but never the bound
  that gates live routing.
- **M6 shadow evaluation by branched live rollout** — a shadow decision is a record, never a
  dispatch, and it is scored by **forking the run** rather than replaying it: a fresh sandbox
  per arm from the run's base revision, SHADOW and CONTROL graded by the node's frozen
  verification spec under one trial index and one recorded seed. Forks are taken only of
  `LOW_DIGITAL`, `WORKTREE`-isolated nodes, inside a digest-sampled fraction, and only while
  the registered budget has room; every refusal is recorded with its reason.
- **M7 the guarded bandit** — the one place the router may take an action the deterministic
  selector would not have taken, and the six breakers with authority over it. Exploration
  happens only inside the safe set, only on reversible verifier-bound nodes whose active
  version has cleared the shadow gate, under a conformal clip, and every `EXPLORE` receipt
  carries its **real** propensity. The cost ledger charges at the upper bound and credits the
  baseline at the lower one, so the `(1 + α)` bound holds at every round rather than on
  average.
- **M8 promotion on an append-only ledger** — "active" is the head of the `router_activations`
  ledger, not a status column; a rollback target is loaded and scored before anything
  activates; and promotion is a calibrated safe-improvement test that refuses outright when
  any record the holdout names comes from a project the candidate was fitted on, reads a
  simultaneous band at a threshold chosen on a disjoint tune half, and rejects on a failed
  critical cohort whatever the mean did.
- **M9 Experiment Studio** — the node routing panel on the run page, the shadow comparison
  beside it, and the router administration page at `/admin/router`, which shows the active
  version as the ledger head rather than the status column and renders promote and rollback
  only for OWNER/ADMIN while the server keeps deciding.
- **M10 the research instrument** — lineage-keyed splits that keep forks and paper extensions
  in one half, selection-valid estimands with Clopper–Pearson intervals and Bonferroni
  multiplicity, a hierarchical bootstrap over projects, power sizing at the measured noise,
  the baseline ladder with ORACLE refused outside REPLAY, and a development-only pilot behind
  a pre-registration that must be frozen before the locked test set may be read.

## Acceptance

| | Count |
|---|---:|
| Criteria across the four SDDs | 167 |
| Proven by a passing claiming test | 159 |
| Proven by the frontend suite | 5 |
| Proven by a recorded live-provider run | 3 |
| Uncovered | 0 |
| **Unmet MUST** | **0** |

**Historical release measurement, confirmed in the [audit](audit.md) on 2026-09-07:**

```
in scope: 167   proven: 159   unmet MUST: 0
```

167 is the 117 criteria of the v0.1–v0.3 SDDs plus the
fifty `AC4-M<owner>-0NN` rows SDD v0.4 §20 adds; 159 is 167 less the **five** criteria proven
by the vitest suite and the three proven by a recorded live-provider run. The line is derived
from [`docs/acceptance/criteria.toml`](../../acceptance/criteria.toml): each row still
recorded `not_yet_due` subtracts one from *in scope* and one from *proven*. Earlier drafts of
this page quoted 161; that figure counted three `frontend` rows when the policy file carries
five — `V01-P4-004`, `V02-P6-008`, `V02-P7-007`, `AC4-M9-040` and `AC4-M9-043` — and M9's two
were double-counted as proven by pytest as well. When the original draft was written twelve rows were
still `not_yet_due` and `make acceptance` reported
`in scope: 155   proven: 149   unmet MUST: 0`; M7 closed three of them the same day, leaving
nine — `AC4-M9-040`, `-043`, `-044` and `AC4-M10-045`..`-050`. M9d flipped the three M9 rows
and M10d deleted the six M10 rows, and no v0.4 row is `not_yet_due` any more.

The per-milestone figures quoted in the `CHANGELOG.md` entries are historical: each records
what the harness reported on the day that milestone merged.

## Honest limitations

This release does not claim more than it proved.

- **The priced real-provider routing run was not performed, and the results page says so.**
  Every v0.4 measurement in this release is a replay: over the seeded synthetic development
  corpus in `evals/router/` while the system was built, and over the locked corpus and drift
  holdout in `evals/router/locked` and `evals/router/drift` for the numbers the release quotes.
  [`docs/research/v0.4/results.md`](../../research/v0.4/results.md) records the priced run as
  **not run**, so no statement here is evidence about hosted models.
- **One superiority statement is made, and it is the paired one.** M9's project-clustered
  paired regret contrast against the best fixed configuration excludes zero at the
  multiplicity-adjusted level on the locked corpus, `[0.009603, 0.939517]`, and replicates on
  the provider-drift holdout, `[0.039887, 0.320070]`. The binary endpoint does not: `g_learn`
  spans zero, and no recovered fraction is quoted because the opportunity gap's lower limit is
  not positive.
- **In the original v0.4.0 analysis, both safety gates fail for every policy on the locked corpora.** That is a property of the
  corpus's conservative trial pooling at the pre-registered 18 trials per cell — verified means
  verified on every trial, and one false acceptance among eighteen is a false acceptance — and
  not of any router. It is recorded as `ADR4-M10-005` and reported rather than repaired,
  because changing the pooling rule after seeing the rows is the post-hoc analysis change the
  pre-registration exists to prevent. The separately frozen amendment 1 and its
  outcome are retained in the v0.4.1 addendum; they do not replace these original tables.
- **The ablations are replay-only.** `RouterBenchmarkRunner` refuses any source but `REPLAY`,
  at the route and again inside the runner. A replay is a reproducibility guarantee, not a new
  measurement.
- **Expect a single-digit recovered fraction, and possibly none at all.** The confirmatory
  audit this release's estimands follow (*Opportunity is not realizability*,
  <https://www.alphaxiv.org/abs/2608.08265>) found routers recovering **7.5–14.4%** of
  certified oracle opportunity across four benchmarks. `estimands` therefore reports
  `recovered_fraction` only when the lower limit of the opportunity gap is positive: when the
  opportunity has not been shown to exist, the share of it that was recovered is not a number.
  On the locked corpus it is not positive, so this release quotes no recovered fraction at all.
- **OQ-419 is deferred.** SDD v0.4 §22 leaves the public benchmark name to protocol
  publication preparation. The router benchmark ships under its internal name only.
- **The pre-registration is frozen, and the locked test set has been read.** All fifteen SDD
  §21 fields in
  [`docs/research/v0.4/preregistration.md`](../../research/v0.4/preregistration.md) were frozen
  on 2026-09-06 and the page's sha256 is pinned in every corpus's `config.v1.json`; the runner
  refuses to start if the file no longer hashes to it. Under ADR-064 the read is recorded:
  the original read wrote two rows to [the access log](../../research/v0.4/access-log.jsonl),
  one for each corpus. Amendment 1 added two authorized rows on 2026-09-07, for
  four total. A further scientific read requires its own recorded decision.
- **Exploration reservations are reconstructed; settlement adjustments are volatile.**
  Clarified 2026-09-08: `LedgerRegistry` rebuilds readable EXPLORE charges from
  immutable receipts, so restart does not erase every prior charge. Its cached
  settlements are lost; an observed overrun can exceed the reserved amount,
  so reconstruction is not an unconditional proof of conservative accounting.
  The count/cost caps use this same ledger, keyed by workspace and node class,
  with normalized cumulative costs rather than a monetary or per-day account.
  [Bounded follow-up work](backlog.md#remaining-bounded-work) covers contention,
  overruns, unreadable charges and cap scope; no repair is claimed by this note.
- **TOOL-node pins and AGENT sessions have different support.** Clarified
  2026-09-08: selected TOOL nodes pass exact receipt-pinned binding checks into
  the existing capability gateway. Routed AGENT sessions carrying selected tools
  are refused before dispatch/session creation because that session boundary
  cannot honor the same pins. The executing-provider seam did not remove this
  AGENT limitation.
- **A crash after a durable dispatch claim is uncertain execution.** It must be reconciled
  before retry; v0.4 does not claim exactly-once external execution.
- **The four v0.3 deferrals are still deferred.** Workspace-shared and `SERVICE_ACCOUNT`
  enterprise authorization, session enumeration in the identity page, real
  identity-provider interoperability as an expiring manual criterion, and the token-exchange
  egress allowlist are enumerated in [backlog.md](backlog.md) rather than implied to be
  absent. None is a v0.4 acceptance criterion.
- **The v0.3.1 operator-UI ladder is parked.** Its stylesheet port completed and ships inside
  this release — no `v0.3.1` tag exists — but Preflight, the projection store, the cosmic
  scene, orbit, the dashboard and its own release step resume from their plan when the owner
  reopens it.

## Accessibility

The accessibility and computed-style gates now sweep **eighteen routes** — the seventeen
navigable screens plus the not-found route — on the production build, with axe-core's full
default ruleset, one `h1` per route, no horizontal overflow at 390 px and WCAG AA on every
text node.

M9 introduced a per-route **structural-change waiver** for the computed-style diff gate
(ADR4-M9-001): a route that deliberately gains an element declares
`structuralChange: { pr, reason }` in `apps/ui/e2e/routes.ts`, which skips the fingerprint and
the style diff for that route while still enforcing the element floor, the focus pass and
axe-core. The waiver is scoped to one route and one PR and carries its reason in the file, so
a waiver that outlives its change is visible in a diff rather than silent. It is proven
non-vacuous by `styleDiff.test.ts`.

## Upgrading

Migrations `0017` through `0020` apply in order and are reversible against a clean database.
The revision chain is **`0017` → `0018` → `0020` → `0019`**: `0019` retires the two partial
unique ACTIVE indexes for the M8 activation ledger and deliberately chains after `0020`, which
moves the experience-record foreign key, so `0019` is the single alembic head.

```bash
uv run alembic upgrade head
```

Reversibility must be checked against a **clean** database. `0020`'s downgrade refuses while
any row has `experience_id != id`, which is correct: those rows are experience-record
revisions and dropping them would delete provenance.

Every v0.4 feature is off by default. Enabling one is an explicit per-deployment decision:

| Setting | Default | What it does |
|---|---|---|
| `ACCRETION_ENABLE_NODE_ROUTING` | `false` | Opts the deployment into node routing at all. With it off the execution path is byte-identical to v0.3. |
| `ACCRETION_NODE_ROUTING_MODE` | `BASELINE_ONLY` | Which of SDD §11.1's three regimes a graph routes under. `SHADOW` and `AUTO` additionally require a learned scorer to have been assembled; a process that cannot honour the requested mode answers `ROUTING_MODE_UNAVAILABLE` with 422 rather than silently downgrading. |
| `ACCRETION_ROUTER_ARTIFACT_DIR` | `.accretion/router-artifacts` | Where trained artefacts, calibrations and holdout evaluations live. Inside the gitignored data directory, because an artefact tree under version control would make every training run a diff. |

| `ACCRETION_ROUTER_LOCKED_TEST` | `false` | Releases `evals/router/locked` and `evals/router/drift` for one read. Nothing in normal operation reads them; the flag exists so that reading the corpora the release's numbers come from is an act an operator performs on purpose and leaves a record of. Every released read appends a row to `docs/research/v0.4/access-log.jsonl`. |

`ACCRETION_ROUTER_LOCKED_TEST` is the second half of the locked-test guard. The first half is
not a setting at all: the runner refuses unless
`docs/research/v0.4/preregistration.md` still hashes to the `preregistration_sha256` recorded
in the benchmark configuration, and it names both digests when it refuses.

## Links

- [Release audit](audit.md)
- [v0.4 milestone index](README.md)
- [Deferred and parked work](backlog.md)
- [M0 freeze record](m0-freeze.md)
- [M2 runbook](m2-runbook.md)
- [Feedback runbook](../../runbooks/v04-feedback.md)
- [Router pre-registration](../../research/v0.4/preregistration.md)
- [SDD v0.4](../../sdd/Accretion_SDD_v0.4.md)
- [Acceptance verification policy](../../acceptance/criteria.toml)

## v0.4.1 addendum (2026-09-07)

A hardening release on the same acceptance line. Four changes, each recorded as an
`ADR4.1-00k` in [backlog.md](backlog.md):

- **Training snapshots include what a real run produces** (#161). `SnapshotBuilder` joins a
  run-projected experience record to its experience through the `experience_id` label, so a live
  routed run's evidence enters a snapshot instead of being refused as an empty window.
- **The selector optimises the utility the benchmark measured** (#162). The default weights move
  from 1.0 / 0.25 / 0.15 to the pre-registered corpus weights 1.0 / 0.3 / 0.15; persisted objective
  contracts keep the vector they were minted with.
- **The safety gates' pooling rule is registered, not assumed** (#163). `PoolingRule` on the
  benchmark config, initially absent in #163 so every v0.4.0 number remained unchanged; every cell carries both
  the conjunction and the per-trial rate; `docs/research/v0.4/amendment-1.md` proposes the rate
  reading with thresholds unchanged and its expected outcome stated before any re-read. The
  maintainer confirmed the amendment on 2026-09-07; it is frozen, pinned beside the
  pre-registration in every corpus config, and one re-read (access-log rows 3–4) is reported
  under "Amendment 1" in `docs/research/v0.4/results.md` beside the v0.4.0 tables. Two of its
  four written expectations did not hold: per policy the learned comparators clear the
  verified-success floor and exceed the false-acceptance ceiling, and M9 passes both gates on the
  drift holdout by a hair (ADR4.1-004).
- **CI** (#160): a `release/*` push skips the computed-style diff's base build, never silently.
