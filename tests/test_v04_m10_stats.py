"""The router benchmark's statistics: exact intervals, selection validity, multiplicity, power.

These tests are the reason the numbers in a benchmark report may be believed, so each one is
pinned to something outside the implementation rather than to the implementation's own
output. The interval tests use published Clopper–Pearson table values and closed-form beta
points; the power test reproduces the run-count table implied by the ≈1.5 pp run-to-run
spread in 2602.07150; the selection test is arranged so that the *wrong* answer is a
different configuration id, not a slightly different float.

Three mutations are what this file exists to kill, and each is named on the test that kills
it: selecting the best fixed configuration on the union of the splits instead of the
selection split alone, dropping the Bonferroni divisor, and resampling units without first
resampling their projects. All three leave the code looking correct and every number
plausible — they are the failures a reviewer cannot see by reading.

Everything here is pure arithmetic: no store, no clock, no I/O. The bootstrap tests pass an
explicit seed and assert exact equality across two runs with that seed, which is a stronger
claim than a tolerance would be and the only one worth making about a pseudo-random routine.
"""

from __future__ import annotations

import pytest

from accretion.routing.stats import (
    INTERVAL_KEYS,
    Interval,
    beta_quantile,
    bonferroni,
    clopper_pearson,
    estimands,
    hierarchical_bootstrap,
    normal_quantile,
    paired_regret_ci,
    pass_at_k,
    pass_pow_k,
    power_sample_size,
    regularised_incomplete_beta,
    select_best_fixed,
)

Benchmark = tuple[
    dict[str, dict[str, int]],  # outcomes: config -> task -> 0/1
    dict[str, str],  # router choice: task -> config
    dict[str, str],  # signal choice: task -> config
    list[str],  # selection split ids
    list[str],  # evaluation split ids
]


def build_complementary_benchmark() -> Benchmark:
    """Four configurations over 20 selection and 40 evaluation tasks, with a real opportunity.

    ``cfg_a`` handles the even tasks and ``cfg_b`` the odd ones, so the per-task oracle is
    perfect while no single configuration beats a coin — the shape that makes routing worth
    doing at all. ``cfg_c`` and ``cfg_d`` are weaker and exist so that K is larger than the
    two configurations the story needs. On the selection split ``cfg_a`` is given two extra
    successes so the baseline is a clear winner rather than a tie broken by id order.

    The learned router misroutes six of the forty evaluation tasks and the signal-restricted
    chooser misroutes ten, which puts ``g_learn`` strictly between ``g_z`` and ``g_out``.
    """

    selection_ids = [f"sel_{index:02d}" for index in range(20)]
    evaluation_ids = [f"evl_{index:02d}" for index in range(40)]
    outcomes: dict[str, dict[str, int]] = {
        "cfg_a": {},
        "cfg_b": {},
        "cfg_c": {},
        "cfg_d": {},
    }
    for index, task_id in enumerate(selection_ids):
        even = index % 2 == 0
        outcomes["cfg_a"][task_id] = 1 if even or index in (1, 3) else 0
        outcomes["cfg_b"][task_id] = 0 if even else 1
        outcomes["cfg_c"][task_id] = 1 if index % 4 == 0 else 0
        outcomes["cfg_d"][task_id] = 1 if index % 3 == 0 else 0
    for index, task_id in enumerate(evaluation_ids):
        even = index % 2 == 0
        outcomes["cfg_a"][task_id] = 1 if even else 0
        outcomes["cfg_b"][task_id] = 0 if even else 1
        outcomes["cfg_c"][task_id] = 1 if index % 4 == 0 else 0
        outcomes["cfg_d"][task_id] = 1 if index % 3 == 0 else 0
    router_choice: dict[str, str] = {}
    signal_choice: dict[str, str] = {}
    for index, task_id in enumerate(evaluation_ids):
        correct = "cfg_a" if index % 2 == 0 else "cfg_b"
        wrong = "cfg_b" if index % 2 == 0 else "cfg_a"
        router_choice[task_id] = wrong if index % 7 == 3 else correct
        signal_choice[task_id] = wrong if index % 4 == 1 else correct
    return outcomes, router_choice, signal_choice, selection_ids, evaluation_ids


def build_narrow_benchmark() -> Benchmark:
    """Twelve evaluation tasks and a small oracle advantage: an opportunity too weak to claim.

    ``cfg_a`` and ``cfg_b`` agree on ten of the twelve evaluation tasks and each rescues one
    the other misses, so the oracle beats the baseline by a single task in twelve. That is an
    8.3 pp point estimate on a sample where an exact interval is nearly half the unit interval
    wide, which is exactly the situation ``recovered_fraction`` must refuse.
    """

    selection_ids = [f"sel_{index:02d}" for index in range(12)]
    evaluation_ids = [f"evl_{index:02d}" for index in range(12)]
    outcomes: dict[str, dict[str, int]] = {"cfg_a": {}, "cfg_b": {}}
    for task_id in selection_ids:
        outcomes["cfg_a"][task_id] = 1
        outcomes["cfg_b"][task_id] = 0
    for index, task_id in enumerate(evaluation_ids):
        shared = 1 if index < 7 else 0
        outcomes["cfg_a"][task_id] = 1 if index == 7 else shared
        outcomes["cfg_b"][task_id] = 1 if index == 8 else shared
    router_choice = {task_id: "cfg_a" for task_id in evaluation_ids}
    signal_choice = {task_id: "cfg_a" for task_id in evaluation_ids}
    return outcomes, router_choice, signal_choice, selection_ids, evaluation_ids


def build_split_benchmark() -> tuple[dict[str, dict[str, int]], list[str], list[str]]:
    """Two configurations whose ranking on the selection split reverses on the evaluation split.

    ``cfg_steady`` wins the selection split 8–5 and loses the evaluation split 4–9. The
    numbers are chosen so the union ranks them the other way round too (12/20 against 14/20),
    which is what makes "select on the union" a distinguishable mistake rather than a
    difference of opinion about a tie.
    """

    selection_ids = [f"sel_{index:02d}" for index in range(10)]
    evaluation_ids = [f"evl_{index:02d}" for index in range(10)]
    outcomes: dict[str, dict[str, int]] = {"cfg_steady": {}, "cfg_late": {}}
    for index, task_id in enumerate(selection_ids):
        outcomes["cfg_steady"][task_id] = 1 if index < 8 else 0
        outcomes["cfg_late"][task_id] = 1 if index < 5 else 0
    for index, task_id in enumerate(evaluation_ids):
        outcomes["cfg_steady"][task_id] = 1 if index < 4 else 0
        outcomes["cfg_late"][task_id] = 1 if index < 9 else 0
    return outcomes, selection_ids, evaluation_ids


def width(interval: Interval) -> float:
    """The length of an interval, which is what the bootstrap tests actually compare."""

    return interval[1] - interval[0]


def mean(values: list[float]) -> float:
    """The bootstrap statistic used throughout; defined here so the tests pass a real callable."""

    return sum(values) / len(values)


def test_clopper_pearson_matches_tabulated_values() -> None:
    lower, upper = clopper_pearson(3, 10, 0.05)
    assert lower == pytest.approx(0.0667, abs=1e-3)
    assert upper == pytest.approx(0.6525, abs=1e-3)

    # Zero successes: the lower limit is exactly zero and the upper limit is the
    # closed-form 1 - (alpha/2)^(1/n), the classic "rule of three"-style bound.
    empty_lower, empty_upper = clopper_pearson(0, 20, 0.05)
    assert empty_lower == 0.0
    assert empty_upper == pytest.approx(0.1684, abs=1e-3)
    assert empty_upper == pytest.approx(1.0 - 0.025 ** (1 / 20), abs=1e-9)

    # A sample that never failed cannot exclude 1, and one that never succeeded cannot
    # exclude 0; both ends are returned exactly rather than as a quantile near the boundary.
    assert clopper_pearson(20, 20, 0.05)[1] == 1.0
    assert clopper_pearson(20, 20, 0.05)[0] == pytest.approx(0.8316, abs=1e-3)
    assert clopper_pearson(0, 20, 0.05)[0] == 0.0

    # Conservative means wider: a smaller alpha may never produce a narrower interval.
    assert width(clopper_pearson(3, 10, 0.01)) > width(clopper_pearson(3, 10, 0.05))

    # The interval always brackets the point estimate.
    tight_lower, tight_upper = clopper_pearson(300, 1000, 0.05)
    assert tight_lower < 0.3 < tight_upper
    assert width((tight_lower, tight_upper)) < width((lower, upper))

    with pytest.raises(ValueError, match="successes must lie"):
        clopper_pearson(11, 10, 0.05)
    with pytest.raises(ValueError, match="alpha must lie"):
        clopper_pearson(3, 10, 0.0)


def test_regularised_incomplete_beta_matches_known_points() -> None:
    # I_0.5(2, 3) is the binomial tail (6 + 4 + 1)/16 = 11/16, exactly.
    assert regularised_incomplete_beta(0.5, 2, 3) == pytest.approx(0.6875, abs=1e-12)
    # Beta(1, 1) is the uniform distribution, so I_x(1, 1) = x.
    assert regularised_incomplete_beta(0.2, 1, 1) == pytest.approx(0.2, abs=1e-12)
    assert regularised_incomplete_beta(0.0, 2, 3) == 0.0
    assert regularised_incomplete_beta(1.0, 2, 3) == 1.0

    # Non-integer parameters must work: Clopper-Pearson only happens to use integers, and a
    # gamma-free implementation that assumed them would pass every other assertion here.
    assert regularised_incomplete_beta(0.5, 2.5, 2.5) == pytest.approx(0.5, abs=1e-12)
    assert regularised_incomplete_beta(0.5, 0.5, 0.5) == pytest.approx(0.5, abs=1e-12)
    # I_x(a, b) = 1 - I_{1-x}(b, a) is the symmetry the two convergence branches share.
    assert regularised_incomplete_beta(0.13, 3.7, 1.2) == pytest.approx(
        1.0 - regularised_incomplete_beta(0.87, 1.2, 3.7), abs=1e-12
    )

    # The quantile is the inverse, at non-integer parameters too.
    for probability in (0.025, 0.5, 0.975):
        point = beta_quantile(probability, 2.5, 6.25)
        assert regularised_incomplete_beta(point, 2.5, 6.25) == pytest.approx(probability, abs=1e-9)

    with pytest.raises(ValueError, match="beta parameters must be positive"):
        regularised_incomplete_beta(0.5, 0.0, 1.0)


def test_power_sample_size_reproduces_the_randomness_paper_table() -> None:
    # sigma = 1.5 pp run-to-run spread (2602.07150): 2 pp needs ~9 runs per arm, 1 pp ~36.
    assert abs(power_sample_size(0.02, 0.015) - 9) <= 1
    assert abs(power_sample_size(0.01, 0.015) - 36) <= 1

    # The quadratic in 1/delta is the budgeting fact; halving the effect roughly quadruples n.
    assert power_sample_size(0.005, 0.015) == pytest.approx(
        4 * power_sample_size(0.01, 0.015), rel=0.05
    )
    # More power and a stricter alpha both cost runs; neither may ever save any.
    assert power_sample_size(0.02, 0.015, power=0.9) > power_sample_size(0.02, 0.015)
    assert power_sample_size(0.02, 0.015, alpha=0.01) > power_sample_size(0.02, 0.015)

    # The two quantiles the formula rests on, to the digits the tables print.
    assert normal_quantile(0.975) == pytest.approx(1.959964, abs=1e-6)
    assert normal_quantile(0.8) == pytest.approx(0.8416212, abs=1e-6)
    assert normal_quantile(0.5) == pytest.approx(0.0, abs=1e-12)
    assert normal_quantile(0.001) == pytest.approx(-3.090232, abs=1e-6)
    assert normal_quantile(0.999) == pytest.approx(3.090232, abs=1e-6)

    with pytest.raises(ValueError, match="detectable difference must be positive"):
        power_sample_size(0.0, 0.015)
    with pytest.raises(ValueError, match="normal quantile"):
        normal_quantile(1.0)


def test_best_fixed_is_chosen_on_the_selection_split_only() -> None:
    outcomes, selection_ids, evaluation_ids = build_split_benchmark()

    best = select_best_fixed(outcomes, selection_ids, evaluation_ids)

    # cfg_late is the winner on the evaluation split AND on the union of the two splits, so
    # a selector that peeked at either would have returned it. It did not.
    union = selection_ids + evaluation_ids
    union_rates = {
        config_id: sum(outcomes[config_id][task_id] for task_id in union) / len(union)
        for config_id in outcomes
    }
    evaluation_rates = {
        config_id: sum(outcomes[config_id][task_id] for task_id in evaluation_ids)
        / len(evaluation_ids)
        for config_id in outcomes
    }
    assert union_rates["cfg_late"] > union_rates["cfg_steady"]
    assert evaluation_rates["cfg_late"] > evaluation_rates["cfg_steady"]
    assert best.config_id == "cfg_steady"

    # The winner's curse is visible: it won at 0.8 and is honestly worth 0.4, and only the
    # honest number carries an interval.
    assert best.selection_rate == pytest.approx(0.8)
    assert best.selection_successes == 8
    assert best.evaluation_rate == pytest.approx(0.4)
    assert best.evaluation_successes == 4
    assert best.evaluation_trials == 10
    assert best.evaluation_interval[0] < 0.4 < best.evaluation_interval[1]
    assert best.evaluation_interval == clopper_pearson(4, 10, 0.05)

    # A split that overlaps is refused rather than silently rescored.
    with pytest.raises(ValueError, match="appear in both the selection and evaluation splits"):
        select_best_fixed(outcomes, selection_ids, selection_ids[:1] + evaluation_ids)

    # A configuration that did not run every task is refused rather than scored 0 on what it
    # is missing: treating an absent outcome as a failure would reward the shortest column
    # and make the argmax a comparison between different task sets.
    missing_selection = {cfg: dict(tasks) for cfg, tasks in outcomes.items()}
    del missing_selection["cfg_late"][selection_ids[0]]
    with pytest.raises(ValueError, match="has no outcome for task"):
        select_best_fixed(missing_selection, selection_ids, evaluation_ids)


def test_recovered_fraction_is_none_when_the_gap_interval_includes_zero() -> None:
    outcomes, router_choice, signal_choice, selection_ids, evaluation_ids = build_narrow_benchmark()

    narrow = estimands(outcomes, router_choice, signal_choice, selection_ids, evaluation_ids, 0.05)

    # The point estimate says there is an opportunity; the interval says it is not shown.
    assert narrow.g_out == pytest.approx(1 / 12)
    assert narrow.intervals["g_out"][0] < 0.0 < narrow.intervals["g_out"][1]
    assert narrow.recovered_fraction is None

    # The same computation on a sample where the gap is real does report the fraction, so
    # the None above is a decision and not an unconditional return.
    wide_outcomes, wide_router, wide_signal, wide_selection, wide_evaluation = (
        build_complementary_benchmark()
    )
    wide = estimands(wide_outcomes, wide_router, wide_signal, wide_selection, wide_evaluation, 0.05)
    assert wide.g_out == pytest.approx(0.5)
    assert wide.g_learn == pytest.approx(0.35)
    assert wide.g_z == pytest.approx(0.25)
    assert wide.intervals["g_out"][0] > 0.0
    assert wide.recovered_fraction is not None
    assert wide.recovered_fraction == pytest.approx(wide.g_learn / wide.g_out)
    assert wide.recovered_fraction == pytest.approx(0.7)

    # The ordering the three estimands exist to expose: the router beats the signal-only
    # chooser, and the oracle bounds them both.
    assert wide.g_z < wide.g_learn < wide.g_out
    assert set(wide.intervals) == set(INTERVAL_KEYS)

    # The oracle is only a ceiling if every configuration was actually run on every
    # evaluation task. A hole is refused, because scoring the gap as 0 there would lower the
    # ceiling silently and inflate the recovered fraction.
    missing_evaluation = {cfg: dict(tasks) for cfg, tasks in wide_outcomes.items()}
    del missing_evaluation["cfg_d"][wide_evaluation[0]]
    with pytest.raises(ValueError, match="has no outcome for task"):
        estimands(
            missing_evaluation,
            wide_router,
            wide_signal,
            wide_selection,
            wide_evaluation,
            0.05,
        )


def test_bonferroni_divides_alpha_by_k_plus_l_in_estimands() -> None:
    outcomes, router_choice, signal_choice, selection_ids, evaluation_ids = (
        build_complementary_benchmark()
    )

    result = estimands(
        outcomes,
        router_choice,
        signal_choice,
        selection_ids,
        evaluation_ids,
        0.05,
        k_configs=4,
        l_policies=3,
    )

    expected_alpha = 0.05 / 7
    assert bonferroni(0.05, 7) == pytest.approx(expected_alpha, rel=1e-12)
    assert result.adjusted_alpha == pytest.approx(expected_alpha, rel=1e-12)

    # Every reported interval is at the adjusted level, the baseline's included: the
    # selection of the baseline is one of the comparisons the family has to pay for.
    corrected = clopper_pearson(
        result.best_fixed.evaluation_successes,
        result.best_fixed.evaluation_trials,
        expected_alpha,
    )
    uncorrected = clopper_pearson(
        result.best_fixed.evaluation_successes,
        result.best_fixed.evaluation_trials,
        0.05,
    )
    assert result.intervals["best_fixed"][0] == pytest.approx(corrected[0], rel=1e-12)
    assert result.intervals["best_fixed"][1] == pytest.approx(corrected[1], rel=1e-12)
    # The correction has to bite: an uncorrected interval is strictly narrower at both ends.
    assert result.intervals["best_fixed"][0] < uncorrected[0]
    assert result.intervals["best_fixed"][1] > uncorrected[1]

    # A larger family widens further, and the gap intervals inherit the same level.
    larger = estimands(
        outcomes,
        router_choice,
        signal_choice,
        selection_ids,
        evaluation_ids,
        0.05,
        k_configs=40,
        l_policies=3,
    )
    assert larger.adjusted_alpha == pytest.approx(0.05 / 43, rel=1e-12)
    for key in INTERVAL_KEYS:
        assert width(larger.intervals[key]) >= width(result.intervals[key])
    assert width(larger.intervals["g_out"]) > width(result.intervals["g_out"])

    # k defaults to the number of configurations actually supplied.
    default_family = estimands(
        outcomes, router_choice, signal_choice, selection_ids, evaluation_ids, 0.05
    )
    assert default_family.adjusted_alpha == pytest.approx(0.05 / 7, rel=1e-12)

    with pytest.raises(ValueError, match="at least one comparison"):
        bonferroni(0.05, 0)

    # g_learn is the only estimand that is a claim about the shipped router, so a router with
    # a coverage hole must fail loudly rather than report a deflated but plausible gain.
    incomplete_router = {k: v for k, v in router_choice.items() if k != evaluation_ids[0]}
    with pytest.raises(ValueError, match="makes no choice for task"):
        estimands(outcomes, incomplete_router, signal_choice, selection_ids, evaluation_ids, 0.05)
    unknown_router = {**router_choice, evaluation_ids[0]: "cfg_does_not_exist"}
    with pytest.raises(ValueError, match="chose unknown configuration"):
        estimands(outcomes, unknown_router, signal_choice, selection_ids, evaluation_ids, 0.05)
    with pytest.raises(ValueError, match="chose unknown configuration"):
        estimands(
            outcomes,
            router_choice,
            {**signal_choice, evaluation_ids[0]: "cfg_does_not_exist"},
            selection_ids,
            evaluation_ids,
            0.05,
        )


def test_hierarchical_bootstrap_is_seed_deterministic_and_widens_with_fewer_groups() -> None:
    # Four project profiles, repeated three times for the twelve-project sample and used once
    # for the four-project sample: both have the same spread of project means, so the only
    # difference between them is how many projects there are to resample.
    profiles = [
        [0.02, 0.05, 0.11],
        [0.31, 0.36, 0.28],
        [0.62, 0.55, 0.71],
        [0.88, 0.94, 0.83],
    ]
    many = {f"proj_{index:02d}": list(profiles[index % 4]) for index in range(12)}
    few = {f"proj_{index:02d}": list(profiles[index]) for index in range(4)}

    first = hierarchical_bootstrap(many, mean, 400, 11)
    again = hierarchical_bootstrap(many, mean, 400, 11)
    other_seed = hierarchical_bootstrap(many, mean, 400, 12)

    # Same seed, identical digits — not merely close. A different seed must move the answer,
    # which is what shows the seed is being used rather than swallowed.
    assert first == again

    # Same groups, same units, different dict insertion order: the seed must still pin the
    # answer, which it only does because the group ids are sorted before sampling.
    reordered = {key: list(many[key]) for key in sorted(many, reverse=True)}
    assert list(reordered) != list(many)
    assert hierarchical_bootstrap(reordered, mean, 400, 11) == first

    assert other_seed != first
    assert first[0] < mean([value for group in many.values() for value in group]) < first[1]

    sparse = hierarchical_bootstrap(few, mean, 400, 11)
    assert width(sparse) > width(first)

    # Clustering has to be doing work. Pooling every unit and resampling units alone gives a
    # visibly narrower interval on this data (about 0.22 against about 0.33), so a
    # single-level bootstrap wearing this function's name fails here.
    pooled = {"all_units": [value for group in many.values() for value in group]}
    units_only = hierarchical_bootstrap(pooled, mean, 400, 11)
    assert width(units_only) < 0.25

    # The second level has to be doing work too. With one group the group-level draw is a
    # no-op (every replicate draws the same project), so the entire width comes from
    # resampling units within it. A bootstrap that took each drawn group's units wholesale
    # returns a point mass here.
    one_group = hierarchical_bootstrap({"proj_00": [0.0, 0.25, 0.5, 0.75, 1.0]}, mean, 400, 11)
    assert width(one_group) > 0.3
    assert width(first) > 0.28

    with pytest.raises(ValueError, match="at least one group"):
        hierarchical_bootstrap({}, mean, 400, 11)
    with pytest.raises(ValueError, match="no units to resample"):
        hierarchical_bootstrap({"proj_00": []}, mean, 400, 11)


def test_paired_regret_ci_is_a_project_clustered_interval_on_the_reduction() -> None:
    # Three projects, six paired trials each. The candidate regrets 0.10 less on every trial
    # in two projects and 0.02 less in the third, so the reduction is real but unequal.
    pairs_by_project = {
        "proj_a": [(0.40 + 0.01 * index, 0.30 + 0.01 * index) for index in range(6)],
        "proj_b": [(0.50 + 0.01 * index, 0.40 + 0.01 * index) for index in range(6)],
        "proj_c": [(0.20 + 0.01 * index, 0.18 + 0.01 * index) for index in range(6)],
    }

    interval = paired_regret_ci(pairs_by_project, 400, 5)

    # Positive means the candidate regretted less; the whole interval is above zero here, so
    # this is the protocol's "interval excluding no improvement".
    assert interval[0] > 0.0
    assert interval[0] < (0.10 + 0.10 + 0.02) / 3 < interval[1]
    assert paired_regret_ci(pairs_by_project, 400, 5) == interval

    # Reversing every pair reverses the sign of the interval and nothing else.
    reversed_pairs = {
        project_id: [(candidate, baseline) for baseline, candidate in pairs]
        for project_id, pairs in pairs_by_project.items()
    }
    mirrored = paired_regret_ci(reversed_pairs, 400, 5)
    assert mirrored[1] < 0.0
    assert mirrored[0] == pytest.approx(-interval[1], abs=1e-12)
    assert mirrored[1] == pytest.approx(-interval[0], abs=1e-12)

    with pytest.raises(ValueError, match="at least one project"):
        paired_regret_ci({}, 400, 5)


def test_pass_at_k_and_pass_pow_k_edge_cases() -> None:
    # k = 1 is the plain success rate; that is the estimator's anchor.
    assert pass_at_k(10, 3, 1) == pytest.approx(0.3)
    # No sample passed, so no subset of samples contains a pass.
    assert pass_at_k(10, 0, 5) == 0.0
    # Every sample passed, so every subset does.
    assert pass_at_k(10, 10, 5) == 1.0
    # Fewer failures than the subset size: a subset of k must contain a pass.
    assert pass_at_k(4, 1, 4) == 1.0
    # 1 - C(2, 2)/C(4, 2) = 1 - 1/6.
    assert pass_at_k(4, 2, 2) == pytest.approx(5 / 6)
    # Monotone in k, and never below the k = 1 rate: more attempts cannot hurt.
    assert pass_at_k(10, 3, 1) < pass_at_k(10, 3, 2) < pass_at_k(10, 3, 3)
    # The estimator is not c/n for k > 1, which is the mistake it exists to prevent.
    assert pass_at_k(10, 3, 2) > 0.3

    with pytest.raises(ValueError, match="k must lie"):
        pass_at_k(10, 3, 11)
    with pytest.raises(ValueError, match="k must lie"):
        pass_at_k(10, 3, 0)
    with pytest.raises(ValueError, match="pass count must lie"):
        pass_at_k(10, 11, 2)
    with pytest.raises(ValueError, match="at least one sample"):
        pass_at_k(0, 0, 1)

    # pass^k is the other direction: every attempt must succeed.
    assert pass_pow_k(0.9, 1) == pytest.approx(0.9)
    assert pass_pow_k(0.9, 2) == pytest.approx(0.81)
    assert pass_pow_k(0.9, 0) == 1.0
    assert pass_pow_k(0.0, 3) == 0.0
    assert pass_pow_k(1.0, 100) == 1.0
    assert pass_pow_k(0.0, 0) == 1.0
    # The two measures move opposite ways in k, which is why both are reported.
    assert pass_pow_k(0.5, 3) < pass_pow_k(0.5, 1) < pass_at_k(10, 5, 3)

    with pytest.raises(ValueError, match="success probability must lie"):
        pass_pow_k(1.5, 2)
    with pytest.raises(ValueError, match="cannot be negative"):
        pass_pow_k(0.5, -1)
