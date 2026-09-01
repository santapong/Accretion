from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

_ResultT = TypeVar("_ResultT")


class RemoteMcpError(RuntimeError):
    pass


class RemoteMcpAuthError(RemoteMcpError):
    pass


class RemoteMcpTransportError(RemoteMcpError):
    pass


@dataclass(frozen=True)
class RemoteDiscovery:
    protocol_version: str
    server_info: dict[str, Any] = field(default_factory=dict)
    tools: list[dict[str, Any]] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    resource_templates: list[dict[str, Any]] = field(default_factory=list)
    prompts: list[dict[str, Any]] = field(default_factory=list)
    cache_hints: dict[str, tuple[int, str]] = field(default_factory=dict)


class RemoteMcpClient(Protocol):
    async def discover(
        self,
        endpoint: str,
        *,
        authorization_header: str | None,
        timeout_seconds: float,
        max_items_per_kind: int,
        include_tools: bool,
        include_resources: bool,
        include_prompts: bool,
    ) -> RemoteDiscovery: ...

    async def call_tool(
        self,
        endpoint: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        authorization_header: str | None,
        timeout_seconds: float,
    ) -> dict[str, Any]: ...


class SdkRemoteMcpClient:
    """MCP SDK v2 adapter with redirects and ambient proxy credentials disabled."""

    def __init__(
        self,
        http_client_factory: Callable[
            [dict[str, str], float], httpx2.AsyncClient
        ]
        | None = None,
    ) -> None:
        self.http_client_factory = http_client_factory or self._http_client

    async def discover(
        self,
        endpoint: str,
        *,
        authorization_header: str | None,
        timeout_seconds: float,
        max_items_per_kind: int,
        include_tools: bool,
        include_resources: bool,
        include_prompts: bool,
    ) -> RemoteDiscovery:
        async def operation(client: Client) -> RemoteDiscovery:
            capabilities = client.server_capabilities
            tools: list[dict[str, Any]] = []
            tool_hint = (0, "private")
            if include_tools and capabilities.tools is not None:
                tools, tool_hint = await self._collect(
                    client.list_tools, "tools", max_items_per_kind
                )
            resources: list[dict[str, Any]] = []
            resource_templates: list[dict[str, Any]] = []
            resource_hint = (0, "private")
            template_hint = (0, "private")
            if include_resources and capabilities.resources is not None:
                resources, resource_hint = await self._collect(
                    client.list_resources, "resources", max_items_per_kind
                )
                resource_templates, template_hint = await self._collect(
                    client.list_resource_templates,
                    "resource_templates",
                    max_items_per_kind,
                )
            prompts: list[dict[str, Any]] = []
            prompt_hint = (0, "private")
            if include_prompts and capabilities.prompts is not None:
                prompts, prompt_hint = await self._collect(
                    client.list_prompts, "prompts", max_items_per_kind
                )
            info = client.server_info
            return RemoteDiscovery(
                protocol_version=client.protocol_version,
                server_info=info.model_dump(mode="json", by_alias=True) if info else {},
                tools=tools,
                resources=resources,
                resource_templates=resource_templates,
                prompts=prompts,
                cache_hints={
                    "tools": tool_hint,
                    "resources": resource_hint,
                    "resource_templates": template_hint,
                    "prompts": prompt_hint,
                },
            )

        return await self._with_client(
            endpoint, authorization_header, timeout_seconds, operation
        )

    async def call_tool(
        self,
        endpoint: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        authorization_header: str | None,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        async def operation(client: Client) -> dict[str, Any]:
            # The v2 SDK validates structured tool output against the current tool
            # schema. A fresh transport has no per-tool schema state, so absorb the
            # listing before the call; the manager still controls publication and
            # policy using its durable snapshot.
            await client.list_tools(cache_mode="bypass")
            result = await client.call_tool(
                tool_name, arguments, read_timeout_seconds=timeout_seconds
            )
            dumped = result.model_dump(mode="json", by_alias=True)
            if not isinstance(dumped, dict):  # pragma: no cover - Pydantic contract
                raise RemoteMcpTransportError("remote MCP returned a non-object tool result")
            return dumped

        return await self._with_client(
            endpoint, authorization_header, timeout_seconds, operation
        )

    async def _with_client(
        self,
        endpoint: str,
        authorization_header: str | None,
        timeout_seconds: float,
        operation: Callable[[Client], Awaitable[_ResultT]],
    ) -> _ResultT:
        headers = {"Authorization": authorization_header} if authorization_header else {}
        try:
            async with self.http_client_factory(headers, timeout_seconds) as http_client:
                transport = streamable_http_client(endpoint, http_client=http_client)
                async with Client(
                    transport,
                    mode="auto",
                    raise_exceptions=True,
                    read_timeout_seconds=timeout_seconds,
                ) as client:
                    return await operation(client)
        except httpx2.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise RemoteMcpAuthError("remote MCP authorization was rejected") from exc
            raise RemoteMcpTransportError("remote MCP HTTP request failed") from exc
        except RemoteMcpError:
            raise
        except Exception as exc:
            # SDK transports may wrap an HTTP status in an exception group. Never
            # include that exception's text because it may contain request headers.
            status = _status_code(exc)
            if status in {401, 403}:
                raise RemoteMcpAuthError("remote MCP authorization was rejected") from exc
            raise RemoteMcpTransportError("remote MCP request failed") from exc

    @staticmethod
    def _http_client(headers: dict[str, str], timeout_seconds: float) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            headers=headers,
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            limits=httpx2.Limits(max_connections=4, max_keepalive_connections=2),
        )

    @staticmethod
    async def _collect(
        fetch: Callable[..., Awaitable[Any]],
        attribute: str,
        limit: int,
    ) -> tuple[list[dict[str, Any]], tuple[int, str]]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        ttl_ms: int | None = None
        public_scope = True
        while True:
            result = await fetch(cursor=cursor, cache_mode="bypass")
            page = getattr(result, attribute)
            items.extend(item.model_dump(mode="json", by_alias=True) for item in page)
            if len(items) > limit:
                raise RemoteMcpTransportError(f"remote MCP {attribute} listing exceeds limit")
            page_ttl = int(result.ttl_ms)
            ttl_ms = page_ttl if ttl_ms is None else min(ttl_ms, page_ttl)
            public_scope = public_scope and result.cache_scope == "public"
            cursor = result.next_cursor
            if cursor is None:
                break
        return items, (ttl_ms or 0, "public" if public_scope else "private")


def _status_code(exc: BaseException) -> int | None:
    if isinstance(exc, httpx2.HTTPStatusError):
        return exc.response.status_code
    if isinstance(exc, BaseExceptionGroup):
        for nested in exc.exceptions:
            status = _status_code(nested)
            if status is not None:
                return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None
