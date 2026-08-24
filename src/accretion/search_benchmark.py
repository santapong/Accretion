from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from accretion.contracts import Provider, StrictModel

EVAL_ROOT = Path(__file__).resolve().parents[2] / "evals" / "search"
FROZEN_AT = datetime(2026, 8, 24, tzinfo=UTC)


class SearchReplayCandidate(StrictModel):
    ordinal: int = Field(ge=1, le=4)
    provider: Provider
    runtime_model: str
    runtime_version: str
    eligible: bool
    quality: float = Field(ge=0, le=1)
    turns: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    latency_ms: int = Field(ge=0)


class SearchReplayTrace(StrictModel):
    task_id: str
    candidates: list[SearchReplayCandidate]


class SearchBenchmarkCurvePoint(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    candidate_count: int
    task_count: int
    accepted_tasks: int
    acceptance_rate: float = Field(ge=0, le=1)
    mean_quality: float = Field(ge=0, le=1)
    marginal_quality_gain: float
    mean_turns: float = Field(ge=0)
    mean_tool_calls: float = Field(ge=0)
    mean_latency_ms: float = Field(ge=0)


class ProviderSearchBenchmark(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    provider: Provider
    task_count: int
    accepted_tasks: int
    acceptance_rate: float = Field(ge=0, le=1)
    mean_best_quality: float = Field(ge=0, le=1)


class SearchBenchmarkTaskResult(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    task_id: str
    family: str
    title: str
    quality_by_candidate_count: dict[str, float]
    accepted_by_candidate_count: dict[str, bool]
    selected_provider_at_four: Provider | None = None
    gain_from_two_to_four: float


class SearchBenchmarkSummary(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    benchmark_run_id: str
    suite_version: str
    configuration_version: str
    selector_version: str
    execution_source: Literal["REPLAY"] = "REPLAY"
    task_count: int
    candidate_counts: list[int]
    corpus_sha256: str
    trace_sha256: str
    config_sha256: str
    frozen_at: datetime
    curve: list[SearchBenchmarkCurvePoint]
    provider_comparison: list[ProviderSearchBenchmark]
    tasks: list[SearchBenchmarkTaskResult]
    null_gain_task_ids: list[str]


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_id(value: str) -> str:
    return f"sbr_{hashlib.sha256(value.encode()).hexdigest()[:26]}"


class SearchBenchmarkRunner:
    """Reproduce the P6 quality-vs-compute curve from frozen held-out traces."""

    def __init__(self, root: Path = EVAL_ROOT) -> None:
        self.root = root
        self.config_path = root / "config.v1.json"
        self.tasks_path = root / "tasks.v1.json"
        self.traces_path = root / "replay-traces.v1.json"
        self.config = _read_json(self.config_path)

    def run(self) -> SearchBenchmarkSummary:
        task_items = _read_json(self.tasks_path).get("tasks", [])
        trace_items = _read_json(self.traces_path).get("traces", [])
        if not all(isinstance(item, dict) for item in task_items):
            raise ValueError("search benchmark tasks must be objects")
        tasks = {str(item["task_id"]): item for item in task_items}
        traces = [SearchReplayTrace.model_validate(item) for item in trace_items]
        required_count = int(self.config["held_out_task_count"])
        candidate_counts = [int(item) for item in self.config["candidate_counts"]]
        if len(tasks) != required_count or len(traces) != required_count:
            raise ValueError(
                f"P6 search benchmark requires exactly {required_count} tasks and traces"
            )
        if candidate_counts != [1, 2, 4]:
            raise ValueError("P6 search benchmark must preserve the N=1,2,4 curve")
        if Counter(item.task_id for item in traces) != Counter(tasks.keys()):
            raise ValueError("search trace/task identifiers drifted")
        for trace in traces:
            if [item.ordinal for item in trace.candidates] != [1, 2, 3, 4]:
                raise ValueError(f"trace {trace.task_id} must contain ordered N=4 evidence")

        precision = int(self.config["quality_precision"])
        point_values: dict[int, list[tuple[float, bool, int, int, int]]] = {
            count: [] for count in candidate_counts
        }
        task_results: list[SearchBenchmarkTaskResult] = []
        provider_values: dict[Provider, list[tuple[float, bool]]] = {
            Provider.CLAUDE: [],
            Provider.CODEX: [],
        }
        for trace in traces:
            quality_by_n: dict[str, float] = {}
            accepted_by_n: dict[str, bool] = {}
            selected_at_four: SearchReplayCandidate | None = None
            for count in candidate_counts:
                observed = trace.candidates[:count]
                eligible = [item for item in observed if item.eligible]
                selected = max(
                    eligible,
                    key=lambda item: (item.quality, -item.ordinal),
                    default=None,
                )
                quality = selected.quality if selected is not None else 0.0
                accepted = selected is not None
                quality_by_n[str(count)] = round(quality, precision)
                accepted_by_n[str(count)] = accepted
                point_values[count].append(
                    (
                        quality,
                        accepted,
                        sum(item.turns for item in observed),
                        sum(item.tool_calls for item in observed),
                        max(item.latency_ms for item in observed),
                    )
                )
                if count == 4:
                    selected_at_four = selected
            task = tasks[trace.task_id]
            task_results.append(
                SearchBenchmarkTaskResult(
                    task_id=trace.task_id,
                    family=str(task["family"]),
                    title=str(task["title"]),
                    quality_by_candidate_count=quality_by_n,
                    accepted_by_candidate_count=accepted_by_n,
                    selected_provider_at_four=(
                        selected_at_four.provider if selected_at_four else None
                    ),
                    gain_from_two_to_four=round(quality_by_n["4"] - quality_by_n["2"], precision),
                )
            )
            for provider in provider_values:
                candidates = [
                    item for item in trace.candidates if item.provider is provider and item.eligible
                ]
                best = max((item.quality for item in candidates), default=0.0)
                provider_values[provider].append((best, bool(candidates)))

        curve: list[SearchBenchmarkCurvePoint] = []
        previous_quality = 0.0
        for count in candidate_counts:
            values = point_values[count]
            mean_quality = sum(item[0] for item in values) / len(values)
            accepted_tasks = sum(item[1] for item in values)
            curve.append(
                SearchBenchmarkCurvePoint(
                    candidate_count=count,
                    task_count=len(values),
                    accepted_tasks=accepted_tasks,
                    acceptance_rate=round(accepted_tasks / len(values), precision),
                    mean_quality=round(mean_quality, precision),
                    marginal_quality_gain=round(mean_quality - previous_quality, precision),
                    mean_turns=round(sum(item[2] for item in values) / len(values), 3),
                    mean_tool_calls=round(sum(item[3] for item in values) / len(values), 3),
                    mean_latency_ms=round(sum(item[4] for item in values) / len(values), 3),
                )
            )
            previous_quality = mean_quality

        provider_comparison = [
            ProviderSearchBenchmark(
                provider=provider,
                task_count=len(values),
                accepted_tasks=sum(item[1] for item in values),
                acceptance_rate=round(sum(item[1] for item in values) / len(values), precision),
                mean_best_quality=round(sum(item[0] for item in values) / len(values), precision),
            )
            for provider, values in provider_values.items()
        ]
        corpus_sha = _sha256(self.tasks_path)
        trace_sha = _sha256(self.traces_path)
        config_sha = _sha256(self.config_path)
        minimum_gain = float(self.config["minimum_expected_gain"])
        return SearchBenchmarkSummary(
            benchmark_run_id=_stable_id(f"{corpus_sha}:{trace_sha}:{config_sha}"),
            suite_version=str(self.config["suite_version"]),
            configuration_version=str(self.config["configuration_version"]),
            selector_version=str(self.config["selector_version"]),
            task_count=len(tasks),
            candidate_counts=candidate_counts,
            corpus_sha256=corpus_sha,
            trace_sha256=trace_sha,
            config_sha256=config_sha,
            frozen_at=FROZEN_AT,
            curve=curve,
            provider_comparison=provider_comparison,
            tasks=task_results,
            null_gain_task_ids=[
                item.task_id for item in task_results if item.gain_from_two_to_four < minimum_gain
            ],
        )
