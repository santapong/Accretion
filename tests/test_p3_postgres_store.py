from __future__ import annotations

import os
from pathlib import Path

import pytest

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
    RunState,
    Task,
    TaskBudgets,
    TaskEnvelope,
    TemplateStatus,
)
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import PostgresStore
from accretion.templates import (
    FIXED_GRAPH_V1,
    HYBRID_RD_V1,
    instantiate_run_graph,
    seed_templates,
)

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]


async def seeded_run(store: PostgresStore, tmp_path: Path) -> Run:
    project = Project(
        project_id=new_id("project"), name="P3 PostgreSQL", repository_path=tmp_path
    )
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective="Persist a run graph.",
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
    return run


async def test_postgres_templates_graphs_checkpoints_and_approvals(tmp_path: Path) -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    try:
        seeded = await seed_templates(store)
        assert len(seeded) == 5
        again = await seed_templates(store)
        assert [item.template_record_id for item in again] == [
            item.template_record_id for item in seeded
        ]
        validated = await store.get_workflow_template("fixed-graph-v1")
        assert validated is not None and validated.status is TemplateStatus.VALIDATED
        assert len(await store.list_workflow_templates(TemplateStatus.VALIDATED)) >= 5

        drifted = FIXED_GRAPH_V1.model_copy(update={"checksum": "0" * 64})
        with pytest.raises(ValueError, match="drift"):
            await store.upsert_workflow_template(drifted)

        run = await seeded_run(store, tmp_path)
        graph = instantiate_run_graph(
            validated, run_id=run.run_id, task_id=run.task_id, budgets=TaskBudgets()
        )
        await store.create_run_graph(graph)
        loaded = await store.get_run_graph(run.run_id)
        assert loaded is not None
        assert loaded.graph_revision == 1
        assert [node.key for node in loaded.nodes] == [node.key for node in graph.nodes]
        assert [edge.key for edge in loaded.edges] == [edge.key for edge in graph.edges]

        with pytest.raises(ValueError, match="already has a run graph"):
            await store.create_run_graph(
                instantiate_run_graph(
                    validated, run_id=run.run_id, task_id=run.task_id, budgets=TaskBudgets()
                )
            )

        running = loaded.nodes[1].model_copy(update={"status": GraphNodeStatus.RUNNING})
        updated = await store.update_run_graph(
            loaded.run_graph_id, nodes=[running], expected_revision=1
        )
        assert updated.graph_revision == 2
        assert updated.nodes[1].status is GraphNodeStatus.RUNNING
        with pytest.raises(ValueError, match="revision conflict"):
            await store.update_run_graph(
                loaded.run_graph_id, nodes=[running], expected_revision=1
            )

        # P3 node ids exceed the pre-P3 40-character column; the widened
        # agent_events.node_id must accept a 49-character identifier.
        long_node_id = f"{run.run_id}:experiment-observe"
        assert len(long_node_id) == 49
        stored_event = await store.append_event(
            AgentEvent(
                event_id=new_id("event"),
                run_id=run.run_id,
                session_id=new_id("session"),
                provider=Provider.DETERMINISTIC,
                native_type="accretion/node-entered",
                normalized_type=EventType.NODE_ENTERED,
                correlation_id=run.run_id,
                node_id=long_node_id,
                payload={"status": "RUNNING"},
                adapter_version="test-v1",
            )
        )
        assert stored_event.sequence == 1

        checkpoint = Checkpoint(
            checkpoint_id=new_id("checkpoint"),
            run_id=run.run_id,
            kind=CheckpointKind.NODE_BOUNDARY,
            sequence=0,
            run_state=RunState.RUNNING,
            run_revision=0,
            run_graph_id=loaded.run_graph_id,
            graph_revision=2,
        )
        saved = await store.append_checkpoint(
            checkpoint,
            events=[
                AgentEvent(
                    event_id=new_id("event"),
                    run_id=run.run_id,
                    session_id=new_id("session"),
                    provider=Provider.DETERMINISTIC,
                    native_type="accretion/checkpoint-saved",
                    normalized_type=EventType.CHECKPOINT_SAVED,
                    correlation_id=run.run_id,
                    node_id=long_node_id,
                    adapter_version="test-v1",
                )
            ],
        )
        assert saved.sequence == 2
        latest = await store.get_latest_checkpoint(run.run_id)
        assert latest is not None and latest.sequence == 2
        conflicting = checkpoint.model_copy(
            update={"checkpoint_id": new_id("checkpoint"), "sequence": 2, "run_revision": 9}
        )
        with pytest.raises(ValueError, match="immutable"):
            await store.append_checkpoint(conflicting)

        approval = ApprovalRecord(
            approval_id=new_id("approval"),
            run_id=run.run_id,
            node_id=f"{run.run_id}:approve-plan",
            native_request_id="gate:approve-plan",
            method="accretion/gate",
            summary="Approve the plan.",
        )
        stored = await store.save_approval(approval)
        replay = await store.save_approval(
            approval.model_copy(update={"approval_id": new_id("approval")})
        )
        assert replay.approval_id == stored.approval_id
        decided = await store.decide_approval(
            stored.approval_id, ApprovalDecisionValue.APPROVE
        )
        assert decided.status is ApprovalStatus.APPROVED
        with pytest.raises(ValueError, match="already decided"):
            await store.decide_approval(stored.approval_id, ApprovalDecisionValue.DENY)
    finally:
        await engine.dispose()


async def test_postgres_loop_executions_are_node_scoped(tmp_path: Path) -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    try:
        run = await seeded_run(store, tmp_path)
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

        loops = {node.key: node for node in HYBRID_RD_V1.nodes if node.loop is not None}
        assert set(loops) == {"experiment", "develop"}
        experiment = await store.create_loop_execution(loop_for("experiment"))
        develop = await store.create_loop_execution(loop_for("develop"))
        with pytest.raises(ValueError, match="already has loop attempt"):
            await store.create_loop_execution(loop_for("develop"))
        second_attempt = await store.create_loop_execution(loop_for("experiment", attempt=2))

        latest = await store.get_loop_execution_for_node(run.run_id, "experiment")
        assert latest is not None
        assert latest.loop_execution_id == second_attempt.loop_execution_id
        first = await store.get_loop_execution_for_node(run.run_id, "experiment", attempt=1)
        assert first is not None
        assert first.loop_execution_id == experiment.loop_execution_id
        everything = await store.list_loop_executions_for_run(run.run_id)
        assert {item.loop_execution_id for item in everything} == {
            experiment.loop_execution_id,
            develop.loop_execution_id,
            second_attempt.loop_execution_id,
        }
    finally:
        await engine.dispose()
