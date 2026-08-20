from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

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


TERMINAL_RUN_STATES = {
    RunState.SUCCEEDED,
    RunState.FAILED,
    RunState.CANCELLED,
    RunState.REQUIRES_HUMAN,
}


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


class LoopExecutionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REQUIRES_HUMAN = "REQUIRES_HUMAN"


class LoopIterationStatus(StrEnum):
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class LoopStopReason(StrEnum):
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    MAX_ITERATIONS = "MAX_ITERATIONS"
    WALL_TIME_EXCEEDED = "WALL_TIME_EXCEEDED"
    MAX_TOOL_CALLS = "MAX_TOOL_CALLS"
    MAX_TURNS = "MAX_TURNS"
    NO_PROGRESS = "NO_PROGRESS"
    REPEATED_FAILURE = "REPEATED_FAILURE"
    POLICY_ESCALATION = "POLICY_ESCALATION"
    VERIFIER_UNAVAILABLE = "VERIFIER_UNAVAILABLE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    OPERATOR_CANCELLED = "OPERATOR_CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class VerificationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class FindingSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class VerificationTargetKind(StrEnum):
    OUTPUT_CONTRACT = "OUTPUT_CONTRACT"
    GIT_DIFF = "GIT_DIFF"
    COMMAND_SUITE = "COMMAND_SUITE"
    TRAJECTORY_POLICY = "TRAJECTORY_POLICY"


class IterationDirectiveKind(StrEnum):
    INITIAL = "INITIAL"
    REPAIR = "REPAIR"


class GraphNodeKind(StrEnum):
    TASK = "TASK"
    AGENT = "AGENT"
    TOOL = "TOOL"
    VERIFIER = "VERIFIER"
    GATE = "GATE"
    LOOP = "LOOP"
    JOIN = "JOIN"
    TERMINAL = "TERMINAL"


class GraphNodeStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class GraphEdgeKind(StrEnum):
    NORMAL = "NORMAL"
    CONDITION = "CONDITION"
    LOOP_BACK = "LOOP_BACK"
    RETRY = "RETRY"
    ERROR = "ERROR"
    FANOUT = "FANOUT"
    MERGE = "MERGE"
    APPROVAL = "APPROVAL"


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
    RUNTIME_CALL_STARTED = "RUNTIME_CALL_STARTED"
    RUNTIME_CALL_COMPLETED = "RUNTIME_CALL_COMPLETED"
    RUNTIME_CALL_FAILED = "RUNTIME_CALL_FAILED"
    RUNTIME_CALL_CANCELLED = "RUNTIME_CALL_CANCELLED"
    LOOP_ITERATION_STARTED = "LOOP_ITERATION_STARTED"
    LOOP_ITERATION_COMPLETED = "LOOP_ITERATION_COMPLETED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_RESULT = "VERIFICATION_RESULT"
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
    max_tool_calls: int = Field(default=100, gt=0)
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


class LoopBudgetRemaining(StrictModel):
    wall_time_seconds: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    turns: int = Field(ge=0)
    iterations: int = Field(ge=0)


class LoopSpec(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    version: Literal["loop-engine-v1"] = "loop-engine-v1"
    loop_id: str
    max_iterations: int = Field(default=3, gt=0)
    max_wall_time_seconds: int = Field(default=1800, gt=0)
    max_tool_calls: int = Field(default=100, gt=0)
    max_turns: int = Field(default=20, gt=0)
    no_progress_window: int = Field(default=2, gt=0)
    repeated_failure_threshold: int = Field(default=2, gt=0)
    provider_failure_threshold: int = Field(default=2, gt=0)
    success_condition: Literal["ACCEPTANCE_POLICY_PASS"] = "ACCEPTANCE_POLICY_PASS"
    no_progress_condition: Literal["UNCHANGED_EVIDENCE_FINGERPRINT"] = (
        "UNCHANGED_EVIDENCE_FINGERPRINT"
    )
    escalation_target: str = "HUMAN"
    verifier_refs: list[str] = Field(default_factory=list)


class LoopState(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    iteration: int = Field(default=0, ge=0)
    latest_observation_ref: str | None = None
    accumulated_evidence_refs: list[str] = Field(default_factory=list)
    progress_score: float | None = Field(default=None, ge=0, le=1)
    repeated_failure_signature: str | None = None
    consecutive_no_progress: int = Field(default=0, ge=0)
    repeated_failure_count: int = Field(default=0, ge=0)
    provider_failure_count: int = Field(default=0, ge=0)
    budget_remaining: LoopBudgetRemaining


class Finding(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    code: str
    severity: FindingSeverity
    message: str
    path: str | None = None
    line: int | None = Field(default=None, gt=0)
    evidence_ref: str | None = None
    fingerprint: str | None = None


class VerificationTarget(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    target_ref: str
    kind: VerificationTargetKind
    run_id: str
    iteration_id: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    required_outputs: list[dict[str, Any]] = Field(default_factory=list)
    expected_changed_paths: list[str] = Field(default_factory=list)
    require_git_changes: bool = True
    expected_diff_sha256: str | None = None
    command_suite_refs: list[str] = Field(default_factory=list)


class VerificationContext(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: str
    project_id: str
    workspace: Path
    allowed_capabilities: list[str] = Field(default_factory=list)
    denied_capabilities: list[str] = Field(default_factory=list)
    observed_capabilities: list[str] = Field(default_factory=list)
    unresolved_approval_ids: list[str] = Field(default_factory=list)
    trajectory_events: list[dict[str, Any]] = Field(default_factory=list)
    timeout_seconds: float = Field(default=300, gt=0, le=3600)
    max_output_bytes: int = Field(default=1_000_000, gt=0, le=10_000_000)


class VerificationResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    verification_id: str
    run_id: str
    iteration_id: str | None = None
    verifier_id: str
    verifier_version: str
    target_ref: str
    status: VerificationStatus
    score: float | None = Field(default=None, ge=0, le=1)
    findings: list[Finding] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    false_accept_risk_estimate: float | None = Field(default=None, ge=0, le=1)
    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: int = Field(default=0, ge=0)


class AcceptancePolicy(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    policy_id: str
    version: Literal["acceptance-policy-v1"] = "acceptance-policy-v1"
    required_verifiers: list[str] = Field(default_factory=list)
    all_required_must_pass: bool = True
    score_thresholds: dict[str, Annotated[float, Field(ge=0, le=1)]] = Field(
        default_factory=dict
    )
    allow_inconclusive: bool = False
    require_independent_reviewer: bool = False
    independent_reviewer_ref: str | None = None
    require_human_if_risk_gte: RiskLevel | None = RiskLevel.HIGH
    outcome_check: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LoopIteration(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    iteration_id: str
    loop_execution_id: str
    run_id: str
    number: int = Field(gt=0)
    status: LoopIterationStatus
    runtime_call_ref: str | None = None
    observation_ref: str | None = None
    diff_artifact_ref: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    verification_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    diff_sha256: str | None = None
    output_fingerprint: str | None = None
    finding_signature: str | None = None
    tool_calls: int = Field(default=0, ge=0)
    turns: int = Field(default=0, ge=0)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error: ErrorSummary | None = None


class LoopExecution(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    loop_execution_id: str
    run_id: str
    spec: LoopSpec
    state: LoopState
    acceptance_policy_ref: str
    acceptance_policy: AcceptancePolicy | None = None
    status: LoopExecutionStatus = LoopExecutionStatus.PENDING
    stop_reason: LoopStopReason | None = None
    revision: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class IterationDirective(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: IterationDirectiveKind
    objective: str = Field(min_length=1, max_length=20_000)
    findings: list[Finding] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    previous_iteration_id: str | None = None


class RuntimeExecutionRequest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    version: Literal["p2-runtime-request-v1"] = "p2-runtime-request-v1"
    runtime_call_id: str
    run_id: str
    task: TaskEnvelope
    iteration_number: int = Field(default=1, gt=0)
    directive: IterationDirective
    deadline: datetime | None = None
    max_turns: int = Field(default=20, gt=0)
    max_tool_calls: int = Field(default=100, gt=0)


class GraphProjectionNode(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    node_id: str
    parent_id: str | None = None
    kind: GraphNodeKind
    label: str
    status: GraphNodeStatus
    provider: Provider | None = None
    iteration: int | None = Field(default=None, ge=0)
    max_iterations: int | None = Field(default=None, gt=0)
    artifact_count: int = Field(default=0, ge=0)
    verifier_state: VerificationStatus | None = None
    risk: RiskLevel


class GraphProjectionEdge(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    edge_id: str
    source: str
    target: str
    kind: GraphEdgeKind
    label: str | None = None
    active: bool = False
    traversal_count: int = Field(default=0, ge=0)


class GraphProjection(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    version: Literal["loop-projection-v1"] = "loop-projection-v1"
    run_id: str
    workflow_template_id: str
    run_graph_version: int = Field(default=1, gt=0)
    nodes: list[GraphProjectionNode] = Field(default_factory=list)
    edges: list[GraphProjectionEdge] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
    runtime_call_id: str | None = None


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
    strategy_decision_id: str | None = None
    execution_mode: ExecutionMode | None = None
    workflow_template_id: str | None = None
    acceptance_policy_id: str | None = None
    loop_execution_id: str | None = None
    error: ErrorSummary | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentRuntime(Protocol):
    async def health(self) -> RuntimeHealth: ...
    async def create_session(self, config: SessionConfig) -> SessionRef: ...
    async def submit(
        self, session: SessionRef, request: TaskEnvelope | RuntimeExecutionRequest
    ) -> RunRef: ...
    def events(self, run: RunRef) -> AsyncIterator[AgentEvent]: ...
    async def approve(self, request: ApprovalRequest, decision: ApprovalDecision) -> None: ...
    async def interrupt(self, run: RunRef) -> None: ...
    async def resume(self, run: RunRef) -> None: ...
    async def artifacts(self, run: RunRef) -> list[ArtifactRef]: ...
    async def usage(self, run: RunRef) -> UsageSnapshot: ...
    async def terminate(self, run: RunRef) -> None: ...
