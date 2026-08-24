from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    Capability,
    CapabilityBackend,
    CapabilityKind,
    IdempotencyMode,
    Provider,
    RiskLevel,
    SessionRef,
    TaskBudgets,
    VerificationStatus,
    WorkspaceLease,
)
from accretion.orchestration.models import (
    CandidateScore,
    CandidateStatus,
    CandidateTrajectory,
    ProjectFeatureSettings,
    SearchBudgetEnvelope,
    SearchMode,
    SearchPlan,
    SearchStatus,
    SearchStopReason,
)
from accretion.orchestration.search import CandidateSearchConflictError, SearchService
from accretion.orchestration.service import DynamicWorkflowService
from accretion.persistence.store import MemoryStore
from accretion.runtimes.common import RuntimeSubmission
from accretion.runtimes.fake import FakeCallOutcome, FakeRuntime
from accretion.services.run_manager import RunManager
from accretion.workspace import WorktreeManager


def initialize_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Accretion Test"],
        check=True,
    )
    (path / "README.md").write_text("P6 fixture\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)


def services(
    tmp_path: Path, *, runtime: FakeRuntime | None = None
) -> tuple[RunManager, DynamicWorkflowService, SearchService]:
    manager = RunManager(
        store=MemoryStore(),
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: runtime or FakeRuntime()},
        limiter=ConcurrencyLimiter(global_limit=4, provider_limit=4, project_limit=4),
        live_providers_enabled=False,
    )
    dynamic = DynamicWorkflowService(manager, globally_enabled=True, operator_identity="p6-test")
    search = SearchService(manager, globally_enabled=True, operator_identity="p6-test")
    return manager, dynamic, search


async def prepared_run(
    tmp_path: Path,
    *,
    runtime: FakeRuntime | None = None,
    allowed_capabilities: list[str] | None = None,
) -> tuple[RunManager, DynamicWorkflowService, SearchService, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    manager, dynamic, search = services(tmp_path, runtime=runtime)
    project = await manager.create_project("P6", repository)
    for capability_id in allowed_capabilities or []:
        await manager.store.upsert_capability(
            Capability(
                capability_id=capability_id,
                kind=CapabilityKind.TOOL,
                version="1.0.0",
                risk=RiskLevel.LOW,
                idempotency=IdempotencyMode.NONE,
                backend=CapabilityBackend.PYTHON,
            )
        )
    features = await dynamic.get_project_features(project.project_id)
    features = await dynamic.update_project_features(
        project.project_id,
        dynamic_workflows=True,
        candidate_search=True,
        expected_revision=features.revision,
    )
    assert features.dynamic_workflows and features.candidate_search
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Produce and independently verify a bounded candidate.",
        task_patch={
            "task_type": "REVIEW",
            "required_outputs": [{"path": "candidate.txt", "kind": "file"}],
            "allowed_capabilities": allowed_capabilities or [],
            "budgets": TaskBudgets(max_parallel_runs=2).model_dump(mode="json"),
        },
    )
    proposal = await dynamic.propose(task.envelope.task_id, execution_provider=Provider.FAKE)
    assert proposal.run_id is not None
    outcome = await dynamic.validate(proposal.run_id, proposal.proposal_id)
    assert outcome.validation.status.value == "ACCEPT"
    return manager, dynamic, search, proposal.run_id


def budget(*, wall: int = 120, turns: int = 4, tools: int = 12) -> SearchBudgetEnvelope:
    return SearchBudgetEnvelope(
        wall_time_seconds=wall,
        max_turns=turns,
        max_tool_calls=tools,
    )


@pytest.mark.acceptance("V02-P6-002")
def test_search_contracts_reject_invalid_bounds_and_feature_dependencies() -> None:
    with pytest.raises(ValidationError, match="requires dynamic workflows"):
        ProjectFeatureSettings(project_id="prj_test", candidate_search=True)
    with pytest.raises(ValidationError, match="max_parallel"):
        SearchPlan(
            search_id="src_test",
            run_id="run_test",
            parent_node_id="act",
            graph_revision=1,
            mode=SearchMode.BEST_OF_N,
            branch_count=1,
            max_parallel=2,
            per_branch_budget=budget(),
            total_budget=budget(),
            verifier_policy_ref="policy",
            requested_by="test",
        )


async def test_inert_search_plan_is_validated_persisted_and_cancellable(
    tmp_path: Path,
) -> None:
    manager, _, search, run_id = await prepared_run(tmp_path)
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
    assert record.status is SearchStatus.PLANNED
    assert await manager.store.get_search(record.plan.search_id) == record
    cancelled = await search.cancel(record.plan.search_id)
    assert cancelled.status is SearchStatus.CANCELLED
    assert cancelled.revision == 2


async def test_replay_branch_is_reserved_for_p7(tmp_path: Path) -> None:
    _, _, search, run_id = await prepared_run(tmp_path)
    with pytest.raises(CandidateSearchConflictError, match="REPLAY_BRANCH_REQUIRES_P7"):
        await search.create_plan(
            run_id,
            parent_node_id="act",
            mode=SearchMode.REPLAY_BRANCH,
            branch_count=2,
            max_parallel=2,
            per_branch_budget=budget(),
            total_budget=budget(wall=240, turns=8, tools=24),
            candidate_directives=[],
        )


@pytest.mark.acceptance("V02-P6-001")
async def test_best_of_two_isolates_candidates_and_promotes_only_unique_winner(
    tmp_path: Path,
) -> None:
    observed_workspaces: list[Path] = []

    def write(value: str):
        def hook(session: SessionRef, _request: RuntimeSubmission) -> None:
            workspace = session.workspace
            observed_workspaces.append(workspace)
            (workspace / "candidate.txt").write_text(value)

        return hook

    runtime = FakeRuntime(
        scripted_outcomes=[
            FakeCallOutcome(hook=write("")),
            FakeCallOutcome(hook=write("strong")),
        ]
    )
    manager, dynamic, search, run_id = await prepared_run(tmp_path, runtime=runtime)
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
    proposals = await manager.store.list_workflow_proposals(run_id=run_id)
    await dynamic.activate(run_id, proposals[-1].proposal_id)
    background = manager.background.get(run_id)
    if background is not None:
        await background

    completed = await search.get(record.plan.search_id)
    assert completed.status is SearchStatus.SUCCEEDED
    assert completed.selected_candidate_id is not None
    candidates = await manager.store.list_search_candidates(record.plan.search_id)
    selected = next(
        item for item in candidates if item.candidate_id == completed.selected_candidate_id
    )
    assert selected.status.value == "SELECTED"
    assert selected.patch_sha256 is not None
    assert len(observed_workspaces) == 2
    assert observed_workspaces[0] != observed_workspaces[1]
    assert all(path.parent.name == record.plan.search_id for path in observed_workspaces)
    assert len(await manager.store.list_candidate_scores(record.plan.search_id)) == 2


@pytest.mark.acceptance("V02-P6-002")
async def test_shared_budget_prunes_unfunded_sibling(tmp_path: Path) -> None:
    runtime = FakeRuntime(
        scripted_outcomes=[
            FakeCallOutcome(
                hook=lambda session, request: (session.workspace / "candidate.txt").write_text(
                    "strong"
                )
            )
        ]
    )
    manager, dynamic, search, run_id = await prepared_run(tmp_path, runtime=runtime)
    record = await search.create_plan(
        run_id,
        parent_node_id="act",
        mode=SearchMode.BEST_OF_N,
        branch_count=2,
        max_parallel=2,
        per_branch_budget=budget(turns=1, tools=2),
        total_budget=budget(wall=120, turns=1, tools=2),
        candidate_directives=[],
    )
    proposals = await manager.store.list_workflow_proposals(run_id=run_id)
    await dynamic.activate(run_id, proposals[-1].proposal_id)
    background = manager.background.get(run_id)
    if background is not None:
        await background
    candidates = await manager.store.list_search_candidates(record.plan.search_id)
    assert sum(item.status.value == "PRUNED" for item in candidates) == 1
    final = await search.get(record.plan.search_id)
    assert final.budget_spent.turns <= 1
    assert final.budget_spent.tool_calls <= 2


@pytest.mark.acceptance("V02-P6-002")
async def test_search_borrows_parent_slot_without_deadlocking_limit_one(
    tmp_path: Path,
) -> None:
    def write(value: str):
        def hook(session: SessionRef, _request: RuntimeSubmission) -> None:
            (session.workspace / "candidate.txt").write_text(value)

        return hook

    runtime = FakeRuntime(
        scripted_outcomes=[
            FakeCallOutcome(hook=write("")),
            FakeCallOutcome(hook=write("strong")),
        ]
    )
    manager, dynamic, search, run_id = await prepared_run(tmp_path, runtime=runtime)
    manager.limiter = ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1)
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
    proposal = (await manager.store.list_workflow_proposals(run_id=run_id))[-1]
    await dynamic.activate(run_id, proposal.proposal_id)
    background = manager.background.get(run_id)
    assert background is not None
    await asyncio.wait_for(background, timeout=3)
    assert (await search.get(record.plan.search_id)).status is SearchStatus.SUCCEEDED


@pytest.mark.acceptance("V02-P6-003")
async def test_protected_capability_node_cannot_receive_search_plan(tmp_path: Path) -> None:
    _, _, search, run_id = await prepared_run(tmp_path, allowed_capabilities=["filesystem.write"])
    with pytest.raises(CandidateSearchConflictError, match="capability-bearing"):
        await search.create_plan(
            run_id,
            parent_node_id="act",
            mode=SearchMode.BEST_OF_N,
            branch_count=2,
            max_parallel=2,
            per_branch_budget=budget(),
            total_budget=budget(wall=240, turns=8, tools=24),
            candidate_directives=[],
        )


@pytest.mark.acceptance("V02-P6-001")
async def test_candidate_failure_does_not_corrupt_sibling_or_parent(tmp_path: Path) -> None:
    def fail_hook(session: SessionRef, _request: RuntimeSubmission) -> None:
        (session.workspace / "failed-only.txt").write_text("not promotable")
        raise RuntimeError("candidate crash")

    def succeed_hook(session: SessionRef, _request: RuntimeSubmission) -> None:
        assert not (session.workspace / "failed-only.txt").exists()
        (session.workspace / "candidate.txt").write_text("strong")

    runtime = FakeRuntime(
        scripted_outcomes=[
            FakeCallOutcome(hook=fail_hook),
            FakeCallOutcome(hook=succeed_hook),
        ]
    )
    manager, dynamic, search, run_id = await prepared_run(tmp_path, runtime=runtime)
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
    proposal = (await manager.store.list_workflow_proposals(run_id=run_id))[-1]
    await dynamic.activate(run_id, proposal.proposal_id)
    background = manager.background.get(run_id)
    if background is not None:
        await background
    final = await search.get(record.plan.search_id)
    candidates = await manager.store.list_search_candidates(record.plan.search_id)
    assert final.status is SearchStatus.SUCCEEDED
    assert sum(item.status.value == "FAILED" for item in candidates) == 1
    assert sum(item.status.value == "SELECTED" for item in candidates) == 1


@pytest.mark.acceptance("V02-P6-004")
async def test_operator_cancellation_stops_search_without_promotion(tmp_path: Path) -> None:
    runtime = FakeRuntime(step_delay=0.2)
    manager, dynamic, search, run_id = await prepared_run(tmp_path, runtime=runtime)
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
    proposal = (await manager.store.list_workflow_proposals(run_id=run_id))[-1]
    await dynamic.activate(run_id, proposal.proposal_id)
    for _ in range(100):
        if (await search.get(record.plan.search_id)).status is SearchStatus.RUNNING:
            break
        await asyncio.sleep(0.01)
    cancelled = await search.cancel(record.plan.search_id)
    assert cancelled.status is SearchStatus.CANCELLED
    background = manager.background.get(run_id)
    if background is not None:
        await background
    final = await search.get(record.plan.search_id)
    assert final.status is SearchStatus.CANCELLED
    assert final.selected_candidate_id is None
    assert await manager.store.get_search_promotion(record.plan.search_id) is None


async def test_identical_verified_candidates_stop_for_low_diversity(tmp_path: Path) -> None:
    def same(session: SessionRef, _request: RuntimeSubmission) -> None:
        (session.workspace / "candidate.txt").write_text("same")

    runtime = FakeRuntime(
        scripted_outcomes=[FakeCallOutcome(hook=same), FakeCallOutcome(hook=same)]
    )
    manager, dynamic, search, run_id = await prepared_run(tmp_path, runtime=runtime)
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
    proposal = (await manager.store.list_workflow_proposals(run_id=run_id))[-1]
    await dynamic.activate(run_id, proposal.proposal_id)
    background = manager.background.get(run_id)
    if background is not None:
        await background
    final = await search.get(record.plan.search_id)
    assert final.status is SearchStatus.STOPPED
    assert final.stop_reason is not None
    assert final.stop_reason.value == "LOW_DIVERSITY"
    assert final.selected_candidate_id is None


@pytest.mark.acceptance("V02-P6-007")
async def test_cross_provider_records_runtime_provenance_and_promotes_winner(
    tmp_path: Path,
) -> None:
    def write(value: str):
        def hook(session: SessionRef, _request: RuntimeSubmission) -> None:
            (session.workspace / "candidate.txt").write_text(value)

        return hook

    manager, dynamic, search, run_id = await prepared_run(tmp_path)
    manager.runtimes[Provider.CLAUDE] = FakeRuntime(
        scripted_outcomes=[FakeCallOutcome(hook=write(""))]
    )
    manager.runtimes[Provider.CODEX] = FakeRuntime(
        scripted_outcomes=[FakeCallOutcome(hook=write("codex winner"))]
    )
    manager.live_providers_enabled = True
    record = await search.create_plan(
        run_id,
        parent_node_id="act",
        mode=SearchMode.CROSS_PROVIDER,
        branch_count=2,
        max_parallel=2,
        per_branch_budget=budget(),
        total_budget=budget(wall=240, turns=8, tools=24),
        candidate_directives=[],
    )
    proposal = (await manager.store.list_workflow_proposals(run_id=run_id))[-1]
    await dynamic.activate(run_id, proposal.proposal_id)
    background = manager.background.get(run_id)
    if background is not None:
        await background
    final = await search.get(record.plan.search_id)
    candidates = await manager.store.list_search_candidates(record.plan.search_id)
    assert final.status is SearchStatus.SUCCEEDED
    assert {item.provider for item in candidates} == {Provider.CLAUDE, Provider.CODEX}
    assert all(item.runtime_id and item.runtime_version for item in candidates)
    winner = next(item for item in candidates if item.status is CandidateStatus.SELECTED)
    assert winner.provider is Provider.CODEX


@pytest.mark.acceptance("V02-P6-006")
async def test_restart_interrupts_active_candidates_and_charges_full_branch_budget(
    tmp_path: Path,
) -> None:
    manager, _, search, run_id = await prepared_run(tmp_path)
    record = await search.create_plan(
        run_id,
        parent_node_id="act",
        mode=SearchMode.BEST_OF_N,
        branch_count=2,
        max_parallel=2,
        per_branch_budget=budget(turns=3, tools=7),
        total_budget=budget(wall=240, turns=8, tools=24),
        candidate_directives=[],
    )
    record = await manager.store.update_search(
        record.model_copy(update={"status": SearchStatus.RUNNING}),
        expected_revision=record.revision,
    )
    candidate = CandidateTrajectory(
        candidate_id="search_candidate_restart",
        search_id=record.plan.search_id,
        run_id=run_id,
        ordinal=1,
        provider=Provider.FAKE,
        runtime_id="runtime_fake",
        runtime_model="default",
        runtime_version="fake-p2-v1",
        status=CandidateStatus.RUNNING,
    )
    await manager.store.save_search_candidate(candidate)

    await search.reconcile()

    final = await search.get(record.plan.search_id)
    recovered = await manager.store.get_search_candidate(candidate.candidate_id)
    assert final.status is SearchStatus.REQUIRES_HUMAN
    assert final.budget_spent.turns == 3
    assert final.budget_spent.tool_calls == 7
    assert recovered is not None
    assert recovered.status is CandidateStatus.INTERRUPTED
    assert recovered.budget_spent.turns == 3
    assert recovered.budget_spent.tool_calls == 7


@pytest.mark.parametrize(
    ("runner_up_score", "expected_status", "expected_reason"),
    [
        (0.9, SearchStatus.REQUIRES_HUMAN, SearchStopReason.VERIFIER_UNCERTAIN),
        (0.895, SearchStatus.STOPPED, SearchStopReason.LOW_EXPECTED_GAIN),
    ],
)
@pytest.mark.acceptance("V02-P6-004")
async def test_search_stops_on_tie_or_low_expected_gain(
    tmp_path: Path,
    runner_up_score: float,
    expected_status: SearchStatus,
    expected_reason: SearchStopReason,
) -> None:
    manager, _, search, run_id = await prepared_run(tmp_path)
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
    candidates = [
        CandidateTrajectory(
            candidate_id=f"search_candidate_score_{index}",
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
        for index, character in ((1, "a"), (2, "b"))
    ]
    for candidate, score in zip(candidates, (0.9, runner_up_score), strict=True):
        await manager.store.save_search_candidate(candidate)
        await manager.store.save_candidate_score(
            CandidateScore(
                score_id=f"score_{candidate.ordinal}",
                search_id=record.plan.search_id,
                candidate_id=candidate.candidate_id,
                verifier_policy_ref=record.plan.verifier_policy_ref,
                verifier_status=VerificationStatus.PASS.value,
                eligible=True,
                quality_score=1,
                cost_proxy=0,
                latency_proxy=0,
                risk_score=0,
                total_score=score,
                explanation="deterministic selection fixture",
            )
        )
    run = await manager._require_run(run_id)
    task = await manager._require_task(run.task_id)
    policy = await manager._require_policy(run.acceptance_policy_id)
    lease = WorkspaceLease(
        lease_id="workspace_selection_fixture",
        project_id=run.project_id,
        run_id=run_id,
        base_revision="fixture",
        path=tmp_path / "repository",
        branch_name="fixture",
    )

    final = await search._select_and_promote(record, run, task, lease, policy)

    assert final.status is expected_status
    assert final.stop_reason is expected_reason
    assert final.selected_candidate_id is None
