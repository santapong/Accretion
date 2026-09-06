"""Off-policy evaluation for the promotion gate: pessimism first, diagnostics second (R4, R3).

SDD §10.2 asks whether a candidate router is *better* than the incumbent, on evidence that
was logged under neither. That is an off-policy question, and off-policy questions have a
characteristic failure: the optimistic estimator. Inverse propensity scoring is unbiased and
has unbounded variance, so the policy it likes best is very often the policy whose weights
blew up on three lucky records — and a promotion gate built on it promotes noise.

**The primary estimator is Logarithmic Smoothing (R4), and everything else here is a
diagnostic.** :func:`ls_estimate` replaces each importance-weighted cost ``w·c`` with
``-(1/λ)·log(1 - λ·w·c)``, which for costs in ``[-1, 0]`` is a *pessimistic* surrogate: the
logarithm grows more slowly than its argument, so a large weight is never allowed to credit
the target policy with the full ``w·c`` it would earn under IPS. R4 proves a finite-sample
upper bound on the true risk from it and reports that pessimistic selection under it never
picked a policy worse than the logging policy across some five hundred scenarios. λ is not
tuned: :func:`lambda_rule` fixes it at ``1/sqrt(n)``, which is the rate R4 registers, and a
gate that could tune λ after seeing the rows would have one free parameter per surprising
result.

**Why costs are in ``[-1, 0]`` and not rewards in ``[0, 1]``.** R4's bound is stated for
bounded *costs* under minimisation, and the sign matters to the smoothing: ``-λ·w·c`` is
non-negative exactly when ``c ≤ 0``, so ``log1p`` is evaluated on ``[0, ∞)`` and the estimator
is monotone in the weight. Handing this module a reward in ``[0, 1]`` would put ``log1p`` on
``(-1, 0]``, where it diverges, and the estimator would go to ``-∞`` for a single large
weight — the exact optimism the smoothing exists to remove. So the convention is stated in
the argument name, checked at the boundary, and translated once by the caller: a verified
success is a cost of ``-1``, a failure is a cost of ``0``.

**λ → 0 recovers IPS**, and that is a test rather than a remark: ``-(1/λ)·log(1 - λwc) → wc``
as ``λ → 0``, so an implementation that smoothed in the wrong direction, or that dropped the
minus sign, is visible as a limit that does not converge to the unsmoothed mean.

**The sup-t band is here and not in :mod:`accretion.routing.stats` because it is about a
grid.** R3's policy class is "use the learned configuration only when its lower-confidence
success is at least ``c``", one policy per threshold, and the gate reads the *whole* grid
before it picks one. A per-threshold 95% interval read at the maximising threshold is not a
95% statement about that threshold, and Bonferroni over a grid of ten correlated thresholds
is enormously conservative because the thresholds are nearly the same policy. The sup-t band
takes the correlation from the bootstrap itself: it studentises each column, takes the
maximum absolute t across the grid *within* each replicate, and reads one critical value off
that maximum's distribution. Its coverage is simultaneous over the grid, which is what makes
"the threshold we chose" an admissible thing to have chosen.

The resampling is the two-level one :func:`~accretion.routing.stats.hierarchical_bootstrap`
performs, in the same order and off the same seeded generator — projects with replacement,
then that project's own units with replacement — because a promotion report quotes both and
two bootstraps that disagreed about what a "unit" was would be two answers to one question.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from accretion.contracts import StrictModel
from accretion.contracts.routing import UtilityWeights
from accretion.routing.stats import Interval

PROMOTION_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "evals" / "router" / "promotion.v1.json"
)
"""``evals/router/promotion.v1.json``, beside the benchmark corpus it is deliberately not in.

:class:`~accretion.router_benchmark.RouterBenchmarkConfig` is ``extra="forbid"`` and
``config.v1.json`` is the document it validates, so the promotion constants cannot be added
there without either widening a frozen model or breaking every reader of the corpus. A second
registered file is the cheaper of the two, and it also keeps the two freezes independent: the
benchmark's constants move when the corpus is regenerated and the gate's move when the gate
is re-registered, and those are not the same event (ADR4-M8-003).
"""


# --------------------------------------------------------------------------------------
# The estimators. Every one of them takes costs in [-1, 0] and returns a cost.
# --------------------------------------------------------------------------------------


def lambda_rule(n: int) -> float:
    """R4's registered smoothing rate, ``1/sqrt(n)``.

    A function of the sample size and of nothing else. Written as a named rule rather than
    inlined at the one call site so that ``promotion.v1.json``'s ``ls_lambda_rule`` string
    has something to name, and so a reader can see that ``n`` is the number of *units*
    rather than the number of projects: the bound R4 proves is over units, and feeding it
    the cluster count would make λ far too large and the estimator far too pessimistic.
    """

    if n < 1:
        raise ValueError(f"the smoothing rate needs at least one unit; got n={n}")
    return 1.0 / math.sqrt(float(n))


def _checked(w: Sequence[float], c: Sequence[float]) -> None:
    """The three preconditions every estimator here shares, checked once.

    Costs outside ``[-1, 0]`` are refused rather than clipped. A caller that handed this
    module a reward has made a sign error, and silently clipping it to zero would turn that
    error into a policy value of exactly zero for every unit — an answer that looks like a
    measurement.
    """

    if len(w) != len(c):
        raise ValueError(
            f"{len(w)} importance weights and {len(c)} costs; each cost is the cost of the "
            "action its weight re-weights, so the two are the same length by construction"
        )
    if not w:
        raise ValueError("an off-policy estimate needs at least one logged unit")
    for index, weight in enumerate(w):
        if weight < 0.0 or not math.isfinite(weight):
            raise ValueError(
                f"importance weight {weight} at position {index} is negative or not finite; "
                "a weight is a ratio of two probabilities"
            )
    for index, cost in enumerate(c):
        if not -1.0 <= cost <= 0.0:
            raise ValueError(
                f"cost {cost} at position {index} is outside [-1, 0]; R4's bound is stated "
                "for bounded costs under minimisation, and a reward handed in here would "
                "put log1p on the branch where it diverges"
            )


def ls_estimate(w: Sequence[float], c: Sequence[float], lam: float) -> float:
    """R4's Logarithmic-Smoothing estimate of the target policy's risk.

    ``mean_i -(1/λ)·log(1 - λ·w_i·c_i)``. With ``c_i ≤ 0`` and ``w_i ≥ 0`` the logarithm's
    argument is at least one, so every term is in ``[w_i·c_i, 0]``: the estimate is a cost,
    it is never optimistic about a large weight, and it is exactly ``0`` for a unit the
    target policy did not retain (``w_i = 0``). That last property is what lets the caller
    express "defer to the baseline here" as a zero weight rather than as a filtered list,
    and keeps ``n`` — and therefore λ — the size of the holdout rather than of a subset the
    threshold chose.
    """

    _checked(w, c)
    if lam <= 0.0 or not math.isfinite(lam):
        raise ValueError(
            f"the smoothing rate must be positive and finite; got λ={lam}. λ = 0 is the "
            "unsmoothed IPS limit and is approached, never passed in"
        )
    terms = [-math.log1p(-lam * weight * cost) / lam for weight, cost in zip(w, c, strict=True)]
    return math.fsum(terms) / float(len(terms))


def snips(w: Sequence[float], c: Sequence[float]) -> float:
    """The self-normalised IPS estimate: ``Σ w·c / Σ w``. A diagnostic, never the gate.

    Kept because it fails differently from :func:`ls_estimate`: SNIPS is biased and bounded
    within the observed cost range, so a large gap between the two is a signal that the
    weights are concentrated rather than that either estimate is wrong. R4 explicitly does
    not cover self-normalised or doubly-robust estimators, which is why this one may inform
    a reader and may not decide a promotion.

    Returns ``0.0`` when the target policy retained nothing. That is not a fallback: with
    costs in ``[-1, 0]``, zero is the cost of taking no credited action at all, which is
    exactly what an all-zero weight vector describes.
    """

    _checked(w, c)
    total = math.fsum(w)
    if total <= 0.0:
        return 0.0
    return math.fsum(weight * cost for weight, cost in zip(w, c, strict=True)) / total


def dr(w: Sequence[float], c: Sequence[float], q: Sequence[float]) -> float:
    """The doubly-robust estimate: ``mean_i q_i + w_i·(c_i - q_i)``. Also a diagnostic.

    ``q`` is the direct method's predicted cost for each unit under the *target* policy —
    here, what the baseline model expects the outcome to cost. DR is unbiased if either the
    weights or ``q`` are right, which is a genuinely useful property and still not a
    finite-sample bound, so it sits beside SNIPS on the diagnostic side of the line R4 draws.
    """

    _checked(w, c)
    if len(q) != len(c):
        raise ValueError(
            f"{len(q)} direct-method predictions for {len(c)} units; the regression is "
            "evaluated at every logged context or it corrects nothing"
        )
    terms = [
        predicted + weight * (cost - predicted)
        for weight, cost, predicted in zip(w, c, q, strict=True)
    ]
    return math.fsum(terms) / float(len(terms))


def ess(w: Sequence[float]) -> float:
    """Kish's effective sample size, ``(Σ w)² / Σ w²``.

    The number this reports is how many equally-weighted units the weighted sample is worth.
    A promotion report quotes it because "n = 400" and "ESS = 6" are the same holdout and
    completely different evidence, and the second one is a gate a reader can apply by eye:
    an estimate whose ESS has collapsed is an estimate about a handful of records however
    many were logged.
    """

    if not w:
        raise ValueError("the effective sample size of an empty sample is undefined")
    squares = math.fsum(weight * weight for weight in w)
    if squares <= 0.0:
        return 0.0
    return (math.fsum(w) ** 2) / squares


def clip_mass(w: Sequence[float], tau: float) -> float:
    """The share of total importance weight that clipping at ``tau`` would discard.

    Reported rather than applied. Clipping trades variance for a bias whose size is exactly
    this number, so a gate that clipped silently would be a gate with an undisclosed bias;
    a gate that reports the mass it *would* have removed lets a reader price the pessimistic
    estimate against the positivity violation behind it. ``0.0`` for an all-zero weight
    vector, where there is no mass to discard.
    """

    if not w:
        raise ValueError("clip mass is undefined for an empty sample")
    if tau <= 0.0 or not math.isfinite(tau):
        raise ValueError(f"the clipping threshold must be positive and finite; got {tau}")
    total = math.fsum(w)
    if total <= 0.0:
        return 0.0
    return math.fsum(max(0.0, weight - tau) for weight in w) / total


# --------------------------------------------------------------------------------------
# The simultaneous band over the threshold grid.
# --------------------------------------------------------------------------------------


def _quantile(ordered: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile of an already-sorted sample.

    Written out rather than imported from :mod:`accretion.routing.stats`, whose equivalent
    is private, and deliberately using the same ``(n - 1)·q`` convention so that a band read
    here and a percentile interval read there agree on what "the 95th" means.
    """

    if not ordered:
        raise ValueError("no replicates to take a quantile of")
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[int(low)]
    weight = position - low
    return ordered[int(low)] * (1.0 - weight) + ordered[int(high)] * weight


def _column_means(units: Sequence[Sequence[float]], width: int) -> list[float]:
    """The mean of each column across pooled units."""

    total = float(len(units))
    return [math.fsum(unit[index] for unit in units) / total for index in range(width)]


def sup_t_band(
    groups: Mapping[str, Sequence[Sequence[float]]],
    B: int,
    seed: int,
    alpha: float = 0.05,
) -> list[Interval]:
    """A band that covers *every* column of ``groups`` simultaneously with probability ``1 - α``.

    Each unit is a vector with one entry per threshold — the same node's contribution to the
    policy value at each candidate ``c`` — so the columns are read off one resample rather
    than from independent bootstraps, and their correlation is what makes the band tight.

    Resampling is the hierarchical one: ``len(groups)`` project ids drawn with replacement
    off a sorted list, then each drawn project's own number of units drawn with replacement
    from it. Sorting the ids before sampling is what makes ``seed`` mean something; without
    it a dictionary's iteration order would be a second, unrecorded seed.

    A column whose bootstrap standard error is zero contributes no ``t`` — every replicate
    put it in the same place, so it cannot be the column that widens the band — and its
    interval is the degenerate ``(θ, θ)``. That is the honest reading of a statistic that
    did not move: a positive width there would be invented, and a ``0/0`` would be a crash.
    """

    if not groups:
        raise ValueError("the bootstrap needs at least one group")
    if B < 1:
        raise ValueError(f"the bootstrap needs at least one replicate; got B={B}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must lie in (0, 1); got {alpha}")
    group_ids = sorted(groups)
    widths = {len(unit) for units in groups.values() for unit in units}
    if not widths:
        raise ValueError("every group is empty; there is nothing to resample")
    if len(widths) != 1:
        raise ValueError(
            f"units carry {sorted(widths)!r} different lengths; a simultaneous band is over "
            "one grid, and rows of different lengths are two grids"
        )
    width = widths.pop()
    if width == 0:
        raise ValueError("the threshold grid is empty, so there is no band to compute")
    for group_id in group_ids:
        if not groups[group_id]:
            raise ValueError(f"group {group_id!r} has no units to resample")

    pooled_all = [unit for group_id in group_ids for unit in groups[group_id]]
    point = _column_means(pooled_all, width)

    # ``random.Random`` and not ``numpy``: the repository has no numpy dependency, and the
    # draw order below matches ``hierarchical_bootstrap``'s exactly so that a band and a
    # percentile interval quoted in one report were taken over the same resamples.
    rng = random.Random(seed)
    replicates: list[list[float]] = []
    for _ in range(B):
        pooled: list[Sequence[float]] = []
        for _ in range(len(group_ids)):
            units = groups[rng.choice(group_ids)]
            pooled.extend(rng.choice(units) for _ in range(len(units)))
        replicates.append(_column_means(pooled, width))

    errors: list[float] = []
    for index in range(width):
        column = [replicate[index] for replicate in replicates]
        mean = math.fsum(column) / float(B)
        if B < 2:
            errors.append(0.0)
            continue
        variance = math.fsum((value - mean) ** 2 for value in column) / float(B - 1)
        errors.append(math.sqrt(variance))

    statistics: list[float] = []
    for replicate in replicates:
        deviations = [
            abs(replicate[index] - point[index]) / errors[index]
            for index in range(width)
            if errors[index] > 0.0
        ]
        statistics.append(max(deviations) if deviations else 0.0)
    statistics.sort()
    critical = _quantile(statistics, 1.0 - alpha)
    return [
        (point[index] - critical * errors[index], point[index] + critical * errors[index])
        for index in range(width)
    ]


# --------------------------------------------------------------------------------------
# The registered constants.
# --------------------------------------------------------------------------------------


class PromotionConfig(StrictModel):
    """``promotion.v1.json``: every number the CSPI-MT gate is not allowed to choose.

    Pre-registration, for the reason :class:`~accretion.router_benchmark.RouterBenchmarkConfig`
    gives about the benchmark: a gate whose margin could be set after the report was read is
    a gate with one free parameter per disappointing candidate. The two files are separate
    because the benchmark config is ``extra="forbid"`` and because they are re-registered on
    different occasions (ADR4-M8-003).

    ``critical_cohorts`` is a list and not a hard-coded set for the reason
    :class:`~accretion.contracts.routing.CohortResult` carries ``critical`` per row: OQ-413
    names five and leaves the list open, so "a critical regression blocks" has to stay true
    when a sixth is registered.
    """

    schema_version: Literal["1.0"] = "1.0"
    config_version: str = Field(min_length=1, max_length=64)
    seed: int = Field(ge=0)
    gamma: float = Field(gt=0, lt=1)
    delta_min: float = Field(gt=0, le=1)
    delta_ni: float = Field(ge=-1, le=0)
    alpha: float = Field(gt=0, lt=1)
    power: float = Field(gt=0, lt=1)
    tune_eval_split: list[float] = Field(min_length=2, max_length=2)
    threshold_grid_start: float = Field(ge=0, le=1)
    threshold_grid_stop: float = Field(ge=0, le=1)
    threshold_grid_step: float = Field(gt=0, le=1)
    ls_lambda_rule: Literal["1/sqrt(n)"]
    clip_beta_rule: Literal["conformal"]
    bootstrap_replicates: int = Field(ge=1)
    critical_cohorts: list[str] = Field(min_length=1, max_length=64)
    calibration_max_ece: float = Field(ge=0, le=1)
    shadow_min_paired_runs: int = Field(ge=1)
    multiplicity: Literal["bonferroni"]
    utility_weights: UtilityWeights

    @model_validator(mode="after")
    def _the_split_and_the_grid_are_well_formed(self) -> Self:
        tune, evaluation = self.tune_eval_split
        if tune <= 0.0 or evaluation <= 0.0:
            raise ValueError(
                f"the tune/eval split {self.tune_eval_split!r} gives one half no projects; "
                "R3 chooses the threshold on the tune half and tests it on the other, and a "
                "half of size zero collapses that into choosing and testing on one sample"
            )
        if abs(tune + evaluation - 1.0) > 1e-9:
            raise ValueError(
                f"the tune/eval split {self.tune_eval_split!r} does not sum to one; the two "
                "halves partition the holdout projects and nothing may be in neither"
            )
        if self.threshold_grid_stop < self.threshold_grid_start:
            raise ValueError(
                f"the threshold grid runs from {self.threshold_grid_start} to "
                f"{self.threshold_grid_stop}, which is backwards"
            )
        if self.threshold_grid_step > (self.threshold_grid_stop - self.threshold_grid_start):
            if self.threshold_grid_stop != self.threshold_grid_start:
                raise ValueError(
                    f"a step of {self.threshold_grid_step} does not fit inside "
                    f"[{self.threshold_grid_start}, {self.threshold_grid_stop}]; the grid "
                    "would hold one threshold and the multiplicity correction would be a "
                    "correction for a family of one"
                )
        if len(set(self.critical_cohorts)) != len(self.critical_cohorts):
            raise ValueError("critical_cohorts repeats a cohort id")
        if sorted(self.critical_cohorts) != self.critical_cohorts:
            raise ValueError(
                "critical_cohorts must be ascending, so that two readers of the registered "
                "file compare the same list rather than the same set"
            )
        return self

    def thresholds(self) -> list[float]:
        """The registered acceptance grid, inclusive of both ends where the step lands on it.

        Built from three numbers rather than written out as a list because the three numbers
        are what is registered: a literal list could be edited one entry at a time without
        the edit reading as a change of grid. Values are rounded to ten places so that a
        step of ``0.05`` does not accumulate a float error into the tenth threshold and make
        the grid depend on the order it was summed in.
        """

        grid: list[float] = []
        steps = int(
            round((self.threshold_grid_stop - self.threshold_grid_start) / self.threshold_grid_step)
        )
        for index in range(steps + 1):
            grid.append(round(self.threshold_grid_start + index * self.threshold_grid_step, 10))
        return grid

    def tune_projects(self, project_ids: Sequence[str]) -> tuple[list[str], list[str]]:
        """Split holdout projects into the tune half and the evaluation half, deterministically.

        By position in the *sorted* list and not at random: R3's guarantee needs the two
        halves to be disjoint and needs the choice of ``c*`` to have been made without seeing
        the evaluation half, and neither of those is helped by randomness — while a shuffled
        split would make the report's ``c*`` depend on a seed nobody quoted. At least one
        project lands on each side whenever there are two; with a single holdout project the
        tune half is empty and the caller has to say so rather than silently tuning on the
        sample it then tests.
        """

        ordered = sorted(project_ids)
        if len(ordered) < 2:
            return ([], ordered)
        take = max(1, min(len(ordered) - 1, int(round(len(ordered) * self.tune_eval_split[0]))))
        return (ordered[:take], ordered[take:])

    @classmethod
    def load(cls, path: Path = PROMOTION_CONFIG_PATH) -> PromotionConfig:
        """Read and validate the registered file. Raises ``FileNotFoundError`` if it is gone.

        No caching here: :func:`default_promotion_config` is the cached door, and a
        classmethod that cached would make a test that points at another file get the first
        test's document.
        """

        document: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError(f"{path} must contain a JSON object")
        return cls.model_validate(document)


@cache
def default_promotion_config() -> PromotionConfig:
    """The registered gate constants, parsed once per process.

    Cached because the HTTP route builds an evaluator per request and re-reading a committed
    JSON file on every promotion evaluation would be work performed to get the same answer.
    """

    return PromotionConfig.load()


__all__ = [
    "PROMOTION_CONFIG_PATH",
    "PromotionConfig",
    "clip_mass",
    "default_promotion_config",
    "dr",
    "ess",
    "lambda_rule",
    "ls_estimate",
    "snips",
    "sup_t_band",
]
