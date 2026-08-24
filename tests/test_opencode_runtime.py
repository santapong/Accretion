import asyncio
from pathlib import Path

import pytest

from accretion.contracts import (
    EventType,
    Provider,
    RunRef,
    SessionConfig,
    SessionRef,
    TaskEnvelope,
)
from accretion.ids import new_id
from accretion.redaction import redact
from accretion.runtimes.opencode import (
    _EVENT_PATH,
    OpencodeProtocolError,
    OpencodeRuntime,
)


def _task() -> TaskEnvelope:
    return TaskEnvelope(
        task_id=new_id("task"),
        project_id=new_id("project"),
        objective="ship the adapter",
        success_criteria=["tests pass"],
    )


async def _session(
    runtime: OpencodeRuntime, workspace: Path, **kwargs: list[str]
) -> SessionRef:
    return await runtime.create_session(
        SessionConfig(run_id=new_id("run"), workspace=workspace, **kwargs)
    )


def _registered_run(runtime: OpencodeRuntime, session: SessionRef) -> RunRef:
    """Attach a live call to the adapter without spawning a server."""

    call_id = new_id("runtime_call")
    run = RunRef(
        run_id=session.run_id, session_id=session.session_id, runtime_call_id=call_id
    )
    runtime.queues[call_id] = asyncio.Queue()
    runtime.run_refs[call_id] = run
    runtime.native_to_call["ses_native"] = call_id
    return run


def test_normalize_maps_the_opencode_event_vocabulary() -> None:
    normalize = OpencodeRuntime._normalize
    # Only an explicitly busy status means the provider picked the turn up.
    assert normalize("session.status", {"status": {"type": "busy"}}) == (
        EventType.RUNTIME_CALL_STARTED
    )
    assert normalize("session.status", {"status": {"type": "retry"}}) == EventType.RUN_PROGRESS
    assert normalize("session.status", {"status": {"type": "idle"}}) == EventType.RUN_PROGRESS
    assert normalize("session.idle", {}) == EventType.RUNTIME_CALL_COMPLETED
    assert normalize("session.diff", {}) == EventType.FILE_CHANGED
    assert normalize("permission.updated", {}) == EventType.APPROVAL_REQUIRED
    assert normalize("todo.updated", {}) == EventType.RUN_PROGRESS


def test_normalize_treats_an_aborted_message_as_cancellation_not_failure() -> None:
    normalize = OpencodeRuntime._normalize
    assert normalize("session.error", {"error": {"name": "MessageAbortedError"}}) == (
        EventType.RUNTIME_CALL_CANCELLED
    )
    assert normalize("session.error", {"error": {"name": "ProviderAuthError"}}) == (
        EventType.RUNTIME_CALL_FAILED
    )
    assert normalize("session.error", {}) == EventType.RUNTIME_CALL_FAILED


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("pending", EventType.TOOL_REQUESTED),
        ("running", EventType.TOOL_STARTED),
        ("completed", EventType.TOOL_COMPLETED),
        ("error", EventType.TOOL_FAILED),
        ("surprise", EventType.RUN_PROGRESS),
    ],
)
def test_normalize_covers_every_tool_state(status: str, expected: EventType) -> None:
    properties = {"part": {"type": "tool", "state": {"status": status}}}
    assert OpencodeRuntime._normalize("message.part.updated", properties) == expected


def test_redaction_scrubs_session_ids_so_events_must_resolve_on_raw_properties() -> None:
    """Guards the ordering inside _handle_event.

    redact() matches any key like session[_-]?id, so resolving a call against redacted
    properties would look up "[REDACTED]" and silently drop the entire event stream.
    """

    assert redact({"sessionID": "ses_abc"})["sessionID"] == "[REDACTED]"
    assert OpencodeRuntime._native_id("session.idle", {"sessionID": "ses_abc"}) == "ses_abc"
    assert OpencodeRuntime._native_id("session.idle", redact({"sessionID": "ses_abc"})) == (
        "[REDACTED]"
    )


def test_file_edited_is_unattributable_and_never_resolves_to_a_call() -> None:
    # file.edited carries only a path, so on a shared server it cannot be tied to one run.
    assert OpencodeRuntime._native_id("file.edited", {"file": "src/a.py"}) is None
    assert OpencodeRuntime._native_id("message.part.updated", {"part": {"sessionID": "s"}}) == "s"
    assert OpencodeRuntime._native_id("message.part.updated", {"part": "not-a-dict"}) is None


def test_tool_policy_denies_network_reach_by_default() -> None:
    config = SessionConfig(run_id=new_id("run"), workspace=Path("/tmp"), denied_tools=["bash"])
    policy = OpencodeRuntime._tool_policy(config)
    assert policy["webfetch"] is False
    assert policy["websearch"] is False
    assert policy["read"] is True
    # An explicit denial overrides the default allowance.
    assert policy["bash"] is False


def test_server_config_carries_no_mcp_gateway() -> None:
    """A shared server cannot hold a per-run gateway id, so it must hold none at all."""

    config = OpencodeRuntime(gateway_environment={"ACCRETION_DATABASE_URL": "postgres://x"})
    server_config = config._server_config()
    assert "mcp" not in server_config
    assert server_config["permission"]["webfetch"] == "deny"
    assert server_config["permission"]["external_directory"] == "deny"


async def test_capability_requests_are_refused_rather_than_silently_dropped(
    tmp_path: Path,
) -> None:
    runtime = OpencodeRuntime()
    session = await _session(runtime, tmp_path, allowed_tools=["echo"])
    with pytest.raises(OpencodeProtocolError, match="capability gateway"):
        runtime._prompt_body(session, _task())


async def test_model_must_be_provider_slash_model(tmp_path: Path) -> None:
    runtime = OpencodeRuntime(model="not-a-qualified-model")
    session = await _session(runtime, tmp_path)
    with pytest.raises(OpencodeProtocolError, match="providerID/modelID"):
        runtime._prompt_body(session, _task())


async def test_prompt_body_splits_the_model_and_scopes_tools(tmp_path: Path) -> None:
    runtime = OpencodeRuntime(model="opencode/some-model")
    session = await _session(runtime, tmp_path)
    body = runtime._prompt_body(session, _task())
    assert body["model"] == {"providerID": "opencode", "modelID": "some-model"}
    assert body["tools"]["webfetch"] is False
    assert "ship the adapter" in body["parts"][0]["text"]


async def test_startup_failure_emits_one_terminal_failure_and_closes_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = OpencodeRuntime()

    async def fail_start() -> None:
        raise FileNotFoundError("opencode is not installed")

    monkeypatch.setattr(runtime, "_ensure_server", fail_start)
    session = await _session(runtime, tmp_path)
    run = await runtime.submit(session, _task())
    events = [event async for event in runtime.events(run)]
    assert [event.normalized_type for event in events] == [EventType.RUNTIME_CALL_FAILED]
    assert events[0].provider is Provider.OPENCODE


async def test_double_interrupt_yields_exactly_one_cancellation(tmp_path: Path) -> None:
    runtime = OpencodeRuntime()
    session = await _session(runtime, tmp_path)
    run = _registered_run(runtime, session)

    await runtime.interrupt(run)
    await runtime.interrupt(run)

    events = [event async for event in runtime.events(run)]
    assert [event.normalized_type for event in events] == [EventType.RUNTIME_CALL_CANCELLED]


async def test_idle_before_the_turn_goes_busy_does_not_complete_the_call(
    tmp_path: Path,
) -> None:
    """A session can report idle between create and prompt; that is not a completion."""

    runtime = OpencodeRuntime()
    session = await _session(runtime, tmp_path)
    run = _registered_run(runtime, session)
    idle = {"type": "session.idle", "properties": {"sessionID": "ses_native"}}
    busy = {
        "type": "session.status",
        "properties": {"sessionID": "ses_native", "status": {"type": "busy"}},
    }

    await runtime._handle_event(idle)
    assert runtime._call_id(run) not in runtime.terminal_calls

    await runtime._handle_event(busy)
    await runtime._handle_event(idle)

    events = [event async for event in runtime.events(run)]
    assert [event.normalized_type for event in events] == [
        EventType.RUNTIME_CALL_STARTED,
        EventType.RUNTIME_CALL_COMPLETED,
    ]


async def test_events_correlate_to_the_call_and_redact_the_persisted_payload(
    tmp_path: Path,
) -> None:
    runtime = OpencodeRuntime()
    session = await _session(runtime, tmp_path)
    run = _registered_run(runtime, session)
    call_id = runtime._call_id(run)

    await runtime._handle_event(
        {
            "type": "session.status",
            "properties": {"sessionID": "ses_native", "status": {"type": "busy"}},
        }
    )

    event = await runtime.queues[call_id].get()
    assert event.correlation_id == call_id
    # Sequencing belongs to the persistence layer, never to the adapter.
    assert event.sequence == 0
    assert event.adapter_version == "opencode-server-p2-v1"
    # Resolution used the raw id; the stored payload is still redacted.
    assert event.payload["provider_extension"]["sessionID"] == "[REDACTED]"


async def test_dead_event_stream_fails_every_active_call_once(tmp_path: Path) -> None:
    runtime = OpencodeRuntime()
    session = await _session(runtime, tmp_path)
    run = _registered_run(runtime, session)

    await runtime._fail_active_runs("opencode event stream failed")
    await runtime._fail_active_runs("opencode event stream failed")

    events = [event async for event in runtime.events(run)]
    assert [event.normalized_type for event in events] == [EventType.RUNTIME_CALL_FAILED]
    assert "event stream failed" in events[0].payload["error"]


def test_events_are_read_from_the_global_bus_and_unwrapped() -> None:
    """GET /event yields only server.connected and heartbeats; sessions live on the global bus."""

    assert _EVENT_PATH == "/global/event"
    envelope = {
        "directory": "/tmp/work",
        "project": "global",
        "payload": {"id": "evt_1", "type": "session.idle", "properties": {"sessionID": "ses_a"}},
    }
    assert OpencodeRuntime._unwrap(envelope)["type"] == "session.idle"
    # A bare event passes through untouched, so the endpoint stays swappable.
    bare = {"type": "session.idle", "properties": {"sessionID": "ses_a"}}
    assert OpencodeRuntime._unwrap(bare) == bare


async def test_streaming_deltas_are_dropped_from_the_durable_trace(tmp_path: Path) -> None:
    """message.part.delta is one event per token; persisting them would bury the trace."""

    runtime = OpencodeRuntime()
    session = await _session(runtime, tmp_path)
    run = _registered_run(runtime, session)
    call_id = runtime._call_id(run)

    await runtime._handle_event(
        {
            "type": "message.part.delta",
            "properties": {"sessionID": "ses_native", "field": "text", "delta": "READY"},
        }
    )
    assert runtime.queues[call_id].empty()
