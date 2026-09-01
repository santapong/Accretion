from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from accretion.contracts import (
    EventType,
    IterationDirective,
    IterationDirectiveKind,
    RuntimeExecutionRequest,
    SessionConfig,
    TaskEnvelope,
)
from accretion.ids import new_id
from accretion.runtimes.claude import ClaudeRuntime
from accretion.runtimes.codex import CodexRuntime
from accretion.runtimes.fake import FakeCallOutcome, FakeRuntime


def _task() -> TaskEnvelope:
    return TaskEnvelope(
        task_id=new_id("task"),
        project_id=new_id("project"),
        objective="make the harmless fixture correct",
        success_criteria=["fixture is correct"],
    )


def _request(task: TaskEnvelope, run_id: str, iteration: int) -> RuntimeExecutionRequest:
    return RuntimeExecutionRequest(
        runtime_call_id=new_id("runtime_call"),
        run_id=run_id,
        task=task,
        iteration_number=iteration,
        directive=IterationDirective(
            kind=(
                IterationDirectiveKind.INITIAL if iteration == 1 else IterationDirectiveKind.REPAIR
            ),
            objective=task.objective,
        ),
    )


async def test_fake_runtime_scripts_independent_calls_and_workspace_hooks(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.txt"

    def write_bad(*_args: object) -> None:
        fixture.write_text("bad")

    async def write_fixed(*_args: object) -> None:
        fixture.write_text("fixed")

    runtime = FakeRuntime(
        scripted_outcomes=[
            FakeCallOutcome(
                terminal=EventType.RUNTIME_CALL_COMPLETED,
                payload={"candidate": "bad"},
                hook=write_bad,
            ),
            FakeCallOutcome(
                terminal=EventType.RUNTIME_CALL_COMPLETED,
                payload={"candidate": "fixed"},
                hook=write_fixed,
            ),
        ]
    )
    run_id = new_id("run")
    task = _task()
    session = await runtime.create_session(SessionConfig(run_id=run_id, workspace=tmp_path))

    first = await runtime.submit(session, _request(task, run_id, 1))
    first_events = [event async for event in runtime.events(first)]
    assert fixture.read_text() == "bad"

    second = await runtime.submit(session, _request(task, run_id, 2))
    second_events = [event async for event in runtime.events(second)]

    assert fixture.read_text() == "fixed"
    assert first.runtime_call_id != second.runtime_call_id
    assert first.native_run_id == second.native_run_id == session.native_session_id
    assert len(runtime.queues) == 2
    assert first_events[-1].payload["candidate"] == "bad"
    assert second_events[-1].payload["candidate"] == "fixed"
    assert first_events[-1].normalized_type is EventType.RUNTIME_CALL_COMPLETED
    assert second_events[-1].normalized_type is EventType.RUNTIME_CALL_COMPLETED
    assert all(event.correlation_id == first.runtime_call_id for event in first_events)
    assert all(event.correlation_id == second.runtime_call_id for event in second_events)


async def test_fake_runtime_rejects_overlapping_calls_without_overwriting_queue(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(step_delay=0.01)
    run_id = new_id("run")
    session = await runtime.create_session(SessionConfig(run_id=run_id, workspace=tmp_path))
    first = await runtime.submit(session, _task())

    with pytest.raises(RuntimeError, match="active provider call"):
        await runtime.submit(session, _task())

    await runtime.interrupt(first)
    events = [event async for event in runtime.events(first)]
    assert events[-1].normalized_type is EventType.RUNTIME_CALL_CANCELLED
    assert len(runtime.queues) == 1


async def test_codex_reuses_one_thread_for_sequential_turns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = CodexRuntime(gateway_environment={"ACCRETION_POLICY_ID": "policy_default"})
    methods: list[tuple[str, dict[str, Any]]] = []
    turn_number = 0

    async def server_ready() -> None:
        return None

    async def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal turn_number
        methods.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "thread-shared"}}
        if method == "turn/start":
            turn_number += 1
            return {"turn": {"id": f"turn-{turn_number}"}}
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(runtime, "_ensure_server", server_ready)
    monkeypatch.setattr(runtime, "_request", request)
    run_id = new_id("run")
    session = await runtime.create_session(SessionConfig(run_id=run_id, workspace=tmp_path))

    first = await runtime.submit(session, _task())
    await runtime._handle_notification(
        {
            "method": "turn/started",
            "params": {"threadId": "thread-shared", "turn": {"id": "turn-1"}},
        }
    )
    await runtime._handle_notification(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-shared",
                "turn": {"id": "turn-1", "status": "completed"},
            },
        }
    )
    first_events = [event async for event in runtime.events(first)]

    # Deliberately reuse the original SessionRef. The adapter's canonical session
    # retains the native thread even if the caller has not copied it back yet.
    second = await runtime.submit(session, _task())
    await runtime._handle_notification(
        {
            "method": "turn/started",
            "params": {"threadId": "thread-shared", "turn": {"id": "turn-2"}},
        }
    )
    await runtime._handle_notification(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-shared",
                "turn": {"id": "turn-2", "status": "completed"},
            },
        }
    )
    second_events = [event async for event in runtime.events(second)]

    assert [method for method, _params in methods] == [
        "thread/start",
        "turn/start",
        "turn/start",
    ]
    thread_config = methods[0][1]
    assert thread_config["sandbox"] == "workspace-write"
    assert thread_config["config"]["sandbox_workspace_write"]["network_access"] is False
    assert thread_config["config"]["shell_environment_policy"] == {"inherit": "core"}
    gateway = thread_config["config"]["mcp_servers"]["accretion"]
    assert gateway["args"] == ["-m", "accretion.mcp_gateway"]
    assert gateway["required"] is True
    assert gateway["env"] == {
        "ACCRETION_POLICY_ID": "policy_default",
        "ACCRETION_GATEWAY_RUN_ID": run_id,
    }
    assert methods[1][1]["threadId"] == methods[2][1]["threadId"] == "thread-shared"
    assert first.native_run_id == second.native_run_id == "thread-shared"
    assert first.runtime_call_id != second.runtime_call_id
    assert first_events[-1].normalized_type is EventType.RUNTIME_CALL_COMPLETED
    assert second_events[-1].normalized_type is EventType.RUNTIME_CALL_COMPLETED


async def test_codex_startup_failure_returns_one_closed_failure_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = CodexRuntime()

    async def fail_server() -> None:
        raise FileNotFoundError("codex disappeared")

    monkeypatch.setattr(runtime, "_ensure_server", fail_server)
    run_id = new_id("run")
    session = await runtime.create_session(SessionConfig(run_id=run_id, workspace=tmp_path))
    run = await runtime.submit(session, _task())
    await runtime._fail_active_runs("duplicate failure")

    events = [event async for event in runtime.events(run)]
    assert [event.normalized_type for event in events] == [EventType.RUNTIME_CALL_FAILED]


async def test_codex_direct_probe_does_not_require_an_unconfigured_gateway(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = CodexRuntime()
    methods: list[tuple[str, dict[str, Any]]] = []

    async def server_ready() -> None:
        return None

    async def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
        methods.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "thread-probe"}}
        return {"turn": {"id": "turn-probe"}}

    monkeypatch.setattr(runtime, "_ensure_server", server_ready)
    monkeypatch.setattr(runtime, "_request", request)
    run_id = new_id("run")
    session = await runtime.create_session(SessionConfig(run_id=run_id, workspace=tmp_path))
    run = await runtime.submit(session, _task())

    gateway = methods[0][1]["config"]["mcp_servers"]["accretion"]
    assert gateway["required"] is False
    await runtime._fail_call(run.runtime_call_id or run.run_id, "probe complete")


class _Stdout:
    def __init__(self) -> None:
        self.lines = [
            json.dumps({"type": "system", "subtype": "init"}).encode() + b"\n",
            json.dumps({"type": "assistant", "message": "working"}).encode() + b"\n",
            json.dumps({"type": "result", "is_error": False}).encode() + b"\n",
        ]

    async def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""


class _Stderr:
    async def read(self) -> bytes:
        return b""


class _ClaudeProcess:
    def __init__(self) -> None:
        self.stdout = _Stdout()
        self.stderr = _Stderr()
        self.returncode: int | None = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = 143

    def kill(self) -> None:
        self.returncode = 137


@pytest.mark.acceptance("V01-P0-003")
async def test_claude_uses_session_id_once_then_resume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[tuple[str, ...]] = []
    invocations: list[dict[str, object]] = []

    async def start(*args: str, **kwargs: object) -> _ClaudeProcess:
        commands.append(args)
        invocations.append(kwargs)
        return _ClaudeProcess()

    monkeypatch.setattr("accretion.runtimes.claude.asyncio.create_subprocess_exec", start)
    runtime = ClaudeRuntime(gateway_environment={"ACCRETION_POLICY_ID": "policy_default"})
    run_id = new_id("run")
    session = await runtime.create_session(
        SessionConfig(
            run_id=run_id,
            workspace=tmp_path,
            model="sonnet",
            allowed_tools=["accretion.echo"],
        )
    )

    first = await runtime.submit(session, _task())
    first_events = [event async for event in runtime.events(first)]
    second = await runtime.submit(session, _task())
    second_events = [event async for event in runtime.events(second)]

    assert "--session-id" in commands[0]
    assert "--resume" not in commands[0]
    assert "--resume" in commands[1]
    assert "--session-id" not in commands[1]
    assert commands[0][commands[0].index("--session-id") + 1] == session.native_session_id
    assert commands[1][commands[1].index("--resume") + 1] == session.native_session_id
    assert "--strict-mcp-config" in commands[0]
    assert "--safe-mode" in commands[0]
    assert commands[0][commands[0].index("--model") + 1] == "sonnet"
    mcp_config = json.loads(commands[0][commands[0].index("--mcp-config") + 1])
    assert mcp_config["mcpServers"]["accretion"]["args"] == [
        "-m",
        "accretion.mcp_gateway",
    ]
    assert mcp_config["mcpServers"]["accretion"]["env"] == {
        "ACCRETION_POLICY_ID": "policy_default",
        "ACCRETION_GATEWAY_RUN_ID": run_id,
    }
    visible_tools = commands[0][commands[0].index("--tools") + 1].split(",")
    assert "mcp__accretion__accretion.echo" in visible_tools
    assert "WebFetch" not in visible_tools
    assert "WebSearch" not in visible_tools
    process_environment = invocations[0]["env"]
    assert isinstance(process_environment, dict)
    assert not any(
        "TOKEN" in key or "SECRET" in key or "PASSWORD" in key
        for key in process_environment
    )
    assert first.native_run_id == second.native_run_id == session.native_session_id
    assert first.runtime_call_id != second.runtime_call_id
    assert first_events[-1].normalized_type is EventType.RUNTIME_CALL_COMPLETED
    assert second_events[-1].normalized_type is EventType.RUNTIME_CALL_COMPLETED


async def test_claude_pre_start_interrupt_closes_call_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def unexpected_start(*_args: object, **_kwargs: object) -> _ClaudeProcess:
        raise AssertionError("cancelled task must not start a provider process")

    monkeypatch.setattr(
        "accretion.runtimes.claude.asyncio.create_subprocess_exec", unexpected_start
    )
    runtime = ClaudeRuntime()
    session = await runtime.create_session(SessionConfig(run_id=new_id("run"), workspace=tmp_path))
    run = await runtime.submit(session, _task())

    await runtime.interrupt(run)
    await runtime.interrupt(run)

    events = [event async for event in runtime.events(run)]
    assert [event.normalized_type for event in events] == [EventType.RUNTIME_CALL_CANCELLED]
