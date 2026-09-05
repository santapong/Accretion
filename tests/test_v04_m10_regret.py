"""Regret: priced from stored records, and never quietly from the object that computed it.

Two claims, and both of them are about trust rather than arithmetic.

**The number survives the runner.** The benchmark reports a regret figure; an auditor has a
store and a corpus. So the test runs a policy, emits the receipts and candidates that run
would have written, persists them into a fresh :class:`MemoryStore`, **drops the runner**,
and recomputes the whole report from what came back out of the store plus a freshly loaded
corpus. Equality is asserted on the entire report — every row, both totals, the per-project
breakdown and the safety counters — not on a summary statistic that several different reports
could share. Two guards keep that from passing vacuously: a second policy's report must
differ from the first, and re-pointing a single stored receipt at a different configuration
must change exactly the row it names. An implementation that read anything from a cached
object rather than from the receipt it was handed fails the second one.

**An invalid selection costs and counts.** Selecting a configuration the node was never
eligible for is priced at the registered penalty *and* increments
``safety.invalid_selections``. The test asserts both, separately, and asserts that a valid
selection increments neither — so dropping the counter is red even though every utility in
the report stays right.

Everything is offline: one in-memory store, one corpus read from disk, no clock and no
network.
"""

from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path

import pytest

from accretion.contracts import Project
from accretion.contracts.routing import UtilityWeights
from accretion.persistence.store import MemoryStore
from accretion.router_benchmark import (
    BENCHMARK_WORKSPACE_ID,
    BenchmarkSplit,
    RouterBenchmarkCorpus,
    RouterBenchmarkRunner,
    StoredDecisions,
    configuration_id_for,
)
from accretion.routing.regret import (
    Outcome,
    RegretReport,
    RegretRow,
    SafetyCounters,
    TaskSelection,
    constrained_regret,
    outcome_key,
    regret_from_receipts,
    regret_over_selections,
    utility,
)

WEIGHTS = UtilityWeights(quality=1.0, cost=0.30, latency=0.15)
PENALTY = 1.0


def outcome(
    quality: float,
    cost: float,
    latency: float,
    *,
    verified: bool = True,
    false_accept: bool = False,
    invalid: bool = False,
) -> Outcome:
    """One observed cell, with the raw quantities utility is priced from."""

    return Outcome(
        quality=quality,
        cost=cost,
        latency=latency,
        latency_ms=int(latency * 60_000),
        verified=verified,
        false_accept=false_accept,
        invalid=invalid,
    )


async def setup_cold_store(decisions: StoredDecisions) -> MemoryStore:
    """A fresh store holding one policy's emitted candidates and receipts, and nothing else.

    The corpus's projects are seeded first because every v0.4 table carries
    ``project_id -> projects.id`` and ``MemoryStore`` mirrors that foreign key: a store
    without them would refuse the rows exactly as PostgreSQL does, and the test would be
    testing the key rather than the arithmetic.
    """

    store = MemoryStore()
    for project_id in sorted({candidate.project_id or "" for candidate in decisions.candidates}):
        await store.create_project(
            Project(
                project_id=project_id,
                name=project_id,
                repository_path=Path("/tmp/accretion-router-benchmark") / project_id,
            )
        )
    for candidate in decisions.candidates:
        await store.put_configuration_candidate(candidate)
    for receipt in decisions.receipts:
        await store.put_routing_receipt(receipt)
    return store


def as_stored(report: RegretReport) -> tuple[RegretRow, ...]:
    """The runner's rows renamed into the ``cfg_`` ids a stored receipt uses.

    The corpus names configurations ``cnf-...`` and a persisted receipt names them by contract
    id. :func:`configuration_id_for` is the only mapping between the two and it is a pure
    function, so translating here compares the ids as well as the numbers rather than
    quietly excluding them from the comparison.
    """

    return tuple(
        replace(
            row,
            selected_candidate_id=configuration_id_for(row.selected_candidate_id),
            oracle_candidate_id=configuration_id_for(row.oracle_candidate_id),
        )
        for row in report.rows
    )


async def test_regret_is_recomputed_identically_from_a_cold_store() -> None:
    runner = RouterBenchmarkRunner()
    result = runner.run(["M1", "M6"], split=BenchmarkSplit.EVALUATION)
    expected = result.policy("M1").regret
    other = result.policy("M6").regret
    assert expected is not None and other is not None
    # Non-vacuity: the two policies really do produce different reports, so an equality that
    # matched "any report" would not pass below.
    assert expected != other
    assert expected.total_regret != other.total_regret

    store = await setup_cold_store(runner.stored_decisions(result.policy("M1")))
    other_decisions = runner.stored_decisions(result.policy("M6"))
    del runner, result

    receipts = await store.list_routing_receipts(workspace_id=BENCHMARK_WORKSPACE_ID)
    candidates = await store.list_configuration_candidates(workspace_id=BENCHMARK_WORKSPACE_ID)
    corpus = RouterBenchmarkCorpus.load()
    recomputed = regret_from_receipts(
        receipts=receipts,
        candidates=candidates,
        outcomes=corpus.stored_outcomes(),
        weights=corpus.config.weights,
        invalid_penalty=corpus.config.invalid_action_penalty,
    )

    assert recomputed.rows == as_stored(expected)
    assert recomputed.total_regret == expected.total_regret
    assert recomputed.mean_regret == expected.mean_regret
    assert dict(recomputed.regret_by_project) == dict(expected.regret_by_project)
    assert recomputed.safety == expected.safety

    # A second cold store holding a different policy's receipts over the same tasks must
    # recompute that policy's report, not anything left over from the first call: an
    # implementation that cached a selection per task would hand M1's rows back here.
    other_store = await setup_cold_store(other_decisions)
    other_recomputed = regret_from_receipts(
        receipts=await other_store.list_routing_receipts(workspace_id=BENCHMARK_WORKSPACE_ID),
        candidates=await other_store.list_configuration_candidates(
            workspace_id=BENCHMARK_WORKSPACE_ID
        ),
        outcomes=corpus.stored_outcomes(),
        weights=corpus.config.weights,
        invalid_penalty=corpus.config.invalid_action_penalty,
    )
    assert other_recomputed.rows == as_stored(other)
    assert other_recomputed.rows != recomputed.rows
    assert other_recomputed.total_regret == other.total_regret


async def test_a_tampered_receipt_changes_the_recomputed_regret_for_the_row_it_names() -> None:
    runner = RouterBenchmarkRunner()
    result = runner.run(["M1"], split=BenchmarkSplit.EVALUATION)
    decisions = runner.stored_decisions(result.policy("M1"))
    corpus = runner.corpus

    target = decisions.receipts[0]
    admissible = sorted(
        candidate.configuration.contract_id
        for candidate in decisions.candidates
        if candidate.routing_request_id == target.routing_request_id and candidate.hard_eligible
    )
    replacement = next(
        candidate_id
        for candidate_id in admissible
        if candidate_id != target.selected_configuration_id
    )
    payload = target.model_dump(mode="python")
    payload["selected_configuration_id"] = replacement
    payload["content_hash"] = ""
    tampered = type(target).model_validate(payload)

    store = await setup_cold_store(
        StoredDecisions(
            policy_id="M1",
            receipts=(tampered, *decisions.receipts[1:]),
            candidates=decisions.candidates,
        )
    )
    recomputed = regret_from_receipts(
        receipts=await store.list_routing_receipts(workspace_id=BENCHMARK_WORKSPACE_ID),
        candidates=await store.list_configuration_candidates(
            workspace_id=BENCHMARK_WORKSPACE_ID
        ),
        outcomes=corpus.stored_outcomes(),
        weights=corpus.config.weights,
        invalid_penalty=corpus.config.invalid_action_penalty,
    )

    honest = {row.task_id: row for row in as_stored(result.policy("M1").regret or _never())}
    changed = {row.task_id: row for row in recomputed.rows}
    assert changed[target.routing_request_id] != honest[target.routing_request_id]
    assert changed[target.routing_request_id].selected_candidate_id == replacement
    untouched = set(honest) - {target.routing_request_id}
    assert all(changed[task_id] == honest[task_id] for task_id in untouched)


def _never() -> RegretReport:  # pragma: no cover - narrows an Optional the runner always fills
    raise AssertionError("the M1 policy always produces a regret report")


def test_an_invalid_selection_takes_the_penalty_and_counts_as_a_safety_event() -> None:
    admissible = {
        "cfg-alpha": outcome(0.90, 0.60, 0.50),
        "cfg-beta": outcome(0.70, 0.20, 0.20),
    }
    oracle_utility = utility(admissible["cfg-alpha"], WEIGHTS)
    assert oracle_utility > utility(admissible["cfg-beta"], WEIGHTS)

    invalid_row = constrained_regret(
        admissible,
        "cfg-gamma",
        tuple(admissible),
        WEIGHTS,
        PENALTY,
        task_id="task-1",
        project_id="prj-one",
    )
    assert invalid_row.invalid is True
    assert invalid_row.selected_utility == pytest.approx(-PENALTY)
    assert invalid_row.regret == pytest.approx(oracle_utility + PENALTY)
    assert invalid_row.oracle_candidate_id == "cfg-alpha"

    valid_row = constrained_regret(
        admissible,
        "cfg-beta",
        tuple(admissible),
        WEIGHTS,
        PENALTY,
        task_id="task-2",
        project_id="prj-one",
    )
    assert valid_row.invalid is False
    assert valid_row.selected_utility == pytest.approx(utility(admissible["cfg-beta"], WEIGHTS))

    def selection(task_id: str, selected: str) -> TaskSelection:
        return TaskSelection(
            task_id=task_id,
            project_id="prj-one",
            selected_id=selected,
            eligible_ids=tuple(admissible),
            oracle_subset_ids=tuple(admissible),
            task_outcomes=admissible,
            selected_outcome=admissible.get(selected),
        )

    only_valid = regret_over_selections([selection("task-2", "cfg-beta")], WEIGHTS, PENALTY)
    assert only_valid.safety == SafetyCounters()

    both = regret_over_selections(
        [selection("task-1", "cfg-gamma"), selection("task-2", "cfg-beta")], WEIGHTS, PENALTY
    )
    assert both.safety.invalid_selections == 1
    assert both.safety.unverified_selections == 1, "the invalid choice verified nothing"
    assert both.safety.false_acceptances == 0
    assert both.total_regret == pytest.approx(invalid_row.regret + valid_row.regret)

    # And on the shipped corpus the two stay in step: every priced invalid row is counted.
    report = RouterBenchmarkRunner().run(["M2"]).policy("M2").regret
    assert report is not None
    priced = [row for row in report.rows if row.invalid]
    assert priced, "the corpus must exercise the invalid path at all"
    assert report.safety.invalid_selections == len(priced)
    assert all(row.selected_utility == pytest.approx(-PENALTY) for row in priced)


def test_utility_prices_cost_and_latency_against_quality_and_keeps_the_raw_numbers() -> None:
    cell = outcome(0.80, 0.40, 0.20)
    assert utility(cell, WEIGHTS) == pytest.approx(0.80 - 0.30 * 0.40 - 0.15 * 0.20)

    # An objective that stops caring about cost values the expensive cell more highly, which
    # is the only thing the weight vector is allowed to change.
    indifferent = UtilityWeights(quality=1.0, cost=0.0, latency=0.15)
    assert utility(cell, indifferent) > utility(cell, WEIGHTS)

    row = constrained_regret(
        {"cfg-alpha": cell},
        "cfg-alpha",
        ("cfg-alpha",),
        WEIGHTS,
        PENALTY,
        task_id="task-1",
        project_id="prj-one",
    )
    assert (row.quality, row.cost, row.latency) == (0.80, 0.40, 0.20)
    # The verdict fields belong to the gates, and a regret row must not be able to carry them
    # into the utility column by accident.
    names = {field.name for field in fields(RegretRow)}
    assert names.isdisjoint({"verified", "false_accept", "verified_success_rate"})


def test_a_receipt_that_selected_nothing_is_counted_rather_than_dropped() -> None:
    admissible = {"cfg-alpha": outcome(0.90, 0.60, 0.50)}
    report = regret_over_selections(
        [
            TaskSelection(
                task_id="task-1",
                project_id="prj-one",
                selected_id=None,
                eligible_ids=("cfg-alpha",),
                oracle_subset_ids=("cfg-alpha",),
                task_outcomes=admissible,
                selected_outcome=None,
            )
        ],
        WEIGHTS,
        PENALTY,
    )
    assert report.rows == ()
    assert report.safety.deferred_to_human == 1
    assert report.safety.invalid_selections == 0


def test_an_outcome_key_survives_a_task_id_that_contains_the_separator() -> None:
    # Two different cells that a naive "<task>::<config>" encoding would merge into one.
    assert outcome_key("prj-one::node", "cfg-alpha") != outcome_key(
        "prj-one", "node::cfg-alpha"
    )
    assert outcome_key("task-1", "cfg-alpha") == outcome_key("task-1", "cfg-alpha")
    assert outcome_key("task-1", "cfg-alpha") != outcome_key("task-10", "cfg-alpha")
