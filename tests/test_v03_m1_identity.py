from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fake_idp import CLIENT_ID, ISSUER, FakeIdp, FakeUser
from httpx import ASGITransport, AsyncClient

from accretion.api.auth import AuthRuntime
from accretion.api.main import app
from accretion.contracts import (
    Connection,
    ConnectionScope,
    ConnectionStatus,
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
    PrincipalStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.identity import (
    LOCAL_WORKSPACE_ID,
    AuthenticationError,
    IdentityService,
    OidcClient,
    OidcProviderConfig,
    code_challenge_s256,
)
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore


def build_service(idp: FakeIdp) -> IdentityService:
    store = MemoryStore()
    oidc = OidcClient(
        config=OidcProviderConfig(issuer=ISSUER, client_id=CLIENT_ID),
        http=httpx.AsyncClient(
            transport=ASGITransport(app=idp.app()), base_url=ISSUER
        ),
    )
    return IdentityService(store, oidc)


async def login(
    service: IdentityService, idp: FakeIdp, user: FakeUser
) -> tuple[str, str]:
    """Run the full code flow; returns (principal_id, auth_session_id)."""
    url = await service.begin_login()
    query = parse_qs(urlparse(url).query)
    state = query["state"][0]
    nonce = query["nonce"][0]
    code = idp.issue_code(user, nonce)
    principal, session = await service.complete_login(state=state, code=code)
    return principal.principal_id, session.auth_session_id


async def test_pkce_challenge_is_s256_and_verifier_never_leaves_the_server() -> None:
    idp = FakeIdp()
    service = build_service(idp)
    url = await service.begin_login()
    query = parse_qs(urlparse(url).query)
    assert query["code_challenge_method"] == ["S256"]
    transactions = list(service.store.auth_transactions.values())  # type: ignore[attr-defined]
    assert len(transactions) == 1
    verifier = transactions[0].code_verifier
    assert query["code_challenge"] == [code_challenge_s256(verifier)]
    assert verifier not in url


async def test_full_login_creates_principal_membership_and_session() -> None:
    idp = FakeIdp()
    service = build_service(idp)
    principal_id, session_id = await login(
        service, idp, FakeUser("alice", email="alice@example.test", name="Alice")
    )
    principal = await service.store.get_principal(principal_id)
    assert principal is not None
    assert (principal.issuer, principal.subject) == (ISSUER, "alice")
    assert principal.email == "alice@example.test"
    memberships = await service.store.list_workspace_memberships(
        principal_id=principal_id
    )
    assert [m.role for m in memberships] == [WorkspaceRole.DEVELOPER]
    assert await service.resolve_session(session_id) == principal


async def test_changed_email_updates_in_place_without_duplicate_identity() -> None:
    idp = FakeIdp()
    service = build_service(idp)
    first_id, _ = await login(service, idp, FakeUser("alice", email="old@example.test"))
    second_id, _ = await login(service, idp, FakeUser("alice", email="new@example.test"))
    assert first_id == second_id
    assert len(await service.store.list_principals()) == 1
    principal = await service.store.get_principal(first_id)
    assert principal is not None and principal.email == "new@example.test"


async def test_wrong_issuer_audience_and_nonce_fail_authentication() -> None:
    for override in (
        {"claim_issuer": "https://evil.test"},
        {"audience": "someone-else"},
        {"nonce_override": "wrong-nonce"},
    ):
        idp = FakeIdp(**override)  # type: ignore[arg-type]
        service = build_service(idp)
        url = await service.begin_login()
        query = parse_qs(urlparse(url).query)
        code = idp.issue_code(FakeUser("alice"), query["nonce"][0])
        with pytest.raises(AuthenticationError):
            await service.complete_login(state=query["state"][0], code=code)


async def test_discovery_issuer_mismatch_is_rejected() -> None:
    idp = FakeIdp(issuer=ISSUER)
    service = build_service(idp)
    assert service.oidc is not None
    service.oidc.config = OidcProviderConfig(
        issuer="https://expected.test", client_id=CLIENT_ID
    )
    with pytest.raises(AuthenticationError):
        await service.oidc.discover()


async def test_unknown_replayed_and_expired_state_fail_closed() -> None:
    idp = FakeIdp()
    service = build_service(idp)
    with pytest.raises(AuthenticationError):
        await service.complete_login(state="never-issued", code="whatever")
    url = await service.begin_login()
    query = parse_qs(urlparse(url).query)
    state, nonce = query["state"][0], query["nonce"][0]
    code = idp.issue_code(FakeUser("alice"), nonce)
    await service.complete_login(state=state, code=code)
    with pytest.raises(AuthenticationError):  # replay
        await service.complete_login(state=state, code=code)
    url = await service.begin_login()
    query = parse_qs(urlparse(url).query)
    stale = service.store.auth_transactions[query["state"][0]]  # type: ignore[attr-defined]
    service.store.auth_transactions[query["state"][0]] = stale.model_copy(  # type: ignore[attr-defined]
        update={"expires_at": datetime.now(UTC) - timedelta(minutes=1)}
    )
    with pytest.raises(AuthenticationError):
        await service.complete_login(state=query["state"][0], code="whatever")


async def test_local_principal_mode_is_deterministic_and_owner() -> None:
    store = MemoryStore()
    service = IdentityService(store, None)
    first = await service.local_principal()
    second = await service.local_principal()
    assert first.principal_id == second.principal_id
    memberships = await store.list_workspace_memberships(
        principal_id=first.principal_id
    )
    assert [m.role for m in memberships] == [WorkspaceRole.OWNER]
    assert memberships[0].workspace_id == LOCAL_WORKSPACE_ID


def oidc_runtime(service: IdentityService) -> AuthRuntime:
    return AuthRuntime(
        mode="OIDC",
        identity=service,
        cookie_name="accretion_session",
        cookie_secure=False,
        session_ttl_seconds=3600,
    )


async def test_multi_user_sessions_isolate_and_logout_revokes_one() -> None:
    idp = FakeIdp()
    service = build_service(idp)
    app.state.manager = type("M", (), {"store": service.store})()
    app.state.auth = oidc_runtime(service)
    try:
        _, alice_session = await login(
            service, idp, FakeUser("alice", name="Alice")
        )
        _, bob_session = await login(service, idp, FakeUser("bob", name="Bob"))
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unauthenticated = await client.get("/api/v1/me")
            assert unauthenticated.status_code == 401
            alice_me = await client.get(
                "/api/v1/me", headers={"Cookie": f"accretion_session={alice_session}"}
            )
            assert alice_me.status_code == 200
            assert alice_me.json()["principal"]["subject"] == "alice"
            bob_me = await client.get(
                "/api/v1/me", headers={"Cookie": f"accretion_session={bob_session}"}
            )
            assert bob_me.json()["principal"]["subject"] == "bob"
            logout = await client.post(
                "/api/v1/auth/logout", headers={"Cookie": f"accretion_session={alice_session}"}
            )
            assert logout.status_code == 204
            after = await client.get(
                "/api/v1/me", headers={"Cookie": f"accretion_session={alice_session}"}
            )
            assert after.status_code == 401
            bob_still = await client.get(
                "/api/v1/me", headers={"Cookie": f"accretion_session={bob_session}"}
            )
            assert bob_still.status_code == 200
    finally:
        del app.state.auth
        del app.state.manager


async def test_disabled_principal_is_refused_everywhere() -> None:
    idp = FakeIdp()
    service = build_service(idp)
    app.state.manager = type("M", (), {"store": service.store})()
    app.state.auth = oidc_runtime(service)
    try:
        principal_id, session_id = await login(service, idp, FakeUser("mallory"))
        principal = await service.store.get_principal(principal_id)
        assert principal is not None
        await service.store.upsert_principal(
            principal.model_copy(update={"status": PrincipalStatus.DISABLED})
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/me", headers={"Cookie": f"accretion_session={session_id}"}
            )
            assert response.status_code == 403
            assert response.json()["code"] == "FORBIDDEN"
    finally:
        del app.state.auth
        del app.state.manager


async def test_per_user_connection_never_resolves_for_another_user() -> None:
    idp = FakeIdp()
    service = build_service(idp)
    store = service.store
    app.state.manager = type("M", (), {"store": store})()
    app.state.auth = oidc_runtime(service)
    try:
        alice_id, alice_session = await login(service, idp, FakeUser("alice"))
        _, bob_session = await login(service, idp, FakeUser("bob"))
        from accretion.contracts import (
            Capability,
            CapabilityBackend,
            CapabilityBinding,
            CapabilityBindingBackend,
        )

        await store.upsert_capability(
            Capability(
                capability_id="cap.private",
                version="1.0.0",
                backend=CapabilityBackend.PYTHON,
            )
        )
        await store.upsert_connector_definition(
            ConnectorDefinition(
                connector_id="conndef_private",
                name="private",
                kind=ConnectorKind.LOCAL,
                auth_type=ConnectorAuthType.API_KEY,
            )
        )
        await store.upsert_capability_binding(
            CapabilityBinding(
                binding_id="capbind_private",
                capability_id="cap.private",
                connector_id="conndef_private",
                backend=CapabilityBindingBackend(type=CapabilityBackend.PYTHON),
            )
        )
        await store.upsert_connection(
            Connection(
                connection_id="conn_alice",
                connector_id="conndef_private",
                workspace_id=LOCAL_WORKSPACE_ID,
                principal_id=alice_id,
                scope=ConnectionScope.USER,
                status=ConnectionStatus.ACTIVE,
            )
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alice = await client.post(
                "/api/v1/capabilities/resolve",
                json={"capability_id": "cap.private"},
                headers={"Cookie": f"accretion_session={alice_session}"},
            )
            assert alice.json()["outcome"] == "OK"
            bob = await client.post(
                "/api/v1/capabilities/resolve",
                json={"capability_id": "cap.private"},
                headers={"Cookie": f"accretion_session={bob_session}"},
            )
            assert bob.json()["outcome"] == "NO_CONNECTION"
    finally:
        del app.state.auth
        del app.state.manager


async def test_role_change_is_live_without_reinstall() -> None:
    idp = FakeIdp()
    service = build_service(idp)
    app.state.manager = type("M", (), {"store": service.store})()
    app.state.auth = oidc_runtime(service)
    try:
        principal_id, session_id = await login(service, idp, FakeUser("carol"))
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            before = await client.get(
                "/api/v1/me", headers={"Cookie": f"accretion_session={session_id}"}
            )
            assert before.json()["memberships"][0]["role"] == "DEVELOPER"
            await service.store.upsert_workspace_membership(
                WorkspaceMembership(
                    membership_id=new_id("workspace_membership"),
                    workspace_id=LOCAL_WORKSPACE_ID,
                    principal_id=principal_id,
                    role=WorkspaceRole.ADMIN,
                )
            )
            after = await client.get(
                "/api/v1/me", headers={"Cookie": f"accretion_session={session_id}"}
            )
            membership = after.json()["memberships"][0]
            assert membership["role"] == "ADMIN"
            assert membership["revision"] == 2
    finally:
        del app.state.auth
        del app.state.manager
