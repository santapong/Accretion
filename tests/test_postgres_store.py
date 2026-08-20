from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from accretion.contracts import EventType, Project, Provider, Run, RunState, Task, TaskEnvelope
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.side_effects import PostgresSideEffectLedger
from accretion.persistence.store import PostgresStore
from accretion.runtimes.common import make_event

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]


async def test_postgres_store_round_trip_and_event_sequence() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    project_id = new_id("project")
    task_id = new_id("task")
    run_id = new_id("run")
    session_id = new_id("session")

    try:
        project = Project(
            project_id=project_id,
            name="PostgreSQL integration",
            repository_path=Path.cwd(),
        )
        task = Task(
            envelope=TaskEnvelope(
                task_id=task_id,
                project_id=project_id,
                objective="Exercise durable state",
            )
        )
        run = Run(
            run_id=run_id,
            task_id=task_id,
            project_id=project_id,
            provider=Provider.FAKE,
            state=RunState.PENDING,
        )

        await store.create_project(project)
        await store.create_task(task)
        await store.create_run(run)

        first = await store.append_event(
            make_event(
                run_id=run_id,
                session_id=session_id,
                provider=Provider.FAKE,
                native_type="fake/start",
                normalized_type=EventType.RUN_STARTED,
                adapter_version="postgres-integration-v1",
            )
        )
        second = await store.append_event(
            make_event(
                run_id=run_id,
                session_id=session_id,
                provider=Provider.FAKE,
                native_type="fake/complete",
                normalized_type=EventType.RUN_COMPLETED,
                adapter_version="postgres-integration-v1",
            )
        )
        updated = await store.update_run(run_id, RunState.SUCCEEDED)

        assert (first.sequence, second.sequence) == (1, 2)
        assert updated.state is RunState.SUCCEEDED
        assert updated.last_sequence == 2
        assert [event.sequence for event in await store.list_events(run_id)] == [1, 2]
        assert (await store.get_project(project_id)) == project
        assert (await store.get_task(task_id)) == task
    finally:
        await engine.dispose()


async def test_postgres_side_effect_deduplication_across_store_instances() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    sessions = create_session_factory(engine)
    store = PostgresStore(sessions)
    project = Project(
        project_id=new_id("project"),
        name="Idempotency integration",
        repository_path=Path.cwd(),
    )
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"), project_id=project.project_id, objective="deduplicate"
        )
    )
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
    )
    try:
        await store.create_project(project)
        await store.create_task(task)
        await store.create_run(run)
        first_ledger = PostgresSideEffectLedger(sessions)
        second_ledger = PostgresSideEffectLedger(sessions)
        first, second = await asyncio.gather(
            first_ledger.record_intent(
                run_id=run.run_id,
                idempotency_key=f"deploy:{run.run_id}",
                capability_id="deploy.test",
                payload={"source": "first"},
            ),
            second_ledger.record_intent(
                run_id=run.run_id,
                idempotency_key=f"deploy:{run.run_id}",
                capability_id="deploy.test",
                payload={"source": "second"},
            ),
        )
        assert sorted([first[1], second[1]]) == [False, True]
        assert first[0].operation_id == second[0].operation_id
    finally:
        await engine.dispose()
