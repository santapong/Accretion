"""Prepare a tiny COPY-only context; never invokes Docker or accesses the network."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def prepare(root: Path, target: Path) -> dict[str, str]:
    target.mkdir(mode=0o700, parents=False, exist_ok=False)
    here = root / "scripts/robotics/m2_host_probe"
    files = {
        "Dockerfile": here / "Dockerfile",
        "simulation-worker": here / "entrypoint.py",
        "accretion/robotics/host/worker.py": here / "worker.py",
        "accretion/robotics/host/watchdog.py": root / "src/accretion/robotics/host/watchdog.py",
        "accretion/robotics/errors.py": root / "src/accretion/robotics/errors.py",
    }
    hashes = {}
    for name, source in files.items():
        raw = source.read_bytes()
        if not 0 < len(raw) <= 65536:
            raise ValueError("minimal fixed source size exceeded")
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    for name in ("accretion", "accretion/robotics", "accretion/robotics/host"):
        (target / name / "__init__.py").write_bytes(b"")
        hashes[name + "/__init__.py"] = hashlib.sha256(b"").hexdigest()
    (target / "source-hashes.json").write_text(json.dumps(hashes, sort_keys=True, indent=2) + "\n")
    return hashes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(Path(__file__).resolve().parents[3], args.target), sort_keys=True))
