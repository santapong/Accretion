from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from accretion.contracts import (
    CapabilityBackend,
    CapabilityBinding,
    CapabilityBindingBackend,
    Connection,
    ConnectionScope,
    ConnectionStatus,
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
)
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import PostgresStore

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]

CREATED_AT = datetime(2026, 8, 24, tzinfo=UTC)


async def test_v03_m0_connection_contracts_round_trip_and_update() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    connector_id = new_id("conndef")
    connection_id = new_id("conn")
    binding_id = new_id("capbind")
    capability_id = f"cap.m0.{connection_id[-8:]}"
    try:
        connector = ConnectorDefinition(
            connector_id=connector_id,
            name="M0 round-trip connector",
            kind=ConnectorKind.LOCAL,
            auth_type=ConnectorAuthType.NONE,
            connection_scope=ConnectionScope.WORKSPACE,
            created_at=CREATED_AT,
        )
        await store.upsert_connector_definition(connector)
        assert await store.get_connector_definition(connector_id) == connector

        connection = Connection(
            connection_id=connection_id,
            connector_id=connector_id,
            workspace_id="workspace_pg_test",
            scope=ConnectionScope.WORKSPACE,
            status=ConnectionStatus.PENDING,
            workspace_shareable=True,
            created_at=CREATED_AT,
        )
        await store.upsert_connection(connection)
        assert await store.get_connection(connection_id) == connection

        # Connections are mutable: a status change must round-trip and be
        # visible to status-filtered listings.
        activated = connection.model_copy(update={"status": ConnectionStatus.ACTIVE})
        await store.upsert_connection(activated)
        assert await store.get_connection(connection_id) == activated
        active = await store.list_connections(
            connector_id=connector_id, status=ConnectionStatus.ACTIVE
        )
        assert [item.connection_id for item in active] == [connection_id]

        binding = CapabilityBinding(
            binding_id=binding_id,
            capability_id=capability_id,
            connector_id=connector_id,
            backend=CapabilityBindingBackend(type=CapabilityBackend.PYTHON),
            created_at=CREATED_AT,
        )
        await store.upsert_capability_binding(binding)
        assert await store.list_capability_bindings(capability_id=capability_id) == [binding]

        disabled = binding.model_copy(update={"enabled": False})
        await store.upsert_capability_binding(disabled)
        assert await store.list_capability_bindings(capability_id=capability_id) == []
        assert await store.list_capability_bindings(
            capability_id=capability_id, enabled_only=False
        ) == [disabled]
    finally:
        await engine.dispose()
