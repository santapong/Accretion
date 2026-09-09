"""Bounded original-writer and raw-observation loading for construction checks."""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from accretion.contracts import StrictModel
from accretion.contracts.canonical import canonical_json
from accretion.contracts.robotics import (
    ActionIntent,
    CanonicalWriterEnvelope,
    EmbodiedVerificationSpec,
    EmbodimentDescriptor,
    EpisodeRecord,
    ObservationSpec,
    PreparedCommand,
    RobotAdapterManifest,
    SimulationActionReceipt,
    SimulationDomainEvent,
    SimulationEnvironmentSnapshot,
    SimulationEpisodeApproval,
    SimulationExperimentContract,
    SimulationPreflightReceipt,
)
from accretion.contracts.robotics.models import RoboticsContract, SafetyEnvelope
from accretion.contracts.robotics.values import (
    ContentAddressedArtifactRef,
    ObservationField,
    SimulationArtifactRef,
)
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.observations import (
    ArtifactReader,
    ObservationBatch,
    ObservationValidator,
    verified_chunks,
)
from accretion.robotics.protocol import (
    ProtocolRequest,
    ProtocolResponse,
    bounded_json,
    parse_message,
    validate_response_binding,
)
from accretion.robotics.safety import GeometryEvidence, SafetyEvaluation

from .types import (
    ActionChunkV1,
    ActionEvidenceRow,
    ActualSafetyRow,
    ChunkBase,
    ChunkManifestV1,
    EpisodeProvenanceV1,
    EvidenceReadLimits,
    EvidenceScope,
    MetricChunkV1,
    RecoveryLinkV1,
    ReplayToleranceProfileV1,
    SafetyChunkV1,
    SafetyIssuanceV1,
    SensorChunkV1,
    TerminationPayloadV1,
    TraceIndexRow,
    TrajectoryChunkV1,
    TrustedVerificationBinding,
    VerifierConfigurationV1,
)

FORMATS = {"float64": "d", "float32": "f", "uint8": "B", "int64": "q", "bool": "B"}


class EvidenceProblem(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise EvidenceProblem(reason)


class EvidenceReader:
    """Verify every complete read; account for unique content before allocation.

    The wrapped reader must authorize scope and bound chunks before yielding.
    Cached immutable bytes prevent later reads from substituting a different
    value. Different metadata for one digest is refused instead of relabelling.
    """

    def __init__(self, source: ArtifactReader, limits: EvidenceReadLimits) -> None:
        self.source, self.limits = source, limits
        self.cache: dict[str, tuple[ContentAddressedArtifactRef, bytes]] = {}
        self.total = 0

    def read(self, reference: ContentAddressedArtifactRef, *, max_bytes: int) -> bytes:
        ref = ContentAddressedArtifactRef.model_validate(reference.model_dump(mode="python"))
        require(ref.size_bytes <= max_bytes, "ARTIFACT_SIZE_CAP")
        previous = self.cache.get(ref.digest)
        if previous is not None:
            require(previous[0] == ref, "ARTIFACT_METADATA_SUBSTITUTION")
            return previous[1]
        if len(self.cache) >= self.limits.max_artifacts or (
            self.total + ref.size_bytes > self.limits.max_total_bytes
        ):
            raise RoboticsError(Code.RESOURCE_CAP_EXHAUSTED)
        raw = b"".join(verified_chunks(self.source, ref, max_bytes=max_bytes))
        self.cache[ref.digest] = (ref, raw)
        self.total += len(raw)
        return raw

    def iter_bytes(
        self, ref: ContentAddressedArtifactRef, *, max_bytes: int, chunk_size: int
    ) -> Iterator[bytes]:
        raw = self.read(ref, max_bytes=max_bytes)
        for offset in range(0, len(raw), chunk_size):
            yield raw[offset : offset + chunk_size]

    def json[T: StrictModel](self, reference: SimulationArtifactRef, model: type[T]) -> T:
        ref = SimulationArtifactRef.model_validate(reference.model_dump(mode="python"))
        require(ref.media_type == "application/json", "JSON_MEDIA_TYPE")
        raw = self.read(ref, max_bytes=self.limits.max_json_bytes)
        parsed = model.model_validate(bounded_json(raw, max_bytes=self.limits.max_json_bytes))
        require(canonical_json(parsed) == raw, "NONCANONICAL_ARTIFACT")
        return parsed

    def writer[T: RoboticsContract](self, reference: SimulationArtifactRef, model: type[T]) -> T:
        ref = SimulationArtifactRef.model_validate(reference.model_dump(mode="python"))
        require(ref.media_type == "application/json", "WRITER_MEDIA_TYPE")
        raw = self.read(ref, max_bytes=self.limits.max_json_bytes)
        bounded_json(raw, max_bytes=self.limits.max_json_bytes)
        return CanonicalWriterEnvelope(raw.decode()).for_execution(model)

    def protocol(self, reference: SimulationArtifactRef) -> ProtocolRequest | ProtocolResponse:
        require(reference.media_type == "application/json", "PROTOCOL_MEDIA_TYPE")
        return parse_message(self.read(reference, max_bytes=self.limits.max_json_bytes))

    def chunks[T: ChunkBase](
        self, ref: SimulationArtifactRef, scope: EvidenceScope, model: type[T], role: str
    ) -> tuple[Any, ...]:
        manifest = self.json(ref, ChunkManifestV1)
        require(manifest.scope == scope and manifest.role == role, "MANIFEST_SCOPE_ROLE")
        require(manifest.total_records <= self.limits.max_records, "RECORD_COUNT_CAP")
        result: list[Any] = []
        for chunk in manifest.chunks:
            document = self.json(chunk.artifact, model)
            rows: tuple[Any, ...] = document.records  # type: ignore[attr-defined]
            require(
                document.scope == scope and document.first_index == chunk.first_index,
                "CHUNK_SCOPE_INDEX",
            )
            require(len(rows) == chunk.record_count, "CHUNK_RECORD_COUNT")
            result.extend(rows)
        require(len(result) == manifest.total_records, "MANIFEST_RECORD_COUNT")
        return tuple(result)


@dataclass(frozen=True, slots=True)
class ObservedChannel:
    field: ObservationField
    raw: bytes
    sim_time_ns: int

    def numbers(self, *, maximum: int = 64) -> tuple[float, ...]:
        size = struct.calcsize("<" + FORMATS[self.field.dtype])
        require(len(self.raw) // size <= maximum, "TASK_CHANNEL_SHAPE")
        return tuple(
            float(value)
            for (value,) in struct.iter_unpack("<" + FORMATS[self.field.dtype], self.raw)
        )


@dataclass(frozen=True, slots=True)
class ObservedFrame:
    index: TraceIndexRow
    batch: ObservationBatch
    channels: dict[str, ObservedChannel]


@dataclass(frozen=True, slots=True)
class LoadedAction:
    row: ActionEvidenceRow
    intent: ActionIntent
    prepared: PreparedCommand
    issuance: SafetyEvaluation
    receipts: tuple[SimulationActionReceipt, ...]
    request: ProtocolRequest | None
    response: ProtocolResponse | None


@dataclass(frozen=True, slots=True)
class LoadedRecovery:
    link: RecoveryLinkV1
    source: EpisodeRecord
    source_provenance: EpisodeProvenanceV1
    event: SimulationDomainEvent
    termination: TerminationPayloadV1
    reset: ObservationBatch
    preflight: SimulationPreflightReceipt
    approval: SimulationEpisodeApproval


@dataclass(frozen=True, slots=True)
class LoadedEpisode:
    record: EpisodeRecord
    provenance: EpisodeProvenanceV1
    configuration: VerifierConfigurationV1
    experiment: SimulationExperimentContract
    environment: SimulationEnvironmentSnapshot
    descriptor: EmbodimentDescriptor
    observation_spec: ObservationSpec
    safety_envelope: SafetyEnvelope
    verification_spec: EmbodiedVerificationSpec
    preflight: SimulationPreflightReceipt
    approval: SimulationEpisodeApproval
    adapter: RobotAdapterManifest
    frames: tuple[ObservedFrame, ...]
    actions: tuple[LoadedAction, ...]
    safety: tuple[ActualSafetyRow, ...]
    geometry: GeometryEvidence
    termination_event: SimulationDomainEvent
    termination: TerminationPayloadV1
    reset_request: ProtocolRequest
    reset_response: ProtocolResponse
    replay_tolerances: ReplayToleranceProfileV1
    recovery: LoadedRecovery | None


def _load_action(row: ActionEvidenceRow, reader: EvidenceReader) -> LoadedAction:
    request = None if row.request_ref is None else reader.protocol(row.request_ref)
    response = None if row.response_ref is None else reader.protocol(row.response_ref)
    require(request is None or isinstance(request, ProtocolRequest), "REQUEST_TYPE")
    require(response is None or isinstance(response, ProtocolResponse), "ACK_TYPE")
    if response is not None:
        require(request is not None, "ACK_WITHOUT_REQUEST")
        assert isinstance(request, ProtocolRequest) and isinstance(response, ProtocolResponse)
        validate_response_binding(request, response)
    assert request is None or isinstance(request, ProtocolRequest)
    assert response is None or isinstance(response, ProtocolResponse)
    return LoadedAction(
        row,
        reader.writer(row.intent_ref, ActionIntent),
        reader.writer(row.prepared_ref, PreparedCommand),
        reader.json(row.safety_issuance_ref, SafetyIssuanceV1).evaluation(),
        tuple(reader.writer(ref, SimulationActionReceipt) for ref in row.receipt_refs),
        request,
        response,
    )


def load_episode(
    original: CanonicalWriterEnvelope, binding: TrustedVerificationBinding, source: ArtifactReader
) -> LoadedEpisode:
    """Load pure construction input; this does not authenticate a verifier process."""
    binding = binding.snapshot()
    record = original.for_execution(EpisodeRecord)
    scope = EvidenceScope(
        workspace_id=record.workspace_id,
        project_id=str(record.project_id),
        episode_id=record.episode_id,
        run_id=record.run_id,
    )
    require(scope == binding.scope, "TRUSTED_SCOPE_MISMATCH")
    reader = EvidenceReader(source, binding.limits)
    provenance = reader.json(record.provenance_manifest_ref, EpisodeProvenanceV1)
    require(provenance.scope == scope, "PROVENANCE_SCOPE")
    require(
        provenance.pins == binding.pins and provenance.dependencies == binding.dependencies,
        "TRUSTED_CLOSURE_MISMATCH",
    )
    require(
        provenance.verifier_configuration_ref.digest == binding.configuration_digest,
        "UNTRUSTED_CONFIGURATION",
    )
    config = reader.json(provenance.verifier_configuration_ref, VerifierConfigurationV1)
    require(canonical_json(config) == binding.configuration_bytes, "UNTRUSTED_CONFIGURATION")
    sources = provenance.contracts
    experiment = reader.writer(sources.experiment, SimulationExperimentContract)
    environment = reader.writer(sources.environment, SimulationEnvironmentSnapshot)
    descriptor = reader.writer(sources.descriptor, EmbodimentDescriptor)
    observations = reader.writer(sources.observations, ObservationSpec)
    envelope = reader.writer(sources.safety_envelope, SafetyEnvelope)
    spec = reader.writer(sources.verification_spec, EmbodiedVerificationSpec)
    preflight = reader.writer(sources.preflight, SimulationPreflightReceipt)
    approval = reader.writer(sources.approval, SimulationEpisodeApproval)
    adapter = reader.writer(sources.adapter_manifest, RobotAdapterManifest)
    for document in (
        experiment,
        environment,
        descriptor,
        observations,
        envelope,
        spec,
        preflight,
        approval,
        adapter,
    ):
        require(
            (document.workspace_id, document.project_id) == (scope.workspace_id, scope.project_id),
            "WRITER_SCOPE_MISMATCH",
        )
    for key, ref in (
        ("trajectory", record.trajectory_ref),
        ("sensors", record.sensor_manifest_ref),
        ("actions", record.action_receipts_ref),
        ("safety", record.safety_events_ref),
        ("metrics", record.metrics_ref),
    ):
        require(getattr(provenance.artifacts, key) == ref, "EPISODE_ARTIFACT_SUBSTITUTION")
    trace = reader.chunks(record.trajectory_ref, scope, TrajectoryChunkV1, "TRAJECTORY")
    batches = reader.chunks(record.sensor_manifest_ref, scope, SensorChunkV1, "SENSORS")
    require(len(trace) == len(batches) and len(trace) >= 2, "OBSERVATION_COVERAGE")
    frames: list[ObservedFrame] = []
    previous = None
    validator = ObservationValidator()
    for index, (entry, batch) in enumerate(zip(trace, batches, strict=True)):
        require(
            entry.index == index and entry.state == batch.state_binding(), "TRACE_STATE_BINDING"
        )
        validator.validate(
            observations,
            descriptor,
            batch,
            reader,
            episode_id=record.episode_id,
            lease=binding.pins.lease,
            previous=previous,
        )
        previous = batch.state_binding()
        channels = {
            sample.field.field: ObservedChannel(
                sample.field,
                reader.read(sample.artifact, max_bytes=validator.limits.tensor_bytes),
                sample.sim_time_ns,
            )
            for sample in batch.samples
        }
        frames.append(ObservedFrame(entry, batch, channels))
    actions = tuple(
        _load_action(row, reader)
        for row in reader.chunks(record.action_receipts_ref, scope, ActionChunkV1, "ACTIONS")
    )
    safety = reader.chunks(record.safety_events_ref, scope, SafetyChunkV1, "SAFETY")
    reader.chunks(record.metrics_ref, scope, MetricChunkV1, "METRICS")
    require(provenance.geometry_ref == binding.expected_geometry_ref, "UNTRUSTED_GEOMETRY")
    geometry = reader.json(provenance.geometry_ref, GeometryEvidence)
    event = reader.writer(provenance.termination_event_ref, SimulationDomainEvent)
    termination = TerminationPayloadV1.model_validate(event.payload)
    request, response = (
        reader.protocol(provenance.reset_request_ref),
        reader.protocol(provenance.reset_response_ref),
    )
    require(
        isinstance(request, ProtocolRequest) and isinstance(response, ProtocolResponse),
        "RESET_PROTOCOL_TYPES",
    )
    assert isinstance(request, ProtocolRequest) and isinstance(response, ProtocolResponse)
    validate_response_binding(request, response)
    require(
        provenance.policy_and_routing_refs == binding.expected_ancillary_artifacts,
        "ANCILLARY_INVENTORY_NOT_TRUSTED",
    )
    for ref in provenance.policy_and_routing_refs:
        reader.read(ref, max_bytes=binding.limits.max_json_bytes)
    tolerances = reader.json(config.replay_tolerance_ref, ReplayToleranceProfileV1)
    recovery = None
    if binding.expected_recovery_link_digest is not None:
        require(provenance.recovery_link_ref is not None, "FRESH_RECOVERY_LINK_REQUIRED")
    if provenance.recovery_link_ref is not None:
        require(binding.expected_recovery_link_digest is not None, "FRESH_RECOVERY_LINK_REQUIRED")
        require(
            binding.expected_recovery_link_digest == provenance.recovery_link_ref.digest,
            "UNTRUSTED_RECOVERY_LINK",
        )
        link = reader.json(provenance.recovery_link_ref, RecoveryLinkV1)
        source_record = reader.writer(link.source_episode_record_ref, EpisodeRecord)
        source_event = reader.writer(link.source_termination_event_ref, SimulationDomainEvent)
        recovery = LoadedRecovery(
            link,
            source_record,
            reader.json(source_record.provenance_manifest_ref, EpisodeProvenanceV1),
            source_event,
            TerminationPayloadV1.model_validate(source_event.payload),
            reader.json(link.reset_batch_ref, ObservationBatch),
            reader.writer(link.preflight_ref, SimulationPreflightReceipt),
            reader.writer(link.approval_ref, SimulationEpisodeApproval),
        )
    return LoadedEpisode(
        record,
        provenance,
        config,
        experiment,
        environment,
        descriptor,
        observations,
        envelope,
        spec,
        preflight,
        approval,
        adapter,
        tuple(frames),
        actions,
        safety,
        geometry,
        event,
        termination,
        request,
        response,
        tolerances,
        recovery,
    )
