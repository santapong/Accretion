"""The registry snapshot every routing decision is made against (SDD §8.3, §9.1).

SDD §8.3 requires routing to use an *exact snapshot* of the world rather than whatever the
registry happens to say at the moment a rule runs. Two things follow, and this module exists
for both.

**A decision must be replayable.** A compatibility verdict that read the live store would be
unreproducible the instant anything changed, so every rule in
:mod:`accretion.routing.compatibility` reads :class:`RoutingSnapshot` and nothing else. The
snapshot is a frozen dataclass, built once, passed by value.

**A receipt must say what it saw.** Four digests identify the snapshot: the capability
registry, runtime availability, connection availability and the capability policy. They are
computed over deliberately narrow projections — ``(id, status, version)`` and no more —
which gives them two properties at once. They move when something routing depends on moves,
and they do not move when something it does not depend on moves: a connection's
``last_health_check`` timestamp, a runtime's ``active_sessions`` count and a capability's
``description`` all churn constantly and none of them changes a digest here. A snapshot id
that changed every second would prove nothing about two receipts that shared it.

**Connections are the reason the projections are narrow, not just the reason they are
cheap.** A connection carries ``token_handle_ref``, ``granted_scopes`` and a free-form
``metadata`` dict that a connector may fill with anything, including an access token. So the
connection projection is ``(connection_id, connector_id, status)`` — three fields chosen by
name, never ``model_dump()`` — and neither the snapshot's digests nor the dataclass itself
ever holds a ``Connection``. INV3-003 already forbids token material from reaching a runtime;
this keeps it out of the routing audit trail as well, where it would otherwise be persisted
forever inside a hash input that someone will one day want to reproduce.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from accretion.contracts import (
    AgentRuntime,
    CapabilityPolicy,
    Provider,
    ResolvedCapability,
    RuntimeHealth,
    Task,
)
from accretion.contracts.canonical import canonical_json
from accretion.persistence.store import StateStore
from accretion.resolver import CapabilityResolver
from accretion.verifiers.registry import VerifierRegistry

DEFAULT_CAPABILITY_POLICY_ID = "local-capability-policy"
"""The policy id the local deployment seeds, matching ``CapabilityPolicyEngine``'s default.

Named here rather than hard-coded in the builder so the two stay one value: a snapshot taken
against a different policy than the one governance enforces would pin the wrong authority
into every receipt derived from it.
"""

FALLBACK_BUNDLE_DIGEST_M1 = "fallback-bundle/0"
"""The placeholder digest M1 records for the audited fallback bundle.

SDD §9.2 requires a fallback that survives pruning, and M2 owns the bundle that defines it.
A snapshot taken in M1 still has to say *which* bundle it was taken against, because the
field is inside the receipt digest and adding it later would be a change no reader could
distinguish from a bundle swap. So the field exists now, carries a version string that is
honestly zero, and M2 replaces the value with the real ``fallback-bundle/1`` digest.
"""


@dataclass(frozen=True, slots=True)
class RoutingSnapshot:
    """One immutable observation of everything routing is allowed to consider.

    Frozen and slotted: a rule that mutated the snapshot it was handed would make the four
    digests describe a world that no longer exists, and the failure would surface as an
    unreproducible receipt rather than as an error.

    The four ``*_snapshot_id`` fields are what a receipt pins. The remaining fields are the
    observation itself, carried so that a rule never has to reach past the snapshot to the
    store. ``policy`` is the whole :class:`~accretion.contracts.CapabilityPolicy` because
    M1.2's gates evaluate it; ``policy_snapshot_id`` is its ``id@version`` identity, which is
    a label rather than a digest and therefore the one snapshot id that is not hex.
    """

    capability_registry_snapshot_id: str
    available_runtime_snapshot_id: str
    connection_availability_snapshot_id: str
    policy_snapshot_id: str
    capabilities: tuple[ResolvedCapability, ...]
    skills: tuple[str, ...]
    plugins: tuple[str, ...]
    verifier_ids: tuple[str, ...]
    runtime_health: tuple[RuntimeHealth, ...]
    policy: CapabilityPolicy
    fallback_bundle_digest: str
    taken_at: datetime

    def resolved(self, capability_id: str) -> ResolvedCapability | None:
        """The resolution this snapshot holds for ``capability_id``, or ``None``.

        ``None`` means *this snapshot never saw that capability*, which the compatibility
        engine turns into ``UNKNOWN`` rather than into a refusal. The distinction is the
        whole of AC4-M1-007: "the registry says no" and "the registry was not asked" are
        different answers, and only one of them may ever be argued away.
        """

        for candidate in self.capabilities:
            if candidate.capability.capability_id == capability_id:
                return candidate
        return None

    def runtime(self, runtime_id: str) -> RuntimeHealth | None:
        """The observed health of ``runtime_id`` in this snapshot, or ``None``."""

        for health in self.runtime_health:
            if health.runtime_id == runtime_id:
                return health
        return None


def _digest(sections: Mapping[str, object]) -> str:
    """SHA-256 over the canonical JSON of ``sections``.

    One function for all three digests so that they cannot drift apart in encoding, and
    :func:`~accretion.contracts.canonical.canonical_json` rather than ``json.dumps`` so that
    key order, whitespace and number formatting are fixed by the same rules the contract
    hashes use. A digest computed here is comparable with one computed anywhere else in the
    repository.
    """

    return hashlib.sha256(canonical_json(sections)).hexdigest()


class RegistrySnapshotBuilder:
    """Reads the world once and freezes it into a :class:`RoutingSnapshot`.

    The four collaborators are the four sources routing is permitted to consult, and they
    are injected rather than constructed so that a test can drive the whole builder against
    a :class:`~accretion.persistence.store.MemoryStore` and a ``FakeRuntime`` with no
    network, no database and no live provider:

    * ``store`` — the capability registry, skills, allow-listed plugins, capability
      bindings, connector definitions, connections, MCP servers and the capability policy.
    * ``resolver`` — :class:`~accretion.resolver.CapabilityResolver`, which is the one place
      that knows how a capability, a binding, a connector and a connection combine into an
      outcome. The builder wraps it rather than re-deriving the outcome, because a second
      implementation of that join would be a second answer to "is this tool usable".
    * ``runtimes`` — the provider-keyed runtime adapters, asked for ``health()``.
    * ``verifiers`` — :class:`~accretion.verifiers.registry.VerifierRegistry`, for the ids a
      verifier binding may name.

    ``policy_id`` mirrors ``CapabilityPolicyEngine.__init__``'s parameter of the same name
    so a deployment that renamed its policy renames it in one place.
    """

    def __init__(
        self,
        store: StateStore,
        resolver: CapabilityResolver,
        runtimes: Mapping[Provider, AgentRuntime],
        verifiers: VerifierRegistry,
        *,
        policy_id: str = DEFAULT_CAPABILITY_POLICY_ID,
    ) -> None:
        self.store = store
        self.resolver = resolver
        self.runtimes = runtimes
        self.verifiers = verifiers
        self.policy_id = policy_id

    async def build(
        self,
        *,
        workspace_id: str,
        project_id: str | None,
        task: Task,
        clock: Callable[[], datetime] | None = None,
    ) -> RoutingSnapshot:
        """Observe the registry, the runtimes, the connections and the policy, once.

        ``task`` names what the snapshot is being taken *for*. Its allow list, deny list and
        requested skills are authority rather than registry state — they belong to one task
        and the snapshot belongs to a workspace — so they are deliberately not folded into
        any digest here: M1.2's gates and M2's candidate builder read them from the task
        itself. What the task is used for is the one check that would otherwise never
        happen: a snapshot must not be taken for a task that belongs to another project,
        because every decision derived from it would then be filed under a project the task
        was never authorised in.

        ``clock`` is injected so ``taken_at`` is deterministic under test. It does not enter
        any digest — two snapshots of an unchanged world are the same snapshot, and a
        timestamp inside the ids would deny that.
        """

        if task.envelope.project_id != project_id:
            raise ValueError(
                f"task {task.envelope.task_id!r} belongs to project "
                f"{task.envelope.project_id!r}, so a routing snapshot may not be taken for "
                f"it under project {project_id!r}"
            )

        # `enabled_only=False` on purpose, and it is the one place this builder widens what
        # it observes. The digest below carries each capability's ENABLED/DISABLED status, so
        # nothing is lost from the identity — but filtering the disabled rows out here would
        # lose something the rules need: a capability that was switched off would be
        # *indistinguishable* from one the registry never had, and SDD §7.7 turns on exactly
        # that distinction. "The registry says no" is INCOMPATIBLE/CAPABILITY_DISABLED;
        # "the registry was not asked" is UNKNOWN, which may never be argued away. Collapsing
        # them would make `CAPABILITY_DISABLED` a code no rule could ever emit.
        capabilities = tuple(
            await self.resolver.list_resolved(workspace_id=workspace_id, enabled_only=False)
        )
        skill_rows = await self.store.list_skills()
        plugin_rows = await self.store.list_plugins(allowlisted_only=True)
        binding_rows = await self.store.list_capability_bindings(enabled_only=False)
        mcp_rows = await self.store.list_mcp_servers(workspace_id=workspace_id)
        connection_rows = await self.store.list_connections()
        connector_rows = await self.store.list_connector_definitions()

        policy = await self.store.get_capability_policy(self.policy_id)
        if policy is None:
            raise RuntimeError(
                f"capability policy {self.policy_id!r} is unavailable, so no routing "
                "snapshot can name the authority its decisions were made under"
            )

        runtime_health: list[RuntimeHealth] = []
        for provider in sorted(self.runtimes, key=lambda item: item.value):
            runtime_health.append(await self.runtimes[provider].health())

        capability_registry_snapshot_id = _digest(
            {
                "capabilities": sorted(
                    [
                        resolved.capability.capability_id,
                        "ENABLED" if resolved.capability.enabled else "DISABLED",
                        resolved.capability.version,
                    ]
                    for resolved in capabilities
                ),
                "skills": sorted(
                    [skill.skill_id, "REGISTERED", skill.version] for skill in skill_rows
                ),
                "plugins": sorted(
                    [plugin.plugin_id, "ALLOWLISTED", plugin.version] for plugin in plugin_rows
                ),
                # A binding has no version of its own, so the third slot carries what the
                # binding actually is — which capability it wires to which connector. A
                # rebinding is then a different registry, which is the point.
                "capability_bindings": sorted(
                    [
                        binding.binding_id,
                        "ENABLED" if binding.enabled else "DISABLED",
                        f"{binding.capability_id}->{binding.connector_id}",
                    ]
                    for binding in binding_rows
                ),
                # Id and state only, per the M1 design: an MCP server's endpoint, command
                # line and auth profile are configuration a routing decision does not read,
                # and two of the three can carry credentials.
                "mcp_servers": sorted(
                    [server.mcp_server_id, server.state.value] for server in mcp_rows
                ),
            }
        )
        available_runtime_snapshot_id = _digest(
            {
                "runtimes": {
                    health.provider.value: [
                        health.runtime_id,
                        health.status.value,
                        health.runtime_version,
                        sorted(health.capabilities),
                    ]
                    for health in runtime_health
                }
            }
        )
        connection_availability_snapshot_id = _digest(
            {
                # Three named fields, never `model_dump()`: `token_handle_ref`,
                # `granted_scopes` and the free-form `metadata` dict must not reach a hash
                # input that an operator will later want to reproduce.
                "connections": sorted(
                    [
                        connection.connection_id,
                        connection.connector_id,
                        connection.status.value,
                    ]
                    for connection in connection_rows
                ),
                "connectors": sorted(
                    [connector.connector_id, connector.auth_type.value]
                    for connector in connector_rows
                ),
            }
        )

        return RoutingSnapshot(
            capability_registry_snapshot_id=capability_registry_snapshot_id,
            available_runtime_snapshot_id=available_runtime_snapshot_id,
            connection_availability_snapshot_id=connection_availability_snapshot_id,
            policy_snapshot_id=f"{policy.policy_id}@{policy.version}",
            capabilities=capabilities,
            skills=tuple(sorted(skill.skill_id for skill in skill_rows)),
            plugins=tuple(sorted(plugin.plugin_id for plugin in plugin_rows)),
            verifier_ids=tuple(self.verifiers.list_ids()),
            runtime_health=tuple(runtime_health),
            policy=policy,
            fallback_bundle_digest=FALLBACK_BUNDLE_DIGEST_M1,
            taken_at=clock() if clock is not None else datetime.now(UTC),
        )


__all__ = [
    "DEFAULT_CAPABILITY_POLICY_ID",
    "FALLBACK_BUNDLE_DIGEST_M1",
    "RegistrySnapshotBuilder",
    "RoutingSnapshot",
]
