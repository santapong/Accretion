"""Adapter SDK and exact-command guard; no simulator, launcher or authority store.

The mandatory AdmissionGuard is supplied by the host/gateway. It must check
live lease fencing, expiry/revocation, approval and atomic budget/command
reservation before returning. Local bookkeeping here is defense in depth, not
durable admission or evidence of host isolation.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from threading import Lock
from typing import Annotated, Protocol

from pydantic import Field

from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.refs import PolicyRef, VerifierRef
from accretion.contracts.robotics import (
    ActionIntent,
    EmbodimentDescriptor,
    ObservationSpec,
    PreparedCommand,
    SafetyDecisionReceipt,
    TrustedSafetyKey,
    verify_safety_signature,
)
from accretion.contracts.robotics.values import (
    DependencyClosure,
    Digest,
    EpisodeId,
    EpisodePins,
    Identifier,
    LeaseBinding,
    MotionBudgetState,
    PreparedTrajectory,
    SequenceNumber,
    StateBinding,
)

from .errors import RoboticsError
from .errors import RoboticsErrorCode as Code
from .observations import ArtifactReader, ObservationBatch, ObservationValidator, validate_artifact
from .protocol import (
    MAX_FRAME_BYTES,
    AdapterDescription,
    CommandScope,
    DescribeRequest,
    DescribeResult,
    DescriptorRecord,
    ErrorOutcome,
    ExecuteRequest,
    ExecuteResult,
    FrameCodec,
    HeartbeatRequest,
    HeartbeatResult,
    HelloRequest,
    HelloResult,
    IntentRecord,
    ObservationResult,
    ObserveRequest,
    PreparedRecord,
    PrepareRequest,
    PrepareResult,
    ProtocolRequest,
    ProtocolResponse,
    ResetRequest,
    RestoreRequest,
    SafetyRecord,
    SnapshotReference,
    SnapshotRequest,
    SnapshotResult,
    SuccessOutcome,
    SuccessPayload,
    TerminateRequest,
    TerminateResult,
    WireModel,
    parse_message,
    validate_response_binding,
)


class InitializationPins(WireModel):
    """Time-zero capture identity before preflight/approval exist; never authority."""

    episode_id: EpisodeId
    lease: LeaseBinding
    seed: Annotated[int, Field(ge=0, strict=True)]
    randomization_sample_hash: Digest

    @classmethod
    def from_episode(cls, episode: EpisodePins | InitializationPins) -> InitializationPins:
        return cls.model_validate({name: getattr(episode, name) for name in cls.model_fields})


class ExecutionPins(WireModel):
    """Trusted current values, resolved by the gateway, never taken from the caller."""

    workspace_id: Identifier
    project_id: Identifier
    episode: EpisodePins
    dependencies: DependencyClosure
    descriptor_hash: Digest
    adapter_principal_id: Identifier
    evaluator_principal_id: Identifier
    policy_ref: PolicyRef
    evaluator: VerifierRef
    approval_hash: Digest
    state: StateBinding
    budget: MotionBudgetState
    next_action_sequence: SequenceNumber
    replay_source_episode_id: EpisodeId | None = None

    def command_scope(self) -> CommandScope:
        return CommandScope(
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            episode_id=self.episode.episode_id,
            lease=self.episode.lease,
            expected_state=self.state,
            dependency_closure_hash=content_hash(self.dependencies, exclude=()),
        )


class RobotAdapter(Protocol):
    """Called only inside the approved host; never choose an endpoint from input.

    prepare/observe/snapshot must not advance physics. Trajectories use the
    pinned controller's PIECEWISE_QUINTIC_HERMITE_V1 q/v/a/t interpretation;
    the SDK neither substitutes interpolation nor proves actual dynamics.
    """

    def describe(self) -> AdapterDescription: ...
    def observe(self) -> ObservationBatch: ...
    def reset(self, *, seed: int, randomization_sample_hash: str) -> ObservationBatch: ...
    def prepare(self, intent: ActionIntent) -> PreparedCommand: ...
    def execute(
        self, prepared: PreparedCommand, safety: SafetyDecisionReceipt
    ) -> ObservationBatch: ...
    def snapshot(self) -> SnapshotReference: ...
    def restore(self, snapshot: SnapshotReference) -> ObservationBatch: ...
    def terminate(self) -> None: ...


class AdmissionGuard(Protocol):
    def authorize(self, request: ProtocolRequest, *, pins: ExecutionPins) -> None:
        """Fail closed unless current authority permits this exact operation.

        EXECUTE also atomically reserves the signed budget and consumes the
        exact candidate once. A failed/lost call cannot refund that reservation.
        Resolve the persisted SafetyEvaluation by receipt hash and compare its
        full current phase/physics-step/closure/preview context before consuming;
        a valid receipt signature by itself is insufficient authority.
        RESET/RESTORE require internal episode phase and fresh replay authority.
        """
        ...


def _intent(intent: ActionIntent, pins: ExecutionPins) -> None:
    if (
        intent.workspace_id != pins.workspace_id
        or intent.project_id != pins.project_id
        or intent.episode_id != pins.episode.episode_id
        or intent.state != pins.state
        or intent.sequence != pins.next_action_sequence
        or intent.expires_at_sim_time_ns <= pins.state.sim_time_ns
    ):
        raise RoboticsError(Code.EPISODE_STATE_CONFLICT)


def validate_prepared(
    intent: ActionIntent,
    prepared: PreparedCommand,
    *,
    pins: ExecutionPins,
    descriptor: EmbodimentDescriptor,
) -> None:
    # Boundary re-parsing catches altered nested models and preserves original seals.
    intent = IntentRecord.from_contract(intent).for_execution(ActionIntent)
    prepared = PreparedRecord.from_contract(prepared).for_execution(PreparedCommand)
    descriptor = DescriptorRecord.from_contract(descriptor).for_execution(EmbodimentDescriptor)
    if descriptor.content_hash != pins.descriptor_hash:
        raise RoboticsError(Code.CONFORMANCE_STALE)
    _intent(intent, pins)
    if (
        prepared.workspace_id != pins.workspace_id
        or prepared.project_id != pins.project_id
        or prepared.episode_id != pins.episode.episode_id
        or prepared.action_intent_hash != intent.content_hash
        or prepared.state != pins.state
        or prepared.sequence != intent.sequence
        or prepared.lease != pins.episode.lease
        or prepared.created_by.principal_id != pins.adapter_principal_id
        or prepared.controller_digest != pins.dependencies.controller_digest
        or prepared.adapter_artifact_digest != pins.dependencies.adapter_artifact_digest
        or prepared.command_schema_hash != pins.dependencies.prepared_command_schema_hash
        or prepared.controller_mode not in descriptor.control_interfaces
        or prepared.expires_at_sim_time_ns <= pins.state.sim_time_ns
        or prepared.expires_at_sim_time_ns > intent.expires_at_sim_time_ns
    ):
        raise RoboticsError(Code.SAFETY_DENIED)
    trajectory = prepared.command_payload
    if isinstance(trajectory, PreparedTrajectory):
        if (
            trajectory.joint_names != descriptor.kinematics.joint_names
            or intent.semantic_action.value == "SET_GRIPPER"
            or pins.state.sim_time_ns + trajectory.points[-1].time_from_start_ns
            > prepared.expires_at_sim_time_ns
        ):
            raise RoboticsError(Code.SAFETY_DENIED)
    elif intent.semantic_action.value != "SET_GRIPPER":
        raise RoboticsError(Code.SAFETY_DENIED)


def validate_execution(
    intent: ActionIntent,
    prepared: PreparedCommand,
    receipt: SafetyDecisionReceipt,
    *,
    pins: ExecutionPins,
    descriptor: EmbodimentDescriptor,
    trusted_keys: Mapping[str, TrustedSafetyKey],
) -> None:
    validate_prepared(intent, prepared, pins=pins, descriptor=descriptor)
    receipt = SafetyRecord.from_contract(receipt).for_execution(SafetyDecisionReceipt)
    decision = receipt.decision
    if (
        decision.decision != "ALLOW"
        or decision.workspace_id != pins.workspace_id
        or decision.project_id != pins.project_id
        or decision.episode_id != pins.episode.episode_id
        or decision.action_intent_hash != intent.content_hash
        or decision.prepared_command_hash != prepared.content_hash
        or decision.safety_envelope_hash != pins.episode.safety_envelope_hash
        or decision.episode_approval_hash != pins.approval_hash
        or decision.state != pins.state
        or decision.lease != pins.episode.lease
        or decision.policy_ref != pins.policy_ref
        or decision.evaluator != pins.evaluator
        or decision.evaluator_principal_id != pins.evaluator_principal_id
        or decision.budget_before != pins.budget
        or decision.expires_at_sim_time_ns <= pins.state.sim_time_ns
        or decision.expires_at_sim_time_ns > prepared.expires_at_sim_time_ns
    ):
        raise RoboticsError(Code.SAFETY_DENIED)
    try:
        verify_safety_signature(receipt, trusted_keys=trusted_keys)
    except ValueError as exc:
        raise RoboticsError(Code.SAFETY_DENIED) from exc


class AdapterSession:
    """Bound one worker to one episode. No automatic retries or reconnects.

    Constructor validates a real initial observation against trusted bootstrap
    pins. It performs no reset or actuation. The host must still supervise this
    synchronous SDK with process/deadline limits, including blocking adapters.
    """

    def __init__(
        self,
        adapter: RobotAdapter,
        *,
        pins: ExecutionPins,
        artifact_reader: ArtifactReader,
        admission_guard: AdmissionGuard,
        trusted_keys: Mapping[str, TrustedSafetyKey],
        observations: ObservationValidator | None = None,
    ) -> None:
        self._pins = canonical_json(ExecutionPins.model_validate(pins.model_dump(mode="python")))
        self.adapter = adapter
        self.reader = artifact_reader
        self.guard = admission_guard
        # Keep the trusted registry view live: revocation must not leave a stale copied key.
        self.trusted_keys = trusted_keys
        self.observations = observations or ObservationValidator()
        self._description = canonical_json(adapter.describe())
        description = AdapterDescription.model_validate_json(self._description)
        self._descriptor = description.descriptor.for_execution(EmbodimentDescriptor)
        self._spec = description.observation_spec.for_execution(ObservationSpec)
        if (
            description.dependencies != pins.dependencies
            or self._descriptor.content_hash != pins.descriptor_hash
            or self._spec.content_hash != pins.dependencies.observation_spec_hash
            or self._descriptor.workspace_id != pins.workspace_id
            or self._descriptor.project_id != pins.project_id
            or self._spec.workspace_id != pins.workspace_id
            or self._spec.project_id != pins.project_id
            or pins.replay_source_episode_id == pins.episode.episode_id
        ):
            raise RoboticsError(Code.CONFORMANCE_STALE)
        initial = self._observe(adapter.observe(), previous=pins.state)
        if initial.state_binding() != pins.state:
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        self._prepared: bytes | None = None
        self._seen: set[str] = set()
        self._last_sequence = -1
        self._dispatch_lock = Lock()
        self._reset = self._restored = self._executed = self._aborted = self._closed = False

    @property
    def pins(self) -> ExecutionPins:
        return ExecutionPins.model_validate_json(self._pins)

    @property
    def aborted(self) -> bool:
        return self._aborted

    def _observe(
        self, batch: ObservationBatch, *, previous: StateBinding | None
    ) -> ObservationBatch:
        pins = self.pins
        return self.observations.validate(
            self._spec,
            self._descriptor,
            batch,
            self.reader,
            episode_id=pins.episode.episode_id,
            lease=pins.episode.lease,
            previous=previous,
        ).batch

    def _update(
        self, batch: ObservationBatch, receipt: SafetyDecisionReceipt | None = None
    ) -> None:
        values = self.pins.model_dump(mode="python")
        values["state"] = batch.state_binding()
        if receipt is not None:
            values["budget"] = receipt.decision.budget_after
            values["next_action_sequence"] += 1
        self._pins = canonical_json(ExecutionPins.model_validate(values))
        self._prepared = None

    def _unchanged(self) -> ObservationBatch:
        batch = self._observe(self.adapter.observe(), previous=self.pins.state)
        if batch.state_binding() != self.pins.state:
            self._aborted = True
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        return batch

    def _authorize(self, request: ProtocolRequest, pins: ExecutionPins) -> None:
        copied = parse_message(canonical_json(request))
        assert isinstance(copied, ProtocolRequest)
        try:
            self.guard.authorize(
                copied, pins=ExecutionPins.model_validate_json(canonical_json(pins))
            )
        except RoboticsError:
            raise
        except Exception as exc:
            # Reservation status is unknown if the authoritative service fails unexpectedly.
            self._aborted = True
            raise RoboticsError(Code.ACKNOWLEDGEMENT_UNCERTAIN) from exc

    def dispatch(self, request: ProtocolRequest) -> ProtocolResponse:
        if not self._dispatch_lock.acquire(blocking=False):
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        try:
            return self._dispatch_serial(request)
        finally:
            self._dispatch_lock.release()

    def _dispatch_serial(self, request: ProtocolRequest) -> ProtocolResponse:
        checked = parse_message(FrameCodec().encode(request)[:-1])
        assert isinstance(checked, ProtocolRequest)
        if checked.request_id in self._seen or checked.sequence <= self._last_sequence:
            raise RoboticsError(Code.IDEMPOTENCY_CONFLICT)
        if len(self._seen) >= 10_000:
            raise RoboticsError(Code.RESOURCE_CAP_EXHAUSTED)
        if self._closed or (self._aborted and not isinstance(checked.payload, TerminateRequest)):
            raise RoboticsError(
                Code.ACKNOWLEDGEMENT_UNCERTAIN if self._aborted else Code.ADAPTER_CRASH
            )
        self._seen.add(checked.request_id)
        self._last_sequence = checked.sequence
        completed_mutation = False
        try:
            result = self._dispatch(checked)
            completed_mutation = isinstance(
                checked.payload, (ExecuteRequest, ResetRequest, RestoreRequest)
            )
            # A nested object supplied to an adapter cannot silently mutate a sealed request.
            FrameCodec().encode(checked)
            response = ProtocolResponse.create(checked, SuccessOutcome(payload=result))
            validate_response_binding(checked, response)
            FrameCodec().encode(response)
            return response
        except RoboticsError as exc:
            if completed_mutation or exc.code is Code.ACKNOWLEDGEMENT_UNCERTAIN:
                self._aborted = True
                return ProtocolResponse.create(
                    checked, ErrorOutcome(code=Code.ACKNOWLEDGEMENT_UNCERTAIN)
                )
            return ProtocolResponse.create(checked, ErrorOutcome(code=exc.code))

    def _dispatch(self, request: ProtocolRequest) -> SuccessPayload:
        payload = request.payload
        if isinstance(payload, HelloRequest):
            return HelloResult()
        pins = self.pins
        if request.scope != pins.command_scope():
            raise RoboticsError(Code.LEASE_INVALID)
        # All local structural checks precede authority consumption and adapter calls.
        if isinstance(payload, ExecuteRequest):
            return self._execute(request, payload)
        if isinstance(payload, ResetRequest):
            if (
                self._reset
                or self._restored
                or self._executed
                or (
                    payload.seed != pins.episode.seed
                    or payload.randomization_sample_hash != pins.episode.randomization_sample_hash
                )
            ):
                raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        if isinstance(payload, RestoreRequest):
            if (
                self._restored
                or self._executed
                or (
                    pins.replay_source_episode_id is None
                    or payload.snapshot.source_episode_id != pins.replay_source_episode_id
                    or payload.snapshot.dependency_closure_hash
                    != content_hash(pins.dependencies, exclude=())
                )
            ):
                raise RoboticsError(Code.REPLAY_FAILED)
            validate_artifact(self.reader, payload.snapshot.artifact, max_bytes=32 * 1024 * 1024)
        if isinstance(payload, PrepareRequest):
            _intent(payload.intent.for_execution(ActionIntent), pins)
        self._authorize(request, pins)
        try:
            if isinstance(payload, DescribeRequest):
                return DescribeResult(
                    description=AdapterDescription.model_validate_json(self._description)
                )
            if isinstance(payload, HeartbeatRequest):
                return HeartbeatResult()
            if isinstance(payload, TerminateRequest):
                self.adapter.terminate()
                self._closed = True
                return TerminateResult()
            if isinstance(payload, ObserveRequest):
                # v1 reads the exact current observation: no clock/sequence/state refresh.
                # In particular, observing cannot spend an unsigned simulation step.
                batch = self._unchanged()
                return ObservationResult(op=payload.op, observation=batch)
            if isinstance(payload, ResetRequest):
                self._reset = True
                batch = self._observe(
                    self.adapter.reset(
                        seed=payload.seed,
                        randomization_sample_hash=payload.randomization_sample_hash,
                    ),
                    previous=None,
                )
                if batch.sim_time_ns != 0:
                    raise RoboticsError(Code.CLOCK_REGRESSION)
                self._update(batch)
                return ObservationResult(op=payload.op, observation=batch)
            if isinstance(payload, RestoreRequest):
                self._restored = True
                batch = self._observe(self.adapter.restore(payload.snapshot), previous=None)
                if (
                    batch.sim_time_ns != payload.snapshot.state.sim_time_ns
                    or batch.frame_transform_digest != payload.snapshot.state.frame_transform_digest
                ):
                    raise RoboticsError(Code.REPLAY_FAILED)
                self._update(batch)
                return ObservationResult(op=payload.op, observation=batch)
            if isinstance(payload, PrepareRequest):
                self._unchanged()
                intent = payload.intent.for_execution(ActionIntent)
                prepared = self.adapter.prepare(intent)
                self._unchanged()
                validate_prepared(intent, prepared, pins=pins, descriptor=self._descriptor)
                validate_artifact(
                    self.reader,
                    prepared.command_ref,
                    max_bytes=MAX_FRAME_BYTES,
                    expected=canonical_json(prepared.command_payload),
                )
                record = PreparedRecord.from_contract(prepared)
                self._prepared = record.original_json.encode()
                return PrepareResult(prepared=record)
            if isinstance(payload, SnapshotRequest):
                self._unchanged()
                snapshot = self.adapter.snapshot()
                self._unchanged()
                if (
                    snapshot.source_episode_id != pins.episode.episode_id
                    or snapshot.state != pins.state
                    or snapshot.dependency_closure_hash
                    != content_hash(pins.dependencies, exclude=())
                ):
                    raise RoboticsError(Code.ARTIFACT_INVALID)
                validate_artifact(self.reader, snapshot.artifact, max_bytes=32 * 1024 * 1024)
                return SnapshotResult(snapshot=snapshot)
        except Exception as exc:
            # Once an adapter is invoked, an error cannot establish absence of effects.
            self._aborted = True
            if isinstance(payload, (ResetRequest, RestoreRequest)):
                raise RoboticsError(Code.ACKNOWLEDGEMENT_UNCERTAIN) from exc
            if isinstance(exc, RoboticsError):
                raise
            raise RoboticsError(Code.ADAPTER_CRASH) from exc
        raise RoboticsError(Code.INVALID_REQUEST)

    def _execute(self, request: ProtocolRequest, payload: ExecuteRequest) -> ExecuteResult:
        pins = self.pins
        intent = payload.intent.for_execution(ActionIntent)
        prepared = payload.prepared.for_execution(PreparedCommand)
        safety = payload.safety.for_execution(SafetyDecisionReceipt)
        validate_execution(
            intent,
            prepared,
            safety,
            pins=pins,
            descriptor=self._descriptor,
            trusted_keys=self.trusted_keys,
        )
        if self._prepared != payload.prepared.original_json.encode():
            raise RoboticsError(Code.SAFETY_DENIED)
        validate_artifact(
            self.reader,
            prepared.command_ref,
            max_bytes=MAX_FRAME_BYTES,
            expected=canonical_json(prepared.command_payload),
        )
        self._unchanged()
        self._authorize(request, pins)
        # Burn before invocation. Neither a typed exception nor a lost acknowledgement refunds it.
        self._prepared = None
        self._executed = True
        prepared_bytes, safety_bytes = canonical_json(prepared), canonical_json(safety)
        try:
            batch = self._observe(self.adapter.execute(prepared, safety), previous=pins.state)
            if canonical_json(prepared) != prepared_bytes or canonical_json(safety) != safety_bytes:
                raise RoboticsError(Code.SAFETY_DENIED)
            if (
                batch.sim_time_ns <= pins.state.sim_time_ns
                or batch.sequence <= pins.state.observation_sequence
                or batch.sim_time_ns > safety.decision.expires_at_sim_time_ns
                or Decimal(batch.sim_time_ns - pins.state.sim_time_ns)
                > (
                    Decimal(str(safety.decision.budget_after.elapsed_sim_seconds))
                    - Decimal(str(safety.decision.budget_before.elapsed_sim_seconds))
                )
                * 1_000_000_000
            ):
                raise RoboticsError(Code.OBSERVATION_INVALID)
            self._update(batch, safety)
            return ExecuteResult(
                observation=batch,
                prepared_command_hash=prepared.content_hash,
                safety_decision_hash=safety.content_hash,
            )
        except Exception as exc:
            self._aborted = True
            raise RoboticsError(Code.ACKNOWLEDGEMENT_UNCERTAIN) from exc
