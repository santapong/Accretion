"""The operator connections surface leaks no credential (v0.3 M6, AC3-UI-02).

The `ConnectionsPage` is only as safe as the API it renders. Rather than assert that
the page omits a token, this drives a *sentinel* credential through the real
``EncryptedTokenBroker`` and a real ``Connection``, then exercises every request the
page makes over ASGI and searches each response body for the sentinel.

The status transition ``ACTIVE -> REAUTH_REQUIRED -> REVOKED`` is produced by the real
components that own it — ``RemoteMcpManager`` for the reauth mark, ``ConnectionService``
for the revocation — never by writing a status into the store by hand.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import httpx2
import pytest
from fake_research_api import FakeResearchApi
from httpx import ASGITransport, AsyncClient

from accretion.api.auth import AuthRuntime
from accretion.api.main import app
from accretion.api.schemas import ConnectionSummary
from accretion.connections import ConnectionService
from accretion.contracts import (
    Capability,
    CapabilityBackend,
    CapabilityBinding,
    CapabilityBindingBackend,
    Connection,
    ConnectionScope,
    ConnectionStatus,
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
    McpDiscoveryPolicy,
    McpServerDefinition,
    McpServerState,
    OAuthTransactionPurpose,
    PluginState,
    Principal,
    RiskLevel,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.governance import CapabilityPolicyEngine, seed_governance
from accretion.identity import IdentityService, code_challenge_s256
from accretion.ids import new_id
from accretion.mcp.endpoint_policy import McpEndpointPolicy
from accretion.mcp.manager import (
    McpServerAuthRequired,
    McpServerUnavailable,
    RemoteMcpManager,
)
from accretion.mcp.remote_client import (
    RemoteDiscovery,
    RemoteMcpAuthError,
    SdkRemoteMcpClient,
)
from accretion.oauth import OAuthClient, OAuthEndpoints, OAuthTokenResponse
from accretion.persistence.store import MemoryStore
from accretion.plugins.manager import DirectoryPluginSource, PluginManager
from accretion.plugins.manifest import canonical_manifest_digest, parse_manifest
from accretion.plugins.trust import PluginTrustVerifier
from accretion.research import (
    BACKENDS,
    CROSSREF_CONNECTOR,
    OPENALEX_CONNECTOR,
    RESEARCH_CAPABILITY_IDS,
    RESEARCH_PLUGIN_ID,
    bind_research_backend,
    seed_research_connectors,
)
from accretion.research.server import CROSSREF_HOST, OPENALEX_HOST
from accretion.secrets_store import EnvelopeSecretStore
from accretion.token_broker import EncryptedTokenBroker

ACCESS = "gho_m6_admin_surface_sentinel_access"
REFRESH = "ghr_m6_admin_surface_sentinel_refresh"
ISSUER = "https://authorization.test"
RESOURCE = "https://mcp.example.test"
ENDPOINT = f"{RESOURCE}/mcp"

#: Exactly what ``ConnectionSummary`` promises the frontend. The vitest fixtures in
#: apps/ui/src/pages/ConnectionsPage.test.tsx are pinned to the same set.
SUMMARY_KEYS = {
    "connection_id",
    "connector_id",
    "created_at",
    "granted_scopes",
    "last_health_check",
    "principal_id",
    "scope",
    "status",
    "workspace_id",
    "workspace_shareable",
}


class StaticKey:
    key_id = "m6-1"

    def material(self) -> bytes:
        return b"M" * 32


class AuthRejectingMcpClient:
    """A remote MCP server that has stopped accepting the credential."""

    def __init__(self) -> None:
        self.discover_calls = 0

    async def discover(self, endpoint: str, **kwargs: Any) -> RemoteDiscovery:
        del kwargs
        assert endpoint == ENDPOINT
        self.discover_calls += 1
        raise RemoteMcpAuthError("the remote server rejected the credential")

    async def call_tool(
        self, endpoint: str, tool_name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:  # pragma: no cover - discovery fails first
        del endpoint, tool_name, arguments, kwargs
        raise RemoteMcpAuthError("the remote server rejected the credential")


async def public_dns(host: str, port: int) -> list[str]:
    del host, port
    return ["93.184.216.34"]


class Surface:
    """Everything one admin-surface exercise needs, built from real components."""

    def __init__(
        self,
        *,
        store: MemoryStore,
        client: AsyncClient,
        principal: Principal,
        connector: ConnectorDefinition,
        connection: Connection,
        workspace_id: str,
        broker: EncryptedTokenBroker,
    ) -> None:
        self.store = store
        self.client = client
        self.principal = principal
        self.connector = connector
        self.connection = connection
        self.workspace_id = workspace_id
        self.broker = broker


async def setup_surface() -> Surface:
    """Push the sentinel through the real broker and stand the API up over ASGI."""

    suffix = uuid4().hex[:12]
    store = MemoryStore()
    workspace_id = f"wks_{suffix}"
    who = Principal(
        principal_id=f"prin_{suffix}", issuer="accretion-local", subject=f"alice-{suffix}"
    )
    await store.upsert_principal(who)
    await store.upsert_workspace(WorkspaceEntity(workspace_id=workspace_id, name="M6"))
    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=f"wsm_{suffix}",
            workspace_id=workspace_id,
            principal_id=who.principal_id,
            role=WorkspaceRole.OWNER,
        )
    )
    connector = ConnectorDefinition(
        connector_id=f"conndef_{suffix}",
        name="Remote MCP",
        kind=ConnectorKind.MCP,
        auth_type=ConnectorAuthType.OAUTH2,
        authorization_server=ISSUER,
        resource_server=RESOURCE,
        default_scopes=["mcp:invoke"],
    )
    await store.upsert_connector_definition(connector)

    broker = EncryptedTokenBroker(store, EnvelopeSecretStore(StaticKey()))
    handle = await broker.store_authorization(
        connector=connector,
        principal_id=who.principal_id,
        workspace_id=workspace_id,
        response=OAuthTokenResponse(
            access_token=ACCESS,
            refresh_token=REFRESH,
            expires_in=28_800,
            granted_scopes=["mcp:invoke"],
        ),
    )
    connection = await store.upsert_connection(
        Connection(
            connection_id=new_id("conn"),
            connector_id=connector.connector_id,
            workspace_id=workspace_id,
            principal_id=who.principal_id,
            scope=ConnectionScope.USER,
            status=ConnectionStatus.ACTIVE,
            granted_scopes=["mcp:invoke"],
            token_handle_ref=handle.token_handle_id,
        )
    )

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
        broker=broker,
        clients={
            connector.connector_id: OAuthClient(
                client_id="accretion-m6",
                client_secret="m6-client-secret",
                redirect_url=(
                    f"http://test/api/v1/oauth/callback/{connector.connector_id}"
                ),
                endpoints=OAuthEndpoints(
                    authorization_url=f"{ISSUER}/authorize",
                    token_url=f"{ISSUER}/token",
                    revocation_url=f"{ISSUER}/revoke",
                ),
                # `begin` builds a URL and performs no network I/O; nothing in this
                # test reaches the token endpoint, so an unused client is honest here.
                http=httpx.AsyncClient(base_url=ISSUER),
            )
        },
    )
    return Surface(
        store=store,
        client=AsyncClient(transport=ASGITransport(app=app), base_url="http://test"),
        principal=who,
        connector=connector,
        connection=connection,
        workspace_id=workspace_id,
        broker=broker,
    )


def teardown_surface() -> None:
    for name in ("connections", "auth", "manager"):
        if hasattr(app.state, name):
            delattr(app.state, name)


async def force_reauthorization(surface: Surface) -> None:
    """Make the real ``RemoteMcpManager`` mark the connection as needing re-consent."""

    remote = AuthRejectingMcpClient()
    manager = RemoteMcpManager(
        store=surface.store,
        client=remote,  # type: ignore[arg-type]
        endpoint_policy=McpEndpointPolicy(resolver=public_dns),
        token_broker=surface.broker,
    )
    server = await manager.register(
        McpServerDefinition(
            mcp_server_id=new_id("mcp_server"),
            workspace_id=surface.workspace_id,
            connector_id=surface.connector.connector_id,
            name="Remote MCP",
            endpoint=ENDPOINT,
            owner_principal_id=surface.principal.principal_id,
            discovery_policy=McpDiscoveryPolicy(default_ttl_ms=60_000),
        )
    )
    with pytest.raises(McpServerAuthRequired):
        await manager.refresh_discovery(
            server.mcp_server_id,
            principal_id=surface.principal.principal_id,
            workspace_id=surface.workspace_id,
        )
    # The mark must come from the remote rejecting the credential, not from the
    # manager refusing to assemble one: that would prove nothing about reauth.
    assert remote.discover_calls == 1
    stored_server = await surface.store.get_mcp_server(server.mcp_server_id)
    assert stored_server is not None
    assert stored_server.state is McpServerState.AUTH_REQUIRED


async def assert_authorization_started(
    surface: Surface,
    authorization_url: str,
    *,
    purpose: OAuthTransactionPurpose,
    connection_id: str | None,
) -> str:
    """Prove the returned URL is a real PKCE authorization request backed by a stored
    transaction, and return its ``state``.

    A stub that answers 200 with any string fails here: the URL must point at the
    connector's authorization endpoint, and its ``state`` must key a transaction the
    callback can consume, whose ``code_verifier`` hashes to the advertised challenge.
    """

    parsed = urlparse(authorization_url)
    assert authorization_url.startswith(f"{ISSUER}/authorize"), authorization_url
    query = parse_qs(parsed.query)
    state = query["state"][0]
    challenge = query["code_challenge"][0]
    assert state
    assert challenge
    assert query["code_challenge_method"] == ["S256"]

    transaction = await surface.store.consume_oauth_transaction(state)
    assert transaction is not None, "authorization URL is not backed by a transaction"
    assert transaction.purpose is purpose
    assert transaction.connection_id == connection_id
    assert transaction.connector_id == surface.connector.connector_id
    assert transaction.principal_id == surface.principal.principal_id
    assert transaction.workspace_id == surface.workspace_id
    # The challenge in the URL is derived from the verifier only the store holds.
    assert challenge == code_challenge_s256(transaction.code_verifier)
    return state


@pytest.mark.acceptance("AC3-UI-02")
async def test_no_connections_surface_request_returns_the_credential_it_describes() -> None:
    surface = await setup_surface()
    connection_id = surface.connection.connection_id
    connector_id = surface.connector.connector_id
    bodies: dict[str, str] = {}
    try:
        async with surface.client as client:
            # The six requests ConnectionsPage makes, in the order an operator makes them.
            bodies["GET /connectors"] = (await client.get("/api/v1/connectors")).text
            listing = await client.get("/api/v1/connections")
            bodies["GET /connections"] = listing.text
            assert listing.status_code == 200
            assert [item["status"] for item in listing.json()] == ["ACTIVE"]

            connect = await client.post(
                f"/api/v1/connectors/{connector_id}/connect",
                json={"workspace_id": surface.workspace_id, "redirect_target": "/"},
            )
            assert connect.status_code == 200
            bodies["POST /connectors/{id}/connect"] = connect.text
            connect_state = await assert_authorization_started(
                surface,
                connect.json()["authorization_url"],
                purpose=OAuthTransactionPurpose.CONNECT,
                connection_id=None,
            )

            reauth = await client.post(
                f"/api/v1/connections/{connection_id}/reauthorize",
                json={"workspace_id": surface.workspace_id, "redirect_target": "/"},
            )
            assert reauth.status_code == 200
            bodies["POST /connections/{id}/reauthorize"] = reauth.text
            reauth_state = await assert_authorization_started(
                surface,
                reauth.json()["authorization_url"],
                purpose=OAuthTransactionPurpose.REAUTHORIZE,
                connection_id=connection_id,
            )
            # Two independent consent flows: reusing a state would let one callback
            # satisfy the other (INV3-008).
            assert connect_state != reauth_state

            health = await client.get(f"/api/v1/connections/{connection_id}/health")
            assert health.status_code == 200
            bodies["GET /connections/{id}/health"] = health.text

            # A real remote rejection moves ACTIVE -> REAUTH_REQUIRED, and the surface
            # must show it without being told.
            await force_reauthorization(surface)
            degraded = await client.get("/api/v1/connections")
            bodies["GET /connections (reauth)"] = degraded.text
            assert [item["status"] for item in degraded.json()] == ["REAUTH_REQUIRED"]

            revoke = await client.post(f"/api/v1/connections/{connection_id}/revoke")
            assert revoke.status_code == 200
            bodies["POST /connections/{id}/revoke"] = revoke.text
            assert revoke.json()["status"] == "REVOKED"
    finally:
        teardown_surface()

    leaks = {
        name: token for name, body in bodies.items() for token in (ACCESS, REFRESH) if token in body
    }
    assert not leaks, f"credential reached the admin surface: {leaks}"
    # Non-vacuous: every request answered with a real body.
    assert len(bodies) == 7
    assert all(len(body) > 2 for body in bodies.values()), bodies

    # The status history the page renders, as the store recorded it.
    stored = await surface.store.get_connection(connection_id)
    assert stored is not None
    assert stored.status is ConnectionStatus.REVOKED
    # The handle reference survives revocation: it is an opaque correlation key, and
    # dropping it would orphan the audit trail rather than protect anything.
    assert stored.token_handle_ref == surface.connection.token_handle_ref
    handle = await surface.store.get_token_handle(stored.token_handle_ref or "")
    assert handle is not None
    assert ACCESS not in handle.model_dump_json()


@pytest.mark.acceptance("AC3-UI-02")
async def test_the_connections_surface_exposes_exactly_the_summary_key_set() -> None:
    """Pin the contract the page is allowed to read.

    Widening ``ConnectionSummary`` — most dangerously with ``token_handle_ref`` — must
    fail here and in the vitest key-set assertion at the same time.
    """

    assert set(ConnectionSummary.model_fields) == SUMMARY_KEYS

    surface = await setup_surface()
    try:
        async with surface.client as client:
            listing = await client.get("/api/v1/connections")
            assert listing.status_code == 200
            payload = listing.json()
            assert [set(item) for item in payload] == [SUMMARY_KEYS]
            revoked = await client.post(
                f"/api/v1/connections/{surface.connection.connection_id}/revoke"
            )
            assert set(revoked.json()) == SUMMARY_KEYS
    finally:
        teardown_surface()

    # The stored aggregate does carry the handle; the summary is what withholds it.
    stored = await surface.store.get_connection(surface.connection.connection_id)
    assert stored is not None
    assert stored.token_handle_ref
    assert "token_handle_ref" not in json.dumps(payload)


# ======================================================================================
# Plugins and MCP servers: the fixtures the operator pages are tested against
# (v0.3 M6, AC3-UI-01 and AC3-UI-03)
# ======================================================================================
#
# The two admin pages are only trustworthy if what vitest renders is what the API
# really answers. So the fixtures under ``apps/ui/src/pages/__fixtures__`` are *not*
# hand-written: they are produced here, from the live ASGI app, over a real
# ``PluginManager`` that installs the bundled research package and a real
# ``RemoteMcpManager`` driven against the in-process fake research servers until its
# circuit breaker trips. This module then asserts the committed files are byte-for-byte
# what that run produces, so a backend change that alters either payload fails here
# instead of silently invalidating every frontend test.

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "apps" / "ui" / "src" / "pages" / "__fixtures__"

#: Set to regenerate the committed fixtures after an intended payload change.
REGENERATE = os.environ.get("ACCRETION_REGENERATE_UI_FIXTURES") == "1"

M6_WORKSPACE_PERMISSIONS = frozenset(
    {"research.read", "github.read", "accretion.sample.read", "accretion.sample.write"}
)
SAMPLE_PLUGIN_ID = "accretion-sample-plugin"

#: Every field whose value is a wall-clock instant. Replaced with a fixed instant so the
#: fixtures are stable; the *presence* and the null-vs-set distinction are preserved,
#: which is exactly what the derived health text depends on.
_TIME_FIELDS = frozenset(
    {
        "created_at",
        "updated_at",
        "last_health_check",
        "circuit_open_until",
        "granted_at",
        "expires_at",
    }
)

#: A value minted per run: either a ``new_id`` ULID or a uuid-suffixed test id. Both are
#: rewritten to a stable alias in first-seen order, so cross-references between fixtures
#: survive while the bytes stay put. Content-derived values (64-hex digests) and domain
#: names (capability ids, connector ids, tool names) match nothing here on purpose.
_MINTED_ID = re.compile(r"^([a-z][a-z0-9]{1,7})_(?:[0-9A-Z]{26}|[0-9a-f]{12})$")

_FIXED_INSTANT = "2026-08-28T00:00:00Z"


class _Aliases:
    """A stable rename for every id minted during one exercise."""

    def __init__(self) -> None:
        self.seen: dict[str, str] = {}
        self.counts: dict[str, int] = {}

    def rename(self, value: str) -> str:
        match = _MINTED_ID.match(value)
        if match is None:
            return value
        if value in self.seen:
            return self.seen[value]
        prefix = match.group(1)
        index = self.counts.get(prefix, 0) + 1
        self.counts[prefix] = index
        alias = f"{prefix}_m6_{index:02d}"
        self.seen[value] = alias
        return alias


def stabilize(payload: Any, aliases: _Aliases) -> Any:
    """Strip only what a second run would legitimately change.

    Content-derived values --- ``manifest_digest``, ``content_sha256``, capability ids,
    states, trust levels, tool patterns --- are deliberately left alone, so the fixtures
    still break when the bundled manifest or a projection changes.
    """

    if isinstance(payload, dict):
        return {
            key: _FIXED_INSTANT
            if key in _TIME_FIELDS and isinstance(value, str)
            else stabilize(value, aliases)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [stabilize(item, aliases) for item in payload]
    if isinstance(payload, str):
        return aliases.rename(payload)
    return payload


def fixture_bytes(payload: Any, aliases: _Aliases) -> bytes:
    return (json.dumps(stabilize(payload, aliases), indent=2, sort_keys=True) + "\n").encode()


def assert_fixture(
    name: str, payload: Any, aliases: _Aliases, *, order_by: str | None = None
) -> Any:
    """Compare one generated payload against its committed file, byte for byte.

    ``order_by`` is used only where the API's own order is not part of its contract.
    Both ``/mcp/servers`` and ``/mcp/servers/{id}/capabilities`` order rows by a minted
    id whose tail is random, so two servers registered in the same millisecond can come
    back either way round. Every other fixture is compared in exactly the order the
    route emitted, so a reordering there is a failure.
    """

    path = FIXTURE_DIR / name
    if order_by is not None:
        assert isinstance(payload, list)
        payload = sorted(payload, key=lambda item: str(item[order_by]))
    generated = fixture_bytes(payload, aliases)
    if REGENERATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(generated)
    assert path.is_file(), (
        f"missing UI fixture {path}; regenerate with ACCRETION_REGENERATE_UI_FIXTURES=1"
    )
    assert path.read_bytes() == generated, (
        f"{path} is stale: the live API no longer produces it. Re-run with "
        "ACCRETION_REGENERATE_UI_FIXTURES=1 and review the diff before committing."
    )
    return json.loads(generated)


class RefusingTransport:
    """The fake research hosts, behind a switch that makes the network refuse.

    Fault injection lives in the transport rather than in the fake's handlers, because
    the failure M3's breaker counts is a *transport* failure: a handler raising would
    reach the client as a protocol-level error and take a different path.
    """

    def __init__(self, router: Any) -> None:
        self.router = router
        self.offline = False
        self.refusals = 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if self.offline:
            self.refusals += 1
            raise httpx2.ConnectError("the fake research host refused the connection")
        await self.router(scope, receive, send)


async def research_dns(host: str, port: int) -> list[str]:
    del port
    if host in {OPENALEX_HOST, CROSSREF_HOST}:
        return ["93.184.216.34"]
    raise OSError(f"unexpected host {host!r}")


class AdminStack:
    """A live admin surface: two installed plugins and two registered MCP servers."""

    def __init__(
        self,
        *,
        store: MemoryStore,
        client: AsyncClient,
        plugins: PluginManager,
        remote_mcp: RemoteMcpManager,
        transport: RefusingTransport,
        workspace_id: str,
        principal_id: str,
        servers: dict[str, str],
    ) -> None:
        self.store = store
        self.client = client
        self.plugins = plugins
        self.remote_mcp = remote_mcp
        self.transport = transport
        self.workspace_id = workspace_id
        self.principal_id = principal_id
        self.servers = servers


@contextlib.asynccontextmanager
async def admin_stack() -> AsyncIterator[AdminStack]:
    """Module-local async builder; this repository has no ``conftest.py``.

    An async context manager because a streamable-HTTP MCP app must have its lifespan
    entered before it will serve, and both research backends really are served here.
    """

    suffix = uuid4().hex[:12]
    workspace_id = f"wks_{suffix}"
    principal_id = f"prin_{suffix}"
    api = FakeResearchApi()
    router, apps = api.transport()
    transport = RefusingTransport(router)

    def client_factory(headers: dict[str, str], timeout: float) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=transport),
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )

    store = MemoryStore()
    await seed_governance(store)
    who = Principal(principal_id=principal_id, issuer="accretion-local", subject=principal_id)
    await store.upsert_principal(who)
    await store.upsert_workspace(WorkspaceEntity(workspace_id=workspace_id, name="M6 admin"))
    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=f"wsm_{suffix}",
            workspace_id=workspace_id,
            principal_id=principal_id,
            role=WorkspaceRole.OWNER,
        )
    )
    await seed_research_connectors(store)

    remote_mcp_manager = RemoteMcpManager(
        store=store,
        client=SdkRemoteMcpClient(http_client_factory=client_factory),
        endpoint_policy=McpEndpointPolicy(resolver=research_dns),
    )
    plugin_manager = PluginManager(
        store=store,
        trust_verifier=PluginTrustVerifier(builtin_ids=(RESEARCH_PLUGIN_ID, SAMPLE_PLUGIN_ID)),
        policy_engine=CapabilityPolicyEngine(set(M6_WORKSPACE_PERMISSIONS)),
        source=DirectoryPluginSource(),
        remote_mcp=remote_mcp_manager,
    )

    app.state.manager = type("M", (), {"store": store})()
    app.state.auth = AuthRuntime(
        mode="LOCAL_PRINCIPAL",
        identity=IdentityService(store),
        cookie_name="accretion_session",
        cookie_secure=False,
        session_ttl_seconds=3600,
        local_principal_cache=who,
    )
    app.state.plugins = plugin_manager
    app.state.remote_mcp = remote_mcp_manager

    async with contextlib.AsyncExitStack() as stack:
        for fake_app in apps:
            await stack.enter_async_context(fake_app.router.lifespan_context(fake_app))
        servers: dict[str, str] = {}
        for reference in (RESEARCH_PLUGIN_ID, SAMPLE_PLUGIN_ID):
            manifest = parse_manifest(await plugin_manager.source.read_manifest(reference))
            installation = await plugin_manager.install(
                reference,
                workspace_id=workspace_id,
                principal_id=principal_id,
                consent_digest=canonical_manifest_digest(manifest),
                consent_capability_ids=[
                    item.capability_id for item in manifest.capabilities
                ],
            )
            for mcp_server_id in installation.registered_mcp_server_ids:
                server = await store.get_mcp_server(mcp_server_id)
                assert server is not None
                # Real M3 discovery, schema validation, and endpoint policy against the
                # fake upstream: a drifted fake fails here rather than passing silently.
                await remote_mcp_manager.refresh_discovery(
                    mcp_server_id, principal_id=principal_id, workspace_id=workspace_id
                )
                await remote_mcp_manager.enable(
                    mcp_server_id, principal_id=principal_id, workspace_id=workspace_id
                )
                servers[server.connector_id] = mcp_server_id
        for backend in BACKENDS:
            # Publish each backend's canonical capability bindings, which is what makes
            # "capabilities by identity" a real projection rather than an empty list.
            await bind_research_backend(
                store,
                connector_id=backend.connector_id,
                mcp_server_id=servers[backend.connector_id],
                enabled=backend.connector_id == OPENALEX_CONNECTOR,
            )
        # Only the research package is left enabled; the sample package is taken
        # through the manager's real DISABLED transition, so the two rows the page
        # renders disagree on state and on capability set.
        await plugin_manager.enable(
            RESEARCH_PLUGIN_ID, workspace_id=workspace_id, principal_id=principal_id
        )
        await plugin_manager.disable(
            SAMPLE_PLUGIN_ID, workspace_id=workspace_id, principal_id=principal_id
        )
        try:
            yield AdminStack(
                store=store,
                client=AsyncClient(transport=ASGITransport(app=app), base_url="http://test"),
                plugins=plugin_manager,
                remote_mcp=remote_mcp_manager,
                transport=transport,
                workspace_id=workspace_id,
                principal_id=principal_id,
                servers=servers,
            )
        finally:
            for name in ("plugins", "remote_mcp", "auth", "manager"):
                if hasattr(app.state, name):
                    delattr(app.state, name)


async def trip_breaker(stack: AdminStack, mcp_server_id: str) -> list[McpServerDefinition]:
    """Refuse the connection until M3's breaker opens, returning the state after each try."""

    stack.transport.offline = True
    observed: list[McpServerDefinition] = []
    server = await stack.store.get_mcp_server(mcp_server_id)
    assert server is not None
    for _ in range(server.health_policy.failure_threshold):
        with pytest.raises(McpServerUnavailable):
            await stack.remote_mcp.refresh_discovery(
                mcp_server_id,
                principal_id=stack.principal_id,
                workspace_id=stack.workspace_id,
                force=True,
            )
        current = await stack.store.get_mcp_server(mcp_server_id)
        assert current is not None
        observed.append(current)
    stack.transport.offline = False
    # Non-vacuous: the refusals really came from the transport, once per attempt plus
    # the manager's configured retries.
    assert stack.transport.refusals >= server.health_policy.failure_threshold
    return observed


@pytest.mark.acceptance("AC3-UI-01")
async def test_the_plugin_detail_route_carries_the_capabilities_and_connectors_it_renders() -> None:
    """AC3-UI-01: the plugins page can show requested capabilities and connector status.

    The route must answer with a detail whose ``requested_capability_ids`` and
    ``connector_resolutions`` are non-empty for a really-installed package; a page
    cannot render a diagnosis the API does not carry.
    """

    async with admin_stack() as stack:
        async with stack.client as client:
            detail = await client.get(
                f"/api/v1/plugins/{RESEARCH_PLUGIN_ID}?workspace_id={stack.workspace_id}"
            )
            assert detail.status_code == 200
            body = detail.json()
            installation = body["installation"]
            assert installation["state"] == "ENABLED"
            assert installation["trust_level"] == "BUILTIN"
            # The installed *version* is the criterion's first noun. Pinned literally
            # and cross-checked against the manifest actually installed, so neither a
            # drifted manifest nor a page reading the registry's version can pass.
            manifest = parse_manifest(
                await stack.plugins.source.read_manifest(RESEARCH_PLUGIN_ID)
            )
            assert installation["version"] == "1.0.0"
            assert installation["version"] == manifest.version
            assert sorted(installation["requested_capability_ids"]) == sorted(
                RESEARCH_CAPABILITY_IDS
            )
            resolutions = installation["connector_resolutions"]
            assert {item["connector_id"] for item in resolutions} >= {
                OPENALEX_CONNECTOR,
                CROSSREF_CONNECTOR,
            }
            assert body["recent_events"], "an installed plugin has a lifecycle history"
            assert {grant["capability_id"] for grant in installation["capability_grants"]} == set(
                RESEARCH_CAPABILITY_IDS
            )


@pytest.mark.acceptance("AC3-UI-03")
async def test_refusing_the_connection_opens_the_circuit_the_mcp_page_reports() -> None:
    """AC3-UI-03: the MCP page's health text has real state behind every branch.

    Driven through the real ``RemoteMcpManager``: the first refusals leave the server
    ``UNREACHABLE`` with no circuit, and crossing the failure threshold sets
    ``circuit_open_until``. Both are read back over the API, because that is what the
    page sees.
    """

    async with admin_stack() as stack:
        crossref = stack.servers[CROSSREF_CONNECTOR]
        observed = await trip_breaker(stack, crossref)
        assert [server.consecutive_failures for server in observed] == [1, 2, 3]
        assert observed[0].state is McpServerState.UNREACHABLE
        assert observed[0].circuit_open_until is None
        assert observed[-1].circuit_open_until is not None

        async with stack.client as client:
            listing = await client.get(f"/api/v1/mcp/servers?workspace_id={stack.workspace_id}")
            assert listing.status_code == 200
            by_connector = {item["connector_id"]: item for item in listing.json()}
            broken = by_connector[CROSSREF_CONNECTOR]
            healthy = by_connector[OPENALEX_CONNECTOR]
            assert broken["circuit_open_until"] is not None
            assert broken["consecutive_failures"] == 3
            # The differential: the untouched server is still serving, so the page has
            # two rows that must not read alike.
            assert healthy["circuit_open_until"] is None
            assert healthy["consecutive_failures"] == 0
            assert healthy["state"] == McpServerState.READY.value
            assert healthy["last_health_check"] is not None
            assert healthy["trust_level"] and healthy["transport"] and healthy["endpoint"]

            # The other half of AC3-UI-03: *discovered capabilities*. Both come from
            # the real discovery run against the fake upstream, so an empty discovery
            # or an unpublished binding fails here rather than rendering a blank panel.
            openalex = stack.servers[OPENALEX_CONNECTOR]
            discovery = await client.get(f"/api/v1/mcp/servers/{openalex}/discovery")
            assert discovery.status_code == 200
            snapshot = discovery.json()
            assert [tool["name"] for tool in snapshot["tools"]], "discovery found no tools"
            assert snapshot["valid"] is True
            assert snapshot["protocol_version"]
            capabilities = await client.get(f"/api/v1/mcp/servers/{openalex}/capabilities")
            assert capabilities.status_code == 200
            assert {item["capability_id"] for item in capabilities.json()} == set(
                RESEARCH_CAPABILITY_IDS
            )


@pytest.mark.acceptance("AC3-UI-01")
@pytest.mark.acceptance("AC3-UI-03")
async def test_the_committed_ui_fixtures_are_what_the_live_admin_api_answers() -> None:
    """Every fixture the plugin and MCP pages are tested against, byte-compared.

    This is the join between the two suites: vitest renders these files, and this test
    is what keeps them honest about the API.
    """

    aliases = _Aliases()
    async with admin_stack() as stack:
        crossref = stack.servers[CROSSREF_CONNECTOR]
        await trip_breaker(stack, crossref)
        async with stack.client as client:
            responses = {
                "plugins.json": await client.get("/api/v1/plugins"),
                "plugin-installations.json": await client.get(
                    f"/api/v1/plugins/installations?workspace_id={stack.workspace_id}"
                ),
                "plugin-detail.json": await client.get(
                    f"/api/v1/plugins/{RESEARCH_PLUGIN_ID}?workspace_id={stack.workspace_id}"
                ),
                "mcp-servers.json": await client.get(
                    f"/api/v1/mcp/servers?workspace_id={stack.workspace_id}"
                ),
                "mcp-capabilities.json": await client.get(
                    f"/api/v1/mcp/servers/{stack.servers[OPENALEX_CONNECTOR]}/capabilities"
                ),
                "mcp-discovery.json": await client.get(
                    f"/api/v1/mcp/servers/{stack.servers[OPENALEX_CONNECTOR]}/discovery"
                ),
            }
    assert all(response.status_code == 200 for response in responses.values()), {
        name: response.status_code for name, response in responses.items()
    }
    written = {
        name: assert_fixture(
            name,
            response.json(),
            aliases,
            order_by={
                "mcp-capabilities.json": "capability_id",
                "mcp-servers.json": "connector_id",
            }.get(name),
        )
        for name, response in responses.items()
    }
    # Non-vacuous: the fixtures the pages differentiate on really do differ.
    installations = {item["plugin_id"]: item for item in written["plugin-installations.json"]}
    assert installations[RESEARCH_PLUGIN_ID]["state"] == "ENABLED"
    assert installations[SAMPLE_PLUGIN_ID]["state"] == "DISABLED"
    assert set(installations[RESEARCH_PLUGIN_ID]["requested_capability_ids"]) != set(
        installations[SAMPLE_PLUGIN_ID]["requested_capability_ids"]
    )
    servers = {item["connector_id"]: item for item in written["mcp-servers.json"]}
    assert servers[CROSSREF_CONNECTOR]["circuit_open_until"] is not None
    assert servers[OPENALEX_CONNECTOR]["circuit_open_until"] is None
    assert written["mcp-discovery.json"]["tools"], "the page renders discovered tools"
    assert written["mcp-capabilities.json"], "the page renders capabilities by identity"


# --- AC3-UI-04: the capability inspector -------------------------------------------
#
# The inspector's whole claim is that a canonical capability id resolves to the
# *particular* backend binding and connection an operator is about to use. A single
# capability cannot prove that: one binding always "matches". So three capabilities
# are resolved through one live API — one bound to an OAuth connector, one to a really
# registered remote MCP server, one to the synthetic connector a really installed
# plugin contributes — and the three answers are required to disagree on backend, on
# binding identity, and on connection identity, with each connection id checked against
# the row read back out of the store.
#
# The two negative halves are what make the panel trustworthy rather than merely
# populated: a principal holding no connection must be told so, not handed someone
# else's, and asking about another principal must be refused outright.

INSPECTOR_ENDPOINT = "https://mcp.inspector.test/mcp"


class DiscoveringMcpClient:
    """A remote MCP server that answers discovery with one schema-valid tool."""

    def __init__(self) -> None:
        self.discover_calls = 0

    async def discover(self, endpoint: str, **kwargs: Any) -> RemoteDiscovery:
        del kwargs
        assert endpoint == INSPECTOR_ENDPOINT
        self.discover_calls += 1
        return RemoteDiscovery(
            protocol_version="2026-07-28",
            server_info={"name": "inspector-remote", "version": "1.0.0"},
            tools=[
                {
                    "name": "probe",
                    "description": "A remote tool the inspector resolves onto",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                }
            ],
            resources=[],
            resource_templates=[],
            prompts=[],
            cache_hints={"tools": (60_000, "private")},
        )

    async def call_tool(
        self, endpoint: str, tool_name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:  # pragma: no cover - the inspector never invokes
        del endpoint, tool_name, arguments, kwargs
        raise AssertionError("the capability inspector must not invoke a capability")


class Inspector:
    """One live inspector surface with three differently-bound capabilities."""

    def __init__(
        self,
        *,
        store: MemoryStore,
        client: AsyncClient,
        principal: Principal,
        stranger: Principal,
        workspace_id: str,
        capability_ids: dict[str, str],
    ) -> None:
        self.store = store
        self.client = client
        self.principal = principal
        self.stranger = stranger
        self.workspace_id = workspace_id
        self.capability_ids = capability_ids


async def setup_inspector() -> Inspector:
    """Build the three bindings out of the real components that own each one.

    Nothing here writes a binding's connection by hand except the OAuth one, which is
    minted by the real ``EncryptedTokenBroker``: the MCP connection is created by
    ``RemoteMcpManager.register`` and the plugin connection by ``PluginManager.install``,
    so "the inspector shows the connection actually in use" is checked against state the
    subsystems produced themselves.
    """

    suffix = uuid4().hex[:12]
    store = MemoryStore()
    await seed_governance(store)
    workspace_id = f"wks_{suffix}"
    who = Principal(
        principal_id=f"prin_{suffix}", issuer="accretion-local", subject=f"alice-{suffix}"
    )
    stranger = Principal(
        principal_id=f"prin_bob{suffix}", issuer="accretion-local", subject=f"bob-{suffix}"
    )
    await store.upsert_principal(who)
    await store.upsert_principal(stranger)
    await store.upsert_workspace(WorkspaceEntity(workspace_id=workspace_id, name="M6 inspect"))
    for index, member in enumerate((who, stranger)):
        await store.upsert_workspace_membership(
            WorkspaceMembership(
                membership_id=f"wsm_{index}{suffix}",
                workspace_id=workspace_id,
                principal_id=member.principal_id,
                role=WorkspaceRole.OWNER if member is who else WorkspaceRole.DEVELOPER,
            )
        )

    # 1. OAuth connector: a user-scoped connection carrying a real brokered token.
    oauth_connector = ConnectorDefinition(
        connector_id=f"conndef_oauth{suffix}",
        name="GitHub (inspector)",
        kind=ConnectorKind.REST,
        auth_type=ConnectorAuthType.OAUTH2,
        authorization_server=ISSUER,
        resource_server="https://api.github.test",
        default_scopes=["repo:status"],
    )
    await store.upsert_connector_definition(oauth_connector)
    broker = EncryptedTokenBroker(store, EnvelopeSecretStore(StaticKey()))
    handle = await broker.store_authorization(
        connector=oauth_connector,
        principal_id=who.principal_id,
        workspace_id=workspace_id,
        response=OAuthTokenResponse(
            access_token=ACCESS,
            refresh_token=REFRESH,
            expires_in=28_800,
            granted_scopes=["repo:status"],
        ),
    )
    await store.upsert_connection(
        Connection(
            connection_id=new_id("conn"),
            connector_id=oauth_connector.connector_id,
            workspace_id=workspace_id,
            principal_id=who.principal_id,
            scope=ConnectionScope.USER,
            status=ConnectionStatus.ACTIVE,
            granted_scopes=["repo:status"],
            token_handle_ref=handle.token_handle_id,
        )
    )
    oauth_capability_id = f"inspector.oauth.{suffix}"
    await store.upsert_capability(
        Capability(
            capability_id=oauth_capability_id,
            version="1.0.0",
            description="Read repository status over HTTP.",
            risk=RiskLevel.MEDIUM,
            backend=CapabilityBackend.HTTP,
        )
    )
    await store.upsert_capability_binding(
        CapabilityBinding(
            binding_id=new_id("capbind"),
            capability_id=oauth_capability_id,
            connector_id=oauth_connector.connector_id,
            backend=CapabilityBindingBackend(
                type=CapabilityBackend.HTTP, method="GET /repos/{owner}/{repo}/status"
            ),
            policy_ref="policy.http.read",
        )
    )

    # 2. A really registered remote MCP server, enabled through M3's own transitions.
    mcp_connector = ConnectorDefinition(
        connector_id=f"conndef_mcp{suffix}",
        name="Inspector remote MCP",
        kind=ConnectorKind.MCP,
        auth_type=ConnectorAuthType.NONE,
        connection_scope=ConnectionScope.WORKSPACE,
    )
    await store.upsert_connector_definition(mcp_connector)
    remote = DiscoveringMcpClient()
    remote_mcp = RemoteMcpManager(
        store=store,
        client=remote,  # type: ignore[arg-type]
        endpoint_policy=McpEndpointPolicy(resolver=public_dns),
    )
    server = await remote_mcp.register(
        McpServerDefinition(
            mcp_server_id=new_id("mcp_server"),
            workspace_id=workspace_id,
            connector_id=mcp_connector.connector_id,
            name="Inspector remote MCP",
            endpoint=INSPECTOR_ENDPOINT,
            owner_principal_id=who.principal_id,
            discovery_policy=McpDiscoveryPolicy(default_ttl_ms=60_000),
        )
    )
    await remote_mcp.refresh_discovery(
        server.mcp_server_id, principal_id=who.principal_id, workspace_id=workspace_id
    )
    enabled_server = await remote_mcp.enable(
        server.mcp_server_id, principal_id=who.principal_id, workspace_id=workspace_id
    )
    assert enabled_server.state is McpServerState.READY
    mcp_capability_id = f"inspector.mcp.{suffix}"
    await store.upsert_capability(
        Capability(
            capability_id=mcp_capability_id,
            version="1.0.0",
            description="Probe a remote MCP tool.",
            risk=RiskLevel.HIGH,
            backend=CapabilityBackend.MCP,
        )
    )
    await store.upsert_capability_binding(
        CapabilityBinding(
            binding_id=new_id("capbind"),
            capability_id=mcp_capability_id,
            connector_id=mcp_connector.connector_id,
            backend=CapabilityBindingBackend(
                type=CapabilityBackend.MCP,
                server_ref=server.mcp_server_id,
                tool_name="probe",
            ),
            policy_ref="policy.mcp.probe",
        )
    )

    # 3. A really installed plugin, resolving through the synthetic local connector the
    #    plugin manager mints for it.
    plugin_manager = PluginManager(
        store=store,
        trust_verifier=PluginTrustVerifier(builtin_ids=(SAMPLE_PLUGIN_ID,)),
        policy_engine=CapabilityPolicyEngine(set(M6_WORKSPACE_PERMISSIONS)),
        source=DirectoryPluginSource(),
    )
    manifest = parse_manifest(await plugin_manager.source.read_manifest(SAMPLE_PLUGIN_ID))
    installation = await plugin_manager.install(
        SAMPLE_PLUGIN_ID,
        workspace_id=workspace_id,
        principal_id=who.principal_id,
        consent_digest=canonical_manifest_digest(manifest),
        consent_capability_ids=[item.capability_id for item in manifest.capabilities],
    )
    installation = await plugin_manager.enable(
        SAMPLE_PLUGIN_ID, workspace_id=workspace_id, principal_id=who.principal_id
    )
    assert installation.state is PluginState.ENABLED
    plugin_capability_id = "accretion.sample.echo"
    assert plugin_capability_id in installation.registered_capability_ids

    app.state.manager = type("M", (), {"store": store})()
    app.state.auth = AuthRuntime(
        mode="LOCAL_PRINCIPAL",
        identity=IdentityService(store),
        cookie_name="accretion_session",
        cookie_secure=False,
        session_ttl_seconds=3600,
        local_principal_cache=who,
    )
    return Inspector(
        store=store,
        client=AsyncClient(transport=ASGITransport(app=app), base_url="http://test"),
        principal=who,
        stranger=stranger,
        workspace_id=workspace_id,
        capability_ids={
            "oauth": oauth_capability_id,
            "mcp": mcp_capability_id,
            "plugin": plugin_capability_id,
        },
    )


async def stored_connection_id(inspector: Inspector, connector_id: str) -> str:
    """The connection the store holds for one connector in the inspector's workspace."""

    candidates = [
        item
        for item in await inspector.store.list_connections(connector_id=connector_id)
        if item.workspace_id == inspector.workspace_id
    ]
    assert len(candidates) == 1, candidates
    return candidates[0].connection_id


@pytest.mark.acceptance("AC3-UI-04")
async def test_the_inspector_resolves_each_capability_onto_its_own_backend_binding() -> None:
    """AC3-UI-04: canonical capability -> the backend connector/MCP binding in use."""

    inspector = await setup_inspector()
    try:
        async with inspector.client as client:
            resolved = {}
            for name, capability_id in inspector.capability_ids.items():
                response = await client.post(
                    "/api/v1/capabilities/resolve",
                    json={
                        "capability_id": capability_id,
                        "workspace_id": inspector.workspace_id,
                    },
                )
                assert response.status_code == 200, (name, response.text)
                resolved[name] = response.json()

        # Every one of the three really resolved; a page cannot diagnose an error the
        # API never reached.
        assert {name: item["outcome"] for name, item in resolved.items()} == {
            "oauth": "OK",
            "mcp": "OK",
            "plugin": "OK",
        }

        # The differential: backend, binding identity and connection identity all differ.
        backends = {name: item["binding"]["backend"]["type"] for name, item in resolved.items()}
        assert backends == {"oauth": "HTTP", "mcp": "MCP", "plugin": "PYTHON"}
        binding_ids = [item["binding"]["binding_id"] for item in resolved.values()]
        assert len(set(binding_ids)) == 3, binding_ids
        connection_ids = [item["connection"]["connection_id"] for item in resolved.values()]
        assert len(set(connection_ids)) == 3, connection_ids

        # Each connection id is the row the owning subsystem really wrote.
        for name in ("oauth", "mcp", "plugin"):
            connector_id = resolved[name]["binding"]["connector_id"]
            assert resolved[name]["connection"]["connector_id"] == connector_id
            assert resolved[name]["connection"]["connection_id"] == await stored_connection_id(
                inspector, connector_id
            ), name

        # The rest of the panel: risk, policy and the MCP server the binding names.
        assert resolved["oauth"]["capability"]["risk"] == "MEDIUM"
        assert resolved["mcp"]["capability"]["risk"] == "HIGH"
        assert resolved["oauth"]["binding"]["policy_ref"] == "policy.http.read"
        assert resolved["mcp"]["binding"]["policy_ref"] == "policy.mcp.probe"
        assert resolved["mcp"]["binding"]["backend"]["tool_name"] == "probe"
        server = await inspector.store.get_mcp_server(
            resolved["mcp"]["binding"]["backend"]["server_ref"]
        )
        assert server is not None and server.state is McpServerState.READY
        assert all(item["reason"] for item in resolved.values())
    finally:
        teardown_surface()


@pytest.mark.acceptance("AC3-UI-04")
async def test_a_principal_without_a_connection_is_told_so_rather_than_shown_another() -> None:
    """The unbound outcome is a diagnosis, never a fallback onto someone else's row."""

    inspector = await setup_inspector()
    owner_connection = await stored_connection_id(
        inspector,
        (
            await inspector.store.list_capability_bindings(
                capability_id=inspector.capability_ids["oauth"], enabled_only=True
            )
        )[0].connector_id,
    )
    # Same live app, now answering as the other member of the same workspace.
    app.state.auth.local_principal_cache = inspector.stranger
    try:
        async with inspector.client as client:
            response = await client.post(
                "/api/v1/capabilities/resolve",
                json={
                    "capability_id": inspector.capability_ids["oauth"],
                    "workspace_id": inspector.workspace_id,
                },
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["outcome"] == "NO_CONNECTION"
        assert body["connection"] is None
        # Not merely absent from the rendered field: absent from the whole answer.
        assert owner_connection not in response.text
        assert inspector.principal.principal_id not in response.text
        # Non-vacuous: the binding itself is still reported, so the operator learns the
        # capability is bound and *they* are not connected.
        assert body["binding"] is not None
        assert body["reason"]
    finally:
        teardown_surface()


@pytest.mark.acceptance("AC3-UI-04")
async def test_resolving_as_another_principal_is_refused_by_the_api() -> None:
    """Naming someone else's principal must be refused, not answered (INV3-008)."""

    inspector = await setup_inspector()
    try:
        async with inspector.client as client:
            response = await client.post(
                "/api/v1/capabilities/resolve",
                json={
                    "capability_id": inspector.capability_ids["oauth"],
                    "principal_id": inspector.stranger.principal_id,
                    "workspace_id": inspector.workspace_id,
                },
            )
        assert response.status_code == 403, response.text
        body = response.json()
        assert body["code"] == "FORBIDDEN"
        assert body["retryable"] is False
        assert body["correlation_id"]
        # No resolution leaked alongside the refusal.
        assert "outcome" not in body
        assert "connection" not in body
    finally:
        teardown_surface()
