from __future__ import annotations

from typing import Any

import httpx2
import pytest
from httpx import ASGITransport, AsyncClient
from mcp.server import CacheHint, MCPServer

from accretion.api.auth import AuthRuntime
from accretion.api.main import app
from accretion.contracts import (
    CapabilityExecutionStatus,
    CapabilityRequest,
    CapabilityResolutionOutcome,
    Connection,
    ConnectionScope,
    ConnectionStatus,
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
    McpCacheScope,
    McpDiscoveryPolicy,
    McpHealthPolicy,
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
    TokenHandle,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.governance import (
    CapabilityExecutor,
    CapabilityGateway,
    CapabilityPolicyEngine,
    CredentialBroker,
    seed_governance,
)
from accretion.identity import IdentityService
from accretion.ids import new_id
from accretion.mcp.endpoint_policy import McpEndpointPolicy, McpEndpointPolicyError
from accretion.mcp.manager import McpManagerError, McpServerAuthRequired, RemoteMcpManager
from accretion.mcp.remote_client import (
    RemoteDiscovery,
    RemoteMcpAuthError,
    RemoteMcpTransportError,
    SdkRemoteMcpClient,
)
from accretion.persistence.side_effects import MemorySideEffectLedger
from accretion.persistence.store import MemoryStore
from accretion.resolver import CapabilityResolver
from accretion.token_broker import EphemeralCredential

PRINCIPAL = "usr_alice"
WORKSPACE = "wks_test"
CONNECTOR = "conndef_remote_mcp"
ENDPOINT = "https://mcp.example.test/mcp"
ISSUER = "https://auth.example.test"


async def public_dns(host: str, port: int) -> list[str]:
    del host, port
    return ["93.184.216.34"]


class StaticTokenBroker:
    async def get_access_material(self, handle: TokenHandle, **kwargs: Any) -> EphemeralCredential:
        del kwargs
        return EphemeralCredential(handle.token_handle_id, "remote-secret-token")


class FakeRemoteMcpClient:
    def __init__(
        self,
        *,
        cache_scope: str = "private",
        cache_scopes: dict[str, str] | None = None,
        ttl_ms: int = 60_000,
    ) -> None:
        self.cache_scope = cache_scope
        self.cache_scopes = cache_scopes or {}
        self.ttl_ms = ttl_ms
        self.discover_calls = 0
        self.discovery_requests: list[tuple[bool, bool, bool]] = []
        self.tool_calls = 0
        self.authorization_headers: list[str | None] = []
        self.fail_call_auth = False
        self.fail_call_transport = False
        self.tools: list[dict[str, Any]] = [
            {
                "name": "echo",
                "description": "Echo a message from a remote MCP server",
                "inputSchema": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                    "additionalProperties": False,
                },
            }
        ]

    async def discover(self, endpoint: str, **kwargs: Any) -> RemoteDiscovery:
        assert endpoint == ENDPOINT
        self.discover_calls += 1
        self.discovery_requests.append(
            (
                bool(kwargs["include_tools"]),
                bool(kwargs["include_resources"]),
                bool(kwargs["include_prompts"]),
            )
        )
        self.authorization_headers.append(kwargs["authorization_header"])
        hints = {
            kind: (self.ttl_ms, self.cache_scopes.get(kind, self.cache_scope))
            for kind in ("tools", "resources", "resource_templates", "prompts")
        }
        return RemoteDiscovery(
            protocol_version="2026-07-28",
            server_info={"name": "fake-remote", "version": "1.0.0"},
            tools=list(self.tools),
            resources=[{"uri": "https://mcp.example.test/readme", "name": "readme"}],
            resource_templates=[],
            prompts=[{"name": "summarize", "description": "Summarize text"}],
            cache_hints=hints,
        )

    async def call_tool(
        self,
        endpoint: str,
        tool_name: str,
        arguments: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        assert endpoint == ENDPOINT
        assert tool_name == "echo"
        self.tool_calls += 1
        self.authorization_headers.append(kwargs["authorization_header"])
        if self.fail_call_auth:
            raise RemoteMcpAuthError("rejected")
        if self.fail_call_transport:
            raise RemoteMcpTransportError("unreachable")
        return {
            "content": [{"type": "text", "text": str(arguments["message"])}],
            "structuredContent": {"message": arguments["message"]},
            "isError": False,
        }


async def setup_manager(
    *,
    client: FakeRemoteMcpClient | None = None,
    second_principal: bool = False,
) -> tuple[MemoryStore, RemoteMcpManager, FakeRemoteMcpClient, McpServerDefinition]:
    store = MemoryStore()
    remote = client or FakeRemoteMcpClient()
    broker = StaticTokenBroker()
    await store.upsert_connector_definition(
        ConnectorDefinition(
            connector_id=CONNECTOR,
            name="Authenticated remote MCP",
            kind=ConnectorKind.MCP,
            auth_type=ConnectorAuthType.OAUTH2,
            authorization_server=ISSUER,
            resource_server="https://mcp.example.test",
            default_scopes=["mcp:invoke"],
        )
    )
    for principal in ([PRINCIPAL, "usr_bob"] if second_principal else [PRINCIPAL]):
        handle = TokenHandle(
            token_handle_id=new_id("token_handle"),
            connector_id=CONNECTOR,
            principal_id=principal,
            workspace_id=WORKSPACE,
            issuer=ISSUER,
            scopes=["mcp:invoke"],
            audience=["https://mcp.example.test"],
            secret_store_key=f"secret-{principal}",
        )
        await store.upsert_token_handle(handle)
        await store.upsert_connection(
            Connection(
                connection_id=new_id("conn"),
                connector_id=CONNECTOR,
                workspace_id=WORKSPACE,
                principal_id=principal,
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
        token_broker=broker,  # type: ignore[arg-type]
    )
    server = await manager.register(
        McpServerDefinition(
            mcp_server_id=new_id("mcp_server"),
            workspace_id=WORKSPACE,
            connector_id=CONNECTOR,
            name="Fake remote",
            endpoint=ENDPOINT,
            owner_principal_id=PRINCIPAL,
            discovery_policy=McpDiscoveryPolicy(default_ttl_ms=60_000),
            tool_mappings=[
                McpToolMapping(capability_id="remote.echo", tool_name="echo")
            ],
        )
    )
    return store, manager, remote, server


@pytest.mark.acceptance("AC3-MCP-02")
async def test_authenticated_remote_server_is_registered_discovered_and_invoked() -> None:
    store, manager, remote, server = await setup_manager()
    snapshot = await manager.refresh_discovery(
        server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    enabled = await manager.enable(
        server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    resolved = await CapabilityResolver(store).resolve(
        "remote.echo", principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )

    assert snapshot.protocol_version == "2026-07-28"
    assert enabled.state is McpServerState.READY
    assert resolved is not None and resolved.outcome is CapabilityResolutionOutcome.OK
    assert resolved.binding is not None and resolved.connection is not None
    output = await manager.execute(
        resolved.binding,
        resolved.connection,
        {"message": "hello"},
        {f"connection:{CONNECTOR}": "remote-secret-token"},
    )
    assert output["structuredContent"] == {"message": "hello"}
    assert remote.authorization_headers == [
        "Bearer remote-secret-token",
        "Bearer remote-secret-token",
    ]


@pytest.mark.acceptance("AC3-MCP-03")
async def test_invalid_discovered_tool_schema_is_never_published() -> None:
    remote = FakeRemoteMcpClient()
    remote.tools[0]["inputSchema"] = {"type": "string"}
    store, manager, _, server = await setup_manager(client=remote)

    snapshot = await manager.refresh_discovery(
        server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )

    assert not snapshot.valid
    assert snapshot.tools == []
    assert "root type" in snapshot.schema_errors[0]
    with pytest.raises(McpManagerError, match="invalid tool schemas"):
        await manager.enable(
            server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
        )
    assert await store.get_capability("remote.echo") is None


@pytest.mark.acceptance("AC3-MCP-04")
async def test_discovery_cache_honors_ttl_and_public_private_partition_hints() -> None:
    public_client = FakeRemoteMcpClient(cache_scope="public")
    _, manager, _, server = await setup_manager(
        client=public_client, second_principal=True
    )
    first = await manager.refresh_discovery(
        server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    shared = await manager.refresh_discovery(
        server.mcp_server_id, principal_id="usr_bob", workspace_id=WORKSPACE
    )
    assert shared.discovery_snapshot_id == first.discovery_snapshot_id
    assert public_client.discover_calls == 1
    assert first.cache_hints["tools"].scope is McpCacheScope.PUBLIC

    private_client = FakeRemoteMcpClient(cache_scope="private")
    _, private_manager, _, private_server = await setup_manager(
        client=private_client, second_principal=True
    )
    await private_manager.refresh_discovery(
        private_server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    await private_manager.refresh_discovery(
        private_server.mcp_server_id,
        principal_id="usr_bob",
        workspace_id=WORKSPACE,
    )
    assert private_client.discover_calls == 2

    mixed_client = FakeRemoteMcpClient(
        cache_scopes={
            "tools": "public",
            "resources": "public",
            "resource_templates": "public",
            "prompts": "private",
        }
    )
    _, mixed_manager, _, mixed_server = await setup_manager(
        client=mixed_client, second_principal=True
    )
    await mixed_manager.refresh_discovery(
        mixed_server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    await mixed_manager.refresh_discovery(
        mixed_server.mcp_server_id, principal_id="usr_bob", workspace_id=WORKSPACE
    )
    await mixed_manager.refresh_discovery(
        mixed_server.mcp_server_id, principal_id="usr_bob", workspace_id=WORKSPACE
    )
    assert mixed_client.discovery_requests == [
        (True, True, True),
        (False, False, True),
    ]

    zero_ttl = FakeRemoteMcpClient(cache_scope="public", ttl_ms=0)
    _, zero_manager, _, zero_server = await setup_manager(client=zero_ttl)
    await zero_manager.refresh_discovery(
        zero_server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    await zero_manager.refresh_discovery(
        zero_server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    assert zero_ttl.discover_calls == 2


@pytest.mark.acceptance("AC3-MCP-05")
async def test_disabling_server_immediately_removes_it_from_resolution() -> None:
    store, manager, _, server = await setup_manager()
    await manager.refresh_discovery(
        server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    await manager.enable(
        server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    await manager.disable(
        server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )

    resolved = await CapabilityResolver(store).resolve(
        "remote.echo", principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    assert resolved is not None
    assert resolved.outcome is CapabilityResolutionOutcome.DISABLED

    await manager.enable(
        server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    restored = await CapabilityResolver(store).resolve(
        "remote.echo", principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    assert restored is not None
    assert restored.outcome is CapabilityResolutionOutcome.OK


@pytest.mark.acceptance("AC3-MCP-06")
async def test_remote_tool_call_cannot_bypass_capability_policy() -> None:
    store, manager, remote, server = await setup_manager()
    await manager.refresh_discovery(
        server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    await manager.enable(
        server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    project = Project(project_id=new_id("project"), name="M3", repository_path=".")
    await store.create_project(project)
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective="Prove policy before remote execution",
            allowed_capabilities=["remote.echo"],
            denied_capabilities=["remote.echo"],
        )
    )
    await store.create_task(task)
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
    )
    await store.create_run(run)
    await seed_governance(store)
    resolved = await CapabilityResolver(store).resolve(
        "remote.echo", principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    assert resolved is not None and resolved.binding is not None
    gateway = CapabilityGateway(
        store=store,
        side_effects=MemorySideEffectLedger(),
        broker=CredentialBroker(),
        executor=CapabilityExecutor(),
        policy_engine=CapabilityPolicyEngine(),
        token_broker=StaticTokenBroker(),  # type: ignore[arg-type]
        remote_mcp=manager,
    )
    result = await gateway.execute(
        CapabilityRequest(
            request_id=new_id("capability_request"),
            run_id=run.run_id,
            node_id="act",
            capability_id="remote.echo",
            capability_version="1.0.0",
            arguments={"message": "blocked"},
            declared_reason="acceptance test",
        ),
        resolved.connection,
        resolved.binding,
    )
    assert result.status is CapabilityExecutionStatus.DENIED
    assert remote.tool_calls == 0


@pytest.mark.acceptance("AC3-MCP-07")
async def test_ssrf_prohibited_endpoint_registration_fails() -> None:
    async def private_dns(host: str, port: int) -> list[str]:
        del host, port
        return ["169.254.169.254"]

    store = MemoryStore()
    await store.upsert_connector_definition(
        ConnectorDefinition(
            connector_id=CONNECTOR,
            name="blocked",
            kind=ConnectorKind.MCP,
            auth_type=ConnectorAuthType.NONE,
        )
    )
    manager = RemoteMcpManager(
        store=store,
        client=FakeRemoteMcpClient(),
        endpoint_policy=McpEndpointPolicy(resolver=private_dns),
    )
    with pytest.raises(McpEndpointPolicyError, match="non-public"):
        await manager.register(
            McpServerDefinition(
                mcp_server_id=new_id("mcp_server"),
                workspace_id=WORKSPACE,
                connector_id=CONNECTOR,
                name="metadata service",
                endpoint="https://metadata.invalid/mcp",
                owner_principal_id=PRINCIPAL,
            )
        )
    assert await store.list_mcp_servers() == []


@pytest.mark.acceptance("AC3-MCP-08")
async def test_auth_failure_sets_observable_server_and_connection_reauth_states() -> None:
    store, manager, remote, server = await setup_manager()
    await manager.refresh_discovery(
        server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    await manager.enable(
        server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    resolved = await CapabilityResolver(store).resolve(
        "remote.echo", principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    assert resolved is not None and resolved.binding and resolved.connection
    remote.fail_call_auth = True

    with pytest.raises(McpServerAuthRequired):
        await manager.execute(
            resolved.binding,
            resolved.connection,
            {"message": "expired"},
            {f"connection:{CONNECTOR}": "expired-token"},
        )

    stored_server = await store.get_mcp_server(server.mcp_server_id)
    stored_connection = await store.get_connection(resolved.connection.connection_id)
    assert stored_server is not None
    assert stored_server.state is McpServerState.AUTH_REQUIRED
    assert stored_connection is not None
    assert stored_connection.status is ConnectionStatus.REAUTH_REQUIRED
    events = await store.list_mcp_server_events(server.mcp_server_id)
    assert events[-1].event_type == "AUTH_REQUIRED"


async def test_transport_failure_opens_circuit_without_retrying_a_tool_call() -> None:
    store, manager, remote, server = await setup_manager()
    await manager.refresh_discovery(
        server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    await manager.enable(
        server.mcp_server_id, principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    current = await store.get_mcp_server(server.mcp_server_id)
    assert current is not None
    await store.upsert_mcp_server(
        current.model_copy(
            update={
                "health_policy": McpHealthPolicy(
                    failure_threshold=1, cooldown_seconds=60
                )
            }
        )
    )
    resolved = await CapabilityResolver(store).resolve(
        "remote.echo", principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    assert resolved is not None and resolved.binding and resolved.connection
    remote.fail_call_transport = True

    with pytest.raises(McpManagerError, match="tool call failed"):
        await manager.execute(
            resolved.binding,
            resolved.connection,
            {"message": "once"},
            {f"connection:{CONNECTOR}": "remote-secret-token"},
        )

    assert remote.tool_calls == 1
    stored = await store.get_mcp_server(server.mcp_server_id)
    assert stored is not None and stored.state is McpServerState.DEGRADED
    blocked = await CapabilityResolver(store).resolve(
        "remote.echo", principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    assert blocked is not None
    assert blocked.outcome is CapabilityResolutionOutcome.DISABLED


async def test_admin_api_exposes_remote_mcp_lifecycle() -> None:
    store, manager, _, _ = await setup_manager()
    who = Principal(principal_id=PRINCIPAL, issuer="test", subject=PRINCIPAL)
    await store.upsert_principal(who)
    await store.upsert_workspace(WorkspaceEntity(workspace_id=WORKSPACE, name="M3"))
    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=WORKSPACE,
            principal_id=PRINCIPAL,
            role=WorkspaceRole.OWNER,
        )
    )
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
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        async with client:
            created = await client.post(
                "/api/v1/mcp/servers",
                json={
                    "workspace_id": WORKSPACE,
                    "connector_id": CONNECTOR,
                    "name": "API remote",
                    "endpoint": ENDPOINT,
                    "tool_mappings": [
                        {"capability_id": "remote.api-echo", "tool_name": "echo"}
                    ],
                },
            )
            assert created.status_code == 201
            server_id = created.json()["mcp_server_id"]
            assert created.json()["state"] == "DISABLED"

            refreshed = await client.post(
                f"/api/v1/mcp/servers/{server_id}/refresh-discovery"
            )
            enabled = await client.post(f"/api/v1/mcp/servers/{server_id}/enable")
            capabilities = await client.get(
                f"/api/v1/mcp/servers/{server_id}/capabilities"
            )
            disabled = await client.post(f"/api/v1/mcp/servers/{server_id}/disable")

        assert refreshed.status_code == 200
        assert enabled.json()["state"] == "READY"
        assert [item["capability_id"] for item in capabilities.json()] == [
            "remote.api-echo"
        ]
        assert disabled.json()["state"] == "DISABLED"
    finally:
        for attribute in ("auth", "remote_mcp", "manager"):
            if hasattr(app.state, attribute):
                delattr(app.state, attribute)


@pytest.mark.acceptance("AC3-MCP-02")
async def test_sdk_v2_adapter_negotiates_modern_http_and_preserves_auth_and_hints() -> None:
    sdk_server = MCPServer(
        "sdk-v2-fixture",
        version="1.0.0",
        cache_hints={
            "tools/list": CacheHint(ttl_ms=12_345, scope="private"),
            "resources/list": CacheHint(ttl_ms=23_456, scope="public"),
            "resources/templates/list": CacheHint(ttl_ms=23_456, scope="public"),
            "prompts/list": CacheHint(ttl_ms=34_567, scope="private"),
        },
    )

    @sdk_server.tool(name="echo", structured_output=True)
    async def echo(message: str) -> dict[str, str]:
        return {"message": message}

    http_app = sdk_server.streamable_http_app(
        stateless_http=True, json_response=True, host="mcp.example.test"
    )
    seen_headers: list[dict[str, str]] = []

    def client_factory(headers: dict[str, str], timeout: float) -> httpx2.AsyncClient:
        seen_headers.append(headers)
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=http_app),
            base_url="https://mcp.example.test",
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )

    client = SdkRemoteMcpClient(http_client_factory=client_factory)
    async with http_app.router.lifespan_context(http_app):
        discovery = await client.discover(
            ENDPOINT,
            authorization_header="Bearer sdk-token",
            timeout_seconds=2,
            max_items_per_kind=10,
            include_tools=True,
            include_resources=True,
            include_prompts=True,
        )
        result = await client.call_tool(
            ENDPOINT,
            "echo",
            {"message": "sdk-ready"},
            authorization_header="Bearer sdk-token",
            timeout_seconds=2,
        )

    assert discovery.protocol_version == "2026-07-28"
    assert discovery.tools[0]["name"] == "echo"
    assert discovery.cache_hints["tools"] == (12_345, "private")
    assert discovery.cache_hints["resources"] == (23_456, "public")
    assert result["structuredContent"] == {"message": "sdk-ready"}
    assert seen_headers == [
        {"Authorization": "Bearer sdk-token"},
        {"Authorization": "Bearer sdk-token"},
    ]
