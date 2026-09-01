from __future__ import annotations

from datetime import UTC, datetime

import pytest

from accretion.contracts import (
    Capability,
    CapabilityBackend,
    CapabilityBinding,
    CapabilityBindingBackend,
    CapabilityResolutionOutcome,
    Connection,
    ConnectionScope,
    ConnectionStatus,
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
)
from accretion.governance import seed_governance
from accretion.persistence.store import MemoryStore
from accretion.resolver import CapabilityResolver

CREATED_AT = datetime(2026, 8, 24, tzinfo=UTC)


def capability(capability_id: str, *, enabled: bool = True) -> Capability:
    return Capability(
        capability_id=capability_id,
        version="1.0.0",
        description="M0 test capability",
        backend=CapabilityBackend.PYTHON,
        enabled=enabled,
        created_at=CREATED_AT,
    )


def connector(
    connector_id: str,
    *,
    auth_type: ConnectorAuthType = ConnectorAuthType.NONE,
    default_scopes: list[str] | None = None,
) -> ConnectorDefinition:
    return ConnectorDefinition(
        connector_id=connector_id,
        name=connector_id,
        kind=ConnectorKind.LOCAL,
        auth_type=auth_type,
        default_scopes=default_scopes or [],
        created_at=CREATED_AT,
    )


def binding(capability_id: str, connector_id: str) -> CapabilityBinding:
    return CapabilityBinding(
        binding_id=f"capbind_{capability_id}_{connector_id}",
        capability_id=capability_id,
        connector_id=connector_id,
        backend=CapabilityBindingBackend(type=CapabilityBackend.PYTHON),
        created_at=CREATED_AT,
    )


def connection(
    connection_id: str,
    connector_id: str,
    *,
    scope: ConnectionScope = ConnectionScope.USER,
    principal_id: str | None = None,
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
    granted_scopes: list[str] | None = None,
    workspace_shareable: bool = False,
    workspace_id: str = "workspace_test",
) -> Connection:
    return Connection(
        connection_id=connection_id,
        connector_id=connector_id,
        workspace_id=workspace_id,
        principal_id=principal_id,
        scope=scope,
        status=status,
        granted_scopes=granted_scopes or [],
        workspace_shareable=workspace_shareable,
        created_at=CREATED_AT,
    )


async def test_contract_round_trips_through_store() -> None:
    store = MemoryStore()
    conndef = connector("conndef_rt", auth_type=ConnectorAuthType.API_KEY)
    conn = connection("conn_rt", "conndef_rt", principal_id="prin_1")
    bind = binding("cap.rt", "conndef_rt")
    await store.upsert_connector_definition(conndef)
    await store.upsert_connection(conn)
    await store.upsert_capability_binding(bind)
    assert await store.get_connector_definition("conndef_rt") == conndef
    assert await store.get_connection("conn_rt") == conn
    assert await store.list_capability_bindings(capability_id="cap.rt") == [bind]
    assert await store.list_connections(
        connector_id="conndef_rt", status=ConnectionStatus.ACTIVE
    ) == [conn]


async def test_capability_without_binding_resolves_as_no_connector_required() -> None:
    store = MemoryStore()
    await store.upsert_capability(capability("cap.plain"))
    resolved = await CapabilityResolver(store).resolve("cap.plain")
    assert resolved is not None
    assert resolved.outcome is CapabilityResolutionOutcome.NO_CONNECTOR_REQUIRED
    assert resolved.binding is None
    assert resolved.connection is None


async def test_user_connection_takes_precedence_over_workspace_connection() -> None:
    store = MemoryStore()
    await store.upsert_capability(capability("cap.bound"))
    await store.upsert_connector_definition(connector("conndef_a"))
    await store.upsert_capability_binding(binding("cap.bound", "conndef_a"))
    await store.upsert_connection(
        connection(
            "conn_workspace",
            "conndef_a",
            scope=ConnectionScope.WORKSPACE,
            workspace_shareable=True,
        )
    )
    await store.upsert_connection(
        connection("conn_user", "conndef_a", principal_id="prin_1")
    )
    resolved = await CapabilityResolver(store).resolve("cap.bound", principal_id="prin_1")
    assert resolved is not None
    assert resolved.outcome is CapabilityResolutionOutcome.OK
    assert resolved.connection is not None
    assert resolved.connection.connection_id == "conn_user"


@pytest.mark.acceptance("AC3-CON-05")
async def test_other_principals_user_connection_is_never_used() -> None:
    store = MemoryStore()
    await store.upsert_capability(capability("cap.bound"))
    await store.upsert_connector_definition(connector("conndef_a"))
    await store.upsert_capability_binding(binding("cap.bound", "conndef_a"))
    await store.upsert_connection(
        connection("conn_user_other", "conndef_a", principal_id="prin_other")
    )
    resolved = await CapabilityResolver(store).resolve("cap.bound", principal_id="prin_1")
    assert resolved is not None
    assert resolved.outcome is CapabilityResolutionOutcome.NO_CONNECTION


@pytest.mark.acceptance("AC3-CON-05")
async def test_workspace_connection_requires_explicit_share_policy() -> None:
    store = MemoryStore()
    await store.upsert_capability(capability("cap.bound"))
    await store.upsert_connector_definition(connector("conndef_a"))
    await store.upsert_capability_binding(binding("cap.bound", "conndef_a"))
    await store.upsert_connection(
        connection(
            "conn_workspace",
            "conndef_a",
            scope=ConnectionScope.WORKSPACE,
            workspace_shareable=False,
        )
    )
    resolved = await CapabilityResolver(store).resolve("cap.bound", principal_id="prin_1")
    assert resolved is not None
    assert resolved.outcome is CapabilityResolutionOutcome.NO_CONNECTION


async def test_revoked_and_reauth_connections_fail_closed() -> None:
    store = MemoryStore()
    await store.upsert_capability(capability("cap.bound"))
    await store.upsert_connector_definition(connector("conndef_a"))
    await store.upsert_capability_binding(binding("cap.bound", "conndef_a"))
    for status in (ConnectionStatus.REVOKED, ConnectionStatus.REAUTH_REQUIRED):
        await store.upsert_connection(
            connection("conn_user", "conndef_a", principal_id="prin_1", status=status)
        )
        resolved = await CapabilityResolver(store).resolve("cap.bound", principal_id="prin_1")
        assert resolved is not None
        assert resolved.outcome is CapabilityResolutionOutcome.REQUIRE_REAUTH


@pytest.mark.acceptance("AC3-CON-03")
async def test_missing_scopes_require_reauth_instead_of_silent_expansion() -> None:
    store = MemoryStore()
    await store.upsert_capability(capability("cap.bound"))
    await store.upsert_connector_definition(
        connector("conndef_a", default_scopes=["read", "write"])
    )
    await store.upsert_capability_binding(binding("cap.bound", "conndef_a"))
    await store.upsert_connection(
        connection("conn_user", "conndef_a", principal_id="prin_1", granted_scopes=["read"])
    )
    resolved = await CapabilityResolver(store).resolve("cap.bound", principal_id="prin_1")
    assert resolved is not None
    assert resolved.outcome is CapabilityResolutionOutcome.REQUIRE_REAUTH
    assert "write" in resolved.reason


async def test_disabled_capability_resolves_disabled() -> None:
    store = MemoryStore()
    await store.upsert_capability(capability("cap.off", enabled=False))
    resolved = await CapabilityResolver(store).resolve("cap.off")
    assert resolved is not None
    assert resolved.outcome is CapabilityResolutionOutcome.DISABLED


async def test_connection_ref_exposes_no_token_material() -> None:
    store = MemoryStore()
    await store.upsert_capability(capability("cap.bound"))
    await store.upsert_connector_definition(connector("conndef_a"))
    await store.upsert_capability_binding(binding("cap.bound", "conndef_a"))
    conn = connection("conn_user", "conndef_a", principal_id="prin_1")
    await store.upsert_connection(
        conn.model_copy(update={"token_handle_ref": "th_secret_handle"})
    )
    resolved = await CapabilityResolver(store).resolve("cap.bound", principal_id="prin_1")
    assert resolved is not None
    payload = resolved.model_dump(mode="json")
    assert "th_secret_handle" not in str(payload)
    assert set(payload["connection"]) == {"connection_id", "connector_id", "status"}


async def test_seeded_governance_resolves_echo_through_demo_connector() -> None:
    store = MemoryStore()
    await seed_governance(store)
    resolver = CapabilityResolver(store)
    echo = await resolver.resolve("accretion.echo")
    assert echo is not None
    assert echo.outcome is CapabilityResolutionOutcome.OK
    assert echo.connection is not None
    assert echo.connection.connection_id == "conn_local_echo"
    protected = await resolver.resolve("accretion.protected-write")
    assert protected is not None
    assert protected.outcome is CapabilityResolutionOutcome.NO_CONNECTOR_REQUIRED
