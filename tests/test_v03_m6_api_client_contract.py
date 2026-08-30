"""The M6 browser client and the generated schema must track the live FastAPI app.

`apps/ui/src/api.ts` builds request paths from template literals, so a typo in one
is invisible to `tsc` and to the generated `schema.d.ts`: both would still compile.
These tests execute the real app and compare, per client function, the path it calls
and the response type it declares against the route the app actually serves.
"""

from __future__ import annotations

import re
from pathlib import Path
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

REPO_ROOT = Path(__file__).resolve().parents[1]
API_TS = REPO_ROOT / "apps" / "ui" / "src" / "api.ts"
ENDPOINT = "https://mcp.example.test/mcp"
ISSUER = "https://auth.example.test"

# name -> (HTTP method, response type written in api.ts)
M6_CLIENT_FUNCTIONS: dict[str, tuple[str, str]] = {
    "me": ("get", "MeResponse"),
    "workspaces": ("get", "WorkspaceEntity[]"),
    "authProviders": ("get", "AuthProviderInfo[]"),
    "plugins": ("get", "MetaPlugin[]"),
    "pluginDetail": ("get", "PluginDetail"),
    "pluginInstallations": ("get", "PluginInstallation[]"),
    "pluginAudit": ("get", "PluginAuditEvent[]"),
    "connectors": ("get", "ConnectorDefinition[]"),
    "connections": ("get", "ConnectionSummary[]"),
    "connect": ("post", "AuthorizationStart"),
    "reauthorize": ("post", "AuthorizationStart"),
    "revoke": ("post", "ConnectionSummary"),
    "connectionHealth": ("get", "Record<string, unknown>"),
    "mcpServers": ("get", "McpServerDefinition[]"),
    "mcpServerCapabilities": ("get", "Capability[]"),
    "mcpServerDiscovery": ("get", "McpDiscoverySnapshot"),
    "resolveCapability": ("post", "ResolvedCapability"),
}

_ENTRY = re.compile(
    r"^  (?P<name>[A-Za-z0-9_]+):.*?"
    r"(?P<verb>getJson|postJson|patchJson)<(?P<type>.+?)>\(\s*",
    re.DOTALL | re.MULTILINE,
)


def _read_literal(source: str, start: int) -> str:
    """Read one string or template literal, tolerating nested `${ ... `...` ... }`."""

    quote = source[start]
    assert quote in "`\"'", f"expected a string literal at offset {start}"
    out: list[str] = []
    index = start + 1
    depth = 0
    while index < len(source):
        char = source[index]
        if char == "\\":
            out.append(source[index : index + 2])
            index += 2
            continue
        if char == quote and depth == 0:
            return "".join(out)
        if quote == "`" and source.startswith("${", index):
            depth += 1
        elif quote == "`" and char == "}" and depth:
            depth -= 1
        out.append(char)
        index += 1
    raise AssertionError("unterminated literal in api.ts")


def _parse_api_ts() -> dict[str, tuple[str, str, str]]:
    """name -> (method, path template, response type), read from the real client."""

    source = API_TS.read_text(encoding="utf-8")
    body = source.split("export const api = {", 1)[1]
    verbs = {"getJson": "get", "postJson": "post", "patchJson": "patch"}
    entries: dict[str, tuple[str, str, str]] = {}
    for match in _ENTRY.finditer(body):
        entries.setdefault(
            match.group("name"),
            (
                verbs[match.group("verb")],
                _read_literal(body, match.end()),
                match.group("type").strip(),
            ),
        )
    return entries


def _substitutions(path: str) -> list[str]:
    """Every `${ ... }` in a template literal, with nesting respected."""

    found: list[str] = []
    index = 0
    while True:
        start = path.find("${", index)
        if start < 0:
            return found
        depth = 0
        cursor = start
        while cursor < len(path):
            if path.startswith("${", cursor):
                depth += 1
                cursor += 2
                continue
            if path[cursor] == "}":
                depth -= 1
                if not depth:
                    break
            cursor += 1
        found.append(path[start : cursor + 1])
        index = cursor + 1


def _normalise(path: str) -> str:
    """Erase path parameters and query strings so route shapes can be compared."""

    for substitution in _substitutions(path):
        # A substitution that builds `?name=value` is an optional query string,
        # not a path segment, so it contributes nothing to the route shape.
        replacement = "" if re.search(r"\?[a-z_]+=", substitution) else "*"
        path = path.replace(substitution, replacement)
    path = path.split("?", 1)[0]
    return re.sub(r"\{[^}]*\}", "*", path)


def _expected_schema(response_type: str) -> dict[str, Any]:
    if response_type == "Record<string, unknown>":
        return {"type": "object"}
    if response_type.endswith("[]"):
        return {"type": "array", "item": response_type[:-2]}
    return {"type": "object", "item": response_type}


def _actual_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if schema.get("type") == "array":
        return {"type": "array", "item": schema["items"]["$ref"].rsplit("/", 1)[-1]}
    if "$ref" in schema:
        return {"type": "object", "item": schema["$ref"].rsplit("/", 1)[-1]}
    return {"type": "object"}


def test_every_m6_client_function_calls_a_route_the_app_actually_serves() -> None:
    spec = app.openapi()
    routes = {
        (_normalise(path), method): operation
        for path, item in spec["paths"].items()
        for method, operation in item.items()
    }
    entries = _parse_api_ts()

    missing = sorted(set(M6_CLIENT_FUNCTIONS) - set(entries))
    assert missing == [], f"api.ts is missing M6 client functions: {missing}"

    mismatches: list[str] = []
    for name, (expected_method, expected_type) in M6_CLIENT_FUNCTIONS.items():
        method, path, response_type = entries[name]
        if method != expected_method or response_type != expected_type:
            mismatches.append(f"{name}: client declares {method} <{response_type}>")
            continue
        key = (_normalise(path), method)
        if key not in routes:
            mismatches.append(f"{name}: {method.upper()} {path} is not served by the app")
            continue
        served = _actual_schema(
            routes[key]["responses"]["200"]["content"]["application/json"]["schema"]
        )
        if served != _expected_schema(response_type):
            mismatches.append(
                f"{name}: client expects {response_type}, route returns {served}"
            )
    assert mismatches == []


def test_the_live_application_serves_the_discovery_route_returning_a_snapshot() -> None:
    """Read the app itself, never a build product.

    `openapi.json` is a generated, gitignored artifact (it feeds `npm run api:generate`),
    so asserting `committed == live` would either be tautological or fail on a fresh
    checkout where the file has never been generated. The criterion that matters is
    that the app really serves the route and really declares it returns the
    `McpDiscoverySnapshot` contract, which is exactly what is asserted here.
    """

    live = app.openapi()
    path = "/api/v1/mcp/servers/{mcp_server_id}/discovery"
    assert path in live["paths"], "the discovery route is not registered on the app"
    operation = live["paths"][path]["get"]
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert _actual_schema(schema) == {"type": "object", "item": "McpDiscoverySnapshot"}
    assert "McpDiscoverySnapshot" in live["components"]["schemas"]


def test_the_generated_typescript_schema_declares_the_discovery_operation() -> None:
    schema = (REPO_ROOT / "apps" / "ui" / "src" / "api" / "schema.d.ts").read_text(
        encoding="utf-8"
    )
    assert '"/api/v1/mcp/servers/{mcp_server_id}/discovery"' in schema
    assert "get_mcp_server_discovery" in schema


# --- authorization is the membership check, not an accident --------------------


async def _public_dns(host: str, port: int) -> list[str]:
    del host, port
    return ["93.184.216.34"]


class StaticTokenBroker:
    async def get_access_material(
        self, handle: TokenHandle, **kwargs: Any
    ) -> EphemeralCredential:
        del kwargs
        return EphemeralCredential(handle.token_handle_id, "remote-secret-token")


class DiscoveryOnlyRemoteClient:
    """Counts discover calls and can be armed to fail if contacted again."""

    def __init__(self) -> None:
        self.discover_calls = 0
        self.fail_discovery = False

    async def discover(self, endpoint: str, **kwargs: Any) -> RemoteDiscovery:
        del kwargs
        assert endpoint == ENDPOINT
        self.discover_calls += 1
        if self.fail_discovery:
            raise AssertionError("the read-only route must not contact the server")
        return RemoteDiscovery(
            protocol_version="2026-07-28",
            server_info={"name": "fake-remote", "version": "1.0.0"},
            tools=[
                {
                    "name": "echo",
                    "description": "Echo a message",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ],
            resources=[],
            resource_templates=[],
            prompts=[],
            cache_hints={
                kind: (60_000, "public")
                for kind in ("tools", "resources", "resource_templates", "prompts")
            },
        )

    async def call_tool(
        self, endpoint: str, tool_name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        del endpoint, tool_name, kwargs
        return {"content": [], "structuredContent": arguments, "isError": False}


async def setup_unauthenticated_server() -> tuple[
    MemoryStore, RemoteMcpManager, DiscoveryOnlyRemoteClient, McpServerDefinition, str, str
]:
    """One workspace with an owner, one principal deliberately left outside it."""

    suffix = uuid4().hex[:8]
    workspace_id = f"wks_m6c_{suffix}"
    owner_id = f"usr_owner_{suffix}"
    outsider_id = f"usr_outsider_{suffix}"
    store = MemoryStore()
    remote = DiscoveryOnlyRemoteClient()
    for principal_id in (owner_id, outsider_id):
        await store.upsert_principal(
            Principal(principal_id=principal_id, issuer="test", subject=principal_id)
        )
    await store.upsert_workspace(WorkspaceEntity(workspace_id=workspace_id, name="M6C"))
    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=workspace_id,
            principal_id=owner_id,
            role=WorkspaceRole.OWNER,
        )
    )
    connector_id = f"conndef_mcp_{suffix}"
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
        principal_id=owner_id,
        workspace_id=workspace_id,
        issuer=ISSUER,
        scopes=["mcp:invoke"],
        audience=["https://mcp.example.test"],
        secret_store_key=f"secret-{owner_id}",
    )
    await store.upsert_token_handle(handle)
    await store.upsert_connection(
        Connection(
            connection_id=new_id("conn"),
            connector_id=connector_id,
            workspace_id=workspace_id,
            principal_id=owner_id,
            scope=ConnectionScope.USER,
            token_handle_ref=handle.token_handle_id,
            granted_scopes=["mcp:invoke"],
            status=ConnectionStatus.ACTIVE,
        )
    )
    manager = RemoteMcpManager(
        store=store,
        client=remote,
        endpoint_policy=McpEndpointPolicy(resolver=_public_dns),
        token_broker=StaticTokenBroker(),  # type: ignore[arg-type]
    )
    server = await manager.register(
        McpServerDefinition(
            mcp_server_id=new_id("mcp_server"),
            workspace_id=workspace_id,
            connector_id=connector_id,
            name="Fake remote",
            endpoint=ENDPOINT,
            owner_principal_id=owner_id,
            discovery_policy=McpDiscoveryPolicy(default_ttl_ms=60_000),
            tool_mappings=[McpToolMapping(capability_id="remote.echo", tool_name="echo")],
        )
    )
    await manager.refresh_discovery(
        server.mcp_server_id, principal_id=owner_id, workspace_id=workspace_id
    )
    return store, manager, remote, server, owner_id, outsider_id


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


async def _get_discovery_as(
    store: MemoryStore, manager: RemoteMcpManager, principal_id: str, mcp_server_id: str
) -> Any:
    who = await store.get_principal(principal_id)
    assert who is not None
    _install_app_state(store, manager, who)
    try:
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        async with client:
            return await client.get(f"/api/v1/mcp/servers/{mcp_server_id}/discovery")
    finally:
        _clear_app_state()


async def test_a_non_member_reads_discovery_only_once_granted_the_weakest_membership() -> None:
    store, manager, remote, server, _, outsider_id = await setup_unauthenticated_server()
    remote.fail_discovery = True
    calls_before = remote.discover_calls

    denied = await _get_discovery_as(store, manager, outsider_id, server.mcp_server_id)
    assert denied.status_code == 403
    assert denied.json()["code"] == "FORBIDDEN"

    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=server.workspace_id,
            principal_id=outsider_id,
            role=WorkspaceRole.VIEWER,
        )
    )
    allowed = await _get_discovery_as(store, manager, outsider_id, server.mcp_server_id)

    assert allowed.status_code == 200
    stored = await store.list_mcp_discovery_snapshots(server.mcp_server_id)
    assert allowed.json()["discovery_snapshot_id"] == stored[0].discovery_snapshot_id
    assert remote.discover_calls == calls_before
