# v0.4 M7 — Guarded exploration, and the six conditions that switch it off

> **Status clarification — 2026-09-08:** Historical milestone measurements below predate the completed release audit. M7 merged in #151. The [current backlog](backlog.md#remaining-bounded-work) records the separate accounting witnesses; this banner does not claim those concerns repaired.

SDD v0.4 §9.5 is the only place in this release where the router is permitted to take an action
its own deterministic selector would not have taken. §15.3 is the list of six conditions under
which it may not. M7 is both, and the ordering between them is the milestone: the breakers, the
cost ledger and the shadow gate all have authority *over* the bandit, and none of them is a
branch inside it.

M7.1 landed the two halves that could be proved without a store — the six §15.3 predicates over
a frozen `BreakerInput`, and `CostLedger`'s conservative inequality (R5). M7.2 is the policy that
obeys them: `GuardedBandit` implementing `stages.BehaviorPolicy`, `BreakerSampler` turning a
workspace's rows into the input the six predicates read, and `ExplorationSettlement` closing the
ledger once a node's outcome lands.

The milestone claims three criteria: `AC4-M7-018`, `-019` and `-020`.

**The SDD gate for exploration.** §11.1 makes `AUTO` a position a workspace *earns*, and the
program plan makes M0–M6 the evidence it earns it with. Those gates are green on this base:
`scripts/check_acceptance.py --stage v0.4-M6` reports `in scope: 2   proven: 2   unmet MUST: 0`
and `--stage v0.4-M3` reports `in scope: 13   proven: 13   unmet MUST: 0`. Exploration is
therefore enabled on top of a shadow stage that has been measured and a feedback pipeline that
records what it costs, rather than beside them.

## Ladder

| PR | Content | Proof |
|---|---|---|
| M7.1 — `pr1-breakers-ledger` | `routing/breakers.py` (the six §15.3 predicates, `BreakerInput`, `exploration_allowed`); `routing/ledger.py` (`ExplorationCaps`, `LedgerDecision`, `LedgerEntry`, `CostLedger`) | every threshold checked at the boundary and at `math.nextafter` of it; the composite naming two tripped breakers at every position in `BREAKERS`; the first claimant on `-020` |
| M7.2 — `pr2-guarded-bandit` | `routing/bandit.py` (`GuardedBandit`, `BanditConfig`, `LedgerRegistry`); `routing/breaker_inputs.py` (`BreakerSampler`, `BreakerSamplerConfig`, `BreakerSampling`); `routing/settlement.py` (`ExplorationSettlement`); both stages wired in `routing/bootstrap.py`; all three rows flipped | the eight corners of the safe envelope, 240 draws against the recorded propensities, the second claimant on `-020` on the real `route(mode=AUTO)` path, and the Postgres twin |

## What M7.2 decides

**Explore only inside `A_safe`, and `A_safe` is wider than the ranked set.** The safe action set
is every hard-eligible candidate whose `lower_confidence_success` clears the objective's floor,
that runs under `WORKTREE` isolation and whose verifier is bound to this node's verification
spec. It deliberately keeps the Pareto-dominated candidates and the audited fallback, which the
deterministic selector's *ranked* set excludes: a dominated candidate is one the selector would
not prefer, not one it considers unsafe, and dropping it would shrink `K` and therefore *inflate*
every remaining exploration probability. A safety gate that errs should err toward less mass on
the alternatives, not more.

**Nine gates, in §9.5's order, evaluated until one refuses.** Risk class is `LOW_DIGITAL`; the
deterministic choice exists; its isolation is `WORKTREE`; the task declares no irreversible
actions; its verifier is bound to this node's spec; a learned router is active; that version has
cleared `shadow_gate` on its own decisions and rollouts *recomputed now* rather than read off a
stored report; the objective authorises a budget; the safe set holds at least two actions; the
conformal clip is above zero; the six breakers are quiet; and the ledger permits the charge.
Unlike the breakers — which are alternatives an operator has to fix together, and which are
therefore all evaluated and all named — these are a **ladder**, and the ones below only mean
anything once the ones above hold. Asking whether an ACTIVE version cleared its shadow gate for a
node that may never be explored at all would be asking a question about a decision nobody is
entitled to make.

**Inverse-gap weighting, not ε-greedy (R5, ADR4-M7-001).** `p(a) = 1/(K + γ_t·(Û(â) − Û(a)))` for
every non-greedy action, with the remainder on `â` and `γ_t = γ₀·√t`. Two properties, and both
are load-bearing downstream: every action keeps a strictly positive probability, so nothing is
ever logged at a propensity of zero and no later estimator divides by one; and the probability
decays with the *utility gap*, so a candidate that is a little worse is sampled far more often
than one that is much worse. `t` is counted per `(workspace, node class)`, the same key the
ledger uses, because a graph's tolerance for wrong answers does not pool across node classes.
Utilities are recomputed from the objective's weights rather than read off
`ConfigurationCandidate.utility_score`, which is `None` for exactly the members of `A_safe` the
selector declined to rank — and an inverse-gap distribution cannot be taken over a set whose gaps
are undefined.

**The conformal clip is what makes it a safety property (R6, ADR4-M7-001).** Inverse-gap
weighting bounds regret, and regret is an average over rounds. The clip is
`p(a) ≤ β · p_safe`, where `p_safe = 1 − ε + ε/K` is the greedy action's probability under the
ε-smoothed baseline (ε = 0.02) and `β` is one minus the split-conformal quantile of the safety
losses the workspace has logged — the shortfall of each past decision against the objective's own
verified-success floor, exchangeable over **projects** and not over rows. A workspace whose
decisions never fell short gets `β = 1` and the unclipped distribution. A workspace with fewer
projects than the quantile's index needs gets `conformal_quantile`'s vacuous 1.0, therefore
`β = 0`, and exploration is refused outright rather than performed against a point mass. That
last case is the one that matters: reading the vacuous quantile as "no constraint" would explore
hardest exactly where there is least evidence. The clipped mass goes back to `â`, so every
adjustment moves toward the deterministic choice.

**An `EXPLORE` receipt may name the greedy action.** `DecisionType` distinguishes `EXPLORE` from
`EXPLOIT` so that off-policy evaluation knows which decisions were *drawn from the behaviour
policy* — not which ones happened to differ from the baseline. A draw that lands on `â` was still
a draw, its propensity is `p(â) < 1`, and recording it as `EXPLOIT` at 1.0 would put a fabricated
number into the one field every estimator divides by. Conversely, every refusal really is 1.0:
the policy that acted had one admissible action and took it.

**A refusal is a measurement with a cause attached.** `exploration.refused` carries the sentence
of the gate that stopped it, and `breakers.tripped` carries every tripped id in `BREAKERS` order
when the breakers were the cause. An operator who restores verification coverage and turns
exploration back on into a blown calibration has been misled by the record, not by the code,
which is why the composite never short-circuits and why the receipt carries the whole list.

**Sampling fails closed on every axis (ADR4-M7-001's corollary).** `BreakerSampler` measures the
node class's *own* recent history — verification results joined through the experience records
that carry the contract signature, because a workspace-wide rate would let a busy healthy class
launder a sick one's false-acceptance rate. An unreadable calibration report, a version with no
training snapshot and a store that will not answer are three different absences and all three
land on the tripping side. The last one returns an input where five of the six breakers trip and
not merely `audit_probe_ok=False`: a sampler that reported only the probe would be claiming to
have measured five things it never read, and a later reader would take those silences for clean
bills of health.

**α comes from the objective, and the caps bind whatever α says (OQ-410, ADR4-M7-002).** The
router does not choose its own exploration rate. `ObjectiveContract.exploration_policy` carries
α, `max_explore_count` and `max_cost`, and the refusal names whichever one bound — an operator
told "the inequality would break" when the cap was the binding constraint will go and change the
wrong knob. An objective sealed before ADR-062 added the field reads its budget from three
`exploration.*` labels on a sealed revision instead, all three required together.

**The ledger has no table (ADR4-M7-003).** `LedgerRegistry` replays a workspace's `EXPLORE`
receipts in `contract_id` order and folds in every one it has not already seen, so a decision
taken a moment ago is charged against the next one. `ExplorationSettlement` replaces an
exploration's charged upper bound with the cost its experience record recorded, normalised
against the node's own `resource_cap`, which is the unit `ledger.py` states. A restart forgets
the settlements and re-charges every past exploration at its UCB — conservative in the only
direction a budget may be wrong in.

**The two stages are wired together or not at all.** `bootstrap.py` attaches `GuardedBandit` and
`ExplorationSettlement` under `AUTO` only, sharing one `LedgerRegistry`: two registries over one
store would each rebuild the same ledger from the same receipts and only one of them would ever
see a settlement. Under `SHADOW` the learned router selects and the *baseline* executes (§11.1),
so an exploration there would be a decision nothing acts on and a cost nothing incurs — which is
why the branch is `is AUTO` and not `is not BASELINE_ONLY`.

## Acceptance

| Criterion | Claiming test | Mutation that kills it |
|---|---|---|
| `AC4-M7-018` | `test_exploration_happens_only_on_low_digital_worktree_reversible_nodes` | drop the `allowed_risk_class is LOW_DIGITAL` check → `MEDIUM_DIGITAL` and `SIMULATION` corners explore and the list is no longer a singleton |
| `AC4-M7-018` | `test_a_candidate_bound_to_another_verification_spec_is_never_explored_to` | drop the verifier-binding term from `_safe_actions` → the unbound alternative rejoins `A_safe` and the second half stops refusing |
| `AC4-M7-019` | `test_explored_decisions_carry_the_propensity_they_were_actually_drawn_from` | hard-code `propensity = 1.0` (or `1/K`) → the frequency assertion fails for every action, and the sum-to-one check fails first |
| `AC4-M7-019` | `test_the_committed_receipt_records_the_propensity_and_the_charge` | stop passing the bandit's propensity through `_behave` → the row reads 1.0 and the cost labels vanish with the ledger they rebuild |
| `AC4-M7-020` | `test_a_tripped_breaker_returns_the_route_to_the_deterministic_baseline` | ignore `exploration_allowed`'s verdict → the receipt is `EXPLORE`, the propensity is below 1.0, `breakers.tripped` is absent and the ledger's `explore_count` moves to 1 |
| `AC4-M7-020` | `test_two_tripped_breakers_are_both_named_on_the_receipt` | report only the first tripped id → the label reads `calibration_exceeded` alone |
| `AC4-M7-020` | `test_any_tripped_breaker_disables_exploration_and_names_itself` (M7.1) | short-circuit the composite → one of the two names is dropped in one of the six runs |

Every marker sits on a `MemoryStore` test; `tests/test_v04_m7_postgres_store.py` carries parity
assertions only and no marker, so none of the three can classify `SKIPPED_ONLY`.

Witnesses, none of them marked:

| Test | What it would catch |
|---|---|
| `test_the_first_explore_receipt_waits_for_the_shadow_gate` | §11.1's progression collapsing — a workspace exploring before its shadow stage was measured |
| `test_an_objective_that_authorises_no_budget_never_explores` | a router that chose its own exploration rate |
| `test_an_exploration_over_the_conservative_budget_is_refused` | R5's inequality becoming decorative beside the caps |
| `test_the_absolute_count_cap_binds_whatever_alpha_says` | a generous α buying explorations past an absolute bound |
| `test_a_workspace_with_too_few_projects_cannot_clear_the_conformal_clip` | a vacuous conformal quantile read as "no constraint" |
| `test_settling_an_exploration_replaces_its_upper_bound_with_what_it_cost` | a budget that can only ever be spent and never released |
| `test_an_exploration_whose_outcome_has_not_landed_keeps_its_upper_bound` | an unmeasured exploration being credited as free |
| `test_a_decision_that_did_not_explore_is_never_settled` | a settlement crediting a round the ledger never charged |
| `test_a_settlement_that_cannot_be_made_never_fails_the_node` | bookkeeping failing a node that ran and was verified |
| `test_a_node_with_no_cost_cap_pays_the_whole_budget_for_any_spend` | an undivided cost normalising to zero and exploring for free |
| `test_the_false_acceptance_rate_is_the_node_classs_own_and_not_the_workspaces` | a sick node class hiding behind a healthy one's volume |
| `test_a_verifier_that_judged_no_claims_is_recorded_as_covering_nothing` | empty verdicts reporting the coverage of the one real one |
| `test_an_unreadable_calibration_report_is_uncalibrated_rather_than_calibrated` | a router whose bounds cannot be checked satisfying a safety breaker |
| `test_the_validated_window_is_the_training_snapshots_boundary_and_nothing_wider` | a single recorded version read as an open interval |
| `test_a_version_with_no_training_snapshot_leaves_every_component_unvalidated` | no declared boundary read as a permissive boundary |
| `test_only_the_critical_cohorts_of_the_latest_report_reach_the_breaker` | a stale report, a non-critical cohort, or a point estimate passed as a bound |
| `test_a_store_that_will_not_answer_disables_exploration_on_every_axis` | five unmeasured fields reading as five clean bills of health |
| `test_the_window_holds_the_most_recent_executions_and_not_the_first` | a workspace's first bad week frozen into every later decision |
| `test_both_backends_rebuild_the_same_exploration_ledger_from_receipts` | a budget that differs between backends because the replay took store order |
| `test_both_backends_sample_the_same_breaker_input_from_the_same_rows` | a window, a cohort or a boundary map that depends on which backend answered |

## Counts

With the lane database up, after deleting the three M7 policy rows:

```
in scope: 158   proven: 152   unmet MUST: 0
```

That is the base's `in scope: 155   proven: 149` plus three. `--stage v0.4-M7` reports
`in scope: 3   proven: 3   unmet MUST: 0`. `scripts/release_gate.py` reports `PASS` on all five
SDD §24.8 conditions.

The full suite reads `2 failed, 3305 passed, 6 skipped`. Both failures are
`tests/test_acceptance_harness.py`'s two node-id assertions
(`test_a_fixture_that_raises_is_recorded_as_an_error_and_classifies_failing`,
`test_a_claim_that_never_reported_an_outcome_is_failing_not_proven`), which compare a pytest node
id against a relative path and therefore fail in any git worktree whose checkout is not the
repository root. They are a harness artefact of where the tree lives, not a finding, and they are
untouched.

## Reproduce

```bash
uv run --no-sync ruff check . && uv run --no-sync mypy src
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin \
  tests/test_v04_m7_bandit.py tests/test_v04_m7_breaker_inputs.py \
  tests/test_v04_m7_breakers.py tests/test_v04_m7_ledger.py \
  tests/test_v04_m7_postgres_store.py
uv run --no-sync python scripts/export_contract_schemas.py --check
uv run --no-sync python scripts/check_docs.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python scripts/check_acceptance.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python scripts/release_gate.py
```

The bandit's tests script the scorer rather than fitting one, for the reason M8's gate tests give:
what is under test is what the policy does with a prediction, and a fitted model would make the
arithmetic depend on the fit. The shadow stage behind the fixture is real — thirty complete
`(SHADOW, CONTROL)` pairs, which is `shadow.DEFAULT_MIN_PAIRED_RUNS` exactly — because the bar
exploration waits behind is the bar M8 promotes under, and a fixture that cleared a softer one
would prove exploration waits for different evidence than §11.1 names. The replicate count is
lowered to 64 in the property test's `ShadowReportConfig` and nowhere else: it moves the width of
the interval, not which side of the floor the bound falls on, and a 240-draw test recomputing a
2 000-replicate bootstrap on every draw would spend its whole budget there.
