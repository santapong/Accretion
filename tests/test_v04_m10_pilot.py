"""The §21 pilot: the variance the locked test will be sized from, and nothing decided.

Protocol §21 requires a development-only pilot before the pre-registration fields are frozen.
These tests hold that pilot to four properties, and every one of them is a way a pilot could
produce a number that looks like evidence and is not:

* the **negative control** — a router that is the baseline recovers no measurable share of an
  opportunity, and the report says so by refusing to quote a fraction;
* **determinism** — two runs over one seeded corpus agree digit for digit, or the σ a sample
  size is frozen against is a different σ every afternoon;
* **honesty about thinness** — two trials per cell is one degree of freedom per cell, and the
  report flags it rather than quoting the σ as though it were solid;
* **the corpus is frozen** — the regeneration path writes a fresh corpus and never touches
  ``evals/``, because a pilot that can rewrite the benchmark's input is not a measurement of
  it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from accretion.router_benchmark import (
    BenchmarkSplit,
    RouterBenchmarkCorpus,
    RouterBenchmarkRunner,
)
from accretion.routing.baselines import BASELINE_ORDER
from accretion.routing.pilot import (
    FLAGGED_TRIALS_PER_CELL,
    PILOT_DELTAS,
    PolicyPilot,
    TrialSigma,
    base_rates,
    icc,
    pilot_report,
    power_table,
    trial_sigma,
    within_between_variance,
)
from accretion.routing.regret import Outcome
from accretion.routing.stats import paired_regret_ci

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import router_pilot  # noqa: E402

BOOTSTRAP_REPLICATES = 2_000


def evals_digest() -> str:
    """One digest over every byte of ``evals/``, so a stray write anywhere in it is visible."""

    hasher = hashlib.sha256()
    for path in sorted(EVALS.rglob("*")):
        if path.is_file():
            hasher.update(str(path.relative_to(EVALS)).encode())
            hasher.update(hashlib.sha256(path.read_bytes()).digest())
    return hasher.hexdigest()


def outcome(*, verified: bool, false_accept: bool) -> Outcome:
    """A verdict-only outcome: the quantities :func:`base_rates` reads and no others."""

    return Outcome(
        quality=0.8 if verified else 0.2,
        cost=0.1,
        latency=0.1,
        latency_ms=6_000,
        verified=verified,
        false_accept=false_accept,
        invalid=False,
    )


@pytest.fixture(scope="module")
def nine_trial_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A nine-trial regeneration from the shipped seed, built once for the whole module."""

    return router_pilot.regenerate(tmp_path_factory.mktemp("pilot-nine"), 9)


def test_a_router_that_is_the_baseline_recovers_no_fraction_and_no_improvement() -> None:
    runner = RouterBenchmarkRunner()
    result = runner.run(["M0"], split=BenchmarkSplit.EVALUATION)
    policy = result.policy("M0")

    assert policy.estimands is not None
    # The opportunity gap's lower limit is not positive on this corpus, so the share of it a
    # router recovered is undefined and must be withheld rather than divided out.
    assert policy.estimands.intervals["g_out"][0] <= 0.0
    assert policy.estimands.recovered_fraction is None

    assert policy.regret is not None
    against_itself = {
        project_id: [(value, value) for value in values]
        for project_id, values in policy.regret.pairs_by_project().items()
    }
    lower, upper = paired_regret_ci(
        against_itself, BOOTSTRAP_REPLICATES, runner.corpus.config.seed
    )
    assert lower <= 0.0 <= upper


def test_two_pilots_over_one_seeded_corpus_agree_digit_for_digit() -> None:
    runner = RouterBenchmarkRunner()
    first = pilot_report(runner, list(BASELINE_ORDER), split=BenchmarkSplit.EVALUATION)
    second = pilot_report(
        RouterBenchmarkRunner(), list(BASELINE_ORDER), split=BenchmarkSplit.EVALUATION
    )

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert first.run_id == runner.corpus.run_id


def test_the_shipped_two_trial_cells_are_flagged_as_one_degree_of_freedom_each() -> None:
    report = pilot_report(
        RouterBenchmarkRunner(), list(BASELINE_ORDER), split=BenchmarkSplit.EVALUATION
    )

    assert report.utility_sigma.minimum_trials_per_cell == FLAGGED_TRIALS_PER_CELL
    assert report.utility_sigma.flagged is True
    assert report.pass_rate_sigma.flagged is True
    # One degree of freedom per cell is exactly what two trials buy, and the report says the
    # count rather than leaving a reader to infer it from the cell census.
    assert report.utility_sigma.degrees_of_freedom == report.utility_sigma.cells


def test_a_nine_trial_regeneration_clears_the_flag_and_multiplies_the_evidence(
    nine_trial_root: Path,
) -> None:
    shipped = pilot_report(
        RouterBenchmarkRunner(), list(BASELINE_ORDER), split=BenchmarkSplit.EVALUATION
    )
    regenerated = pilot_report(
        RouterBenchmarkRunner(RouterBenchmarkCorpus.load(nine_trial_root)),
        list(BASELINE_ORDER),
        split=BenchmarkSplit.EVALUATION,
    )

    assert regenerated.utility_sigma.minimum_trials_per_cell == 9
    assert regenerated.utility_sigma.flagged is False
    assert regenerated.pass_rate_sigma.flagged is False
    assert regenerated.utility_sigma.cells == shipped.utility_sigma.cells
    assert regenerated.utility_sigma.degrees_of_freedom == 8 * shipped.utility_sigma.cells
    # Same seed, same designed world: only the repeat count moved, so the corpus digest over
    # config, tasks and candidates is untouched while the trace digest is not.
    assert regenerated.corpus_sha256 == shipped.corpus_sha256
    assert regenerated.trace_sha256 != shipped.trace_sha256


def test_regenerating_a_corpus_writes_nothing_under_evals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = evals_digest()

    exit_code = router_pilot.main(
        ["--regenerate", "--trials", "3", "--out", str(tmp_path / "corpus"), "--json"]
    )

    assert exit_code == 0
    assert evals_digest() == before
    document = json.loads(capsys.readouterr().out)
    assert document["regenerated"] is True
    assert document["trials_per_cell"] == 3
    assert [report["split"] for report in document["reports"]] == ["SELECTION", "EVALUATION"]


def test_the_regenerator_refuses_a_destination_inside_the_frozen_corpus() -> None:
    with pytest.raises(ValueError, match="the shipped corpus is frozen"):
        router_pilot.regenerate(EVALS / "router", 9)
    with pytest.raises(ValueError, match="the shipped corpus is frozen"):
        router_pilot.regenerate(EVALS / "router" / "scratch", 9)


def test_regeneration_leaves_the_generators_trial_count_where_it_found_it(
    tmp_path: Path,
) -> None:
    generator = router_pilot.load_generator()
    original = generator.TRIALS_PER_CELL

    router_pilot.regenerate(tmp_path / "corpus", 4)

    assert generator.TRIALS_PER_CELL == original


def test_the_decomposition_puts_a_clustered_source_between_and_a_flat_one_within() -> None:
    flat = {"p1": [0.0, 1.0, 0.0, 1.0], "p2": [1.0, 0.0, 1.0, 0.0], "p3": [0.0, 1.0, 1.0, 0.0]}
    clustered = {"p1": [0.0, 0.02, 0.01], "p2": [5.0, 5.02, 5.01], "p3": [10.0, 10.02, 10.01]}

    flat_components = within_between_variance(flat)
    clustered_components = within_between_variance(clustered)

    assert flat_components.between == 0.0
    assert flat_components.within > 0.0
    assert icc(flat_components) == 0.0
    assert clustered_components.between > clustered_components.within
    assert icc(clustered_components) > 0.99
    assert clustered_components.projects == 3
    assert clustered_components.units == 9
    assert clustered_components.effective_group_size == 3.0


def test_the_decomposition_refuses_data_that_has_no_second_level() -> None:
    with pytest.raises(ValueError, match="at least one project"):
        within_between_variance({})
    with pytest.raises(ValueError, match="at least two projects"):
        within_between_variance({"p1": [1.0, 2.0, 3.0]})
    with pytest.raises(ValueError, match="no within-project degrees of freedom"):
        within_between_variance({"p1": [1.0], "p2": [2.0]})
    with pytest.raises(ValueError, match="no values to decompose"):
        within_between_variance({"p1": [1.0, 2.0], "p2": []})


def test_an_identical_column_has_no_variance_to_share() -> None:
    components = within_between_variance({"p1": [2.0, 2.0], "p2": [2.0, 2.0]})

    assert components.within == 0.0
    assert components.between == 0.0
    assert icc(components) == 0.0


def test_a_trial_sigma_needs_a_second_trial_in_every_cell() -> None:
    with pytest.raises(ValueError, match="at least one cell"):
        trial_sigma({})
    with pytest.raises(ValueError, match="fewer than two trials"):
        trial_sigma({"a|b": [0.5], "c|d": [0.4, 0.6]})


def test_the_thinness_flag_is_derived_and_cannot_be_asserted() -> None:
    with pytest.raises(ValueError, match="the flag is derived, not chosen"):
        TrialSigma(
            cells=2,
            trials=4,
            degrees_of_freedom=2,
            minimum_trials_per_cell=2,
            maximum_trials_per_cell=2,
            cells_at_minimum=2,
            sigma=0.1,
            flagged=False,
        )


def test_base_rates_bound_a_false_acceptance_above_the_rate_it_observed() -> None:
    outcomes = [outcome(verified=True, false_accept=index == 0) for index in range(18)]

    rates = base_rates(outcomes)

    assert rates.trials == 18
    assert rates.verified_successes == 18
    assert rates.verified_success_rate == 1.0
    # Eighteen successes out of eighteen contradict no proportion at the top of the range.
    assert rates.verified_success_interval[1] == 1.0
    assert rates.false_acceptances == 1
    assert rates.false_acceptance_rate == pytest.approx(1 / 18)
    # The whole point of the bound: one acceptance in eighteen is compatible with far more
    # than one in eighteen, and a ceiling frozen at the point estimate would be frozen wrong.
    assert rates.false_acceptance_upper > rates.false_acceptance_rate
    assert rates.false_acceptance_upper < 1.0


def test_base_rates_refuse_to_be_rates_of_nothing() -> None:
    with pytest.raises(ValueError, match="at least one selection"):
        base_rates([])


def test_a_zero_count_keeps_its_lower_limit_at_zero() -> None:
    rates = base_rates([outcome(verified=False, false_accept=False) for _ in range(10)])

    assert rates.verified_success_rate == 0.0
    assert rates.verified_success_interval[0] == 0.0
    assert rates.verified_success_interval[1] > 0.0
    assert rates.false_acceptance_upper > 0.0


def test_halving_the_detectable_effect_roughly_quadruples_the_runs() -> None:
    table = power_table(0.05)

    assert [row.delta for row in table.rows] == sorted(PILOT_DELTAS, reverse=True)
    coarse, fine = table.rows[0].runs_per_configuration, table.rows[1].runs_per_configuration
    # n is quadratic in 1/delta; the two ceilings make the identity a band, not an equality.
    assert 4 * coarse - 4 < fine <= 4 * coarse
    assert table.sigma == 0.05


def test_the_sizing_uses_the_trial_sigma_and_not_the_pooled_spread() -> None:
    report = pilot_report(
        RouterBenchmarkRunner(), list(BASELINE_ORDER), split=BenchmarkSplit.EVALUATION
    )

    assert report.utility_power.sigma == report.utility_sigma.sigma
    assert report.pass_rate_power.sigma == report.pass_rate_sigma.sigma
    # Trial-to-trial noise is strictly smaller than the spread across cells, which is why
    # handing the pooled figure to a power calculation would oversize the locked test.
    assert report.utility_sigma.sigma < report.pass_rate_sigma.sigma


def test_every_protocol_baseline_keeps_its_line_including_the_unbuilt_ones() -> None:
    report = pilot_report(
        RouterBenchmarkRunner(), list(BASELINE_ORDER), split=BenchmarkSplit.EVALUATION
    )

    assert [line.policy_id for line in report.policies] == list(BASELINE_ORDER)
    unbuilt = [line for line in report.policies if not line.available]
    assert [line.policy_id for line in unbuilt] == ["M7", "M8", "M9"]
    for line in unbuilt:
        assert line.reason_code == "NOT_AVAILABLE"
        assert line.rates is None
        assert line.mean_regret is None
        assert line.selections == 0


def test_an_unavailable_policy_may_not_carry_a_measurement() -> None:
    with pytest.raises(ValueError, match="has nothing to report"):
        PolicyPilot(
            policy_id="M7",
            available=False,
            reason_code="NOT_AVAILABLE",
            selections=0,
            mean_regret=0.0,
            outage_selections=0,
            outage_rate=0.0,
        )


def test_the_base_rates_and_the_gates_are_counted_on_one_denominator() -> None:
    report = pilot_report(
        RouterBenchmarkRunner(), list(BASELINE_ORDER), split=BenchmarkSplit.EVALUATION
    )
    result = RouterBenchmarkRunner().run(list(BASELINE_ORDER), split=BenchmarkSplit.EVALUATION)

    for line in report.policies:
        if not line.available or line.rates is None:
            continue
        gates = result.policy(line.policy_id).gates
        assert gates is not None
        assert line.rates.trials == gates.selections
        assert line.rates.verified_successes == gates.verified_successes
        assert line.rates.false_acceptances == gates.false_acceptances
        # The replay grid is complete, so nothing was selected that has no recorded outcome.
        assert line.outage_selections == 0
        assert line.outage_rate == 0.0


def test_both_halves_of_the_split_are_measured_on_their_own_tasks() -> None:
    runner = RouterBenchmarkRunner()
    selection = pilot_report(runner, ["M0"], split=BenchmarkSplit.SELECTION)
    evaluation = pilot_report(runner, ["M0"], split=BenchmarkSplit.EVALUATION)

    assert selection.tasks == evaluation.tasks == 18
    assert selection.projects == evaluation.projects == 6
    # Different task sets, therefore different measurements: a report that returned the same
    # sigma for both halves would be reporting the whole corpus under a split's name.
    assert selection.utility_sigma.sigma != evaluation.utility_sigma.sigma
    assert selection.policy("M0").mean_regret != evaluation.policy("M0").mean_regret
    with pytest.raises(KeyError):
        evaluation.policy("M4")
