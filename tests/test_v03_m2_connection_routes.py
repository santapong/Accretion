"""OAuth connection lifecycle through the SDD section 17 routes (v0.3 M2).

Drives the real API against an in-process authorization server, so the exit criterion
"one OAuth connector works end to end with zero secret leakage" is exercised rather
than asserted.
"""

from __future__ import annotations

import httpx
import pytest
from fake_authorization_server import CLIENT_ID, CLIENT_SECRET, ISSUER, FakeAuthorizationServer
from httpx import ASGITransport, AsyncClient

from accretion.api.auth import AuthRuntime
from accretion.api.main import app
from accretion.connections import ConnectionService
from accretion.connectors import GITHUB_CONNECTOR_ID, github_connector, github_endpoints
from accretion.contracts import (
    ConnectionStatus,
    Principal,
    TokenStatus,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.identity import IdentityService
from accretion.oauth import OAuthClient
from accretion.persistence.store import MemoryStore
from accretion.secrets_store import EnvelopeSecretStore
from accretion.token_broker import EncryptedTokenBroker

WORKSPACE = "workspace_test"


class StaticKey:
    key_id = "test-1"

    def material(self) -> bytes:
        return b"K" * 32


async def harness(
    server: FakeAuthorizationServer, *, principal_id: str = "prin_alice"
) -> tuple[MemoryStore, AsyncClient, Principal]:
    store = MemoryStore()
    who = Principal(principal_id=principal_id, issuer="accretion-local", subject=principal_id)
    await store.upsert_principal(who)
    await store.upsert_workspace(WorkspaceEntity(workspace_id=WORKSPACE, name="test"))
    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=f"wsm_{principal_id}",
            workspace_id=WORKSPACE,
            principal_id=principal_id,
            role=WorkspaceRole.OWNER,
        )
    )
    await store.upsert_connector_definition(github_connector(authorization_server=ISSUER))

    app.state.manager = type("M", (), {"store": store})()
    app.state.auth = AuthRuntime(
        mode="LOCAL_PRINCIPAL",
        identity=IdentityService(store),
        cookie_name="accretion_session",
        cookie_secure=False,
        session_ttl_seconds=3600,
        local_principal_cache=who,
    )
    app.state.connections = ConnectionService(
        store=store,
        broker=EncryptedTokenBroker(store, EnvelopeSecretStore(StaticKey())),
        clients={
            GITHUB_CONNECTOR_ID: OAuthClient(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                redirect_url=f"http://test/api/v1/oauth/callback/{GITHUB_CONNECTOR_ID}",
                endpoints=github_endpoints(ISSUER),
                http=httpx.AsyncClient(
                    transport=ASGITransport(app=server.app()), base_url=ISSUER
                ),
            )
        },
    )
    return store, AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), who


def teardown() -> None:
    for name in ("connections", "auth", "manager"):
        if hasattr(app.state, name):
            delattr(app.state, name)


def state_of(url: str) -> str:
    from urllib.parse import parse_qs, urlparse

    return parse_qs(urlparse(url).query)["state"][0]


@pytest.mark.acceptance("AC3-CON-01")
async def test_an_operator_can_create_a_connection_and_no_token_reaches_the_api() -> None:
    server = FakeAuthorizationServer()
    store, client, _ = await harness(server)
    try:
        async with client:
            start = await client.post(
                f"/api/v1/connectors/{GITHUB_CONNECTOR_ID}/connect",
                json={"workspace_id": WORKSPACE},
            )
            assert start.status_code == 200
            url = start.json()["authorization_url"]
            assert "code_challenge_method=S256" in url
            assert CLIENT_SECRET not in url

            code = server.issue_code(["read:user", "repo:status"])
            callback = await client.get(
                f"/api/v1/oauth/callback/{GITHUB_CONNECTOR_ID}",
                params={"state": state_of(url), "code": code},
            )
            assert callback.status_code == 200
            body = callback.json()
            assert body["status"] == ConnectionStatus.ACTIVE.value
            assert body["granted_scopes"] == ["read:user", "repo:status"]

            listed = await client.get("/api/v1/connections")
            health = await client.get(
                f"/api/v1/connections/{body['connection_id']}/health"
            )

        # Zero secret leakage across every response the operator can see.
        sentinel = server.issued[-1]
        for response in (start, callback, listed, health):
            assert sentinel not in response.text
        assert "token_handle_ref" not in listed.text
        assert health.json()["token_status"] == TokenStatus.ACTIVE.value
        # The credential exists, but only behind the broker.
        stored = await store.get_connection(body["connection_id"])
        assert stored is not None and stored.token_handle_ref is not None
    finally:
        teardown()


@pytest.mark.acceptance("AC3-SEC-04")
async def test_replayed_unknown_and_foreign_callback_states_all_fail_alike() -> None:
    server = FakeAuthorizationServer()
    _, client, _ = await harness(server)
    try:
        async with client:
            url = (
                await client.post(
                    f"/api/v1/connectors/{GITHUB_CONNECTOR_ID}/connect",
                    json={"workspace_id": WORKSPACE},
                )
            ).json()["authorization_url"]
            state = state_of(url)

            first = await client.get(
                f"/api/v1/oauth/callback/{GITHUB_CONNECTOR_ID}",
                params={"state": state, "code": server.issue_code(["read:user"])},
            )
            replay = await client.get(
                f"/api/v1/oauth/callback/{GITHUB_CONNECTOR_ID}",
                params={"state": state, "code": server.issue_code(["read:user"])},
            )
            unknown = await client.get(
                f"/api/v1/oauth/callback/{GITHUB_CONNECTOR_ID}",
                params={"state": "never-issued", "code": "x"},
            )
        assert first.status_code == 200
        # Replayed and unknown are indistinguishable, so neither can be probed.
        assert replay.status_code == 400
        assert unknown.status_code == 400
        assert replay.json()["code"] == unknown.json()["code"]
        assert replay.json()["message"] == unknown.json()["message"]
    finally:
        teardown()


async def test_a_state_minted_for_one_principal_is_refused_for_another() -> None:
    server = FakeAuthorizationServer()
    _, client, _ = await harness(server)
    try:
        async with client:
            url = (
                await client.post(
                    f"/api/v1/connectors/{GITHUB_CONNECTOR_ID}/connect",
                    json={"workspace_id": WORKSPACE},
                )
            ).json()["authorization_url"]
            # The session changes between authorize and callback.
            app.state.auth.local_principal_cache = Principal(
                principal_id="prin_bob", issuer="accretion-local", subject="bob"
            )
            await app.state.manager.store.upsert_principal(
                app.state.auth.local_principal_cache
            )
            hijacked = await client.get(
                f"/api/v1/oauth/callback/{GITHUB_CONNECTOR_ID}",
                params={"state": state_of(url), "code": server.issue_code(["read:user"])},
            )
        assert hijacked.status_code == 400
    finally:
        teardown()


async def test_revocation_empties_the_scopes_and_reports_unhealthy() -> None:
    server = FakeAuthorizationServer()
    store, client, _ = await harness(server)
    try:
        async with client:
            url = (
                await client.post(
                    f"/api/v1/connectors/{GITHUB_CONNECTOR_ID}/connect",
                    json={"workspace_id": WORKSPACE},
                )
            ).json()["authorization_url"]
            created = (
                await client.get(
                    f"/api/v1/oauth/callback/{GITHUB_CONNECTOR_ID}",
                    params={"state": state_of(url), "code": server.issue_code(["read:user"])},
                )
            ).json()
            revoked = await client.post(
                f"/api/v1/connections/{created['connection_id']}/revoke"
            )
            health = await client.get(
                f"/api/v1/connections/{created['connection_id']}/health"
            )
        assert revoked.json()["status"] == ConnectionStatus.REVOKED.value
        assert revoked.json()["granted_scopes"] == []
        assert health.json()["token_status"] == TokenStatus.REVOKED.value
        stored = await store.get_connection(created["connection_id"])
        assert stored is not None and stored.token_handle_ref is not None
        # The ciphertext is destroyed, not merely dereferenced.
        assert await store.get_secret_record(stored.token_handle_ref) is None
    finally:
        teardown()


async def test_a_connector_cannot_be_talked_into_scopes_it_never_declared() -> None:
    server = FakeAuthorizationServer()
    _, client, _ = await harness(server)
    try:
        async with client:
            response = await client.post(
                f"/api/v1/connectors/{GITHUB_CONNECTOR_ID}/connect",
                json={"workspace_id": WORKSPACE, "scopes": ["admin:everything"]},
            )
        assert response.status_code == 400
        assert "admin:everything" in response.json()["message"]
    finally:
        teardown()
