"""Strict value objects shared by the v0.5 simulation contracts.

These describe declared inputs. They do not resolve a lease, grant authority,
validate a robot model or establish that an adapter passed conformance.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from accretion.contracts import EvidenceClass, StrictModel
from accretion.contracts.refs import VerifierRef

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Semver = Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
Identifier = Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[^\s*]+$")]
Finite = Annotated[float, Field(allow_inf_nan=False, strict=True)]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False, strict=True)]
Scale = Annotated[float, Field(gt=0, le=1, allow_inf_nan=False, strict=True)]
SequenceNumber = Annotated[int, Field(ge=0, strict=True)]
EpisodeId = Annotated[str, Field(pattern=r"^sep_[0-9A-HJKMNP-TV-Z]{26}$")]


class ControllerMode(StrEnum):
    JOINT_TRAJECTORY = "JOINT_TRAJECTORY"


class ReplayClass(StrEnum):
    EXACT = "EXACT"
    TOLERANT = "TOLERANT"
    STATISTICAL = "STATISTICAL"


class ContactPolicy(StrEnum):
    STOP_BEFORE_CONTACT = "STOP_BEFORE_CONTACT"
    DECLARED_CONTACT_ONLY = "DECLARED_CONTACT_ONLY"


class SemanticAction(StrEnum):
    MOVE_END_EFFECTOR = "MOVE_END_EFFECTOR"
    JOINT_TRAJECTORY = "JOINT_TRAJECTORY"
    SET_GRIPPER = "SET_GRIPPER"


class RoboticsContractRef(StrictModel):
    """Exact persisted robotics record; not a mutable logical alias."""

    contract_id: str = Field(pattern=r"^[a-z]{3}_[0-9A-HJKMNP-TV-Z]{26}$")
    content_hash: Digest
    schema_version: Semver = "1.0.0"


class ContentAddressedArtifactRef(StrictModel):
    """Run-independent blob identity; the URI is derived from its exact digest.

    This does not weaken the existing run/path-based ArtifactRef. The artifact
    store must enforce retention and byte limits when resolving this reference.
    """

    uri: str = Field(pattern=r"^artifact://sha256/[0-9a-f]{64}$")
    digest: Digest
    media_type: str = Field(min_length=3, max_length=255, pattern=r"^[^\s/]+/[^\s/]+$")
    size_bytes: int = Field(ge=0, strict=True)
    retention_class: Literal["RUN", "PROJECT", "RESEARCH_ARCHIVE"]
    evidence_class: EvidenceClass

    @model_validator(mode="after")
    def _digest_matches_uri(self) -> Self:
        if self.uri != f"artifact://sha256/{self.digest}":
            raise ValueError("artifact URI must name the declared digest")
        if self.evidence_class is EvidenceClass.PHYSICAL:
            raise ValueError("v0.5 simulation artifacts cannot declare PHYSICAL evidence")
        return self


class SimulationArtifactRef(ContentAddressedArtifactRef):
    evidence_class: Literal[EvidenceClass.SIMULATION] = EvidenceClass.SIMULATION


class JointLimit(StrictModel):
    joint_name: Identifier
    lower_rad: Finite
    upper_rad: Finite
    velocity_rad_s: Positive
    acceleration_rad_s2: Positive
    effort_nm: Positive

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.lower_rad >= self.upper_rad:
            raise ValueError("joint lower limit must be below upper limit")
        return self


class Kinematics(StrictModel):
    joint_names: list[Identifier] = Field(min_length=1, max_length=64)
    base_frame: Identifier
    tool_frame: Identifier
    joint_limits_ref: ContentAddressedArtifactRef
    transform_provenance_ref: ContentAddressedArtifactRef

    @model_validator(mode="after")
    def _unique_joints(self) -> Self:
        if len(set(self.joint_names)) != len(self.joint_names):
            raise ValueError("joint order must contain unique joint names")
        return self


class SensorDescriptor(StrictModel):
    sensor_id: Identifier
    modality: Literal["RGB", "DEPTH", "JOINT_STATE", "CONTACT", "POSE", "GRIPPER_OPENING"]
    frame_id: Identifier


class EndEffectorDescriptor(StrictModel):
    type: Literal["PARALLEL_GRIPPER"] = "PARALLEL_GRIPPER"
    joint_names: list[Identifier] = Field(min_length=1, max_length=8)
    joint_types: list[Literal["REVOLUTE", "PRISMATIC"]] = Field(min_length=1, max_length=8)
    minimum_opening_m: Nonnegative
    maximum_opening_m: Positive
    maximum_force_n: Positive

    @model_validator(mode="after")
    def _opening_range(self) -> Self:
        if self.minimum_opening_m >= self.maximum_opening_m:
            raise ValueError("gripper opening limits must be ordered")
        if len(set(self.joint_names)) != len(self.joint_names):
            raise ValueError("gripper joints must be unique")
        if len(self.joint_names) != len(self.joint_types):
            raise ValueError("every gripper joint needs its actual joint type")
        return self


class GripperEnvelope(StrictModel):
    """Normalized aperture bounds; adapter translates metres to actual finger joints."""

    minimum_opening_m: Nonnegative
    maximum_opening_m: Positive
    maximum_velocity_m_s: Positive
    maximum_acceleration_m_s2: Positive
    maximum_force_n: Positive

    @model_validator(mode="after")
    def _ordered_opening(self) -> Self:
        if self.minimum_opening_m >= self.maximum_opening_m:
            raise ValueError("gripper opening bounds must be ordered")
        return self


class ObservationField(StrictModel):
    field: Identifier
    sensor_id: Identifier
    modality: Literal["RGB", "DEPTH", "JOINT_STATE", "CONTACT", "POSE", "GRIPPER_OPENING"]
    dtype: Literal["float64", "float32", "uint8", "int64", "bool"]
    shape: list[Annotated[int, Field(gt=0, strict=True)]] = Field(min_length=1, max_length=4)
    unit: Literal["rad", "rad/s", "rad/s2", "m", "m/s", "N", "N.m", "s", "ns", "1", "pixel"]
    frame_id: Identifier

    @model_validator(mode="after")
    def _modality_shape(self) -> Self:
        numeric = self.dtype in {"float32", "float64"}
        valid = {
            "RGB": self.dtype == "uint8"
            and len(self.shape) == 3
            and self.shape[-1] == 3
            and self.unit == "pixel",
            "DEPTH": numeric and len(self.shape) == 2 and self.unit == "m",
            "JOINT_STATE": numeric
            and len(self.shape) == 1
            and self.unit in {"rad", "rad/s", "rad/s2", "N.m"},
            "CONTACT": len(self.shape) == 1
            and ((numeric and self.unit == "N") or (self.dtype == "bool" and self.unit == "1")),
            "POSE": numeric
            and (
                (self.shape == [3] and self.unit == "m") or (self.shape == [4] and self.unit == "1")
            ),
            "GRIPPER_OPENING": numeric and self.shape == [1] and self.unit == "m",
        }
        if not valid[self.modality]:
            raise ValueError("observation modality disagrees with dtype, shape or canonical unit")
        return self


class TimeAlignment(StrictModel):
    clock: Literal["SIMULATION"] = "SIMULATION"
    maximum_skew_ms: Nonnegative


class PoseTarget(StrictModel):
    kind: Literal["MOVE_END_EFFECTOR"] = "MOVE_END_EFFECTOR"
    frame_id: Identifier
    position_m: tuple[Finite, Finite, Finite]
    orientation_xyzw: tuple[Finite, Finite, Finite, Finite]

    @model_validator(mode="after")
    def _unit_quaternion(self) -> Self:
        if not math.isclose(sum(v * v for v in self.orientation_xyzw), 1.0, abs_tol=1e-6):
            raise ValueError("orientation must be a unit quaternion")
        return self


class TrajectoryTarget(StrictModel):
    kind: Literal["JOINT_TRAJECTORY"] = "JOINT_TRAJECTORY"
    frame_id: Identifier
    trajectory_ref: ContentAddressedArtifactRef
    joint_names: list[Identifier] = Field(min_length=1, max_length=64)


class GripperTarget(StrictModel):
    kind: Literal["SET_GRIPPER"] = "SET_GRIPPER"
    opening_m: Nonnegative
    force_n: Positive


ActionTarget = Annotated[PoseTarget | TrajectoryTarget | GripperTarget, Field(discriminator="kind")]


class ActionConstraints(StrictModel):
    speed_scale: Scale
    acceleration_scale: Scale
    planning_time_ms: int = Field(gt=0, le=60_000, strict=True)


class AllowedContact(StrictModel):
    first_body: Identifier
    second_body: Identifier
    phases: list[Literal["APPROACH", "GRASP", "TRANSPORT", "RELEASE"]] = Field(min_length=1)
    maximum_force_n: Positive
    maximum_penetration_m: Nonnegative

    @model_validator(mode="after")
    def _distinct_contact(self) -> Self:
        if self.first_body == self.second_body or len(self.phases) != len(set(self.phases)):
            raise ValueError("contact bodies must differ and phases must be unique")
        return self


class SimulatorProfile(StrictModel):
    """Exact simulator/middleware profile; only simulation transports are representable."""

    family: Literal["MUJOCO", "GAZEBO"]
    version: Semver
    middleware: Literal["NONE", "ROS2"]
    middleware_version: str | None = Field(default=None, max_length=128)
    transport: Literal["LOCAL_PROCESS", "ROS2_SIMULATION"]
    profile_id: Identifier

    @model_validator(mode="after")
    def _transport_profile(self) -> Self:
        if self.middleware == "ROS2":
            if not self.middleware_version or self.transport != "ROS2_SIMULATION":
                raise ValueError("ROS2 simulation requires a pinned distribution and transport")
        elif self.middleware_version is not None or self.transport != "LOCAL_PROCESS":
            raise ValueError("middleware NONE uses only a local process")
        return self


class ResourceBudget(StrictModel):
    max_episodes: int = Field(gt=0, strict=True)
    max_wall_seconds: Positive
    max_artifact_bytes: int = Field(gt=0, strict=True)


class ProcessBudget(StrictModel):
    max_cpu_seconds: Positive
    max_wall_seconds: Positive
    max_memory_bytes: int = Field(gt=0, strict=True)
    max_output_bytes: int = Field(gt=0, strict=True)


class VerifierRequirement(StrictModel):
    verifier: VerifierRef
    required: Literal[True] = True
    independent: Literal[True] = True
    deterministic: Literal[True] = True


class DependencyClosure(StrictModel):
    """Conformance identity without a manifest ↔ report hash cycle."""

    adapter_artifact_digest: Digest
    simulator_image_digest: Digest
    world_digest: Digest
    robot_model_digest: Digest
    controller_digest: Digest
    observation_spec_hash: Digest
    action_intent_schema_hash: Digest
    prepared_command_schema_hash: Digest
    tolerance_profile_hash: Digest
    environment_profile_hash: Digest
    physics_parameters_hash: Digest
    rendering_parameters_hash: Digest
    host_compatibility_profile_hash: Digest


class StateBinding(StrictModel):
    observation_digest: Digest
    observation_sequence: SequenceNumber
    sim_time_ns: SequenceNumber
    frame_transform_digest: Digest


class LeaseBinding(StrictModel):
    lease_id: Annotated[str, Field(pattern=r"^sle_[0-9A-HJKMNP-TV-Z]{26}$")]
    generation: int = Field(gt=0, strict=True)


class EpisodePins(StrictModel):
    episode_id: EpisodeId
    experiment_contract_hash: Digest
    preflight_receipt_hash: Digest
    environment_snapshot_hash: Digest
    adapter_manifest_hash: Digest
    safety_envelope_hash: Digest
    verification_spec_hash: Digest
    seed: int = Field(ge=0, strict=True)
    randomization_sample_hash: Digest
    lease: LeaseBinding


class MotionBudgetState(StrictModel):
    actions: SequenceNumber
    cumulative_joint_motion_rad: Nonnegative
    elapsed_sim_seconds: Nonnegative


class TrajectoryPoint(StrictModel):
    time_from_start_ns: SequenceNumber
    positions_rad: list[Finite] = Field(min_length=1, max_length=64)
    velocities_rad_s: list[Finite] = Field(min_length=1, max_length=64)
    accelerations_rad_s2: list[Finite] = Field(min_length=1, max_length=64)
    effort_upper_bounds_nm: list[Nonnegative] = Field(min_length=1, max_length=64)


class PreparedTrajectory(StrictModel):
    """Arm-only normalized trajectory. Joint order must match the admitted descriptor."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    command_type: Literal["JOINT_TRAJECTORY"] = "JOINT_TRAJECTORY"
    joint_names: list[Identifier] = Field(min_length=1, max_length=64)
    points: list[TrajectoryPoint] = Field(min_length=2, max_length=10_000)

    @model_validator(mode="after")
    def _trajectory_shape(self) -> Self:
        dof = len(self.joint_names)
        if len(set(self.joint_names)) != dof:
            raise ValueError("prepared trajectory joint names must be unique")
        times = [point.time_from_start_ns for point in self.points]
        if times[0] != 0 or any(a >= b for a, b in zip(times, times[1:], strict=False)):
            raise ValueError("trajectory time starts at zero and increases strictly")
        for point in self.points:
            if any(
                len(values) != dof
                for values in (
                    point.positions_rad,
                    point.velocities_rad_s,
                    point.accelerations_rad_s2,
                    point.effort_upper_bounds_nm,
                )
            ):
                raise ValueError("trajectory arrays must match the declared arm joint order")
        return self


class PreparedGripperCommand(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    command_type: Literal["GRIPPER_OPENING"] = "GRIPPER_OPENING"
    opening_m: Nonnegative
    velocity_m_s: Positive
    acceleration_m_s2: Positive
    force_limit_n: Positive


PreparedCommandPayload = Annotated[
    PreparedTrajectory | PreparedGripperCommand, Field(discriminator="command_type")
]


class DetachedSignature(StrictModel):
    algorithm: Literal["ED25519"] = "ED25519"
    key_id: Identifier
    signer_principal_id: Identifier
    domain: Literal["accretion.simulation.safety-decision.v1"] = (
        "accretion.simulation.safety-decision.v1"
    )
    unsigned_payload_digest: Digest
    signature_base64: str = Field(min_length=88, max_length=88)
