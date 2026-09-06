"""Calibration, conformal lower bounds, and the report that has to exist before a model ships.

A gradient-boosted score is not a probability, and §9.5's exploration gate is defined over a
*lower confidence bound* on success. Two different jobs follow, and this module keeps them apart
because they fail in different ways.

**Calibration** maps a score onto a probability that means what it says: among the candidates
this model calls 0.7, about seventy per cent succeed. :class:`PlattCalibrator` fits the two
parameters of a logistic link by damped Newton steps and is the right default at the row counts
v0.4 has; :class:`IsotonicCalibrator` fits an arbitrary monotone map by pool-adjacent-violators
and is worth its variance only once there are enough rows per node class to support it — the M4
plan's threshold is five hundred. :class:`IdentityCalibrator` is the honest "none of the above":
it squashes the margin and says so in :attr:`~IdentityCalibrator.method`, so a report can never
silently claim a calibration that was never fitted.

**The lower bound** is a separate promise, and OQ-405 left its method open. This module implements
the one the plan chose — split conformal on a *project-grouped* calibration split (R6) — with the
bootstrap kept alongside as the sensitivity check rather than as the bound. Grouping is the whole
point and is easy to lose: rows from one project are not exchangeable with each other, so a
quantile taken over rows is a quantile over a sample whose effective size is the number of
*projects*, not the number of rows. A handful of large well-behaved projects would then drown out
the small badly-behaved ones and the bound would cover far less than it claims. So
:func:`conformal_quantile` reduces each project to its mean residual first and takes the
``ceil((m + 1)(1 - alpha)) / m`` quantile over the ``m`` group means — the finite-sample
split-conformal quantile, with the ``+1`` that makes the guarantee hold at these sample sizes.

Everything here is a pure function of its inputs and, where a draw is needed, of a seed. Nothing
reads a clock: a :class:`CalibrationReport` is compared against another report by value, and a
timestamp would make two runs of the same calibration look like two different calibrations.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol, Self, runtime_checkable

from pydantic import Field, model_validator

from accretion.contracts import StrictModel
from accretion.contracts.canonical import canonical_json
from accretion.routing.gbdt import sigmoid

CALIBRATION_SCHEMA_VERSION = "1.0"
"""Version of the serialized calibrator format, carried inside every calibration artefact."""

DEFAULT_ALPHA = 0.05
"""The plan's floor: a lower bound is quoted at 95% unless a caller argues for something else."""

DEFAULT_BINS = 10
"""``ece_10bin`` is a named field on the report, so ten is the default and not a magic number."""

DEFAULT_BOOTSTRAPS = 200
"""The M4 plan's B for the project-level bootstrap around the ECE."""

_NEWTON_ITERATIONS = 50
"""Hard cap. Newton on a two-parameter convex problem converges in single digits or not at all."""

_NEWTON_TOLERANCE = 1e-10
_LINE_SEARCH_STEPS = 12
_HESSIAN_RIDGE = 1e-12
_PROBABILITY_FLOOR = 1e-12


class CalibrationError(ValueError):
    """Base class for every rejection in this module."""


class CalibrationDataError(CalibrationError):
    """Scores, labels, groups or an alpha do not describe a calibratable sample."""


class CalibrationDecodeError(CalibrationError):
    """A serialized calibrator is not one this module can rebuild."""


class CalibrationMethod(StrEnum):
    """How a score became a probability. Recorded because a bound whose method is unknown
    cannot be recalibrated later (the same reason ``DistributionEstimate`` carries ``method``).
    """

    IDENTITY = "IDENTITY"
    PLATT = "PLATT"
    ISOTONIC = "ISOTONIC"


@runtime_checkable
class Calibrator(Protocol):
    """A monotone map from a model margin to a probability, plus its own identity.

    ``version`` is not decoration: it goes onto ``UncertaintySummary.calibration_version``, so a
    receipt records which calibration produced its bound and a recalibration is visibly a
    different thing from a retrain.
    """

    @property
    def method(self) -> CalibrationMethod:
        """Which family this calibrator belongs to."""

    @property
    def version(self) -> str:
        """A stable identifier: the method and a digest of the fitted parameters."""

    def apply(self, logit: float) -> float:
        """The calibrated probability for one model margin."""

    def to_json(self) -> dict[str, Any]:
        """A JSON-safe dict of the fitted parameters."""


def _calibrator_version(payload: Mapping[str, Any], method: CalibrationMethod) -> str:
    digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    return f"{method.value}:{digest[:24]}"


@dataclass(frozen=True, slots=True)
class IdentityCalibrator:
    """No calibration: the margin is squashed and reported as-is.

    Useful as a default and as a control in tests, and dangerous only if it lies about itself,
    which is why it still carries a method and a version.
    """

    @property
    def method(self) -> CalibrationMethod:
        return CalibrationMethod.IDENTITY

    @property
    def version(self) -> str:
        return _calibrator_version(self.to_json(), self.method)

    def apply(self, logit: float) -> float:
        return sigmoid(logit)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": CALIBRATION_SCHEMA_VERSION,
            "method": CalibrationMethod.IDENTITY.value,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> IdentityCalibrator:
        _require_method(payload, CalibrationMethod.IDENTITY)
        return cls()


@dataclass(frozen=True, slots=True)
class PlattCalibrator:
    """``p = sigmoid(a * margin + b)``, fitted by damped Newton on the log-likelihood.

    Two details are not cosmetic. The targets are Platt's smoothed ones — ``(n+ + 1)/(n+ + 2)``
    for a positive and ``1/(n- + 2)`` for a negative — because on a separable calibration split
    the unsmoothed likelihood is maximized only as ``a`` runs to infinity, and a calibrator that
    returns 0.0 and 1.0 makes every downstream log-loss infinite. And each Newton step is
    accepted only if it decreases the objective, halving up to a dozen times if not, so a
    badly-conditioned start cannot step past the optimum and diverge inside the iteration cap.
    """

    a: float
    b: float

    @property
    def method(self) -> CalibrationMethod:
        return CalibrationMethod.PLATT

    @property
    def version(self) -> str:
        return _calibrator_version(self.to_json(), self.method)

    def apply(self, logit: float) -> float:
        return sigmoid(self.a * logit + self.b)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": CALIBRATION_SCHEMA_VERSION,
            "method": CalibrationMethod.PLATT.value,
            "a": self.a,
            "b": self.b,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> PlattCalibrator:
        _require_method(payload, CalibrationMethod.PLATT)
        return cls(
            a=_as_float(payload.get("a"), "platt a"),
            b=_as_float(payload.get("b"), "platt b"),
        )

    @classmethod
    def fit(cls, logits: Sequence[float], labels: Sequence[float]) -> PlattCalibrator:
        scores, targets = _checked_pairs(logits, labels)
        positives = math.fsum(targets)
        negatives = len(targets) - positives
        high = (positives + 1.0) / (positives + 2.0)
        low = 1.0 / (negatives + 2.0)
        smoothed = [high if target > 0.5 else low for target in targets]
        a = 0.0
        mean = math.fsum(smoothed) / len(smoothed)
        b = math.log(mean / (1.0 - mean))
        objective = _platt_objective(scores, smoothed, a, b)
        for _ in range(_NEWTON_ITERATIONS):
            grad_a = 0.0
            grad_b = 0.0
            hess_aa = _HESSIAN_RIDGE
            hess_ab = 0.0
            hess_bb = _HESSIAN_RIDGE
            for score, target in zip(scores, smoothed, strict=True):
                probability = sigmoid(a * score + b)
                residual = probability - target
                curvature = probability * (1.0 - probability)
                grad_a += residual * score
                grad_b += residual
                hess_aa += curvature * score * score
                hess_ab += curvature * score
                hess_bb += curvature
            if abs(grad_a) < _NEWTON_TOLERANCE and abs(grad_b) < _NEWTON_TOLERANCE:
                break
            determinant = hess_aa * hess_bb - hess_ab * hess_ab
            if determinant <= 0.0:
                break
            step_a = -(hess_bb * grad_a - hess_ab * grad_b) / determinant
            step_b = -(hess_aa * grad_b - hess_ab * grad_a) / determinant
            scale = 1.0
            improved = False
            for _ in range(_LINE_SEARCH_STEPS):
                trial_a = a + scale * step_a
                trial_b = b + scale * step_b
                trial = _platt_objective(scores, smoothed, trial_a, trial_b)
                if trial < objective:
                    a, b, objective = trial_a, trial_b, trial
                    improved = True
                    break
                scale *= 0.5
            if not improved:
                break
        return cls(a=a, b=b)


def _platt_objective(
    scores: Sequence[float], targets: Sequence[float], a: float, b: float
) -> float:
    total = 0.0
    for score, target in zip(scores, targets, strict=True):
        probability = min(1.0 - _PROBABILITY_FLOOR, max(_PROBABILITY_FLOOR, sigmoid(a * score + b)))
        total -= target * math.log(probability) + (1.0 - target) * math.log(1.0 - probability)
    return total


@dataclass(frozen=True, slots=True)
class IsotonicCalibrator:
    """A non-decreasing step map fitted by pool-adjacent-violators, read by interpolation.

    Ties are resolved before the pooling rather than during it: equal scores are collapsed to one
    weighted point, so two calibration rows with the same margin cannot be assigned two different
    probabilities depending on which one the sort happened to put first.
    """

    thresholds: tuple[float, ...]
    values: tuple[float, ...]

    @property
    def method(self) -> CalibrationMethod:
        return CalibrationMethod.ISOTONIC

    @property
    def version(self) -> str:
        return _calibrator_version(self.to_json(), self.method)

    def apply(self, logit: float) -> float:
        thresholds = self.thresholds
        values = self.values
        if logit <= thresholds[0]:
            return values[0]
        if logit >= thresholds[-1]:
            return values[-1]
        low = 0
        high = len(thresholds) - 1
        while high - low > 1:
            middle = (low + high) // 2
            if thresholds[middle] <= logit:
                low = middle
            else:
                high = middle
        span = thresholds[high] - thresholds[low]
        if span <= 0.0:
            return values[high]
        weight = (logit - thresholds[low]) / span
        interpolated = values[low] + weight * (values[high] - values[low])
        return min(1.0, max(0.0, interpolated))

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": CALIBRATION_SCHEMA_VERSION,
            "method": CalibrationMethod.ISOTONIC.value,
            "thresholds": list(self.thresholds),
            "values": list(self.values),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> IsotonicCalibrator:
        _require_method(payload, CalibrationMethod.ISOTONIC)
        thresholds = payload.get("thresholds")
        values = payload.get("values")
        if not isinstance(thresholds, list) or not isinstance(values, list):
            raise CalibrationDecodeError("isotonic thresholds and values must be lists")
        if not thresholds or len(thresholds) != len(values):
            raise CalibrationDecodeError(
                "isotonic thresholds and values must align and be non-empty"
            )
        if not all(_is_number(item) for item in (*thresholds, *values)):
            raise CalibrationDecodeError("isotonic knots must be numbers")
        return cls(
            thresholds=tuple(float(item) for item in thresholds),
            values=tuple(float(item) for item in values),
        )

    @classmethod
    def fit(cls, logits: Sequence[float], labels: Sequence[float]) -> IsotonicCalibrator:
        scores, targets = _checked_pairs(logits, labels)
        order = sorted(range(len(scores)), key=lambda index: (scores[index], index))
        knots: list[float] = []
        sums: list[float] = []
        weights: list[float] = []
        for index in order:
            score = scores[index]
            if knots and knots[-1] == score:
                sums[-1] += targets[index]
                weights[-1] += 1.0
            else:
                knots.append(score)
                sums.append(targets[index])
                weights.append(1.0)
        # Pool adjacent violators: merge left while the running block means decrease.
        block_sum: list[float] = []
        block_weight: list[float] = []
        block_end: list[int] = []
        for position, (total, weight) in enumerate(zip(sums, weights, strict=True)):
            block_sum.append(total)
            block_weight.append(weight)
            block_end.append(position)
            while len(block_sum) > 1 and (
                block_sum[-2] / block_weight[-2] > block_sum[-1] / block_weight[-1]
            ):
                merged_sum = block_sum.pop() + block_sum.pop()
                merged_weight = block_weight.pop() + block_weight.pop()
                block_end.pop(-2)
                block_sum.append(merged_sum)
                block_weight.append(merged_weight)
        fitted: list[float] = []
        start = 0
        for total, weight, end in zip(block_sum, block_weight, block_end, strict=True):
            value = min(1.0, max(0.0, total / weight))
            fitted.extend([value] * (end - start + 1))
            start = end + 1
        return cls(thresholds=tuple(knots), values=tuple(fitted))


def _require_method(payload: Mapping[str, Any], method: CalibrationMethod) -> None:
    version = payload.get("schema_version")
    if version != CALIBRATION_SCHEMA_VERSION:
        raise CalibrationDecodeError(
            f"calibration schema version {version!r} is not {CALIBRATION_SCHEMA_VERSION!r}"
        )
    if payload.get("method") != method.value:
        raise CalibrationDecodeError(
            f"payload method {payload.get('method')!r} is not {method.value!r}"
        )


def calibrator_from_json(payload: Mapping[str, Any]) -> Calibrator:
    """Rebuild whichever calibrator wrote ``payload``, by its declared method."""

    method = payload.get("method")
    if method == CalibrationMethod.PLATT.value:
        return PlattCalibrator.from_json(payload)
    if method == CalibrationMethod.ISOTONIC.value:
        return IsotonicCalibrator.from_json(payload)
    if method == CalibrationMethod.IDENTITY.value:
        return IdentityCalibrator.from_json(payload)
    raise CalibrationDecodeError(f"unknown calibration method {method!r}")


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _as_float(value: object, what: str) -> float:
    if not _is_number(value) or not isinstance(value, int | float):
        raise CalibrationDecodeError(f"{what} {value!r} is not a number")
    return float(value)


def _checked_pairs(
    scores: Sequence[float], labels: Sequence[float]
) -> tuple[list[float], list[float]]:
    if len(scores) != len(labels):
        raise CalibrationDataError(f"{len(scores)} scores but {len(labels)} labels")
    if not scores:
        raise CalibrationDataError("cannot calibrate on an empty sample")
    checked_scores: list[float] = []
    checked_labels: list[float] = []
    for index, (score, label) in enumerate(zip(scores, labels, strict=True)):
        value = float(score)
        target = float(label)
        if not math.isfinite(value):
            raise CalibrationDataError(f"score {score!r} at position {index} is not finite")
        if not 0.0 <= target <= 1.0:
            raise CalibrationDataError(f"label {label!r} at position {index} is outside [0, 1]")
        checked_scores.append(value)
        checked_labels.append(target)
    return checked_scores, checked_labels


def success_residuals(
    probabilities: Sequence[float], labels: Sequence[float]
) -> list[float]:
    """``r_i = max(0, p_i - y_i)``: how far the predictor over-promised on each row.

    One-sided on purpose. A lower confidence bound is only wrong when the prediction was too
    optimistic, so under-prediction contributes nothing and cannot buy back the budget that
    over-prediction spends.
    """

    checked_probabilities, checked_labels = _checked_pairs(probabilities, labels)
    return [
        max(0.0, probability - label)
        for probability, label in zip(checked_probabilities, checked_labels, strict=True)
    ]


def conformal_quantile(
    residuals: Sequence[float], alpha: float, groups: Sequence[str]
) -> float:
    """The split-conformal quantile over *projects*, not over rows.

    Each group is reduced to its mean residual and the ``ceil((m + 1)(1 - alpha))``-th smallest
    of the ``m`` group means is returned. Two consequences worth stating plainly. The exchangeable
    unit is a project, so a hundred rows from one project buy exactly as much confidence as five
    do — which is the correct answer, and the reason a row-level quantile silently overstates its
    sample size. And when ``m`` is too small for the index to exist at all — fewer than
    ``1/alpha - 1`` groups — there is no finite bound to give, so the maximum residual 1.0 is
    returned and every lower bound derived from it collapses to zero. Vacuous, and visibly so,
    rather than confident and wrong.
    """

    if not 0.0 < alpha < 1.0:
        raise CalibrationDataError(f"alpha {alpha} is outside (0, 1)")
    if len(residuals) != len(groups):
        raise CalibrationDataError(f"{len(residuals)} residuals but {len(groups)} groups")
    if not residuals:
        raise CalibrationDataError("cannot take a conformal quantile of an empty sample")
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for residual, group in zip(residuals, groups, strict=True):
        value = float(residual)
        if not math.isfinite(value):
            raise CalibrationDataError(f"residual {residual!r} for group {group!r} is not finite")
        totals[group] = totals.get(group, 0.0) + value
        counts[group] = counts.get(group, 0) + 1
    means = sorted(totals[group] / counts[group] for group in sorted(totals))
    m = len(means)
    index = math.ceil((m + 1) * (1.0 - alpha))
    if index > m:
        return 1.0
    return means[index - 1]


def lcb(p: float, q: float) -> float:
    """The lower confidence bound on a success probability: the estimate less the quantile."""

    return max(0.0, p - q)


class BootstrapInterval(StrictModel):
    """A percentile interval from a resampling draw, carrying the draw that produced it."""

    schema_version: Literal["1.0"] = "1.0"
    lower: float
    upper: float
    level: float = Field(gt=0, lt=1)
    bootstraps: int = Field(ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.lower > self.upper:
            raise ValueError(f"interval [{self.lower}, {self.upper}] is inverted")
        return self


def bootstrap_interval[T](
    values: Sequence[T],
    groups: Sequence[str],
    stat: Callable[[Sequence[T]], float],
    bootstraps: int = DEFAULT_BOOTSTRAPS,
    seed: int = 0,
    *,
    level: float = 0.95,
) -> BootstrapInterval:
    """Resample *projects* with replacement and report the percentile interval of ``stat``.

    The cluster bootstrap, for the reason :func:`conformal_quantile` groups: resampling rows would
    treat a hundred rows from one project as a hundred independent facts and would return an
    interval several times too narrow. ``seed`` is required in practice — the default exists so
    the parameter can be passed positionally in the plan's order — and the draw is a
    ``random.Random(seed)``, so two runs on the same data give the same interval.
    """

    if len(values) != len(groups):
        raise CalibrationDataError(f"{len(values)} values but {len(groups)} groups")
    if not values:
        raise CalibrationDataError("cannot bootstrap an empty sample")
    if bootstraps < 1:
        raise CalibrationDataError(f"bootstraps {bootstraps} must be at least 1")
    if not 0.0 < level < 1.0:
        raise CalibrationDataError(f"level {level} is outside (0, 1)")
    members: dict[str, list[T]] = {}
    for value, group in zip(values, groups, strict=True):
        members.setdefault(group, []).append(value)
    ordered = [members[group] for group in sorted(members)]
    rng = random.Random(seed)
    count = len(ordered)
    draws: list[float] = []
    for _ in range(bootstraps):
        resampled: list[T] = []
        for _ in range(count):
            resampled.extend(ordered[rng.randrange(count)])
        draws.append(stat(resampled))
    draws.sort()
    tail = (1.0 - level) / 2.0
    low = min(bootstraps - 1, max(0, math.floor(tail * bootstraps)))
    high = min(bootstraps - 1, max(low, math.ceil((1.0 - tail) * bootstraps) - 1))
    return BootstrapInterval(
        lower=draws[low], upper=draws[high], level=level, bootstraps=bootstraps
    )


def brier(probabilities: Sequence[float], labels: Sequence[float]) -> float:
    """Mean squared error of the probabilities: the proper score the report quotes alongside ECE."""

    checked_probabilities, checked_labels = _checked_pairs(probabilities, labels)
    return math.fsum(
        (probability - label) ** 2
        for probability, label in zip(checked_probabilities, checked_labels, strict=True)
    ) / len(checked_labels)


class ReliabilityBin(StrictModel):
    """One equal-width bin of the reliability diagram. Empty bins are omitted, not zero-filled:
    a bin with no rows has no observed rate, and inventing 0.0 for it would bias every reading.
    """

    lower: float = Field(ge=0, le=1)
    upper: float = Field(ge=0, le=1)
    count: int = Field(ge=1)
    mean_predicted: float = Field(ge=0, le=1)
    observed_rate: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _bin_is_coherent(self) -> Self:
        if self.lower >= self.upper:
            raise ValueError(f"bin [{self.lower}, {self.upper}) is empty or inverted")
        if not self.lower <= self.mean_predicted <= self.upper:
            raise ValueError(
                f"mean prediction {self.mean_predicted} lies outside its bin "
                f"[{self.lower}, {self.upper}]"
            )
        return self


def reliability_bins(
    probabilities: Sequence[float],
    labels: Sequence[float],
    bins: int = DEFAULT_BINS,
) -> list[ReliabilityBin]:
    """The reliability diagram as data: equal-width bins over [0, 1], ascending, empties dropped."""

    checked_probabilities, checked_labels = _checked_pairs(probabilities, labels)
    if bins < 1:
        raise CalibrationDataError(f"bins {bins} must be at least 1")
    totals = [0.0] * bins
    observed = [0.0] * bins
    counts = [0] * bins
    for probability, label in zip(checked_probabilities, checked_labels, strict=True):
        index = min(bins - 1, int(probability * bins))
        totals[index] += probability
        observed[index] += label
        counts[index] += 1
    result: list[ReliabilityBin] = []
    for index in range(bins):
        if counts[index] == 0:
            continue
        # The edges must be derived the same way the index was, and in the same order of
        # operations: ``index / bins`` is the inverse of ``int(probability * bins)``, while
        # ``index * (1.0 / bins)`` is a different double and can land above a probability that
        # binned here. The mean is then clamped into its own bin because summing k copies of a
        # value that sits exactly on an edge (an isotonic block mean such as 0.7 = 7/10) drifts
        # by an ulp either way, and the bin's own validator rejects that.
        lower = index / bins
        upper = min(1.0, (index + 1) / bins)
        mean_predicted = totals[index] / counts[index]
        result.append(
            ReliabilityBin(
                lower=lower,
                upper=upper,
                count=counts[index],
                mean_predicted=min(upper, max(lower, mean_predicted)),
                observed_rate=observed[index] / counts[index],
            )
        )
    return result


def ece(
    probabilities: Sequence[float],
    labels: Sequence[float],
    bins: int = DEFAULT_BINS,
) -> float:
    """Expected calibration error: the count-weighted gap between promise and outcome per bin."""

    diagram = reliability_bins(probabilities, labels, bins)
    total = float(len(labels))
    return math.fsum(
        (item.count / total) * abs(item.mean_predicted - item.observed_rate) for item in diagram
    )


class CohortCalibration(StrictModel):
    """Calibration inside one cohort. §14.3's reward-hacking check is a cohort question: a model
    can look calibrated overall while being badly wrong on exactly the slice that matters.
    """

    cohort_id: str = Field(min_length=1, max_length=128)
    ece: float = Field(ge=0, le=1)
    n: int = Field(ge=1)


class CalibrationReport(StrictModel):
    """What a router version must show before anything downstream may load it.

    Deliberately valueless as a summary and specific as evidence: the point is not the single
    ``ece_10bin`` number but that the bins, the cohorts, the conformal quantile, the coverage it
    actually achieved on held-out projects and the seed that produced all of it travel together.
    A number without its bins can be argued with; a number with its bins can be checked.
    """

    schema_version: Literal["1.0"] = "1.0"
    method: CalibrationMethod
    alpha: float = Field(gt=0, lt=1)
    conformal_quantile: float = Field(ge=0, le=1)
    ece_10bin: float = Field(ge=0, le=1)
    brier: float = Field(ge=0, le=1)
    bins: list[ReliabilityBin] = Field(min_length=1)
    holdout_coverage: float = Field(ge=0, le=1)
    bootstrap_ece_interval: BootstrapInterval
    per_cohort: list[CohortCalibration] = Field(default_factory=list)
    seed: int = Field(ge=0)
    feature_schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")

    @model_validator(mode="after")
    def _bins_and_cohorts_are_ordered(self) -> Self:
        edges = [item.lower for item in self.bins]
        if edges != sorted(edges) or len(set(edges)) != len(edges):
            raise ValueError("reliability bins must be ascending and non-overlapping")
        cohorts = [item.cohort_id for item in self.per_cohort]
        if cohorts != sorted(cohorts) or len(set(cohorts)) != len(cohorts):
            raise ValueError("per-cohort entries must be ascending and unique by cohort_id")
        return self


def build_calibration_report(
    *,
    probabilities: Sequence[float],
    labels: Sequence[float],
    groups: Sequence[str],
    method: CalibrationMethod,
    feature_schema_version: str,
    seed: int,
    cohorts: Sequence[str] | None = None,
    alpha: float = DEFAULT_ALPHA,
    conformal_quantile_value: float | None = None,
    bins: int = DEFAULT_BINS,
    bootstraps: int = DEFAULT_BOOTSTRAPS,
) -> CalibrationReport:
    """Assemble the report for one calibrated holdout.

    ``conformal_quantile_value`` is injected rather than recomputed when the caller has one: split
    conformal fits its quantile on the *calibration* split and measures coverage on the *holdout*,
    and a report that quietly refitted the quantile on the same rows it then scored would be
    reporting training coverage under a held-out name. Passing ``None`` means "this is the
    calibration split", and only then is the quantile taken from these rows.
    """

    checked_probabilities, checked_labels = _checked_pairs(probabilities, labels)
    if len(groups) != len(checked_labels):
        raise CalibrationDataError(f"{len(checked_labels)} rows but {len(groups)} groups")
    if cohorts is not None and len(cohorts) != len(checked_labels):
        raise CalibrationDataError(f"{len(checked_labels)} rows but {len(cohorts)} cohorts")
    residuals = success_residuals(checked_probabilities, checked_labels)
    quantile = (
        conformal_quantile(residuals, alpha, groups)
        if conformal_quantile_value is None
        else conformal_quantile_value
    )
    pairs = list(zip(checked_probabilities, checked_labels, strict=True))
    interval = bootstrap_interval(
        pairs,
        groups,
        lambda sample: ece([item[0] for item in sample], [item[1] for item in sample], bins),
        bootstraps,
        seed,
    )
    per_cohort: list[CohortCalibration] = []
    if cohorts is not None:
        buckets: dict[str, list[tuple[float, float]]] = {}
        for cohort, pair in zip(cohorts, pairs, strict=True):
            buckets.setdefault(cohort, []).append(pair)
        for cohort in sorted(buckets):
            sample = buckets[cohort]
            per_cohort.append(
                CohortCalibration(
                    cohort_id=cohort,
                    ece=ece([item[0] for item in sample], [item[1] for item in sample], bins),
                    n=len(sample),
                )
            )
    return CalibrationReport(
        method=method,
        alpha=alpha,
        conformal_quantile=quantile,
        ece_10bin=ece(checked_probabilities, checked_labels, bins),
        brier=brier(checked_probabilities, checked_labels),
        bins=reliability_bins(checked_probabilities, checked_labels, bins),
        holdout_coverage=group_coverage(residuals, groups, quantile),
        bootstrap_ece_interval=interval,
        per_cohort=per_cohort,
        seed=seed,
        feature_schema_version=feature_schema_version,
    )


def group_coverage(
    residuals: Sequence[float], groups: Sequence[str], quantile: float
) -> float:
    """The fraction of *groups* whose mean residual is within ``quantile``.

    The empirical counterpart of what :func:`conformal_quantile` promises, computed on the same
    exchangeable unit the promise was made about. Measuring it per row instead would answer a
    question nobody asked and would pass while the bound was failing on the small projects.
    """

    if len(residuals) != len(groups):
        raise CalibrationDataError(f"{len(residuals)} residuals but {len(groups)} groups")
    if not residuals:
        raise CalibrationDataError("cannot measure coverage on an empty sample")
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for residual, group in zip(residuals, groups, strict=True):
        totals[group] = totals.get(group, 0.0) + float(residual)
        counts[group] = counts.get(group, 0) + 1
    covered = sum(1 for group in totals if totals[group] / counts[group] <= quantile)
    return covered / len(totals)
