from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class Provider(StrEnum):
    CLAUDE = "CLAUDE"
    CODEX = "CODEX"
    OPENCODE = "OPENCODE"
    FAKE = "FAKE"
    DETERMINISTIC = "DETERMINISTIC"
    HUMAN = "HUMAN"


LIVE_PROVIDERS: frozenset[Provider] = frozenset(
    {Provider.CLAUDE, Provider.CODEX, Provider.OPENCODE}
)
"""Providers backed by a signed-in agent CLI, gated by the live-provider policy."""


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


class BenchmarkCategory(StrEnum):
    DIRECT_SIMPLE = "DIRECT_SIMPLE"
    FEEDBACK_REFINEMENT = "FEEDBACK_REFINEMENT"
    PREDICTABLE_GRAPH = "PREDICTABLE_GRAPH"
    HYBRID_ENGINEERING = "HYBRID_ENGINEERING"
    SAFETY_RECOVERY = "SAFETY_RECOVERY"


class BenchmarkExecutionSource(StrEnum):
    REPLAY = "REPLAY"
    LIVE = "LIVE"


class BenchmarkRunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


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
    HUMAN = "HUMAN"
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


class TemplateStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    RETIRED = "RETIRED"


class EdgeGuard(StrEnum):
    ON_SUCCESS = "ON_SUCCESS"
    ON_FAIL = "ON_FAIL"
    ON_INCONCLUSIVE = "ON_INCONCLUSIVE"
    ON_APPROVED = "ON_APPROVED"
    ON_DENIED = "ON_DENIED"
    ON_REPLAN_AVAILABLE = "ON_REPLAN_AVAILABLE"
    ON_REPLAN_EXHAUSTED = "ON_REPLAN_EXHAUSTED"


class TerminalOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REQUIRES_HUMAN = "REQUIRES_HUMAN"


# StrEnum comparisons are alphabetical (CRITICAL < HIGH < LOW < MEDIUM); every
# severity comparison must go through this explicit ordering instead.
RISK_RANK: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


class CheckpointKind(StrEnum):
    NODE_BOUNDARY = "NODE_BOUNDARY"
    SIDE_EFFECT_BOUNDARY = "SIDE_EFFECT_BOUNDARY"


class ErrorSummary(StrictModel):
    code: str
    message: str
    retryable: bool = False


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    CANCELLED = "CANCELLED"


class CapabilityKind(StrEnum):
    TOOL = "TOOL"
    AGENT = "AGENT"


class CapabilityBackend(StrEnum):
    MCP = "MCP"
    NATIVE = "NATIVE"
    HTTP = "HTTP"
    CLI = "CLI"
    PYTHON = "PYTHON"
    AGENT_RUNTIME = "AGENT_RUNTIME"


class IdempotencyMode(StrEnum):
    NONE = "NONE"
    KEYED = "KEYED"
    TRANSACTIONAL = "TRANSACTIONAL"


class AuthorizationOutcome(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


class CapabilityExecutionStatus(StrEnum):
    DENIED = "DENIED"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class Capability(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    capability_id: str
    kind: CapabilityKind = CapabilityKind.TOOL
    version: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = RiskLevel.LOW
    side_effects: list[str] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    credential_refs: list[str] = Field(default_factory=list)
    idempotency: IdempotencyMode = IdempotencyMode.NONE
    backend: CapabilityBackend
    provider_projections: dict[str, Any] = Field(default_factory=dict)
    verifier_policy_ref: str | None = None
    enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MetaSkill(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    skill_id: str
    version: str
    description: str
    activation_criteria: dict[str, Any] = Field(default_factory=dict)
    instructions: str
    required_capabilities: list[str] = Field(default_factory=list)
    required_context: list[str] = Field(default_factory=list)
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    verifiers: list[str] = Field(default_factory=list)
    examples: list[dict[str, Any]] = Field(default_factory=list)
    provider_overrides: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MetaPlugin(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    plugin_id: str
    version: str
    description: str = ""
    capability_refs: list[str] = Field(default_factory=list)
    skill_refs: list[str] = Field(default_factory=list)
    verifier_refs: list[str] = Field(default_factory=list)
    policy_refs: list[str] = Field(default_factory=list)
    provider_projections: dict[str, Any] = Field(default_factory=dict)
    checksum: str
    allowlisted: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CapabilityPolicy(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    policy_id: str
    version: str
    description: str = ""
    explicitly_denied: list[str] = Field(default_factory=list)
    require_approval_at_risk: RiskLevel = RiskLevel.HIGH
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CredentialReference(StrictModel):
    credential_ref: str
    available: bool


class CapabilityRequest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str
    run_id: str
    node_id: str
    capability_id: str
    capability_version: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    declared_reason: str = Field(min_length=1, max_length=2_000)
    idempotency_key: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CapabilityAuthorization(StrictModel):
    outcome: AuthorizationOutcome
    policy_id: str
    policy_version: str
    reason: str
    approval_id: str | None = None


class CapabilityExecutionResult(StrictModel):
    request: CapabilityRequest
    authorization: CapabilityAuthorization
    status: CapabilityExecutionStatus
    output: dict[str, Any] | None = None
    error: ErrorSummary | None = None
    side_effect_operation_id: str | None = None
    completed_at: datetime | None = None


class PrincipalType(StrEnum):
    HUMAN = "HUMAN"
    SERVICE = "SERVICE"


class PrincipalStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class WorkspaceRole(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    DEVELOPER = "DEVELOPER"
    RESEARCHER = "RESEARCHER"
    VIEWER = "VIEWER"
    SERVICE = "SERVICE"


class Principal(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    principal_id: str
    type: PrincipalType = PrincipalType.HUMAN
    # Identity uniqueness derives from (issuer, subject), never email alone.
    issuer: str
    subject: str
    email: str | None = None
    display_name: str | None = None
    status: PrincipalStatus = PrincipalStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PrincipalRef(StrictModel):
    principal_id: str
    display_name: str | None = None
    status: PrincipalStatus


class WorkspaceEntity(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    workspace_id: str
    name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkspaceMembership(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    membership_id: str
    workspace_id: str
    principal_id: str
    role: WorkspaceRole
    revision: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuthSession(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    auth_session_id: str
    principal_id: str
    issued_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    revoked: bool = False


class AuthTransaction(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    transaction_id: str
    state: str
    nonce: str
    code_verifier: str
    redirect_target: str = "/"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime


class OAuthTransactionPurpose(StrEnum):
    """Why an OAuth transaction exists.

    A login state and a connector state must never be redeemable at each other's
    callback (ADR3-003), so purpose is mandatory and carries no default.
    """

    CONNECT = "CONNECT"
    REAUTHORIZE = "REAUTHORIZE"


class OAuthTransaction(StrictModel):
    """Short-lived, single-use connector authorization state (SDD 19.1).

    Deliberately a sibling of AuthTransaction rather than a widening of it: sharing
    one state keyspace between SSO login and connector authorization is the confused
    deputy ADR3-003 exists to prevent.
    """

    schema_version: Literal["1.0"] = "1.0"
    transaction_id: str
    purpose: OAuthTransactionPurpose
    state: str
    code_verifier: str
    connector_id: str
    principal_id: str
    workspace_id: str
    connection_id: str | None = None
    requested_scopes: list[str] = Field(default_factory=list)
    redirect_target: str = "/"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime


class TokenStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    ERROR = "ERROR"


class TokenHandle(StrictModel):
    """Opaque reference to broker-held credentials (SDD 6.2).

    Carries no token material. ``secret_store_key`` addresses the ciphertext in the
    secret store and is never returned through the public API or runtime context.
    """

    schema_version: Literal["1.0"] = "1.0"
    token_handle_id: str
    connector_id: str
    principal_id: str | None = None
    workspace_id: str
    issuer: str
    scopes: list[str] = Field(default_factory=list)
    audience: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    secret_store_key: str
    status: TokenStatus = TokenStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    refreshed_at: datetime | None = None


class ConnectorKind(StrEnum):
    MCP = "MCP"
    REST = "REST"
    GRAPHQL = "GRAPHQL"
    SDK = "SDK"
    LOCAL = "LOCAL"


class ConnectorAuthType(StrEnum):
    NONE = "NONE"
    OAUTH2 = "OAUTH2"
    OIDC = "OIDC"
    API_KEY = "API_KEY"
    SERVICE_ACCOUNT = "SERVICE_ACCOUNT"
    EMA = "EMA"


class ConnectionScope(StrEnum):
    USER = "USER"
    WORKSPACE = "WORKSPACE"


class ConnectionStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    REVOKED = "REVOKED"


class CapabilityBindingBackend(StrictModel):
    type: CapabilityBackend
    server_ref: str | None = None
    method: str | None = None
    tool_name: str | None = None


class CapabilityBinding(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    binding_id: str
    capability_id: str
    connector_id: str
    backend: CapabilityBindingBackend
    input_transform_ref: str | None = None
    output_transform_ref: str | None = None
    policy_ref: str | None = None
    enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConnectorDefinition(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    connector_id: str
    plugin_id: str | None = None
    name: str
    kind: ConnectorKind
    auth_type: ConnectorAuthType = ConnectorAuthType.NONE
    authorization_server: str | None = None
    resource_server: str | None = None
    default_scopes: list[str] = Field(default_factory=list)
    optional_scopes: list[str] = Field(default_factory=list)
    connection_scope: ConnectionScope = ConnectionScope.USER
    health_check_ref: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Connection(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    connection_id: str
    connector_id: str
    workspace_id: str
    principal_id: str | None = None
    scope: ConnectionScope = ConnectionScope.USER
    token_handle_ref: str | None = None
    granted_scopes: list[str] = Field(default_factory=list)
    status: ConnectionStatus = ConnectionStatus.PENDING
    workspace_shareable: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_health_check: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConnectionRef(StrictModel):
    connection_id: str
    connector_id: str
    status: ConnectionStatus


class CapabilityResolutionOutcome(StrEnum):
    OK = "OK"
    NO_CONNECTOR_REQUIRED = "NO_CONNECTOR_REQUIRED"
    REQUIRE_REAUTH = "REQUIRE_REAUTH"
    NO_CONNECTION = "NO_CONNECTION"
    DISABLED = "DISABLED"


class ResolvedCapability(StrictModel):
    capability: Capability
    outcome: CapabilityResolutionOutcome
    binding: CapabilityBinding | None = None
    connection: ConnectionRef | None = None
    reason: str = ""


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
    schema_version: Literal["1.0", "2.0"] = "1.0"
    context_bundle_id: str
    task_ref: str
    version: Literal["context-bundle-v1", "context-bundle-v2"] = "context-bundle-v1"
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
    supersedes_context_bundle_id: str | None = None
    experience_query_id: str | None = None
    experience_match_refs: list[str] = Field(default_factory=list, max_length=3)
    experience_refs: list[str] = Field(default_factory=list, max_length=3)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_experience_revision(self) -> ContextBundle:
        has_experience = bool(
            self.experience_refs
            or self.experience_match_refs
            or self.experience_query_id
            or self.supersedes_context_bundle_id
        )
        if self.version == "context-bundle-v1" and (
            self.schema_version != "1.0" or has_experience
        ):
            raise ValueError("ContextBundle v1 cannot reference experience")
        if self.version == "context-bundle-v2":
            if self.schema_version != "2.0":
                raise ValueError("ContextBundle v2 requires schema version 2.0")
            if not all(
                (
                    self.supersedes_context_bundle_id,
                    self.experience_query_id,
                    self.experience_refs,
                    self.experience_match_refs,
                )
            ):
                raise ValueError("ContextBundle v2 requires complete experience provenance")
            if len(self.experience_refs) != len(self.experience_match_refs):
                raise ValueError("experience and match references must align")
        return self


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
    WORKFLOW_PROPOSAL_CREATED = "WORKFLOW_PROPOSAL_CREATED"
    WORKFLOW_PROPOSAL_REPAIRED = "WORKFLOW_PROPOSAL_REPAIRED"
    GRAPH_VALIDATION_STARTED = "GRAPH_VALIDATION_STARTED"
    GRAPH_VALIDATION_RESULT = "GRAPH_VALIDATION_RESULT"
    GRAPH_REVISION_ACTIVATED = "GRAPH_REVISION_ACTIVATED"
    REPLAN_REQUESTED = "REPLAN_REQUESTED"
    REPLAN_STARTED = "REPLAN_STARTED"
    REPLAN_COMPLETED = "REPLAN_COMPLETED"
    RUNTIME_DECISION = "RUNTIME_DECISION"
    SEARCH_STARTED = "SEARCH_STARTED"
    SEARCH_CANDIDATE_STARTED = "SEARCH_CANDIDATE_STARTED"
    SEARCH_CANDIDATE_COMPLETED = "SEARCH_CANDIDATE_COMPLETED"
    SEARCH_CANDIDATE_PRUNED = "SEARCH_CANDIDATE_PRUNED"
    SEARCH_SELECTION = "SEARCH_SELECTION"
    SEARCH_PROMOTION_STARTED = "SEARCH_PROMOTION_STARTED"
    SEARCH_PROMOTION_COMPLETED = "SEARCH_PROMOTION_COMPLETED"
    SEARCH_STOPPED = "SEARCH_STOPPED"
    EXPERIENCE_QUERY = "EXPERIENCE_QUERY"
    EXPERIENCE_RETRIEVED = "EXPERIENCE_RETRIEVED"
    TRAJECTORY_REPLAY_STARTED = "TRAJECTORY_REPLAY_STARTED"
    TRAJECTORY_REPLAY_REJECTED = "TRAJECTORY_REPLAY_REJECTED"
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


class BenchmarkTask(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    benchmark_task_id: str
    version: str
    title: str
    category: BenchmarkCategory
    task_type: TaskType
    environment_ref: str
    environment_version: str
    verifier_id: str
    verifier_version: str
    success_criteria: list[str] = Field(min_length=1)
    budgets: TaskBudgets
    applicable_modes: list[ExecutionMode] = Field(min_length=2)
    selector_mode: ExecutionMode
    selector_version: str


class ArchitectureMetric(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    metric_id: str
    benchmark_run_id: str
    benchmark_task_id: str
    task_version: str
    category: BenchmarkCategory
    task_type: TaskType
    mode: ExecutionMode
    provider: Provider
    execution_source: BenchmarkExecutionSource
    verifier_id: str
    selector_version: str
    success: bool
    quality: float = Field(ge=0, le=1)
    cost: float = Field(ge=0, le=1)
    latency: float = Field(ge=0, le=1)
    risk: float = Field(ge=0, le=1)
    human_burden: float = Field(ge=0, le=1)
    utility: float
    architecture_regret: float = Field(ge=0)
    duration_ms: int = Field(ge=0)
    turns: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    approvals: int = Field(ge=0)
    trace_ref: str
    environment_ref: str
    environment_version: str


class BenchmarkRun(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    benchmark_run_id: str
    suite_version: str
    configuration_version: str
    execution_source: BenchmarkExecutionSource
    status: BenchmarkRunStatus
    corpus_sha256: str
    trace_sha256: str
    scenario_count: int = Field(ge=0)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class AcrArchSummary(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    suite: Literal["ACR-ARCH"] = "ACR-ARCH"
    suite_version: str
    configuration_version: str
    task_count: int = Field(ge=0)
    scenario_count: int = Field(ge=0)
    latest_run: BenchmarkRun | None = None
    metrics: list[ArchitectureMetric] = Field(default_factory=list)
    filters: dict[str, list[str]] = Field(default_factory=dict)


class BenchmarkTaskDetail(StrictModel):
    task: BenchmarkTask
    metrics: list[ArchitectureMetric] = Field(default_factory=list)


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
    node_key: str = "evaluate"
    attempt: int = Field(default=1, gt=0)
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
    version: Literal["loop-projection-v1", "graph-projection-v1"] = "loop-projection-v1"
    run_id: str
    workflow_template_id: str
    run_graph_version: int = Field(default=1, gt=0)
    nodes: list[GraphProjectionNode] = Field(default_factory=list)
    edges: list[GraphProjectionEdge] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BudgetPolicy(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    version: Literal["budget-policy-v1"] = "budget-policy-v1"
    max_wall_time_seconds: int = Field(default=1800, gt=0)
    max_total_turns: int = Field(default=20, gt=0)
    max_total_tool_calls: int = Field(default=100, gt=0)
    max_runtime_calls: int = Field(default=12, gt=0)
    max_node_retries: int = Field(default=1, ge=0)
    max_replans: int = Field(default=0, ge=0)


class GateSpec(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    gate_id: str = Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=64)
    node_key: str
    summary: str
    required_for_risk_gte: RiskLevel = RiskLevel.HIGH


class NodeLoopPolicy(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    region_keys: list[str] = Field(min_length=1)
    act_key: str
    observe_key: str | None = None
    verify_in_region: bool = True
    max_iterations_source: Literal["TASK_BUDGET", "FIXED"] = "TASK_BUDGET"
    fixed_max_iterations: int | None = Field(default=None, gt=0)
    budget_fraction: float = Field(default=1.0, gt=0, le=1)


class WorkflowNodeSpec(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    key: str = Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=32)
    kind: GraphNodeKind
    label: str
    parent_key: str | None = None
    instruction: str | None = None
    loop: NodeLoopPolicy | None = None


class WorkflowEdgeSpec(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    key: str = Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=64)
    source: str
    target: str
    kind: GraphEdgeKind
    label: str | None = None
    guard: EdgeGuard | None = None


class WorkflowTemplate(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    template_record_id: str
    template_id: str = Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=64)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    mode: ExecutionMode
    input_schema: dict[str, Any] = Field(default_factory=dict)
    nodes: list[WorkflowNodeSpec] = Field(min_length=1)
    edges: list[WorkflowEdgeSpec] = Field(default_factory=list)
    global_budget_policy: BudgetPolicy = Field(default_factory=BudgetPolicy)
    required_verifiers: list[str] = Field(default_factory=list)
    required_approval_gates: list[GateSpec] = Field(default_factory=list)
    checksum: str
    status: TemplateStatus = TemplateStatus.DRAFT
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunNode(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    node_id: str
    key: str
    kind: GraphNodeKind
    label: str
    parent_id: str | None = None
    status: GraphNodeStatus = GraphNodeStatus.PENDING
    iteration: int | None = Field(default=None, ge=0)
    max_iterations: int | None = Field(default=None, gt=0)
    loop_execution_id: str | None = None
    approval_id: str | None = None
    verifier_state: VerificationStatus | None = None
    terminal_outcome: TerminalOutcome | None = None


class RunEdge(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    edge_id: str
    key: str
    source: str
    target: str
    kind: GraphEdgeKind
    label: str | None = None
    guard: EdgeGuard | None = None
    active: bool = False
    traversal_count: int = Field(default=0, ge=0)


class RunGraph(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_graph_id: str
    run_id: str
    task_id: str
    template_record_id: str
    template_id: str
    template_version: str
    template_checksum: str
    nodes: list[RunNode] = Field(min_length=1)
    edges: list[RunEdge] = Field(default_factory=list)
    graph_revision: int = Field(default=1, ge=1)
    instantiated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CheckpointLoopCursor(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    loop_execution_id: str
    iteration: int = Field(ge=0)
    revision: int = Field(ge=0)
    status: LoopExecutionStatus


class Checkpoint(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    version: Literal["checkpoint-v1"] = "checkpoint-v1"
    checkpoint_id: str
    run_id: str
    kind: CheckpointKind
    sequence: int = Field(ge=0)
    run_state: RunState
    run_revision: int = Field(ge=0)
    active_node_ids: list[str] = Field(default_factory=list)
    # The durable routing decision into the active node: without it a resume
    # could not reproduce guard-dependent behavior (e.g. a TERMINAL commit).
    arrival_edge_key: str | None = None
    node_statuses: dict[str, GraphNodeStatus] = Field(default_factory=dict)
    loop_cursors: list[CheckpointLoopCursor] = Field(default_factory=list)
    run_graph_id: str | None = None
    graph_revision: int | None = Field(default=None, ge=1)
    budget_remaining: LoopBudgetRemaining | None = None
    workspace_lease_id: str | None = None
    workspace_revision: str | None = None
    workspace_diff_sha256: str | None = None
    side_effect_operation_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))




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


class ApprovalRecord(StrictModel):
    approval_id: str
    run_id: str
    node_id: str | None = None
    native_request_id: str
    method: str
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    decision: ApprovalDecisionValue | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None


class NodeTraversal(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    node_id: str
    entered_sequence: int
    exited_sequence: int | None = None
    entered_at: datetime
    exited_at: datetime | None = None
    status: GraphNodeStatus
    iteration_number: int | None = None


class LoopIterationTraceRef(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    iteration_id: str
    number: int
    started_sequence: int | None = None
    completed_sequence: int | None = None
    acceptance: VerificationStatus | None = None
    status: str | None = None


class RuntimeCallTraceRef(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    runtime_call_id: str
    node_id: str | None = None
    started_sequence: int | None = None
    finished_sequence: int | None = None
    outcome: Literal["COMPLETED", "FAILED", "CANCELLED", "UNKNOWN"] = "UNKNOWN"


class ToolCallTraceRef(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    tool_call_id: str
    capability_id: str | None = None
    node_id: str | None = None
    requested_sequence: int
    finished_sequence: int | None = None
    status: Literal["REQUESTED", "STARTED", "COMPLETED", "FAILED"] = "REQUESTED"


class ApprovalTraceRef(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    approval_id: str
    required_sequence: int
    resolved_sequence: int | None = None
    resolved: bool = False


class VerificationTraceRef(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    verification_id: str
    verifier_id: str | None = None
    iteration_id: str | None = None
    status: VerificationStatus | None = None
    sequence: int


class CheckpointTraceRef(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    checkpoint_id: str
    kind: CheckpointKind
    node_id: str | None = None
    sequence: int


class ExecutionTrace(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    version: Literal["execution-trace-v1"] = "execution-trace-v1"
    run_id: str
    run_graph_id: str | None = None
    workflow_template_id: str | None = None
    traversals: list[NodeTraversal] = Field(default_factory=list)
    loop_iterations: list[LoopIterationTraceRef] = Field(default_factory=list)
    runtime_calls: list[RuntimeCallTraceRef] = Field(default_factory=list)
    tool_calls: list[ToolCallTraceRef] = Field(default_factory=list)
    approvals: list[ApprovalTraceRef] = Field(default_factory=list)
    verifications: list[VerificationTraceRef] = Field(default_factory=list)
    checkpoints: list[CheckpointTraceRef] = Field(default_factory=list)
    last_sequence: int = Field(ge=0)
    terminal_state: RunState | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
    context_history: list[ContextBundle] = Field(default_factory=list)
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


class RunAudit(StrictModel):
    """Authoritative, linked evidence bundle for one operator-visible run."""

    schema_version: Literal["1.0"] = "1.0"
    run: Run
    task: Task
    profile: TaskProfile
    strategy: StrategyDecision
    template: WorkflowTemplate
    runtime: RuntimeHealth
    session: SessionRef | None = None
    events: list[AgentEvent] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    verifications: list[VerificationResult] = Field(default_factory=list)
    capability_results: list[CapabilityExecutionResult] = Field(default_factory=list)
    trace: ExecutionTrace
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
