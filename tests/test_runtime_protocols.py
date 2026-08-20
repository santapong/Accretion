import asyncio
from pathlib import Path

import pytest

from accretion.contracts import (
    EventType,
    Provider,
    RunRef,
    RuntimeStatus,
    SessionConfig,
    TaskEnvelope,
    UsagePressure,
)
from accretion.ids import new_id
from accretion.runtimes.claude import ClaudeRuntime
from accretion.runtimes.codex import CodexRuntime
from accretion.runtimes.common import classify_runtime_health


def test_codex_stable_notifications_normalize_to_run_lifecycle() -> None:
    assert CodexRuntime._normalize("turn/started", {"turn": {"status": "inProgress"}}) == (
        EventType.RUN_STARTED
    )
    assert CodexRuntime._normalize("turn/completed", {"turn": {"status": "completed"}}) == (
        EventType.RUN_COMPLETED
    )
    assert CodexRuntime._normalize("turn/completed", {"turn": {"status": "interrupted"}}) == (
        EventType.RUN_CANCELLED
    )
    assert CodexRuntime._normalize("turn/completed", {"turn": {"status": "failed"}}) == (
        EventType.RUN_FAILED
    )


def test_claude_stream_json_normalizes_to_run_lifecycle() -> None:
    assert ClaudeRuntime._normalize({"type": "system", "subtype": "init"}) == (
        EventType.RUN_STARTED
    )
    assert ClaudeRuntime._normalize({"type": "assistant"}) == EventType.RUN_PROGRESS
    assert ClaudeRuntime._normalize({"type": "result", "is_error": False}) == (
        EventType.RUN_COMPLETED
    )
    assert ClaudeRuntime._normalize({"type": "result", "is_error": True}) == EventType.RUN_FAILED


def test_runtime_health_classifies_every_required_state() -> None:
    cases = [
        ((0, "codex 0.148.0", 0, "logged in"), RuntimeStatus.READY),
        ((0, "codex 0.149.0", 0, "logged in"), RuntimeStatus.DEGRADED),
        ((0, "codex 0.148.0", 1, "login required"), RuntimeStatus.AUTH_REQUIRED),
        ((0, "codex 0.148.0", 1, "usage limit reached"), RuntimeStatus.RATE_LIMITED),
        ((127, "command not found", 127, "command not found"), RuntimeStatus.UNAVAILABLE),
    ]
    for (version_code, version_output, auth_code, auth_output), expected in cases:
        status, pressure, _error = classify_runtime_health(
            version_code=version_code,
            version_output=version_output,
            auth_code=auth_code,
            auth_output=auth_output,
            minimum=(0, 148, 0),
            maximum=(0, 149, 0),
        )
        assert status is expected
        assert pressure is (
            UsagePressure.EXHAUSTED
            if expected is RuntimeStatus.RATE_LIMITED
            else UsagePressure.UNKNOWN
        )


async def test_codex_eof_emits_one_terminal_failure_and_closes_consumer() -> None:
    runtime = CodexRuntime()
    run_id = new_id("run")
    session_id = new_id("session")
    runtime.queues[run_id] = asyncio.Queue()
    runtime.run_refs[run_id] = RunRef(run_id=run_id, session_id=session_id)

    await runtime._fail_active_runs("unexpected EOF")
    await runtime._fail_active_runs("duplicate EOF")

    events = [event async for event in runtime.events(runtime.run_refs[run_id])]
    assert [event.normalized_type for event in events] == [EventType.RUN_FAILED]


async def test_claude_startup_failure_emits_one_terminal_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fail_start(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("claude disappeared")

    monkeypatch.setattr("accretion.runtimes.claude.asyncio.create_subprocess_exec", fail_start)
    runtime = ClaudeRuntime()
    run_id = new_id("run")
    session = await runtime.create_session(SessionConfig(run_id=run_id, workspace=tmp_path))
    run = await runtime.submit(
        session,
        TaskEnvelope(
            task_id=new_id("task"),
            project_id=new_id("project"),
            objective="harmless",
        ),
    )

    events = [event async for event in runtime.events(run)]
    assert [event.normalized_type for event in events] == [EventType.RUN_FAILED]
    assert events[0].provider is Provider.CLAUDE
