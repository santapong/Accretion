# v0.4 M10c — the ten ablations, and the learned comparators that make them measurable

Protocol §14 requires ten ablations. Before this PR the router benchmark could not run one of
them, for a reason that is easy to state and was easy to miss: three of the eleven comparators
in §8.1 were `NotAvailablePolicy` placeholders, and the three were exactly M7, M8 and M9 — the
v0.4 stack the ablations are ablations *of*. A table of ten rows, none of which had anything
to remove, is not a required-ablations section; it is a promise to write one.

M10c does two things. `routing/flags.py` turns §14's table into ten registered, loadable
configurations, and `evals/router/ablations.v1.json` is that table on disk. `routing/
baselines.py` makes M7, M8 and M9 real policies over the replay corpus, so there is something
for each flag to switch off.

## What each flag switches, and where

| Ablation | Flag | Where it is read | What changes |
|---|---|---|---|
| A1 | `hierarchical_construction` | `routing/candidates.py`; the benchmark's ranking | Build side: behaviourally equivalent tuples are no longer collapsed by configuration signature, so duplicates compete for the beam. Replay side: the ranking is taken over the whole slate instead of inside the best-scoring provider family. |
| A2 | `compatibility_pruning` | `routing/candidates.py`; the benchmark's ranking | A configuration the joint-compatibility rules refuse stays in the slate as a candidate that is not hard-eligible, instead of being rejected. The rule still runs and the decision is still recorded — A2 removes pruning, not auditing. It never promotes a refused configuration to the audited fallback. |
| A3 | `experience_retrieval` | `routing/service.py`; the benchmark's features | Retrieval is skipped and reported on the §15.1 ladder as `EVIDENCE_UNAVAILABLE`. In the benchmark the two retrieved-history columns are `None` in both fitting and serving. |
| A4 | `project_adapter` | The benchmark's M8/M9 | The project adapter is not fitted and not applied; M8 is M7. |
| A5 | `node_feedback` | The benchmark's training targets | The node-success head is fitted on the run's outcome instead of the node's own verdict. |
| A6 | `final_run_feedback` | The benchmark's training targets | The run head is fitted on the node's verdict, so the score's run term carries no independent information. |
| A7 | `guarded_exploration` | `routing/service.py`; the benchmark's M9 | The service holds a `DeterministicBehavior` — the injected bandit is *replaced*, not merely skipped. M9 never draws and every propensity is exactly 1.0. |
| A8 | `independent_verification` | The benchmark's labels | A false acceptance counts as a success, which is what learning from self-reported verdicts means. |
| A9 | `uncertainty_gate` | The benchmark's ranking | The conformal lower bound and the conservative cost/latency bounds are replaced by the calibrated means; the success floor no longer filters the slate. |
| A10 | `evi_recovery_stop` | The benchmark's cells | Every trial of every cell is paid for and pooled, instead of stopping after a first trial that verified. |

Five of the ten are replay-side by nature — A5, A6, A8 and A10 change *which evidence the
policy was fitted on*, and A3 changes it at both ends. Those have no service-side hunk and
should not: there is no way to remove node-level feedback from a live router except by
training a different one.

`RouterFeatureFlags.labels()` is empty for `FULL`. That is the property the default rests on:
a deployment that names no flags writes the receipts it wrote before this file existed, so an
ablation can never be mistaken for a production decision.

## The learned comparators

- **M7** — one bagged ranker fitted on the corpus's *selection* half, applied node by node.
  Candidates are ranked by expected utility: the predicted utility of the configuration
  weighted by its predicted chance of verifying, with the registered invalid-action penalty
  charged for the complement. Ranking on utility alone is M1's method and ranking on predicted
  success alone is M5's; M7 is neither.
- **M8** — M7 plus a project adapter fitted on the project's own log. An evaluation project has
  no rows in the training half by construction, so the only local evidence that exists is the
  outcome of the actions the policy has already taken in that project, in the order it took
  them. The correction at task *n* is a function of tasks 1..n-1 and of the arm actually
  played, and of nothing else.
- **M9** — M8 drawn under §9.5's inverse-gap weighting with the ε-smoothed conformal clip, and
  charged to a real `CostLedger` with the shipped conservative inequality and absolute caps.
  Explorations are settled at the corpus's observed cost, so the inequality governing the next
  draw is evaluated against what the last one actually spent.

## What the replay cannot check, and does not claim to

§9.5 admits an exploration only on a `LOW_DIGITAL` node, under worktree isolation, bound to
the node's verification spec, under an `ACTIVE` version past its shadow gate, with the six
§15.3 breakers untripped. A corpus of tasks and traces records none of those. The replay
evaluates the gates it can — the node class, hard eligibility, a safe set with more than one
member, the conformal clip and the ledger — and this note is where the rest are named instead
of being silently reported as passed.

Three constants are declared for the replay rather than inherited, and each is arithmetic
rather than taste:

- **`BENCHMARK_CONFORMAL_ALPHA = 0.25`.** `conformal_quantile` needs more than `1/α − 1`
  exchangeable groups and returns the vacuous residual 1.0 otherwise. The training half is six
  projects, three of which calibrate, so a 95% bound over projects is not a bound this corpus
  can support: every lower bound would be zero and M7 through M9 would tie on every task.
  Quoting 75% and saying so is the honest form of that; grouping by row to reach 95% would not
  be.
- **`ADAPTER_HALF_LIFE = 2.0`.** Production's half-life is twenty. Projects here have three
  nodes, so an adapter is applied after at most two outcomes and would carry at most `2/22` of
  its correction; A4 would then measure the half-life rather than local adaptation.
- **`REPLAY_EXPLORATION_POLICY`.** In production the budget is the objective contract's,
  because the approver sets it. A replay has no approver, so M9 declares its own beside the
  policy it governs — changing it changes what M9 *is*, and that belongs in a diff.

## Measured on the shipped corpus (evaluation half, registered weights)

| Policy | Mean utility | Mean regret | Explored rows |
|---|---|---|---|
| M7 | 0.6739 | 0.0173 | — |
| M8 | 0.6579 | 0.0334 | — |
| M9 | 0.6467 | 0.0446 | 11 of 18 |
| ORACLE | 0.6912 | 0.0000 | — |

Two of those are results worth reading rather than numbers to be tidied. Exploration costs
utility here, and should: a replay has no future in which the information bought can be spent,
so M9 pays for the draws and collects nothing, which is the correct sign for a benchmark with
no learning loop in it. And A1 and A8 leave the evaluation-half rows unchanged — the
factorization narrows the slate on all thirty-six tasks and moves the top choice on three of
them, all in the selection half, and eight false-accept cells in four hundred and thirty-two
are too few to move a fit. "This component changed nothing on this corpus" is an ablation
result. It is reported, not fixed.

## Each ablation is shown to change something

Ten entries that run is §14's obligation and is not evidence that anything was removed: a
flag read quietly turned into a no-op still runs and still returns rows, and the table would
go on reporting ten measurements of one router. So `tests/test_v04_m10_ablations.py` runs
each comparator twice over one corpus — under `FULL` and under the ablation — and pins the
divergence to that component and no other. Two of the ten needed a constructed fixture rather
than the shipped corpus, and the reason is worth recording:

- **A8.** Every false acceptance in the committed traces is on the *evaluation* side, so the
  relabelling never enters a fit and no choice moves — which is the result reported above. The
  test therefore builds a corpus whose fitting half is entirely false acceptances, where the
  full router finds no successes at all and the ablation finds exactly the wrongly-given
  passes, and shows the two fits routing differently.
- **A9's success floor.** On the shipped corpus the conformal lower bound is above the floor
  for every eligible configuration on a task or below it for all of them, so the filter never
  gets to *choose* and the ablation's effect there is invisible. The floor is therefore
  exercised on a hand-built slate. The flag's other three reads — the node bound, the run
  bound and the conservative cost/latency/quality bounds — are all measured on the corpus.

`_Scored` carries `run_success` beside the two node estimates for the same reason: §7.6 ranks
on both heads, A9 changes which end of each interval is read, and a record that kept only the
number that happened to be used would make half of that ranking unauditable.

## Deliberate widening

`tests/test_v04_m4_train.py::test_no_module_outside_the_loader_assembles_a_learned_predictor`
(AC4-M4-016) now allows `routing/baselines.py` to assemble a `LearnedOutcomePredictor`. The
invariant protects the routing path, where a predictor loaded from artifact bytes has passed a
digest check and no promotion evaluation. The benchmark's comparators fit their artefact
in-process from a frozen corpus, load nothing, mint no `RouterModelVersion`, and are reachable
only through a runner that refuses any execution source but `REPLAY`. The mutation the test
still kills — a dispatcher or a service module calling the classmethod — is untouched.

Four further test files changed as a direct consequence of M7, M8 and M9 becoming real, and
are named here so a later audit does not have to rediscover why:
`tests/test_v04_m10_baselines.py` and `tests/test_v04_m10_router_benchmark.py` asserted that
those three were `NOT_AVAILABLE`; `tests/test_v04_m10_pilot.py` and
`tests/test_v04_m10_benchmark_api.py` asserted the same through the pilot route and the API.
`tests/router_corpus_generator.py` gained the `ablations_path` field the config now carries.
