"""Token broker, secret store, and OAuth transaction state (v0.3 M2).

Covers the milestone's build list — transaction state, token storage, refresh/revoke,
scope model — and the acceptance criteria that make "zero secret leakage" checkable
rather than asserted.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fake_authorization_server import (
    CLIENT_ID,
    CLIENT_SECRET,
    ISSUER,
    FakeAuthorizationServer,
)
from httpx import ASGITransport

from accretion.contracts import (
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
    OAuthTransaction,
    OAuthTransactionPurpose,
    TokenStatus,
)
from accretion.ids import new_id
from accretion.oauth import OAuthClient, OAuthEndpoints, OAuthError
from accretion.persistence.store import MemoryStore
from accretion.redaction import redact
from accretion.secrets_store import (
    EnvelopeSecretStore,
    EnvironmentKeyProvider,
    SecretStoreError,
    generate_master_key,
)
from accretion.token_broker import (
    EncryptedTokenBroker,
    EphemeralCredential,
    TokenBrokerError,
)

CONNECTOR_ID = "conndef_github"


class StaticKeyProvider:
    """Deterministic key, so tests never depend on process environment."""

    def __init__(self, key_id: str = "test-1") -> None:
        self._key_id = key_id
        self._material = os.urandom(32)

    @property
    def key_id(self) -> str:
        return self._key_id

    def material(self) -> bytes:
        return self._material


def connector(*, resource_server: str = "https://api.test") -> ConnectorDefinition:
    return ConnectorDefinition(
        connector_id=CONNECTOR_ID,
        name="GitHub",
        kind=ConnectorKind.REST,
        auth_type=ConnectorAuthType.OAUTH2,
        authorization_server=ISSUER,
        resource_server=resource_server,
        default_scopes=["repo:read"],
        optional_scopes=["repo:write"],
    )


def oauth_client(server: FakeAuthorizationServer) -> OAuthClient:
    return OAuthClient(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_url="http://localhost:8000/api/v1/oauth/callback/conndef_github",
        endpoints=OAuthEndpoints(
            authorization_url=f"{ISSUER}/login/oauth/authorize",
            token_url=f"{ISSUER}/login/oauth/access_token",
            revocation_url=f"{ISSUER}/login/oauth/revoke",
            audience=("https://api.test",),
        ),
        http=httpx.AsyncClient(transport=ASGITransport(app=server.app()), base_url=ISSUER),
    )


def build(server: FakeAuthorizationServer) -> tuple[EncryptedTokenBroker, MemoryStore]:
    store = MemoryStore()
    secrets_store = EnvelopeSecretStore(StaticKeyProvider())
    broker = EncryptedTokenBroker(
        store, secrets_store, clients={CONNECTOR_ID: oauth_client(server)}
    )
    return broker, store


async def authorize(
    broker: EncryptedTokenBroker,
    server: FakeAuthorizationServer,
    *,
    scopes: list[str] | None = None,
):
    client = broker.clients[CONNECTOR_ID]
    verifier = secrets.token_urlsafe(48)
    requested = scopes or ["repo:read"]
    code = server.issue_code(requested)
    response = await client.exchange_code(code=code, code_verifier=verifier)
    return await broker.store_authorization(
        connector=connector(),
        principal_id="prin_alice",
        workspace_id="workspace_test",
        response=response,
    )


# ------------------------------------------------------------------ secret store


async def test_sealed_secrets_are_unreadable_and_bound_to_their_handle() -> None:
    store = EnvelopeSecretStore(StaticKeyProvider())
    record = await store.seal("super-secret-token", associated_id="tkh_1")

    assert "super-secret-token" not in record.ciphertext
    assert await store.open(record, associated_id="tkh_1") == "super-secret-token"
    # AAD binding: a ciphertext lifted onto another handle will not open (AC3-CON-02).
    with pytest.raises(SecretStoreError):
        await store.open(record, associated_id="tkh_2")


async def test_a_different_key_cannot_open_a_sealed_secret() -> None:
    record = await EnvelopeSecretStore(StaticKeyProvider()).seal("t", associated_id="tkh_1")
    with pytest.raises(SecretStoreError):
        await EnvelopeSecretStore(StaticKeyProvider("test-2")).open(
            record, associated_id="tkh_1"
        )


def test_a_missing_or_malformed_master_key_fails_closed() -> None:
    provider = EnvironmentKeyProvider(variable="ACCRETION_TEST_MISSING_KEY")
    with pytest.raises(SecretStoreError, match="is not set"):
        provider.material()

    os.environ["ACCRETION_TEST_SHORT_KEY"] = base64.urlsafe_b64encode(b"tooshort").decode()
    try:
        short = EnvironmentKeyProvider(variable="ACCRETION_TEST_SHORT_KEY")
        with pytest.raises(SecretStoreError, match="32 bytes"):
            short.material()
    finally:
        del os.environ["ACCRETION_TEST_SHORT_KEY"]


def test_generated_master_key_is_accepted() -> None:
    os.environ["ACCRETION_TEST_GOOD_KEY"] = generate_master_key()
    try:
        assert len(EnvironmentKeyProvider(variable="ACCRETION_TEST_GOOD_KEY").material()) == 32
    finally:
        del os.environ["ACCRETION_TEST_GOOD_KEY"]


# ---------------------------------------------------------------------- lifecycle


async def test_authorization_stores_only_ciphertext_and_returns_a_handle() -> None:
    server = FakeAuthorizationServer()
    broker, store = build(server)

    handle = await authorize(broker, server)

    assert handle.status is TokenStatus.ACTIVE
    assert handle.scopes == ["repo:read"]
    assert handle.audience == ["https://api.test"]
    assert handle.expires_at is not None
    # The handle itself is opaque: it names a secret record, it does not carry one.
    assert "access_token" not in handle.model_dump_json()
    record = await store.get_secret_record(handle.secret_store_key)
    assert record is not None
    for issued in server.issued:
        assert issued not in record.ciphertext


async def test_granted_scopes_come_from_the_server_not_the_request() -> None:
    """A server may grant fewer scopes than asked for; recording the request would lie."""

    server = FakeAuthorizationServer(downgrade_scopes_to=["repo:read"])
    broker, _ = build(server)

    handle = await authorize(broker, server, scopes=["repo:read", "repo:write"])

    assert handle.scopes == ["repo:read"]


async def test_access_material_is_refused_for_scopes_the_grant_does_not_cover() -> None:
    server = FakeAuthorizationServer()
    broker, _ = build(server)
    handle = await authorize(broker, server)

    with pytest.raises(TokenBrokerError, match="scopes"):
        await broker.get_access_material(handle, audience=[], scopes=["repo:write"])


async def test_access_material_is_refused_for_an_unrelated_audience() -> None:
    server = FakeAuthorizationServer()
    broker, _ = build(server)
    handle = await authorize(broker, server)

    with pytest.raises(TokenBrokerError, match="audience"):
        await broker.get_access_material(
            handle, audience=["https://elsewhere.test"], scopes=[]
        )


async def test_expiring_material_is_refreshed_transparently() -> None:
    server = FakeAuthorizationServer()
    broker, store = build(server)
    handle = await authorize(broker, server)
    # Force staleness rather than waiting eight hours.
    stale = await store.upsert_token_handle(
        handle.model_copy(update={"expires_at": datetime.now(UTC) + timedelta(seconds=5)})
    )

    credential = await broker.get_access_material(stale, audience=[], scopes=["repo:read"])

    assert isinstance(credential, EphemeralCredential)
    refreshed = await store.get_token_handle(handle.token_handle_id)
    assert refreshed is not None
    assert refreshed.refreshed_at is not None
    assert refreshed.expires_at is not None
    assert refreshed.expires_at > datetime.now(UTC) + timedelta(seconds=60)


async def test_a_grant_without_a_refresh_token_expires_rather_than_refreshing() -> None:
    server = FakeAuthorizationServer(emit_refresh_token=False)
    broker, store = build(server)
    handle = await authorize(broker, server)

    with pytest.raises(TokenBrokerError, match="no refresh token"):
        await broker.refresh(handle)

    stored = await store.get_token_handle(handle.token_handle_id)
    assert stored is not None
    assert stored.status is TokenStatus.EXPIRED


async def test_a_rejected_refresh_marks_the_handle_in_error() -> None:
    server = FakeAuthorizationServer(refresh_should_fail=True)
    broker, store = build(server)
    handle = await authorize(broker, server)

    with pytest.raises(TokenBrokerError, match="rejected"):
        await broker.refresh(handle)

    stored = await store.get_token_handle(handle.token_handle_id)
    assert stored is not None
    assert stored.status is TokenStatus.ERROR


async def test_revocation_destroys_the_secret_and_fails_closed_afterwards() -> None:
    server = FakeAuthorizationServer()
    broker, store = build(server)
    handle = await authorize(broker, server)

    await broker.revoke(handle)

    stored = await store.get_token_handle(handle.token_handle_id)
    assert stored is not None
    assert stored.status is TokenStatus.REVOKED
    # The ciphertext is gone, not merely unreferenced (INV3-012).
    assert await store.get_secret_record(handle.secret_store_key) is None
    with pytest.raises(TokenBrokerError):
        await broker.get_access_material(stored, audience=[], scopes=[])


async def test_status_reports_expiry_without_mutating_the_handle() -> None:
    server = FakeAuthorizationServer()
    broker, _ = build(server)
    handle = await authorize(broker, server)
    past = handle.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})

    assert await broker.status(past) is TokenStatus.EXPIRED
    assert past.status is TokenStatus.ACTIVE


# ------------------------------------------------------------------- transactions


async def test_oauth_transaction_is_single_use_and_expires() -> None:
    store = MemoryStore()

    def transaction(state: str, *, ttl: int) -> OAuthTransaction:
        return OAuthTransaction(
            transaction_id=new_id("oauth_transaction"),
            purpose=OAuthTransactionPurpose.CONNECT,
            state=state,
            code_verifier=secrets.token_urlsafe(48),
            connector_id=CONNECTOR_ID,
            principal_id="prin_alice",
            workspace_id="workspace_test",
            requested_scopes=["repo:read"],
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
        )

    await store.create_oauth_transaction(transaction("state_live", ttl=600))
    await store.create_oauth_transaction(transaction("state_dead", ttl=-1))

    assert await store.consume_oauth_transaction("state_live") is not None
    # Replay fails closed (AC3-SEC-04).
    assert await store.consume_oauth_transaction("state_live") is None
    assert await store.consume_oauth_transaction("state_dead") is None
    assert await store.consume_oauth_transaction("state_unknown") is None


async def test_a_login_state_is_not_redeemable_as_a_connector_state() -> None:
    """ADR3-003: the two flows must not share a redemption keyspace."""

    from accretion.contracts import AuthTransaction

    store = MemoryStore()
    await store.create_auth_transaction(
        AuthTransaction(
            transaction_id=new_id("auth_transaction"),
            state="shared_state",
            nonce="n",
            code_verifier="v",
            expires_at=datetime.now(UTC) + timedelta(seconds=600),
        )
    )

    assert await store.consume_oauth_transaction("shared_state") is None
    assert await store.consume_auth_transaction("shared_state") is not None


# ------------------------------------------------------------------ no leakage


async def test_no_token_value_reaches_a_contract_a_repr_or_a_redacted_payload() -> None:
    """AC3-SEC-01, and the backlog's stricter "no token values in model, API, or logs"."""

    server = FakeAuthorizationServer()
    broker, store = build(server)
    handle = await authorize(broker, server)
    sentinel = server.issued[-1]

    credential = await broker.get_access_material(handle, audience=[], scopes=["repo:read"])

    # The credential can be used, but never rendered.
    assert credential.reveal() == sentinel
    assert sentinel not in repr(credential)
    assert sentinel not in str(credential)
    assert sentinel not in f"{credential}"

    # Nor does it appear in anything persisted or serialized.
    assert sentinel not in handle.model_dump_json()
    record = await store.get_secret_record(handle.secret_store_key)
    assert record is not None
    assert sentinel not in json.dumps(record.__dict__)

    # Nor in a redacted event payload carrying the handle.
    payload = redact({"token_handle_ref": handle.token_handle_id, "access_token": sentinel})
    assert payload["access_token"] == "[REDACTED]"
    assert payload["token_handle_ref"] == handle.token_handle_id


async def test_the_oauth_client_never_surfaces_a_rejected_body() -> None:
    server = FakeAuthorizationServer()
    client = oauth_client(server)

    with pytest.raises(OAuthError) as excinfo:
        await client.exchange_code(code="never-issued", code_verifier="v")

    assert "invalid_grant" not in str(excinfo.value)


def test_authorization_url_uses_pkce_s256_and_carries_no_secret() -> None:
    server = FakeAuthorizationServer()
    url = oauth_client(server).authorization_url(
        state="st", scopes=["repo:read"], code_verifier="verifier-value"
    )

    assert "code_challenge_method=S256" in url
    assert "verifier-value" not in url
    assert CLIENT_SECRET not in url
