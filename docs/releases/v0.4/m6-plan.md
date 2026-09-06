# v0.4 M6 — Shadow evaluation by branched live rollout

SDD v0.4 §10.2 gates the shadow stage on *evidence*, and ADR-046 stages v0.4 as offline, then
shadow, then guarded bandit. M6 is the middle stage. Its whole difficulty is one sentence from
R7 and ADR-060: a shadow recommendation is scored by **forking the run**, not by replaying it.

The milestone claims two criteria — `AC4-M6-017` ("shadow mode observes and never dispatches")
and `AC4-M6-041` ("promotion evidence is paired") — and it closes M6.

## Ladder

| PR | Content | Proof |
|---|---|---|
| M6.1 — `pr1-shadow-policy` | `routing/shadow.py` (`ShadowEvaluator.register`, `record_shadow_decision`, `paired_deltas`, `shadow_report`, `shadow_gate`, the decision-clustered bootstrap); `api/shadow.py` (`POST /api/v1/shadow-policies`); `runtimes/fake.py` per-model scripts | registration is a second version parented on the candidate; AC4-M4-016 holds at the second door; an unpaired arm is dropped rather than scored |
| M6.2 — `pr2-branched-rollouts` | `routing/rollout.py` (`ShadowRoutingHook`, `BranchedRolloutExecutor`); `GET /api/v1/shadow-policies/{version_id}/report`; the scheduler's routing mode and its post-node hook dispatch; `AC4-M6-017` and `AC4-M6-041` flipped | two real runs of one graph, one shadowed and one not, agree on everything a run produces; the report pairs every recommendation with an executed outcome and a CONTROL arm |

## What M6.2 decides

**A shadow decision is scored by a branched live rollout, never by a replay (ADR4-M6-001).**
A trajectory records what one configuration did. Another configuration would have diverged from
its second turn, so replaying it under the shadow configuration measures how well the shadow
*imitates* the executed run rather than how well it does the node's work.
`BranchedRolloutExecutor` therefore forks: `WorktreeManager.acquire_candidate` gives each arm a
fresh sandbox at the run's base revision, a session is opened per arm under that arm's model id,
and the two arms are graded by the same frozen verification spec. The per-node difficulty both
arms share cancels, which is the only reason a pair is worth more than a single number.

**The CONTROL arm is re-run rather than read off the live node.** The live node ran in the run's
own workspace, at its own point in the graph, with whatever earlier nodes left behind. Scoring
the shadow fork against it would compare two executions that differ in their starting state as
well as in their configuration, and the whole difference would be attributed to the router. Both
arms are forks, from one base revision, under one recorded seed.

**The shadow receipt is persisted, and is born superseded.** The shadow decision is a real
`RoutingDecisionReceipt` under its **own** `routing_request_id` (`routing_request_id(..., mode=
SHADOW, <shadow version id>)`), so its slate, its explanation and its predicted outcomes stay
auditable and the executor can dereference the configuration it recommended.
`DefaultNodeRoutingService` finds the decision in force for a node by taking the *head* of the
receipts sharing its `node_contract_hash` — the ones no other receipt supersedes — so a second
head would make `latest_receipt` a coin flip and `route` refuse the node for having competing
heads. The shadow receipt therefore names *itself* in `supersedes_contract_id`. It is excluded
from every head set by construction, the executed decision stays the only head, and
`_assert_amendable` refuses to amend or claim it. **A shadow decision that cannot be a head
cannot be dispatched**, which is the mechanical form of AC4-M6-017.

**Both stages are attached together or not at all.** `routing/bootstrap.py` registers
`ShadowRoutingHook` and `BranchedRolloutExecutor` in one branch, the same branch that builds the
learned scorer. Attaching the recorder alone would write decisions nothing measures; attaching
the executor alone would look for decisions nothing wrote. Under `BASELINE_ONLY` neither exists,
so "shadow evaluation is off" is a structural fact rather than a branch inside a hook.

**A fork is taken only where a second execution is safe, sampled, and paid for.**
`allowed_risk_class is LOW_DIGITAL` — every higher class either reaches outside the workspace or
spent a human approval given for one execution. `environment.workspace_isolation == "WORKTREE"` —
a fork is only a sandbox if the configuration was going to run somewhere branchable. Sampling is
`sha256(receipt_id) mod 1000 < fork_fraction * 1000`, a digest and not a random draw, so a replay
of a seeded run forks the same nodes. The budget is the registered `ShadowBudget`, charged per
policy per UTC day against `completed_at`, and both of its limits are enforced: a policy whose
forks are individually cheap can still saturate the executor. Each refusal appends a
`router.shadow.rollout-skipped` event naming its reason (`not_fork_eligible`, `not_sampled`,
`budget_exhausted`) rather than disappearing.

**Nothing in this milestone can fail a run.** The post-route hook runs after a receipt is durable
and the post-node executor runs after a node has reported. Both swallow every exception and log
it, and `RunManager._after_routed_node` swallows a third-party hook's for the same reason: a
rollout is a whole second execution and can time out, run out of disk or lose a worktree, and
none of that is the run's problem.

**A rollout is graded but never verified.** The fork is scored with
`_verify_candidate(persist=False, emit_result=False)`, so no §7.9 `IndependentVerificationResult`
exists for it, and `ShadowRolloutResult` refuses a row that calls itself `verified` without
naming the record that verified it. `observed.verified` is therefore `False` and the fork's
success is carried by `observed.quality` — the graders' mean score, falling back to the fraction
of results that passed, exactly as the candidate-search scorer does. `utility()` is defined over
quality, cost and latency and does not read `verified`, so the paired delta is unaffected.
`observed.cost` and `budget_consumed` are the same budget-relative proxy candidate search
already uses (`min(1, (1/max_attempts + tool_calls/max_tool_calls)/2)`): no runtime in this
repository reports a price, and a fabricated currency figure inside a record that gates a
promotion would be worse than an honest budget fraction.

**The report is rendered under pre-registered constants.** `GET /api/v1/shadow-policies/{id}/
report` returns M6.1's `ShadowReport` unchanged, computed with a module-level
`ShadowReportConfig` and the deterministic selector's utility weights. A caller that could pass
its own seed could re-roll the bootstrap until the lower bound cleared the floor. Membership is
enough to read it — an operator who cannot see the evidence cannot argue with the promotion it
justifies — and a version in another workspace is a 404, produced by turning the membership
error into a `KeyError`, so the refusal cannot confirm the id.

**An unshadowed policy answers with the empty report, not a 500.** `shadow_report` refuses an
empty decision list on purpose, because a zeroed report would let a reader mistake "nothing was
shadowed" for "the shadow was neutral". A dashboard polling a minute-old policy needs an answer
in the shape it already parses, so the route says it: zero pairs, `non_inferior` false, and both
gates present and unmet.

## Acceptance

`AC4-M6-017` — *shadow mode observes and never dispatches* — claimed by
`tests/test_v04_m6_e2e.py` (MemoryStore, two real runs over a real git worktree, the real
`RunManager`, the real routing service assembled by `build_node_routing`):

| Test | Mutation that kills it |
|---|---|
| `test_a_shadowed_run_dispatches_exactly_what_the_unshadowed_run_dispatched` | fork into `lease.path` instead of a fresh sandbox → the run's captured diff digests move and the comparison fails |
| `test_the_shadow_stage_produced_paired_evidence_that_the_baseline_run_did_not` | drop the post-node dispatch from `_execute_graph` → no rollout rows, and the previous test would have stayed green |
| `test_every_rollout_arm_ran_in_a_fresh_sandbox_and_never_in_the_runs_lease` | open the arm's session at the lease path → a fork session appears at the lease path and the `SHADOW_FORK_PREFIX` assertion fails |
| `test_no_shadow_receipt_can_be_the_decision_the_scheduler_dispatches` | write the shadow receipt under the *executed* request id → `_guard_receipt_request_uniqueness` refuses, the hook records nothing, and no shadow receipt is found; drop the self-supersession → the shadow receipt is a head |
| `test_the_dispatched_receipts_are_the_executed_ones_and_never_a_shadow` | let a shadow receipt reach `claim_dispatch` → its id appears among the dispatch events |
| `test_the_scheduler_routes_under_the_services_own_mode` | restore `mode=RoutingMode.BASELINE_ONLY` at the call site → every other test above stays green because a baseline-only run is what they compare against, and this one fails |

`AC4-M6-041` — *promotion evidence is paired* — claimed by `tests/test_v04_m6_report_api.py`
(MemoryStore, golden fixtures, the real `shadow_report`, the real route):

| Test | Mutation that kills it |
|---|---|
| `test_the_report_pairs_every_recommendation_with_its_executed_and_control_arms` | pair on `shadow_decision_id` alone → trial 0's shadow arm crosses trial 3's control and the result-id assertions fail |
| `test_a_recommendation_whose_control_arm_is_missing_is_listed_but_never_counted` | drop the CONTROL arm → `paired_count` goes to 0 and a lone arm is not evidence |
| `test_the_report_names_every_gate_the_stage_still_owes` | collapse `remaining_gates` into `non_inferior` → an operator cannot tell which bar was missed and M8.2 has nothing to quote |

Supporting witnesses, none of them marked:

| Test | What it would catch |
|---|---|
| `test_both_arms_of_one_trial_are_recorded_under_one_index_and_one_seed` | arms numbered independently, so every "complete" pair pairs with nothing |
| `test_each_arm_records_the_configuration_that_ran_in_it_and_not_the_recommendation` | the CONTROL arm filed under the router's recommendation, misattributing half of every measurement |
| `test_the_shadow_receipt_the_arm_ran_was_never_the_head_of_its_node` | a second head for one node contract |
| `test_a_receipt_outside_the_fork_fraction_is_never_forked` | sampling that forks everything whatever the fraction says |
| `test_the_sampling_decision_is_a_pure_function_of_the_receipt_id` | a random draw, so a replay of a seeded run forks different nodes |
| `test_a_fork_fraction_outside_zero_to_one_is_refused_at_construction` | a share silently read as a multiplier |
| `test_a_node_above_low_digital_risk_is_never_forked` | a second unasked-for execution at a risk class a human approved once |
| `test_a_node_that_is_not_workspace_isolated_is_never_forked` | the second arm running in the first one's world |
| `test_a_node_with_no_lease_is_never_forked` | a fork from an invented base revision |
| `test_a_policy_that_has_spent_its_day_is_refused_with_a_budget_exhausted_note` | an unbounded recurring spend on live traffic |
| `test_a_fork_that_cannot_be_taken_never_reaches_the_run` | a failed experiment failing the run it was observing |
| `test_a_workspace_with_no_registered_shadow_stage_has_nothing_to_shadow` | an absent stage treated as an error |
| `test_the_two_stages_are_attached_together_or_not_at_all` | half a shadow stage, in either direction |
| `test_a_version_nobody_has_shadowed_yet_answers_with_the_empty_report` | the arithmetic's deliberate refusal reaching a dashboard as a 500 |
| `test_a_shadow_stage_in_another_workspace_is_absent_rather_than_forbidden` | a 403 confirming an id in a workspace the caller cannot see |
| `test_the_report_route_is_published_and_is_not_exempt_from_authentication` | a route missing from the generated TypeScript client, or open |

Both claiming files use `MemoryStore` and need no PostgreSQL, so neither criterion can classify
`SKIPPED_ONLY`.

## Counts

`--stage v0.4-M6` reports `in scope: 2   proven: 2   unmet MUST: 0`, which is the form CI gates
this milestone on.

The unfiltered command on this base reads:

```
in scope: 136   proven: 130   unmet MUST: 0
```

with the lane database up. Measured on a worktree with no lane database it reads one fewer
`proven` and one `unmet MUST`: `V02-P5-001`'s only claiming test enters the API lifespan and
therefore needs PostgreSQL. It is the same row M4 and M5 recorded for the same reason and
nothing about it is M6's.

## Reproduce

```bash
uv run --no-sync ruff check . && uv run --no-sync mypy src
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin \
  tests/test_v04_m6_rollout.py tests/test_v04_m6_report_api.py tests/test_v04_m6_e2e.py \
  tests/test_v04_m6_shadow.py tests/test_v04_m2_end_to_end.py tests/test_v04_m2_dispatch.py
uv run --no-sync python scripts/export_contract_schemas.py --check
uv run --no-sync python scripts/check_docs.py
uv run --no-sync python scripts/check_acceptance.py --stage v0.4-M6
npm run api:generate && git diff --exit-code openapi.json apps/ui/src/api/schema.d.ts
```

The two end-to-end runs are executed once per session and cached at module scope, because each
is a full graph over a real git worktree with two approval gates.

## Deviations recorded

- `tests/test_v04_m2_dispatch.py`'s `RoutingSpy` gained a `default_mode` attribute. The scheduler
  now reads the mode off the service instead of pinning it at the call site, so a stand-in for
  the service has to declare one. The test still asserts `route` was called with
  `BASELINE_ONLY`; it proves the same thing about a deployment that has not earned a learned
  mode.
- `RoutingMode` was removed from `run_manager.py`'s import list. It became unused the moment the
  pinned mode was replaced, and leaving it would have failed `ruff`.
- The end-to-end runs use a `LOW` risk, `IMPLEMENT` task created on the imported M2 fixture's
  project rather than the fixture's own `HIGH`/`OTHER` task. ADR-060 permits a fork only at
  `LOW_DIGITAL`, and `NodeContractFreezer` maps the task's risk level straight onto the node's
  risk class. `tests/test_v04_m2_end_to_end.py` is imported and not edited.
- The audited FAKE catalog exposes exactly one model id, so the two arms of a rollout are
  distinguished by their sandbox and their configuration hash rather than by their model.
  `FakeRuntime.outcomes_by_model` (M6.1) is therefore exercised by M6.1's own tests and not by
  the end-to-end pair.
