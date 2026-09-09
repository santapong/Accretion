"""Pure daemon/journal doubles: ordering proof, never real restart/isolation evidence."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace

import pytest
from test_v05_host_docker_profile import fixture
from test_v05_host_review import FakeDaemon

from accretion.robotics.errors import RoboticsError
from accretion.robotics.host.docker import DockerSupervisor


class Journal:
    def __init__(self, daemon):
        self.daemon = daemon
        self.attempt = None
        self.events = []
        self.fresh = True
        self.fail_created = self.fail_started = self.fail_cleaned = False

    async def planned(self, attempt):
        assert not self.daemon.present
        self.events.append("PLANNED")
        self.attempt = attempt
        return self.fresh

    async def created(self, attempt, witness):
        self.daemon.supervisor.verify_created(attempt, witness)
        self.events.append("CREATED")
        if self.fail_created:
            raise RuntimeError("journal created write failed")
        self.attempt = attempt

    async def cleanup_started(self, attempt):
        assert self.attempt is not None
        assert replace(self.attempt, container_id=attempt.container_id) == attempt
        self.events.append("CLEANUP_STARTED")
        if self.fail_started:
            raise RuntimeError("journal fence unavailable")

    async def cleaned(self, attempt, witness):
        self.daemon.supervisor.verify_cleaned(attempt, witness)
        self.events.append("CLEANED")
        assert not self.daemon.present
        if self.fail_cleaned:
            raise RuntimeError("journal cleanup publication failed")

    async def authorize_recovery(self, attempt):
        if attempt != self.attempt:
            raise RoboticsError("LEASE_INVALID")
        self.events.append("RECOVERY_AUTHORIZED")


class DurableDaemon(FakeDaemon):
    def __init__(self, tmp_path, mode="valid"):
        super().__init__(tmp_path, mode)
        self.bootstrap = tmp_path / "bootstrap.json"
        self.bootstrap.write_bytes(b'{"fixture":"HOST_CONSTRUCTION_ONLY"}')
        self.bootstrap.chmod(0o444)
        self.journal = Journal(self)
        self.supervisor._journal = self.journal
        self.stuck_pid = False

    async def command(self, arguments, *, allow_failure=False):
        if arguments[:2] == ["container", "kill"]:
            assert "CLEANUP_STARTED" in self.journal.events
            self.commands.append(arguments)
            self.row["State"]["Running"] = False
            self.row["State"]["Pid"] = 19 if self.stuck_pid else 0
            return b""
        if arguments[:2] == ["container", "rm"] and len(arguments) == 3:
            self.commands.append(arguments)
            self.present = False
            return b""
        if arguments[:2] == ["container", "create"]:
            assert self.journal.events == ["PLANNED"]
        return await super().command(arguments, allow_failure=allow_failure)

    async def json(self, arguments):
        if arguments[:2] == ["container", "inspect"] and not self.present:
            raise RoboticsError("ISOLATION_UNAVAILABLE")
        return await super().json(arguments)

    def restart(self):
        supervisor, _, _, _ = fixture()
        supervisor._profiles = self.supervisor._profiles.copy()
        supervisor._owned = {}
        supervisor._unresolved_creations = {}
        supervisor._creation_cleanup_tasks = set()
        supervisor._journal = self.journal
        supervisor._command, supervisor._json = self.command, self.json
        self.supervisor = supervisor


async def test_fresh_journal_precedes_create_and_preserves_exact_bootstrap(tmp_path):
    daemon = DurableDaemon(tmp_path)
    witness = await daemon.prepare()
    expected = hashlib.sha256(daemon.bootstrap.read_bytes()).hexdigest()
    assert daemon.journal.events == ["PLANNED", "CREATED"]
    assert witness.bootstrap_digest == daemon.journal.attempt.bootstrap_digest == expected
    assert witness.creation_name == daemon.journal.attempt.name
    daemon.supervisor.verify_created(daemon.journal.attempt, witness)
    with pytest.raises(RoboticsError):
        daemon.supervisor.verify_created(daemon.journal.attempt, replace(witness))
    cleaned = await daemon.supervisor.cleanup(witness)
    assert daemon.journal.events[-2:] == ["CLEANUP_STARTED", "CLEANED"]
    daemon.supervisor.verify_cleaned(daemon.journal.attempt, cleaned)
    with pytest.raises(RoboticsError):
        daemon.supervisor.verify_cleaned(daemon.journal.attempt, replace(cleaned))
    assert not daemon.supervisor._owned and not daemon.supervisor.unresolved_creations


@pytest.mark.parametrize("fresh", [False, None, 1, "true"])
async def test_nonfresh_or_untyped_journal_reply_cannot_create(tmp_path, fresh):
    daemon = DurableDaemon(tmp_path)
    daemon.journal.fresh = fresh
    with pytest.raises(RoboticsError):
        await daemon.prepare()
    assert not daemon.present and not daemon.commands


@pytest.mark.parametrize("change", ["missing", "symlink", "writable", "empty", "oversized"])
async def test_unpinnable_bootstrap_cannot_enter_journal_or_create(tmp_path, change):
    daemon = DurableDaemon(tmp_path)
    if change == "missing":
        daemon.bootstrap.unlink()
    elif change == "symlink":
        daemon.bootstrap.unlink()
        daemon.bootstrap.symlink_to("/dev/null")
    elif change == "writable":
        daemon.bootstrap.chmod(0o644)
    else:
        daemon.bootstrap.chmod(0o644)
        daemon.bootstrap.write_bytes(b"" if change == "empty" else b"x" * (1024 * 1024 + 1))
        daemon.bootstrap.chmod(0o444)
    with pytest.raises(RoboticsError):
        await daemon.prepare()
    assert not daemon.present and not daemon.journal.events


async def test_bootstrap_change_after_durable_begin_cannot_create(tmp_path):
    daemon = DurableDaemon(tmp_path)
    planned = daemon.journal.planned

    async def changed(attempt):
        result = await planned(attempt)
        daemon.bootstrap.chmod(0o644)
        daemon.bootstrap.write_bytes(b'{"changed":true}')
        daemon.bootstrap.chmod(0o444)
        return result

    daemon.journal.planned = changed
    with pytest.raises(RoboticsError):
        await daemon.prepare()
    assert daemon.journal.attempt is not None and not daemon.present
    assert not daemon.commands


async def test_failed_created_commit_still_stops_and_removes_container(tmp_path):
    daemon = DurableDaemon(tmp_path)
    daemon.journal.fail_created = True
    with pytest.raises(RuntimeError, match="created write"):
        await daemon.prepare()
    assert daemon.journal.events == ["PLANNED", "CREATED", "CLEANUP_STARTED", "CLEANED"]
    assert not daemon.present and not daemon.supervisor._owned


@pytest.mark.parametrize("failure", ["fail_started", "fail_cleaned"])
async def test_cleanup_journal_failure_never_prevents_stop_and_retains_uncertainty(
    tmp_path, failure
):
    daemon = DurableDaemon(tmp_path)
    witness = await daemon.prepare()
    setattr(daemon.journal, failure, True)
    with pytest.raises(RuntimeError):
        await daemon.supervisor.cleanup(witness)
    assert not daemon.present
    assert witness.container_id in daemon.supervisor._owned
    assert bool(daemon.supervisor._cleanup_witnesses)
    if failure == "fail_started":
        assert "CLEANED" not in daemon.journal.events
    observed = daemon.supervisor._cleanup_witnesses[daemon.profile.resource_id][1]
    setattr(daemon.journal, failure, False)
    before = len(daemon.commands), len(daemon.inspections)
    redelivered = await daemon.supervisor.cleanup(witness)
    assert redelivered is observed
    assert (len(daemon.commands), len(daemon.inspections)) == before
    assert not daemon.supervisor._owned


async def test_restart_authorizes_exact_record_and_only_cleans_up(tmp_path):
    daemon = DurableDaemon(tmp_path)
    await daemon.prepare()
    attempt = replace(daemon.journal.attempt)
    daemon.row["State"].update(Running=True, Pid=1234)
    daemon.restart()
    before = len(daemon.commands)
    cleaned = await daemon.supervisor.reconcile(attempt)
    assert cleaned.removed and not daemon.present
    assert daemon.journal.events[-3:] == ["RECOVERY_AUTHORIZED", "CLEANUP_STARTED", "CLEANED"]
    assert [command[1] for command in daemon.commands[before:]] == ["kill", "rm", "ls"]


@pytest.mark.parametrize("field", ["host_instance_id", "image_id", "bootstrap_digest", "name"])
async def test_forged_restart_record_denies_before_daemon_io(tmp_path, field):
    daemon = DurableDaemon(tmp_path)
    await daemon.prepare()
    attempt = replace(daemon.journal.attempt, **{field: "forged"})
    daemon.restart()
    before = len(daemon.commands), len(daemon.inspections)
    with pytest.raises(RoboticsError):
        await daemon.supervisor.reconcile(attempt)
    assert (len(daemon.commands), len(daemon.inspections)) == before and daemon.present


@pytest.mark.parametrize("conflict", ["owner", "image", "name", "configured_image"])
async def test_restart_does_not_delete_substituted_container(tmp_path, conflict):
    daemon = DurableDaemon(tmp_path)
    await daemon.prepare()
    daemon.restart()
    daemon.conflict = conflict
    before = len(daemon.commands)
    with pytest.raises(RoboticsError):
        await daemon.supervisor.reconcile(daemon.journal.attempt)
    assert len(daemon.commands) == before and daemon.present
    assert "CLEANED" not in daemon.journal.events


async def test_unknown_cid_absence_never_confirms_create_settled(tmp_path):
    daemon = DurableDaemon(tmp_path, "lost")
    daemon.cleanup_failure = "inspect"
    with pytest.raises(TimeoutError):
        await daemon.prepare()
    assert daemon.journal.attempt.container_id is None
    daemon.present = False
    daemon.restart()
    with pytest.raises(RoboticsError):
        await daemon.supervisor.reconcile(daemon.journal.attempt)
    assert "CLEANED" not in daemon.journal.events


async def test_pid_still_present_prevents_removal_confirmation(tmp_path):
    daemon = DurableDaemon(tmp_path)
    witness = await daemon.prepare()
    daemon.stuck_pid = True
    with pytest.raises(RoboticsError):
        await daemon.supervisor.cleanup(witness)
    assert daemon.present and "CLEANED" not in daemon.journal.events


async def test_bootstrap_change_after_create_prevents_start(tmp_path):
    daemon = DurableDaemon(tmp_path)
    witness = await daemon.prepare()
    daemon.bootstrap.chmod(0o644)
    daemon.bootstrap.write_bytes(b'{"changed":true}')
    daemon.bootstrap.chmod(0o444)
    before = len(daemon.commands), len(daemon.inspections)
    with pytest.raises(RoboticsError):
        await daemon.supervisor.start(witness)
    assert (len(daemon.commands), len(daemon.inspections)) == before


async def test_stubborn_journal_cancellation_cannot_delay_cleanup_forever(tmp_path):
    daemon = DurableDaemon(tmp_path)
    witness = await daemon.prepare()
    release = asyncio.Event()
    original = daemon.journal.cleanup_started

    async def stubborn(attempt):
        await original(attempt)
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()

    daemon.journal.cleanup_started = stubborn
    try:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(3):
                await daemon.supervisor.cleanup(witness)
        assert not daemon.present and "CLEANED" not in daemon.journal.events
    finally:
        release.set()
        await asyncio.sleep(0)


async def test_restart_without_durable_authority_is_refused(tmp_path):
    daemon = DurableDaemon(tmp_path)
    await daemon.prepare()
    daemon.supervisor._journal = None
    before = len(daemon.commands), len(daemon.inspections)
    with pytest.raises(RoboticsError):
        await DockerSupervisor.reconcile(daemon.supervisor, daemon.journal.attempt)
    assert (len(daemon.commands), len(daemon.inspections)) == before
