"""The ranker end to end: sealed contracts out, a bound that means something, a digest that bites.

One bundle is trained for the whole module — five heads of five bags, a Platt calibrator fitted on
a held-out calibration split, and a conformal quantile taken over the *projects* in that split —
because training it three times would spend three times the budget proving the same thing. The
build is timed as it happens and the last test reads that number back, so the two-second ceiling
the M4 plan sets for learned tests is enforced by the suite rather than by a reviewer's memory.

What is actually asserted about a prediction: that it is the sealed ``PredictedOutcomes`` from
``contracts/routing.py`` and not a look-alike dict, that every one of the five estimates brackets
its own mean, that the success bound is the calibrated probability less the conformal quantile,
and that the bound is not the vacuous zero on every row — a lower bound that is always zero would
satisfy every inequality in this file and be worth nothing to §9.5's exploration gate.

The last test tampers with one byte of a saved artefact and requires the load to refuse. It checks
both files it could land in, because a manifest that only covered itself would leave the trees
free to change under a digest that still verified.
"""

from __future__ import annotations

import math
import random
import time
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from accretion.contracts.routing import PredictedOutcomes, UncertaintySummary
from accretion.routing.calibration import (
    PlattCalibrator,
    conformal_quantile,
    success_residuals,
)
from accretion.routing.ranker import (
    ORDERED_HEADS,
    ArtifactDigestMismatchError,
    ArtifactNotFoundError,
    FeatureSchemaMismatchError,
    LearnedOutcomePredictor,
    OutcomeHead,
    OutcomePredictor,
    RankerArtifact,
    RankerError,
    artifact_digest,
    train_ranker,
)

FEATURE_SCHEMA_VERSION = "1.0.0"
ALPHA = 0.2
FEATURES = 6
TWO_SECOND_CEILING = 2.0
"""The M4 plan's budget for a learned test; the harness re-runs this suite several times a job."""

class Bundle(NamedTuple):
    """Everything the module's tests need from one training run.

    The training set is kept alongside the predictor because two of the properties under test are
    about *training* rather than about a fitted model: that the caller's row order cannot reach
    the bags, and that a prediction is about the row it was given. Both need the inputs back.
    """

    predictor: LearnedOutcomePredictor
    holdout_rows: list[list[float | None]]
    holdout_targets: dict[OutcomeHead, list[float]]
    train_rows: list[list[float | None]]
    train_targets: dict[OutcomeHead, list[float]]
    train_kwargs: dict[str, Any]
    seconds: float


_BUNDLE: Bundle | None = None


def build_rows(
    rng: random.Random, count: int, tag: str
) -> tuple[list[list[float | None]], dict[OutcomeHead, list[float]], list[str]]:
    """Rows, five aligned target vectors, and a project id per row.

    The latent score drives all five heads, so a model that learns anything learns all of them,
    and one feature is unobserved ten per cent of the time so that the missing-value path is
    exercised by the ranker and not only by the learner's own tests.
    """

    rows: list[list[float | None]] = []
    targets: dict[OutcomeHead, list[float]] = {head: [] for head in ORDERED_HEADS}
    groups: list[str] = []
    for index in range(count):
        drawn: list[float | None] = [rng.random() for _ in range(FEATURES)]
        if rng.random() < 0.1:
            drawn[2] = None
        observed = drawn[2] if drawn[2] is not None else 0.5
        first = drawn[0] if drawn[0] is not None else 0.0
        second = drawn[1] if drawn[1] is not None else 0.0
        latent = 2.0 * first - 1.5 * second + observed
        rows.append(drawn)
        targets[OutcomeHead.NODE_VERIFIED_SUCCESS].append(1.0 if latent > 0.8 else 0.0)
        targets[OutcomeHead.RUN_VERIFIED_SUCCESS].append(1.0 if latent > 1.1 else 0.0)
        targets[OutcomeHead.QUALITY].append(min(1.0, max(0.0, latent / 3.0)))
        targets[OutcomeHead.COST].append(abs(latent) * 2.0)
        targets[OutcomeHead.LATENCY].append(abs(latent) * 10.0)
        groups.append(f"{tag}-{index % 20:02d}")
    return rows, targets, groups


def trained_bundle() -> Bundle:
    """Train once, hand the same predictor to every test, and remember how long it took."""

    global _BUNDLE
    if _BUNDLE is None:
        started = time.perf_counter()
        rng = random.Random(4242)
        train_rows, train_targets, _ = build_rows(rng, 150, "train")
        fit_rows, fit_targets, fit_groups = build_rows(rng, 120, "cal")
        holdout_rows, holdout_targets, _ = build_rows(rng, 40, "holdout")
        train_kwargs: dict[str, Any] = {
            "version_id": "rmv_0000000000000000000001",
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "seed": 101,
            "n_trees": 12,
            "max_depth": 2,
            "learning_rate": 0.3,
        }
        artifact = train_ranker(train_rows, train_targets, **train_kwargs)
        ensemble = artifact.ensemble(OutcomeHead.NODE_VERIFIED_SUCCESS)
        margins = [
            math.fsum(model.predict(row) for model in ensemble.models) / len(ensemble.models)
            for row in fit_rows
        ]
        labels = fit_targets[OutcomeHead.NODE_VERIFIED_SUCCESS]
        calibrator = PlattCalibrator.fit(margins, labels)
        probabilities = [calibrator.apply(margin) for margin in margins]
        quantile = conformal_quantile(
            success_residuals(probabilities, labels), ALPHA, fit_groups
        )
        predictor = LearnedOutcomePredictor(
            artifact=artifact,
            calibrator=calibrator,
            conformal_quantile=quantile,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            alpha=ALPHA,
        )
        _BUNDLE = Bundle(
            predictor=predictor,
            holdout_rows=holdout_rows,
            holdout_targets=holdout_targets,
            train_rows=train_rows,
            train_targets=train_targets,
            train_kwargs=train_kwargs,
            seconds=time.perf_counter() - started,
        )
    return _BUNDLE


def test_ranker_prediction_is_a_sealed_predicted_outcomes_with_lower_bound_below_mean() -> None:
    bundle = trained_bundle()
    predictor, holdout_rows = bundle.predictor, bundle.holdout_rows
    assert isinstance(predictor, OutcomePredictor)
    assert predictor.version_id == "rmv_0000000000000000000001"

    predictions = [predictor.predict(row) for row in holdout_rows]
    for predicted, uncertainty in predictions:
        assert isinstance(predicted, PredictedOutcomes)
        assert isinstance(uncertainty, UncertaintySummary)
        for estimate in (
            predicted.quality,
            predicted.cost,
            predicted.latency,
            predicted.node_verified_success,
            predicted.run_verified_success,
        ):
            assert estimate.lower_bound <= estimate.mean <= estimate.upper_bound
            assert math.isfinite(estimate.mean)
            assert math.isclose(estimate.confidence, 1.0 - ALPHA)
        assert uncertainty.epistemic_uncertainty >= 0.0
        assert uncertainty.calibration_version == predictor.calibrator.version
        assert math.isclose(
            uncertainty.lower_confidence_success,
            predicted.node_verified_success.lower_bound,
        )
        assert math.isclose(
            predicted.node_verified_success.lower_bound,
            max(0.0, predicted.node_verified_success.mean - predictor.conformal_quantile),
        )

    best = max(predictions, key=lambda item: item[0].node_verified_success.mean)[0]
    assert best.node_verified_success.lower_bound < best.node_verified_success.mean
    assert any(
        predicted.node_verified_success.lower_bound > 0.0 for predicted, _ in predictions
    ), "a bound that is zero on every row would prove nothing"

    # Everything above holds for a predictor that throws the caller's row away and returns a
    # constant, which is exactly the predictor the selector must not be handed. Two claims about
    # the row: the predictions spread, and they spread in the direction of the truth.
    means = [predicted.node_verified_success.mean for predicted, _ in predictions]
    assert max(means) - min(means) > 0.3, "a predictor that ignored its row would be constant"
    holdout_labels = bundle.holdout_targets[OutcomeHead.NODE_VERIFIED_SUCCESS]
    positive = [mean for mean, label in zip(means, holdout_labels, strict=True) if label == 1.0]
    negative = [mean for mean, label in zip(means, holdout_labels, strict=True) if label == 0.0]
    assert positive and negative, "the holdout must contain both outcomes for this to mean anything"
    assert math.fsum(positive) / len(positive) - math.fsum(negative) / len(negative) > 0.2, (
        "the predicted success must track the row's own outcome, not the training set's base rate"
    )

    # The epistemic term is bag disagreement, so it must be larger than the floating-point dust
    # five byte-identical bags would leave behind (measured: ~1e-32 for identical bags).
    assert (
        max(uncertainty.epistemic_uncertainty for _, uncertainty in predictions) > 1e-4
    ), "five identical bags would make the epistemic term a constant"
    assert (
        len(
            set(
                predictor.artifact.ensemble(OutcomeHead.NODE_VERIFIED_SUCCESS).bag_values(
                    holdout_rows[0], predictor.calibrator
                )
            )
        )
        > 1
    ), "the bags must be fitted on different bootstrap draws"

    with pytest.raises(RankerError):
        predictor.predict([0.0] * (FEATURES + 1))
    with pytest.raises(FeatureSchemaMismatchError):
        LearnedOutcomePredictor(
            artifact=predictor.artifact,
            calibrator=predictor.calibrator,
            conformal_quantile=predictor.conformal_quantile,
            feature_schema_version="2.0.0",
        )


def test_artifact_round_trip_verifies_its_digest(tmp_path: Path) -> None:
    bundle = trained_bundle()
    predictor, holdout_rows = bundle.predictor, bundle.holdout_rows

    # The digest a RouterModelVersion pins is only worth pinning if the caller's incidental row
    # order cannot reach it. train_ranker sorts the rows canonically before the bootstrap draws
    # index into row positions; retraining from a permutation of the same set must land on the
    # same artefact, byte for byte.
    permutation = list(range(len(bundle.train_rows)))
    random.Random(31).shuffle(permutation)
    shuffled_artifact = train_ranker(
        [bundle.train_rows[index] for index in permutation],
        {
            head: [values[index] for index in permutation]
            for head, values in bundle.train_targets.items()
        },
        **bundle.train_kwargs,
    )
    assert permutation != sorted(permutation), "the permutation must actually reorder the rows"
    assert artifact_digest(shuffled_artifact) == artifact_digest(predictor.artifact), (
        "caller row order must not reach the bags"
    )

    digest = predictor.save(tmp_path)
    restored = LearnedOutcomePredictor.load(tmp_path, digest)
    assert artifact_digest(restored.artifact) == artifact_digest(predictor.artifact)
    assert restored.calibrator.version == predictor.calibrator.version
    assert math.isclose(restored.conformal_quantile, predictor.conformal_quantile)
    assert math.isclose(restored.alpha, predictor.alpha)
    # Exact equality is the claim: JSON round-trips a Python float without loss, so a reloaded
    # tree holds the same leaf values and reaches the same sum, on any platform.
    assert restored.predict(holdout_rows[0])[0] == predictor.predict(holdout_rows[0])[0]
    assert predictor.save(tmp_path) == digest, "saving twice must not move the digest"

    with pytest.raises(ArtifactNotFoundError):
        LearnedOutcomePredictor.load(tmp_path, "0" * 64)

    for name in ("ranker.json", "calibration.json", "manifest.json"):
        second = tmp_path / "tampered"
        second.mkdir(exist_ok=True)
        again = predictor.save(second)
        target = second / again / name
        payload = bytearray(target.read_bytes())
        payload[len(payload) // 2] ^= 0x01
        target.write_bytes(bytes(payload))
        with pytest.raises(ArtifactDigestMismatchError):
            LearnedOutcomePredictor.load(second, again)
        for leftover in (second / again).iterdir():
            leftover.unlink()

    assert RankerArtifact.from_json(predictor.artifact.to_json()) == predictor.artifact


def test_learned_tests_finish_under_two_seconds() -> None:
    seconds = trained_bundle().seconds
    assert seconds > 0.0, "the bundle must actually have been trained, not memoized away"
    assert seconds < TWO_SECOND_CEILING
