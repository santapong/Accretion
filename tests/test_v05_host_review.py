"""Failure-ordering construction witnesses: real child pipes, mocked Docker only."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
import test_v05_host_transport as transport_tests
from test_v05_host_docker_profile import fixture as docker_fixture

from accretion.contracts.canonical import canonical_json
from accretion.contracts.robotics.values import LeaseBinding
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.host.docker import LABEL
from accretion.robotics.protocol import HeartbeatRequest


@pytest.mark.parametrize("terminal", ["stderr", "close"])
async def test_completion_cannot_return_success_after_terminal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal: str
) -> None:
    if terminal == "stderr":
        # A valid real acknowledgement precedes overflow, so completion is
        # already awaiting storage when the independent stderr reader trips.
        child = transport_tests.CHILD
        marker = "    sys.stdout.buffer.flush()\n"
        position = child.rfind(marker)
        assert position != -1
        child = child[:position] + child[position:].replace(
            marker,
            marker
            + "    time.sleep(0.05)\n"
            + '    sys.stderr.buffer.write(b"x" * 131072); sys.stderr.buffer.flush()\n',
            1,
        )
        monkeypatch.setattr(transport_tests, "CHILD", child)
    harness, transport, admissions, completed, failures = await transport_tests.setup(
        tmp_path, "normal"
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def complete(evidence):
        entered.set()
        await release.wait()
        # Deliberately weaker than the documented durable collaborator contract:
        # transport must still refuse OK if this callback returns after abort.
        completed.append(evidence)

    transport.complete = complete
    request = asyncio.create_task(
        transport.request(harness.request(HeartbeatRequest()), harness.session.pins)
    )
    close_task = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        if terminal == "close":
            close_task = asyncio.create_task(transport.close())
        await asyncio.wait_for(transport.process.wait(), timeout=3)
        assert transport.closed and admissions
        assert len(failures) == 1
        release.set()
        with pytest.raises(RoboticsError) as exc:
            await asyncio.wait_for(request, timeout=3)
        expected = (
            Code.RESOURCE_CAP_EXHAUSTED if terminal == "stderr" else Code.ACKNOWLEDGEMENT_UNCERTAIN
        )
        assert exc.value.code is expected
        assert len(completed) == 1 and len(failures) == 1
        assert failures[0][0].response_bytes == completed[0].response_bytes
    finally:
        release.set()
        if not request.done():
            request.cancel()
        await asyncio.gather(request, return_exceptions=True)
        if close_task is not None:
            await close_task
        else:
            await transport.close()


class FakeDaemon:
    """Models daemon-side creation followed by a lost/corrupt CLI response."""

    def __init__(self, tmp_path: Path, mode: str = "lost"):
        self.supervisor, self.profile, self.row, _ = docker_fixture()
        self.supervisor._profiles = {self.profile.resource_id: canonical_json(self.profile)}
        self.supervisor._owned = {}
        self.supervisor._unresolved_creations = {}
        self.supervisor._creation_cleanup_tasks = set()
        self.supervisor._json = self.json
        self.supervisor._command = self.command
        self.directory = tmp_path.resolve()
        self.directory.chmod(0o700)
        self.row["Mounts"][0]["Source"] = str(self.directory)
        self.mode = mode
        self.commands = []
        self.inspections = []
        self.created = asyncio.Event()
        self.removing = asyncio.Event()
        self.release_remove = asyncio.Event()
        self.release_remove.set()
        self.present = False
        self.name = None
        self.conflict = None
        self.cleanup_failure = None
        self.lease = LeaseBinding(lease_id="sle_" + "0" * 26, generation=1)

    async def json(self, arguments):
        self.inspections.append(arguments)
        if arguments[0] == "info":
            value = {
                "OSType": "linux",
                "CgroupVersion": "2",
                "SecurityOptions": ["name=seccomp,profile=builtin", "name=apparmor"],
            }
        elif arguments[:2] == ["image", "inspect"]:
            value = [
                {"Id": self.profile.image_id, "Os": "linux", "Architecture": "amd64", "Config": {}}
            ]
        else:
            assert arguments[:2] == ["container", "inspect"]
            if self.cleanup_failure == "inspect":
                raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
            if self.conflict == "owner":
                self.row["Config"]["Labels"][LABEL] = "unrelated-supervisor"
            elif self.conflict == "image":
                self.row["Image"] = "sha256:" + "0" * 64
            elif self.conflict == "name":
                self.row["Name"] = "/unrelated-container"
            elif self.conflict == "configured_image":
                self.row["Config"]["Image"] = "sha256:" + "0" * 64
            value = [self.row]
        return value, canonical_json(value)

    async def command(self, arguments, *, allow_failure=False):
        self.commands.append(arguments)
        if arguments[:2] == ["container", "create"]:
            self.name = arguments[arguments.index("--name") + 1]
            self.row["Name"] = "/" + self.name
            self.present = True
            self.created.set()
            if self.mode == "cancel":
                await asyncio.Future()
            if self.mode == "lost":
                raise TimeoutError("created, then CLI reply lost")
            if self.mode == "non_ascii":
                return b"\xff\n"
            if self.mode == "invalid_ascii":
                return b"invalid-container-id\n"
            if self.mode == "invalid_inspection":
                self.row["HostConfig"]["ReadonlyRootfs"] = False
            return self.row["Id"].encode() + b"\n"
        if arguments[:2] == ["container", "rm"]:
            assert arguments == ["container", "rm", "--force", self.row["Id"]]
            self.removing.set()
            await self.release_remove.wait()
            if self.cleanup_failure == "remove":
                raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
            if self.cleanup_failure != "remaining":
                self.present = False
            return b""
        assert arguments[:2] == ["container", "ls"]
        assert arguments[-1] == "id=" + self.row["Id"]
        return self.row["Id"].encode() if self.present else b""

    async def prepare(self):
        return await self.supervisor.prepare(
            self.profile,
            episode_id="synthetic-review-episode",
            lease=self.lease,
            bootstrap_directory=self.directory,
        )


@pytest.mark.parametrize("mode", ["lost", "non_ascii", "invalid_ascii", "invalid_inspection"])
async def test_failed_create_resolves_name_and_removes_only_verified_cid(tmp_path: Path, mode: str):
    daemon = FakeDaemon(tmp_path, mode)
    with pytest.raises((TimeoutError, UnicodeDecodeError, RoboticsError)):
        await daemon.prepare()
    assert ["container", "inspect", daemon.name] in daemon.inspections
    assert ["container", "rm", "--force", daemon.row["Id"]] in daemon.commands
    assert not daemon.present
    assert not daemon.supervisor.unresolved_creations and not daemon.supervisor._owned


async def test_cancelled_create_performs_verified_cleanup(tmp_path: Path):
    daemon = FakeDaemon(tmp_path, "cancel")
    task = asyncio.create_task(daemon.prepare())
    await asyncio.wait_for(daemon.created.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not daemon.present and not daemon.supervisor.unresolved_creations


async def test_repeated_cancellation_cannot_cancel_cleanup_or_forget_identity(tmp_path: Path):
    daemon = FakeDaemon(tmp_path, "cancel")
    daemon.release_remove.clear()
    task = asyncio.create_task(daemon.prepare())
    await asyncio.wait_for(daemon.created.wait(), timeout=1)
    task.cancel()
    await asyncio.wait_for(daemon.removing.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert daemon.present and len(daemon.supervisor.unresolved_creations) == 1
    cleanup_tasks = tuple(daemon.supervisor._creation_cleanup_tasks)
    assert len(cleanup_tasks) == 1 and not cleanup_tasks[0].cancelled()
    daemon.release_remove.set()
    await asyncio.wait_for(asyncio.gather(*cleanup_tasks), timeout=1)
    assert not daemon.present and not daemon.supervisor.unresolved_creations


@pytest.mark.parametrize("conflict", ["owner", "image", "configured_image", "name"])
async def test_uncertain_name_never_deletes_a_conflicting_container(tmp_path: Path, conflict: str):
    daemon = FakeDaemon(tmp_path)
    daemon.conflict = conflict
    with pytest.raises(TimeoutError):
        await daemon.prepare()
    assert daemon.present and not any(args[:2] == ["container", "rm"] for args in daemon.commands)
    (attempt,) = daemon.supervisor.unresolved_creations
    assert attempt.name == daemon.name and attempt.image_id == daemon.profile.image_id
    assert attempt.host_instance_id == daemon.supervisor.instance_id
    assert attempt.episode_id == "synthetic-review-episode"
    assert attempt.lease_bytes == canonical_json(daemon.lease)
    assert attempt.bootstrap_directory == daemon.directory


@pytest.mark.parametrize("failure", ["inspect", "remove", "remaining"])
async def test_unconfirmed_cleanup_retains_creation_identity(tmp_path: Path, failure: str):
    daemon = FakeDaemon(tmp_path)
    daemon.cleanup_failure = failure
    with pytest.raises(TimeoutError):
        await daemon.prepare()
    (attempt,) = daemon.supervisor.unresolved_creations
    assert attempt.name == daemon.name and daemon.present
    assert not daemon.supervisor._owned
    # A later trusted in-process reconciliation can recheck this same identity;
    # this does not test persistent supervisor restart recovery.
    daemon.cleanup_failure = None
    await daemon.supervisor._cleanup_uncertain_creation(attempt)
    assert not daemon.present and not daemon.supervisor.unresolved_creations


async def test_successful_creation_moves_from_unresolved_to_owned(tmp_path: Path):
    daemon = FakeDaemon(tmp_path, "valid")
    witness = await daemon.prepare()
    assert witness.container_id == daemon.row["Id"] and daemon.present
    assert daemon.supervisor._owned[witness.container_id] is witness
    assert not daemon.supervisor.unresolved_creations
    assert not any(args[:2] == ["container", "rm"] for args in daemon.commands)


async def test_reconciliation_rejects_forged_attempt_before_daemon_call(tmp_path: Path):
    daemon = FakeDaemon(tmp_path)
    daemon.cleanup_failure = "inspect"
    with pytest.raises(TimeoutError):
        await daemon.prepare()
    (attempt,) = daemon.supervisor.unresolved_creations
    before = len(daemon.inspections), len(daemon.commands)
    with pytest.raises(RoboticsError) as exc:
        await daemon.supervisor._cleanup_uncertain_creation(replace(attempt))
    assert exc.value.code is Code.LEASE_INVALID
    assert (len(daemon.inspections), len(daemon.commands)) == before
