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

EVAL_ROOT = Path(__file__).resolve().parents[2] / "evals" / "dynamic_workflow"
FROZEN_AT = datetime(2026, 8, 24, tzinfo=UTC)


class DynamicTreatment(StrEnum):
    STATIC = "STATIC"
    DYNAMIC = "DYNAMIC"


class DynamicCohort(StrEnum):
    PREDICTABLE = "PREDICTABLE"
    HETEROGENEOUS = "HETEROGENEOUS"
    UNCERTAIN = "UNCERTAIN"


class DynamicWorkflowReplayTrace(StrictModel):
    task_id: str
    treatment: DynamicTreatment
    success: bool
    quality: float = Field(ge=0, le=1)
    turns: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    approvals: int = Field(ge=0)
    risk_events: int = Field(ge=0)
    false_accept: bool = False
    invalid_proposal: bool = False
    fallback_used: bool = False
    replan_count: int = Field(ge=0)
    graph_nodes: int = Field(ge=1)
    graph_edges: int = Field(ge=0)
    graph_depth: int = Field(ge=1)
    structure_hash: str


class DynamicTreatmentSummary(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    treatment: DynamicTreatment
    task_count: int
    successful_tasks: int
    success_rate: float = Field(ge=0, le=1)
    mean_quality: float = Field(ge=0, le=1)
    mean_utility: float
    mean_turns: float = Field(ge=0)
    mean_tool_calls: float = Field(ge=0)
    mean_latency_ms: float = Field(ge=0)
    invalid_proposal_rate: float = Field(ge=0, le=1)
    replan_rate: float = Field(ge=0, le=1)
    human_intervention_rate: float = Field(ge=0, le=1)
    mean_graph_nodes: float = Field(ge=0)
    mean_graph_depth: float = Field(ge=0)
    structural_variation_rate: float = Field(ge=0, le=1)


class DynamicCohortComparison(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    cohort: DynamicCohort
    task_count: int
    static_mean_utility: float
    dynamic_mean_utility: float
    utility_uplift: float
    static_success_rate: float = Field(ge=0, le=1)
    dynamic_success_rate: float = Field(ge=0, le=1)


class DynamicBenchmarkTaskResult(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    task_id: str
    cohort: DynamicCohort
    task_type: TaskType
    family: str
    title: str
    utility_by_treatment: dict[str, float]
    success_by_treatment: dict[str, bool]
    architecture_regret_by_treatment: dict[str, float]
    dynamic_invalid_proposal: bool
    dynamic_fallback_used: bool
    dynamic_replan_count: int


class DynamicBenchmarkGate(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    passed: bool
    research_classification: Literal["POSITIVE", "EXPERIMENTAL_NULL_OR_NEGATIVE"]
    benefit_passed: bool
    predictable_non_inferiority_passed: bool
    success_rate_not_regressed: bool
    safety_invariants_passed: bool
    static_fallback_operational: bool
    heterogeneous_uncertain_uplift: float
    predictable_uplift: float
    thresholds: dict[str, float]


class DynamicWorkflowBenchmarkSummary(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    benchmark_run_id: str
    suite_version: str
    configuration_version: str
    selector_version: str
    execution_source: Literal["REPLAY"] = "REPLAY"
    task_count: int
    trace_count: int
    corpus_sha256: str
    trace_sha256: str
    config_sha256: str
    frozen_at: datetime
    treatments: list[DynamicTreatmentSummary]
    cohorts: list[DynamicCohortComparison]
    tasks: list[DynamicBenchmarkTaskResult]
    gate: DynamicBenchmarkGate


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_id(value: str) -> str:
    return f"dbr_{hashlib.sha256(value.encode()).hexdigest()[:26]}"


class DynamicWorkflowBenchmarkRunner:
    """Reproduce the preregistered P5 static-versus-dynamic release evidence."""

    def __init__(self, root: Path = EVAL_ROOT) -> None:
        self.root = root
        self.config_path = root / "config.v1.json"
        self.tasks_path = root / "tasks.v1.json"
        self.traces_path = root / "replay-traces.v1.json"
        self.config = _read_json(self.config_path)

    def run(self) -> DynamicWorkflowBenchmarkSummary:
        task_items = _read_json(self.tasks_path).get("tasks", [])
        trace_items = _read_json(self.traces_path).get("traces", [])
        if not all(isinstance(item, dict) for item in task_items):
            raise ValueError("dynamic benchmark tasks must be objects")
        tasks = {str(item["task_id"]): item for item in task_items}
        traces = [DynamicWorkflowReplayTrace.model_validate(item) for item in trace_items]
        treatments = [DynamicTreatment(item) for item in self.config["treatments"]]
        cohorts = [DynamicCohort(item) for item in self.config["cohorts"]]
        required_tasks = int(self.config["held_out_task_count"])
        if len(tasks) != required_tasks:
            raise ValueError(f"P5 dynamic benchmark requires exactly {required_tasks} tasks")
        expected_cohorts = Counter({item.value: 4 for item in cohorts})
        if Counter(str(item["cohort"]) for item in task_items) != expected_cohorts:
            raise ValueError("P5 dynamic benchmark requires four tasks per frozen cohort")
        if Counter(str(item["task_type"]) for item in task_items) != Counter(
            {
                item.value: 3
                for item in TaskType
                if item
                in {
                    TaskType.IMPLEMENT,
                    TaskType.REVIEW,
                    TaskType.ANALYSIS,
                    TaskType.RESEARCH,
                }
            }
        ):
            raise ValueError("P5 dynamic benchmark requires three tasks per frozen task type")
        expected_trace_keys = Counter(
            (task_id, treatment) for task_id in tasks for treatment in treatments
        )
        if Counter((item.task_id, item.treatment) for item in traces) != expected_trace_keys:
            raise ValueError("P5 benchmark requires one trace per task and treatment")

        precision = int(self.config["precision"])
        by_task = {
            task_id: {item.treatment: item for item in traces if item.task_id == task_id}
            for task_id in tasks
        }
        utilities: dict[tuple[str, DynamicTreatment], float] = {}
        for task_id, task in tasks.items():
            budgets = task["budgets"]
            for treatment, trace in by_task[task_id].items():
                cost = min(
                    1.0,
                    (
                        trace.turns / int(budgets["max_turns"])
                        + trace.tool_calls / int(budgets["max_tool_calls"])
                    )
                    / 2,
                )
                latency = min(
                    1.0,
                    trace.latency_ms / (int(budgets["wall_time_seconds"]) * 1000),
                )
                risk = min(1.0, trace.risk_events / 2)
                human = min(1.0, trace.approvals / 2)
                utilities[(task_id, treatment)] = round(
                    trace.quality
                    - float(self.config["usage_cost_weight"]) * cost
                    - float(self.config["latency_weight"]) * latency
                    - float(self.config["risk_weight"]) * risk
                    - float(self.config["human_burden_weight"]) * human,
                    precision,
                )

        treatment_summaries: list[DynamicTreatmentSummary] = []
        for treatment in treatments:
            values = [item for item in traces if item.treatment is treatment]
            treatment_summaries.append(
                DynamicTreatmentSummary(
                    treatment=treatment,
                    task_count=len(values),
                    successful_tasks=sum(item.success for item in values),
                    success_rate=round(
                        sum(item.success for item in values) / len(values), precision
                    ),
                    mean_quality=round(
                        sum(item.quality for item in values) / len(values), precision
                    ),
                    mean_utility=round(
                        sum(utilities[(item.task_id, treatment)] for item in values) / len(values),
                        precision,
                    ),
                    mean_turns=round(sum(item.turns for item in values) / len(values), 3),
                    mean_tool_calls=round(sum(item.tool_calls for item in values) / len(values), 3),
                    mean_latency_ms=round(sum(item.latency_ms for item in values) / len(values), 3),
                    invalid_proposal_rate=round(
                        sum(item.invalid_proposal for item in values) / len(values), precision
                    ),
                    replan_rate=round(
                        sum(item.replan_count > 0 for item in values) / len(values),
                        precision,
                    ),
                    human_intervention_rate=round(
                        sum(item.approvals > 0 for item in values) / len(values), precision
                    ),
                    mean_graph_nodes=round(
                        sum(item.graph_nodes for item in values) / len(values), 3
                    ),
                    mean_graph_depth=round(
                        sum(item.graph_depth for item in values) / len(values), 3
                    ),
                    structural_variation_rate=round(
                        len({item.structure_hash for item in values}) / len(values), precision
                    ),
                )
            )

        cohort_summaries: list[DynamicCohortComparison] = []
        for cohort in cohorts:
            task_ids = [
                task_id for task_id, task in tasks.items() if task["cohort"] == cohort.value
            ]
            static_utility = sum(
                utilities[(task_id, DynamicTreatment.STATIC)] for task_id in task_ids
            ) / len(task_ids)
            dynamic_utility = sum(
                utilities[(task_id, DynamicTreatment.DYNAMIC)] for task_id in task_ids
            ) / len(task_ids)
            cohort_summaries.append(
                DynamicCohortComparison(
                    cohort=cohort,
                    task_count=len(task_ids),
                    static_mean_utility=round(static_utility, precision),
                    dynamic_mean_utility=round(dynamic_utility, precision),
                    utility_uplift=round(dynamic_utility - static_utility, precision),
                    static_success_rate=round(
                        sum(by_task[item][DynamicTreatment.STATIC].success for item in task_ids)
                        / len(task_ids),
                        precision,
                    ),
                    dynamic_success_rate=round(
                        sum(by_task[item][DynamicTreatment.DYNAMIC].success for item in task_ids)
                        / len(task_ids),
                        precision,
                    ),
                )
            )
        cohort_by_name = {item.cohort: item for item in cohort_summaries}
        research_ids = [
            task_id
            for task_id, task in tasks.items()
            if task["cohort"]
            in {
                DynamicCohort.HETEROGENEOUS.value,
                DynamicCohort.UNCERTAIN.value,
            }
        ]
        research_uplift = round(
            sum(
                utilities[(item, DynamicTreatment.DYNAMIC)]
                - utilities[(item, DynamicTreatment.STATIC)]
                for item in research_ids
            )
            / len(research_ids),
            precision,
        )
        predictable_uplift = cohort_by_name[DynamicCohort.PREDICTABLE].utility_uplift
        benefit_passed = research_uplift >= float(
            self.config["minimum_heterogeneous_uncertain_utility_uplift"]
        )
        predictable_passed = predictable_uplift >= float(
            self.config["minimum_predictable_utility_uplift"]
        )
        static_successes = sum(
            item.success for item in traces if item.treatment is DynamicTreatment.STATIC
        )
        dynamic_successes = sum(
            item.success for item in traces if item.treatment is DynamicTreatment.DYNAMIC
        )
        success_safe = dynamic_successes >= static_successes
        safety_safe = not any(
            item.false_accept or item.risk_events
            for item in traces
            if item.treatment is DynamicTreatment.DYNAMIC
        )
        invalid_dynamic = [
            item
            for item in traces
            if item.treatment is DynamicTreatment.DYNAMIC and item.invalid_proposal
        ]
        fallback_safe = bool(invalid_dynamic) and all(
            item.fallback_used for item in invalid_dynamic
        )
        classification = "POSITIVE" if benefit_passed else "EXPERIMENTAL_NULL_OR_NEGATIVE"
        gate = DynamicBenchmarkGate(
            passed=(
                benefit_passed
                and predictable_passed
                and success_safe
                and safety_safe
                and fallback_safe
            ),
            research_classification=classification,
            benefit_passed=benefit_passed,
            predictable_non_inferiority_passed=predictable_passed,
            success_rate_not_regressed=success_safe,
            safety_invariants_passed=safety_safe,
            static_fallback_operational=fallback_safe,
            heterogeneous_uncertain_uplift=research_uplift,
            predictable_uplift=predictable_uplift,
            thresholds={
                "minimum_heterogeneous_uncertain_utility_uplift": float(
                    self.config["minimum_heterogeneous_uncertain_utility_uplift"]
                ),
                "minimum_predictable_utility_uplift": float(
                    self.config["minimum_predictable_utility_uplift"]
                ),
            },
        )
        task_results = []
        for task_id, task in tasks.items():
            observed = by_task[task_id]
            best = max(utilities[(task_id, item)] for item in treatments)
            task_results.append(
                DynamicBenchmarkTaskResult(
                    task_id=task_id,
                    cohort=DynamicCohort(task["cohort"]),
                    task_type=TaskType(task["task_type"]),
                    family=str(task["family"]),
                    title=str(task["title"]),
                    utility_by_treatment={
                        item.value: utilities[(task_id, item)] for item in treatments
                    },
                    success_by_treatment={
                        item.value: observed[item].success for item in treatments
                    },
                    architecture_regret_by_treatment={
                        item.value: round(best - utilities[(task_id, item)], precision)
                        for item in treatments
                    },
                    dynamic_invalid_proposal=observed[DynamicTreatment.DYNAMIC].invalid_proposal,
                    dynamic_fallback_used=observed[DynamicTreatment.DYNAMIC].fallback_used,
                    dynamic_replan_count=observed[DynamicTreatment.DYNAMIC].replan_count,
                )
            )

        corpus_sha = _sha256(self.tasks_path)
        trace_sha = _sha256(self.traces_path)
        config_sha = _sha256(self.config_path)
        return DynamicWorkflowBenchmarkSummary(
            benchmark_run_id=_stable_id(f"{corpus_sha}:{trace_sha}:{config_sha}"),
            suite_version=str(self.config["suite_version"]),
            configuration_version=str(self.config["configuration_version"]),
            selector_version=str(self.config["selector_version"]),
            task_count=len(tasks),
            trace_count=len(traces),
            corpus_sha256=corpus_sha,
            trace_sha256=trace_sha,
            config_sha256=config_sha,
            frozen_at=FROZEN_AT,
            treatments=treatment_summaries,
            cohorts=cohort_summaries,
            tasks=task_results,
            gate=gate,
        )
