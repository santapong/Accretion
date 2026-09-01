from __future__ import annotations

import os
import uuid

import pytest

from accretion.contracts import (
    McpCacheHint,
    McpDiscoverySnapshot,
    McpServerDefinition,
    McpServerEvent,
    McpServerState,
)
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import PostgresStore

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]


async def test_v03_m3_server_snapshot_and_event_round_trip() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    # Uuid-suffixed so the test is re-runnable against a database it already wrote to.
    # M4's acceptance stage gates re-run the whole suite in-process, so a fixed
    # workspace id made this fail on every run after the first.
    server = McpServerDefinition(
        mcp_server_id=new_id("mcp_server"),
        workspace_id=f"workspace_m3_postgres_{uuid.uuid4().hex[:12]}",
        connector_id=new_id("conndef"),
        name="M3 PostgreSQL fixture",
        endpoint="https://mcp.example.test/mcp",
        owner_principal_id="usr_postgres",
    )
    try:
        await store.upsert_mcp_server(server)
        assert await store.get_mcp_server(server.mcp_server_id) == server

        ready = server.model_copy(
            update={"enabled": True, "state": McpServerState.READY, "revision": 2}
        )
        await store.upsert_mcp_server(ready)
        listed = await store.list_mcp_servers(workspace_id=server.workspace_id)
        assert listed == [ready]

        snapshot = McpDiscoverySnapshot(
            discovery_snapshot_id=new_id("mcp_snapshot"),
            mcp_server_id=server.mcp_server_id,
            connection_id="conn_postgres",
            protocol_version="2026-07-28",
            tools=[
                {
                    "name": "echo",
                    "inputSchema": {"type": "object"},
                }
            ],
            cache_hints={"tools": McpCacheHint(ttl_ms=60_000)},
            content_sha256="a" * 64,
        )
        await store.save_mcp_discovery_snapshot(snapshot)
        assert await store.list_mcp_discovery_snapshots(
            server.mcp_server_id, connection_id="conn_postgres"
        ) == [snapshot]

        event = McpServerEvent(
            mcp_event_id=new_id("mcp_event"),
            mcp_server_id=server.mcp_server_id,
            event_type="READY",
            correlation_id="request-postgres",
        )
        await store.append_mcp_server_event(event)
        assert await store.list_mcp_server_events(server.mcp_server_id) == [event]
    finally:
        await engine.dispose()
