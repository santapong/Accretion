"""Pure, fail-closed simulation admission; this module never executes or reserves.

Desired joint trajectories use piecewise quintic Hermite interpolation of each
waypoint's q/v/a. Exact rational Bernstein hulls conservatively bound the entire
path, not only its samples. Actual tracking, effort, swept geometry and contacts
require an injected *trusted host* preview over an isolated state clone. No
kinematics, dynamics, or trust in an adapter's self-report is invented here.

The host must certify continuous bounds between physics steps, resolve every
referenced model/payload/volume, and enumerate all moving and stationary robot
bodies (including the payload). Workspace boxes are certified inner subsets;
forbidden boxes are conservative outer supersets. Missing proof is a refusal.
These internal types are not a public request/approval API. A configured host's
identity and implementation must be authenticated outside this pure boundary.

ALLOW is a signed prospective upper budget bound, not permission, a reservation,
an execution result, or a claim of physical safety. The gateway must atomically
recheck current permission, approval, key trust, state, fence and budget, reserve
once, and execute precisely the pinned command/controller. Real adapters must
enforce tracking/force limits at each physics step and stop on divergence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Annotated, Final, Literal, Protocol, Self

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import Field, model_validator

from accretion.contracts import PrincipalRef, PrincipalStatus, StrictModel
from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.refs import PolicyRef, VerifierRef
from accretion.contracts.robotics import (
    ActionIntent,
    CanonicalWriterEnvelope,
    EmbodimentDescriptor,
    PreparedCommand,
    SafetyDecisionPayload,
    SafetyDecisionReceipt,
    SafetyEnvelope,
    SimulationEpisodeApproval,
    TrustedSafetyKey,
    sign_safety_payload,
    verify_safety_signature,
)
from accretion.contracts.robotics.models import RoboticsContract
from accretion.contracts.robotics.values import (
    ContactPolicy,
    ContentAddressedArtifactRef,
    DependencyClosure,
    Digest,
    EpisodePins,
    Finite,
    GripperTarget,
    Identifier,
    JointLimit,
    MotionBudgetState,
    Nonnegative,
    PoseTarget,
    PreparedGripperCommand,
    PreparedTrajectory,
    SequenceNumber,
    StateBinding,
    TrajectoryTarget,
)
from accretion.robotics.errors import RoboticsError, RoboticsErrorCode

INTERPOLATION: Final = "PIECEWISE_QUINTIC_HERMITE_V1"
Phase = Literal["APPROACH", "GRASP", "TRANSPORT", "RELEASE"]
PositiveInt = Annotated[int, Field(gt=0, strict=True)]
StrictBool = Annotated[bool, Field(strict=True)]


class SafetyContext(StrictModel):
    """Snapshot read by the trusted backend, never supplied by an agent/adapter.

    The backend resolves current approval membership/revocation and lease/key
    activation. True booleans are claims about that snapshot, not authority.
    ``budget`` includes actual prior motion and elapsed idle time; it is copied.
    Its float counters must round upward when converting integer simulator time.
    One action belongs to one phase. Multi-phase actions must be split.
    """

    workspace_id: Identifier
    project_id: Identifier
    receipt_id: Identifier
    now: datetime
    state: StateBinding
    sequence: SequenceNumber
    episode: EpisodePins
    closure: DependencyClosure
    approval_hash: Digest
    approval_active: StrictBool
    policy_ref: PolicyRef
    lease_active: StrictBool
    lease_expires_at: datetime
    last_action_sim_time_ns: SequenceNumber
    episode_start_sim_time_ns: SequenceNumber
    budget: MotionBudgetState
    phase: Phase
    physics_step_ns: PositiveInt
    max_preview_intervals: Annotated[int, Field(gt=0, le=10000, strict=True)] = 10000
    joint_positions_rad: list[Finite] = Field(min_length=1, max_length=64)
    joint_velocities_rad_s: list[Finite] = Field(min_length=1, max_length=64)
    joint_accelerations_rad_s2: list[Finite] = Field(min_length=1, max_length=64)
    gripper_opening_m: Nonnegative

    @model_validator(mode="after")
    def _clock_and_shape(self) -> Self:
        canonical_json(self)
        count = len(self.joint_positions_rad)
        if any(
            len(x) != count for x in (self.joint_velocities_rad_s, self.joint_accelerations_rad_s2)
        ):
            raise ValueError("current joint state dimensions disagree")
        return self


class PreviewRequest(StrictModel):
    """All copied inputs are digest-bound; a provider must not mutate this request."""

    interpolation: Literal["PIECEWISE_QUINTIC_HERMITE_V1"] = INTERPOLATION
    intent: ActionIntent
    prepared: PreparedCommand
    envelope: SafetyEnvelope
    descriptor: EmbodimentDescriptor
    approval: SimulationEpisodeApproval
    context: SafetyContext

    @property
    def digest(self) -> str:
        return content_hash(self, exclude=())


class AxisAlignedBox(StrictModel):
    minimum_m: tuple[Finite, Finite, Finite]
    maximum_m: tuple[Finite, Finite, Finite]

    @model_validator(mode="after")
    def _order(self) -> Self:
        if any(a > b for a, b in zip(self.minimum_m, self.maximum_m, strict=True)):
            raise ValueError("box bounds are inverted")
        return self


class ResolvedVolume(StrictModel):
    """Host-certified bound from the exact referenced artifact, in base frame.

    This is not a new artifact encoding: the authenticated host must read/hash
    the original bytes and justify the conservative inner/outer approximation.
    """

    artifact: ContentAddressedArtifactRef
    box: AxisAlignedBox


class GeometryEvidence(StrictModel):
    descriptor_workspace: ResolvedVolume
    envelope_workspace: ResolvedVolume
    forbidden_volumes: list[ResolvedVolume] = Field(max_length=256)
    joint_limits_ref: ContentAddressedArtifactRef
    joint_limits: list[JointLimit] = Field(min_length=1, max_length=64)
    tool_payload_ref: ContentAddressedArtifactRef
    body_names: list[Identifier] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def _unique_inventory(self) -> Self:
        if len(set(self.body_names)) != len(self.body_names):
            raise ValueError("body inventory must be complete and unique")
        names = [item.joint_name for item in self.joint_limits]
        if len(names) != len(set(names)):
            raise ValueError("model joint limits must be unique")
        return self


class JointMotionBound(StrictModel):
    joint_name: Identifier
    minimum_rad: Finite
    maximum_rad: Finite
    speed_upper_rad_s: Nonnegative
    acceleration_upper_rad_s2: Nonnegative
    effort_upper_nm: Nonnegative
    motion_upper_rad: Nonnegative

    @model_validator(mode="after")
    def _order(self) -> Self:
        if self.minimum_rad > self.maximum_rad:
            raise ValueError("joint interval is inverted")
        return self


class GripperMotionBound(StrictModel):
    minimum_opening_m: Nonnegative
    maximum_opening_m: Nonnegative
    speed_upper_m_s: Nonnegative
    acceleration_upper_m_s2: Nonnegative
    force_upper_n: Nonnegative

    @model_validator(mode="after")
    def _order(self) -> Self:
        if self.minimum_opening_m > self.maximum_opening_m:
            raise ValueError("gripper interval is inverted")
        return self


class BodySweep(StrictModel):
    body_name: Identifier
    box: AxisAlignedBox


class ContactBound(StrictModel):
    first_body: Identifier
    second_body: Identifier
    phase: Phase
    force_upper_n: Nonnegative
    penetration_upper_m: Nonnegative

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if self.first_body == self.second_body:
            raise ValueError("contact must name distinct bodies")
        return self


class PhysicsInterval(StrictModel):
    """Conservative *actual* bounds for the closed interval, including its interior."""

    start_ns: SequenceNumber
    end_ns: PositiveInt
    joints: list[JointMotionBound] = Field(min_length=1, max_length=64)
    gripper: GripperMotionBound
    bodies: list[BodySweep] = Field(min_length=1, max_length=256)
    contacts: list[ContactBound] = Field(max_length=4096)

    @model_validator(mode="after")
    def _interval(self) -> Self:
        if self.end_ns <= self.start_ns:
            raise ValueError("physics interval must have positive duration")
        for names in (
            [item.joint_name for item in self.joints],
            [item.body_name for item in self.bodies],
        ):
            if len(names) != len(set(names)):
                raise ValueError("interval inventory must be unique")
        return self


class HostPreview(StrictModel):
    request_digest: Digest
    coverage: Literal["CONTINUOUS_CONSERVATIVE_PHYSICS_BOUNDS"]
    duration_ns: PositiveInt
    geometry: GeometryEvidence
    intervals: list[PhysicsInterval] = Field(min_length=1, max_length=10000)
    intent_satisfied: StrictBool


class TrustedPreviewProvider(Protocol):
    """Trusted backend injection, never selected from agent-supplied code/data.

    Authenticate the worker and exact closure; copy the pinned simulator state;
    validate the exact command without advancing its live instance; certify all
    actual tracking/dynamics/geometries/contacts continuously at every physics
    interval. A numerical rollout at sample points alone cannot satisfy this
    interface. Return None if the required bounds cannot be justified.
    """

    def preview(self, request: PreviewRequest) -> HostPreview | None: ...


@dataclass(frozen=True, slots=True)
class SafetyEvaluation:
    """Immutable issuance retained in the trusted ledger, keyed by receipt hash.

    The frozen M0 receipt does not itself sign phase or host-preview metadata.
    Consequently a receipt alone is insufficient for consumption: M2 must keep
    this exact trusted issuance and atomically compare its complete context.
    Arbitrary imported records cannot reconstruct that trusted provenance.
    Projections return fresh objects; mutating them cannot alter the issuance.
    """

    receipt_envelope: CanonicalWriterEnvelope
    request_bytes: bytes
    preview_bytes: bytes | None

    def __post_init__(self) -> None:
        if not isinstance(self.request_bytes, bytes) or (
            self.preview_bytes is not None and not isinstance(self.preview_bytes, bytes)
        ):
            raise ValueError("issuance snapshots must be immutable bytes")
        request, receipt, preview = self.request, self.receipt, self.preview
        for record in (
            request.intent,
            request.prepared,
            request.envelope,
            request.descriptor,
            request.approval,
        ):
            _checked(record)
        if canonical_json(request) != self.request_bytes or (
            preview is not None and canonical_json(preview) != self.preview_bytes
        ):
            raise ValueError("issuance snapshots must be canonical")
        if receipt.decision.decision == "ALLOW" and (
            preview is None or preview.request_digest != request.digest
        ):
            raise ValueError("ALLOW issuance requires its exact preview")
        decision = receipt.decision
        if (
            decision.action_intent_hash != request.intent.content_hash
            or decision.prepared_command_hash != request.prepared.content_hash
            or decision.safety_envelope_hash != request.envelope.content_hash
            or decision.episode_approval_hash != request.approval.content_hash
            or decision.receipt_id != request.context.receipt_id
            or decision.issued_at != request.context.now
            or decision.budget_before != request.context.budget
            or (decision.workspace_id, decision.project_id)
            != (request.context.workspace_id, request.context.project_id)
            or decision.episode_id != request.intent.episode_id
            or decision.state != request.intent.state
            or decision.lease != request.prepared.lease
            or decision.policy_ref != request.context.policy_ref
        ):
            raise ValueError("issuance does not match its signed decision")

    @property
    def receipt(self) -> SafetyDecisionReceipt:
        return self.receipt_envelope.for_execution(SafetyDecisionReceipt)

    @property
    def request(self) -> PreviewRequest:
        return PreviewRequest.model_validate_json(self.request_bytes)

    @property
    def preview(self) -> HostPreview | None:
        return (
            None
            if self.preview_bytes is None
            else HostPreview.model_validate_json(self.preview_bytes)
        )

    @property
    def request_digest(self) -> str:
        return self.request.digest

    @property
    def preview_digest(self) -> str | None:
        preview = self.preview
        return None if preview is None else content_hash(preview, exclude=())

    def validate_current_context(self, current: SafetyContext) -> None:
        """Pure prerequisite only; gateway still verifies trust/permission atomically.

        Wall time may advance, with no regression or lease/approval expiration.
        Every other context field must equal issuance, including phase, physics
        step, resource ceiling, current approval status and actual budget.
        """
        try:
            checked = SafetyContext.model_validate(
                current.model_dump(mode="python", warnings=False)
            )
        except (ValueError, TypeError, AttributeError) as exc:
            raise RoboticsError(RoboticsErrorCode.INVALID_CONTRACT) from exc
        request = self.request
        if (
            self.receipt.decision.decision != "ALLOW"
            or canonical_json(checked.model_dump(mode="python", exclude={"now"}))
            != canonical_json(request.context.model_dump(mode="python", exclude={"now"}))
            or checked.now < request.context.now
            or checked.now >= checked.lease_expires_at
            or checked.now >= request.approval.expires_at
        ):
            raise RoboticsError(RoboticsErrorCode.SAFETY_DENIED)


@dataclass(frozen=True, slots=True)
class SafetySigner:
    key_id: str
    principal: PrincipalRef
    evaluator: VerifierRef
    private_key: Ed25519PrivateKey
    trusted_key: TrustedSafetyKey

    def validate(self) -> None:
        principal = PrincipalRef.model_validate(self.principal.model_dump(mode="python"))
        VerifierRef.model_validate(self.evaluator.model_dump(mode="python"))
        if (
            principal.status is not PrincipalStatus.ACTIVE
            or self.trusted_key.principal_id != principal.principal_id
            or self.private_key.public_key().public_bytes_raw() != self.trusted_key.public_key
        ):
            raise RoboticsError(RoboticsErrorCode.SAFETY_DENIED)


def _checked[TContract: RoboticsContract](value: TContract) -> TContract:
    # Preserves original writer seals and refuses unknown/lossy schema versions.
    return CanonicalWriterEnvelope.from_contract(value).for_execution(type(value))


def _r(value: float | int) -> Fraction:
    return Fraction(value)


def _upper_float(value: Fraction) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("budget cannot be represented")
    return math.nextafter(result, math.inf) if _r(result) < value else result


def _inside(inner: AxisAlignedBox, outer: AxisAlignedBox) -> bool:
    return all(
        lo <= a <= b <= hi
        for a, b, lo, hi in zip(
            inner.minimum_m, inner.maximum_m, outer.minimum_m, outer.maximum_m, strict=True
        )
    )


def _intersects(first: AxisAlignedBox, second: AxisAlignedBox) -> bool:
    # Touching a forbidden boundary counts as intersection.
    return all(
        a <= d and c <= b
        for a, b, c, d in zip(
            first.minimum_m, first.maximum_m, second.minimum_m, second.maximum_m, strict=True
        )
    )


def _desired_path(request: PreviewRequest, reasons: list[str]) -> Fraction:
    """Exact binary-input arithmetic avoids inward floating-point rounding."""
    command, context, envelope = (
        request.prepared.command_payload,
        request.context,
        request.envelope,
    )
    limits = envelope.joint_limits
    if [limit.joint_name for limit in limits] != request.descriptor.kinematics.joint_names:
        reasons.append("JOINT_ORDER_MISMATCH")
        return Fraction(0)
    if len(context.joint_positions_rad) != len(limits):
        reasons.append("CURRENT_STATE_SHAPE")
        return Fraction(0)
    if isinstance(command, PreparedGripperCommand):
        target = request.intent.target
        if not isinstance(target, GripperTarget) or (
            command.opening_m != target.opening_m or command.force_limit_n > target.force_n
        ):
            reasons.append("GRIPPER_TARGET_MISMATCH")
        if not (
            envelope.gripper.minimum_opening_m
            <= command.opening_m
            <= envelope.gripper.maximum_opening_m
            and _r(command.velocity_m_s)
            <= _r(envelope.gripper.maximum_velocity_m_s)
            * _r(request.intent.constraints.speed_scale)
            and _r(command.acceleration_m_s2)
            <= _r(envelope.gripper.maximum_acceleration_m_s2)
            * _r(request.intent.constraints.acceleration_scale)
            and command.force_limit_n <= envelope.gripper.maximum_force_n
        ):
            reasons.append("GRIPPER_COMMAND_LIMIT")
        return Fraction(0)
    if isinstance(request.intent.target, GripperTarget):
        reasons.append("COMMAND_KIND_MISMATCH")
    if command.joint_names != request.descriptor.kinematics.joint_names:
        reasons.append("JOINT_ORDER_MISMATCH")
        return Fraction(0)
    first = command.points[0]
    if (first.positions_rad, first.velocities_rad_s, first.accelerations_rad_s2) != (
        context.joint_positions_rad,
        context.joint_velocities_rad_s,
        context.joint_accelerations_rad_s2,
    ):
        reasons.append("TRAJECTORY_START_MISMATCH")
    motion = Fraction(0)
    for start, end in zip(command.points, command.points[1:], strict=False):
        h = Fraction(end.time_from_start_ns - start.time_from_start_ns, 1_000_000_000)
        for index, limit in enumerate(limits):
            q0, q1 = _r(start.positions_rad[index]), _r(end.positions_rad[index])
            v0, v1 = _r(start.velocities_rad_s[index]), _r(end.velocities_rad_s[index])
            a0, a1 = _r(start.accelerations_rad_s2[index]), _r(end.accelerations_rad_s2[index])
            coefficients = (
                q0,
                q0 + v0 * h / 5,
                q0 + 2 * v0 * h / 5 + a0 * h * h / 20,
                q1 - 2 * v1 * h / 5 + a1 * h * h / 20,
                q1 - v1 * h / 5,
                q1,
            )
            velocity = tuple(
                5 * (b - a) / h for a, b in zip(coefficients, coefficients[1:], strict=False)
            )
            acceleration = tuple(
                4 * (b - a) / h for a, b in zip(velocity, velocity[1:], strict=False)
            )
            margin = _r(envelope.joint_limit_margin_rad)
            if min(coefficients) < _r(limit.lower_rad) + margin or (
                max(coefficients) > _r(limit.upper_rad) - margin
            ):
                reasons.append("TRAJECTORY_POSITION_LIMIT")
            if max(map(abs, velocity)) > (
                _r(limit.velocity_rad_s) * _r(request.intent.constraints.speed_scale)
            ):
                reasons.append("TRAJECTORY_VELOCITY_LIMIT")
            if max(map(abs, acceleration)) > (
                _r(limit.acceleration_rad_s2) * _r(request.intent.constraints.acceleration_scale)
            ):
                reasons.append("TRAJECTORY_ACCELERATION_LIMIT")
            if max(start.effort_upper_bounds_nm[index], end.effort_upper_bounds_nm[index]) > (
                limit.effort_nm
            ):
                reasons.append("TRAJECTORY_EFFORT_LIMIT")
            motion += sum(
                (abs(b - a) for a, b in zip(coefficients, coefficients[1:], strict=False)),
                Fraction(0),
            )
    return motion


def _bindings(request: PreviewRequest, reasons: list[str]) -> None:
    intent, prepared, envelope, descriptor, approval, context = (
        request.intent,
        request.prepared,
        request.envelope,
        request.descriptor,
        request.approval,
        request.context,
    )
    if any(
        (item.workspace_id, item.project_id) != (context.workspace_id, context.project_id)
        for item in (intent, prepared, envelope, descriptor, approval)
    ):
        reasons.append("SCOPE_MISMATCH")
    if (
        intent.episode_id != context.episode.episode_id
        or prepared.episode_id != intent.episode_id
        or intent.sequence != context.sequence
        or prepared.sequence != intent.sequence
        or prepared.action_intent_hash != intent.content_hash
    ):
        reasons.append("ACTION_BINDING_MISMATCH")
    if intent.state != context.state or prepared.state != context.state:
        reasons.append("STATE_BINDING_MISMATCH")
    if (
        prepared.lease != context.episode.lease
        or not context.lease_active
        or (context.now >= context.lease_expires_at)
    ):
        reasons.append("LEASE_INVALID")
    if min(intent.expires_at_sim_time_ns, prepared.expires_at_sim_time_ns) <= (
        context.state.sim_time_ns
    ):
        reasons.append("ACTION_EXPIRED")
    if (
        approval.pins != context.episode
        or approval.content_hash != context.approval_hash
        or not context.approval_active
        or context.now >= approval.expires_at
        or context.now < approval.created_at
        or approval.approved_by.status is not PrincipalStatus.ACTIVE
    ):
        reasons.append("APPROVAL_INVALID")
    if approval.policy_ref != context.policy_ref or envelope.policy_ref != context.policy_ref:
        reasons.append("POLICY_BINDING_MISMATCH")
    if (
        envelope.content_hash != context.episode.safety_envelope_hash
        or envelope.embodiment_descriptor_hash != descriptor.content_hash
        or prepared.command_schema_hash != context.closure.prepared_command_schema_hash
        or prepared.controller_digest != context.closure.controller_digest
        or prepared.adapter_artifact_digest != context.closure.adapter_artifact_digest
        or descriptor.robot_model_ref.digest != context.closure.robot_model_digest
    ):
        reasons.append("CLOSURE_MISMATCH")
    if prepared.controller_mode not in envelope.controller_modes or (
        prepared.controller_mode not in descriptor.control_interfaces
    ):
        reasons.append("CONTROLLER_UNSUPPORTED")
    if isinstance(intent.target, PoseTarget | TrajectoryTarget) and (
        intent.target.frame_id != descriptor.kinematics.base_frame
    ):
        reasons.append("FRAME_UNSUPPORTED")
    if isinstance(intent.target, TrajectoryTarget) and (
        intent.target.joint_names != descriptor.kinematics.joint_names
    ):
        reasons.append("TARGET_JOINT_ORDER_MISMATCH")
    if (
        intent.constraints.speed_scale > envelope.speed_scale_max
        or intent.constraints.acceleration_scale > envelope.acceleration_scale_max
    ):
        reasons.append("ACTION_SCALE_LIMIT")
    elapsed_ns = context.state.sim_time_ns - context.episode_start_sim_time_ns
    idle_ns = context.state.sim_time_ns - context.last_action_sim_time_ns
    if elapsed_ns < 0 or idle_ns < 0:
        reasons.append("CLOCK_REGRESSION")
    if Fraction(idle_ns, 1_000_000_000) >= _r(envelope.no_action_timeout_seconds):
        reasons.append("NO_ACTION_TIMEOUT")
    if _r(context.budget.elapsed_sim_seconds) < Fraction(elapsed_ns, 1_000_000_000):
        reasons.append("ELAPSED_BUDGET_STALE")
    if context.budget.actions >= envelope.max_actions:
        reasons.append("ACTION_BUDGET_EXHAUSTED")


def _preview_checks(request: PreviewRequest, preview: HostPreview, reasons: list[str]) -> Fraction:
    context, envelope, descriptor = request.context, request.envelope, request.descriptor
    geometry = preview.geometry
    if preview.request_digest != request.digest:
        reasons.append("PREVIEW_BINDING_MISMATCH")
    if not preview.intent_satisfied:
        reasons.append("INTENT_NOT_SATISFIED")
    if (
        geometry.descriptor_workspace.artifact != descriptor.workspace_ref
        or geometry.envelope_workspace.artifact != envelope.workspace_ref
        or geometry.joint_limits_ref != descriptor.kinematics.joint_limits_ref
        or geometry.tool_payload_ref != envelope.tool_payload_ref
        or [item.artifact for item in geometry.forbidden_volumes] != envelope.forbidden_volume_refs
    ):
        reasons.append("GEOMETRY_BINDING_MISMATCH")
    if not _inside(geometry.envelope_workspace.box, geometry.descriptor_workspace.box):
        reasons.append("WORKSPACE_OUTSIDE_MODEL")
    names = descriptor.kinematics.joint_names
    if [item.joint_name for item in geometry.joint_limits] != names:
        reasons.append("MODEL_JOINT_ORDER_MISMATCH")
        return Fraction(0)
    for admitted, physical in zip(envelope.joint_limits, geometry.joint_limits, strict=True):
        if not (
            physical.lower_rad <= admitted.lower_rad < admitted.upper_rad <= physical.upper_rad
            and admitted.velocity_rad_s <= physical.velocity_rad_s
            and admitted.acceleration_rad_s2 <= physical.acceleration_rad_s2
            and admitted.effort_nm <= physical.effort_nm
        ):
            reasons.append("ENVELOPE_EXCEEDS_MODEL")
    gripper, effector = envelope.gripper, descriptor.end_effectors[0]
    if not (
        effector.minimum_opening_m
        <= gripper.minimum_opening_m
        < gripper.maximum_opening_m
        <= effector.maximum_opening_m
        and gripper.maximum_force_n <= effector.maximum_force_n
    ):
        reasons.append("GRIPPER_ENVELOPE_EXCEEDS_MODEL")
    command = request.prepared.command_payload
    if isinstance(command, PreparedTrajectory) and preview.duration_ns != (
        command.points[-1].time_from_start_ns
    ):
        reasons.append("PREVIEW_DURATION_MISMATCH")
    if context.state.sim_time_ns + preview.duration_ns >= min(
        request.intent.expires_at_sim_time_ns, request.prepared.expires_at_sim_time_ns
    ):
        reasons.append("ACTION_COMPLETION_EXPIRED")
    if len(preview.intervals) > context.max_preview_intervals:
        reasons.append("PREVIEW_INTERVAL_CAP")
    next_ns, motion = 0, Fraction(0)
    for interval in preview.intervals:
        if interval.start_ns != next_ns or interval.end_ns != min(
            interval.start_ns + context.physics_step_ns, preview.duration_ns
        ):
            reasons.append("PHYSICS_COVERAGE_INCOMPLETE")
        next_ns = interval.end_ns
        if [item.joint_name for item in interval.joints] != names:
            reasons.append("PHYSICS_JOINT_INCOMPLETE")
            continue
        for index, (bound, limit) in enumerate(
            zip(interval.joints, envelope.joint_limits, strict=True)
        ):
            if interval.start_ns == 0 and not (
                bound.minimum_rad <= context.joint_positions_rad[index] <= bound.maximum_rad
                and abs(context.joint_velocities_rad_s[index]) <= bound.speed_upper_rad_s
                and abs(context.joint_accelerations_rad_s2[index])
                <= bound.acceleration_upper_rad_s2
            ):
                reasons.append("PREVIEW_INITIAL_STATE_MISMATCH")
            margin = _r(envelope.joint_limit_margin_rad)
            if _r(bound.minimum_rad) < _r(limit.lower_rad) + margin or (
                _r(bound.maximum_rad) > _r(limit.upper_rad) - margin
            ):
                reasons.append("ACTUAL_POSITION_LIMIT")
            if _r(bound.speed_upper_rad_s) > (
                _r(limit.velocity_rad_s) * _r(request.intent.constraints.speed_scale)
            ):
                reasons.append("ACTUAL_VELOCITY_LIMIT")
            if _r(bound.acceleration_upper_rad_s2) > (
                _r(limit.acceleration_rad_s2) * _r(request.intent.constraints.acceleration_scale)
            ):
                reasons.append("ACTUAL_ACCELERATION_LIMIT")
            if bound.effort_upper_nm > limit.effort_nm:
                reasons.append("ACTUAL_EFFORT_LIMIT")
            motion += _r(bound.motion_upper_rad)
        actual_gripper = interval.gripper
        if interval.start_ns == 0 and not (
            actual_gripper.minimum_opening_m
            <= context.gripper_opening_m
            <= actual_gripper.maximum_opening_m
        ):
            reasons.append("PREVIEW_INITIAL_GRIPPER_MISMATCH")
        if not (
            gripper.minimum_opening_m
            <= actual_gripper.minimum_opening_m
            <= actual_gripper.maximum_opening_m
            <= gripper.maximum_opening_m
            and _r(actual_gripper.speed_upper_m_s)
            <= _r(gripper.maximum_velocity_m_s) * _r(request.intent.constraints.speed_scale)
            and _r(actual_gripper.acceleration_upper_m_s2)
            <= _r(gripper.maximum_acceleration_m_s2)
            * _r(request.intent.constraints.acceleration_scale)
            and actual_gripper.force_upper_n <= gripper.maximum_force_n
        ):
            reasons.append("ACTUAL_GRIPPER_LIMIT")
        if isinstance(command, PreparedGripperCommand):
            if actual_gripper.force_upper_n > command.force_limit_n:
                reasons.append("GRIPPER_COMMANDED_FORCE_LIMIT")
            if actual_gripper.speed_upper_m_s > command.velocity_m_s or (
                actual_gripper.acceleration_upper_m_s2 > command.acceleration_m_s2
            ):
                reasons.append("GRIPPER_COMMANDED_MOTION_LIMIT")
        if {body.body_name for body in interval.bodies} != set(geometry.body_names):
            reasons.append("BODY_COVERAGE_INCOMPLETE")
        for body in interval.bodies:
            if not _inside(body.box, geometry.envelope_workspace.box):
                reasons.append("WORKSPACE_VIOLATION")
            if any(_intersects(body.box, item.box) for item in geometry.forbidden_volumes):
                reasons.append("FORBIDDEN_VOLUME_INTERSECTION")
        for contact in interval.contacts:
            if envelope.collision_policy is ContactPolicy.STOP_BEFORE_CONTACT:
                reasons.append("CONTACT_FORBIDDEN")
                continue
            allowed = next(
                (
                    item
                    for item in envelope.allowed_contacts
                    if (
                        {item.first_body, item.second_body}
                        == {contact.first_body, contact.second_body}
                        and contact.phase in item.phases
                        and contact.phase == context.phase
                    )
                ),
                None,
            )
            if allowed is None:
                reasons.append("CONTACT_UNDECLARED")
            elif contact.force_upper_n > allowed.maximum_force_n or (
                contact.penetration_upper_m > allowed.maximum_penetration_m
            ):
                reasons.append("CONTACT_LIMIT")
    if next_ns != preview.duration_ns:
        reasons.append("PHYSICS_COVERAGE_INCOMPLETE")
    return motion


def evaluate_safety(
    intent: ActionIntent,
    prepared: PreparedCommand,
    envelope: SafetyEnvelope,
    descriptor: EmbodimentDescriptor,
    approval: SimulationEpisodeApproval,
    *,
    context: SafetyContext,
    host: TrustedPreviewProvider | None,
    signer: SafetySigner,
) -> SafetyEvaluation:
    """Return signed decision plus immutable issuance evidence; mutate nothing.

    Malformed records, writer seals or signer configurations refuse with a domain
    error. Well-formed unsafe/stale/missing-evidence requests receive signed DENY.
    Caller must supply a fresh unique receipt ID and a trusted, current context.
    """
    try:
        signer.validate()
        request = PreviewRequest(
            intent=_checked(intent),
            prepared=_checked(prepared),
            envelope=_checked(envelope),
            descriptor=_checked(descriptor),
            approval=_checked(approval),
            context=SafetyContext.model_validate(context.model_dump(mode="python", warnings=False)),
        )
    except (ValueError, TypeError, AttributeError) as exc:
        raise RoboticsError(RoboticsErrorCode.INVALID_CONTRACT) from exc
    envelope = request.envelope
    reasons: list[str] = []
    _bindings(request, reasons)
    desired_motion = _desired_path(request, reasons)
    budget = request.context.budget
    after = budget.model_copy(deep=True)
    preview = None
    if not reasons:
        try:
            if host is not None:
                # The provider gets a separate copy, so it cannot replace our pins.
                returned = host.preview(request.model_copy(deep=True))
                if returned is not None:
                    preview = HostPreview.model_validate(returned.model_dump(mode="python"))
        except Exception:
            reasons.append("PREVIEW_UNAVAILABLE")
        if preview is None:
            reasons.append("PREVIEW_REQUIRED")
        else:
            actual_motion = _preview_checks(request, preview, reasons)
            motion = _r(budget.cumulative_joint_motion_rad) + max(desired_motion, actual_motion)
            elapsed = _r(budget.elapsed_sim_seconds) + Fraction(preview.duration_ns, 1_000_000_000)
            if motion > _r(envelope.max_cumulative_joint_motion_rad):
                reasons.append("MOTION_BUDGET_EXHAUSTED")
            if elapsed > _r(envelope.max_episode_sim_seconds):
                reasons.append("TIME_BUDGET_EXHAUSTED")
            if not reasons:
                after = MotionBudgetState(
                    actions=budget.actions + 1,
                    cumulative_joint_motion_rad=_upper_float(motion),
                    elapsed_sim_seconds=_upper_float(elapsed),
                )
                # Rounded upper bounds themselves must fit the admitted caps.
                if after.cumulative_joint_motion_rad > envelope.max_cumulative_joint_motion_rad:
                    reasons.append("MOTION_BUDGET_EXHAUSTED")
                if after.elapsed_sim_seconds > envelope.max_episode_sim_seconds:
                    reasons.append("TIME_BUDGET_EXHAUSTED")
    context = request.context
    decision = SafetyDecisionPayload(
        receipt_id=context.receipt_id,
        workspace_id=context.workspace_id,
        project_id=context.project_id,
        episode_id=request.intent.episode_id,
        action_intent_hash=request.intent.content_hash,
        prepared_command_hash=request.prepared.content_hash,
        safety_envelope_hash=request.envelope.content_hash,
        episode_approval_hash=request.approval.content_hash,
        policy_ref=context.policy_ref,
        state=request.intent.state,
        lease=request.prepared.lease,
        evaluator=signer.evaluator,
        evaluator_principal_id=signer.principal.principal_id,
        decision="DENY" if reasons else "ALLOW",
        reason_codes=list(dict.fromkeys(reasons)) if reasons else ["WITHIN_ENVELOPE"],
        budget_before=budget,
        budget_after=budget if reasons else after,
        issued_at=context.now,
        expires_at_sim_time_ns=(
            request.intent.expires_at_sim_time_ns
            if reasons
            else min(request.intent.expires_at_sim_time_ns, request.prepared.expires_at_sim_time_ns)
        ),
    )
    receipt = SafetyDecisionReceipt(
        contract_id=context.receipt_id,
        workspace_id=context.workspace_id,
        project_id=context.project_id,
        created_at=context.now,
        created_by=signer.principal,
        decision=decision,
        signature=sign_safety_payload(
            decision, key_id=signer.key_id, private_key=signer.private_key
        ),
    )
    verify_safety_signature(receipt, trusted_keys={signer.key_id: signer.trusted_key})
    return SafetyEvaluation(
        receipt_envelope=CanonicalWriterEnvelope.from_contract(receipt),
        request_bytes=canonical_json(request),
        preview_bytes=None if preview is None else canonical_json(preview),
    )
