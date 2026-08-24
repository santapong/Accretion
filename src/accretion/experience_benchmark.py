from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from accretion.contracts import StrictModel, TaskType

EVAL_ROOT = Path(__file__).resolve().parents[2] / "evals" / "experience"
FROZEN_AT = datetime(2026, 8, 24, tzinfo=UTC)


class ExperienceTreatment(StrEnum):
    FRESH = "FRESH"
    SUCCESS_ONLY = "SUCCESS_ONLY"
    SUCCESS_FAILURE = "SUCCESS_FAILURE"
    REPLAY = "REPLAY"


class ExperienceReplayTrace(StrictModel):
    task_id: str
    treatment: ExperienceTreatment
    success: bool
    quality: float = Field(ge=0, le=1)
    turns: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    false_accept: bool = False
    source_ids: list[str]
    experience_used: bool
    experience_rejected: bool
    experience_null: bool


class ExperienceTreatmentSummary(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    treatment: ExperienceTreatment
    task_count: int
    successful_tasks: int
    success_rate: float = Field(ge=0, le=1)
    mean_quality: float = Field(ge=0, le=1)
    mean_turns: float = Field(ge=0)
    mean_tool_calls: float = Field(ge=0)
    mean_latency_ms: float = Field(ge=0)
    mean_compute: float = Field(ge=0)
    quality_uplift: float
    tool_call_reduction: float
    false_accepts: int = Field(ge=0)
    negative_transfers: int = Field(ge=0)
    experience_use_rate: float = Field(ge=0, le=1)
    experience_rejection_rate: float = Field(ge=0, le=1)
    experience_null_rate: float = Field(ge=0, le=1)


class ExperienceBenchmarkTaskResult(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    task_id: str
    task_type: TaskType
    family: str
    title: str
    quality_by_treatment: dict[str, float]
    success_by_treatment: dict[str, bool]
    negative_transfer_treatments: list[ExperienceTreatment]


class ExperienceBenchmarkGate(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    passed: bool
    false_accepts_not_increased: bool
    stale_rejection_passed: bool
    negative_transfer_passed: bool
    benefit_passed: bool
    success_rate_not_regressed: bool
    stale_rejection_rate: float = Field(ge=0, le=1)
    negative_transfer_rate: float = Field(ge=0, le=1)
    replay_quality_uplift: float
    replay_tool_call_reduction: float
    thresholds: dict[str, float]


class ExperienceBenchmarkSummary(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    benchmark_run_id: str
    suite_version: str
    configuration_version: str
    selector_version: str
    execution_source: Literal["REPLAY"] = "REPLAY"
    task_count: int
    source_count: int
    trace_count: int
    source_counts: dict[str, int]
    corpus_sha256: str
    source_sha256: str
    trace_sha256: str
    config_sha256: str
    frozen_at: datetime
    treatments: list[ExperienceTreatmentSummary]
    tasks: list[ExperienceBenchmarkTaskResult]
    gate: ExperienceBenchmarkGate


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_id(value: str) -> str:
    return f"ebr_{hashlib.sha256(value.encode()).hexdigest()[:26]}"


class ExperienceBenchmarkRunner:
    """Reproduce the preregistered P7 gate from frozen held-out evidence."""

    def __init__(self, root: Path = EVAL_ROOT) -> None:
        self.root = root
        self.config_path = root / "config.v1.json"
        self.tasks_path = root / "tasks.v1.json"
        self.sources_path = root / "sources.v1.json"
        self.traces_path = root / "replay-traces.v1.json"
        self.config = _read_json(self.config_path)

    def run(self) -> ExperienceBenchmarkSummary:
        task_items = _read_json(self.tasks_path).get("tasks", [])
        source_items = _read_json(self.sources_path).get("sources", [])
        trace_items = _read_json(self.traces_path).get("traces", [])
        if not all(isinstance(item, dict) for item in task_items):
            raise ValueError("experience benchmark tasks must be objects")
        if not all(isinstance(item, dict) for item in source_items):
            raise ValueError("experience benchmark sources must be objects")
        tasks = {str(item["task_id"]): item for item in task_items}
        sources = {str(item["source_id"]): item for item in source_items}
        traces = [ExperienceReplayTrace.model_validate(item) for item in trace_items]
        treatments = [ExperienceTreatment(item) for item in self.config["treatments"]]
        required_tasks = int(self.config["held_out_task_count"])
        if len(tasks) != required_tasks:
            raise ValueError(f"P7 experience benchmark requires exactly {required_tasks} tasks")
        if Counter(str(item["task_type"]) for item in task_items) != Counter(
            {item.value: 5 for item in TaskType if item in {
                TaskType.IMPLEMENT,
                TaskType.REVIEW,
                TaskType.ANALYSIS,
                TaskType.RESEARCH,
            }}
        ):
            raise ValueError("P7 experience benchmark requires five tasks per frozen task type")
        expected_sources = {
            str(key): int(value) for key, value in self.config["source_counts"].items()
        }
        source_counts = Counter(str(item["class"]) for item in source_items)
        if dict(source_counts) != expected_sources:
            raise ValueError("P7 experience source corpus counts drifted")
        expected_trace_keys = Counter(
            (task_id, treatment) for task_id in tasks for treatment in treatments
        )
        if Counter((item.task_id, item.treatment) for item in traces) != expected_trace_keys:
            raise ValueError("P7 benchmark requires one trace per task and treatment")
        if any(source_id not in sources for trace in traces for source_id in trace.source_ids):
            raise ValueError("experience trace references an unknown frozen source")

        precision = int(self.config["quality_precision"])
        by_task = {
            task_id: {item.treatment: item for item in traces if item.task_id == task_id}
            for task_id in tasks
        }
        quality_delta = float(self.config["negative_transfer_quality_delta"])
        negative_keys = {
            (task_id, treatment)
            for task_id, observed in by_task.items()
            for treatment, trace in observed.items()
            if treatment is not ExperienceTreatment.FRESH
            and (
                (observed[ExperienceTreatment.FRESH].success and not trace.success)
                or trace.quality
                <= observed[ExperienceTreatment.FRESH].quality - quality_delta
            )
        }
        fresh_traces = [
            item for item in traces if item.treatment is ExperienceTreatment.FRESH
        ]
        fresh_quality = sum(item.quality for item in fresh_traces) / len(fresh_traces)
        fresh_tools = sum(item.tool_calls for item in fresh_traces) / len(fresh_traces)
        summaries: list[ExperienceTreatmentSummary] = []
        for treatment in treatments:
            values = [item for item in traces if item.treatment is treatment]
            mean_quality = sum(item.quality for item in values) / len(values)
            mean_tools = sum(item.tool_calls for item in values) / len(values)
            summaries.append(
                ExperienceTreatmentSummary(
                    treatment=treatment,
                    task_count=len(values),
                    successful_tasks=sum(item.success for item in values),
                    success_rate=round(
                        sum(item.success for item in values) / len(values), precision
                    ),
                    mean_quality=round(mean_quality, precision),
                    mean_turns=round(sum(item.turns for item in values) / len(values), 3),
                    mean_tool_calls=round(mean_tools, 3),
                    mean_latency_ms=round(
                        sum(item.latency_ms for item in values) / len(values), 3
                    ),
                    mean_compute=round(
                        sum(item.turns + item.tool_calls for item in values) / len(values),
                        3,
                    ),
                    quality_uplift=round(mean_quality - fresh_quality, precision),
                    tool_call_reduction=round(
                        (fresh_tools - mean_tools) / fresh_tools, precision
                    ),
                    false_accepts=sum(item.false_accept for item in values),
                    negative_transfers=sum(
                        (item.task_id, treatment) in negative_keys for item in values
                    ),
                    experience_use_rate=round(
                        sum(item.experience_used for item in values) / len(values), precision
                    ),
                    experience_rejection_rate=round(
                        sum(item.experience_rejected for item in values) / len(values),
                        precision,
                    ),
                    experience_null_rate=round(
                        sum(item.experience_null for item in values) / len(values), precision
                    ),
                )
            )
        by_treatment = {item.treatment: item for item in summaries}
        fresh = by_treatment[ExperienceTreatment.FRESH]
        replay = by_treatment[ExperienceTreatment.REPLAY]
        stale = [
            item for item in source_items if item["class"] == "STALE_INCOMPATIBLE"
        ]
        stale_rejection_rate = sum(
            item["retrieval_outcome"] == "REJECTED" for item in stale
        ) / len(stale)
        negative_transfer_rate = len(negative_keys) / (
            required_tasks * (len(treatments) - 1)
        )
        false_accepts_safe = all(
            item.false_accepts <= fresh.false_accepts for item in summaries[1:]
        )
        success_safe = replay.success_rate >= fresh.success_rate
        stale_passed = stale_rejection_rate >= float(
            self.config["minimum_stale_rejection_rate"]
        )
        negative_passed = negative_transfer_rate <= float(
            self.config["maximum_negative_transfer_rate"]
        )
        benefit_passed = replay.quality_uplift >= float(
            self.config["minimum_quality_uplift"]
        ) or (
            replay.tool_call_reduction >= float(self.config["minimum_tool_call_reduction"])
            and success_safe
        )
        gate = ExperienceBenchmarkGate(
            passed=(
                false_accepts_safe
                and stale_passed
                and negative_passed
                and benefit_passed
                and success_safe
            ),
            false_accepts_not_increased=false_accepts_safe,
            stale_rejection_passed=stale_passed,
            negative_transfer_passed=negative_passed,
            benefit_passed=benefit_passed,
            success_rate_not_regressed=success_safe,
            stale_rejection_rate=round(stale_rejection_rate, precision),
            negative_transfer_rate=round(negative_transfer_rate, precision),
            replay_quality_uplift=replay.quality_uplift,
            replay_tool_call_reduction=replay.tool_call_reduction,
            thresholds={
                "minimum_stale_rejection_rate": float(
                    self.config["minimum_stale_rejection_rate"]
                ),
                "maximum_negative_transfer_rate": float(
                    self.config["maximum_negative_transfer_rate"]
                ),
                "minimum_quality_uplift": float(self.config["minimum_quality_uplift"]),
                "minimum_tool_call_reduction": float(
                    self.config["minimum_tool_call_reduction"]
                ),
            },
        )
        task_results = [
            ExperienceBenchmarkTaskResult(
                task_id=task_id,
                task_type=TaskType(task["task_type"]),
                family=str(task["family"]),
                title=str(task["title"]),
                quality_by_treatment={
                    treatment.value: observed[treatment].quality for treatment in treatments
                },
                success_by_treatment={
                    treatment.value: observed[treatment].success for treatment in treatments
                },
                negative_transfer_treatments=[
                    treatment
                    for treatment in treatments
                    if (task_id, treatment) in negative_keys
                ],
            )
            for task_id, task in tasks.items()
            for observed in [by_task[task_id]]
        ]
        corpus_sha = _sha256(self.tasks_path)
        source_sha = _sha256(self.sources_path)
        trace_sha = _sha256(self.traces_path)
        config_sha = _sha256(self.config_path)
        return ExperienceBenchmarkSummary(
            benchmark_run_id=_stable_id(
                f"{corpus_sha}:{source_sha}:{trace_sha}:{config_sha}"
            ),
            suite_version=str(self.config["suite_version"]),
            configuration_version=str(self.config["configuration_version"]),
            selector_version=str(self.config["selector_version"]),
            task_count=len(tasks),
            source_count=len(sources),
            trace_count=len(traces),
            source_counts=dict(source_counts),
            corpus_sha256=corpus_sha,
            source_sha256=source_sha,
            trace_sha256=trace_sha,
            config_sha256=config_sha,
            frozen_at=FROZEN_AT,
            treatments=summaries,
            tasks=task_results,
            gate=gate,
        )
