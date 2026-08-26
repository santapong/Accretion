from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    repository_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProjectFeatureSettingsRow(Base):
    __tablename__ = "project_feature_settings"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskRow(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    envelope: Mapped[dict[str, Any]] = mapped_column(JSON)
    prompt_contract_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    context_bundle_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    current_profile_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    current_strategy_decision_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PromptContractRow(Base):
    __tablename__ = "prompt_contracts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(64))
    contract: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ContextBundleRow(Base):
    __tablename__ = "context_bundles"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(64))
    bundle: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskProfileRow(Base):
    __tablename__ = "task_profiles"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    profiler_version: Mapped[str] = mapped_column(String(64))
    profile: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_task_profiles_task_created", "task_id", "created_at"),)


class StrategyDecisionRow(Base):
    __tablename__ = "strategy_decisions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    task_profile_id: Mapped[str] = mapped_column(
        ForeignKey("task_profiles.id", ondelete="RESTRICT")
    )
    policy_version: Mapped[str] = mapped_column(String(64))
    decision: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_strategy_decisions_task_created", "task_id", "created_at"),)


class StrategyOverrideRow(Base):
    __tablename__ = "strategy_overrides"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    original_decision_id: Mapped[str] = mapped_column(
        ForeignKey("strategy_decisions.id", ondelete="RESTRICT")
    )
    resulting_decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategy_decisions.id", ondelete="RESTRICT"), nullable=True
    )
    operator_identity: Mapped[str] = mapped_column(String(255))
    accepted: Mapped[bool] = mapped_column(Boolean)
    override: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_strategy_overrides_task_created", "task_id", "created_at"),)


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(32))
    last_sequence: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    session_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    workspace_lease_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    strategy_decision_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    execution_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    workflow_template_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acceptance_policy_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    loop_execution_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    budget_spent: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_runs_state", "state"),)


class AcceptancePolicyRow(Base):
    __tablename__ = "acceptance_policies"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    version: Mapped[str] = mapped_column(String(64))
    policy: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LoopExecutionRow(Base):
    __tablename__ = "loop_executions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    node_key: Mapped[str] = mapped_column(String(64), default="evaluate")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    acceptance_policy_id: Mapped[str] = mapped_column(
        ForeignKey("acceptance_policies.id", ondelete="RESTRICT")
    )
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32))
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "run_id", "node_key", "attempt", name="uq_loop_executions_run_node_attempt"
        ),
        Index("ix_loop_executions_status", "status"),
    )


class LoopIterationRow(Base):
    __tablename__ = "loop_iterations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    loop_execution_id: Mapped[str] = mapped_column(
        ForeignKey("loop_executions.id", ondelete="CASCADE")
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    iteration: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("loop_execution_id", "number", name="uq_loop_iterations_execution_number"),
        Index("ix_loop_iterations_execution_number", "loop_execution_id", "number"),
    )


class VerificationRow(Base):
    __tablename__ = "verifications"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    loop_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("loop_executions.id", ondelete="CASCADE"), nullable=True
    )
    iteration_id: Mapped[str | None] = mapped_column(
        ForeignKey("loop_iterations.id", ondelete="CASCADE"), nullable=True
    )
    verifier_id: Mapped[str] = mapped_column(String(255))
    verifier_version: Mapped[str] = mapped_column(String(64))
    target_ref: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_verifications_run_executed", "run_id", "executed_at"),
        Index("ix_verifications_iteration", "iteration_id"),
    )


class RuntimeSessionRow(Base):
    __tablename__ = "runtime_sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), unique=True)
    provider: Mapped[str] = mapped_column(String(32))
    native_session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    workspace_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkspaceLeaseRow(Base):
    __tablename__ = "workspace_leases"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), unique=True)
    base_revision: Mapped[str] = mapped_column(String(128))
    path: Mapped[str] = mapped_column(Text, unique=True)
    branch_name: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    cleanup_policy: Mapped[str] = mapped_column(String(32))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentEventRow(Base):
    __tablename__ = "agent_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    session_id: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(32))
    native_type: Mapped[str] = mapped_column(Text)
    normalized_type: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    correlation_id: Mapped[str] = mapped_column(String(128))
    causation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    adapter_version: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_events_run_sequence"),
        Index("ix_agent_events_run_sequence", "run_id", "sequence"),
    )


class CheckpointRow(Base):
    __tablename__ = "checkpoints"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_checkpoints_run_sequence"),
        Index("ix_checkpoints_run_sequence", "run_id", "sequence"),
    )


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApprovalRow(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    node_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    native_request_id: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "native_request_id", name="uq_approvals_run_native"),
    )


class SideEffectOperationRow(Base):
    __tablename__ = "side_effect_operations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    capability_id: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    intent_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CapabilityRow(Base):
    __tablename__ = "capabilities"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    capability_id: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(64))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("capability_id", "version", name="uq_capabilities_id_version"),
        Index("ix_capabilities_enabled_id", "enabled", "capability_id"),
    )


class SkillRow(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(64))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("skill_id", "version", name="uq_skills_id_version"),)


class PluginRow(Base):
    __tablename__ = "plugins"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    plugin_id: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(64))
    checksum: Mapped[str] = mapped_column(String(64))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    allowlisted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("plugin_id", "version", name="uq_plugins_id_version"),
        Index("ix_plugins_allowlisted_id", "allowlisted", "plugin_id"),
    )


class PluginVersionRow(Base):
    __tablename__ = "plugin_versions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    plugin_version_id: Mapped[str] = mapped_column(String(255), unique=True)
    plugin_id: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(64))
    manifest_digest: Mapped[str] = mapped_column(String(64))
    trust_level: Mapped[str] = mapped_column(String(32))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("plugin_id", "version", name="uq_plugin_versions_id_version"),
        Index("ix_plugin_versions_digest", "manifest_digest"),
    )


class PluginInstallationRow(Base):
    __tablename__ = "plugin_installations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    installation_id: Mapped[str] = mapped_column(String(255), unique=True)
    workspace_id: Mapped[str] = mapped_column(String(255))
    plugin_id: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32))
    trust_level: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("workspace_id", "plugin_id", name="uq_plugin_installations_ws_plugin"),
        Index("ix_plugin_installations_workspace_state", "workspace_id", "state"),
    )


class PluginAuditEventRow(Base):
    """Append-only: no ``updated_at``, and nothing in the store ever deletes a row."""

    __tablename__ = "plugin_audit_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    plugin_event_id: Mapped[str] = mapped_column(String(255), unique=True)
    plugin_id: Mapped[str] = mapped_column(String(255))
    installation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_plugin_audit_events_plugin_created", "plugin_id", "created_at"),
        Index("ix_plugin_audit_events_installation", "installation_id"),
    )


class PrincipalRow(Base):
    __tablename__ = "principals"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    principal_id: Mapped[str] = mapped_column(String(255), unique=True)
    issuer: Mapped[str] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_principals_issuer_subject"),
    )


class WorkspaceRow(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(255), unique=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkspaceMembershipRow(Base):
    __tablename__ = "workspace_memberships"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    membership_id: Mapped[str] = mapped_column(String(255), unique=True)
    workspace_id: Mapped[str] = mapped_column(String(255))
    principal_id: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "principal_id", name="uq_workspace_memberships_pair"
        ),
    )


class AuthSessionRow(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    auth_session_id: Mapped[str] = mapped_column(String(255), unique=True)
    principal_id: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_auth_sessions_principal", "principal_id", "revoked"),)


class OAuthTransactionRow(Base):
    """Short-lived connector authorization state, single-use by state."""

    __tablename__ = "oauth_transactions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    state: Mapped[str] = mapped_column(String(255), unique=True)
    connector_id: Mapped[str] = mapped_column(String(255))
    principal_id: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TokenHandleRow(Base):
    """Opaque handle metadata. Carries no token material (SDD 18: secrets stay outside)."""

    __tablename__ = "token_handles"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    token_handle_id: Mapped[str] = mapped_column(String(255), unique=True)
    connector_id: Mapped[str] = mapped_column(String(255))
    workspace_id: Mapped[str] = mapped_column(String(255))
    principal_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_token_handles_owner", "connector_id", "principal_id"),
        Index("ix_token_handles_status", "status", "expires_at"),
    )


class SecretRecordRow(Base):
    """Ciphertext envelope. The master key lives outside PostgreSQL (SDD 13.3)."""

    __tablename__ = "secret_records"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    secret_store_key: Mapped[str] = mapped_column(String(255), unique=True)
    key_id: Mapped[str] = mapped_column(String(64))
    nonce: Mapped[str] = mapped_column(String(64))
    ciphertext: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuthTransactionRow(Base):
    __tablename__ = "auth_transactions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    state: Mapped[str] = mapped_column(String(255), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConnectorDefinitionRow(Base):
    __tablename__ = "connector_definitions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    connector_id: Mapped[str] = mapped_column(String(255), unique=True)
    auth_type: Mapped[str] = mapped_column(String(32))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_connector_definitions_auth", "auth_type", "connector_id"),)


class ConnectionRow(Base):
    __tablename__ = "connections"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    connection_id: Mapped[str] = mapped_column(String(255), unique=True)
    connector_id: Mapped[str] = mapped_column(String(255))
    workspace_id: Mapped[str] = mapped_column(String(255))
    principal_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scope: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_connections_connector_status", "connector_id", "status"),
        Index("ix_connections_workspace", "workspace_id", "principal_id"),
    )


class CapabilityBindingRow(Base):
    __tablename__ = "capability_bindings"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    binding_id: Mapped[str] = mapped_column(String(255), unique=True)
    capability_id: Mapped[str] = mapped_column(String(255))
    connector_id: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("capability_id", "connector_id", name="uq_capability_bindings_cap_conn"),
        Index("ix_capability_bindings_capability", "enabled", "capability_id"),
    )


class McpServerRow(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mcp_server_id: Mapped[str] = mapped_column(String(255), unique=True)
    workspace_id: Mapped[str] = mapped_column(String(255))
    connector_id: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_mcp_servers_workspace_state", "workspace_id", "state"),
        Index("ix_mcp_servers_connector", "connector_id", "enabled"),
    )


class McpDiscoverySnapshotRow(Base):
    __tablename__ = "mcp_server_discovery_snapshots"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    discovery_snapshot_id: Mapped[str] = mapped_column(String(255), unique=True)
    mcp_server_id: Mapped[str] = mapped_column(String(255))
    connection_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    valid: Mapped[bool] = mapped_column(Boolean)
    content_sha256: Mapped[str] = mapped_column(String(64))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "ix_mcp_snapshots_server_connection_created",
            "mcp_server_id",
            "connection_id",
            "created_at",
        ),
    )


class McpServerEventRow(Base):
    __tablename__ = "mcp_server_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mcp_event_id: Mapped[str] = mapped_column(String(255), unique=True)
    mcp_server_id: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_mcp_events_server_created", "mcp_server_id", "created_at"),
        Index("ix_mcp_events_correlation", "correlation_id"),
    )


class CapabilityPolicyRow(Base):
    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(64))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("policy_id", "version", name="uq_policies_id_version"),)


class CapabilityRequestRow(Base):
    __tablename__ = "capability_requests"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    capability_id: Mapped[str] = mapped_column(String(255))
    capability_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    authorization: Mapped[dict[str, Any]] = mapped_column(JSON)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    side_effect_operation_id: Mapped[str | None] = mapped_column(
        ForeignKey("side_effect_operations.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_capability_requests_run_created", "run_id", "created_at"),
        Index("ix_capability_requests_capability", "capability_id", "capability_version"),
    )


class ProjectVersionRow(Base):
    __tablename__ = "project_versions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(64))
    revision: Mapped[str] = mapped_column(String(128))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_project_versions_project_version"),
    )


class ExperienceRow(Base):
    __tablename__ = "experiences"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"))
    source_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"))
    source_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("search_candidates.id", ondelete="RESTRICT"), nullable=True
    )
    source_key: Mapped[str] = mapped_column(String(96), unique=True)
    repository_identity: Mapped[str] = mapped_column(String(64))
    trust: Mapped[str] = mapped_column(String(16))
    polarity: Mapped[str] = mapped_column(String(16))
    retracted: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_experiences_project_created", "project_id", "created_at"),
        Index("ix_experiences_repository_trust", "repository_identity", "trust"),
    )


class TrajectorySegmentRow(Base):
    __tablename__ = "trajectory_segments"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    experience_id: Mapped[str] = mapped_column(
        ForeignKey("experiences.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("experience_id", "ordinal", name="uq_trajectory_segment_ordinal"),
        Index("ix_trajectory_segments_experience", "experience_id", "ordinal"),
    )


class ExperienceEmbeddingRow(Base):
    __tablename__ = "experience_embeddings"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    experience_id: Mapped[str] = mapped_column(
        ForeignKey("experiences.id", ondelete="CASCADE"), unique=True
    )
    version: Mapped[str] = mapped_column(String(64))
    input_digest: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[Any] = mapped_column(VECTOR(384))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExperienceQueryRow(Base):
    __tablename__ = "experience_queries"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    repository_identity: Mapped[str] = mapped_column(String(64))
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    embedding: Mapped[Any] = mapped_column(VECTOR(384))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_experience_queries_task_created", "task_id", "created_at"),)


class ExperienceMatchRow(Base):
    __tablename__ = "experience_matches"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    query_id: Mapped[str] = mapped_column(
        ForeignKey("experience_queries.id", ondelete="CASCADE")
    )
    experience_id: Mapped[str] = mapped_column(
        ForeignKey("experiences.id", ondelete="RESTRICT")
    )
    rank: Mapped[int] = mapped_column(Integer)
    disposition: Mapped[str] = mapped_column(String(16))
    final_score: Mapped[float] = mapped_column(Float)
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("query_id", "experience_id", name="uq_experience_match_query_source"),
        UniqueConstraint("query_id", "rank", name="uq_experience_match_query_rank"),
        Index("ix_experience_matches_query_rank", "query_id", "rank"),
    )


class ExperienceSelectionRow(Base):
    __tablename__ = "experience_selections"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    query_id: Mapped[str] = mapped_column(
        ForeignKey("experience_queries.id", ondelete="RESTRICT")
    )
    expected_context_bundle_id: Mapped[str] = mapped_column(
        ForeignKey("context_bundles.id", ondelete="RESTRICT")
    )
    resulting_context_bundle_id: Mapped[str] = mapped_column(
        ForeignKey("context_bundles.id", ondelete="RESTRICT")
    )
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_experience_selections_task", "task_id", "created_at"),)


class ExperienceModerationActionRow(Base):
    __tablename__ = "experience_moderation_actions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    experience_id: Mapped[str] = mapped_column(
        ForeignKey("experiences.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(16))
    expected_revision: Mapped[int] = mapped_column(Integer)
    resulting_revision: Mapped[int] = mapped_column(Integer)
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "experience_id", "resulting_revision", name="uq_experience_moderation_revision"
        ),
        Index("ix_experience_moderation_experience", "experience_id", "created_at"),
    )


class TrajectoryReplaySeedRow(Base):
    __tablename__ = "trajectory_replay_seeds"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    search_id: Mapped[str] = mapped_column(ForeignKey("search_plans.id", ondelete="CASCADE"))
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("search_candidates.id", ondelete="CASCADE"), unique=True
    )
    match_id: Mapped[str] = mapped_column(
        ForeignKey("experience_matches.id", ondelete="RESTRICT")
    )
    experience_id: Mapped[str] = mapped_column(
        ForeignKey("experiences.id", ondelete="RESTRICT")
    )
    validation_status: Mapped[str] = mapped_column(String(16))
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_trajectory_replay_seeds_search", "search_id", "created_at"),)


class EvidenceRow(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ClaimRow(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TheoryRow(Base):
    __tablename__ = "theories"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HypothesisRow(Base):
    __tablename__ = "hypotheses"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExperimentRow(Base):
    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExperimentRunRow(Base):
    __tablename__ = "experiment_runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"))
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResultRow(Base):
    __tablename__ = "results"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    experiment_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("experiment_runs.id", ondelete="CASCADE"), nullable=True
    )
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DecisionRow(Base):
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BenchmarkTaskRow(Base):
    __tablename__ = "benchmark_tasks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    benchmark_task_id: Mapped[str] = mapped_column(String(96))
    version: Mapped[str] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(32))
    task_type: Mapped[str] = mapped_column(String(32))
    environment_ref: Mapped[str] = mapped_column(String(255))
    environment_version: Mapped[str] = mapped_column(String(32))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("benchmark_task_id", "version", name="uq_benchmark_tasks_id_version"),
        Index("ix_benchmark_tasks_category", "category", "task_type"),
    )


class BenchmarkRunRow(Base):
    __tablename__ = "benchmark_runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    suite_version: Mapped[str] = mapped_column(String(32))
    configuration_version: Mapped[str] = mapped_column(String(32))
    execution_source: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    corpus_sha256: Mapped[str] = mapped_column(String(64))
    trace_sha256: Mapped[str] = mapped_column(String(64))
    scenario_count: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_benchmark_runs_suite_started", "suite_version", "started_at"),)


class ArchitectureMetricRow(Base):
    __tablename__ = "architecture_metrics"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    benchmark_run_id: Mapped[str] = mapped_column(
        ForeignKey("benchmark_runs.id", ondelete="CASCADE")
    )
    benchmark_task_id: Mapped[str] = mapped_column(String(96))
    task_version: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(16))
    verifier_id: Mapped[str] = mapped_column(String(96))
    selector_version: Mapped[str] = mapped_column(String(64))
    metric: Mapped[dict[str, Any]] = mapped_column(JSON)

    __table_args__ = (
        UniqueConstraint(
            "benchmark_run_id",
            "benchmark_task_id",
            "task_version",
            "mode",
            name="uq_architecture_metrics_run_task_mode",
        ),
        Index("ix_architecture_metrics_filters", "mode", "provider", "verifier_id"),
    )


class WorkflowTemplateRow(Base):
    __tablename__ = "workflow_templates"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    checksum: Mapped[str] = mapped_column(String(64))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_workflow_templates_id_version"),
        Index("ix_workflow_templates_id_status", "template_id", "status"),
    )


class RunGraphRow(Base):
    __tablename__ = "run_graphs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), unique=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    template_record_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_templates.id", ondelete="RESTRICT")
    )
    template_id: Mapped[str] = mapped_column(String(64))
    template_version: Mapped[str] = mapped_column(String(32))
    template_checksum: Mapped[str] = mapped_column(String(64))
    graph_revision: Mapped[int] = mapped_column(Integer, default=1)
    instantiated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RunGraphNodeRow(Base):
    __tablename__ = "run_graph_nodes"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_graph_id: Mapped[str] = mapped_column(ForeignKey("run_graphs.id", ondelete="CASCADE"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    position: Mapped[int] = mapped_column(Integer)
    node: Mapped[dict[str, Any]] = mapped_column(JSON)

    __table_args__ = (
        UniqueConstraint("run_graph_id", "key", name="uq_run_graph_nodes_graph_key"),
        Index("ix_run_graph_nodes_run", "run_id"),
    )


class RunGraphEdgeRow(Base):
    __tablename__ = "run_graph_edges"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    run_graph_id: Mapped[str] = mapped_column(ForeignKey("run_graphs.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(32))
    traversal_count: Mapped[int] = mapped_column(Integer, default=0)
    position: Mapped[int] = mapped_column(Integer)
    edge: Mapped[dict[str, Any]] = mapped_column(JSON)

    __table_args__ = (UniqueConstraint("run_graph_id", "key", name="uq_run_graph_edges_graph_key"),)


class WorkflowProposalRow(Base):
    __tablename__ = "workflow_proposals"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=True
    )
    based_on_graph_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    planner_version: Mapped[str] = mapped_column(String(64))
    proposal: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_workflow_proposals_task_created", "task_id", "created_at"),
        Index("ix_workflow_proposals_run_created", "run_id", "created_at"),
    )


class GraphValidationResultRow(Base):
    __tablename__ = "graph_validation_results"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_proposals.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(32))
    validator_version: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_graph_validation_proposal_created", "proposal_id", "created_at"),)


class RunGraphRevisionRow(Base):
    __tablename__ = "run_graph_revisions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_graph_id: Mapped[str] = mapped_column(ForeignKey("run_graphs.id", ondelete="CASCADE"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer)
    parent_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_proposals.id", ondelete="RESTRICT")
    )
    graph_hash: Mapped[str] = mapped_column(String(64))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("run_graph_id", "revision", name="uq_run_graph_revisions_graph_revision"),
        Index("ix_run_graph_revisions_run_revision", "run_id", "revision"),
    )


class ReplanRequestRow(Base):
    __tablename__ = "replan_requests"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    based_on_graph_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_replan_requests_run_created", "run_id", "created_at"),)


class RuntimeDecisionRow(Base):
    __tablename__ = "runtime_decisions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    node_id: Mapped[str] = mapped_column(String(128))
    selected_runtime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(64))
    decision: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_runtime_decisions_run_created", "run_id", "created_at"),)


class SearchPlanRow(Base):
    __tablename__ = "search_plans"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    parent_node_id: Mapped[str] = mapped_column(String(96))
    graph_revision: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "graph_revision",
            "parent_node_id",
            name="uq_search_plan_run_revision_node",
        ),
        Index("ix_search_plans_run_created", "run_id", "created_at"),
        Index("ix_search_plans_status_updated", "status", "updated_at"),
    )


class SearchCandidateRow(Base):
    __tablename__ = "search_candidates"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    search_id: Mapped[str] = mapped_column(
        ForeignKey("search_plans.id", ondelete="CASCADE")
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    trajectory: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("search_id", "ordinal", name="uq_search_candidates_ordinal"),
        Index("ix_search_candidates_search_ordinal", "search_id", "ordinal"),
    )


class CandidateScoreRow(Base):
    __tablename__ = "candidate_scores"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    search_id: Mapped[str] = mapped_column(
        ForeignKey("search_plans.id", ondelete="CASCADE")
    )
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("search_candidates.id", ondelete="CASCADE"), unique=True
    )
    eligible: Mapped[bool] = mapped_column(Boolean)
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_candidate_scores_search", "search_id", "created_at"),)


class SearchPromotionRow(Base):
    __tablename__ = "search_promotions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    search_id: Mapped[str] = mapped_column(
        ForeignKey("search_plans.id", ondelete="CASCADE"), unique=True
    )
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("search_candidates.id", ondelete="RESTRICT")
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32))
    record: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("ix_search_promotions_run", "run_id", "created_at"),)
