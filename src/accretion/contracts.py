from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class Provider(StrEnum):
    CLAUDE = "CLAUDE"
    CODEX = "CODEX"
    FAKE = "FAKE"
    DETERMINISTIC = "DETERMINISTIC"
    HUMAN = "HUMAN"


class RuntimeStatus(StrEnum):
    READY = "READY"
    BUSY = "BUSY"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"
    DEGRADED = "DEGRADED"


class AuthMode(StrEnum):
    SUBSCRIPTION = "SUBSCRIPTION"
    API = "API"
    LOCAL = "LOCAL"


class UsagePressure(StrEnum):
    UNKNOWN = "UNKNOWN"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    EXHAUSTED = "EXHAUSTED"


class RunState(StrEnum):
    PENDING = "PENDING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    RECONCILING = "RECONCILING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REQUIRES_HUMAN = "REQUIRES_HUMAN"


TERMINAL_RUN_STATES = {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED}


class TaskType(StrEnum):
    RESEARCH = "RESEARCH"
    ANALYSIS = "ANALYSIS"
    IMPLEMENT = "IMPLEMENT"
    REVIEW = "REVIEW"
    EXPERIMENT = "EXPERIMENT"
    OTHER = "OTHER"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EventType(StrEnum):
    RUN_CREATED = "RUN_CREATED"
    RUN_STARTED = "RUN_STARTED"
    RUN_PROGRESS = "RUN_PROGRESS"
    NODE_ENTERED = "NODE_ENTERED"
    NODE_EXITED = "NODE_EXITED"
    TOOL_REQUESTED = "TOOL_REQUESTED"
    TOOL_STARTED = "TOOL_STARTED"
    TOOL_COMPLETED = "TOOL_COMPLETED"
    TOOL_FAILED = "TOOL_FAILED"
    FILE_CHANGED = "FILE_CHANGED"
    DIFF_AVAILABLE = "DIFF_AVAILABLE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_RESOLVED = "APPROVAL_RESOLVED"
    ARTIFACT_CREATED = "ARTIFACT_CREATED"
    CHECKPOINT_SAVED = "CHECKPOINT_SAVED"
    RUN_PAUSED = "RUN_PAUSED"
    RUN_RESUMED = "RUN_RESUMED"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"
    RUN_CANCELLED = "RUN_CANCELLED"


class ErrorSummary(StrictModel):
    code: str
    message: str
    retryable: bool = False


class RuntimeHealth(StrictModel):
    runtime_id: str
    provider: Provider
    status: RuntimeStatus
    auth_mode: AuthMode
    runtime_version: str
    capabilities: list[str] = Field(default_factory=list)
    active_sessions: int = 0
    active_runs: int = 0
    observed_usage_pressure: UsagePressure = UsagePressure.UNKNOWN
    last_error: ErrorSummary | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TaskBudgets(StrictModel):
    wall_time_seconds: int = Field(default=1800, gt=0)
    max_turns: int = Field(default=20, gt=0)
    max_loop_iterations: int = Field(default=1, gt=0)
    max_parallel_runs: int = Field(default=1, gt=0)


class TaskEnvelope(StrictModel):
    task_id: str
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
    verifier_policy_ref: str = "p0-observe-only-v1"
    prompt_contract_ref: str = "p0-runtime-v1"
    context_policy_ref: str = "p0-local-worktree-v1"
    budgets: TaskBudgets = Field(default_factory=TaskBudgets)
    required_outputs: list[dict[str, Any]] = Field(default_factory=list)


class SessionConfig(StrictModel):
    run_id: str
    workspace: Path
    model: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    resume_native_session_id: str | None = None


class SessionRef(StrictModel):
    session_id: str
    run_id: str
    provider: Provider
    native_session_id: str | None = None
    workspace: Path


class RunRef(StrictModel):
    run_id: str
    session_id: str
    native_run_id: str | None = None


class AgentEvent(StrictModel):
    event_id: str
    run_id: str
    session_id: str
    provider: Provider
    native_type: str
    normalized_type: EventType
    sequence: int = Field(default=0, ge=0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: str
    causation_id: str | None = None
    node_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    adapter_version: str


class ApprovalDecisionValue(StrEnum):
    APPROVE = "APPROVE"
    APPROVE_SESSION = "APPROVE_SESSION"
    DENY = "DENY"
    CANCEL = "CANCEL"


class ApprovalRequest(StrictModel):
    approval_id: str
    run_id: str
    native_request_id: str
    method: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecision(StrictModel):
    approval_id: str
    decision: ApprovalDecisionValue


class ArtifactRef(StrictModel):
    artifact_id: str
    run_id: str
    kind: str
    path: Path
    sha256: str | None = None


class UsageSnapshot(StrictModel):
    observed_usage_pressure: UsagePressure = UsagePressure.UNKNOWN
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class WorkspaceLease(StrictModel):
    lease_id: str
    project_id: str
    run_id: str
    base_revision: str
    path: Path
    branch_name: str
    isolation: str = "WORKTREE"
    writable: bool = True
    acquired_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    cleanup_policy: str = "KEEP_ON_FAILURE"


class Project(StrictModel):
    project_id: str
    name: str
    repository_path: Path
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Task(StrictModel):
    envelope: TaskEnvelope
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Run(StrictModel):
    run_id: str
    task_id: str
    project_id: str
    provider: Provider
    state: RunState
    last_sequence: int = 0
    revision: int = 0
    session_id: str | None = None
    workspace_lease_id: str | None = None
    error: ErrorSummary | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentRuntime(Protocol):
    async def health(self) -> RuntimeHealth: ...
    async def create_session(self, config: SessionConfig) -> SessionRef: ...
    async def submit(self, session: SessionRef, task: TaskEnvelope) -> RunRef: ...
    def events(self, run: RunRef) -> AsyncIterator[AgentEvent]: ...
    async def approve(self, request: ApprovalRequest, decision: ApprovalDecision) -> None: ...
    async def interrupt(self, run: RunRef) -> None: ...
    async def resume(self, run: RunRef) -> None: ...
    async def artifacts(self, run: RunRef) -> list[ArtifactRef]: ...
    async def usage(self, run: RunRef) -> UsageSnapshot: ...
    async def terminate(self, run: RunRef) -> None: ...
