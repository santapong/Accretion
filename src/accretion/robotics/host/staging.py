"""Staged worker startup composed with the existing terminal transport fence.

This internal subclass deliberately shares AdapterTransport's failure future,
watchdog, single-flight flag and abort path; it does not install a second process
supervisor. Changes to those protected seams require the staging regression suite.

Archive, activation, protocol-completion and startup-completion collaborators must
serialize with ``failed`` on the same durable episode fence. Cancellation is never
proof that their writes rolled back. A cancelled raw-validation thread may finish
its bounded read later; its result cannot activate this transport. The reader must
apply its own I/O deadlines. No callback may mint human approval here.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, TypeVar

from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics import EmbodimentDescriptor, ObservationSpec
from accretion.contracts.robotics.values import SimulationArtifactRef
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.observations import ArtifactReader, ObservationValidator
from accretion.robotics.protocol import (
    MAX_FRAME_BYTES,
    DescribeRequest,
    DescribeResult,
    HelloRequest,
    HelloResult,
    ProtocolRequest,
    ProtocolResponse,
    SuccessOutcome,
    bounded_json,
)
from accretion.robotics.sdk import ExecutionPins

from .authority_channel import LeaseAuthorityChannel
from .transport import AdapterTransport, Cleanup, Complete, TransportEvidence
from .worker import (
    WorkerActivation,
    WorkerBootstrap,
    WorkerReady,
    activation_deadline,
    validate_activation,
)


class StagePhase(StrEnum):
    WAITING_READY = "WAITING_READY"
    VALIDATING_READY = "VALIDATING_READY"
    ARCHIVING_READY = "ARCHIVING_READY"
    ACTIVATING = "ACTIVATING"
    ARCHIVING_ACTIVATION = "ARCHIVING_ACTIVATION"
    SENDING_ACTIVATION = "SENDING_ACTIVATION"
    ACTIVATION_SENT = "ACTIVATION_SENT"
    HELLO_CONFIRMED = "HELLO_CONFIRMED"
    DESCRIPTION_CONFIRMED = "DESCRIPTION_CONFIRMED"
    PUBLISHING = "PUBLISHING"
    ACTIVATED = "ACTIVATED"


@dataclass(frozen=True)
class StagingEvidence:
    """Immutable observed bytes, including failed attempts; not an execution receipt.

    Bytes exclude the JSONL delimiter. Oversized/incomplete input retains at most
    MAX_FRAME_BYTES + 1 bytes and marks truncation rather than claiming a full frame.
    A started callback with no result stays attributable by its phase, even when
    the collaborator committed then raised/cancelled before returning the result.
    """

    bootstrap_bytes: bytes
    phase: StagePhase = StagePhase.WAITING_READY
    ready_bytes: bytes | None = None
    ready_truncated: bool = False
    ready_ref_json: bytes | None = None
    activation_bytes: bytes | None = None
    activation_truncated: bool = False
    activation_ref_json: bytes | None = None
    activation_write_attempted: bool = False
    activation_drained: bool = False
    authority_deadline: datetime | None = None
    hello: TransportEvidence | None = None
    describe: TransportEvidence | None = None
    failure: Code | None = None


Archive = Callable[[Literal["READY", "ACTIVATION"], bytes], Awaitable[SimulationArtifactRef]]
Activate = Callable[[WorkerReady, SimulationArtifactRef], Awaitable[WorkerActivation]]
ActivationAuthority = Callable[[WorkerReady, WorkerActivation], Awaitable[datetime]]
Activated = Callable[[StagingEvidence, ExecutionPins], Awaitable[None]]
StagingFailed = Callable[[StagingEvidence, TransportEvidence | None, Code], Awaitable[None]]
T = TypeVar("T")


class StagedAdapterTransport(AdapterTransport):
    """Own startup once; expose protocol only after HELLO and durable publication.

    ``initialization_seconds`` is an explicit startup liveness policy, independent
    of the operational heartbeat interval. It can only shorten the immutable wall
    and lease caps. No heartbeat or authority reservation is generated while a
    genuine human approval is pending. Successful startup consumes wire sequence 0
    with HELLO and sequence 1 with a scoped authority-checked DESCRIBE; the caller's
    first protocol request must use sequence 2 or greater. HELLO is version-only
    and grants no authority; exact DESCRIBE checks the admitted descriptor/closure.
    """

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        authority: LeaseAuthorityChannel,
        *,
        bootstrap: WorkerBootstrap,
        artifacts: ArtifactReader,
        archive: Archive,
        activate: Activate,
        activation_authority: ActivationAuthority,
        activated: Activated,
        complete: Complete,
        failed: StagingFailed,
        cleanup: Cleanup,
        initialization_seconds: float,
        wall_seconds: float,
        request_seconds: float = 10,
        heartbeat_seconds: float = 15,
        stderr_bytes: int = 1024 * 1024,
    ):
        if (
            isinstance(initialization_seconds, bool)
            or not math.isfinite(initialization_seconds)
            or not 0 < initialization_seconds <= 86400
        ):
            raise ValueError("initialization deadline must be explicit, finite and bounded")
        if not all(
            callable(callback)
            for callback in (archive, activate, activation_authority, activated, complete, failed)
        ):
            raise ValueError("all durable staging collaborators are mandatory")
        frozen = canonical_json(bootstrap)
        parsed = WorkerBootstrap.model_validate(bounded_json(frozen))
        self._bootstrap_bytes = frozen
        self._stage_evidence = StagingEvidence(bootstrap_bytes=frozen)
        self._artifacts, self._archive = artifacts, archive
        self._activate, self._activated_callback = activate, activated
        self._activation_authority = activation_authority
        self._started = self._activated = False
        self._startup_operation: Literal["hello", "describe"] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._authority_expiry: asyncio.TimerHandle | None = None

        async def complete_protocol(evidence: TransportEvidence) -> None:
            if self._startup_operation is not None:
                self._stage_evidence = (
                    replace(self._stage_evidence, hello=evidence)
                    if self._startup_operation == "hello"
                    else replace(self._stage_evidence, describe=evidence)
                )
            await complete(evidence)

        async def failed_protocol(evidence: TransportEvidence | None, code: Code) -> None:
            if self._startup_operation is not None and evidence is not None:
                self._stage_evidence = (
                    replace(self._stage_evidence, hello=evidence)
                    if self._startup_operation == "hello"
                    else replace(self._stage_evidence, describe=evidence)
                )
            self._stage_evidence = replace(self._stage_evidence, failure=code)
            await failed(self._stage_evidence, evidence, code)

        super().__init__(
            process,
            authority,
            complete=complete_protocol,
            failed=failed_protocol,
            cleanup=cleanup,
            wall_seconds=min(wall_seconds, parsed.wall_seconds),
            request_seconds=request_seconds,
            heartbeat_seconds=heartbeat_seconds,
            stderr_bytes=stderr_bytes,
        )
        remaining = (parsed.authority_expires_at - datetime.now(UTC)).total_seconds()
        self._stage_deadline = min(
            self._wall_deadline,
            self._loop.time() + initialization_seconds,
            self._loop.time() + max(0, remaining),
        )
        # Set once before the inherited watchdog can run. Approval polling never
        # renews it, and the immutable CPU/wall watchdog is not paused.
        self._heartbeat_deadline = self._stage_deadline

    @property
    def staging_evidence(self) -> StagingEvidence:
        return self._stage_evidence

    @property
    def activated(self) -> bool:
        return self._activated and not self.closed and not self._failure.done()

    @staticmethod
    def _consume_late_result(task: asyncio.Future[Any]) -> None:
        # A collaborator may ignore cancellation. Never wait for it before child
        # cleanup, and retrieve its eventual exception without claiming rollback.
        if not task.cancelled():
            task.exception()

    async def _step(self, operation: Awaitable[T]) -> T:
        task = asyncio.ensure_future(operation)
        try:
            self._current()
            async with asyncio.timeout_at(self._stage_deadline):
                done, _ = await asyncio.wait(
                    (task, self._failure), return_when=asyncio.FIRST_COMPLETED
                )
                if self._failure in done:
                    raise RoboticsError(self._failure.result())
                self._current()
                return task.result()
        finally:
            if not task.done():
                task.cancel()
                task.add_done_callback(self._consume_late_result)
            elif not task.cancelled():
                task.exception()

    def _current(self) -> None:
        if self._failure.done():
            raise RoboticsError(self._failure.result())
        if self._closed or self.process.returncode is not None:
            raise RoboticsError(Code.ADAPTER_CRASH)
        if self._loop.time() >= self._stage_deadline:
            raise RoboticsError(Code.LEASE_INVALID)

    async def _ready(self) -> bytes:
        assert self.process.stdout is not None
        captured = bytearray()
        while len(captured) <= MAX_FRAME_BYTES:
            chunk = await self.process.stdout.read(min(65536, MAX_FRAME_BYTES + 1 - len(captured)))
            captured.extend(chunk)
            # Retain each bounded partial read before the next await, including
            # cancellation/EOF. Do not depend on the stream's configured limit.
            self._stage_evidence = replace(
                self._stage_evidence,
                ready_bytes=bytes(captured),
                ready_truncated=True,
            )
            if not chunk:
                raise RoboticsError(Code.ADAPTER_CRASH)
            delimiter = captured.find(b"\n")
            if delimiter >= 0:
                if delimiter != len(captured) - 1:
                    raise RoboticsError(Code.INVALID_REQUEST)
                raw = bytes(captured[:-1])
                self._stage_evidence = replace(
                    self._stage_evidence,
                    ready_bytes=raw,
                    ready_truncated=False,
                )
                return raw
        raise RoboticsError(Code.PAYLOAD_TOO_LARGE)

    def _validate_raw(self, raw: bytes) -> None:
        bootstrap = WorkerBootstrap.model_validate_json(self._bootstrap_bytes)
        ready = WorkerReady.model_validate_json(raw)
        ObservationValidator().validate(
            bootstrap.description.observation_spec.for_execution(ObservationSpec),
            bootstrap.description.descriptor.for_execution(EmbodimentDescriptor),
            ready.initial_observation,
            self._artifacts,
            episode_id=bootstrap.episode.episode_id,
            lease=bootstrap.episode.lease,
            previous=None,
        )

    async def _archived(
        self, kind: Literal["READY", "ACTIVATION"], raw: bytes
    ) -> SimulationArtifactRef:
        ref = await self._archive(kind, raw)
        parsed = SimulationArtifactRef.model_validate(bounded_json(canonical_json(ref)))
        if (
            parsed.digest != content_hash(bounded_json(raw), exclude=())
            or parsed.size_bytes != len(raw)
            or parsed.media_type != "application/json"
        ):
            raise RoboticsError(Code.ARTIFACT_INVALID)
        return parsed

    async def _activation(self, ready: WorkerReady, ref: SimulationArtifactRef) -> bytes:
        value = await self._activate(
            WorkerReady.model_validate_json(canonical_json(ready)),
            SimulationArtifactRef.model_validate_json(canonical_json(ref)),
        )
        raw = canonical_json(value)
        self._stage_evidence = replace(
            self._stage_evidence,
            activation_bytes=raw[: MAX_FRAME_BYTES + 1],
            activation_truncated=len(raw) > MAX_FRAME_BYTES,
        )
        if len(raw) > MAX_FRAME_BYTES:
            raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
        return raw

    async def start(self) -> ExecutionPins:
        if self._started or self._busy:
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        self._started = self._busy = True
        try:
            self._current()
            bootstrap = WorkerBootstrap.model_validate_json(self._bootstrap_bytes)
            raw = await self._step(self._ready())
            ready = WorkerReady.model_validate(bounded_json(raw))
            if (
                canonical_json(ready) != raw
                or ready.bootstrap_hash != content_hash(bootstrap, exclude=())
                or ready.description != bootstrap.description
                or ready.observed_at > datetime.now(UTC)
            ):
                raise RoboticsError(Code.CONFORMANCE_STALE)
            bootstrap.validate_initial(ready.initial_observation)
            self._stage_evidence = replace(self._stage_evidence, phase=StagePhase.VALIDATING_READY)
            await self._step(asyncio.to_thread(self._validate_raw, raw))
            self._stage_evidence = replace(self._stage_evidence, phase=StagePhase.ARCHIVING_READY)
            ref = await self._step(self._archived("READY", raw))
            self._stage_evidence = replace(
                self._stage_evidence,
                ready_ref_json=canonical_json(ref),
                phase=StagePhase.ACTIVATING,
            )
            activation_raw = await self._step(self._activation(ready, ref))
            activation = WorkerActivation.model_validate(bounded_json(activation_raw))
            pins = validate_activation(bootstrap, ready, activation)
            remaining = (
                activation_deadline(bootstrap, activation) - datetime.now(UTC)
            ).total_seconds()
            self._stage_deadline = min(self._stage_deadline, self._loop.time() + max(0, remaining))
            self._heartbeat_deadline = min(self._heartbeat_deadline, self._stage_deadline)
            self._stage_evidence = replace(
                self._stage_evidence, phase=StagePhase.ARCHIVING_ACTIVATION
            )
            activation_ref = await self._step(self._archived("ACTIVATION", activation_raw))
            self._stage_evidence = replace(
                self._stage_evidence,
                activation_ref_json=canonical_json(activation_ref),
                phase=StagePhase.SENDING_ACTIVATION,
            )
            # Recheck current policy/principal/lease authority after artifact storage;
            # caller-produced receipt models cannot stand in for this live read.
            deadline = await self._step(
                self._activation_authority(
                    WorkerReady.model_validate_json(raw),
                    WorkerActivation.model_validate_json(activation_raw),
                )
            )
            if not isinstance(deadline, datetime):
                raise RoboticsError(Code.LEASE_INVALID)
            canonical_json(deadline)
            self._stage_evidence = replace(self._stage_evidence, authority_deadline=deadline)
            remaining = (
                min(deadline, activation_deadline(bootstrap, activation)) - datetime.now(UTC)
            ).total_seconds()
            # A newly shortened authority bound must wake an idle worker even
            # when the inherited heartbeat watchdog is sleeping to an older
            # deadline. Reuse its terminal trip/cleanup, not another supervisor.
            self._authority_expiry = self._loop.call_at(
                self._loop.time() + max(0, remaining), self._trip, Code.LEASE_INVALID
            )
            self._stage_deadline = min(self._stage_deadline, self._loop.time() + max(0, remaining))
            self._heartbeat_deadline = min(self._heartbeat_deadline, self._stage_deadline)
            # Both originals and current expiry are checked again after authority I/O.
            pins = validate_activation(bootstrap, ready, activation)
            self._current()
            assert self.process.stdin is not None
            self._stage_evidence = replace(self._stage_evidence, activation_write_attempted=True)
            self.process.stdin.write(activation_raw + b"\n")
            await self._step(self.process.stdin.drain())
            self._stage_evidence = replace(
                self._stage_evidence,
                activation_drained=True,
                phase=StagePhase.ACTIVATION_SENT,
            )
            self._current()
            self._startup_operation = "hello"
            self._busy = False  # inherited request owns the same single-flight flag
            hello = ProtocolRequest.create(
                request_id="worker-activation-hello",
                sequence=0,
                scope=None,
                payload=HelloRequest(),
            )
            response = await self._step(super().request(hello, pins))
            self._busy = True
            if not isinstance(response.outcome, SuccessOutcome) or not isinstance(
                response.outcome.payload, HelloResult
            ):
                raise RoboticsError(Code.CONFORMANCE_STALE)
            self._startup_operation = None
            self._stage_evidence = replace(self._stage_evidence, phase=StagePhase.HELLO_CONFIRMED)
            self._startup_operation = "describe"
            self._busy = False
            describe = ProtocolRequest.create(
                request_id="worker-activation-describe",
                sequence=1,
                scope=pins.command_scope(),
                payload=DescribeRequest(),
            )
            response = await self._step(super().request(describe, pins))
            self._busy = True
            if (
                not isinstance(response.outcome, SuccessOutcome)
                or not isinstance(response.outcome.payload, DescribeResult)
                or response.outcome.payload.description != bootstrap.description
            ):
                raise RoboticsError(Code.CONFORMANCE_STALE)
            self._startup_operation = None
            self._stage_evidence = replace(
                self._stage_evidence, phase=StagePhase.DESCRIPTION_CONFIRMED
            )
            pins = validate_activation(bootstrap, ready, activation)
            self._stage_evidence = replace(self._stage_evidence, phase=StagePhase.PUBLISHING)
            await self._step(self._activated_callback(self._stage_evidence, pins))
            self._current()
            pins = validate_activation(bootstrap, ready, activation)
            self._stage_evidence = replace(self._stage_evidence, phase=StagePhase.ACTIVATED)
            self._activated = True
            return pins
        except BaseException as exc:
            code = exc.code if isinstance(exc, RoboticsError) else Code.ACKNOWLEDGEMENT_UNCERTAIN
            self._trip(code)
            assert self._abort_task is not None
            with contextlib.suppress(Exception):
                await asyncio.shield(self._abort_task)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise RoboticsError(code) from exc
        finally:
            self._busy = False
            self._startup_operation = None

    async def request(self, request: ProtocolRequest, pins: ExecutionPins) -> ProtocolResponse:
        if not self.activated:
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        return await super().request(request, pins)

    async def close(self) -> None:
        if self._close_task is None:
            if self._authority_expiry is not None:
                self._authority_expiry.cancel()
            if not self._activated and not self._failure.done():
                self._trip(Code.ACKNOWLEDGEMENT_UNCERTAIN)
            self._close_task = asyncio.create_task(super().close())
        await asyncio.shield(self._close_task)
