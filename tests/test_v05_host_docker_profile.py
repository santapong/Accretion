"""Replay an observed construction inspection; no daemon or robot runs in CI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from accretion.robotics.errors import RoboticsError
from accretion.robotics.host.docker import LABEL, DockerSupervisor, HostLimits, LaunchProfile

FIXTURE = Path(__file__).parent / "fixtures/robotics/host/created-egl-construction.json"


def fixture():
    row = json.loads(FIXTURE.read_text())
    # Host identity/UID/path are rewritten for this pure replay. The committed
    # fixture remains a scoped projection of the actual development inspection.
    row["Config"]["User"] = f"{os.geteuid()}:{os.getegid()}"
    supervisor = object.__new__(DockerSupervisor)
    supervisor.instance_id = row["Config"]["Labels"][LABEL]
    supervisor._journal = None
    supervisor._start_claims = set()
    supervisor._cleanup_witnesses = {}
    profile = LaunchProfile(
        resource_id="construction-host-probe",
        image_id=row["Image"],
        adapter_artifact_digest="a" * 64,
        model_bundle_digest="b" * 64,
        worker_kind="CONFORMANCE",
        limits=HostLimits(
            cpu_millicores=500,
            memory_bytes=128 * 1024**2,
            temporary_bytes=1024**2,
            shared_memory_bytes=1024**2,
            pids=16,
            cpu_seconds=10,
            wall_seconds=20,
        ),
    )
    directory = Path(row["Mounts"][0]["Source"])
    return supervisor, profile, row, directory


def test_observed_restricted_inspection_is_accepted() -> None:
    supervisor, profile, row, directory = fixture()
    supervisor.validate_inspection([row], profile, cid=row["Id"], directory=directory)


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("HostConfig", "Privileged", True),
        ("HostConfig", "NetworkMode", "host"),
        ("HostConfig", "PidMode", "host"),
        ("HostConfig", "IpcMode", "host"),
        ("HostConfig", "CgroupnsMode", "host"),
        ("HostConfig", "ReadonlyRootfs", False),
        ("HostConfig", "Devices", [{"PathOnHost": "/dev/ttyUSB0"}]),
        ("HostConfig", "DeviceRequests", [{"Capabilities": [["gpu"]]}]),
        ("HostConfig", "CapAdd", ["SYS_ADMIN"]),
        ("HostConfig", "SecurityOpt", ["seccomp=unconfined"]),
        ("HostConfig", "Memory", 0),
        ("HostConfig", "MemorySwap", -1),
        ("HostConfig", "NanoCpus", 0),
        ("HostConfig", "PidsLimit", -1),
        ("HostConfig", "Ulimits", []),
        ("HostConfig", "ReadonlyPaths", []),
        ("HostConfig", "MaskedPaths", []),
        ("HostConfig", "OomKillDisable", True),
        ("HostConfig", "Sysctls", {"net.ipv4.ip_forward": "1"}),
        ("HostConfig", "RestartPolicy", {"Name": "always"}),
        ("HostConfig", "LogConfig", {"Type": "json-file"}),
        ("Config", "Entrypoint", ["/bin/sh"]),
        ("Config", "Cmd", ["-c", "arbitrary"]),
        ("Config", "Tty", True),
        ("Config", "Env", []),
        ("State", "Running", True),
        ("State", "Paused", True),
    ],
)
def test_changed_isolation_or_limits_are_refused(section: str, key: str, value) -> None:
    supervisor, profile, row, directory = fixture()
    row[section][key] = value
    with pytest.raises(RoboticsError):
        supervisor.validate_inspection([row], profile, cid=row["Id"], directory=directory)


@pytest.mark.parametrize("change", ["writable", "other_path", "extra_mount", "image"])
def test_no_shared_artifact_or_device_mount_substitution(change: str) -> None:
    supervisor, profile, row, directory = fixture()
    if change == "writable":
        row["Mounts"][0]["RW"] = True
    elif change == "other_path":
        row["Mounts"][0]["Source"] = "/var/run/docker.sock"
    elif change == "extra_mount":
        row["Mounts"].append(dict(row["Mounts"][0]))
    else:
        row["Image"] = "sha256:" + "0" * 64
    with pytest.raises(RoboticsError):
        supervisor.validate_inspection([row], profile, cid=row["Id"], directory=directory)


async def test_unlisted_profile_fails_before_any_daemon_call(tmp_path: Path) -> None:
    supervisor, profile, row, directory = fixture()
    supervisor._profiles = {}
    from accretion.contracts.robotics.values import LeaseBinding

    contract = json.loads(
        (
            Path(__file__).parent / "fixtures/contracts/v0.5/PreparedCommand/complete.json"
        ).read_text()
    )
    with pytest.raises(RoboticsError) as exc:
        await supervisor.prepare(
            profile,
            episode_id="test",
            lease=LeaseBinding.model_validate(contract["lease"]),
            bootstrap_directory=directory,
        )
    assert exc.value.code.value == "PHYSICAL_ENDPOINT_DENIED"


def test_python_optimization_does_not_remove_isolation_checks(tmp_path: Path) -> None:
    supervisor, profile, row, directory = fixture()
    row["HostConfig"]["Privileged"] = True
    values = tmp_path / "input.json"
    values.write_text(json.dumps({"row": row, "profile": profile.model_dump(mode="json")}))
    script = """
import json, sys
from pathlib import Path
from accretion.robotics.errors import RoboticsError
from accretion.robotics.host.docker import DockerSupervisor, LaunchProfile, LABEL
value=json.loads(Path(sys.argv[1]).read_text()); row=value['row']
supervisor=object.__new__(DockerSupervisor)
supervisor.instance_id=row['Config']['Labels'][LABEL]
try:
 supervisor.validate_inspection([row],LaunchProfile.model_validate(value['profile']),cid=row['Id'],directory=Path(row['Mounts'][0]['Source']))
except RoboticsError:
 sys.exit(0)
sys.exit(1)
"""
    result = subprocess.run(
        [sys.executable, "-O", "-c", script, str(values)], timeout=10, capture_output=True
    )
    assert result.returncode == 0, result.stderr
