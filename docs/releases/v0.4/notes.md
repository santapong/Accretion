# Accretion v0.4.0 release notes

> **Draft.** Every number below is sourced from the repository at the head of `develop` on
> 2026-09-06. The counts line, the release date, the tag and the commit SHAs are filled by the
> release PR after M10d, and nothing here may be published until it has been.

Version: `v0.4.0` (pending). Theme: **Evidence-aware node configuration routing**.

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
| Proven by a passing claiming test | 161 |
| Proven by the frontend suite | 3 |
| Proven by a recorded live-provider run | 3 |
| Uncovered | 0 |
| **Unmet MUST** | **0** |

**This table is the target, not a measurement.** The release PR must confirm

```
in scope: 167   proven: 161   unmet MUST: 0
```

before this draft may be published. 167 is the 117 criteria of the v0.1–v0.3 SDDs plus the
fifty `AC4-M<owner>-0NN` rows SDD v0.4 §20 adds; 161 is 167 less the three criteria proven by
the vitest suite and the three proven by a recorded live-provider run. The line is derived
from [`docs/acceptance/criteria.toml`](../../acceptance/criteria.toml): each row still
recorded `not_yet_due` subtracts one from *in scope* and one from *proven*. When this draft
was written twelve rows were still `not_yet_due` and `make acceptance` reported
`in scope: 155   proven: 149   unmet MUST: 0`; M7 closed three of them the same day, leaving
nine — `AC4-M9-040`, `-043`, `-044` and `AC4-M10-045`..`-050`. M9d flips the three M9 rows and
M10d deletes the six M10 rows, which is exactly the `+3`, `+6` that closes the gap.

The per-milestone figures quoted in the `CHANGELOG.md` entries are historical: each records
what the harness reported on the day that milestone merged.

## Honest limitations

This release does not claim more than it proved.

- **No priced real-provider routing run is recorded.** Every v0.4 measurement in this release
  is a replay over the seeded synthetic development corpus in `evals/router/`. Until
  `docs/research/v0.4/results.md` records a priced run against real providers — or records
  that it was not run — no statement here should be read as evidence about hosted models.
- **The ablations are replay-only.** `RouterBenchmarkRunner` refuses any source but `REPLAY`,
  at the route and again inside the runner. A replay is a reproducibility guarantee, not a new
  measurement.
- **Expect a single-digit recovered fraction, and possibly none at all.** The confirmatory
  audit this release's estimands follow (*Opportunity is not realizability*,
  <https://www.alphaxiv.org/abs/2608.08265>) found routers recovering **7.5–14.4%** of
  certified oracle opportunity across four benchmarks. `estimands` therefore reports
  `recovered_fraction` only when the lower limit of the opportunity gap is positive: when the
  opportunity has not been shown to exist, the share of it that was recovered is not a number.
- **OQ-419 is deferred.** SDD v0.4 §22 leaves the public benchmark name to protocol
  publication preparation. The router benchmark ships under its internal name only.
- **The pre-registration is not frozen.** All fifteen SDD §21 fields in
  [`docs/research/v0.4/preregistration.md`](../../research/v0.4/preregistration.md) carry
  proposals and remain `TBD`; `docs/research/README.md` records the row as **PENDING FREEZE**.
  Under ADR-064 the locked test set is readable once, after the pre-registration is frozen and
  the development pilot has closed, and every read is recorded.
- **The M7 exploration cost ledger is in-memory.** `routing/ledger.py` touches no store, no
  clock and no network; `CostLedger.snapshot()` hands a plain sorted dict to whoever wants to
  persist it, and nothing does yet. A restart forgets what a workspace has already spent, so
  `ExplorationCaps` — the absolute count and cost caps that sit outside the `(1 + α)`
  inequality — is what actually bounds a long-lived deployment today.
- **Routed AGENT tool configurations fail closed.** M2 pins governed-tool identity, and the
  MCP boundary cannot yet honour an exact binding pin, so a routed AGENT node that names a
  tool configuration is refused rather than dispatched under a looser binding.
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

One further setting is **planned, not shipped**: `ACCRETION_ROUTER_LOCKED_TEST` arrives with
M10d as the second half of the locked-test guard, beside the check that the pre-registration's
sha256 matches the one recorded in the benchmark configuration. It does not exist in this
release, and the locked test set cannot be read without it.

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
