"""Versioned recorder artifacts, not new registry contracts or authority grants.

These v1 payloads use canonical JSON and SIMULATION artifact references. Original
contract/protocol documents referenced by them retain their original bytes.
No artifact references the enclosing EpisodeRecord hash, avoiding a seal cycle.
All scientific thresholds are required configuration fields; none are defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from accretion.contracts import StrictModel
from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.refs import VerifierRef
from accretion.contracts.robotics import CanonicalWriterEnvelope
from accretion.contracts.robotics.values import (
    DependencyClosure,
    Digest,
    EpisodeId,
    EpisodePins,
    Finite,
    Identifier,
    Nonnegative,
    Positive,
    SequenceNumber,
    SimulationArtifactRef,
    StateBinding,
)
from accretion.robotics.observations import ObservationBatch
from accretion.robotics.safety import GeometryEvidence, Phase, PhysicsInterval, SafetyEvaluation

PositiveInt = Annotated[int, Field(gt=0, strict=True)]
Role = Literal["TRAJECTORY", "SENSORS", "ACTIONS", "SAFETY", "METRICS"]
FindingStatus = Literal["SATISFIED", "VIOLATED", "INCONCLUSIVE"]


class ArtifactModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceScope(ArtifactModel):
    workspace_id: Identifier
    project_id: Identifier
    episode_id: EpisodeId
    run_id: Identifier


class EpisodeArtifactRoles(ArtifactModel):
    trajectory: SimulationArtifactRef
    sensors: SimulationArtifactRef
    actions: SimulationArtifactRef
    safety: SimulationArtifactRef
    metrics: SimulationArtifactRef


class ContractSourcesV1(ArtifactModel):
    experiment: SimulationArtifactRef
    environment: SimulationArtifactRef
    descriptor: SimulationArtifactRef
    observations: SimulationArtifactRef
    safety_envelope: SimulationArtifactRef
    verification_spec: SimulationArtifactRef
    preflight: SimulationArtifactRef
    approval: SimulationArtifactRef
    adapter_manifest: SimulationArtifactRef


class EpisodeProvenanceV1(ArtifactModel):
    format: Literal["accretion.episode-provenance.v1"] = "accretion.episode-provenance.v1"
    scope: EvidenceScope
    pins: EpisodePins
    dependencies: DependencyClosure
    descriptor_hash: Digest
    observation_spec_hash: Digest
    verifier_configuration_ref: SimulationArtifactRef
    contracts: ContractSourcesV1
    artifacts: EpisodeArtifactRoles
    geometry_ref: SimulationArtifactRef
    reset_request_ref: SimulationArtifactRef
    reset_response_ref: SimulationArtifactRef
    termination_event_ref: SimulationArtifactRef
    policy_and_routing_refs: tuple[SimulationArtifactRef, ...] = Field(min_length=1, max_length=64)
    producer_principal_id: Identifier
    producer_process_id: Identifier
    initial_state: StateBinding
    final_state: StateBinding
    physics_step_ns: PositiveInt
    recovery_link_ref: SimulationArtifactRef | None = None


class EvidenceChunkRef(ArtifactModel):
    artifact: SimulationArtifactRef
    first_index: SequenceNumber
    record_count: PositiveInt


class ChunkManifestV1(ArtifactModel):
    format: Literal["accretion.episode-chunks.v1"] = "accretion.episode-chunks.v1"
    scope: EvidenceScope
    role: Role
    total_records: SequenceNumber
    chunks: tuple[EvidenceChunkRef, ...] = Field(max_length=1024)

    @model_validator(mode="after")
    def _coverage(self) -> Self:
        offset = 0
        for chunk in self.chunks:
            if chunk.first_index != offset:
                raise ValueError("chunk records must form one contiguous sequence")
            offset += chunk.record_count
        if offset != self.total_records:
            raise ValueError("manifest count disagrees with chunk coverage")
        return self


class TraceIndexRow(ArtifactModel):
    index: SequenceNumber
    state: StateBinding
    wall_time: datetime
    phase: Phase

    @model_validator(mode="after")
    def _time(self) -> Self:
        canonical_json(self)
        return self


class ActionEvidenceRow(ArtifactModel):
    sequence: SequenceNumber
    intent_ref: SimulationArtifactRef
    prepared_ref: SimulationArtifactRef
    safety_issuance_ref: SimulationArtifactRef
    receipt_refs: tuple[SimulationArtifactRef, ...] = Field(min_length=1, max_length=3)
    request_ref: SimulationArtifactRef | None
    response_ref: SimulationArtifactRef | None


class ActualSafetyRow(ArtifactModel):
    """Absolute simulator times and complete bounds, not desired-path previews.

    Each interval joins adjacent raw observation states. For an action, absolute
    interval ns = PreparedCommand.state.sim_time_ns + command-relative interval
    ns from M3. The first before-state is that command's start state, and the last
    after-state is the actual acknowledgement state. The trusted host must
    capture continuous actual bounds, including interior extrema and all robot
    bodies/payload. Contact force is the conservative sum for each unordered
    pair; PhysicsInterval refuses multiple records for the same pair.
    """

    index: SequenceNumber
    action_sequence: SequenceNumber | None
    before: StateBinding
    after: StateBinding
    phase: Phase
    interval: PhysicsInterval


class RecordedMetric(ArtifactModel):
    name: Identifier
    value: Finite
    unit: Identifier


class ChunkBase(ArtifactModel):
    format: Literal["accretion.episode-chunk.v1"] = "accretion.episode-chunk.v1"
    scope: EvidenceScope
    first_index: SequenceNumber


class TrajectoryChunkV1(ChunkBase):
    role: Literal["TRAJECTORY"] = "TRAJECTORY"
    records: tuple[TraceIndexRow, ...] = Field(min_length=1, max_length=1000)


class SensorChunkV1(ChunkBase):
    role: Literal["SENSORS"] = "SENSORS"
    records: tuple[ObservationBatch, ...] = Field(min_length=1, max_length=1000)


class ActionChunkV1(ChunkBase):
    role: Literal["ACTIONS"] = "ACTIONS"
    records: tuple[ActionEvidenceRow, ...] = Field(min_length=1, max_length=1000)


class SafetyChunkV1(ChunkBase):
    role: Literal["SAFETY"] = "SAFETY"
    records: tuple[ActualSafetyRow, ...] = Field(min_length=1, max_length=1000)


class MetricChunkV1(ChunkBase):
    role: Literal["METRICS"] = "METRICS"
    records: tuple[RecordedMetric, ...] = Field(min_length=1, max_length=1000)


class SafetyIssuanceV1(ArtifactModel):
    format: Literal["accretion.safety-issuance.v1"] = "accretion.safety-issuance.v1"
    receipt_original_json: str
    request_json: str
    preview_json: str | None

    def evaluation(self) -> SafetyEvaluation:
        return SafetyEvaluation(
            CanonicalWriterEnvelope(self.receipt_original_json),
            self.request_json.encode(),
            None if self.preview_json is None else self.preview_json.encode(),
        )

    @classmethod
    def from_evaluation(cls, value: SafetyEvaluation) -> SafetyIssuanceV1:
        return cls(
            receipt_original_json=value.receipt_envelope.original_json,
            request_json=value.request_bytes.decode(),
            preview_json=None if value.preview_bytes is None else value.preview_bytes.decode(),
        )


class TerminationPayloadV1(ArtifactModel):
    """Payload of an original simulation_episode.terminated domain event."""

    format: Literal["accretion.episode-termination.v1"] = "accretion.episode-termination.v1"
    termination_reason: Identifier
    final_state: Literal["VERIFYING", "ABORTED", "REJECTED"]
    final_observation: StateBinding
    action_count: SequenceNumber
    observation_count: SequenceNumber


class RecoveryLinkV1(ArtifactModel):
    format: Literal["accretion.episode-recovery.v1"] = "accretion.episode-recovery.v1"
    source_episode_record_ref: SimulationArtifactRef
    source_termination_event_ref: SimulationArtifactRef
    source_final_state: Literal["ABORTED", "REJECTED"]
    source_final_observation: StateBinding
    recovery_scope: EvidenceScope
    reset_batch_ref: SimulationArtifactRef
    preflight_ref: SimulationArtifactRef
    approval_ref: SimulationArtifactRef


class PoseChannels(ArtifactModel):
    position: Identifier
    orientation: Identifier


class PoseGoal(ArtifactModel):
    frame_id: Identifier
    position_m: tuple[Finite, Finite, Finite]
    orientation_xyzw: tuple[Finite, Finite, Finite, Finite]
    position_tolerance_m: Nonnegative
    orientation_tolerance_rad: Annotated[float, Field(ge=0, le=3.141592653589793, strict=True)]
    hold_ns: PositiveInt

    @model_validator(mode="after")
    def _quaternion(self) -> Self:
        from accretion.contracts.robotics.values import PoseTarget

        PoseTarget(
            frame_id=self.frame_id,
            position_m=self.position_m,
            orientation_xyzw=self.orientation_xyzw,
        )
        return self


class ReachTaskV1(ArtifactModel):
    kind: Literal["REACH"] = "REACH"
    tool: PoseChannels
    goal: PoseGoal


class PickPlaceTaskV1(ArtifactModel):
    kind: Literal["PICK_PLACE"] = "PICK_PLACE"
    tool: PoseChannels
    object: PoseChannels
    gripper_opening: Identifier
    finger_contact_forces: tuple[Identifier, ...] = Field(min_length=2, max_length=8)
    support_contact_force: Identifier
    goal: PoseGoal
    closed_opening_max_m: Nonnegative
    released_opening_min_m: Positive
    grasp_contact_min_n: Positive
    release_contact_max_n: Nonnegative
    support_contact_min_n: Positive
    minimum_lift_m: Positive
    minimum_transport_m: Positive
    maximum_grasp_relative_drift_m: Nonnegative
    maximum_stability_displacement_m: Nonnegative

    @model_validator(mode="after")
    def _limits(self) -> Self:
        if self.closed_opening_max_m >= self.released_opening_min_m:
            raise ValueError("closed and released openings must be distinct")
        if self.release_contact_max_n >= self.grasp_contact_min_n:
            raise ValueError("released contact must be below grasp contact")
        if len(set(self.finger_contact_forces)) != len(self.finger_contact_forces):
            raise ValueError("grasp needs distinct force channels")
        return self


class PerceptionRecoveryV1(ArtifactModel):
    kind: Literal["PERCEPTION_DISTURBANCE"] = "PERCEPTION_DISTURBANCE"
    perceived_position: Identifier
    ground_truth_position: Identifier
    declared_start_ns: SequenceNumber
    declared_end_ns: PositiveInt
    minimum_fault_error_m: Positive
    maximum_recovered_error_m: Nonnegative

    @model_validator(mode="after")
    def _limits(self) -> Self:
        if self.declared_end_ns <= self.declared_start_ns:
            raise ValueError("disturbance window must have positive duration")
        if self.maximum_recovered_error_m >= self.minimum_fault_error_m:
            raise ValueError("recovery must resolve the declared disturbance")
        return self


class FieldToleranceV1(ArtifactModel):
    field: Identifier
    unit: Identifier
    mode: Literal["COMPONENT", "QUATERNION_ANGLE"]
    absolute: Nonnegative
    relative: Nonnegative

    @model_validator(mode="after")
    def _quaternion(self) -> Self:
        if self.mode == "QUATERNION_ANGLE" and (self.unit != "rad" or self.relative != 0):
            raise ValueError("quaternion tolerance is one explicit absolute angle in radians")
        return self


class ReplayToleranceProfileV1(ArtifactModel):
    format: Literal["accretion.replay-tolerances.v1"] = "accretion.replay-tolerances.v1"
    time_tolerance_ns: SequenceNumber
    fields: tuple[FieldToleranceV1, ...] = Field(min_length=1, max_length=64)
    metric_tolerances: tuple[FieldToleranceV1, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        for entries in (self.fields, self.metric_tolerances):
            if len({entry.field for entry in entries}) != len(entries):
                raise ValueError("tolerances must name each field/metric once")
        return self


class ContactChannelV1(ArtifactModel):
    """One aggregate force (N) or presence (bool) channel per unordered pair.

    Global maxima and individual contact-point forces cannot be relabelled as
    pair aggregates. The trusted capture configuration must resolve the pair.
    """

    field: Identifier
    first_body: Identifier
    second_body: Identifier

    @model_validator(mode="after")
    def _distinct_bodies(self) -> Self:
        if self.first_body == self.second_body:
            raise ValueError("contact channel must identify distinct bodies")
        return self


class SafetyChannelsV1(ArtifactModel):
    joint_position: Identifier
    joint_velocity: Identifier
    joint_acceleration: Identifier
    joint_effort: Identifier
    gripper_opening: Identifier
    contacts: tuple[ContactChannelV1, ...] = Field(max_length=64)

    @model_validator(mode="after")
    def _unique_contacts(self) -> Self:
        if len({item.field for item in self.contacts}) != len(self.contacts):
            raise ValueError("contact channel fields must be unique")
        pairs = {frozenset((item.first_body, item.second_body)) for item in self.contacts}
        if len(pairs) != len(self.contacts):
            raise ValueError("contact channels must aggregate each unordered pair once")
        return self


class VerifierConfigurationV1(ArtifactModel):
    format: Literal["accretion.verifier-configuration.v1"] = "accretion.verifier-configuration.v1"
    task_verifier: VerifierRef
    safety_verifier: VerifierRef
    completeness_verifier: VerifierRef
    replay_verifier: VerifierRef
    task: Annotated[ReachTaskV1 | PickPlaceTaskV1, Field(discriminator="kind")]
    recovery: PerceptionRecoveryV1 | None
    requires_fresh_episode_recovery: bool = Field(strict=True)
    replay_tolerance_ref: SimulationArtifactRef
    safety_channels: SafetyChannelsV1
    maximum_observation_gap_ns: PositiveInt


@dataclass(frozen=True, slots=True)
class EvidenceReadLimits:
    """Engineering resource bounds, not scientific acceptance thresholds."""

    max_total_bytes: int
    max_artifacts: int
    max_records: int
    max_json_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        for value, ceiling in (
            (self.max_total_bytes, 1024 * 1024 * 1024),
            (self.max_artifacts, 1_000_000),
            (self.max_records, 1_000_000),
            (self.max_json_bytes, 1024 * 1024),
        ):
            if type(value) is not int or not 0 < value <= ceiling:
                raise ValueError("evidence limits must be positive and bounded")


@dataclass(frozen=True, slots=True)
class TrustedVerificationBinding:
    """Trusted preselected host inputs, never constructed from producer metadata.

    Host selection authenticates configuration before episode outcome access,
    matches installed verifier implementations and supplies current exact pins.
    The host also pins resolved geometry from retained trusted state; copying
    the producer's geometry reference does not authenticate its bounds/inventory.
    A matching producer-copied digest does not establish this provenance.
    This object grants no process independence, execution or attestation.
    """

    scope: EvidenceScope
    pins: EpisodePins
    dependencies: DependencyClosure
    configuration_bytes: bytes
    limits: EvidenceReadLimits
    expected_geometry_ref: SimulationArtifactRef
    expected_ancillary_artifacts: tuple[SimulationArtifactRef, ...]
    expected_recovery_link_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.configuration_bytes, bytes):
            raise ValueError("preselected configuration must be immutable bytes")
        if canonical_json(self.configuration) != self.configuration_bytes:
            raise ValueError("configuration must have canonical original bytes")

    def snapshot(self) -> TrustedVerificationBinding:
        """Detach nested mutable models before an untrusted artifact callback runs."""
        return TrustedVerificationBinding(
            EvidenceScope.model_validate_json(canonical_json(self.scope)),
            EpisodePins.model_validate_json(canonical_json(self.pins)),
            DependencyClosure.model_validate_json(canonical_json(self.dependencies)),
            self.configuration_bytes,
            self.limits,
            SimulationArtifactRef.model_validate_json(canonical_json(self.expected_geometry_ref)),
            tuple(
                SimulationArtifactRef.model_validate_json(canonical_json(ref))
                for ref in self.expected_ancillary_artifacts
            ),
            self.expected_recovery_link_digest,
        )

    @property
    def configuration(self) -> VerifierConfigurationV1:
        return VerifierConfigurationV1.model_validate_json(self.configuration_bytes)

    @property
    def configuration_digest(self) -> str:
        return content_hash(self.configuration, exclude=())


class RoleFinding(ArtifactModel):
    role: Literal["TASK", "SAFETY", "COMPLETENESS", "REPLAY", "RECOVERY"]
    status: FindingStatus
    reasons: tuple[str, ...] = Field(min_length=1)


class ConstructionFindings(ArtifactModel):
    evidence_level: Literal["CONSTRUCTION_FINDINGS"] = "CONSTRUCTION_FINDINGS"
    episode_record_hash: Digest
    configuration_digest: Digest
    findings: tuple[RoleFinding, ...] = Field(min_length=1)
    derived_metrics: tuple[RecordedMetric, ...]


# GeometryEvidence is reused unchanged: recorder hosts must certify actual body
# inventory and conservative workspace/forbidden-volume resolutions separately.
ActualGeometryV1 = GeometryEvidence
