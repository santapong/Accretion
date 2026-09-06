"""The development-only pilot the §21 freeze is sized from.

Protocol §21 lists fifteen pre-registration fields that "require a development-only pilot
before the locked test begins". A pilot is not a small version of the experiment: it is the
measurement of the *variance* the experiment will be sized against, taken on data the locked
test will never quote. This module is that measurement, and nothing else. It computes no
verdict, flips no criterion and quotes no superiority claim — every number it produces is an
input to a sizing decision a human then freezes by hand.

**Why the variance has three levels.** A router benchmark observes one utility per task ×
configuration × trial cell, and the noise in that observation arrives from three places that
a single standard deviation would silently pool:

* *within a project* — one node is harder than its neighbour, which is the variance
  :func:`~accretion.routing.stats.paired_regret_ci` removes by pairing;
* *between projects* — a repository's whole objective, policy set and difficulty, which is
  the variance :func:`~accretion.routing.stats.hierarchical_bootstrap` resamples and which
  :func:`icc` reports as a share;
* *between trials of one cell* — the run-to-run spread of an agent asked the same question
  twice, which is the only one of the three that :func:`~accretion.routing.stats.power_sample_size`
  can be honestly handed, because it is the only one that repeating a run reduces.

Handing the pooled standard deviation to a power calculation is the classic way to size an
experiment far too small, so :func:`trial_sigma` measures the third level on its own and
:func:`power_table` is the only consumer of it.

**Two trials are not a spread.** The shipped corpus records two trials per cell, which gives
one degree of freedom per cell and a σ that is a very noisy estimate of itself. Every report
this module builds therefore *flags* that fact rather than burying it
(:attr:`TrialSigma.flagged`), and the CLI's ``--regenerate --trials 9`` path exists so that
the flag can be seen to clear.

**Base rates and their intervals.** :func:`base_rates` reports the two §8.2 gate quantities
with Clopper–Pearson limits rather than as bare fractions. A verified-success rate of 12/18
and a rate of 1200/1800 are the same fraction and completely different evidence, and a
pre-registration that fixes a floor without knowing which of those it will be measured
against has fixed nothing.

Everything here is pure in the sense :mod:`accretion.routing.stats` uses: no store, no clock,
no network, and no floating-point path a seed does not pin. :func:`pilot_report` reads an
already-loaded corpus through :class:`~accretion.router_benchmark.RouterBenchmarkRunner` and
returns a document; deciding where that corpus came from, and where the document goes, is the
CLI's job (``scripts/router_pilot.py``).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Literal, Self

from pydantic import Field, model_validator

from accretion.contracts import StrictModel
from accretion.router_benchmark import (
    BenchmarkRow,
    BenchmarkSplit,
    RouterBenchmarkRunner,
)
from accretion.routing.regret import Outcome, outcome_key, utility
from accretion.routing.stats import (
    Interval,
    clopper_pearson,
    pass_at_k,
    pass_pow_k,
    power_sample_size,
)

PILOT_DELTAS: tuple[float, ...] = (0.02, 0.01)
"""The two minimum detectable differences §21 item 4 has to choose between.

Two percentage points is the effect the promotion gate already registers as ``delta_min``
(``evals/router/promotion.v1.json``); one point is the next halving, and it is in the table
because the quadratic in ``1/delta`` is invisible until two rows of it sit side by side.
"""

PASS_ATTEMPTS = 2
"""``k`` for both repeated-sampling measures, fixed rather than taken from the corpus.

The shipped corpus records two trials per cell and the regeneration path records nine; if
``k`` followed the trial count, the two runs would report ``pass@k`` for different ``k`` and
the comparison the regeneration exists to make — does the σ change? — would be confounded by
a moving measure. Two is also the smallest ``k`` at which ``pass@k`` and ``pass^k`` differ,
which is the whole reason both are reported.
"""

FLAGGED_TRIALS_PER_CELL = 2
"""At or below this many trials per cell, a trial σ is flagged as resting on ~1 df per cell."""

_PLACES = 9
"""Rounding for every emitted statistic: the precedent :mod:`accretion.routing.regret` sets."""


# --------------------------------------------------------------------------------------
# The value objects.
# --------------------------------------------------------------------------------------


class VarianceComponents(StrictModel):
    """A one-way random-effects decomposition of one quantity into two levels.

    ``within`` is the residual mean square — the spread of nodes inside a project — and
    ``between`` is the project-level component recovered from the two mean squares as
    ``(MSB - MSW) / n0``. It is **clamped at zero**, because the moment estimator is allowed
    to go negative when the projects are more alike than chance and a negative variance is
    not a quantity anyone can size an experiment from. A clamped zero is reported as zero and
    said so here rather than hidden: it means the data show no project-level variance, which
    is a finding about the corpus and not a defect in the arithmetic.

    ``n0`` is the effective group size for unbalanced groups; with equal group sizes it is
    that size exactly.
    """

    schema_version: Literal["1.0"] = "1.0"
    projects: int = Field(ge=2)
    units: int = Field(ge=3)
    grand_mean: float
    mean_square_within: float = Field(ge=0)
    mean_square_between: float = Field(ge=0)
    effective_group_size: float = Field(gt=0)
    within: float = Field(ge=0)
    between: float = Field(ge=0)


class TrialSigma(StrictModel):
    """The pooled within-cell standard deviation, and how much evidence stands behind it.

    ``sigma`` is ``sqrt(SS_within / df)`` pooled over every cell, which is the trial-to-trial
    noise of one configuration asked one task twice or more. ``degrees_of_freedom`` is
    ``sum(n_cell - 1)`` and is carried beside it because a σ from 216 cells of two trials and
    a σ from 24 cells of nine trials have the same units and very different standing.

    ``flagged`` is true when the thinnest cell has at most
    :data:`FLAGGED_TRIALS_PER_CELL` trials. It is a field and not a log line: the pilot's
    whole job is to tell a reader whether the σ they are about to freeze a sample size
    against is worth freezing against.
    """

    schema_version: Literal["1.0"] = "1.0"
    cells: int = Field(ge=1)
    trials: int = Field(ge=2)
    degrees_of_freedom: int = Field(ge=1)
    minimum_trials_per_cell: int = Field(ge=2)
    maximum_trials_per_cell: int = Field(ge=2)
    cells_at_minimum: int = Field(ge=1)
    sigma: float = Field(ge=0)
    flagged: bool

    @model_validator(mode="after")
    def _the_flag_follows_the_thinnest_cell(self) -> Self:
        if self.minimum_trials_per_cell > self.maximum_trials_per_cell:
            raise ValueError(
                "the thinnest cell cannot hold more trials than the fullest one; the cell "
                "census disagrees with itself"
            )
        if self.flagged != (self.minimum_trials_per_cell <= FLAGGED_TRIALS_PER_CELL):
            raise ValueError(
                f"flagged={self.flagged} contradicts a thinnest cell of "
                f"{self.minimum_trials_per_cell} trials against a threshold of "
                f"{FLAGGED_TRIALS_PER_CELL}; the flag is derived, not chosen"
            )
        return self


class BaseRates(StrictModel):
    """The two §8.2 gate quantities as rates with exact limits, not as bare fractions.

    ``false_acceptance_upper`` is the upper limit of the two-sided ``1 - alpha``
    Clopper–Pearson interval, so it is a *conservative* one-sided bound at ``1 - alpha/2``.
    Deliberately the conservative reading: a ceiling is the direction in which being wrong is
    expensive, and a pre-registration that sized its ceiling from a one-sided limit computed
    at the nominal level would be choosing the tighter of two defensible numbers after seeing
    which one cleared.
    """

    schema_version: Literal["1.0"] = "1.0"
    alpha: float = Field(gt=0, lt=1)
    trials: int = Field(ge=0)
    verified_successes: int = Field(ge=0)
    verified_success_rate: float = Field(ge=0, le=1)
    verified_success_interval: Interval
    false_acceptances: int = Field(ge=0)
    false_acceptance_rate: float = Field(ge=0, le=1)
    false_acceptance_upper: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _counts_fit_inside_the_trials(self) -> Self:
        if self.verified_successes > self.trials or self.false_acceptances > self.trials:
            raise ValueError(
                f"{self.verified_successes} verified and {self.false_acceptances} falsely "
                f"accepted out of {self.trials} selections; a count cannot exceed its "
                "denominator"
            )
        return self


class PowerRow(StrictModel):
    """One line of the sizing table: an effect, and the runs per arm that would detect it."""

    schema_version: Literal["1.0"] = "1.0"
    delta: float = Field(gt=0)
    runs_per_configuration: int = Field(ge=1)


class PowerTable(StrictModel):
    """:func:`~accretion.routing.stats.power_sample_size` evaluated at every pilot effect.

    The σ it was computed from is carried in the table rather than left at the call site,
    because a sample size quoted without its σ is a number nobody can check and the single
    most common way a sizing survives a change of measurement scale it should not have.
    """

    schema_version: Literal["1.0"] = "1.0"
    sigma: float = Field(gt=0)
    alpha: float = Field(gt=0, lt=1)
    power: float = Field(gt=0, lt=1)
    rows: list[PowerRow] = Field(min_length=1)


class PolicyPilot(StrictModel):
    """One comparator's pilot line, or the reason it has none.

    Unavailable §8.1 methods keep their row with every measurement ``None``, for the reason
    :class:`~accretion.router_benchmark.PolicyResult` gives: §8.2 requires all baselines to
    stay in the report, and a pilot that dropped the unbuilt ones would size the locked test
    against a smaller field than the test will actually run.
    """

    schema_version: Literal["1.0"] = "1.0"
    policy_id: str = Field(min_length=1, max_length=64)
    available: bool
    reason_code: str | None = None
    selections: int = Field(ge=0)
    mean_regret: float | None = None
    regret_variance: VarianceComponents | None = None
    regret_icc: float | None = None
    rates: BaseRates | None = None
    outage_selections: int = Field(ge=0)
    outage_rate: float = Field(ge=0, le=1)
    per_trial_pass_rate: float | None = Field(default=None, ge=0, le=1)
    mean_pass_at_k: float | None = Field(default=None, ge=0, le=1)
    pass_pow_k: float | None = Field(default=None, ge=0, le=1)
    pass_attempts: int = Field(default=PASS_ATTEMPTS, ge=1)

    @model_validator(mode="after")
    def _an_unavailable_policy_measured_nothing(self) -> Self:
        measurements = (
            self.mean_regret,
            self.regret_variance,
            self.regret_icc,
            self.rates,
            self.per_trial_pass_rate,
            self.mean_pass_at_k,
            self.pass_pow_k,
        )
        if not self.available and any(value is not None for value in measurements):
            raise ValueError(
                f"policy {self.policy_id!r} is unavailable but carries measurements; a "
                "method that never selected anything has nothing to report"
            )
        if not self.available and self.selections:
            raise ValueError(
                f"policy {self.policy_id!r} is unavailable but claims {self.selections} "
                "selections"
            )
        if self.rates is not None and self.rates.trials + self.outage_selections != self.selections:
            raise ValueError(
                f"policy {self.policy_id!r} rates {self.rates.trials} selections and reports "
                f"{self.outage_selections} outages against {self.selections} total; the base "
                "rates and the gate report would be computed on different denominators"
            )
        return self


class PilotReport(StrictModel):
    """One pilot run: the corpus it read, the split it measured, and every comparator's line.

    Named by the corpus digests exactly as a benchmark run is, so a pilot and the benchmark
    result it sizes can be shown to have read the same bytes. The two power tables are the
    document's point: one on the utility scale the objective is expressed in, one on the
    verified-success scale the §8.2 gates are expressed in, and §21 item 4 has to be frozen
    against whichever of the two the primary endpoint actually lives on.
    """

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1, max_length=64)
    suite_version: str = Field(min_length=1, max_length=64)
    configuration_version: str = Field(min_length=1, max_length=64)
    corpus_sha256: str = Field(min_length=64, max_length=64)
    trace_sha256: str = Field(min_length=64, max_length=64)
    seed: int
    split: BenchmarkSplit
    alpha: float = Field(gt=0, lt=1)
    projects: int = Field(ge=1)
    tasks: int = Field(ge=1)
    candidates: int = Field(ge=1)
    utility_sigma: TrialSigma
    pass_rate_sigma: TrialSigma
    utility_power: PowerTable
    pass_rate_power: PowerTable
    policies: list[PolicyPilot] = Field(min_length=1)

    def policy(self, policy_id: str) -> PolicyPilot:
        """One comparator's pilot line, by protocol id."""

        for line in self.policies:
            if line.policy_id == policy_id:
                return line
        raise KeyError(policy_id)


# --------------------------------------------------------------------------------------
# The statistics.
# --------------------------------------------------------------------------------------


def within_between_variance(
    pairs_by_project: Mapping[str, Sequence[float]],
) -> VarianceComponents:
    """Split one quantity's variance into a within-project and a between-project part.

    ``pairs_by_project`` is the shape
    :meth:`~accretion.routing.regret.RegretReport.pairs_by_project` returns: a project id
    mapped to that project's per-node values, in row order. The decomposition is the standard
    one-way random-effects moment estimator — ``MSW`` from the residual sum of squares and the
    project component from ``(MSB - MSW) / n0`` with Satterthwaite's effective group size, so
    that unequal project sizes do not quietly reweight the answer.

    At least two projects and at least one within-project degree of freedom are required, and
    both are refusals rather than defaults: a single project has no between-project variance
    to estimate and one node per project has no within-project variance to estimate, and
    returning zero for either would be indistinguishable from having measured a zero.
    """

    if not pairs_by_project:
        raise ValueError("the variance decomposition needs at least one project")
    empty = sorted(project_id for project_id, values in pairs_by_project.items() if not values)
    if empty:
        raise ValueError(f"projects {empty!r} carry no values to decompose")
    groups = sorted(pairs_by_project)
    sizes = {project_id: len(pairs_by_project[project_id]) for project_id in groups}
    k = len(groups)
    if k < 2:
        raise ValueError(
            "the between-project component needs at least two projects; one project has no "
            "between-project variance to estimate"
        )
    total = sum(sizes.values())
    if total <= k:
        raise ValueError(
            f"{total} values across {k} projects leaves no within-project degrees of freedom; "
            "at least one project must carry more than one value"
        )
    grand = sum(sum(pairs_by_project[project_id]) for project_id in groups) / total
    within_sum = 0.0
    between_sum = 0.0
    for project_id in groups:
        values = pairs_by_project[project_id]
        mean = sum(values) / len(values)
        within_sum += sum((value - mean) ** 2 for value in values)
        between_sum += len(values) * (mean - grand) ** 2
    mean_square_within = within_sum / (total - k)
    mean_square_between = between_sum / (k - 1)
    effective = (total - sum(size**2 for size in sizes.values()) / total) / (k - 1)
    between = max(0.0, (mean_square_between - mean_square_within) / effective)
    return VarianceComponents(
        projects=k,
        units=total,
        grand_mean=round(grand, _PLACES),
        mean_square_within=round(mean_square_within, _PLACES),
        mean_square_between=round(mean_square_between, _PLACES),
        effective_group_size=round(effective, _PLACES),
        within=round(mean_square_within, _PLACES),
        between=round(between, _PLACES),
    )


def icc(components: VarianceComponents) -> float:
    """The intraclass correlation ``between / (between + within)``.

    The share of the total variance that lives *between* projects, and therefore the number
    that says how much a project-clustered interval will be widened relative to one that
    treated nodes as independent. Takes the decomposition rather than the raw groups so that
    a caller reporting both cannot accidentally report an ICC computed from a second pass
    over the data.

    Zero total variance returns ``0.0``: every value was identical, so no share of the
    variance is shared because there is no variance to share. That is a defensible zero and
    not a hidden division, which is why it is stated here.
    """

    total = components.between + components.within
    if total <= 0.0:
        return 0.0
    return round(components.between / total, _PLACES)


def trial_sigma(cells: Mapping[str, Sequence[float]]) -> TrialSigma:
    """The pooled within-cell standard deviation of a repeated measurement.

    ``cells`` maps a cell label — one task × configuration pair — to that cell's per-trial
    values. Pooling across cells rather than averaging per-cell standard deviations is what
    makes the estimate usable at two trials per cell: each cell contributes its one degree of
    freedom to a common pool instead of contributing a per-cell σ that is itself almost pure
    noise.

    Every cell needs at least two trials. A one-trial cell is a cell with no spread to
    measure, and silently skipping it would let a corpus shrink σ by adding cells that carry
    no information about it.
    """

    if not cells:
        raise ValueError("the trial standard deviation needs at least one cell")
    thin = sorted(label for label, values in cells.items() if len(values) < 2)
    if thin:
        raise ValueError(
            f"cells {thin!r} record fewer than two trials; a single trial has no "
            "trial-to-trial spread to pool"
        )
    sizes = [len(values) for values in cells.values()]
    residual = 0.0
    for label in sorted(cells):
        values = cells[label]
        mean = sum(values) / len(values)
        residual += sum((value - mean) ** 2 for value in values)
    degrees = sum(size - 1 for size in sizes)
    minimum = min(sizes)
    return TrialSigma(
        cells=len(cells),
        trials=sum(sizes),
        degrees_of_freedom=degrees,
        minimum_trials_per_cell=minimum,
        maximum_trials_per_cell=max(sizes),
        cells_at_minimum=sum(1 for size in sizes if size == minimum),
        sigma=round(math.sqrt(residual / degrees), _PLACES),
        flagged=minimum <= FLAGGED_TRIALS_PER_CELL,
    )


def base_rates(outcomes: Sequence[Outcome], alpha: float = 0.05) -> BaseRates:
    """The verified-success and false-acceptance rates with exact Clopper–Pearson limits.

    The counts are read off the recorded verdicts and nothing else — not one utility weight
    appears here, for the reason :meth:`~accretion.router_benchmark.RouterBenchmarkRunner._gates`
    gives: a safety quantity that shared an expression with the objective would move when the
    objective was re-weighted.

    An empty sequence is a refusal. Clopper–Pearson answers ``[0, 1]`` for ``n = 0``, which is
    correct and useless as a pre-registration input, and a pilot that reported it as though it
    were a measurement would be reporting the absence of data as data.
    """

    if not outcomes:
        raise ValueError("base rates need at least one selection to be rates of")
    trials = len(outcomes)
    verified = sum(1 for outcome in outcomes if outcome.verified)
    false_accepts = sum(1 for outcome in outcomes if outcome.false_accept)
    interval = clopper_pearson(verified, trials, alpha)
    return BaseRates(
        alpha=alpha,
        trials=trials,
        verified_successes=verified,
        verified_success_rate=round(verified / trials, _PLACES),
        verified_success_interval=(round(interval[0], _PLACES), round(interval[1], _PLACES)),
        false_acceptances=false_accepts,
        false_acceptance_rate=round(false_accepts / trials, _PLACES),
        false_acceptance_upper=round(clopper_pearson(false_accepts, trials, alpha)[1], _PLACES),
    )


def power_table(sigma: float, alpha: float = 0.05, power: float = 0.8) -> PowerTable:
    """:data:`PILOT_DELTAS` sized at one measured σ, in descending effect order.

    A table and not a single number because §21 item 1 and item 4 are one decision taken
    twice: the effect you are willing to call meaningful and the number of runs you are
    willing to pay for are the same choice seen from either end, and the quadratic between
    them is only legible as a table.
    """

    return PowerTable(
        sigma=sigma,
        alpha=alpha,
        power=power,
        rows=[
            PowerRow(
                delta=delta,
                runs_per_configuration=power_sample_size(delta, sigma, alpha, power),
            )
            for delta in sorted(PILOT_DELTAS, reverse=True)
        ],
    )


# --------------------------------------------------------------------------------------
# Assembling one pilot over a corpus.
# --------------------------------------------------------------------------------------


def _cell_values(
    runner: RouterBenchmarkRunner, task_ids: frozenset[str]
) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
    """Per-trial utility and per-trial pass, keyed by cell, over the reported tasks.

    Cells the configuration refused are **excluded**. A refusal records the same fabricated
    row on every trial, so its within-cell spread is structurally zero, and pooling those
    zeros into σ would shrink the sample size the pilot recommends by making the measurement
    look more repeatable than the measurement is. The exclusion is the reason ``cells`` in
    the returned :class:`TrialSigma` is smaller than the full grid.
    """

    weights = runner.weights
    budget = runner.corpus.config.latency_budget_ms
    utilities: dict[str, list[float]] = {}
    passes: dict[str, list[float]] = {}
    for trace in runner.corpus.traces:
        if trace.task_id not in task_ids or trace.invalid:
            continue
        label = outcome_key(trace.task_id, trace.candidate_id)
        observed = Outcome(
            quality=trace.quality,
            cost=trace.cost,
            latency=min(1.0, trace.latency_ms / budget),
            latency_ms=trace.latency_ms,
            verified=trace.verified,
            false_accept=trace.false_accept,
            invalid=trace.invalid,
        )
        utilities.setdefault(label, []).append(utility(observed, weights))
        passes.setdefault(label, []).append(1.0 if trace.verified else 0.0)
    return utilities, passes


def _trials_by_cell(runner: RouterBenchmarkRunner) -> dict[str, list[bool]]:
    """``{cell label: [verified per trial]}`` over the whole grid, refusals included.

    The repeated-sampling measures need every cell a policy might select, including the ones
    it should not have: an invalid selection whose trials all failed is exactly the case
    ``pass^k`` is meant to price, and dropping it would let a policy improve its ``pass@k`` by
    choosing something ineligible.
    """

    grid: dict[str, list[bool]] = {}
    for trace in runner.corpus.traces:
        grid.setdefault(outcome_key(trace.task_id, trace.candidate_id), []).append(trace.verified)
    return grid


def _repeated_sampling(
    rows: Sequence[BenchmarkRow], grid: Mapping[str, list[bool]]
) -> tuple[float, float, float]:
    """``(per-trial pass rate, mean pass@k, pass^k)`` for one policy's selections."""

    trials = 0
    successes = 0
    at_k: list[float] = []
    for row in rows:
        cell = grid.get(outcome_key(row.task_id, row.selected_candidate_id))
        if not cell:
            continue
        passed = sum(1 for verified in cell if verified)
        trials += len(cell)
        successes += passed
        at_k.append(pass_at_k(len(cell), passed, min(PASS_ATTEMPTS, len(cell))))
    if not trials or not at_k:
        return 0.0, 0.0, 0.0
    rate = successes / trials
    return (
        round(rate, _PLACES),
        round(sum(at_k) / len(at_k), _PLACES),
        round(pass_pow_k(rate, PASS_ATTEMPTS), _PLACES),
    )


def pilot_report(
    runner: RouterBenchmarkRunner,
    policy_ids: Sequence[str],
    *,
    split: BenchmarkSplit = BenchmarkSplit.EVALUATION,
    alpha: float = 0.05,
) -> PilotReport:
    """Measure every §21 sizing input this corpus can supply, for one side of the split.

    Runs ``policy_ids`` through :meth:`~accretion.router_benchmark.RouterBenchmarkRunner.run`
    and turns each comparator's rows into a pilot line. Nothing is executed and nothing is
    written: the runner refuses any execution source but ``REPLAY``, and this function has no
    handle on a store.

    Every quantity is measured on the tasks of ``split`` and only those, including the two
    trial standard deviations. That costs a little precision — half the cells go unused — and
    buys the property that makes the report readable: every number in it is a statement about
    one task set, so a σ quoted beside a base rate quoted beside a mean regret are three
    facts about the same population rather than three facts about two.
    """

    result = runner.run(policy_ids, split=split)
    reported = frozenset(result.reported_task_ids)
    outcomes = runner.corpus.outcomes()
    utilities, passes = _cell_values(runner, reported)
    grid = _trials_by_cell(runner)
    utility_sigma = trial_sigma(utilities)
    pass_rate_sigma = trial_sigma(passes)

    lines: list[PolicyPilot] = []
    for policy in result.policies:
        if not policy.available or policy.regret is None:
            lines.append(
                PolicyPilot(
                    policy_id=policy.policy_id,
                    available=False,
                    reason_code=policy.reason_code,
                    selections=0,
                    outage_selections=0,
                    outage_rate=0.0,
                )
            )
            continue
        selected = [
            outcomes[outcome_key(row.task_id, row.selected_candidate_id)]
            for row in policy.rows
            if outcome_key(row.task_id, row.selected_candidate_id) in outcomes
        ]
        outages = len(policy.rows) - len(selected)
        components = within_between_variance(policy.regret.pairs_by_project())
        rate, mean_at_k, pow_k = _repeated_sampling(policy.rows, grid)
        lines.append(
            PolicyPilot(
                policy_id=policy.policy_id,
                available=True,
                reason_code=None,
                selections=len(policy.rows),
                mean_regret=policy.regret.mean_regret,
                regret_variance=components,
                regret_icc=icc(components),
                rates=base_rates(selected, alpha) if selected else None,
                outage_selections=outages,
                outage_rate=round(outages / len(policy.rows), _PLACES) if policy.rows else 0.0,
                per_trial_pass_rate=rate,
                mean_pass_at_k=mean_at_k,
                pass_pow_k=pow_k,
            )
        )

    tasks = [task for task in runner.corpus.tasks if task.task_id in reported]
    return PilotReport(
        run_id=result.run_id,
        suite_version=result.suite_version,
        configuration_version=result.configuration_version,
        corpus_sha256=result.corpus_sha256,
        trace_sha256=result.trace_sha256,
        seed=runner.corpus.config.seed,
        split=split,
        alpha=alpha,
        projects=len({task.project_id for task in tasks}),
        tasks=len(tasks),
        candidates=len(runner.corpus.candidates),
        utility_sigma=utility_sigma,
        pass_rate_sigma=pass_rate_sigma,
        utility_power=power_table(utility_sigma.sigma, alpha),
        pass_rate_power=power_table(pass_rate_sigma.sigma, alpha),
        policies=lines,
    )


__all__ = [
    "FLAGGED_TRIALS_PER_CELL",
    "PASS_ATTEMPTS",
    "PILOT_DELTAS",
    "BaseRates",
    "PilotReport",
    "PolicyPilot",
    "PowerRow",
    "PowerTable",
    "TrialSigma",
    "VarianceComponents",
    "base_rates",
    "icc",
    "pilot_report",
    "power_table",
    "trial_sigma",
    "within_between_variance",
]
