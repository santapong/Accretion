"""The learner's three load-bearing claims: determinism, missing-value handling, and signal.

Determinism first, because it is the one an artefact digest depends on. A ``RouterModelVersion``
stores the digest of the trees it was built from, and a version whose digest moved because the
training rows arrived in a different order would make every promotion comparison meaningless. The
first test therefore trains on the same rows twice, then on a shuffled copy, and requires
byte-identical artefacts; deleting the canonical sort in ``_prepare`` turns the shuffled case red.

Then missing values, which are not an edge case here: a feature is often absent *because* of
something, and a split that always sends the unobserved rows the same way cannot learn that. Both
directions are exercised, so a hard-coded default passes only one of the two.

Then signal, because a learner can be perfectly deterministic and perfectly useless. The synthetic
problem has an interaction term a single linear model would miss, and the AUC is measured on rows
the model never saw. No assertion in this file compares floats for equality except where two
expressions are the same computation reached by different routes, and those say so.
"""

from __future__ import annotations

import math
import random

import pytest

from accretion.routing.gbdt import (
    GBDT,
    GBDTModel,
    Learner,
    Model,
    TrainingDataError,
    artifact_bytes,
    artifact_digest,
)

FEATURES = 8
"""Wide enough that feature subsampling actually subsamples, narrow enough to stay fast."""


def build_interaction_problem(
    seed: int, rows: int, *, missing_rate: float = 0.0
) -> tuple[list[list[float | None]], list[float]]:
    """A seeded binary problem with an interaction term and four irrelevant features.

    ``2 x0 + 2 x1 x2 - x3`` thresholded at its own middle: linearly separable in *no* single
    feature, so an AUC above 0.9 is evidence the trees found the interaction rather than evidence
    the problem was trivial.
    """

    rng = random.Random(seed)
    built: list[list[float | None]] = []
    targets: list[float] = []
    for _ in range(rows):
        drawn = [rng.random() for _ in range(FEATURES)]
        score = 2.0 * drawn[0] + 2.0 * (drawn[1] * drawn[2]) - drawn[3]
        row: list[float | None] = list(drawn)
        if missing_rate > 0.0 and rng.random() < missing_rate:
            row[5] = None
        built.append(row)
        targets.append(1.0 if score > 1.05 else 0.0)
    return built, targets


def build_missing_direction_problem(
    seed: int, missing_label: float
) -> tuple[list[list[float | None]], list[float]]:
    """One feature, a clean threshold at 0.5, and unobserved rows that all share ``missing_label``.

    The only way to fit the unobserved rows is to route them to the side whose label they share,
    so the learned default direction is forced by the data and not by the tie-break convention.
    """

    rng = random.Random(seed)
    rows: list[list[float | None]] = []
    targets: list[float] = []
    for _ in range(120):
        rows.append([rng.random() * 0.4])
        targets.append(0.0)
        rows.append([0.6 + rng.random() * 0.4])
        targets.append(1.0)
    for _ in range(80):
        rows.append([None])
        targets.append(missing_label)
    return rows, targets


def area_under_curve(scores: list[float], labels: list[float]) -> float:
    """Rank-based AUC with tied scores sharing an average rank."""

    ranked = sorted(zip(scores, labels, strict=True))
    total = len(ranked)
    ranks = [0.0] * total
    start = 0
    while start < total:
        end = start
        while end + 1 < total and ranked[end + 1][0] == ranked[start][0]:
            end += 1
        average = (start + end) / 2.0 + 1.0
        for position in range(start, end + 1):
            ranks[position] = average
        start = end + 1
    positives = sum(1 for _, label in ranked if label > 0.5)
    negatives = total - positives
    positive_ranks = math.fsum(
        ranks[position] for position in range(total) if ranked[position][1] > 0.5
    )
    return (positive_ranks - positives * (positives + 1) / 2.0) / (positives * negatives)


def test_same_seed_and_rows_give_byte_identical_artifacts() -> None:
    rows, targets = build_interaction_problem(seed=17, rows=200, missing_rate=0.15)
    learner = GBDT(
        loss="logistic",
        n_trees=20,
        max_depth=3,
        learning_rate=0.2,
        feature_subsample=0.6,
        seed=5,
    )

    # The seam OQ-401 asks for: a scikit-learn wrapper would enter through these two protocols.
    assert isinstance(learner, Learner)

    fitted = learner.fit(rows, targets)
    assert isinstance(fitted, Model)
    first = artifact_bytes(fitted)
    again = artifact_bytes(learner.fit(rows, targets))
    assert first == again

    order = list(range(len(rows)))
    random.Random(999).shuffle(order)
    shuffled = artifact_bytes(
        learner.fit([rows[index] for index in order], [targets[index] for index in order])
    )
    assert shuffled == first, "row order must not reach the trees"

    rebuilt = GBDTModel.from_json(learner.fit(rows, targets).to_json())
    assert artifact_bytes(rebuilt) == first
    assert artifact_digest(rebuilt) == artifact_digest(learner.fit(rows, targets))

    other_seed = GBDT(
        loss="logistic",
        n_trees=20,
        max_depth=3,
        learning_rate=0.2,
        feature_subsample=0.6,
        seed=6,
    )
    assert artifact_bytes(other_seed.fit(rows, targets)) != first, "the seed must do something"

    with pytest.raises(TrainingDataError):
        learner.fit(rows, targets[:-1])


def test_missing_values_route_to_the_learned_default_direction() -> None:
    learner = GBDT(loss="logistic", n_trees=25, max_depth=2, learning_rate=0.3, seed=3)

    with_high = learner.fit(*build_missing_direction_problem(seed=11, missing_label=1.0))
    with_low = learner.fit(*build_missing_direction_problem(seed=11, missing_label=0.0))

    high_root = with_high.trees[0][0]
    low_root = with_low.trees[0][0]
    assert not high_root.is_leaf and not low_root.is_leaf
    assert high_root.missing_left is False
    assert low_root.missing_left is True
    assert high_root.missing_left is not low_root.missing_left

    unobserved_high = with_high.predict([None])
    unobserved_low = with_low.predict([None])
    assert with_high.predict([0.1]) < unobserved_high
    assert unobserved_low < with_low.predict([0.9])
    assert abs(unobserved_high - with_high.predict([0.9])) < abs(
        unobserved_high - with_high.predict([0.1])
    )
    assert abs(unobserved_low - with_low.predict([0.1])) < abs(
        unobserved_low - with_low.predict([0.9])
    )

    # Not a numerical claim: a NaN is mapped onto the missing branch before any arithmetic
    # happens, so the two calls walk the same nodes and sum the same leaves.
    assert math.isclose(with_high.predict([float("nan")]), unobserved_high, rel_tol=1e-12)


def test_logistic_gbdt_separates_a_seeded_synthetic_problem() -> None:
    train_rows, train_targets = build_interaction_problem(seed=21, rows=300)
    holdout_rows, holdout_targets = build_interaction_problem(seed=22, rows=200)
    assert 0.3 < sum(train_targets) / len(train_targets) < 0.7, "the problem must not be degenerate"

    model = GBDT(
        loss="logistic", n_trees=60, max_depth=3, learning_rate=0.15, seed=11
    ).fit(train_rows, train_targets)

    scores = [model.predict(row) for row in holdout_rows]
    assert area_under_curve(scores, holdout_targets) > 0.9

    probabilities = [model.predict_probability(row) for row in holdout_rows]
    assert all(0.0 <= probability <= 1.0 for probability in probabilities)
    assert min(probabilities) < 0.25 < 0.75 < max(probabilities), "predictions must be decisive"

    squared = GBDT(loss="squared", n_trees=40, max_depth=3, learning_rate=0.2, seed=11).fit(
        train_rows, train_targets
    )
    assert area_under_curve([squared.predict(row) for row in holdout_rows], holdout_targets) > 0.9
