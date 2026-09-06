# v0.4 M5 — Cold start, the project adapter, and the routing stage collaborators

SDD v0.4 §9.4 writes the cold-start policy as a pipeline and ends it with one sentence:
*"Cross-domain evidence receives a capped prior weight and cannot directly enable live
routing."* M5 turns that sentence into code and then into a property. It also does the
structural half of the job the sentence implies: M2 shipped the §9.1 pipeline with two seam
*comments* in it, and every later milestone was scheduled to edit the same 160-line method.
This milestone replaces the comments with five typed extension points, so M6, M7 and M8 each
add a module and one constructor argument instead.

The milestone claims one criterion, `AC4-M5-021` — "cross-domain evidence cannot directly
enable live routing".

## Ladder

| PR | Content | Proof |
|---|---|---|
| M5.1 — `pr1-project-adapter` | `routing/adapter.py` (OQ-406's regularised residual on the workspace prior's logit, `influence(n, k) = n / (n + k)` exactly zero at `n = 0`, damped Newton fit on a strictly convex objective, canonical-JSON artefact digest) | identity at zero observations; the same seed reproduces the same bits; the signature admits a logit and a count and nothing else |
| M5.2 — `pr2-cold-start-stages` | `routing/stages.py` (five protocols, three inert defaults, the §15.1 ladder and §7.10's retrieval key); `routing/coldstart.py` (`ColdStartScorer`); `routing/service.py` rewritten around the collaborators; the four §12 lifecycle events; `node_routing_mode`; `AC4-M5-021` flipped | the seeded property below, the §18.5 cold-start end-to-end, three degradation ladders, the event sequence, the exclusion refusal, the Postgres parity twin |

## What M5.2 decides

**Cross-domain evidence is a capped prior mean and is never a count.**
`features.summarize_evidence` already counts an out-of-domain record in `n_cross_domain` and
in nothing else. `coldstart.cross_domain_prior` is the only thing that may then use it, and
what it does is blend the *mean* toward the observed out-of-domain rate with weight
`min(0.15, n_cross / (n_cross + 20))`. The lower confidence bound is not an argument to that
function. §9.5's safe set is defined on the bound, so a quantity the blend cannot see is a
quantity that cannot move a candidate into the set — which is `AC4-M5-021`, stated as a
signature rather than as a convention.

**The adapter shifts the mean and the bound by the same logit delta (OQ-406).**
`ProjectAdapter.apply` corrects the prior's logit; `ColdStartScorer._adapt` applies the
resulting delta to `mean` *and* to `lower_bound` before either is written. Moving only the
mean would let a project's own history raise a candidate's expected value while leaving the
number the §9.5 gate reads untouched — passing the gate by not being measured by it.
`n_project` is `n_same_signature` and never includes `n_cross_domain`, which is the second
route by which out-of-domain evidence could otherwise reach the bound; the property test
installs a large-bias adapter precisely so that a mis-scoped count would be visible.

**Degradation is a ladder, not an exception.** §15.1 prescribes a different, weaker answer for
each input the router can lose, and none of them is an error. Every failure path in
`ColdStartScorer` ends in a `ScoredSlate`; the ones that lost something say which under the
single `degraded` label, and `stages.worst_degradation` picks the most consequential when more
than one applies. A loader refusal — `RouterNotEvaluatedError`, a digest mismatch, a missing
artefact, a decode failure — degrades to the audited deterministic baseline and never escapes
`route`.

**A receipt attributes the decision to whoever actually decided.** `workspace_router_version`
is read by §10.2's evaluation as an attribution, so a prior that could not be loaded, or one
whose vocabulary digest did not match, is *not* named: `service._attributed` collapses those
two rungs to `deterministic-router/1`. The routing context keeps the in-force versions
unchanged, because §8.3 makes it the snapshot of the inputs and what was in force is an input.
The two differing is the record of a degradation.

**Outside `AUTO`, the resolver is not asked at all.** `service._versions` returns the
deterministic labels for `SHADOW` and `BASELINE_ONLY` without reading a row. Those labels are
inside `routing_request_id`, so consulting the resolver would make *promoting a model* change
every future request id — and stop every stored receipt from replaying — for a workspace that
had not turned learned routing on.

**The vocabulary is pinned by the snapshot, not by the artefact.** `RankerArtifact` stores no
token table, so the only written record of the vocabulary a version was fitted under is its
training snapshot's `vocab_digest` label. `ColdStartScorer` compares that label with the digest
of the table it featurizes under and degrades on a mismatch. §7.12's rule is that a model's
weights are *about* a feature schema; a mismatched vocabulary is silent at every other layer
and would look like a merely worse model.

**Which modes exist is a property of assembly, so the gate moved into the service.** M2 rejected
`AUTO` and `SHADOW` at the HTTP boundary. Availability now depends on whether a scorer was
injected, which a route handler cannot see, so `api/routing.py` passes the mode through and
`DefaultNodeRoutingService._assert_mode_available` answers once for HTTP and run-manager callers
alike. `routing/bootstrap.py` builds the scorer only when `settings.node_routing_mode` is not
`BASELINE_ONLY`: constructing it and then declining to use it would leave a loaded predictor one
attribute away from a decision nobody authorised.

**A post-route hook cannot unmake a decision.** Hooks run after the receipt is committed and
after the caller has been promised it. A hook that raised would turn a durable decision into an
error the caller would retry, and the retry would replay to that very receipt. Exceptions are
logged and swallowed, and one failing hook does not silence the rest.

**Event payloads carry ids, digests, counts and enum values only.** An objective is user text and
can contain a credential; §17's event log is the widest-read surface in the system. The four §12
events are emitted inside the routing transaction, so a run's event log cannot claim a decision
the store does not hold, and a replay — which is a lookup — appends none of them.

## Acceptance

`AC4-M5-021` — *cross-domain evidence cannot directly enable live routing*, claimed by one
seeded property test over 500 draws in `tests/test_v04_m5_coldstart.py` (MemoryStore, real
`ColdStartScorer`, real `DeterministicSelector`, a real project adapter loaded from a real
`ArtifactStore`):

| Test | Mutation that kills it |
|---|---|
| `test_cross_domain_evidence_moves_the_mean_within_the_cap_and_never_the_bound` | pass `n_same_signature + n_cross_domain` as the adapter's `n_project` → the shifted bound stops matching the unshifted one and `lower_confidence_success` diverges |
| the same test | widen the shrinkage weight (`min(4 * cap, …)`) → a draw moves the mean by 0.21 and the `abs(moved) <= 0.15` assertion fails |
| the same test | raise `CROSS_DOMAIN_CAP` itself → the literal `OQ_408_CAP` pin fails, which is why the test writes 0.15 out rather than importing it |

All three were run and all three fail as described. The claiming test needs no PostgreSQL, so
the criterion cannot classify `SKIPPED_ONLY`; `tests/test_v04_m5_postgres_store.py` carries
parity assertions only and no marker.

Supporting witnesses, none of them marked:

| Test | What it would catch |
|---|---|
| `test_a_fresh_project_routes_on_the_workspace_prior_and_records_no_adapter` | §18.5 #1: a receipt claiming a project adapter a fresh project never had |
| `test_a_prior_that_will_not_load_falls_back_to_the_deterministic_router` | a deterministic fallback attributed to a model that refused to load |
| `test_an_unevaluated_version_is_refused_by_the_real_loader_and_degrades` | M4's refusal escaping `route` and failing a run |
| `test_a_snapshot_under_another_vocabulary_is_refused_rather_than_absorbed` | predicting from columns whose categorical indices point at other tokens |
| `test_a_retriever_that_raises_leaves_the_receipt_without_experience_refs` | "we could not read the history" recorded as "there was no history" |
| `test_a_baseline_route_appends_the_four_lifecycle_events_in_order` | a decision event before the slate that produced it |
| `test_no_routing_event_payload_repeats_the_objective_or_a_credential_in_it` | an objective echoed into §17's log "for context" |
| `test_a_post_route_hook_that_raises_leaves_the_receipt_committed_and_returned` | a shadow recorder able to fail the path it observes |
| `test_an_excluded_configuration_is_refused_at_construct_tuple` | a §9.7-excluded configuration still reachable as a fallback or an override target |

## Counts

With the lane database up, after deleting the `AC4-M5-021` policy row:

```
in scope: 134   proven: 128   unmet MUST: 0
```

`--stage v0.4-M5` reports `in scope: 1   proven: 1   unmet MUST: 0`, which is the form CI gates
this milestone on, and it was measured green on a worktree with no lane database — the claiming
property test uses `MemoryStore` and needs none.

Measured on that same database-less worktree, the unfiltered command reads:

```
in scope: 134   proven: 127   unmet MUST: 1
```

The one `FAILING` is `V02-P5-001`, whose only claiming test enters the API lifespan and therefore
needs PostgreSQL. It is the 128th, it is the same row M4 recorded for the same reason, and nothing
about it is M5's. `scripts/release_gate.py` reports the same single failure and passes its other
four checks.

## Reproduce

```bash
uv run --no-sync ruff check . && uv run --no-sync mypy src
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin \
  tests/test_v04_m5_coldstart.py tests/test_v04_m5_stages.py tests/test_v04_m5_events.py \
  tests/test_v04_m5_postgres_store.py
uv run --no-sync python scripts/export_contract_schemas.py --check
uv run --no-sync python scripts/check_docs.py
uv run --no-sync python scripts/check_acceptance.py --stage v0.4-M5
npm run api:generate && git diff --exit-code openapi.json apps/ui/src/api/schema.d.ts
```

The shared trained router is fitted once per session from `tests/router_corpus_generator.py`'s
committed corpus — twelve projects, three nodes each, two trials per node — so the corpus a
router learns from is a seeded, regenerable file rather than numbers invented in a test.

## Deviations recorded

- `tests/test_v04_m2_service.py`, `tests/test_v04_m2_api.py` and
  `tests/test_v04_m2_memory_atomicity.py` were edited outside the brief's file list. Each was
  asserting a behaviour this milestone deliberately changes, and each was rewritten to prove
  the same claim about the new behaviour rather than relaxed: the event list is still an exact
  equality (now four entries), the mode refusal is still a 422 (now raised by the service, with
  the added assertion that the handler passes the mode through unedited), and the atomicity test
  now calls `_receipt_event`, the method `route` actually uses.
- The §15.1 label the SDD spells `vocabulary_digest` is stored by
  `routing/training_snapshot.py` under the key `vocab_digest`. The scorer reads the key that
  exists.
- §7.10's `capability_digest` had no production derivation anywhere in the tree, so
  `stages.node_signature` defines one: `content_hash` over sorted
  `(capability_id, capability_version, required_scope)` triples. M3's feedback projection must
  derive the same value for retrieval to match, and this is where that convention is written
  down.
