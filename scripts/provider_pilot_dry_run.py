"""Run only the reviewed FAKE/BASELINE_ONLY instrumentation recipe."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from accretion.provider_pilot import run_dry_run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New local evidence directory")
    args = parser.parse_args()
    report = asyncio.run(run_dry_run(args.output))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
