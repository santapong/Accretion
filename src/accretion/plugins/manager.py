"""The plugin lifecycle boundary (SDD 9.3, 20.3; ADR3-006).

`PluginManager` mirrors `RemoteMcpManager`: one object owning registration, lifecycle,
and the governed projection of a package onto the capability registry. Two structural
properties are deliberate and load-bearing:

* **Every** state change routes through the single private ``_transition``, which
  validates the edge against ``_ALLOWED_TRANSITIONS``, persists, and only then appends
  a ``PluginAuditEvent``. SDD 20.3's "all transitions emit audit events" is therefore a
  property of the code shape, not of per-call-site discipline.
* Nothing here deletes. ``disable`` and ``remove`` flip ``Capability.enabled`` and
  ``CapabilityBinding.enabled`` for ids this installation registered, and the module
  calls no store deletion method at all (AC3-PLG-05).

Authority is never computed here. Grants come from the existing
:class:`~accretion.governance.CapabilityPolicyEngine`; this module supplies the probe
and records the verdict.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from accretion.contracts import (
    ApprovalRecord,
    ApprovalStatus,
    AuthorizationOutcome,
    Capability,
    CapabilityPolicy,
    CapabilityRequest,
    Connection,
    ConnectionScope,
    ConnectionStatus,
    McpServerDefinition,
    MetaPlugin,
    MetaPluginManifest,
    MetaSkill,
    PluginAuditEvent,
    PluginCapabilityDecision,
    PluginCapabilityGrant,
    PluginConsent,
    PluginInstallation,
    PluginState,
    PluginTrustLevel,
    PluginVersionRecord,
    RiskLevel,
    Task,
    TaskEnvelope,
)
from accretion.governance import CapabilityPolicyEngine
from accretion.ids import new_id
from accretion.mcp.manager import RemoteMcpManager
from accretion.persistence.store import StateStore
from accretion.plugins.dependencies import (
    resolve_connector_requirements,
    unsatisfied_required_connectors,
)
from accretion.plugins.errors import (
    PluginManagerError,
    PluginManifestError,
    PluginPolicyDenied,
)
from accretion.plugins.manifest import canonical_manifest_digest, parse_manifest
from accretion.plugins.registration import (
    PluginDetail,
    granted_capability_ids,
    plugin_connector_id,
    project_bindings,
    project_capabilities,
    project_connector,
    project_registry_entry,
    project_skills,
    validate_provider_projections,
)
from accretion.plugins.trust import PluginTrustVerifier

BUNDLED_ROOT = Path(__file__).parent / "bundled"
"""Where the packages that ship inside the wheel live."""

MANIFEST_FILENAME = "plugin.json"

REASON_GRANTED = "PLUGIN_CAPABILITY_GRANTED"
REASON_POLICY_DENIED = "PLUGIN_CAPABILITY_DENIED_BY_POLICY"
REASON_PERMISSIONS_MISSING = "PLUGIN_PERMISSIONS_NOT_GRANTED"
REASON_DOWNGRADED = "PLUGIN_CAPABILITY_DOWNGRADED_READ_ONLY"

# SDD 20.3, adopted verbatim (ADR3-M4-001). REMOVED is terminal; FAILED is recoverable
# only by re-validating the package, which is what a retried install does.
_ALLOWED_TRANSITIONS: dict[PluginState, frozenset[PluginState]] = {
    PluginState.DISCOVERED: frozenset({PluginState.VALIDATING, PluginState.FAILED}),
    PluginState.VALIDATING: frozenset(
        {PluginState.INSTALLED, PluginState.FAILED, PluginState.REMOVED}
    ),
    PluginState.INSTALLED: frozenset(
        {
            PluginState.SETUP_REQUIRED,
            PluginState.READY,
            PluginState.FAILED,
            PluginState.REMOVED,
        }
    ),
    PluginState.SETUP_REQUIRED: frozenset(
        {
            PluginState.VALIDATING,
            PluginState.READY,
            PluginState.DISABLED,
            PluginState.FAILED,
            PluginState.REMOVED,
        }
    ),
    PluginState.READY: frozenset(
        {
            PluginState.VALIDATING,
            PluginState.ENABLED,
            PluginState.DISABLED,
            PluginState.SETUP_REQUIRED,
            PluginState.FAILED,
            PluginState.REMOVED,
        }
    ),
    PluginState.ENABLED: frozenset(
        {
            PluginState.VALIDATING,
            PluginState.READY,
            PluginState.DISABLED,
            PluginState.SETUP_REQUIRED,
            PluginState.FAILED,
            PluginState.REMOVED,
        }
    ),
    PluginState.DISABLED: frozenset(
        {
            PluginState.VALIDATING,
            PluginState.READY,
            PluginState.ENABLED,
            PluginState.FAILED,
            PluginState.REMOVED,
        }
    ),
    PluginState.FAILED: frozenset({PluginState.VALIDATING, PluginState.REMOVED}),
    PluginState.REMOVED: frozenset(),
}

_LIVE_STATES = frozenset(
    {
        PluginState.INSTALLED,
        PluginState.SETUP_REQUIRED,
        PluginState.READY,
        PluginState.ENABLED,
        PluginState.DISABLED,
    }
)


class PluginPackageSource(Protocol):
    """Where a package reference is read from. The single seam for future formats."""

    async def read_manifest(self, reference: str) -> Mapping[str, Any] | str | bytes: ...


class DirectoryPluginSource:
    """Read ``<root>/<reference>/plugin.json`` from one or more package roots.

    ``reference`` is a single path segment by construction: anything with a separator,
    a drive, or a parent reference is refused before it touches the filesystem, so a
    reference can never escape a configured root.
    """

    def __init__(self, roots: Sequence[Path] = (BUNDLED_ROOT,)) -> None:
        self.roots = [Path(root) for root in roots]

    async def read_manifest(self, reference: str) -> Mapping[str, Any] | str | bytes:
        segment = reference.strip()
        if not segment or segment in {".", ".."} or Path(segment).name != segment:
            raise PluginManifestError(f"{reference!r} is not a valid package reference")
        for root in self.roots:
            candidate = root / segment / MANIFEST_FILENAME
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        raise PluginManifestError(f"no package named {reference!r} is available")


class PluginManager:
    """Install, enable, disable, upgrade, roll back, and remove plugin packages."""

    def __init__(
        self,
        *,
        store: StateStore,
        trust_verifier: PluginTrustVerifier,
        policy_engine: CapabilityPolicyEngine,
        source: PluginPackageSource | None = None,
        remote_mcp: RemoteMcpManager | None = None,
        policy_id: str = "local-capability-policy",
        protected_plugin_ids: Sequence[str] = ("accretion-core-governance",),
    ) -> None:
        self.store = store
        self.trust_verifier = trust_verifier
        self.policy_engine = policy_engine
        self.source = source or DirectoryPluginSource()
        self.remote_mcp = remote_mcp
        self.policy_id = policy_id
        self.protected_plugin_ids = frozenset(protected_plugin_ids)

    # ------------------------------------------------------------------ queries

    async def list_installations(self, workspace_id: str | None = None) -> list[PluginInstallation]:
        return await self.store.list_plugin_installations(workspace_id)

    async def detail(self, plugin_id: str, *, workspace_id: str | None = None) -> PluginDetail:
        installation = (
            await self.store.get_plugin_installation(workspace_id, plugin_id)
            if workspace_id is not None
            else None
        )
        versions = await self.store.list_plugin_versions(plugin_id)
        version_record = (
            await self.store.get_plugin_version(plugin_id, installation.version)
            if installation is not None
            else (versions[-1] if versions else None)
        )
        registry_entry = (
            await self._registry_entry(plugin_id, version_record.version)
            if version_record is not None
            else None
        )
        if not versions and registry_entry is None and installation is None:
            raise KeyError(plugin_id)
        return PluginDetail(
            plugin_id=plugin_id,
            installation=installation,
            version_record=version_record,
            registry_entry=registry_entry,
            known_versions=[record.to_ref() for record in versions],
            recent_events=await self.store.list_plugin_audit_events(
                plugin_id=plugin_id,
                installation_id=installation.installation_id if installation else None,
            ),
        )

    # ------------------------------------------------------------------ install

    async def install(
        self,
        reference: str,
        *,
        workspace_id: str,
        principal_id: str,
        consent_digest: str,
        consent_capability_ids: Sequence[str],
        expected_digest: str | None = None,
        correlation_id: str | None = None,
    ) -> PluginInstallation:
        """Run the SDD 9.3 install sequence, in order, for one workspace."""

        manifest = await self._read(reference)
        existing = await self.store.get_plugin_installation(workspace_id, manifest.id)
        if existing is not None and existing.state in _LIVE_STATES:
            raise PluginManagerError(
                f"plugin {manifest.id} is already installed in workspace {workspace_id}"
            )
        installation = PluginInstallation(
            installation_id=(
                existing.installation_id if existing is not None else new_id("plugin_installation")
            ),
            workspace_id=workspace_id,
            plugin_id=manifest.id,
            version=manifest.version,
            manifest_digest=canonical_manifest_digest(manifest),
            state=PluginState.DISCOVERED,
            requested_capability_ids=[item.capability_id for item in manifest.capabilities],
            installed_by_principal_id=principal_id,
            revision=(existing.revision + 1) if existing is not None else 1,
        )
        await self.store.upsert_plugin_installation(installation)
        await self._audit(
            installation,
            "DISCOVERED",
            principal_id,
            correlation_id,
            {"reference": reference, "version": manifest.version},
            from_state=existing.state if existing is not None else None,
        )
        return await self._apply_version(
            installation,
            manifest,
            principal_id=principal_id,
            consent_digest=consent_digest,
            consent_capability_ids=consent_capability_ids,
            expected_digest=expected_digest,
            correlation_id=correlation_id,
            event_prefix="INSTALL",
        )

    async def upgrade(
        self,
        plugin_id: str,
        reference: str,
        *,
        workspace_id: str,
        principal_id: str,
        consent_digest: str,
        consent_capability_ids: Sequence[str],
        expected_digest: str | None = None,
        correlation_id: str | None = None,
    ) -> PluginInstallation:
        """Move an installation to a new version, re-running the whole policy evaluation.

        The re-run is the point: a v1.1 manifest that adds ``secrets.read`` must not
        inherit v1.0's decision. Nothing about the old version is edited or deleted —
        its ``plugin_versions`` row stays byte-identical for historical traces.
        """

        installation = await self._live(workspace_id, plugin_id)
        self._reject_protected(plugin_id, "upgraded")
        manifest = await self._read(reference)
        if manifest.id != plugin_id:
            raise PluginManagerError(
                f"package {reference!r} declares plugin {manifest.id}, not {plugin_id}"
            )
        if manifest.version == installation.version:
            raise PluginManagerError(
                f"plugin {plugin_id} is already at version {installation.version}"
            )
        return await self._apply_version(
            installation.model_copy(
                update={
                    "previous_version": installation.version,
                    "version": manifest.version,
                    "manifest_digest": canonical_manifest_digest(manifest),
                    "requested_capability_ids": [
                        item.capability_id for item in manifest.capabilities
                    ],
                }
            ),
            manifest,
            principal_id=principal_id,
            consent_digest=consent_digest,
            consent_capability_ids=consent_capability_ids,
            expected_digest=expected_digest,
            correlation_id=correlation_id,
            event_prefix="UPGRADE",
        )

    async def rollback(
        self,
        plugin_id: str,
        *,
        workspace_id: str,
        principal_id: str,
        correlation_id: str | None = None,
    ) -> PluginInstallation:
        """Return the installation to ``previous_version`` from the immutable registry."""

        installation = await self._live(workspace_id, plugin_id)
        target = installation.previous_version
        if target is None:
            raise PluginManagerError(f"plugin {plugin_id} has no previous version to roll back to")
        record = await self.store.get_plugin_version(plugin_id, target)
        if record is None:
            raise PluginManagerError(
                f"plugin {plugin_id}@{target} is not in the version registry"
            )
        return await self._apply_version(
            installation.model_copy(
                update={
                    "previous_version": installation.version,
                    "version": record.version,
                    "manifest_digest": record.manifest_digest,
                    "requested_capability_ids": [
                        item.capability_id for item in record.manifest.capabilities
                    ],
                }
            ),
            record.manifest,
            principal_id=principal_id,
            consent_digest=record.manifest_digest,
            consent_capability_ids=[
                item.capability_id for item in record.manifest.capabilities
            ],
            expected_digest=record.manifest_digest,
            correlation_id=correlation_id,
            event_prefix="ROLLBACK",
        )

    # ---------------------------------------------------------------- lifecycle

    async def enable(
        self,
        plugin_id: str,
        *,
        workspace_id: str,
        principal_id: str,
        correlation_id: str | None = None,
    ) -> PluginInstallation:
        installation = await self._live(workspace_id, plugin_id)
        if installation.state is PluginState.ENABLED:
            return installation
        denied = [
            grant.capability_id
            for grant in installation.capability_grants
            if grant.decision is PluginCapabilityDecision.DENIED
        ]
        unsatisfied = unsatisfied_required_connectors(installation.connector_resolutions)
        if unsatisfied:
            raise PluginManagerError(
                f"plugin {plugin_id} still needs connector(s): {', '.join(unsatisfied)}"
            )
        await self._set_registration_enabled(installation, enabled=True)
        return await self._transition(
            installation,
            PluginState.ENABLED,
            "ENABLED",
            principal_id,
            {"denied_capability_ids": denied},
            correlation_id=correlation_id,
        )

    async def disable(
        self,
        plugin_id: str,
        *,
        workspace_id: str,
        principal_id: str,
        correlation_id: str | None = None,
    ) -> PluginInstallation:
        """Flip this installation's registrations off. Nothing is deleted (AC3-PLG-03)."""

        installation = await self._live(workspace_id, plugin_id)
        if installation.state is PluginState.DISABLED:
            return installation
        report = await self._set_registration_enabled(installation, enabled=False)
        return await self._transition(
            installation,
            PluginState.DISABLED,
            "DISABLED",
            principal_id,
            {"capability_ids": list(installation.registered_capability_ids), **report},
            correlation_id=correlation_id,
        )

    async def remove(
        self,
        plugin_id: str,
        *,
        workspace_id: str,
        principal_id: str,
        force: bool = False,
        correlation_id: str | None = None,
    ) -> PluginInstallation:
        """Retire an installation without destroying a single row.

        Removal is a lifecycle transition, not a deletion: capabilities and bindings
        are disabled, and every artifact, event, and capability result from prior runs
        is left exactly where it was (AC3-PLG-05). This method calls no store deletion
        method, and the module as a whole exposes none.
        """

        installation = await self._live(workspace_id, plugin_id)
        self._reject_protected(plugin_id, "removed")
        if installation.state is PluginState.ENABLED and not force:
            raise PluginManagerError(
                f"plugin {plugin_id} is enabled; disable it first or pass force=True"
            )
        report = await self._set_registration_enabled(installation, enabled=False)
        return await self._transition(
            installation,
            PluginState.REMOVED,
            "REMOVED",
            principal_id,
            {
                "forced": force,
                "capability_ids": list(installation.registered_capability_ids),
                "evidence_deleted": False,
                **report,
            },
            correlation_id=correlation_id,
        )

    # ------------------------------------------------------------------ internals

    async def _read(self, reference: str) -> MetaPluginManifest:
        manifest = parse_manifest(await self.source.read_manifest(reference))
        validate_provider_projections(manifest)
        return manifest

    async def _registry_entry(self, plugin_id: str, version: str) -> MetaPlugin | None:
        """The narrow ``plugins`` row for one version, allowlisted or not."""

        for entry in await self.store.list_plugins(allowlisted_only=False):
            if entry.plugin_id == plugin_id and entry.version == version:
                return entry
        return None

    async def _live(self, workspace_id: str, plugin_id: str) -> PluginInstallation:
        installation = await self.store.get_plugin_installation(workspace_id, plugin_id)
        if installation is None or installation.state not in _LIVE_STATES:
            raise KeyError(plugin_id)
        return installation

    def _reject_protected(self, plugin_id: str, verb: str) -> None:
        if plugin_id in self.protected_plugin_ids:
            raise PluginManagerError(f"built-in plugin {plugin_id} cannot be {verb}")

    async def _apply_version(
        self,
        installation: PluginInstallation,
        manifest: MetaPluginManifest,
        *,
        principal_id: str,
        consent_digest: str,
        consent_capability_ids: Sequence[str],
        expected_digest: str | None,
        correlation_id: str | None,
        event_prefix: str,
    ) -> PluginInstallation:
        """The SDD 9.3 sequence, shared by install, upgrade, and rollback.

        Ordering is the security property: the complete grant set is computed *before*
        anything is registered, and consent is taken against the digest that was
        actually shown.
        """

        digest = canonical_manifest_digest(manifest)
        installation = await self._transition(
            installation,
            PluginState.VALIDATING,
            f"{event_prefix}_VALIDATING",
            principal_id,
            {"version": manifest.version, "manifest_digest": digest},
            correlation_id=correlation_id,
        )
        try:
            trust_level = self.trust_verifier.verify_for_install(
                manifest, expected_digest=expected_digest
            )
        except PluginManagerError as error:
            await self._fail(installation, principal_id, str(error), correlation_id)
            raise

        workspace_connections = [
            connection
            for connection in await self.store.list_connections()
            if connection.workspace_id == installation.workspace_id
        ]
        connections = {
            connection.connector_id: connection.connection_id
            for connection in workspace_connections
            if connection.status in {ConnectionStatus.ACTIVE, ConnectionStatus.DEGRADED}
        }
        granted_scopes = {
            connection.connector_id: list(connection.granted_scopes)
            for connection in workspace_connections
        }
        try:
            resolutions = resolve_connector_requirements(
                manifest, connections=connections, granted_scopes=granted_scopes
            )
        except PluginManagerError as error:
            await self._fail(installation, principal_id, str(error), correlation_id)
            raise

        # The complete grant set, computed before a single object is registered.
        grants = await self._evaluate_grants(manifest)
        granted = granted_capability_ids(grants)
        try:
            consent = self._consent(
                manifest,
                digest,
                principal_id=principal_id,
                consent_digest=consent_digest,
                consent_capability_ids=consent_capability_ids,
                granted=granted,
            )
        except PluginManagerError as error:
            await self._fail(installation, principal_id, str(error), correlation_id)
            raise

        installation = installation.model_copy(
            update={
                "version": manifest.version,
                "manifest_digest": digest,
                "trust_level": trust_level,
                "requested_capability_ids": [
                    item.capability_id for item in manifest.capabilities
                ],
                "capability_grants": grants,
                "connector_resolutions": resolutions,
                "consent": consent,
                "failure_reason": None,
            }
        )

        if manifest.capabilities and not granted:
            # Every requested capability was refused: there is nothing left to install.
            await self.store.upsert_plugin_installation(installation)
            return await self._fail(
                installation,
                principal_id,
                "policy denied every requested capability",
                correlation_id,
                details={"denied_capability_ids": installation.requested_capability_ids},
            )

        await self._record_version(manifest, digest, trust_level)
        try:
            installation = await self._register(installation, manifest, grants, trust_level)
            mcp_server_ids = await self._register_mcp_servers(
                installation, manifest, principal_id=principal_id
            )
        except Exception as error:
            # Deliberately broad: registration reaches M3's endpoint policy and the
            # store, whose failures are not PluginManagerError. Whatever refuses the
            # package, the installation must not be left mid-validation.
            await self._fail(installation, principal_id, str(error), correlation_id)
            raise
        installation = installation.model_copy(
            update={"registered_mcp_server_ids": mcp_server_ids}
        )
        installation = await self._transition(
            installation,
            PluginState.INSTALLED,
            f"{event_prefix}_INSTALLED",
            principal_id,
            {
                "granted_capability_ids": granted,
                "registered_mcp_server_ids": mcp_server_ids,
                "trust_level": trust_level.value,
            },
            correlation_id=correlation_id,
        )

        unsatisfied = unsatisfied_required_connectors(resolutions)
        if unsatisfied:
            # A missing required connector is a setup gap, not a failure. Registrations
            # stay in place so the resolver can report the gap precisely; the plugin
            # gate refuses execution while the installation sits in SETUP_REQUIRED.
            return await self._transition(
                installation,
                PluginState.SETUP_REQUIRED,
                "SETUP_REQUIRED",
                principal_id,
                {"missing_connectors": unsatisfied},
                correlation_id=correlation_id,
            )

        installation = await self._transition(
            installation,
            PluginState.READY,
            "READY",
            principal_id,
            {"granted_capability_ids": granted},
            correlation_id=correlation_id,
        )
        withheld = [
            grant.capability_id
            for grant in grants
            if grant.decision is not PluginCapabilityDecision.GRANTED
        ]
        if withheld:
            # AC3-PLG-02: a plugin that asked for more than policy allows never gains
            # the authority automatically. It lands disabled and waits for an admin.
            await self._set_registration_enabled(installation, enabled=False)
            return await self._transition(
                installation,
                PluginState.DISABLED,
                "DISABLED_BY_POLICY",
                principal_id,
                {"withheld_capability_ids": withheld},
                correlation_id=correlation_id,
            )
        await self._set_registration_enabled(installation, enabled=True)
        return await self._transition(
            installation,
            PluginState.ENABLED,
            "ENABLED",
            principal_id,
            {"granted_capability_ids": granted},
            correlation_id=correlation_id,
        )

    async def _record_version(
        self,
        manifest: MetaPluginManifest,
        digest: str,
        trust_level: PluginTrustLevel,
    ) -> PluginVersionRecord:
        """Write the immutable registry entry, or confirm the existing one is identical.

        ``(plugin_id, version)`` is immutable by construction, so a rollback re-reaches
        a row that already exists. Re-upserting it would raise on the surrogate id
        alone; drift in the manifest *content* must still raise, and does.
        """

        existing = await self.store.get_plugin_version(manifest.id, manifest.version)
        if existing is not None:
            if existing.manifest_digest != digest:
                raise PluginManagerError(
                    f"plugin {manifest.id}@{manifest.version} is already registered with "
                    f"digest {existing.manifest_digest}; publish a new version"
                )
            return existing
        return await self.store.upsert_plugin_version(
            PluginVersionRecord(
                plugin_version_id=new_id("plugin_version"),
                plugin_id=manifest.id,
                version=manifest.version,
                manifest_digest=digest,
                trust_level=trust_level,
                manifest=manifest,
            )
        )

    def _consent(
        self,
        manifest: MetaPluginManifest,
        digest: str,
        *,
        principal_id: str,
        consent_digest: str,
        consent_capability_ids: Sequence[str],
        granted: Sequence[str],
    ) -> PluginConsent:
        """Bind consent to the exact bytes the administrator was shown."""

        if consent_digest != digest:
            raise PluginPolicyDenied(
                f"consent echoes manifest digest {consent_digest}, but the package digests "
                f"to {digest}"
            )
        requested = {item.capability_id for item in manifest.capabilities}
        unknown = sorted(set(consent_capability_ids) - requested)
        if unknown:
            raise PluginPolicyDenied(
                f"consent names capabilities the manifest never requested: {', '.join(unknown)}"
            )
        excess = sorted(set(consent_capability_ids) - set(granted))
        if excess:
            # Consent may narrow what policy allowed; it may never widen it.
            raise PluginPolicyDenied(
                f"consent exceeds the capabilities policy granted: {', '.join(excess)}"
            )
        return PluginConsent(
            granted_by_principal_id=principal_id,
            manifest_digest=digest,
            granted_capability_ids=sorted(consent_capability_ids),
        )

    async def _evaluate_grants(
        self, manifest: MetaPluginManifest
    ) -> list[PluginCapabilityGrant]:
        """Ask the existing policy engine what each requested capability may have.

        No policy logic lives here. Each capability is put to
        ``CapabilityPolicyEngine.authorize`` under a probe envelope that allows exactly
        what the manifest requested, with an approved record supplied so that per-call
        approval — the gateway's job at execution time — does not masquerade as an
        install-time denial.
        """

        policy = await self.store.get_capability_policy(self.policy_id)
        if policy is None:
            raise PluginManagerError(f"capability policy {self.policy_id!r} is unavailable")
        grants: list[PluginCapabilityGrant] = []
        for capability in manifest.capabilities:
            grants.append(self._evaluate_capability(manifest, capability, policy))
        return grants

    def _evaluate_capability(
        self,
        manifest: MetaPluginManifest,
        capability: Capability,
        policy: CapabilityPolicy,
    ) -> PluginCapabilityGrant:
        requested = list(capability.required_permissions)
        full = self.policy_engine.authorize(
            task=self._probe_task(manifest, capability.risk),
            capability=capability,
            request=self._probe_request(capability),
            policy=policy,
            approval=_APPROVED_PROBE,
        )
        if full.outcome is not AuthorizationOutcome.DENY:
            return PluginCapabilityGrant(
                capability_id=capability.capability_id,
                requested_permissions=requested,
                granted_permissions=requested,
                decision=PluginCapabilityDecision.GRANTED,
                reason=f"{REASON_GRANTED}: {full.reason}",
            )
        missing = sorted(set(requested) - set(self.policy_engine.granted_permissions))
        if not missing:
            # Denied for a reason a narrower grant cannot cure: an explicit denylist.
            return PluginCapabilityGrant(
                capability_id=capability.capability_id,
                requested_permissions=requested,
                granted_permissions=[],
                decision=PluginCapabilityDecision.DENIED,
                reason=f"{REASON_POLICY_DENIED}: {full.reason}",
            )
        retained = [item for item in requested if item not in set(missing)]
        if not capability.side_effects or not retained:
            # Nothing to downgrade *to*: a read capability missing its permission, or a
            # capability that would retain no granted authority at all.
            return PluginCapabilityGrant(
                capability_id=capability.capability_id,
                requested_permissions=requested,
                granted_permissions=[],
                decision=PluginCapabilityDecision.DENIED,
                reason=f"{REASON_PERMISSIONS_MISSING}: {full.reason}",
            )
        read_only = capability.model_copy(
            update={"side_effects": [], "required_permissions": retained}
        )
        downgraded = self.policy_engine.authorize(
            task=self._probe_task(manifest, read_only.risk),
            capability=read_only,
            request=self._probe_request(read_only),
            policy=policy,
            approval=_APPROVED_PROBE,
        )
        if downgraded.outcome is AuthorizationOutcome.DENY:
            return PluginCapabilityGrant(
                capability_id=capability.capability_id,
                requested_permissions=requested,
                granted_permissions=[],
                decision=PluginCapabilityDecision.DENIED,
                reason=f"{REASON_PERMISSIONS_MISSING}: {downgraded.reason}",
            )
        return PluginCapabilityGrant(
            capability_id=capability.capability_id,
            requested_permissions=requested,
            granted_permissions=retained,
            decision=PluginCapabilityDecision.DOWNGRADED_READ_ONLY,
            reason=f"{REASON_DOWNGRADED}: missing {', '.join(missing)}",
        )

    def _probe_task(self, manifest: MetaPluginManifest, risk: RiskLevel) -> Task:
        return Task(
            envelope=TaskEnvelope(
                task_id=f"task_plugin_grant_{manifest.id}_{manifest.version}",
                project_id=f"project_plugin_grant_{manifest.id}",
                objective=(
                    f"Evaluate the capability grants plugin {manifest.id}@{manifest.version} "
                    "requests"
                ),
                risk_level=risk,
                allowed_capabilities=[
                    item.capability_id for item in manifest.capabilities
                ],
            )
        )

    def _probe_request(self, capability: Capability) -> CapabilityRequest:
        return CapabilityRequest(
            request_id=f"req_plugin_grant_{capability.capability_id}",
            run_id="run_plugin_grant_probe",
            node_id="node_plugin_grant_probe",
            capability_id=capability.capability_id,
            capability_version=capability.version,
            declared_reason="install-time capability grant evaluation",
            idempotency_key=f"plugin-grant-{capability.capability_id}",
        )

    async def _register(
        self,
        installation: PluginInstallation,
        manifest: MetaPluginManifest,
        grants: Sequence[PluginCapabilityGrant],
        trust_level: PluginTrustLevel,
    ) -> PluginInstallation:
        """Register granted skills and capabilities. Denied ones are never written."""

        await self._ensure_connector(installation, manifest)
        capabilities = project_capabilities(manifest, grants, installation)
        for capability in capabilities:
            # The capability registry is global and immutable per ``(id, version)``,
            # while installations are workspace-scoped. A second workspace installing
            # the same version therefore re-reaches a row that already exists and must
            # keep it: the row records which installation first contributed the
            # capability, and per-workspace authority comes from the installation
            # record the resolver looks up, not from this projection.
            stored = await self.store.get_capability(
                capability.capability_id, capability.version
            )
            if stored is not None and self._same_contribution(stored, capability):
                continue
            await self.store.upsert_capability(capability)
        existing = {
            binding.capability_id: binding.binding_id
            for binding in await self.store.list_capability_bindings(
                connector_id=project_connector(manifest).connector_id, enabled_only=False
            )
        }
        for binding in project_bindings(capabilities, installation, existing=existing):
            await self.store.upsert_capability_binding(binding)
        granted = granted_capability_ids(grants)
        skills = project_skills(manifest, granted)
        for skill in skills:
            # Skills are immutable per ``(skill_id, version)`` too, and a manifest
            # re-parse restamps ``created_at``; an identical declaration must not be
            # mistaken for drift.
            known = next(
                (
                    item
                    for item in await self.store.list_skills()
                    if item.skill_id == skill.skill_id and item.version == skill.version
                ),
                None,
            )
            if known is not None and _without_timestamp(known) == _without_timestamp(skill):
                continue
            await self.store.upsert_skill(skill)
        entry = project_registry_entry(
            manifest, grants, installation.manifest_digest, trust_level=trust_level
        )
        if await self._registry_entry(entry.plugin_id, entry.version) is None:
            # The registry row is immutable per ``(plugin_id, version)``; re-reaching an
            # existing one (a rollback) must not attempt to rewrite it.
            await self.store.upsert_plugin(entry)
        return installation.model_copy(
            update={
                "registered_capability_ids": [
                    capability.capability_id for capability in capabilities
                ],
                "registered_skill_ids": [skill.skill_id for skill in skills],
            }
        )

    @staticmethod
    def _same_contribution(existing: Capability, projected: Capability) -> bool:
        """True when a stored capability is the same plugin version as the projected one."""

        stored = existing.provider_projections.get("accretion")
        fresh = projected.provider_projections.get("accretion")
        if not isinstance(stored, dict) or not isinstance(fresh, dict):
            return False
        keys = ("plugin_id", "plugin_version", "manifest_digest", "decision")
        return all(stored.get(key) == fresh.get(key) for key in keys)

    async def _ensure_connector(
        self, installation: PluginInstallation, manifest: MetaPluginManifest
    ) -> None:
        connector = project_connector(manifest)
        if await self.store.get_connector_definition(connector.connector_id) is None:
            await self.store.upsert_connector_definition(connector)
        existing = await self.store.list_connections(connector_id=connector.connector_id)
        if any(item.workspace_id == installation.workspace_id for item in existing):
            return
        await self.store.upsert_connection(
            Connection(
                connection_id=new_id("conn"),
                connector_id=connector.connector_id,
                workspace_id=installation.workspace_id,
                scope=ConnectionScope.WORKSPACE,
                status=ConnectionStatus.ACTIVE,
                workspace_shareable=True,
            )
        )

    async def _register_mcp_servers(
        self,
        installation: PluginInstallation,
        manifest: MetaPluginManifest,
        *,
        principal_id: str,
    ) -> list[str]:
        """Register plugin-declared MCP servers disabled, through M3's own validator.

        Routing through ``RemoteMcpManager.register`` is deliberate: it applies the
        AC3-MCP-07 endpoint policy, so a plugin manifest cannot become an SSRF bypass.
        Activation stays M3's decision — servers are registered ``enabled=False``.
        """

        if not manifest.mcp_servers:
            return []
        if self.remote_mcp is None:
            raise PluginManagerError(
                f"plugin {manifest.id} declares MCP servers but no MCP manager is configured"
            )
        registered: list[str] = []
        for declaration in manifest.mcp_servers:
            payload = dict(declaration)
            payload.pop("enabled", None)
            payload.pop("state", None)
            try:
                server = McpServerDefinition(
                    mcp_server_id=new_id("mcp_server"),
                    workspace_id=installation.workspace_id,
                    owner_principal_id=principal_id,
                    **payload,
                )
            except (TypeError, ValueError) as error:
                raise PluginManifestError(
                    f"plugin {manifest.id} declares an invalid MCP server: {error}"
                ) from error
            stored = await self.remote_mcp.register(server)
            registered.append(stored.mcp_server_id)
        return registered

    async def _set_registration_enabled(
        self, installation: PluginInstallation, *, enabled: bool
    ) -> dict[str, Any]:
        """Flip only the ids this installation registered. Nothing is ever deleted.

        Two mechanisms, because one of them is not always available. Capability
        *bindings* are mutable and are the primary switch. Capability rows are
        immutable per ``(capability_id, version)`` (``store.upsert_capability`` raises
        on any drift), so on a store that already holds the row the ``enabled`` flip is
        refused; that is recorded rather than swallowed, and authority does not depend
        on it — the resolver's plugin gate keys off installation state, so a capability
        left ``enabled=True`` by an immutable registry still stops resolving.
        """

        registered = set(installation.registered_capability_ids)
        flipped: list[str] = []
        immutable: list[str] = []
        for capability_id in installation.registered_capability_ids:
            capability = await self.store.get_capability(capability_id)
            if capability is None or capability.enabled == enabled:
                continue
            try:
                await self.store.upsert_capability(
                    capability.model_copy(update={"enabled": enabled})
                )
            except ValueError:
                immutable.append(capability_id)
                continue
            flipped.append(capability_id)
        bindings = await self.store.list_capability_bindings(
            connector_id=plugin_connector_id(installation.plugin_id), enabled_only=False
        )
        bound: list[str] = []
        for binding in bindings:
            if binding.capability_id not in registered or binding.enabled == enabled:
                continue
            await self.store.upsert_capability_binding(
                binding.model_copy(update={"enabled": enabled})
            )
            bound.append(binding.binding_id)
        return {
            "capabilities_flipped": flipped,
            "capabilities_immutable": immutable,
            "bindings_flipped": bound,
            "deleted": False,
        }

    async def _fail(
        self,
        installation: PluginInstallation,
        principal_id: str,
        reason: str,
        correlation_id: str | None,
        *,
        details: dict[str, Any] | None = None,
    ) -> PluginInstallation:
        return await self._transition(
            installation.model_copy(update={"failure_reason": reason}),
            PluginState.FAILED,
            "FAILED",
            principal_id,
            {"reason": reason, **(details or {})},
            correlation_id=correlation_id,
        )

    async def _transition(
        self,
        installation: PluginInstallation,
        to_state: PluginState,
        event_type: str,
        actor: str | None,
        details: dict[str, Any] | None = None,
        *,
        correlation_id: str | None = None,
    ) -> PluginInstallation:
        """The single door every state change goes through.

        Validate the edge, persist, then audit. Because there is exactly one such
        door, "every transition emits an audit event" is a structural fact.
        """

        from_state = installation.state
        allowed = _ALLOWED_TRANSITIONS[from_state]
        if to_state is not from_state and to_state not in allowed:
            raise PluginManagerError(
                f"plugin {installation.plugin_id} cannot move from {from_state.value} "
                f"to {to_state.value}"
            )
        updated = installation.model_copy(
            update={
                "state": to_state,
                "revision": installation.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        await self.store.upsert_plugin_installation(updated)
        await self._audit(
            updated, event_type, actor, correlation_id, details, from_state=from_state
        )
        return updated

    async def _audit(
        self,
        installation: PluginInstallation,
        event_type: str,
        actor: str | None,
        correlation_id: str | None,
        details: dict[str, Any] | None = None,
        *,
        from_state: PluginState | None,
    ) -> PluginAuditEvent:
        return await self.store.append_plugin_audit_event(
            PluginAuditEvent(
                plugin_event_id=new_id("plugin_event"),
                plugin_id=installation.plugin_id,
                installation_id=installation.installation_id,
                workspace_id=installation.workspace_id,
                event_type=event_type,
                from_state=from_state,
                to_state=installation.state,
                actor_principal_id=actor,
                correlation_id=correlation_id,
                details=json.loads(json.dumps(details or {}, default=str)),
            )
        )


def _without_timestamp(model: MetaSkill) -> dict[str, Any]:
    """A declaration compared on content, ignoring when the object was constructed."""

    return model.model_dump(mode="json", exclude={"created_at"})


_APPROVED_PROBE = ApprovalRecord(
    approval_id="apr_plugin_grant_probe",
    run_id="run_plugin_grant_probe",
    native_request_id="plugin-grant-probe",
    method="plugin/install",
    summary="Install-time grant evaluation; per-call approval remains the gateway's job.",
    status=ApprovalStatus.APPROVED,
)
