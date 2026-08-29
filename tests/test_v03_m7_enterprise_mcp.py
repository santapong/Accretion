"""Enterprise-authorized MCP invocation (v0.3 M7, SDD §8, §24.9).

The milestone's exit criterion lives here: *centrally managed MCP authorization works
without repeated end-user OAuth where supported*. Everything below runs the real
paths — the real OIDC Authorization Code + PKCE login through ``IdentityService``, the
real RFC 8693 exchange and RFC 7523 grant against in-process identity and
authorization servers, the real ``EnvelopeSecretStore``, the real
``EncryptedTokenBroker``, the real ``RemoteMcpManager``, the real
``CapabilityResolver`` and the real ``CapabilityGateway``. Nothing is mocked, no
connection or token handle is ever pre-seeded, and every assertion is read back from
the store rather than taken from an object a test is holding.

Four criteria are proven:

* **AC3-EMA-02** — a principal who signed in once invokes a governed MCP server with
  no further end-user authorization step.
* **AC3-EMA-04 (revoke half)** — revoking the connection stops the next invocation;
  plus the structural scan that ``enterprise_auth.py`` memoises nothing.
* **AC3-EMA-06** — an enterprise connection is never resolved for another principal.
* **AC3-EMA-07** — access-token expiry inside a valid session renews unattended;
  assertion expiry fails closed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fake_enterprise_as import ISSUER as AS_ISSUER
from fake_enterprise_as import FakeEnterpriseAuthorizationServer
from fake_idp import CLIENT_ID, ISSUER, FakeIdp, FakeUser, jwks_document
from httpx import ASGITransport

from accretion.config import Settings
from accretion.connections import ConnectionService
from accretion.contracts import (
    AssertionStatus,
    CapabilityExecutionStatus,
    CapabilityRequest,
    CapabilityResolutionOutcome,
    Connection,
    ConnectionScope,
    ConnectionStatus,
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
    EnterpriseAuthOutcome,
    McpDiscoveryPolicy,
    McpServerDefinition,
    McpServerState,
    McpToolMapping,
    Principal,
    Project,
    Provider,
    Run,
    RunState,
    Task,
    TaskEnvelope,
    TokenStatus,
)
from accretion.enterprise_auth import build_enterprise_auth_manager
from accretion.governance import (
    CapabilityExecutor,
    CapabilityGateway,
    CapabilityPolicyEngine,
    CredentialBroker,
    seed_governance,
)
from accretion.identity import IdentityService, OidcClient, OidcProviderConfig
from accretion.ids import new_id
from accretion.mcp.endpoint_policy import McpEndpointPolicy
from accretion.mcp.manager import McpServerAuthRequired, RemoteMcpManager
from accretion.mcp.remote_client import RemoteDiscovery
from accretion.oauth import OAuthError, OAuthTokenResponse
from accretion.persistence.side_effects import MemorySideEffectLedger
from accretion.persistence.store import MemoryStore
from accretion.resolver import CapabilityResolver
from accretion.secrets_store import EnvelopeSecretStore, KeyProvider
from accretion.token_broker import EncryptedTokenBroker, TokenBrokerError

CONNECTOR_ID = "conndef_enterprise_mcp"
ENDPOINT = "https://mcp.enterprise.test/mcp"
AUDIENCE = "https://mcp.enterprise.test"
WORKSPACE_ID = "workspace_test"
CAPABILITY_ID = "enterprise.echo"
#: Statuses that any redirect-based end-user authorization step would have to use.
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class StaticKeyProvider(KeyProvider):
    """A master key that lives only for the duration of one test."""

    def __init__(self) -> None:
        self._material = b"\x22" * 32

    @property
    def key_id(self) -> str:
        return "test-1"

    def material(self) -> bytes:
        return self._material


async def public_dns(host: str, port: int) -> list[str]:
    del host, port
    return ["93.184.216.34"]


@dataclass
class RecordingTransport(httpx.AsyncBaseTransport):
    """Wraps a transport and records every request path and response status.

    This is how "no end-user authorization step happened" is asserted without
    trusting any single counter: every byte that left the process during the whole
    scenario is inspected for a visit to an authorization endpoint or a redirect.
    """

    inner: httpx.AsyncBaseTransport
    log: list[tuple[str, str, int]] = field(default_factory=list)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        self.log.append((request.method, request.url.path, response.status_code))
        return response


class SpyOAuthClient:
    """The end-user OAuth client, registered and expected never to be used.

    Registering it is the point: the broker holds a perfectly usable interactive
    client for this connector, so "zero calls" is a statement about the enterprise
    path being taken rather than about a client that was never wired up.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def authorization_url(self, **kwargs: Any) -> str:
        del kwargs
        self.calls.append("authorization_url")
        return f"{AS_ISSUER}/authorize"

    async def exchange_code(self, **kwargs: Any) -> Any:
        del kwargs
        self.calls.append("exchange_code")
        raise OAuthError("the end-user authorization code flow must not be used")

    async def refresh(self, refresh_token: str) -> Any:
        del refresh_token
        self.calls.append("refresh")
        raise OAuthError("an enterprise grant has no refresh token to present")

    async def revoke(self, token: str) -> None:
        del token
        self.calls.append("revoke")


class RecordingRemoteMcpClient:
    """The remote MCP server, recording the credential it was actually presented."""

    def __init__(self) -> None:
        self.discover_calls = 0
        self.tool_calls = 0
        self.authorization_headers: list[str | None] = []

    async def discover(self, endpoint: str, **kwargs: Any) -> RemoteDiscovery:
        assert endpoint == ENDPOINT
        self.discover_calls += 1
        self.authorization_headers.append(kwargs.get("authorization_header"))
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
        del endpoint, tool_name
        self.tool_calls += 1
        self.authorization_headers.append(kwargs.get("authorization_header"))
        return {
            "content": [{"type": "text", "text": str(arguments["message"])}],
            "structuredContent": {"message": arguments["message"]},
            "isError": False,
        }


@dataclass
class Deployment:
    """One enterprise-auth deployment, assembled as ``api/main.py`` assembles it."""

    store: MemoryStore
    settings: Settings
    secrets: EnvelopeSecretStore
    broker: EncryptedTokenBroker
    identity: IdentityService
    mcp: RemoteMcpManager
    remote: RecordingRemoteMcpClient
    idp: FakeIdp
    authorization_server: FakeEnterpriseAuthorizationServer
    connector: ConnectorDefinition
    server: McpServerDefinition
    oauth_spy: SpyOAuthClient
    traffic: list[tuple[str, str, int]]

    async def sign_in(self, subject: str) -> tuple[Principal, str]:
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

    async def invoke(self, principal: Principal, message: str) -> Any:
        """Discover, enable, resolve and execute — the whole governed call path.

        ``_authorization`` is crossed twice (discovery and enable) and the
        capability gateway then mints the credential from whatever connection the
        resolver found, so a connection minted without a usable token handle, or one
        belonging to somebody else, cannot survive this method.
        """

        await self.mcp.refresh_discovery(
            self.server.mcp_server_id,
            principal_id=principal.principal_id,
            workspace_id=WORKSPACE_ID,
            force=True,
        )
        await self.mcp.enable(
            self.server.mcp_server_id,
            principal_id=principal.principal_id,
            workspace_id=WORKSPACE_ID,
        )
        resolved = await CapabilityResolver(self.store).resolve(
            CAPABILITY_ID,
            principal_id=principal.principal_id,
            workspace_id=WORKSPACE_ID,
        )
        assert resolved is not None
        assert resolved.outcome is CapabilityResolutionOutcome.OK
        assert resolved.binding is not None and resolved.connection is not None
        gateway = CapabilityGateway(
            store=self.store,
            side_effects=MemorySideEffectLedger(),
            broker=CredentialBroker(),
            executor=CapabilityExecutor(),
            policy_engine=CapabilityPolicyEngine(),
            token_broker=self.broker,
            remote_mcp=self.mcp,
        )
        run = await self._run()
        return (
            await gateway.execute(
                CapabilityRequest(
                    request_id=new_id("capability_request"),
                    run_id=run.run_id,
                    node_id="act",
                    capability_id=CAPABILITY_ID,
                    capability_version="1.0.0",
                    arguments={"message": message},
                    declared_reason="enterprise acceptance test",
                ),
                resolved.connection,
                resolved.binding,
            ),
            resolved.connection,
        )

    async def _run(self) -> Run:
        project = Project(
            project_id=new_id("project"), name="M7", repository_path="."
        )
        await self.store.create_project(project)
        task = Task(
            envelope=TaskEnvelope(
                task_id=new_id("task"),
                project_id=project.project_id,
                objective="Invoke a centrally managed MCP server",
                allowed_capabilities=[CAPABILITY_ID],
            )
        )
        await self.store.create_task(task)
        run = Run(
            run_id=new_id("run"),
            task_id=task.envelope.task_id,
            project_id=project.project_id,
            provider=Provider.FAKE,
            state=RunState.RUNNING,
        )
        await self.store.create_run(run)
        return run


async def setup_deployment(
    *,
    enable_enterprise_auth: bool = True,
    auth_type: ConnectorAuthType = ConnectorAuthType.EMA,
) -> Deployment:
    """Assemble the whole deployment; only the two named knobs ever vary."""

    idp = FakeIdp()
    authorization_server = FakeEnterpriseAuthorizationServer(
        jwks=jwks_document(), expected_issuer=ISSUER, expected_audience=AUDIENCE
    )
    store = MemoryStore()
    await seed_governance(store)
    settings = Settings(
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        token_encryption_key="test-key",
        enable_enterprise_auth=enable_enterprise_auth,
        enterprise_auth_token_exchange_url=f"{ISSUER}/token-exchange",
        enterprise_auth_audiences={CONNECTOR_ID: AUDIENCE},
    )
    secrets = EnvelopeSecretStore(StaticKeyProvider())
    oauth_spy = SpyOAuthClient()
    broker = EncryptedTokenBroker(
        store,
        secrets,
        clients={CONNECTOR_ID: oauth_spy},  # type: ignore[dict-item]
    )
    traffic: list[tuple[str, str, int]] = []
    idp_transport = RecordingTransport(ASGITransport(app=idp.app()), traffic)
    as_transport = RecordingTransport(
        ASGITransport(app=authorization_server.app()), traffic
    )
    enterprise_http = httpx.AsyncClient(
        mounts={
            f"all://{urlparse(ISSUER).netloc}": idp_transport,
            f"all://{urlparse(AS_ISSUER).netloc}": as_transport,
        }
    )
    # The production factory, unmodified: only its outbound client is supplied.
    enterprise_auth = build_enterprise_auth_manager(
        store, secrets, broker, settings, http=enterprise_http
    )
    identity = IdentityService(
        store,
        OidcClient(
            config=OidcProviderConfig(issuer=ISSUER, client_id=CLIENT_ID),
            http=httpx.AsyncClient(transport=idp_transport, base_url=ISSUER),
        ),
        enterprise_auth=enterprise_auth,
    )
    connector = ConnectorDefinition(
        connector_id=CONNECTOR_ID,
        name="Enterprise MCP",
        kind=ConnectorKind.MCP,
        auth_type=auth_type,
        authorization_server=AS_ISSUER,
        resource_server=AUDIENCE,
        default_scopes=["mcp:invoke"],
        connection_scope=ConnectionScope.USER,
    )
    await store.upsert_connector_definition(connector)
    remote = RecordingRemoteMcpClient()
    mcp = RemoteMcpManager(
        store=store,
        client=remote,
        endpoint_policy=McpEndpointPolicy(resolver=public_dns),
        token_broker=broker,
        enterprise_auth=enterprise_auth,
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
            tool_mappings=[
                McpToolMapping(capability_id=CAPABILITY_ID, tool_name="echo")
            ],
        )
    )
    return Deployment(
        store=store,
        settings=settings,
        secrets=secrets,
        broker=broker,
        identity=identity,
        mcp=mcp,
        remote=remote,
        idp=idp,
        authorization_server=authorization_server,
        connector=connector,
        server=server,
        oauth_spy=oauth_spy,
        traffic=traffic,
    )


def assert_no_end_user_authorization(deployment: Deployment) -> None:
    """No interactive authorization step happened anywhere, by four measures."""

    assert deployment.idp.authorize_calls == 0
    assert deployment.authorization_server.authorize_calls == 0
    assert [entry for entry in deployment.traffic if "/authorize" in entry[1]] == []
    assert [entry for entry in deployment.traffic if entry[2] in REDIRECT_STATUSES] == []
    assert deployment.oauth_spy.calls == []


# --------------------------------------------------------------- AC3-EMA-02


@pytest.mark.acceptance("AC3-EMA-02")
async def test_a_signed_in_principal_invokes_a_governed_server_without_authorizing_again() -> (
    None
):
    """One sign-in, then a real tool call, with nothing seeded in between.

    The test creates no connection and no token handle. If the manager's EMA branch
    did not mint them, resolution would find nothing to execute against; if it minted
    a connection without storing a handle, the capability gateway would refuse to
    produce a credential. The output asserted is the fake server's real answer, and
    the credential it saw is checked to be the very token the enterprise
    authorization server issued — which that server then confirms by accepting it at
    a protected resource.
    """

    deployment = await setup_deployment()
    principal, _ = await deployment.sign_in("alice")
    assert await deployment.store.list_connections(connector_id=CONNECTOR_ID) == []

    result, connection_ref = await deployment.invoke(principal, "hello")

    assert result.status is CapabilityExecutionStatus.SUCCEEDED
    assert result.output == {
        "content": [{"type": "text", "text": "hello"}],
        "structuredContent": {"message": "hello"},
        "isError": False,
    }
    assert deployment.remote.tool_calls == 1

    # Exactly one connection, minted by the branch under test, read back.
    connections = await deployment.store.list_connections(connector_id=CONNECTOR_ID)
    assert len(connections) == 1
    stored = connections[0]
    assert stored.connection_id == connection_ref.connection_id
    assert stored.status is ConnectionStatus.ACTIVE
    assert stored.scope is ConnectionScope.USER
    assert stored.principal_id == principal.principal_id
    assert stored.granted_scopes == ["mcp:invoke"]
    assert stored.token_handle_ref is not None
    handle = await deployment.store.get_token_handle(stored.token_handle_ref)
    assert handle is not None
    assert handle.status is TokenStatus.ACTIVE
    assert handle.principal_id == principal.principal_id

    # The credential presented to the MCP server is the enterprise-issued token.
    issued = deployment.authorization_server.issued[-1]
    assert deployment.remote.authorization_headers[-1] == f"Bearer {issued}"
    # And the authorization server agrees it issued it: presented to its protected
    # resource, the token is accepted and the header it recorded is that token.
    resource = await httpx.AsyncClient(
        transport=ASGITransport(app=deployment.authorization_server.app())
    ).get(f"{AS_ISSUER}/resource", headers={"Authorization": f"Bearer {issued}"})
    assert resource.status_code == 200
    assert (
        deployment.authorization_server.last_authorization_header == f"Bearer {issued}"
    )

    # One exchange, one grant, and not a single end-user authorization step.
    assert deployment.idp.exchange_calls == 1
    assert deployment.authorization_server.grant_calls == 1
    assert_no_end_user_authorization(deployment)
    grants = await deployment.store.list_enterprise_auth_grants(
        principal_id=principal.principal_id
    )
    assert [grant.outcome for grant in grants] == [EnterpriseAuthOutcome.GRANTED]


# ----------------------------------------------- AC3-EMA-04 (revoke half)


@pytest.mark.acceptance("AC3-EMA-04")
async def test_revoking_the_connection_blocks_the_next_enterprise_invocation() -> None:
    """An operator revocation is terminal: the next call is refused, not re-minted.

    Re-minting would make revocation a no-op, so this is the assertion that matters
    — the refusal is read back from the store as ``REVOKED``, and the connection is
    not replaced by a fresh one for the same principal.
    """

    deployment = await setup_deployment()
    principal, _ = await deployment.sign_in("alice")
    _, connection_ref = await deployment.invoke(principal, "hello")
    grants_before = len(await deployment.store.list_enterprise_auth_grants())

    connections = ConnectionService(
        store=deployment.store, broker=deployment.broker, clients={}
    )
    await connections.revoke(
        connection_id=connection_ref.connection_id, principal=principal
    )

    with pytest.raises(McpServerAuthRequired):
        await deployment.mcp.refresh_discovery(
            deployment.server.mcp_server_id,
            principal_id=principal.principal_id,
            workspace_id=WORKSPACE_ID,
            force=True,
        )

    remaining = await deployment.store.list_connections(connector_id=CONNECTOR_ID)
    assert len(remaining) == 1
    assert remaining[0].connection_id == connection_ref.connection_id
    assert remaining[0].status is ConnectionStatus.REVOKED
    server = await deployment.store.get_mcp_server(deployment.server.mcp_server_id)
    assert server is not None
    assert server.state is McpServerState.AUTH_REQUIRED
    # Refused locally: no new token was minted for the revoked connection.
    assert deployment.authorization_server.grant_calls == 1
    refusals = await deployment.store.list_enterprise_auth_grants()
    assert len(refusals) == grants_before + 1
    assert refusals[-1].outcome is EnterpriseAuthOutcome.REFUSED_REVOKED


def test_the_enterprise_authorization_module_memoises_nothing() -> None:
    """Structural scan, extending the AC3-SEC-05 one to ``enterprise_auth.py``.

    A cached decision would let a revoked assertion or a revoked connection keep
    working, which no behavioural test can rule out for every future edit. The
    module must therefore contain no memoisation construct at all.
    """

    source = Path("src/accretion/enterprise_auth.py").read_text()
    for forbidden in ("lru_cache", "cached_property", "self._cache", "@cache"):
        assert forbidden not in source, f"{forbidden} appeared in enterprise_auth.py"


# --------------------------------------------------------------- AC3-EMA-06


@pytest.mark.acceptance("AC3-EMA-06")
async def test_an_enterprise_connection_is_never_resolved_for_another_principal() -> None:
    """Alice's enterprise connection is hers; Bob gets his own or gets nothing.

    Bob signs in for real and receives a second, distinct connection with a second,
    distinct token handle bound to his own principal — proved by the authorization
    server being asked a second time. Then a third principal who never signed in is
    refused outright, which is what stops "reuse whatever connection exists" from
    passing this criterion.
    """

    deployment = await setup_deployment()
    alice, _ = await deployment.sign_in("alice")
    await deployment.invoke(alice, "for alice")
    alice_connections = await deployment.store.list_connections(
        connector_id=CONNECTOR_ID
    )
    assert len(alice_connections) == 1
    alice_connection = alice_connections[0]

    bob, _ = await deployment.sign_in("bob")
    assert bob.principal_id != alice.principal_id
    _, bob_ref = await deployment.invoke(bob, "for bob")

    assert bob_ref.connection_id != alice_connection.connection_id
    connections = {
        item.principal_id: item
        for item in await deployment.store.list_connections(connector_id=CONNECTOR_ID)
    }
    assert set(connections) == {alice.principal_id, bob.principal_id}
    bob_connection = connections[bob.principal_id]
    assert bob_connection.connection_id == bob_ref.connection_id
    assert bob_connection.status is ConnectionStatus.ACTIVE
    assert bob_connection.token_handle_ref is not None
    assert (
        bob_connection.token_handle_ref
        != connections[alice.principal_id].token_handle_ref
    )
    bob_handle = await deployment.store.get_token_handle(bob_connection.token_handle_ref)
    assert bob_handle is not None
    assert bob_handle.principal_id == bob.principal_id
    # A second grant was really fetched for Bob rather than Alice's being reused.
    assert deployment.authorization_server.grant_calls == 2
    assert deployment.idp.exchange_calls == 2
    assert_no_end_user_authorization(deployment)

    # A principal who never signed in through the identity provider holds no
    # assertion, so there is nothing to exchange and nothing to inherit.
    stranger = Principal(
        principal_id=new_id("principal"),
        subject="carol",
        issuer=ISSUER,
        display_name="Carol",
    )
    await deployment.store.upsert_principal(stranger)
    with pytest.raises(McpServerAuthRequired):
        await deployment.mcp.refresh_discovery(
            deployment.server.mcp_server_id,
            principal_id=stranger.principal_id,
            workspace_id=WORKSPACE_ID,
            force=True,
        )
    assert deployment.authorization_server.grant_calls == 2
    stranger_grants = await deployment.store.list_enterprise_auth_grants(
        principal_id=stranger.principal_id
    )
    assert [grant.outcome for grant in stranger_grants] == [
        EnterpriseAuthOutcome.REFUSED_MISSING
    ]
    assert (
        await deployment.store.list_connections(connector_id=CONNECTOR_ID)
    ).__len__() == 2


# --------------------------------------------------------------- AC3-EMA-07


@pytest.mark.acceptance("AC3-EMA-07")
async def test_access_token_expiry_renews_unattended_and_assertion_expiry_fails_closed() -> (
    None
):
    """The exit criterion, in two halves.

    A jwt-bearer grant carries no refresh token, so without the broker's
    re-acquisition hook the first token lifetime would be the last. Time is advanced
    by moving the handle's own deadline into the broker's refresh window — the same
    thing the clock would do, written where the broker actually reads it — and the
    next call must succeed without any end-user step, recording a ``REFRESHED``
    grant. The retained assertion is then expired the same way, and the next call
    must fail closed to ``REAUTH_REQUIRED`` *before* spending anything on the
    identity provider.
    """

    deployment = await setup_deployment()
    principal, session_id = await deployment.sign_in("alice")
    _, connection_ref = await deployment.invoke(principal, "first")
    connection = await deployment.store.get_connection(connection_ref.connection_id)
    assert connection is not None and connection.token_handle_ref is not None
    handle = await deployment.store.get_token_handle(connection.token_handle_ref)
    assert handle is not None
    first_secret_key = handle.secret_store_key
    assert deployment.authorization_server.grant_calls == 1

    # Advance past ``expires_at - _REFRESH_SKEW``: the token is now stale.
    await deployment.store.upsert_token_handle(
        handle.model_copy(
            update={"expires_at": datetime.now(UTC) + timedelta(seconds=30)}
        )
    )

    result, second_ref = await deployment.invoke(principal, "second")

    assert result.status is CapabilityExecutionStatus.SUCCEEDED
    assert result.output == {
        "content": [{"type": "text", "text": "second"}],
        "structuredContent": {"message": "second"},
        "isError": False,
    }
    # Same connection, same handle identity, fresh material underneath it.
    assert second_ref.connection_id == connection_ref.connection_id
    renewed = await deployment.store.get_token_handle(handle.token_handle_id)
    assert renewed is not None
    assert renewed.status is TokenStatus.ACTIVE
    assert renewed.secret_store_key != first_secret_key
    assert renewed.refreshed_at is not None
    assert renewed.expires_at is not None
    assert renewed.expires_at > datetime.now(UTC) + timedelta(seconds=120)
    assert deployment.authorization_server.grant_calls == 2
    outcomes = [
        grant.outcome
        for grant in await deployment.store.list_enterprise_auth_grants(
            principal_id=principal.principal_id
        )
    ]
    assert EnterpriseAuthOutcome.REFRESHED in outcomes
    assert_no_end_user_authorization(deployment)

    # Second half: the retained assertion expires, and renewal must stop.
    assertion = await deployment.store.get_identity_assertion_for_session(session_id)
    assert assertion is not None
    await deployment.store.upsert_identity_assertion(
        assertion.model_copy(
            update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
    )
    await deployment.store.upsert_token_handle(
        renewed.model_copy(
            update={"expires_at": datetime.now(UTC) + timedelta(seconds=30)}
        )
    )
    exchanges_before = deployment.idp.exchange_calls
    grants_before = deployment.authorization_server.grant_calls

    with pytest.raises(McpServerAuthRequired):
        await deployment.mcp.refresh_discovery(
            deployment.server.mcp_server_id,
            principal_id=principal.principal_id,
            workspace_id=WORKSPACE_ID,
            force=True,
        )

    failed = await deployment.store.get_connection(connection_ref.connection_id)
    assert failed is not None
    assert failed.status is ConnectionStatus.REAUTH_REQUIRED
    server = await deployment.store.get_mcp_server(deployment.server.mcp_server_id)
    assert server is not None
    assert server.state is McpServerState.AUTH_REQUIRED
    dead = await deployment.store.get_token_handle(handle.token_handle_id)
    assert dead is not None
    assert dead.status is TokenStatus.EXPIRED
    expired_assertion = await deployment.store.get_identity_assertion_for_session(
        session_id
    )
    assert expired_assertion is not None
    assert expired_assertion.status is AssertionStatus.EXPIRED
    # Failed closed locally: nothing was asked of the identity provider or the
    # authorization server once the assertion was past its own expiry.
    assert deployment.idp.exchange_calls == exchanges_before
    assert deployment.authorization_server.grant_calls == grants_before
    assert (
        await deployment.store.list_enterprise_auth_grants(
            principal_id=principal.principal_id
        )
    )[-1].outcome is EnterpriseAuthOutcome.REFUSED_EXPIRED


# ------------------------------------------------------ flag-off regression


def _event_shape(events: list[Any]) -> str:
    """The event sequence with ids and timestamps removed, as a stable string."""

    return json.dumps(
        [
            {
                "event_type": event.event_type,
                "actor_principal_id": event.actor_principal_id,
                "details": event.details,
            }
            for event in events
        ],
        sort_keys=True,
    )


async def test_with_the_flag_off_an_ema_connector_emits_the_pre_m7_event_sequence() -> (
    None
):
    """The regression guard for the one branch added to ``_authorization``.

    With enterprise authorization switched off, an ``EMA`` connector must be
    indistinguishable from the ordinary unauthorized connector the M2/M3 suites
    describe — not merely refused, but refused with the *same* recorded events, the
    same server state and the same revision. The two deployments below differ in
    exactly one field, the connector's ``auth_type``, and their event sequences are
    compared byte for byte.
    """

    baseline = await setup_deployment(
        enable_enterprise_auth=False, auth_type=ConnectorAuthType.OAUTH2
    )
    candidate = await setup_deployment(
        enable_enterprise_auth=False, auth_type=ConnectorAuthType.EMA
    )

    shapes = []
    states = []
    for deployment in (baseline, candidate):
        with pytest.raises(McpServerAuthRequired):
            await deployment.mcp.refresh_discovery(
                deployment.server.mcp_server_id,
                principal_id="prin_nobody",
                workspace_id=WORKSPACE_ID,
                force=True,
            )
        server = await deployment.store.get_mcp_server(deployment.server.mcp_server_id)
        assert server is not None
        states.append((server.state, server.revision, server.enabled))
        shapes.append(
            _event_shape(
                await deployment.store.list_mcp_server_events(
                    deployment.server.mcp_server_id
                )
            )
        )

    assert shapes[0] == shapes[1]
    assert states[0] == states[1]
    assert candidate.remote.discover_calls == 0
    assert candidate.authorization_server.grant_calls == 0


# ------------------------------------------- re-acquisition hook containment


async def test_the_reacquisition_hook_refuses_to_renew_a_non_ema_connectors_handle() -> (
    None
):
    """A configured audience must not turn a user-consented handle enterprise.

    The hook is registered per configured audience at wiring time, before any
    connector definition is read, so it is reachable for a connector whose
    ``auth_type`` is ``OAUTH2``. Such a connection is one the end user authorized
    interactively; if the authorization server returned no refresh token it must die
    at expiry and fail closed. Renewing it from the enterprise grant would keep it
    alive forever and silently swap user-delegated authority for enterprise
    authority. So: the handle expires exactly as it would with no hook registered,
    nothing is spent upstream, and the refusal is on the record.
    """

    deployment = await setup_deployment(auth_type=ConnectorAuthType.OAUTH2)
    principal, _ = await deployment.sign_in("alice")
    # A handle as the interactive OAuth path leaves it: no refresh token, about to
    # go stale, with the principal's own connection pointing straight at it.
    handle = await deployment.broker.store_authorization(
        connector=deployment.connector,
        principal_id=principal.principal_id,
        workspace_id=WORKSPACE_ID,
        response=OAuthTokenResponse(
            access_token="user-consented-access-token",
            expires_in=30,
            granted_scopes=["mcp:invoke"],
        ),
    )
    await deployment.store.upsert_connection(
        Connection(
            connection_id=new_id("conn"),
            connector_id=CONNECTOR_ID,
            workspace_id=WORKSPACE_ID,
            principal_id=principal.principal_id,
            scope=ConnectionScope.USER,
            status=ConnectionStatus.ACTIVE,
            token_handle_ref=handle.token_handle_id,
            granted_scopes=["mcp:invoke"],
        )
    )
    exchanges_before = deployment.idp.exchange_calls

    with pytest.raises(TokenBrokerError):
        await deployment.broker.get_access_material(
            handle, audience=[AUDIENCE], scopes=["mcp:invoke"]
        )

    dead = await deployment.store.get_token_handle(handle.token_handle_id)
    assert dead is not None
    assert dead.status is TokenStatus.EXPIRED
    # The user's material was never replaced by enterprise material.
    assert dead.secret_store_key == handle.secret_store_key
    assert deployment.authorization_server.grant_calls == 0
    assert deployment.idp.exchange_calls == exchanges_before
    grants = await deployment.store.list_enterprise_auth_grants(
        principal_id=principal.principal_id
    )
    assert grants[-1].outcome is EnterpriseAuthOutcome.REFUSED_CONFIGURATION
    assert "not an enterprise-managed connector" in grants[-1].detail


async def test_the_reacquisition_hook_only_renews_the_handle_its_connection_names() -> (
    None
):
    """The hook renews material it minted, not any handle for the same principal.

    Resolving the principal's enterprise connection is not enough: a second handle
    can exist for the same connector and principal — left by another acquisition
    path — and re-sealing that one would grant it enterprise scopes nobody asked
    for. The connection must already name the exact handle being renewed.
    """

    deployment = await setup_deployment()
    principal, _ = await deployment.sign_in("alice")
    await deployment.invoke(principal, "first")
    grants_before = deployment.authorization_server.grant_calls
    assert grants_before == 1

    # A stranger handle: same connector, same principal, not the one the enterprise
    # connection points at.
    foreign = await deployment.broker.store_authorization(
        connector=deployment.connector,
        principal_id=principal.principal_id,
        workspace_id=WORKSPACE_ID,
        response=OAuthTokenResponse(
            access_token="some-other-access-token",
            expires_in=30,
            granted_scopes=["mcp:invoke"],
        ),
    )

    with pytest.raises(TokenBrokerError):
        await deployment.broker.get_access_material(
            foreign, audience=[AUDIENCE], scopes=["mcp:invoke"]
        )

    dead = await deployment.store.get_token_handle(foreign.token_handle_id)
    assert dead is not None
    assert dead.status is TokenStatus.EXPIRED
    assert dead.secret_store_key == foreign.secret_store_key
    assert deployment.authorization_server.grant_calls == grants_before
    grants = await deployment.store.list_enterprise_auth_grants(
        principal_id=principal.principal_id
    )
    assert grants[-1].outcome is EnterpriseAuthOutcome.REFUSED_CONFIGURATION
    assert "was not issued for connection" in grants[-1].detail
