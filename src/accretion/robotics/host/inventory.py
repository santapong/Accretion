"""Explicit trusted deployment inventory; never manifest-derived permission.

Construct these immutable snapshots only from independently reviewed deployment
configuration. Source artifact references are provenance pins, not evidence that
this module inspected their contents. The composition root verifies model bundle,
static host-compatibility evidence and actual launch/inspection outside runtime
transactions. Nothing here starts a host, signs a receipt, installs configuration,
creates an approval or confirms cleanup. Revocation requires replacing the trusted
configuration snapshot; current persisted service disable/membership is rechecked
on every call in the supplied transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal, Protocol, Self, cast

from pydantic import AwareDatetime, Field, model_validator

from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics import TrustedSafetyKey
from accretion.contracts.robotics.values import (
    DependencyClosure,
    Digest,
    Identifier,
    SimulationArtifactRef,
)
from accretion.robotics.authority import AuthorityCheck
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.protocol import WireModel, bounded_json
from accretion.robotics.runtime_store import (
    RuntimeBinding,
    RuntimeEpisode,
    RuntimeLease,
    RuntimeResource,
    RuntimeTransaction,
)

from .docker import LaunchProfile

MAX_INVENTORY_ENTRIES = 128
MAX_KEYS_PER_BINDING = 16
MAX_VALIDITY = timedelta(hours=24)


class InventoryValidity(WireModel):
    valid_from: AwareDatetime
    valid_until: AwareDatetime
    disposition: Literal["ACTIVE", "REVOKED"]

    @model_validator(mode="after")
    def _interval(self) -> Self:
        if not timedelta(0) < self.valid_until - self.valid_from <= MAX_VALIDITY:
            raise ValueError("deployment inventory interval must be positive and at most 24 hours")
        canonical_json(self)
        return self


class ModelBundlePins(WireModel):
    """Configured bundle/selected-model association, independently verified at install."""

    bundle_digest: Digest
    world_digest: Digest
    robot_model_digest: Digest


class HostProfileBinding(InventoryValidity):
    workspace_id: Identifier
    project_id: Identifier
    host_principal_id: Identifier
    host_instance_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$", strict=True)
    profile: LaunchProfile
    dependencies: DependencyClosure
    model_bundle: ModelBundlePins
    host_compatibility_ref: SimulationArtifactRef
    adapter_principal_id: Identifier
    evaluator_principal_id: Identifier
    orchestrator_principal_id: Identifier

    @model_validator(mode="after")
    def _pins(self) -> Self:
        if (
            self.profile.worker_kind not in {"UR5E", "PANDA"}
            or self.profile.image_id != "sha256:" + self.dependencies.simulator_image_digest
            or self.profile.adapter_artifact_digest != self.dependencies.adapter_artifact_digest
            or self.profile.model_bundle_digest != self.model_bundle.bundle_digest
            or self.model_bundle.world_digest != self.dependencies.world_digest
            or self.model_bundle.robot_model_digest != self.dependencies.robot_model_digest
            or self.host_compatibility_ref.digest
            != self.dependencies.host_compatibility_profile_hash
            or self.host_compatibility_ref.media_type != "application/json"
            or len(
                {
                    self.adapter_principal_id,
                    self.evaluator_principal_id,
                    self.orchestrator_principal_id,
                }
            )
            != 3
            or self.host_principal_id in {self.adapter_principal_id, self.evaluator_principal_id}
        ):
            raise ValueError("deployment host, image, model, adapter or identity pins disagree")
        return self


class SafetyPublicKeyBinding(InventoryValidity):
    workspace_id: Identifier
    project_id: Identifier
    evaluator_principal_id: Identifier
    dependencies: DependencyClosure
    key_id: Identifier
    public_key_hex: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)


class _IntervalTransaction(Protocol):
    """Additive transaction seam supplied by authority-providers integration.

    This is typing only, not an implementation or fallback. Older runtime stores
    are explicitly refused rather than dropping the final commit-time recheck.
    """

    def require_valid_interval(
        self, valid_from: datetime, valid_until: datetime, code: Code
    ) -> None: ...

    @property
    def authority_valid_until(self) -> datetime | None: ...


def _require(value: bool, code: Code) -> None:
    if not value:
        raise RoboticsError(code)


def _interval(tx: RuntimeTransaction, start: datetime, end: datetime, code: Code) -> None:
    _require(
        callable(getattr(tx, "require_valid_interval", None))
        and hasattr(tx, "authority_valid_until"),
        Code.SIMULATION_UNAVAILABLE,
    )
    cast(_IntervalTransaction, tx).require_valid_interval(start, end, code)


def _snapshot(check: AuthorityCheck) -> AuthorityCheck:
    return AuthorityCheck.model_validate(bounded_json(canonical_json(check)))


def _entries[C: WireModel](entries: Sequence[C], model: type[C]) -> tuple[bytes, ...]:
    if not 1 <= len(entries) <= MAX_INVENTORY_ENTRIES:
        raise ValueError("explicit deployment inventory must contain 1 to 128 entries")
    return tuple(
        canonical_json(model.model_validate(bounded_json(canonical_json(item)))) for item in entries
    )


def _closure(value: DependencyClosure) -> str:
    return content_hash(value, exclude=())


async def _episode(tx: RuntimeTransaction, check: AuthorityCheck) -> RuntimeBinding:
    ep = check.episode
    current = await tx.get(RuntimeEpisode, ep.id)
    _require(current == ep, Code.EPISODE_STATE_CONFLICT)
    binding = await tx.get(RuntimeBinding, ep.binding_id)
    _require(binding is not None, Code.EPISODE_STATE_CONFLICT)
    assert binding is not None
    _require(
        (binding.workspace_id, binding.project_id, binding.episode_id, binding.run_id)
        == (ep.workspace_id, ep.project_id, ep.id, ep.run_id),
        Code.EPISODE_STATE_CONFLICT,
    )
    return binding


class HostInventoryAuthority:
    """CurrentAuthority over explicit scoped launch configuration; no cleanup authority."""

    def __init__(self, entries: Sequence[HostProfileBinding]):
        self._entries = _entries(entries, HostProfileBinding)
        resources: set[str] = set()
        matches: set[tuple[str, ...]] = set()
        for raw in self._entries:
            entry = HostProfileBinding.model_validate_json(raw)
            identity = (
                entry.workspace_id,
                entry.project_id,
                _closure(entry.dependencies),
                entry.adapter_principal_id,
                entry.evaluator_principal_id,
                entry.orchestrator_principal_id,
            )
            if entry.profile.resource_id in resources or identity in matches:
                raise ValueError("duplicate resource or ambiguous scoped host closure")
            resources.add(entry.profile.resource_id)
            matches.add(identity)

    async def resolve(self, tx: RuntimeTransaction, check: AuthorityCheck) -> HostProfileBinding:
        """Resolve the same exact profile used by check; returned model is a new copy."""
        await tx.require_scope(check.episode.workspace_id, check.episode.project_id)
        check = _snapshot(check)
        ep = check.episode
        binding = await _episode(tx, check)
        matches = [
            entry
            for raw in self._entries
            if (entry := HostProfileBinding.model_validate_json(raw)).workspace_id
            == ep.workspace_id
            and entry.project_id == ep.project_id
            and entry.dependencies == ep.setup.dependencies
            and entry.adapter_principal_id == ep.setup.adapter_principal_id
            and entry.evaluator_principal_id == ep.setup.evaluator_principal_id
            and entry.orchestrator_principal_id == binding.orchestrator_id
        ]
        _require(len(matches) == 1, Code.ISOLATION_UNAVAILABLE)
        entry = matches[0]
        _require(entry.disposition == "ACTIVE", Code.ISOLATION_UNAVAILABLE)
        for identity in sorted(
            {
                entry.host_principal_id,
                entry.adapter_principal_id,
                entry.evaluator_principal_id,
                entry.orchestrator_principal_id,
            }
        ):
            await tx.registry.authorize(identity, ep.workspace_id, ep.project_id, service=True)
        if check.resource is None:
            _require(
                check.operation == "BIND_RUN" and check.lease is None and ep.lease_id is None,
                Code.LEASE_INVALID,
            )
        else:
            resource = check.resource
            _require(await tx.get(RuntimeResource, resource.id) == resource, Code.LEASE_INVALID)
            _require(
                (
                    resource.workspace_id,
                    resource.project_id,
                    resource.id,
                    resource.host_principal_id,
                    resource.host_instance_id,
                )
                == (
                    entry.workspace_id,
                    entry.project_id,
                    entry.profile.resource_id,
                    entry.host_principal_id,
                    entry.host_instance_id,
                )
                and not resource.quarantined,
                Code.LEASE_INVALID,
            )
            lease = check.lease
            if lease is None:
                _require(check.operation == "ACQUIRE_LEASE", Code.LEASE_INVALID)
                # Idempotent acquire rechecks its current owned lease before
                # returning history. An occupied unrelated resource is refused.
                if resource.current_lease_id is not None:
                    lease = await tx.get(RuntimeLease, resource.current_lease_id)
                    _require(lease is not None, Code.LEASE_INVALID)
                else:
                    _require(ep.lease_id is None, Code.LEASE_INVALID)
            if lease is not None:
                _require(await tx.get(RuntimeLease, lease.id) == lease, Code.LEASE_INVALID)
                _require(
                    (
                        lease.workspace_id,
                        lease.project_id,
                        lease.resource_id,
                        lease.episode_id,
                        lease.run_id,
                        lease.owner_principal_id,
                    )
                    == (
                        ep.workspace_id,
                        ep.project_id,
                        resource.id,
                        ep.id,
                        ep.run_id,
                        entry.orchestrator_principal_id,
                    )
                    and lease.id == lease.contract_id == ep.lease_id == resource.current_lease_id
                    and lease.generation == resource.generation
                    and lease.status == "ACTIVE",
                    Code.LEASE_INVALID,
                )
                now = await tx.now()
                end = min(lease.expires_at, lease.heartbeat_deadline)
                _require(lease.last_heartbeat_at <= now < end, Code.LEASE_INVALID)
                _interval(tx, lease.last_heartbeat_at, end, Code.LEASE_INVALID)
        now = await tx.now()  # authoritative clock after identity/resource reads
        _require(entry.valid_from <= now < entry.valid_until, Code.ISOLATION_UNAVAILABLE)
        _interval(tx, entry.valid_from, entry.valid_until, Code.ISOLATION_UNAVAILABLE)
        return entry

    async def check(self, tx: RuntimeTransaction, check: AuthorityCheck) -> None:
        await self.resolve(tx, check)

    async def require_profile(
        self, tx: RuntimeTransaction, check: AuthorityCheck, profile: LaunchProfile
    ) -> HostProfileBinding:
        """Bind composition's selected launch profile, without inspecting/launching it."""
        expected = await self.resolve(tx, check)
        actual = LaunchProfile.model_validate(bounded_json(canonical_json(profile)))
        _require(actual == expected.profile, Code.ISOLATION_UNAVAILABLE)
        return expected


class ConfiguredSafetyKeyAuthority:
    """SafetyKeyAuthority returning only current explicitly configured public keys."""

    def __init__(self, entries: Sequence[SafetyPublicKeyBinding]):
        self._entries = _entries(entries, SafetyPublicKeyBinding)
        ids: set[str] = set()
        material: set[str] = set()
        for raw in self._entries:
            entry = SafetyPublicKeyBinding.model_validate_json(raw)
            if entry.key_id in ids or entry.public_key_hex in material:
                raise ValueError("duplicate key identity or aliased public key material")
            ids.add(entry.key_id)
            material.add(entry.public_key_hex)

    async def keys(
        self, tx: RuntimeTransaction, check: AuthorityCheck
    ) -> dict[str, TrustedSafetyKey]:
        await tx.require_scope(check.episode.workspace_id, check.episode.project_id)
        check = _snapshot(check)
        ep = check.episode
        binding = await _episode(tx, check)
        _require(
            ep.setup.evaluator_principal_id
            not in {ep.setup.adapter_principal_id, binding.orchestrator_id},
            Code.SAFETY_DENIED,
        )
        await tx.registry.authorize(
            ep.setup.evaluator_principal_id,
            ep.workspace_id,
            ep.project_id,
            service=True,
        )
        now = await tx.now()
        matches = [
            entry
            for raw in self._entries
            if (entry := SafetyPublicKeyBinding.model_validate_json(raw)).workspace_id
            == ep.workspace_id
            and entry.project_id == ep.project_id
            and entry.evaluator_principal_id == ep.setup.evaluator_principal_id
            and entry.dependencies == ep.setup.dependencies
            and entry.disposition == "ACTIVE"
            and entry.valid_from <= now < entry.valid_until
        ]
        _require(0 < len(matches) <= MAX_KEYS_PER_BINDING, Code.SAFETY_DENIED)
        keys: dict[str, TrustedSafetyKey] = {}
        for entry in matches:
            _interval(tx, entry.valid_from, entry.valid_until, Code.SAFETY_DENIED)
            keys[entry.key_id] = TrustedSafetyKey(
                principal_id=entry.evaluator_principal_id,
                public_key=bytes.fromhex(entry.public_key_hex),
            )
        return keys
