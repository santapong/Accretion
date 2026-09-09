#!/usr/local/bin/python
"""Fixed construction-only entrypoint; imports the exact production watchdog."""

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "/opt/probe")
from accretion.robotics.host.watchdog import supervise  # noqa: E402

raw = Path("/run/accretion/bootstrap.json").read_bytes()
if not 0 < len(raw) <= 65536:
    raise SystemExit(2)
config = json.loads(raw)
if config["scope"] != "NONROBOT_CONSTRUCTION_PROBE":
    raise SystemExit(2)
raise SystemExit(
    supervise(
        SimpleNamespace(
            wall_seconds=20,
            cpu_seconds=5,
            authority_expires_at=datetime.fromisoformat(config["authority_expires_at"]),
        )
    )
)
