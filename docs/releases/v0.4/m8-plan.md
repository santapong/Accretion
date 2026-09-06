# v0.4 M8 — Promotion, rollback, and the gate that authorises them

SDD v0.4 §10.3 gives promotion two words: *atomic* and *reversible*. M0 delivered neither.
Atomicity was missing because the version rows and the record of which one was serving were
separate writes; reversibility was missing because "serving" was a `status` column on an
append-only table, so the first `ACTIVE` row could never be retired and a workspace was
activatable exactly once, forever. §10.2 then asks a second question the milestone has to
answer before either word means anything: *on what evidence* may a router be promoted at all.

M8 closes both. M8.1 replaced "active" with the head of an append-only ledger and made a
promotion one transaction with a rehearsed reversal. M8.2 puts a gate in front of it: a
calibrated safe-improvement test over a pessimistic off-policy estimator, on a holdout that is
project-disjoint from what the candidate was fitted on, with the shadow stage and the rollback
drill as inputs — and it wires the ledger head into routing, so what the gate promotes is what
the next request is actually routed by.

The milestone claims six criteria: `AC4-M8-035`, `-036`, `-037`, `-038`, `-039` and `-042`.

## Ladder

| PR | Content | Proof |
|---|---|---|
| M8.1 — `pr1-activation-ledger` | migration `0019` (retires M0's two partial `ACTIVE` indexes); `routing/activation.py` (`ActivationLedger`, `LedgerActiveVersionResolver`, the two family keys); `routing/promotion.py` (`RollbackDrill`, `PromotionService.promote`/`.rollback`); `persistence/store.py`'s composite `activate_router_version` and its contiguity guard; `api/router_admin.py`'s two mutating routes | markers for `-038` and the first half of `-039`; both stores refuse `sequence = head + 2` with identical text; the Postgres twin |
| M8.2 — `pr2-promotion-gate` | `routing/ope.py` (R4's Logarithmic-Smoothing estimator, the SNIPS/DR/ESS/clip-mass diagnostics, the sup-t band, `PromotionConfig`); `evals/router/promotion.v1.json`; `PromotionEvaluator`; the ledger resolver wired into `routing/bootstrap.py`; `api/router_admin.py`'s three read/evaluate routes; all six rows flipped | the four claim tests below, the adversarial trio, and the second half of `-039` routed end to end under `mode=AUTO` |

## What M8.2 decides

**The estimator is pessimistic before it is anything else (R4, OQ-414).** Inverse propensity
scoring is unbiased with unbounded variance, so the policy it likes best is very often the one
whose weights blew up on three lucky rows. `ope.ls_estimate` replaces each `w·c` with
`-(1/λ)·log(1 - λ·w·c)`, which for costs in `[-1, 0]` never credits a large weight with the
full amount IPS would; λ is fixed at `1/√n` by `lambda_rule` and is not tuned, because a gate
that could choose λ after reading the rows would have one free parameter per disappointing
candidate. SNIPS and DR are computed and are explicitly **diagnostics**: R4's bound does not
cover them, so they may inform a reader and may not decide a release.

**Costs are in `[-1, 0]` and the boundary refuses a reward.** R4's bound is stated for bounded
costs under minimisation, and the sign is what keeps `log1p` on the branch where it converges:
a reward handed in would send the estimate to `-∞` for one large weight, which is exactly the
optimism the smoothing exists to remove. `_checked` refuses rather than clipping — clipping a
sign error to zero turns a mistake into a number that looks like a measurement.

**Deferring to the incumbent is a zero weight, not a shorter list.** The policy family is R3's
threshold class: *take the learned action where its lower-confidence success is at least `c`,
and defer everywhere else*. Rows the threshold declines stay in the sample with weight zero and
contribute the incumbent model's own expected cost. Filtering them out instead would shrink `n`
as the threshold tightened, and λ = 1/√n would grow with it — making the most selective policy
look best for a reason that has nothing to do with the policy.

**The threshold is chosen on one half and tested on the other, and the band is simultaneous
anyway.** `PromotionConfig.tune_projects` splits the holdout's projects 20/80 by position in
the sorted list — deterministic, because R3 needs the two halves disjoint and a shuffled split
would make `c*` depend on a seed nobody quoted. `c*` maximises the improvement on the tune
half; the report reads `ope.sup_t_band` at `c*` on the evaluation half. Both, not either: the
band studentises each grid column and takes the maximum absolute *t* within each bootstrap
replicate, so its coverage is simultaneous over the whole grid and the thresholds' correlation
comes from the data rather than from a Bonferroni factor over ten nearly-identical policies.
`stats.bonferroni` still governs the *family* of comparisons — the per-cohort level is
`α / k` over the cohorts examined.

**Leakage raises; everything else is a finding.** A regression, an artefact that no longer
hashes to its digest, a rollback target that will not drill, an uncalibrated candidate and a
shadow stage with too little evidence all land *inside* the report, because each is something
an operator needs written down and attributable. A holdout that is not project-disjoint is not
in that category: every number in such a report would be a statement about memorisation wearing
the name of a generalisation claim, and a document an operator could reasonably read and act on
is worse than no document. `HoldoutLeakageError` therefore writes nothing.

**Project-disjointness is checked twice, and the second check is the one that matters.**
`split.assert_disjoint` reads what the two snapshots *declare*, which a leaking snapshot can
satisfy while still leaking: `included_experience_ids` is the manifest and `split` is a
description of it. So `_refuse_leakage` dereferences every record the holdout names to the
project it came from and compares that against the training snapshot's declared training
projects unioned with the projects its own manifest actually drew from.

**A critical cohort blocks whatever the mean did.** `CohortResult.critical` comes from the
registered `critical_cohorts` list rather than from a hard-coded set, so "a critical regression
blocks" stays true when OQ-413's open list changes. Three of the five cohorts are derived from
typed fields (`high_risk` from the risk class, `verifier_conflict` from the contradiction
status or a `VERIFICATION_CONFLICT` failure, `policy` from a `POLICY_RISK` failure or a
`PROHIBITED` class) and `correctness` from whether the verifier reached a judgement at all.
`secrets` has nothing in an experience record to key on — whether a node handled a credential
is a property of what it *did* — so it is declared through the `accretion.evaluation-cohort`
label rather than proxied by something that would be wrong invisibly.

**A cohort with no members passes, and says so with a zero.** Failing an absent cohort would
block every promotion in a workspace that has never run a secret-handling node, which would
make the cohort list a liveness requirement rather than a safety gate. The `sample_size` beside
the comparison is what keeps that honest, on the reasoning `ShadowSummary` already gives about
agreement over four decisions and over four thousand.

**The three §10.2 gates are read off sealed documents, not recomputed.** Each version's
`HoldoutEvaluation` is the document `LearnedPredictorLoader` already refuses to route without,
sealed at fitting time and digest-pinned by the version. Recomputing them here would be grading
the same homework with a second pencil; reading the sealed claims makes the comparison
falsifiable by anyone holding the two digests. The two rate comparisons carry conservative
Clopper–Pearson difference intervals — the candidate's lower limit against the baseline's upper
— because the two evaluations are independent samples with no row-level pairing. The
calibration comparison carries a degenerate interval on purpose: ECE has no closed form here
and its gate is an absolute ceiling rather than a contrast, so an invented width would be the
only fiction in the report.

**`REQUIRE_REVIEW` is a verdict and not an absence of one.** A candidate that showed no
regression and also did not show the improvement `delta_min` asks for is reviewed rather than
rejected; a candidate with too little shadow evidence is reviewed with the bound it missed
disclosed on the finding, because `RouterPromotionReport` refuses an undisclosed tradeoff.
`REJECT` is reserved for something critical: a failed critical cohort, a failed mandatory
non-regression, an ECE over the ceiling, an artefact that will not verify, a rollback target
that will not drill.

**A retry is a retry only when it carries a key.** With `Idempotency-Key` the report id is
derived from candidate, baseline, holdout and key, so a replayed request finds the sealed
report and returns it. Without one, every call is a fresh sealed claim — two evaluations of the
same triple a month apart are genuinely claims about different evidence, and returning the
older verdict would be the wrong answer rather than an efficient one.

**Routing now reads the ledger head.** `bootstrap.py` installs
`LedgerActiveVersionResolver` where `StatusActiveVersionResolver` was. Promotion and rollback
both *append* `ACTIVE` rows and never retire the original, so "the latest ACTIVE row" and "the
head" answer differently from the moment a withdrawal lands — which is the moment an incident
depends on them agreeing. `LedgerActiveVersionResolver.resolve` now returns
`stages.ActiveVersions` rather than the local mirror M8.1 declared while `stages.py` was still
in flight.

**The gate's diagnostics travel with its verdict.** ESS, the clip mass at a fixed τ, the SNIPS
cross-check and the retained/scored row counts go on the report's labels. They decide nothing;
they are how a reader decides whether to believe what was decided. "n = 24" and "ESS = 24" are
the same holdout and the same evidence; "n = 24, ESS = 3" is not.

## Acceptance

| Criterion | Claiming test | Mutation that kills it |
|---|---|---|
| `AC4-M8-035` | `test_a_stored_snapshot_rebuilds_to_the_same_bytes_and_a_second_put_is_a_no_op` | stamp `created_at` from a live clock instead of the injected one → the rebuilt `content_hash` moves and the second `put` conflicts instead of being a no-op |
| `AC4-M8-035` | `test_the_same_evidence_gathered_in_another_order_seals_the_same_snapshot` | drop the manifest sort in `_deduplicate` → the reversed gathering seals a different `contract_id`, `content_hash` and manifest |
| `AC4-M8-035` | `test_a_snapshot_rebuilt_under_a_live_clock_is_refused_rather_than_overwritten` | let the store overwrite a sealed row → the refusal disappears and the stored digest moves |
| `AC4-M8-036` | `test_a_holdout_naming_a_training_projects_record_is_refused_and_writes_nothing` | delete the explicit manifest check → the declared-list validator passes the leaking holdout |
| `AC4-M8-036` | `test_a_project_disjoint_holdout_is_accepted_and_scored` | refuse every holdout → the disjoint case stops being scored |
| `AC4-M8-037` | `test_a_critical_cohort_regression_rejects_a_candidate_whose_mean_improved` | read only the mean → a positive primary promotes over a failing critical cohort |
| `AC4-M8-037` | `test_the_same_numbers_promote_once_that_cohort_is_no_longer_critical` | hard-code the critical set → removing `secrets` from the registered list changes nothing |
| `AC4-M8-038` | `test_a_rollback_target_that_will_not_drill_cannot_produce_a_promotable_report` | drop the drill from `evaluate` → an undrillable target still yields a promotable report |
| `AC4-M8-038` | `test_a_report_whose_rollback_target_cannot_be_drilled_writes_no_row` (M8.1) | move the drill after the write → a refusal leaves a version row and a ledger entry behind |
| `AC4-M8-039` | `test_routing_pins_the_head_before_and_after_a_rollback_and_rewrites_no_receipt` | read `router_model_versions.status` instead of the ledger head → both requests pin the `ACTIVE` row no activation ever named (measured: fails 5 runs out of 5) |
| `AC4-M8-039` | `test_a_rollback_preserves_every_receipt_and_leaves_one_head` (M8.1) | amend a receipt on rollback → the re-sealed digest stops matching |
| `AC4-M8-042` | `test_the_lineage_route_returns_the_parent_chain_the_history_and_the_report_ids` | drop `activations` from the response, or filter them to entries naming this version → the history and the report ids vanish |
| `AC4-M8-042` | `test_a_member_can_read_the_report_that_authorised_a_promotion` | require administration on the read → a member can no longer audit the release |

Every marker sits on a `MemoryStore` test; `tests/test_v04_m8_postgres_store.py` carries parity
assertions only and no marker, so none of the six can classify `SKIPPED_ONLY`.

Adversarial witnesses, none of them marked:

| Test | What it would catch |
|---|---|
| `test_a_large_mean_improvement_does_not_buy_a_small_critical_regression` | an aggregate metric concealing the slice §10.3 protects |
| `test_a_rejected_report_cannot_be_turned_into_an_activation` | the gate and the ledger disagreeing about what a `REJECT` means |
| `test_a_candidate_whose_bytes_no_longer_verify_is_rejected_and_claims_nothing` | a report full of real-looking numbers about a different model |
| `test_a_candidate_that_was_never_evaluated_cannot_pass_the_sealed_gates` | AC4-M4-016's rule not holding at the promotion boundary |
| `test_a_holdout_logged_at_low_propensity_is_priced_pessimistically` | a gate quietly using IPS where it says it uses LS |
| `test_the_report_carries_the_diagnostics_that_say_whether_to_believe_it` | a verdict shipped without the ESS behind it |
| `test_the_smoothed_estimate_converges_to_inverse_propensity_scoring_as_lambda_vanishes` | a smoothing that lost its sign, its reciprocal or its direction |
| `test_a_column_that_never_moved_gets_a_degenerate_interval_and_not_a_crash` | a zero standard error becoming a division rather than an interval |
| `test_a_leaking_holdout_returns_its_own_code_and_not_a_500` | a refusal reaching the wire as an internal error |

## Counts

With the lane database up, after deleting the six M8 policy rows:

```
in scope: 140   proven: 134   unmet MUST: 0
```

That is the base's `in scope: 134   proven: 128` plus six. `--stage v0.4-M8` reports
`in scope: 6   proven: 6   unmet MUST: 0`. `scripts/release_gate.py` reports `PASS` on all five
SDD §24.8 conditions.

The full suite reads `2 failed, 3223 passed, 6 skipped`. Both failures are
`tests/test_acceptance_harness.py`'s two node-id assertions
(`test_a_fixture_that_raises_is_recorded_as_an_error_and_classifies_failing`,
`test_a_claim_that_never_reported_an_outcome_is_failing_not_proven`), which compare a pytest
node id against a relative path and therefore fail in any git worktree whose checkout is not
the repository root. They are a harness artefact of where the tree lives, not a finding, and
they are untouched.

## Reproduce

```bash
uv run --no-sync ruff check . && uv run --no-sync mypy src
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin \
  tests/test_v04_m8_ope.py tests/test_v04_m8_evaluator.py tests/test_v04_m8_api.py \
  tests/test_v04_m8_adversarial.py tests/test_v04_m8_promotion.py \
  tests/test_v04_m8_activation.py tests/test_v04_m8_postgres_store.py
uv run --no-sync python scripts/export_contract_schemas.py --check
uv run --no-sync python scripts/check_docs.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python scripts/check_acceptance.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python scripts/release_gate.py
npm run api:generate && git diff --exit-code -- apps/ui/src/api/schema.d.ts
```

The gate's own tests script the predictor rather than fitting one. What is under test is what
the gate does *to* a prediction — the threshold family, the smoothing, the band, the cohorts,
the three sealed gates — and real fitted numbers would make every assertion a statement about
the trainer's arithmetic instead. Everything else in those tests is real: real experience
records, a real `SnapshotBuilder`, real materialization through the real vocabulary, and
contracts sealed by their own validators. `tests/test_v04_m8_promotion.py`'s drill and routing
tests still use the corpus M4 trains once per session, because what *they* exercise is whether
stored bytes still load.

## Deviations recorded

- **`utility_weights` is in `promotion.v1.json` and was not in the brief's field list.**
  `shadow_report` prices quality, cost and latency against each other and cannot be called
  without them, and they are exactly the kind of constant a registered gate file exists to
  freeze. The registered values are the corpus weights §21 item 3 names (1.0 / 0.3 / 0.15).
- **`PromotionEvaluator` takes a keyword-only `vocabulary`.** A snapshot records a vocabulary
  *digest* and nothing can reconstruct the table from it, so `materialize` needs the table
  passed in. The default is `Vocabulary.frozen_over()`, the same empty table
  `routing/coldstart.py` featurizes under, which is what the HTTP route uses; a workspace that
  freezes another one passes it, on the precedent of `LedgerActiveVersionResolver`'s
  `algorithm_id`.
- **`evaluate` takes a keyword-only `idempotency_key`.** Without it a replayed `POST` would
  seal a second report for one request, which is the failure the header exists to prevent. The
  key is not what makes the operation safe — the derived id is — and it is recorded on the
  report's labels for the operator tracing which request produced it.
- **The evaluator has its own join from snapshot rows to feature rows.** `RouterTrainingService`
  has an equivalent private one and `train.py` is outside this PR's file list, so the join is
  written twice. Both call the same public `materialize`, `summarize_evidence` and `featurize`;
  a milestone that owns `train.py` should lift the shared part into a function rather than
  leave two copies drifting.
- **`tests/test_v04_m8_activation.py` was edited outside the brief's test list.** It constructed
  the `LedgerActiveVersions` dataclass this PR deletes; the three references now name
  `stages.ActiveVersions`, which is the same four fields and the same assertions.
