"""Real child pipes and Unix authority, with synthetic data and no robot/approval."""

from __future__ import annotations

import asyncio
import sys
import threading
from datetime import UTC, datetime, timedelta

import pytest
from test_v05_host_worker import activation_for, fixture

from accretion.contracts.canonical import canonical_json
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.host.authority_channel import AuthorityGrant, LeaseAuthorityChannel
from accretion.robotics.host.staging import StagedAdapterTransport, StagePhase
from accretion.robotics.host.worker import WorkerActivation, WorkerReady
from accretion.robotics.protocol import (
    HeartbeatRequest,
    ProtocolRequest,
    parse_message,
)

# Runs the actual bounded serve handshake with a test-only adapter. No simulator,
# container, provider, persisted policy grant or authentic approval is constructed.
CHILD = r"""
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_v05_host_worker import fixture
from v05_sdk_fixtures import replace_contract
from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics import EmbodimentDescriptor
from accretion.robotics.host import worker
from accretion.robotics.host.authority_channel import UnixAdmissionGuard
from accretion.robotics.protocol import DescriptorRecord, ProtocolResponse
from accretion.robotics.errors import RoboticsError, RoboticsErrorCode as Code
mode, bootstrap_file, socket_path = sys.argv[1:]
harness, adapter, _ = fixture()
bootstrap = worker.WorkerBootstrap.model_validate_json(Path(bootstrap_file).read_bytes())
write = worker.write_frame

def output(destination, value):
    if isinstance(value, worker.WorkerReady):
        if mode == 'no_ready': time.sleep(10)
        if mode == 'stale_ready': value = value.model_copy(update={'bootstrap_hash': '0' * 64})
        if mode == 'oversized_ready':
            destination.write(b'x' * (2 * 1024 * 1024) + b'\n'); destination.flush(); time.sleep(10)
        if mode == 'partial_ready':
            destination.write(b'{"partial":'); destination.flush(); os._exit(4)
    if (isinstance(value, ProtocolResponse) and value.op == 'DESCRIBE'
        and mode == 'wrong_descriptor'):
        data = value.model_dump(mode='python')
        desc = value.outcome.payload.description.descriptor.for_execution(EmbodimentDescriptor)
        changed = replace_contract(desc,
            created_by={'principal_id': 'different-test-adapter', 'status': 'ACTIVE'})
        record = DescriptorRecord.from_contract(changed)
        data['outcome']['payload']['description']['descriptor'] = record
        data.pop('response_digest')
        value = ProtocolResponse.model_validate(
            {**data, 'response_digest': content_hash(data, exclude=())})
    write(destination, value)
worker.write_frame = output
if mode == 'reject_bind':
    def reject(_): raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
    adapter.bind_episode = reject
if mode == 'lost_hello':
    def bind(_): os._exit(5)
    adapter.bind_episode = bind
worker.serve(bootstrap, factory=lambda _: adapter, artifacts=harness.artifacts,
    authority=UnixAdmissionGuard(Path(socket_path)), source=sys.stdin.buffer,
    destination=sys.stdout.buffer)
"""


class Scenario:
    def __init__(self):
        self.calls = []
        self.events = {}
        self.pause = None
        self.fail = None
        self.release = asyncio.Event()
        self.fence = asyncio.Lock()
        self.terminal = False
        self.published = False
        self.complete_rows = []
        self.failures = []
        self.admissions = []
        self.archives = []
        self.cleanup_calls = 0
        self.forged_activation = False
        self.deny_authority = False
        self.authority_expired = False
        self.authority_short = None
        self.archive_change = None

    async def point(self, name):
        self.calls.append(name)
        self.events.setdefault(name, asyncio.Event()).set()
        if self.pause == name:
            await self.release.wait()
        if self.fail == name:
            raise RuntimeError("synthetic storage failure")

    async def reached(self, name):
        await asyncio.wait_for(self.events.setdefault(name, asyncio.Event()).wait(), 5)


async def setup(tmp_path, mode="normal", **options):
    scenario = Scenario()
    for key, value in options.items():
        setattr(scenario, key, value)
    harness, _, bootstrap = fixture()
    callback_bootstrap = type(bootstrap).model_validate_json(canonical_json(bootstrap))
    directory = tmp_path / "ipc"
    directory.mkdir(mode=0o700)

    async def authorize(request, pins):
        scenario.admissions.append(request.payload.op)
        return AuthorityGrant(
            reservation_id="synthetic-read-reservation",
            valid_until=datetime.now(UTC) + timedelta(seconds=10),
        )

    channel = LeaseAuthorityChannel(directory, authorize, timeout_seconds=2)
    await channel.start()
    bootstrap_path = tmp_path / "bootstrap.json"
    bootstrap_path.write_bytes(canonical_json(bootstrap))
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        CHILD,
        mode,
        str(bootstrap_path),
        str(channel.path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=1024 * 1024 + 1,
    )

    async def cleanup():
        scenario.cleanup_calls += 1
        if process.returncode is None:
            process.kill()
        await process.wait()

    async def archive(kind, raw):
        # Store first: an exception/cancellation is not proof of rollback.
        ref = harness.artifacts.put(raw, media_type="application/json")
        scenario.archives.append((kind, raw))
        await scenario.point("ARCHIVE_" + kind)
        if scenario.archive_change is not None:
            data = ref.model_dump(mode="json")
            data.update(scenario.archive_change)
            return ref.model_copy(update=data)
        return ref

    async def activate(ready, ref):
        assert scenario.archives[0] == ("READY", canonical_json(ready))
        assert not scenario.admissions
        await scenario.point("ACTIVATE")
        result = activation_for(callback_bootstrap, ready, harness.artifacts)
        if scenario.forged_activation:
            result.pins.episode.lease.generation += 1
        return result

    async def activation_authority(ready, activation):
        await scenario.point("AUTHORITY")
        if scenario.deny_authority:
            raise RoboticsError(Code.CAPABILITY_DENIED)
        if scenario.authority_expired:
            return datetime.now(UTC) - timedelta(seconds=1)
        return datetime.now(UTC) + timedelta(seconds=scenario.authority_short or 60)

    async def complete(evidence):
        operation = parse_message(evidence.request_bytes).payload.op
        await scenario.point(operation + "_COMPLETE")
        async with scenario.fence:
            if scenario.terminal:
                raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
            scenario.complete_rows.append(evidence)

    async def activated(evidence, pins):
        await scenario.point("PUBLISH")
        async with scenario.fence:
            if scenario.terminal:
                raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
            scenario.published = True

    async def failed(evidence, protocol_evidence, code):
        async with scenario.fence:
            scenario.terminal = True
            scenario.failures.append((evidence, protocol_evidence, code))

    transport = StagedAdapterTransport(
        process,
        channel,
        bootstrap=bootstrap,
        artifacts=harness.artifacts,
        archive=archive,
        activate=activate,
        activation_authority=activation_authority,
        activated=activated,
        complete=complete,
        failed=failed,
        cleanup=cleanup,
        initialization_seconds=options.get("initialization_seconds", 8),
        wall_seconds=10,
        request_seconds=2,
        heartbeat_seconds=options.get("heartbeat_seconds", 5),
        stderr_bytes=4096,
    )
    return scenario, harness, bootstrap, transport


async def test_actual_worker_starts_only_after_raw_archive_authority_and_scoped_ack(tmp_path):
    scenario, harness, _, transport = await setup(tmp_path)
    try:
        with pytest.raises(RoboticsError):
            await transport.request(harness.request(HeartbeatRequest()), harness.pins)
        pins = await transport.start()
        evidence = transport.staging_evidence
        assert transport.activated and scenario.published and not scenario.failures
        assert scenario.calls == [
            "ARCHIVE_READY",
            "ACTIVATE",
            "ARCHIVE_ACTIVATION",
            "AUTHORITY",
            "HELLO_COMPLETE",
            "DESCRIBE_COMPLETE",
            "PUBLISH",
        ]
        assert evidence.phase is StagePhase.ACTIVATED
        assert evidence.activation_write_attempted and evidence.activation_drained
        assert evidence.authority_deadline is not None
        assert scenario.admissions == ["DESCRIBE"]
        assert parse_message(evidence.hello.request_bytes).scope is None
        describe = parse_message(evidence.describe.request_bytes)
        assert describe.scope == pins.command_scope() and describe.sequence == 1
        assert (
            WorkerReady.model_validate_json(evidence.ready_bytes).initial_observation
            == harness.initial
        )
        assert WorkerActivation.model_validate_json(evidence.activation_bytes).pins == pins
        request = ProtocolRequest.create(
            request_id="after-staging",
            sequence=2,
            scope=pins.command_scope(),
            payload=HeartbeatRequest(),
        )
        assert (await transport.request(request, pins)).op == "HEARTBEAT"
        assert scenario.admissions == ["DESCRIBE", "HEARTBEAT"]
        with pytest.raises(RoboticsError):
            await transport.start()
    finally:
        await transport.close()
    assert scenario.cleanup_calls == 1


@pytest.mark.parametrize("mode", ["stale_ready", "partial_ready", "oversized_ready"])
async def test_bad_ready_retains_bounded_received_bytes_and_never_activates(tmp_path, mode):
    scenario, _, _, transport = await setup(tmp_path, mode)
    try:
        with pytest.raises(RoboticsError):
            await transport.start()
        assert not scenario.admissions and not scenario.published and not scenario.archives
        evidence = scenario.failures[0][0]
        assert evidence.ready_bytes and len(evidence.ready_bytes) <= 1024 * 1024 + 1
        assert not evidence.activation_write_attempted
        assert evidence.ready_truncated == (mode != "stale_ready")
        assert transport.process.returncode is not None
    finally:
        await transport.close()


@pytest.mark.parametrize(
    "stage",
    [
        "ARCHIVE_READY",
        "ACTIVATE",
        "ARCHIVE_ACTIVATION",
        "AUTHORITY",
        "HELLO_COMPLETE",
        "DESCRIBE_COMPLETE",
        "PUBLISH",
    ],
)
async def test_each_awaited_collaborator_failure_is_terminal_and_retains_attempt(tmp_path, stage):
    scenario, _, _, transport = await setup(tmp_path, fail=stage)
    try:
        with pytest.raises(RoboticsError):
            await transport.start()
        assert not transport.activated and not scenario.published
        assert len(scenario.failures) == 1 and transport.process.returncode is not None
        evidence = scenario.failures[0][0]
        assert evidence.ready_bytes is not None
        if stage not in {"ARCHIVE_READY", "ACTIVATE"}:
            assert evidence.activation_bytes is not None
        if stage in {"HELLO_COMPLETE", "DESCRIBE_COMPLETE", "PUBLISH"}:
            assert evidence.activation_write_attempted and evidence.hello.response_bytes is not None
        if stage in {"DESCRIBE_COMPLETE", "PUBLISH"}:
            assert evidence.describe.response_bytes is not None
    finally:
        await transport.close()


@pytest.mark.parametrize(
    "stage",
    [
        "ARCHIVE_READY",
        "ACTIVATE",
        "ARCHIVE_ACTIVATION",
        "AUTHORITY",
        "HELLO_COMPLETE",
        "DESCRIBE_COMPLETE",
        "PUBLISH",
    ],
)
async def test_cancel_during_every_stage_stops_child_without_losing_known_bytes(tmp_path, stage):
    scenario, _, _, transport = await setup(tmp_path, pause=stage)
    try:
        task = asyncio.create_task(transport.start())
        await scenario.reached(stage)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert transport.closed and not transport.activated and not scenario.published
        assert len(scenario.failures) == 1 and transport.process.returncode is not None
        assert scenario.failures[0][0].ready_bytes is not None
    finally:
        scenario.release.set()
        await transport.close()


@pytest.mark.parametrize("mode", ["reject_bind", "lost_hello", "wrong_descriptor"])
async def test_activation_drain_is_not_worker_acceptance_or_exact_descriptor(tmp_path, mode):
    scenario, _, _, transport = await setup(tmp_path, mode)
    try:
        with pytest.raises(RoboticsError):
            await transport.start()
        assert not transport.activated and not scenario.published
        evidence = scenario.failures[0][0]
        assert evidence.activation_bytes is not None and evidence.activation_write_attempted
        if mode == "wrong_descriptor":
            assert evidence.hello.response_bytes and evidence.describe.response_bytes
            assert scenario.admissions == ["DESCRIBE"]
        else:
            assert not scenario.admissions
    finally:
        await transport.close()


@pytest.mark.parametrize("option", ["forged_activation", "deny_authority", "authority_expired"])
async def test_receipt_models_cannot_bypass_live_activation_authority(tmp_path, option):
    scenario, _, _, transport = await setup(tmp_path, **{option: True})
    try:
        with pytest.raises(RoboticsError):
            await transport.start()
        assert scenario.failures[0][0].activation_bytes is not None
        assert not scenario.failures[0][0].activation_write_attempted
        assert not scenario.admissions and not scenario.published
    finally:
        await transport.close()


@pytest.mark.parametrize(
    "change",
    [
        {"media_type": "text/plain"},
        {"size_bytes": 1},
        {"evidence_class": "DIGITAL"},
        {"digest": "0" * 64, "uri": "artifact://sha256/" + "0" * 64},
    ],
)
async def test_archive_reference_must_describe_exact_simulation_json(tmp_path, change):
    scenario, _, _, transport = await setup(tmp_path, archive_change=change)
    try:
        with pytest.raises(RoboticsError):
            await transport.start()
        assert "ACTIVATE" not in scenario.calls and not scenario.admissions
        assert scenario.failures[0][0].ready_bytes == scenario.archives[0][1]
    finally:
        await transport.close()


async def test_denied_raw_observation_never_reaches_archive_or_approval(tmp_path):
    scenario, harness, _, transport = await setup(tmp_path)
    harness.artifacts.blobs.clear()
    try:
        with pytest.raises(RoboticsError):
            await transport.start()
        assert not scenario.calls and not scenario.admissions
        assert scenario.failures[0][0].phase is StagePhase.VALIDATING_READY
        assert scenario.failures[0][0].ready_bytes
    finally:
        await transport.close()


async def test_close_races_publication_on_same_fence_and_cleans_once(tmp_path):
    scenario, _, _, transport = await setup(tmp_path, pause="PUBLISH")
    try:
        task = asyncio.create_task(transport.start())
        await scenario.reached("PUBLISH")
        await asyncio.gather(transport.close(), transport.close())
        with pytest.raises(RoboticsError):
            await task
        scenario.release.set()
        await asyncio.sleep(0)
        assert not scenario.published and not transport.activated
        assert scenario.cleanup_calls == 1 and len(scenario.failures) == 1
        assert scenario.failures[0][0].describe.response_bytes
    finally:
        scenario.release.set()
        await transport.close()


async def test_explicit_initialization_policy_does_not_generate_heartbeats(tmp_path):
    scenario, _, _, transport = await setup(tmp_path, pause="ACTIVATE", heartbeat_seconds=0.05)
    try:
        task = asyncio.create_task(transport.start())
        await scenario.reached("ACTIVATE")
        await asyncio.sleep(0.1)  # beyond operational heartbeat, within explicit startup policy
        assert not transport.closed and not scenario.admissions
        scenario.release.set()
        await task
        assert transport.activated and scenario.admissions == ["DESCRIBE"]
    finally:
        await transport.close()


async def test_short_live_authority_expiry_caps_pending_publication(tmp_path):
    scenario, _, _, transport = await setup(tmp_path, pause="PUBLISH", authority_short=0.2)
    try:
        with pytest.raises(RoboticsError):
            await transport.start()
        assert not scenario.published and not transport.activated
        assert scenario.failures[0][0].authority_deadline is not None
        assert transport.process.returncode is not None
    finally:
        scenario.release.set()
        await transport.close()


async def test_raw_validation_thread_cannot_publish_after_cancellation(tmp_path):
    scenario, harness, _, transport = await setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = harness.artifacts.iter_bytes

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        yield from original(*args, **kwargs)

    harness.artifacts.iter_bytes = blocked
    try:
        task = asyncio.create_task(transport.start())
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert transport.process.returncode is not None and not scenario.calls
        release.set()
        await asyncio.sleep(0.02)
        assert not scenario.published and not transport.activated and len(scenario.failures) == 1
    finally:
        release.set()
        await transport.close()


async def test_constructor_snapshot_survives_original_bootstrap_mutation(tmp_path):
    scenario, _, bootstrap, transport = await setup(tmp_path)
    original = transport.staging_evidence.bootstrap_bytes
    bootstrap.episode.lease.generation += 1
    try:
        pins = await transport.start()
        assert transport.staging_evidence.bootstrap_bytes == original
        assert pins.episode.lease.generation != bootstrap.episode.lease.generation
        assert scenario.published
    finally:
        await transport.close()


async def test_callback_commit_then_cancel_is_not_reported_as_rollback(tmp_path):
    scenario, _, _, transport = await setup(tmp_path)
    committed = asyncio.Event()
    block = asyncio.Event()

    async def committed_activation(ready, ref):
        # This stands for a durable collaborator's own commit, whose result did
        # not reach the caller. Staging must retain that pending phase, not mint
        # a replacement approval or claim the commit disappeared.
        scenario.published = True
        committed.set()
        await block.wait()
        raise AssertionError("cancelled callback must not return an invented activation")

    transport._activate = committed_activation
    try:
        task = asyncio.create_task(transport.start())
        await asyncio.wait_for(committed.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert scenario.published and scenario.terminal and not transport.activated
        evidence = scenario.failures[0][0]
        assert evidence.phase is StagePhase.ACTIVATING and evidence.ready_ref_json
        assert evidence.activation_bytes is None and not evidence.activation_write_attempted
        assert transport.process.returncode is not None
    finally:
        await transport.close()


async def test_initialization_expiry_stops_silent_owned_child(tmp_path):
    scenario, _, _, transport = await setup(tmp_path, mode="no_ready", initialization_seconds=0.05)
    try:
        with pytest.raises(RoboticsError):
            await transport.start()
        assert not transport.activated and not scenario.admissions
        assert len(scenario.failures) == 1 and transport.process.returncode is not None
        assert scenario.cleanup_calls == 1
    finally:
        await transport.close()


async def test_live_authority_expiry_stops_idle_activated_worker(tmp_path):
    scenario, _, _, transport = await setup(tmp_path, authority_short=0.2)
    try:
        await transport.start()
        assert transport.activated and scenario.published
        await asyncio.wait_for(transport.process.wait(), 2)
        assert not transport.activated and transport.closed
        assert scenario.failures[0][2] is Code.LEASE_INVALID
        assert scenario.cleanup_calls == 1
    finally:
        await transport.close()
