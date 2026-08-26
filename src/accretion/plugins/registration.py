"""Pure projections from a plugin manifest onto registry objects.

Nothing here touches the store, decides policy, or mutates state: every function is a
total, side-effect-free map from *(manifest, decisions)* to the objects the registry
already understands. ADR3-006 keeps the authority in :mod:`accretion.governance`; this
module only renders what that authority allowed.

Two conventions matter and are enforced rather than documented:

* the ``"accretion"`` key of ``Capability.provider_projections`` is the M3 metadata
  escape hatch (``mcp/manager.py``) — plugin-contributed capabilities record their
  owning ``plugin_id`` and ``installation_id`` there, and the resolver reads it back;
* the canonical capability id is never rewritten by a provider projection. A projection
  naming an id outside the plugin's own capabilities is rejected at install (AC3-PLG-06).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from pydantic import Field

from accretion.contracts import (
    Capability,
    CapabilityBinding,
    CapabilityBindingBackend,
    ConnectionScope,
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
    MetaPlugin,
    MetaPluginManifest,
    MetaSkill,
    PluginAuditEvent,
    PluginCapabilityDecision,
    PluginCapabilityGrant,
    PluginInstallation,
    PluginRef,
    PluginTrustLevel,
    PluginVersionRecord,
    StrictModel,
)
from accretion.ids import new_id
from accretion.plugins.errors import PluginManifestError

ACCRETION_PROJECTION_KEY = "accretion"
"""Reserved provider key carrying Accretion's own metadata about a projection."""

_TRUSTED_LEVELS = frozenset(
    {
        PluginTrustLevel.BUILTIN,
        PluginTrustLevel.WORKSPACE_APPROVED,
        PluginTrustLevel.SIGNED_THIRD_PARTY,
    }
)

_GRANTING_DECISIONS = frozenset(
    {
        PluginCapabilityDecision.GRANTED,
        PluginCapabilityDecision.DOWNGRADED_READ_ONLY,
    }
)


class PluginDetail(StrictModel):
    """The SDD 16.1 plugin detail projection, assembled from stored state."""

    schema_version: Literal["1.0"] = "1.0"
    plugin_id: str
    installation: PluginInstallation | None = None
    version_record: PluginVersionRecord | None = None
    registry_entry: MetaPlugin | None = None
    known_versions: list[PluginRef] = Field(default_factory=list)
    recent_events: list[PluginAuditEvent] = Field(default_factory=list)


def plugin_connector_id(plugin_id: str) -> str:
    """The synthetic local connector every plugin capability is bound through.

    Plugin capabilities need a binding so the resolver's per-binding plugin gate can
    run; a capability with no binding takes the ``NO_CONNECTOR_REQUIRED`` fast path and
    would stay resolvable after its plugin was disabled.
    """

    return f"conndef_plugin_{plugin_id}"


def project_connector(manifest: MetaPluginManifest) -> ConnectorDefinition:
    """The local, credential-free connector that carries this plugin's bindings."""

    return ConnectorDefinition(
        connector_id=plugin_connector_id(manifest.id),
        name=f"{manifest.name} (plugin-local)",
        kind=ConnectorKind.LOCAL,
        auth_type=ConnectorAuthType.NONE,
        connection_scope=ConnectionScope.WORKSPACE,
    )


def validate_provider_projections(manifest: MetaPluginManifest) -> None:
    """Reject projections that rename, reserve, or reach outside the package.

    Without this, "canonical capability IDs stay stable" (AC3-PLG-06) would be an
    unenforced hope: a manifest could point a provider projection at another plugin's
    capability and quietly borrow its identity.
    """

    own = {capability.capability_id for capability in manifest.capabilities}
    for capability in manifest.capabilities:
        for provider, projection in capability.provider_projections.items():
            if provider == ACCRETION_PROJECTION_KEY:
                raise PluginManifestError(
                    f"capability {capability.capability_id} may not declare the reserved "
                    f"{ACCRETION_PROJECTION_KEY!r} provider projection"
                )
            named = _projected_capability_id(projection)
            if named is None:
                continue
            if named not in own:
                raise PluginManifestError(
                    f"provider projection {provider!r} on {capability.capability_id} names "
                    f"capability {named!r}, which the plugin does not declare"
                )
            if named != capability.capability_id:
                raise PluginManifestError(
                    f"provider projection {provider!r} on {capability.capability_id} may not "
                    f"rename the canonical capability id to {named!r}"
                )


def render_provider_projection(capability: Capability, provider: str) -> dict[str, Any]:
    """Render one capability for one provider.

    Providers may differ in every surface detail they carry; the canonical id is
    stamped last and always wins, so it is byte-identical across providers.
    """

    declared = capability.provider_projections.get(provider)
    rendered: dict[str, Any] = dict(declared) if isinstance(declared, Mapping) else {}
    rendered.pop(ACCRETION_PROJECTION_KEY, None)
    rendered["capability_id"] = capability.capability_id
    return rendered


def accretion_projection(
    installation: PluginInstallation,
    decision: PluginCapabilityDecision,
) -> dict[str, Any]:
    """The metadata the resolver reads back to gate a plugin-contributed capability."""

    return {
        "plugin_id": installation.plugin_id,
        "installation_id": installation.installation_id,
        "workspace_id": installation.workspace_id,
        "plugin_version": installation.version,
        "manifest_digest": installation.manifest_digest,
        "trust_level": installation.trust_level.value,
        "decision": decision.value,
    }


def granted_capability_ids(grants: Iterable[PluginCapabilityGrant]) -> list[str]:
    """The capability ids policy actually let through, in manifest order."""

    return [grant.capability_id for grant in grants if grant.decision in _GRANTING_DECISIONS]


def project_capabilities(
    manifest: MetaPluginManifest,
    grants: Sequence[PluginCapabilityGrant],
    installation: PluginInstallation,
) -> list[Capability]:
    """Project the *granted* capabilities only. A denied capability is never rendered."""

    by_id = {grant.capability_id: grant for grant in grants}
    projected: list[Capability] = []
    for capability in manifest.capabilities:
        grant = by_id.get(capability.capability_id)
        if grant is None or grant.decision not in _GRANTING_DECISIONS:
            continue
        update: dict[str, Any] = {
            "required_permissions": list(grant.granted_permissions),
            "enabled": True,
            "provider_projections": {
                **capability.provider_projections,
                ACCRETION_PROJECTION_KEY: accretion_projection(installation, grant.decision),
            },
        }
        if grant.decision is PluginCapabilityDecision.DOWNGRADED_READ_ONLY:
            # A downgraded capability keeps its identity and loses its teeth: the
            # side effects it was not granted the permissions to perform.
            update["side_effects"] = []
        projected.append(capability.model_copy(update=update))
    return projected


def project_bindings(
    capabilities: Sequence[Capability],
    installation: PluginInstallation,
    *,
    existing: Mapping[str, str] | None = None,
) -> list[CapabilityBinding]:
    """One binding per granted capability, through the plugin's local connector.

    ``existing`` maps ``capability_id -> binding_id`` so an upgrade rebinds in place
    rather than accumulating a new binding row per version.
    """

    known = dict(existing or {})
    return [
        CapabilityBinding(
            binding_id=known.get(capability.capability_id) or new_id("capbind"),
            capability_id=capability.capability_id,
            connector_id=plugin_connector_id(installation.plugin_id),
            backend=CapabilityBindingBackend(type=capability.backend),
            enabled=True,
        )
        for capability in capabilities
    ]


def project_skills(manifest: MetaPluginManifest, granted: Sequence[str]) -> list[MetaSkill]:
    """Skills whose required capabilities all survived policy.

    A skill that instructs an agent to call a denied capability is not a partial win;
    registering it would advertise authority the plugin does not have.
    """

    allowed = set(granted)
    return [
        skill
        for skill in manifest.skills
        if set(skill.required_capabilities) <= allowed
    ]


def project_registry_entry(
    manifest: MetaPluginManifest,
    grants: Sequence[PluginCapabilityGrant],
    digest: str,
    *,
    trust_level: PluginTrustLevel = PluginTrustLevel.UNVERIFIED_DEV,
) -> MetaPlugin:
    """Project the manifest onto the narrow, immutable ``plugins`` registry row.

    ``MetaPlugin`` is byte-identical to its v0.1 shape and rows are immutable per
    ``(plugin_id, version)``, so nothing mutable — no lifecycle state — may appear
    here. State lives in ``plugin_installations``.
    """

    granted = granted_capability_ids(grants)
    return MetaPlugin(
        plugin_id=manifest.id,
        version=manifest.version,
        description=manifest.description,
        capability_refs=granted,
        skill_refs=[skill.skill_id for skill in project_skills(manifest, granted)],
        verifier_refs=list(manifest.verifiers),
        policy_refs=list(manifest.policies),
        provider_projections={
            **manifest.provider_projections,
            ACCRETION_PROJECTION_KEY: {
                "manifest_digest": digest,
                "trust_level": trust_level.value,
                "declared_capability_ids": [
                    capability.capability_id for capability in manifest.capabilities
                ],
                "granted_capability_ids": granted,
            },
        },
        checksum=digest,
        allowlisted=trust_level in _TRUSTED_LEVELS,
    )


def _projected_capability_id(projection: Any) -> str | None:
    if not isinstance(projection, Mapping):
        return None
    named = projection.get("capability_id")
    return named if isinstance(named, str) else None
