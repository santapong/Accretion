"""GET /api/v1/mcp/servers/{id}/discovery — the read-only discovery view (v0.3 M6)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

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
    McpDiscoveryPolicy,
    McpServerDefinition,
    McpToolMapping,
    Principal,
    TokenHandle,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.identity import IdentityService
from accretion.ids import new_id
from accretion.mcp.endpoint_policy import McpEndpointPolicy
from accretion.mcp.manager import RemoteMcpManager
from accretion.mcp.remote_client import RemoteDiscovery
from accretion.persistence.store import MemoryStore
from accretion.token_broker import EphemeralCredential

ENDPOINT = "https://mcp.example.test/mcp"
ISSUER = "https://auth.example.test"


async def public_dns(host: str, port: int) -> list[str]:
    del host, port
    return ["93.184.216.34"]


class StaticTokenBroker:
    async def get_access_material(
        self, handle: TokenHandle, **kwargs: Any
    ) -> EphemeralCredential:
        del kwargs
        return EphemeralCredential(handle.token_handle_id, "remote-secret-token")


class FakeRemoteMcpClient:
    """Discovery-only remote client with a per-call tool list and a failure switch."""

    def __init__(self) -> None:
        self.discover_calls = 0
        self.fail_discovery = False
        self.tools: list[dict[str, Any]] = [
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
        ]

    async def discover(self, endpoint: str, **kwargs: Any) -> RemoteDiscovery:
        del kwargs
        assert endpoint == ENDPOINT
        self.discover_calls += 1
        if self.fail_discovery:
            raise AssertionError("the discovery route must not contact the server")
        hints = {
            kind: (60_000, "public")
            for kind in ("tools", "resources", "resource_templates", "prompts")
        }
        return RemoteDiscovery(
            protocol_version="2026-07-28",
            server_info={"name": "fake-remote", "version": "1.0.0"},
            tools=[dict(item) for item in self.tools],
            resources=[],
            resource_templates=[],
            prompts=[{"name": "summarize", "description": "Summarize text"}],
            cache_hints=hints,
        )

    async def call_tool(
        self, endpoint: str, tool_name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        del endpoint, kwargs
        return {"content": [], "structuredContent": arguments, "isError": False}


async def setup_discovery_api() -> tuple[
    MemoryStore, RemoteMcpManager, FakeRemoteMcpClient, McpServerDefinition, str, str
]:
    """Build a store with one member, one non-member and one registered MCP server."""

    suffix = uuid4().hex[:8]
    workspace_id = f"wks_m6_{suffix}"
    member_id = f"usr_member_{suffix}"
    outsider_id = f"usr_outsider_{suffix}"
    connector_id = f"conndef_mcp_{suffix}"
    store = MemoryStore()
    remote = FakeRemoteMcpClient()

    for principal_id in (member_id, outsider_id):
        await store.upsert_principal(
            Principal(principal_id=principal_id, issuer="test", subject=principal_id)
        )
    await store.upsert_workspace(WorkspaceEntity(workspace_id=workspace_id, name="M6"))
    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=workspace_id,
            principal_id=member_id,
            role=WorkspaceRole.OWNER,
        )
    )
    await store.upsert_connector_definition(
        ConnectorDefinition(
            connector_id=connector_id,
            name="Authenticated remote MCP",
            kind=ConnectorKind.MCP,
            auth_type=ConnectorAuthType.OAUTH2,
            authorization_server=ISSUER,
            resource_server="https://mcp.example.test",
            default_scopes=["mcp:invoke"],
        )
    )
    handle = TokenHandle(
        token_handle_id=new_id("token_handle"),
        connector_id=connector_id,
        principal_id=member_id,
        workspace_id=workspace_id,
        issuer=ISSUER,
        scopes=["mcp:invoke"],
        audience=["https://mcp.example.test"],
        secret_store_key=f"secret-{member_id}",
    )
    await store.upsert_token_handle(handle)
    await store.upsert_connection(
        Connection(
            connection_id=new_id("conn"),
            connector_id=connector_id,
            workspace_id=workspace_id,
            principal_id=member_id,
            scope=ConnectionScope.USER,
            token_handle_ref=handle.token_handle_id,
            granted_scopes=["mcp:invoke"],
            status=ConnectionStatus.ACTIVE,
        )
    )
    manager = RemoteMcpManager(
        store=store,
        client=remote,
        endpoint_policy=McpEndpointPolicy(resolver=public_dns),
        token_broker=StaticTokenBroker(),  # type: ignore[arg-type]
    )
    server = await manager.register(
        McpServerDefinition(
            mcp_server_id=new_id("mcp_server"),
            workspace_id=workspace_id,
            connector_id=connector_id,
            name="Fake remote",
            endpoint=ENDPOINT,
            owner_principal_id=member_id,
            discovery_policy=McpDiscoveryPolicy(default_ttl_ms=60_000),
            tool_mappings=[McpToolMapping(capability_id="remote.echo", tool_name="echo")],
        )
    )
    return store, manager, remote, server, member_id, outsider_id


def _install_app_state(store: MemoryStore, manager: RemoteMcpManager, who: Principal) -> None:
    app.state.manager = type("Manager", (), {"store": store})()
    app.state.remote_mcp = manager
    app.state.auth = AuthRuntime(
        mode="LOCAL_PRINCIPAL",
        identity=IdentityService(store),
        cookie_name="session",
        cookie_secure=False,
        session_ttl_seconds=3600,
        local_principal_cache=who,
    )


def _clear_app_state() -> None:
    for attribute in ("auth", "remote_mcp", "manager"):
        if hasattr(app.state, attribute):
            delattr(app.state, attribute)


async def _get_discovery(mcp_server_id: str) -> Any:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    async with client:
        return await client.get(f"/api/v1/mcp/servers/{mcp_server_id}/discovery")


async def test_a_workspace_member_reads_the_latest_stored_discovery_snapshot() -> None:
    store, manager, remote, server, member_id, _ = await setup_discovery_api()
    await manager.refresh_discovery(
        server.mcp_server_id,
        principal_id=member_id,
        workspace_id=server.workspace_id,
    )
    remote.tools = [
        {
            "name": "echo",
            "description": "Echo a message",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "search",
            "description": "Search the corpus",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]
    await manager.refresh_discovery(
        server.mcp_server_id,
        principal_id=member_id,
        workspace_id=server.workspace_id,
        force=True,
    )
    stored = await store.list_mcp_discovery_snapshots(server.mcp_server_id)
    remote.fail_discovery = True
    who = await store.get_principal(member_id)
    assert who is not None
    _install_app_state(store, manager, who)
    try:
        response = await _get_discovery(server.mcp_server_id)
    finally:
        _clear_app_state()

    assert response.status_code == 200
    body = response.json()
    assert len(stored) == 2
    assert body["discovery_snapshot_id"] == stored[0].discovery_snapshot_id
    assert [tool["name"] for tool in body["tools"]] == ["echo", "search"]
    assert body["prompts"][0]["name"] == "summarize"
    assert body["cache_hints"]["tools"]["ttl_ms"] == 60_000


async def test_a_non_member_is_forbidden_from_reading_a_discovery_snapshot() -> None:
    store, manager, _, server, member_id, outsider_id = await setup_discovery_api()
    await manager.refresh_discovery(
        server.mcp_server_id,
        principal_id=member_id,
        workspace_id=server.workspace_id,
    )
    outsider = await store.get_principal(outsider_id)
    assert outsider is not None
    _install_app_state(store, manager, outsider)
    try:
        response = await _get_discovery(server.mcp_server_id)
    finally:
        _clear_app_state()

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_a_server_without_any_snapshot_is_not_found() -> None:
    store, manager, _, server, member_id, _ = await setup_discovery_api()
    who = await store.get_principal(member_id)
    assert who is not None
    _install_app_state(store, manager, who)
    try:
        response = await _get_discovery(server.mcp_server_id)
    finally:
        _clear_app_state()

    assert response.status_code == 404
    assert await store.list_mcp_discovery_snapshots(server.mcp_server_id) == []
