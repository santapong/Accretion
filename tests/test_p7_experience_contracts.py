from __future__ import annotations

from hashlib import sha256

import pytest
from pydantic import ValidationError

from accretion.contracts import Provider, TaskType
from accretion.experience.models import (
    CompatibilityAssessment,
    Experience,
    ExperienceEmbedding,
    ExperienceMatch,
    ExperiencePolarity,
    ExperienceQuery,
    ExperienceSourceKind,
    ExperienceTrust,
    MatchDisposition,
    ModerationAction,
    TrajectorySegment,
    TrajectorySegmentKind,
)
from accretion.ids import new_id
from accretion.orchestration.models import (
    SearchBudgetEnvelope,
    SearchMode,
    SearchPlan,
)
from accretion.persistence.store import MemoryStore


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def positive_experience(*, experience_id: str | None = None) -> Experience:
    return Experience(
        experience_id=experience_id or new_id("experience"),
        project_id=new_id("project"),
        repository_identity=digest("repository"),
        task_id=new_id("task"),
        task_type=TaskType.IMPLEMENT,
        task_family="python-service",
        source_kind=ExperienceSourceKind.RUN,
        source_run_id=new_id("run"),
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
        content_digest=digest("content"),
    )


def test_contracts_enforce_trust_and_replay_search_bounds() -> None:
    experience = positive_experience()
    with pytest.raises(ValidationError, match="positive replay evidence"):
        Experience.model_validate(
            {**experience.model_dump(), "trust": ExperienceTrust.MEDIUM}
        )

    budget = SearchBudgetEnvelope(
        wall_time_seconds=120, max_turns=4, max_tool_calls=12
    )
    plan = SearchPlan(
        search_id=new_id("search"),
        run_id=new_id("run"),
        parent_node_id="act",
        graph_revision=1,
        mode=SearchMode.REPLAY_BRANCH,
        branch_count=3,
        max_parallel=2,
        per_branch_budget=budget,
        total_budget=budget.model_copy(
            update={"wall_time_seconds": 360, "max_turns": 12, "max_tool_calls": 36}
        ),
        replay_seed_match_ids=[new_id("experience_match"), new_id("experience_match")],
        negative_guidance_match_ids=[new_id("experience_match")],
        verifier_policy_ref="search-verifier-v2",
        requested_by="operator",
    )
    assert plan.branch_count == 1 + len(plan.replay_seed_match_ids)

    with pytest.raises(ValidationError, match="allowed only for replay"):
        SearchPlan.model_validate(
            {
                **plan.model_dump(),
                "mode": SearchMode.BEST_OF_N,
                "branch_count": 2,
            }
        )


async def test_memory_store_keeps_experience_immutable_and_moderation_append_only() -> None:
    store = MemoryStore()
    experience = positive_experience()
    segment = TrajectorySegment(
        segment_id=new_id("trajectory_segment"),
        experience_id=experience.experience_id,
        ordinal=1,
        kind=TrajectorySegmentKind.WORKFLOW_PATH,
        content={"nodes": ["plan", "act", "verify"]},
        content_digest=digest("segment"),
    )
    embedding = ExperienceEmbedding(
        embedding_id=new_id("experience_embedding"),
        experience_id=experience.experience_id,
        input_digest=digest("embedding-input"),
        vector=[1.0] + [0.0] * 383,
    )

    assert await store.save_experience(experience, [segment], embedding) == experience
    assert await store.save_experience(experience, [segment], embedding) == experience
    with pytest.raises(ValueError, match="conflicting"):
        await store.save_experience(
            experience.model_copy(update={"content_digest": digest("different")}),
            [segment],
            embedding,
        )

    query = ExperienceQuery(
        query_id=new_id("experience_query"),
        project_id=experience.project_id,
        task_id=experience.task_id,
        task_profile_id=new_id("profile"),
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
    await store.save_experience_query(query, embedding.vector)
    assessment = CompatibilityAssessment(
        semantic_score=1,
        environment_score=1,
        version_score=1,
        freshness_score=1,
        final_score=1,
        transfer_risk=0,
        disposition=MatchDisposition.ACCEPTED,
        replay_eligible=True,
    )
    match = ExperienceMatch(
        match_id=new_id("experience_match"),
        query_id=query.query_id,
        experience_id=experience.experience_id,
        rank=1,
        trust=experience.trust,
        polarity=experience.polarity,
        assessment=assessment,
    )
    assert await store.save_experience_matches([match]) == [match]

    action = ModerationAction(
        action_id=new_id("moderation_action"),
        experience_id=experience.experience_id,
        reason="Source was invalidated after review.",
        expected_revision=1,
        resulting_revision=2,
        actor="operator",
    )
    retracted = await store.retract_experience(action)
    assert retracted.retracted and retracted.revision == 2
    assert await store.list_moderation_actions(experience.experience_id) == [action]
    assert await store.list_experiences(project_id=experience.project_id) == []
    assert await store.list_experiences(
        project_id=experience.project_id, include_retracted=True
    ) == [retracted]
