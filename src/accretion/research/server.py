"""The in-repo research MCP server (SDD 9.1 ``mcp_servers: [research-mcp]``).

SDD 10.1 draws the research path as *MCP Gateway -> Research MCP / API adapter*, and
SDD 9.1 has the research package declare an MCP server rather than a Python handler.
That choice decides three things at once, and all three are load-bearing:

* :class:`~accretion.mcp.manager.RemoteMcpManager` already executes MCP bindings, so
  no new execution seam had to be opened in the gateway for research;
* the query travels inside the JSON-RPC body, so
  :class:`~accretion.mcp.endpoint_policy.McpEndpointPolicy` --- which rejects any URL
  carrying a query string, along with plaintext HTTP and non-global DNS answers ---
  needs no relaxation to accommodate a search capability. Its SSRF controls stay
  exactly as M3 left them;
* swapping the connector backend becomes a pure binding change.

Two servers are built here, one per upstream shape. They are ordinary MCP servers
built on the SDK, reachable over streamable HTTP like any third-party server, and they
are registered, discovered, and validated through M3's own machinery --- there is no
privileged path for being in-repo.

:func:`research_mcp_transport` mounts both behind one ASGI router keyed by hostname,
which is how a test (or a local operator) can serve them in-process without giving the
adapter a second, untested code path.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Any

from mcp.server import CacheHint, MCPServer
from mcp_types.methods import CacheableMethod

from accretion.research import sources

__all__ = [
    "CROSSREF_ENDPOINT",
    "CROSSREF_HOST",
    "CROSSREF_TOOLS",
    "OPENALEX_ENDPOINT",
    "OPENALEX_HOST",
    "OPENALEX_TOOLS",
    "HostRouter",
    "build_crossref_server",
    "build_openalex_server",
    "research_mcp_transport",
]

OPENALEX_HOST = "research-openalex.mcp.accretion.test"
CROSSREF_HOST = "research-crossref.mcp.accretion.test"
OPENALEX_ENDPOINT = f"https://{OPENALEX_HOST}/mcp"
CROSSREF_ENDPOINT = f"https://{CROSSREF_HOST}/mcp"

OPENALEX_TOOLS: dict[str, str] = {
    "research.literature.search": "openalex_works_search",
    "research.paper.fetch": "openalex_work_get",
    "research.metadata.resolve": "openalex_entity_metadata",
    "research.citation.verify": "openalex_citation_resolve",
    "github.search": "openalex_repository_search",
}

_CACHE_HINTS: Mapping[CacheableMethod, CacheHint] = {
    "tools/list": CacheHint(ttl_ms=300_000, scope="private")
}
"""Tool listings are stable and cheap to cache; resources and prompts are not served.

M3 caches discovery only when the server says how long its listing stays good, so a
server that publishes no hint is re-discovered on every enable. Declaring the hint is
ordinary server configuration, not a concession made for the adapter.
"""

CROSSREF_TOOLS: dict[str, str] = {
    "research.literature.search": "crossref_works_query",
    "research.paper.fetch": "crossref_work_by_doi",
    "research.metadata.resolve": "crossref_identifier_metadata",
    "research.citation.verify": "crossref_citation_resolve",
    "github.search": "crossref_repository_query",
}


def build_openalex_server() -> MCPServer:
    """Backend A: opaque work ids, ``items``, nested authorships."""

    server: MCPServer = MCPServer(
        "accretion-research-openalex", version="1.0.0", cache_hints=_CACHE_HINTS
    )

    @server.tool(name=OPENALEX_TOOLS["research.literature.search"], structured_output=True)
    async def works_search(q: str, per_page: int = 10) -> dict[str, Any]:
        """Search literature by free-text query."""
        return sources.openalex_search(q, per_page)

    @server.tool(name=OPENALEX_TOOLS["research.paper.fetch"], structured_output=True)
    async def work_get(work_id: str) -> dict[str, Any]:
        """Fetch one work by its opaque work identifier."""
        return sources.openalex_work(work_id)

    @server.tool(name=OPENALEX_TOOLS["research.metadata.resolve"], structured_output=True)
    async def entity_metadata(entity_id: str) -> dict[str, Any]:
        """Resolve an entity identifier to its metadata record."""
        return sources.openalex_metadata(entity_id)

    @server.tool(name=OPENALEX_TOOLS["research.citation.verify"], structured_output=True)
    async def citation_resolve(work_id: str, claimed_doi: str) -> dict[str, Any]:
        """Resolve a work and report the identifier actually registered for it."""
        return sources.openalex_citation_check(work_id, claimed_doi)

    @server.tool(name=OPENALEX_TOOLS["github.search"], structured_output=True)
    async def repository_search(q: str, per_page: int = 10) -> dict[str, Any]:
        """Search source repositories associated with published work."""
        return sources.openalex_repository_search(q, per_page)

    return server


def build_crossref_server() -> MCPServer:
    """Backend B: DOI identity, ``results``, list titles, split author names."""

    server: MCPServer = MCPServer(
        "accretion-research-crossref", version="1.0.0", cache_hints=_CACHE_HINTS
    )

    @server.tool(name=CROSSREF_TOOLS["research.literature.search"], structured_output=True)
    async def works_query(query_bibliographic: str, rows: int = 10) -> dict[str, Any]:
        """Search literature by bibliographic query."""
        return sources.crossref_search(query_bibliographic, rows)

    @server.tool(name=CROSSREF_TOOLS["research.paper.fetch"], structured_output=True)
    async def work_by_doi(doi: str) -> dict[str, Any]:
        """Fetch every registered record carrying a DOI."""
        return sources.crossref_work(doi)

    @server.tool(name=CROSSREF_TOOLS["research.metadata.resolve"], structured_output=True)
    async def identifier_metadata(identifier: str) -> dict[str, Any]:
        """Resolve a DOI or alternative identifier to its metadata record."""
        return sources.crossref_metadata(identifier)

    @server.tool(name=CROSSREF_TOOLS["research.citation.verify"], structured_output=True)
    async def citation_resolve(doi: str, claimed_title: str) -> dict[str, Any]:
        """Resolve a DOI and report whether the claimed title agrees with the register."""
        return sources.crossref_citation_check(doi, claimed_title)

    @server.tool(name=CROSSREF_TOOLS["github.search"], structured_output=True)
    async def repository_query(query_bibliographic: str, rows: int = 10) -> dict[str, Any]:
        """Search source repositories associated with published work."""
        return sources.crossref_repository_search(query_bibliographic, rows)

    return server


Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


class HostRouter:
    """Dispatch ASGI requests to one of several apps by ``Host`` header.

    The remote MCP client is handed a full URL and builds its own transport, so
    serving two backends in one process needs a router rather than two clients. Doing
    it here keeps the adapter's production code path and its in-process code path
    identical --- the same client, the same manager, the same endpoint policy.
    """

    def __init__(self, apps: Mapping[str, Any]) -> None:
        self.apps = dict(apps)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            # Each mounted app owns its own session manager lifespan; the caller
            # enters those directly, so the router's own lifespan is a no-op.
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        host = ""
        for key, value in scope.get("headers", []):
            if key.lower() == b"host":
                host = value.decode("latin-1").split(":", 1)[0]
                break
        app = self.apps.get(host)
        if app is None:
            raise LookupError(f"no research MCP server is mounted for host {host!r}")
        await app(scope, receive, send)


def research_mcp_transport(*, json_response: bool = True) -> tuple[HostRouter, list[Any]]:
    """Both research servers behind one router, with the apps whose lifespans to enter.

    Returns the router and the individual Starlette apps, because a streamable HTTP
    MCP app needs its own lifespan context entered before it will serve.
    """

    openalex_app = build_openalex_server().streamable_http_app(
        stateless_http=True, json_response=json_response, host=OPENALEX_HOST
    )
    crossref_app = build_crossref_server().streamable_http_app(
        stateless_http=True, json_response=json_response, host=CROSSREF_HOST
    )
    router = HostRouter({OPENALEX_HOST: openalex_app, CROSSREF_HOST: crossref_app})
    return router, [openalex_app, crossref_app]
