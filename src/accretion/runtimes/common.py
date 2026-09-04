from __future__ import annotations

import asyncio
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from accretion.contracts import (
    AgentEvent,
    EventType,
    Provider,
    RuntimeExecutionRequest,
    RuntimeStatus,
    TaskEnvelope,
    UsagePressure,
)
from accretion.ids import new_id
from accretion.redaction import redact

RuntimeSubmission = TaskEnvelope | RuntimeExecutionRequest

# asyncio's StreamReader defaults to a 64 KiB line limit, and every provider
# frames its protocol as one JSON object per line. A single tool result carrying
# a modest file therefore overruns it and `readline()` raises ValueError, killing
# the call -- a run reading 30 KB notes died exactly that way. Sized off this
# repository's own bound on captured subprocess output: CommandVerifier caps
# `max_output_bytes` at 10 MB (contracts/__init__.py), so a line carrying that much output,
# JSON-escaped and wrapped in a protocol envelope, still fits. Bounded rather than
# unbounded on purpose: a runaway child must not be able to exhaust memory.
RUNTIME_STREAM_LIMIT = 16 * 1024 * 1024

_PROVIDER_ENV_ALLOWLIST = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "CODEX_HOME",
    "CLAUDE_CONFIG_DIR",
    "OPENCODE_CONFIG",
    "OPENCODE_CONFIG_DIR",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
}


def provider_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build a provider environment that never inherits application credentials."""

    environment = {
        key: value for key, value in os.environ.items() if key in _PROVIDER_ENV_ALLOWLIST
    }
    environment.update(extra or {})
    return environment


def submission_task(submission: RuntimeSubmission) -> TaskEnvelope:
    """Return the task carried by either the legacy or P2 runtime request."""

    if isinstance(submission, RuntimeExecutionRequest):
        return submission.task
    return submission


def submission_call_id(submission: RuntimeSubmission) -> str:
    """Give every adapter invocation its own stable event-stream identity."""

    if isinstance(submission, RuntimeExecutionRequest):
        return submission.runtime_call_id
    return new_id("runtime_call")


def submission_timeout_seconds(submission: RuntimeSubmission) -> float:
    """Respect the P2 run deadline instead of resetting wall time per iteration."""

    task_timeout = float(submission_task(submission).budgets.wall_time_seconds)
    if not isinstance(submission, RuntimeExecutionRequest) or submission.deadline is None:
        return task_timeout
    deadline = submission.deadline
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    remaining = (deadline - datetime.now(UTC)).total_seconds()
    return max(0.0, min(task_timeout, remaining))


def submission_metadata(submission: RuntimeSubmission) -> dict[str, Any]:
    """Return structured, provider-neutral iteration context for prompts and events."""

    if not isinstance(submission, RuntimeExecutionRequest):
        return {}
    return {
        "runtime_call_id": submission.runtime_call_id,
        "iteration_number": submission.iteration_number,
        "directive": submission.directive.model_dump(mode="json"),
    }


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
