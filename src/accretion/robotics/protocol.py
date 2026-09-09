"""Simulation SDK v1 control frames, with no transport or process launcher.

The host owns bounded reads, deadlines and isolation. Feed this codec chunks
no larger than its ceiling. stdout is exclusively JSONL; stderr is a separate,
host-bounded diagnostic stream. Wire digests provide correlation, not authority.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from typing import Annotated, Any, Literal, NoReturn, Self

from pydantic import ConfigDict, Field, TypeAdapter, model_validator

from accretion.contracts import StrictModel
from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics import (
    ActionIntent,
    CanonicalWriterEnvelope,
    EmbodimentDescriptor,
    ObservationSpec,
    PreparedCommand,
    SafetyDecisionReceipt,
)
from accretion.contracts.robotics.models import RoboticsContract
from accretion.contracts.robotics.values import (
    DependencyClosure,
    Digest,
    EpisodeId,
    Identifier,
    LeaseBinding,
    SequenceNumber,
    SimulationArtifactRef,
    StateBinding,
)

from .errors import RoboticsError
from .errors import RoboticsErrorCode as Code
from .observations import ObservationBatch

WIRE_VERSION: Literal["1.0.0"] = "1.0.0"
MAX_FRAME_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_TOKENS = 100_000
Operation = Literal[
    "HELLO",
    "DESCRIBE",
    "RESET",
    "OBSERVE",
    "PREPARE",
    "EXECUTE",
    "SNAPSHOT",
    "RESTORE",
    "TERMINATE",
    "HEARTBEAT",
]
MUTATING_OPERATIONS = frozenset({"RESET", "EXECUTE", "RESTORE"})


def _scan_json(raw: bytes, max_bytes: int) -> None:
    if not raw or len(raw) > max_bytes:
        raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
    depth = tokens = 0
    quoted = escaped = False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
            continue
        if char == 34:
            quoted = True
        elif char in (91, 123):
            depth += 1
            tokens += 1
        elif char in (93, 125):
            depth -= 1
        elif char in (44, 58):
            tokens += 1
        if depth > MAX_JSON_DEPTH or tokens > MAX_JSON_TOKENS:
            raise RoboticsError(Code.PAYLOAD_TOO_LARGE)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite JSON number")
    return result


def _int(value: str) -> int:
    if len(value) > 32:
        raise ValueError("unbounded JSON integer")
    return int(value)


def _constant(value: str) -> None:
    raise ValueError("invalid JSON numeric constant")


def bounded_json(raw: bytes, *, max_bytes: int = MAX_FRAME_BYTES) -> Any:
    """Bound nesting and token allocation before invoking the JSON parser."""
    _scan_json(raw, max_bytes)
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_float=_float,
            parse_int=_int,
            parse_constant=_constant,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise RoboticsError(Code.INVALID_REQUEST) from exc


class WireModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WriterRecord(WireModel):
    """Retain original bytes and seal. Never authorize through an upcast projection."""

    original_json: str = Field(min_length=1, max_length=MAX_FRAME_BYTES, strict=True)

    def for_execution[C: RoboticsContract](self, model: type[C]) -> C:
        payload = bounded_json(self.original_json.encode())
        if not isinstance(payload, dict) or payload.get("schema_version") != WIRE_VERSION:
            raise RoboticsError(Code.UNKNOWN_CONTRACT_VERSION)
        try:
            return CanonicalWriterEnvelope(self.original_json).for_execution(model)
        except ValueError as exc:
            raise RoboticsError(Code.INVALID_CONTRACT) from exc

    @classmethod
    def from_contract(cls, contract: RoboticsContract) -> Self:
        try:
            original = CanonicalWriterEnvelope.from_contract(contract).forward_json()
        except ValueError as exc:
            raise RoboticsError(Code.INVALID_CONTRACT) from exc
        return cls(original_json=original)


class IntentRecord(WriterRecord):
    @model_validator(mode="after")
    def _typed(self) -> Self:
        self.for_execution(ActionIntent)
        return self


class PreparedRecord(WriterRecord):
    @model_validator(mode="after")
    def _typed(self) -> Self:
        self.for_execution(PreparedCommand)
        return self


class SafetyRecord(WriterRecord):
    @model_validator(mode="after")
    def _typed(self) -> Self:
        self.for_execution(SafetyDecisionReceipt)
        return self


class DescriptorRecord(WriterRecord):
    @model_validator(mode="after")
    def _typed(self) -> Self:
        self.for_execution(EmbodimentDescriptor)
        return self


class ObservationSpecRecord(WriterRecord):
    @model_validator(mode="after")
    def _typed(self) -> Self:
        self.for_execution(ObservationSpec)
        return self


class CommandScope(WireModel):
    workspace_id: Identifier
    project_id: Identifier
    episode_id: EpisodeId
    lease: LeaseBinding
    expected_state: StateBinding
    dependency_closure_hash: Digest


class AdapterDescription(WireModel):
    descriptor: DescriptorRecord
    observation_spec: ObservationSpecRecord
    dependencies: DependencyClosure
    wire_version: Literal["1.0.0"] = WIRE_VERSION


class SnapshotReference(WireModel):
    source_episode_id: EpisodeId
    state: StateBinding
    dependency_closure_hash: Digest
    artifact: SimulationArtifactRef


class HelloRequest(WireModel):
    op: Literal["HELLO"] = "HELLO"


class DescribeRequest(WireModel):
    op: Literal["DESCRIBE"] = "DESCRIBE"


class ResetRequest(WireModel):
    op: Literal["RESET"] = "RESET"
    seed: SequenceNumber
    randomization_sample_hash: Digest


class ObserveRequest(WireModel):
    op: Literal["OBSERVE"] = "OBSERVE"


class PrepareRequest(WireModel):
    op: Literal["PREPARE"] = "PREPARE"
    intent: IntentRecord


class ExecuteRequest(WireModel):
    op: Literal["EXECUTE"] = "EXECUTE"
    intent: IntentRecord
    prepared: PreparedRecord
    safety: SafetyRecord


class SnapshotRequest(WireModel):
    op: Literal["SNAPSHOT"] = "SNAPSHOT"


class RestoreRequest(WireModel):
    op: Literal["RESTORE"] = "RESTORE"
    snapshot: SnapshotReference


class TerminateRequest(WireModel):
    op: Literal["TERMINATE"] = "TERMINATE"


class HeartbeatRequest(WireModel):
    op: Literal["HEARTBEAT"] = "HEARTBEAT"


RequestPayload = Annotated[
    HelloRequest
    | DescribeRequest
    | ResetRequest
    | ObserveRequest
    | PrepareRequest
    | ExecuteRequest
    | SnapshotRequest
    | RestoreRequest
    | TerminateRequest
    | HeartbeatRequest,
    Field(discriminator="op"),
]


class ProtocolRequest(WireModel):
    kind: Literal["request"] = "request"
    wire_version: Literal["1.0.0"] = WIRE_VERSION
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$", strict=True)
    sequence: SequenceNumber
    scope: CommandScope | None
    payload: RequestPayload
    request_digest: Digest

    @model_validator(mode="after")
    def _binding(self) -> Self:
        if (self.scope is None) != (self.payload.op == "HELLO"):
            raise ValueError("all session operations require scope; HELLO has no session authority")
        if self.request_digest != content_hash(self, exclude=("request_digest",)):
            raise ValueError("request digest mismatch")
        return self

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        sequence: int,
        scope: CommandScope | None,
        payload: RequestPayload,
    ) -> ProtocolRequest:
        body = dict(
            kind="request",
            wire_version=WIRE_VERSION,
            request_id=request_id,
            sequence=sequence,
            scope=scope,
            payload=payload,
        )
        return cls.model_validate({**body, "request_digest": content_hash(body, exclude=())})


class HelloResult(WireModel):
    op: Literal["HELLO"] = "HELLO"
    wire_version: Literal["1.0.0"] = WIRE_VERSION


class DescribeResult(WireModel):
    op: Literal["DESCRIBE"] = "DESCRIBE"
    description: AdapterDescription


class ObservationResult(WireModel):
    op: Literal["OBSERVE", "RESET", "RESTORE"]
    observation: ObservationBatch


class PrepareResult(WireModel):
    op: Literal["PREPARE"] = "PREPARE"
    prepared: PreparedRecord


class ExecuteResult(WireModel):
    op: Literal["EXECUTE"] = "EXECUTE"
    observation: ObservationBatch
    prepared_command_hash: Digest
    safety_decision_hash: Digest


class SnapshotResult(WireModel):
    op: Literal["SNAPSHOT"] = "SNAPSHOT"
    snapshot: SnapshotReference


class TerminateResult(WireModel):
    op: Literal["TERMINATE"] = "TERMINATE"


class HeartbeatResult(WireModel):
    op: Literal["HEARTBEAT"] = "HEARTBEAT"


SuccessPayload = Annotated[
    HelloResult
    | DescribeResult
    | ObservationResult
    | PrepareResult
    | ExecuteResult
    | SnapshotResult
    | TerminateResult
    | HeartbeatResult,
    Field(discriminator="op"),
]


class SuccessOutcome(WireModel):
    status: Literal["OK"] = "OK"
    payload: SuccessPayload


class ErrorOutcome(WireModel):
    status: Literal["ERROR"] = "ERROR"
    code: Code


class ProtocolResponse(WireModel):
    kind: Literal["response"] = "response"
    wire_version: Literal["1.0.0"] = WIRE_VERSION
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$", strict=True)
    sequence: SequenceNumber
    op: Operation
    request_digest: Digest
    scope: CommandScope | None
    outcome: Annotated[SuccessOutcome | ErrorOutcome, Field(discriminator="status")]
    response_digest: Digest

    @model_validator(mode="after")
    def _binding(self) -> Self:
        if isinstance(self.outcome, SuccessOutcome) and self.op != self.outcome.payload.op:
            raise ValueError("response operation mismatch")
        if self.response_digest != content_hash(self, exclude=("response_digest",)):
            raise ValueError("response digest mismatch")
        return self

    @classmethod
    def create(
        cls, request: ProtocolRequest, outcome: SuccessOutcome | ErrorOutcome
    ) -> ProtocolResponse:
        body = dict(
            kind="response",
            wire_version=WIRE_VERSION,
            request_id=request.request_id,
            sequence=request.sequence,
            op=request.payload.op,
            request_digest=request.request_digest,
            scope=request.scope,
            outcome=outcome,
        )
        return cls.model_validate({**body, "response_digest": content_hash(body, exclude=())})


ProtocolMessage = Annotated[ProtocolRequest | ProtocolResponse, Field(discriminator="kind")]
_MESSAGES: TypeAdapter[ProtocolMessage] = TypeAdapter(ProtocolMessage)


def parse_message(raw: bytes, *, max_bytes: int = MAX_FRAME_BYTES) -> ProtocolMessage:
    value = bounded_json(raw, max_bytes=max_bytes)
    if not isinstance(value, dict) or value.get("wire_version") != WIRE_VERSION:
        raise RoboticsError(Code.UNKNOWN_CONTRACT_VERSION)
    try:
        return _MESSAGES.validate_python(value)
    except ValueError as exc:
        raise RoboticsError(Code.INVALID_REQUEST) from exc


def validate_response_binding(request: ProtocolRequest, response: ProtocolResponse) -> None:
    """Check correlated claims; actual observation/snapshot bytes still need the reader."""
    if (
        response.request_id != request.request_id
        or response.sequence != request.sequence
        or response.request_digest != request.request_digest
        or response.scope != request.scope
        or response.op != request.payload.op
    ):
        raise RoboticsError(Code.INVALID_REQUEST)
    if isinstance(response.outcome, ErrorOutcome):
        return
    result = response.outcome.payload
    scope = request.scope
    if isinstance(result, HelloResult):
        return
    if scope is None:
        raise RoboticsError(Code.INVALID_REQUEST)
    if isinstance(result, DescribeResult) and (
        content_hash(result.description.dependencies, exclude=()) != scope.dependency_closure_hash
    ):
        raise RoboticsError(Code.CONFORMANCE_STALE)
    if isinstance(result, PrepareResult):
        if not isinstance(request.payload, PrepareRequest):
            raise RoboticsError(Code.INVALID_REQUEST)
        intent = request.payload.intent.for_execution(ActionIntent)
        prepared = result.prepared.for_execution(PreparedCommand)
        if (
            prepared.episode_id != scope.episode_id
            or prepared.lease != scope.lease
            or prepared.workspace_id != scope.workspace_id
            or prepared.project_id != scope.project_id
            or prepared.state != scope.expected_state
            or prepared.action_intent_hash != intent.content_hash
            or prepared.sequence != intent.sequence
        ):
            raise RoboticsError(Code.SAFETY_DENIED)
    if isinstance(result, SnapshotResult) and (
        result.snapshot.source_episode_id != scope.episode_id
        or result.snapshot.state != scope.expected_state
        or result.snapshot.dependency_closure_hash != scope.dependency_closure_hash
    ):
        raise RoboticsError(Code.ARTIFACT_INVALID)
    if isinstance(result, (ObservationResult, ExecuteResult)):
        batch = result.observation
        if batch.episode_id != scope.episode_id or batch.lease != scope.lease:
            raise RoboticsError(Code.LEASE_INVALID)
        if result.op == "OBSERVE":
            if batch.state_binding() != scope.expected_state:
                raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        elif result.op == "RESET":
            if batch.sim_time_ns != 0 or batch.sequence != 0:
                raise RoboticsError(Code.CLOCK_REGRESSION)
        elif result.op == "RESTORE":
            if not isinstance(request.payload, RestoreRequest) or (
                batch.sim_time_ns != request.payload.snapshot.state.sim_time_ns
                or batch.frame_transform_digest
                != request.payload.snapshot.state.frame_transform_digest
            ):
                raise RoboticsError(Code.REPLAY_FAILED)
        elif (
            batch.sim_time_ns < scope.expected_state.sim_time_ns
            or batch.sequence < scope.expected_state.observation_sequence
            or (
                batch.sequence == scope.expected_state.observation_sequence
                and batch.state_binding() != scope.expected_state
            )
        ):
            raise RoboticsError(Code.CLOCK_REGRESSION)
    if isinstance(result, ExecuteResult):
        if not isinstance(request.payload, ExecuteRequest):
            raise RoboticsError(Code.INVALID_REQUEST)
        prepared = request.payload.prepared.for_execution(PreparedCommand)
        safety = request.payload.safety.for_execution(SafetyDecisionReceipt)
        if (
            result.prepared_command_hash != prepared.content_hash
            or result.safety_decision_hash != safety.content_hash
            or result.observation.sim_time_ns <= scope.expected_state.sim_time_ns
            or result.observation.sequence <= scope.expected_state.observation_sequence
            or result.observation.sim_time_ns > safety.decision.expires_at_sim_time_ns
        ):
            raise RoboticsError(Code.SAFETY_DENIED)


class FrameCodec:
    def __init__(self, max_frame_bytes: int = MAX_FRAME_BYTES) -> None:
        if type(max_frame_bytes) is not int or not 0 < max_frame_bytes <= MAX_FRAME_BYTES:
            raise ValueError("frame ceiling may only be lowered")
        self.max_frame_bytes = max_frame_bytes
        self._buffer = bytearray()
        self._failed = False

    def encode(self, message: ProtocolMessage) -> bytes:
        raw = canonical_json(message)
        parse_message(raw, max_bytes=self.max_frame_bytes)
        return raw + b"\n"

    def feed(self, data: bytes) -> Iterator[ProtocolMessage]:
        if self._failed:
            raise RoboticsError(Code.INVALID_REQUEST)
        try:
            if type(data) is not bytes or len(data) > self.max_frame_bytes:
                raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
            start = 0
            while start < len(data):
                newline = data.find(b"\n", start)
                end = len(data) if newline < 0 else newline
                if len(self._buffer) + end - start > self.max_frame_bytes:
                    raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
                self._buffer.extend(memoryview(data)[start:end])
                if newline < 0:
                    break
                raw = bytes(self._buffer)
                self._buffer.clear()
                yield parse_message(raw, max_bytes=self.max_frame_bytes)
                start = end + 1
        except RoboticsError:
            self._failed = True
            self._buffer.clear()
            raise

    def finish(self) -> None:
        if self._buffer or self._failed:
            self._failed = True
            self._buffer.clear()
            raise RoboticsError(Code.INVALID_REQUEST)


class ResponseCorrelator:
    """Single in-flight request; a failed mutation is never retryable in this session.

    The host calls fail_pending on deadline, EOF, heartbeat loss or malformed
    response. This is local protocol bookkeeping, not durable exactly-once storage.
    """

    def __init__(self) -> None:
        self._pending: bytes | None = None
        self._last_sequence = -1
        self._seen: set[str] = set()
        self.uncertain = False
        self._failed = False

    def begin(self, request: ProtocolRequest) -> bytes:
        if self._failed:
            raise RoboticsError(
                Code.ACKNOWLEDGEMENT_UNCERTAIN if self.uncertain else Code.ADAPTER_CRASH
            )
        raw = FrameCodec().encode(request)
        checked = parse_message(raw[:-1])
        assert isinstance(checked, ProtocolRequest)
        if self._pending is not None or checked.sequence <= self._last_sequence:
            raise RoboticsError(Code.INVALID_REQUEST)
        if checked.request_id in self._seen:
            raise RoboticsError(Code.IDEMPOTENCY_CONFLICT)
        if len(self._seen) >= 10_000:
            raise RoboticsError(Code.RESOURCE_CAP_EXHAUSTED)
        self._seen.add(checked.request_id)
        self._last_sequence = checked.sequence
        self._pending = raw[:-1]
        return raw

    def fail_pending(self) -> NoReturn:
        self._failed = True
        if self._pending is not None:
            request = parse_message(self._pending)
            assert isinstance(request, ProtocolRequest)
            self.uncertain |= request.payload.op in MUTATING_OPERATIONS
        self._pending = None
        raise RoboticsError(
            Code.ACKNOWLEDGEMENT_UNCERTAIN if self.uncertain else Code.ADAPTER_CRASH
        )

    def accept(self, response: ProtocolResponse) -> ProtocolResponse:
        if self._pending is None:
            raise RoboticsError(
                Code.ACKNOWLEDGEMENT_UNCERTAIN if self.uncertain else Code.INVALID_REQUEST
            )
        request = parse_message(self._pending)
        assert isinstance(request, ProtocolRequest)
        try:
            checked = parse_message(canonical_json(response))
            if not isinstance(checked, ProtocolResponse):
                self.fail_pending()
            validate_response_binding(request, checked)
            if isinstance(checked.outcome, ErrorOutcome) and (checked.op in MUTATING_OPERATIONS):
                # An adapter error cannot establish that mutation never occurred.
                self.fail_pending()
        except (ValueError, RoboticsError):
            self.fail_pending()
        self._pending = None
        return checked
