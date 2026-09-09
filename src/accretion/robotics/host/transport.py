"""Bounded single-flight child protocol transport with no reconnect or retry.

This transport alone is not isolation. The production supervisor must construct
it only from an inspected admitted container and supply durable completion,
uncertainty and complete process-tree cleanup callbacks.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.protocol import (
    MAX_FRAME_BYTES,
    ErrorOutcome,
    HelloRequest,
    ProtocolRequest,
    ProtocolResponse,
    ResponseCorrelator,
    parse_message,
)
from accretion.robotics.sdk import ExecutionPins

from .authority_channel import LeaseAuthorityChannel, PendingAuthority


@dataclass(frozen=True)
class TransportEvidence:
    request_bytes: bytes
    response_bytes: bytes | None
    reservation_id: str | None
    failure: Code | None
    authority_valid_until: datetime | None = None
    # Also true for incomplete EOF, even when no size truncation was needed.
    response_truncated: bool = False


# Durable complete/failed collaborators must serialize against the same episode
# fence/current-state transaction. If failure wins, completion cannot publish a
# successful outcome over the terminal state. Callback cancellation is not proof
# that a database commit was rolled back. Cleanup intentionally runs concurrently
# with failure recording; storage latency must never keep the child executing.
Complete = Callable[[TransportEvidence], Awaitable[None]]
Failed = Callable[[TransportEvidence | None, Code], Awaitable[None]]
Cleanup = Callable[[], Awaitable[None]]


class AdapterTransport:
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        authority: LeaseAuthorityChannel,
        *,
        complete: Complete,
        failed: Failed,
        cleanup: Cleanup,
        wall_seconds: float,
        request_seconds: float = 10,
        heartbeat_seconds: float = 15,
        stderr_bytes: int = 1024 * 1024,
    ):
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise ValueError("all protocol pipes must be separate")
        if any(
            not math.isfinite(x) or not 0 < x <= 86400
            for x in (wall_seconds, request_seconds, heartbeat_seconds)
        ):
            raise ValueError("transport deadlines must be finite and bounded")
        if type(stderr_bytes) is not int or not 0 < stderr_bytes <= 16 * 1024 * 1024:
            raise ValueError("stderr ceiling must be bounded")
        self.process, self.authority = process, authority
        self.complete, self.failed, self.cleanup = complete, failed, cleanup
        self.request_seconds, self.heartbeat_seconds = request_seconds, heartbeat_seconds
        self.stderr_limit = stderr_bytes
        self._stderr = bytearray()
        self._loop = asyncio.get_running_loop()
        self._wall_deadline = self._loop.time() + wall_seconds
        self._heartbeat_deadline = self._loop.time() + heartbeat_seconds
        self._correlator = ResponseCorrelator()
        self._pending: PendingAuthority | None = None
        self._evidence: TransportEvidence | None = None
        self._terminal_evidence: TransportEvidence | None = None
        self._terminal_pending: PendingAuthority | None = None
        self._busy = False
        self._closed = False
        self._failure: asyncio.Future[Code] = self._loop.create_future()
        self._abort_task: asyncio.Task[None] | None = None
        self._stderr_task = asyncio.create_task(self._read_stderr())
        self._watchdog_task = asyncio.create_task(self._watchdog())

    @property
    def stderr(self) -> bytes:
        return bytes(self._stderr)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def failure_evidence(self) -> TransportEvidence | None:
        """Latest known failure bytes/late reservation for close or reconciliation.

        The failure collaborator must fence by request digest even when the
        reservation ID is not known yet. A late ID is evidence, never a permit.
        """
        evidence, pending = self._terminal_evidence, self._terminal_pending
        if evidence is not None and pending is not None:
            return replace(
                evidence,
                reservation_id=pending.reservation_id,
                authority_valid_until=pending.valid_until,
            )
        return evidence

    def _trip(self, code: Code) -> None:
        # No await may precede this poison. Cleanup/fence IO can be slow while
        # an earlier committed authority callback is still returning its grant.
        self.authority.invalidate(code)
        if not self._failure.done():
            self._failure.set_result(code)
        if self._abort_task is None:
            self._abort_task = asyncio.create_task(self._abort(code))

    async def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        try:
            while chunk := await self.process.stderr.read(65536):
                remaining = self.stderr_limit - len(self._stderr)
                self._stderr.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self._trip(Code.RESOURCE_CAP_EXHAUSTED)
                    return
        except (OSError, ValueError):
            self._trip(Code.ADAPTER_CRASH)

    async def _watchdog(self) -> None:
        # No unauthorised heartbeat is manufactured. A successful explicit
        # request is liveness, and the durable authority still controls leases.
        try:
            while not self._closed:
                now = self._loop.time()
                deadline = min(self._wall_deadline, self._heartbeat_deadline)
                await asyncio.sleep(max(0, deadline - now))
                now = self._loop.time()
                if now >= self._wall_deadline:
                    self._trip(Code.RESOURCE_CAP_EXHAUSTED)
                elif now >= self._heartbeat_deadline:
                    self._trip(Code.HEARTBEAT_LOST)
        except asyncio.CancelledError:
            return

    async def _abort(self, code: Code) -> None:
        self._closed = True
        evidence = self._evidence
        if evidence is not None:
            evidence = TransportEvidence(
                evidence.request_bytes,
                evidence.response_bytes,
                self._pending.reservation_id if self._pending else evidence.reservation_id,
                code,
                self._pending.valid_until if self._pending else evidence.authority_valid_until,
                evidence.response_truncated,
            )
        self._terminal_evidence, self._terminal_pending = evidence, self._pending
        # Even a database outage cannot leave the process executing. Conversely,
        # cleanup success alone cannot clear durable uncertainty/quarantine.
        results = await asyncio.gather(
            self.failed(evidence, code), self.cleanup(), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def request(self, request: ProtocolRequest, pins: ExecutionPins) -> ProtocolResponse:
        if self._closed or self._failure.done():
            raise RoboticsError(Code.ADAPTER_CRASH)
        if self._busy:
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        self._busy = True
        read_task: asyncio.Task[bytes] | None = None
        pending: PendingAuthority | None = None
        permit_watch: asyncio.Task[None] | None = None
        response_raw: bytes | None = None
        response_truncated = False
        try:
            raw = self._correlator.begin(request)
            if not isinstance(request.payload, HelloRequest):
                pending = self.authority.arm(request, pins)
                permit_watch = asyncio.create_task(self._permit_deadline(pending))
            self._pending = pending
            self._evidence = TransportEvidence(raw[:-1], None, None, None)
            assert self.process.stdin is not None and self.process.stdout is not None
            if self.process.returncode is not None:
                raise RoboticsError(Code.ADAPTER_CRASH)
            deadline = min(
                self._wall_deadline,
                self._heartbeat_deadline,
                self._loop.time() + self.request_seconds,
            )
            async with asyncio.timeout_at(deadline):
                self.process.stdin.write(raw)
                await self.process.stdin.drain()
                read_task = asyncio.create_task(self.process.stdout.readuntil(b"\n"))
                done, _ = await asyncio.wait(
                    (read_task, self._failure), return_when=asyncio.FIRST_COMPLETED
                )
                if self._failure in done:
                    raise RoboticsError(self._failure.result())
                response_raw = read_task.result()
                if len(response_raw) > MAX_FRAME_BYTES + 1:
                    raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
                parsed = parse_message(response_raw[:-1])
                if not isinstance(parsed, ProtocolResponse):
                    raise RoboticsError(Code.INVALID_REQUEST)
                # A fabricated worker response cannot bypass its SDK authority
                # callback, even for a read. A real denial remains attributable.
                if pending is not None and not pending.admitted:
                    if not (
                        pending.claimed
                        and pending.failure is not None
                        and isinstance(parsed.outcome, ErrorOutcome)
                        and parsed.outcome.code == pending.failure
                    ):
                        raise RoboticsError(Code.CAPABILITY_DENIED)
                self._check_deadlines(pending, deadline)
                response = self._correlator.accept(parsed)
                self._evidence = TransportEvidence(
                    raw[:-1],
                    response_raw[:-1],
                    pending.reservation_id if pending else None,
                    None,
                    pending.valid_until if pending else None,
                )
                await self.complete(self._evidence)
                # Finish awaited watcher teardown before the final clock check;
                # a return's async finally block must not reopen that race.
                if permit_watch is not None:
                    permit_watch.cancel()
                    await asyncio.gather(permit_watch, return_exceptions=True)
                    permit_watch = None
                # stderr/close can trip while durable completion is awaiting I/O.
                # Never expose a successful protocol result after terminal abort,
                # even if the completion collaborator returned successfully.
                if self._failure.done():
                    raise RoboticsError(self._failure.result())
                # A due timer cannot run during synchronous parsing/completion
                # work. Check both clocks explicitly before publishing success.
                self._check_deadlines(pending, deadline)
                self._heartbeat_deadline = self._loop.time() + self.heartbeat_seconds
                return response
        except BaseException as exc:
            code = exc.code if isinstance(exc, RoboticsError) else Code.ACKNOWLEDGEMENT_UNCERTAIN
            if isinstance(
                exc, (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError)
            ):
                code = Code.ACKNOWLEDGEMENT_UNCERTAIN
            self.authority.invalidate(code)
            if isinstance(exc, asyncio.IncompleteReadError):
                response_raw = exc.partial[: MAX_FRAME_BYTES + 1]
                response_truncated = True
            elif isinstance(exc, asyncio.LimitOverrunError) and exc.consumed > 0:
                # readuntil leaves these already-buffered bytes available. With
                # the failed reader finished and a single-flight transport,
                # read(n) returns immediately; it never waits for more input.
                assert self.process.stdout is not None
                response_raw = await self.process.stdout.read(
                    min(exc.consumed, MAX_FRAME_BYTES + 1)
                )
                response_truncated = True
            elif response_raw is not None and len(response_raw) > MAX_FRAME_BYTES + 1:
                response_raw = response_raw[: MAX_FRAME_BYTES + 1]
                response_truncated = True
            with contextlib.suppress(RoboticsError):
                self._correlator.fail_pending()
            if response_raw is not None and self._evidence is not None:
                self._evidence = TransportEvidence(
                    self._evidence.request_bytes,
                    response_raw,
                    pending.reservation_id if pending else None,
                    code,
                    pending.valid_until if pending else None,
                    response_truncated,
                )
            self._trip(code)
            assert self._abort_task is not None
            try:
                await asyncio.shield(self._abort_task)
            except Exception:
                # Keep the failed abort task for close/recovery to report. Do not
                # imply that a storage/cleanup exception cleared uncertainty.
                pass
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise RoboticsError(code) from exc
        finally:
            if permit_watch is not None:
                permit_watch.cancel()
                await asyncio.gather(permit_watch, return_exceptions=True)
            if read_task is not None and not read_task.done():
                read_task.cancel()
                await asyncio.gather(read_task, return_exceptions=True)
            if read_task is not None and self._failure.done():
                # Cleanup can produce EOF after the once-only failure callback.
                # Retrieve the finished reader's exception and preserve any
                # already-known bytes for reconciliation without another read.
                late_raw: bytes | None = None
                late_truncated = False
                try:
                    late_raw = read_task.result()
                    late_truncated = len(late_raw) > MAX_FRAME_BYTES + 1
                    late_raw = late_raw[: MAX_FRAME_BYTES + 1]
                except asyncio.IncompleteReadError as read_error:
                    late_raw = read_error.partial[: MAX_FRAME_BYTES + 1]
                    late_truncated = True
                except (Exception, asyncio.CancelledError):
                    pass
                if (
                    late_raw is not None
                    and self._terminal_evidence is not None
                    and self._terminal_evidence.response_bytes is None
                ):
                    self._terminal_evidence = replace(
                        self._terminal_evidence,
                        response_bytes=late_raw,
                        response_truncated=late_truncated,
                    )
            if pending is not None:
                self.authority.disarm(pending)
            self._pending = None
            self._evidence = None
            self._busy = False

    async def _permit_deadline(self, pending: PendingAuthority) -> None:
        await pending.granted.wait()
        if pending.valid_until is None or pending.monotonic_valid_until is None:
            self._trip(Code.LEASE_INVALID)
            return
        remaining = min(
            (pending.valid_until - datetime.now(UTC)).total_seconds(),
            pending.monotonic_valid_until - self._loop.time(),
        )
        if remaining > 0:
            await asyncio.sleep(remaining)
        if self._pending is pending and self._busy:
            self._trip(Code.LEASE_INVALID)

    def _check_deadlines(self, pending: PendingAuthority | None, request_deadline: float) -> None:
        now = self._loop.time()
        if now >= self._wall_deadline:
            raise RoboticsError(Code.RESOURCE_CAP_EXHAUSTED)
        if now >= self._heartbeat_deadline:
            raise RoboticsError(Code.HEARTBEAT_LOST)
        if now >= request_deadline:
            raise RoboticsError(Code.ACKNOWLEDGEMENT_UNCERTAIN)
        if (
            pending is not None
            and pending.admitted
            and (
                pending.invalidated
                or pending.valid_until is None
                or pending.monotonic_valid_until is None
                or datetime.now(UTC) >= pending.valid_until
                or now >= pending.monotonic_valid_until
            )
        ):
            raise RoboticsError(Code.LEASE_INVALID)

    async def close(self) -> None:
        self._closed = True
        self.authority.invalidate()
        try:
            if self._abort_task is not None:
                await asyncio.shield(self._abort_task)
            elif self._busy:
                self._trip(Code.ACKNOWLEDGEMENT_UNCERTAIN)
                assert self._abort_task is not None
                await asyncio.shield(self._abort_task)
            else:
                await self.cleanup()
        finally:
            self._watchdog_task.cancel()
            self._stderr_task.cancel()
            await asyncio.gather(self._watchdog_task, self._stderr_task, return_exceptions=True)
            await self.authority.close()
