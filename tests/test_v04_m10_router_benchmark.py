"""The router benchmark itself: a pinned corpus, two independent columns, and honest gaps.

Four claims, each of them a way the suite could report a number nobody should believe.

**The gates are not the utility.** The same corpus is run twice under two different weight
vectors. Every comparator makes the same choices, every utility moves and every gate stays
exactly where it was, which is a stronger statement than "they are computed separately"
because it survives a refactor that merges the two expressions. The oracle is the one
exception and is asserted separately: it is an argmax over utility by definition, so
re-weighting genuinely changes what it selects, and its gates move *because the selection
moved* rather than because a weight reached a gate. The test also pins the substantive
consequence on this corpus: under the registered weights the method with the best mean
utility fails the false-acceptance ceiling that a worse-scoring method clears, so a report
that collapsed the two columns would rank them the wrong way round.

**The run id is the corpus.** The shipped corpus is copied to a temporary directory, loaded,
and confirmed to produce the shipped run id; then one quality value in one trace is changed
and the run id, the trace digest and nothing else about the request must move. A benchmark
whose id survived an edit to its data would let two different experiments share a name.

**The estimands are selection-valid and corrected.** The best fixed configuration is the one
:func:`~accretion.routing.stats.select_best_fixed` picks from the selection half, the alpha
is Bonferroni over the K configurations plus the L reported policies, and
``recovered_fraction`` is ``None`` because the opportunity gap's interval includes zero on
eighteen evaluation tasks. That last one is the assertion that keeps a ratio with an
undetermined denominator out of the report.

**The corpus is what its seed says it is.** ``tests/router_corpus_generator.py`` is rerun and
its output compared byte for byte with the committed files, so a hand edit to a trace is a
red test rather than a quiet re-tuning.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import fields
from pathlib import Path

import pytest
from router_corpus_generator import build, write

from accretion.contracts import BenchmarkExecutionSource
from accretion.contracts.routing import UtilityWeights
from accretion.router_benchmark import (
    BenchmarkSplit,
    CorpusError,
    GateReport,
    LiveRunRefused,
    RouterBenchmarkCorpus,
    RouterBenchmarkRunner,
)
from accretion.routing.baselines import BASELINE_ORDER
from accretion.routing.split import SplitViolation
from accretion.routing.stats import bonferroni, select_best_fixed

CORPUS_FILES = (
    "config.v1.json",
    "tasks.v1.json",
    "candidates.v1.json",
    "replay-traces.v1.json",
    "projects.v1.json",
)


def setup_corpus_copy(tmp_path: Path) -> Path:
    """The shipped corpus, copied somewhere a test may edit it."""

    root = tmp_path / "router"
    root.mkdir(parents=True)
    for name in CORPUS_FILES:
        shutil.copy(RouterBenchmarkCorpus.load().root / name, root / name)
    return root


def rewrite(path: Path, mutate: object) -> None:
    """Apply ``mutate`` to a corpus document and write it back with the shipped formatting."""

    document = json.loads(path.read_text(encoding="utf-8"))
    assert callable(mutate)
    mutate(document)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_gates_are_reported_separately_from_utility() -> None:
    corpus = RouterBenchmarkCorpus.load()
    # Every comparator whose *selection* does not read utility. The oracle is excluded here
    # and tested below: it is an argmax over utility by definition, so re-weighting really
    # does change what it picks, and asserting otherwise would be asserting a bug.
    policies = ["M0", "M1", "M2", "M5", "M6"]

    quality_only = RouterBenchmarkRunner(
        corpus, weights=UtilityWeights(quality=1.0, cost=0.0, latency=0.0)
    ).run(policies)
    cost_averse = RouterBenchmarkRunner(
        corpus, weights=UtilityWeights(quality=1.0, cost=0.9, latency=0.6)
    ).run(policies)

    for policy_id in policies:
        cheap = quality_only.policy(policy_id)
        dear = cost_averse.policy(policy_id)
        chosen = [row.selected_candidate_id for row in cheap.rows]
        assert [row.selected_candidate_id for row in dear.rows] == chosen
        assert cheap.gates == dear.gates, "a gate must not move when the objective is re-weighted"
        assert cheap.mean_utility != dear.mean_utility, "re-weighting must move the utility"

    # The gate really is read off the verdicts of the rows and not off anything derived.
    for result in cost_averse.policies:
        assert result.gates is not None
        assert result.gates.verified_successes == sum(1 for row in result.rows if row.verified)
        assert result.gates.false_acceptances == sum(1 for row in result.rows if row.false_accept)

    # The two columns disagree about the ranking under the corpus's own registered weights,
    # which is why there are two of them: the method with the highest mean utility is not the
    # one that clears the safety ceiling.
    registered = RouterBenchmarkRunner(corpus).run([*policies, "ORACLE"])
    ranked = sorted(
        (result for result in registered.policies if result.mean_utility is not None),
        key=lambda result: result.mean_utility or 0.0,
        reverse=True,
    )
    best_utility = ranked[0]
    assert best_utility.policy_id == "ORACLE"
    assert best_utility.gates is not None
    assert best_utility.gates.false_acceptance_met is False
    clears = [
        result
        for result in registered.policies
        if result.gates is not None and result.gates.false_acceptance_met
    ]
    assert clears, "some method must clear the ceiling, or the assertion above is vacuous"
    assert all(
        (result.mean_utility or 0.0) < (best_utility.mean_utility or 0.0) for result in clears
    )

    # The oracle is the exception that proves the separation: its *choice* is a function of
    # the weights, so its gates move with them — and they move because it selected different
    # configurations, never because a weight leaked into a gate.
    oracle_cheap = RouterBenchmarkRunner(
        corpus, weights=UtilityWeights(quality=1.0, cost=0.0, latency=0.0)
    ).run(["ORACLE"]).policy("ORACLE")
    oracle_dear = RouterBenchmarkRunner(
        corpus, weights=UtilityWeights(quality=1.0, cost=0.9, latency=0.6)
    ).run(["ORACLE"]).policy("ORACLE")
    assert [row.selected_candidate_id for row in oracle_cheap.rows] != [
        row.selected_candidate_id for row in oracle_dear.rows
    ]
    assert oracle_cheap.gates is not None and oracle_dear.gates is not None
    assert oracle_cheap.gates.verified_successes == sum(
        1 for row in oracle_cheap.rows if row.verified
    )

    # And the gate report carries verdict counts and thresholds only. A field named for the
    # objective would be the first step back towards one combined score.
    gate_fields = {field.name for field in fields(GateReport)}
    assert gate_fields == {
        "selections",
        "verified_successes",
        "verified_success_rate",
        "verified_success_floor",
        "verified_success_met",
        "false_acceptances",
        "false_acceptance_rate",
        "false_acceptance_ceiling",
        "false_acceptance_met",
    }


def test_corpus_hashes_pin_the_run_id(tmp_path: Path) -> None:
    shipped = RouterBenchmarkCorpus.load()
    root = setup_corpus_copy(tmp_path)
    copied = RouterBenchmarkCorpus.load(root)
    assert copied.run_id == shipped.run_id
    assert copied.trace_sha256 == shipped.trace_sha256
    assert copied.corpus_sha256 == shipped.corpus_sha256

    def bump_one_quality(document: dict[str, object]) -> None:
        traces = document["traces"]
        assert isinstance(traces, list)
        first = traces[0]
        assert isinstance(first, dict)
        first["quality"] = round(min(0.99, float(first["quality"]) + 0.01), 6)

    rewrite(root / "replay-traces.v1.json", bump_one_quality)
    edited = RouterBenchmarkCorpus.load(root)
    assert edited.trace_sha256 != shipped.trace_sha256
    assert edited.corpus_sha256 == shipped.corpus_sha256, "only the traces changed"
    assert edited.run_id != shipped.run_id

    # The task documents are inside the corpus digest too, so a relabelled node also renames
    # the run rather than silently reusing the previous run's identity.
    second = setup_corpus_copy(tmp_path / "second")

    def relabel_one_node(document: dict[str, object]) -> None:
        tasks = document["tasks"]
        assert isinstance(tasks, list)
        first = tasks[0]
        assert isinstance(first, dict)
        first["planner_choice"] = "cnf-claude-opus-full"

    rewrite(second / "tasks.v1.json", relabel_one_node)
    relabelled = RouterBenchmarkCorpus.load(second)
    assert relabelled.corpus_sha256 != shipped.corpus_sha256
    assert relabelled.trace_sha256 == shipped.trace_sha256
    assert relabelled.run_id != shipped.run_id


def test_estimands_use_the_selection_split_and_bonferroni() -> None:
    corpus = RouterBenchmarkCorpus.load()
    result = RouterBenchmarkRunner(corpus).run(["M0", "M6"])

    selection = corpus.task_ids_for(BenchmarkSplit.SELECTION)
    evaluation = corpus.task_ids_for(BenchmarkSplit.EVALUATION)
    assert set(selection).isdisjoint(evaluation)
    assert len(evaluation) == 18

    expected_baseline = select_best_fixed(
        corpus.binary_outcomes(),
        selection,
        evaluation,
        bonferroni(0.05, len(corpus.candidates) + 3),
    )
    for policy_id in ("M0", "M6"):
        found = result.policy(policy_id).estimands
        assert found is not None
        assert found.best_fixed.config_id == expected_baseline.config_id
        assert found.adjusted_alpha == pytest.approx(
            bonferroni(0.05, len(corpus.candidates) + 3)
        )
        # The winner's curse is visible rather than hidden: the rate that won the argmax is
        # higher than the rate it was then scored at.
        assert found.best_fixed.selection_rate > found.best_fixed.evaluation_rate
        # Eighteen evaluation tasks at a Bonferroni-adjusted alpha cannot exclude zero, so
        # the recovered share of the opportunity is not a small number — it is not a number.
        assert found.intervals["g_out"][0] <= 0.0
        assert found.recovered_fraction is None

    # A run reported on the selection half computes no estimands at all: they are defined on
    # the evaluation half, and a partial choice map would change their denominator.
    selection_run = RouterBenchmarkRunner(corpus).run(["M0"], split=BenchmarkSplit.SELECTION)
    assert selection_run.policy("M0").estimands is None
    assert selection_run.policy("M0").regret is not None


def test_a_live_execution_source_is_refused() -> None:
    runner = RouterBenchmarkRunner()
    with pytest.raises(LiveRunRefused) as refusal:
        runner.run(["M1"], execution_source=BenchmarkExecutionSource.LIVE)
    assert "LIVE" in str(refusal.value)
    assert runner.run(["M1"]).execution_source is BenchmarkExecutionSource.REPLAY


def test_a_split_that_divides_a_lineage_is_refused(tmp_path: Path) -> None:
    root = setup_corpus_copy(tmp_path)

    def move_a_fork(document: dict[str, object]) -> None:
        split = document["selection_split"]
        assert isinstance(split, dict)
        # `prj-router-web-api-fork` shares a repository digest with `prj-router-web-api`, so
        # moving one of the pair leaves a lineage on both sides of the split.
        split["evaluation_project_ids"] = [
            project_id
            for project_id in split["evaluation_project_ids"]
            if project_id != "prj-router-web-api-fork"
        ]
        split["selection_project_ids"] = [
            *split["selection_project_ids"],
            "prj-router-web-api-fork",
        ]

    rewrite(root / "config.v1.json", move_a_fork)
    with pytest.raises(SplitViolation) as refusal:
        RouterBenchmarkCorpus.load(root)
    assert "prj-router-web-api" in str(refusal.value)


def test_a_project_on_both_sides_of_the_split_is_refused(tmp_path: Path) -> None:
    root = setup_corpus_copy(tmp_path)

    def duplicate_a_project(document: dict[str, object]) -> None:
        split = document["selection_split"]
        assert isinstance(split, dict)
        split["selection_project_ids"] = [
            *split["selection_project_ids"],
            split["evaluation_project_ids"][0],
        ]

    rewrite(root / "config.v1.json", duplicate_a_project)
    with pytest.raises(ValueError, match="both sides"):
        RouterBenchmarkCorpus.load(root)


def test_a_trace_that_contradicts_declared_eligibility_is_refused(tmp_path: Path) -> None:
    root = setup_corpus_copy(tmp_path)

    def unmark_one_refusal(document: dict[str, object]) -> None:
        traces = document["traces"]
        assert isinstance(traces, list)
        refused = next(cell for cell in traces if cell["invalid"])
        refused["invalid"] = False

    rewrite(root / "replay-traces.v1.json", unmark_one_refusal)
    with pytest.raises(CorpusError, match="contradicts itself"):
        RouterBenchmarkCorpus.load(root)


def test_the_shipped_corpus_is_exactly_what_its_seed_generates(tmp_path: Path) -> None:
    shipped_root = RouterBenchmarkCorpus.load().root
    regenerated = tmp_path / "regenerated"
    regenerated.mkdir()
    write(regenerated)
    for stem in build():
        name = f"{stem}.json"
        assert (regenerated / name).read_bytes() == (shipped_root / name).read_bytes(), (
            f"{name} is not what the seeded generator produces; regenerate it with "
            "`python -m tests.router_corpus_generator` and say in the pull request why the "
            "corpus moved"
        )


def test_every_requested_policy_appears_in_the_result_including_the_unwired_ones() -> None:
    result = RouterBenchmarkRunner().run(list(BASELINE_ORDER))
    assert tuple(item.policy_id for item in result.policies) == BASELINE_ORDER
    unavailable = [item.policy_id for item in result.policies if not item.available]
    assert unavailable == ["M7", "M8", "M9"]
    for item in result.policies:
        if item.available:
            assert item.rows and item.regret is not None and item.gates is not None
        else:
            assert item.reason_code == "NOT_AVAILABLE"
            assert item.rows == () and item.regret is None and item.gates is None

    # The oracle is the post-hoc bound, so no method may out-score it and it regrets nothing.
    oracle = result.policy("ORACLE")
    assert oracle.regret is not None and oracle.regret.total_regret == pytest.approx(0.0)
