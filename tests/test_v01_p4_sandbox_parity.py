"""Egress policy parity across the live runtime adapters (V01-P4-001).

The criterion is that a denied external capability cannot be reached through a
provider's native escape path. Codex is network-denied by its own sandbox; Claude Code
exposes no equivalent switch on the pinned version, so its egress is narrowed by tool
policy instead. These tests pin that policy so it cannot quietly widen again.

An honest limit, stated rather than implied: a deny list enumerates, and an interpreter
reached through an allowed command can still open a socket. This is defence in depth,
not equivalence with an OS-level sandbox.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from accretion.contracts import SessionConfig, TaskEnvelope
from accretion.ids import new_id
from accretion.runtimes.claude import ClaudeRuntime
from accretion.runtimes.opencode import OpencodeRuntime


class _Stdout:
    def __init__(self) -> None:
        self.lines = [
            b'{"type":"system","subtype":"init"}\n',
            b'{"type":"result","is_error":false}\n',
            b"",
        ]

    async def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""


class _Stderr:
    async def read(self) -> bytes:
        return b""


class _Process:
    def __init__(self) -> None:
        self.stdout = _Stdout()
        self.stderr = _Stderr()
        self.returncode = 0

    async def wait(self) -> int:
        return 0

    def terminate(self) -> None:  # pragma: no cover - not reached
        self.returncode = 0

    def kill(self) -> None:  # pragma: no cover - not reached
        self.returncode = 0


def _task() -> TaskEnvelope:
    return TaskEnvelope(
        task_id=new_id("task"),
        project_id=new_id("project"),
        objective="Exercise the egress policy.",
    )


async def claude_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    captured: list[list[str]] = []

    async def start(*command: str, **kwargs: Any) -> _Process:
        del kwargs
        captured.append(list(command))
        return _Process()

    monkeypatch.setattr(
        "accretion.runtimes.claude.asyncio.create_subprocess_exec", start
    )
    runtime = ClaudeRuntime()
    session = await runtime.create_session(
        SessionConfig(run_id=new_id("run"), workspace=tmp_path)
    )
    run = await runtime.submit(session, _task())
    async for _ in runtime.events(run):
        pass
    await asyncio.sleep(0)
    return captured[0]


def flag(argv: list[str], name: str) -> list[str]:
    return argv[argv.index(name) + 1].split(",")


@pytest.mark.acceptance("V01-P4-001")
async def test_claude_denies_every_direct_egress_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    denied = flag(await claude_argv(monkeypatch, tmp_path), "--disallowedTools")

    for command in ("curl", "wget", "nc", "ssh", "scp", "rsync", "telnet"):
        assert f"Bash({command}*)" in denied, command
    # Fetching or mutating a remote repository is egress too.
    for command in ("git push", "git fetch", "git pull", "git clone", "git remote"):
        assert f"Bash({command}*)" in denied, command
    # Package installers are arbitrary remote code execution.
    for command in ("pip", "uv add", "uv pip", "npm install", "npx", "pnpm", "yarn"):
        assert f"Bash({command}*)" in denied, command
    # Provider-native fetch is denied as well as withheld from --tools.
    assert "WebFetch" in denied
    assert "WebSearch" in denied


@pytest.mark.acceptance("V01-P4-001")
async def test_claude_allows_no_prefix_that_matches_an_arbitrary_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`Bash(uv run*)` matched `uv run python -c "<anything>"`, so it constrained nothing."""

    allowed = flag(await claude_argv(monkeypatch, tmp_path), "--allowedTools")

    assert "Bash(uv run*)" not in allowed
    assert "Bash(npm run*)" not in allowed
    for rule in allowed:
        if not rule.startswith("Bash("):
            continue
        body = rule[len("Bash(") : -1]
        # Every bash rule must name a concrete subcommand, not just a runner.
        assert body not in {"uv run*", "npm run*", "*"}, rule
        assert not body.startswith(("python", "node", "sh", "bash", "eval")), rule


async def test_opencode_denies_bash_by_default_and_webfetch_outright() -> None:
    """The third adapter's posture, for comparison: deny-by-default with an allowlist."""

    config = OpencodeRuntime()._server_config()["permission"]

    assert config["webfetch"] == "deny"
    assert config["external_directory"] == "deny"
    assert config["bash"]["*"] == "deny"
    assert config["bash"]["git status*"] == "allow"


def test_codex_and_claude_state_their_egress_posture_explicitly() -> None:
    """A reader must be able to see how each adapter denies egress, and that they differ.

    Codex refuses at the sandbox; Claude refuses by policy. Neither is silent about it.
    """

    codex = Path("src/accretion/runtimes/codex.py").read_text()
    claude = Path("src/accretion/runtimes/claude.py").read_text()

    assert '"sandbox_workspace_write": {"network_access": False}' in codex
    assert "_DENIED_TOOLS" in claude
    assert "--disallowedTools" in claude
    # The asymmetry is documented where a maintainer will meet it.
    assert "network-denied" in claude
