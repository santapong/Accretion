from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from accretion.contracts import (
    AcceptancePolicy,
    AgentEvent,
    ApprovalDecisionValue,
    ApprovalRecord,
    ApprovalStatus,
    Checkpoint,
    CheckpointKind,
    EventType,
    GraphNodeStatus,
    LoopBudgetRemaining,
    LoopExecution,
    LoopExecutionStatus,
    LoopSpec,
    LoopState,
    Project,
    Provider,
    Run,
    RunGraph,
    RunState,
    Task,
    TaskBudgets,
    TaskEnvelope,
    TemplateStatus,
)
from accretion.ids import has_prefix, new_id
from accretion.persistence.store import MemoryStore
from accretion.templates import (
    DIRECT_V1,
    FIXED_GRAPH_V1,
    compute_template_checksum,
    instantiate_run_graph,
)


def build_checkpoint(run: Run, *, sequence: int = 0) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=new_id("checkpoint"),
        run_id=run.run_id,
        kind=CheckpointKind.NODE_BOUNDARY,
        sequence=sequence,
        run_state=RunState.RUNNING,
        run_revision=run.revision,
    )


def control_event(run: Run) -> AgentEvent:
    return AgentEvent(
        event_id=new_id("event"),
        run_id=run.run_id,
        session_id=new_id("session"),
        provider=Provider.DETERMINISTIC,
        native_type="accretion/checkpoint-saved",
        normalized_type=EventType.CHECKPOINT_SAVED,
        correlation_id=run.run_id,
        adapter_version="test-v1",
    )


async def graph_store(tmp_path: Path) -> tuple[MemoryStore, Run]:
    store = MemoryStore()
    project = Project(project_id=new_id("project"), name="P3", repository_path=tmp_path)
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective="Exercise the P3 foundation.",
        )
    )
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
    )
    await store.create_project(project)
    await store.create_task(task)
    await store.create_run(run)
    return store, run


def test_p3_contract_versions_are_strict() -> None:
    with pytest.raises(ValidationError):
        Checkpoint.model_validate(
            {
                "schema_version": "2.0",
                "checkpoint_id": new_id("checkpoint"),
                "run_id": new_id("run"),
                "kind": "NODE_BOUNDARY",
                "sequence": 0,
                "run_state": "RUNNING",
                "run_revision": 0,
            }
        )
    with pytest.raises(ValidationError):
        RunGraph.model_validate(
            {
                "run_graph_id": new_id("run_graph"),
                "run_id": new_id("run"),
                "unknown_field": True,
            }
        )
    with pytest.raises(ValidationError):
        DIRECT_V1.model_copy(update={"nodes": []}).model_validate(
            DIRECT_V1.model_dump(mode="json") | {"nodes": []}
        )


@pytest.mark.parametrize("kind", ["workflow_template", "run_graph", "checkpoint"])
def test_p3_identifiers_have_stable_prefixes(kind: str) -> None:
    assert has_prefix(new_id(kind), kind)


@pytest.mark.parametrize("status", [TemplateStatus.DRAFT, TemplateStatus.RETIRED])
@pytest.mark.acceptance("V01-P3-001")
def test_non_validated_template_cannot_instantiate(status: TemplateStatus) -> None:
    template = DIRECT_V1.model_copy(update={"status": status})
    with pytest.raises(ValueError, match="VALIDATED"):
        instantiate_run_graph(
            template, run_id=new_id("run"), task_id=new_id("task"), budgets=TaskBudgets()
        )


def test_template_content_drift_fails_instantiation() -> None:
    tampered = FIXED_GRAPH_V1.model_copy(update={"required_verifiers": ["output-contract"]})
    with pytest.raises(ValueError, match="checksum"):
        instantiate_run_graph(
            tampered, run_id=new_id("run"), task_id=new_id("task"), budgets=TaskBudgets()
        )


async def test_template_upsert_is_idempotent_and_rejects_drift(tmp_path: Path) -> None:
    store, _ = await graph_store(tmp_path)
    stored = await store.upsert_workflow_template(DIRECT_V1)
    again = await store.upsert_workflow_template(DIRECT_V1)
    assert again == stored

    drifted = DIRECT_V1.model_copy(update={"checksum": "0" * 64})
    with pytest.raises(ValueError, match="drift"):
        await store.upsert_workflow_template(drifted)

    validated = await store.get_workflow_template("direct-v1")
    assert validated is not None
    assert validated.status is TemplateStatus.VALIDATED
    assert compute_template_checksum(validated) == validated.checksum


async def test_run_graph_is_unique_per_run_and_ids_are_immutable(tmp_path: Path) -> None:
    store, run = await graph_store(tmp_path)
    await store.upsert_workflow_template(FIXED_GRAPH_V1)
    graph = instantiate_run_graph(
        FIXED_GRAPH_V1,
        run_id=run.run_id,
        task_id=run.task_id,
        budgets=TaskBudgets(),
    )
    created = await store.create_run_graph(graph)
    assert created.graph_revision == 1

    with pytest.raises(ValueError, match="already has a run graph"):
        await store.create_run_graph(
            instantiate_run_graph(
                FIXED_GRAPH_V1,
                run_id=run.run_id,
                task_id=run.task_id,
                budgets=TaskBudgets(),
            )
        )

    node = graph.nodes[1].model_copy(update={"status": GraphNodeStatus.RUNNING})
    updated = await store.update_run_graph(
        graph.run_graph_id, nodes=[node], expected_revision=1
    )
    assert updated.graph_revision == 2
    assert updated.nodes[1].status is GraphNodeStatus.RUNNING

    with pytest.raises(ValueError, match="revision conflict"):
        await store.update_run_graph(graph.run_graph_id, nodes=[node], expected_revision=1)

    renamed = node.model_copy(update={"node_id": f"{run.run_id}:hijacked"})
    with pytest.raises(ValueError, match="immutable"):
        await store.update_run_graph(
            graph.run_graph_id, nodes=[renamed], expected_revision=2
        )


async def test_checkpoints_are_append_only_immutable_evidence(tmp_path: Path) -> None:
    store, run = await graph_store(tmp_path)
    first = await store.append_checkpoint(build_checkpoint(run), events=[control_event(run)])
    assert first.sequence == 1

    persisted_run = await store.get_run(run.run_id)
    assert persisted_run is not None and persisted_run.last_sequence == 1

    duplicate = await store.append_checkpoint(
        build_checkpoint(run, sequence=first.sequence)
    )
    assert duplicate.checkpoint_id == first.checkpoint_id

    conflicting = build_checkpoint(run, sequence=first.sequence).model_copy(
        update={"run_state": RunState.PAUSED}
    )
    with pytest.raises(ValueError, match="immutable"):
        await store.append_checkpoint(conflicting)

    second = await store.append_checkpoint(build_checkpoint(run), events=[control_event(run)])
    assert second.sequence == 2
    latest = await store.get_latest_checkpoint(run.run_id)
    assert latest is not None and latest.sequence == 2
    assert [item.sequence for item in await store.list_checkpoints(run.run_id)] == [1, 2]

    await store.update_run(run.run_id, RunState.SUCCEEDED)
    with pytest.raises(ValueError, match="terminal"):
        await store.append_checkpoint(build_checkpoint(run, sequence=99))


async def test_approvals_are_idempotent_and_decided_exactly_once(tmp_path: Path) -> None:
    store, run = await graph_store(tmp_path)
    approval = ApprovalRecord(
        approval_id=new_id("approval"),
        run_id=run.run_id,
        node_id=f"{run.run_id}:approve-plan",
        native_request_id="gate:approve-plan",
        method="accretion/gate",
        summary="Approve the plan.",
    )
    stored = await store.save_approval(approval)
    replayed = await store.save_approval(
        approval.model_copy(update={"approval_id": new_id("approval")})
    )
    assert replayed.approval_id == stored.approval_id

    decided = await store.decide_approval(stored.approval_id, ApprovalDecisionValue.APPROVE)
    assert decided.status is ApprovalStatus.APPROVED
    assert decided.decided_at is not None

    with pytest.raises(ValueError, match="already decided"):
        await store.decide_approval(stored.approval_id, ApprovalDecisionValue.DENY)

    pending = await store.list_approvals(run.run_id, ApprovalStatus.PENDING)
    assert pending == []
    assert await store.list_approvals(run.run_id) == [decided]


async def test_loop_executions_are_node_scoped(tmp_path: Path) -> None:
    store, run = await graph_store(tmp_path)
    policy = AcceptancePolicy(
        policy_id=new_id("acceptance_policy"),
        required_verifiers=["output-contract"],
        require_human_if_risk_gte=None,
    )
    await store.save_acceptance_policy(policy)

    def loop_for(node_key: str, attempt: int = 1) -> LoopExecution:
        return LoopExecution(
            loop_execution_id=new_id("loop_execution"),
            run_id=run.run_id,
            node_key=node_key,
            attempt=attempt,
            spec=LoopSpec(loop_id=new_id("loop")),
            state=LoopState(
                budget_remaining=LoopBudgetRemaining(
                    wall_time_seconds=60, tool_calls=10, turns=4, iterations=2
                )
            ),
            acceptance_policy_ref=policy.policy_id,
            status=LoopExecutionStatus.RUNNING,
        )

    experiment = await store.create_loop_execution(loop_for("experiment"))
    develop = await store.create_loop_execution(loop_for("develop"))
    with pytest.raises(ValueError, match="already has loop attempt"):
        await store.create_loop_execution(loop_for("experiment"))

    replan_attempt = await store.create_loop_execution(loop_for("experiment", attempt=2))
    resolved = await store.get_loop_execution_for_node(run.run_id, "experiment")
    assert resolved is not None
    assert resolved.loop_execution_id == replan_attempt.loop_execution_id
    first_attempt = await store.get_loop_execution_for_node(run.run_id, "experiment", attempt=1)
    assert first_attempt is not None
    assert first_attempt.loop_execution_id == experiment.loop_execution_id

    executions = await store.list_loop_executions_for_run(run.run_id)
    assert {item.loop_execution_id for item in executions} == {
        experiment.loop_execution_id,
        develop.loop_execution_id,
        replan_attempt.loop_execution_id,
    }

    persisted_run = await store.get_run(run.run_id)
    assert persisted_run is not None
    assert persisted_run.loop_execution_id == replan_attempt.loop_execution_id


def test_p3_timestamps_are_timezone_aware() -> None:
    checkpoint = Checkpoint(
        checkpoint_id=new_id("checkpoint"),
        run_id=new_id("run"),
        kind=CheckpointKind.NODE_BOUNDARY,
        sequence=0,
        run_state=RunState.RUNNING,
        run_revision=0,
    )
    assert checkpoint.created_at.tzinfo is not None
    assert datetime.now(UTC).tzinfo is not None
