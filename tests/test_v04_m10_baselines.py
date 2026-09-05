"""The protocol's eleven comparators: all present, and each one doing its own job.

Protocol §8.1 lists eleven methods and §8.2 requires every one of them to stay in the final
report. The first test here is the one that keeps that true as milestones land: it walks the
registry, runs everything that can run, and pins the set that cannot to exactly ``{M7, M8,
M9}``. Wiring M7 without removing its placeholder, or quietly dropping M4 because it is
awkward, turns that test red.

The other two are the mutations a reviewer cannot see by reading. **M0 on the union.** The
strongest fixed configuration is an argmax, and an argmax scored on the data that chose it is
biased upward; the grid here is built so the selection-split winner and the union winner are
*different configuration ids*, not two floats that happen to differ. **The oracle in
production.** §8.3 makes the post-hoc bound benchmark-only, so it is asked to select under a
live execution source and under a replay that withheld the outcomes, and it must refuse both.

Everything is hand-built and offline. Not one test here loads the shipped corpus: a
comparator that only behaves on the corpus it was tuned against has not been tested, and a
grid written out in a fixture is a grid a reviewer can check by eye.
"""

from __future__ import annotations

import pytest

from accretion.contracts import (
    AuthMode,
    BenchmarkExecutionSource,
    ExecutionMode,
    Provider,
    RuntimeHealth,
    RuntimeStatus,
    UsagePressure,
)
from accretion.routing.baselines import (
    BASELINE_ORDER,
    BASELINE_REGISTRY,
    BaselineError,
    BaselinePolicy,
    BenchmarkCandidate,
    BenchmarkContext,
    NotAvailablePolicy,
    OracleOutsideReplay,
    PerRunPolicy,
    PolicyNotAvailable,
    StrongestFixedPolicy,
    UnknownBaseline,
    baseline_for,
)

NODE_CLASS = "IMPLEMENTATION"

CANDIDATES: tuple[BenchmarkCandidate, ...] = (
    BenchmarkCandidate(
        candidate_id="cfg-alpha",
        provider=Provider.CLAUDE,
        runtime_id="claude-cli",
        runtime_version="2.4.0",
        model_id="opus",
        tool_profile="full",
        declared_cost=0.60,
        declared_latency_ms=40_000,
        predicted_success=0.85,
        eligible_node_classes=frozenset({"IMPLEMENTATION", "ANALYSIS"}),
    ),
    BenchmarkCandidate(
        candidate_id="cfg-beta",
        provider=Provider.CODEX,
        runtime_id="codex-cli",
        runtime_version="1.9.0",
        model_id="codex",
        tool_profile="standard",
        declared_cost=0.30,
        declared_latency_ms=25_000,
        predicted_success=0.70,
        eligible_node_classes=frozenset({"IMPLEMENTATION"}),
    ),
    BenchmarkCandidate(
        candidate_id="cfg-gamma",
        provider=Provider.OPENCODE,
        runtime_id="opencode-cli",
        runtime_version="0.7.0",
        model_id="local",
        tool_profile="local",
        declared_cost=0.05,
        declared_latency_ms=50_000,
        predicted_success=0.40,
        eligible_node_classes=frozenset({"ANALYSIS"}),
    ),
)
"""Three configurations with three different eligibility profiles. ``cfg-gamma`` is the
cheapest and is *not* admissible for an implementation node, which is what makes the M1 test
a test rather than a restatement of ``min``."""


def build_grid() -> tuple[dict[str, dict[str, int]], tuple[str, ...], tuple[str, ...]]:
    """A binary grid whose selection-split winner is not its union winner.

    Six selection tasks and six evaluation tasks. ``cfg-alpha`` takes five of the six
    selection tasks and one of the six evaluation tasks; ``cfg-beta`` takes two of the
    selection tasks and every evaluation task. So the selection argmax is ``cfg-alpha``
    (5 > 2) while the argmax over the union is ``cfg-beta`` (8 > 6) — two different ids, so a
    policy that picked on the union returns the wrong *name* and not a slightly wrong number.
    """

    selection = tuple(f"sel-{index}" for index in range(6))
    evaluation = tuple(f"eval-{index}" for index in range(6))
    grid = {
        "cfg-alpha": {
            **{task_id: int(index < 5) for index, task_id in enumerate(selection)},
            **{task_id: int(index < 1) for index, task_id in enumerate(evaluation)},
        },
        "cfg-beta": {
            **{task_id: int(index < 2) for index, task_id in enumerate(selection)},
            **{task_id: 1 for task_id in evaluation},
        },
        "cfg-gamma": {task_id: 0 for task_id in (*selection, *evaluation)},
    }
    return grid, selection, evaluation


def build_context(
    *,
    task_id: str = "task-1",
    project_id: str = "prj-one",
    run_id: str = "run-one",
    node_class: str = NODE_CLASS,
    execution_source: BenchmarkExecutionSource = BenchmarkExecutionSource.REPLAY,
    strategy_decision: ExecutionMode = ExecutionMode.GRAPH,
    planner_choice: str = "cfg-beta",
    observed_utility: dict[str, float] | None = None,
    oracle_candidate_subset: tuple[str, ...] = ("cfg-alpha", "cfg-beta"),
    runtime_health: tuple[RuntimeHealth, ...] = (),
    performance_scores: dict[str, float] | None = None,
    predicted_success: dict[str, float] | None = None,
) -> BenchmarkContext:
    """One context, with the corpus-declared blocks filled in from :func:`build_grid`."""

    grid, selection, evaluation = build_grid()
    return BenchmarkContext(
        task_id=task_id,
        project_id=project_id,
        run_id=run_id,
        node_class=node_class,
        execution_source=execution_source,
        strategy_decision=strategy_decision,
        planner_choice=planner_choice,
        deterministic_v01_table={
            ExecutionMode.DIRECT: "cfg-gamma",
            ExecutionMode.LOOP: "cfg-beta",
            ExecutionMode.GRAPH: "cfg-beta",
            ExecutionMode.HYBRID: "cfg-alpha",
        },
        predicted_success=predicted_success
        or {"cfg-alpha": 0.4, "cfg-beta": 0.9, "cfg-gamma": 0.2},
        performance_scores=performance_scores
        or {"cfg-alpha": 0.9, "cfg-beta": 0.5, "cfg-gamma": 0.1},
        runtime_health=runtime_health,
        historical_quality={"CLAUDE|2.4.0": 0.9, "CODEX|1.9.0": 0.4, "OPENCODE|0.7.0": 0.2},
        selection_task_ids=selection,
        evaluation_task_ids=evaluation,
        fixed_baseline_outcomes=grid,
        oracle_candidate_subset=oracle_candidate_subset,
        observed_utility=observed_utility,
    )


def health(
    provider: Provider, runtime_id: str, version: str, status: RuntimeStatus
) -> RuntimeHealth:
    """One runtime-health row for the M3 comparator's real-router path."""

    return RuntimeHealth(
        runtime_id=runtime_id,
        provider=provider,
        status=status,
        auth_mode=AuthMode.SUBSCRIPTION,
        runtime_version=version,
        observed_usage_pressure=UsagePressure.LOW,
    )


def test_every_protocol_baseline_is_registered_and_runs_or_reports_not_available() -> None:
    assert tuple(BASELINE_REGISTRY) == BASELINE_ORDER
    assert BASELINE_ORDER == ("M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "ORACLE")

    context = build_context(observed_utility={"cfg-alpha": 0.5, "cfg-beta": 0.8})
    known_ids = {candidate.candidate_id for candidate in CANDIDATES}
    unavailable: list[str] = []
    selected: dict[str, str] = {}
    for policy_id in BASELINE_ORDER:
        registered = BASELINE_REGISTRY[policy_id]
        assert isinstance(registered, BaselinePolicy)
        assert registered.policy_id == policy_id
        policy = baseline_for(policy_id)
        assert policy is not registered, "a run must never share a stateful policy instance"
        try:
            selection = policy.select(context, CANDIDATES)
        except PolicyNotAvailable as error:
            assert error.reason_code == "NOT_AVAILABLE"
            assert isinstance(BASELINE_REGISTRY[policy_id], NotAvailablePolicy)
            unavailable.append(policy_id)
            continue
        selected[policy_id] = selection.candidate_id
        assert selection.candidate_id in known_ids

    assert unavailable == ["M7", "M8", "M9"]
    assert set(selected) == {"M0", "M1", "M2", "M3", "M4", "M5", "M6", "ORACLE"}
    # The eight runnable methods are eight methods and not one method eight times.
    assert len(set(selected.values())) > 1

    with pytest.raises(UnknownBaseline):
        baseline_for("M10")


def test_strongest_fixed_is_chosen_on_the_selection_split_only() -> None:
    grid, selection, evaluation = build_grid()
    selection_rates = {
        config_id: sum(row[task_id] for task_id in selection) for config_id, row in grid.items()
    }
    union_rates = {
        config_id: sum(row[task_id] for task_id in (*selection, *evaluation))
        for config_id, row in grid.items()
    }
    # The premise of the test: the two argmaxes disagree, and disagree by name.
    assert max(selection_rates, key=lambda key: selection_rates[key]) == "cfg-alpha"
    assert max(union_rates, key=lambda key: union_rates[key]) == "cfg-beta"

    policy = StrongestFixedPolicy()
    context = build_context()
    assert policy.select(context, CANDIDATES).candidate_id == "cfg-alpha"

    best = policy.best_fixed(context)
    assert best.config_id == "cfg-alpha"
    # The optimistic number and the honest one are both carried, and they differ: 5/6 on the
    # data that chose it, 1/6 on the data that scored it. That gap is the winner's curse.
    assert best.selection_rate == pytest.approx(5 / 6)
    assert best.evaluation_rate == pytest.approx(1 / 6)

    empty = build_context()
    stripped = BenchmarkContext(
        task_id=empty.task_id,
        project_id=empty.project_id,
        run_id=empty.run_id,
        node_class=empty.node_class,
        execution_source=empty.execution_source,
        strategy_decision=empty.strategy_decision,
        planner_choice=empty.planner_choice,
    )
    with pytest.raises(BaselineError):
        policy.select(stripped, CANDIDATES)


def test_oracle_refuses_outside_replay() -> None:
    policy = baseline_for("ORACLE")
    observed = {"cfg-alpha": 0.5, "cfg-beta": 0.8, "cfg-gamma": 0.95}

    live = build_context(
        execution_source=BenchmarkExecutionSource.LIVE, observed_utility=observed
    )
    with pytest.raises(OracleOutsideReplay) as refusal:
        policy.select(live, CANDIDATES)
    assert "LIVE" in str(refusal.value)

    # A replay that withheld the outcomes is refused too: the oracle never guesses, and the
    # runner withholds them from every policy but this one.
    blind = build_context(observed_utility=None)
    with pytest.raises(OracleOutsideReplay):
        policy.select(blind, CANDIDATES)

    # Under replay, with outcomes, it takes the best *inside the registered subset* —
    # `cfg-gamma` scores highest and is outside it, so §8.3's bound ignores it.
    replayed = build_context(observed_utility=observed)
    selection = policy.select(replayed, CANDIDATES)
    assert selection.candidate_id == "cfg-beta"
    assert selection.propensity is None, "a post-hoc bound has no behaviour propensity"


def test_the_deterministic_v01_policy_reads_the_table_the_corpus_declares() -> None:
    policy = baseline_for("M2")
    assert policy.select(build_context(strategy_decision=ExecutionMode.HYBRID), CANDIDATES) \
        .candidate_id == "cfg-alpha"
    assert policy.select(build_context(strategy_decision=ExecutionMode.DIRECT), CANDIDATES) \
        .candidate_id == "cfg-gamma"

    bare = BenchmarkContext(
        task_id="task-1",
        project_id="prj-one",
        run_id="run-one",
        node_class=NODE_CLASS,
        execution_source=BenchmarkExecutionSource.REPLAY,
        strategy_decision=ExecutionMode.LOOP,
        planner_choice="cfg-beta",
    )
    with pytest.raises(BaselineError):
        policy.select(bare, CANDIDATES)


def test_the_cheapest_valid_policy_will_not_buy_a_configuration_the_node_refuses() -> None:
    policy = baseline_for("M1")
    cheapest_overall = min(CANDIDATES, key=lambda candidate: candidate.declared_cost)
    assert cheapest_overall.candidate_id == "cfg-gamma"
    assert not cheapest_overall.serves(NODE_CLASS)

    chosen = policy.select(build_context(), CANDIDATES).candidate_id
    assert chosen == "cfg-beta", "the cheapest *admissible* configuration, not the cheapest"

    # On an analysis node the same policy does take the cheap one, because there it is valid.
    assert policy.select(build_context(node_class="ANALYSIS"), CANDIDATES).candidate_id \
        == "cfg-gamma"


def test_the_per_run_policy_holds_one_choice_for_every_node_in_its_run() -> None:
    policy = PerRunPolicy()
    first = policy.select(build_context(task_id="task-1", run_id="run-a"), CANDIDATES)
    # A second node in the same run whose declared scores now favour something else.
    second = policy.select(
        build_context(
            task_id="task-2",
            run_id="run-a",
            performance_scores={"cfg-alpha": 0.1, "cfg-beta": 0.99, "cfg-gamma": 0.2},
        ),
        CANDIDATES,
    )
    assert second == first, "M4 decides once per run and holds it"

    other_run = policy.select(
        build_context(
            task_id="task-3",
            run_id="run-b",
            performance_scores={"cfg-alpha": 0.1, "cfg-beta": 0.99, "cfg-gamma": 0.2},
        ),
        CANDIDATES,
    )
    assert other_run.candidate_id == "cfg-beta", "a different run is a different decision"

    policy.reset()
    reconsidered = policy.select(
        build_context(
            task_id="task-4",
            run_id="run-a",
            performance_scores={"cfg-alpha": 0.1, "cfg-beta": 0.99, "cfg-gamma": 0.2},
        ),
        CANDIDATES,
    )
    assert reconsidered.candidate_id == "cfg-beta"


def test_the_performance_aware_policy_runs_the_real_v02_router_when_health_exists() -> None:
    policy = baseline_for("M3")
    # Without health rows it falls back to the declared scores, which favour cfg-alpha.
    assert policy.select(build_context(), CANDIDATES).candidate_id == "cfg-alpha"

    # With health rows the v0.2 router runs for real, and CLAUDE being unavailable moves the
    # choice to a CODEX-bound configuration. Nothing here restates the router's arithmetic:
    # the assertion is about which provider was excluded, which is the router's own rule.
    unavailable_claude = (
        health(Provider.CLAUDE, "claude-cli", "2.4.0", RuntimeStatus.UNAVAILABLE),
        health(Provider.CODEX, "codex-cli", "1.9.0", RuntimeStatus.READY),
        health(Provider.OPENCODE, "opencode-cli", "0.7.0", RuntimeStatus.READY),
    )
    chosen = policy.select(build_context(runtime_health=unavailable_claude), CANDIDATES)
    assert chosen.candidate_id == "cfg-beta"
