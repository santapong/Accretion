"""Scoped, bounded per-lease artifact RPC; never mount a writable global store.

The trusted supervisor supplies the initial exact reference inventory and a live
access guard. A worker can read only that inventory or its own verified uploads.
Writes are SIMULATION/RUN only. Interrupted writes keep their reserved byte
charge; reconnecting does not imply a refund or an operational retry permit.
This per-channel charge is not the future durable episode accounting ledger.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import math
import os
import socket
import stat
import struct
import time
from collections.abc import Awaitable, Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, Literal

from accretion.contracts import EvidenceClass
from accretion.contracts.canonical import canonical_json
from accretion.contracts.robotics.values import ContentAddressedArtifactRef, SimulationArtifactRef
from accretion.robotics.artifacts import ArtifactStore
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.protocol import WireModel, bounded_json

HEADER_BYTES = 4096
CHUNK_BYTES = 64 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


class ArtifactCall(WireModel):
    version: Literal["1.0.0"] = "1.0.0"
    op: Literal["READ", "WRITE"]
    reference: SimulationArtifactRef


class ArtifactReply(WireModel):
    version: Literal["1.0.0"] = "1.0.0"
    status: Literal["READY", "DONE", "ERROR"]
    reference: SimulationArtifactRef | None = None
    code: Code | None = None


AccessGuard = Callable[[Literal["READ", "WRITE"], SimulationArtifactRef], Awaitable[None]]


def _copy(reference: ContentAddressedArtifactRef) -> SimulationArtifactRef:
    try:
        return SimulationArtifactRef.model_validate_json(canonical_json(reference))
    except ValueError as exc:
        raise RoboticsError(Code.ARTIFACT_INVALID) from exc


class LeaseArtifactChannel:
    def __init__(
        self,
        directory: Path,
        store: ArtifactStore,
        *,
        allowed_reads: Sequence[SimulationArtifactRef],
        access: AccessGuard,
        max_artifact_bytes: int = MAX_ARTIFACT_BYTES,
        max_output_bytes: int = 1024 * 1024 * 1024,
        max_calls: int = 100_000,
        timeout_seconds: float = 15,
    ):
        if (
            type(max_artifact_bytes) is not int
            or not 0 < max_artifact_bytes <= MAX_ARTIFACT_BYTES
            or type(max_output_bytes) is not int
            or not 0 < max_output_bytes <= 16 * 1024**3
            or type(max_calls) is not int
            or not 0 < max_calls <= 100_000
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 30
        ):
            raise ValueError("artifact RPC bounds must be finite and limited")
        self.directory, self.path = directory, directory / "artifacts.sock"
        self.store, self.access = store, access
        self.max_artifact_bytes, self.max_output_bytes = max_artifact_bytes, max_output_bytes
        self.max_calls, self.timeout_seconds = max_calls, timeout_seconds
        self.charged_bytes = 0
        self._calls = 0
        self._inventory: dict[str, bytes] = {}
        for reference in allowed_reads:
            copied = _copy(reference)
            raw = canonical_json(copied)
            if copied.digest in self._inventory and self._inventory[copied.digest] != raw:
                raise RoboticsError(Code.ARTIFACT_INVALID)
            self._inventory[copied.digest] = raw
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._storage_tasks: set[asyncio.Task[Any]] = set()
        self._closed = False
        self._write_lock = asyncio.Lock()

    def inventory(self) -> tuple[SimulationArtifactRef, ...]:
        return tuple(
            SimulationArtifactRef.model_validate_json(raw) for raw in self._inventory.values()
        )

    def _require_open(self) -> None:
        if self._closed:
            raise RoboticsError(Code.ARTIFACT_UNAVAILABLE)

    async def start(self) -> None:
        if self._closed or self._server is not None:
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        info = self.directory.lstat()
        if (
            self.directory.resolve() != self.directory
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or self.path.exists()
            or self.path.is_symlink()
        ):
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
        self._server = await asyncio.start_unix_server(
            self._connected, path=str(self.path), limit=HEADER_BYTES + 1, backlog=2
        )
        self.path.chmod(0o600)

    def _connected(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._closed or len(self._tasks) >= 2 or self._calls >= self.max_calls:
            writer.close()
            return
        self._calls += 1
        task = asyncio.create_task(self._serve(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _storage[T](self, operation: Callable[[], T]) -> T:
        # Cancelling a socket handler cannot stop filesystem work already in its
        # thread. Keep it alive/owned and drain it before the store may be closed.
        task = asyncio.create_task(asyncio.to_thread(operation))
        self._storage_tasks.add(task)

        def finished(done: asyncio.Task[T]) -> None:
            self._storage_tasks.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                connection = writer.get_extra_info("socket")
                if connection is None:
                    raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
                _, uid, _ = struct.unpack(
                    "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                )
                if uid != os.geteuid():
                    raise RoboticsError(Code.CAPABILITY_DENIED)
                header = await reader.readuntil(b"\n")
                call = ArtifactCall.model_validate(
                    bounded_json(header[:-1], max_bytes=HEADER_BYTES)
                )
                reference = call.reference
                if reference.size_bytes > min(
                    self.max_artifact_bytes, self.store.max_artifact_bytes
                ):
                    raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
                await self.access(call.op, _copy(reference))
                self._require_open()
                if call.op == "READ":
                    if self._inventory.get(reference.digest) != canonical_json(reference):
                        raise RoboticsError(Code.ARTIFACT_UNAVAILABLE)
                    if await reader.read(1):
                        raise RoboticsError(Code.INVALID_REQUEST)
                    # A maximum of two reads are buffered, each with the explicit
                    # ceiling, and integrity is complete before any bytes leave.
                    raw = await self._storage(
                        lambda: self.store.read_bytes(reference, max_bytes=self.max_artifact_bytes)
                    )
                    await self.access("READ", _copy(reference))
                    self._require_open()
                    writer.write(
                        canonical_json(ArtifactReply(status="READY", reference=reference)) + b"\n"
                    )
                    for start in range(0, len(raw), CHUNK_BYTES):
                        writer.write(raw[start : start + CHUNK_BYTES])
                        await writer.drain()
                else:
                    if reference.retention_class != "RUN":
                        raise RoboticsError(Code.CAPABILITY_DENIED)
                    async with self._write_lock:
                        existing = self._inventory.get(reference.digest)
                        if existing is not None and existing != canonical_json(reference):
                            raise RoboticsError(Code.ARTIFACT_INVALID)
                        if existing is None:
                            if self.charged_bytes + reference.size_bytes > self.max_output_bytes:
                                raise RoboticsError(Code.RESOURCE_CAP_EXHAUSTED)
                            # Charge before waiting for body/storage, without a
                            # refund after disconnect or publication uncertainty.
                            self.charged_bytes += reference.size_bytes
                        raw = await reader.readexactly(reference.size_bytes)
                        if (
                            await reader.read(1)
                            or hashlib.sha256(raw).hexdigest() != reference.digest
                        ):
                            raise RoboticsError(Code.ARTIFACT_INVALID)
                        await self.access("WRITE", _copy(reference))
                        self._require_open()
                        stored = await self._storage(
                            lambda: self.store.put(
                                raw,
                                media_type=reference.media_type,
                                retention_class="RUN",
                                evidence_class=EvidenceClass.SIMULATION,
                            )
                        )
                        if canonical_json(stored) != canonical_json(reference):
                            raise RoboticsError(Code.ARTIFACT_INVALID)
                        self._inventory[reference.digest] = canonical_json(reference)
                        await self.access("WRITE", _copy(reference))
                self._require_open()
                writer.write(
                    canonical_json(ArtifactReply(status="DONE", reference=reference)) + b"\n"
                )
                await writer.drain()
        except (Exception, asyncio.CancelledError) as exc:
            code = exc.code if isinstance(exc, RoboticsError) else Code.ARTIFACT_UNAVAILABLE
            with contextlib.suppress(Exception):
                async with asyncio.timeout(1):
                    writer.write(canonical_json(ArtifactReply(status="ERROR", code=code)) + b"\n")
                    await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                async with asyncio.timeout(1):
                    await writer.wait_closed()

    async def close(self) -> None:
        self._closed = True
        if self._server is not None:
            self._server.close()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Python's Server.wait_closed also waits for accepted connections. Cancel
        # handlers first, otherwise an active upload may succeed during shutdown.
        if self._server is not None:
            await self._server.wait_closed()
        if self._storage_tasks:
            try:
                async with asyncio.timeout(30):
                    await asyncio.shield(
                        asyncio.gather(*tuple(self._storage_tasks), return_exceptions=True)
                    )
            except TimeoutError as exc:
                # Caller must preserve the store and quarantine the host; this
                # exception is not confirmation that all file activity stopped.
                raise RoboticsError(Code.ARTIFACT_UNAVAILABLE) from exc
        if self.path.is_socket():
            self.path.unlink()


class UnixArtifacts:
    """Worker SDK reader/writer. Every operation has one absolute deadline."""

    def __init__(self, path: Path, *, timeout_seconds: float = 15):
        if (
            not path.is_absolute()
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 30
        ):
            raise ValueError("invalid artifact channel configuration")
        self.path, self.timeout_seconds = path, timeout_seconds

    @contextlib.contextmanager
    def _connection(self, call: ArtifactCall) -> Iterator[tuple[socket.socket, float]]:
        raw = canonical_json(call) + b"\n"
        if len(raw) > HEADER_BYTES:
            raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
                deadline = time.monotonic() + self.timeout_seconds
                channel.settimeout(self.timeout_seconds)
                channel.connect(str(self.path))
                channel.sendall(raw)
                yield channel, deadline
        except RoboticsError:
            raise
        except (OSError, ValueError) as exc:
            raise RoboticsError(Code.ARTIFACT_UNAVAILABLE) from exc

    @staticmethod
    def _remaining(channel: socket.socket, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RoboticsError(Code.ARTIFACT_UNAVAILABLE)
        channel.settimeout(remaining)

    def _reply(self, channel: socket.socket, deadline: float) -> ArtifactReply:
        raw = bytearray()
        while not raw.endswith(b"\n"):
            self._remaining(channel, deadline)
            char = channel.recv(1)
            if not char or len(raw) >= HEADER_BYTES:
                raise RoboticsError(Code.ARTIFACT_INVALID)
            raw.extend(char)
        reply = ArtifactReply.model_validate(bounded_json(bytes(raw[:-1]), max_bytes=HEADER_BYTES))
        if reply.status == "ERROR" or reply.code is not None:
            raise RoboticsError(reply.code or Code.ARTIFACT_UNAVAILABLE)
        return reply

    def put(
        self,
        data: bytes,
        *,
        media_type: str,
        retention_class: Literal["RUN", "PROJECT", "RESEARCH_ARCHIVE"],
        evidence_class: EvidenceClass,
    ) -> ContentAddressedArtifactRef:
        if type(data) is not bytes or len(data) > MAX_ARTIFACT_BYTES:
            raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
        if retention_class != "RUN" or evidence_class is not EvidenceClass.SIMULATION:
            raise RoboticsError(Code.CAPABILITY_DENIED)
        digest = hashlib.sha256(data).hexdigest()
        reference = SimulationArtifactRef(
            uri="artifact://sha256/" + digest,
            digest=digest,
            size_bytes=len(data),
            media_type=media_type,
            retention_class="RUN",
        )
        with self._connection(ArtifactCall(op="WRITE", reference=reference)) as (channel, deadline):
            for start in range(0, len(data), CHUNK_BYTES):
                self._remaining(channel, deadline)
                channel.sendall(data[start : start + CHUNK_BYTES])
            channel.shutdown(socket.SHUT_WR)
            reply = self._reply(channel, deadline)
            self._remaining(channel, deadline)
            if reply.status != "DONE" or reply.reference != reference or channel.recv(1):
                raise RoboticsError(Code.ARTIFACT_INVALID)
        return reference

    def iter_bytes(
        self, ref: ContentAddressedArtifactRef, *, max_bytes: int, chunk_size: int
    ) -> Iterator[bytes]:
        reference = _copy(ref)
        if (
            type(max_bytes) is not int
            or max_bytes <= 0
            or type(chunk_size) is not int
            or not 0 < chunk_size <= CHUNK_BYTES
            or reference.size_bytes > min(max_bytes, MAX_ARTIFACT_BYTES)
        ):
            raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
        with self._connection(ArtifactCall(op="READ", reference=reference)) as (channel, deadline):
            channel.shutdown(socket.SHUT_WR)
            initial = self._reply(channel, deadline)
            if initial.status != "READY" or initial.reference != reference:
                raise RoboticsError(Code.ARTIFACT_INVALID)
            received = 0
            digest = hashlib.sha256()
            while received < reference.size_bytes:
                self._remaining(channel, deadline)
                chunk = channel.recv(min(chunk_size, reference.size_bytes - received))
                if not chunk:
                    raise RoboticsError(Code.ARTIFACT_INVALID)
                received += len(chunk)
                digest.update(chunk)
                yield chunk
            final = self._reply(channel, deadline)
            self._remaining(channel, deadline)
            if (
                final.status != "DONE"
                or final.reference != reference
                or channel.recv(1)
                or digest.hexdigest() != reference.digest
            ):
                raise RoboticsError(Code.ARTIFACT_INVALID)
