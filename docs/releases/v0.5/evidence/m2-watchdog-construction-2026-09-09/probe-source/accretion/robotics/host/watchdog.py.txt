"""PID-1 watchdog for the inspected private container, independent of worker IO.

The cgroup v2 cumulative CPU counter includes every process/thread in this
container, including this monitor. A missing, regressed or unreadable counter
terminates execution. The monitor uses a conservative 250 ms CPU reserve and
50 ms polling; scheduler latency is recorded by actual accounting, never sold
as a hard real-time bound. Docker's kernel CPU/PID/memory limits remain required.
Exiting PID 1 tears down the container's remaining descendants.
"""

from __future__ import annotations

import contextlib
import os
import signal
import stat
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code

if TYPE_CHECKING:
    from .worker import WorkerBootstrap

POLL_SECONDS = 0.05
CPU_RESERVE_MICROSECONDS = 250_000
CPU_STAT = Path("/sys/fs/cgroup/cpu.stat")


def diagnostic(raw: bytes) -> None:
    # A stopped or crashed host reader cannot hold up containment. Descriptor 2
    # is made nonblocking before fork; writes here are short and best effort.
    with contextlib.suppress(OSError):
        os.write(2, raw)


def parse_cpu(raw: bytes) -> int:
    if not raw or len(raw) > 4096:
        raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
    try:
        rows = [line.split() for line in raw.decode("ascii").splitlines()]
        if any(len(row) != 2 for row in rows) or len({row[0] for row in rows}) != len(rows):
            raise ValueError("invalid cgroup counter")
        values = {key: int(value) for key, value in rows}
        if "usage_usec" not in values or any(value < 0 for value in values.values()):
            raise ValueError("invalid cumulative CPU")
        return values["usage_usec"]
    except (UnicodeError, ValueError) as exc:
        raise RoboticsError(Code.ISOLATION_UNAVAILABLE) from exc


def read_cpu() -> int:
    descriptor = os.open(CPU_STAT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o222:
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
        return parse_cpu(os.read(descriptor, 4097))
    finally:
        os.close(descriptor)


def validate_namespace() -> None:
    if os.getpid() != 1 or os.geteuid() == 0:
        raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
    if Path("/proc/self/cgroup").read_bytes() != b"0::/\n":
        raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
    mounts = Path("/proc/self/mountinfo").read_text().splitlines()
    matching = []
    for line in mounts:
        left, separator, right = line.partition(" - ")
        fields, filesystem = left.split(), right.split()
        if separator and len(fields) >= 6 and fields[4] == "/sys/fs/cgroup":
            matching.append((fields[5], filesystem))
    if (
        len(matching) != 1
        or "ro" not in matching[0][0].split(",")
        or not matching[0][1]
        or matching[0][1][0] != "cgroup2"
    ):
        raise RoboticsError(Code.ISOLATION_UNAVAILABLE)


def supervise(bootstrap: WorkerBootstrap) -> int:
    validate_namespace()
    start = time.monotonic()
    authority_remaining = (bootstrap.authority_expires_at - datetime.now(UTC)).total_seconds()
    deadline = start + min(bootstrap.wall_seconds, authority_remaining)
    # Count from container birth, not a fresh baseline that excludes startup.
    previous_cpu = read_cpu()
    cpu_stop = bootstrap.cpu_seconds * 1_000_000 - CPU_RESERVE_MICROSECONDS
    if deadline <= start or previous_cpu >= cpu_stop:
        raise RoboticsError(Code.RESOURCE_CAP_EXHAUSTED)
    stopped = False

    def stop(signum: int, frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    os.set_blocking(2, False)
    child = os.fork()
    if child == 0:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        try:
            from .worker import run_worker

            run_worker(bootstrap, sys.stdin.buffer, sys.stdout.buffer)
        except BaseException:
            # Bounded diagnostic only. Raw exception text may contain paths.
            diagnostic(b"SIMULATION_WORKER_FAILED\n")
            os._exit(1)
        os._exit(0)
    # Only the adapter child owns protocol pipes. The monitor never reads a
    # frame or waits on a database, renderer, artifact callback or child stdout.
    os.close(0)
    os.close(1)
    reaped = False
    try:
        while True:
            cpu = read_cpu()
            now = time.monotonic()
            if stopped or cpu < previous_cpu or cpu >= cpu_stop or now >= deadline:
                diagnostic(f"SIMULATION_WATCHDOG_STOP cpu_usec={cpu}\n".encode("ascii"))
                return 124
            previous_cpu = cpu
            pid, status = os.waitpid(child, os.WNOHANG)
            if pid == child:
                reaped = True
                diagnostic(f"SIMULATION_WATCHDOG_EXIT cpu_usec={cpu}\n".encode("ascii"))
                return max(0, os.waitstatus_to_exitcode(status)) if status == 0 else 1
            time.sleep(min(POLL_SECONDS, max(0, deadline - now)))
    finally:
        if not reaped:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child, signal.SIGKILL)
            # No blocking reap: a kernel-stalled child must not delay PID-1 exit
            # and the container-wide descendant teardown performed by Linux.
            with contextlib.suppress(ChildProcessError):
                os.waitpid(child, os.WNOHANG)
