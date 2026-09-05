# v0.4 M4 — Offline ranker, calibration and the candidate gate

SDD v0.4 §10.1–10.2 and §9.3: a router version is not a file, it is a record with the evidence it
was fitted on, the calibration that makes its probabilities honest and a holdout evaluation on
projects it never saw. M4 builds that record and then makes it a *precondition*: nothing learned
enters routing without it. The milestone claims one criterion, `AC4-M4-016` — "offline ranking
precedes any shadow or live learned policy" — and M6's shadow evaluator and M8's promotion gate
both reach it through the same loader rather than restating the rule.

## Ladder

| PR | Content | Proof |
|---|---|---|
| M4.1 — `pr1-features-snapshot` | `routing/features.py` (`FEATURE_SCHEMA_V1`, 78 ordered columns, `feature_schema_digest`, `featurize`, `Vocabulary`, `summarize_evidence` keeping cross-domain evidence out of every mean); `routing/training_snapshot.py` (`SnapshotBuilder.build` → a sealed `RouterTrainingSnapshot` with a derived `rts_` id, `materialize` → the training table) | schema digest pinned as a literal; two builds one `content_hash`; shuffled input, same manifest; `INCONCLUSIVE` is neither label and is tallied |
| M4.2 — `pr2-gbdt-calibration-ranker` | `routing/gbdt.py` (deterministic in-repo learner); `routing/calibration.py` (Platt, isotonic, grouped split-conformal quantile, `CalibrationReport` with bins, project bootstrap and per-cohort ECE); `routing/ranker.py` (`RankerArtifact`, five heads × five bags, `LearnedOutcomePredictor` with conformal bounds on the success heads and a labelled bag band on the magnitudes) | same seed, identical JSON; NaN takes the default branch; conformal coverage ≥ 1−α on grouped synthetic data; the artefact directory verifies every byte before parsing |
| M4.3 — `pr3-train-candidate` | `routing/train.py` (`RouterTrainingService.train_candidate`, `LearnedPredictorLoader`, `HoldoutEvaluation`); `routing/artifacts.py` (`ArtifactStore`, content-addressed); `api/main.py` `POST /api/v1/router-models/train-candidate` and `GET /api/v1/router-models`; `AC4-M4-016` flipped | the two acceptance tests below, their mutations, project disjointness, seed determinism, the evidence minimum, digest verification on load, the Postgres twin |

## What M4.3 decides

**The gate is on the read path.** `LearnedPredictorLoader.load` is the only way a
`LearnedOutcomePredictor` enters routing, and it refuses a version that carries no
`holdout_eval_digest`, no `calibration_report_digest`, or a status outside
`{CANDIDATE, SHADOW, ACTIVE}`. A trainer that always wrote an evaluation would only be a
convention — the next hand-built version, or one restored from a backup taken mid-write, would
route unevaluated. The label is the SHA-256 of the canonical JSON of the evaluation *and* the key
the document is stored under, so it cannot be forged by writing a string.

**The snapshot is written before the version.** A version naming a snapshot that did not yet exist
would be, for the width of that window, a router pointing at evidence nobody could read. The order
is asserted by a store spy and is one of the two declared mutations.

**Five projects minimum, and the split comes first.** `split.py` allocates whole project lineages
to the five required splits; `SnapshotBuilder.build` takes the sealed three-group split as an
argument rather than inventing one, so the trainer asks which projects have eligible evidence
before it cuts the snapshot that names them. Below five projects one of the five splits cannot be
filled, and below twenty eligible records a calibration report is a description of four rows —
both are refused as `TrainingDataError` rather than trained through.

**The label variation is the run's, not the node's.** `ExperienceRecord` makes
`eligible_for_learning` imply local `PASS`, so every *local* label inside a snapshot is 1.0 and the
node-success head sees no variation at all. Calibration, the conformal quantile and every holdout
statistic are therefore computed on the **run**-verified-success head, where a node that passed
inside a run that later failed supplies the disagreement §14.3 exists to catch. This is a property
of the sealed M3 projection, recorded here rather than papered over.

**Evidence features are built backwards in time and within one project.** A training row is
featurized from records of the same project created strictly before it. Summarising over the whole
snapshot would carry a holdout project's outcomes into a training row's feature vector while every
project-disjointness assertion still passed.

**A re-split of settled evidence is refused.** `_derived_id` puts the workspace, window, rules and
*evidence* into a snapshot's identity, deliberately, so that rebuilding one is a byte-identical
re-put. The split is not in that identity, so a second seed produces the same id with a different
holdout group; the trainer raises `SnapshotConflictError` naming the collision instead of letting
the append-only store report it as an unexplained immutability violation. Retraining over an
unchanged window reuses the stored snapshot, timestamp included.

## Acceptance

`AC4-M4-016` — *offline ranking precedes any shadow or live learned policy*, claimed by four tests
in `tests/test_v04_m4_train.py`:

| Test | Mutation that kills it |
|---|---|
| `test_a_learned_version_without_a_holdout_evaluation_cannot_be_loaded_for_routing` | drop the `holdout_eval_digest` check in `LearnedPredictorLoader.require_evaluated` → the hand-built CANDIDATE loads and the test fails |
| `test_train_candidate_records_snapshot_holdout_and_calibration_before_the_version_exists` | swap the two puts so the version is written first → the recorded call order fails |

Both were run and both fail as described. Neither claiming test needs PostgreSQL, so the criterion
cannot classify `SKIPPED_ONLY`; the Postgres twin carries no acceptance marker.

## Counts

With the lane database up, after deleting the `AC4-M4-016` policy row:

```
in scope: 118   proven: 112   unmet MUST: 0
```

(M1.2 has not merged; when it does the same run reads `122` / `116`.) `--stage v0.4-M4` reports
`in scope: 1   proven: 1   unmet MUST: 0`, which is the form CI gates this milestone on.

Measured on a worktree with no lane database, the same command reads `118 / 111` with one
`FAILING`: `V02-P5-001`, whose only claiming test enters the API lifespan and therefore needs
PostgreSQL. It is the 112th, and nothing about it is M4's.

Deleting the policy row also moves `tests/test_acceptance_harness.py`'s v0.4 inventory test, which
asserted that all fifty rows were still `not_yet_due`. It now carries `FLIPPED_V04_ROWS` — the rows
whose policy line has been deleted — and asserts both halves: a waiting row is still `not_yet_due`
with its owner's reason, and a flipped row has no policy line, defaults to `test` and is in scope.
Each milestone's final PR adds its own id to that set; a row that vanished from the policy file
without a claiming test still fails.

## Exit

```bash
uv run --no-sync ruff check . && uv run --no-sync mypy src
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin \
  tests/test_v04_m4_train.py tests/test_v04_m4_api.py tests/test_v04_m4_postgres_store.py
uv run --no-sync python scripts/export_contract_schemas.py --check
uv run --no-sync python scripts/check_docs.py
npm run api:generate && git diff --exit-code apps/ui/src/api/schema.d.ts
```

Two further claimants carry the same marker: `test_no_module_outside_the_loader_assembles_a_learned_predictor`
(structural: assembling a `LearnedOutcomePredictor` anywhere but `ranker.py` or `train.py`, under any
import alias, turns it red — the scan resolves bindings and is itself tested) and
`test_the_holdout_projects_are_disjoint_from_training_and_calibration_projects` (scoring the
"holdout" on training rows turns it red).

Deviation recorded: `tests/test_acceptance_harness.py` was modified outside the brief's file list.
Deleting the `AC4-M4-016` policy row breaks its pre-existing "every v0.4 row is `not_yet_due`"
assertion, so `test_the_fifty_v04_rows_load_from_the_sdd_under_their_owner_stages` now carries an
explicit `FLIPPED_V04_ROWS` set and asserts both halves (a waiting row is `not_yet_due` with its
owner's reason; a flipped row is `test`, in scope, with no reason), which is a strengthening. The two
protected node-id-path tests in that file are untouched.
