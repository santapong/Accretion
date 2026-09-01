"""Inherited v0.2 P6 search proofs (V02-P6-005, V02-P6-009).

Both criteria describe behaviour that already exists; neither was claimed by a
marked test, which is why the acceptance baseline lists them as uncovered.

`V02-P6-005` names six ways a search must stop. Each test below provokes one of
them through the real `SearchService` and then asserts on the **persisted**
record read back via `store.get_search`, not on the value the call returned, so
a stop reason that is computed but never durably recorded cannot pass. Where the
service emits `SEARCH_STOPPED` the event payload is checked too. The reasons are
finally asserted pairwise-distinct, so a service that collapsed every stop into
one reason would fail even though each individual assertion still held.

`V02-P6-009` pins the N=1,2,4 curve. `tests/test_p6_search_benchmark.py` already
pins the fixture digests but claims no criterion; this file adds the part that
makes the curve an actual measurement — every published point is asserted
literally, and each is shown to be *derived* by perturbing one trace in a
`tmp_path` copy of the corpus and asserting that the expected point moves while
the others hold still.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from test_p6_search_contracts import budget, prepared_run

from accretion.contracts import (
    EventType,
    Provider,
    SessionRef,
    TaskBudgets,
    VerificationStatus,
    WorkspaceLease,
)
from accretion.orchestration.models import (
    CandidateScore,
    CandidateStatus,
    CandidateTrajectory,
    SearchBudgetEnvelope,
    SearchMode,
    SearchRecord,
    SearchStatus,
    SearchStopReason,
)
from accretion.orchestration.search import SearchService
from accretion.runtimes.common import RuntimeSubmission
from accretion.runtimes.fake import FakeCallOutcome, FakeRuntime
from accretion.search_benchmark import SearchBenchmarkRunner
from accretion.services.run_manager import RunManager

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def scratch(tmp_path: Path, name: str) -> Path:
    """`prepared_run` creates `<base>/repository`, so the base must exist first."""
    base = tmp_path / name
    base.mkdir(parents=True, exist_ok=True)
    return base


def writes(value: str):
    """A fake-runtime hook that writes `value` as the required candidate output."""

    def hook(session: SessionRef, _request: RuntimeSubmission) -> None:
        (session.workspace / "candidate.txt").write_text(value)

    return hook


async def run_search_to_completion(
    manager: RunManager,
    dynamic,  # DynamicWorkflowService
    search: SearchService,
    run_id: str,
    *,
    per_branch: SearchBudgetEnvelope | None = None,
    total: SearchBudgetEnvelope | None = None,
    charge_after_plan: tuple[int, int] | None = None,
) -> SearchRecord:
    """Plan a best-of-2 search, activate the graph, and await the background run.

    `charge_after_plan` spends (turns, tool_calls) against the run *after* the
    plan is admitted. `create_plan` refuses a plan the remaining budget cannot
    fund, so the exhausted-at-execution path is only reachable when a sibling
    consumes the shared budget between planning and execution — which is
    exactly the race the BUDGET_EXHAUSTED guard exists to catch.
    """
    record = await search.create_plan(
        run_id,
        parent_node_id="act",
        mode=SearchMode.BEST_OF_N,
        branch_count=2,
        max_parallel=2,
        per_branch_budget=per_branch or budget(),
        total_budget=total or budget(wall=240, turns=8, tools=24),
        candidate_directives=[],
    )
    if charge_after_plan is not None:
        turns, tool_calls = charge_after_plan
        await manager.store.add_budget_spent(run_id, turns=turns, tool_calls=tool_calls)
    proposal = (await manager.store.list_workflow_proposals(run_id=run_id))[-1]
    await dynamic.activate(run_id, proposal.proposal_id)
    background = manager.background.get(run_id)
    if background is not None:
        await background
    return record


async def persisted_stop_reason(manager: RunManager, search_id: str) -> SearchStopReason | None:
    """Read the stop reason back out of the store rather than trusting a return value."""
    stored = await manager.store.get_search(search_id)
    assert stored is not None, f"search {search_id} was never persisted"
    return stored.stop_reason


async def stopped_event_payloads(manager: RunManager, run_id: str) -> list[dict[str, object]]:
    events = await manager.store.list_events(run_id)
    return [
        event.payload
        for event in events
        if event.normalized_type is EventType.SEARCH_STOPPED
    ]


# ---------------------------------------------------------------------------
# V02-P6-005 — the six stop reasons
# ---------------------------------------------------------------------------


@pytest.mark.acceptance("V02-P6-005")
async def test_search_stops_on_acceptance_and_records_the_selected_candidate(
    tmp_path: Path,
) -> None:
    """Acceptance: two distinct candidates, a clear winner, promotion succeeds."""
    runtime = FakeRuntime(
        scripted_outcomes=[
            # Writes no output at all, so it misses the required output and is
            # deterministically ineligible. It must NOT write an empty file:
            # `capture_diff` returns None for an empty diff, which would leave
            # an *eligible* candidate with `patch_sha256 = None` and trip the
            # LOW_DIVERSITY guard instead of promoting the winner.
            FakeCallOutcome(),
            FakeCallOutcome(hook=writes("a materially better candidate")),
        ]
    )
    manager, dynamic, search, run_id = await prepared_run(tmp_path, runtime=runtime)
    record = await run_search_to_completion(manager, dynamic, search, run_id)

    stored = await manager.store.get_search(record.plan.search_id)
    assert stored is not None
    assert stored.status is SearchStatus.SUCCEEDED
    assert stored.stop_reason is SearchStopReason.ACCEPTED
    assert stored.selected_candidate_id is not None
    assert stored.completed_at is not None

    # Pin the state this outcome depends on, so the LOW_DIVERSITY flake cannot
    # come back silently: exactly one candidate is eligible, and it is the one
    # that produced an artifact. A second eligible candidate carrying a null
    # patch hash is what used to divert this run to LOW_DIVERSITY.
    candidates = await manager.store.list_search_candidates(record.plan.search_id)
    scores = await manager.store.list_candidate_scores(record.plan.search_id)
    assert len(candidates) == 2
    eligible = [score for score in scores if score.eligible]
    assert len(eligible) == 1, (
        "exactly one candidate may be eligible; two eligible with one artifact "
        "trips the LOW_DIVERSITY guard"
    )
    winner = next(c for c in candidates if c.candidate_id == stored.selected_candidate_id)
    assert winner.patch_sha256 is not None
    assert eligible[0].candidate_id == winner.candidate_id

    payloads = await stopped_event_payloads(manager, run_id)
    assert [item["stop_reason"] for item in payloads] == ["ACCEPTED"]
    assert payloads[0]["selected_candidate_id"] == stored.selected_candidate_id


@pytest.mark.acceptance("V02-P6-005")
async def test_search_stops_when_the_run_budget_is_already_exhausted(tmp_path: Path) -> None:
    """Budget: the shared run budget is spent before the search opens its first wave."""
    manager, dynamic, search, run_id = await prepared_run(
        tmp_path,
        task_budgets=TaskBudgets(max_parallel_runs=2, max_turns=4, max_tool_calls=8),
    )
    record = await run_search_to_completion(
        manager,
        dynamic,
        search,
        run_id,
        per_branch=budget(wall=60, turns=2, tools=4),
        total=budget(wall=120, turns=4, tools=8),
        # Charged through the same store API the runtime path uses, so the
        # search sees a genuinely consumed budget, not a doctored record.
        charge_after_plan=(4, 8),
    )

    stored = await manager.store.get_search(record.plan.search_id)
    assert stored is not None
    assert stored.status is SearchStatus.STOPPED
    assert stored.stop_reason is SearchStopReason.BUDGET_EXHAUSTED
    assert stored.selected_candidate_id is None
    # No candidate may be launched once the budget is gone.
    assert await manager.store.get_search_promotion(record.plan.search_id) is None

    payloads = await stopped_event_payloads(manager, run_id)
    assert [item["stop_reason"] for item in payloads] == ["BUDGET_EXHAUSTED"]


@pytest.mark.acceptance("V02-P6-005")
async def test_search_stops_for_low_diversity_when_candidates_are_byte_identical(
    tmp_path: Path,
) -> None:
    """Low diversity: both branches verify, but they produce the same patch."""
    runtime = FakeRuntime(
        scripted_outcomes=[
            FakeCallOutcome(hook=writes("identical")),
            FakeCallOutcome(hook=writes("identical")),
        ]
    )
    manager, dynamic, search, run_id = await prepared_run(tmp_path, runtime=runtime)
    record = await run_search_to_completion(manager, dynamic, search, run_id)

    stored = await manager.store.get_search(record.plan.search_id)
    assert stored is not None
    assert stored.status is SearchStatus.STOPPED
    assert stored.stop_reason is SearchStopReason.LOW_DIVERSITY
    assert stored.selected_candidate_id is None

    # The reason must be diversity, not failure: both candidates really did run
    # and really did produce a patch.
    candidates = await manager.store.list_search_candidates(record.plan.search_id)
    hashes = {item.patch_sha256 for item in candidates if item.patch_sha256 is not None}
    assert len(candidates) == 2
    assert len(hashes) == 1

    payloads = await stopped_event_payloads(manager, run_id)
    assert [item["stop_reason"] for item in payloads] == ["LOW_DIVERSITY"]


@pytest.mark.acceptance("V02-P6-005")
async def test_operator_cancellation_persists_before_the_runtimes_are_interrupted(
    tmp_path: Path,
) -> None:
    """Operator cancellation: the reason is durable even though no event is emitted."""
    manager, _dynamic, search, run_id = await prepared_run(tmp_path)
    record = await search.create_plan(
        run_id,
        parent_node_id="act",
        mode=SearchMode.BEST_OF_N,
        branch_count=2,
        max_parallel=2,
        per_branch_budget=budget(),
        total_budget=budget(wall=240, turns=8, tools=24),
        candidate_directives=[],
    )

    cancelled = await search.cancel(record.plan.search_id)
    assert cancelled.status is SearchStatus.CANCELLED

    stored = await manager.store.get_search(record.plan.search_id)
    assert stored is not None
    assert stored.status is SearchStatus.CANCELLED
    assert stored.stop_reason is SearchStopReason.OPERATOR_CANCELLED
    assert stored.completed_at is not None

    # Cancellation short-circuits `_stop()`, so it deliberately emits no
    # SEARCH_STOPPED event. Pin that, so the day it changes this test says so
    # rather than silently passing on a different contract.
    assert await stopped_event_payloads(manager, run_id) == []

    # Cancelling again is idempotent and does not rewrite the reason.
    again = await search.cancel(record.plan.search_id)
    assert again.status is SearchStatus.CANCELLED
    assert again.stop_reason is SearchStopReason.OPERATOR_CANCELLED
    assert again.revision == stored.revision


async def scored_selection(
    tmp_path: Path,
    *,
    scores: tuple[float, float],
    verifier_status: str = VerificationStatus.PASS.value,
    eligible: bool = True,
) -> tuple[RunManager, SearchService, SearchRecord]:
    """Drive `_select_and_promote` over two fully-scored, distinct candidates."""
    manager, _dynamic, search, run_id = await prepared_run(tmp_path)
    record = await search.create_plan(
        run_id,
        parent_node_id="act",
        mode=SearchMode.BEST_OF_N,
        branch_count=2,
        max_parallel=2,
        per_branch_budget=budget(),
        total_budget=budget(wall=240, turns=8, tools=24),
        candidate_directives=[],
    )
    record = await manager.store.update_search(
        record.model_copy(update={"status": SearchStatus.SELECTING}),
        expected_revision=record.revision,
    )
    for (index, character), score in zip(
        ((1, "a"), (2, "b")), scores, strict=True
    ):
        candidate = CandidateTrajectory(
            candidate_id=f"search_candidate_m8_{index}",
            search_id=record.plan.search_id,
            run_id=run_id,
            ordinal=index,
            provider=Provider.FAKE,
            runtime_id="runtime_fake",
            runtime_model="default",
            runtime_version="fake-p2-v1",
            status=CandidateStatus.COMPLETED,
            patch_sha256=character * 64,
        )
        await manager.store.save_search_candidate(candidate)
        await manager.store.save_candidate_score(
            CandidateScore(
                score_id=f"score_m8_{index}",
                search_id=record.plan.search_id,
                candidate_id=candidate.candidate_id,
                verifier_policy_ref=record.plan.verifier_policy_ref,
                verifier_status=verifier_status,
                eligible=eligible,
                quality_score=1,
                cost_proxy=0,
                latency_proxy=0,
                risk_score=0,
                total_score=score,
                explanation="M8 inherited-criteria selection fixture",
            )
        )
    run = await manager._require_run(run_id)
    task = await manager._require_task(run.task_id)
    policy = await manager._require_policy(run.acceptance_policy_id)
    lease = WorkspaceLease(
        lease_id="workspace_m8_selection",
        project_id=run.project_id,
        run_id=run_id,
        base_revision="fixture",
        path=tmp_path / "repository",
        branch_name="fixture",
    )
    await search._select_and_promote(record, run, task, lease, policy)
    return manager, search, record


@pytest.mark.acceptance("V02-P6-005")
async def test_search_stops_for_low_expected_gain_below_the_policy_threshold(
    tmp_path: Path,
) -> None:
    """Low expected gain: the winner beats the runner-up by less than the floor."""
    manager, _search, record = await scored_selection(tmp_path, scores=(0.9, 0.895))

    stored = await manager.store.get_search(record.plan.search_id)
    assert stored is not None
    assert stored.status is SearchStatus.STOPPED
    assert stored.stop_reason is SearchStopReason.LOW_EXPECTED_GAIN
    assert stored.selected_candidate_id is None

    # The margin really is below the declared floor, and non-zero — otherwise
    # this would be the VERIFIER_UNCERTAIN tie branch instead.
    gain = 0.9 - 0.895
    assert 0 < gain < record.plan.stop_policy.minimum_score_gain

    payloads = await stopped_event_payloads(manager, record.plan.run_id)
    assert [item["stop_reason"] for item in payloads] == ["LOW_EXPECTED_GAIN"]


@pytest.mark.acceptance("V02-P6-005")
async def test_search_requires_a_human_when_the_verifier_cannot_separate_candidates(
    tmp_path: Path,
) -> None:
    """Verifier uncertainty: an exact tie cannot be resolved by the selector."""
    manager, _search, record = await scored_selection(tmp_path, scores=(0.9, 0.9))

    stored = await manager.store.get_search(record.plan.search_id)
    assert stored is not None
    assert stored.status is SearchStatus.REQUIRES_HUMAN
    assert stored.stop_reason is SearchStopReason.VERIFIER_UNCERTAIN
    assert stored.selected_candidate_id is None
    assert await manager.store.get_search_promotion(record.plan.search_id) is None

    payloads = await stopped_event_payloads(manager, record.plan.run_id)
    assert [item["stop_reason"] for item in payloads] == ["VERIFIER_UNCERTAIN"]


@pytest.mark.acceptance("V02-P6-005")
async def test_an_inconclusive_verifier_is_uncertainty_not_candidate_failure(
    tmp_path: Path,
) -> None:
    """The two 'nothing eligible' branches must stay distinguishable."""
    manager, _search, record = await scored_selection(
        tmp_path,
        scores=(0.9, 0.8),
        verifier_status=VerificationStatus.INCONCLUSIVE.value,
        eligible=False,
    )
    stored = await manager.store.get_search(record.plan.search_id)
    assert stored is not None
    assert stored.status is SearchStatus.REQUIRES_HUMAN
    assert stored.stop_reason is SearchStopReason.VERIFIER_UNCERTAIN

    manager2, _search2, record2 = await scored_selection(
        scratch(tmp_path, "failure"),
        scores=(0.9, 0.8),
        verifier_status=VerificationStatus.FAIL.value,
        eligible=False,
    )
    stored2 = await manager2.store.get_search(record2.plan.search_id)
    assert stored2 is not None
    assert stored2.status is SearchStatus.STOPPED
    assert stored2.stop_reason is SearchStopReason.CANDIDATE_FAILURE


@pytest.mark.acceptance("V02-P6-005")
async def test_the_six_named_stop_reasons_are_pairwise_distinct(tmp_path: Path) -> None:
    """The criterion names six outcomes; a service that collapsed them would pass
    each test above in isolation. Provoke all six and assert they differ."""
    observed: dict[str, SearchStopReason] = {}

    accept_runtime = FakeRuntime(
        scripted_outcomes=[
            # Writes no output at all, so it misses the required output and is
            # deterministically ineligible. It must NOT write an empty file:
            # `capture_diff` returns None for an empty diff, which would leave
            # an *eligible* candidate with `patch_sha256 = None` and trip the
            # LOW_DIVERSITY guard instead of promoting the winner.
            FakeCallOutcome(),
            FakeCallOutcome(hook=writes("clearly better")),
        ]
    )
    manager, dynamic, search, run_id = await prepared_run(
        scratch(tmp_path, "accepted"), runtime=accept_runtime
    )
    record = await run_search_to_completion(manager, dynamic, search, run_id)
    reason = await persisted_stop_reason(manager, record.plan.search_id)
    assert reason is not None
    observed["acceptance"] = reason

    manager, dynamic, search, run_id = await prepared_run(
        scratch(tmp_path, "budget"),
        task_budgets=TaskBudgets(max_parallel_runs=2, max_turns=4, max_tool_calls=8),
    )
    record = await run_search_to_completion(
        manager,
        dynamic,
        search,
        run_id,
        per_branch=budget(wall=60, turns=2, tools=4),
        total=budget(wall=120, turns=4, tools=8),
        charge_after_plan=(4, 8),
    )
    reason = await persisted_stop_reason(manager, record.plan.search_id)
    assert reason is not None
    observed["budget"] = reason

    same_runtime = FakeRuntime(
        scripted_outcomes=[
            FakeCallOutcome(hook=writes("identical")),
            FakeCallOutcome(hook=writes("identical")),
        ]
    )
    manager, dynamic, search, run_id = await prepared_run(
        scratch(tmp_path, "diversity"), runtime=same_runtime
    )
    record = await run_search_to_completion(manager, dynamic, search, run_id)
    reason = await persisted_stop_reason(manager, record.plan.search_id)
    assert reason is not None
    observed["low_diversity"] = reason

    manager, _dynamic, search, run_id = await prepared_run(scratch(tmp_path, "cancelled"))
    record = await search.create_plan(
        run_id,
        parent_node_id="act",
        mode=SearchMode.BEST_OF_N,
        branch_count=2,
        max_parallel=2,
        per_branch_budget=budget(),
        total_budget=budget(wall=240, turns=8, tools=24),
        candidate_directives=[],
    )
    await search.cancel(record.plan.search_id)
    reason = await persisted_stop_reason(manager, record.plan.search_id)
    assert reason is not None
    observed["operator_cancellation"] = reason

    manager, _search, record = await scored_selection(
        scratch(tmp_path, "gain"), scores=(0.9, 0.895)
    )
    reason = await persisted_stop_reason(manager, record.plan.search_id)
    assert reason is not None
    observed["low_expected_gain"] = reason

    manager, _search, record = await scored_selection(scratch(tmp_path, "tie"), scores=(0.9, 0.9))
    reason = await persisted_stop_reason(manager, record.plan.search_id)
    assert reason is not None
    observed["verifier_uncertainty"] = reason

    assert set(observed) == {
        "acceptance",
        "budget",
        "low_diversity",
        "operator_cancellation",
        "low_expected_gain",
        "verifier_uncertainty",
    }
    assert observed == {
        "acceptance": SearchStopReason.ACCEPTED,
        "budget": SearchStopReason.BUDGET_EXHAUSTED,
        "low_diversity": SearchStopReason.LOW_DIVERSITY,
        "operator_cancellation": SearchStopReason.OPERATOR_CANCELLED,
        "low_expected_gain": SearchStopReason.LOW_EXPECTED_GAIN,
        "verifier_uncertainty": SearchStopReason.VERIFIER_UNCERTAIN,
    }
    assert len(set(observed.values())) == 6


# ---------------------------------------------------------------------------
# V02-P6-009 — quality-vs-compute curve for N=1,2,4 on held-out tasks
# ---------------------------------------------------------------------------

# The published points, pinned literally. These are the numbers the acceptance
# document reports; if the runner's arithmetic drifts, this test fails rather
# than the document quietly becoming wrong.
EXPECTED_ACCEPTED = [8, 10, 12]
EXPECTED_MEAN_QUALITY = [0.4725, 0.608333, 0.768333]
EXPECTED_MEAN_TURNS = [1.0, 2.0, 4.0]
EXPECTED_MEAN_TOOL_CALLS = [1.833, 3.75, 8.75]
EXPECTED_MEAN_LATENCY_MS = [866.667, 937.5, 1091.667]
EXPECTED_MARGINAL_GAIN = [0.4725, 0.135833, 0.16]


@pytest.mark.acceptance("V02-P6-009")
def test_frozen_curve_reports_the_published_n_1_2_4_points_on_held_out_tasks() -> None:
    summary = SearchBenchmarkRunner().run()

    assert summary.execution_source == "REPLAY"
    assert summary.candidate_counts == [1, 2, 4]
    assert [point.candidate_count for point in summary.curve] == [1, 2, 4]

    # Held-out set: exactly the 12 tasks the suite declares, each scored at
    # every candidate count.
    assert summary.task_count == 12
    assert len(summary.tasks) == 12
    assert len({task.task_id for task in summary.tasks}) == 12
    for task in summary.tasks:
        assert sorted(task.quality_by_candidate_count) == ["1", "2", "4"]
        assert sorted(task.accepted_by_candidate_count) == ["1", "2", "4"]
    for point in summary.curve:
        assert point.task_count == 12

    assert [point.accepted_tasks for point in summary.curve] == EXPECTED_ACCEPTED
    assert [point.mean_quality for point in summary.curve] == EXPECTED_MEAN_QUALITY
    assert [point.mean_turns for point in summary.curve] == EXPECTED_MEAN_TURNS
    assert [point.mean_tool_calls for point in summary.curve] == EXPECTED_MEAN_TOOL_CALLS
    assert [point.mean_latency_ms for point in summary.curve] == EXPECTED_MEAN_LATENCY_MS
    assert [point.marginal_quality_gain for point in summary.curve] == EXPECTED_MARGINAL_GAIN

    # Quality-vs-compute: quality must rise with compute, and compute must
    # actually rise — a flat curve is not a curve.
    qualities = [point.mean_quality for point in summary.curve]
    assert qualities == sorted(qualities)
    assert qualities[0] < qualities[-1]
    assert EXPECTED_MEAN_TURNS == sorted(EXPECTED_MEAN_TURNS)
    # The first point's "gain" is measured from zero; every later point must
    # show a real positive increment over its predecessor.
    assert all(point.marginal_quality_gain > 0 for point in summary.curve)
    for previous, point in zip(summary.curve, summary.curve[1:], strict=False):
        assert round(point.mean_quality - previous.mean_quality, 6) == round(
            point.marginal_quality_gain, 6
        )

    # Negative results are preserved rather than smoothed away.
    assert summary.null_gain_task_ids
    assert set(summary.null_gain_task_ids) <= {task.task_id for task in summary.tasks}


def perturbed_corpus(tmp_path: Path, *, task_id: str, ordinal: int, quality: float) -> Path:
    """Copy the frozen eval corpus and change one candidate's quality."""
    source = SearchBenchmarkRunner().root
    root = tmp_path / "evals-search"
    shutil.copytree(source, root)
    traces_path = root / "replay-traces.v1.json"
    traces = json.loads(traces_path.read_text())
    rows = traces["traces"] if isinstance(traces, dict) else traces
    changed = 0
    for row in rows:
        if row["task_id"] != task_id:
            continue
        for candidate in row["candidates"]:
            if candidate["ordinal"] == ordinal:
                candidate["quality"] = quality
                changed += 1
    assert changed == 1, f"expected exactly one candidate to perturb, changed {changed}"
    traces_path.write_text(json.dumps(traces, indent=2) + "\n")
    return root


@pytest.mark.acceptance("V02-P6-009")
def test_each_curve_point_is_derived_from_the_traces_it_claims_to_summarize(
    tmp_path: Path,
) -> None:
    """Sensitivity: perturbing the N=1 candidate of one task must move the N=1
    point and leave N=2 and N=4 untouched.

    Without this, the literal pins above would only prove the fixtures are
    unchanged — not that the runner computes anything from them.
    """
    baseline = SearchBenchmarkRunner().run()
    target = baseline.tasks[0].task_id

    root = perturbed_corpus(tmp_path, task_id=target, ordinal=1, quality=0.0)
    perturbed = SearchBenchmarkRunner(root=root).run()

    by_n = {point.candidate_count: point for point in perturbed.curve}
    base_by_n = {point.candidate_count: point for point in baseline.curve}

    # The N=1 point is built from ordinal-1 candidates, so it must move.
    assert by_n[1].mean_quality != base_by_n[1].mean_quality
    assert by_n[1].mean_quality < base_by_n[1].mean_quality

    # N=2 and N=4 take the best of a wider set, which still contains the
    # untouched higher-ordinal candidates, so they must not move.
    assert by_n[2].mean_quality == base_by_n[2].mean_quality
    assert by_n[4].mean_quality == base_by_n[4].mean_quality

    # A perturbed corpus is a different corpus: the digests and the derived run
    # id must all change, so a tampered fixture can never impersonate the
    # published run.
    assert perturbed.trace_sha256 != baseline.trace_sha256
    assert perturbed.benchmark_run_id != baseline.benchmark_run_id
    assert perturbed.corpus_sha256 == baseline.corpus_sha256
    assert perturbed.config_sha256 == baseline.config_sha256


@pytest.mark.acceptance("V02-P6-009")
def test_the_runner_refuses_a_corpus_that_is_not_the_n_1_2_4_held_out_suite(
    tmp_path: Path,
) -> None:
    """The curve's shape is a hard gate, not a convention."""
    source = SearchBenchmarkRunner().root

    root = tmp_path / "bad-counts"
    shutil.copytree(source, root)
    config_path = root / "config.v1.json"
    config = json.loads(config_path.read_text())
    config["candidate_counts"] = [1, 2, 3]
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    with pytest.raises(ValueError, match="N=1,2,4"):
        SearchBenchmarkRunner(root=root).run()

    root = tmp_path / "bad-held-out"
    shutil.copytree(source, root)
    config_path = root / "config.v1.json"
    config = json.loads(config_path.read_text())
    config["held_out_task_count"] = 11
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    with pytest.raises(ValueError):
        SearchBenchmarkRunner(root=root).run()
