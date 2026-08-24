"""Connection-aware capability resolution (v0.3 M0, SDD §12.3).

The resolver sits between the capability registry and every consumer that
plans or executes capabilities. Capabilities without a connector binding
resolve exactly as they did in v0.1/v0.2 (`NO_CONNECTOR_REQUIRED`), which is
the M0 compatibility guarantee. Runtimes only ever see `ConnectionRef`
(INV3-003) — never token material.
"""

from __future__ import annotations

from accretion.contracts import (
    Capability,
    CapabilityBinding,
    CapabilityResolutionOutcome,
    Connection,
    ConnectionRef,
    ConnectionScope,
    ConnectionStatus,
    ConnectorAuthType,
    ConnectorDefinition,
    ResolvedCapability,
)
from accretion.persistence.store import StateStore

_USABLE_STATUSES = {ConnectionStatus.ACTIVE, ConnectionStatus.DEGRADED}


def _connection_ref(connection: Connection) -> ConnectionRef:
    return ConnectionRef(
        connection_id=connection.connection_id,
        connector_id=connection.connector_id,
        status=connection.status,
    )


class CapabilityResolver:
    def __init__(self, store: StateStore) -> None:
        self.store = store

    async def resolve(
        self,
        capability_id: str,
        *,
        version: str | None = None,
        principal_id: str | None = None,
        workspace_id: str | None = None,
    ) -> ResolvedCapability | None:
        capability = await self.store.get_capability(capability_id, version)
        if capability is None:
            return None
        return await self.resolve_capability(
            capability, principal_id=principal_id, workspace_id=workspace_id
        )

    async def resolve_capability(
        self,
        capability: Capability,
        *,
        principal_id: str | None = None,
        workspace_id: str | None = None,
    ) -> ResolvedCapability:
        if not capability.enabled:
            return ResolvedCapability(
                capability=capability,
                outcome=CapabilityResolutionOutcome.DISABLED,
                reason="capability is disabled",
            )
        bindings = await self.store.list_capability_bindings(
            capability_id=capability.capability_id
        )
        if not bindings:
            return ResolvedCapability(
                capability=capability,
                outcome=CapabilityResolutionOutcome.NO_CONNECTOR_REQUIRED,
                reason="capability has no connector binding",
            )
        # A capability may be bound to several connectors. Try each in the store's
        # deterministic order and take the first that fully resolves, rather than
        # silently considering only one and reporting the rest as unavailable.
        attempts = [
            await self._resolve_binding(
                capability, binding, principal_id=principal_id, workspace_id=workspace_id
            )
            for binding in bindings
        ]
        for attempt in attempts:
            if attempt.outcome is CapabilityResolutionOutcome.OK:
                return attempt
        return attempts[0]

    async def _resolve_binding(
        self,
        capability: Capability,
        binding: CapabilityBinding,
        *,
        principal_id: str | None,
        workspace_id: str | None,
    ) -> ResolvedCapability:
        connector = await self.store.get_connector_definition(binding.connector_id)
        if connector is None:
            return ResolvedCapability(
                capability=capability,
                outcome=CapabilityResolutionOutcome.NO_CONNECTION,
                binding=binding,
                reason=f"connector {binding.connector_id} is not registered",
            )
        connection = await self._select_connection(
            connector, principal_id=principal_id, workspace_id=workspace_id
        )
        if connection is None:
            return ResolvedCapability(
                capability=capability,
                outcome=CapabilityResolutionOutcome.NO_CONNECTION,
                binding=binding,
                reason=f"no usable connection for connector {connector.connector_id}",
            )
        if connection.status not in _USABLE_STATUSES:
            return ResolvedCapability(
                capability=capability,
                outcome=CapabilityResolutionOutcome.REQUIRE_REAUTH,
                binding=binding,
                connection=_connection_ref(connection),
                reason=f"connection status is {connection.status.value}",
            )
        missing = set(connector.default_scopes) - set(connection.granted_scopes)
        if missing:
            # Insufficient scopes never expand silently (AC3-CON-03).
            return ResolvedCapability(
                capability=capability,
                outcome=CapabilityResolutionOutcome.REQUIRE_REAUTH,
                binding=binding,
                connection=_connection_ref(connection),
                reason=f"missing scopes: {', '.join(sorted(missing))}",
            )
        return ResolvedCapability(
            capability=capability,
            outcome=CapabilityResolutionOutcome.OK,
            binding=binding,
            connection=_connection_ref(connection),
            reason="resolved",
        )

    async def list_resolved(
        self,
        *,
        principal_id: str | None = None,
        workspace_id: str | None = None,
        enabled_only: bool = True,
    ) -> list[ResolvedCapability]:
        return [
            await self.resolve_capability(
                capability, principal_id=principal_id, workspace_id=workspace_id
            )
            for capability in await self.store.list_capabilities(enabled_only=enabled_only)
        ]

    async def _select_connection(
        self,
        connector: ConnectorDefinition,
        *,
        principal_id: str | None,
        workspace_id: str | None,
    ) -> Connection | None:
        candidates = await self.store.list_connections(connector_id=connector.connector_id)
        if workspace_id is not None:
            candidates = [item for item in candidates if item.workspace_id == workspace_id]
        # User connection first (OQ3-03); never another principal's (INV3-008).
        user_connections = [
            item
            for item in candidates
            if item.scope == ConnectionScope.USER
            and principal_id is not None
            and item.principal_id == principal_id
        ]
        if user_connections:
            return self._best(user_connections)
        # Workspace connection second, only when explicitly shareable (INV3-009).
        workspace_connections = [
            item
            for item in candidates
            if item.scope == ConnectionScope.WORKSPACE and item.workspace_shareable
        ]
        if workspace_connections:
            return self._best(workspace_connections)
        # Anonymous local-first mode: only a connector that needs no credential may
        # fall back to an unowned connection, and only one that carries no token
        # handle. Post-M1 every API request has a principal, so this path exists for
        # the credential-free gateway subprocess alone.
        if principal_id is None and connector.auth_type is ConnectorAuthType.NONE:
            unowned = [
                item
                for item in candidates
                if item.principal_id is None and item.token_handle_ref is None
            ]
            if unowned:
                return self._best(unowned)
        return None

    @staticmethod
    def _best(connections: list[Connection]) -> Connection:
        usable = [item for item in connections if item.status in _USABLE_STATUSES]
        pool = usable or connections
        return sorted(pool, key=lambda item: item.connection_id)[0]
