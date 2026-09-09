#!/usr/bin/env python3
"""Run optional UR5e development tests in one bounded, explicitly local window.

An existing window is resumed, never reset. Each attempt retains its command,
exit, measured child CPU and output. A new authorized window requires a new path.
No study seeds, GPU, download, external provider or physical endpoint are used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

WALL_LIMIT = 1_200
CPU_LIMIT = 600
OUTPUT_LIMIT = 2 * 1024**3
SOFTWARE_ENV = {
    "MUJOCO_GL": "egl",
    "PYOPENGL_PLATFORM": "egl",
    "LIBGL_ALWAYS_SOFTWARE": "1",
    "MESA_LOADER_DRIVER_OVERRIDE": "llvmpipe",
    "EGL_PLATFORM": "surfaceless",
    "LP_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
}


def total_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--window", type=Path, required=True)
    parser.add_argument("--select", default="")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    window = args.window.resolve()
    window.mkdir(parents=True, exist_ok=True)
    ledger_path = window / "window.json"
    now = time.time()
    ledger = (
        json.loads(ledger_path.read_text())
        if ledger_path.exists()
        else {
            "started_unix_seconds": now,
            "cpu_seconds": 0.0,
            "attempts": [],
            "scope": "DEVELOPMENT_ONLY",
            "seeds": [707],
            "wall_limit_seconds": WALL_LIMIT,
            "cpu_limit_seconds": CPU_LIMIT,
            "output_limit_bytes": OUTPUT_LIMIT,
        }
    )
    remaining_wall = WALL_LIMIT - (now - ledger["started_unix_seconds"])
    remaining_cpu = CPU_LIMIT - ledger["cpu_seconds"]
    # Reserve 512 MiB per attempt, above this fixed test suite's artifact quota.
    if (
        remaining_wall < 1
        or remaining_cpu < 1
        or total_bytes(window) > OUTPUT_LIMIT - 512 * 1024**2
    ):
        parser.error("authorized development window exhausted; no simulator started")
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n")
    attempt = window / f"attempt-{len(ledger['attempts']) + 1:02d}"
    attempt.mkdir()
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        str(root / "tests/test_v05_ur5e_simulation.py"),
        "--basetemp",
        str(attempt / "tmp"),
        "--tb=short",
    ]
    if args.select:
        command += ["-k", args.select]
    env = (
        os.environ
        | SOFTWARE_ENV
        | {
            "ACCRETION_RUN_SIMULATION_TESTS": "1",
            "ACCRETION_MENAGERIE_ROOT": str(args.models.resolve()),
            "ACCRETION_DEVELOPMENT_EVIDENCE": str(attempt),
        }
    )

    def limits() -> None:
        cpu = max(1, math.floor(remaining_cpu))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2, 32 * 1024**2))

    def source_hashes() -> dict[str, str]:
        inputs = [
            *sorted((root / "src/accretion/robotics/adapters").glob("*.py")),
            root / "src/accretion/robotics/adapters/ur5e-model-files.json",
            root / "tests/test_v05_ur5e_simulation.py",
            root / "uv.lock",
            Path(__file__),
        ]
        return {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in inputs
        }

    sources_before = source_hashes()
    started = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    with (attempt / "output.log").open("w") as output:
        try:
            result = subprocess.run(
                command,
                cwd=attempt,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=remaining_wall,
                preexec_fn=limits,
                check=False,
            )
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime
    sources_after = source_hashes()
    unchanged = sources_before == sources_after
    if not unchanged:
        code = 2
    record = dict(
        source_hashes=sources_before,
        source_hashes_unchanged=unchanged,
        command=command,
        exit_code=code,
        wall_seconds=time.monotonic() - started,
        cpu_seconds=cpu,
        output_bytes=total_bytes(attempt),
        software_environment=SOFTWARE_ENV,
    )
    (attempt / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    ledger["attempts"].append(record)
    ledger["cpu_seconds"] += cpu
    ledger["total_output_bytes"] = total_bytes(window)
    ledger["elapsed_wall_seconds"] = time.time() - ledger["started_unix_seconds"]
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n")
    print((attempt / "output.log").read_text())
    print(json.dumps(record))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
