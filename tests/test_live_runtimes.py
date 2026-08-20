from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from accretion.contracts import (
    AgentEvent,
    AgentRuntime,
    EventType,
    RunRef,
    RuntimeStatus,
    SessionConfig,
    TaskEnvelope,
)
from accretion.ids import new_id
from accretion.runtimes.claude import ClaudeRuntime
from accretion.runtimes.codex import CodexRuntime

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("ACCRETION_LIVE_PROVIDERS") != "1",
        reason="set ACCRETION_LIVE_PROVIDERS=1 to use signed-in provider sessions",
    ),
]


def initialize_repository(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def harmless_task() -> TaskEnvelope:
    return TaskEnvelope(
        task_id=new_id("task"),
        project_id=new_id("project"),
        objective="Create no files and invoke no tools. Reply with exactly READY.",
    )


async def collect(runtime: AgentRuntime, run: RunRef) -> list[AgentEvent]:
    return [event async for event in runtime.events(run)]


async def test_live_codex_runs_two_independent_threads_on_one_server(tmp_path: Path) -> None:
    first_workspace = tmp_path / "codex-first"
    second_workspace = tmp_path / "codex-second"
    initialize_repository(first_workspace)
    initialize_repository(second_workspace)
    runtime = CodexRuntime()
    try:
        assert (await runtime.health()).status is RuntimeStatus.READY
        first_session = await runtime.create_session(
            SessionConfig(run_id=new_id("run"), workspace=first_workspace)
        )
        second_session = await runtime.create_session(
            SessionConfig(run_id=new_id("run"), workspace=second_workspace)
        )
        first_run, second_run = await asyncio.gather(
            runtime.submit(first_session, harmless_task()),
            runtime.submit(second_session, harmless_task()),
        )
        first_events, second_events = await asyncio.gather(
            collect(runtime, first_run), collect(runtime, second_run)
        )
        assert first_run.native_run_id != second_run.native_run_id
        assert first_events[-1].normalized_type is EventType.RUN_COMPLETED
        assert second_events[-1].normalized_type is EventType.RUN_COMPLETED
    finally:
        await runtime.close()


async def test_live_claude_emits_normalized_start_progress_terminal(tmp_path: Path) -> None:
    workspace = tmp_path / "claude"
    initialize_repository(workspace)
    runtime = ClaudeRuntime()
    assert (await runtime.health()).status is RuntimeStatus.READY
    session = await runtime.create_session(SessionConfig(run_id=new_id("run"), workspace=workspace))
    run = await runtime.submit(session, harmless_task())
    normalized = [event.normalized_type for event in await collect(runtime, run)]
    assert EventType.RUN_STARTED in normalized
    assert EventType.RUN_PROGRESS in normalized
    assert normalized.index(EventType.RUN_STARTED) < len(normalized) - 1
    assert normalized[-1] is EventType.RUN_COMPLETED


async def test_live_claude_and_codex_run_in_separate_worktrees(tmp_path: Path) -> None:
    codex_workspace = tmp_path / "codex"
    claude_workspace = tmp_path / "claude"
    initialize_repository(codex_workspace)
    initialize_repository(claude_workspace)
    codex = CodexRuntime()
    claude = ClaudeRuntime()
    try:
        codex_session, claude_session = await asyncio.gather(
            codex.create_session(SessionConfig(run_id=new_id("run"), workspace=codex_workspace)),
            claude.create_session(SessionConfig(run_id=new_id("run"), workspace=claude_workspace)),
        )
        codex_run, claude_run = await asyncio.gather(
            codex.submit(codex_session, harmless_task()),
            claude.submit(claude_session, harmless_task()),
        )
        codex_events, claude_events = await asyncio.gather(
            collect(codex, codex_run), collect(claude, claude_run)
        )
        assert codex_workspace != claude_workspace
        assert codex_events[-1].normalized_type is EventType.RUN_COMPLETED
        assert claude_events[-1].normalized_type is EventType.RUN_COMPLETED
    finally:
        await codex.close()
