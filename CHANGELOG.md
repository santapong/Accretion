# Changelog

All notable changes to Accretion are documented in this file.

## [Unreleased]

### v0.4.1 — hardening

- Changed CI so a `release/*` branch's own push skips the computed-style diff's base build the
  way a `main`-base pull request already did: the v0.4.0 bridge's first push ran the diff against
  the repository's initial commit and went red before its pull request existed to carry
  `base_ref`. `STYLE_DIFF_SKIP=release-bridge` is set for the same case, so the skip is never silent (#160).

#### Changed

- The shipped selector now optimises the utility the benchmark measured:
  `accretion.routing.selector.DEFAULT_UTILITY_WEIGHTS` moves from quality/cost/latency
  `1.0 / 0.25 / 0.15` to the corpus weights `1.0 / 0.3 / 0.15` that pre-registration item 3
  froze and every `evals/router` corpus declares, and the M2 objective minter in
  `routing/freeze.py` imports that one constant instead of restating a second literal, so the
  two cannot drift apart again. A new test pins the constant against the literal *and* against
  the `weights` block of all three registered corpora — shipped, locked and drift. No M10
  number is re-measured (the corpora, seeds and traces are untouched), no persisted objective
  contract is rewritten, and an `ObjectiveContract` may still declare its own
  `utility_weights`; `ADR4.1-002` records the decision and its two limits (#TBD).

## [0.4.0] - 2026-09-07

Theme: **Evidence-aware node configuration routing**. Full notes in
[docs/releases/v0.4/notes.md](docs/releases/v0.4/notes.md); the release audit and
its disclosed limitations are in
[docs/releases/v0.4/audit.md](docs/releases/v0.4/audit.md).

Acceptance at release: 167 criteria in scope, 159 proven by a passing claiming
test, 5 by the frontend suite, 3 by a recorded live-provider run, **0 uncovered
MUST criteria**, and the five SDD §24.8 release-gate conditions all PASS
(`in scope: 167   proven: 159   unmet MUST: 0`).

The per-milestone acceptance figures quoted below are historical — each records what the
harness reported when that milestone merged.

The v0.3.1 operator-UI ladder (M9 of the v0.3 plan) is parked after its stylesheet port and
was never tagged: no `v0.3.1` tag exists, so its merged entries ship inside this release and
are kept together under their own sub-heading at the end of this section.

### v0.4 — M0 contract and feature freeze

- Unlocked the v0.4 SDD (evidence-aware node configuration routing): it moved from the
  forward package to `docs/sdd/Accretion_SDD_v0.4.md`, its fifty acceptance criteria became
  `AC4-M<owner>-0NN` rows the harness reads, ADR-051..059 record the freeze decisions, and
  every criterion starts `not_yet_due` under its owning milestone (#118).
- Turned `accretion.contracts` into a package — `contracts.py` became
  `contracts/__init__.py` byte-identically, so every existing dotted import still
  resolves — and added the two cross-release foundations the later v0.4 contracts build
  on: `contracts/canonical.py` implements ADR-056 canonical JSON and `content_hash`
  (sorted keys, no whitespace, UTF-8, integers as integers, decimals as strings, RFC 3339
  UTC `Z`, non-finite numbers refused, `content_hash` omitted from its own input) against
  committed golden vectors the tests read, and `contracts/refs.py` adds the registry §4
  typed references that did not exist yet — `RuntimeRef`, `CapabilityRef`, `ToolRef`,
  `SkillRef`, `EnvironmentRef`, `VerifierRef`, `EvidenceRef`, `PolicyRef` and
  `ApprovalArtifactRef` — while `PrincipalRef`, `PluginRef`, `ConnectionRef` and
  `ArtifactRef` are reused unchanged (#121).
- Froze the v0.4 contract family in `contracts/routing.py`: nineteen contracts
  (`ObjectiveContract`, `ObjectiveContractRef`, `NodeContract`, `VerificationSpec`,
  `TaskFeatures`, `ProjectFeatures`, `RoutingContext`, `ExecutionConfiguration`,
  `ConfigurationCandidate`, `CompatibilityDecision`, `StructuredExplanation`,
  `RoutingDecisionReceipt`, `IndependentVerificationResult`, `ExperienceRecord`,
  `FailureEvent`, `RouterModelVersion`, `RouterTrainingSnapshot`, `RouterPromotionReport`,
  `ShadowDecision`) exported as `CONTRACT_INVENTORY`, all inheriting the registry §3 header
  through a new `CanonicalContract` base that seals its own ADR-056 digest and rejects an
  unknown schema major; fifteen registry §5 enums with the total `risk_level_for` mapping;
  the fourteen ADR-055 id prefixes; seventy-six golden fixtures (minimal, complete, invalid,
  unknown-version) carrying real content hashes; and nineteen committed JSON Schema 2020-12
  documents under `docs/contracts/v0.4/` with an export-and-`--check` script. No table,
  migration, store method, route or UI file changes (#122).
- Froze v0.4 persistence: migration `0017_v04_m0_routing_contracts` adds the fourteen
  tables SDD v0.4 §13 lists plus `objective_contracts`, which §7.1 requires and ADR-058
  counts — fifteen additive tables in all (`objective_contracts`, `node_contracts`,
  `verification_specs`, `routing_requests`, `configuration_candidates`,
  `compatibility_decisions`, `routing_receipts`, `routing_overrides`,
  `verification_results`, `experience_records`, `failure_events`,
  `router_model_versions`, `router_training_snapshots`, `router_promotion_reports`,
  `shadow_decisions`) carrying the §13.1 constraints —
  unique `(content_hash, schema_version)` on every table, a unique `routing_request_id`
  on receipts, and the repository's first two partial unique indexes for "one ACTIVE
  workspace router per workspace" and "one ACTIVE adapter per project and algorithm",
  both mirrored in `MemoryStore`; every foreign key is `ON DELETE RESTRICT` so nothing
  cascades into provenance, and `experience_records` is keyed by the P7 `experience_id`
  it projects. The `StateStore` protocol gains append-only `put_`/`get_`/`list_` methods
  for each table — implemented identically in `MemoryStore` and `PostgresStore`, refusing
  a different payload under a stored id or a stored digest, treating a byte-identical
  repeat as a no-op, and offering no `update_` or `delete_` anywhere. Every `list_` takes
  `workspace_id` as a required keyword with no default, so a v0.4 listing cannot be taken
  across tenants by forgetting an argument. The receipt rule and the two ACTIVE-router
  rules are mirrored in Python on both backends too, each inside the same lock or
  transaction as the insert it guards, so a second receipt for one routing request raises
  `ValueError` rather than escaping `PostgresStore` as a driver `IntegrityError`;
  `MemoryStore` also mirrors the two foreign keys, refusing a record that names a project
  or an experience it does not hold.
  `routing_overrides` — the one §13 table PR2 froze no contract for — stores a
  *pre-contract* record: its type marker is `document_type`, held deliberately outside the
  frozen `accretion.<contract>` namespace so it makes no promise M2's `RoutingOverride`
  would have to keep, its ids come from a new `routing_override` -> `rov` kind rather than
  the v0.1 strategy-override `ovr`, its retry check ignores the store-stamped clock so an
  at-least-once redelivery is a no-op (an *explicitly supplied* `created_at` is part of
  the identity a retry is compared on), and its shape is frozen by a committed golden
  document with a recorded digest. The freeze record classifies M2's future
  `RoutingOverride` as a **Major, fail-closed** replacement of that pre-contract record
  rather than an additive Minor change, and records the discrimination rule: a row
  carrying `document_type` is an M0 pre-contract record and must never be fed to a
  contract's `model_validate`. Plus `docs/releases/v0.4/m0-freeze.md` recording the
  sha256 of every committed contract schema, the table that stores it and the migration
  that created it, checked by a test that also asserts every one of the fifteen tables has
  a frozen shape. The migration is reversible and no existing table, model, route or UI
  file changes (#123).
- Amended the freeze exactly once, for the three M0 facts that do not compose with M6–M8:
  `ShadowDecision` carries no observed-outcome fields (a branched rollout needs them), the
  partial unique index plus the no-update store make a second ACTIVE router impossible (M8
  needs an append-only activation ledger), and `ObjectiveContract` carries no exploration
  budget (OQ-410). Two contracts, two tables, one additive field and the governance rows
  travel together, once. `ShadowRolloutResult` (prefix `shr`: kind SHADOW or CONTROL, fork
  execution, configuration hash, serving window, observed outcome, budget, trial and seed) and
  `RouterActivation` (prefix `rac`: scope, family key, sequence, PROMOTE or ROLLBACK, version
  ids, rollback target, report, approver and cause, where ROLLBACK requires a cause and a
  target and sequence 1 has no predecessor) take `CONTRACT_INVENTORY` to twenty-one;
  `ObjectiveContract.exploration_policy` is additive and optional. `shadow_rollout_results` and
  `router_activations` arrive in reversible migration `0018_v04_freeze_delta`, and
  `V04_M0_ROUTING_TABLES` gains both while `0017` subtracts them, so the already-applied
  revision keeps creating exactly the fifteen tables it always created. SDD §7.13a and §7.14,
  the §13 rows, ADR-060..064 and decisions for sixteen open questions are recorded with it.
  **Disclosed rather than hidden:** `exploration_policy` is registry §3.2 Minor by field list
  but is not digest-neutral, because ADR-056's canonical form keeps nulls, so every pre-delta
  `ObjectiveContract` document's `content_hash` moves. No release contains migration 0017 —
  v0.3.0 ends at 0016 — and nothing outside the test suite writes an `objective_contracts`
  row, so no stored document exists to break; the case is pinned by a test and is the exact
  case the M8.3 upcaster exists for (#134).

### v0.4 — M1 compatibility engine

- Added `src/accretion/routing/` with the deterministic compatibility engine: a versioned
  `ReasonCode` catalogue (`compat-rules/1`) that lifts the nine configuration-relevant P7
  codes and adds nine v0.4 ones, `RegistrySnapshotBuilder`/`RoutingSnapshot` (four snapshot
  digests over narrow `(id, status, version)` projections that never contain a token), and
  `CompatibilityEngine.evaluate`/`evaluate_joint`/`map_resolution`, which emit one
  `CompatibilityDecision` per registry §7.3 layer plus the SDD §9.1 stage-7 joint decision.
  The engine is pure — it reads a snapshot and never the store — decision ids are derived
  by a new `ids.derived_id(kind, *parts)` so the same snapshot replays byte-identical
  decisions, and `UNKNOWN` is never compatible. Stage 7 also enforces each
  `CapabilityRequirement.version_range` against the version the snapshot observed, refusing
  an out-of-range binding as `CAPABILITY_UNAVAILABLE` and a range grammar it may not
  interpret as `COMPATIBILITY_UNKNOWN`; `required_scope` is authority rather than
  availability and is deferred to M1.2's permission gate, pinned by a test named for the
  deferral. All twelve SDD §12 routing, feedback and
  router `EventType` members are declared here once, so no later v0.4 PR regenerates
  `openapi.json` or `apps/ui/src/api/schema.d.ts` for them again (#128).
- Added `routing/gates.py`, `routing/protocols.py` and `routing/identity.py`, which close
  M1. Policy, risk and permission gates live in a module that imports nothing from a
  selector, candidate builder, ranker, GBDT, project adapter or the experience layer, run
  before any scoring in `gate_then_evaluate` and seal the same `CompatibilityDecision` a
  rule seals; `REQUIRE_APPROVAL` becomes the new `APPROVAL_REQUIRED` reason code because
  routing never pre-approves. `NodeRoutingService` and `FeedbackPipeline` freeze the seams
  M2 and M3 implement, with `RoutingMode` and `FrozenNode`, and `RunManager` gains
  `routing_service` and `feedback_pipeline` attributes defaulting to `None` on the
  `search_executor` precedent, so nothing changes for a deployment that never sets them.
  Identity is derived rather than minted: `execution_instance_id`, `routing_request_id`
  (over all four snapshot ids, SDD §8.2), `workspace_for_run` (ADR4-M1-001) and a
  `VerificationSpecBuilder` whose `contract_id` derives from the spec body, so a re-put is a
  no-op. `RoutingSnapshot` now carries the observed `(id, state)` of each MCP server, which
  lets `map_resolution` stop reporting a disabled *plugin* as an unready *server*. The four
  M1 criteria rows flip to `test` (#136).

### v0.4 — M2 deterministic receipt-first node routing

- Added deterministic, receipt-first node routing behind the opt-in
  `ACCRETION_ENABLE_NODE_ROUTING` setting, which defaults to false, so existing execution is
  unchanged while it is off. Verification semantics and the immutable `NodeContract` are
  frozen before a node is routed; candidates are built bounded and deduplicated beside a
  digest-pinned audited fallback; and the routing context, the compatibility evidence, the
  candidates, the receipt, its amendments and the audit events are persisted atomically. No
  AGENT, TOOL or VERIFIER effect happens without the latest receipt and a durable dispatch
  claim. Adds the authenticated route, read, candidate, override and cancel APIs with replay
  and version-conflict control, pins runtime, model, verifier and governed-tool identity and
  fails closed on drift or an unsupported exact-binding path, and adds PostgreSQL
  concurrency and rollback, workspace-isolation, adversarial and real Fake-runtime graph
  evidence. Only `BASELINE_ONLY` is supported, the shipped enabled catalogue is FAKE-only, a
  routed AGENT tool configuration fails closed until the MCP boundary can honour an exact
  binding pin, and a crash after a durable dispatch claim is treated as uncertain execution
  to be reconciled before retry rather than claimed as exactly-once. Rollback, uncertain
  dispatch recovery, protocol extension and the known M2 limits are documented in
  [docs/releases/v0.4/m2-runbook.md](docs/releases/v0.4/m2-runbook.md). Eleven M2 criteria
  proven, `unmet MUST: 0` (#139).

### v0.4 — wave-1 integration: taxonomy, breakers, upcaster, features, verification, provider seam, gbdt

- Merged seven independently built and reviewed branches — all pure modules over the sealed
  M0 contracts, with disjoint files — as one change, so they land under one CI run instead of
  seven update-and-rerun rounds under the branch-must-be-up-to-date rule:
  `feedback/verification.py` (M3a.1: claim-level coverage, undeclared verifier sessions
  refused, `INCONCLUSIVE` as a third state); `feedback/failures.py` and `feedback/recovery.py`
  (M3a.3: the ordered rule table with fixed authority scopes and the EVI-gated recovery
  guard); `routing/features.py` and `routing/training_snapshot.py` (M4.1: the versioned
  feature schema and reproducible training snapshots); `routing/gbdt.py`,
  `routing/calibration.py` and `routing/ranker.py` (M4.2: a pure-Python deterministic
  learner, Platt, isotonic and split-conformal calibration, and the five-head ranker with bag
  variance and artefact digests); `routing/breakers.py` and `routing/ledger.py` (M7.1: six
  pure circuit breakers and the (1 + α) conservative cost ledger); `contracts/upcast.py`
  (M8.3: ADR-057's read-boundary upcaster, with `_load_v04_contract` routed through it and
  newer-minor fixtures for all nineteen contracts); and the executing provider threaded
  through the session in `run_manager.py` and the governance terminals with **zero** behaviour
  change, pinned by a golden-trace test generated from the pristine parent commit, with
  cancellation and the limiter following the session. Four of the seven had already been
  opened as #129 (taxonomy), #130 (breakers), #131 (upcaster) and #132 (features); those pull
  requests are closed in favour of this one, which is why those numbers appear nowhere else.
  No `criteria.toml` row flips: every acceptance marker in these branches stays `not_yet_due`
  until its milestone's closing PR (#133).

### v0.4 — M3 experience, feedback and recovery

- Changed `src/accretion/persistence/models.py` and `store.py`: M0 keyed
  `experience_records.id` as both the primary key and the foreign key into the P7
  `experiences` table, so an `ExperienceRecord` revision — a new row with a derived id and
  `supersedes_contract_id`, the shape SDD §7.10/§9.6 and `AC4-M3-025`, `-026` and `-034`
  require for attribution, contradiction and final-status revisions — could not be stored at
  all. Migration `0020_v04_experience_record_revisions` moves the foreign key onto a new NOT
  NULL, indexed `experience_id` column (`ON DELETE RESTRICT`), so every revision of one
  projection pins its experience and coexists with its root. `put_experience_record` takes
  `experience_id` as an optional keyword whose `None` default keeps the M0 behaviour for every
  existing caller, and `list_experience_record_revisions` reads revisions back in
  `(created_at, contract_id)` order on both backends. The migration is reversible and its
  downgrade refuses while any row has `experience_id != id`. No contract edit (#137).
- Added the store-only half of the feedback pipeline, ahead of M3b wiring it into the run
  manager: `feedback/experience.py`'s `ExperienceProjector.project` materialises the run's P7
  experience first — the foreign-key guard refuses a record naming a missing `experiences` row
  — then writes the record under `experience_id` with the contract signature, the
  configuration hash, the local and final statuses, the outcomes, the permission provenance
  and PROJECT visibility, refusing on its own any visibility wider than the provenance scope;
  `eligible_for_learning` is local PASS **and** no OPEN contradiction, and
  `ContradictionDetector` and `resolve_contradiction` write OPEN and RESOLVED revisions (a new
  `contract_id`, a `supersedes_contract_id`, the same `experience_id`) rather than editing.
  `feedback/attribution.py`'s `DependencyAttributor` (`dep-heuristic+retry-delta/1`, OQ-407)
  derives credit over graph parents and `(node_key, attempt)` retry pairs, in revisions only.
  `feedback/service.py`'s `DefaultFeedbackPipeline` and `feedback/bootstrap.py` assemble the
  four protocol methods, and `routing/identity.py` gains the `contract_signature_for(node)`
  the projector, M3b's evidence retriever and `features.summarize_evidence` all share (#142).
- Completed `src/accretion/feedback/`: `verification.py` seals a v0.1 verdict set into one §7.9
  `IndependentVerificationResult` with claim-level coverage against the frozen spec, OQ-418's
  structural producer ≠ verifier check and PASS-versus-FAIL conflict detection between records;
  `failures.py` types a failure through an ordered §7.11 rule table and assigns the owning
  layer; `recovery.py` answers §9.7 with a fixed authority scope per owner, a hard attempt cap
  and a Wilson lower bound on expected value of information; `attribution.py` derives §9.6
  credit append-only; `experience.py` projects the §7.10 record over the v0.2 P7 experience it
  is keyed by (ADR-054 b); `service.py` is the four-method `FeedbackPipeline`; `evidence.py` is
  the store-backed §9.4 retriever that `routing/bootstrap.py` now installs in place of
  `NoEvidence` (#146).
- Changed `src/accretion/services/run_manager.py`: the three seams the pure modules could not
  take. A routed verifier node records one §7.9 result per verifier against the *producer's*
  execution instance, and an unadjudicated material conflict leaves the node `WAITING` and
  pauses the run instead of accepting the failing side — `AC4-M3-027` (material verifier
  conflict blocks acceptance until resolved), with `RunManager.resolve_verification_contradiction`
  as the adjudication. A node that fails, or a dispatch that raises, is classified and handed to
  §9.7: a `CONFIGURATION` failure re-enters the node under a new attempt with the failed
  configuration excluded (`ATTEMPTED_WITHOUT_NEW_EVIDENCE`), a `STRUCTURAL` one is left to the
  template's own repair edge, and neither widens the policy snapshot or the capability set. The
  run terminal projects one experience record per routed node (ADR-048). A node re-entered
  after a retry edge or a restart is re-frozen under the next attempt number rather than
  re-claiming a dispatched receipt, which is what makes a paused routed run resumable (#146).
- Fixed `src/accretion/feedback/experience.py`: the §7.10 key a projection is written under is
  now derived by `record_signature_for`, which delegates to `routing/stages.py`'s
  `node_signature` — the derivation the router retrieves with. The projector previously used
  `routing/identity.py`'s `contract_signature_for`, and the two disagreed in two of five fields
  (the objective digest was over the node's prose rather than the approved objective contract's
  hash, and the capability digest excluded `required_scope`), so no projection this milestone
  writes could ever have been retrieved by the router that wrote it and nothing would have
  raised. `feedback/service.py`'s prior-success lookup reads through the same derivation (#146).
- Added `src/accretion/api/feedback.py`: SDD §11.2's five routes —
  `POST /api/v1/node-executions/{execution_instance_id}/verification-results` (idempotent by the
  v0.1 verification id), `POST /api/v1/runs/{run_id}/final-verification`,
  `GET /api/v1/experiences/search`, `GET /api/v1/experiences/{id}` and
  `POST /api/v1/experiences/{id}/resolve-contradiction`. Cross-workspace reads answer 404 rather
  than 403, and every route refuses with `FEEDBACK_PIPELINE_UNAVAILABLE` when no pipeline is
  wired (#146).
- Flipped the thirteen M3 acceptance rows (`AC4-M3-003`, `-023`..`-034`) (#146).

### v0.4 — M4 offline ranker and candidate gate

- Added `src/accretion/routing/train.py` and `routing/artifacts.py`: `RouterTrainingService`
  cuts a training snapshot over a project-disjoint split, fits the five-head ranker on the
  training projects, calibrates on the calibration projects and scores a `HoldoutEvaluation`
  (verified-success lower bound, ECE, Brier, per-cohort ECE, false acceptance and a pairwise
  ranking metric against the deterministic §9.4 baseline) on projects it never saw, then
  writes the snapshot **before** the CANDIDATE `RouterModelVersion` that cites it.
  `LearnedPredictorLoader` is the only way a learned predictor enters routing and refuses any
  version without a `holdout_eval_digest` and a `calibration_report_digest` on record, or
  outside `{CANDIDATE, SHADOW, ACTIVE}`, verifying the pinned artefact digest on load —
  `AC4-M4-016` (offline ranking precedes any shadow or live learned policy) is now proven.
  Adds `POST /api/v1/router-models/train-candidate` (workspace admin, `Idempotency-Key`
  required) and `GET /api/v1/router-models`, and the `ACCRETION_ROUTER_ARTIFACT_DIR`
  setting (#138).

### v0.4 — M5 cold start, the project adapter, and the routing stage collaborators

- Added `src/accretion/routing/adapter.py`: the project adapter (SDD §6.5, OQ-406) as a
  regularised residual on the workspace prior's logit —
  `adapted = prior + influence(n) · (bias + slope · prior)` with `influence(n) = n / (n + k)`
  — so a brand-new project routes exactly like its workspace and gains influence only as
  resolved outcomes accumulate. `AdapterArtifact` is nine scalars sealed by a canonical
  digest: a runaway or non-finite fit fails at construction, and an artefact with zero
  outcomes must carry a zero residual. `ProjectAdapter.fit` is a two-parameter logistic
  residual with L2 shrinkage toward zero under a deterministic seeded Newton step, and
  `influence` schedules on the artefact's own `k`. A pure module with no store and no wiring;
  composition into the ranker and the cold-start scorer is M5.2 (#125).
- Added `src/accretion/routing/stages.py`: the five extension points SDD §9.4's routing stages
  are assembled from — `ActiveVersionResolver`, `EvidenceRetriever`, `CandidateScorer`,
  `BehaviorPolicy` and the two hooks `PostRouteHook`/`PostNodeHook` — with the three inert
  defaults (`StatusActiveVersionResolver`, `NoEvidence`, `DeterministicBehavior`) that reproduce
  M2's behaviour exactly, §15.1's availability ladder as a single `degraded` label with a stated
  precedence, and §7.10's retrieval key as `node_signature`. This replaces the two seam comments
  M2 left in `routing/service.py`, so M6, M7 and M8 each add a module and one constructor
  argument instead of editing one method (#141).
- Added `src/accretion/routing/coldstart.py`: `ColdStartScorer` consults M4's learned predictor
  from routing for the first time, in `AUTO` mode only and only through
  `LearnedPredictorLoader`. A project adapter shifts the predicted mean **and** the lower
  confidence bound by the same logit delta (OQ-406), and out-of-domain evidence may pull the
  mean toward the rate observed out of domain by at most 0.15 with the bound left untouched
  (OQ-408) — `AC4-M5-021` (cross-domain evidence cannot directly enable live routing) is now
  proven by a seeded 500-draw property test. Every §15.1 loss — no loadable prior, a mismatched
  training-snapshot vocabulary digest, an unavailable adapter, a retrieval failure — degrades to
  a weaker answer and is recorded on the receipt rather than raised (#141).
- Changed `src/accretion/routing/service.py`: `route` is rewritten in §9.4 order around the
  injected collaborators and now emits the four §12 lifecycle events M2 declared and left
  unemitted — `ROUTING_REQUESTED`, `ROUTING_CANDIDATES_BUILT` and, by decision type,
  `ROUTING_FALLBACK_SELECTED` or `ROUTING_HUMAN_REVIEW_REQUIRED` — inside the routing
  transaction, with payloads restricted to ids, digests, counts and enum values. Receipts now
  carry a real `selection_propensity`, the scorer's `calibration_version`, `experience_refs`
  from retrieved evidence, and a `workspace_router_version` that attributes the decision to
  whoever actually made it. A replay stays a lookup: it appends no event and runs no hook.
  `route` gains `excluded_configuration_hashes` (SDD §9.7), refused during candidate
  construction as `ATTEMPTED_WITHOUT_NEW_EVIDENCE` at `CONSTRUCT_TUPLE` so an excluded
  configuration is not a candidate at all (#141).
- Changed `src/accretion/api/routing.py`: the hard `BASELINE_ONLY` reject is removed. Which of
  §11.1's modes a process can honour depends on whether a learned scorer was injected, which a
  route handler cannot see, so the service answers once — with `ROUTING_MODE_UNAVAILABLE`, 422 —
  for HTTP and run-manager callers alike. Added the `ACCRETION_NODE_ROUTING_MODE` setting,
  defaulting to `BASELINE_ONLY`; `routing/bootstrap.py` builds the cold-start scorer only when it
  is set to something else. Graph execution continues to route `BASELINE_ONLY` (#141).

### v0.4 — M6 shadow evaluation by branched live rollout

- Added `src/accretion/routing/shadow.py` and `src/accretion/api/shadow.py`: a shadow policy
  is a SHADOW `RouterModelVersion` parented on an evaluated CANDIDATE, and a shadow decision
  is a record, never a dispatch. `ShadowEvaluator.register` refuses a candidate without a
  holdout evaluation and a calibration report, through
  `LearnedPredictorLoader.require_evaluated` — a second witness for `AC4-M4-016` at the new
  caller's boundary — and copies the evaluation digests and the budget onto the SHADOW
  version. `paired_deltas` pairs by `(shadow_decision_id, trial_index)` and drops incomplete
  pairs; `shadow_report` computes `delta_lcb` under a seeded project bootstrap; `shadow_gate`
  is false below `min_paired_runs` and false while `delta_lcb < delta_ni`. `ShadowReport`,
  `ShadowPair` and `GateStatus` are reused unchanged by M6.2's report route and M8.2's
  promotion gate, so a dashboard and a release gate quote the same numbers.
  `POST /api/v1/shadow-policies` is an `APIRouter` module: workspace admin,
  `Idempotency-Key`, and unavailable while node routing is off. `runtimes/fake.py` gains
  additive `outcomes_by_model` and `session_models`; with neither set, the event list of a
  FAKE graph run is byte-identical to `develop` (#140).
- Added `src/accretion/routing/rollout.py`: `ShadowRoutingHook` re-scores the executed slate under
  the workspace's registered SHADOW version, persists the result as a routing receipt under its
  **own** `routing_request_id` and records one `ShadowDecision` beside the executed decision. It
  dispatches nothing, and it cannot: the shadow receipt names itself in `supersedes_contract_id`,
  so it is excluded from every head set the service computes and `latest_receipt`, `route` and
  `_assert_amendable` all refuse to treat it as the decision in force. `BranchedRolloutExecutor`
  then scores that recommendation the way R7 and ADR-060 require — by **forking the run** rather
  than replaying it: a fresh sandbox per arm from the run's base revision, a session per arm under
  that arm's model id, SHADOW and CONTROL graded by the node's frozen verification spec under one
  trial index and one recorded seed, and one `ShadowRolloutResult` written per arm. Forks are
  taken only of `LOW_DIGITAL`, `WORKTREE`-isolated nodes, only inside a digest-sampled
  `fork_fraction`, and only while the registered `ShadowBudget` has room for the policy's UTC day;
  every refusal appends a `router.shadow.rollout-skipped` event naming its reason, and a fork that
  raises never reaches the run. `AC4-M6-017` (shadow mode observes and never dispatches) is now
  proven by two real runs of one graph that agree on every node dispatched, every node status, the
  final `RunState` and every diff digest the run captured (#145).
- Added `GET /api/v1/shadow-policies/{version_id}/report`: M6.1's `ShadowReport` unchanged as the
  `response_model`, computed under a module-level `ShadowReportConfig` and the deterministic
  selector's utility weights so that a dashboard and M8.2's promotion gate quote the same numbers,
  and so that no caller can re-roll the bootstrap seed until the lower bound clears the floor.
  Workspace membership is enough to read it; a version in another workspace is a 404 rather than a
  403; and a policy nobody has shadowed yet returns the empty report with both gates present and
  unmet instead of the arithmetic's deliberate refusal as a 500. `AC4-M6-041` (promotion evidence
  is paired) is now proven: every recommendation is reported with its executed outcome *and* its
  CONTROL arm, `paired_count` counts only trials that produced both, and `remaining_gates` names
  each unmet gate with the number that decided it (#145).
- Changed `src/accretion/services/run_manager.py`: graph execution now routes under
  `routing_service.default_mode` instead of a pinned `BASELINE_ONLY`, so a deployment assembled
  for `SHADOW` or `AUTO` reaches the mode it was assembled for, and dispatches the §9.4 post-node
  stage after every routed AGENT or TOOL node through the new `_after_routed_node`. Hook failures
  are logged and swallowed: the node has already reported its outcome, and a hook that could fail
  it would turn an observation into a control action (#145).
- Changed `src/accretion/routing/bootstrap.py`: the two M6 stages are registered together, in the
  same branch that builds the learned scorer, so a deployment runs the whole shadow stage or none
  of it. Under `BASELINE_ONLY` neither hook exists (#145).

### v0.4 — M7 the guarded bandit, and the breakers that have authority over it

- Added `src/accretion/routing/bandit.py`: `GuardedBandit` implements `stages.BehaviorPolicy` and
  is the first thing in this repository that takes an action the deterministic selector would not
  have taken. It explores only inside `A_safe` — hard-eligible candidates over the objective's
  verified-success floor — and only on a `LOW_DIGITAL`, `WORKTREE`-isolated, reversible,
  verifier-bound node whose ACTIVE router version has cleared `shadow_gate` and whose objective
  authorises a budget (`AC4-M7-018`). The distribution is inverse-gap weighting,
  `p(a) = 1/(K + gamma_t * (U(a_hat) - U(a)))` with `gamma_t = gamma_0 * sqrt(t)` counted per
  `(workspace, node class)`, under R6's conformal clip `p(a) <= beta * p_safe` against the
  epsilon-smoothed baseline; every drawn decision is an `EXPLORE` receipt carrying its **real**
  propensity, including one that drew the greedy action, because the field records that an action
  was drawn and not that it differed (`AC4-M7-019`) (#151).
- Added `src/accretion/routing/breaker_inputs.py`: `BreakerSampler` turns a workspace's stored
  evidence into one frozen `BreakerInput` — the node class's own recent false-acceptance rate and
  claim coverage, the ACTIVE version's sealed calibration ECE read through
  `LearnedPredictorLoader`, the critical cohorts of the latest promotion report as lower bounds,
  and the training snapshot's provider windows against the snapshot's serving versions. Every
  absence fails closed, and a store that will not answer returns an input that trips five of the
  six breakers rather than only the availability one (`AC4-M7-020`) (#151).
- Added `src/accretion/routing/settlement.py`: `ExplorationSettlement` implements
  `stages.PostNodeHook` and replaces an exploration's charged upper bound with the cost its
  experience record actually recorded, normalised against the node's own `resource_cap`. It
  cannot raise: a node that ran and was verified is not failed by its own bookkeeping (#151).
- Changed `src/accretion/routing/bootstrap.py`: under `AUTO` the behaviour policy is the
  `GuardedBandit` and `ExplorationSettlement` joins the post-node hooks, sharing one
  `LedgerRegistry` so the budget the bandit checks is the one the settlements moved. Under
  `SHADOW` and `BASELINE_ONLY` the deterministic behaviour is unchanged, so exploration is a
  structural property of the assembly rather than a branch inside the policy (#151).

### v0.4 — M8.1 the append-only router activation ledger

- Added `migrations/versions/0019_v04_m8_router_activation_ledger.py`,
  `src/accretion/routing/activation.py`, the rollback drill in `routing/promotion.py` and two
  `api/router_admin.py` routes: "active" becomes the head of the append-only
  `router_activations` ledger rather than a status column. Migration 0019 retires the two M0
  partial unique ACTIVE indexes on `router_model_versions` — its downgrade recreates them
  exactly — and chains after 0020, leaving a single alembic head. `StateStore` gains
  `activation_transaction`, `activate_router_version` and `head_router_activation` on both
  backends, where the old single-ACTIVE guard is replaced by a ledger contiguity guard whose
  refusal text comes from one shared function, so `MemoryStore` and `PostgresStore` refuse
  `sequence = head + 2`, and a first entry naming a predecessor, identically. `RollbackDrill`
  assembles and scores the rollback target on the golden fixture context before anything
  activates; `PromotionService.promote` and `.rollback` are one transaction each and re-read
  the head **inside** it, so a promotion landing during the drill is a conflict rather than a
  withdrawal of the new head under the old version's name.
  `POST /api/v1/router-promotions/{id}/promote` and `POST /api/v1/router-models/{id}/rollback`
  are workspace admin with `Idempotency-Key`, and a drill refusal returns its own code rather
  than a 500 (#143).

### v0.4 — M8.2 the promotion gate, and routing on the ledger head

- Added `src/accretion/routing/ope.py` and `evals/router/promotion.v1.json`: R4's
  Logarithmic-Smoothing estimator (`ls_estimate`, λ fixed at `1/sqrt(n)` by `lambda_rule`, costs
  in `[-1, 0]` refused outside that range rather than clipped), the SNIPS/DR/ESS/clip-mass
  diagnostics that inform a reader and may not decide a release, and `sup_t_band` — a
  simultaneous band over the acceptance-threshold grid that takes the thresholds' correlation
  from the bootstrap rather than paying a Bonferroni factor for ten nearly-identical policies.
  `PromotionConfig` loads the registered constants from their own file, separate from
  `config.v1.json` because `RouterBenchmarkConfig` forbids extras (ADR4-M8-003) (#147).
- Added `PromotionEvaluator` in `src/accretion/routing/promotion.py`: R3's calibrated
  safe-improvement test over a project-disjoint holdout. It refuses outright when any record the
  holdout *names* comes from a project the candidate was fitted on — the check beyond
  `SnapshotSplit`'s declared-list validator, which a leaking snapshot can satisfy
  (`AC4-M8-036`) — chooses the acceptance threshold on a disjoint tune half, reads the
  simultaneous band at it on the evaluation half, folds in §10.2's three sealed gates, M6.1's
  shadow gate and M8.1's rollback drill, and rejects on a failed critical cohort whatever the
  mean did (`AC4-M8-037`). A regression, an unverifiable artefact and an undrillable rollback
  target are findings inside the sealed report; only leakage writes nothing (#147).
- Changed `src/accretion/routing/bootstrap.py` and `src/accretion/routing/activation.py`:
  node routing now resolves the active workspace router and project adapter from the activation
  ledger's head instead of `router_model_versions.status`. Promotion and rollback both append
  `ACTIVE` rows and never retire the original, so the two answers diverge from the moment a
  withdrawal lands; a request routed after a rollback now pins the restored version while every
  receipt written before it is byte-identical (`AC4-M8-039`). `LedgerActiveVersionResolver.resolve`
  returns `routing.stages.ActiveVersions`, and M8.1's local mirror of it is gone (#147).
- Added three routes to `src/accretion/api/router_admin.py`: `POST /api/v1/router-promotions`
  (workspace admin, `Idempotency-Key` required, and under a key the report id is derived so a
  retry returns the first verdict rather than sealing a second), `GET
  /api/v1/router-promotions/{report_id}` and `GET /api/v1/router-models/{version_id}/lineage` —
  the parent-version chain, the family's activation history in ledger order, the reports that
  authorised those acts, and the head's rollback target (`AC4-M8-042`). Both reads stop at
  workspace membership: §10.3 makes the *act* an administrator's, and the audit of it belongs to
  everyone the router routes for (#147).
- Changed `docs/acceptance/criteria.toml`: `AC4-M8-035`, `-036`, `-037`, `-038`, `-039` and
  `-042` are in scope and proven, closing M8. The harness reports
  `in scope: 140   proven: 134   unmet MUST: 0` (#147).

### v0.4 — M9 Experiment Studio: the routing panel, the shadow comparison and router administration

- Added `apps/ui/src/RoutingPanel.tsx` and `apps/ui/src/routingIndex.ts` beside the run page's
  dynamic-workflow inspector: SDD §17.1's node routing panel, fed only by the audit the page
  already fetches plus the two M2 read routes. It shows a node picker (the canvas stays
  passive), the selected configuration, the decision type and uncertainty, the predicted
  intervals labelled with their method, the verified-success lower bound, experience refs,
  alternatives (`hard_eligible && !pareto_dominated`), rejected candidates by reason code, the
  frozen verifier and the version pins, offers override and cancel, and renders an empty state
  naming `ACCRETION_ENABLE_NODE_ROUTING`. `routingIndex.ts` maps audit events to canvas nodes
  purely, so no backend route was needed (ADR4-M9-002). This PR also lands the mechanism every
  later M9 change needs: a per-route `structuralChange` waiver for the computed-style diff
  gate, where a waived route skips the fingerprint and the style diff but still enforces the
  element floor, the focus pass and axe-core (ADR4-M9-001). `theme.css` is deliberately
  untouched — appending any rule breaks three `cssPort.test.ts` pins — so the panel reuses
  existing pinned classes (ADR4-M9-003) (#144).
- Added `apps/ui/src/ShadowComparison.tsx` beside the routing panel: SDD §17.2's comparison
  shows, per pair, the executed baseline against the shadow recommendation, the predicted
  delta against the observed one (the CONTROL fork against the shadow fork), the accumulated
  non-inferiority evidence and the remaining gates, read straight from the M6.2 report route
  and filtered to this run's receipts. A workspace with no SHADOW version renders a disabled
  state and never calls the report route, and a pair whose CONTROL arm has not finished reads
  *pending* rather than zero. A second witness for `AC4-M6-041` (#148).
- Added `apps/ui/src/pages/RouterAdminPage.tsx` at `/admin/router`: SDD §17.3's router
  administration page shows a version's lineage, training snapshot, holdout definition, cohort
  results and promotion report, with the active version taken from **the ledger head rather
  than the row whose status column says ACTIVE**, its rollback target beside it, and promote
  and rollback rendered only for OWNER/ADMIN while the server keeps deciding — a refused
  promotion renders the server's own message. A second witness for `AC4-M8-042`. The operator
  UI now covers eighteen routes in the accessibility and computed-style gates, and with the
  routing panel, the shadow comparison and this page all in the initial closure the bundle
  measures 592,520 B raw and 174,385 B gzip, so `budget.ts` restates the two initial-JS caps
  as ceil(measured × 1.05) — 622,146 B and 183,105 B — with that provenance recorded beside
  them (#149).
- Added `apps/ui/src/projection/` (M9d): `ProjectionCanvas`, `ProjectionNodeLabel`, `NodeBadges`
  and `LoopBackEdge` moved verbatim out of `RunExecution.tsx`, with the xyflow stylesheet import
  and `../react-flow.css` travelling together onto the component that mounts the canvas. The
  package boundary is the claim: no module under `src/projection/` may name `../api`,
  `@tanstack/react-query`, `fetch(` or `EventSource(`, and driving the rendered canvas — controls,
  wheel, nodes, badges — issues no request and exposes no interactive role inside a badge.
  `AC4-M9-043` is now proven (#TBD).
- Added `apps/ui/src/routingBadges.ts` (M9d): the §17.1 badges a node shows — routed, shadowed,
  overridden, explore, fallback or human review, with the runtime, model, tool count, verification
  state, receipt revision, predicted cost and predicted latency behind them. Pure, importing no
  API client, derived from the receipts, slates and shadow pairs the panels already hold;
  `RunExecution.tsx` observes those panels' query keys with react-query's `skipToken`, which reads
  a cache entry and can never fetch one, so the canvas gains badges without gaining a request
  (ADR4-M9-006). The markup reuses the pinned stylesheet's `.node-badge*` classes: no CSS rule was
  added anywhere (ADR4-M9-003) (#TBD).
- Added `apps/ui/src/contracts/canonical.ts` (M9d): a TypeScript twin of
  `src/accretion/contracts/canonical.py` that replays all nineteen committed hash vectors from
  `tests/fixtures/contracts/v0.4/hash_vectors.json` byte for byte, so a browser can verify a
  `content_hash` instead of trusting the field beside it. Keys sort by Unicode **code point**
  (RFC 8785's UTF-16 order would put `😀` before `ｽ`), an integral float still prints `1.0`, an
  integer past 2^53 must be a `bigint`, decimals keep their trailing zeros, and a datetime without
  an offset is refused. It verifies and never writes a digest (ADR4-M9-005) (#TBD).
- Added `tests/test_v04_m9_correlation.py` (M9d): the §16.2 chain walked end to end by ids over
  one real routed run — task, run, graph revision, node contract, routing request, receipt, the
  `RUNTIME_CALL_STARTED` stamp and the `ROUTING_DECISION_CREATED` causation, verification result,
  experience record, training-snapshot manifest, promotion report, and back to the run through the
  `ROUTER_PROMOTION_EVALUATED` event. `AC4-M9-044` is now proven (#TBD).
- Recorded a gap rather than working around it: `SnapshotBuilder` resolves an experience record by
  its `contract_id`, which is an experience id only for records the M4/M8 fixtures build. The M3
  pipeline derives a per-node id and files the record under the experience id separately, so **no
  experience a live routed run produces can currently enter a training snapshot** — the builder
  refuses the window as "nothing eligible". `test_the_snapshot_builder_cannot_yet_include_a_run_projected_record`
  pins the behaviour and its cause; the repair belongs to the milestone owning
  `routing/training_snapshot.py` and `feedback/experience.py` (#TBD).
- Changed `apps/ui/e2e/cssPort.test.ts` (M9d): the xyflow-stylesheet importer scan walks `src/`
  recursively and the adjacency assertion computes the expected specifier from the importer's own
  directory, so the invariant stays "one component imports the sheet and imports ours on the next
  line" rather than "that component sits at the root of `src`" (#TBD).

### v0.4 — M10 the research instrument

- Added `src/accretion/routing/stats.py`: the analysis the research protocol binds the router
  benchmark to, in pure Python with no numpy and no scipy — `clopper_pearson` exact intervals
  (regularised incomplete beta by continued fraction plus bisection), `bonferroni` across
  configurations and policies, `normal_quantile`, `power_sample_size`, `pass_at_k`,
  `pass_pow_k`, `hierarchical_bootstrap` (groups, then units within groups),
  `paired_regret_ci`, `select_best_fixed` (argmax on the selection split only, interval on the
  evaluation split) and `estimands` for G_out, G_Z and G_learn, where `recovered_fraction` is
  reported only when the lower limit of the opportunity gap is positive (#126).
- Added `src/accretion/routing/split.py` and `evals/router/projects.v1.json`: the benchmark
  split is keyed on **project lineage**, not on projects, so forks, derived benchmarks, paper
  extensions and repository versions stay in one split. Union-find `lineage_roots` over
  repository identity, task family and declared ancestry; a seeded `assign` of lineage roots
  to the five splits under quotas that never starve a split; `assert_disjoint` naming the
  offending project and both splits; `SplitAssignment.to_sealed()` into the sealed
  `SnapshotSplit`; the leakage helpers `exact_duplicate_digests` and
  `near_duplicate_objectives`; and an append-only `TestSetAccessLog`. Stated in the docstring
  and pinned by a test rather than implied: `DRIFT` is allocated by the same seeded root hash
  as every other split and reads no temporal or provider key yet (#127).
- Added `src/accretion/routing/baselines.py`, `routing/regret.py` and
  `src/accretion/router_benchmark.py` with a seeded synthetic development corpus under
  `evals/router/`: the protocol's baseline ladder M0..M9 plus ORACLE — strongest fixed (chosen
  on the selection split only), cheapest valid, the deterministic v0.1 mapping,
  performance-aware, per-run, model-only and planner-LLM from the replay trace, with M7–M9
  reporting `NOT_AVAILABLE` until their milestones wire them and ORACLE refused outside
  REPLAY — regret computed from stored receipts, candidates and outcomes alone, where an
  invalid selection takes the registered penalty and counts as a safety event, and a
  REPLAY-only `RouterBenchmarkRunner` whose run id is pinned by the corpus hashes and which
  reports the verified-success and false-acceptance gates separately from utility (#135).
- Added `src/accretion/routing/pilot.py`, `scripts/router_pilot.py`,
  `src/accretion/api/benchmarks_router.py` and
  [docs/research/v0.4/preregistration.md](docs/research/v0.4/preregistration.md): the
  development-only pilot the protocol demands before the locked test may begin, and the
  instrument that measures it. `pilot.py` computes within- and between-project variance and
  the ICC from the regret pairs, the verified-success rate with a Clopper–Pearson interval,
  the false-acceptance count with its upper bound, trial-to-trial σ of utility and pass rate
  (two-trial cells flagged as thin), the outage rate, the power table at δ = 0.02 and 0.01,
  and `pass@k`/`pass^k`, seed-deterministically. `scripts/router_pilot.py` runs it over the
  shipped replay corpus or a nine-trial scratch regeneration and never writes under `evals/`,
  which a tree digest asserts. `GET /api/v2/benchmarks/router` and
  `POST /api/v2/benchmarks/router/run` refuse any source but REPLAY with a stable 422 at the
  route **and** again inside the runner, so a caller who reaches past the route still gets the
  422. The pre-registration drafts the fifteen SDD §21 fields as proposals with an empty pilot
  table; it is recorded **PENDING FREEZE**, and under ADR-064 the locked test set may not be
  read until it is frozen (#150).
- Added `evals/router/locked/` and `evals/router/drift/`: two corpora nobody iterated on,
  generated from new seeds (`20260906`, `20260907`) at the size pre-registration item 1 froze —
  18 trials per cell, 12 projects, six per lineage-disjoint half, 3,888 replay traces each. They
  are directories and not documents (ADR4-M10-004) because a corpus is four documents plus the
  lineage registry its split is proved against, and each carries its own generated
  `projects.v1.json` so the hand-authored development registry stays at twelve projects. Both
  are byte-reproducible from `tests/router_corpus_generator.py`, whose `build`/`write` gained
  `seed`, `trials_per_cell`, `project_prefix`, `provider_era` and `runtime_minor_bump` keywords
  resolved at call time; the shipped development corpus regenerates byte-identically. The drift
  holdout carries `labels.provider_era = "2026-H2"` on every lineage and a later
  `runtime_version` on every configuration, so it differs at both levels item 8 names, and no
  lineage is shared between the three corpora (#TBD).
- Added `src/accretion/routing/locked_test.py` and `scripts/router_locked_test.py`: the one door
  onto the locked corpora. `LockedTestRunner` refuses unless
  `docs/research/v0.4/preregistration.md` still hashes to the `preregistration_sha256` the corpus
  pinned (both digests in the error), `ACCRETION_ROUTER_LOCKED_TEST=1` reaches it through the new
  `Settings.router_locked_test`, and no `RouterTrainingSnapshot` handed in names a locked project
  in **any** of its three groups (`SplitViolation`). The development corpus cannot be read through
  it at all. Every released read appends one `TestSetAccessEntry.to_rows()` row to the committed
  append-only `docs/research/v0.4/access-log.jsonl` — a file and not a `BenchmarkRun` row, because
  `BenchmarkRun.suite` is a frozen `Literal["ACR-ARCH"]` (ADR4-M10-003) (#TBD).
- Added `docs/research/v0.4/results.md`: the release's one locked read, generated by the script
  and diffed against it by a test rather than typed. It reports the three estimands with exact
  Clopper–Pearson intervals at the α/(K + L) = 0.05/9 level, the safety gates in a different table
  from utility, all ten registered §14 ablations, and the drift holdout beside the headline with
  the same estimator. The paired regret contrast excludes zero on both corpora
  (`[0.009603, 0.939517]` locked, `[0.039887, 0.320070]` drift) and is the one superiority
  statement the release makes; `g_learn` spans zero, so no binary superiority is claimed, and no
  `recovered_fraction` is quoted because the opportunity gap's lower limit is not positive. The
  priced real-provider run is recorded as **not run**. Recorded as ADR4-M10-005: every policy
  fails both gates at the frozen size because the corpus's conservative trial pooling
  (`all` for verified, `any` for false acceptance) is near-degenerate at 18 trials — reported, not
  repaired, because repairing it after seeing the rows is the post-hoc change the pre-registration
  exists to prevent (#TBD).
- Changed `src/accretion/router_benchmark.py` so a corpus root is not assumed to be at a fixed
  depth (ADR4-M10-006): a relative `ablations_path` resolves against a `REPOSITORY_ROOT` derived
  from the package's location rather than against `root.parents[1]`, and `_validate_split` reads
  the registry beside the corpus with the shipped one as fallback, through the new
  `RouterBenchmarkCorpus.project_registry()`. Both leave every development-corpus resolution
  byte-identical (#TBD).
- Flipped `AC4-M10-045` … `AC4-M10-050`, closing M10 and the v0.4 acceptance program: the
  locked-test door and its access log, all eleven §8.1 comparators available on the locked
  evaluation half, regret recomputed from a cold store, the gates reported apart from utility,
  the ablations executable from configuration, and the pre-registered effect-size, confidence and
  multiplicity gates checked before a superiority claim — including the negative control that
  reports nothing and its non-vacuous twin that does (#TBD).

### v0.4 — canonical digest convergence

- Changed the seven call sites that hashed JSON with their own
  `json.dumps(sort_keys=True, separators=(",", ":"))` instead of the ADR-056 canonical
  serializer, which the M0 freeze recorded as M8 debt. The three whose bytes are provably
  unchanged — the experience embedding, the governance seed and the live sample's prompt text
  — now go through `contracts.canonical`; the other four — the approval binding, the template
  checksum, the MCP discovery snapshot and the normalized graph hash — route through one
  `legacy_json_digest` helper in `src/accretion/digests.py`, so there is one copy of the old
  expression instead of four, each with its reason recorded per site in
  [docs/releases/v0.4/backlog.md](docs/releases/v0.4/backlog.md). No persisted checksum moves:
  the built-in governance plugin checksum, all five built-in template checksums, the
  fragment-planner graph hash, the MCP discovery snapshot digest and the ASCII approval
  binding are pinned as literal hex by a test written and run **before** any site changed
  (#124).

### Operator UI stylesheet port (v0.3.1 M9 PR0–PR5c, shipped in 0.4.0)

#### Added

- Added repairable vitest anchors: `make anchors` re-addresses the test pointers
  in `docs/acceptance/criteria.toml` after a frontend test file moves, so a
  comment added above a test no longer invalidates the acceptance record (#109).
- Added Tailwind v4 and the v0.3.1 design tokens with **zero rendered diff**:
  the import is written out explicitly minus preflight, `@theme static` forces
  the fourteen tokens into the stylesheet, and content detection is scoped to
  `apps/ui`. Every pre-existing rule survives byte-identically (#110).
- Added a real-browser accessibility gate: `apps/ui/e2e/a11y.spec.ts` and the
  `browser` CI job sweep all seventeen routes on the production build with
  axe-core's full default ruleset, one `h1` per route, no horizontal overflow at
  390 px, and WCAG AA on every text node. It found and fixed five real
  `scrollable-region-focusable` violations on its first run (#111).
- Added a bundle-size budget enforced by `vite build`: `apps/ui/budget/`
  measures every emitted chunk, prints a table on every build, and fails the
  build on any of five rules — per-chunk raw size, initial JS raw and gzip,
  initial CSS raw and gzip, vendor grouping identity, and lazy-only modules.
  `apps/ui/vite.config.ts` splits the app into an app chunk plus four static
  vendor chunks so no chunk exceeds 500,000 B, with the stylesheet cascade
  provably unchanged. Discharges the v0.2 chunk-size advisory carried in
  `docs/releases/v0.3/backlog.md` (#113).
- Added a computed-style diff gate and began the Tailwind port. The gate builds
  the merge-base with `develop` alongside the branch and compares every element's
  computed styles between the two on 17 routes x 5 widths, plus a scripted
  focus/hover pass and two fixture-mocked pages that render the gate, loop,
  candidate-search, experience-transfer and revision rules the seed never reaches
  — `apps/ui/e2e/style-diff.spec.ts`, `styleDiff.ts` and its mutation table,
  `computedStyleProbe()` in `audit.ts`, `make style-diff-base`, and a required
  step in the `browser` CI job that fails rather than skips when the base is
  missing. `apps/ui/e2e/cssPort.test.ts` proves the same thing textually, against
  a pinned byte-identical copy of the pre-migration stylesheet, for the rules no
  browser pass renders. On that evidence the fonts `@import` and 113 of the 441 rules — the
  `:root` tokens, the bare element rules, the shell and nav, forms and buttons,
  page chrome, the runtime cards, `.pill`, the registry, history and audit chain,
  the run and event lists, and their `@media` entries — moved byte-verbatim from
  `apps/ui/src/styles.css` into `@layer components` in `apps/ui/src/theme.css`,
  with the palette, font and breakpoint tokens for the current design declared
  inert beside the cosmic ones. Zero computed-style differences across every route,
  width, interaction and mocked page (#116).
- Added the second Tailwind port slice: the planning review, the task studio, the
  four benchmark screens and the P6/P7 planner rules — `styles.css:35`, `:45-50`
  (including the bare `details`/`summary` rules), `:96`, `:183-188`, `:221-236`,
  `:245-274`, the 900 px block at `:275`, and their entries in the 1100/900/620 px
  blocks — moved byte-verbatim from `apps/ui/src/styles.css` into `@layer
  components` in `apps/ui/src/theme.css`. 224 of the 441 rules now live there and
  the run page is all that remains. The eight comma-joined lists that name a
  planner selector and a run-page `.experience-lineage*` selector moved whole
  rather than being split, which brings the `!important` at `:236` and the more
  specific rule it beats at `:231` across together. `apps/ui/e2e/fixtures/benchmarks.ts`
  and four new fixture-mocked passes put the benchmark rules in front of the
  computed-style probe with a task detail open and a replay status rendered —
  regions no unattended sweep can reach. Zero computed-style differences across
  every route, width, interaction and mocked page (#117).
- Completed the Tailwind port and deleted `apps/ui/src/styles.css`. The run page's
  217 remaining rules — the section heading, the page and inspector stacks, the 25
  `.pill-*` states, the execution, loop and verification chrome, the P3 approval
  gate, the M5 dynamic-workflow inspectors and replan control, the `.search-inspector`
  and `.candidate-*` families, `.experience-lineage/-capture`, the M6 diff, router
  and revision rules, and the 720 px block — moved byte-verbatim into `@layer
  components` in `apps/ui/src/theme.css`, and the two `@media` blocks that PR5b had
  to split are whole again. The 36 rules that style elements inside the React Flow
  canvas moved instead into a new **unlayered** `apps/ui/src/react-flow.css`,
  imported from `RunExecution.tsx` on the statement immediately after
  `@xyflow/react/dist/style.css`: xyflow's stylesheet is unlayered, so those rules
  would lose to it from inside a layer at any specificity, and several beat it only
  by coming later at equal specificity. `button:disabled` is there for the same
  reason — it is the rule whose move into a layer the gate caught in PR5a as
  `/runs/:runId @ 390: #148 button cursor not-allowed -> pointer`. The partition is by
  competition, not by location: `.iteration-badge`, `.gate-waiting-hint` and the
  `.pill-*` states render inside a node and are layered, because xyflow says nothing
  about them. `apps/ui/e2e/cssPort.test.ts` now spans both surviving sheets and, with
  the pre-migration file gone, its union check is the completeness proof for the whole
  migration: all 441 rules exist exactly once, in order, none edited. It also asserts
  the canvas partition in both directions, that `react-flow.css` contains no `@layer`,
  and the import adjacency; `style-diff.spec.ts` gained a built-output assertion that
  xyflow's `.react-flow__node-default` precedes ours and that neither ours nor
  `button:disabled` is inside a layer. `App.tsx` drops its second stylesheet import.
  Zero computed-style differences across every route, width, interaction and mocked
  page (#120).
- `apps/ui/e2e/style-diff.spec.ts` now answers both runtime endpoints from a fixture on
  every route, so `/` and `/runtimes` render four fixed runtimes covering all four `.pill-*`
  colour groups rather than whatever the local machine's CLIs report at that second;
  `/runtimes` is seed-independent and its element floor an equality rather than a margin.
  The probe storm and status churn that motivated it are fixed on the backend in their
  own change (#120).
- Added a cascade-inversion case to `apps/ui/e2e/cssPort.test.ts`, derived from the
  pinned sheet rather than from a hand-kept list. For every pair of rules — one now
  unlayered, one now in `@layer components` — that can match the same element and
  declares a common property with a different value, it fails when the layered rule
  used to win on specificity or source order, because an unlayered rule now beats it
  regardless. Thirty-four pairs are examined and one is exempted, with its reason
  recorded beside it: `button:disabled` (unlayered, so it can beat xyflow's
  `.react-flow__controls-button {cursor:pointer}`) now wins over
  `.benchmark-table td button` (layered), so a disabled button in a benchmark-table
  cell shows `not-allowed` where it used to show `pointer`. Both halves of that pair
  already sat on their current sides of the layer boundary on `develop`, so the port
  neither introduces it nor can fix it without changing a declaration; the repair
  belongs to the token-substitution follow-up.
- Added `apps/ui/src/routes.tsx` as the single source of both the navigation
  bar and the router, and moved the eleven pages still defined inside
  `App.tsx` into `apps/ui/src/pages/`, leaving `App.tsx` thirteen lines. Every
  moved body is byte-identical, the built stylesheet's sha256 is unchanged, and
  `OperatorShell.test.tsx` now guards the three things the move could have
  broken silently: the nav/route pairing, `end` on `/` alone, and the
  stylesheet import order React Flow's cascade depends on. Recorded as
  ADR3-M9-001 in `docs/runbooks/v03-operator-ui.md` (#114).

#### Fixed

- Fixed the runtime health probe storm and the kill race it exposed. `GET /api/v1/runtimes`
  shelled out to `codex`, `claude` and `opencode` on every request, and `runtime_sessions`
  re-probed every runtime just to resolve a `runtime_id`, so one five-second tick of the
  runtime monitor asked for up to N + N·N live probes — roughly sixty subprocess spawns and a
  minute of wall time. Probes that overlapped hit their own five-second deadline and reported
  UNAVAILABLE or DEGRADED for a CLI that was merely slow, and `command_result` killed children
  that had already exited, raising `ProcessLookupError` out of `health()` as a 500. Probes are
  now memoized for thirty seconds behind a single-flight lock (`probe_result`,
  `PROBE_CACHE_SECONDS`), the kill race is suppressed, and the runtime's own
  `active_runs`/`active_sessions` counters stay live because only the subprocess results are
  cached: the operator reads a status at most thirty seconds old against the page's five-second
  poll. The tests count spawns through a tally file and prove the single flight and the expiry
  (#119).

## [0.3.0] - 2026-09-01

Theme: **Plugin, MCP & Identity Integration Platform**. Full notes in
[docs/releases/v0.3/notes.md](docs/releases/v0.3/notes.md); the release audit and
its disclosed limitations are in
[docs/releases/v0.3/audit.md](docs/releases/v0.3/audit.md).

Acceptance at release: 117 criteria in scope, 111 proven by a passing claiming
test, 3 by the frontend suite, 3 by a recorded live-provider run, **0 uncovered
and `unmet MUST: 0`**. All five SDD §24.8 release-gate conditions pass. The
per-milestone figures quoted in the entries below are historical — each records
what was true when that milestone merged.

Three criteria (`V01-P0-002`, `V01-P0-004`, `V01-P4-008`) are `manual` records
backed by a real signed-in provider run and **expire on 2027-02-28**.

### Added

- Added the v0.3 M0 connection-aware capability layer: connector, connection,
  and binding contracts, the capability resolver, and migration 0010, with
  every v0.1/v0.2 capability resolving unchanged (#57).
- Added the v0.3 M1 identity layer: principals keyed by issuer and subject,
  workspaces and memberships, an OIDC Authorization Code + PKCE client with a
  fake IdP for tests, session middleware with a `LOCAL_PRINCIPAL` default mode,
  `/me` and `/auth` routes, and migration 0011 (#58).
- Added the opencode runtime adapter as the third governed runtime (#59).
- Added the acceptance-criteria harness: `docs/acceptance/criteria.toml`
  records how each SDD criterion is verified, tests claim criteria with
  `@pytest.mark.acceptance`, and `make acceptance` computes the status instead
  of documents asserting it (#63, #74).
- Added the v0.3 M2 token broker and OAuth connections: the encrypted secret
  store and master key, single-use OAuth transactions, broker-backed capability
  invocation, the GitHub connector with the `connect`, `oauth/callback`,
  `reauthorize`, `revoke`, and `health` routes, principal-bound runs, and
  migration 0012 (#62, #75, #77).
- Added the v0.3 M3 remote MCP manager with authenticated MCP SDK v2 HTTP
  discovery and invocation, explicit canonical capability mappings, durable
  per-connection discovery snapshots, server lifecycle/audit records, health
  state and circuit breaking, workspace-admin lifecycle APIs, and migration
  0013 (#79).
- Added executable acceptance coverage for `AC3-MCP-02` through
  `AC3-MCP-08`, plus a real SDK v2 ASGI server test and PostgreSQL migration
  round-trip coverage. Existing local stdio coverage continues to prove
  `AC3-MCP-01`.
- Added the v0.3 M4 plugin manager: `MetaPluginManifest` alongside the unchanged
  `MetaPlugin` registry projection, the SDD §20.3 nine-state lifecycle behind a
  single audited transition, Ed25519 and digest-pinned trust levels with a
  risk-to-trust floor, connector dependency resolution, workspace-scoped
  installations over a global immutable version registry, install / enable /
  disable / upgrade / rollback / remove routes, an append-only
  `GET /api/v1/audit/plugins` trail, two bundled fixture packages, and migration
  0014.
- Added executable acceptance coverage for `AC3-PLG-01` through `AC3-PLG-06`,
  including a structural test asserting `StateStore` exposes no deletion method
  beyond `delete_secret_record`, so "removal cannot delete evidence" fails the
  moment one is added.
- Added the `docs/runbooks/v03-plugins.md` operator runbook, carrying ADR3-M4-001
  (SDD §20.3 adopted over §9.2 for the plugin state machine).
- Added milestone acceptance gates to CI: `check_acceptance.py --stage M1`
  through `--stage M6`, plus `--stage v0.2-ui`, now run after the backend test
  suite.
- Added the v0.3 M5 research intelligence plugin: the bundled
  `accretion-research` package declaring five skills and five canonical
  capabilities over two deliberately divergent MCP backends, the SDD §7.6
  transform seam that normalizes both wire shapes into one `EvidenceCandidate`
  stream, the `research_evidence` Evidence Store with content-addressed
  deduplication and migration 0015, three research verifiers behind a new
  `EXTERNAL_EVIDENCE` verification target, and the `EvidenceClass` /
  `EvidenceTrust` / `EvidenceProvenance` / `EvidenceCandidate` /
  `EvidenceRecord` / `CitationCheck` contracts.
- Added `WorkflowNodeSpec.capability_refs` and carried it through
  `_materialize_node` into `RunManager`, closing SDD §27's exit criterion: a
  dynamic workflow now names a canonical capability id and nothing else, and
  the resolver and gateway decide which connector serves it. The field is
  additive and optional, so every template persisted before M5 still
  deserializes and an empty list is the pre-M5 execution path unchanged.
- Added `GET /api/v1/runs/{run_id}/research-evidence`, a read-only projection
  of a run's Evidence Store in the store's deterministic
  `(created_at, evidence_id)` order, gated by workspace membership.
- Added executable acceptance coverage for `AC3-RES-01` through `AC3-RES-04`,
  including a backend swap proven to change exactly the `enabled` field on two
  binding rows, a poisoning test in which a payload claiming
  `"trust": "VERIFIED"` still stores as unverified, and a ranking test in which
  an unverified record with `similarity = 1.0` still sorts below a verified
  record with `similarity = 0.01`.
- Added the `docs/runbooks/v03-research.md` operator runbook, carrying
  ADR3-M5-001 (SDD §10 adopted over §9.1 for the research capability surface,
  because §9.1's `research.citation.resolve` resolves rather than verifies and
  so cannot satisfy `AC3-RES-01`) and ADR3-M5-002 (`github.search` in,
  `python.execute` out, enforced by a test rather than by prose).
- Added the research settings to `.env.example` and `config.py`. The plugin is
  off by default behind two independent gates:
  `ACCRETION_ENABLE_RESEARCH_PLUGIN` and an
  `ACCRETION_RESEARCH_ALLOWED_HOSTS` allowlist that starts empty, so enabling
  the plugin alone opens no upstream egress.
- Added the v0.3 M6 administration surface: five operator routes under
  `/admin` — Connections, Plugins, MCP servers, Capability inspector, and
  Identity — each with an `h1`, rendering only projections the API already emits.
  No page can display a token, a refresh token, or a token handle, because no
  route it calls returns one.
- Added `GET /api/v1/mcp/servers/{mcp_server_id}/discovery`, returning the most
  recent `McpDiscoverySnapshot` so SDD §16.3's discovered tools, prompts, and
  cache TTL have a path to the browser. Additive over M3 contracts: no new
  contract, table, or migration. It never contacts the server, and 404s
  identically for a non-member, an unknown server, and a server that has never
  discovered.
- Added capability badges on the React Flow run nodes and in their accessible
  summary mirror, a graph diff that names every added, removed, and changed node
  **and edge**, and a router inspector rendering `fallback_order` and
  `observed_features`. The last two close the `V02-UI-003` and `V02-UI-006`
  specification mismatches the acceptance baseline recorded as open.
- Added executable acceptance coverage for `AC3-UI-01` through `AC3-UI-05` and
  for the six inherited `V02-UI-001..006` criteria. Every criterion in the three
  SDDs is now in scope: `NOT_YET_DUE` is empty for the first time, and
  `make acceptance` reports `in scope: 110   proven: 96   unmet MUST: 10`, all
  ten inherited v0.1/v0.2 items.
- Added `frontend_evidence` to `docs/acceptance/criteria.toml` and the acceptance
  harness: a criterion proven by pytest can now name the vitest test carrying the
  rendering half of its proof, and the pointer is checked by path, `:line` anchor,
  and exact test title. Deleting a page test or retitling one by a byte fails the
  gate. `verification = "frontend"` evidence is now checked the same way instead
  of being any non-empty string.
- Added the `docs/runbooks/v03-frontend-admin.md` operator runbook, carrying
  ADR3-M6-001 (the proof of a page is split between pytest and vitest, and the
  split is machine-checked), ADR3-M6-002 (`GET .../discovery` is added though
  SDD §17 does not list it), ADR3-M6-003 (identity is read-only; session
  enumeration and the enterprise authorization panel are M7), and ADR3-M6-004
  (a node badge names the plugin from its synthetic connector; manifest-declared
  `node_badges` is M8).

- Added the v0.3 M7 enterprise-managed authorization path, optional and off by
  default behind `ACCRETION_ENABLE_ENTERPRISE_AUTH` plus a configured
  `ACCRETION_ENTERPRISE_AUTH_TOKEN_EXCHANGE_URL`: signing in once retains the
  principal's `id_token`, sealed against its own auth session and bounded by the
  token's own `exp`; an invocation of a centrally managed MCP server exchanges it
  for an identity assertion grant (RFC 8693 `id-jag`) and presents that grant to
  the connector's authorization server (RFC 7523 `jwt-bearer`), minting a real
  `Connection` and `TokenHandle` so revocation, isolation, health and audit are
  the unchanged M2 implementations. With the flag down the deployment is
  byte-identical to the pre-M7 one: no manager is constructed, nothing is
  retained, and no exchange or grant call is made.
- Added the `IdentityAssertion` and append-only `EnterpriseAuthGrant` contracts,
  their `identity_assertions` and `enterprise_auth_grants` tables, and migration
  0016. No field was added to any existing persisted model, and the store gained
  no deletion surface: revocation destroys the sealed assertion through the
  existing `delete_secret_record` and marks the row `REVOKED`, so the row remains
  as evidence (AC3-PLG-05 stays a closed structural fact).
- Added `GET /api/v1/enterprise-auth/profile`,
  `POST /api/v1/mcp/servers/{mcp_server_id}/enterprise-authorize`, and the
  append-only `GET /api/v1/audit/enterprise-auth` trail, all under
  `/api/v1/enterprise-auth/` or the existing MCP prefix rather than
  `/api/v1/auth/`, which is exempt from the session middleware. None of them can
  return an identity assertion, an enterprise grant, or an access token.
- Added the enterprise authorization panel to the Identity admin page and an
  "Authorize (enterprise)" action to the MCP servers page, both of which state a
  disabled deployment outright rather than rendering an empty panel.
- Added executable acceptance coverage for `AC3-EMA-01` through `AC3-EMA-07`,
  including a flag-off run proving zero exchange and grant calls after a real
  sign-in, three deliberately malformed assertions producing three distinct
  `REFUSED_*` outcomes before the authorization server is contacted, a
  second principal receiving his own connection and handle rather than the
  first's, a mid-session token expiry renewing with `grant_calls == 2` and a
  `REFRESHED` row, and a three-sentinel secret scan over events, envelopes,
  bundles, the three new routes, grant `detail`, and the OpenTelemetry export.
  `make acceptance` now reports `in scope: 117   proven: 103   unmet MUST: 10` —
  the ten unchanged inherited v0.1/v0.2 items.
- Added the `docs/runbooks/v03-enterprise-auth.md` operator runbook, carrying
  ADR3-M7-001 (the EMA criteria are SDD §24.9, appended rather than renumbering
  the release gate), ADR3-M7-002 (the retained `id_token`, its accepted risk and
  its five tested mitigations), ADR3-M7-003 (EMA mints a real connection, and
  re-acquisition lives in the broker so `get_access_material` stays the single
  expiry authority), and ADR3-M7-004 (the assertion row is evidence and
  revocation never deletes it), plus the configuration guide and the exact
  verification commands.
- Added the enterprise settings to `.env.example` and `config.py`. The flag alone
  opens nothing: without a token-exchange URL the subsystem is inert, and a
  connector absent from `ACCRETION_ENTERPRISE_AUTH_AUDIENCES` cannot be
  enterprise-authorized.
- Added `--stage M7` to CI, and made an empty stage selection an explicit
  non-zero error, so a stage gate can no longer pass by selecting no criteria.

### Changed

- `EncryptedTokenBroker` gained `register_reacquirer(connector_id, fn)`, consulted
  inside `refresh()` before the no-refresh-token failure. A jwt-bearer grant returns
  no refresh token, so without the hook every enterprise handle would expire at the
  end of its first lifetime; with it, `get_access_material` remains the single
  authority on expiry and a mid-session expiry renews with no user interaction. The
  hook refuses any connector whose `auth_type` is not `EMA`, so an interactively
  consented handle can never be resealed with enterprise authority.
- `RemoteMcpManager._authorization()` gained one additive branch: an `EMA` connector
  with no `ACTIVE` connection mints one, and a refusal marks the server
  `AUTH_REQUIRED` exactly as an unauthorized OAuth connector does. Everything below
  it is untouched, and the flag-off event sequence is byte-identical to M6's.
- `IdentityService.complete_login` retains the assertion when the flag is on, and
  `logout` destroys it before revoking the auth session row.
- `apps/ui/src/api.ts` gained the M6 client functions (plugin detail,
  installations and audit, connectors and connections with connect / reauthorize
  / revoke / health, MCP servers with capabilities and discovery, capability
  resolution, workspaces, auth providers), and `MeResponse` moved from a
  hand-written interface in `api.ts` to the generated schema types. vitest
  fixtures are typed as `components["schemas"][...]`, so backend schema drift
  breaks `npm run check` rather than producing a green suite over a stale shape.
- The read-only `/capabilities` registry page now links to the capability
  inspector, because a capability listed there may still be unresolvable.
- The API process now builds its `VerifierRegistry` explicitly, including
  `research_verifiers(store)`, and wires a `GatewayCapabilityInvoker` onto the
  run manager. Both closed the same class of gap: a component that resolved in
  tests and raised in production because only the test supplied it.
- The MCP gateway process now passes `default_transform_registry()` to
  `CapabilityGateway`, so a binding's `output_transform_ref` resolves in the one
  process that actually serves capability calls to a running agent.

- Capability resolution now treats disabled remote bindings and unavailable MCP
  server lifecycle states as non-executable, and remote calls pass through the
  existing Accretion authorization and credential boundaries.
- `WorkspaceRole` and principal status now change outcomes: a disabled
  principal is refused at the capability boundary, not only at HTTP (#77).
- Generated frontend API types now include the connection, identity, and M3
  MCP server lifecycle routes.
- `.env.example` documents the identity (`AUTH_MODE`, OIDC, session), remote
  MCP endpoint-policy, and plugin trust settings.
- `GET /api/v1/plugins` now filters by workspace membership. Built-in registry
  rows stay visible to everyone; a row contributed by an installation is visible
  only to members of the workspace that installed it. Before M4 every
  authenticated principal saw every registry row, including another tenant's.
- Capability resolution now treats a disabled plugin's capabilities as
  non-executable. The resolver gates on the owning installation's state, so a
  capability re-flagged `enabled=True` by hand still does not resolve while its
  plugin is disabled, removed, or awaiting connector setup.

### Security

- Claude Code runs now carry a sandbox and a meaningful tool allowlist, closing
  the runtime egress asymmetry with Codex and opencode (`V01-P4-001`, #78).
- Hardened the connection surface and made the token audience/issuer guard
  fail closed (#61, #77).
- The OAuth callback stays behind the session middleware so the returning
  browser must be the session that began the flow; unknown, replayed, and
  expired states return one indistinguishable response (#77).
- Remote MCP endpoint registration requires HTTPS (except explicitly enabled
  loopback development endpoints), rejects credentials/query fragments, checks
  hostname and port policy, and rejects every non-public DNS answer before each
  network operation. Redirects and ambient proxy credentials are disabled.
- Discovered tool schemas are checked before publication; credentials remain
  ephemeral; authorization failures atomically expose `AUTH_REQUIRED` and
  `REAUTH_REQUIRED`; remote listings and results are bounded by configured item,
  time, and response-size limits.
- The M2 secret scan's OpenTelemetry guard now verifies that Accretion does not
  instrument OpenTelemetry and no tracer provider or SDK is configured, since
  the MCP SDK makes `opentelemetry-api` a transitive dependency.
- Plugin manifests are requests, not grants (ADR3-006). Every capability a
  package declares is put through the existing `CapabilityPolicyEngine`; the
  complete grant set is computed before anything is registered; a denied
  capability is never registered at all, and a package whose requests are only
  partly granted installs `DISABLED` while one whose requests are wholly denied
  installs `FAILED`. No plugin gains authority automatically.
- Upgrade and rollback re-run the full policy evaluation against the new
  manifest rather than inheriting the previous verdict, so a later version that
  adds a permission must earn it on its own merits.
- MCP servers declared by a plugin manifest are registered disabled and pass the
  same M3 endpoint policy as operator-registered servers, so a manifest cannot
  reach an endpoint the M3 routes would have refused.
- Packages are verified before installation: the canonical manifest digest is
  checked against the pinned digest, detached Ed25519 signatures are verified
  against operator-configured keys, and a capability's risk level sets a minimum
  trust floor. Unsigned packages install only when explicitly permitted, and a
  `SHA256_PIN` alone never confers authorship.
- Consent must echo the manifest digest the administrator was shown and may
  narrow but never widen what policy granted.
- Removal never deletes evidence. Disable and remove flip capability and binding
  flags without deleting rows; `StateStore` exposes exactly one deletion method
  in the whole interface (`delete_secret_record`), a structural test asserts it
  gains no second, and migration 0014 introduces no `ON DELETE CASCADE`.

### Release hardening (M8)

- Closed the ten inherited unmet MUST acceptance criteria. Seven needed a
  claiming test rather than a behaviour change: the graph validator's cycle,
  fan-out, denied-capability, privilege-expansion and risk-expansion branches,
  the six search stop reasons, the N=1,2,4 quality curve and the benchmark
  version axes were all implemented and simply unclaimed, so a regression would
  not have named what it broke.
- `V02-P7-003` is now proven against the real `ExperienceService.assess()`, with
  all 19 compatibility reason codes provoked by distinct single-variable
  perturbations and a guard test that fails if a code is added without coverage.
- Added `scripts/release_gate.py` and `make release-gate`, making each of SDD
  §24.8's five conditions independently executable and independently failable.
  `capability_policy_bypass` is derived from `CapabilityGateway` audit rows and
  `secret_exposure_incidents` from the secret-scan suites (ADR3-M8-002).
- Added `scripts/live_acceptance.py`, which produces a dated evidence document
  from a real signed-in Codex and Claude run. `V01-P0-002`, `V01-P0-004` and
  `V01-P4-008` are recorded as `manual` criteria expiring 2027-02-28.
- Hardened the acceptance harness before widening its authority: an unreadable
  waiver end date now counts as expired, waivers need an ISO date inside 180
  days, a failing claimed test outranks any recorded belief, and a claimed test
  that reports no outcome classifies `FAILING` rather than `PROVEN`
  (ADR3-M8-001).
- Pinned all four ACR-ARCH fixtures to literal digests — `config.v1.json` and
  `environments.v1.json` were previously unhashed — and proved the three version
  axes move independently (ADR3-M8-004).
- CI now gates the full unscoped acceptance harness plus the release gate,
  replacing eight stage-scoped gates that each re-ran the whole suite and could
  report PASS over an empty scope. A `clean-checkout` job proves the result
  reproduces from a fresh clone with no caches.

### Fixed

- Closed the four accessibility findings inherited from v0.2 (F1–F4). axe-core
  4.10.2 now reports zero violations across all seventeen routes, no route
  scrolls horizontally at 390 px, and none of 1,421 measured text nodes falls
  below WCAG AA. Two of the findings were partly misdiagnosed and the
  corrections are recorded in
  [browser-a11y-evidence.md](docs/releases/v0.3/browser-a11y-evidence.md): the
  status-pill palette always passed AA (pills lost their colour to a more
  specific `.panel-header > span` rule), and the five `/admin/*` pages already
  had an `h1`.
- Fixed the M6 administration pages scrolling the document sideways at 390 px:
  their registry tables had no horizontal scroll container.
- Introduced the stylesheet's first CSS custom properties (`--ink-dim`,
  `--ink-muted`, `--ink-amber`); every colour had previously been a repeated
  literal.
- Isolated `tests/test_p5_postgres_store.py`, which failed on a second run
  against the same database and was green in CI only because each run used a
  fresh container.

## [0.2.0] - 2026-08-24

### Added

- Added a newcomer-focused project overview, a complete operator frontend guide,
  and accessible repository-native SVGs for release orientation and the eleven-route
  UI/data-flow map.
- Added a developer documentation hub, accessible architecture and lifecycle
  diagrams, and a deterministic public-API showcase using the fake runtime.
- Added an actionable v0.2 P5–P7 delivery plan tied to the normative v0.2 SDD.
- Added opt-in P5 validated dynamic workflows with typed proposals, deterministic
  graph validation, one bounded repair, static fallback, immutable revisions,
  safe replanning, runtime-decision evidence, and operator inspection
  ([PR #35](https://github.com/santapong/Accretion/pull/35)).
- Added opt-in P6 candidate-search contracts and PostgreSQL persistence for
  versioned plans, shared budgets, candidate trajectories and scores, runtime
  provenance, and crash-reconcilable promotion records
  ([PR #37](https://github.com/santapong/Accretion/pull/37)).
- Added the bounded P6 executor with best-of-N, hypothesis, cross-provider, and
  generator-reviewer modes, isolated worktrees and sessions, independent
  verifier ranking, hard parent-owned budgets, cancellation, and conservative
  restart recovery ([PR #38](https://github.com/santapong/Accretion/pull/38)).
- Added P6 planning and candidate-lineage operator views, including status,
  provider/runtime/model/version, reviewer, score, quality, cost/latency proxies,
  actual spend, terminal reason, and final selection
  ([PR #39](https://github.com/santapong/Accretion/pull/39)).
- Added a deterministic 12-task P6 replay benchmark with frozen fixture hashes,
  N=1/2/4 quality-vs-compute curves, provider comparison, preserved null results,
  API endpoints, and an operator research page.
- Added the P6 runbook, developer showcase, acceptance report, decision record,
  and accessible repository-native lifecycle and quality/compute diagrams.
- Added opt-in P7 immutable experience contracts, controlled procedural
  segments, deterministic 384-dimensional embeddings, exact pgvector retrieval,
  compatibility/transfer-risk evidence, retraction, negative knowledge,
  operator-frozen `ContextBundle` v2 selection, and additive APIs
  ([PR #41](https://github.com/santapong/Accretion/pull/41),
  [PR #42](https://github.com/santapong/Accretion/pull/42)).
- Added P7 `REPLAY_BRANCH` execution with one fresh control, one new isolated
  candidate per selected positive seed, revalidated negative avoidance guidance,
  durable seed/start/rejection evidence, repeated launch/selection/promotion/
  recovery checks, and fail-closed stale-seed pruning without substitution
  ([PR #43](https://github.com/santapong/Accretion/pull/43)).
- Added P7 planning, provenance, replay-lineage, and benchmark operator views;
  runbook, showcase, acceptance report, and accessible lifecycle/gate diagrams.
- Added a frozen 20-task, 50-source, 80-trace P7 benchmark with four treatments,
  false-accept and negative-transfer accounting, stale rejection, quality/compute
  uplift, use/rejection/null rates, negative cases, replay-only API endpoints,
  and exact fixture fingerprints.
- Added the missing frozen 12-task, 24-trace P5 static-versus-dynamic benchmark,
  replay-only API, operator route, cohort utility/non-inferiority gate, explicit
  invalid-proposal fallback evidence, and accessible release diagram.

### Changed

- Reorganized documentation into purpose-owned guides, runbooks, research,
  releases, and governance folders; added an authoritative experiment/results
  index, maintenance rules, and an accessible research-evidence diagram.
- Reworked the project README and active documentation hub, developer guide,
  showcases, and P0–P7 runbooks around the v0.2 release scope and immutable
  v0.1 static-control evidence.
- Updated the Python, frontend, and GitHub Actions toolchains to their current
  compatible releases.
- Reworked UI project defaults and event-stream state to comply with the current
  React Hooks correctness rules without duplicating query-backed state.
- Updated the operator shell and project documentation to describe implemented
  P5/P6/P7 scope while retaining `v0.1.0` as the immutable static control.
- Activated `REPLAY_BRANCH` only when P7 is independently enabled and selected
  experience passes current compatibility and applicability checks; P6-only
  deployments retain the original fail-closed reservation behavior.
- Bumped every public package and runtime version marker to `0.2.0` and aligned
  release-facing documentation with the audited eleven-route frontend.
- Made Claude execution independent of user hooks and plugins through safe mode,
  honored the typed session model override, and recorded the selected live
  calibration model alongside provider versions.

### Known limitations

- The supported browser-control surface had no connected browser during release
  finalization. No rendered route, responsive, keyboard, focus, or automated
  accessibility PASS is claimed; post-release evidence is tracked in
  [issue #52](https://github.com/santapong/Accretion/issues/52).

### Security

- Updated pytest to 9.1.1, resolving CVE-2025-71176 in development test runs.
- Kept speculative P6 candidates inside isolated workspaces with protected
  external side effects and permission expansion denied.
- Required independently verified unique selection before promotion, persisted
  cancellation before interruption, re-evaluated policy before patch application,
  and recorded parent-before/after digests for recovery.
- Restricted P7 evidence to redacted deterministic procedure, excluded patches,
  transcripts, credentials, native sessions, capability arguments/results,
  approvals, permissions, and side-effect state, and required compatibility
  revalidation before replay can launch, win selection, promote, or recover.

## [0.1.0] - 2026-08-22

### Added

- Provider-neutral Codex and Claude runtime control with normalized durable events.
- Deterministic task profiling and static DIRECT/LOOP/GRAPH/HYBRID selection.
- Bounded verifier-gated loops, validated workflow templates, checkpoints, replay,
  approvals, and isolated Git worktrees.
- Governed capability/MCP boundary, credential broker, and idempotent side-effect
  evidence.
- Complete React operator surfaces with snapshot-first resumable SSE.
- Reproducible 30-task ACR-ARCH benchmark and balanced live provider calibration.

### Security

- Deny-by-default external capability policy and task-scoped provider tool exposure.
- Credential values excluded from model context and serialized API/event payloads.
- Durable side-effect intent is recorded before execution and terminal result after.
