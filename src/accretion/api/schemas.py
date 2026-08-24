from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from accretion.contracts import (
    ApprovalDecisionValue,
    BenchmarkExecutionSource,
    ExecutionMode,
    Provider,
    RiskLevel,
    TaskBudgets,
    TaskType,
    TemplateStatus,
)
from accretion.orchestration.models import (
    PlannerRuntime,
    ReplanReason,
    SearchBudgetEnvelope,
    SearchMode,
)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    repository_path: Path


class TaskCreate(BaseModel):
    project_id: str
    objective: str = Field(min_length=1, max_length=20_000)
    task_type: TaskType = TaskType.OTHER
    inputs: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    requested_skills: list[str] = Field(default_factory=list)
    allowed_capabilities: list[str] = Field(default_factory=list)
    denied_capabilities: list[str] = Field(default_factory=list)
    budgets: TaskBudgets = Field(default_factory=TaskBudgets)
    required_outputs: list[dict[str, Any]] = Field(default_factory=list)

    def envelope_patch(self) -> dict[str, Any]:
        return self.model_dump(exclude={"project_id", "objective"})


class RunCreate(BaseModel):
    provider: Provider = Provider.FAKE


class ProjectFeatureUpdate(BaseModel):
    dynamic_workflows: bool | None = None
    candidate_search: bool | None = None
    expected_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def require_feature(self) -> ProjectFeatureUpdate:
        if self.dynamic_workflows is None and self.candidate_search is None:
            raise ValueError("at least one feature setting is required")
        return self


class WorkflowProposeCreate(BaseModel):
    execution_provider: Provider = Provider.FAKE
    planner_runtime: PlannerRuntime = PlannerRuntime.DETERMINISTIC


class ReplanCreate(BaseModel):
    reason: ReplanReason
    evidence_refs: list[str] = Field(default_factory=list)


class SearchCreate(BaseModel):
    parent_node_id: str = Field(min_length=1, max_length=96)
    mode: SearchMode
    branch_count: int = Field(default=2, ge=1, le=4)
    max_parallel: int = Field(default=2, ge=1, le=4)
    per_branch_budget: SearchBudgetEnvelope
    total_budget: SearchBudgetEnvelope
    candidate_directives: list[str] = Field(default_factory=list, max_length=4)


class StrategyOverrideCreate(BaseModel):
    requested_mode: ExecutionMode
    requested_template_id: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=2_000)


class ApprovalDecisionCreate(BaseModel):
    decision: ApprovalDecisionValue


class BenchmarkRunCreate(BaseModel):
    execution_source: BenchmarkExecutionSource = BenchmarkExecutionSource.REPLAY


class WorkflowTemplateSummary(BaseModel):
    """Read-only template listing; nodes and edges never enter the API."""

    template_id: str
    version: str
    mode: ExecutionMode
    status: TemplateStatus
    checksum: str


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    correlation_id: str
    retryable: bool = False
