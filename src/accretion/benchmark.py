from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from accretion.contracts import (
    AcrArchSummary,
    ArchitectureMetric,
    BenchmarkCategory,
    BenchmarkExecutionSource,
    BenchmarkRun,
    BenchmarkRunStatus,
    BenchmarkTask,
    BenchmarkTaskDetail,
    ExecutionMode,
    Provider,
    StrictModel,
    TaskType,
)
from accretion.persistence.store import StateStore

EVAL_ROOT = Path(__file__).resolve().parents[2] / "evals" / "acr_arch"
FROZEN_AT = datetime(2026, 8, 22, tzinfo=UTC)


class ReplayScenario(StrictModel):
    scenario_id: str
    benchmark_task_id: str
    task_version: str
    mode: ExecutionMode
    provider: Provider
    success: bool
    quality: float = Field(ge=0, le=1)
    duration_ms: int = Field(ge=0)
    turns: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    approvals: int = Field(ge=0)
    risk_events: int = Field(ge=0)
    verifier_status: str
    trace_ref: str


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:26]}"


class AcrArchRunner:
    """Reproduce ACR-ARCH metrics from frozen provider trace fixtures."""

    def __init__(self, root: Path = EVAL_ROOT) -> None:
        self.root = root
        self.config_path = root / "config.v1.json"
        self.tasks_path = root / "tasks.v1.json"
        self.traces_path = root / "replay-traces.v1.json"
        self.environments_path = root / "environments.v1.json"
        self.config = _read_json(self.config_path)

    def tasks(self) -> list[BenchmarkTask]:
        payload = _read_json(self.tasks_path)
        tasks = [BenchmarkTask.model_validate(item) for item in payload.get("tasks", [])]
        environments = _read_json(self.environments_path).get("environments", [])
        environment_versions = {
            (item["environment_id"], item["version"])
            for item in environments
            if isinstance(item, dict)
        }
        for task in tasks:
            if (task.environment_ref, task.environment_version) not in environment_versions:
                raise ValueError(f"task {task.benchmark_task_id} has an unknown environment")
            if task.selector_mode not in task.applicable_modes:
                raise ValueError(f"task {task.benchmark_task_id} selector mode is not applicable")
            if len(set(task.applicable_modes)) != len(task.applicable_modes):
                raise ValueError(f"task {task.benchmark_task_id} repeats an applicable mode")
        if len(tasks) != 30:
            raise ValueError(f"ACR-ARCH v1 requires exactly 30 tasks, found {len(tasks)}")
        expected = {
            BenchmarkCategory.DIRECT_SIMPLE: 5,
            BenchmarkCategory.FEEDBACK_REFINEMENT: 8,
            BenchmarkCategory.PREDICTABLE_GRAPH: 7,
            BenchmarkCategory.HYBRID_ENGINEERING: 7,
            BenchmarkCategory.SAFETY_RECOVERY: 3,
        }
        if Counter(task.category for task in tasks) != expected:
            raise ValueError("ACR-ARCH category composition drifted")
        return tasks

    def replay(self) -> tuple[BenchmarkRun, list[ArchitectureMetric]]:
        tasks = self.tasks()
        task_index = {(task.benchmark_task_id, task.version): task for task in tasks}
        payload = _read_json(self.traces_path)
        scenarios = [
            ReplayScenario.model_validate(item) for item in payload.get("scenarios", [])
        ]
        if len(scenarios) != 68:
            raise ValueError(f"ACR-ARCH replay requires 68 scenarios, found {len(scenarios)}")
        corpus_sha = _sha256(self.tasks_path)
        trace_sha = _sha256(self.traces_path)
        run_id = _stable_id("bnr", f"{corpus_sha}:{trace_sha}")
        by_task: dict[str, list[ArchitectureMetric]] = defaultdict(list)
        for scenario in scenarios:
            task = task_index.get((scenario.benchmark_task_id, scenario.task_version))
            if task is None or scenario.mode not in task.applicable_modes:
                raise ValueError(f"scenario {scenario.scenario_id} is not declared by its task")
            cost = min(
                1.0,
                (
                    scenario.turns / task.budgets.max_turns
                    + scenario.tool_calls / task.budgets.max_tool_calls
                )
                / 2,
            )
            latency = min(
                1.0,
                scenario.duration_ms / (task.budgets.wall_time_seconds * 1000),
            )
            risk = min(1.0, scenario.risk_events / 2)
            human = min(1.0, scenario.approvals / 2)
            utility = (
                scenario.quality
                - float(self.config["usage_cost_weight"]) * cost
                - float(self.config["latency_weight"]) * latency
                - float(self.config["risk_weight"]) * risk
                - float(self.config["human_burden_weight"]) * human
            )
            metric = ArchitectureMetric(
                metric_id=_stable_id("acm", f"{run_id}:{scenario.scenario_id}"),
                benchmark_run_id=run_id,
                benchmark_task_id=task.benchmark_task_id,
                task_version=task.version,
                category=task.category,
                task_type=task.task_type,
                mode=scenario.mode,
                provider=scenario.provider,
                execution_source=BenchmarkExecutionSource.REPLAY,
                verifier_id=task.verifier_id,
                selector_version=task.selector_version,
                success=scenario.success,
                quality=scenario.quality,
                cost=round(cost, 6),
                latency=round(latency, 6),
                risk=round(risk, 6),
                human_burden=round(human, 6),
                utility=round(utility, 6),
                architecture_regret=0,
                duration_ms=scenario.duration_ms,
                turns=scenario.turns,
                tool_calls=scenario.tool_calls,
                approvals=scenario.approvals,
                trace_ref=scenario.trace_ref,
                environment_ref=task.environment_ref,
                environment_version=task.environment_version,
            )
            by_task[task.benchmark_task_id].append(metric)

        metrics: list[ArchitectureMetric] = []
        for task in tasks:
            candidates = by_task[task.benchmark_task_id]
            if len(candidates) < 2:
                raise ValueError(f"task {task.benchmark_task_id} has fewer than two modes")
            selected = next(
                (item for item in candidates if item.mode is task.selector_mode), None
            )
            if selected is None:
                raise ValueError(f"task {task.benchmark_task_id} lacks its selector trace")
            best = max(item.utility for item in candidates)
            regret = round(max(0.0, best - selected.utility), 6)
            metrics.extend(
                item.model_copy(update={"architecture_regret": regret})
                for item in candidates
            )

        run = BenchmarkRun(
            benchmark_run_id=run_id,
            suite_version=str(self.config["suite_version"]),
            configuration_version=str(self.config["configuration_version"]),
            execution_source=BenchmarkExecutionSource.REPLAY,
            status=BenchmarkRunStatus.COMPLETED,
            corpus_sha256=corpus_sha,
            trace_sha256=trace_sha,
            scenario_count=len(metrics),
            started_at=FROZEN_AT,
            completed_at=FROZEN_AT,
        )
        return run, metrics

    async def persist(self, store: StateStore) -> BenchmarkRun:
        for task in self.tasks():
            await store.upsert_benchmark_task(task)
        run, metrics = self.replay()
        return await store.save_benchmark_run(run, metrics)


async def seed_acr_arch(store: StateStore) -> BenchmarkRun:
    return await AcrArchRunner().persist(store)


async def acr_arch_summary(
    store: StateStore,
    *,
    mode: ExecutionMode | None = None,
    provider: Provider | None = None,
    task_type: TaskType | None = None,
    verifier: str | None = None,
    selector_version: str | None = None,
) -> AcrArchSummary:
    runs = await store.list_benchmark_runs(1)
    latest = runs[0] if runs else None
    all_metrics = (
        await store.list_architecture_metrics(latest.benchmark_run_id) if latest else []
    )
    metrics = [
        item
        for item in all_metrics
        if (mode is None or item.mode is mode)
        and (provider is None or item.provider is provider)
        and (task_type is None or item.task_type is task_type)
        and (verifier is None or item.verifier_id == verifier)
        and (selector_version is None or item.selector_version == selector_version)
    ]
    return AcrArchSummary(
        suite_version=latest.suite_version if latest else "1.0.0",
        configuration_version=latest.configuration_version if latest else "1.0.0",
        task_count=len({item.benchmark_task_id for item in metrics}),
        scenario_count=len(metrics),
        latest_run=latest,
        metrics=metrics,
        filters={
            "mode": sorted({item.mode.value for item in all_metrics}),
            "provider": sorted({item.provider.value for item in all_metrics}),
            "task_type": sorted({item.task_type.value for item in all_metrics}),
            "verifier": sorted({item.verifier_id for item in all_metrics}),
            "selector_version": sorted(
                {item.selector_version for item in all_metrics}
            ),
        },
    )


async def acr_arch_task_detail(
    store: StateStore, task_id: str
) -> BenchmarkTaskDetail | None:
    task = await store.get_benchmark_task(task_id)
    if task is None:
        return None
    runs = await store.list_benchmark_runs(1)
    metrics = await store.list_architecture_metrics(
        runs[0].benchmark_run_id if runs else None
    )
    return BenchmarkTaskDetail(
        task=task,
        metrics=[item for item in metrics if item.benchmark_task_id == task_id],
    )
