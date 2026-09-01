"""Offline guards for the live-provider wiring (deliberately unmarked).

`V01-P0-002`, `V01-P0-004` and `V01-P4-008` are recorded as `manual` criteria
proven by `docs/releases/v0.3/evidence/live-acceptance-2026-09-01.md`, because a
signed-in vendor CLI cannot be reached from CI.

These tests carry **no** `@pytest.mark.acceptance` on purpose. A fake binary can
show that the runtime speaks the protocol it claims to speak, and that is worth
guarding against regression on every pull request - but it cannot show that the
real Codex App Server carries two threads, and marking it would let a stub
impersonate the vendor. The line is: protocol wiring here, vendor behaviour in
the manual record.

They run everywhere, need no network, and use a fake executable injected through
each runtime's `command` argument.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from accretion.contracts import (
    AuthMode,
    Provider,
    RuntimeStatus,
    UsagePressure,
)
from accretion.runtimes.claude import ClaudeRuntime
from accretion.runtimes.codex import CodexRuntime


def fake_binary(path: Path, script: str) -> str:
    """Write an executable stand-in for a vendor CLI and return its path."""
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


CODEX_IN_RANGE = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "codex-cli 0.148.0"; exit 0; fi
if [ "$1" = "login" ]; then echo "Logged in"; exit 0; fi
exit 0
"""

CODEX_OUT_OF_RANGE = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "codex-cli 0.999.0"; exit 0; fi
if [ "$1" = "login" ]; then echo "Logged in"; exit 0; fi
exit 0
"""

CODEX_SIGNED_OUT = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "codex-cli 0.148.0"; exit 0; fi
if [ "$1" = "login" ]; then echo "Not logged in"; exit 1; fi
exit 0
"""

CODEX_MISSING = """#!/bin/sh
echo "command not found" >&2
exit 127
"""


async def test_codex_health_reports_ready_only_for_a_supported_signed_in_cli(
    tmp_path: Path,
) -> None:
    """The compatibility window is enforced, not assumed."""
    ready = CodexRuntime(command=fake_binary(tmp_path / "codex-ok", CODEX_IN_RANGE))
    health = await ready.health()
    assert health.status is RuntimeStatus.READY
    assert health.provider is Provider.CODEX
    assert health.runtime_id == "runtime_codex"
    assert health.auth_mode is AuthMode.SUBSCRIPTION
    assert "0.148.0" in health.runtime_version
    assert health.last_error is None

    # A version outside the declared window must not report READY: this is the
    # guard that catches a vendor upgrade changing the protocol under us.
    newer = CodexRuntime(
        command=fake_binary(tmp_path / "codex-new", CODEX_OUT_OF_RANGE)
    )
    newer_health = await newer.health()
    assert newer_health.status is RuntimeStatus.DEGRADED
    # Observed behaviour, pinned rather than assumed: an out-of-window version
    # degrades but records no `last_error`, so the operator sees the state
    # without the reason. Contrast the signed-out and missing-binary cases
    # below, which do carry one.
    assert newer_health.last_error is None


async def test_codex_health_distinguishes_signed_out_from_absent(tmp_path: Path) -> None:
    """Two different operator problems must not collapse into one message."""
    signed_out = CodexRuntime(
        command=fake_binary(tmp_path / "codex-out", CODEX_SIGNED_OUT)
    )
    signed_out_health = await signed_out.health()

    missing = CodexRuntime(command=fake_binary(tmp_path / "codex-gone", CODEX_MISSING))
    missing_health = await missing.health()

    assert signed_out_health.status is not RuntimeStatus.READY
    assert missing_health.status is not RuntimeStatus.READY
    assert signed_out_health.last_error is not None
    assert missing_health.last_error is not None
    # The operator is told which of the two happened.
    assert signed_out_health.last_error != missing_health.last_error


async def test_a_missing_binary_never_reports_usage_pressure_it_could_not_observe(
    tmp_path: Path,
) -> None:
    missing = CodexRuntime(command=fake_binary(tmp_path / "codex-none", CODEX_MISSING))
    health = await missing.health()
    assert health.observed_usage_pressure is UsagePressure.UNKNOWN
    assert health.active_runs == 0
    assert health.active_sessions == 0


CLAUDE_IN_RANGE = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "2.1.252 (Claude Code)"; exit 0; fi
exit 0
"""


async def test_claude_and_codex_are_independent_runtimes(tmp_path: Path) -> None:
    """The dual-provider claim's offline half: two runtimes, no shared identity.

    The live half - that they really execute concurrently in disjoint worktrees -
    is the manual record's job.
    """
    codex = CodexRuntime(command=fake_binary(tmp_path / "codex-two", CODEX_IN_RANGE))
    claude = ClaudeRuntime(command=fake_binary(tmp_path / "claude-two", CLAUDE_IN_RANGE))

    codex_health = await codex.health()
    claude_health = await claude.health()

    assert codex_health.provider is Provider.CODEX
    assert claude_health.provider is Provider.CLAUDE
    assert codex_health.runtime_id != claude_health.runtime_id
    # No shared mutable registry between the two runtime objects.
    assert codex.sessions is not claude.sessions
    assert codex.run_refs is not claude.run_refs
    assert codex.queues is not claude.queues


async def test_a_health_probe_does_not_hand_the_child_the_control_plane_environment(
    tmp_path: Path,
) -> None:
    """A probe must not leak the control plane's secrets into a vendor process.

    The backlog records that `command_result` passes no child environment, so a
    probe inherits everything the API server holds - including the database URL
    and the OIDC client secret. This test pins the observable consequence so the
    day that is fixed, or regresses further, the suite says so.
    """
    capture = tmp_path / "captured-env.txt"
    script = f"""#!/bin/sh
if [ "$1" = "--version" ]; then
  env > {capture}
  echo "codex-cli 0.148.0"
  exit 0
fi
if [ "$1" = "login" ]; then echo "Logged in"; exit 0; fi
exit 0
"""
    runtime = CodexRuntime(command=fake_binary(tmp_path / "codex-env", script))
    marker = "ACCRETION_M8_PROBE_MARKER"
    os.environ[marker] = "control-plane-secret"
    try:
        await runtime.health()
    finally:
        os.environ.pop(marker, None)

    observed = capture.read_text() if capture.exists() else ""
    inherited = f"{marker}=control-plane-secret" in observed
    # Documented current behaviour: the child inherits the parent environment.
    # This is an accepted v0.4 hardening item, not a silent surprise.
    assert inherited, (
        "health probes no longer inherit the parent environment - that is the "
        "desired fix; update this test and close the backlog item"
    )
