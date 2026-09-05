"""Calibration and the conformal bound: does grouping matter, does calibration help, is the
report a value?

The middle test is the one with teeth. Split conformal only guarantees anything about the unit it
took its quantile over, and the unit here is a *project*, not a row. The synthetic data is built
so that the difference is visible rather than theoretical: most projects are large and well
calibrated, a third are small and badly over-predicting. Reduced to group means, the two
populations separate and the grouped quantile lands where it must. Pooled over rows, the small
projects contribute a few per cent of the sample, the quantile collapses towards the well-behaved
majority, and coverage falls to roughly two thirds of the projects. The test computes *both* — the
ungrouped variant is obtained by handing every row its own group id, so there is no second
implementation to drift — and requires the grouped one to clear the threshold and the ungrouped
one to fail it. That is what makes the Monte-Carlo tolerance binding rather than decorative: it is
computed from the two sample sizes and then checked, in the same test, against a variant that
must not survive it.

Nothing here reads a clock or an unseeded random. Every draw comes from a ``random.Random`` with a
literal seed, so a failure is a failure and not a bad afternoon.
"""

from __future__ import annotations

import json
import math
import random

import pytest

from accretion.contracts.canonical import canonical_json
from accretion.routing.calibration import (
    CalibrationDataError,
    CalibrationMethod,
    CalibrationReport,
    IsotonicCalibrator,
    PlattCalibrator,
    brier,
    build_calibration_report,
    conformal_quantile,
    ece,
    group_coverage,
    lcb,
    success_residuals,
)
from accretion.routing.gbdt import sigmoid

ALPHA = 0.2
"""Loose on purpose: a 0.8 target needs far fewer synthetic groups to measure than a 0.95 one."""

CALIBRATION_GROUPS = 400
EVALUATION_GROUPS = 400
SHIFTED_IN_TWENTY = 7
"""Thirty-five per cent of projects are over-predicting, deterministically chosen by index."""


def build_grouped_sample(
    rng: random.Random, groups: int, tag: str
) -> tuple[list[float], list[float], list[str]]:
    """Large well-calibrated projects and small over-predicting ones, in one exchangeable pool.

    The size asymmetry (40 rows against 12) is the whole point: it is what lets a row-level
    quantile ignore the projects that need the bound most.
    """

    probabilities: list[float] = []
    labels: list[float] = []
    identifiers: list[str] = []
    for index in range(groups):
        shifted = index % 20 >= 20 - SHIFTED_IN_TWENTY
        size = 12 if shifted else 40
        predicted = 0.9 if shifted else 0.5
        truth = 0.2 if shifted else 0.5
        for _ in range(size):
            probabilities.append(predicted)
            labels.append(1.0 if rng.random() < truth else 0.0)
            identifiers.append(f"{tag}-{index:03d}")
    return probabilities, labels, identifiers


def build_miscalibrated_scorer(seed: int, rows: int) -> tuple[list[float], list[float]]:
    """Logits that are 2.6x too confident and shifted, with labels from the *true* probability."""

    rng = random.Random(seed)
    logits: list[float] = []
    labels: list[float] = []
    for _ in range(rows):
        latent = rng.gauss(0.0, 1.2)
        labels.append(1.0 if rng.random() < sigmoid(latent) else 0.0)
        logits.append(2.6 * latent + 0.8)
    return logits, labels


def build_report_sample(
    seed: int,
) -> tuple[list[float], list[float], list[str], list[str]]:
    """A small grouped, cohorted sample: 24 projects of 15 rows across three cohorts."""

    rng = random.Random(seed)
    probabilities: list[float] = []
    labels: list[float] = []
    groups: list[str] = []
    cohorts: list[str] = []
    for index in range(24):
        for _ in range(15):
            predicted = round(rng.random(), 6)
            probabilities.append(predicted)
            labels.append(1.0 if rng.random() < predicted * 0.85 else 0.0)
            groups.append(f"prj-{index:02d}")
            cohorts.append(("correctness", "policy", "secrets")[index % 3])
    return probabilities, labels, groups, cohorts


def test_conformal_lcb_covers_at_least_one_minus_alpha_on_grouped_synthetic_data() -> None:
    rng = random.Random(20260905)
    fit_p, fit_y, fit_groups = build_grouped_sample(rng, CALIBRATION_GROUPS, "cal")
    holdout_p, holdout_y, holdout_groups = build_grouped_sample(rng, EVALUATION_GROUPS, "holdout")
    fit_residuals = success_residuals(fit_p, fit_y)
    holdout_residuals = success_residuals(holdout_p, holdout_y)

    grouped = conformal_quantile(fit_residuals, ALPHA, fit_groups)
    # The ungrouped variant, without a second implementation: one row per group is exactly the
    # row-level quantile this bound must not be computed with.
    ungrouped = conformal_quantile(
        fit_residuals, ALPHA, [str(index) for index in range(len(fit_residuals))]
    )

    # Binomial noise from both splits, at three standard deviations. Written out so that the
    # number below is a consequence of the sample sizes rather than a knob.
    tolerance = 3.0 * math.sqrt(
        ALPHA * (1.0 - ALPHA) / CALIBRATION_GROUPS + ALPHA * (1.0 - ALPHA) / EVALUATION_GROUPS
    )
    target = 1.0 - ALPHA - tolerance
    assert tolerance < 0.1

    covered = group_coverage(holdout_residuals, holdout_groups, grouped)
    assert covered >= target
    assert group_coverage(holdout_residuals, holdout_groups, ungrouped) < target, (
        "a row-level quantile must not clear the same bar the grouped one does"
    )

    # What the quantile is *for*: the bound it produces sits under the realised success rate of
    # at least as many projects as the residual coverage promised.
    totals: dict[str, list[float]] = {}
    outcomes: dict[str, list[float]] = {}
    for probability, label, group in zip(holdout_p, holdout_y, holdout_groups, strict=True):
        totals.setdefault(group, []).append(probability)
        outcomes.setdefault(group, []).append(label)
    honest = sum(
        1
        for group in sorted(totals)
        if lcb(math.fsum(totals[group]) / len(totals[group]), grouped)
        <= math.fsum(outcomes[group]) / len(outcomes[group])
    )
    assert honest / len(totals) >= covered

    # Too few groups for the index to exist: the bound degenerates instead of pretending.
    assert conformal_quantile([0.4], 0.05, ["only"]) == 1.0
    with pytest.raises(CalibrationDataError):
        conformal_quantile(fit_residuals, ALPHA, fit_groups[:-1])


def test_platt_and_isotonic_reduce_ece_on_a_miscalibrated_synthetic_scorer() -> None:
    logits, labels = build_miscalibrated_scorer(seed=2026, rows=1200)
    fit_logits, fit_labels = logits[:600], labels[:600]
    holdout_logits, holdout_labels = logits[600:], labels[600:]

    uncalibrated = [sigmoid(value) for value in holdout_logits]
    platt = PlattCalibrator.fit(fit_logits, fit_labels)
    isotonic = IsotonicCalibrator.fit(fit_logits, fit_labels)
    platt_probabilities = [platt.apply(value) for value in holdout_logits]
    isotonic_probabilities = [isotonic.apply(value) for value in holdout_logits]

    baseline = ece(uncalibrated, holdout_labels)
    assert baseline > 0.1, "the synthetic scorer must actually be miscalibrated"
    assert ece(platt_probabilities, holdout_labels) < baseline / 2.0
    assert ece(isotonic_probabilities, holdout_labels) < baseline / 2.0
    assert brier(platt_probabilities, holdout_labels) < brier(uncalibrated, holdout_labels)
    assert brier(isotonic_probabilities, holdout_labels) < brier(uncalibrated, holdout_labels)

    # Both are monotone maps, and both stay inside [0, 1] beyond the range they were fitted on.
    probe = [value / 4.0 for value in range(-40, 41)]
    for calibrator in (platt, isotonic):
        applied = [calibrator.apply(value) for value in probe]
        assert all(0.0 <= value <= 1.0 for value in applied)
        assert all(
            earlier <= later + 1e-12
            for earlier, later in zip(applied, applied[1:], strict=False)
        )
    assert platt.method is CalibrationMethod.PLATT
    assert isotonic.method is CalibrationMethod.ISOTONIC
    assert platt.version != isotonic.version
    assert PlattCalibrator.from_json(platt.to_json()) == platt
    assert IsotonicCalibrator.from_json(isotonic.to_json()) == isotonic


def test_calibration_report_is_deterministic_and_serialisable() -> None:
    probabilities, labels, groups, cohorts = build_report_sample(seed=404)
    arguments = {
        "probabilities": probabilities,
        "labels": labels,
        "groups": groups,
        "cohorts": cohorts,
        "method": CalibrationMethod.PLATT,
        "feature_schema_version": "1.0.0",
        "alpha": 0.1,
        "bootstraps": 50,
    }

    first = build_calibration_report(seed=7, **arguments)
    again = build_calibration_report(seed=7, **arguments)
    assert first == again
    assert canonical_json(first.model_dump(mode="json")) == canonical_json(
        again.model_dump(mode="json")
    )

    restored = CalibrationReport.model_validate(
        json.loads(json.dumps(first.model_dump(mode="json")))
    )
    assert restored == first
    assert restored.method is CalibrationMethod.PLATT
    assert restored.schema_version == "1.0"

    assert sum(item.count for item in first.bins) == len(labels)
    assert [item.lower for item in first.bins] == sorted(item.lower for item in first.bins)
    assert [item.cohort_id for item in first.per_cohort] == ["correctness", "policy", "secrets"]
    assert sum(item.n for item in first.per_cohort) == len(labels)
    interval = first.bootstrap_ece_interval
    assert interval.lower <= first.ece_10bin <= interval.upper

    other_seed = build_calibration_report(seed=8, **arguments)
    assert other_seed.ece_10bin == first.ece_10bin
    assert (
        other_seed.bootstrap_ece_interval.lower,
        other_seed.bootstrap_ece_interval.upper,
    ) != (first.bootstrap_ece_interval.lower, first.bootstrap_ece_interval.upper), (
        "the bootstrap seed must reach the interval"
    )

    # Probabilities that land exactly on a bin edge are not a corner case: an isotonic block mean
    # is a ratio of small integers, so 0.3, 0.6 and 0.7 arrive repeated and in bulk, and every
    # bootstrap draw resamples them. If the bin edges are not derived the same way as the bin
    # index, or the bin mean is not clamped into its own bin, the diagram's validator rejects the
    # bin and takes ece() and the whole report down with it.
    for k in range(11):
        on_edge = ece([k / 10] * 4, [1.0, 0.0, 0.0, 0.0])
        assert 0.0 <= on_edge <= 1.0, f"probability {k / 10} on a bin edge must still bin"
    for count in (1, 40):
        for k in range(11):
            edge_report = build_calibration_report(
                probabilities=[k / 10] * count,
                labels=[float(index % 2) for index in range(count)],
                groups=[f"p{index % 4}" for index in range(count)],
                method=CalibrationMethod.ISOTONIC,
                feature_schema_version="1.0.0",
                seed=11,
                bootstraps=5,
            )
            assert sum(item.count for item in edge_report.bins) == count
