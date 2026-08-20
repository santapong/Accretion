from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

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


class ExecutionMode(StrEnum):
    DIRECT = "DIRECT"
    LOOP = "LOOP"
    GRAPH = "GRAPH"
    HYBRID = "HYBRID"


class ExpectedHorizon(StrEnum):
    SHORT = "SHORT"
    MEDIUM = "MEDIUM"
    LONG = "LONG"


class OverridePolicyResult(StrEnum):
    ACCEPTED = "ACCEPTED"
    DENIED_TEMPLATE_MISMATCH = "DENIED_TEMPLATE_MISMATCH"
    DENIED_SAFETY_POLICY = "DENIED_SAFETY_POLICY"


class PromptContract(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    prompt_contract_id: str
    task_id: str
    version: Literal["p1-task-execution-v1"] = "p1-task-execution-v1"
    role: str
    objective: str = Field(min_length=1, max_length=20_000)
    hard_constraints: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    tool_rules: list[str] = Field(default_factory=list)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    uncertainty_policy: dict[str, Any] = Field(default_factory=dict)
    completion_criteria: list[str] = Field(default_factory=list)
    examples: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContextBundle(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    context_bundle_id: str
    task_ref: str
    version: Literal["context-bundle-v1"] = "context-bundle-v1"
    phase: str = "TASK_EXECUTION"
    project_summary: str
    evidence_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    workspace_map: dict[str, Any] = Field(default_factory=dict)
    previous_failure_refs: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    freshness: dict[str, Any] = Field(default_factory=dict)
    provenance: list[str] = Field(default_factory=list)
    token_budget: int = Field(default=8_000, gt=0)
    experience_refs: list[str] = Field(default_factory=list, max_length=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FeatureEvidence(StrictModel):
    feature: str
    value: bool | int | float | str | list[str] | None = None
    source: str
    available: bool = True
    rationale: str


class TaskProfile(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    profile_id: str
    task_id: str
    complexity: float | None = Field(default=None, ge=0, le=1)
    structure_certainty: float | None = Field(default=None, ge=0, le=1)
    feedback_dependency: float | None = Field(default=None, ge=0, le=1)
    dependency_complexity: float | None = Field(default=None, ge=0, le=1)
    parallelism_potential: float | None = Field(default=None, ge=0, le=1)
    uncertainty: float | None = Field(default=None, ge=0, le=1)
    verifier_strength: float | None = Field(default=None, ge=0, le=1)
    risk: RiskLevel
    irreversible_actions: bool
    expected_horizon: ExpectedHorizon
    profile_confidence: float = Field(ge=0, le=1)
    observed_features: list[FeatureEvidence] = Field(default_factory=list)
    unknown_features: list[str] = Field(default_factory=list)
    semantic_rationale: str
    profiler_version: Literal["deterministic-profiler-v1"] = "deterministic-profiler-v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StrategyDecision(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    decision_id: str
    task_id: str
    selected_mode: ExecutionMode
    selected_template_id: str
    task_profile_ref: str
    policy_version: Literal["selector-v1"] = "selector-v1"
    matched_rules: list[str] = Field(default_factory=list)
    alternatives: list[ExecutionMode] = Field(default_factory=list)
    rationale: str
    operator_override_allowed: bool
    requires_approval: bool = False
    requires_independent_verifier: bool = False
    supersedes_decision_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StrategyOverride(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    override_id: str
    task_id: str
    original_decision_id: str
    requested_mode: ExecutionMode
    requested_template_id: str
    operator_identity: str
    reason: str = Field(min_length=1, max_length=2_000)
    policy_result: OverridePolicyResult
    accepted: bool
    resulting_decision_id: str | None = None
    denial_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
    prompt_contract_id: str | None = None
    context_bundle_id: str | None = None
    current_profile_id: str | None = None
    current_strategy_decision_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TaskPlanning(StrictModel):
    task_id: str
    prompt_contract: PromptContract
    context_bundle: ContextBundle
    current_profile: TaskProfile
    current_decision: StrategyDecision
    profile_history: list[TaskProfile] = Field(default_factory=list)
    decision_history: list[StrategyDecision] = Field(default_factory=list)
    override_history: list[StrategyOverride] = Field(default_factory=list)


class StrategyOverrideResult(StrictModel):
    override: StrategyOverride
    current_decision: StrategyDecision


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
