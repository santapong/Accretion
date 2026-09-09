#!/usr/bin/env python3
"""Prepare a bounded allowlisted image context; neither build nor launch a worker.

Only tracked package sources, the locked dependency export, selected verified
model files, license and fixed recipe are included. No workspace-wide COPY,
credentials, Git metadata, database, artifact store or runtime grants are copied.
The output directory must be new. Any error leaves its evidence for inspection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

MAX_CONTEXT_BYTES = 512 * 1024**2


def read_regular(root: Path, relative: str) -> bytes:
    path = root / relative
    if (
        Path(relative).is_absolute()
        or ".." in Path(relative).parts
        or not path.is_file()
        or path.resolve() != path
        or path.stat().st_size > 64 * 1024**2
    ):
        raise ValueError(f"not an admitted regular input: {relative}")
    return path.read_bytes()


def prepare(root: Path, models: Path, output: Path) -> dict[str, object]:
    root, models, output = root.resolve(), models.resolve(), output.absolute()
    if output.exists() or output.parent.resolve() != output.parent:
        raise ValueError("output must be a new directory under a real existing parent")
    output.mkdir(mode=0o700)
    inputs: dict[str, bytes] = {}
    paths = (
        subprocess.check_output(["git", "ls-files", "-z", "src/accretion"], cwd=root)
        .decode()
        .split("\0")
    )
    for relative in filter(None, paths):
        inputs[relative] = read_regular(root, relative)
    for relative in ("LICENSE", "pyproject.toml", "uv.lock"):
        inputs[relative] = read_regular(root, relative)
    model_hashes: dict[str, str] = {}
    for kind in ("ur5e",):
        manifest = json.loads(inputs[f"src/accretion/robotics/adapters/{kind}-model-files.json"])
        for relative, expected in manifest.items():
            data = read_regular(models, relative)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError(f"model digest mismatch: {relative}")
            if relative in model_hashes and model_hashes[relative] != expected:
                raise ValueError("conflicting model inventories")
            model_hashes[relative] = expected
            inputs[f"models/{relative}"] = data
    for name in ("Dockerfile", "simulation-worker"):
        inputs[name] = read_regular(root, f"scripts/robotics/image/{name}")
    if sum(map(len, inputs.values())) > MAX_CONTEXT_BYTES:
        raise ValueError("context size ceiling exceeded")
    requirements = subprocess.check_output(
        [
            "uv",
            "export",
            "--locked",
            "--no-dev",
            "--group",
            "simulation",
            "--no-emit-project",
            "--no-header",
            "--no-annotate",
        ],
        cwd=root,
    )
    # A second read detects source mutation during the export/model copy. Model
    # bytes themselves were already digest-checked and are copied from memory.
    for relative, original in inputs.items():
        if relative.startswith("models/") or relative in ("Dockerfile", "simulation-worker"):
            continue
        if read_regular(root, relative) != original:
            raise ValueError(f"input changed during preparation: {relative}")
    inputs["requirements.txt"] = requirements
    manifest = {
        "format": "accretion.simulation-image-source.v1",
        "scope": "IMAGE_CONSTRUCTION_ONLY",
        "worker_kind": "UR5E",
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip(),
        "source_files": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
            for name, data in sorted(inputs.items())
        },
        "model_files": model_hashes,
        "context_limit_bytes": MAX_CONTEXT_BYTES,
    }
    inputs["source-manifest.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode()
    if sum(map(len, inputs.values())) > MAX_CONTEXT_BYTES:
        raise ValueError("final context size ceiling exceeded")
    for relative, data in inputs.items():
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o555 if relative == "simulation-worker" else 0o444)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(Path(__file__).resolve().parents[2], args.models, args.output)
    print(json.dumps({"commit": result["commit"], "output": str(args.output.resolve())}))


if __name__ == "__main__":
    main()
