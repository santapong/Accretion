from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

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


class P5Records(NamedTuple):
    """Everything one round-trip wrote, so a later pass can re-read its own rows."""

    run_id: str
    proposal: WorkflowProposal
    validation: GraphValidationResult
    revision: RunGraphRevision
    request: ReplanRequest
    decision: RuntimeDecision


async def write_and_read_p5_records(
    store: PostgresStore, tmp_path: Path, marker: str
) -> P5Records:
    """Write one full set of P5 records and assert every id-scoped read sees it."""

    project = Project(
        project_id=new_id("project"),
        name=f"P5 PostgreSQL {marker}",
        repository_path=tmp_path / f"repo-{marker}",
    )
    project.repository_path.mkdir()
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective=f"Persist P5 evidence ({marker}).",
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
        rationale_summary=f"PostgreSQL round trip ({marker}).",
        confidence=1,
    )
    validation = GraphValidationResult(
        validation_id=new_id("graph_validation"),
        proposal_id=proposal.proposal_id,
        status=GraphValidationStatus.ACCEPT,
        normalized_graph_hash="a" * 64,
    )
    await store.create_project(project)
    await store.create_task(task)
    await store.create_run(run)
    # Use the template the store returns, not the module constant: a second pass
    # against the same database finds the existing row and returns it, and its
    # template_record_id is the one minted by the first pass.
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
        requested_by=f"postgres-test-{marker}",
    )
    await store.save_replan_request(request)
    decision = RuntimeDecision(
        decision_id=new_id("runtime_decision"),
        run_id=run.run_id,
        node_id=f"{run.run_id}:workflow",
        candidates=[],
        selected_runtime=None,
        selected_reason=f"no candidate in persistence fixture ({marker})",
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
    return P5Records(
        run_id=run.run_id,
        proposal=proposal,
        validation=validation,
        revision=revision,
        request=request,
        decision=decision,
    )


async def assert_reads_exactly_its_own_rows(
    store: PostgresStore, records: P5Records
) -> None:
    """Every id-scoped read returns that pass's single row and nothing else."""

    assert (
        await store.get_workflow_proposal(records.proposal.proposal_id) == records.proposal
    )
    assert await store.list_graph_validations(records.proposal.proposal_id) == [
        records.validation
    ]
    assert await store.list_graph_revisions(records.run_id) == [records.revision]
    assert await store.list_replan_requests(records.run_id) == [records.request]
    assert await store.list_runtime_decisions(records.run_id) == [records.decision]


@pytest.mark.acceptance("V02-P5-007", "V02-P5-010")
async def test_p5_postgres_records_round_trip_and_remain_immutable(tmp_path: Path) -> None:
    """Round-trip the P5 records against a real PostgreSQL twice over, in one test.

    Integration tests run against one long-lived database that nobody truncates
    between runs, so isolation cannot be assumed --- it has to be demonstrated. Two
    complete passes are written through the same store, and afterwards *both* passes'
    id-scoped reads are asserted to return exactly their own single row. A store that
    leaked rows across passes, or a key that stopped being per-pass unique, makes the
    second block of assertions fail; the uuid marker keeps the human-readable payload
    distinguishable when it does.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    try:
        first = await write_and_read_p5_records(store, tmp_path, uuid.uuid4().hex[:12])
        second = await write_and_read_p5_records(store, tmp_path, uuid.uuid4().hex[:12])

        assert first.run_id != second.run_id
        # The second pass must not have disturbed the first, and the first must not
        # be visible through the second's ids.
        await assert_reads_exactly_its_own_rows(store, first)
        await assert_reads_exactly_its_own_rows(store, second)
    finally:
        await engine.dispose()
