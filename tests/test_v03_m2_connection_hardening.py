"""Hardening of the M0 connection surface ahead of the M2 token broker.

Every defect covered here is latent only because ``token_handle_ref`` is always
``None`` today. Each becomes a real disclosure the moment the broker writes handles.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from accretion.api.main import app
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
    Principal,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.identity import IdentityService
from accretion.persistence.store import MemoryStore
from accretion.redaction import redact
from accretion.resolver import CapabilityResolver

CREATED_AT = datetime(2026, 8, 24, tzinfo=UTC)


def capability(capability_id: str) -> Capability:
    return Capability(
        capability_id=capability_id,
        version="1.0.0",
        description="M2 hardening capability",
        backend=CapabilityBackend.PYTHON,
        created_at=CREATED_AT,
    )


def connector(
    connector_id: str, *, auth_type: ConnectorAuthType = ConnectorAuthType.NONE
) -> ConnectorDefinition:
    return ConnectorDefinition(
        connector_id=connector_id,
        name=connector_id,
        kind=ConnectorKind.LOCAL,
        auth_type=auth_type,
        created_at=CREATED_AT,
    )


def binding(capability_id: str, connector_id: str, *, binding_id: str) -> CapabilityBinding:
    return CapabilityBinding(
        binding_id=binding_id,
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
    workspace_id: str = "workspace_test",
    workspace_shareable: bool = False,
    token_handle_ref: str | None = None,
) -> Connection:
    return Connection(
        connection_id=connection_id,
        connector_id=connector_id,
        workspace_id=workspace_id,
        principal_id=principal_id,
        scope=scope,
        status=status,
        workspace_shareable=workspace_shareable,
        token_handle_ref=token_handle_ref,
        created_at=CREATED_AT,
    )


# --------------------------------------------------------------------- resolver


async def test_capability_resolves_through_a_later_binding_when_the_first_cannot() -> None:
    """A capability bound to two connectors must not fail because the first is unusable."""

    store = MemoryStore()
    await store.upsert_capability(capability("cap.multi"))
    await store.upsert_connector_definition(connector("conndef_a"))
    await store.upsert_connector_definition(connector("conndef_b"))
    await store.upsert_capability_binding(
        binding("cap.multi", "conndef_a", binding_id="capbind_1_a")
    )
    await store.upsert_capability_binding(
        binding("cap.multi", "conndef_b", binding_id="capbind_2_b")
    )
    # Only the second connector has a usable connection.
    await store.upsert_connection(connection("conn_b", "conndef_b", principal_id="prin_1"))

    resolved = await CapabilityResolver(store).resolve("cap.multi", principal_id="prin_1")

    assert resolved is not None
    assert resolved.outcome is CapabilityResolutionOutcome.OK
    assert resolved.binding is not None
    assert resolved.binding.connector_id == "conndef_b"


async def test_unresolvable_capability_still_reports_the_first_binding_failure() -> None:
    store = MemoryStore()
    await store.upsert_capability(capability("cap.none"))
    await store.upsert_connector_definition(connector("conndef_a"))
    await store.upsert_capability_binding(
        binding("cap.none", "conndef_a", binding_id="capbind_1_a")
    )

    resolved = await CapabilityResolver(store).resolve("cap.none", principal_id="prin_1")

    assert resolved is not None
    assert resolved.outcome is CapabilityResolutionOutcome.NO_CONNECTION
    assert "no usable connection" in resolved.reason


async def test_anonymous_fallback_never_selects_a_credential_bearing_connection() -> None:
    """An unowned connection that carries a token handle is not anonymous-usable."""

    store = MemoryStore()
    await store.upsert_capability(capability("cap.anon"))
    await store.upsert_connector_definition(connector("conndef_anon"))
    await store.upsert_capability_binding(
        binding("cap.anon", "conndef_anon", binding_id="capbind_anon")
    )
    await store.upsert_connection(
        connection("conn_anon", "conndef_anon", token_handle_ref="tkh_secret")
    )

    resolved = await CapabilityResolver(store).resolve("cap.anon")

    assert resolved is not None
    assert resolved.outcome is CapabilityResolutionOutcome.NO_CONNECTION


async def test_anonymous_fallback_is_refused_for_a_credentialed_connector() -> None:
    store = MemoryStore()
    await store.upsert_capability(capability("cap.oauth"))
    await store.upsert_connector_definition(
        connector("conndef_oauth", auth_type=ConnectorAuthType.OAUTH2)
    )
    await store.upsert_capability_binding(
        binding("cap.oauth", "conndef_oauth", binding_id="capbind_oauth")
    )
    await store.upsert_connection(connection("conn_unowned", "conndef_oauth"))

    resolved = await CapabilityResolver(store).resolve("cap.oauth")

    assert resolved is not None
    assert resolved.outcome is CapabilityResolutionOutcome.NO_CONNECTION


# -------------------------------------------------------------------- redaction


def test_opaque_token_handles_stay_readable_for_the_audit_trail() -> None:
    """INV3-011 needs the handle as a correlation key; only token values are secrets."""

    payload = redact(
        {
            "token_handle_ref": "tkh_01ABC",
            "token_handle_id": "tkh_01ABC",
            "access_token": "super-secret",
            "refresh_token": "also-secret",
        }
    )
    assert payload["token_handle_ref"] == "tkh_01ABC"
    assert payload["token_handle_id"] == "tkh_01ABC"
    assert payload["access_token"] == "[REDACTED]"
    assert payload["refresh_token"] == "[REDACTED]"


# ------------------------------------------------------------------------- API


async def seeded_api_store() -> tuple[MemoryStore, Principal, Principal]:
    store = MemoryStore()
    alice = Principal(
        principal_id="prin_alice", issuer="accretion-local", subject="alice"
    )
    bob = Principal(principal_id="prin_bob", issuer="accretion-local", subject="bob")
    for who in (alice, bob):
        await store.upsert_principal(who)
    await store.upsert_workspace(
        WorkspaceEntity(workspace_id="workspace_test", name="test")
    )
    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id="wsm_alice",
            workspace_id="workspace_test",
            principal_id=alice.principal_id,
            role=WorkspaceRole.OWNER,
        )
    )
    await store.upsert_connector_definition(connector("conndef_api"))
    await store.upsert_connection(
        connection(
            "conn_alice",
            "conndef_api",
            principal_id=alice.principal_id,
            token_handle_ref="tkh_alice_secret",
        )
    )
    await store.upsert_connection(
        connection("conn_bob", "conndef_api", principal_id=bob.principal_id)
    )
    return store, alice, bob


async def api_client(store: MemoryStore, who: Principal) -> AsyncClient:
    """Drive the API as a specific principal in LOCAL_PRINCIPAL mode."""

    from accretion.api.auth import AuthRuntime

    app.state.manager = type("M", (), {"store": store})()
    app.state.auth = AuthRuntime(
        mode="LOCAL_PRINCIPAL",
        identity=IdentityService(store),
        cookie_name="accretion_session",
        cookie_secure=False,
        session_ttl_seconds=3600,
        local_principal_cache=who,
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.acceptance("AC3-CON-05")
async def test_connection_listing_hides_token_handles_and_other_principals() -> None:
    store, alice, bob = await seeded_api_store()
    try:
        async with await api_client(store, alice) as client:
            response = await client.get("/api/v1/connections")
        assert response.status_code == 200
        body = response.json()

        assert [item["connection_id"] for item in body] == ["conn_alice"]
        # The handle is broker-internal and must not appear on the wire at all.
        assert all("token_handle_ref" not in item for item in body)
        assert "tkh_alice_secret" not in response.text
    finally:
        del app.state.auth
        del app.state.manager


@pytest.mark.acceptance("AC3-CON-05")
async def test_capability_resolution_cannot_be_run_as_another_principal() -> None:
    store, alice, bob = await seeded_api_store()
    await store.upsert_capability(capability("cap.api"))
    try:
        async with await api_client(store, alice) as client:
            forbidden = await client.post(
                "/api/v1/capabilities/resolve",
                json={"capability_id": "cap.api", "principal_id": bob.principal_id},
            )
            own = await client.post(
                "/api/v1/capabilities/resolve",
                json={"capability_id": "cap.api", "principal_id": alice.principal_id},
            )
            other_workspace = await client.post(
                "/api/v1/capabilities/resolve",
                json={"capability_id": "cap.api", "workspace_id": "workspace_other"},
            )
        assert forbidden.status_code == 403
        assert own.status_code == 200
        assert other_workspace.status_code == 403
    finally:
        del app.state.auth
        del app.state.manager


# --------------------------------------------------------------------- ownership


@pytest.mark.parametrize("field", ["workspace_id", "principal_id"])
async def test_reowning_a_connection_round_trips_in_memory(field: str) -> None:
    """Baseline for the Postgres equivalent, which is where the indexed columns live."""

    store = MemoryStore()
    original = connection("conn_move", "conndef_api", principal_id="prin_alice")
    await store.upsert_connection(original)
    await store.upsert_connection(original.model_copy(update={field: "moved"}))

    stored = await store.get_connection("conn_move")
    assert stored is not None
    assert getattr(stored, field) == "moved"
