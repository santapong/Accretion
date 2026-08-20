from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from accretion.contracts import Provider, RiskLevel, TaskBudgets, TaskType


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    repository_path: Path


class TaskCreate(BaseModel):
    project_id: str
    objective: str = Field(min_length=1, max_length=20_000)
    task_type: TaskType = TaskType.OTHER
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    allowed_capabilities: list[str] = Field(default_factory=list)
    denied_capabilities: list[str] = Field(default_factory=list)
    budgets: TaskBudgets = Field(default_factory=TaskBudgets)

    def envelope_patch(self) -> dict[str, Any]:
        return self.model_dump(exclude={"project_id", "objective"})


class RunCreate(BaseModel):
    provider: Provider = Provider.FAKE


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    correlation_id: str
    retryable: bool = False
