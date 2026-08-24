from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    AcceptancePolicy,
    ErrorSummary,
    EventType,
    Provider,
    Run,
    RunState,
    RuntimeExecutionRequest,
    SessionRef,
    TaskType,
)
from accretion.experience.models import ExperiencePolarity, SeedValidationStatus
from accretion.experience.service import ExperienceService
from accretion.ids import new_id
from accretion.orchestration.models import (
    CandidateSourceKind,
    CandidateStatus,
    SearchBudgetEnvelope,
    SearchMode,
    SearchStatus,
)
from accretion.orchestration.search import SearchService
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
    (path / "README.md").write_text("P7 trajectory replay fixture\n")
    (path / "pyproject.toml").write_text('[project]\nname = "replay-fixture"\n')
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)


async def create_task(manager: RunManager, project_id: str, objective: str) -> str:
    task = await manager.create_task(
        project_id=project_id,
        objective=objective,
        task_patch={
            "task_type": TaskType.IMPLEMENT,
            "constraints": ["Keep the public API stable."],
            "success_criteria": ["All deterministic checks pass."],
        },
    )
    return task.envelope.task_id


async def prepare_replay(
    tmp_path: Path,
    runtime: FakeRuntime,
) -> tuple[
    RunManager,
    DynamicWorkflowService,
    ExperienceService,
    SearchService,
    str,
    str,
    str,
]:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    manager = RunManager(
        store=MemoryStore(),
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: runtime},
        limiter=ConcurrencyLimiter(global_limit=4, provider_limit=4, project_limit=4),
        live_providers_enabled=False,
    )
    dynamic = DynamicWorkflowService(
        manager, globally_enabled=True, operator_identity="p7-replay-test"
    )
    experience = ExperienceService(
        manager, globally_enabled=True, operator_identity="p7-replay-test"
    )
    search = SearchService(
        manager,
        globally_enabled=True,
        operator_identity="p7-replay-test",
        experience_service=experience,
    )
    project = await manager.create_project("P7 replay", repository)
    features = await dynamic.get_project_features(project.project_id)
    await dynamic.update_project_features(
        project.project_id,
        dynamic_workflows=True,
        candidate_search=True,
        experience_retrieval=True,
        expected_revision=features.revision,
    )
    objective = "Add a deterministic local health endpoint."

    positive_task_id = await create_task(manager, project.project_id, objective)
    policy = AcceptancePolicy(
        policy_id=new_id("acceptance_policy"),
        required_verifiers=[],
        outcome_check="controlled trajectory replay fixture",
    )
    await manager.store.save_acceptance_policy(policy)
    positive_run = Run(
        run_id=new_id("run"),
        task_id=positive_task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.SUCCEEDED,
        acceptance_policy_id=policy.policy_id,
    )
    await manager.store.create_run(positive_run)
    positive = await experience.materialize(positive_run.run_id)

    negative_task_id = await create_task(manager, project.project_id, objective)
    negative_run = Run(
        run_id=new_id("run"),
        task_id=negative_task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.FAILED,
        error=ErrorSummary(
            code="CONTROLLED_FIXTURE_FAILURE",
            message="Bearer private-fixture-token must never enter replay guidance",
        ),
    )
    await manager.store.create_run(negative_run)
    await experience.materialize(negative_run.run_id)

    target_task_id = await create_task(manager, project.project_id, objective)
    planning = await manager.get_task_planning(target_task_id)
    matches = await experience.query(target_task_id, include_failures=True, top_k=5)
    positive_match = next(
        item for item in matches if item.polarity is ExperiencePolarity.POSITIVE
    )
    negative_match = next(
        item for item in matches if item.polarity is ExperiencePolarity.NEGATIVE
    )
    await experience.select(
        target_task_id,
        query_id=positive_match.query_id,
        match_ids=[positive_match.match_id, negative_match.match_id],
        expected_context_bundle_id=planning.context_bundle.context_bundle_id,
    )
    proposal = await dynamic.propose(target_task_id, execution_provider=Provider.FAKE)
    assert proposal.run_id is not None
    validation = await dynamic.validate(proposal.run_id, proposal.proposal_id)
    assert validation.validation.status.value == "ACCEPT"
    per_branch = SearchBudgetEnvelope(
        wall_time_seconds=60, max_turns=2, max_tool_calls=5
    )
    record = await search.create_plan(
        proposal.run_id,
        parent_node_id="act",
        mode=SearchMode.REPLAY_BRANCH,
        branch_count=2,
        max_parallel=1,
        per_branch_budget=per_branch,
        total_budget=per_branch.model_copy(
            update={"wall_time_seconds": 120, "max_turns": 4, "max_tool_calls": 10}
        ),
        candidate_directives=[],
        replay_seed_match_ids=[positive_match.match_id],
        negative_guidance_match_ids=[negative_match.match_id],
    )
    return (
        manager,
        dynamic,
        experience,
        search,
        proposal.proposal_id,
        record.plan.search_id,
        positive.experience.experience_id,
    )


@pytest.mark.acceptance("V02-P7-005")
async def test_replay_executes_fresh_control_and_frozen_procedural_seed(
    tmp_path: Path,
) -> None:
    directives: list[str] = []
    workspaces: list[Path] = []

    def write_candidate(value: str):
        def hook(session: SessionRef, request: RuntimeSubmission) -> None:
            assert isinstance(request, RuntimeExecutionRequest)
            directives.append(request.directive.objective)
            workspaces.append(session.workspace)
            (session.workspace / "candidate.txt").write_text(value)

        return hook

    runtime = FakeRuntime(
        scripted_outcomes=[
            FakeCallOutcome(hook=write_candidate("fresh")),
            FakeCallOutcome(hook=write_candidate("replay")),
        ]
    )
    manager, dynamic, _, search, proposal_id, search_id, experience_id = (
        await prepare_replay(tmp_path, runtime)
    )
    record = await search.get(search_id)
    await dynamic.activate(record.plan.run_id, proposal_id)
    background = manager.background.get(record.plan.run_id)
    if background is not None:
        await background

    candidates = await manager.store.list_search_candidates(search_id)
    seeds = await manager.store.list_trajectory_seeds(search_id)
    assert [item.source_kind for item in candidates] == [
        CandidateSourceKind.FRESH,
        CandidateSourceKind.REPLAY,
    ]
    assert len(seeds) == 1
    assert seeds[0].candidate_id == candidates[1].candidate_id
    assert seeds[0].experience_id == experience_id
    assert seeds[0].validation_status is SeedValidationStatus.ELIGIBLE
    assert seeds[0].procedural_guidance
    assert len(workspaces) == 2 and workspaces[0] != workspaces[1]
    assert candidates[0].session_id != candidates[1].session_id
    assert "Verified procedural seed" not in directives[0]
    assert "failure taxonomy" not in directives[0]
    assert "Verified procedural seed" in directives[1]
    assert "CONTROLLED_FIXTURE_FAILURE" in directives[1]

    serialized = json.dumps(seeds[0].model_dump(mode="json"))
    for forbidden in (
        "private-fixture-token",
        "native_session_id",
        "tool_args",
        "tool_results",
        "raw_patch",
        "transcript",
    ):
        assert forbidden not in serialized
    events = await manager.store.list_events(record.plan.run_id)
    query = next(item for item in events if item.normalized_type is EventType.EXPERIENCE_QUERY)
    retrieved = next(
        item for item in events if item.normalized_type is EventType.EXPERIENCE_RETRIEVED
    )
    started = [
        item
        for item in events
        if item.normalized_type is EventType.TRAJECTORY_REPLAY_STARTED
    ]
    assert retrieved.causation_id == query.event_id
    assert len(started) == 1
    assert started[0].causation_id == retrieved.event_id
    assert not any(
        item.normalized_type is EventType.TRAJECTORY_REPLAY_REJECTED for item in events
    )
    assert (await search.get(search_id)).status in {
        SearchStatus.SUCCEEDED,
        SearchStatus.STOPPED,
        SearchStatus.REQUIRES_HUMAN,
    }


@pytest.mark.acceptance("V02-P7-006")
async def test_retracted_seed_is_pruned_before_workspace_and_fresh_control_continues(
    tmp_path: Path,
) -> None:
    submitted: list[str] = []

    def write_fresh(session: SessionRef, request: RuntimeSubmission) -> None:
        assert isinstance(request, RuntimeExecutionRequest)
        submitted.append(request.directive.objective)
        (session.workspace / "candidate.txt").write_text("fresh control survives")

    runtime = FakeRuntime(scripted_outcomes=[FakeCallOutcome(hook=write_fresh)])
    manager, dynamic, experience, search, proposal_id, search_id, experience_id = (
        await prepare_replay(tmp_path, runtime)
    )
    await experience.retract(
        experience_id,
        reason="Invalidate the seed between planning and launch.",
        expected_revision=1,
    )
    record = await search.get(search_id)
    await dynamic.activate(record.plan.run_id, proposal_id)
    background = manager.background.get(record.plan.run_id)
    if background is not None:
        await background

    candidates = await manager.store.list_search_candidates(search_id)
    fresh = next(item for item in candidates if item.source_kind is CandidateSourceKind.FRESH)
    replay = next(item for item in candidates if item.source_kind is CandidateSourceKind.REPLAY)
    assert len(submitted) == 1
    assert fresh.session_id is not None and fresh.workspace_lease_id is not None
    assert fresh.status in {CandidateStatus.COMPLETED, CandidateStatus.SELECTED}
    assert replay.status is CandidateStatus.PRUNED
    assert replay.session_id is None and replay.workspace_lease_id is None
    assert replay.seed_revalidation_status == SeedValidationStatus.REJECTED.value
    assert "EXPERIENCE_RETRACTED" in replay.seed_revalidation_reasons
    assert len(await manager.store.list_trajectory_seeds(search_id)) == 1
    events = await manager.store.list_events(record.plan.run_id)
    rejected = [
        item
        for item in events
        if item.normalized_type is EventType.TRAJECTORY_REPLAY_REJECTED
    ]
    assert len(rejected) == 1
    assert rejected[0].payload["phase"] == "LAUNCH"
    retrieved = next(
        item for item in events if item.normalized_type is EventType.EXPERIENCE_RETRIEVED
    )
    assert rejected[0].causation_id == retrieved.event_id
    assert not any(
        item.normalized_type is EventType.TRAJECTORY_REPLAY_STARTED for item in events
    )
