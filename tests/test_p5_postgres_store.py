from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from accretion.contracts import (
    GraphEdgeKind,
    GraphNodeKind,
    Project,
    Provider,
    Run,
    RunState,
    Task,
    TaskBudgets,
    TaskEnvelope,
)
from accretion.ids import new_id
from accretion.orchestration.models import (
    DynamicWorkflowEdgeSpec,
    DynamicWorkflowNodeSpec,
    GraphValidationResult,
    GraphValidationStatus,
    ProjectFeatureSettings,
    ReplanReason,
    ReplanRequest,
    RunGraphRevision,
    RuntimeDecision,
    WorkflowProposal,
)
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import PostgresStore
from accretion.templates import DIRECT_V1, instantiate_run_graph

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]


@pytest.mark.acceptance("V02-P5-007", "V02-P5-010")
async def test_p5_postgres_records_round_trip_and_remain_immutable(tmp_path: Path) -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    project = Project(
        project_id=new_id("project"), name="P5 PostgreSQL", repository_path=tmp_path
    )
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective="Persist P5 evidence.",
        )
    )
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.PENDING,
    )
    start = DynamicWorkflowNodeSpec(
        local_id="start", kind=GraphNodeKind.TASK, objective="Start."
    )
    complete = DynamicWorkflowNodeSpec(
        local_id="complete", kind=GraphNodeKind.TERMINAL, objective="Complete."
    )
    edge = DynamicWorkflowEdgeSpec(
        local_id="start-complete",
        source="start",
        target="complete",
        kind=GraphEdgeKind.NORMAL,
    )
    proposal = WorkflowProposal(
        proposal_id=new_id("workflow_proposal"),
        task_id=task.envelope.task_id,
        run_id=run.run_id,
        objective=task.envelope.objective,
        nodes=[start, complete],
        edges=[edge],
        rationale_summary="PostgreSQL round trip.",
        confidence=1,
    )
    validation = GraphValidationResult(
        validation_id=new_id("graph_validation"),
        proposal_id=proposal.proposal_id,
        status=GraphValidationStatus.ACCEPT,
        normalized_graph_hash="a" * 64,
    )
    try:
        await store.create_project(project)
        await store.create_task(task)
        await store.create_run(run)
        # Use the template the store returns, not the module constant: a second run
        # against the same database finds the existing row and returns it, and its
        # template_record_id is the one minted by the first run's process.
        stored_template = await store.upsert_workflow_template(DIRECT_V1)
        graph = instantiate_run_graph(
            stored_template,
            run_id=run.run_id,
            task_id=task.envelope.task_id,
            budgets=TaskBudgets(),
        )
        await store.create_run_graph(graph)

        features = await store.update_project_features(
            ProjectFeatureSettings(project_id=project.project_id, dynamic_workflows=True),
            expected_revision=1,
        )
        assert features.revision == 2
        await store.save_workflow_proposal(proposal)
        await store.save_graph_validation(validation)
        revision = RunGraphRevision(
            revision_id=new_id("graph_revision"),
            run_graph_id=graph.run_graph_id,
            run_id=run.run_id,
            revision=1,
            proposal_id=proposal.proposal_id,
            reason=ReplanReason.INITIAL,
            nodes=proposal.nodes,
            edges=proposal.edges,
            normalized_graph_hash="a" * 64,
            activated_at=datetime.now(UTC),
        )
        await store.save_graph_revision(revision)
        request = ReplanRequest(
            replan_request_id=new_id("replan_request"),
            run_id=run.run_id,
            based_on_graph_revision=1,
            reason=ReplanReason.HUMAN_REQUEST,
            requested_by="postgres-test",
        )
        await store.save_replan_request(request)
        decision = RuntimeDecision(
            decision_id=new_id("runtime_decision"),
            run_id=run.run_id,
            node_id=f"{run.run_id}:workflow",
            candidates=[],
            selected_runtime=None,
            selected_reason="no candidate in persistence fixture",
        )
        await store.save_runtime_decision(decision)

        assert await store.get_workflow_proposal(proposal.proposal_id) == proposal
        assert await store.list_graph_validations(proposal.proposal_id) == [validation]
        assert await store.list_graph_revisions(run.run_id) == [revision]
        assert await store.list_replan_requests(run.run_id) == [request]
        assert await store.list_runtime_decisions(run.run_id) == [decision]
        with pytest.raises(ValueError, match="immutable"):
            await store.save_workflow_proposal(
                proposal.model_copy(update={"confidence": 0.5})
            )
    finally:
        await engine.dispose()
