"""One unit of work for runtime state, original contracts, events and identities.

Internal DTOs are explicitly separate from sealed canonical writer contracts.
No method here is an HTTP authority surface. Use SimulationAuthority for policy.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal, Self

from pydantic import AwareDatetime, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from accretion.contracts import Capability, CapabilityPolicy, Run, StrictModel, Task
from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.refs import PolicyRef, VerifierRef
from accretion.contracts.robotics import CanonicalWriterEnvelope, SimulationRunBinding
from accretion.contracts.robotics.values import (
    DependencyClosure,
    Digest,
    Finite,
    Identifier,
    MotionBudgetState,
    Nonnegative,
    StateBinding,
)
from accretion.persistence import models as db
from accretion.persistence.store import MemoryStore, PostgresStore, StateStore
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.store import RegistryTransaction, registry_store_for


class RuntimeDTO(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: Identifier
    workspace_id: Identifier
    project_id: Identifier
    revision: int = Field(default=1, ge=1, le=2**63 - 1, strict=True)


class LiveState(StrictModel):
    """Host-observed state; only an authenticated exact acknowledgement updates it."""

    state: StateBinding
    sequence: int = Field(ge=0, strict=True)
    budget: MotionBudgetState
    phase: Literal["APPROACH", "GRASP", "TRANSPORT", "RELEASE"]
    physics_step_ns: int = Field(gt=0, strict=True)
    max_preview_intervals: int = Field(default=10000, gt=0, le=10000, strict=True)
    last_action_sim_time_ns: int = Field(ge=0, strict=True)
    episode_start_sim_time_ns: int = Field(ge=0, strict=True)
    joint_positions_rad: list[Finite] = Field(min_length=1, max_length=64)
    joint_velocities_rad_s: list[Finite] = Field(min_length=1, max_length=64)
    joint_accelerations_rad_s2: list[Finite] = Field(min_length=1, max_length=64)
    gripper_opening_m: Nonnegative

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if not (
            len(self.joint_positions_rad)
            == len(self.joint_velocities_rad_s)
            == len(self.joint_accelerations_rad_s2)
        ):
            raise ValueError("live joint state dimensions disagree")
        canonical_json(self)
        return self


class EpisodeSetup(StrictModel):
    """Complete immutable pins resolved before creating an attributable episode."""

    experiment_contract_hash: Digest
    environment_snapshot_hash: Digest
    adapter_manifest_hash: Digest
    safety_envelope_hash: Digest
    verification_spec_hash: Digest
    seed: int = Field(ge=0, strict=True)
    randomization_sample_hash: Digest
    dependencies: DependencyClosure
    descriptor_original: str
    adapter_principal_id: str
    evaluator_principal_id: str
    evaluator: VerifierRef
    policy_ref: PolicyRef


class RuntimeBinding(RuntimeDTO):
    run_id: str
    episode_id: str
    contract_id: str
    initiator_id: str
    orchestrator_id: str


class GrantedCapability(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    capability_id: Identifier
    capability_version: Identifier
    content_digest: Digest


class PolicyGrantSpec(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operator_id: Identifier
    orchestrator_id: Identifier
    policy_ref: PolicyRef
    capabilities: tuple[GrantedCapability, ...] = Field(min_length=1, max_length=6)
    operator_permissions: tuple[Identifier, ...] = Field(default=(), max_length=64)
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def _unique(self) -> Self:
        names = [c.capability_id for c in self.capabilities]
        if len(set(names)) != len(names) or len(set(self.operator_permissions)) != len(
            self.operator_permissions
        ):
            raise ValueError("duplicate capability or permission")
        if self.operator_id == self.orchestrator_id:
            raise ValueError("the human operator and service orchestrator must be independent")
        return self


class InventoryRecord(RuntimeDTO):
    """Internal authority inventory; never a canonical experiment approval."""

    revision: int = Field(default=1, ge=1, le=2**31 - 1, strict=True)
    event_stream_id: Identifier
    created_by: Identifier
    created_at: AwareDatetime
    updated_by: Identifier
    updated_at: AwareDatetime
    valid_from: AwareDatetime
    valid_until: AwareDatetime
    disposition: Literal["ACTIVE", "REVOKED", "QUARANTINED"] = "ACTIVE"

    @model_validator(mode="after")
    def _interval(self) -> Self:
        if not self.created_at <= self.updated_at or not (
            self.created_at <= self.valid_from < self.valid_until
        ):
            raise ValueError("invalid authority inventory lifetime")
        if (self.valid_until - self.valid_from).total_seconds() > 86_400:
            raise ValueError("authority inventory lifetime exceeds 24 hours")
        return self


class RuntimePolicyGrant(InventoryRecord):
    operator_id: Identifier
    orchestrator_id: Identifier
    policy_ref: PolicyRef
    capabilities: tuple[GrantedCapability, ...] = Field(min_length=1, max_length=6)
    operator_permissions: tuple[Identifier, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def _spec(self) -> Self:
        PolicyGrantSpec.model_validate(
            {key: getattr(self, key) for key in PolicyGrantSpec.model_fields}
        )
        return self


class ConformanceAdmissionSpec(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    adapter_contract_id: Identifier
    report_id: Identifier
    report_hash: Digest
    closure_hash: Digest
    attestation_original_json: str = Field(min_length=2, max_length=16_384)
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def _proof_size(self) -> Self:
        if len(self.attestation_original_json.encode()) > 16_384:
            raise ValueError("attestation exceeds byte ceiling")
        return self


class RuntimeConformanceAdmission(InventoryRecord):
    adapter_contract_id: Identifier
    adapter_manifest_hash: Digest
    report_id: Identifier
    report_hash: Digest
    closure_hash: Digest
    verifier: VerifierRef
    verifier_principal_id: Identifier
    suite_version: Identifier
    suite_artifact_digest: Digest
    attestation_original_json: str = Field(min_length=2, max_length=16_384)
    attestation_digest: Digest

    @model_validator(mode="after")
    def _proof(self) -> Self:
        raw = self.attestation_original_json.encode()
        if len(raw) > 16_384 or sha256(raw).hexdigest() != self.attestation_digest:
            raise ValueError("attestation original and digest disagree")
        return self


class RuntimeResource(RuntimeDTO):
    host_principal_id: str
    host_instance_id: str
    generation: int = Field(default=0, ge=0, le=2**63 - 1, strict=True)
    current_lease_id: str | None = None
    quarantined: bool = False


class RuntimeLease(RuntimeDTO):
    resource_id: str
    run_id: str
    episode_id: str
    generation: int = Field(gt=0, strict=True)
    contract_id: str
    owner_principal_id: str
    endpoint_handle: str
    status: Literal["ACTIVE", "REVOKED", "EXPIRED", "RELEASED"] = "ACTIVE"
    expires_at: datetime
    heartbeat_deadline: datetime
    last_heartbeat_at: datetime
    heartbeat_timeout_seconds: float


class RuntimeEpisode(RuntimeDTO):
    run_id: str
    binding_id: str
    setup: EpisodeSetup
    status: Literal[
        "BOUND", "LEASED", "PREFLIGHT", "APPROVED", "RUNNING", "VERIFYING", "ABORTED"
    ] = "BOUND"
    lease_id: str | None = None
    preflight_id: str | None = None
    approval_id: str | None = None
    live: LiveState | None = None
    in_flight: str | None = None
    reset_completed: bool = False
    last_request_sequence: int = -1
    event_sequence: int = 0


class RuntimeApproval(RuntimeDTO):
    episode_id: str
    contract_id: str
    consumed_episode_id: str | None = None
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None


class RuntimeIssuance(RuntimeDTO):
    episode_id: str
    contract_id: str
    receipt_hash: str
    request_json: str
    preview_json: str | None


class DispatchReservation(RuntimeDTO):
    episode_id: str
    request_sequence: int
    request_json: str
    prepared_hash: str | None = None
    receipt_hash: str | None = None
    status: Literal["RESERVED", "COMPLETED", "UNCERTAIN"] = "RESERVED"
    response_json: str | None = None
    reserved_at: datetime
    completed_at: datetime | None = None
    # Optional only for compatibility with historical/configuration-only rows.
    # A configured inventory provider always supplies a finite host permit cap.
    authority_valid_until: AwareDatetime | None = None


class HostJournalEntry(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sequence: int = Field(ge=1, le=32, strict=True)
    kind: Literal["PLANNED", "CREATED", "CLEANUP_STARTED", "CLEANUP_UNCERTAIN", "CLEANUP_CONFIRMED"]
    actor_id: Identifier
    occurred_at: AwareDatetime
    evidence_original: str = Field(default="{}", min_length=2, max_length=524288)
    previous_hash: Digest | None
    entry_hash: Digest

    @model_validator(mode="after")
    def _hash(self) -> Self:
        if len(self.evidence_original.encode()) > 524288 or self.entry_hash != content_hash(
            self, exclude=("entry_hash",)
        ):
            raise ValueError("host journal evidence or digest is invalid")
        return self


class RuntimeHostCreation(RuntimeDTO):
    """Durable cleanup identity, never a restart or adapter execution permit."""

    episode_id: Identifier
    run_id: Identifier
    lease_id: Identifier
    lease_generation: int = Field(ge=1, strict=True)
    resource_id: Identifier
    host_principal_id: Identifier
    host_instance_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    docker_name: str = Field(pattern=r"^accretion-sim-[0-9a-f]{32}$")
    profile_original: str = Field(min_length=2, max_length=16384)
    profile_hash: Digest
    lease_original: str = Field(min_length=2, max_length=4096)
    bootstrap_directory: str = Field(min_length=1, max_length=4096)
    bootstrap_digest: Digest
    dependency_hash: Digest
    created_at: AwareDatetime
    created_by: Identifier
    status: Literal["PLANNED", "CREATED", "CLEANUP_PENDING", "CLEANED"] = "PLANNED"
    container_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    creation_valid_until: AwareDatetime
    entries: tuple[HostJournalEntry, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def _journal(self) -> Self:
        previous = None
        status = "PLANNED"
        last_time = self.created_at
        for index, entry in enumerate(self.entries, start=1):
            if (
                entry.sequence != index
                or entry.previous_hash != previous
                or entry.occurred_at < last_time
            ):
                raise ValueError("host journal ordering/hash chain is invalid")
            previous = entry.entry_hash
            last_time = entry.occurred_at
            if index > 1:
                if entry.kind == "CREATED" and status == "PLANNED":
                    status = "CREATED"
                elif entry.kind == "CLEANUP_STARTED" and status != "CLEANED":
                    status = "CLEANUP_PENDING"
                elif entry.kind == "CLEANUP_UNCERTAIN" and status == "CLEANUP_PENDING":
                    pass
                elif entry.kind == "CLEANUP_CONFIRMED" and status == "CLEANUP_PENDING":
                    status = "CLEANED"
                else:
                    raise ValueError("invalid host journal transition")
        planned = json.loads(self.entries[0].evidence_original)
        immutable = (
            "episode_id",
            "host_instance_id",
            "docker_name",
            "resource_id",
            "lease_id",
            "lease_generation",
            "profile_original",
            "profile_hash",
            "lease_original",
            "bootstrap_directory",
            "bootstrap_digest",
        )
        if not isinstance(planned, dict) or any(
            planned.get(k) != getattr(self, k) for k in immutable
        ):
            raise ValueError("host creation pins differ from the original plan")
        if (
            self.entries[0].kind != "PLANNED"
            or self.entries[0].occurred_at != self.created_at
            or self.entries[0].actor_id != self.created_by
            or self.revision != len(self.entries)
            or self.status != status
            or (self.status in {"CREATED", "CLEANED"} and self.container_id is None)
            or self.creation_valid_until <= self.created_at
            or len(canonical_json(self)) > 1048576
        ):
            raise ValueError("host journal metadata or size is invalid")
        return self


ROWS: dict[type[RuntimeDTO], type[Any]] = {
    RuntimeBinding: db.SimulationRunBindingRow,
    RuntimeResource: db.SimulationResourceRow,
    RuntimeLease: db.SimulationLeaseRow,
    RuntimeEpisode: db.SimulationEpisodeStateRow,
    RuntimeApproval: db.SimulationApprovalStateRow,
    RuntimeIssuance: db.SimulationSafetyIssuanceRow,
    DispatchReservation: db.SimulationDispatchRow,
    RuntimePolicyGrant: db.SimulationPolicyGrantRow,
    RuntimeConformanceAdmission: db.SimulationConformanceAdmissionRow,
    RuntimeHostCreation: db.SimulationHostCreationRow,
}


def checked[C: RuntimeDTO](model: type[C], payload: dict[str, Any]) -> C:
    try:
        record = model.model_validate_json(payload["record"])
        if content_hash(record, exclude=()) != payload["record_hash"]:
            raise ValueError("runtime DTO digest mismatch")
        if any(
            getattr(record, name) != value
            for name, value in payload.items()
            if name not in {"record", "record_hash"}
        ):
            raise ValueError("runtime index and original disagree")
        return record
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise RoboticsError(Code.INVALID_CONTRACT) from exc


def columns(record: RuntimeDTO) -> dict[str, Any]:
    row = ROWS[type(record)]
    body = canonical_json(record).decode()
    values = {
        name: getattr(record, name)
        for name in row.__table__.columns.keys()
        if name not in {"record", "record_hash"}
    }
    return {**values, "record": body, "record_hash": content_hash(record, exclude=())}


def _governance_value[C: StrictModel](model: type[C], definition: Any) -> C:
    try:
        raw = canonical_json(definition)
        value = model.model_validate_json(raw, strict=True)
        # The legacy governance rows have no canonical writer seal. Require the
        # full stored definition, not a coercing/default-filling projection.
        if canonical_json(value.model_dump(mode="json")) != raw:
            raise ValueError("governance definition is not an exact writer")
        return value
    except (ValueError, TypeError) as exc:
        raise RoboticsError(Code.INVALID_CONTRACT) from exc


class RuntimeTransaction:
    def __init__(
        self,
        state: MemoryStore | PostgresStore,
        registry: RegistryTransaction,
        clock: Callable[[], datetime],
        project_id: str,
    ):
        self.state, self._registry, self.clock = state, registry, clock
        self._project_id = project_id
        self._owner = asyncio.current_task()
        self._active = True
        self.session: AsyncSession | None = getattr(registry, "session", None)
        self._time_guards: list[tuple[datetime, datetime, Code]] = []

    def require_active(self, project_id: str | None = None) -> None:
        """Reject escaped or cross-task handles before any reads, writes or grants."""
        if not self._active or self._owner is None or asyncio.current_task() is not self._owner:
            raise RoboticsError(Code.SIMULATION_UNAVAILABLE)
        self._registry.require_active()
        if project_id is not None and project_id != self._project_id:
            raise RoboticsError(Code.RESOURCE_NOT_FOUND)

    async def require_scope(self, workspace_id: str, project_id: str) -> None:
        """Check the persisted binding inside this project's actual transaction."""
        self.require_active(project_id)
        if self.session:
            row = await self.session.scalar(
                select(db.SimulationProjectBindingRow)
                .where(db.SimulationProjectBindingRow.project_id == project_id)
                .with_for_update(read=True)
            )
            bound_workspace = row.workspace_id if row else None
        else:
            assert isinstance(self.state, MemoryStore)
            bound_workspace = self.state.robotics_registry_state["bindings"].get(project_id)
        if bound_workspace != workspace_id:
            raise RoboticsError(Code.RESOURCE_NOT_FOUND)

    @property
    def registry(self) -> RegistryTransaction:
        self.require_active()
        return self._registry

    def _close(self) -> None:
        self._active = False

    def require_valid_interval(
        self, valid_from: datetime, valid_until: datetime, code: Code
    ) -> None:
        self.require_active()
        if valid_from.tzinfo is None or valid_until.tzinfo is None or valid_from >= valid_until:
            raise RoboticsError(Code.INVALID_CONTRACT)
        guard = (valid_from, valid_until, code)
        if guard not in self._time_guards:
            if len(self._time_guards) >= 32:
                raise RoboticsError(Code.RESOURCE_CAP_EXHAUSTED)
            self._time_guards.append(guard)

    @property
    def authority_valid_until(self) -> datetime | None:
        """Trusted transaction-local cap, not a reusable authorization token."""
        self.require_active()
        return min((end for _, end, _ in self._time_guards), default=None)

    async def validate_time_guards(self) -> None:
        self.require_active()
        if self._time_guards:
            # Final authoritative read after all collaborator awaits and writes.
            # Do not perform any collaborator or write after this check.
            now = await self.now()
            for start, end, code in self._time_guards:
                if not start <= now < end:
                    raise RoboticsError(code)

    async def now(self) -> datetime:
        self.require_active()
        # Call AFTER all contended resource/authority locks, not transaction start.
        value = (
            await self.session.scalar(select(func.clock_timestamp()))
            if self.session
            else self.clock()
        )
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise RoboticsError(Code.INVALID_CONTRACT)
        return value

    async def capability(self, capability_id: str, version: str) -> Capability | None:
        """Read one exact governance version under the authority transaction."""
        self.require_active()
        if self.session:
            row = await self.session.scalar(
                select(db.CapabilityRow)
                .where(
                    db.CapabilityRow.capability_id == capability_id,
                    db.CapabilityRow.version == version,
                )
                .with_for_update(read=True)
            )
            if row is None:
                return None
            value = _governance_value(Capability, row.definition)
            if (value.capability_id, value.version, value.enabled, value.created_at) != (
                row.capability_id,
                row.version,
                row.enabled,
                row.created_at,
            ):
                raise RoboticsError(Code.INVALID_CONTRACT)
        else:
            assert isinstance(self.state, MemoryStore)
            source = self.state.capabilities.get((capability_id, version))
            if source is None:
                return None
            value = _governance_value(Capability, source)
        if (value.capability_id, value.version) != (capability_id, version):
            raise RoboticsError(Code.INVALID_CONTRACT)
        return value

    async def capability_policy(self, policy_id: str, version: str) -> CapabilityPolicy | None:
        """No mutable latest-version selection or separate store transaction."""
        self.require_active()
        if self.session:
            row = await self.session.scalar(
                select(db.CapabilityPolicyRow)
                .where(
                    db.CapabilityPolicyRow.policy_id == policy_id,
                    db.CapabilityPolicyRow.version == version,
                )
                .with_for_update(read=True)
            )
            if row is None:
                return None
            value = _governance_value(CapabilityPolicy, row.definition)
            if (value.policy_id, value.version, value.created_at) != (
                row.policy_id,
                row.version,
                row.created_at,
            ):
                raise RoboticsError(Code.INVALID_CONTRACT)
        else:
            assert isinstance(self.state, MemoryStore)
            source = self.state.capability_policies.get((policy_id, version))
            if source is None:
                return None
            value = _governance_value(CapabilityPolicy, source)
        if (value.policy_id, value.version) != (policy_id, version):
            raise RoboticsError(Code.INVALID_CONTRACT)
        return value

    async def lock_resource(self, resource_id: str) -> None:
        self.require_active()
        if self.session:
            lock = int.from_bytes(
                sha256(("robotics-resource:" + resource_id).encode()).digest()[:8],
                "big",
                signed=True,
            )
            await self.session.execute(select(func.pg_advisory_xact_lock(lock)))

    async def get[C: RuntimeDTO](self, model: type[C], identity: str) -> C | None:
        self.require_active()
        if self.session:
            row_type = ROWS[model]
            row = await self.session.scalar(
                select(row_type).where(row_type.id == identity).with_for_update()
            )
            payload = (
                {name: getattr(row, name) for name in row_type.__table__.columns.keys()}
                if row
                else None
            )
        else:
            assert isinstance(self.state, MemoryStore)
            payload = self.state.robotics_runtime_state.get(model.__name__, {}).get(identity)
        value = checked(model, deepcopy(payload)) if payload else None
        if value is not None:
            await self.require_scope(value.workspace_id, value.project_id)
        return value

    async def list_rows[C: RuntimeDTO](
        self,
        model: type[C],
        *,
        workspace_id: str,
        project_id: str,
        after: str = "",
        limit: int = 25,
    ) -> list[C]:
        await self.require_scope(workspace_id, project_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise RoboticsError(Code.INVALID_REQUEST)
        if self.session:
            row_type = ROWS[model]
            rows = (
                await self.session.scalars(
                    select(row_type)
                    .where(
                        row_type.workspace_id == workspace_id,
                        row_type.project_id == project_id,
                        row_type.id > after,
                    )
                    .order_by(row_type.id)
                    .limit(limit)
                )
            ).all()
            payloads = [
                {name: getattr(row, name) for name in row_type.__table__.columns.keys()}
                for row in rows
            ]
        else:
            assert isinstance(self.state, MemoryStore)
            rows_by_id = self.state.robotics_runtime_state.get(model.__name__, {})
            payloads = [
                rows_by_id[key]
                for key in sorted(rows_by_id)
                if key > after
                and rows_by_id[key]["workspace_id"] == workspace_id
                and rows_by_id[key]["project_id"] == project_id
            ][:limit]
        return [checked(model, deepcopy(payload)) for payload in payloads]

    async def put(self, record: RuntimeDTO, *, insert: bool = False) -> None:
        await self.require_scope(record.workspace_id, record.project_id)
        if isinstance(record, RuntimeHostCreation):
            record = RuntimeHostCreation.model_validate_json(canonical_json(record))
            current = await self.get(RuntimeHostCreation, record.id)
            if current is not None:
                mutable = {"revision", "entries", "status", "container_id"}
                if (
                    any(
                        getattr(record, key) != getattr(current, key)
                        for key in type(record).model_fields
                        if key not in mutable
                    )
                    or record.revision != current.revision + 1
                    or len(record.entries) != len(current.entries) + 1
                    or record.entries[:-1] != current.entries
                    or (
                        current.container_id is not None
                        and record.container_id != current.container_id
                    )
                ):
                    raise RoboticsError(Code.CONTRACT_CONFLICT)
        values = columns(record)
        if self.session:
            row_type = ROWS[type(record)]
            row = await self.session.get(row_type, record.id)
            if insert and row is not None:
                raise RoboticsError(Code.CONTRACT_CONFLICT)
            if row is None:
                self.session.add(row_type(**values))
            else:
                for name, value in values.items():
                    setattr(row, name, value)
            await self.session.flush()
        else:
            assert isinstance(self.state, MemoryStore)
            rows = self.state.robotics_runtime_state.setdefault(type(record).__name__, {})
            if insert and record.id in rows:
                raise RoboticsError(Code.CONTRACT_CONFLICT)
            for existing in rows.values():
                other = checked(type(record), existing)
                if other.id == record.id:
                    continue
                unique: list[tuple[str, ...]] = []
                if isinstance(record, RuntimeBinding):
                    unique = [("run_id",), ("episode_id",)]
                if isinstance(record, RuntimeLease):
                    unique = [("resource_id", "generation")]
                if isinstance(record, RuntimeEpisode):
                    unique = [("run_id",)]
                if isinstance(record, RuntimeApproval):
                    unique = [("consumed_episode_id",)]
                if isinstance(record, RuntimeIssuance):
                    unique = [("receipt_hash",)]
                if isinstance(record, DispatchReservation):
                    unique = [
                        ("episode_id", "request_sequence"),
                        ("prepared_hash",),
                        ("receipt_hash",),
                    ]
                if isinstance(record, RuntimePolicyGrant):
                    unique = [
                        ("workspace_id", "project_id", "operator_id", "orchestrator_id"),
                        ("event_stream_id",),
                    ]
                if isinstance(record, RuntimeConformanceAdmission):
                    unique = [
                        ("workspace_id", "project_id", "adapter_contract_id", "closure_hash"),
                        ("event_stream_id",),
                    ]
                if isinstance(record, RuntimeHostCreation):
                    unique = [("lease_id",), ("docker_name",), ("container_id",)]
                for names in unique:
                    if all(
                        getattr(record, n) is not None and getattr(record, n) == getattr(other, n)
                        for n in names
                    ):
                        raise RoboticsError(Code.CONTRACT_CONFLICT)
            rows[record.id] = deepcopy(values)

    async def create_task_run(self, task: Task, run: Run) -> None:
        self.require_active(task.envelope.project_id)
        self.require_active(run.project_id)
        if run.task_id != task.envelope.task_id:
            raise RoboticsError(Code.INVALID_CONTRACT)
        if self.session:
            task_existing = await self.session.get(db.TaskRow, task.envelope.task_id)
            run_existing = await self.session.get(db.RunRow, run.run_id)
            if task_existing is not None or run_existing is not None:
                raise RoboticsError(Code.CONTRACT_CONFLICT)
            self.session.add(PostgresStore._task_to_row(task))
            await self.session.flush()
            self.session.add(PostgresStore._run_to_row(run))
            await self.session.flush()
        else:
            assert isinstance(self.state, MemoryStore)
            if task.envelope.task_id in self.state.tasks or run.run_id in self.state.runs:
                raise RoboticsError(Code.CONTRACT_CONFLICT)
            self.state.tasks[task.envelope.task_id] = task.model_copy(deep=True)
            self.state.runs[run.run_id] = run.model_copy(deep=True)

    async def run_task(self, run_id: str) -> tuple[Run, Task]:
        self.require_active()
        if self.session:
            row = await self.session.scalar(
                select(db.RunRow).where(db.RunRow.id == run_id).with_for_update()
            )
            if row is None:
                raise RoboticsError(Code.RESOURCE_NOT_FOUND)
            task_row = await self.session.get(db.TaskRow, row.task_id, with_for_update=True)
            if task_row is None:
                raise RoboticsError(Code.RESOURCE_NOT_FOUND)
            run, task = PostgresStore._row_to_run(row), PostgresStore._row_to_task(task_row)
            self.require_active(run.project_id)
            self.require_active(task.envelope.project_id)
            return run, task
        assert isinstance(self.state, MemoryStore)
        memory_run = self.state.runs.get(run_id)
        if memory_run is None or memory_run.task_id not in self.state.tasks:
            raise RoboticsError(Code.RESOURCE_NOT_FOUND)
        self.require_active(memory_run.project_id)
        self.require_active(self.state.tasks[memory_run.task_id].envelope.project_id)
        return memory_run.model_copy(deep=True), self.state.tasks[memory_run.task_id].model_copy(
            deep=True
        )


class RuntimeStore:
    def __init__(
        self, state: MemoryStore | PostgresStore, *, clock: Callable[[], datetime] | None = None
    ):
        self.state = state
        self.registry = registry_store_for(state)
        self.clock = clock or (lambda: datetime.now(UTC))

    @asynccontextmanager
    async def transaction(self, project_id: str) -> AsyncIterator[RuntimeTransaction]:
        async with self.registry.transaction(project_id) as registry:
            snapshot: tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None = None
            # Memory public Task/Run/authority methods share the registry mutex.
            # Reads cannot observe these uncommitted dictionaries and rollback
            # cannot overwrite a generic caller's concurrent create/cancellation.
            if isinstance(self.state, MemoryStore):
                snapshot = deepcopy(
                    (self.state.robotics_runtime_state, self.state.tasks, self.state.runs)
                )
            tx = RuntimeTransaction(self.state, registry, self.clock, project_id)
            try:
                yield tx
                await tx.validate_time_guards()
            except BaseException:
                if snapshot is not None and isinstance(self.state, MemoryStore):
                    targets: tuple[dict[str, Any], ...] = (
                        self.state.robotics_runtime_state,
                        self.state.tasks,
                        self.state.runs,
                    )
                    for target, old in zip(
                        targets,
                        snapshot,
                        strict=True,
                    ):
                        target.clear()
                        target.update(old)
                raise
            finally:
                tx._close()

    async def lookup_run_owner(
        self, run_id: str, *, actor_id: str | None = None
    ) -> RuntimeBinding | None:
        """Internal generic-dispatch lookup. Corruption never means unowned.

        An actor-bound lookup checks current membership before returning any
        ownership or corruption detail. Omitting the actor is internal-only.
        Ownership never grants permission to operate the episode.
        """
        found: tuple[str, str, str] | None = None
        if isinstance(self.state, PostgresStore):
            async with self.state.sessions() as session:
                binding_row = await session.scalar(
                    select(db.SimulationRunBindingRow).where(
                        db.SimulationRunBindingRow.run_id == run_id
                    )
                )
                if binding_row is not None:
                    found = (binding_row.id, binding_row.workspace_id, binding_row.project_id)
        else:
            async with self.state.robotics_registry_lock:
                for payload in self.state.robotics_runtime_state.get("RuntimeBinding", {}).values():
                    if payload["run_id"] == run_id:
                        found = (payload["id"], payload["workspace_id"], payload["project_id"])
                        break
        if found is None:
            return None
        binding_id, workspace_id, project_id = found
        async with self.transaction(project_id) as tx:
            if actor_id is not None:
                try:
                    await tx.registry.authorize(actor_id, workspace_id, project_id)
                except RoboticsError as exc:
                    # No ownership, scope or corrupted-record disclosure to outsiders.
                    raise RoboticsError(Code.RESOURCE_NOT_FOUND) from exc
            binding = await tx.get(RuntimeBinding, binding_id)
            if binding is None or binding.run_id != run_id:
                raise RoboticsError(Code.INVALID_CONTRACT)
            if (binding.workspace_id, binding.project_id) != (workspace_id, project_id):
                raise RoboticsError(Code.RESOURCE_NOT_FOUND)
            row = await tx.registry.get(binding.contract_id)
            if row is None:
                raise RoboticsError(Code.INVALID_CONTRACT)
            try:
                seal = CanonicalWriterEnvelope(row.original_json).for_execution(
                    SimulationRunBinding
                )
                expected = (
                    binding.contract_id,
                    binding.workspace_id,
                    binding.project_id,
                    binding.run_id,
                    binding.episode_id,
                    binding.orchestrator_id,
                )
                actual = (
                    seal.contract_id,
                    seal.workspace_id,
                    seal.project_id,
                    seal.run_id,
                    seal.episode_id,
                    seal.orchestrator_principal.principal_id,
                )
                if (
                    actual != expected
                    or row.id != seal.contract_id
                    or row.content_hash != seal.content_hash
                ):
                    raise ValueError("binding original does not match indexed identity")
                if (
                    row.workspace_id,
                    row.project_id,
                    row.contract_type,
                    row.schema_version,
                    row.logical_name,
                    row.version,
                    row.created_by,
                    row.created_at,
                ) != (
                    seal.workspace_id,
                    seal.project_id,
                    seal.contract_type,
                    seal.schema_version,
                    seal.contract_id,
                    seal.schema_version,
                    seal.created_by.principal_id,
                    seal.created_at,
                ):
                    raise ValueError("binding writer columns disagree")
            except (ValueError, TypeError) as exc:
                raise RoboticsError(Code.INVALID_CONTRACT) from exc
            run, task = await tx.run_task(run_id)
            if (
                run.principal_id != binding.initiator_id
                or run.project_id != binding.project_id
                or task.envelope.project_id != binding.project_id
            ):
                raise RoboticsError(Code.INVALID_CONTRACT)
            return binding

    async def lookup_task_owner(
        self, task_id: str, *, actor_id: str | None = None
    ) -> RuntimeBinding | None:
        """Prevent starting a coding run from an episode-owned experiment task."""
        run_id: str | None = None
        if isinstance(self.state, PostgresStore):
            async with self.state.sessions() as session:
                candidate = await session.scalar(
                    select(db.SimulationRunBindingRow.run_id)
                    .join(db.RunRow, db.RunRow.id == db.SimulationRunBindingRow.run_id)
                    .where(db.RunRow.task_id == task_id)
                    .limit(1)
                )
                run_id = candidate
        else:
            async with self.state.robotics_registry_lock:
                for payload in self.state.robotics_runtime_state.get("RuntimeBinding", {}).values():
                    run = self.state.runs.get(payload["run_id"])
                    if run is not None and run.task_id == task_id:
                        run_id = run.run_id
                        break
        return None if run_id is None else await self.lookup_run_owner(run_id, actor_id=actor_id)


def runtime_store_for(
    state: StateStore, *, clock: Callable[[], datetime] | None = None
) -> RuntimeStore:
    if isinstance(state, (MemoryStore, PostgresStore)):
        return RuntimeStore(state, clock=clock)
    raise RoboticsError(Code.SIMULATION_UNAVAILABLE)
