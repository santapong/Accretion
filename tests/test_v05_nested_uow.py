"""Reject ambiguous nested commits; pure Memory and PostgreSQL-session doubles."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import copy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from test_v05_memory_authority_isolation import records

from accretion.persistence.store import MemoryStore
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.runtime_store import RuntimeStore
from accretion.robotics.store import MemoryRoboticsStore, PostgresRoboticsStore, RegistryRecord


def record(suffix="outer"):
    return RegistryRecord(
        id=f"contract-{suffix}",
        workspace_id="workspace-test",
        project_id="project-test",
        contract_type="synthetic-registry-record",
        logical_name=suffix,
        version="1.0.0",
        schema_version="1.0.0",
        content_hash="a" * 64,
        original_json="{}",
        created_by="synthetic-principal",
        created_at=datetime.now(UTC),
    )


def store_for(state, kind):
    return RuntimeStore(state) if kind == "runtime" else MemoryRoboticsStore(state)


def registry(tx, kind):
    return tx.registry if kind == "runtime" else tx


@pytest.mark.parametrize("outer_kind", ["registry", "runtime"])
@pytest.mark.parametrize("inner_kind", ["registry", "runtime"])
@pytest.mark.parametrize("project", ["project-test", "other-project"])
@pytest.mark.parametrize("catch_inside", [False, True])
async def test_nested_unit_refuses_before_entry_and_preserves_outer_disposition(
    outer_kind, inner_kind, project, catch_inside
):
    state = MemoryStore()
    outer_store, inner_store = store_for(state, outer_kind), store_for(state, inner_kind)
    original, proposed = record("existing"), record()
    async with MemoryRoboticsStore(state).transaction("project-test") as initial:
        await initial.insert(original)
    entered_inner = False

    async def nested():
        nonlocal entered_inner
        async with inner_store.transaction(project):
            entered_inner = True

    try:
        async with outer_store.transaction("project-test") as outer:
            handle = registry(outer, outer_kind)
            await handle.insert(proposed)
            # Public same-task reads stay reentrant while the UoW owns the mutex.
            assert await state.get_run("absent") is None
            if catch_inside:
                with pytest.raises(RoboticsError) as refusal:
                    await nested()
                assert refusal.value.code is Code.SIMULATION_UNAVAILABLE
                handle.require_active()
                assert await handle.get(proposed.id) == proposed
            else:
                await nested()
    except RoboticsError as refusal:
        assert not catch_inside and refusal.code is Code.SIMULATION_UNAVAILABLE
    assert not entered_inner
    async with MemoryRoboticsStore(state).transaction("project-test") as after:
        assert await after.get(original.id) == original
        assert (await after.get(proposed.id) is not None) == catch_inside
    assert not state.robotics_registry_lock.locked()


async def test_different_project_inner_runtime_cannot_claim_commit_then_be_erased():
    state = MemoryStore()
    outer_task, outer_run = records("outer")
    inner_task, inner_run = records("inner")
    with pytest.raises(RoboticsError) as refusal:
        async with RuntimeStore(state).transaction(outer_run.project_id) as outer:
            await outer.create_task_run(outer_task, outer_run)
            async with RuntimeStore(state).transaction(inner_run.project_id) as inner:
                pytest.fail("inner transaction must refuse before caller can write or commit")
                await inner.create_task_run(inner_task, inner_run)
    assert refusal.value.code is Code.SIMULATION_UNAVAILABLE
    assert await state.list_runs() == []
    assert await state.get_task(outer_task.envelope.task_id) is None
    assert await state.get_task(inner_task.envelope.task_id) is None


async def test_different_backing_stores_commit_independently_of_outer_rollback():
    first, second = MemoryStore(), MemoryStore()
    task, run = records("independent")
    with pytest.raises(ValueError, match="outer rollback"):
        async with RuntimeStore(first).transaction(run.project_id):
            async with RuntimeStore(second).transaction(run.project_id) as independent:
                await independent.create_task_run(task, run)
            assert await second.get_run(run.run_id) == run
            raise ValueError("outer rollback")
    assert await first.get_run(run.run_id) is None
    assert await second.get_run(run.run_id) == run
    async with RuntimeStore(first).transaction(run.project_id) as sequential:
        await sequential.create_task_run(task, run)
    assert await first.get_run(run.run_id) == run


async def test_scoped_memory_copy_cannot_hide_same_backing_unit():
    state = MemoryStore()
    async with RuntimeStore(state).transaction("outer"):
        with pytest.raises(RoboticsError) as refusal:
            async with RuntimeStore(copy(state)).transaction("inner"):
                pytest.fail("a scoped wrapper is not an independent backing store")
        assert refusal.value.code is Code.SIMULATION_UNAVAILABLE


async def test_child_inherited_context_waits_then_opens_its_own_transaction():
    state = MemoryStore()
    started, entered = asyncio.Event(), asyncio.Event()

    async def child():
        started.set()
        async with RuntimeStore(state).transaction("child"):
            entered.set()

    async with RuntimeStore(state).transaction("parent"):
        pending = asyncio.create_task(child())
        await started.wait()
        assert not pending.done() and not entered.is_set()
    await asyncio.wait_for(pending, 2)
    assert entered.is_set()


@pytest.mark.parametrize("kind", ["registry", "runtime"])
async def test_cancelled_context_does_not_poison_sequential_transaction(kind):
    state = MemoryStore()
    store = store_for(state, kind)
    with pytest.raises(asyncio.CancelledError):
        async with store.transaction("cancelled"):
            raise asyncio.CancelledError
    async with store.transaction("next") as next_tx:
        registry(next_tx, kind).require_active()


async def test_postgres_entry_refuses_nesting_before_opening_second_session():
    """No DB server: inspect only UoW entry, not SQL or PostgreSQL semantics."""
    opened = []

    class Session:
        async def execute(self, statement):
            pass

    @asynccontextmanager
    async def begin():
        opened.append("session")
        yield Session()

    state = SimpleNamespace(sessions=SimpleNamespace(begin=begin))
    first, wrapper = PostgresRoboticsStore(state), PostgresRoboticsStore(state)
    async with first.transaction("first") as outer:
        with pytest.raises(RoboticsError) as refusal:
            async with wrapper.transaction("different-project"):
                pytest.fail("must refuse before opening a second session")
        assert refusal.value.code is Code.SIMULATION_UNAVAILABLE
        assert opened == ["session"]
        outer.require_active()
    async with wrapper.transaction("sequential"):
        pass
    assert opened == ["session", "session"]
