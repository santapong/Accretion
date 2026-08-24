from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from accretion.api.main import app
from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    AcceptancePolicy,
    ErrorSummary,
    Provider,
    Run,
    RunState,
    TaskType,
)
from accretion.experience.embedding import deterministic_embedding, repository_manifests
from accretion.experience.models import (
    ExperiencePolarity,
    ExperienceTrust,
    MatchDisposition,
)
from accretion.experience.service import ExperienceConflictError, ExperienceService
from accretion.ids import new_id
from accretion.orchestration.models import (
    CandidateScore,
    CandidateStatus,
    CandidateTrajectory,
    SearchBudgetEnvelope,
    SearchMode,
    SearchPlan,
    SearchRecord,
)
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
    (path / "README.md").write_text("P7 fixture\n")
    (path / "pyproject.toml").write_text('[project]\nname = "fixture"\n')
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)


def services(
    tmp_path: Path,
) -> tuple[RunManager, DynamicWorkflowService, ExperienceService]:
    manager = RunManager(
        store=MemoryStore(),
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: FakeRuntime()},
        limiter=ConcurrencyLimiter(global_limit=4, provider_limit=4, project_limit=4),
        live_providers_enabled=False,
    )
    dynamic = DynamicWorkflowService(
        manager, globally_enabled=True, operator_identity="p7-test"
    )
    experience = ExperienceService(
        manager, globally_enabled=True, operator_identity="p7-test"
    )
    return manager, dynamic, experience


async def enable_p7(dynamic: DynamicWorkflowService, project_id: str) -> None:
    current = await dynamic.get_project_features(project_id)
    updated = await dynamic.update_project_features(
        project_id,
        dynamic_workflows=True,
        candidate_search=True,
        experience_retrieval=True,
        expected_revision=current.revision,
    )
    assert updated.experience_retrieval


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


async def seed_succeeded_run(
    manager: RunManager, *, task_id: str, project_id: str
) -> Run:
    policy = AcceptancePolicy(
        policy_id=new_id("acceptance_policy"),
        required_verifiers=[],
        outcome_check="controlled P7 fixture",
    )
    await manager.store.save_acceptance_policy(policy)
    run = Run(
        run_id=new_id("run"),
        task_id=task_id,
        project_id=project_id,
        provider=Provider.FAKE,
        state=RunState.SUCCEEDED,
        acceptance_policy_id=policy.policy_id,
    )
    return await manager.store.create_run(run)


@pytest.mark.acceptance("V02-P7-001")
async def test_materialize_query_select_and_freeze_context(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    manager, dynamic, experience = services(tmp_path)
    project = await manager.create_project("P7", repository)
    await enable_p7(dynamic, project.project_id)

    source_task_id = await create_task(
        manager, project.project_id, "Add a deterministic health endpoint."
    )
    source_run = await seed_succeeded_run(
        manager, task_id=source_task_id, project_id=project.project_id
    )
    terminal = await manager.store.get_run(source_run.run_id)
    assert terminal is not None and terminal.state is RunState.SUCCEEDED

    detail = await experience.materialize(source_run.run_id)
    assert detail.experience.polarity is ExperiencePolarity.POSITIVE
    assert detail.experience.retracted is False
    assert {item.kind.value for item in detail.segments} <= {
        "WORKFLOW_PATH",
        "TOOL_SEQUENCE",
        "VERIFIER_FINDINGS",
        "REPAIR_PATTERN",
        "FAILURE_PATTERN",
        "ARTIFACT_SHAPE",
    }
    assert await experience.materialize(source_run.run_id) == detail

    target_task_id = await create_task(
        manager, project.project_id, "Add a deterministic health endpoint."
    )
    planning = await manager.get_task_planning(target_task_id)
    matches = await experience.query(target_task_id, top_k=5)
    assert len(matches) == 1
    assert matches[0].assessment.disposition is MatchDisposition.ACCEPTED
    assert matches[0].assessment.replay_eligible

    selection = await experience.select(
        target_task_id,
        query_id=matches[0].query_id,
        match_ids=[matches[0].match_id],
        expected_context_bundle_id=planning.context_bundle.context_bundle_id,
    )
    revised = await manager.get_task_planning(target_task_id)
    assert revised.context_bundle.version == "context-bundle-v2"
    assert revised.context_bundle.supersedes_context_bundle_id == (
        planning.context_bundle.context_bundle_id
    )
    assert revised.context_bundle.experience_refs == [detail.experience.experience_id]
    assert len(revised.context_history) == 2
    assert await experience.selections(target_task_id) == [selection]

    proposal = await dynamic.propose(target_task_id, execution_provider=Provider.FAKE)
    assert f"experience-match:{matches[0].match_id}" in proposal.provenance_refs
    with pytest.raises(ExperienceConflictError, match="frozen"):
        await experience.query(target_task_id)
    with pytest.raises(ExperienceConflictError, match="frozen"):
        await experience.select(
            target_task_id,
            query_id=matches[0].query_id,
            match_ids=[matches[0].match_id],
            expected_context_bundle_id=revised.context_bundle.context_bundle_id,
        )


@pytest.mark.acceptance("V02-P7-004")
async def test_negative_knowledge_retraction_and_stale_rejection(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    manager, dynamic, experience = services(tmp_path)
    project = await manager.create_project("P7 negative", repository)
    await enable_p7(dynamic, project.project_id)
    source_task_id = await create_task(
        manager, project.project_id, "Review a deterministic service boundary."
    )
    failed = Run(
        run_id=new_id("run"),
        task_id=source_task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.FAILED,
        error=ErrorSummary(code="FIXTURE_FAILURE", message="controlled failure"),
    )
    await manager.store.create_run(failed)
    negative = await experience.materialize(failed.run_id)
    assert negative.experience.polarity is ExperiencePolarity.NEGATIVE
    assert negative.experience.failure_taxonomy == ["FIXTURE_FAILURE"]

    target_task_id = await create_task(
        manager, project.project_id, "Review a deterministic service boundary."
    )
    matches = await experience.query(target_task_id, include_failures=True)
    assert matches[0].assessment.negative_guidance_eligible
    assert not matches[0].assessment.replay_eligible

    retracted = await experience.retract(
        negative.experience.experience_id,
        reason="Fixture evidence is intentionally invalidated.",
        expected_revision=1,
    )
    assert retracted.retracted and retracted.revision == 2
    another_task = await create_task(
        manager, project.project_id, "Review a deterministic service boundary."
    )
    rejected = await experience.query(another_task, include_failures=True)
    assert rejected[0].assessment.disposition is MatchDisposition.REJECTED
    assert "EXPERIENCE_RETRACTED" in rejected[0].assessment.reasons

    query_record = await manager.store.get_experience_query(rejected[0].query_id)
    assert query_record is not None
    stale = negative.experience.model_copy(
        update={
            "retracted": False,
            "created_at": datetime.now(UTC) - timedelta(days=181),
        }
    )
    assessment = await experience.assess(
        query_record[0], stale, semantic_score=1, repository=repository
    )
    assert assessment.freshness_score == 0.8
    max_age_query = query_record[0].model_copy(update={"max_age_days": 30})
    max_age = await experience.assess(
        max_age_query, stale, semantic_score=1, repository=repository
    )
    assert max_age.disposition is MatchDisposition.REJECTED
    assert "MAX_AGE_EXCEEDED" in max_age.reasons
    unavailable = stale.model_copy(update={"requested_skills": ["missing-plugin"]})
    unavailable_query = query_record[0].model_copy(
        update={"requested_skills": ["missing-plugin"]}
    )
    unavailable_assessment = await experience.assess(
        unavailable_query, unavailable, semantic_score=1, repository=repository
    )
    assert "SKILL_OR_PLUGIN_UNAVAILABLE" in unavailable_assessment.reasons


@pytest.mark.acceptance("V02-P7-004")
async def test_terminal_candidate_materialization_distinguishes_winner_and_out_ranked(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    manager, dynamic, experience = services(tmp_path)
    project = await manager.create_project("P7 candidates", repository)
    await enable_p7(dynamic, project.project_id)
    task_id = await create_task(manager, project.project_id, "Compare safe local candidates.")
    run = Run(
        run_id=new_id("run"),
        task_id=task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.SUCCEEDED,
    )
    await manager.store.create_run(run)
    budget = SearchBudgetEnvelope(
        wall_time_seconds=60, max_turns=2, max_tool_calls=4
    )
    search = SearchRecord(
        plan=SearchPlan(
            search_id=new_id("search"),
            run_id=run.run_id,
            parent_node_id="act",
            graph_revision=1,
            mode=SearchMode.BEST_OF_N,
            branch_count=2,
            max_parallel=2,
            per_branch_budget=budget,
            total_budget=budget.model_copy(
                update={"wall_time_seconds": 120, "max_turns": 4, "max_tool_calls": 8}
            ),
            verifier_policy_ref="search-verifier-v2",
            requested_by="p7-test",
        )
    )
    await manager.store.create_search(search)
    selected = CandidateTrajectory(
        candidate_id=new_id("search_candidate"),
        search_id=search.plan.search_id,
        run_id=run.run_id,
        ordinal=1,
        provider=Provider.FAKE,
        runtime_id="fake",
        runtime_model="fake",
        runtime_version="test",
        status=CandidateStatus.SELECTED,
        completed_at=datetime.now(UTC),
    )
    pruned = selected.model_copy(
        update={
            "candidate_id": new_id("search_candidate"),
            "ordinal": 2,
            "status": CandidateStatus.PRUNED,
        }
    )
    await manager.store.save_search_candidate(selected)
    await manager.store.save_search_candidate(pruned)
    for candidate, total in ((selected, 0.9), (pruned, 0.8)):
        await manager.store.save_candidate_score(
            CandidateScore(
                score_id=new_id("candidate_score"),
                search_id=search.plan.search_id,
                candidate_id=candidate.candidate_id,
                verifier_policy_ref="search-verifier-v2",
                verifier_status="PASS",
                eligible=True,
                quality_score=total,
                cost_proxy=0.1,
                latency_proxy=0.1,
                risk_score=0,
                total_score=total,
                explanation="controlled fixture",
            )
        )

    winner = await experience.materialize(run.run_id, candidate_id=selected.candidate_id)
    avoided = await experience.materialize(run.run_id, candidate_id=pruned.candidate_id)
    assert winner.experience.trust is ExperienceTrust.HIGH
    assert winner.experience.polarity is ExperiencePolarity.POSITIVE
    assert avoided.experience.trust is ExperienceTrust.MEDIUM
    assert avoided.experience.polarity is ExperiencePolarity.NEGATIVE
    assert "OUT_RANKED" in avoided.experience.failure_taxonomy


@pytest.mark.acceptance("V02-P7-002")
async def test_embedding_is_deterministic_redacted_and_repository_scoped(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    manager, _, experience = services(tmp_path)
    project = await manager.create_project("P7 embedding", repository)
    first_id = await create_task(
        manager,
        project.project_id,
        "Inspect Bearer first-secret-value and normalize ＡＰＩ output.",
    )
    second_id = await create_task(
        manager,
        project.project_id,
        "Inspect Bearer second-secret-value and normalize API output.",
    )
    first_task = await manager.store.get_task(first_id)
    second_task = await manager.store.get_task(second_id)
    assert first_task is not None and second_task is not None
    first_profile = (await manager.get_task_planning(first_id)).current_profile
    second_profile = (await manager.get_task_planning(second_id)).current_profile
    manifests = repository_manifests(repository)
    first = deterministic_embedding(
        first_task, first_profile, manifests=manifests, verifier_ids=[]
    )
    second = deterministic_embedding(
        second_task, second_profile, manifests=manifests, verifier_ids=[]
    )
    assert first == second
    assert abs(sum(value * value for value in first.vector) - 1) < 1e-12
    persisted_shape = json.dumps(
        {
            "vector": first.vector,
            "input_digest": first.input_digest,
            "version": first.version,
        }
    )
    assert "first-secret-value" not in persisted_shape

    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "remote",
            "add",
            "origin",
            "https://user:token@GitHub.com/Owner/Repo.git/",
        ],
        check=True,
    )
    identity = await experience.repository_identity(repository, project.project_id)
    assert identity == sha256(b"github.com/owner/repo").hexdigest()
    assert identity == await experience.repository_identity(repository, "another-project")


async def test_p7_experience_api_surface(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    manager, dynamic, experience = services(tmp_path)
    app.state.manager = manager
    app.state.dynamic_workflows = dynamic
    app.state.experience = experience
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        project = (
            await client.post(
                "/api/v1/projects",
                json={"name": "P7 API", "repository_path": str(repository)},
            )
        ).json()
        enabled = await client.patch(
            f"/api/v2/projects/{project['project_id']}/features",
            json={
                "dynamic_workflows": True,
                "candidate_search": True,
                "experience_retrieval": True,
                "expected_revision": 1,
            },
        )
        assert enabled.status_code == 200
        task_payload = {
            "project_id": project["project_id"],
            "objective": "Add an operator-visible deterministic endpoint.",
            "task_type": "IMPLEMENT",
        }
        source_task = (await client.post("/api/v1/tasks", json=task_payload)).json()
        source_task_id = source_task["envelope"]["task_id"]
        source_run = await seed_succeeded_run(
            manager,
            task_id=source_task_id,
            project_id=project["project_id"],
        )
        source_run_id = source_run.run_id

        materialized = await client.post(
            f"/api/v2/runs/{source_run_id}/experiences", json={}
        )
        assert materialized.status_code == 201
        experience_id = materialized.json()["experience"]["experience_id"]
        assert (await client.get(f"/api/v2/experiences/{experience_id}")).status_code == 200

        target_task = (await client.post("/api/v1/tasks", json=task_payload)).json()
        target_task_id = target_task["envelope"]["task_id"]
        planning = await client.get(f"/api/v1/tasks/{target_task_id}/planning")
        queried = await client.post(
            "/api/v2/experiences/query",
            json={"task_id": target_task_id, "top_k": 5},
        )
        assert queried.status_code == 200
        match = queried.json()[0]
        selected = await client.post(
            f"/api/v2/tasks/{target_task_id}/experience-selections",
            json={
                "query_id": match["query_id"],
                "match_ids": [match["match_id"]],
                "expected_context_bundle_id": planning.json()["context_bundle"][
                    "context_bundle_id"
                ],
            },
        )
        assert selected.status_code == 201
        assert len(
            (await client.get(f"/api/v2/tasks/{target_task_id}/experience-selections")).json()
        ) == 1
        selected_matches = await client.get(
            f"/api/v2/tasks/{target_task_id}/experience-matches"
        )
        assert selected_matches.status_code == 200
        assert [item["match_id"] for item in selected_matches.json()] == [
            match["match_id"]
        ]
        retracted = await client.post(
            f"/api/v2/experiences/{experience_id}/retract",
            json={"reason": "API moderation fixture", "expected_revision": 1},
        )
        assert retracted.status_code == 200
        assert retracted.json()["retracted"] is True
