from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import Provider, TaskBudgets
from accretion.orchestration.models import (
    ProjectFeatureSettings,
    SearchBudgetEnvelope,
    SearchMode,
    SearchPlan,
    SearchStatus,
)
from accretion.orchestration.search import CandidateSearchConflictError, SearchService
from accretion.orchestration.service import DynamicWorkflowService
from accretion.persistence.store import MemoryStore
from accretion.runtimes.fake import FakeRuntime
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


def services(tmp_path: Path) -> tuple[RunManager, DynamicWorkflowService, SearchService]:
    manager = RunManager(
        store=MemoryStore(),
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: FakeRuntime()},
        limiter=ConcurrencyLimiter(global_limit=4, provider_limit=4, project_limit=4),
        live_providers_enabled=False,
    )
    dynamic = DynamicWorkflowService(
        manager, globally_enabled=True, operator_identity="p6-test"
    )
    search = SearchService(manager, globally_enabled=True, operator_identity="p6-test")
    return manager, dynamic, search


async def prepared_run(
    tmp_path: Path,
) -> tuple[RunManager, DynamicWorkflowService, SearchService, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    manager, dynamic, search = services(tmp_path)
    project = await manager.create_project("P6", repository)
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
            "required_outputs": [{"path": "README.md", "kind": "file"}],
            "budgets": TaskBudgets(max_parallel_runs=2).model_dump(mode="json"),
        },
    )
    proposal = await dynamic.propose(
        task.envelope.task_id, execution_provider=Provider.FAKE
    )
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
