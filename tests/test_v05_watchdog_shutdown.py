"""Real local pipes with injected process accounting; no container or simulator."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = r"""
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from accretion.robotics.host import watchdog as wd

mode = sys.argv[1]
source, sink = os.pipe()
os.set_blocking(sink, False)
try:
    while True:
        os.write(sink, b"x" * 4096)
except BlockingIOError:
    pass
os.set_blocking(sink, True)
os.dup2(sink, 2)

# Only stderr backpressure is real. No real child, namespace, CPU activity or
# simulator is claimed by the injected fork/cgroup/process-control callbacks.
wd.validate_namespace = lambda: None
counter = iter([1000, 800000 if mode == "cpu_stop" else 1000])
wd.read_cpu = lambda: next(counter)
wd.os.fork = lambda: 0 if mode == "child_failure" else 424242
original_write = os.write
wd.os.close = lambda fd: None
calls = {"kills": [], "wait_flags": []}
wd.os.kill = lambda pid, sig: calls["kills"].append([pid, int(sig)])

def waitpid(pid, flags):
    calls["wait_flags"].append(flags)
    if flags != os.WNOHANG:
        # Model a kernel-stalled child: a blocking reap must never hold PID1.
        time.sleep(10)
    return (pid, 0) if mode == "normal_exit" else (0, 0)

wd.os.waitpid = waitpid
if mode == "child_failure":
    from accretion.robotics.host import worker
    def fail(*args):
        original_write(1, b"INJECTED_WORKER_FAILURE\n")
        raise RuntimeError("injected worker failure")
    worker.run_worker = fail

bootstrap = SimpleNamespace(
    authority_expires_at=datetime.now(UTC) + timedelta(seconds=30),
    wall_seconds=10,
    cpu_seconds=1,
)
result = wd.supervise(bootstrap)
original_write(1, json.dumps({"result": result, **calls}).encode() + b"\n")
"""


@pytest.mark.parametrize("mode", ["cpu_stop", "normal_exit", "child_failure"])
def test_full_stderr_never_blocks_watchdog_shutdown(mode: str, tmp_path: Path) -> None:
    # The override permits this isolated review worktree to exercise the
    # coordinator's not-yet-committed module; normal repository runs use src/.
    source = os.environ.get(
        "ACCRETION_WATCHDOG_TEST_SOURCE", str(Path(__file__).resolve().parents[1] / "src")
    )
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT, mode],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": source},
        capture_output=True,
        timeout=5,
        check=False,
    )
    if mode == "child_failure":
        assert result.returncode == 1
        assert result.stdout == b"INJECTED_WORKER_FAILURE\n"
        return
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    output = json.loads(result.stdout)
    assert output["wait_flags"] == [os.WNOHANG]
    if mode == "cpu_stop":
        assert output["result"] == 124
        assert output["kills"] == [[424242, 9]]
    else:
        assert output["result"] == 0
        assert output["kills"] == []
