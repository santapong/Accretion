"""Actual socket/child witnesses for permits delayed across their trust expiry."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from test_v05_host_authority_channel import make_channel
from test_v05_host_transport import setup
from v05_sdk_fixtures import Harness

from accretion.contracts.canonical import canonical_json
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.host.authority_channel import (
    AuthorityGrant,
    AuthorityReply,
    UnixAdmissionGuard,
)
from accretion.robotics.protocol import HeartbeatRequest


@pytest.mark.parametrize("kind", ["expired", "unbounded", "legacy"])
async def test_invalid_postcommit_grant_never_admits_or_retries(tmp_path, kind):
    harness = Harness()
    request = harness.request(HeartbeatRequest())
    channel, guard, _ = await make_channel(tmp_path)
    calls = []

    async def authorize(request, pins):
        calls.append(request.request_digest)
        if kind == "legacy":
            return "old-deadline-free-permit"
        return AuthorityGrant(
            reservation_id="already-committed",
            valid_until=datetime.now(UTC) + timedelta(seconds=-1 if kind == "expired" else 100000),
        )

    channel.authorize = authorize
    try:
        pending = channel.arm(request, harness.session.pins)
        with pytest.raises(RoboticsError):
            await asyncio.to_thread(guard.authorize, request, pins=harness.session.pins)
        assert pending.claimed and not pending.admitted
        assert pending.reservation_id == (None if kind == "legacy" else "already-committed")
        with pytest.raises(RoboticsError):
            await asyncio.to_thread(guard.authorize, request, pins=harness.session.pins)
        assert calls == [request.request_digest]
        with pytest.raises(RoboticsError):
            guard.check_current()
    finally:
        await channel.close()


async def test_permit_expired_while_waiting_for_socket_eof_is_refused(tmp_path):
    harness = Harness()
    request = harness.request(HeartbeatRequest())

    async def reply_then_hold_eof(reader, writer):
        await reader.readuntil(b"\n")
        writer.write(
            canonical_json(
                AuthorityReply(
                    request_digest=request.request_digest,
                    allowed=True,
                    valid_until=datetime.now(UTC) + timedelta(seconds=0.05),
                )
            )
            + b"\n"
        )
        await writer.drain()
        await asyncio.sleep(0.1)
        writer.close()
        await writer.wait_closed()

    path = tmp_path / "expiry.sock"
    server = await asyncio.start_unix_server(reply_then_hold_eof, path=str(path))
    try:
        guard = UnixAdmissionGuard(path, timeout_seconds=1)
        with pytest.raises(RoboticsError) as error:
            await asyncio.to_thread(guard.authorize, request, pins=harness.session.pins)
        assert error.value.code is Code.LEASE_INVALID
    finally:
        server.close()
        await server.wait_closed()


async def test_permit_deadline_stops_executing_child_and_retains_once_reservation(tmp_path):
    harness, transport, admissions, completed, failures = await setup(
        tmp_path,
        "delayed_after_admission",
        permit_seconds=0.15,
    )
    try:
        with pytest.raises(RoboticsError) as error:
            await transport.request(harness.request(HeartbeatRequest()), harness.session.pins)
        assert error.value.code is Code.LEASE_INVALID
        assert transport.process.returncode is not None and transport.closed
        assert len(admissions) == len(failures) == 1 and not completed
        assert failures[0][0].reservation_id == "synthetic-reservation"
        assert failures[0][0].authority_valid_until <= datetime.now(UTC)
        with pytest.raises(RoboticsError):
            await transport.request(harness.request(HeartbeatRequest()), harness.session.pins)
        assert len(admissions) == 1
    finally:
        await transport.close()


async def test_deadline_during_awaited_completion_cannot_return_success(tmp_path):
    harness, transport, admissions, completed, failures = await setup(
        tmp_path,
        "normal",
        permit_seconds=0.2,
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def complete(evidence):
        entered.set()
        await release.wait()
        completed.append(evidence)

    transport.complete = complete
    operation = asyncio.create_task(
        transport.request(harness.request(HeartbeatRequest()), harness.session.pins)
    )
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await asyncio.wait_for(transport.process.wait(), 2)
        assert transport.closed and len(failures) == 1
        release.set()
        with pytest.raises(RoboticsError) as error:
            await operation
        assert error.value.code is Code.LEASE_INVALID
        assert len(admissions) == len(completed) == 1
        assert failures[0][0].response_bytes == completed[0].response_bytes
    finally:
        release.set()
        await asyncio.gather(operation, return_exceptions=True)
        await transport.close()


@pytest.mark.parametrize(
    "allowed,code,deadline",
    [
        (True, None, None),
        (False, None, None),
        (False, Code.APPROVAL_INVALID, datetime.now(UTC)),
        (True, Code.APPROVAL_INVALID, datetime.now(UTC)),
    ],
)
def test_reply_cannot_mix_permission_with_missing_or_contradictory_authority(
    allowed, code, deadline
):
    with pytest.raises(ValueError):
        AuthorityReply(request_digest="a" * 64, allowed=allowed, code=code, valid_until=deadline)
