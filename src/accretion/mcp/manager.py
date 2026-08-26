from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatch
from typing import Any

from jsonschema import SchemaError
from jsonschema.validators import validator_for

from accretion.contracts import (
    Capability,
    CapabilityBackend,
    CapabilityBinding,
    CapabilityBindingBackend,
    Connection,
    ConnectionRef,
    ConnectionScope,
    ConnectionStatus,
    ConnectorAuthType,
    ConnectorKind,
    McpCacheHint,
    McpCacheScope,
    McpDiscoverySnapshot,
    McpServerDefinition,
    McpServerEvent,
    McpServerState,
    McpTransport,
)
from accretion.ids import new_id
from accretion.mcp.endpoint_policy import McpEndpointPolicy
from accretion.mcp.remote_client import (
    RemoteDiscovery,
    RemoteMcpAuthError,
    RemoteMcpClient,
    RemoteMcpTransportError,
)
from accretion.persistence.store import StateStore
from accretion.token_broker import TokenBroker, TokenBrokerError


class McpManagerError(RuntimeError):
    pass


class McpServerAuthRequired(McpManagerError):
    pass


class McpServerUnavailable(McpManagerError):
    pass


class RemoteMcpManager:
    """Registration, discovery publication, and governed remote execution boundary."""

    def __init__(
        self,
        *,
        store: StateStore,
        client: RemoteMcpClient,
        endpoint_policy: McpEndpointPolicy,
        token_broker: TokenBroker | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.endpoint_policy = endpoint_policy
        self.token_broker = token_broker

    async def register(self, server: McpServerDefinition) -> McpServerDefinition:
        if await self.store.get_mcp_server(server.mcp_server_id) is not None:
            raise McpManagerError(f"MCP server {server.mcp_server_id} is already registered")
        if server.transport is not McpTransport.HTTP or server.endpoint is None:
            raise McpManagerError("M3 remote registration requires HTTP transport")
        if server.enabled or server.state is not McpServerState.DISABLED:
            raise McpManagerError("new MCP servers must be registered disabled")
        if server.tool_mappings and not server.discovery_policy.tools:
            raise McpManagerError("tool discovery is required when tool mappings are configured")
        endpoint = await self.endpoint_policy.validate(server.endpoint)
        connector = await self.store.get_connector_definition(server.connector_id)
        if connector is None:
            raise McpManagerError(f"connector {server.connector_id} is not registered")
        if connector.kind is not ConnectorKind.MCP:
            raise McpManagerError("remote MCP servers require an MCP connector")
        if connector.resource_server:
            resource_endpoint = await self.endpoint_policy.validate(connector.resource_server)
            if _origin(resource_endpoint) != _origin(endpoint):
                raise McpManagerError("connector resource server does not match MCP endpoint")
        if connector.authorization_server:
            await self.endpoint_policy.validate(connector.authorization_server)
        normalized = server.model_copy(update={"endpoint": endpoint})
        await self.store.upsert_mcp_server(normalized)
        if connector.auth_type is ConnectorAuthType.NONE:
            existing = await self.store.list_connections(connector_id=connector.connector_id)
            if not any(item.workspace_id == server.workspace_id for item in existing):
                await self.store.upsert_connection(
                    Connection(
                        connection_id=new_id("conn"),
                        connector_id=connector.connector_id,
                        workspace_id=server.workspace_id,
                        scope=ConnectionScope.WORKSPACE,
                        status=ConnectionStatus.ACTIVE,
                        workspace_shareable=True,
                    )
                )
        await self._event(normalized, "REGISTERED", normalized.owner_principal_id)
        return normalized

    async def refresh_discovery(
        self,
        mcp_server_id: str,
        *,
        principal_id: str,
        workspace_id: str,
        force: bool = False,
        correlation_id: str | None = None,
    ) -> McpDiscoverySnapshot:
        server = await self._owned_server(mcp_server_id, workspace_id)
        await self._ensure_circuit_closed(server)
        endpoint = await self._validated_endpoint(server)
        connection, authorization = await self._authorization(
            server, principal_id=principal_id, workspace_id=workspace_id
        )
        cached_parts: dict[
            str, tuple[list[dict[str, Any]], McpCacheHint, McpDiscoverySnapshot]
        ] = {}
        if not force:
            cached = await self._fresh_snapshot(server, connection)
            if cached is not None:
                await self._event(
                    server,
                    "DISCOVERY_CACHE_HIT",
                    principal_id,
                    correlation_id,
                    {"snapshot_id": cached.discovery_snapshot_id},
                )
                return cached
            cached_parts = await self._fresh_discovery_parts(server, connection)
        need_tools = server.discovery_policy.tools and "tools" not in cached_parts
        need_resources = server.discovery_policy.resources and (
            "resources" not in cached_parts or "resource_templates" not in cached_parts
        )
        need_prompts = server.discovery_policy.prompts and "prompts" not in cached_parts
        requested_kinds = self._requested_kinds(server)
        try:
            remote: RemoteDiscovery | None = None
            if need_tools or need_resources or need_prompts or not requested_kinds:
                for attempt in range(server.health_policy.max_discovery_retries + 1):
                    try:
                        remote = await self.client.discover(
                            endpoint,
                            authorization_header=authorization,
                            timeout_seconds=server.health_policy.timeout_ms / 1000,
                            max_items_per_kind=server.discovery_policy.max_items_per_kind,
                            include_tools=need_tools,
                            include_resources=need_resources,
                            include_prompts=need_prompts,
                        )
                        break
                    except RemoteMcpTransportError:
                        if attempt >= server.health_policy.max_discovery_retries:
                            raise
        except RemoteMcpAuthError as exc:
            await self._mark_auth_required(server, connection, principal_id, correlation_id)
            raise McpServerAuthRequired("remote MCP authorization is required") from exc
        except RemoteMcpTransportError as exc:
            await self._record_failure(server, principal_id, correlation_id)
            raise McpServerUnavailable("remote MCP discovery failed") from exc

        discovery = self._merge_discovery(remote, cached_parts)
        snapshot = self._snapshot(server, connection, discovery)
        encoded_snapshot = snapshot.model_dump_json().encode()
        if len(encoded_snapshot) > server.health_policy.max_response_bytes:
            await self._record_failure(server, principal_id, correlation_id)
            raise McpServerUnavailable("remote MCP discovery response exceeds size limit")
        await self.store.save_mcp_discovery_snapshot(snapshot)
        state = McpServerState.SCHEMA_ERROR if not snapshot.valid else (
            McpServerState.READY if server.enabled else McpServerState.DISABLED
        )
        updated = server.model_copy(
            update={
                "state": state,
                "consecutive_failures": 0,
                "circuit_open_until": None,
                "last_health_check": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "revision": server.revision + 1,
            }
        )
        await self.store.upsert_mcp_server(updated)
        await self._event(
            updated,
            "DISCOVERY_REFRESHED",
            principal_id,
            correlation_id,
            {
                "snapshot_id": snapshot.discovery_snapshot_id,
                "valid": snapshot.valid,
                "schema_error_count": len(snapshot.schema_errors),
            },
        )
        return snapshot

    async def enable(
        self,
        mcp_server_id: str,
        *,
        principal_id: str,
        workspace_id: str,
    ) -> McpServerDefinition:
        server = await self._owned_server(mcp_server_id, workspace_id)
        if server.enabled and server.state is McpServerState.READY:
            return server
        connection, _ = await self._authorization(
            server, principal_id=principal_id, workspace_id=workspace_id
        )
        snapshot = await self._fresh_snapshot(server, connection)
        if snapshot is None:
            raise McpManagerError("a fresh discovery snapshot is required before enabling")
        if not snapshot.valid:
            raise McpManagerError("MCP discovery contains invalid tool schemas")
        discovered = {str(tool.get("name")): tool for tool in snapshot.tools}
        for mapping in server.tool_mappings:
            tool = discovered.get(mapping.tool_name)
            if tool is None:
                raise McpManagerError(
                    f"mapped tool {mapping.tool_name!r} was not present in discovery"
                )
            output_schema = tool.get("outputSchema")
            published_output_schema = (
                {
                    "type": "object",
                    "properties": {"structuredContent": output_schema},
                    "required": ["structuredContent"],
                }
                if isinstance(output_schema, dict)
                else {"type": "object"}
            )
            existing_capability = await self.store.get_capability(
                mapping.capability_id, mapping.version
            )
            if existing_capability is None:
                capability = Capability(
                    capability_id=mapping.capability_id,
                    version=mapping.version,
                    description=str(tool.get("description") or ""),
                    input_schema=dict(tool["inputSchema"]),
                    output_schema=published_output_schema,
                    risk=mapping.risk,
                    side_effects=list(mapping.side_effects),
                    required_permissions=list(mapping.required_permissions),
                    idempotency=mapping.idempotency,
                    backend=CapabilityBackend.MCP,
                    provider_projections={
                        "accretion": {
                            "mcp_server_id": server.mcp_server_id,
                            "remote_tool_name": mapping.tool_name,
                            "trust_level": server.trust_level.value,
                            "discovery_snapshot_id": snapshot.discovery_snapshot_id,
                        }
                    },
                )
                await self.store.upsert_capability(capability)
            else:
                projection = existing_capability.provider_projections.get("accretion", {})
                if (
                    existing_capability.backend is not CapabilityBackend.MCP
                    or existing_capability.input_schema != tool["inputSchema"]
                    or existing_capability.output_schema != published_output_schema
                    or not isinstance(projection, dict)
                    or projection.get("mcp_server_id") != server.mcp_server_id
                    or projection.get("remote_tool_name") != mapping.tool_name
                ):
                    raise McpManagerError(
                        f"capability {mapping.capability_id}@{mapping.version} changed; "
                        "publish a new semantic version"
                    )
            previous = [
                item
                for item in await self.store.list_capability_bindings(
                    capability_id=mapping.capability_id, enabled_only=False
                )
                if item.backend.server_ref == server.mcp_server_id
            ]
            binding = CapabilityBinding(
                binding_id=previous[0].binding_id if previous else new_id("capbind"),
                capability_id=mapping.capability_id,
                connector_id=server.connector_id,
                backend=CapabilityBindingBackend(
                    type=CapabilityBackend.MCP,
                    server_ref=server.mcp_server_id,
                    method="tools/call",
                    tool_name=mapping.tool_name,
                ),
                enabled=True,
            )
            await self.store.upsert_capability_binding(binding)
        updated = server.model_copy(
            update={
                "enabled": True,
                "state": McpServerState.READY,
                "revision": server.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        await self.store.upsert_mcp_server(updated)
        await self._event(updated, "ENABLED", principal_id)
        return updated

    async def disable(
        self,
        mcp_server_id: str,
        *,
        principal_id: str,
        workspace_id: str,
    ) -> McpServerDefinition:
        server = await self._owned_server(mcp_server_id, workspace_id)
        if not server.enabled and server.state is McpServerState.DISABLED:
            return server
        bindings = await self.store.list_capability_bindings(
            connector_id=server.connector_id, enabled_only=False
        )
        for binding in bindings:
            if binding.backend.server_ref == server.mcp_server_id and binding.enabled:
                await self.store.upsert_capability_binding(
                    binding.model_copy(update={"enabled": False})
                )
        updated = server.model_copy(
            update={
                "enabled": False,
                "state": McpServerState.DISABLED,
                "revision": server.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        await self.store.upsert_mcp_server(updated)
        await self._event(updated, "DISABLED", principal_id)
        return updated

    async def execute(
        self,
        binding: CapabilityBinding,
        connection: ConnectionRef | None,
        arguments: dict[str, Any],
        credentials: dict[str, str],
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        server_ref = binding.backend.server_ref
        tool_name = binding.backend.tool_name
        if not server_ref or not tool_name:
            raise McpManagerError("MCP capability binding is incomplete")
        server = await self.store.get_mcp_server(server_ref)
        if server is None or not server.enabled or server.state not in {
            McpServerState.READY,
            McpServerState.DEGRADED,
        }:
            raise McpServerUnavailable("remote MCP server is not executable")
        await self._ensure_circuit_closed(server)
        endpoint = await self._validated_endpoint(server)
        token = credentials.get(f"connection:{server.connector_id}")
        connector = await self.store.get_connector_definition(server.connector_id)
        if connector is None:
            raise McpManagerError("MCP connector is missing")
        if connector.auth_type is not ConnectorAuthType.NONE and not token:
            stored = (
                await self.store.get_connection(connection.connection_id)
                if connection
                else None
            )
            await self._mark_auth_required(server, stored, None, correlation_id)
            raise McpServerAuthRequired("remote MCP credential is unavailable")
        authorization = f"Bearer {token}" if token else None
        try:
            output = await self.client.call_tool(
                endpoint,
                tool_name,
                arguments,
                authorization_header=authorization,
                timeout_seconds=server.health_policy.timeout_ms / 1000,
            )
        except RemoteMcpAuthError as exc:
            stored = (
                await self.store.get_connection(connection.connection_id)
                if connection
                else None
            )
            await self._mark_auth_required(server, stored, None, correlation_id)
            raise McpServerAuthRequired("remote MCP authorization is required") from exc
        except RemoteMcpTransportError as exc:
            await self._record_failure(server, None, correlation_id)
            raise McpServerUnavailable("remote MCP tool call failed") from exc
        if len(json.dumps(output, separators=(",", ":")).encode()) > (
            server.health_policy.max_response_bytes
        ):
            await self._record_failure(server, None, correlation_id)
            raise McpServerUnavailable("remote MCP tool result exceeds size limit")
        await self._event(
            server,
            "TOOL_CALLED",
            correlation_id=correlation_id,
            details={"tool_name": tool_name},
        )
        return output

    async def mark_auth_required(
        self,
        binding: CapabilityBinding,
        connection: ConnectionRef | None,
        *,
        correlation_id: str | None = None,
    ) -> None:
        server = (
            await self.store.get_mcp_server(binding.backend.server_ref)
            if binding.backend.server_ref
            else None
        )
        if server is None:
            return
        stored = (
            await self.store.get_connection(connection.connection_id)
            if connection
            else None
        )
        await self._mark_auth_required(server, stored, None, correlation_id)

    async def capabilities(
        self, mcp_server_id: str, *, workspace_id: str
    ) -> list[Capability]:
        server = await self._owned_server(mcp_server_id, workspace_id)
        bindings = await self.store.list_capability_bindings(
            connector_id=server.connector_id, enabled_only=False
        )
        result: list[Capability] = []
        for binding in bindings:
            if binding.backend.server_ref != server.mcp_server_id:
                continue
            capability = await self.store.get_capability(binding.capability_id)
            if capability is not None:
                result.append(capability)
        return result

    async def _authorization(
        self,
        server: McpServerDefinition,
        *,
        principal_id: str,
        workspace_id: str,
    ) -> tuple[Connection | None, str | None]:
        connector = await self.store.get_connector_definition(server.connector_id)
        if connector is None:
            raise McpManagerError("MCP connector is missing")
        if connector.auth_type is ConnectorAuthType.NONE:
            return None, None
        connection = self._select_connection(
            await self.store.list_connections(connector_id=server.connector_id),
            principal_id,
            workspace_id,
        )
        if connection is None or connection.status not in {
            ConnectionStatus.ACTIVE,
            ConnectionStatus.DEGRADED,
        }:
            await self._mark_auth_required(server, connection, principal_id, None)
            raise McpServerAuthRequired("an active MCP connection is required")
        if connection.token_handle_ref is None or self.token_broker is None:
            await self._mark_auth_required(server, connection, principal_id, None)
            raise McpServerAuthRequired("MCP connection has no available credential")
        handle = await self.store.get_token_handle(connection.token_handle_ref)
        if handle is None:
            await self._mark_auth_required(server, connection, principal_id, None)
            raise McpServerAuthRequired("MCP connection credential is missing")
        try:
            material = await self.token_broker.get_access_material(
                handle,
                audience=[connector.resource_server] if connector.resource_server else [],
                scopes=list(connection.granted_scopes),
                expected_issuer=connector.authorization_server,
            )
        except TokenBrokerError as exc:
            await self._mark_auth_required(server, connection, principal_id, None)
            raise McpServerAuthRequired("MCP connection credential is unusable") from exc
        return connection, material.authorization_header()

    async def _fresh_snapshot(
        self, server: McpServerDefinition, connection: Connection | None
    ) -> McpDiscoverySnapshot | None:
        kinds = self._requested_kinds(server)
        for snapshot in await self.store.list_mcp_discovery_snapshots(server.mcp_server_id):
            same_connection = snapshot.connection_id == (
                connection.connection_id if connection else None
            )
            public = all(
                snapshot.cache_hints.get(kind) is not None
                and snapshot.cache_hints[kind].scope is McpCacheScope.PUBLIC
                for kind in kinds
            )
            if (same_connection or public) and all(snapshot.is_fresh(kind) for kind in kinds):
                return snapshot
        return None

    async def _fresh_discovery_parts(
        self, server: McpServerDefinition, connection: Connection | None
    ) -> dict[str, tuple[list[dict[str, Any]], McpCacheHint, McpDiscoverySnapshot]]:
        parts: dict[
            str, tuple[list[dict[str, Any]], McpCacheHint, McpDiscoverySnapshot]
        ] = {}
        connection_id = connection.connection_id if connection else None
        for snapshot in await self.store.list_mcp_discovery_snapshots(server.mcp_server_id):
            if not snapshot.valid:
                continue
            for kind in self._requested_kinds(server):
                if kind in parts:
                    continue
                hint = snapshot.cache_hints.get(kind)
                if hint is None or not snapshot.is_fresh(kind):
                    continue
                if (
                    snapshot.connection_id != connection_id
                    and hint.scope is not McpCacheScope.PUBLIC
                ):
                    continue
                parts[kind] = (list(getattr(snapshot, kind)), hint, snapshot)
        return parts

    @staticmethod
    def _requested_kinds(server: McpServerDefinition) -> list[str]:
        kinds: list[str] = []
        if server.discovery_policy.tools:
            kinds.append("tools")
        if server.discovery_policy.resources:
            kinds.extend(["resources", "resource_templates"])
        if server.discovery_policy.prompts:
            kinds.append("prompts")
        return kinds

    @staticmethod
    def _merge_discovery(
        remote: RemoteDiscovery | None,
        cached: dict[
            str, tuple[list[dict[str, Any]], McpCacheHint, McpDiscoverySnapshot]
        ],
    ) -> RemoteDiscovery:
        source = remote
        if source is None:
            if not cached:
                raise McpManagerError("MCP discovery produced no metadata")
            newest = max(
                (part[2] for part in cached.values()), key=lambda item: item.created_at
            )
            source = RemoteDiscovery(
                protocol_version=newest.protocol_version,
                server_info=newest.server_info,
            )
        values: dict[str, list[dict[str, Any]]] = {
            "tools": source.tools,
            "resources": source.resources,
            "resource_templates": source.resource_templates,
            "prompts": source.prompts,
        }
        hints = dict(source.cache_hints)
        for kind, (items, hint, _snapshot) in cached.items():
            values[kind] = items
            hints[kind] = (hint.ttl_ms, hint.scope.value.casefold())
        return RemoteDiscovery(
            protocol_version=source.protocol_version,
            server_info=source.server_info,
            tools=values["tools"],
            resources=values["resources"],
            resource_templates=values["resource_templates"],
            prompts=values["prompts"],
            cache_hints=hints,
        )

    def _snapshot(
        self,
        server: McpServerDefinition,
        connection: Connection | None,
        discovery: RemoteDiscovery,
    ) -> McpDiscoverySnapshot:
        tools, errors = _validated_tools(
            discovery.tools,
            allowed=server.allowed_tool_patterns,
            denied=server.denied_tool_patterns,
        )
        if discovery.protocol_version not in server.protocol_versions:
            errors.insert(
                0,
                f"unsupported negotiated protocol version {discovery.protocol_version!r}",
            )
        hints: dict[str, McpCacheHint] = {}
        for kind in ("tools", "resources", "resource_templates", "prompts"):
            ttl, scope = discovery.cache_hints.get(
                kind,
                (server.discovery_policy.default_ttl_ms, "private"),
            )
            hints[kind] = McpCacheHint(
                ttl_ms=ttl,
                scope=McpCacheScope.PUBLIC if scope == "public" else McpCacheScope.PRIVATE,
            )
        partition = (
            None
            if all(hint.scope is McpCacheScope.PUBLIC for hint in hints.values())
            else (connection.connection_id if connection else None)
        )
        content = {
            "protocol_version": discovery.protocol_version,
            "server_info": discovery.server_info,
            "tools": tools,
            "resources": discovery.resources,
            "resource_templates": discovery.resource_templates,
            "prompts": discovery.prompts,
            "cache_hints": {
                key: value.model_dump(mode="json") for key, value in hints.items()
            },
            "schema_errors": errors,
        }
        digest = hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return McpDiscoverySnapshot(
            discovery_snapshot_id=new_id("mcp_snapshot"),
            mcp_server_id=server.mcp_server_id,
            connection_id=partition,
            protocol_version=discovery.protocol_version,
            server_info=discovery.server_info,
            tools=tools,
            resources=discovery.resources,
            resource_templates=discovery.resource_templates,
            prompts=discovery.prompts,
            cache_hints=hints,
            schema_errors=errors,
            valid=not errors,
            content_sha256=digest,
        )

    async def _mark_auth_required(
        self,
        server: McpServerDefinition,
        connection: Connection | None,
        actor: str | None,
        correlation_id: str | None,
    ) -> None:
        if connection is not None and connection.status is not ConnectionStatus.REVOKED:
            await self.store.upsert_connection(
                connection.model_copy(
                    update={
                        "status": ConnectionStatus.REAUTH_REQUIRED,
                        "last_health_check": datetime.now(UTC),
                    }
                )
            )
        updated = server.model_copy(
            update={
                "state": McpServerState.AUTH_REQUIRED,
                "last_health_check": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "revision": server.revision + 1,
            }
        )
        await self.store.upsert_mcp_server(updated)
        await self._event(updated, "AUTH_REQUIRED", actor, correlation_id)

    async def _record_failure(
        self,
        server: McpServerDefinition,
        actor: str | None,
        correlation_id: str | None,
    ) -> None:
        failures = server.consecutive_failures + 1
        circuit = None
        state = McpServerState.UNREACHABLE
        if failures >= server.health_policy.failure_threshold:
            circuit = datetime.now(UTC) + timedelta(
                seconds=server.health_policy.cooldown_seconds
            )
            state = McpServerState.DEGRADED
        updated = server.model_copy(
            update={
                "state": state,
                "consecutive_failures": failures,
                "circuit_open_until": circuit,
                "last_health_check": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "revision": server.revision + 1,
            }
        )
        await self.store.upsert_mcp_server(updated)
        await self._event(updated, "TRANSPORT_FAILURE", actor, correlation_id)

    async def _ensure_circuit_closed(self, server: McpServerDefinition) -> None:
        if server.circuit_open_until and server.circuit_open_until > datetime.now(UTC):
            raise McpServerUnavailable("remote MCP circuit breaker is open")

    async def _owned_server(
        self, mcp_server_id: str, workspace_id: str
    ) -> McpServerDefinition:
        server = await self.store.get_mcp_server(mcp_server_id)
        if server is None or server.workspace_id != workspace_id:
            raise KeyError(mcp_server_id)
        return server

    async def _validated_endpoint(self, server: McpServerDefinition) -> str:
        if server.endpoint is None:
            raise McpManagerError("remote MCP server has no endpoint")
        return await self.endpoint_policy.validate(server.endpoint)

    async def _event(
        self,
        server: McpServerDefinition,
        event_type: str,
        actor: str | None = None,
        correlation_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        await self.store.append_mcp_server_event(
            McpServerEvent(
                mcp_event_id=new_id("mcp_event"),
                mcp_server_id=server.mcp_server_id,
                event_type=event_type,
                actor_principal_id=actor,
                correlation_id=correlation_id,
                details=details or {},
            )
        )

    @staticmethod
    def _select_connection(
        candidates: list[Connection], principal_id: str, workspace_id: str
    ) -> Connection | None:
        owned = [
            item
            for item in candidates
            if item.workspace_id == workspace_id
            and item.scope is ConnectionScope.USER
            and item.principal_id == principal_id
        ]
        shared = [
            item
            for item in candidates
            if item.workspace_id == workspace_id
            and item.scope is ConnectionScope.WORKSPACE
            and item.workspace_shareable
        ]
        pool = owned or shared
        usable = [
            item
            for item in pool
            if item.status in {ConnectionStatus.ACTIVE, ConnectionStatus.DEGRADED}
        ]
        return sorted(usable or pool, key=lambda item: item.connection_id)[0] if pool else None


def _validated_tools(
    tools: list[dict[str, Any]], *, allowed: list[str], denied: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    valid: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, tool in enumerate(tools):
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"tools[{index}]: name must be a non-empty string")
            continue
        if name in seen:
            errors.append(f"tool {name!r}: duplicate name")
            continue
        seen.add(name)
        if denied and any(fnmatch(name, pattern) for pattern in denied):
            continue
        if allowed and not any(fnmatch(name, pattern) for pattern in allowed):
            continue
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict):
            errors.append(f"tool {name!r}: inputSchema must be an object")
            continue
        if schema.get("type") != "object":
            errors.append(f"tool {name!r}: inputSchema root type must be object")
            continue
        try:
            validator_for(schema).check_schema(schema)
            output_schema = tool.get("outputSchema")
            if output_schema is not None:
                if not isinstance(output_schema, dict):
                    raise SchemaError("outputSchema must be an object")
                validator_for(output_schema).check_schema(output_schema)
        except SchemaError as exc:
            errors.append(f"tool {name!r}: invalid JSON Schema: {exc.message}")
            continue
        valid.append(tool)
    return valid, errors


def _origin(url: str) -> tuple[str, str, int | None]:
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname or "", parsed.port
