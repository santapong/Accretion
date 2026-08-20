from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Sequence
from typing import Any

from accretion.contracts import AgentEvent, EventType, Provider, RuntimeStatus, UsagePressure
from accretion.ids import new_id
from accretion.redaction import redact


async def command_result(command: Sequence[str], timeout_seconds: float = 5.0) -> tuple[int, str]:
    if not shutil.which(command[0]):
        return 127, "command not found"
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return 127, str(exc)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout_seconds)
    except TimeoutError:
        process.kill()
        await process.wait()
        return 124, "command timed out"
    return process.returncode or 0, output.decode(errors="replace").strip()


def parse_version(output: str) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)+", output)
    return tuple(int(part) for part in match.group().split(".")) if match else ()


def in_range(version: tuple[int, ...], minimum: tuple[int, ...], maximum: tuple[int, ...]) -> bool:
    return minimum <= version < maximum


def classify_runtime_health(
    *,
    version_code: int,
    version_output: str,
    auth_code: int,
    auth_output: str,
    minimum: tuple[int, ...],
    maximum: tuple[int, ...],
) -> tuple[RuntimeStatus, UsagePressure, str | None]:
    """Classify availability, compatibility, auth, and quota without reading credentials."""

    combined = f"{version_output}\n{auth_output}".lower()
    rate_limited = any(
        marker in combined
        for marker in ("rate limit", "usage limit", "quota exhausted", "limit reached")
    )
    if version_code != 0:
        return RuntimeStatus.UNAVAILABLE, UsagePressure.UNKNOWN, version_output
    if rate_limited:
        return RuntimeStatus.RATE_LIMITED, UsagePressure.EXHAUSTED, auth_output
    if auth_code != 0:
        return RuntimeStatus.AUTH_REQUIRED, UsagePressure.UNKNOWN, auth_output
    status = (
        RuntimeStatus.READY
        if in_range(parse_version(version_output), minimum, maximum)
        else RuntimeStatus.DEGRADED
    )
    return status, UsagePressure.UNKNOWN, None


def make_event(
    *,
    run_id: str,
    session_id: str,
    provider: Provider,
    native_type: str,
    normalized_type: EventType,
    payload: dict[str, Any] | None = None,
    adapter_version: str,
    correlation_id: str | None = None,
) -> AgentEvent:
    return AgentEvent(
        event_id=new_id("event"),
        run_id=run_id,
        session_id=session_id,
        provider=provider,
        native_type=native_type,
        normalized_type=normalized_type,
        correlation_id=correlation_id or run_id,
        payload=redact(payload or {}),
        adapter_version=adapter_version,
    )
