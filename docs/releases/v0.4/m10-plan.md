# v0.4 M10 — The research instrument

SDD v0.4 §18 asks for a benchmark that can answer one question honestly: does choosing a
configuration per node beat choosing one good configuration and keeping it? M10 builds the
instrument and then uses it exactly once. The milestone owns six criteria — `AC4-M10-045`
through `-050` — and closes the v0.4 acceptance program with them.

The thing M10d adds is not another measurement. It is the reason a measurement counts: the
numbers this release quotes come from corpora nobody iterated on, generated from seeds nobody
had seen, readable only while the pre-registration on disk still hashes to the digest it was
frozen at, and every read of them is recorded in a file a reviewer has to approve.

## Ladder

| PR | Content | Proof |
|---|---|---|
| M10a — statistics and lineage splits (#126, #127) | `routing/stats.py`, `routing/split.py`, `evals/router/projects.v1.json` | exact intervals with no scipy; lineage-keyed splits that keep a fork and its upstream together |
| M10b — baselines, corpus and pilot (#135, #150) | `routing/baselines.py`, `routing/regret.py`, `router_benchmark.py`, `evals/router/`, `routing/pilot.py`, `scripts/router_pilot.py`, the §21 pre-registration | the corpus is byte-reproducible from its seed; the benchmark refuses any source but REPLAY at the route and again inside the runner |
| M10c — the ablations and the real learned three (#154) | `routing/flags.py`, `evals/router/ablations.v1.json`; M7/M8/M9 compose the real ranker, adapter and guarded bandit | ten ablations run from a table the *corpus* names; `-048` and `-049` flipped |
| §21 freeze (#155) | `preregistration_sha256` on `RouterBenchmarkConfig`, pinned in `evals/router/config.v1.json` | a test proves the pin equals the file's digest |
| M10d — the locked test, the drift holdout and the results page (this) | `evals/router/{locked,drift}/`, `routing/locked_test.py`, `scripts/router_locked_test.py`, `docs/research/v0.4/{results.md,access-log.jsonl}`; `-045`, `-046`, `-047`, `-050` flipped | the door refuses three ways, the page is regenerated and diffed rather than typed |

## What M10d decides

**A locked corpus is a corpus, not a document (ADR4-M10-004).** The plan named
`test.v1.json` and `drift.v1.json`. A corpus is four documents plus the lineage registry its
split is proved against, and `SelectionSplit` is `extra="forbid"` with exactly two lists, so
there was nowhere to put a second split. `evals/router/locked` and `evals/router/drift` are
directories, each generated from `tests/router_corpus_generator.py` by the same `build()` the
development corpus comes from — new keyword arguments, not a second generator, because the only
defensible claim about a locked test set is that it is the *same experiment* at a seed nobody has
seen, and two functions that were meant to agree would eventually not.

Each carries its own generated `projects.v1.json`. That is what keeps the hand-authored
development registry at exactly twelve projects, which `tests/test_v04_m10_split.py` pins by a
golden split; adding the locked lineages to it would have re-answered every published split.

**Three refusals, in order, and each one paired with what it still allows.** The pre-registration
must still hash to the pin (both digests in the error); `ACCRETION_ROUTER_LOCKED_TEST=1` must
reach the runner through the new `Settings.router_locked_test`; and no `RouterTrainingSnapshot`
handed in may name a locked project in **any** of its three groups. Holdout is refused along with
training and validation on purpose: a snapshot that sealed a locked project into its holdout was
still cut from a registry that contains it, and the next cut of that registry is one seed away
from putting it in training. The development corpus cannot be read through the door at all, so
the access log's row count means what the audit says it means.

**The log is a committed file, because the contract that would have held it is frozen
(ADR4-M10-003).** `BenchmarkRun.suite` is `Literal["ACR-ARCH"]`, so the planned
`ROUTER-LOCKED-TEST` benchmark row would have required a Major contract change in the milestone
whose job is to measure the release. Each released read appends one `TestSetAccessEntry.to_rows()`
row to `docs/research/v0.4/access-log.jsonl`. Over-reading the locked set now shows up in a diff.

**The drift holdout is required and is reported with the same estimator (ADR4-M10-001).**
`routing/split.py` already allocated a `DRIFT` quota by seeded root hash and said so in its own
docstring: no era key was read. A holdout allocated by hash is a second sample, and the locked
corpus is already a second sample. So `evals/router/drift` is a whole corpus whose lineages are
all provider era `2026-H2` and whose runtimes are a minor version later — both levels
pre-registration item 8 names — and `results.md` renders it with the same two functions that
render the headline.

**The page is generated and diffed, not written.** `render_blocks` produces seven named text
blocks; `results.md` quotes them inside `text` fences; a test regenerates them from the committed
corpora and requires the two mappings to be equal. A number in that page that nobody can
reproduce cannot survive the suite.

**A corpus root is no longer at a fixed depth (ADR4-M10-006).** Two places in
`router_benchmark.py` were counting parent directories and answered differently for a nested
corpus: `ablations_path` resolved a relative path against `root.parents[1]`, and `_validate_split`
read the shipped registry unconditionally. Both now use a fixed point — a `REPOSITORY_ROOT`
derived from the package's location, and `RouterBenchmarkCorpus.project_registry()` reading beside
the corpus with the shipped registry as fallback. Neither moves a byte of what the development
corpus resolves to.

## What the locked read found

**One superiority statement, and it is the paired one.** Pre-registration item 5 makes the
project-clustered paired regret contrast the primary analysis and item 2 makes M9-against-the-best-
fixed the primary comparison. That contrast excludes zero at the adjusted level on the locked
corpus, `[0.009603, 0.939517]`, and replicates on the drift holdout, `[0.039887, 0.320070]`.

**No binary superiority, and no recovered fraction.** `g_learn` is 0.166667 with an interval that
spans zero, and `g_out`'s lower limit is not positive — so `recovered_fraction` is `None` rather
than a ratio with an undefined sign. Item 1 predicted this before the read: verified success at 18
evaluation tasks would need roughly 6,000 units at δ = 0.02 and was registered as a gate rather
than as a powered comparison.

**Both gates fail for every policy, and that is a property of the frozen size (ADR4-M10-005).**
The corpus pools a cell's trials conservatively — verified on *every* trial, false-accepting on
*any* — and those rules were chosen when a cell held two trials. At eighteen the locked corpus's
per-trial verified rate of 0.5121 pools to 0.0870 and its per-trial false-acceptance rate of
0.0390 pools to 0.4348, while the registered floor (0.70) and ceiling (0.05) were set on the
per-trial scale. Repairing the pooling rule after seeing those rows is precisely the post-hoc
analysis change the pre-registration exists to prevent, and item 1 is frozen. It is reported as
measured, the arithmetic is stated on the page, and the fix is an amendment for the next
pre-registration. The episode is also the strongest argument this milestone produced for
`GateReport` and `RegretReport` being different types: the two columns disagree, and the
disagreement is readable only because neither can be computed from the other.

**The priced real-provider run is recorded as not run.** Every v0.4 number is a replay.

## Acceptance

| Criterion | Claiming test | Mutation that kills it |
|---|---|---|
| `AC4-M10-045` | `test_a_snapshot_that_names_a_locked_project_stops_the_read_in_any_group` | `if False:` the `assert_no_training_overlap` call in `LockedTestRunner.run` → the leaked project is read and no `SplitViolation` is raised |
| `AC4-M10-046` | `test_every_one_of_the_eleven_policies_runs_on_the_locked_evaluation_half` | make `baseline_for("M9")` return `NotAvailablePolicy` → M9 comes back `available=False` with `NOT_AVAILABLE` |
| `AC4-M10-047` | `test_regret_is_recomputed_identically_from_a_cold_store` | read the selected configuration from the runner's cached row instead of from the stored receipt → the re-pointed receipt no longer moves its row |
| `AC4-M10-048` | `test_gates_are_reported_separately_from_utility` (M10c) | compute `verified_success_rate` from a thresholded utility → re-weighting the objective moves the gate |
| `AC4-M10-049` | `test_the_table_is_the_one_the_corpus_names_and_not_the_one_the_code_knows` (M10c) | resolve the ablation table by convention instead of through `config.ablations_path` → the repointed corpus keeps answering from the shipped table |
| `AC4-M10-050` | `test_a_router_that_is_the_baseline_by_construction_makes_no_superiority_claim` | return `g_learn / g_out` unconditionally → a ratio is reported where the denominator has not been shown to differ from zero |
| `AC4-M10-050` | `test_editing_one_frozen_field_makes_the_locked_runner_refuse_with_both_digests` | compare the pin against itself instead of against the file → the amended `alpha = 0.10` page is read without complaint |
| `AC4-M10-050` | `test_a_difference_significant_at_alpha_is_not_significant_at_the_adjusted_level` | make `bonferroni(alpha, k)` return `alpha` → the α-significant difference stays significant after correction |

Supporting witnesses, none of them marked:

| Test | What it would catch |
|---|---|
| `test_a_positive_opportunity_gap_does_report_a_recovered_fraction` | deleting the whole `recovered_fraction` computation, which the negative control alone would not notice |
| `test_the_release_flag_alone_decides_whether_the_locked_set_may_be_read` | reading the flag through the `lru_cache`d `get_settings()`, so the value answers for import time rather than for the operator |
| `test_one_released_read_appends_exactly_one_row_and_the_next_appends_one_more` | a log written on success only, which under-counts exactly the reads somebody had a reason not to finish |
| `test_the_development_corpus_is_not_readable_through_the_locked_door` | a door that also admits `evals/router`, which would fill the audited log with rows that mean nothing |
| `test_a_hand_edited_access_log_line_is_refused_rather_than_repaired` | a reader that silently normalises a short row, so a trimmed log still parses |
| `test_the_results_page_quotes_the_blocks_the_locked_run_generates` | a number in `results.md` adjusted by one digit, or a block quietly dropped from the page |
| `test_the_committed_access_log_holds_the_release_read_and_nothing_else` | a suite that appends to the committed log, which would make the audited row count meaningless |
| `test_no_lineage_is_shared_between_the_three_corpora` | a derived corpus that kept a repository digest, which unions it with the corpus everybody iterated on while every id stays distinct |
| `test_both_corpora_carry_eighteen_trials_in_every_cell_of_a_complete_grid` | a corpus regenerated at the development size, which loads, runs and reports while being under-powered ninefold |
| `test_every_drift_lineage_is_the_later_provider_era_and_every_runtime_is_later` | a holdout that moved the label and not the serving window |
| `test_the_locked_corpus_is_exactly_what_its_seed_generates` | a trace edited until the result looked better |

## Counts

Measured in a worktree with no PostgreSQL container:

```
in scope: 167   proven: 158   unmet MUST: 1
```

`--stage v0.4-M10` reports `in scope: 6   proven: 6   unmet MUST: 0` and `PASS`.

The single unmet MUST is `V02-P5-001`, whose claiming test
(`tests/test_p5_dynamic_service.py::test_p5_api_surface_is_additive_and_project_gated`) needs a
database and fails with an `asyncpg` connection error when none is up. It is the same artefact
M9d recorded. With PostgreSQL the line is `in scope: 167   proven: 159   unmet MUST: 0`, which is
the release target: 167 is the 117 criteria of the v0.1–v0.3 SDDs plus SDD v0.4 §20's fifty, and
159 is 167 less the **five** rows proven by the vitest suite and the three proven by a recorded
live-provider run. The v0.4 drafts said 161; that figure counted three frontend rows when
`docs/acceptance/criteria.toml` carries five (`V01-P4-004`, `V02-P6-008`, `V02-P7-007`,
`AC4-M9-040`, `AC4-M9-043`), and `notes.md` and `audit.md` are corrected in this PR.

The Python suite reads `7 failed, 3299 passed, 102 skipped`: the five database failures above and
the two `tests/test_acceptance_harness.py` node-id assertions, which compare a pytest node id
against a relative path and therefore fail in any git worktree. All seven fail identically on the
base.

## Reproduce

```bash
uv run --no-sync ruff check . && uv run --no-sync mypy src
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin \
  tests/test_v04_m10_locked_test.py tests/test_v04_m10_locked_corpus.py \
  tests/test_v04_m10_router_benchmark.py tests/test_v04_m10_ablations.py \
  tests/test_v04_m10_regret.py tests/test_v04_m10_split.py \
  tests/test_v04_m10_preregistration.py tests/test_acceptance_harness.py
uv run --no-sync python scripts/export_contract_schemas.py --check
uv run --no-sync python scripts/check_docs.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python scripts/check_acceptance.py --stage v0.4-M10
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python scripts/release_gate.py
git diff --exit-code -- evals/router/config.v1.json evals/router/tasks.v1.json \
  evals/router/candidates.v1.json evals/router/replay-traces.v1.json \
  evals/router/ablations.v1.json evals/router/projects.v1.json
```

The corpora are regenerated with `PYTHONPATH=src python -m tests.router_corpus_generator`, which
must leave every committed file — development, locked and drift — byte-identical.

Re-reading the locked test set is a deliberate act and appends to the committed log:

```bash
ACCRETION_ROUTER_LOCKED_TEST=1 PYTHONPATH=src uv run --no-sync python \
  scripts/router_locked_test.py --principal usr-accretion-release \
  --reason "the v0.4.0 release's locked read (M10d)"
```

Pass `--access-log` a path outside the repository to rehearse without touching it.

## Deviations recorded

The first two entries began as unlisted deviations and are now **authorised scope amendments**: the
milestone brief for this PR was amended to name both, because the brief's own requirements could not
be met without them. They are listed here so the amendment is auditable from the release notes and
not only from the brief.

- **`src/accretion/router_benchmark.py` is modified (authorised amendment).** The brief's first
  Create item asks each locked corpus's `config.v1.json` to carry *the same* `ablations_path` as the
  shipped corpus, and its split to be proved on load — and neither is reachable at the old fixed
  depth. `ablations_path` resolved relative paths by counting parents above the corpus, which for
  `evals/router/locked` lands on `evals/` rather than the repository root; `_validate_split` read the
  shipped registry unconditionally, so every locked project failed as "not in the development
  registry". Both resolutions now use a fixed point. The change is additive and leaves the
  development corpus's behaviour byte-identical, which
  `test_both_corpora_pin_the_frozen_preregistration_and_the_one_registered_ablation_table` and the
  shipped corpus's `git diff --exit-code` both hold to (ADR4-M10-006). Rejected alternatives: siting
  the corpora at `evals/router-locked/` so the parent count stays valid, which contradicts
  ADR4-M10-004; and merging the new lineages into the shipped registry, which re-answers the golden
  split.
- **The locked and drift corpus directories carry a fifth document, `projects.v1.json` (authorised
  amendment).** The brief listed four. The registry is what a split is *proved against*, and adding
  twenty-four projects to the hand-authored `evals/router/projects.v1.json` would have broken the
  golden split `tests/test_v04_m10_split.py` pins. It cannot be folded into `config.v1.json` either:
  `RouterBenchmarkConfig` is a `StrictModel` with a registry-locked field list, and widening it is a
  contract change this milestone must not make (ADR4-M10-004). Committed beside its corpus and
  covered by `test_the_derived_registries_are_committed_beside_their_corpora`.
- **The access log holds two rows, not one.** Reading the drift holdout is a read of a locked
  corpus and is logged as one. The audit's draft sentence "any value above 1 is a finding" is
  restated as two, one per corpus, in this PR.
- **`build()`'s `seed` and `trials_per_cell` default to `None` and resolve to the module constants
  at call time** rather than binding the shipped values as literal defaults. Binding them would
  have silently broken `scripts/router_pilot.py`, which regenerates the pilot corpus by rebinding
  `TRIALS_PER_CELL` around a build.
- **ADR4-M10-005 is a finding, not a fix.** Both safety gates are degenerate at the frozen trial
  count. Repairing the pooling rule after the read would be the post-hoc analysis change the
  pre-registration forbids, so it is reported and filed instead.
