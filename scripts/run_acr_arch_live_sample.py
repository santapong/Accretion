from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from accretion.live_sample import run_live_sample


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ACR-ARCH live provider calibration")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/release/acr-arch-live-sample.json"),
    )
    args = parser.parse_args()
    report = asyncio.run(run_live_sample(args.output))
    if not report.passed:
        raise SystemExit("ACR-ARCH live provider calibration failed")
    print(f"verified {report.sample_size}/{report.sample_size} live provider artifacts")


if __name__ == "__main__":
    main()
