"""One lease's bounded local authority channel, without keys in the adapter.

The socket path is mounted only into its admitted container. The supervisor arms
one exact request with its own current pins; caller-supplied pins never become
authority. A matching SDK callback invokes the durable authority collaborator
once. A socket acknowledgement proves neither physics completion nor durable
completion. The supervisor retains the reservation even if the connection dies.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import stat
import struct
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from accretion.contracts.canonical import canonical_json
from accretion.contracts.robotics.values import Digest
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.protocol import (
    MAX_FRAME_BYTES,
    ProtocolRequest,
    WireModel,
    bounded_json,
    parse_message,
)
from accretion.robotics.sdk import ExecutionPins

RPC_LIMIT = MAX_FRAME_BYTES + 64 * 1024


class AuthorityCall(WireModel):
    version: Literal["1.0.0"] = "1.0.0"
    request: ProtocolRequest
    pins: ExecutionPins


class AuthorityReply(WireModel):
    version: Literal["1.0.0"] = "1.0.0"
    request_digest: Digest
    allowed: bool = Field(strict=True)
    code: Code | None = None
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def _grant_shape(self) -> AuthorityReply:
        canonical_json(self)
        if self.allowed != (self.code is None and self.valid_until is not None):
            raise ValueError("allowed reply requires a deadline and no denial code")
        if not self.allowed and (self.valid_until is not None or self.code is None):
            raise ValueError("denied reply must contain only its denial code")
        return self


class AuthorityGrant(WireModel):
    """Trusted callback outcome after durable commit, never a worker claim.

    valid_until is the minimum of current lease, approval, preflight, policy,
    conformance and evaluator-key validity. Cleanup-only TERMINATE can receive
    a separate short cleanup deadline; it cannot authorize another operation.
    """

    reservation_id: str | None = Field(default=None, min_length=1, max_length=128)
    valid_until: datetime

    @model_validator(mode="after")
    def _aware(self) -> AuthorityGrant:
        canonical_json(self)
        return self


@dataclass
class PendingAuthority:
    """Private per-call supervisor state; never deserialize this from the worker."""

    request_bytes: bytes
    pins_bytes: bytes
    claimed: bool = False
    admitted: bool = False
    reservation_id: str | None = None
    failure: Code | None = None
    valid_until: datetime | None = None
    granted: asyncio.Event = field(default_factory=asyncio.Event)
    monotonic_valid_until: float | None = None
    invalidated: bool = False


Authorize = Callable[[ProtocolRequest, ExecutionPins], Awaitable[AuthorityGrant]]


class LeaseAuthorityChannel:
    def __init__(self, directory: Path, authorize: Authorize, *, timeout_seconds: float = 5):
        if not 0 < timeout_seconds <= 30:
            raise ValueError("authority deadline must be bounded")
        self.directory = directory
        self.path = directory / "authority.sock"
        self.authorize = authorize
        self.timeout_seconds = timeout_seconds
        self.pending: PendingAuthority | None = None
        # A terminal callback may return its already-committed reservation after
        # the active request was disarmed. Retain that exact object for recovery.
        self.last_pending: PendingAuthority | None = None
        self._server: asyncio.Server | None = None
        self._connections: set[asyncio.Task[None]] = set()
        self._closed = False

    async def start(self) -> None:
        if self._server is not None or self._closed:
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
        # A private parent-owned directory is created by the launch supervisor.
        info = self.directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or self.directory.resolve() != self.directory
            or self.path.exists()
            or self.path.is_symlink()
        ):
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
        self._server = await asyncio.start_unix_server(
            self._connected, path=str(self.path), limit=RPC_LIMIT + 1, backlog=2
        )
        self.path.chmod(0o600)

    def arm(self, request: ProtocolRequest, pins: ExecutionPins) -> PendingAuthority:
        if self._closed or self._server is None or self.pending is not None:
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        request_bytes = canonical_json(request)
        parsed = parse_message(request_bytes)
        if not isinstance(parsed, ProtocolRequest) or request.scope != pins.command_scope():
            raise RoboticsError(Code.LEASE_INVALID)
        pins_bytes = canonical_json(pins)
        ExecutionPins.model_validate_json(pins_bytes)
        self.pending = PendingAuthority(request_bytes, pins_bytes)
        return self.pending

    def disarm(self, pending: PendingAuthority) -> None:
        if self.pending is not pending:
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        pending.invalidated = True
        self.last_pending = pending
        self.pending = None

    def invalidate(self, code: Code = Code.ACKNOWLEDGEMENT_UNCERTAIN) -> None:
        """Synchronously burn admission before asynchronous fencing/cleanup.

        This never undoes a committed reservation or an already-delivered permit.
        Late callback metadata is retained, but it cannot authorize the worker.
        """
        self._closed = True
        if self.pending is not None:
            self.pending.invalidated = True
            self.pending.failure = self.pending.failure or code
            self.last_pending = self.pending

    def _connected(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._closed or len(self._connections) >= 2:
            writer.close()
            return
        task = asyncio.create_task(self._serve(reader, writer))
        self._connections.add(task)
        task.add_done_callback(self._connections.discard)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        digest: str | None = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                # The peer must be the same unprivileged UID configured for the
                # worker. The separate mount and pending-byte comparison narrow
                # this local channel further; UID alone is not admission.
                connection = writer.get_extra_info("socket")
                if connection is None:
                    raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
                peer = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                _, uid, _ = struct.unpack("3i", peer)
                if uid != os.geteuid():
                    raise RoboticsError(Code.CAPABILITY_DENIED)
                raw = await reader.readuntil(b"\n")
                if len(raw) > RPC_LIMIT or raw[-1:] != b"\n":
                    raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
                call = AuthorityCall.model_validate(bounded_json(raw[:-1], max_bytes=RPC_LIMIT))
                digest = call.request.request_digest
                pending = self.pending
                if (
                    self._closed
                    or pending is None
                    or pending.invalidated
                    or pending.claimed
                    or canonical_json(call.request) != pending.request_bytes
                    or canonical_json(call.pins) != pending.pins_bytes
                ):
                    raise RoboticsError(Code.LEASE_INVALID)
                # Claim before the first await: duplicate/concurrent callbacks
                # cannot race the storage transaction or invoke it twice.
                pending.claimed = True
                try:
                    grant = await self.authorize(
                        ProtocolRequest.model_validate_json(pending.request_bytes),
                        ExecutionPins.model_validate_json(pending.pins_bytes),
                    )
                except RoboticsError as exc:
                    pending.failure = exc.code
                    raise
                except BaseException:
                    pending.failure = Code.ACKNOWLEDGEMENT_UNCERTAIN
                    raise
                if not isinstance(grant, AuthorityGrant):
                    pending.failure = Code.ISOLATION_UNAVAILABLE
                    raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
                grant = AuthorityGrant.model_validate_json(canonical_json(grant))
                pending.reservation_id = grant.reservation_id
                pending.valid_until = grant.valid_until
                now = datetime.now(UTC)
                pending.monotonic_valid_until = (
                    asyncio.get_running_loop().time() + (grant.valid_until - now).total_seconds()
                )
                pending.granted.set()
                if self._closed or pending.invalidated or self.pending is not pending:
                    pending.failure = pending.failure or Code.ACKNOWLEDGEMENT_UNCERTAIN
                    raise RoboticsError(pending.failure)
                if not now < grant.valid_until <= now + timedelta(hours=24):
                    pending.failure = Code.ACKNOWLEDGEMENT_UNCERTAIN
                    raise RoboticsError(Code.ACKNOWLEDGEMENT_UNCERTAIN)
                pending.admitted = True
                writer.write(
                    canonical_json(
                        AuthorityReply(
                            request_digest=digest, allowed=True, valid_until=grant.valid_until
                        )
                    )
                    + b"\n"
                )
                await writer.drain()
        except (Exception, asyncio.CancelledError) as exc:
            if digest is not None:
                code = (
                    exc.code if isinstance(exc, RoboticsError) else Code.ACKNOWLEDGEMENT_UNCERTAIN
                )
                with contextlib.suppress(Exception):
                    async with asyncio.timeout(1):
                        writer.write(
                            canonical_json(
                                AuthorityReply(request_digest=digest, allowed=False, code=code)
                            )
                            + b"\n"
                        )
                        await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                async with asyncio.timeout(1):
                    await writer.wait_closed()

    async def close(self) -> None:
        self.invalidate()
        if self._server is not None:
            self._server.close()
        connections = tuple(self._connections)
        for task in connections:
            task.cancel()
        await asyncio.gather(*connections, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()
        # Preserve the private directory and pending evidence for the host's
        # cleanup record. Only this channel's socket is removed.
        if self.path.is_socket():
            self.path.unlink()


class UnixAdmissionGuard:
    """Synchronous SDK guard inside the worker; no storage or private keys."""

    def __init__(self, path: Path, *, timeout_seconds: float = 5):
        if not path.is_absolute() or not 0 < timeout_seconds <= 30:
            raise ValueError("invalid authority channel configuration")
        self.path, self.timeout_seconds = path, timeout_seconds
        self._valid_until: datetime | None = None

    def check_current(self) -> None:
        """Worker controllers call before/after each solver step and publication."""
        if self._valid_until is None or datetime.now(UTC) >= self._valid_until:
            raise RoboticsError(Code.LEASE_INVALID)

    def authorize(self, request: ProtocolRequest, *, pins: ExecutionPins) -> None:
        self._valid_until = None
        raw = canonical_json(AuthorityCall(request=request, pins=pins)) + b"\n"
        if len(raw) > RPC_LIMIT:
            raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
                deadline = time.monotonic() + self.timeout_seconds
                channel.settimeout(self.timeout_seconds)
                channel.connect(str(self.path))
                channel.sendall(raw)
                channel.shutdown(socket.SHUT_WR)
                response = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    channel.settimeout(remaining)
                    chunk = channel.recv(1024)
                    if not chunk:
                        break
                    response.extend(chunk)
                    if len(response) > 2048:
                        raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
                if not response.endswith(b"\n") or response.count(b"\n") != 1:
                    raise RoboticsError(Code.INVALID_REQUEST)
                reply = AuthorityReply.model_validate(
                    bounded_json(bytes(response[:-1]), max_bytes=2048)
                )
                if reply.request_digest != request.request_digest:
                    raise RoboticsError(Code.LEASE_INVALID)
                if not reply.allowed or reply.code is not None:
                    raise RoboticsError(reply.code or Code.CAPABILITY_DENIED)
                if reply.valid_until is None or reply.valid_until > datetime.now(UTC) + timedelta(
                    hours=24
                ):
                    raise RoboticsError(Code.LEASE_INVALID)
                self._valid_until = reply.valid_until
                # Check after the complete frame and EOF: a permit delayed in
                # the socket cannot outlive its current authority inputs.
                self.check_current()
        except RoboticsError:
            raise
        except (OSError, ValueError) as exc:
            raise RoboticsError(Code.ACKNOWLEDGEMENT_UNCERTAIN) from exc
