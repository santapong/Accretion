"""Selection-valid estimands, exact intervals, multiplicity, clustered bootstrap, power.

The router benchmark makes a numerical claim — "adaptive routing beats the best fixed
configuration" — and this module is the arithmetic that claim is allowed to use. Everything
here is pure: no store, no clock, no network, no dependency beyond the standard library, and
no floating-point path that a seed does not pin. That is not austerity for its own sake. A
benchmark number is only evidence if a reviewer can rerun it and get the same digits, and a
statistic that reaches for ``scipy`` on one machine and a rational approximation on another
is two statistics wearing one name.

**Three estimands, one denominator.** The research protocol §12 asks for a project-clustered
paired estimator and a superiority claim with an interval that excludes no improvement. That
leaves open the question the routing literature (2608.08265) answers: *improvement over
what?* Three quantities are reported, all measured on the same evaluation split and all
against the same baseline — the best fixed configuration:

* ``g_out`` — **oracle opportunity**. The per-task best over all K configurations, minus the
  baseline. This is the ceiling: no router that only *chooses among these configurations*
  can beat it, so a benchmark whose ``g_out`` interval includes zero has shown that there was
  nothing to win, and no result about the router is interesting.
* ``g_z`` — **signal-restricted opportunity**. The best achievable by a chooser that sees only
  the routing signal Z, minus the baseline. Between ``g_learn`` and ``g_out`` it separates
  "the router is weak" from "the signal is weak", which are different bugs with different fixes.
* ``g_learn`` — **learned gain**. What the router actually did, minus the baseline. The only
  one of the three that is a claim about the shipped system.

``recovered_fraction`` is ``g_learn / g_out``: the share of the available opportunity the
router captured. It is reported **only** when the lower limit of the opportunity gap's
interval is positive, because a ratio whose denominator has not been shown to differ from
zero is a number with no defined sign and a very persuasive appearance.

**Selection validity.** The baseline is *chosen* — argmax over K configurations — and an
argmax evaluated on the data that selected it is biased upward by the winner's curse, which
would silently shrink every gap above. So :func:`select_best_fixed` takes two disjoint sets
of task ids and uses them for two different jobs: the selection split picks the winner, and
only then does the evaluation split score it. The split ids are required to be disjoint;
the function refuses rather than warns, because a leak here does not produce a worse number,
it produces a wrong one that looks fine.

**Exactness and multiplicity.** Success counts are small, so intervals are Clopper–Pearson
(exact, conservative) rather than normal-approximate: at n = 10 the Wald interval for 3/10
extends below zero, which is not a probability. Protocol §12.4 requires a registered
multiplicity correction for the secondary comparisons, and :func:`estimands` applies
Bonferroni over the K fixed configurations that competed for the baseline slot plus the L
policies reported against it — the selection is a comparison too, and pretending otherwise
is where a "corrected" analysis usually leaks its alpha.

**Clustering.** Nodes within a project share an objective, a repository and a policy set, so
they are not independent draws and an interval that treats them as such is too narrow.
:func:`hierarchical_bootstrap` resamples *projects* with replacement and then units within
each resampled project (protocol §12.1), which keeps the between-project variance in the
interval where it belongs. :func:`paired_regret_ci` is that estimator applied to within-pair
differences, which is the protocol's primary analysis.

**Power.** :func:`power_sample_size` sizes a two-sample comparison from a pre-registered
minimum meaningful effect. With the run-to-run standard deviation of about 1.5 pp reported
for repeated agent evaluations (2602.07150), detecting 2 pp needs about 9 runs per
configuration and 1 pp about 36 — the difference between a cheap experiment and an expensive
one, which is why the number is computed before the runs rather than after.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

Interval = tuple[float, float]
"""A closed confidence interval as ``(lower, upper)``. Always ordered, never ``None``."""

Statistic = Callable[[Sequence[float]], float]
"""A summary of pooled units, e.g. the mean. Called once per bootstrap replicate."""

_BETA_MAX_ITERATIONS = 300
"""Continued-fraction iteration cap. Lentz's method converges in well under 100 here."""

_BETA_EPSILON = 3.0e-16
"""Relative convergence target for the continued fraction — about one ulp of a double."""

_BETA_TINY = 1.0e-300
"""Lentz's floor: replaces an exact zero denominator so a rescaling cannot divide by it."""

_QUANTILE_ITERATIONS = 200
"""Bisection steps for the beta quantile. 200 halvings of [0, 1] exhaust double precision."""


# --------------------------------------------------------------------------------------
# The regularised incomplete beta function, and its inverse.
#
# Clopper-Pearson limits are beta quantiles, so this is the one piece of special-function
# machinery the module cannot avoid. It is Numerical Recipes' `betacf`/`betai` pair:
# a continued fraction evaluated by the modified Lentz algorithm, entered from whichever
# tail converges. Everything takes floats, not integers -- the limits for x successes in n
# trials need Beta(x, n - x + 1) and Beta(x + 1, n - x), and callers outside Clopper-Pearson
# have no reason to hold to whole numbers.
# --------------------------------------------------------------------------------------


def _beta_continued_fraction(x: float, a: float, b: float) -> float:
    """The continued fraction for ``I_x(a, b)``, by the modified Lentz algorithm.

    Converges rapidly for ``x < (a + 1) / (a + b + 2)``; :func:`regularised_incomplete_beta`
    is responsible for reflecting the argument when it is not, and this function does not
    check, because the check would be a second copy of the same threshold.
    """

    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _BETA_TINY:
        d = _BETA_TINY
    d = 1.0 / d
    result = d
    for iteration in range(1, _BETA_MAX_ITERATIONS + 1):
        even = 2 * iteration
        # The even step of the fraction.
        numerator = iteration * (b - iteration) * x / ((qam + even) * (a + even))
        d = 1.0 + numerator * d
        if abs(d) < _BETA_TINY:
            d = _BETA_TINY
        c = 1.0 + numerator / c
        if abs(c) < _BETA_TINY:
            c = _BETA_TINY
        d = 1.0 / d
        result *= d * c
        # The odd step.
        numerator = -(a + iteration) * (qab + iteration) * x / ((a + even) * (qap + even))
        d = 1.0 + numerator * d
        if abs(d) < _BETA_TINY:
            d = _BETA_TINY
        c = 1.0 + numerator / c
        if abs(c) < _BETA_TINY:
            c = _BETA_TINY
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) < _BETA_EPSILON:
            break
    return result


def regularised_incomplete_beta(x: float, a: float, b: float) -> float:
    """``I_x(a, b)``: the CDF of a Beta(a, b) variable at ``x``, for real ``a, b > 0``.

    Exact at the tabulated integer points — ``I_0.5(2, 3) = 0.6875``, ``I_0.2(1, 1) = 0.2``
    — to within a few ulp, and equally defined at non-integer parameters, which is what
    Clopper–Pearson and any future Jeffreys interval need.
    """

    if a <= 0.0 or b <= 0.0:
        raise ValueError(f"beta parameters must be positive; got a={a}, b={b}")
    if not 0.0 <= x <= 1.0:
        raise ValueError(f"the argument of I_x(a, b) must lie in [0, 1]; got {x}")
    if x == 0.0:
        return 0.0
    if x == 1.0:
        return 1.0
    log_front = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    front = math.exp(log_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(x, a, b) / a
    return 1.0 - front * _beta_continued_fraction(1.0 - x, b, a) / b


def beta_quantile(p: float, a: float, b: float) -> float:
    """The ``p``-quantile of Beta(a, b), by bisection on :func:`regularised_incomplete_beta`.

    Bisection and not Newton: ``I_x`` is monotone on [0, 1], so bisection cannot diverge or
    leave the support, and 200 halvings reach the precision of a double regardless of how
    flat the density is near the limit. A Newton step would be faster and would occasionally
    step outside [0, 1] for the extreme tails these intervals are made of.
    """

    if not 0.0 <= p <= 1.0:
        raise ValueError(f"a quantile probability must lie in [0, 1]; got {p}")
    if p == 0.0:
        return 0.0
    if p == 1.0:
        return 1.0
    low = 0.0
    high = 1.0
    for _ in range(_QUANTILE_ITERATIONS):
        middle = 0.5 * (low + high)
        if middle <= low or middle >= high:
            break
        if regularised_incomplete_beta(middle, a, b) < p:
            low = middle
        else:
            high = middle
    return 0.5 * (low + high)


# --------------------------------------------------------------------------------------
# Intervals, multiplicity, and normal quantiles.
# --------------------------------------------------------------------------------------


def clopper_pearson(successes: int, n: int, alpha: float = 0.05) -> Interval:
    """The exact ``1 - alpha`` interval for a binomial proportion (Clopper–Pearson, 1934).

    The limits are beta quantiles: ``lo = B^-1(alpha/2; x, n - x + 1)`` and
    ``hi = B^-1(1 - alpha/2; x + 1, n - x)``, with the degenerate ends returned exactly —
    ``x = 0`` has lower limit 0 and ``x = n`` has upper limit 1, because there is no
    evidence against a proportion the sample never contradicted.

    Exact means *at least* ``1 - alpha`` coverage, never less; the interval is conservative
    and that is the property wanted here, since every gap in :func:`estimands` is a
    difference of two of these and an anti-conservative input would make the whole claim
    anti-conservative. ``n = 0`` returns the whole unit interval rather than raising: a
    cohort with no observations is a legitimate, and highly informative, ``[0, 1]``.
    """

    if n < 0:
        raise ValueError(f"trial count cannot be negative; got n={n}")
    if not 0 <= successes <= n:
        raise ValueError(f"successes must lie in [0, n]; got {successes} of {n}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must lie in (0, 1); got {alpha}")
    if n == 0:
        return (0.0, 1.0)
    lower = (
        0.0
        if successes == 0
        else beta_quantile(alpha / 2.0, float(successes), float(n - successes + 1))
    )
    upper = (
        1.0
        if successes == n
        else beta_quantile(1.0 - alpha / 2.0, float(successes + 1), float(n - successes))
    )
    return (lower, upper)


def bonferroni(alpha: float, k: int) -> float:
    """The per-comparison level for ``k`` comparisons at family-wise level ``alpha``.

    ``alpha / k``, which is the whole of it. It is a named function rather than an inline
    division so that the divisor is visible at every call site and a reviewer can see what
    ``k`` was counted as — which is the only part of Bonferroni anyone ever gets wrong.
    """

    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must lie in (0, 1); got {alpha}")
    if k < 1:
        raise ValueError(f"a family has at least one comparison; got k={k}")
    return alpha / k


_ACKLAM_A = (
    -3.969683028665376e01,
    2.209460984245205e02,
    -2.759285104469687e02,
    1.383577518672690e02,
    -3.066479806614716e01,
    2.506628277459239e00,
)
_ACKLAM_B = (
    -5.447609879822406e01,
    1.615858368580409e02,
    -1.556989798598866e02,
    6.680131188771972e01,
    -1.328068155288572e01,
)
_ACKLAM_C = (
    -7.784894002430293e-03,
    -3.223964580411365e-01,
    -2.400758277161838e00,
    -2.549732539343734e00,
    4.374664141464968e00,
    2.938163982698783e00,
)
_ACKLAM_D = (
    7.784695709041462e-03,
    3.224671290700398e-01,
    2.445134137142996e00,
    3.754408661907416e00,
)
_ACKLAM_LOW = 0.02425
"""Below this the central rational form loses accuracy and the tail form takes over."""


def normal_quantile(p: float) -> float:
    """The standard normal quantile ``z`` with ``P(Z <= z) = p``, for ``0 < p < 1``.

    Acklam's rational approximation, whose documented relative error is below
    ``1.15e-9`` over the whole open interval, followed by one Halley refinement against
    :func:`math.erfc`. The refinement costs one special-function call and takes the result to
    within a few ulp; it is included because :func:`power_sample_size` squares this number,
    which would square the error too, and because a sample size that changes when the
    approximation is swapped is a sample size nobody can reproduce.
    """

    if not 0.0 < p < 1.0:
        raise ValueError(f"a normal quantile needs 0 < p < 1; got {p}")
    if p < _ACKLAM_LOW:
        q = math.sqrt(-2.0 * math.log(p))
        z = (
            ((((_ACKLAM_C[0] * q + _ACKLAM_C[1]) * q + _ACKLAM_C[2]) * q + _ACKLAM_C[3]) * q
             + _ACKLAM_C[4]) * q + _ACKLAM_C[5]
        ) / ((((_ACKLAM_D[0] * q + _ACKLAM_D[1]) * q + _ACKLAM_D[2]) * q + _ACKLAM_D[3]) * q + 1.0)
    elif p <= 1.0 - _ACKLAM_LOW:
        q = p - 0.5
        r = q * q
        z = (
            ((((_ACKLAM_A[0] * r + _ACKLAM_A[1]) * r + _ACKLAM_A[2]) * r + _ACKLAM_A[3]) * r
             + _ACKLAM_A[4]) * r + _ACKLAM_A[5]
        ) * q / (
            ((((_ACKLAM_B[0] * r + _ACKLAM_B[1]) * r + _ACKLAM_B[2]) * r + _ACKLAM_B[3]) * r
             + _ACKLAM_B[4]) * r + 1.0
        )
    else:
        q = math.sqrt(-2.0 * math.log1p(-p))
        z = -(
            ((((_ACKLAM_C[0] * q + _ACKLAM_C[1]) * q + _ACKLAM_C[2]) * q + _ACKLAM_C[3]) * q
             + _ACKLAM_C[4]) * q + _ACKLAM_C[5]
        ) / ((((_ACKLAM_D[0] * q + _ACKLAM_D[1]) * q + _ACKLAM_D[2]) * q + _ACKLAM_D[3]) * q + 1.0)
    # One Halley step on F(z) - p, with F' the standard normal density.
    error = 0.5 * math.erfc(-z / math.sqrt(2.0)) - p
    scaled = error * math.sqrt(2.0 * math.pi) * math.exp(z * z / 2.0)
    return z - scaled / (1.0 + z * scaled / 2.0)


def power_sample_size(
    delta: float, sigma: float, alpha: float = 0.05, power: float = 0.8
) -> int:
    """Runs per configuration to detect a difference ``delta`` at ``power``, two-sample.

    ``n = ceil(2 ((z_{1-alpha/2} + z_{power}) sigma / delta)^2)`` — the textbook two-sample
    normal formula, with the factor 2 because the difference of two independent arms carries
    twice the variance of one. It is deliberately the *unadjusted* form: no finite-population
    correction, no t-distribution inflation for the small ``n`` it returns. Both would move
    the answer by a run or two, and a sizing that pretends to more precision than the
    ``sigma`` it was handed is false comfort.

    With the ``sigma ≈ 1.5 pp`` run-to-run spread measured for repeated agent evaluations
    (2602.07150), ``delta = 2 pp`` gives 9 and ``delta = 1 pp`` gives 36: the quadratic in
    ``1/delta`` is the whole budgeting story.
    """

    if delta <= 0.0:
        raise ValueError(f"the detectable difference must be positive; got {delta}")
    if sigma <= 0.0:
        raise ValueError(f"the standard deviation must be positive; got {sigma}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must lie in (0, 1); got {alpha}")
    if not 0.0 < power < 1.0:
        raise ValueError(f"power must lie in (0, 1); got {power}")
    z_alpha = normal_quantile(1.0 - alpha / 2.0)
    z_power = normal_quantile(power)
    return math.ceil(2.0 * ((z_alpha + z_power) * sigma / delta) ** 2)


# --------------------------------------------------------------------------------------
# Repeated-sampling success measures.
# --------------------------------------------------------------------------------------


def pass_at_k(n: int, c: int, k: int) -> float:
    """The unbiased ``pass@k`` estimator: ``1 - C(n - c, k) / C(n, k)``.

    ``n`` samples were drawn and ``c`` of them passed; this is the probability that a random
    subset of ``k`` of those samples contains at least one pass. Computed as the product
    ``1 - prod_{i=n-c+1}^{n} (1 - k/i)`` rather than from binomial coefficients, because the
    coefficients overflow the exact-integer comfort zone long before the product loses a
    digit, and the product needs no big integers at all.

    Reporting ``c/n`` instead would answer a different question — the chance that *one*
    sample passes — and the two diverge exactly where agent evaluation lives, at small ``n``
    with a low pass rate.
    """

    if n < 1:
        raise ValueError(f"pass@k needs at least one sample; got n={n}")
    if not 0 <= c <= n:
        raise ValueError(f"the pass count must lie in [0, n]; got {c} of {n}")
    if not 1 <= k <= n:
        raise ValueError(f"k must lie in [1, n]; got k={k} with n={n}")
    if n - c < k:
        return 1.0
    failure = 1.0
    for i in range(n - c + 1, n + 1):
        failure *= 1.0 - k / i
    return 1.0 - failure


def pass_pow_k(p: float, k: int) -> float:
    """``pass^k``: the probability that ``k`` independent attempts all succeed, ``p ** k``.

    The pessimistic twin of :func:`pass_at_k`. ``pass@k`` asks whether a user who may retry
    ever succeeds; ``pass^k`` asks whether an unattended pipeline of ``k`` steps survives,
    which is the question a router that composes nodes is actually facing. ``k = 0`` is 1.0:
    a pipeline of no steps cannot fail.
    """

    if not 0.0 <= p <= 1.0:
        raise ValueError(f"a success probability must lie in [0, 1]; got {p}")
    if k < 0:
        raise ValueError(f"the number of attempts cannot be negative; got k={k}")
    return p**k


# --------------------------------------------------------------------------------------
# The clustered bootstrap.
# --------------------------------------------------------------------------------------


def _percentile(ordered: Sequence[float], q: float) -> float:
    """Linear-interpolated ``q``-quantile of an already sorted sequence."""

    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def hierarchical_bootstrap(
    groups: Mapping[str, list[float]],
    stat: Statistic,
    B: int,
    seed: int,
    alpha: float = 0.05,
) -> Interval:
    """A percentile interval for ``stat``, resampling groups and then units within them.

    Protocol §12.1's primary estimator. Each replicate draws ``len(groups)`` group ids with
    replacement, then for every drawn group draws that group's own number of units with
    replacement, pools them and applies ``stat``. Two levels, in that order: resampling only
    the units would treat two nodes from one project as two independent facts, and produce an
    interval that is too narrow by roughly the square root of the cluster size — precisely the
    error that makes a clustered benchmark announce a difference that does not replicate.

    ``seed`` is mandatory and there is no unseeded path, so the interval is a function of its
    inputs. Group ids are sorted before sampling: a dict iteration order that varied would
    make ``seed`` a lie.
    """

    if not groups:
        raise ValueError("the bootstrap needs at least one group")
    if B < 1:
        raise ValueError(f"the bootstrap needs at least one replicate; got B={B}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must lie in (0, 1); got {alpha}")
    group_ids = sorted(groups)
    for group_id in group_ids:
        if not groups[group_id]:
            raise ValueError(f"group {group_id!r} has no units to resample")
    rng = random.Random(seed)
    replicates: list[float] = []
    for _ in range(B):
        pooled: list[float] = []
        for _ in range(len(group_ids)):
            units = groups[rng.choice(group_ids)]
            pooled.extend(rng.choice(units) for _ in range(len(units)))
        replicates.append(stat(pooled))
    replicates.sort()
    return (
        _percentile(replicates, alpha / 2.0),
        _percentile(replicates, 1.0 - alpha / 2.0),
    )


def _mean(values: Sequence[float]) -> float:
    """The arithmetic mean; the default statistic for the paired estimator."""

    return sum(values) / len(values)


def paired_regret_ci(
    pairs_by_project: Mapping[str, list[tuple[float, float]]],
    B: int,
    seed: int,
    alpha: float = 0.05,
) -> Interval:
    """A project-clustered interval for the mean **reduction** in regret, ``baseline - candidate``.

    Each pair is ``(baseline_regret, candidate_regret)`` for one node or trial, and the unit
    that is bootstrapped is their difference, so the interval is on the paired contrast and
    never on either arm alone. The sign convention is stated in the name and repeated here
    because it is the one thing a reader can get backwards: **positive means the candidate
    regretted less**, and protocol §12.1's "confidence interval excluding no improvement" is
    therefore a lower limit above zero.

    Pairing before clustering, rather than bootstrapping the two arms separately and
    subtracting, is what removes the per-node difficulty that both arms share; on this data
    that shared difficulty is the dominant variance component.
    """

    if not pairs_by_project:
        raise ValueError("the paired estimator needs at least one project")
    differences = {
        project_id: [baseline - candidate for baseline, candidate in pairs]
        for project_id, pairs in pairs_by_project.items()
    }
    return hierarchical_bootstrap(differences, _mean, B, seed, alpha)


# --------------------------------------------------------------------------------------
# Selection-valid estimands.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BestFixed:
    """The fixed configuration chosen on the selection split, scored on the evaluation split.

    Both rates are carried, and they are meant to differ: ``selection_rate`` is the number
    that won the argmax and is biased upward by having won it, ``evaluation_rate`` is the
    honest one, and ``evaluation_interval`` is the only interval that may be quoted. Keeping
    the optimistic number visible beside the honest one makes the winner's curse a quantity
    a reviewer can read off rather than a caveat in prose.
    """

    config_id: str
    selection_successes: int
    selection_trials: int
    selection_rate: float
    evaluation_successes: int
    evaluation_trials: int
    evaluation_rate: float
    evaluation_interval: Interval


@dataclass(frozen=True, slots=True)
class Estimands:
    """The three gains, their intervals, and the recovered fraction when it is defined.

    ``intervals`` is keyed by :data:`INTERVAL_KEYS`: four rate intervals (``best_fixed``,
    ``oracle``, ``signal``, ``learned``) and the three gap intervals named after the gains.
    All of them are at the Bonferroni-adjusted level recorded in ``adjusted_alpha``.
    """

    g_out: float
    g_z: float
    g_learn: float
    intervals: Mapping[str, Interval]
    recovered_fraction: float | None
    best_fixed: BestFixed
    adjusted_alpha: float


INTERVAL_KEYS: tuple[str, ...] = (
    "best_fixed",
    "oracle",
    "signal",
    "learned",
    "g_out",
    "g_z",
    "g_learn",
)
"""Every key :func:`estimands` puts in :attr:`Estimands.intervals`, in report order."""


def _split_ids(ids: Iterable[str], role: str) -> list[str]:
    """A sorted, de-duplicated, non-empty list of task ids for one side of the split."""

    unique = sorted(set(ids))
    if not unique:
        raise ValueError(f"the {role} split is empty")
    return unique


def _count(outcomes: Mapping[str, int], task_ids: Sequence[str], config_id: str) -> int:
    """Successes for one configuration over ``task_ids``, refusing partial coverage.

    A configuration that is missing a task cannot be compared with one that has it: the
    argmax would reward the shorter column, and the oracle would silently be an oracle over
    a different task set. Missing coverage raises rather than skipping.
    """

    successes = 0
    for task_id in task_ids:
        if task_id not in outcomes:
            raise ValueError(f"configuration {config_id!r} has no outcome for task {task_id!r}")
        value = outcomes[task_id]
        if value not in (0, 1):
            raise ValueError(
                f"outcome for configuration {config_id!r} on task {task_id!r} must be 0 or 1; "
                f"got {value}"
            )
        successes += value
    return successes


def _difference_interval(better: Interval, worse: Interval) -> Interval:
    """A conservative interval for the difference of two proportions, clipped to [-1, 1].

    ``(lo_a - hi_b, hi_a - lo_b)``: the widest interval consistent with both marginals, and
    so at least the nominal coverage. The paired structure of the data would permit a tighter
    interval — the two arms are measured on the same tasks — but every tighter form assumes
    something about the discordant pairs, and a benchmark that claims superiority is the
    wrong place to spend an assumption to buy a narrower interval.
    """

    lower = max(-1.0, min(1.0, better[0] - worse[1]))
    upper = max(-1.0, min(1.0, better[1] - worse[0]))
    return (lower, upper)


def select_best_fixed(
    configs: Mapping[str, Mapping[str, int]],
    selection_split_ids: Iterable[str],
    evaluation_split_ids: Iterable[str],
    alpha: float = 0.05,
) -> BestFixed:
    """Pick the best fixed configuration on the selection split; score it on the evaluation split.

    ``configs`` maps a configuration id to that configuration's per-task binary outcomes,
    ``{task_id: 0 | 1}``. The argmax runs over the **selection** ids and nothing else — not
    the union, not the evaluation ids — and ties are broken by sorted configuration id so the
    winner does not depend on dict order. Only after the winner is fixed is the evaluation
    split touched, which is what makes :attr:`BestFixed.evaluation_interval` quotable.

    The two splits must be disjoint; an overlap raises. A single shared task is enough to put
    the winner's curse back into the baseline, and the resulting bias shrinks every gap in
    :func:`estimands` in the direction that flatters the router.
    """

    if not configs:
        raise ValueError("there is no configuration to select from")
    selection = _split_ids(selection_split_ids, "selection")
    evaluation = _split_ids(evaluation_split_ids, "evaluation")
    shared = sorted(set(selection) & set(evaluation))
    if shared:
        raise ValueError(
            f"tasks {shared!r} appear in both the selection and evaluation splits; "
            "a configuration chosen on data it is then scored on is chosen invalidly"
        )
    ranked: list[tuple[float, str]] = []
    for config_id in sorted(configs):
        successes = _count(configs[config_id], selection, config_id)
        ranked.append((successes / len(selection), config_id))
    best_rate = max(rate for rate, _ in ranked)
    winner = min(config_id for rate, config_id in ranked if rate == best_rate)
    selection_successes = _count(configs[winner], selection, winner)
    evaluation_successes = _count(configs[winner], evaluation, winner)
    return BestFixed(
        config_id=winner,
        selection_successes=selection_successes,
        selection_trials=len(selection),
        selection_rate=selection_successes / len(selection),
        evaluation_successes=evaluation_successes,
        evaluation_trials=len(evaluation),
        evaluation_rate=evaluation_successes / len(evaluation),
        evaluation_interval=clopper_pearson(evaluation_successes, len(evaluation), alpha),
    )


def _chosen_successes(
    outcomes: Mapping[str, Mapping[str, int]],
    choice: Mapping[str, str],
    task_ids: Sequence[str],
    role: str,
) -> int:
    """Successes of a per-task chooser: for each task, the outcome of the config it picked."""

    successes = 0
    for task_id in task_ids:
        if task_id not in choice:
            raise ValueError(f"the {role} makes no choice for task {task_id!r}")
        config_id = choice[task_id]
        if config_id not in outcomes:
            raise ValueError(
                f"the {role} chose unknown configuration {config_id!r} for task {task_id!r}"
            )
        successes += _count(outcomes[config_id], [task_id], config_id)
    return successes


def estimands(
    outcomes: Mapping[str, Mapping[str, int]],
    router_choice: Mapping[str, str],
    signal_choice: Mapping[str, str],
    selection_split_ids: Iterable[str],
    evaluation_split_ids: Iterable[str],
    alpha: float = 0.05,
    k_configs: int | None = None,
    l_policies: int = 3,
) -> Estimands:
    """The three routing estimands on the evaluation split, against a selection-valid baseline.

    ``outcomes`` is ``{config_id: {task_id: 0 | 1}}``; ``router_choice`` and ``signal_choice``
    are ``{task_id: config_id}`` — what the learned router picked, and what a chooser with
    access only to the routing signal Z would have picked. Every configuration must cover
    every task on both splits, so that the oracle, the baseline and the two choosers are
    scored on the same denominator.

    The multiplicity family is ``k_configs + l_policies``: the K configurations that competed
    for the baseline slot (defaulting to the number supplied) plus the L policies reported
    against it (defaulting to 3 — oracle, signal, learned). Every interval returned is at
    that adjusted level, including the baseline's own, because the baseline was selected by a
    comparison and a family that counts the reports but not the selection is under-corrected.

    ``recovered_fraction`` is ``g_learn / g_out``, and is ``None`` unless the lower limit of
    the ``g_out`` interval is strictly positive. That guard is the point of the field: when
    the opportunity has not been shown to exist, the share of it that was recovered is not a
    small number, it is not a number.
    """

    if not outcomes:
        raise ValueError("there is no configuration to evaluate")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must lie in (0, 1); got {alpha}")
    if l_policies < 1:
        raise ValueError(f"there is at least one reported policy; got l_policies={l_policies}")
    configs = k_configs if k_configs is not None else len(outcomes)
    if configs < 1:
        raise ValueError(f"there is at least one configuration; got k_configs={configs}")
    selection = _split_ids(selection_split_ids, "selection")
    evaluation = _split_ids(evaluation_split_ids, "evaluation")
    adjusted_alpha = bonferroni(alpha, configs + l_policies)

    best = select_best_fixed(outcomes, selection, evaluation, adjusted_alpha)
    trials = len(evaluation)

    oracle_successes = 0
    for task_id in evaluation:
        oracle_successes += max(
            _count(outcomes[config_id], [task_id], config_id) for config_id in sorted(outcomes)
        )
    signal_successes = _chosen_successes(outcomes, signal_choice, evaluation, "signal chooser")
    learned_successes = _chosen_successes(outcomes, router_choice, evaluation, "learned router")

    rates = {
        "best_fixed": best.evaluation_rate,
        "oracle": oracle_successes / trials,
        "signal": signal_successes / trials,
        "learned": learned_successes / trials,
    }
    intervals: dict[str, Interval] = {
        "best_fixed": best.evaluation_interval,
        "oracle": clopper_pearson(oracle_successes, trials, adjusted_alpha),
        "signal": clopper_pearson(signal_successes, trials, adjusted_alpha),
        "learned": clopper_pearson(learned_successes, trials, adjusted_alpha),
    }
    g_out = rates["oracle"] - rates["best_fixed"]
    g_z = rates["signal"] - rates["best_fixed"]
    g_learn = rates["learned"] - rates["best_fixed"]
    for key, arm in (("g_out", "oracle"), ("g_z", "signal"), ("g_learn", "learned")):
        intervals[key] = _difference_interval(intervals[arm], intervals["best_fixed"])

    recovered_fraction = g_learn / g_out if intervals["g_out"][0] > 0.0 else None
    return Estimands(
        g_out=g_out,
        g_z=g_z,
        g_learn=g_learn,
        intervals=MappingProxyType({key: intervals[key] for key in INTERVAL_KEYS}),
        recovered_fraction=recovered_fraction,
        best_fixed=best,
        adjusted_alpha=adjusted_alpha,
    )
