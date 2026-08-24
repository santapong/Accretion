from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path

import pytest

from accretion.contracts import Project, Provider, Run, RunState, Task, TaskEnvelope, TaskType
from accretion.experience.models import (
    CompatibilityAssessment,
    Experience,
    ExperienceEmbedding,
    ExperienceMatch,
    ExperiencePolarity,
    ExperienceQuery,
    ExperienceSelection,
    ExperienceSourceKind,
    ExperienceTrust,
    MatchDisposition,
    ModerationAction,
    SeedValidationStatus,
    TrajectorySeed,
    TrajectorySegment,
    TrajectorySegmentKind,
)
from accretion.ids import new_id
from accretion.orchestration.models import (
    CandidateSourceKind,
    CandidateTrajectory,
    SearchBudgetEnvelope,
    SearchMode,
    SearchPlan,
    SearchRecord,
)
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import PostgresStore
from accretion.planning import build_initial_planning

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


@pytest.mark.acceptance("V02-P7-001")
async def test_p7_vector_evidence_round_trips_and_retracts(tmp_path: Path) -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    project = Project(
        project_id=new_id("project"), name="P7 PostgreSQL", repository_path=tmp_path
    )
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective="Reuse verified local experience.",
            task_type=TaskType.IMPLEMENT,
        )
    )
    prompt, context, profile, decision = build_initial_planning(task, project)
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.SUCCEEDED,
    )
    experience = Experience(
        experience_id=new_id("experience"),
        project_id=project.project_id,
        repository_identity=digest(project.project_id),
        task_id=task.envelope.task_id,
        task_type=TaskType.IMPLEMENT,
        task_family="python-service",
        source_kind=ExperienceSourceKind.RUN,
        source_run_id=run.run_id,
        source_commit="a" * 40,
        architecture_version="2.0",
        manifest_digest=digest("manifest"),
        manifest_paths=["pyproject.toml"],
        policy_digest=digest("policy"),
        verifier_digest=digest("verifier"),
        prompt_digest=digest("prompt"),
        context_digest=digest("context"),
        tool_profile_digest=digest("tools"),
        provider=Provider.FAKE,
        runtime_model="fake",
        runtime_version="test",
        trust=ExperienceTrust.HIGH,
        polarity=ExperiencePolarity.POSITIVE,
        outcome="VERIFIED_SUCCESS",
        content_digest=digest("experience"),
    )
    segment = TrajectorySegment(
        segment_id=new_id("trajectory_segment"),
        experience_id=experience.experience_id,
        ordinal=1,
        kind=TrajectorySegmentKind.WORKFLOW_PATH,
        content={"nodes": ["plan", "act", "verify"]},
        content_digest=digest("segment"),
    )
    vector = [1.0] + [0.0] * 383
    embedding = ExperienceEmbedding(
        embedding_id=new_id("experience_embedding"),
        experience_id=experience.experience_id,
        input_digest=digest("embedding-input"),
        vector=vector,
    )
    query = ExperienceQuery(
        query_id=new_id("experience_query"),
        project_id=project.project_id,
        task_id=task.envelope.task_id,
        task_profile_id=profile.profile_id,
        repository_identity=experience.repository_identity,
        source_commit=experience.source_commit,
        architecture_version=experience.architecture_version,
        manifest_digest=experience.manifest_digest,
        manifest_paths=experience.manifest_paths,
        policy_digest=experience.policy_digest,
        verifier_digest=experience.verifier_digest,
        prompt_digest=experience.prompt_digest,
        context_digest=experience.context_digest,
        tool_profile_digest=experience.tool_profile_digest,
        embedding_input_digest=digest("query-input"),
    )
    match = ExperienceMatch(
        match_id=new_id("experience_match"),
        query_id=query.query_id,
        experience_id=experience.experience_id,
        rank=1,
        trust=experience.trust,
        polarity=experience.polarity,
        assessment=CompatibilityAssessment(
            semantic_score=1,
            environment_score=1,
            version_score=1,
            freshness_score=1,
            final_score=1,
            transfer_risk=0,
            disposition=MatchDisposition.ACCEPTED,
            replay_eligible=True,
        ),
    )
    resulting_context_id = new_id("context")
    selection = ExperienceSelection(
        selection_id=new_id("experience_selection"),
        task_id=task.envelope.task_id,
        query_id=query.query_id,
        match_ids=[match.match_id],
        expected_context_bundle_id=context.context_bundle_id,
        resulting_context_bundle_id=resulting_context_id,
        selected_by="postgres-test",
    )
    revised_context = context.model_copy(
        update={
            "schema_version": "2.0",
            "context_bundle_id": resulting_context_id,
            "version": "context-bundle-v2",
            "supersedes_context_bundle_id": context.context_bundle_id,
            "experience_query_id": query.query_id,
            "experience_match_refs": [match.match_id],
            "experience_refs": [experience.experience_id],
        }
    )
    search_id = new_id("search")
    candidate_id = new_id("search_candidate")
    seed_id = new_id("trajectory_seed")
    budget = SearchBudgetEnvelope(
        wall_time_seconds=120, max_turns=4, max_tool_calls=12
    )
    search = SearchRecord(
        plan=SearchPlan(
            search_id=search_id,
            run_id=run.run_id,
            parent_node_id="act",
            graph_revision=1,
            mode=SearchMode.REPLAY_BRANCH,
            branch_count=2,
            max_parallel=2,
            per_branch_budget=budget,
            total_budget=budget.model_copy(
                update={"wall_time_seconds": 240, "max_turns": 8, "max_tool_calls": 24}
            ),
            replay_seed_match_ids=[match.match_id],
            verifier_policy_ref="search-verifier-v2",
            requested_by="postgres-test",
        )
    )
    candidate = CandidateTrajectory(
        candidate_id=candidate_id,
        search_id=search_id,
        run_id=run.run_id,
        ordinal=2,
        provider=Provider.FAKE,
        runtime_id="fake-runtime",
        runtime_model="fake",
        runtime_version="test",
        source_kind=CandidateSourceKind.REPLAY,
        replay_seed_id=seed_id,
        source_experience_id=experience.experience_id,
        source_match_id=match.match_id,
        trajectory_segment_refs=[segment.segment_id],
    )
    seed = TrajectorySeed(
        seed_id=seed_id,
        search_id=search_id,
        candidate_id=candidate_id,
        match_id=match.match_id,
        experience_id=experience.experience_id,
        segment_ids=[segment.segment_id],
        procedural_guidance=["Follow the verified workflow path."],
        required_revalidations=["source commit remains compatible"],
        validation_status=SeedValidationStatus.ELIGIBLE,
    )
    try:
        await store.create_project(project)
        await store.create_task_with_planning(task, prompt, context, profile, decision)
        await store.create_run(run)
        await store.save_experience(experience, [segment], embedding)
        await store.save_experience_query(query, vector)
        await store.save_experience_matches([match])
        await store.revise_context_with_experience(selection, revised_context)
        await store.create_search(search)
        await store.save_search_candidate(candidate)
        await store.save_trajectory_seed(seed)

        assert await store.get_experience(experience.experience_id) == experience
        assert await store.list_trajectory_segments(experience.experience_id) == [segment]
        stored_embedding = await store.get_experience_embedding(experience.experience_id)
        assert stored_embedding is not None
        assert stored_embedding.vector == vector
        assert await store.get_experience_query(query.query_id) == (query, vector)
        assert await store.nearest_experience_embeddings(
            experience.repository_identity, vector, limit=5
        ) == [(experience.experience_id, 0.0)]
        assert await store.list_experience_matches(query.query_id) == [match]
        assert await store.list_experience_selections(task.envelope.task_id) == [selection]
        planning = await store.get_task_planning(task.envelope.task_id)
        assert planning is not None
        assert planning.context_bundle == revised_context
        assert planning.context_history == [context, revised_context]
        assert await store.list_trajectory_seeds(search_id) == [seed]

        action = ModerationAction(
            action_id=new_id("moderation_action"),
            experience_id=experience.experience_id,
            reason="The source was invalidated after operator review.",
            expected_revision=1,
            resulting_revision=2,
            actor="postgres-test",
        )
        retracted = await store.retract_experience(action)
        assert retracted.retracted and retracted.revision == 2
        assert await store.list_moderation_actions(experience.experience_id) == [action]
        with pytest.raises(ValueError, match="revision conflict"):
            await store.retract_experience(
                action.model_copy(update={"action_id": new_id("moderation_action")})
            )
    finally:
        await engine.dispose()
