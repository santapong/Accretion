from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Sequence
from typing import Any

from accretion.contracts import AgentEvent, EventType, Provider
from accretion.ids import new_id
from accretion.redaction import redact


async def command_result(command: Sequence[str], timeout_seconds: float = 5.0) -> tuple[int, str]:
    if not shutil.which(command[0]):
        return 127, "command not found"
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
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
