from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence
from fnmatch import fnmatch
from urllib.parse import SplitResult, urlsplit, urlunsplit


class McpEndpointPolicyError(ValueError):
    """A remote endpoint is outside the operator's network trust policy."""


AddressResolver = Callable[[str, int], Awaitable[Sequence[str]]]


async def _system_resolver(host: str, port: int) -> Sequence[str]:
    loop = asyncio.get_running_loop()
    answers = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(answer[4][0]) for answer in answers})


class McpEndpointPolicy:
    """Validates both URL syntax and every current DNS answer before network I/O."""

    def __init__(
        self,
        *,
        allowed_hosts: Sequence[str] = (),
        allowed_ports: Sequence[int] = (443,),
        allow_local_http: bool = False,
        resolver: AddressResolver | None = None,
    ) -> None:
        self.allowed_hosts = tuple(item.casefold().rstrip(".") for item in allowed_hosts)
        self.allowed_ports = frozenset(allowed_ports)
        self.allow_local_http = allow_local_http
        self.resolver = resolver or _system_resolver

    async def validate(self, endpoint: str) -> str:
        try:
            parsed = urlsplit(endpoint)
            host = (parsed.hostname or "").encode("idna").decode("ascii").casefold().rstrip(".")
            port = parsed.port
        except (UnicodeError, ValueError) as exc:
            raise McpEndpointPolicyError("MCP endpoint is not a valid URL") from exc
        if parsed.username is not None or parsed.password is not None:
            raise McpEndpointPolicyError("MCP endpoint must not contain user information")
        if parsed.fragment:
            raise McpEndpointPolicyError("MCP endpoint must not contain a fragment")
        if parsed.query:
            raise McpEndpointPolicyError("MCP endpoint must not contain a query")
        if not host:
            raise McpEndpointPolicyError("MCP endpoint requires a hostname")
        if parsed.scheme not in {"https", "http"}:
            raise McpEndpointPolicyError("remote MCP endpoints must use HTTPS")
        effective_port = port or (443 if parsed.scheme == "https" else 80)
        if self.allowed_ports and effective_port not in self.allowed_ports:
            raise McpEndpointPolicyError(f"MCP endpoint port {effective_port} is not allowed")
        if self.allowed_hosts and not any(fnmatch(host, pattern) for pattern in self.allowed_hosts):
            raise McpEndpointPolicyError("MCP endpoint hostname is not allowlisted")

        addresses = await self.resolver(host, effective_port)
        if not addresses:
            raise McpEndpointPolicyError("MCP endpoint hostname did not resolve")
        parsed_addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for address in addresses:
            try:
                parsed_addresses.append(ipaddress.ip_address(address))
            except ValueError as exc:
                raise McpEndpointPolicyError("MCP endpoint returned an invalid DNS answer") from exc
        all_loopback = all(address.is_loopback for address in parsed_addresses)
        if parsed.scheme == "http" and not (self.allow_local_http and all_loopback):
            raise McpEndpointPolicyError("remote MCP endpoints must use HTTPS")
        if not (self.allow_local_http and all_loopback):
            blocked = [address for address in parsed_addresses if not address.is_global]
            if blocked:
                raise McpEndpointPolicyError("MCP endpoint resolves to a non-public address")

        netloc = host
        if port is not None:
            netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        normalized = SplitResult(
            parsed.scheme,
            netloc,
            parsed.path or "/",
            parsed.query,
            "",
        )
        return urlunsplit(normalized)
