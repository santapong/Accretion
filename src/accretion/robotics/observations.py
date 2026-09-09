"""Bounded, simulator-independent observation and artifact validation.

Tensor v1 is an uncompressed, C-order, little-endian byte stream. There is no
pickle, archive, filesystem path or network resolver in this module. The reader
must authorize its caller and enforce the supplied limits before reading; this
consumer independently verifies the bytes it receives.
"""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, Protocol, Self

from pydantic import ConfigDict, Field, model_validator

from accretion.contracts import StrictModel
from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics import (
    CanonicalWriterEnvelope,
    EmbodimentDescriptor,
    ObservationSpec,
)
from accretion.contracts.robotics.values import (
    ContentAddressedArtifactRef,
    Digest,
    EpisodeId,
    LeaseBinding,
    ObservationField,
    SequenceNumber,
    SimulationArtifactRef,
    StateBinding,
)

from .errors import RoboticsError
from .errors import RoboticsErrorCode as Code

TENSOR_MEDIA_TYPE = "application/x.accretion.tensor"
MAX_TENSOR_BYTES = 32 * 1024 * 1024
MAX_BATCH_BYTES = 64 * 1024 * 1024
MAX_CHUNK_BYTES = 64 * 1024
_FORMATS = {"float64": "d", "float32": "f", "uint8": "B", "int64": "q", "bool": "B"}


class ArtifactReader(Protocol):
    def iter_bytes(
        self, ref: ContentAddressedArtifactRef, *, max_bytes: int, chunk_size: int
    ) -> Iterator[bytes]:
        """Yield nonempty chunks <= chunk_size, enforcing max_bytes before allocation."""
        ...


class ObservationSample(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    field: ObservationField
    encoding: Literal["RAW_LE_V1"] = "RAW_LE_V1"
    sequence: SequenceNumber
    sim_time_ns: SequenceNumber
    artifact: SimulationArtifactRef


class ObservationBatch(StrictModel):
    """Internal wire value, not a new canonical registry contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    episode_id: EpisodeId
    lease: LeaseBinding
    sequence: SequenceNumber
    sim_time_ns: SequenceNumber
    frame_transform_digest: Digest
    samples: tuple[ObservationSample, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        names = [sample.field.field for sample in self.samples]
        if len(set(names)) != len(names):
            raise ValueError("duplicate observation field")
        return self

    def state_binding(self) -> StateBinding:
        return StateBinding(
            observation_digest=content_hash(self, exclude=()),
            observation_sequence=self.sequence,
            sim_time_ns=self.sim_time_ns,
            frame_transform_digest=self.frame_transform_digest,
        )


@dataclass(frozen=True, slots=True)
class ObservationLimits:
    tensor_bytes: int = MAX_TENSOR_BYTES
    batch_bytes: int = MAX_BATCH_BYTES
    chunk_bytes: int = MAX_CHUNK_BYTES

    def __post_init__(self) -> None:
        for value, maximum in (
            (self.tensor_bytes, MAX_TENSOR_BYTES),
            (self.batch_bytes, MAX_BATCH_BYTES),
            (self.chunk_bytes, MAX_CHUNK_BYTES),
        ):
            if type(value) is not int or not 0 < value <= maximum:
                raise ValueError("observation limits may only lower the SDK ceilings")


def verified_chunks(
    reader: ArtifactReader,
    ref: ContentAddressedArtifactRef,
    *,
    max_bytes: int,
    chunk_size: int = MAX_CHUNK_BYTES,
) -> Iterator[bytes]:
    """Full consumption is mandatory: a prefix is not verified evidence."""
    if (
        type(max_bytes) is not int
        or max_bytes < 0
        or type(chunk_size) is not int
        or not 0 < chunk_size <= MAX_CHUNK_BYTES
    ):
        raise ValueError("invalid byte limits")
    checked = ContentAddressedArtifactRef.model_validate(ref.model_dump(mode="python"))
    if checked.size_bytes > max_bytes:
        raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
    digest = hashlib.sha256()
    total = 0
    try:
        for chunk in reader.iter_bytes(checked, max_bytes=max_bytes, chunk_size=chunk_size):
            if type(chunk) is not bytes or not chunk or len(chunk) > chunk_size:
                raise RoboticsError(Code.ARTIFACT_INVALID)
            total += len(chunk)
            if total > max_bytes or total > checked.size_bytes:
                raise RoboticsError(Code.ARTIFACT_INVALID)
            digest.update(chunk)
            yield chunk
    except RoboticsError:
        raise
    except (OSError, KeyError) as exc:
        raise RoboticsError(Code.ARTIFACT_UNAVAILABLE) from exc
    if total != checked.size_bytes or digest.hexdigest() != checked.digest:
        raise RoboticsError(Code.ARTIFACT_INVALID)


def validate_artifact(
    reader: ArtifactReader,
    ref: ContentAddressedArtifactRef,
    *,
    max_bytes: int,
    expected: bytes | None = None,
) -> None:
    if expected is not None and len(expected) != ref.size_bytes:
        raise RoboticsError(Code.ARTIFACT_INVALID)
    offset = 0
    for chunk in verified_chunks(reader, ref, max_bytes=max_bytes):
        if expected is not None and chunk != expected[offset : offset + len(chunk)]:
            raise RoboticsError(Code.ARTIFACT_INVALID)
        offset += len(chunk)


@dataclass(frozen=True, slots=True)
class ValidatedObservation:
    """Keep an immutable validated copy; exposed models are fresh projections."""

    original_json: bytes

    @property
    def batch(self) -> ObservationBatch:
        return ObservationBatch.model_validate_json(self.original_json)

    @property
    def state(self) -> StateBinding:
        return self.batch.state_binding()


class ObservationValidator:
    def __init__(self, limits: ObservationLimits | None = None) -> None:
        self.limits = limits or ObservationLimits()

    def validate(
        self,
        spec: ObservationSpec,
        descriptor: EmbodimentDescriptor,
        batch: ObservationBatch,
        artifact_reader: ArtifactReader,
        *,
        episode_id: str,
        lease: LeaseBinding,
        previous: StateBinding | None = None,
    ) -> ValidatedObservation:
        try:
            spec = CanonicalWriterEnvelope.from_contract(spec).for_execution(ObservationSpec)
            descriptor = CanonicalWriterEnvelope.from_contract(descriptor).for_execution(
                EmbodimentDescriptor
            )
            checked = ObservationBatch.model_validate(batch.model_dump(mode="python"))
        except ValueError as exc:
            raise RoboticsError(Code.OBSERVATION_INVALID) from exc
        if checked.episode_id != episode_id or checked.lease != lease:
            raise RoboticsError(Code.LEASE_INVALID)
        state = checked.state_binding()
        if previous is not None:
            if (
                state.sim_time_ns < previous.sim_time_ns
                or state.observation_sequence < previous.observation_sequence
            ):
                raise RoboticsError(Code.CLOCK_REGRESSION)
            if state.observation_sequence == previous.observation_sequence and state != previous:
                raise RoboticsError(Code.OBSERVATION_INVALID)
        definitions = {item.field: item for item in (*spec.required, *spec.optional)}
        sensors = {sensor.sensor_id: sensor for sensor in descriptor.sensors}
        fields = {sample.field.field for sample in checked.samples}
        if not {item.field for item in spec.required} <= fields or not fields <= definitions.keys():
            raise RoboticsError(Code.OBSERVATION_INVALID)
        planned: list[tuple[ObservationSample, int]] = []
        total = 0
        # Validate the whole batch's metadata and aggregate size before any reader call.
        for sample in checked.samples:
            field = sample.field
            sensor = sensors.get(field.sensor_id)
            if (
                field != definitions[field.field]
                or sensor is None
                or (sensor.modality != field.modality or sensor.frame_id != field.frame_id)
            ):
                raise RoboticsError(Code.OBSERVATION_INVALID)
            if field.modality == "JOINT_STATE" and field.shape != [
                len(descriptor.kinematics.joint_names)
            ]:
                raise RoboticsError(Code.OBSERVATION_INVALID)
            if sample.sequence != checked.sequence:
                raise RoboticsError(Code.OBSERVATION_INVALID)
            skew = checked.sim_time_ns - sample.sim_time_ns
            if skew < 0 or skew > spec.time_alignment.maximum_skew_ms * 1_000_000:
                raise RoboticsError(Code.OBSERVATION_SKEW)
            size = struct.calcsize("<" + _FORMATS[field.dtype])
            for dimension in field.shape:
                if dimension > self.limits.tensor_bytes // size:
                    raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
                size *= dimension
            total += size
            if total > self.limits.batch_bytes:
                raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
            if (
                sample.artifact.size_bytes != size
                or sample.artifact.media_type != TENSOR_MEDIA_TYPE
            ):
                raise RoboticsError(Code.ARTIFACT_INVALID)
            planned.append((sample, size))
        for sample, size in planned:
            self._tensor(sample, size, artifact_reader)
        return ValidatedObservation(canonical_json(checked))

    def _tensor(self, sample: ObservationSample, size: int, reader: ArtifactReader) -> None:
        field = sample.field
        fmt = "<" + _FORMATS[field.dtype]
        width = struct.calcsize(fmt)
        carry = b""
        quaternion_norm = 0.0
        for chunk in verified_chunks(
            reader, sample.artifact, max_bytes=size, chunk_size=self.limits.chunk_bytes
        ):
            data = carry + chunk
            end = len(data) // width * width
            for (value,) in struct.iter_unpack(fmt, memoryview(data)[:end]):
                if not math.isfinite(value) or (field.dtype == "bool" and value not in (0, 1)):
                    raise RoboticsError(Code.OBSERVATION_INVALID)
                if field.modality == "DEPTH" and value <= 0:
                    raise RoboticsError(Code.OBSERVATION_INVALID)
                if field.modality == "POSE" and field.unit == "1":
                    quaternion_norm += value * value
            carry = data[end:]
        if carry or (
            field.modality == "POSE"
            and field.unit == "1"
            and not math.isclose(quaternion_norm, 1.0, abs_tol=1e-6)
        ):
            raise RoboticsError(Code.OBSERVATION_INVALID)
