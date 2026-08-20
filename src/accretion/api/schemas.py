from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from accretion.contracts import (
    ApprovalDecisionValue,
    ExecutionMode,
    Provider,
    RiskLevel,
    TaskBudgets,
    TaskType,
    TemplateStatus,
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


class StrategyOverrideCreate(BaseModel):
    requested_mode: ExecutionMode
    requested_template_id: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=2_000)


class ApprovalDecisionCreate(BaseModel):
    decision: ApprovalDecisionValue


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
