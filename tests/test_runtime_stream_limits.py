"""Runtime stdout must survive a protocol line larger than asyncio's default.

Every provider frames its protocol as one JSON object per line, and
`asyncio.create_subprocess_exec` defaults its `StreamReader` to a 64 KiB limit.
A tool result carrying a modest file therefore overruns the limit and
`readline()` raises `ValueError: Separator is found, but chunk is longer than
limit`, killing the call. A real run died this way against a repository whose
files are 22-30 KB each.

These tests carry no `@pytest.mark.acceptance`: this is a regression guard for a
defect, not a claim about an SDD criterion.

**Why the first test spawns a real process.** Every other subprocess test in this
repository monkeypatches `create_subprocess_exec` and returns a hand-rolled stub
(`tests/test_repeatable_runtimes.py:240`), whose `readline()` is
`self.lines.pop(0)`. A plain Python object has no `StreamReader` and therefore no
limit to exceed, so a monkeypatched test passes identically before and after the
fix. That is precisely why a full suite could stay green while this shipped. Only
a real child process reproduces it.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from accretion.contracts import EventType, Provider, RunRef, SessionConfig, TaskEnvelope
from accretion.ids import new_id
from accretion.runtimes.claude import ClaudeRuntime
from accretion.runtimes.codex import CodexRuntime
from accretion.runtimes.common import RUNTIME_STREAM_LIMIT
from accretion.runtimes.opencode import OpencodeRuntime

# asyncio.streams._DEFAULT_LIMIT. Hard-coded rather than imported: the point is
# the number this code used to inherit, not whatever a future asyncio picks.
ASYNCIO_DEFAULT_LIMIT = 64 * 1024

# Comfortably past the old ceiling, and past it by more than a rounding error so
# the test cannot stop reproducing the bug through some incidental change.
OVERSIZED_TEXT = "x" * (200 * 1024)


def fake_binary(path: Path, script: str) -> str:
    """Write an executable stand-in for a vendor CLI and return its path."""
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def claude_stream(tmp_path: Path) -> str:
    """A fake `claude` emitting one stream-json line larger than 64 KiB.

    The payload is built in Python and `cat`-ed by the script so the byte sizes
    are exact rather than dependent on shell quoting.
    """
    oversized = json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": OVERSIZED_TEXT}]}}
    )
    assert len(oversized.encode()) > ASYNCIO_DEFAULT_LIMIT, (
        "the fixture must exceed the limit it exists to test"
    )
    payload = tmp_path / "claude-stream.ndjson"
    payload.write_text(
        json.dumps({"type": "system", "subtype": "init"})
        + "\n"
        + oversized
        + "\n"
        + json.dumps({"type": "result", "is_error": False})
        + "\n"
    )
    return fake_binary(tmp_path / "claude-oversized", f"#!/bin/sh\ncat {payload}\n")


def task() -> TaskEnvelope:
    return TaskEnvelope(
        task_id=new_id("task"),
        project_id=new_id("project"),
        objective="Emit one oversized protocol line.",
    )


async def test_claude_survives_a_protocol_line_larger_than_the_asyncio_default(
    tmp_path: Path,
) -> None:
    """The reproduction. Fails before the fix with the asyncio ValueError."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = ClaudeRuntime(command=claude_stream(tmp_path))
    session = await runtime.create_session(
        SessionConfig(run_id=new_id("run"), workspace=workspace)
    )
    run: RunRef = await runtime.submit(session, task())
    events = [event async for event in runtime.events(run)]

    normalized = [event.normalized_type for event in events]
    assert EventType.RUNTIME_CALL_FAILED not in normalized, (
        f"oversized line was not read: {[str(e.payload.get('error')) for e in events]}"
    )
    assert normalized[-1] is EventType.RUNTIME_CALL_COMPLETED
    assert EventType.RUN_PROGRESS in normalized

    # The oversized message must arrive intact, not truncated to fit.
    progress = [e for e in events if e.normalized_type is EventType.RUN_PROGRESS]
    assert any(
        len(json.dumps(event.payload)) > ASYNCIO_DEFAULT_LIMIT for event in progress
    ), "the large message was dropped or truncated rather than delivered"


async def test_claude_reports_the_limit_by_name_when_a_line_exceeds_even_the_new_ceiling(
    tmp_path: Path,
) -> None:
    """A raised ceiling makes the crash rarer; this makes it legible.

    The old failure said only "Separator is found, but chunk is longer than
    limit" and labelled itself `process/exit`, pointing the operator at a vendor
    CLI that had not exited.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = tmp_path / "huge.ndjson"
    # One line past RUNTIME_STREAM_LIMIT, written by the child rather than held
    # in the test process.
    script = (
        "#!/bin/sh\n"
        'printf \'{"type":"system","subtype":"init"}\\n\'\n'
        f"head -c {RUNTIME_STREAM_LIMIT + 4096} /dev/zero | tr '\\0' 'x'\n"
        "printf '\\n'\n"
    )
    payload.write_text("")
    runtime = ClaudeRuntime(command=fake_binary(tmp_path / "claude-huge", script))
    session = await runtime.create_session(
        SessionConfig(run_id=new_id("run"), workspace=workspace)
    )
    run = await runtime.submit(session, task())
    events = [event async for event in runtime.events(run)]

    failures = [e for e in events if e.normalized_type is EventType.RUNTIME_CALL_FAILED]
    assert failures, "a line past the ceiling must still fail the call"
    error = str(failures[-1].payload.get("error", ""))
    assert "limit" in error.lower()
    assert str(RUNTIME_STREAM_LIMIT) in error or "16" in error, (
        f"the failure must name the limit it hit, got: {error}"
    )
    # It is a reader-side failure: the child did not exit on its own.
    assert failures[-1].native_type != "process/exit"


@pytest.mark.parametrize(
    ("name", "build"),
    [
        ("claude", lambda cmd: ClaudeRuntime(command=cmd)),
        ("codex", lambda cmd: CodexRuntime(command=cmd)),
        ("opencode", lambda cmd: OpencodeRuntime(command=cmd)),
    ],
)
async def test_every_adapter_passes_an_explicit_stream_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, build
) -> None:
    """Guards the kwarg on all three adapters.

    **This cannot reproduce the failure.** The fake below has no `StreamReader`,
    so there is no limit to exceed and it would pass with or without the fix. It
    exists only so that removing `limit=` from a spawn site fails a test. The
    Claude test above is the one that proves the behaviour; faking Codex's and
    opencode's full JSON-RPC handshakes to get a behavioural test for each is
    disproportionate, and pretending this is equivalent evidence would be
    dishonest.
    """
    seen: list[dict[str, object]] = []

    async def start(*args: str, **kwargs: object):
        seen.append(kwargs)
        raise OSError("stop after capturing the spawn arguments")

    monkeypatch.setattr(
        f"accretion.runtimes.{name}.asyncio.create_subprocess_exec", start, raising=True
    )
    runtime = build(str(tmp_path / name))
    session = await runtime.create_session(
        SessionConfig(run_id=new_id("run"), workspace=tmp_path)
    )
    try:
        run = await runtime.submit(session, task())
        [event async for event in runtime.events(run)]
    except Exception:  # noqa: BLE001 - the spawn is meant to fail; we want the kwargs
        pass

    assert seen, f"{name} did not spawn a subprocess"
    limit = seen[0].get("limit")
    assert isinstance(limit, int), f"{name} spawns without an explicit stream limit"
    assert limit >= RUNTIME_STREAM_LIMIT


def test_the_constant_is_bounded_and_above_the_asyncio_default() -> None:
    """Unbounded would let a runaway child exhaust memory; 64 KiB is the bug."""
    assert RUNTIME_STREAM_LIMIT > ASYNCIO_DEFAULT_LIMIT
    assert RUNTIME_STREAM_LIMIT <= 64 * 1024 * 1024


def test_provider_enum_is_reachable() -> None:
    """Cheap import guard: a broken module here would take the file's other
    tests with it, and this file is where the regression evidence lives."""
    assert Provider.CLAUDE.value == "CLAUDE"
