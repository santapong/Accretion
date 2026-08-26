from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from accretion.contracts import (
    ApprovalDecisionValue,
    BenchmarkExecutionSource,
    ConnectionScope,
    ConnectionStatus,
    ExecutionMode,
    McpDiscoveryPolicy,
    McpHealthPolicy,
    McpToolMapping,
    McpTrustLevel,
    Principal,
    Provider,
    RiskLevel,
    TaskBudgets,
    TaskType,
    TemplateStatus,
    WorkspaceMembership,
)
from accretion.orchestration.models import (
    PlannerRuntime,
    ReplanReason,
    SearchBudgetEnvelope,
    SearchMode,
)


class MeResponse(BaseModel):
    principal: Principal
    memberships: list[WorkspaceMembership]
    auth_mode: str


class AuthProviderInfo(BaseModel):
    mode: str
    issuer: str | None = None


class CapabilityResolveRequest(BaseModel):
    capability_id: str
    version: str | None = None
    principal_id: str | None = None
    workspace_id: str | None = None


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
    experience_retrieval: bool | None = None
    expected_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def require_feature(self) -> ProjectFeatureUpdate:
        if (
            self.dynamic_workflows is None
            and self.candidate_search is None
            and self.experience_retrieval is None
        ):
            raise ValueError("at least one feature setting is required")
        return self


class ExperienceMaterializeCreate(BaseModel):
    candidate_id: str | None = None


class ExperienceQueryCreate(BaseModel):
    task_id: str
    include_failures: bool = True
    top_k: int = Field(default=5, ge=1, le=10)
    max_age_days: int | None = Field(default=None, ge=1, le=3650)


class ExperienceSelectionCreate(BaseModel):
    query_id: str
    match_ids: list[str] = Field(min_length=1, max_length=3)
    expected_context_bundle_id: str


class ExperienceRetractCreate(BaseModel):
    reason: str = Field(min_length=1, max_length=2_000)
    expected_revision: int = Field(ge=1)


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
    replay_seed_match_ids: list[str] = Field(default_factory=list, max_length=3)
    negative_guidance_match_ids: list[str] = Field(default_factory=list, max_length=3)


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


class ConnectCreate(BaseModel):
    """Start an authorization. Scopes default to the connector's declared minimum."""

    workspace_id: str = "workspace_local"
    scopes: list[str] | None = None
    redirect_target: str = "/"


class AuthorizationStart(BaseModel):
    authorization_url: str


class ConnectionSummary(BaseModel):
    """Read-only connection listing; token handles never enter the API (INV3-002)."""

    connection_id: str
    connector_id: str
    workspace_id: str
    principal_id: str | None = None
    scope: ConnectionScope
    status: ConnectionStatus
    granted_scopes: list[str] = Field(default_factory=list)
    workspace_shareable: bool = False
    created_at: datetime
    last_health_check: datetime | None = None


class McpServerCreate(BaseModel):
    workspace_id: str
    connector_id: str
    name: str = Field(min_length=1, max_length=255)
    endpoint: str
    protocol_versions: list[str] = Field(default_factory=lambda: ["2026-07-28"])
    auth_profile_ref: str | None = None
    trust_level: McpTrustLevel = McpTrustLevel.RESTRICTED
    health_policy: McpHealthPolicy = Field(default_factory=McpHealthPolicy)
    discovery_policy: McpDiscoveryPolicy = Field(default_factory=McpDiscoveryPolicy)
    allowed_tool_patterns: list[str] = Field(default_factory=lambda: ["*"])
    denied_tool_patterns: list[str] = Field(default_factory=list)
    tool_mappings: list[McpToolMapping] = Field(default_factory=list)


class PluginInstallRequest(BaseModel):
    """Install or upgrade a package into one workspace.

    ``consent_digest`` must echo the manifest digest the administrator was shown, and
    ``consent_capability_ids`` may narrow what policy granted but never widen it.
    """

    workspace_id: str
    reference: str = Field(min_length=1, max_length=255)
    consent_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    consent_capability_ids: list[str] = Field(default_factory=list)
    expected_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class PluginWorkspaceRequest(BaseModel):
    workspace_id: str


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    correlation_id: str
    retryable: bool = False
