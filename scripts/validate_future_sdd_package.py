"""Verify the frozen v1.x document import, optionally running synthetic conformance."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

PACKAGE = (
    Path(__file__).resolve().parents[1] / "docs/sdd/future/v1.1-v1.8/package"
)
MANIFEST_SHA256 = "986746757512f62a440d169497a47cd01846be73617362ead928dd000fc82bf8"


def validate_package(root: Path) -> int:
    """Require exactly the original files and bytes; ignore Python's runtime cache."""
    manifest = root / "MANIFEST.sha256"
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != MANIFEST_SHA256:
        raise ValueError("The imported manifest differs from its pinned identity")
    expected = {"MANIFEST.sha256"}
    for line in manifest.read_text().splitlines():
        digest, relative = line.split("  ", 1)
        target = root / relative
        if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Unsafe package path: {relative}")
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Modified imported file: {relative}")
        expected.add(relative)
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.relative_to(root).parts
    }
    if actual != expected:
        raise ValueError(f"Package inventory differs: {sorted(actual ^ expected)}")
    return len(expected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--integrity-only", action="store_true")
    args = parser.parse_args()
    try:
        count = validate_package(PACKAGE)
    except (OSError, ValueError) as error:
        print(f"Future SDD package check failed: {error}", file=sys.stderr)
        return 1
    print(f"Future SDD package integrity passed: {count} unchanged files", flush=True)
    if args.integrity_only:
        return 0
    result = subprocess.run(
        [sys.executable, "-B", "29_validate_revision4.py"],
        cwd=PACKAGE / "Accretion_v1x_Technical_SDD_Revision_4",
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
