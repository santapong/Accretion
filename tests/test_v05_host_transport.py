"""Actual child-pipe failure witnesses; these children are not isolated robots."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from v05_sdk_fixtures import Harness

from accretion.contracts.canonical import canonical_json
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.host.authority_channel import AuthorityGrant, LeaseAuthorityChannel
from accretion.robotics.host.transport import AdapterTransport, TransportEvidence
from accretion.robotics.protocol import HeartbeatRequest, ProtocolRequest
from accretion.robotics.sdk import ExecutionPins

CHILD = """
import sys, time
from pathlib import Path
from accretion.contracts.canonical import canonical_json
from accretion.robotics.host.authority_channel import UnixAdmissionGuard
from accretion.robotics.protocol import *
from accretion.robotics.sdk import ExecutionPins
from accretion.robotics.errors import RoboticsError
mode, socket_path, pins_path = sys.argv[1:]
pins = ExecutionPins.model_validate_json(Path(pins_path).read_bytes())
for raw in sys.stdin.buffer:
    request = parse_message(raw[:-1])
    if mode == 'crash': sys.exit(4)
    if mode == 'stderr':
        sys.stderr.buffer.write(b'x' * 131072); sys.stderr.buffer.flush()
    if mode == 'delay': time.sleep(5)
    if mode != 'bypass':
        try:
            UnixAdmissionGuard(Path(socket_path)).authorize(request, pins=pins)
        except RoboticsError as exc:
            response = ProtocolResponse.create(request, ErrorOutcome(code=exc.code))
            sys.stdout.buffer.write(canonical_json(response) + b'\\n')
            sys.stdout.buffer.flush()
            continue
    if mode == 'lost_after_admission': sys.exit(5)
    if mode == 'delayed_after_admission': time.sleep(5)
    if mode == 'oversized':
        sys.stdout.buffer.write(b'x' * (2 * 1024 * 1024)); sys.stdout.buffer.flush()
        continue
    response = ProtocolResponse.create(request, SuccessOutcome(payload=HeartbeatResult()))
    if mode == 'wrong_ack':
        wrong = ProtocolRequest.create(request_id='wrong', sequence=request.sequence,
            scope=request.scope, payload=request.payload)
        response = ProtocolResponse.create(wrong, SuccessOutcome(payload=HeartbeatResult()))
    sys.stdout.buffer.write(canonical_json(response) + b'\\n')
    sys.stdout.buffer.flush()
"""


async def setup(
    tmp_path: Path,
    mode: str,
    *,
    deny: bool = False,
    fail_commit: bool = False,
    request_seconds: float = 3,
    heartbeat_seconds: float = 5,
    permit_seconds: float = 10,
):
    harness = Harness()
    channel_dir = tmp_path / "ipc"
    channel_dir.mkdir(mode=0o700)
    admissions = []
    completed: list[TransportEvidence] = []
    failures = []

    async def authorize(request: ProtocolRequest, pins: ExecutionPins) -> AuthorityGrant:
        admissions.append(request.request_digest)
        if deny:
            raise RoboticsError(Code.APPROVAL_INVALID)
        return AuthorityGrant(
            reservation_id="synthetic-reservation",
            valid_until=datetime.now(UTC) + timedelta(seconds=permit_seconds),
        )

    async def complete(evidence: TransportEvidence) -> None:
        if fail_commit:
            raise RuntimeError("synthetic storage outage after real pipe acknowledgement")
        completed.append(evidence)

    async def failed(evidence: TransportEvidence | None, code: Code) -> None:
        failures.append((evidence, code))

    channel = LeaseAuthorityChannel(channel_dir, authorize)
    await channel.start()
    pins_file = tmp_path / "pins.json"
    pins_file.write_bytes(canonical_json(harness.session.pins))
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        CHILD,
        mode,
        str(channel.path),
        str(pins_file),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=1024 * 1024 + 1,
    )

    async def cleanup() -> None:
        if process.returncode is None:
            process.kill()
        await process.wait()

    transport = AdapterTransport(
        process,
        channel,
        complete=complete,
        failed=failed,
        cleanup=cleanup,
        wall_seconds=10,
        request_seconds=request_seconds,
        heartbeat_seconds=heartbeat_seconds,
        stderr_bytes=4096,
    )
    return harness, transport, admissions, completed, failures


async def test_real_child_requires_exact_authority_and_records_original_bytes(
    tmp_path: Path,
) -> None:
    harness, transport, admissions, completed, failures = await setup(tmp_path, "normal")
    try:
        request = harness.request(HeartbeatRequest())
        response = await transport.request(request, harness.session.pins)
        assert response.op == "HEARTBEAT" and len(admissions) == len(completed) == 1
        assert completed[0].request_bytes == canonical_json(request)
        assert completed[0].response_bytes == canonical_json(response)
        assert completed[0].reservation_id == "synthetic-reservation" and not failures
    finally:
        await transport.close()
    assert transport.process.returncode is not None


@pytest.mark.parametrize(
    "mode", ["crash", "bypass", "lost_after_admission", "wrong_ack", "oversized", "stderr"]
)
async def test_faulted_pipe_is_fenced_and_cannot_retry(tmp_path: Path, mode: str) -> None:
    harness, transport, admissions, completed, failures = await setup(tmp_path, mode)
    try:
        request = harness.request(HeartbeatRequest())
        with pytest.raises(RoboticsError):
            await transport.request(request, harness.session.pins)
        assert transport.closed and not completed and len(failures) == 1
        assert transport.process.returncode is not None
        assert len(transport.stderr) <= 4096
        if mode in {"lost_after_admission", "wrong_ack", "oversized"}:
            assert len(admissions) == 1
            assert failures[0][0].reservation_id == "synthetic-reservation"
        with pytest.raises(RoboticsError):
            await transport.request(request, harness.session.pins)
        assert len(failures) == 1
    finally:
        await transport.close()


async def test_durable_completion_failure_preserves_received_ack_and_reservation(
    tmp_path: Path,
) -> None:
    harness, transport, admissions, completed, failures = await setup(
        tmp_path, "normal", fail_commit=True
    )
    try:
        request = harness.request(HeartbeatRequest())
        with pytest.raises(RoboticsError):
            await transport.request(request, harness.session.pins)
        assert admissions and not completed and len(failures) == 1
        evidence = failures[0][0]
        assert evidence.request_bytes == canonical_json(request)
        assert (
            evidence.response_bytes is not None
            and evidence.reservation_id == "synthetic-reservation"
        )
    finally:
        await transport.close()


async def test_actual_request_timeout_stops_child(tmp_path: Path) -> None:
    harness, transport, _, completed, failures = await setup(tmp_path, "delay", request_seconds=0.8)
    try:
        with pytest.raises(RoboticsError):
            await transport.request(harness.request(HeartbeatRequest()), harness.session.pins)
        assert transport.closed and failures and not completed
        assert transport.process.returncode is not None
    finally:
        await transport.close()


async def test_idle_heartbeat_loss_stops_process_without_manufactured_request(
    tmp_path: Path,
) -> None:
    _, transport, admissions, completed, failures = await setup(
        tmp_path, "normal", heartbeat_seconds=0.15
    )
    try:
        await asyncio.wait_for(transport.process.wait(), timeout=3)
        assert transport.closed and not admissions and not completed
        assert failures == [(None, Code.HEARTBEAT_LOST)]
    finally:
        await transport.close()


async def test_declared_read_denial_is_correlated_without_fake_admission(tmp_path: Path) -> None:
    harness, transport, admissions, completed, failures = await setup(tmp_path, "normal", deny=True)
    try:
        result = await transport.request(harness.request(HeartbeatRequest()), harness.session.pins)
        assert result.outcome.code is Code.APPROVAL_INVALID
        assert len(admissions) == len(completed) == 1 and not failures
        assert completed[0].reservation_id is None
    finally:
        await transport.close()


async def test_cancellation_cleans_process_and_retains_uncertainty(tmp_path: Path) -> None:
    harness, transport, _, completed, failures = await setup(tmp_path, "delay")
    try:
        task = asyncio.create_task(
            transport.request(harness.request(HeartbeatRequest()), harness.session.pins)
        )
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not completed and failures and transport.process.returncode is not None
    finally:
        await transport.close()
