# v0.4 M3 — Experience, feedback and recovery

SDD v0.4 §9.5–§9.7 and §7.9–§7.11 describe what happens to a routing decision *after* the node
ran: an independent verdict is sealed, a failure is typed and handed to the layer that owns it,
and — only once the run itself has been graded — an experience is projected that a future router
may learn from. M3 builds all of it and then attaches it to the scheduler, which is the part that
cannot be proven anywhere but in a whole run.

The milestone claims thirteen criteria: `AC4-M3-003` and `AC4-M3-023` through `AC4-M3-034`.

## Ladder

| PR | Content | Proof |
|---|---|---|
| M3a.1 — `pr1-verification-failures-recovery` | `feedback/{verification,failures,recovery}.py` | claim coverage against the frozen spec, OQ-418 independence, the ordered rule table, the §9.7 cap and EVI gate |
| M3a.2 — `pr2-experience-attribution-pipeline` | `feedback/{experience,attribution,service,bootstrap}.py` | the projection over the P7 experience, append-only attribution, the four-method pipeline over a store |
| M3b — `pr4-feedback-hooks-routes` | the three run-manager hooks, `feedback/evidence.py`, `api/feedback.py`, the four end-to-end witnesses; thirteen rows flipped | the run-level witnesses below |

## What M3b decides

**A §7.9 record is about the producer's execution instance, not the verifier node's.** §7.9 asks
what an independent verifier decided about the work a node did, and the independence it checks is
between the producer's session and each verifier's. `_GraphCursor` therefore carries
`last_producer_key` and `last_producer_session_id`, recorded when a routed AGENT node is
dispatched: after a retry the graph holds two execution instances under one node key, and only
the scheduler knows which of them produced the artifact about to be graded.

**One record per verifier, not one per verifier node.** `record_local` folds every verdict it is
handed into a single record by taking the worst status per claim, and §7.9's `conflict_refs` are
detected *between* records. Handing it all three verifiers at once would make a material
disagreement structurally unrepresentable — two verifiers contradicting each other about one
REQUIRED claim would come back as a plain FAIL — and `AC4-M3-027` would have nothing to fire on.
Each verifier is an independent judgement about the same work, so each is recorded as one, and
the second to disagree carries the reference to the first.

**A contradiction is adjudicated, not re-derived.** Verification records are append-only, so a
re-verification of the same execution instance conflicts with the same stored verdict forever:
"resolved" is not a property the evidence can be re-read for. The block is therefore a question
asked of the store — is there a conflicted, unresolved record against any execution instance of
this run — and the resolution is a durable control event,
`accretion/verification-contradiction-resolved`, written only by
`RunManager.resolve_verification_contradiction`. Both verdicts stay readable afterwards
(`AC4-M3-034` is about exactly that).

**Recovery re-enters the node; it never re-dispatches the decision.** ADR-041 makes a retry a
different routable action, so a `CONFIGURATION` failure drops the attempt's frozen contract,
receipt and claimed configuration, increments the attempt and freezes a *new* execution
instance. The claimed decision of the failed attempt stays claimed and stays in the trace, and
`excluded_configuration_hashes` carries the failed signature into the next `route` call, where
it is refused at `CONSTRUCT_TUPLE`. The same rule now applies to a node re-entered through the
template's own repair edge and to one re-entered after a restart (`_recovered_attempts` reads the
attempt back from the durable node contracts and dispatch events) — without it a paused routed
run could not resume at all, because `claim_dispatch` refuses an already-dispatched receipt.

**The router's authority stops at choosing a configuration.** Only a `CONFIGURATION` failure with
a retry-allowed decision *and* a named next configuration re-arms a node. Everything else returns
the outcome unchanged: a `STRUCTURAL` failure goes to the template's own replan edge and, failing
that, to the `REQUIRES_HUMAN` terminal the scheduler already commits, under the same
`policy_snapshot_id` and with no capability the earlier attempt did not require. The §7.11 event
is written either way — an operator asking why a run stopped needs the taxonomy most for the runs
where nothing was retried.

**The projection happens at the run terminal and nowhere else (ADR-048).** `_commit_run_terminal`
projects one experience record per routed node once the run has been graded, mapping `SUCCEEDED`
to `PASS`, `FAILED` to `FAIL` and everything else to `INCONCLUSIVE`: `REQUIRES_HUMAN` and
`CANCELLED` are runs nobody graded, and recording either as a failure would teach the router that
a configuration failed when what happened is that the work stopped. A refusal from the experience
layer is logged and swallowed, for the reason a post-route hook is: the terminal state is already
durable and already announced.

**The retriever is a filter and nothing else.** `feedback/evidence.py` returns the heads of each
projection line whose signature matches, that are eligible, uncontradicted, visible at the asking
scope, not retracted and not sealed after the instant being decided at — in `(created_at,
contract_id)` order. A principal with no membership in the workspace retrieves nothing, because
§10.1's permission proof is part of what makes a record eligible and the `PrincipalRef`'s own
`status` is synthesised from the run and is never authority. The P7 row is resolved through the
`experience_id` *label* and not through `contract_id`, because migration 0020 means a revision's
id names no `experiences` row and dereferencing it would read "retracted" for exactly the
revisions M3a.2 writes to correct the others.

## Acceptance

Thirteen rows, twelve of them already carried by M3a's unit witnesses and one — `AC4-M3-027` —
claimed here:

| Criterion | Claiming test |
|---|---|
| `AC4-M3-003` | `test_v04_m3_verification.py`, `test_v04_m3_pipeline.py` |
| `AC4-M3-023`, `-024` | `test_v04_m3_verification.py` |
| `AC4-M3-025` | `test_v04_m3_pipeline.py` |
| `AC4-M3-026` | `test_v04_m3_attribution.py` |
| `AC4-M3-027` | `test_v04_m3_e2e.py::test_a_material_verifier_conflict_pauses_the_run_until_it_is_adjudicated` |
| `AC4-M3-028` | `test_v04_m3_failures.py` |
| `AC4-M3-029` .. `-032` | `test_v04_m3_recovery.py` |
| `AC4-M3-033`, `-034` | `test_v04_m3_experience.py` |

The M3b witnesses and the mutation each one kills:

| Test | Mutation that kills it |
|---|---|
| `test_a_material_verifier_conflict_pauses_the_run_until_it_is_adjudicated` | treat a conflicted record as a settled FAIL → the run fails instead of pausing; or make the block one-shot → the second resume completes without an adjudication |
| `test_an_immaterial_verifier_disagreement_does_not_pause_the_run` | pause on any disagreement → an INCONCLUSIVE check deadlocks every run |
| `test_a_runtime_drift_reroutes_the_node_without_replanning_the_graph` | drop `excluded_configuration_hashes` → the second receipt refuses nothing and re-selects the configuration that failed |
| `test_a_structural_failure_replans_without_widening_the_routers_authority` | type a required-output finding as `CONFIGURATION` → the router retries a node whose contract it cannot satisfy |
| `test_the_projection_of_a_finished_run_is_written_once_per_routed_node` | project at node completion → records exist before the run is graded, and a thrown-away run teaches the router |
| `test_stored_experience_reaches_an_auto_routed_receipt` | key retrieval on anything but §7.10's signature → `experience_refs` is empty and an AUTO receipt is indistinguishable from a cold start |
| `test_the_key_a_projection_is_written_under_is_the_key_the_router_retrieves_with` | let the two derivations drift again → nothing raises and every stored experience becomes invisible |

## Counts

With the lane database up:

```
in scope: 147   proven: 141   unmet MUST: 0
```

`--stage v0.4-M3` reports `in scope: 13   proven: 13   unmet MUST: 0`, measured green on a
worktree with no lane database — every claiming test uses `MemoryStore`.

On that database-less worktree the unfiltered command reads:

```
in scope: 147   proven: 140   unmet MUST: 1
```

The one `FAILING` is `V02-P5-001`, whose only claiming test enters the API lifespan and therefore
needs PostgreSQL. It is the same row M4 and M5 recorded for the same reason and nothing about it
is M3's.

## Reproduce

```bash
uv run --no-sync ruff check . && uv run --no-sync mypy src
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin \
  tests/test_v04_m3_verification.py tests/test_v04_m3_failures.py tests/test_v04_m3_recovery.py \
  tests/test_v04_m3_experience.py tests/test_v04_m3_attribution.py tests/test_v04_m3_pipeline.py \
  tests/test_v04_m3_evidence.py tests/test_v04_m3_api.py tests/test_v04_m3_e2e.py \
  tests/test_v04_m3_postgres_store.py
uv run --no-sync python scripts/export_contract_schemas.py --check
uv run --no-sync python scripts/check_docs.py
uv run --no-sync python scripts/check_acceptance.py --stage v0.4-M3
npm run api:generate && git diff --exit-code apps/ui/src/api/schema.d.ts
```

## Deviations recorded

- **The projector was moved off `routing/identity.py:contract_signature_for`.** It and
  `routing/stages.py:node_signature` are two derivations of one §7.10 key and they disagreed in
  two of five fields: the objective digest (the node's prose versus the approved objective
  contract's hash) and the capability digest (`required_scope` excluded versus included). The
  router retrieves with the second, the projector wrote with the first, so no experience this
  milestone stores could ever have been retrieved — and nothing raises when a retrieval key
  misses. `stages.py` is M5's and is frozen for this PR, and M5's own plan records the triple
  derivation as "the repository's convention" that "M3's feedback projection must derive", so
  the *feedback* side was moved onto it: `feedback/experience.py:record_signature_for` delegates
  to `node_signature`, supplying the objective contract's hash from the node's own reference and
  falling back to a digest of the objective text for a node whose objective revision is not yet
  pinned (`node_signature` takes the digest as a parameter precisely because that reference is
  nullable). `feedback/service.py`'s prior-success lookup reads through the same function.
  `routing/identity.py` is deliberately left byte-identical to `develop`: it is foundational and
  shared, this PR's brief does not list it, and the seam is closable without it. The consequence
  is that `contract_signature_for` now has no production caller and should be collapsed into
  `record_signature_for` by whichever milestone owns `routing/identity.py` next — recorded as a
  concern rather than acted on here. A new witness
  (`test_the_key_a_projection_is_written_under_is_the_key_the_router_retrieves_with`) compares
  the projector's derivation and the router's directly, and
  `test_an_experience_frozen_against_another_spec_is_not_retrievable` now varies the
  verification spec hash — which is what its name claims and what the corrected key is sensitive
  to — instead of the objective prose, which the corrected key ignores.
- **The failure hook lives in `_run_graph_node` rather than inside `_graph_agent`.** The brief
  placed it in the agent's FAIL branch; a wrapper around the whole node attempt catches the same
  branch *and* a routed VERIFIER's structural failure *and* a dispatch that raised, and it is
  what makes "re-enter `_run_graph_node`" a loop rather than a recursion.
- **`RunManager.resolve_verification_contradiction` is a new public method.** The brief resumed
  a paused run "after `resolve-contradiction`", but that §11.2 route adjudicates an
  `ExperienceRecord`, and no experience record exists before the run terminal projects one. The
  verification conflict needed its own adjudication surface; the route still exists and is proven
  against an open contradiction on a stored projection.
- **The "SUPPORTING-claim disagreement" test asserts an INCONCLUSIVE disagreement instead.**
  `VerificationSpecBuilder` makes *every* claim `REQUIRED` — one per required verifier and one
  per required output — so no frozen spec has a SUPPORTING claim to disagree about. The
  immateriality the test proves is the other one §7.9 names: a verdict that declined to decide is
  not a contradiction.
- **E2E #5 ends `REQUIRES_HUMAN` rather than completing.** Under `BASELINE_ONLY` the only
  selectable configuration is the audited baseline, so once §9.7 has refused it there is nothing
  left this router is authorised to choose and it escalates. That is the fail-closed half of
  `AC4-M3-032`; an implementation that ignored the exclusion would sail through to a green run.
- **E2E #2 asserts the project adapter on the routing *context* and its absence on the receipt.**
  The adapter installed for the test is the workspace prior re-tenanted at project scope, so the
  scorer declines its artefact and records `ADAPTER_UNAVAILABLE`. §8.3 keeps the in-force version
  on the context and the *attribution* on the receipt, and asserting the receipt named a model
  that never ran would be asserting the opposite of `service._attributed`.
- **`_load_graph_cursor` now restores the last captured artifact.** A resumed graph re-entering
  its verifier node captured `final.patch` a second time and the store refused the duplicate
  name, so the resumed run failed on a workspace nobody had touched. On a first run there are no
  artifacts and this restores nothing.
