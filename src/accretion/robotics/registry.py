"""Scoped immutable robotics declarations and independently attested conformance.

Registration grants no capabilities. A conformance report becomes usable only
through an explicitly configured trusted verifier, a current independent service
identity, and equality with the entire requested dependency closure. No public
registration accepts a report, approval, lease or episode execution record.
"""

from __future__ import annotations

import base64
import builtins
import json
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

from pydantic import Field
from sqlalchemy.exc import IntegrityError

from accretion.contracts import Principal, PrincipalRef, PrincipalStatus, StrictModel
from accretion.contracts.canonical import CanonicalContract, canonical_json, content_hash
from accretion.contracts.refs import VerifierRef
from accretion.contracts.robotics import (
    AdapterConformanceReport,
    CanonicalWriterEnvelope,
    EmbodiedVerificationSpec,
    EmbodimentDescriptor,
    ObservationSpec,
    RobotAdapterManifest,
    SafetyEnvelope,
    SimulationDomainEvent,
)
from accretion.contracts.robotics.values import DependencyClosure, RoboticsContractRef
from accretion.ids import new_id
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.store import (
    ConformanceLink,
    IdempotencyScope,
    LedgerRecord,
    RegistryRecord,
    RegistryTransaction,
    RoboticsStore,
    StoredEvent,
)

MAX_WRITER_BYTES = 1_048_576
REGISTRY_MODELS: dict[str, type[CanonicalContract]] = {
    model.CONTRACT_TYPE: model
    for model in (
        EmbodimentDescriptor,
        ObservationSpec,
        RobotAdapterManifest,
        SafetyEnvelope,
        EmbodiedVerificationSpec,
    )
}
_READ_MODELS: dict[str, type[CanonicalContract]] = {
    **REGISTRY_MODELS,
    AdapterConformanceReport.CONTRACT_TYPE: AdapterConformanceReport,
}


class AdapterArtifactVerifier(Protocol):
    async def verify(self, manifest: CanonicalWriterEnvelope) -> None:
        """Verify retained artifact bytes and signature against configured trust."""
        ...


class ConformanceAuthority(Protocol):
    principal_id: str
    verifier: VerifierRef
    suite_version: str
    suite_artifact_digest: str

    async def verify(self, report: CanonicalWriterEnvelope) -> None:
        """Authenticate a result from an admitted independent host execution.

        A PASS label, two different strings or a caller-supplied public key are
        insufficient. Deployment supplies the trust root and pinned runner.
        """
        ...


class ConformanceSummary(StrictModel):
    report_ref: RoboticsContractRef
    closure_hash: str
    result: str
    verifier_principal_id: str


class RegistryEntry(StrictModel):
    contract_id: str
    contract_type: str
    logical_name: str
    version: str
    schema_version: str
    content_hash: str
    workspace_id: str
    project_id: str
    original_json: str
    revision: int = Field(ge=1)
    conformance_status: Literal["PENDING", "PASS", "FAIL", "INCONCLUSIVE"] = "PENDING"
    conformance_reports: builtins.list[ConformanceSummary] = Field(default_factory=list)

    @property
    def envelope(self) -> CanonicalWriterEnvelope:
        return CanonicalWriterEnvelope(self.original_json)


class RegistryPage(StrictModel):
    items: builtins.list[RegistryEntry]
    next_cursor: str | None = None


def _logical(model: CanonicalContract) -> tuple[str, str]:
    if isinstance(model, EmbodimentDescriptor):
        return model.embodiment_id, model.descriptor_version
    if isinstance(model, RobotAdapterManifest):
        return model.adapter_id, model.adapter_version
    return model.contract_id, model.schema_version


def _parse(
    original_json: str, *, report: bool = False
) -> tuple[CanonicalWriterEnvelope, CanonicalContract]:
    try:
        if len(original_json) > MAX_WRITER_BYTES or len(original_json.encode()) > MAX_WRITER_BYTES:
            raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
        envelope = CanonicalWriterEnvelope(original_json)
        models = _READ_MODELS if report else REGISTRY_MODELS
        model_type = models.get(envelope.payload()["contract_type"])
        if model_type is None:
            raise RoboticsError(Code.INVALID_CONTRACT)
        return envelope, envelope.read_projection(model_type)
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise RoboticsError(Code.INVALID_CONTRACT) from error


def _record(envelope: CanonicalWriterEnvelope, model: CanonicalContract) -> RegistryRecord:
    name, version = _logical(model)
    payload = envelope.payload()
    if not isinstance(model, (EmbodimentDescriptor, RobotAdapterManifest)):
        version = payload["schema_version"]
    if len(name) > 255 or len(version) > 64 or len(payload["schema_version"]) > 32:
        raise RoboticsError(Code.INVALID_CONTRACT)
    return RegistryRecord(
        id=model.contract_id,
        workspace_id=model.workspace_id,
        project_id=str(model.project_id),
        contract_type=model.contract_type,
        logical_name=name,
        version=version,
        schema_version=payload["schema_version"],
        content_hash=envelope.writer_content_hash,
        original_json=envelope.original_json,
        created_by=model.created_by.principal_id,
        created_at=model.created_at,
    )


def _checked(row: RegistryRecord) -> tuple[CanonicalWriterEnvelope, CanonicalContract]:
    envelope, model = _parse(row.original_json, report=True)
    expected = _record(envelope, model)
    if (
        any(
            getattr(row, key) != getattr(expected, key)
            for key in RegistryRecord.__dataclass_fields__
            if key != "revision"
        )
        or row.revision < 1
    ):
        raise RoboticsError(Code.INVALID_CONTRACT)
    return envelope, model


def _scoped(row: RegistryRecord | None, workspace_id: str, project_id: str) -> RegistryRecord:
    if row is None or (row.workspace_id, row.project_id) != (workspace_id, project_id):
        raise RoboticsError(Code.RESOURCE_NOT_FOUND)
    _checked(row)
    return row


def _entry(row: RegistryRecord) -> RegistryEntry:
    _checked(row)
    return RegistryEntry(
        contract_id=row.id,
        contract_type=row.contract_type,
        logical_name=row.logical_name,
        version=row.version,
        schema_version=row.schema_version,
        content_hash=row.content_hash,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        original_json=row.original_json,
        revision=row.revision,
    )


def _key(value: str) -> str:
    if not value:
        raise RoboticsError(Code.IDEMPOTENCY_REQUIRED)
    if len(value) > 128 or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise RoboticsError(Code.INVALID_REQUEST)
    return value


def _limit(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise RoboticsError(Code.INVALID_REQUEST)


def _principal_ref(principal: Principal) -> PrincipalRef:
    return PrincipalRef(principal_id=principal.principal_id, status=principal.status)


class RoboticsRegistry:
    def __init__(
        self,
        store: RoboticsStore,
        *,
        artifact_verifier: AdapterArtifactVerifier | None = None,
        conformance_authority: ConformanceAuthority | None = None,
    ) -> None:
        self.store = store
        self.artifact_verifier = artifact_verifier
        self.conformance_authority = conformance_authority

    async def _replay(
        self, tx: RegistryTransaction, scope: IdempotencyScope, digest: str
    ) -> RegistryEntry | None:
        ledger = await tx.ledger(scope)
        if ledger is None:
            return None
        if ledger.request_hash != digest:
            raise RoboticsError(Code.IDEMPOTENCY_CONFLICT)
        try:
            entry = RegistryEntry.model_validate_json(ledger.response_json)
            row = _scoped(await tx.get(entry.contract_id), scope.workspace_id, scope.project_id)
            if (
                entry.content_hash != row.content_hash
                or entry.original_json != row.original_json
                or entry.revision > row.revision
            ):
                raise RoboticsError(Code.INVALID_CONTRACT)
            # Check every response identity against the original writer too.
            expected = _entry(row).model_copy(update={"revision": entry.revision})
            if entry != expected:
                raise RoboticsError(Code.INVALID_CONTRACT)
            return entry
        except (ValueError, TypeError) as error:
            raise RoboticsError(Code.INVALID_CONTRACT) from error

    async def _by_hash[C: CanonicalContract](
        self,
        tx: RegistryTransaction,
        workspace_id: str,
        project_id: str,
        model: type[C],
        digest: str,
    ) -> C:
        row = _scoped(
            await tx.lookup(workspace_id, project_id, model.CONTRACT_TYPE, digest=digest),
            workspace_id,
            project_id,
        )
        envelope, _ = _checked(row)
        try:
            return envelope.for_execution(model)
        except ValueError as error:
            raise RoboticsError(Code.UNKNOWN_CONTRACT_VERSION) from error

    async def _references(self, tx: RegistryTransaction, model: CanonicalContract) -> None:
        workspace, project = model.workspace_id, str(model.project_id)
        if model.objective_contract_ref is not None:
            # Objective/node-owned frozen experiments arrive through M4. A M1
            # declaration cannot silently preserve an unresolved authority ref.
            raise RoboticsError(Code.INVALID_CONTRACT)
        if model.supersedes_contract_id:
            previous = _scoped(await tx.get(model.supersedes_contract_id), workspace, project)
            if (
                previous.contract_type != model.contract_type
                or previous.logical_name != _logical(model)[0]
            ):
                raise RoboticsError(Code.INVALID_CONTRACT)
        if isinstance(model, RobotAdapterManifest):
            observations = await self._by_hash(
                tx, workspace, project, ObservationSpec, model.observation_spec_hash
            )
            for digest in model.embodiment_descriptor_hashes:
                descriptor = await self._by_hash(
                    tx, workspace, project, EmbodimentDescriptor, digest
                )
                if not set(model.controller_modes) <= set(descriptor.control_interfaces):
                    raise RoboticsError(Code.INVALID_CONTRACT)
                sensors = {sensor.sensor_id: sensor for sensor in descriptor.sensors}
                for field in observations.required:
                    sensor = sensors.get(field.sensor_id)
                    if (
                        sensor is None
                        or sensor.modality != field.modality
                        or sensor.frame_id != field.frame_id
                    ):
                        raise RoboticsError(Code.INVALID_CONTRACT)
                    if field.modality == "JOINT_STATE" and field.shape != [
                        len(descriptor.kinematics.joint_names)
                    ]:
                        raise RoboticsError(Code.INVALID_CONTRACT)
        elif isinstance(model, SafetyEnvelope):
            descriptor = await self._by_hash(
                tx, workspace, project, EmbodimentDescriptor, model.embodiment_descriptor_hash
            )
            if (
                not set(model.controller_modes) <= set(descriptor.control_interfaces)
                or [limit.joint_name for limit in model.joint_limits]
                != descriptor.kinematics.joint_names
            ):
                raise RoboticsError(Code.INVALID_CONTRACT)

    async def _emit(
        self,
        tx: RegistryTransaction,
        row: RegistryRecord,
        principal: Principal,
        event_type: str,
        *,
        sequence: int,
        extra: dict[str, Any] | None = None,
    ) -> None:
        event = SimulationDomainEvent.model_validate(
            {
                "contract_id": new_id("simulation_domain_event"),
                "workspace_id": row.workspace_id,
                "project_id": row.project_id,
                "created_by": _principal_ref(principal),
                "event_type": event_type,
                "occurred_at": datetime.now(UTC),
                "correlation_id": row.id,
                "producer": _principal_ref(principal),
                "sequence": sequence,
                "payload": {
                    "contract_id": row.id,
                    "contract_type": row.contract_type,
                    "content_hash": row.content_hash,
                    "revision": sequence,
                    **(extra or {}),
                },
            }
        )
        envelope = CanonicalWriterEnvelope.from_contract(event)
        await tx.append_event(
            StoredEvent(
                id=event.contract_id,
                aggregate_id=row.id,
                workspace_id=row.workspace_id,
                project_id=row.project_id,
                sequence=sequence,
                content_hash=event.content_hash,
                original_json=envelope.original_json,
            )
        )

    async def register(
        self,
        *,
        actor_id: str,
        workspace_id: str,
        project_id: str,
        original_json: str,
        idempotency_key: str,
    ) -> RegistryEntry:
        envelope, model = _parse(original_json)
        if (model.workspace_id, model.project_id) != (workspace_id, project_id):
            raise RoboticsError(Code.RESOURCE_NOT_FOUND)
        if (
            model.created_by.principal_id != actor_id
            or model.created_by.status is not PrincipalStatus.ACTIVE
        ):
            raise RoboticsError(Code.CAPABILITY_DENIED)
        row = _record(envelope, model)
        scope = IdempotencyScope(
            workspace_id,
            project_id,
            actor_id,
            "register",
            model.contract_type,
            _key(idempotency_key),
        )
        digest = content_hash(envelope.payload(), exclude=())
        # Perform potentially asynchronous artifact IO outside the commit lock;
        # authorize and resolve again afterwards to close revocation races.
        async with self.store.transaction(project_id) as tx:
            await tx.authorize(actor_id, workspace_id, project_id, write=True)
            replay = await self._replay(tx, scope, digest)
            if replay is not None:
                return replay
            await self._references(tx, model)
        if isinstance(model, RobotAdapterManifest):
            if self.artifact_verifier is None:
                raise RoboticsError(Code.ARTIFACT_UNAVAILABLE)
            try:
                envelope.for_execution(RobotAdapterManifest)
                await self.artifact_verifier.verify(envelope)
            except (ValueError, OSError) as error:
                raise RoboticsError(Code.ARTIFACT_INVALID) from error
        try:
            async with self.store.transaction(project_id) as tx:
                principal = await tx.authorize(actor_id, workspace_id, project_id, write=True)
                replay = await self._replay(tx, scope, digest)
                if replay is not None:
                    return replay
                await self._references(tx, model)
                await tx.insert(row)
                event_type = (
                    "embodiment.registered"
                    if isinstance(model, EmbodimentDescriptor)
                    else "robot_adapter.registered"
                    if isinstance(model, RobotAdapterManifest)
                    else "simulation_contract.registered"
                )
                await self._emit(tx, row, principal, event_type, sequence=1)
                result = _entry(row)
                await tx.remember(LedgerRecord(scope, digest, result.model_dump_json()))
                return result
        except IntegrityError as error:
            raise RoboticsError(Code.CONTRACT_CONFLICT) from error

    async def _summaries(
        self,
        tx: RegistryTransaction,
        row: RegistryRecord,
        entry: RegistryEntry,
        dependencies: DependencyClosure | None,
    ) -> RegistryEntry:
        if row.contract_type != RobotAdapterManifest.CONTRACT_TYPE:
            return entry
        authority = self.conformance_authority
        closure = content_hash(dependencies, exclude=()) if dependencies is not None else None
        links = await tx.conformance(row.id)
        exact = await tx.conformance(row.id, closure, 1) if closure else []
        current = exact[0] if exact else None
        if current and current not in links:
            links = [current, *links[:99]]
        for link in links:
            report_row = _scoped(await tx.get(link.report_id), row.workspace_id, row.project_id)
            envelope, report = _checked(report_row)
            try:
                report = envelope.for_execution(AdapterConformanceReport)
            except ValueError as error:
                raise RoboticsError(Code.INVALID_CONTRACT) from error
            if (
                not isinstance(report, AdapterConformanceReport)
                or report.adapter_manifest_hash != row.content_hash
                or content_hash(report.dependencies, exclude=()) != link.closure_hash
                or link.adapter_id != row.id
                or link.revision > row.revision
            ):
                raise RoboticsError(Code.INVALID_CONTRACT)
            entry.conformance_reports.append(
                ConformanceSummary(
                    report_ref=RoboticsContractRef(
                        contract_id=report_row.id,
                        content_hash=report_row.content_hash,
                        schema_version=report_row.schema_version,
                    ),
                    closure_hash=link.closure_hash,
                    result=report.result.value,
                    verifier_principal_id=report.verifier_principal.principal_id,
                )
            )
            if current == link and authority:
                try:
                    self._authority_binding(report, row)
                    await tx.authorize(
                        authority.principal_id, row.workspace_id, row.project_id, service=True
                    )
                    await authority.verify(envelope)
                    await tx.authorize(
                        authority.principal_id, row.workspace_id, row.project_id, service=True
                    )
                except (RoboticsError, ValueError, OSError):
                    # Retain the report, but a revoked/unavailable trust binding
                    # cannot remain a current passing qualification.
                    continue
                entry.conformance_status = cast(
                    Literal["PASS", "FAIL", "INCONCLUSIVE"], report.result.value
                )
        return entry

    async def get(
        self,
        *,
        actor_id: str,
        workspace_id: str,
        project_id: str,
        contract_id: str,
        version: str | None = None,
        contract_type: str | None = None,
        dependencies: DependencyClosure | None = None,
    ) -> RegistryEntry:
        async with self.store.transaction(project_id) as tx:
            await tx.authorize(actor_id, workspace_id, project_id)
            row = await tx.get(contract_id)
            if row is None and version is not None and contract_type in REGISTRY_MODELS:
                row = await tx.lookup(
                    workspace_id,
                    project_id,
                    str(contract_type),
                    logical_name=contract_id,
                    version=version,
                )
            row = _scoped(row, workspace_id, project_id)
            if (version is not None and row.version != version) or (
                contract_type is not None and row.contract_type != contract_type
            ):
                raise RoboticsError(Code.RESOURCE_NOT_FOUND)
            return await self._summaries(tx, row, _entry(row), dependencies)

    async def list(
        self,
        *,
        actor_id: str,
        workspace_id: str,
        project_id: str,
        contract_type: str,
        limit: int = 25,
        cursor: str | None = None,
    ) -> RegistryPage:
        _limit(limit)
        if contract_type not in _READ_MODELS:
            raise RoboticsError(Code.INVALID_REQUEST)
        after = ""
        if cursor:
            try:
                if len(cursor) > 2048:
                    raise ValueError("oversized cursor")
                decoded = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
                if (
                    not isinstance(decoded, list)
                    or len(decoded) != 4
                    or decoded[:3] != [workspace_id, project_id, contract_type]
                    or not isinstance(decoded[3], str)
                ):
                    raise ValueError("cursor scope mismatch")
                after = decoded[3]
            except (ValueError, TypeError) as error:
                raise RoboticsError(Code.INVALID_REQUEST) from error
        async with self.store.transaction(project_id) as tx:
            await tx.authorize(actor_id, workspace_id, project_id)
            rows = await tx.list(workspace_id, project_id, contract_type, after, limit + 1)
            entries = [
                await self._summaries(tx, _scoped(row, workspace_id, project_id), _entry(row), None)
                for row in rows[:limit]
            ]
            next_cursor = None
            if len(rows) > limit:
                next_cursor = base64.urlsafe_b64encode(
                    canonical_json([workspace_id, project_id, contract_type, rows[limit - 1].id])
                ).decode()
            return RegistryPage(items=entries, next_cursor=next_cursor)

    async def events(
        self,
        *,
        actor_id: str,
        workspace_id: str,
        project_id: str,
        aggregate_id: str,
        after_sequence: int = 0,
        limit: int = 25,
    ) -> builtins.list[CanonicalWriterEnvelope]:
        _limit(limit)
        if type(after_sequence) is not int or after_sequence < 0:
            raise RoboticsError(Code.INVALID_REQUEST)
        async with self.store.transaction(project_id) as tx:
            await tx.authorize(actor_id, workspace_id, project_id)
            aggregate = _scoped(await tx.get(aggregate_id), workspace_id, project_id)
            result = []
            for row in await tx.events(aggregate_id, after_sequence, limit):
                try:
                    envelope = CanonicalWriterEnvelope(row.original_json)
                    event = envelope.for_execution(SimulationDomainEvent)
                    if (
                        event.contract_id != row.id
                        or envelope.writer_content_hash != row.content_hash
                        or (event.workspace_id, event.project_id) != (workspace_id, project_id)
                        or (row.workspace_id, row.project_id) != (workspace_id, project_id)
                        or event.correlation_id != aggregate_id
                        or row.aggregate_id != aggregate_id
                        or event.sequence != row.sequence
                        or event.sequence > aggregate.revision
                    ):
                        raise ValueError("event index does not match its writer")
                    result.append(envelope)
                except (ValueError, TypeError) as error:
                    raise RoboticsError(Code.INVALID_CONTRACT) from error
            return result

    def _authority_binding(self, report: AdapterConformanceReport, adapter: RegistryRecord) -> None:
        authority = self.conformance_authority
        if authority is None:
            raise RoboticsError(Code.VERIFIER_UNAVAILABLE)
        if (
            report.verifier_principal.principal_id != authority.principal_id
            or report.created_by.principal_id != authority.principal_id
            or report.adapter_producer_principal.principal_id != adapter.created_by
            or report.verifier != authority.verifier
            or report.suite_version != authority.suite_version
            or report.suite_artifact_digest != authority.suite_artifact_digest
        ):
            raise RoboticsError(Code.CONFORMANCE_STALE)

    async def record_conformance(
        self,
        *,
        actor_id: str,
        workspace_id: str,
        project_id: str,
        adapter_contract_id: str,
        original_json: str,
        dependencies: DependencyClosure,
        expected_revision: int,
        idempotency_key: str,
    ) -> RegistryEntry:
        """Internal host-result hook; never expose a caller-provided PASS endpoint."""
        if type(expected_revision) is not int or expected_revision < 1:
            raise RoboticsError(Code.REVISION_REQUIRED)
        envelope, model = _parse(original_json, report=True)
        if not isinstance(model, AdapterConformanceReport):
            raise RoboticsError(Code.INVALID_CONTRACT)
        try:
            report = envelope.for_execution(AdapterConformanceReport)
            dependencies = DependencyClosure.model_validate(dependencies.model_dump())
        except ValueError as error:
            raise RoboticsError(Code.INVALID_CONTRACT) from error
        if (report.workspace_id, report.project_id) != (workspace_id, project_id):
            raise RoboticsError(Code.RESOURCE_NOT_FOUND)
        if actor_id != report.created_by.principal_id:
            raise RoboticsError(Code.CAPABILITY_DENIED)
        if report.dependencies != dependencies:
            raise RoboticsError(Code.CONFORMANCE_STALE)
        scope = IdempotencyScope(
            workspace_id,
            project_id,
            actor_id,
            "record_conformance",
            adapter_contract_id,
            _key(idempotency_key),
        )
        digest = content_hash(
            {
                "report": envelope.payload(),
                "dependencies": dependencies,
                "expected_revision": expected_revision,
            },
            exclude=(),
        )
        async with self.store.transaction(project_id) as tx:
            await tx.authorize(actor_id, workspace_id, project_id, service=True)
            adapter = _scoped(await tx.get(adapter_contract_id), workspace_id, project_id)
            self._authority_binding(report, adapter)
            replay = await self._replay(tx, scope, digest)
            if replay is not None:
                return replay
        assert self.conformance_authority is not None
        try:
            await self.conformance_authority.verify(envelope)
        except (ValueError, OSError) as error:
            raise RoboticsError(Code.CONFORMANCE_STALE) from error
        try:
            async with self.store.transaction(project_id) as tx:
                principal = await tx.authorize(actor_id, workspace_id, project_id, service=True)
                adapter = _scoped(await tx.get(adapter_contract_id), workspace_id, project_id)
                self._authority_binding(report, adapter)
                replay = await self._replay(tx, scope, digest)
                if replay is not None:
                    return replay
                if adapter.revision != expected_revision:
                    raise RoboticsError(Code.REVISION_CONFLICT)
                try:
                    manifest = CanonicalWriterEnvelope(adapter.original_json).for_execution(
                        RobotAdapterManifest
                    )
                except ValueError as error:
                    raise RoboticsError(Code.CONFORMANCE_STALE) from error
                if (
                    report.adapter_manifest_hash != adapter.content_hash
                    or dependencies.adapter_artifact_digest != manifest.artifact_digest
                    or dependencies.observation_spec_hash != manifest.observation_spec_hash
                    or dependencies.action_intent_schema_hash != manifest.action_intent_schema_hash
                    or dependencies.prepared_command_schema_hash
                    != manifest.prepared_command_schema_hash
                ):
                    raise RoboticsError(Code.CONFORMANCE_STALE)
                robot_models = [
                    (
                        await self._by_hash(tx, workspace_id, project_id, EmbodimentDescriptor, d)
                    ).robot_model_ref.digest
                    for d in manifest.embodiment_descriptor_hashes
                ]
                if dependencies.robot_model_digest not in robot_models:
                    raise RoboticsError(Code.CONFORMANCE_STALE)
                row = _record(envelope, report)
                await tx.insert(row)
                revision = adapter.revision + 1
                await tx.set_revision(adapter.id, revision)
                await tx.link_conformance(
                    ConformanceLink(
                        row.id, adapter.id, content_hash(dependencies, exclude=()), revision
                    )
                )
                await self._emit(
                    tx,
                    adapter,
                    principal,
                    "robot_adapter.conformance_completed",
                    sequence=revision,
                    extra={
                        "report_id": row.id,
                        "report_hash": row.content_hash,
                        "result": report.result.value,
                    },
                )
                result = _entry(row)
                await tx.remember(LedgerRecord(scope, digest, result.model_dump_json()))
                return result
        except IntegrityError as error:
            raise RoboticsError(Code.CONTRACT_CONFLICT) from error
