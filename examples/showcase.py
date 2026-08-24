#!/usr/bin/env python3
"""Run a deterministic Accretion task through the public HTTP API."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED", "REQUIRES_HUMAN"}


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> Any:
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - URL is operator-supplied
            return json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Accretion API returned {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach Accretion at {base_url}: {exc.reason}") from exc


def run_showcase(base_url: str, repository: Path, timeout: float) -> dict[str, Any]:
    health = request_json(base_url, "/healthz")
    project = request_json(
        base_url,
        "/api/v1/projects",
        method="POST",
        payload={"name": "Accretion showcase", "repository_path": str(repository)},
    )
    task = request_json(
        base_url,
        "/api/v1/tasks",
        method="POST",
        payload={
            "project_id": project["project_id"],
            "objective": (
                "Review the project overview as a bounded deterministic demonstration. "
                "Do not modify files or use external capabilities."
            ),
            "task_type": "REVIEW",
            "risk_level": "LOW",
            "success_criteria": ["README.md remains present and non-empty."],
            "denied_capabilities": ["external-network", "publish", "delete"],
            "required_outputs": [
                {"path": "README.md", "kind": "file", "non_empty": True}
            ],
            "budgets": {
                "wall_time_seconds": 120,
                "max_turns": 4,
                "max_tool_calls": 8,
                "max_loop_iterations": 1,
                "max_parallel_runs": 1,
            },
        },
    )
    task_id = task["envelope"]["task_id"]
    planning = request_json(base_url, f"/api/v1/tasks/{task_id}/planning")
    run = request_json(
        base_url,
        f"/api/v1/tasks/{task_id}/runs",
        method="POST",
        payload={"provider": "FAKE"},
    )
    run_id = run["run_id"]
    deadline = time.monotonic() + timeout
    while run["state"] not in TERMINAL_STATES:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Run {run_id} did not finish within {timeout:g} seconds")
        time.sleep(0.2)
        run = request_json(base_url, f"/api/v1/runs/{run_id}")

    audit = request_json(base_url, f"/api/v1/runs/{run_id}/audit")
    decision = planning["current_decision"]
    return {
        "api_version": health["version"],
        "project_id": project["project_id"],
        "task_id": task_id,
        "run_id": run_id,
        "strategy": {
            "mode": decision["selected_mode"],
            "template": decision["selected_template_id"],
            "rules": decision["matched_rules"],
        },
        "provider": audit["runtime"]["provider"],
        "state": audit["run"]["state"],
        "events": len(audit["events"]),
        "last_sequence": audit["run"]["last_sequence"],
        "verifications": [
            {"verifier": item["verifier_id"], "status": item["status"]}
            for item in audit["verifications"]
        ],
        "capability_results": len(audit["capability_results"]),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--api-url",
        default="http://localhost:8000",
        help="Accretion API root (default: %(default)s)",
    )
    result.add_argument(
        "--repository",
        type=Path,
        default=Path.cwd(),
        help="Existing Git repository to register (default: current directory)",
    )
    result.add_argument(
        "--timeout",
        type=float,
        default=30,
        help="Seconds to wait for a terminal run state (default: %(default)s)",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    repository = args.repository.expanduser().resolve()
    if not (repository / ".git").exists():
        print(f"error: {repository} is not a Git repository", file=sys.stderr)
        return 2
    try:
        summary = run_showcase(args.api_url, repository, args.timeout)
    except (RuntimeError, TimeoutError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["state"] == "SUCCEEDED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
