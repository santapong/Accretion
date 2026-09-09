"""Local Linux Docker supervisor for an internal immutable simulation inventory.

Profiles are trusted deployment configuration, never adapter manifest commands
or public API parameters. Only a pinned local image is admitted; runtime launch
cannot pull/build images. Code and models live in the read-only image. The sole
bind mount is the current lease's private bootstrap/IPC directory, also read-only.
No Docker socket, physical device, host network or global artifact root reaches
the worker. Inspection is required before attach starts its entrypoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import signal
import stat
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import Field

from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics.values import Digest, Identifier, LeaseBinding
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.protocol import MAX_FRAME_BYTES, WireModel, bounded_json

ENTRYPOINT = "/opt/accretion/bin/simulation-worker"
LABEL = "dev.draveniq.accretion.simulation-host"
_CID = re.compile(r"[0-9a-f]{64}\Z")
_ENVIRONMENT = {
    "HOME": "/tmp",
    "TMPDIR": "/tmp",
    "PYTHONUNBUFFERED": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "MUJOCO_GL": "egl",
    "PYOPENGL_PLATFORM": "egl",
    "LIBGL_ALWAYS_SOFTWARE": "1",
    "MESA_LOADER_DRIVER_OVERRIDE": "llvmpipe",
    "EGL_PLATFORM": "surfaceless",
    "LP_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
}


def _require(condition: bool) -> None:
    if not condition:
        raise RoboticsError(Code.ISOLATION_UNAVAILABLE)


class HostLimits(WireModel):
    cpu_millicores: int = Field(ge=100, le=2000, strict=True)
    memory_bytes: int = Field(ge=64 * 1024**2, le=4 * 1024**3, strict=True)
    temporary_bytes: int = Field(ge=1024**2, le=1024**3, strict=True)
    shared_memory_bytes: int = Field(ge=1024**2, le=256 * 1024**2, strict=True)
    pids: int = Field(ge=8, le=128, strict=True)
    cpu_seconds: int = Field(ge=1, le=86400, strict=True)
    wall_seconds: int = Field(ge=1, le=86400, strict=True)


class LaunchProfile(WireModel):
    resource_id: Identifier
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$", strict=True)
    adapter_artifact_digest: Digest
    model_bundle_digest: Digest
    worker_kind: Literal["UR5E", "PANDA", "CONFORMANCE"]
    limits: HostLimits


@dataclass(frozen=True)
class ContainerWitness:
    container_id: str
    host_instance_id: str
    profile_bytes: bytes
    profile_hash: str
    episode_id: str
    lease_bytes: bytes
    bootstrap_directory: Path
    daemon_bytes: bytes
    image_bytes: bytes
    inspection_bytes: bytes
    creation_name: str | None = None
    bootstrap_digest: str | None = None

    @property
    def lease(self) -> LeaseBinding:
        return LeaseBinding.model_validate_json(self.lease_bytes)


@dataclass(frozen=True)
class CleanupWitness:
    container_id: str
    host_instance_id: str
    episode_id: str
    lease_bytes: bytes
    removed: bool
    stopped_inspection: bytes

    @property
    def lease(self) -> LeaseBinding:
        return LeaseBinding.model_validate_json(self.lease_bytes)


@dataclass(frozen=True)
class CreationAttempt:
    """Local reconciliation identity, never durable restart or deletion authority.

    Production composition must persist attempts before daemon invocation and
    reconcile them after supervisor restart. This in-memory record cannot prove
    that a failed/timed-out create did not create a container.
    """

    name: str
    host_instance_id: str
    image_id: str
    profile_bytes: bytes
    episode_id: str
    lease_bytes: bytes
    bootstrap_directory: Path
    container_id: str | None = None
    bootstrap_digest: str | None = None


class CreationJournal(Protocol):
    """Scoped durable collaborator; every callback owns its own transaction.

    A construction supervisor may omit this interface. Restart recovery and the
    integrated host path require it. False/historical planned receipts cannot
    authorize Docker create. Witness callbacks authenticate supervisor-issued
    objects; serialized labels alone never prove actual host observation.
    """

    async def planned(self, attempt: CreationAttempt) -> bool: ...

    async def created(self, attempt: CreationAttempt, witness: ContainerWitness) -> None: ...

    async def starting(self, attempt: CreationAttempt, witness: ContainerWitness) -> datetime:
        """Fresh current authority only; return its earliest permitted start deadline."""
        ...

    async def cleanup_started(self, attempt: CreationAttempt) -> None: ...

    async def cleaned(self, attempt: CreationAttempt, witness: CleanupWitness) -> None: ...

    async def authorize_recovery(self, attempt: CreationAttempt) -> None: ...


class DockerSupervisor:
    def __init__(
        self,
        *,
        instance_id: str,
        profiles: Sequence[LaunchProfile],
        docker_path: Path = Path("/usr/bin/docker"),
        journal: CreationJournal | None = None,
    ):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", instance_id):
            raise ValueError("invalid host instance identity")
        if not docker_path.is_absolute() or docker_path.resolve() != docker_path:
            raise ValueError("Docker executable must be a trusted absolute file")
        info = docker_path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("Docker executable must be root-owned and nonwritable")
        if os.geteuid() == 0:
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
        self.instance_id, self.docker_path = instance_id, docker_path
        self._journal = journal
        self._cleanup_witnesses: dict[str, tuple[CreationAttempt, CleanupWitness]] = {}
        self._profiles: dict[str, bytes] = {}
        for profile in profiles:
            raw = canonical_json(profile)
            parsed = LaunchProfile.model_validate_json(raw)
            if parsed.resource_id in self._profiles:
                raise ValueError("duplicate admitted resource")
            self._profiles[parsed.resource_id] = raw
        self._owned: dict[str, ContainerWitness] = {}
        self._start_claims: set[str] = set()
        self._unresolved_creations: dict[str, CreationAttempt] = {}
        self._creation_cleanup_tasks: set[asyncio.Task[None]] = set()

    @property
    def unresolved_creations(self) -> tuple[CreationAttempt, ...]:
        """Unconfirmed local creates; callers must not treat these as removed."""
        return tuple(self._unresolved_creations.values())

    @staticmethod
    async def _journal_wait[T](operation: Awaitable[T]) -> T:
        # Each callback owns its transaction task. A storage callback that is
        # slow to cancel cannot keep the container alive beyond this wait.
        task = asyncio.ensure_future(operation)
        try:
            done, _ = await asyncio.wait((task,), timeout=2)
            if not done:
                raise TimeoutError("host journal acknowledgement unavailable")
            return task.result()
        finally:
            if not task.done():
                task.cancel()
                task.add_done_callback(lambda done: None if done.cancelled() else done.exception())

    def _creation_cleanup_finished(self, task: asyncio.Task[None]) -> None:
        self._creation_cleanup_tasks.discard(task)
        # Cleanup failures leave the immutable attempt in unresolved_creations.
        # Retrieve exceptions even if a repeated caller cancellation detached it.
        if not task.cancelled():
            task.exception()

    async def _cleanup_uncertain_creation(self, attempt: CreationAttempt) -> None:
        """Resolve only this attempt's exact name, then remove the verified CID.

        Missing/unreadable inspection does not prove the daemon's create has
        settled. Keep the attempt for later reconciliation in that case.
        """
        if (
            self._unresolved_creations.get(attempt.name) is not attempt
            or attempt.host_instance_id != self.instance_id
        ):
            raise RoboticsError(Code.LEASE_INVALID)
        if self._journal is not None:
            await self._cleanup_durable(attempt)
            return
        entries, _ = await self._json(["container", "inspect", attempt.name])
        try:
            _require(isinstance(entries, list) and len(entries) == 1)
            row = entries[0]
            cid = row["Id"]
            _require(isinstance(cid, str) and _CID.fullmatch(cid) is not None)
            _require(row["Name"] == "/" + attempt.name)
            _require(row["Config"]["Labels"][LABEL] == attempt.host_instance_id)
            _require(row["Image"] == row["Config"]["Image"] == attempt.image_id)
            _require(attempt.container_id is None or attempt.container_id == cid)
        except (KeyError, TypeError) as exc:
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE) from exc
        # Never remove by name: it may be rebound between inspection and removal.
        await self._command(["container", "rm", "--force", cid])
        remaining = await self._command(
            ["container", "ls", "--all", "--quiet", "--no-trunc", "--filter", f"id={cid}"]
        )
        if remaining.strip():
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
        if self._unresolved_creations.get(attempt.name) is attempt:
            del self._unresolved_creations[attempt.name]

    @property
    def prefix(self) -> list[str]:
        # Ignore inherited context, remote daemon and credential configuration.
        return [str(self.docker_path), "--host", "unix:///var/run/docker.sock"]

    async def _spawn(self, arguments: list[str]) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *self.prefix,
            *arguments,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_FRAME_BYTES + 1,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            start_new_session=True,
        )

    async def _command(self, arguments: list[str], *, allow_failure: bool = False) -> bytes:
        process = await self._spawn(arguments)
        assert process.stdout is not None and process.stderr is not None

        async def bounded(reader: asyncio.StreamReader) -> bytes:
            out = bytearray()
            while chunk := await reader.read(65536):
                out.extend(chunk)
                if len(out) > 4 * MAX_FRAME_BYTES:
                    raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
            return bytes(out)

        try:
            async with asyncio.timeout(30):
                stdout, _ = await asyncio.gather(bounded(process.stdout), bounded(process.stderr))
                result = await process.wait()
                if result and not allow_failure:
                    raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
                return stdout
        except BaseException:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
            raise

    async def _json(self, arguments: list[str]) -> tuple[Any, bytes]:
        raw = await self._command(arguments)
        try:
            return bounded_json(raw, max_bytes=4 * MAX_FRAME_BYTES), raw
        except RoboticsError as exc:
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE) from exc

    async def prepare(
        self,
        profile: LaunchProfile,
        *,
        episode_id: str,
        lease: LeaseBinding,
        bootstrap_directory: Path,
    ) -> ContainerWitness:
        profile_bytes = canonical_json(profile)
        profile = LaunchProfile.model_validate_json(profile_bytes)
        if self._profiles.get(profile.resource_id) != profile_bytes:
            raise RoboticsError(Code.PHYSICAL_ENDPOINT_DENIED)
        lease = LeaseBinding.model_validate_json(canonical_json(lease))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", episode_id):
            raise RoboticsError(Code.INVALID_REQUEST)
        self._private_directory(bootstrap_directory)
        daemon, daemon_bytes = await self._json(["info", "--format", "{{json .}}"])
        if not isinstance(daemon, dict) or (
            daemon.get("OSType") != "linux"
            or daemon.get("CgroupVersion") != "2"
            or not any("seccomp" in x for x in daemon.get("SecurityOptions", []))
            or not any("apparmor" in x for x in daemon.get("SecurityOptions", []))
        ):
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
        images, image_bytes = await self._json(["image", "inspect", profile.image_id])
        if not isinstance(images, list) or len(images) != 1:
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
        image = images[0]
        if (
            image.get("Id") != profile.image_id
            or image.get("Os") != "linux"
            or image.get("Architecture") != "amd64"
            or image.get("Config", {}).get("Volumes")
        ):
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
        name = "accretion-sim-" + uuid4().hex
        arguments = self.create_arguments(profile, name=name, directory=bootstrap_directory)
        attempt = CreationAttempt(
            name,
            self.instance_id,
            profile.image_id,
            profile_bytes,
            episode_id,
            canonical_json(lease),
            bootstrap_directory,
            bootstrap_digest=(
                self._bootstrap_digest(bootstrap_directory) if self._journal is not None else None
            ),
        )
        if self._journal is not None:
            # A committed historical begin is not permission to repeat create.
            fresh = await self._journal_wait(self._journal.planned(attempt))
            if fresh is not True:
                raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
            _require(attempt.bootstrap_digest == self._bootstrap_digest(bootstrap_directory))
        self._unresolved_creations[name] = attempt
        try:
            cid = (await self._command(arguments)).decode("ascii").strip()
            if not _CID.fullmatch(cid):
                raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
            attempt = replace(attempt, container_id=cid)
            self._unresolved_creations[name] = attempt
            entries, inspect_bytes = await self._json(["container", "inspect", cid])
            self.validate_inspection(entries, profile, cid=cid, directory=bootstrap_directory)
            witness = ContainerWitness(
                cid,
                self.instance_id,
                profile_bytes,
                content_hash(profile, exclude=()),
                episode_id,
                canonical_json(lease),
                bootstrap_directory,
                daemon_bytes,
                image_bytes,
                inspect_bytes,
                creation_name=name,
                bootstrap_digest=attempt.bootstrap_digest,
            )
            self._owned[cid] = witness
            if self._journal is not None:
                await self._journal_wait(self._journal.created(attempt, witness))
            del self._unresolved_creations[name]
            return witness
        except BaseException:
            cleanup_task = asyncio.create_task(self._cleanup_uncertain_creation(attempt))
            self._creation_cleanup_tasks.add(cleanup_task)
            cleanup_task.add_done_callback(self._creation_cleanup_finished)
            try:
                # Preserve cleanup across repeated cancellation. Its daemon
                # calls remain bounded; the attempt stays unresolved on failure.
                await asyncio.shield(cleanup_task)
            except BaseException:
                pass
            raise

    @staticmethod
    def _bootstrap_digest(directory: Path) -> str:
        """Hash a bounded immutable bootstrap without following the final link."""
        try:
            fd = os.open(directory / "bootstrap.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                before = os.fstat(fd)
                _require(
                    stat.S_ISREG(before.st_mode)
                    and before.st_uid == os.geteuid()
                    and not before.st_mode & 0o222
                    and 0 < before.st_size <= MAX_FRAME_BYTES
                )
                raw = os.read(fd, MAX_FRAME_BYTES + 1)
                after = os.fstat(fd)
                _require(
                    len(raw) == before.st_size
                    and (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns)
                    == (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns)
                    and before.st_size == after.st_size
                )
                return hashlib.sha256(raw).hexdigest()
            finally:
                os.close(fd)
        except OSError as exc:
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE) from exc

    @staticmethod
    def _private_directory(directory: Path) -> None:
        info = directory.lstat()
        if (
            directory.resolve() != directory
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or "," in str(directory)
            or "\n" in str(directory)
        ):
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
        # Only known sockets and one immutable bootstrap document are exposed.
        for child in directory.iterdir():
            info = child.lstat()
            if info.st_uid != os.geteuid():
                raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
            if child.name in {"authority.sock", "artifacts.sock"} and stat.S_ISSOCK(info.st_mode):
                continue
            if (
                child.name == "bootstrap.json"
                and stat.S_ISREG(info.st_mode)
                and not info.st_mode & 0o222
            ):
                continue
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE)

    def create_arguments(self, profile: LaunchProfile, *, name: str, directory: Path) -> list[str]:
        if not re.fullmatch(r"accretion-sim-[0-9a-f]{32}", name):
            raise RoboticsError(Code.INVALID_REQUEST)
        limits = profile.limits
        args = [
            "container",
            "create",
            "--name",
            name,
            "--pull",
            "never",
            "--interactive",
            "--read-only",
            "--network",
            "none",
            "--ipc",
            "private",
            "--cgroupns",
            "private",
            "--runtime",
            "runc",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--security-opt",
            "apparmor=docker-default",
            "--user",
            f"{os.geteuid()}:{os.getegid()}",
            "--memory",
            str(limits.memory_bytes),
            "--memory-swap",
            str(limits.memory_bytes),
            "--cpus",
            str(limits.cpu_millicores / 1000),
            "--pids-limit",
            str(limits.pids),
            "--ulimit",
            f"cpu={limits.cpu_seconds}:{limits.cpu_seconds}",
            "--ulimit",
            "core=0:0",
            "--ulimit",
            "nofile=256:256",
            "--shm-size",
            str(limits.shared_memory_bytes),
            "--restart",
            "no",
            "--no-healthcheck",
            "--log-driver",
            "none",
            "--stop-timeout",
            "1",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={limits.temporary_bytes},mode=1777",
            "--mount",
            f"type=bind,source={directory},target=/run/accretion,readonly,bind-propagation=rprivate",
            "--workdir",
            "/tmp",
            "--label",
            f"{LABEL}={self.instance_id}",
            "--entrypoint",
            ENTRYPOINT,
        ]
        for key, value in sorted(_ENVIRONMENT.items()):
            args.extend(["--env", f"{key}={value}"])
        args.extend([profile.image_id, "--bootstrap", "/run/accretion/bootstrap.json"])
        return args

    def validate_inspection(
        self,
        entries: Any,
        profile: LaunchProfile,
        *,
        cid: str,
        directory: Path,
        running: bool = False,
    ) -> None:
        try:
            _require(isinstance(entries, list) and len(entries) == 1)
            row = entries[0]
            config, host = row["Config"], row["HostConfig"]
            _require(row["Id"] == cid and row["Image"] == profile.image_id)
            _require(config["Image"] == profile.image_id)
            _require(config["Entrypoint"] == [ENTRYPOINT])
            _require(config["Cmd"] == ["--bootstrap", "/run/accretion/bootstrap.json"])
            _require(config["User"] == f"{os.geteuid()}:{os.getegid()}")
            _require(config["WorkingDir"] == "/tmp" and not config["Tty"])
            _require(config["OpenStdin"] and config["AttachStdout"] and config["AttachStderr"])
            _require(config["AttachStdin"] and config["StdinOnce"])
            environment = dict(value.split("=", 1) for value in config["Env"])
            _require(all(environment.get(key) == value for key, value in _ENVIRONMENT.items()))
            _require(config["Labels"][LABEL] == self.instance_id)
            _require(row["State"]["Running"] is running)
            _require(not row["State"]["Paused"] and not row["State"]["Restarting"])
            _require(row["RestartCount"] == 0 and not host["Privileged"])
            _require(host["ReadonlyRootfs"] and host["NetworkMode"] == "none")
            _require(host["IpcMode"] == "private" and host["CgroupnsMode"] == "private")
            _require(host["PidMode"] in ("", "private") and host["UTSMode"] in ("", "private"))
            _require(host["Runtime"] == "runc" and not host["CapAdd"])
            _require(host["CapDrop"] == ["ALL"])
            _require(
                set(host["SecurityOpt"]) == {"no-new-privileges=true", "apparmor=docker-default"}
            )
            _require(row["AppArmorProfile"] == "docker-default")
            _require(
                not host["Devices"] and not host["DeviceRequests"] and not host["DeviceCgroupRules"]
            )
            _require(not host["PortBindings"] and not host["PublishAllPorts"])
            _require(not host["Links"] and not host["VolumesFrom"] and not host["Binds"])
            _require(not host["ExtraHosts"] and not host["GroupAdd"])
            _require(host["Memory"] == profile.limits.memory_bytes == host["MemorySwap"])
            _require(host["NanoCpus"] == profile.limits.cpu_millicores * 1_000_000)
            _require(host["PidsLimit"] == profile.limits.pids)
            _require(host["ShmSize"] == profile.limits.shared_memory_bytes)
            _require(host["LogConfig"]["Type"] == "none")
            _require(host["RestartPolicy"]["Name"] == "no")
            _require(not host["OomKillDisable"] and not host["AutoRemove"])
            _require(not host.get("Sysctls") and host["UsernsMode"] in ("", "private"))
            _require(
                host["Ulimits"]
                == [
                    {"Name": "core", "Hard": 0, "Soft": 0},
                    {
                        "Name": "cpu",
                        "Hard": profile.limits.cpu_seconds,
                        "Soft": profile.limits.cpu_seconds,
                    },
                    {"Name": "nofile", "Hard": 256, "Soft": 256},
                ]
            )
            _require({"/proc/kcore", "/proc/keys", "/sys/firmware"} <= set(host["MaskedPaths"]))
            _require({"/proc/sys", "/proc/sysrq-trigger"} <= set(host["ReadonlyPaths"]))
            mounts = row["Mounts"]
            _require(len(mounts) == 1)
            mount = mounts[0]
            _require(mount["Type"] == "bind" and mount["Source"] == str(directory))
            _require(mount["Destination"] == "/run/accretion" and not mount["RW"])
            _require(mount["Propagation"] == "rprivate")
            _require(
                host["Tmpfs"]
                == {
                    "/tmp": (
                        f"rw,noexec,nosuid,nodev,size={profile.limits.temporary_bytes},mode=1777"
                    )
                }
            )
        except (AssertionError, KeyError, TypeError, ValueError) as exc:
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE) from exc

    async def start(self, witness: ContainerWitness) -> asyncio.subprocess.Process:
        self._require_owned(witness)
        # Claim synchronously before inspection/authority IO. An uncertain start
        # is cleanup-only; neither a concurrent caller nor an exact retry starts
        # this container a second time. Recovery never imports this capability.
        _require(witness.container_id not in self._start_claims)
        self._start_claims.add(witness.container_id)
        if self._journal is not None:
            _require(
                witness.bootstrap_digest == self._bootstrap_digest(witness.bootstrap_directory)
            )
        # Re-inspect immediately before start. Docker attach starts this exact
        # immutable image/container; it never receives an arbitrary command.
        entries, _ = await self._json(["container", "inspect", witness.container_id])
        self.validate_inspection(
            entries,
            LaunchProfile.model_validate_json(witness.profile_bytes),
            cid=witness.container_id,
            directory=witness.bootstrap_directory,
        )
        if self._journal is not None:
            deadline = await self._journal_wait(
                self._journal.starting(self._attempt(witness), witness)
            )
            _require(isinstance(deadline, datetime) and deadline.tzinfo is not None)
            _require(
                witness.bootstrap_digest == self._bootstrap_digest(witness.bootstrap_directory)
            )
            _require(datetime.now(UTC) < deadline)
        return await self._spawn(
            ["container", "start", "--attach", "--interactive", witness.container_id]
        )

    def _require_owned(self, witness: ContainerWitness) -> None:
        if (
            self._owned.get(witness.container_id) is not witness
            or witness.host_instance_id != self.instance_id
        ):
            raise RoboticsError(Code.LEASE_INVALID)

    async def cleanup(self, witness: ContainerWitness) -> CleanupWitness:
        self._require_owned(witness)
        if self._journal is not None:
            return await self._cleanup_durable(self._attempt(witness))
        cid = witness.container_id
        # kill targets Docker's container cgroup, not only the local CLI PID.
        await self._command(["container", "kill", "--signal", "KILL", cid], allow_failure=True)
        entries, stopped = await self._json(["container", "inspect", cid])
        try:
            _require(len(entries) == 1 and entries[0]["Id"] == cid)
            _require(entries[0]["Config"]["Labels"][LABEL] == self.instance_id)
            _require(not entries[0]["State"]["Running"] and entries[0]["State"]["Pid"] == 0)
        except (AssertionError, KeyError, TypeError) as exc:
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE) from exc
        await self._command(["container", "rm", cid])
        remaining = await self._command(
            ["container", "ls", "--all", "--quiet", "--no-trunc", "--filter", f"id={cid}"]
        )
        if remaining.strip():
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
        del self._owned[cid]
        self._start_claims.discard(cid)
        return CleanupWitness(
            cid, self.instance_id, witness.episode_id, witness.lease_bytes, True, stopped
        )

    @staticmethod
    def _attempt(witness: ContainerWitness) -> CreationAttempt:
        _require(witness.creation_name is not None and witness.bootstrap_digest is not None)
        assert witness.creation_name is not None
        profile = LaunchProfile.model_validate_json(witness.profile_bytes)
        return CreationAttempt(
            witness.creation_name,
            witness.host_instance_id,
            profile.image_id,
            witness.profile_bytes,
            witness.episode_id,
            witness.lease_bytes,
            witness.bootstrap_directory,
            witness.container_id,
            witness.bootstrap_digest,
        )

    def verify_created(self, attempt: CreationAttempt, witness: ContainerWitness) -> None:
        """No-IO journal validator for an exact locally observed create witness."""
        self._require_owned(witness)
        _require(self._attempt(witness) == replace(attempt, container_id=witness.container_id))

    def verify_cleaned(self, attempt: CreationAttempt, witness: CleanupWitness) -> None:
        """No-IO journal validator; copied JSON/dataclasses are not host proof."""
        profile = LaunchProfile.model_validate_json(attempt.profile_bytes)
        observed = self._cleanup_witnesses.get(profile.resource_id)
        _require(
            observed is not None
            and observed[1] is witness
            and replace(observed[0], container_id=witness.container_id)
            == replace(attempt, container_id=witness.container_id)
        )
        _require(
            witness.removed
            and witness.host_instance_id == attempt.host_instance_id == self.instance_id
            and witness.episode_id == attempt.episode_id
            and witness.lease_bytes == attempt.lease_bytes
            and attempt.container_id in {None, witness.container_id}
        )

    async def reconcile(self, attempt: CreationAttempt) -> CleanupWitness:
        """Authenticate a persisted attempt, then only stop/remove its exact CID.

        Missing/unreadable names stay unresolved. This method never attaches,
        starts a worker or resumes protocol execution after supervisor restart.
        """
        _require(self._journal is not None)
        assert self._journal is not None
        await self._journal_wait(self._journal.authorize_recovery(attempt))
        return await self._cleanup_durable(attempt)

    async def _cleanup_durable(self, attempt: CreationAttempt) -> CleanupWitness:
        _require(self._journal is not None)
        assert self._journal is not None
        profile = LaunchProfile.model_validate_json(attempt.profile_bytes)
        _require(
            attempt.host_instance_id == self.instance_id
            and self._profiles.get(profile.resource_id) == attempt.profile_bytes
            and attempt.image_id == profile.image_id
            and re.fullmatch(r"accretion-sim-[0-9a-f]{32}", attempt.name) is not None
            and attempt.bootstrap_digest is not None
        )
        # Database outage cannot leave an already-owned child executing. The
        # attempted fence is bounded; failure remains durable uncertainty even
        # if stopping/removal subsequently succeeds.
        journal_failure: BaseException | None = None
        try:
            await self._journal_wait(self._journal.cleanup_started(attempt))
        except BaseException as exc:
            journal_failure = exc
        observed = self._cleanup_witnesses.get(profile.resource_id)
        if observed is not None:
            old_attempt, old_witness = observed
            if replace(old_attempt, container_id=old_witness.container_id) == replace(
                attempt, container_id=old_witness.container_id
            ):
                # A durable acknowledgement may be lost after actual removal.
                # Redeliver only this exact issued proof; do not inspect an
                # absent container or manufacture a replacement observation.
                self.verify_cleaned(attempt, old_witness)
                if journal_failure is not None:
                    raise journal_failure
                await self._journal_wait(self._journal.cleaned(attempt, old_witness))
                self._owned.pop(old_witness.container_id, None)
                self._start_claims.discard(old_witness.container_id)
                self._unresolved_creations.pop(attempt.name, None)
                return old_witness
        entries, _ = await self._json(
            ["container", "inspect", attempt.container_id or attempt.name]
        )
        try:
            _require(isinstance(entries, list) and len(entries) == 1)
            row = entries[0]
            cid = row["Id"]
            _require(isinstance(cid, str) and _CID.fullmatch(cid) is not None)
            _require(row["Name"] == "/" + attempt.name)
            _require(attempt.container_id in {None, cid})
            self.validate_inspection(
                entries,
                profile,
                cid=cid,
                directory=attempt.bootstrap_directory,
                running=row["State"]["Running"],
            )
        except (KeyError, TypeError) as exc:
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE) from exc
        await self._command(["container", "kill", "--signal", "KILL", cid], allow_failure=True)
        stopped_entries, stopped = await self._json(["container", "inspect", cid])
        try:
            _require(
                len(stopped_entries) == 1
                and stopped_entries[0]["Id"] == cid
                and stopped_entries[0]["Config"]["Labels"][LABEL] == self.instance_id
                and not stopped_entries[0]["State"]["Running"]
                and stopped_entries[0]["State"]["Pid"] == 0
            )
        except (KeyError, TypeError) as exc:
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE) from exc
        await self._command(["container", "rm", cid])
        remaining = await self._command(
            ["container", "ls", "--all", "--quiet", "--no-trunc", "--filter", f"id={cid}"]
        )
        _require(not remaining.strip())
        witness = CleanupWitness(
            cid, self.instance_id, attempt.episode_id, attempt.lease_bytes, True, stopped
        )
        # One retained issuance per admitted resource bounds this private map.
        self._cleanup_witnesses[profile.resource_id] = (attempt, witness)
        if journal_failure is not None:
            raise journal_failure
        await self._journal_wait(self._journal.cleaned(attempt, witness))
        self._owned.pop(cid, None)
        self._start_claims.discard(cid)
        self._unresolved_creations.pop(attempt.name, None)
        return witness
