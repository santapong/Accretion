"""The off-policy estimators, the simultaneous band, and the registered gate constants.

Every claim here is arithmetic, so every test computes the answer a second way rather than
pinning a number a previous run printed. The λ → 0 limit is the load-bearing one: R4's
Logarithmic-Smoothing estimator is only the estimator it says it is if it degenerates to
unsmoothed inverse propensity scoring as the smoothing vanishes, and an implementation that
lost the minus sign, divided rather than multiplied, or smoothed the weight instead of the
product would still return plausible-looking costs while failing that limit.

The registered document is read from disk exactly as production reads it, because a config
test that validated a dict built in the test file would prove nothing about the file the gate
loads. There is no ``conftest.py``; every builder below is module-local.
"""

from __future__ import annotations

import json
import math

import pytest

from accretion.routing.ope import (
    PROMOTION_CONFIG_PATH,
    PromotionConfig,
    clip_mass,
    default_promotion_config,
    dr,
    ess,
    lambda_rule,
    ls_estimate,
    snips,
    sup_t_band,
)

WEIGHTS = [2.0, 0.5, 1.0, 0.25]
COSTS = [-1.0, -0.25, 0.0, -1.0]


def ips(weights: list[float], costs: list[float]) -> float:
    """The unsmoothed estimate, written out so the limit below has something to converge to."""

    return math.fsum(w * c for w, c in zip(weights, costs, strict=True)) / len(weights)


# --------------------------------------------------------------------------------------
# The Logarithmic-Smoothing estimator.
# --------------------------------------------------------------------------------------


def test_the_smoothed_estimate_converges_to_inverse_propensity_scoring_as_lambda_vanishes() -> (
    None
):
    """R4's estimator is a smoothing *of* IPS, and this is the statement that makes it one."""

    target = ips(WEIGHTS, COSTS)
    errors = [abs(ls_estimate(WEIGHTS, COSTS, lam) - target) for lam in (1e-2, 1e-4, 1e-6)]

    assert errors == sorted(errors, reverse=True)
    assert errors[-1] < 1e-5


def test_the_smoothed_estimate_is_never_more_optimistic_than_inverse_propensity_scoring() -> None:
    """Pessimism is the property the gate buys, so it is asserted rather than assumed.

    With costs in ``[-1, 0]`` a smaller number is a *better* policy, so "never more
    optimistic" means the smoothed estimate is never below the IPS one at any λ.
    """

    for lam in (0.01, 0.1, 0.5, 1.0, 4.0):
        assert ls_estimate(WEIGHTS, COSTS, lam) >= ips(WEIGHTS, COSTS)


def test_a_unit_the_target_policy_declined_contributes_exactly_nothing() -> None:
    """A zero weight is how "defer to the incumbent here" is expressed, and it must cost 0.

    If it did not, ``n`` could not stay the size of the holdout, and λ = 1/√n would grow as
    the threshold got stricter — which would make the most selective policy look best for a
    reason that has nothing to do with the policy.
    """

    lam = lambda_rule(4)
    kept = ls_estimate([1.0, 1.0], [-1.0, -0.5], lam)
    padded = ls_estimate([1.0, 1.0, 0.0, 0.0], [-1.0, -0.5, -1.0, 0.0], lam)

    assert padded == pytest.approx(kept / 2.0)


def test_a_reward_handed_in_where_a_cost_belongs_is_refused_rather_than_clipped() -> None:
    """The sign convention is the one thing a caller can get backwards, so it is checked."""

    with pytest.raises(ValueError, match=r"outside \[-1, 0\]"):
        ls_estimate([1.0], [0.75], 0.5)


def test_the_smoothing_rate_is_the_registered_rule_and_refuses_an_empty_sample() -> None:
    """``1/sqrt(n)``, over units and not over clusters."""

    assert lambda_rule(4) == pytest.approx(0.5)
    assert lambda_rule(10_000) == pytest.approx(0.01)
    with pytest.raises(ValueError, match="at least one unit"):
        lambda_rule(0)


# --------------------------------------------------------------------------------------
# The diagnostics.
# --------------------------------------------------------------------------------------


def test_the_self_normalised_estimate_divides_by_the_weight_it_actually_used() -> None:
    """SNIPS differs from IPS exactly when the weights do not average to one."""

    assert snips([2.0, 0.5], [-1.0, 0.0]) == pytest.approx(-2.0 / 2.5)
    assert snips([1.0, 1.0], [-1.0, 0.0]) == pytest.approx(-0.5)


def test_a_target_policy_that_retained_nothing_has_a_self_normalised_cost_of_zero() -> None:
    """Zero is the cost of taking no credited action, not a fallback for a division by zero."""

    assert snips([0.0, 0.0], [-1.0, -1.0]) == 0.0


def test_the_doubly_robust_estimate_falls_back_on_the_regression_where_the_weight_is_zero() -> (
    None
):
    """With no weight anywhere, DR is the direct method and nothing else."""

    assert dr([0.0, 0.0], [-1.0, 0.0], [-0.4, -0.6]) == pytest.approx(-0.5)
    assert dr([1.0, 1.0], [-1.0, 0.0], [-0.4, -0.6]) == pytest.approx(-0.5)


def test_the_effective_sample_size_collapses_when_one_weight_dominates() -> None:
    """Kish's ESS: four equal weights are worth four units and a spike is worth about one."""

    assert ess([1.0, 1.0, 1.0, 1.0]) == pytest.approx(4.0)
    assert ess([100.0, 1.0, 1.0, 1.0]) < 1.2


def test_the_clip_mass_is_the_share_of_weight_a_threshold_would_have_discarded() -> None:
    """Reported and never applied, so the bias clipping would introduce stays visible."""

    assert clip_mass([1.0, 1.0, 8.0], 2.0) == pytest.approx(6.0 / 10.0)
    assert clip_mass([1.0, 1.0], 2.0) == 0.0


# --------------------------------------------------------------------------------------
# The simultaneous band.
# --------------------------------------------------------------------------------------


def flat_groups() -> dict[str, list[list[float]]]:
    """Three projects, six units, two columns — the second deliberately constant.

    Unequal group sizes on purpose: the hierarchical bootstrap draws each group's *own*
    number of units, so a corpus with three equal groups would pass under a resampler that
    had forgotten to.
    """

    return {
        "prj_a": [[0.10, 0.5], [0.15, 0.5], [0.05, 0.5]],
        "prj_b": [[0.30, 0.5], [0.20, 0.5]],
        "prj_c": [[-0.10, 0.5]],
    }


def test_the_band_is_the_same_interval_on_every_run_of_one_seed() -> None:
    """A seeded bootstrap is a function of its inputs, or its numbers are decoration."""

    groups = flat_groups()

    first = sup_t_band(groups, 200, 4242)
    second = sup_t_band(groups, 200, 4242)

    assert first == second
    assert len(first) == 2


def test_a_column_that_never_moved_gets_a_degenerate_interval_and_not_a_crash() -> None:
    """Every unit's second entry is 0.5, so its bootstrap standard error is exactly zero."""

    band = sup_t_band(flat_groups(), 200, 7)

    assert band[1] == (pytest.approx(0.5), pytest.approx(0.5))
    assert band[0][0] < band[0][1]


def test_the_simultaneous_band_is_wider_than_the_per_column_interval_it_corrects() -> None:
    """Simultaneity costs width; a band that did not pay it would not be simultaneous.

    Compared against the band the same resamples produce over a *single* column, which is
    the pointwise interval by construction: with one column the maximum absolute t is that
    column's own, so the critical value is its own two-sided quantile.
    """

    groups = flat_groups()
    single = {
        project: [[unit[0]] for unit in units] for project, units in groups.items()
    }

    wide = sup_t_band(groups, 400, 11)[0]
    narrow = sup_t_band(single, 400, 11)[0]

    assert (wide[1] - wide[0]) >= (narrow[1] - narrow[0])


def test_units_of_two_different_lengths_are_two_grids_and_are_refused() -> None:
    """A band is over one grid; rows of different widths would silently drop a threshold."""

    with pytest.raises(ValueError, match="different lengths"):
        sup_t_band({"prj_a": [[0.1, 0.2], [0.3]]}, 10, 1)


# --------------------------------------------------------------------------------------
# The registered constants.
# --------------------------------------------------------------------------------------


def test_the_registered_file_holds_the_constants_the_gate_was_registered_with() -> None:
    """Read from disk exactly as production reads it, so the committed file is what is tested."""

    config = PromotionConfig.load()

    assert config.gamma == 0.05
    assert config.delta_min == 0.02
    assert config.delta_ni == -0.02
    assert config.alpha == 0.05
    assert config.power == 0.8
    assert config.tune_eval_split == [0.2, 0.8]
    assert config.ls_lambda_rule == "1/sqrt(n)"
    assert config.clip_beta_rule == "conformal"
    assert config.bootstrap_replicates == 500
    assert config.calibration_max_ece == 0.05
    assert config.shadow_min_paired_runs == 9
    assert config.multiplicity == "bonferroni"
    assert config.critical_cohorts == [
        "correctness",
        "high_risk",
        "policy",
        "secrets",
        "verifier_conflict",
    ]


def test_the_threshold_grid_runs_from_the_registered_start_to_its_stop_inclusive() -> None:
    """Ten thresholds, ``0.5`` to ``0.95``, with no float drift in the last one."""

    grid = default_promotion_config().thresholds()

    assert grid[0] == 0.5
    assert grid[-1] == 0.95
    assert len(grid) == 10
    assert grid == sorted(set(grid))


def test_the_tune_half_is_taken_off_the_front_of_the_sorted_projects() -> None:
    """Deterministic and disjoint: R3 needs ``c*`` chosen without seeing the tested half."""

    config = default_promotion_config()
    tune, evaluation = config.tune_projects(["prj_e", "prj_a", "prj_c", "prj_d", "prj_b"])

    assert tune == ["prj_a"]
    assert evaluation == ["prj_b", "prj_c", "prj_d", "prj_e"]
    assert set(tune) & set(evaluation) == set()


def test_a_single_holdout_project_yields_no_tune_half_rather_than_a_shared_one() -> None:
    """Choosing and testing on one sample is the failure the split exists to prevent."""

    tune, evaluation = default_promotion_config().tune_projects(["prj_only"])

    assert tune == []
    assert evaluation == ["prj_only"]


def test_a_split_that_does_not_partition_the_holdout_is_refused() -> None:
    """The two halves cover the projects exactly once, or one group is being read twice."""

    document = json.loads(PROMOTION_CONFIG_PATH.read_text(encoding="utf-8"))
    document["tune_eval_split"] = [0.2, 0.5]

    with pytest.raises(ValueError, match="does not sum to one"):
        PromotionConfig.model_validate(document)


def test_critical_cohorts_must_be_ascending_so_two_readers_compare_the_same_list() -> None:
    """A set is not a registered list; the order is part of what was frozen."""

    document = json.loads(PROMOTION_CONFIG_PATH.read_text(encoding="utf-8"))
    document["critical_cohorts"] = ["secrets", "correctness"]

    with pytest.raises(ValueError, match="ascending"):
        PromotionConfig.model_validate(document)


def test_an_unknown_key_in_the_registered_file_is_refused_rather_than_ignored() -> None:
    """``StrictModel`` forbids extras, so a mis-registered constant is loud."""

    document = json.loads(PROMOTION_CONFIG_PATH.read_text(encoding="utf-8"))
    document["delta_maximum"] = 0.5

    with pytest.raises(ValueError, match="delta_maximum"):
        PromotionConfig.model_validate(document)


def test_the_config_lives_beside_the_benchmark_corpus_and_not_inside_it() -> None:
    """ADR4-M8-003: ``RouterBenchmarkConfig`` is ``extra="forbid"`` and M10c edits its file."""

    assert PROMOTION_CONFIG_PATH.name == "promotion.v1.json"
    assert PROMOTION_CONFIG_PATH.parent.parts[-2:] == ("evals", "router")
    assert (PROMOTION_CONFIG_PATH.parent / "config.v1.json").exists()
    benchmark = json.loads(
        (PROMOTION_CONFIG_PATH.parent / "config.v1.json").read_text(encoding="utf-8")
    )
    assert "delta_min" not in benchmark
