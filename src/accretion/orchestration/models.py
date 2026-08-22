from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from accretion.contracts import (
    GraphEdgeKind,
    GraphNodeKind,
    Provider,
    RiskLevel,
    StrictModel,
    TaskBudgets,
    UsagePressure,
)


class PlannerRuntime(StrEnum):
    AUTO = "AUTO"
    CLAUDE = "CLAUDE"
    CODEX = "CODEX"
    DETERMINISTIC = "DETERMINISTIC"


class RuntimeRequirement(StrEnum):
    ANY = "ANY"
    CLAUDE = "CLAUDE"
    CODEX = "CODEX"
    DETERMINISTIC = "DETERMINISTIC"
    HUMAN = "HUMAN"


class ConditionOperator(StrEnum):
    ALL = "ALL"
    ANY = "ANY"
    NOT = "NOT"
    EQ = "EQ"
    NE = "NE"
    LT = "LT"
    LTE = "LTE"
    GT = "GT"
    GTE = "GTE"
    IN = "IN"


class TypedCondition(StrictModel):
    """Restricted JSON expression tree; it is data and is never evaluated as code."""

    schema_version: Literal["2.0"] = "2.0"
    operator: ConditionOperator
    path: str | None = Field(default=None, max_length=160)
    value: Any = None
    operands: list[TypedCondition] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_shape(self) -> TypedCondition:
        aggregate = {ConditionOperator.ALL, ConditionOperator.ANY, ConditionOperator.NOT}
        if self.operator in aggregate:
            expected = 1 if self.operator is ConditionOperator.NOT else None
            if not self.operands or (expected is not None and len(self.operands) != expected):
                raise ValueError(f"{self.operator.value} has an invalid operand count")
            if self.path is not None:
                raise ValueError(f"{self.operator.value} cannot declare a path")
        elif self.path is None or self.operands:
            raise ValueError(f"{self.operator.value} requires a path and no operands")
        return self


class DynamicLoopSpec(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    max_iterations: int = Field(ge=1, le=3)


class DynamicWorkflowNodeSpec(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    local_id: str = Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=48)
    kind: GraphNodeKind
    objective: str = Field(min_length=1, max_length=4_000)
    input_contract: dict[str, Any] = Field(default_factory=dict)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    runtime_requirement: RuntimeRequirement = RuntimeRequirement.ANY
    skill_refs: list[str] = Field(default_factory=list)
    capability_refs: list[str] = Field(default_factory=list)
    verifier_refs: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    max_attempts: int = Field(default=1, ge=1, le=3)
    timeout_seconds: int = Field(default=300, ge=1, le=1_800)
    loop_spec: DynamicLoopSpec | None = None
    checkpoint: bool = True
    fragment_ref: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def loop_matches_kind(self) -> DynamicWorkflowNodeSpec:
        if (self.kind is GraphNodeKind.LOOP) != (self.loop_spec is not None):
            raise ValueError("only LOOP nodes declare loop_spec, and every LOOP must declare one")
        return self


class DynamicWorkflowEdgeSpec(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    local_id: str = Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=64)
    source: str
    target: str
    kind: GraphEdgeKind = GraphEdgeKind.NORMAL
    condition: TypedCondition | None = None
    max_traversals: int | None = Field(default=None, ge=1, le=3)

    @model_validator(mode="after")
    def validate_bounds(self) -> DynamicWorkflowEdgeSpec:
        bounded = {GraphEdgeKind.LOOP_BACK, GraphEdgeKind.RETRY}
        if self.kind in bounded and self.max_traversals is None:
            raise ValueError("loop and retry edges require max_traversals")
        if self.kind not in bounded and self.max_traversals is not None:
            raise ValueError("only loop and retry edges may declare max_traversals")
        if self.kind is GraphEdgeKind.CONDITION and self.condition is None:
            raise ValueError("conditional edges require a typed condition")
        if self.kind is not GraphEdgeKind.CONDITION and self.condition is not None:
            raise ValueError("typed conditions are allowed only on conditional edges")
        return self


class SearchBudgetRequest(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    branch_count: int = Field(default=2, ge=1, le=2)
    max_parallel: int = Field(default=2, ge=1, le=2)
    total: TaskBudgets


class WorkflowProposal(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    proposal_id: str
    task_id: str
    run_id: str | None = None
    based_on_graph_revision: int | None = Field(default=None, ge=1)
    planner_version: str = "fragment-planner-v2"
    planner_runtime: PlannerRuntime = PlannerRuntime.DETERMINISTIC
    objective: str = Field(min_length=1, max_length=20_000)
    assumptions: list[str] = Field(default_factory=list)
    nodes: list[DynamicWorkflowNodeSpec] = Field(min_length=1, max_length=32)
    edges: list[DynamicWorkflowEdgeSpec] = Field(default_factory=list, max_length=64)
    required_capabilities: list[str] = Field(default_factory=list)
    requested_search_budget: SearchBudgetRequest | None = None
    expected_verifiers: list[str] = Field(default_factory=list)
    expected_approval_gates: list[str] = Field(default_factory=list)
    rationale_summary: str = Field(min_length=1, max_length=4_000)
    confidence: float = Field(ge=0, le=1)
    provenance_refs: list[str] = Field(default_factory=list)
    fragment_refs: list[str] = Field(default_factory=list)
    repair_attempt: int = Field(default=0, ge=0, le=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ValidationSeverity(StrEnum):
    WARNING = "WARNING"
    ERROR = "ERROR"


class ValidationFinding(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    code: str
    message: str
    severity: ValidationSeverity
    path: str | None = None
    repairable: bool = False


class GraphValidationStatus(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    REPAIRABLE = "REPAIRABLE"


class GraphValidationResult(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    validation_id: str
    proposal_id: str
    status: GraphValidationStatus
    errors: list[ValidationFinding] = Field(default_factory=list)
    warnings: list[ValidationFinding] = Field(default_factory=list)
    normalized_graph_hash: str | None = None
    required_repairs: list[str] = Field(default_factory=list)
    validator_version: str = "graph-validator-v2"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CapabilitySnapshot(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    capabilities: dict[str, RiskLevel] = Field(default_factory=dict)
    protected_capabilities: set[str] = Field(default_factory=set)
    skills: set[str] = Field(default_factory=set)
    verifiers: set[str] = Field(default_factory=set)
    available_runtimes: set[Provider] = Field(default_factory=set)


class PolicySnapshot(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    allowed_capabilities: set[str] = Field(default_factory=set)
    denied_capabilities: set[str] = Field(default_factory=set)
    required_verifiers: set[str] = Field(default_factory=set)
    require_approval_at_or_above: RiskLevel = RiskLevel.HIGH
    maximum_risk: RiskLevel = RiskLevel.CRITICAL
    execution_runtime: Provider | None = None


class ProjectFeatureSettings(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    project_id: str
    dynamic_workflows: bool = False
    candidate_search: bool = False
    experience_retrieval: bool = False
    revision: int = Field(default=1, ge=1)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReplanReason(StrEnum):
    INITIAL = "INITIAL"
    EVIDENCE_CHANGE = "EVIDENCE_CHANGE"
    NODE_FAILURE = "NODE_FAILURE"
    BUDGET_CHANGE = "BUDGET_CHANGE"
    RUNTIME_FAILURE = "RUNTIME_FAILURE"
    HUMAN_REQUEST = "HUMAN_REQUEST"


class ReplanStatus(StrEnum):
    REQUESTED = "REQUESTED"
    VALIDATING = "VALIDATING"
    ACTIVATED = "ACTIVATED"
    REJECTED = "REJECTED"
    REQUIRES_HUMAN = "REQUIRES_HUMAN"


class ReplanRequest(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    replan_request_id: str
    run_id: str
    based_on_graph_revision: int = Field(ge=1)
    reason: ReplanReason
    evidence_refs: list[str] = Field(default_factory=list)
    requested_by: str
    status: ReplanStatus = ReplanStatus.REQUESTED
    resulting_proposal_id: str | None = None
    resulting_revision: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunGraphRevision(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    revision_id: str
    run_graph_id: str
    run_id: str
    revision: int = Field(ge=1)
    parent_revision: int | None = Field(default=None, ge=1)
    proposal_id: str
    reason: ReplanReason
    nodes: list[DynamicWorkflowNodeSpec]
    edges: list[DynamicWorkflowEdgeSpec]
    normalized_graph_hash: str
    protected_state_refs: list[str] = Field(default_factory=list)
    activated_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkflowValidationOutcome(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    proposal: WorkflowProposal
    validation: GraphValidationResult
    fallback_run_id: str | None = None


class WorkflowActivationOutcome(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    run_id: str
    proposal_id: str
    revision: RunGraphRevision
    workflow_template_id: str


class GraphRevisionDiff(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    run_id: str
    from_revision: int = Field(ge=1)
    to_revision: int = Field(ge=1)
    added_nodes: list[str] = Field(default_factory=list)
    removed_nodes: list[str] = Field(default_factory=list)
    changed_nodes: list[str] = Field(default_factory=list)
    added_edges: list[str] = Field(default_factory=list)
    removed_edges: list[str] = Field(default_factory=list)
    changed_edges: list[str] = Field(default_factory=list)
    protected_state_refs: list[str] = Field(default_factory=list)


class ReplanOutcome(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    request: ReplanRequest
    proposal: WorkflowProposal
    validation: GraphValidationResult
    revision: RunGraphRevision | None = None


class RuntimeCandidate(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    provider: Provider
    runtime_version: str
    available: bool
    historical_quality: float = Field(ge=0, le=1)
    usage_pressure: UsagePressure
    expected_latency: float = Field(ge=0, le=1)
    risk_penalty: float = Field(ge=0, le=1)
    specialization_fit: float = Field(ge=0, le=1)
    score: float
    exclusion_reason: str | None = None


class RuntimeDecision(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    decision_id: str
    run_id: str
    node_id: str
    candidates: list[RuntimeCandidate]
    selected_runtime: Provider | None
    selected_reason: str
    policy_version: str = "performance-router-v2"
    fallback_order: list[Provider] = Field(default_factory=list)
    observed_features: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
