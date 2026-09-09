"""Transaction-handle witnesses on Memory and optional disposable PostgreSQL."""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta

import pytest

from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import MemoryStore, PostgresStore
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.runtime_store import RuntimeStore

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")


@pytest.fixture(
    params=[
        "memory",
        pytest.param(
            "postgres",
            marks=[
                pytest.mark.integration,
                pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL URL absent"),
            ],
        ),
    ]
)
async def runtime(request):
    engine = create_engine(POSTGRES_URL) if request.param == "postgres" else None
    state = PostgresStore(create_session_factory(engine)) if engine else MemoryStore()
    try:
        yield RuntimeStore(state)
    finally:
        if engine:
            await engine.dispose()


@pytest.mark.parametrize("outcome", ["commit", "rollback", "cancelled", "expired"])
async def test_retained_registry_and_deferred_coroutines_never_escape_runtime(runtime, outcome):
    project_id = new_id("project")
    try:
        async with runtime.transaction(project_id) as tx:
            registry = tx.registry
            # Creating a coroutine inside the context does not execute it there.
            delayed_read = registry.get("not-a-contract")
            delayed_write = registry.bootstrap_bind("not-a-workspace", project_id)
            bound_read = registry.get
            if outcome == "rollback":
                raise ValueError("synthetic rollback")
            if outcome == "cancelled":
                raise asyncio.CancelledError
            if outcome == "expired":
                now = await tx.now()
                tx.require_valid_interval(now - timedelta(seconds=1), now, Code.CAPABILITY_DENIED)
    except (ValueError, asyncio.CancelledError):
        assert outcome in {"rollback", "cancelled"}
    except RoboticsError as error:
        assert outcome == "expired" and error.code is Code.CAPABILITY_DENIED
    for operation in (delayed_read, delayed_write, bound_read("not-a-contract")):
        with pytest.raises(RoboticsError) as error:
            await operation
        assert error.value.code is Code.SIMULATION_UNAVAILABLE
    # A new transaction on the same project cannot revive its predecessor.
    async with runtime.transaction(project_id):
        with pytest.raises(RoboticsError) as error:
            await registry.get("not-a-contract")
        assert error.value.code is Code.SIMULATION_UNAVAILABLE


@pytest.mark.parametrize("rollback", [False, True])
async def test_standalone_registry_lifetime_closes_and_refuses_child_task(runtime, rollback):
    try:
        async with runtime.registry.transaction(new_id("project")) as tx:

            async def child():
                with pytest.raises(RoboticsError) as error:
                    await tx.get("not-a-contract")
                assert error.value.code is Code.SIMULATION_UNAVAILABLE

            await asyncio.wait_for(asyncio.create_task(child()), 2)
            tx.require_active()
            if rollback:
                raise ValueError("synthetic registry rollback")
    except ValueError:
        assert rollback
    with pytest.raises(RoboticsError) as error:
        await tx.get("not-a-contract")
    assert error.value.code is Code.SIMULATION_UNAVAILABLE
