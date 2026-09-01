"""Identity retention and sign-out for enterprise-managed authorization (v0.3 M7).

The manager built in PR2 is wired into ``IdentityService`` here: a verified sign-in
retains its id_token when — and only when — enterprise authorization is switched on,
and signing out destroys what was retained before the session row is revoked.

Everything below runs the real code paths. The identity provider and the enterprise
authorization server are in-process ASGI applications behind ``ASGITransport``, the
login is the real Authorization Code + PKCE flow through ``IdentityService``, the
sealing is the real ``EnvelopeSecretStore``, and the MCP server is registered through
the real ``RemoteMcpManager``. No connection is ever pre-seeded and nothing is mocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fake_enterprise_as import ISSUER as AS_ISSUER
from fake_enterprise_as import FakeEnterpriseAuthorizationServer
from fake_idp import CLIENT_ID, ISSUER, FakeIdp, FakeUser, jwks_document
from httpx import ASGITransport

from accretion.config import Settings
from accretion.contracts import (
    AssertionStatus,
    ConnectionScope,
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
    EnterpriseAuthOutcome,
    McpDiscoveryPolicy,
    McpServerDefinition,
    McpServerState,
    Principal,
)
from accretion.enterprise_auth import (
    EnterpriseAuthError,
    EnterpriseAuthManager,
    IdentityAssertionClient,
    JwtBearerClient,
    build_enterprise_auth_manager,
)
from accretion.identity import IdentityService, OidcClient, OidcProviderConfig
from accretion.ids import new_id
from accretion.mcp.endpoint_policy import McpEndpointPolicy
from accretion.mcp.manager import McpServerAuthRequired, RemoteMcpManager
from accretion.mcp.remote_client import RemoteDiscovery
from accretion.persistence.store import MemoryStore
from accretion.secrets_store import EnvelopeSecretStore, KeyProvider
from accretion.token_broker import EncryptedTokenBroker

CONNECTOR_ID = "conndef_enterprise_mcp"
ENDPOINT = "https://mcp.enterprise.test/mcp"
AUDIENCE = "https://mcp.enterprise.test"
WORKSPACE_ID = "workspace_test"


class StaticKeyProvider(KeyProvider):
    """A master key that lives only for the duration of one test."""

    def __init__(self) -> None:
        self._material = b"\x11" * 32

    @property
    def key_id(self) -> str:
        return "test-1"

    def material(self) -> bytes:
        return self._material


async def public_dns(host: str, port: int) -> list[str]:
    del host, port
    return ["93.184.216.34"]


class CountingRemoteMcpClient:
    """Counts every outbound MCP call so "never reached" can be asserted."""

    def __init__(self) -> None:
        self.discover_calls = 0
        self.tool_calls = 0

    async def discover(self, endpoint: str, **kwargs: Any) -> RemoteDiscovery:
        del kwargs
        assert endpoint == ENDPOINT
        self.discover_calls += 1
        return RemoteDiscovery(
            protocol_version="2026-07-28",
            server_info={"name": "fake-enterprise", "version": "1.0.0"},
            tools=[
                {
                    "name": "echo",
                    "description": "Echo a message",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                }
            ],
            resources=[],
            resource_templates=[],
            prompts=[],
            cache_hints={
                kind: (60_000, "private")
                for kind in ("tools", "resources", "resource_templates", "prompts")
            },
        )

    async def call_tool(
        self, endpoint: str, tool_name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        del endpoint, tool_name, kwargs
        self.tool_calls += 1
        return {"content": [{"type": "text", "text": str(arguments)}], "isError": False}


@dataclass
class Fixture:
    """One deployment, assembled exactly as ``api/main.py`` assembles it."""

    store: MemoryStore
    settings: Settings
    identity: IdentityService
    manager: EnterpriseAuthManager | None
    mcp: RemoteMcpManager
    remote: CountingRemoteMcpClient
    idp: FakeIdp
    authorization_server: FakeEnterpriseAuthorizationServer
    connector: ConnectorDefinition
    server: McpServerDefinition

    async def sign_in(self, subject: str = "alice") -> tuple[Principal, str]:
        """The real OIDC code flow, driven end to end through ``IdentityService``."""

        url = await self.identity.begin_login()
        query = parse_qs(urlparse(url).query)
        code = self.idp.issue_code(
            FakeUser(subject, email=f"{subject}@test"), query["nonce"][0]
        )
        principal, session = await self.identity.complete_login(
            state=query["state"][0], code=code
        )
        return principal, session.auth_session_id


async def setup_deployment(
    *, enable_enterprise_auth: bool, carry_manager_regardless: bool = False
) -> Fixture:
    """Build the whole enterprise-auth deployment; the flag is the only variable.

    Both the identity provider and the enterprise authorization server are live and
    reachable in either mode, and the connector and MCP server are registered in
    either mode, so a flag-off assertion about "nothing was exchanged" is about the
    flag and not about a fixture that was never capable of exchanging anything.

    ``carry_manager_regardless`` builds the manager even with the flag down, which
    the production factory would not do. That is the second, inner gate: a
    deployment which somehow carries the collaborator must still retain nothing.
    """

    idp = FakeIdp()
    authorization_server = FakeEnterpriseAuthorizationServer(
        jwks=jwks_document(), expected_issuer=ISSUER, expected_audience=AUDIENCE
    )
    store = MemoryStore()
    settings = Settings(
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        token_encryption_key="test-key",
        enable_enterprise_auth=enable_enterprise_auth,
        enterprise_auth_token_exchange_url=f"{ISSUER}/token-exchange",
        enterprise_auth_audiences={CONNECTOR_ID: AUDIENCE},
    )
    secrets = EnvelopeSecretStore(StaticKeyProvider())
    broker = EncryptedTokenBroker(store, secrets)
    # The production factory is used unmodified; only its outbound HTTP client is
    # supplied, routing the two absolute URLs the manager will build to the two
    # in-process applications. The manager itself cannot tell the difference.
    enterprise_http = httpx.AsyncClient(
        mounts={
            f"all://{urlparse(ISSUER).netloc}": ASGITransport(app=idp.app()),
            f"all://{urlparse(AS_ISSUER).netloc}": ASGITransport(
                app=authorization_server.app()
            ),
        }
    )
    manager = build_enterprise_auth_manager(
        store, secrets, broker, settings, http=enterprise_http
    )
    if manager is None and carry_manager_regardless:
        manager = EnterpriseAuthManager(
            store,
            secrets,
            broker,
            settings,
            IdentityAssertionClient(settings=settings, http=enterprise_http),
            JwtBearerClient(http=enterprise_http),
        )
    identity = IdentityService(
        store,
        OidcClient(
            config=OidcProviderConfig(issuer=ISSUER, client_id=CLIENT_ID),
            http=httpx.AsyncClient(
                transport=ASGITransport(app=idp.app()), base_url=ISSUER
            ),
        ),
        enterprise_auth=manager,
    )
    connector = ConnectorDefinition(
        connector_id=CONNECTOR_ID,
        name="Enterprise MCP",
        kind=ConnectorKind.MCP,
        auth_type=ConnectorAuthType.EMA,
        authorization_server=AS_ISSUER,
        resource_server=AUDIENCE,
        default_scopes=["mcp:invoke"],
        connection_scope=ConnectionScope.USER,
    )
    await store.upsert_connector_definition(connector)
    remote = CountingRemoteMcpClient()
    mcp = RemoteMcpManager(
        store=store,
        client=remote,
        endpoint_policy=McpEndpointPolicy(resolver=public_dns),
        token_broker=broker,
    )
    server = await mcp.register(
        McpServerDefinition(
            mcp_server_id=new_id("mcp_server"),
            workspace_id=WORKSPACE_ID,
            connector_id=CONNECTOR_ID,
            name="enterprise-mcp",
            endpoint=ENDPOINT,
            owner_principal_id="prin_admin",
            discovery_policy=McpDiscoveryPolicy(default_ttl_ms=60_000),
        )
    )
    return Fixture(
        store=store,
        settings=settings,
        identity=identity,
        manager=manager,
        mcp=mcp,
        remote=remote,
        idp=idp,
        authorization_server=authorization_server,
        connector=connector,
        server=server,
    )


# ------------------------------------------------------------------ retention


async def test_signing_in_with_the_feature_on_retains_an_assertion_bounded_by_its_token() -> (
    None
):
    """The positive control for AC3-EMA-01: the wiring does something when enabled."""

    fixture = await setup_deployment(enable_enterprise_auth=True)

    principal, session_id = await fixture.sign_in()

    assertion = await fixture.store.get_identity_assertion_for_session(session_id)
    assert assertion is not None
    assert assertion.principal_id == principal.principal_id
    assert assertion.issuer == ISSUER
    assert assertion.subject == "alice"
    assert assertion.status is AssertionStatus.ACTIVE
    # The fake provider's id_token lives 300 seconds; the session TTL is 8 hours.
    # The row is bounded by the token, never by the session (ADR3-M7-002).
    assert assertion.expires_at < datetime.now(UTC) + fixture.identity.session_ttl
    assert await fixture.store.get_secret_record(assertion.secret_store_key) is not None


# --------------------------------------------------------------- AC3-EMA-01


@pytest.mark.acceptance("AC3-EMA-01")
async def test_with_the_feature_disabled_an_ema_connector_behaves_as_an_unauthorized_one() -> (
    None
):
    """The whole deployment is built enterprise-capable, then only the flag goes down.

    The identity provider's exchange endpoint and the enterprise authorization
    server are both live and reachable, the connector is a real ``EMA`` connector,
    and the MCP server is really registered against it. The single difference from
    the enabled deployment is ``enable_enterprise_auth=False``.

    Three things are then true, and each is read back from the store rather than
    taken from an object this test holds: the governed call is refused exactly as an
    unauthorized OAuth connector's would be, with the server driven to
    ``AUTH_REQUIRED``; neither the identity provider nor the authorization server
    was asked for anything; and a *real* sign-in retained no assertion at all. The
    last one is what a "the connection is None, so raise" implementation cannot
    fake — it is why the login is run for real instead of being assumed.
    """

    fixture = await setup_deployment(enable_enterprise_auth=False)
    assert fixture.manager is None
    principal, session_id = await fixture.sign_in()

    with pytest.raises(McpServerAuthRequired):
        await fixture.mcp.refresh_discovery(
            fixture.server.mcp_server_id,
            principal_id=principal.principal_id,
            workspace_id=WORKSPACE_ID,
        )

    stored = await fixture.store.get_mcp_server(fixture.server.mcp_server_id)
    assert stored is not None
    assert stored.state is McpServerState.AUTH_REQUIRED
    # Refused before any network call: not the identity provider, not the
    # authorization server, not the MCP server itself.
    assert fixture.idp.exchange_calls == 0
    assert fixture.authorization_server.grant_calls == 0
    assert fixture.remote.discover_calls == 0
    # Nothing was retained by the real login, so there is nothing to exchange later.
    assert await fixture.store.get_identity_assertion_for_session(session_id) is None
    assert (
        await fixture.store.get_identity_assertion_for_principal(principal.principal_id)
        is None
    )
    assert await fixture.store.list_enterprise_auth_grants() == []
    # And no connection was conjured for the EMA connector.
    assert await fixture.store.list_connections(connector_id=CONNECTOR_ID) == []

    # The inner gate. Above, the deployment factory declined to build the manager at
    # all; here one is carried anyway, so the flag guard inside retention is the only
    # thing standing between a real sign-in and a sealed identity assertion. Both
    # gates are asserted because either alone would let the criterion pass on a
    # deployment that retains material it was told not to keep.
    carried = await setup_deployment(
        enable_enterprise_auth=False, carry_manager_regardless=True
    )
    assert carried.manager is not None
    carried_principal, carried_session = await carried.sign_in()

    assert (
        await carried.store.get_identity_assertion_for_session(carried_session) is None
    )
    assert (
        await carried.store.get_identity_assertion_for_principal(
            carried_principal.principal_id
        )
        is None
    )
    # Nothing was sealed at all: no record exists to have been sealed under.
    assert carried.store.secret_records == {}
    assert carried.idp.exchange_calls == 0
    assert carried.authorization_server.grant_calls == 0


# ------------------------------------------------- AC3-EMA-04 (logout half)


@pytest.mark.acceptance("AC3-EMA-04")
async def test_signing_out_destroys_the_retained_assertion_and_blocks_later_authorization() -> (
    None
):
    """Ending the session destroys the assertion and stops enterprise authorization.

    The row survives as evidence — there is no store method that deletes an identity
    assertion, and ``delete_secret_record`` stays the single deletion surface
    (AC3-PLG-05) — but the sealed material is gone, read back from the store. The
    subsequent acquisition attempt is what proves destruction rather than mere
    bookkeeping: it must be refused, and it must be refused *locally*, without
    spending a call on the identity provider.
    """

    fixture = await setup_deployment(enable_enterprise_auth=True)
    assert fixture.manager is not None
    principal, session_id = await fixture.sign_in()
    retained = await fixture.store.get_identity_assertion_for_session(session_id)
    assert retained is not None
    exchanges_before = fixture.idp.exchange_calls

    await fixture.identity.logout(session_id)

    assertion = await fixture.store.get_identity_assertion_for_session(session_id)
    assert assertion is not None
    assert assertion.status is AssertionStatus.REVOKED
    assert await fixture.store.get_secret_record(retained.secret_store_key) is None
    grants = await fixture.store.list_enterprise_auth_grants(
        principal_id=principal.principal_id
    )
    assert [grant.outcome for grant in grants] == [EnterpriseAuthOutcome.REVOKED]
    assert await fixture.store.get_auth_session(session_id) is None

    with pytest.raises(EnterpriseAuthError):
        await fixture.manager.ensure_access(
            fixture.connector,
            fixture.server,
            principal_id=principal.principal_id,
            workspace_id=WORKSPACE_ID,
        )

    assert fixture.idp.exchange_calls == exchanges_before
    assert fixture.authorization_server.grant_calls == 0


async def test_signing_out_revokes_the_assertion_before_the_session_row() -> None:
    """Ordering, observed rather than asserted about the source.

    If the session were revoked first, a crash between the two steps would leave a
    sealed identity assertion behind a session that no longer exists. The store is
    watched during ``logout`` to record which write lands first.
    """

    fixture = await setup_deployment(enable_enterprise_auth=True)
    _, session_id = await fixture.sign_in()
    order: list[str] = []
    revoke_session = fixture.store.revoke_auth_session
    delete_secret = fixture.store.delete_secret_record

    async def watched_revoke_session(auth_session_id: str) -> None:
        order.append("session")
        await revoke_session(auth_session_id)

    async def watched_delete_secret(secret_store_key: str) -> None:
        order.append("secret")
        await delete_secret(secret_store_key)

    fixture.store.revoke_auth_session = watched_revoke_session  # type: ignore[method-assign]
    fixture.store.delete_secret_record = watched_delete_secret  # type: ignore[method-assign]

    await fixture.identity.logout(session_id)

    assert order == ["secret", "session"]


async def test_logout_without_enterprise_auth_is_unchanged() -> None:
    """The pre-M7 deployment signs out exactly as it did: one write, no grants."""

    fixture = await setup_deployment(enable_enterprise_auth=False)
    _, session_id = await fixture.sign_in()

    await fixture.identity.logout(session_id)

    assert await fixture.store.get_auth_session(session_id) is None
    assert await fixture.store.list_enterprise_auth_grants() == []
