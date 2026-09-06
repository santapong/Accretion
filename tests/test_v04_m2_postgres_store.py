"""Real PostgreSQL evidence for M2's atomic routing persistence seam.

The database is supplied explicitly through ``ACCRETION_TEST_POSTGRES_URL``.  Every
domain id is freshly minted, so these tests are safe to rerun against the dedicated M2
database without dropping tables or deleting rows.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_v04_m0_postgres_store import build

from accretion.contracts import (
    AgentEvent,
    EventType,
    Project,
    Provider,
    Run,
    RunState,
    Task,
    TaskEnvelope,
    TaskType,
)
from accretion.contracts.routing import NodeContract, RoutingDecisionReceipt
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import MemoryStore, PostgresStore, StateStore

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
    pytest.mark.asyncio,
]


@asynccontextmanager
async def postgres_stores(count: int = 1) -> AsyncIterator[tuple[PostgresStore, ...]]:
    """Open genuinely independent engines/session factories over the same database."""

    assert POSTGRES_URL is not None
    engines = [create_engine(POSTGRES_URL) for _ in range(count)]
    stores = tuple(PostgresStore(create_session_factory(engine)) for engine in engines)
    try:
        yield stores
    finally:
        await asyncio.gather(*(engine.dispose() for engine in engines))


async def seed_run(store: StateStore, tmp_path: Path, marker: str) -> tuple[Project, Run]:
    project = Project(
        project_id=new_id("project"),
        name=f"M2 routing transaction {marker}",
        repository_path=tmp_path,
    )
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective=f"Prove the M2 routing transaction {marker}.",
            task_type=TaskType.IMPLEMENT,
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
    return project, run


async def seed_same_run(
    left: StateStore, right: StateStore, tmp_path: Path, marker: str
) -> tuple[Project, Run]:
    project, run = await seed_run(left, tmp_path, marker)
    task = await left.get_task(run.task_id)
    assert task is not None
    await right.create_project(project)
    await right.create_task(task)
    await right.create_run(run)
    return project, run


def node_contract(
    *, project: Project, run: Run, workspace_id: str, graph_id: str, marker: str
) -> NodeContract:
    return build(
        NodeContract,
        workspace_id=workspace_id,
        project_id=project.project_id,
        run_graph_id=graph_id,
        execution_instance_id=run.run_id,
        node_id=f"route-{marker}",
    )


def receipt_for(
    node: NodeContract, *, routing_request_id: str | None = None
) -> RoutingDecisionReceipt:
    return build(
        RoutingDecisionReceipt,
        workspace_id=node.workspace_id,
        project_id=node.project_id,
        node_contract_hash=node.immutable_hash,
        routing_request_id=routing_request_id or new_id("routing_request"),
    )


def routing_event(run: Run, marker: str) -> AgentEvent:
    return AgentEvent(
        event_id=new_id("event"),
        run_id=run.run_id,
        session_id=f"m2-{marker}",
        provider=Provider.FAKE,
        native_type=f"accretion/routing/{marker}",
        normalized_type=EventType.ROUTING_DECISION_CREATED,
        timestamp=datetime.now(UTC),
        correlation_id=run.run_id,
        payload={"writer": marker},
        adapter_version="fake-p2-v1",
    )


async def test_routing_transaction_rolls_back_v04_rows_and_event_with_memory_parity(
    tmp_path: Path,
) -> None:
    async with postgres_stores() as (postgres,):
        memory = MemoryStore()
        project, run = await seed_same_run(postgres, memory, tmp_path, "rollback")
        node = node_contract(
            project=project,
            run=run,
            workspace_id=new_id("workspace_entity"),
            graph_id=new_id("run_graph"),
            marker="rollback",
        )
        receipt = receipt_for(node)
        event = routing_event(run, "rollback")

        for store in (postgres, memory):
            with pytest.raises(RuntimeError, match="injected routing failure"):
                async with store.routing_transaction(run.run_id) as transaction:
                    await transaction.put_node_contract(node)
                    await transaction.put_routing_receipt(receipt)
                    await transaction.append_event(event)
                    raise RuntimeError("injected routing failure")

            assert await store.get_node_contract(node.contract_id) is None
            assert await store.get_routing_receipt(receipt.contract_id) is None
            assert await store.list_events(run.run_id) == []
            stored_run = await store.get_run(run.run_id)
            assert stored_run is not None
            assert stored_run.last_sequence == 0


async def test_independent_postgres_stores_serialize_same_run_amendments(
    tmp_path: Path,
) -> None:
    async with postgres_stores(2) as (first_store, second_store):
        _project, run = await seed_run(first_store, tmp_path, "serialization")
        first_has_lock = asyncio.Event()
        release_first = asyncio.Event()
        second_attempted = asyncio.Event()
        second_has_lock = asyncio.Event()

        async def first_writer() -> AgentEvent:
            async with first_store.routing_transaction(run.run_id) as transaction:
                stored = await transaction.append_event(routing_event(run, "first"))
                first_has_lock.set()
                await release_first.wait()
                return stored

        async def second_writer() -> AgentEvent:
            await first_has_lock.wait()
            second_attempted.set()
            async with second_store.routing_transaction(run.run_id) as transaction:
                second_has_lock.set()
                return await transaction.append_event(routing_event(run, "second"))

        first_task = asyncio.create_task(first_writer())
        second_task = asyncio.create_task(second_writer())
        try:
            await asyncio.wait_for(second_attempted.wait(), timeout=1)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(second_has_lock.wait(), timeout=0.1)
        finally:
            release_first.set()

        first_event, second_event = await asyncio.gather(first_task, second_task)
        assert [first_event.sequence, second_event.sequence] == [1, 2]
        events = await first_store.list_events(run.run_id)
        assert [event.sequence for event in events] == [1, 2]
        assert [event.payload["writer"] for event in events] == ["first", "second"]
        stored_run = await second_store.get_run(run.run_id)
        assert stored_run is not None
        assert stored_run.last_sequence == 2


async def test_duplicate_receipt_rolls_back_its_event_with_memory_parity(
    tmp_path: Path,
) -> None:
    async with postgres_stores() as (postgres,):
        memory = MemoryStore()
        project, run = await seed_same_run(postgres, memory, tmp_path, "uniqueness")
        node = node_contract(
            project=project,
            run=run,
            workspace_id=new_id("workspace_entity"),
            graph_id=new_id("run_graph"),
            marker="uniqueness",
        )
        request_id = new_id("routing_request")
        first_receipt = receipt_for(node, routing_request_id=request_id)
        duplicate_receipt = receipt_for(node, routing_request_id=request_id)

        for store in (postgres, memory):
            async with store.routing_transaction(run.run_id) as transaction:
                await transaction.put_node_contract(node)
                await transaction.put_routing_receipt(first_receipt)
                await transaction.append_event(routing_event(run, "accepted"))

            with pytest.raises(ValueError, match="already has receipt"):
                async with store.routing_transaction(run.run_id) as transaction:
                    await transaction.append_event(routing_event(run, "duplicate"))
                    await transaction.put_routing_receipt(duplicate_receipt)

            receipts = await store.list_routing_receipts(
                workspace_id=node.workspace_id, project_id=project.project_id
            )
            assert receipts == [first_receipt]
            events = await store.list_events(run.run_id)
            assert len(events) == 1
            assert events[0].payload["writer"] == "accepted"
            assert events[0].sequence == 1


async def test_graph_receipt_reader_is_workspace_scoped_with_memory_parity(
    tmp_path: Path,
) -> None:
    async with postgres_stores() as (postgres,):
        memory = MemoryStore()
        project, run = await seed_same_run(postgres, memory, tmp_path, "reader")
        target_workspace = new_id("workspace_entity")
        other_workspace = new_id("workspace_entity")
        target_graph = new_id("run_graph")
        other_graph = new_id("run_graph")
        nodes = (
            node_contract(
                project=project,
                run=run,
                workspace_id=target_workspace,
                graph_id=target_graph,
                marker="target",
            ),
            node_contract(
                project=project,
                run=run,
                workspace_id=target_workspace,
                graph_id=other_graph,
                marker="other-graph",
            ),
            node_contract(
                project=project,
                run=run,
                workspace_id=other_workspace,
                graph_id=target_graph,
                marker="other-workspace",
            ),
        )
        receipts = tuple(receipt_for(node) for node in nodes)

        for store in (postgres, memory):
            async with store.routing_transaction(run.run_id) as transaction:
                for node, receipt in zip(nodes, receipts, strict=True):
                    await transaction.put_node_contract(node)
                    await transaction.put_routing_receipt(receipt)

        postgres_target = await postgres.list_routing_receipts_for_run_graph(
            workspace_id=target_workspace, run_graph_id=target_graph
        )
        memory_target = await memory.list_routing_receipts_for_run_graph(
            workspace_id=target_workspace, run_graph_id=target_graph
        )
        assert postgres_target == memory_target == [receipts[0]]

        assert await postgres.list_routing_receipts_for_run_graph(
            workspace_id=target_workspace, run_graph_id=other_graph
        ) == await memory.list_routing_receipts_for_run_graph(
            workspace_id=target_workspace, run_graph_id=other_graph
        ) == [receipts[1]]
        assert await postgres.list_routing_receipts_for_run_graph(
            workspace_id=other_workspace, run_graph_id=target_graph
        ) == await memory.list_routing_receipts_for_run_graph(
            workspace_id=other_workspace, run_graph_id=target_graph
        ) == [receipts[2]]
