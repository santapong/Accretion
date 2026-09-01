"""Run the three live-provider acceptance criteria and record what happened.

`V01-P0-002`, `V01-P0-004` and `V01-P4-008` cannot be proven offline: they are
claims about real vendor CLIs -- that the Codex App Server carries two
independent threads, that Claude and Codex run concurrently in disjoint
worktrees, and that a Claude-produced artifact is independently verified by
Codex. CI never sets `ACCRETION_LIVE_PROVIDERS=1`, so these are recorded as
`manual` criteria in `docs/acceptance/criteria.toml`, and this script is what
produces the evidence a `manual` record points at.

It is deliberately not a pytest module. A `manual` record that quietly became a
skipped test would be worse than an honest manual one: the acceptance harness
treats a claimed test that reports no outcome as FAILING, and a skip is exactly
that.

Usage:

    ACCRETION_LIVE_PROVIDERS=1 uv run python scripts/live_acceptance.py \\
        --output docs/releases/v0.3/evidence/live-acceptance-2026-09-01.md

Every check reports PASS or FAIL with the observable facts it rests on; the exit
code is non-zero if any MUST criterion failed, so a bad run cannot quietly
produce a green-looking document.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from accretion.contracts import (  # noqa: E402
    AgentEvent,
    AgentRuntime,
    EventType,
    RunRef,
    RuntimeStatus,
    SessionConfig,
    TaskEnvelope,
)
from accretion.ids import new_id  # noqa: E402
from accretion.runtimes.claude import ClaudeRuntime  # noqa: E402
from accretion.runtimes.codex import CodexRuntime  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class CheckResult:
    criterion: str
    obligation: str
    title: str
    passed: bool
    observations: list[str] = field(default_factory=list)
    error: str | None = None


def initialize_repository(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "live@example.com"], check=True
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Accretion Live"], check=True
    )


def harmless_task(objective: str) -> TaskEnvelope:
    return TaskEnvelope(
        task_id=new_id("task"),
        project_id=new_id("project"),
        objective=objective,
    )


async def collect(runtime: AgentRuntime, run: RunRef) -> list[AgentEvent]:
    return [event async for event in runtime.events(run)]


async def check_codex_two_threads(workdir: Path) -> CheckResult:
    result = CheckResult(
        criterion="V01-P0-002",
        obligation="MUST",
        title="Codex App Server carries at least two independent threads",
        passed=False,
    )
    first = workdir / "codex-first"
    second = workdir / "codex-second"
    initialize_repository(first)
    initialize_repository(second)
    runtime = CodexRuntime()
    try:
        health = await runtime.health()
        result.observations.append(
            f"health: {health.status.value}, runtime_version={health.runtime_version}, "
            f"auth_mode={health.auth_mode.value}"
        )
        if health.status is not RuntimeStatus.READY:
            result.error = f"codex runtime is {health.status.value}"
            return result

        first_session = await runtime.create_session(
            SessionConfig(run_id=new_id("run"), workspace=first)
        )
        second_session = await runtime.create_session(
            SessionConfig(run_id=new_id("run"), workspace=second)
        )
        first_run, second_run = await asyncio.gather(
            runtime.submit(first_session, harmless_task(READY_OBJECTIVE)),
            runtime.submit(second_session, harmless_task(READY_OBJECTIVE)),
        )
        first_events, second_events = await asyncio.gather(
            collect(runtime, first_run), collect(runtime, second_run)
        )
        result.observations.append(
            f"thread 1 native_run_id={first_run.native_run_id}, "
            f"{len(first_events)} events, terminal="
            f"{first_events[-1].normalized_type.value}"
        )
        result.observations.append(
            f"thread 2 native_run_id={second_run.native_run_id}, "
            f"{len(second_events)} events, terminal="
            f"{second_events[-1].normalized_type.value}"
        )
        result.observations.append(
            f"sessions are distinct: {first_session.session_id != second_session.session_id}"
        )
        result.passed = (
            first_run.native_run_id != second_run.native_run_id
            and first_events[-1].normalized_type is EventType.RUNTIME_CALL_COMPLETED
            and second_events[-1].normalized_type is EventType.RUNTIME_CALL_COMPLETED
        )
    except Exception as exc:  # noqa: BLE001 - the document records the failure
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        await runtime.close()
    return result


READY_OBJECTIVE = "Create no files and invoke no tools. Reply with exactly READY."


async def check_concurrent_worktrees(workdir: Path) -> CheckResult:
    result = CheckResult(
        criterion="V01-P0-004",
        obligation="MUST",
        title="Claude and Codex run concurrently in separate worktrees",
        passed=False,
    )
    codex_workspace = workdir / "concurrent-codex"
    claude_workspace = workdir / "concurrent-claude"
    initialize_repository(codex_workspace)
    initialize_repository(claude_workspace)
    codex = CodexRuntime()
    claude = ClaudeRuntime(model=os.getenv("ACCRETION_CLAUDE_LIVE_MODEL"))
    try:
        codex_session, claude_session = await asyncio.gather(
            codex.create_session(
                SessionConfig(run_id=new_id("run"), workspace=codex_workspace)
            ),
            claude.create_session(
                SessionConfig(run_id=new_id("run"), workspace=claude_workspace)
            ),
        )
        started = datetime.now(UTC)
        codex_run, claude_run = await asyncio.gather(
            codex.submit(codex_session, harmless_task(READY_OBJECTIVE)),
            claude.submit(claude_session, harmless_task(READY_OBJECTIVE)),
        )
        codex_events, claude_events = await asyncio.gather(
            collect(codex, codex_run), collect(claude, claude_run)
        )
        elapsed = (datetime.now(UTC) - started).total_seconds()
        result.observations.append(f"codex workspace: {codex_workspace.name}")
        result.observations.append(f"claude workspace: {claude_workspace.name}")
        result.observations.append(
            f"workspaces are disjoint: "
            f"{not codex_workspace.samefile(claude_workspace)}"
        )
        result.observations.append(
            f"codex terminal={codex_events[-1].normalized_type.value}, "
            f"claude terminal={claude_events[-1].normalized_type.value}, "
            f"both dispatched concurrently in {elapsed:.1f}s"
        )
        # No mutable shared state: neither provider may leave a file in the
        # other's working tree. `.git/` is excluded deliberately - every
        # `git init` writes the same hook samples and config, so comparing it
        # would report identical names that have nothing to do with either
        # provider.
        def worktree_files(root: Path) -> set[str]:
            return {
                str(item.relative_to(root))
                for item in root.rglob("*")
                if item.is_file() and ".git" not in item.relative_to(root).parts
            }

        codex_files = worktree_files(codex_workspace)
        claude_files = worktree_files(claude_workspace)
        shared = codex_files & claude_files
        result.observations.append(
            f"codex worktree files: {sorted(codex_files) or 'none'}; "
            f"claude worktree files: {sorted(claude_files) or 'none'}"
        )
        result.observations.append(
            f"working-tree paths present in both workspaces: {sorted(shared) or 'none'}"
        )
        result.passed = (
            codex_events[-1].normalized_type is EventType.RUNTIME_CALL_COMPLETED
            and claude_events[-1].normalized_type is EventType.RUNTIME_CALL_COMPLETED
            and not codex_workspace.samefile(claude_workspace)
            and not shared
        )
    except Exception as exc:  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        await codex.close()
    return result


async def check_cross_provider_verification(workdir: Path) -> CheckResult:
    result = CheckResult(
        criterion="V01-P4-008",
        obligation="SHOULD",
        title="A Claude-produced artifact is independently verified by Codex",
        passed=False,
    )
    workspace = workdir / "cross-provider"
    initialize_repository(workspace)
    claude = ClaudeRuntime(model=os.getenv("ACCRETION_CLAUDE_LIVE_MODEL"))
    codex = CodexRuntime()
    try:
        claude_session = await claude.create_session(
            SessionConfig(run_id=new_id("run"), workspace=workspace)
        )
        produce = harmless_task(
            "Create a single file named artifact.txt in the current directory. "
            "Its only content must be the exact line: ACCRETION LIVE ARTIFACT. "
            "Do not create or modify any other file."
        )
        claude_run = await claude.submit(claude_session, produce)
        claude_events = await collect(claude, claude_run)
        artifact = workspace / "artifact.txt"
        produced = artifact.exists()
        result.observations.append(
            f"claude terminal={claude_events[-1].normalized_type.value}, "
            f"artifact.txt written={produced}"
        )
        if not produced:
            result.error = "claude did not produce artifact.txt"
            return result
        content = artifact.read_text().strip()
        result.observations.append(f"artifact content: {content!r}")

        # Codex verifies independently: a separate session, told only what to
        # check, never what Claude claimed.
        codex_session = await codex.create_session(
            SessionConfig(run_id=new_id("run"), workspace=workspace)
        )
        verify = harmless_task(
            "Read the file artifact.txt in the current directory. If its only "
            "content is exactly the line 'ACCRETION LIVE ARTIFACT', reply with "
            "exactly VERIFIED. Otherwise reply with exactly REJECTED. Create and "
            "modify no files."
        )
        codex_run = await codex.submit(codex_session, verify)
        codex_events = await collect(codex, codex_run)
        transcript = " ".join(
            str(event.payload) for event in codex_events if event.payload
        )
        verified = "VERIFIED" in transcript.upper()
        result.observations.append(
            f"codex terminal={codex_events[-1].normalized_type.value}, "
            f"verdict token present={verified}"
        )
        result.observations.append(
            f"verifier session {codex_session.session_id} is not the producer session "
            f"{claude_session.session_id}"
        )
        result.passed = (
            content == "ACCRETION LIVE ARTIFACT"
            and verified
            and codex_events[-1].normalized_type is EventType.RUNTIME_CALL_COMPLETED
        )
    except Exception as exc:  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        await codex.close()
    return result


def git_commit() -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def cli_version(command: str) -> str:
    try:
        completed = subprocess.run(
            [command, "--version"], check=False, capture_output=True, text=True, timeout=60
        )
        return (completed.stdout or completed.stderr).strip().splitlines()[0]
    except Exception as exc:  # noqa: BLE001
        return f"unavailable ({type(exc).__name__})"


def render(results: list[CheckResult], *, started: datetime) -> str:
    lines = [
        f"# Live acceptance evidence — {started.date().isoformat()}",
        "",
        "Produced by `scripts/live_acceptance.py` against signed-in vendor CLIs.",
        "These three criteria cannot run in CI (`ACCRETION_LIVE_PROVIDERS` is never",
        "set there), so they are recorded as `manual` in",
        "`docs/acceptance/criteria.toml` and this file is the evidence those records",
        "point at. A `manual` record goes stale after 180 days: re-run this script and",
        "update `last_verified` before then.",
        "",
        "## Run",
        "",
        f"- Started: `{started.isoformat()}`",
        f"- Repository commit: `{git_commit()}`",
        f"- Codex CLI: `{cli_version('codex')}`",
        f"- Claude CLI: `{cli_version('claude')}`",
        f"- Host: `{os.uname().sysname} {os.uname().release}`",
        "",
        "## Results",
        "",
        "| Criterion | Obligation | Result | Claim |",
        "| --- | --- | --- | --- |",
    ]
    for item in results:
        verdict = "PASS" if item.passed else "FAIL"
        lines.append(
            f"| `{item.criterion}` | {item.obligation} | **{verdict}** | {item.title} |"
        )
    lines.append("")
    for item in results:
        lines.append(f"### {item.criterion} — {item.title}")
        lines.append("")
        lines.append(f"Result: **{'PASS' if item.passed else 'FAIL'}**")
        lines.append("")
        for observation in item.observations:
            lines.append(f"- {observation}")
        if item.error:
            lines.append(f"- error: `{item.error}`")
        lines.append("")
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="evidence file to write")
    args = parser.parse_args()

    if os.getenv("ACCRETION_LIVE_PROVIDERS") != "1":
        print(
            "refusing to run: set ACCRETION_LIVE_PROVIDERS=1 to use signed-in "
            "provider sessions",
            file=sys.stderr,
        )
        return 2

    started = datetime.now(UTC)
    with TemporaryDirectory(prefix="accretion-live-") as raw:
        workdir = Path(raw)
        results = [
            await check_codex_two_threads(workdir),
            await check_concurrent_worktrees(workdir),
            await check_cross_provider_verification(workdir),
        ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(results, started=started) + "\n")

    for item in results:
        print(f"{'PASS' if item.passed else 'FAIL'}  {item.criterion}  {item.title}")
        if item.error:
            print(f"      {item.error}")
    print(f"\nevidence written to {args.output}")

    return 0 if all(item.passed for item in results if item.obligation == "MUST") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
