"""Test-only adapter. Its counters and artifacts are synthetic protocol witnesses.

This is neither an AgentRuntime FAKE provider nor a second robot embodiment.
It has no physics, gripper, renderer, process isolation or endpoint capability.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from enum import StrEnum

from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics import ActionIntent, PreparedCommand, SafetyDecisionReceipt
from accretion.contracts.robotics.values import ContentAddressedArtifactRef, SimulationArtifactRef
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.observations import ObservationBatch
from accretion.robotics.protocol import AdapterDescription, SnapshotReference


class Fault(StrEnum):
    NONE = "NONE"
    PREPARE_ADVANCES = "PREPARE_ADVANCES"
    OBSERVE_ADVANCES = "OBSERVE_ADVANCES"
    APPLY_THEN_LOST_ACK = "APPLY_THEN_LOST_ACK"
    CRASH_BEFORE_APPLY = "CRASH_BEFORE_APPLY"
    CLOCK_REGRESSION = "CLOCK_REGRESSION"
    MISSING_OBSERVATION_BYTES = "MISSING_OBSERVATION_BYTES"


class MemoryArtifacts:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.read_calls = 0

    def put(self, data: bytes, *, media_type: str) -> SimulationArtifactRef:
        digest = hashlib.sha256(data).hexdigest()
        self.blobs[digest] = data
        return SimulationArtifactRef(
            uri=f"artifact://sha256/{digest}",
            digest=digest,
            size_bytes=len(data),
            media_type=media_type,
            retention_class="RUN",
        )

    def iter_bytes(
        self, ref: ContentAddressedArtifactRef, *, max_bytes: int, chunk_size: int
    ) -> Iterator[bytes]:
        self.read_calls += 1
        if ref.size_bytes > max_bytes:
            raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
        data = self.blobs[ref.digest]
        if len(data) > max_bytes:
            raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
        for offset in range(0, len(data), chunk_size):
            yield data[offset : offset + chunk_size]


class ScriptedFaultAdapter:
    def __init__(
        self,
        description: AdapterDescription,
        initial: ObservationBatch,
        prepared_factory: Callable[[ActionIntent], PreparedCommand],
        artifacts: MemoryArtifacts,
        *,
        fault: Fault = Fault.NONE,
    ) -> None:
        self._description = canonical_json(description)
        self._initial = canonical_json(initial)
        self._batch = self._initial
        self._prepared_factory = prepared_factory
        self.artifacts = artifacts
        self.fault = fault
        self.advance_count = 0
        self.execute_calls = 0
        self.reset_calls = 0
        self.restore_calls = 0
        self.terminated = False

    def describe(self) -> AdapterDescription:
        return AdapterDescription.model_validate_json(self._description)

    def observe(self) -> ObservationBatch:
        if self.fault is Fault.OBSERVE_ADVANCES:
            self._advance()
        return ObservationBatch.model_validate_json(self._batch)

    def reset(self, *, seed: int, randomization_sample_hash: str) -> ObservationBatch:
        self.reset_calls += 1
        self._batch = self._initial
        return self.observe()

    def _advance(self) -> None:
        values = ObservationBatch.model_validate_json(self._batch).model_dump(mode="python")
        values["sequence"] += 1
        values["sim_time_ns"] += 1_000_000
        for sample in values["samples"]:
            sample["sequence"] = values["sequence"]
            sample["sim_time_ns"] = values["sim_time_ns"]
        self._batch = canonical_json(ObservationBatch.model_validate(values))
        self.advance_count += 1

    def prepare(self, intent: ActionIntent) -> PreparedCommand:
        if self.fault is Fault.PREPARE_ADVANCES:
            self._advance()
        prepared = self._prepared_factory(intent)
        self.artifacts.blobs[prepared.command_ref.digest] = canonical_json(prepared.command_payload)
        return prepared

    def execute(self, prepared: PreparedCommand, safety: SafetyDecisionReceipt) -> ObservationBatch:
        self.execute_calls += 1
        if self.fault is Fault.CRASH_BEFORE_APPLY:
            raise RuntimeError("synthetic pre-apply crash")
        self._advance()
        if self.fault is Fault.APPLY_THEN_LOST_ACK:
            raise TimeoutError("synthetic lost acknowledgement")
        if self.fault is Fault.MISSING_OBSERVATION_BYTES:
            for sample in self.observe().samples:
                self.artifacts.blobs.pop(sample.artifact.digest, None)
        if self.fault is Fault.CLOCK_REGRESSION:
            self._batch = self._initial
        return self.observe()

    def snapshot(self) -> SnapshotReference:
        batch = self.observe()
        return SnapshotReference(
            source_episode_id=batch.episode_id,
            state=batch.state_binding(),
            dependency_closure_hash=content_hash(self.describe().dependencies, exclude=()),
            artifact=self.artifacts.put(self._batch, media_type="application/json"),
        )

    def restore(self, snapshot: SnapshotReference) -> ObservationBatch:
        self.restore_calls += 1
        original = ObservationBatch.model_validate_json(
            self.artifacts.blobs[snapshot.artifact.digest]
        )
        # The new host owns the target episode and lease; a snapshot cannot replace them.
        values = original.model_dump(mode="python")
        values["episode_id"] = self.observe().episode_id
        values["lease"] = self.observe().lease
        self._batch = canonical_json(ObservationBatch.model_validate(values))
        return self.observe()

    def terminate(self) -> None:
        self.terminated = True
