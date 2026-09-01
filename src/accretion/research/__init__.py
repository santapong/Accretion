"""Research intelligence adapter (v0.3 M5).

The package that turns SDD 10.1's *Research MCP / API adapter* into running code:

* :mod:`accretion.research.sources` --- two upstream shapes over one body of facts,
  deliberately incompatible so normalization has real work to do;
* :mod:`accretion.research.server` --- the in-repo research MCP servers those shapes
  are served through, registered and discovered like any third-party server;
* :mod:`accretion.research.transforms` --- the SDD 7.6 transform registry, which is
  where the two shapes become one canonical output.

This module holds the seam that ties them to the registry: the connectors the
bindings hang from, and the bindings themselves. Two backends are bound to the *same*
canonical capability ids and differ only in ``connector_id`` and their transform
references, so switching backend is exactly the flip of ``enabled`` on two binding
rows --- no workflow, capability id, or schema moves (AC3-RES-02).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from accretion.contracts import (
    CapabilityBackend,
    CapabilityBinding,
    CapabilityBindingBackend,
    ConnectionScope,
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
)
from accretion.ids import new_id
from accretion.persistence.store import StateStore
from accretion.research.server import (
    CROSSREF_ENDPOINT,
    CROSSREF_TOOLS,
    OPENALEX_ENDPOINT,
    OPENALEX_TOOLS,
)
from accretion.research.transforms import (
    CROSSREF_INPUT_REFS,
    CROSSREF_OUTPUT_REFS,
    OPENALEX_INPUT_REFS,
    OPENALEX_OUTPUT_REFS,
    TransformRegistry,
    default_transform_registry,
)
from accretion.research.trust import assign_trust, rank_evidence

__all__ = [
    "BACKENDS",
    "CROSSREF_CONNECTOR",
    "OPENALEX_CONNECTOR",
    "RESEARCH_CAPABILITY_IDS",
    "RESEARCH_PLUGIN_ID",
    "ResearchBackend",
    "bind_research_backend",
    "default_transform_registry",
    "research_backend",
    "seed_research_connectors",
    "TransformRegistry",
    "assign_trust",
    "rank_evidence",
]

RESEARCH_PLUGIN_ID = "accretion-research"

OPENALEX_CONNECTOR = "research-openalex"
CROSSREF_CONNECTOR = "research-crossref"

RESEARCH_CAPABILITY_IDS: tuple[str, ...] = (
    "research.literature.search",
    "research.paper.fetch",
    "research.metadata.resolve",
    "research.citation.verify",
    "github.search",
)
"""The canonical ids. SDD 10's superset, minus ``python.execute`` (ADR3-M5-002).

No research criterion needs code execution, so the capability is not declared. The
absence is asserted in the M5 tests rather than merely written down, which is what
keeps it a decision instead of an oversight.
"""


@dataclass(frozen=True, slots=True)
class ResearchBackend:
    """One interchangeable connector backend for the canonical research capabilities."""

    connector_id: str
    name: str
    endpoint: str
    tools: Mapping[str, str]
    input_refs: Mapping[str, str]
    output_refs: Mapping[str, str]


BACKENDS: tuple[ResearchBackend, ...] = (
    ResearchBackend(
        connector_id=OPENALEX_CONNECTOR,
        name="Accretion Research (OpenAlex-shaped)",
        endpoint=OPENALEX_ENDPOINT,
        tools=OPENALEX_TOOLS,
        input_refs=OPENALEX_INPUT_REFS,
        output_refs=OPENALEX_OUTPUT_REFS,
    ),
    ResearchBackend(
        connector_id=CROSSREF_CONNECTOR,
        name="Accretion Research (Crossref-shaped)",
        endpoint=CROSSREF_ENDPOINT,
        tools=CROSSREF_TOOLS,
        input_refs=CROSSREF_INPUT_REFS,
        output_refs=CROSSREF_OUTPUT_REFS,
    ),
)


def research_backend(connector_id: str) -> ResearchBackend:
    for backend in BACKENDS:
        if backend.connector_id == connector_id:
            return backend
    raise KeyError(f"no research backend is defined for connector {connector_id!r}")


async def seed_research_connectors(store: StateStore) -> list[ConnectorDefinition]:
    """Register both research connectors, without credentials.

    ``ConnectorKind.MCP`` is required by ``RemoteMcpManager.register``; ``auth_type``
    stays ``NONE`` because the bundled backends are local fixtures. Registration also
    means the manager creates the workspace connection itself, so no credential-shaped
    object is ever invented for a connector that has no credential.

    No ``resource_server`` is declared: M3 requires it to share an origin with the MCP
    endpoint, and leaving it unset keeps that check meaningful for connectors that do
    carry one.
    """

    registered: list[ConnectorDefinition] = []
    for backend in BACKENDS:
        existing = await store.get_connector_definition(backend.connector_id)
        if existing is not None:
            registered.append(existing)
            continue
        connector = ConnectorDefinition(
            connector_id=backend.connector_id,
            plugin_id=RESEARCH_PLUGIN_ID,
            name=backend.name,
            kind=ConnectorKind.MCP,
            auth_type=ConnectorAuthType.NONE,
            connection_scope=ConnectionScope.WORKSPACE,
        )
        await store.upsert_connector_definition(connector)
        registered.append(connector)
    return registered


async def bind_research_backend(
    store: StateStore,
    *,
    connector_id: str,
    mcp_server_id: str,
    capability_ids: Sequence[str] = RESEARCH_CAPABILITY_IDS,
    enabled: bool = True,
) -> list[CapabilityBinding]:
    """Bind the canonical research capabilities to one backend's MCP server.

    Every binding names an ``input_transform_ref`` and an ``output_transform_ref``:
    the canonical arguments are mapped onto this backend's wire arguments, and this
    backend's wire results are mapped back onto the one canonical output. That pair is
    the whole of the backend difference. Nothing about the capability --- its id, its
    version, its schemas --- differs between backends, which is why a workflow does not
    notice the swap.

    Re-binding is in place: a binding already pointing at this connector and server is
    updated rather than duplicated, so repeated enable cycles do not accumulate rows.
    """

    backend = research_backend(connector_id)
    bindings: list[CapabilityBinding] = []
    for capability_id in capability_ids:
        tool_name = backend.tools.get(capability_id)
        if tool_name is None:
            raise KeyError(
                f"backend {connector_id!r} declares no tool for capability {capability_id!r}"
            )
        previous = [
            item
            for item in await store.list_capability_bindings(
                capability_id=capability_id, enabled_only=False
            )
            if item.connector_id == connector_id
            and item.backend.server_ref == mcp_server_id
        ]
        binding = CapabilityBinding(
            binding_id=previous[0].binding_id if previous else new_id("capbind"),
            capability_id=capability_id,
            connector_id=connector_id,
            backend=CapabilityBindingBackend(
                type=CapabilityBackend.MCP,
                server_ref=mcp_server_id,
                method="tools/call",
                tool_name=tool_name,
            ),
            input_transform_ref=backend.input_refs[capability_id],
            output_transform_ref=backend.output_refs[capability_id],
            enabled=enabled,
        )
        await store.upsert_capability_binding(binding)
        bindings.append(binding)
    return bindings
