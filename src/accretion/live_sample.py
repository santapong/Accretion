from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import Field

from accretion.benchmark import AcrArchRunner
from accretion.contracts import (
    AgentEvent,
    AgentRuntime,
    BenchmarkCategory,
    BenchmarkTask,
    EventType,
    Provider,
    RunRef,
    RuntimeStatus,
    SessionConfig,
    StrictModel,
    TaskBudgets,
    TaskEnvelope,
)
from accretion.ids import new_id
from accretion.redaction import redact_text
from accretion.runtimes.claude import ClaudeRuntime
from accretion.runtimes.codex import CodexRuntime

_CATEGORIES = list(BenchmarkCategory)
_TERMINALS = {
    EventType.RUNTIME_CALL_COMPLETED,
    EventType.RUNTIME_CALL_FAILED,
    EventType.RUNTIME_CALL_CANCELLED,
}


class LiveSampleAssignment(StrictModel):
    benchmark_task_id: str
    task_version: str
    category: BenchmarkCategory
    provider: Provider


class LiveSampleResult(StrictModel):
    assignment: LiveSampleAssignment
    run_id: str
    native_run_id: str | None = None
    runtime_version: str
    terminal_event: EventType | None = None
    verified: bool = False
    duration_ms: int = Field(ge=0)
    artifact_sha256: str | None = None
    error: str | None = None


class LiveSampleReport(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    suite: Literal["ACR-ARCH-LIVE-CALIBRATION"] = "ACR-ARCH-LIVE-CALIBRATION"
    sample_size: Literal[10] = 10
    started_at: datetime
    completed_at: datetime
    provider_versions: dict[str, str]
    provider_models: dict[str, str]
    results: list[LiveSampleResult]

    @property
    def passed(self) -> bool:
        return len(self.results) == self.sample_size and all(
            item.verified and item.terminal_event is EventType.RUNTIME_CALL_COMPLETED
            for item in self.results
        )


def select_live_sample(tasks: Sequence[BenchmarkTask]) -> list[LiveSampleAssignment]:
    """Choose two frozen tasks per category and balance providers within every pair."""

    assignments: list[LiveSampleAssignment] = []
    for category in _CATEGORIES:
        category_tasks = sorted(
            (task for task in tasks if task.category is category),
            key=lambda task: task.benchmark_task_id,
        )
        if len(category_tasks) < 2:
            raise ValueError(f"category {category.value} has fewer than two tasks")
        for task, provider in zip(
            category_tasks[:2],
            (Provider.CODEX, Provider.CLAUDE),
            strict=True,
        ):
            assignments.append(
                LiveSampleAssignment(
                    benchmark_task_id=task.benchmark_task_id,
                    task_version=task.version,
                    category=category,
                    provider=provider,
                )
            )
    if len(assignments) != 10:
        raise ValueError("live calibration requires exactly ten assignments")
    return assignments


def expected_artifact(assignment: LiveSampleAssignment) -> dict[str, str]:
    return {
        "benchmark_task_id": assignment.benchmark_task_id,
        "category": assignment.category.value,
        "provider": assignment.provider.value,
        "result": "PASS",
        "task_version": assignment.task_version,
    }


def verify_artifact(path: Path, expected: dict[str, str]) -> str:
    """Independently verify the exact JSON object and return its content digest."""

    raw = path.read_bytes()
    parsed = json.loads(raw)
    if parsed != expected:
        raise ValueError("result.json does not match the exact expected object")
    return hashlib.sha256(raw).hexdigest()


async def _initialize_repository(path: Path) -> None:
    path.mkdir(parents=True)
    process = await asyncio.create_subprocess_exec(
        "git",
        "init",
        "-q",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"git init failed: {stderr.decode(errors='replace').strip()}")


async def _run_assignment(
    assignment: LiveSampleAssignment,
    runtime: AgentRuntime,
    runtime_version: str,
    workspace: Path,
) -> LiveSampleResult:
    started = time.monotonic()
    run_id = new_id("run")
    native_run_id: str | None = None
    terminal: EventType | None = None
    try:
        await _initialize_repository(workspace)
        expected = expected_artifact(assignment)
        exact_json = json.dumps(expected, sort_keys=True, separators=(",", ":"))
        task = TaskEnvelope(
            task_id=new_id("task"),
            project_id=new_id("project"),
            objective=(
                "Create result.json in the workspace root containing exactly this JSON object: "
                f"{exact_json}. Do not create or edit any other file."
            ),
            constraints=[
                "Do not use the network",
                "Do not invoke MCP or external side effects",
                "Finish after writing result.json",
            ],
            success_criteria=["result.json exactly matches the requested JSON object"],
            budgets=TaskBudgets(
                wall_time_seconds=300,
                max_turns=8,
                max_tool_calls=8,
                max_loop_iterations=1,
                max_parallel_runs=1,
            ),
        )
        session = await runtime.create_session(SessionConfig(run_id=run_id, workspace=workspace))
        run = await runtime.submit(session, task)
        native_run_id = run.native_run_id
        events = await asyncio.wait_for(
            asyncio.create_task(_collect_events(runtime, run)),
            300,
        )
        terminal_events = [item for item in events if item.normalized_type in _TERMINALS]
        terminal = terminal_events[-1].normalized_type if terminal_events else None
        if terminal is not EventType.RUNTIME_CALL_COMPLETED:
            detail = terminal_events[-1].payload if terminal_events else "no terminal event"
            raise RuntimeError(f"provider did not complete: {detail}")
        digest = verify_artifact(workspace / "result.json", expected)
        return LiveSampleResult(
            assignment=assignment,
            run_id=run_id,
            native_run_id=native_run_id,
            runtime_version=runtime_version,
            terminal_event=terminal,
            verified=True,
            duration_ms=int((time.monotonic() - started) * 1000),
            artifact_sha256=digest,
        )
    except Exception as exc:
        return LiveSampleResult(
            assignment=assignment,
            run_id=run_id,
            native_run_id=native_run_id,
            runtime_version=runtime_version,
            terminal_event=terminal,
            duration_ms=int((time.monotonic() - started) * 1000),
            error=redact_text(str(exc)),
        )


async def _collect_events(runtime: AgentRuntime, run: RunRef) -> list[AgentEvent]:
    return [event async for event in runtime.events(run)]


async def run_live_sample(output_path: Path) -> LiveSampleReport:
    """Run a balanced, stratified provider calibration outside the replay dataset."""

    started_at = datetime.now(UTC)
    assignments = select_live_sample(AcrArchRunner().tasks())
    codex = CodexRuntime()
    claude_model = os.getenv("ACCRETION_CLAUDE_LIVE_MODEL") or None
    claude = ClaudeRuntime(model=claude_model)
    codex_health, claude_health = await asyncio.gather(codex.health(), claude.health())
    for health in (codex_health, claude_health):
        if health.status is not RuntimeStatus.READY:
            raise RuntimeError(
                f"{health.provider.value} runtime is {health.status.value}: {health.last_error}"
            )
    versions = {
        Provider.CODEX.value: codex_health.runtime_version,
        Provider.CLAUDE.value: claude_health.runtime_version,
    }
    results: list[LiveSampleResult] = []
    try:
        with TemporaryDirectory(prefix="accretion-live-sample-") as temporary:
            root = Path(temporary)
            # Each category contributes one Codex and one Claude call. Run the
            # pair concurrently but keep categories sequential to bound usage.
            for offset in range(0, len(assignments), 2):
                pair = assignments[offset : offset + 2]
                pair_results = await asyncio.gather(
                    *(
                        _run_assignment(
                            assignment,
                            codex if assignment.provider is Provider.CODEX else claude,
                            versions[assignment.provider.value],
                            root / assignment.benchmark_task_id / assignment.provider.value.lower(),
                        )
                        for assignment in pair
                    )
                )
                results.extend(pair_results)
    finally:
        await codex.close()

    report = LiveSampleReport(
        started_at=started_at,
        completed_at=datetime.now(UTC),
        provider_versions=versions,
        provider_models={
            Provider.CODEX.value: "default",
            Provider.CLAUDE.value: claude_model or "default",
        },
        results=results,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.model_dump_json(indent=2) + "\n")
    return report
