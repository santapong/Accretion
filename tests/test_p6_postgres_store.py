from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from accretion.contracts import Project, Provider, Run, RunState, Task, TaskEnvelope
from accretion.ids import new_id
from accretion.orchestration.models import (
    CandidateScore,
    CandidateTrajectory,
    SearchBudgetEnvelope,
    SearchMode,
    SearchPlan,
    SearchPromotionRecord,
    SearchRecord,
    SearchStatus,
)
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import PostgresStore

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]


async def test_p6_search_evidence_round_trips_in_postgres(tmp_path: Path) -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    project = Project(
        project_id=new_id("project"), name="P6 PostgreSQL", repository_path=tmp_path
    )
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective="Persist bounded search evidence.",
        )
    )
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.PENDING,
    )
    search_id = new_id("search")
    candidate_id = new_id("search_candidate")
    budget = SearchBudgetEnvelope(
        wall_time_seconds=120, max_turns=4, max_tool_calls=12
    )
    record = SearchRecord(
        plan=SearchPlan(
            search_id=search_id,
            run_id=run.run_id,
            parent_node_id="act",
            graph_revision=1,
            mode=SearchMode.BEST_OF_N,
            branch_count=2,
            max_parallel=2,
            per_branch_budget=budget,
            total_budget=budget.model_copy(
                update={"wall_time_seconds": 240, "max_turns": 8, "max_tool_calls": 24}
            ),
            verifier_policy_ref="search-verifier-v2",
            requested_by="postgres-test",
        )
    )
    candidate = CandidateTrajectory(
        candidate_id=candidate_id,
        search_id=search_id,
        run_id=run.run_id,
        ordinal=1,
        provider=Provider.FAKE,
        runtime_id="fake-runtime",
        runtime_model="fake",
        runtime_version="test",
    )
    score = CandidateScore(
        score_id=new_id("candidate_score"),
        search_id=search_id,
        candidate_id=candidate_id,
        verifier_policy_ref="search-verifier-v2",
        verifier_status="PASS",
        eligible=True,
        quality_score=1,
        cost_proxy=0.1,
        latency_proxy=0.1,
        risk_score=0,
        total_score=0.98,
        explanation="verified fixture",
    )
    promotion = SearchPromotionRecord(
        promotion_id=new_id("search_promotion"),
        search_id=search_id,
        candidate_id=candidate_id,
        run_id=run.run_id,
        patch_sha256="a" * 64,
        parent_before_sha256="b" * 64,
    )
    try:
        await store.create_project(project)
        await store.create_task(task)
        await store.create_run(run)
        await store.create_search(record)
        await store.save_search_candidate(candidate)
        await store.save_candidate_score(score)
        await store.save_search_promotion(promotion)

        assert await store.get_search(search_id) == record
        assert await store.list_search_candidates(search_id) == [candidate]
        assert await store.list_candidate_scores(search_id) == [score]
        assert await store.get_search_promotion(search_id) == promotion

        updated = await store.update_search(
            record.model_copy(
                update={
                    "status": SearchStatus.RUNNING,
                    "started_at": datetime.now(UTC),
                }
            ),
            expected_revision=1,
        )
        assert updated.revision == 2
        with pytest.raises(ValueError, match="immutable"):
            await store.save_candidate_score(score.model_copy(update={"total_score": 0.5}))
    finally:
        await engine.dispose()
