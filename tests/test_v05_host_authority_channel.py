"""Real Unix IPC construction witnesses; not container or robot conformance."""

from __future__ import annotations

import asyncio
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from v05_sdk_fixtures import Harness

from accretion.contracts.canonical import canonical_json
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.host.authority_channel import (
    RPC_LIMIT,
    AuthorityCall,
    AuthorityGrant,
    LeaseAuthorityChannel,
    UnixAdmissionGuard,
)
from accretion.robotics.protocol import ObserveRequest, ProtocolRequest
from accretion.robotics.sdk import ExecutionPins


async def make_channel(tmp_path: Path, *, deny: bool = False, delay: float = 0):
    tmp_path.chmod(0o700)
    calls = []

    async def authorize(request: ProtocolRequest, pins: ExecutionPins) -> AuthorityGrant:
        calls.append((request.request_digest, pins.episode.episode_id))
        if delay:
            await asyncio.sleep(delay)
        if deny:
            raise RoboticsError(Code.APPROVAL_INVALID)
        return AuthorityGrant(
            reservation_id="synthetic-reservation-not-production",
            valid_until=datetime.now(UTC) + timedelta(seconds=10),
        )

    channel = LeaseAuthorityChannel(tmp_path, authorize, timeout_seconds=0.2)
    await channel.start()
    return channel, UnixAdmissionGuard(channel.path, timeout_seconds=0.5), calls


async def test_exact_once_callback_and_retained_reservation(tmp_path: Path) -> None:
    harness = Harness()
    request = harness.request(ObserveRequest())
    channel, guard, calls = await make_channel(tmp_path)
    try:
        pending = channel.arm(request, harness.session.pins)
        await asyncio.to_thread(guard.authorize, request, pins=harness.session.pins)
        assert pending.claimed and pending.admitted
        assert pending.reservation_id == "synthetic-reservation-not-production"
        with pytest.raises(RoboticsError, match="lease"):
            await asyncio.to_thread(guard.authorize, request, pins=harness.session.pins)
        assert len(calls) == 1
        channel.disarm(pending)
        with pytest.raises(RoboticsError):
            await asyncio.to_thread(guard.authorize, request, pins=harness.session.pins)
        assert len(calls) == 1
    finally:
        await channel.close()


async def test_worker_cannot_replace_supervisor_pins(tmp_path: Path) -> None:
    harness = Harness()
    request = harness.request(ObserveRequest())
    channel, guard, calls = await make_channel(tmp_path)
    try:
        pending = channel.arm(request, harness.session.pins)
        forged = harness.session.pins.model_copy(update={"approval_hash": "a" * 64})
        with pytest.raises(RoboticsError):
            await asyncio.to_thread(guard.authorize, request, pins=forged)
        assert not calls and not pending.claimed
        await asyncio.to_thread(guard.authorize, request, pins=harness.session.pins)
        assert len(calls) == 1
    finally:
        await channel.close()


async def test_concurrent_duplicate_invokes_storage_once(tmp_path: Path) -> None:
    harness = Harness()
    request = harness.request(ObserveRequest())
    channel, guard, calls = await make_channel(tmp_path, delay=0.05)
    try:
        pending = channel.arm(request, harness.session.pins)
        results = await asyncio.gather(
            *(
                asyncio.to_thread(guard.authorize, request, pins=harness.session.pins)
                for _ in range(2)
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(x, RoboticsError) for x in results) == 1
        assert len(calls) == 1 and pending.admitted
    finally:
        await channel.close()


async def test_permission_refusal_preserves_claim_without_admission(tmp_path: Path) -> None:
    harness = Harness()
    request = harness.request(ObserveRequest())
    channel, guard, calls = await make_channel(tmp_path, deny=True)
    try:
        pending = channel.arm(request, harness.session.pins)
        with pytest.raises(RoboticsError) as exc:
            await asyncio.to_thread(guard.authorize, request, pins=harness.session.pins)
        assert exc.value.code is Code.APPROVAL_INVALID
        assert pending.claimed and not pending.admitted
        assert pending.failure is Code.APPROVAL_INVALID and len(calls) == 1
    finally:
        await channel.close()


async def test_callback_timeout_is_uncertain_and_never_retryable(tmp_path: Path) -> None:
    harness = Harness()
    request = harness.request(ObserveRequest())
    channel, guard, calls = await make_channel(tmp_path, delay=1)
    try:
        pending = channel.arm(request, harness.session.pins)
        with pytest.raises(RoboticsError) as exc:
            await asyncio.to_thread(guard.authorize, request, pins=harness.session.pins)
        assert exc.value.code is Code.ACKNOWLEDGEMENT_UNCERTAIN
        assert pending.claimed and not pending.admitted
        assert pending.failure is Code.ACKNOWLEDGEMENT_UNCERTAIN
        with pytest.raises(RoboticsError):
            await asyncio.to_thread(guard.authorize, request, pins=harness.session.pins)
        assert len(calls) == 1
    finally:
        await channel.close()


@pytest.mark.parametrize("commit_during_cancel", [False, True])
async def test_close_refuses_inflight_admission(tmp_path: Path, commit_during_cancel: bool) -> None:
    tmp_path.chmod(0o700)
    entered = asyncio.Event()

    async def authorize(request: ProtocolRequest, pins: ExecutionPins) -> AuthorityGrant:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if not commit_during_cancel:
                raise
        return AuthorityGrant(
            reservation_id="committed-during-cancel",
            valid_until=datetime.now(UTC) + timedelta(seconds=10),
        )

    channel = LeaseAuthorityChannel(tmp_path, authorize, timeout_seconds=5)
    await channel.start()
    harness = Harness()
    request = harness.request(ObserveRequest())
    pending = channel.arm(request, harness.session.pins)
    guard = UnixAdmissionGuard(channel.path)
    caller = asyncio.create_task(
        asyncio.to_thread(guard.authorize, request, pins=harness.session.pins)
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.wait_for(channel.close(), timeout=1)
        with pytest.raises(RoboticsError) as exc:
            await caller
        assert exc.value.code is Code.ACKNOWLEDGEMENT_UNCERTAIN
        assert pending.claimed and not pending.admitted
        assert pending.failure is Code.ACKNOWLEDGEMENT_UNCERTAIN
        assert pending.reservation_id == (
            "committed-during-cancel" if commit_during_cancel else None
        )
    finally:
        await channel.close()
        await asyncio.gather(caller, return_exceptions=True)


async def test_lost_reply_keeps_completed_reservation(tmp_path: Path) -> None:
    harness = Harness()
    request = harness.request(ObserveRequest())
    channel, _, calls = await make_channel(tmp_path)
    try:
        pending = channel.arm(request, harness.session.pins)

        def send_and_disconnect() -> None:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.connect(str(channel.path))
                connection.sendall(
                    canonical_json(AuthorityCall(request=request, pins=harness.session.pins))
                    + b"\n"
                )

        await asyncio.to_thread(send_and_disconnect)
        for _ in range(20):
            if pending.admitted:
                break
            await asyncio.sleep(0.005)
        assert pending.admitted and pending.reservation_id is not None and len(calls) == 1
    finally:
        await channel.close()


@pytest.mark.parametrize("raw", [b'{"version":"1.0.0","version":"2"}\n', b"x" * (RPC_LIMIT + 2)])
async def test_malformed_or_oversized_wire_never_invokes_storage(
    tmp_path: Path, raw: bytes
) -> None:
    channel, _, calls = await make_channel(tmp_path)
    try:
        reader, writer = await asyncio.open_unix_connection(channel.path)
        writer.write(raw)
        await writer.drain()
        writer.write_eof()
        assert await asyncio.wait_for(reader.read(2048), timeout=1) == b""
        writer.close()
        await writer.wait_closed()
        assert not calls
    finally:
        await channel.close()


async def test_channel_refuses_shared_directory(tmp_path: Path) -> None:
    tmp_path.chmod(0o755)

    async def never(request: ProtocolRequest, pins: ExecutionPins) -> str:
        raise AssertionError("must not reach storage")

    with pytest.raises(RoboticsError):
        await LeaseAuthorityChannel(tmp_path, never).start()
