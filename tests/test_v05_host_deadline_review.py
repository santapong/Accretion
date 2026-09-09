"""Actual local child/socket regression witnesses; no simulator or live authority."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest
import test_v05_host_transport as fixtures
from test_v05_host_authority_channel import make_channel
from v05_sdk_fixtures import Harness

from accretion.contracts.canonical import canonical_json
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.host import transport as transport_module
from accretion.robotics.host.authority_channel import AuthorityGrant
from accretion.robotics.protocol import (
    MAX_FRAME_BYTES,
    HeartbeatRequest,
    HeartbeatResult,
    ProtocolResponse,
    SuccessOutcome,
)


@pytest.mark.parametrize("wall_clock_regression", [False, True])
async def test_completion_stall_cannot_publish_after_permit_expiry(
    tmp_path, monkeypatch, wall_clock_regression
):
    harness, transport, admissions, completed, failures = await fixtures.setup(
        tmp_path, "normal", permit_seconds=0.15
    )

    class EarlierWallClock:
        @staticmethod
        def now(tz):
            return datetime.now(tz) - timedelta(hours=1)

    async def complete(evidence):
        if wall_clock_regression:
            monkeypatch.setattr(transport_module, "datetime", EarlierWallClock)
        # Intentional short event-loop stall: timer tasks cannot run until this
        # callback returns. The explicit clock check must close that gap.
        time.sleep(0.25)  # noqa: ASYNC251 -- deliberately starve deadline timer tasks
        completed.append(evidence)

    transport.complete = complete
    try:
        with pytest.raises(RoboticsError) as error:
            await transport.request(harness.request(HeartbeatRequest()), harness.session.pins)
        assert error.value.code is Code.LEASE_INVALID
        assert len(admissions) == len(completed) == len(failures) == 1
        assert transport.closed and transport.process.returncode is not None
        evidence = transport.failure_evidence
        assert evidence is not None and evidence.authority_valid_until <= datetime.now(UTC)
        assert evidence.response_bytes == completed[0].response_bytes + b"\n"
        assert (
            evidence.reservation_id == "synthetic-reservation" and not evidence.response_truncated
        )
    finally:
        await transport.close()


@pytest.mark.parametrize("deny", [False, True])
async def test_completion_stall_also_checks_monotonic_request_cap(tmp_path, deny):
    harness, transport, admissions, completed, failures = await fixtures.setup(
        tmp_path, "normal", deny=deny, permit_seconds=10, request_seconds=5
    )
    # Warm the actual child before applying the shorter per-request deadline.
    # Python import time under concurrent CI load is outside this witness.
    await transport.request(harness.request(HeartbeatRequest()), harness.session.pins)
    admissions.clear()
    completed.clear()
    transport.request_seconds = 1

    async def complete(evidence):
        time.sleep(1.05)  # noqa: ASYNC251 -- deliberately starve deadline timer tasks
        completed.append(evidence)

    transport.complete = complete
    try:
        with pytest.raises(RoboticsError) as error:
            await transport.request(harness.request(HeartbeatRequest()), harness.session.pins)
        assert error.value.code is Code.ACKNOWLEDGEMENT_UNCERTAIN
        assert len(admissions) == len(completed) == len(failures) == 1
        assert transport.closed and transport.process.returncode is not None
    finally:
        await transport.close()


async def test_terminal_cancel_blocks_late_committed_grant_before_slow_cleanup(tmp_path):
    harness, transport, admissions, completed, failures = await fixtures.setup(tmp_path, "normal")
    committed, deliver, cleanup_started, cleanup_release = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    authorize, cleanup = transport.authority.authorize, transport.cleanup

    async def delayed_authorize(request, pins):
        grant = await authorize(request, pins)
        committed.set()  # The durable reservation precedes the delayed return.
        await deliver.wait()
        return grant

    async def delayed_cleanup():
        cleanup_started.set()
        await cleanup_release.wait()  # Model asynchronous daemon latency.
        await cleanup()

    transport.authority.authorize = delayed_authorize
    transport.cleanup = delayed_cleanup
    request = harness.request(HeartbeatRequest())
    task = asyncio.create_task(transport.request(request, harness.session.pins))
    try:
        await asyncio.wait_for(committed.wait(), 3)
        pending = transport.authority.pending
        assert pending is not None and pending.claimed
        task.cancel()
        await asyncio.wait_for(cleanup_started.wait(), 3)
        assert pending.invalidated and transport.closed
        assert len(failures) == 1 and failures[0][0].reservation_id is None
        deliver.set()
        await asyncio.wait_for(pending.granted.wait(), 3)
        assert pending.reservation_id == "synthetic-reservation" and not pending.admitted
        assert pending.failure is Code.ACKNOWLEDGEMENT_UNCERTAIN
        assert transport.process.returncode is None  # Denied without waiting for cleanup.
        evidence = transport.failure_evidence
        assert evidence is not None and evidence.reservation_id == pending.reservation_id
        assert evidence.authority_valid_until == pending.valid_until
        assert evidence.request_bytes == canonical_json(request)
        cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert transport.authority.pending is None
        assert transport.authority.last_pending is pending
        retained = transport.failure_evidence
        assert retained is not None and retained.reservation_id == evidence.reservation_id
        assert retained.request_bytes == evidence.request_bytes
        assert evidence.response_bytes is None  # Earlier immutable snapshot is unchanged.
        assert transport.process.returncode is not None and not completed
        with pytest.raises(RoboticsError):
            await transport.request(request, harness.session.pins)
        assert len(admissions) == len(failures) == 1
    finally:
        deliver.set()
        cleanup_release.set()
        await asyncio.gather(task, return_exceptions=True)
        await transport.close()


async def test_disarmed_request_cannot_receive_late_allow(tmp_path):
    harness = Harness()
    request = harness.request(HeartbeatRequest())
    channel, guard, _ = await make_channel(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def authorize(request, pins):
        entered.set()
        await release.wait()
        return AuthorityGrant(
            reservation_id="late-committed", valid_until=datetime.now(UTC) + timedelta(seconds=10)
        )

    channel.authorize = authorize
    pending = channel.arm(request, harness.session.pins)
    task = asyncio.create_task(
        asyncio.to_thread(guard.authorize, request, pins=harness.session.pins)
    )
    try:
        await asyncio.wait_for(entered.wait(), 3)
        channel.disarm(pending)
        release.set()
        with pytest.raises(RoboticsError):
            await task
        assert pending.invalidated and not pending.admitted
        assert channel.last_pending is pending and pending.reservation_id == "late-committed"
        with pytest.raises(RoboticsError):
            guard.check_current()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await channel.close()


async def test_partial_ack_eof_is_retained_and_marked_incomplete(tmp_path, monkeypatch):
    normal = (
        "sys.stdout.buffer.write(canonical_json(response) + b'\\n')\n    sys.stdout.buffer.flush()"
    )
    partial = (
        "sys.stdout.buffer.write(canonical_json(response)[:100]); "
        "sys.stdout.buffer.flush(); sys.exit(7)"
    )
    assert normal in fixtures.CHILD
    monkeypatch.setattr(fixtures, "CHILD", fixtures.CHILD.replace(normal, partial))
    harness, transport, admissions, completed, failures = await fixtures.setup(tmp_path, "normal")
    request = harness.request(HeartbeatRequest())
    expected = canonical_json(
        ProtocolResponse.create(request, SuccessOutcome(payload=HeartbeatResult()))
    )[:100]
    try:
        with pytest.raises(RoboticsError) as error:
            await transport.request(request, harness.session.pins)
        assert error.value.code is Code.ACKNOWLEDGEMENT_UNCERTAIN
        assert len(admissions) == len(failures) == 1 and not completed
        evidence = failures[0][0]
        assert evidence.response_bytes == expected and evidence.response_truncated
        assert evidence.reservation_id == "synthetic-reservation"
        assert transport.failure_evidence == evidence
        assert transport.closed and transport.process.returncode is not None
    finally:
        await transport.close()


async def test_oversized_unterminated_ack_retains_only_bounded_prefix(tmp_path):
    harness, transport, admissions, completed, failures = await fixtures.setup(
        tmp_path, "oversized"
    )
    try:
        with pytest.raises(RoboticsError):
            await transport.request(harness.request(HeartbeatRequest()), harness.session.pins)
        assert len(admissions) == len(failures) == 1 and not completed
        evidence = failures[0][0]
        assert evidence.response_truncated
        assert evidence.response_bytes == b"x" * (MAX_FRAME_BYTES + 1)
        assert transport.failure_evidence == evidence
        assert transport.closed and transport.process.returncode is not None
    finally:
        await transport.close()


async def test_watcher_teardown_cannot_publish_after_permit_expiry(tmp_path):
    harness, transport, admissions, completed, failures = await fixtures.setup(
        tmp_path, "normal", permit_seconds=0.2
    )
    watch = transport._permit_deadline

    async def slow_teardown(pending):
        try:
            await watch(pending)
        finally:
            await asyncio.sleep(0.3)

    transport._permit_deadline = slow_teardown
    try:
        with pytest.raises(RoboticsError) as error:
            await transport.request(harness.request(HeartbeatRequest()), harness.session.pins)
        assert error.value.code is Code.LEASE_INVALID
        assert len(admissions) == len(completed) == len(failures) == 1
        assert transport.closed and transport.process.returncode is not None
    finally:
        await transport.close()


async def test_cleanup_eof_refreshes_retained_bytes_without_second_failure(tmp_path, monkeypatch):
    normal = (
        "sys.stdout.buffer.write(canonical_json(response) + b'\\n')\n    sys.stdout.buffer.flush()"
    )
    partial = (
        "sys.stdout.buffer.write(canonical_json(response)[:100]); sys.stdout.buffer.flush(); "
        "sys.stderr.buffer.write(b'partial-ready'); sys.stderr.buffer.flush(); time.sleep(5)"
    )
    assert normal in fixtures.CHILD
    monkeypatch.setattr(fixtures, "CHILD", fixtures.CHILD.replace(normal, partial))
    harness, transport, admissions, completed, failures = await fixtures.setup(tmp_path, "normal")
    request = harness.request(HeartbeatRequest())
    expected = canonical_json(
        ProtocolResponse.create(request, SuccessOutcome(payload=HeartbeatResult()))
    )[:100]
    task = asyncio.create_task(transport.request(request, harness.session.pins))
    try:
        async with asyncio.timeout(3):
            while b"partial-ready" not in transport.stderr:  # noqa: ASYNC110 -- child pipe marker
                await asyncio.sleep(0.005)
        transport._trip(Code.ACKNOWLEDGEMENT_UNCERTAIN)
        with pytest.raises(RoboticsError):
            await task
        assert len(admissions) == len(failures) == 1 and not completed
        initial = failures[0][0]
        assert initial.response_bytes is None
        retained = transport.failure_evidence
        assert retained is not None and retained.response_bytes == expected
        assert retained.response_truncated
        assert retained.reservation_id == initial.reservation_id == "synthetic-reservation"
    finally:
        await asyncio.gather(task, return_exceptions=True)
        await transport.close()
