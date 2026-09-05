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
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


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


class ResearchEvidenceRow(Base):
    """v0.3 M5 Evidence Store.

    Deliberately not the orphaned ``evidence`` table: the v0.4 registry pins
    ``EvidenceRef`` identity there, and squatting it would force a Major
    migration later. No foreign key to ``runs``: evidence must outlive the run
    it was gathered for, and a cascading delete would destroy it.
    """

    __tablename__ = "research_evidence"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(255), unique=True)
    run_id: Mapped[str] = mapped_column(String(255))
    capability_id: Mapped[str] = mapped_column(String(255))
    connector_id: Mapped[str] = mapped_column(String(255))
    source_id: Mapped[str] = mapped_column(String(1024))
    content_digest: Mapped[str] = mapped_column(String(64))
    trust: Mapped[str] = mapped_column(String(32))
    trust_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_research_evidence_run_created", "run_id", "created_at"),
        Index("ix_research_evidence_run_digest", "run_id", "content_digest"),
        Index("ix_research_evidence_connector_capability", "connector_id", "capability_id"),
        Index("ix_research_evidence_source", "source_id"),
        Index("ix_research_evidence_trust", "trust"),
    )


class IdentityAssertionRow(Base):
    """Retained identity assertion metadata (v0.3 M7).

    Carries no assertion material: the sealed assertion lives in
    ``secret_records`` under ``secret_store_key``.
    """

    __tablename__ = "identity_assertions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    assertion_id: Mapped[str] = mapped_column(String(255), unique=True)
    auth_session_id: Mapped[str] = mapped_column(String(255))
    principal_id: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_identity_assertions_session", "auth_session_id"),
        Index("ix_identity_assertions_principal_status", "principal_id", "status"),
        Index("ix_identity_assertions_status_expires", "status", "expires_at"),
    )


class EnterpriseAuthGrantRow(Base):
    """Append-only: no ``updated_at``, and nothing in the store ever updates or
    deletes a row."""

    __tablename__ = "enterprise_auth_grants"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    grant_id: Mapped[str] = mapped_column(String(255), unique=True)
    principal_id: Mapped[str] = mapped_column(String(255))
    workspace_id: Mapped[str] = mapped_column(String(255))
    connector_id: Mapped[str] = mapped_column(String(255))
    mcp_server_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connection_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_enterprise_grants_principal_created", "principal_id", "created_at"),
        Index("ix_enterprise_grants_connector_created", "connector_id", "created_at"),
        Index("ix_enterprise_grants_outcome", "outcome"),
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
    # v0.3 M5: which connector, binding and connection served the call, and the
    # source ids it yielded. Nullable because every row written before M5 has none.
    provenance: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
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


# ---------------------------------------------------------------------------
# v0.4 M0 — the routing contract family (SDD v0.4 §13, ADR-058)
#
# Fifteen additive tables, created by migration ``0017_v04_m0_routing_contracts``
# and touched by nothing before it. They share one row shape, declared once in
# ``V04ContractRow`` below, because they share one guarantee: every row is a
# sealed ``CanonicalContract`` document (registry §3) that is written once and
# never updated.
#
# There is no ``updated_at`` on any of them, and no store method that could set
# one. These tables are append-only by construction (ADR-058).
# ---------------------------------------------------------------------------


class V04ContractRow(Base):
    """The registry §3 header, as columns. Abstract: it maps no table of its own.

    Every v0.4 contract carries the same eight header fields, and every one of the
    fifteen §13 tables therefore stores the same seven columns beside whatever it
    promotes for itself. Declaring them fifteen times would have been fifteen chances
    to get one of them subtly wrong — a nullable that should not be, a ``String(40)``
    where the id is longer, a missing timezone on a timestamp — and the store's
    read/write helpers would then have had nothing common to be written against, which
    is how ``MemoryStore`` and ``PostgresStore`` drift apart.

    ``id``
        The ADR-055 prefixed id, which *is* the contract's ``contract_id``. There is no
        second surrogate key: a v0.4 record already carries a globally unique opaque id
        before it reaches the store, and minting another one would create a second
        answer to "which row is this receipt", which registry §16's provenance chain
        cannot tolerate.
    ``workspace_id``
        Promoted because every ``list_`` scopes on it.
    ``project_id``
        Nullable exactly where the contract sets ``PROJECT_SCOPED = False`` (the three
        router-scoped records). A foreign key to ``projects.id`` with
        ``ondelete="RESTRICT"`` — never ``CASCADE``: §13.1's last bullet and registry
        §16 both forbid a project deletion from silently erasing the evidence that
        explains why a decision was made. It is a ``declared_attr`` because a
        ``ForeignKey`` object belongs to exactly one table and cannot be shared by
        fifteen.
    ``content_hash``
        The ADR-056 digest the contract sealed itself with, promoted so the store can
        refuse a duplicate document under a new id without deserialising every row.
    ``schema_version``
        Registry §3 semver, promoted because §13.1's uniqueness rule is over the
        *tuple* (hash, version), not the hash alone.
    ``supersedes_contract_id``
        Registry §3 header. A revision is a new row that points at the row it replaces;
        nothing is ever edited in place (registry §17: "historical records are never
        rewritten in place"). Promoted on every table rather than only the three that
        also carry an integer ``revision``, because walking a supersession chain is a
        query, and a query against a JSON blob is a table scan.
    ``payload``
        The whole contract as ``model_dump(mode="json")``. Reads reconstruct through
        ``model_validate`` and never assemble a contract from the promoted columns,
        which exist only to be queried and indexed.
    ``created_at``
        Timezone-aware, mirroring the header.

    Subclasses add the §13 key columns they are queried by, their own
    ``UniqueConstraint("content_hash", "schema_version")`` — the constraint cannot live
    here, because a named constraint on an abstract base would try to give fifteen
    tables the same constraint name — and their own indexes.
    """

    __abstract__ = True

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(32))
    supersedes_contract_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    @declared_attr
    @classmethod
    def project_id(cls) -> Mapped[str | None]:
        return mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), nullable=True)


class ObjectiveContractRow(V04ContractRow):
    """SDD §7.1 objective contract. Append-only; a revision is a new row.

    ``revision`` is promoted beside ``supersedes_contract_id`` because the two answer
    different questions — "which generation is this" and "which exact row did it
    replace" — and an objective contract that was approved twice at the same revision
    number is a governance incident rather than a merge.
    """

    __tablename__ = "objective_contracts"

    revision: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint(
            "content_hash", "schema_version", name="uq_objective_contracts_hash_version"
        ),
        Index("ix_objective_contracts_project_created", "project_id", "created_at"),
        Index("ix_objective_contracts_workspace_created", "workspace_id", "created_at"),
        Index("ix_objective_contracts_supersedes", "supersedes_contract_id"),
    )


class NodeContractRow(V04ContractRow):
    """SDD §7.2 node contract. §13 asks for "ID, project, graph revision, hash, JSON,
    immutable flag".

    ``immutable_hash`` is the contract's *derived* digest — the identity a routing
    receipt pins and a compatibility decision is evaluated against — and is distinct
    from ``content_hash``, which seals the whole document including its header. Both
    are promoted because M1 and M2 look rows up by either one.

    **Deviation from §13, recorded rather than invented.** §13's key-field list ends with
    an "immutable flag" and this table promotes no such column. Every row in this family is
    immutable by construction — there is no ``update_`` or ``delete_`` method for any of
    the fifteen tables on any of the three store surfaces, and a revision is a new row
    whose ``supersedes_contract_id`` names its parent — so a boolean column would be
    ``true`` on every row ever written, and a constant column is a column a later
    milestone can only get wrong. What a caller actually needs from the flag is the thing
    that makes the contract immutable, and that is promoted: ``immutable_hash``, the
    digest M1 pins a compatibility decision to and M2 pins a receipt to.
    """

    __tablename__ = "node_contracts"

    node_id: Mapped[str] = mapped_column(String(64))
    run_graph_id: Mapped[str] = mapped_column(String(64))
    graph_revision: Mapped[int] = mapped_column(Integer)
    execution_instance_id: Mapped[str] = mapped_column(String(64))
    immutable_hash: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("content_hash", "schema_version", name="uq_node_contracts_hash_version"),
        Index("ix_node_contracts_project_created", "project_id", "created_at"),
        Index("ix_node_contracts_workspace_created", "workspace_id", "created_at"),
        Index("ix_node_contracts_graph_revision", "run_graph_id", "graph_revision"),
        Index("ix_node_contracts_immutable_hash", "immutable_hash"),
        Index("ix_node_contracts_supersedes", "supersedes_contract_id"),
    )


class VerificationSpecRow(V04ContractRow):
    """SDD §7.3 verification specification. §13 asks for "ID, version, hash, JSON"."""

    __tablename__ = "verification_specs"

    revision: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint(
            "content_hash", "schema_version", name="uq_verification_specs_hash_version"
        ),
        Index("ix_verification_specs_project_created", "project_id", "created_at"),
        Index("ix_verification_specs_workspace_created", "workspace_id", "created_at"),
        Index("ix_verification_specs_supersedes", "supersedes_contract_id"),
    )


class RoutingRequestRow(V04ContractRow):
    """SDD §7.4 routing context, stored under §13's name ``routing_requests``.

    The row id *is* §8.2's idempotency key: ``RoutingContext.contract_id`` carries the
    ``rrq`` prefix, so "repeated requests with identical immutable inputs MUST return
    the same receipt" is enforceable as a primary-key lookup here plus the unique
    ``routing_request_id`` on ``routing_receipts`` below.

    The four snapshot ids are promoted together because §8.3's snapshot-consistency rule
    is a query over all four at once: a request whose registry snapshot moved is a
    *different* request and must carry a new id.

    **Deviation from §13, recorded rather than invented.** §13's key-field list for this
    table ends with "status", and there is no status column here. ``RoutingContext``
    declares no status field — it is a sealed input document, not a state machine — and
    M0's store is append-only with no update method, so a status column would be one no
    writer could ever advance. M2 owns the request lifecycle and can add the column
    additively (registry §3.2 Minor) when it owns something to put in it.
    """

    __tablename__ = "routing_requests"

    node_contract_id: Mapped[str] = mapped_column(String(64))
    node_contract_hash: Mapped[str] = mapped_column(String(64))
    available_runtime_snapshot_id: Mapped[str] = mapped_column(String(64))
    capability_registry_snapshot_id: Mapped[str] = mapped_column(String(64))
    connection_availability_snapshot_id: Mapped[str] = mapped_column(String(64))
    policy_snapshot_id: Mapped[str] = mapped_column(String(64))
    workspace_router_version: Mapped[str] = mapped_column(String(64))
    project_adapter_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "content_hash", "schema_version", name="uq_routing_requests_hash_version"
        ),
        Index("ix_routing_requests_project_created", "project_id", "created_at"),
        Index("ix_routing_requests_workspace_created", "workspace_id", "created_at"),
        Index("ix_routing_requests_node_contract", "node_contract_id", "requested_at"),
        Index("ix_routing_requests_supersedes", "supersedes_contract_id"),
    )


class ConfigurationCandidateRow(V04ContractRow):
    """SDD §7.6 configuration candidate. §13: "candidate, request, config hash,
    predictions, eligibility".

    ``configuration_hash`` is lifted out of the embedded ``ExecutionConfiguration``
    rather than given a table of its own. §13 lists no ``execution_configurations``
    table and the contract is only ever reachable through the candidate that proposed
    it, so a separate table would have added a join and a second lifetime to a value
    that has neither.

    The predictions §13 names stay in ``payload``: they are five ``DistributionEstimate``
    objects, nothing in v0.4 queries on a confidence bound, and promoting twenty float
    columns to answer no question would be the opposite of the "promote only what is
    queried" rule this file follows everywhere else.
    """

    __tablename__ = "configuration_candidates"

    routing_request_id: Mapped[str] = mapped_column(String(64))
    configuration_hash: Mapped[str] = mapped_column(String(64))
    construction_stage: Mapped[str] = mapped_column(String(48))
    hard_eligible: Mapped[bool] = mapped_column(Boolean)

    __table_args__ = (
        UniqueConstraint(
            "content_hash", "schema_version", name="uq_config_candidates_hash_version"
        ),
        Index("ix_config_candidates_project_created", "project_id", "created_at"),
        Index("ix_config_candidates_workspace_created", "workspace_id", "created_at"),
        Index("ix_config_candidates_request_created", "routing_request_id", "created_at"),
        Index("ix_config_candidates_configuration_hash", "configuration_hash"),
        Index("ix_config_candidates_supersedes", "supersedes_contract_id"),
    )


class CompatibilityDecisionRow(V04ContractRow):
    """SDD §7.7 compatibility decision. §13: "candidate, rule, status, reason".

    §13's "candidate" is spelled ``subject_ref`` here and is not always a candidate:
    ``SubjectType`` ranges over the six configuration layers plus the joint
    ``CONFIGURATION`` check, so the column has to hold whichever subject the rule
    judged. Typing it as the candidate id would have made the six per-layer decisions
    unstorable, which is the majority of them.
    """

    __tablename__ = "compatibility_decisions"

    subject_type: Mapped[str] = mapped_column(String(32))
    subject_ref: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    rule_id: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(64))
    reason_code: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint(
            "content_hash", "schema_version", name="uq_compat_decisions_hash_version"
        ),
        Index("ix_compat_decisions_project_created", "project_id", "created_at"),
        Index("ix_compat_decisions_workspace_created", "workspace_id", "created_at"),
        Index("ix_compat_decisions_subject", "subject_type", "subject_ref"),
        Index("ix_compat_decisions_rule", "rule_id", "rule_version"),
        Index("ix_compat_decisions_supersedes", "supersedes_contract_id"),
    )


class RoutingReceiptRow(V04ContractRow):
    """SDD §7.8 routing decision receipt. §13: "receipt, selected config, versions,
    propensity, decision type".

    ``routing_request_id`` is **unique**, which is §13.1's "one immutable receipt per
    routing request ID" and §8.2's idempotency guarantee written as a database
    constraint rather than as a convention. It is the one uniqueness rule in this family
    that is not about a content digest, and it is the reason a retried dispatch cannot
    quietly produce a second, differently-argued receipt for the same question.

    ``selection_propensity`` is promoted as a real column because off-policy evaluation
    reads it in bulk across every receipt in a window; a propensity buried in JSON would
    make the M4 estimator scan the table.
    """

    __tablename__ = "routing_receipts"

    routing_request_id: Mapped[str] = mapped_column(String(64), unique=True)
    node_contract_hash: Mapped[str] = mapped_column(String(64))
    selected_configuration_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    selected_configuration_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_type: Mapped[str] = mapped_column(String(32))
    selection_propensity: Mapped[float | None] = mapped_column(Float, nullable=True)
    workspace_router_version: Mapped[str] = mapped_column(String(64))
    project_adapter_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "content_hash", "schema_version", name="uq_routing_receipts_hash_version"
        ),
        Index("ix_routing_receipts_project_created", "project_id", "created_at"),
        Index("ix_routing_receipts_workspace_created", "workspace_id", "created_at"),
        Index("ix_routing_receipts_decision_type", "decision_type", "created_at"),
        Index("ix_routing_receipts_router_version", "workspace_router_version"),
        Index("ix_routing_receipts_supersedes", "supersedes_contract_id"),
    )


class RoutingOverrideRow(V04ContractRow):
    """SDD §13 ``routing_overrides``: "receipt, principal, candidate, reason".

    **This is the one table in the family with no frozen contract behind it.** PR2 froze
    nineteen models and none of them is a routing override: the SDD describes the
    override only as an API request body (§11.1) and an event payload (§12). M0 may not
    invent a twentieth contract — that is precisely the drift a freeze exists to prevent —
    so the table is created now, with §13's four key fields as real columns, and its
    ``payload`` holds the ADR-056 canonical JSON object that the store hashes into
    ``content_hash``. Every immutability guarantee the other fourteen tables get, this one
    gets too; what it does not get is a pydantic model, and M2 — which owns
    ``POST /routing-decisions/{id}/override`` — is where that model belongs.

    The row still needs an identity, and it gets its own. ``ids.py`` has mapped
    ``"override" -> "ovr"`` since v0.1, but that kind is minted by ``planning.py`` for the
    *strategy* override, so reusing it would leave an ``ovr_`` id unable to say which
    record class or which table it names — and would give M2's ``RoutingOverride.ID_KIND``
    check a second claimant to accept. This milestone therefore adds a distinct kind,
    ``"routing_override" -> "rov"``, and that is what ``put_routing_override`` ids are
    minted from. The stored document's type marker is likewise held outside the frozen
    ``accretion.<contract>`` namespace; see ``store.ROUTING_OVERRIDE_DOCUMENT_TYPE``.

    The shape of that document is frozen by
    ``tests/fixtures/records/v0.4/routing_override/minimal.json`` and the digest recorded
    for it in ``docs/releases/v0.4/m0-freeze.md``, which is how this table gets the same
    protection against a silent shape change that the fourteen committed schemas get. That
    file is kept under ``records/`` and out of ``tests/fixtures/contracts/v0.4/`` on
    purpose: the contract tree holds exactly one directory per frozen contract, and filing
    a pre-contract record there would re-assert the claim this docstring withdraws.

    ``superseding_receipt_id`` is nullable because §11.1's override endpoint records the
    human's choice before the replacement receipt is sealed; a non-null value is the link
    registry §16's provenance chain walks from the overridden decision to the executed one.
    """

    __tablename__ = "routing_overrides"

    receipt_id: Mapped[str] = mapped_column(String(64))
    principal_id: Mapped[str] = mapped_column(String(64))
    candidate_id: Mapped[str] = mapped_column(String(64))
    reason_code: Mapped[str] = mapped_column(String(64))
    superseding_receipt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "content_hash", "schema_version", name="uq_routing_overrides_hash_version"
        ),
        Index("ix_routing_overrides_project_created", "project_id", "created_at"),
        Index("ix_routing_overrides_workspace_created", "workspace_id", "created_at"),
        Index("ix_routing_overrides_receipt_created", "receipt_id", "created_at"),
        Index("ix_routing_overrides_principal", "principal_id"),
        Index("ix_routing_overrides_supersedes", "supersedes_contract_id"),
    )


class VerificationResultRow(V04ContractRow):
    """SDD §7.9 independent verification result, stored under §13's
    ``verification_results`` — **not** the v0.1 ``verifications`` table (ADR-054 a).

    The two are different records with different owners: ``verifications`` holds the v0.1
    run/iteration verifier outcome and is API-exposed, this holds the v0.4 independent
    result whose ``status`` is a ``VerificationState``. ``source_verification_id`` is the
    link between them, promoted and indexed because reconciling the two is a query M3
    runs, and nullable because an independent result need not have a v0.1 ancestor.

    **Deviation from §13, recorded rather than invented.** §13 asks for "execution, spec
    hash, status, claim results" and the first three are columns here; the claim results
    stay in ``payload``, for the reason ``FailureEventRow`` gives about its evidence list.
    A column cannot hold a list, and a join table would give the claim results a lifetime
    independent of the result that reached them — which is exactly what registry §16's
    provenance chain forbids, because a claim result is only meaningful as part of the
    sealed document whose ``content_hash`` covers it.
    """

    __tablename__ = "verification_results"

    execution_instance_id: Mapped[str] = mapped_column(String(64))
    verification_spec_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    source_verification_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "content_hash", "schema_version", name="uq_verification_results_hash_version"
        ),
        Index("ix_verification_results_project_created", "project_id", "created_at"),
        Index("ix_verification_results_workspace_created", "workspace_id", "created_at"),
        Index("ix_verification_results_execution", "execution_instance_id", "created_at"),
        Index("ix_verification_results_source", "source_verification_id"),
        Index("ix_verification_results_supersedes", "supersedes_contract_id"),
    )


class ExperienceRecordRow(V04ContractRow):
    """SDD §7.10 experience record — a **projection**, keyed by the v0.2 P7
    ``experience_id`` (ADR-054 b). §13: "source lineage, signatures, outcomes,
    visibility, eligibility".

    The primary key *is* the foreign key, which is why ``id`` is redeclared here rather
    than inherited. ``ExperienceRecord.contract_id`` carries the existing ``exp`` prefix
    because it names the same experience the P7 ``experiences`` row names, so this table
    declares one identity column that is simultaneously its own id and the reference to
    the record it projects. That is what makes "it references ``experiences``, never
    duplicates it" a property of the schema rather than a promise in a docstring: there
    is nowhere here to put a copied P7 field even if someone wanted to.

    ``ondelete="RESTRICT"`` and not ``CASCADE``: §13.1's last bullet says evidence
    deletion "must not orphan provenance silently", and a routing projection is exactly
    the provenance that would be orphaned. Retention removes the projection first, on
    purpose, or it does not remove the experience.

    The four promoted flags — ``visibility``, ``local_verification_status``,
    ``contradiction_status``, ``eligible_for_learning`` — are the entire eligibility
    predicate the M4 training-snapshot builder filters on, and it filters over the whole
    workspace at once.
    """

    __tablename__ = "experience_records"

    id: Mapped[str] = mapped_column(
        ForeignKey("experiences.id", ondelete="RESTRICT"), primary_key=True
    )
    source_node_execution_id: Mapped[str] = mapped_column(String(64))
    configuration_hash: Mapped[str] = mapped_column(String(64))
    visibility: Mapped[str] = mapped_column(String(32))
    local_verification_status: Mapped[str] = mapped_column(String(32))
    final_run_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    contradiction_status: Mapped[str] = mapped_column(String(32))
    eligible_for_learning: Mapped[bool] = mapped_column(Boolean)

    __table_args__ = (
        UniqueConstraint(
            "content_hash", "schema_version", name="uq_experience_records_hash_version"
        ),
        Index("ix_experience_records_project_created", "project_id", "created_at"),
        Index("ix_experience_records_workspace_created", "workspace_id", "created_at"),
        Index(
            "ix_experience_records_eligibility",
            "workspace_id",
            "visibility",
            "eligible_for_learning",
        ),
        Index("ix_experience_records_configuration_hash", "configuration_hash"),
        Index("ix_experience_records_supersedes", "supersedes_contract_id"),
    )


class FailureEventRow(V04ContractRow):
    """SDD §7.11 failure event. §13: "execution, taxonomy, owner, evidence".

    §13's "evidence" stays in ``payload`` as a list of typed ``EvidenceRef``; a column
    cannot hold a list, and the alternative — a join table — would give evidence
    references a lifetime independent of the event that cited them, which registry §16
    forbids.
    """

    __tablename__ = "failure_events"

    execution_instance_id: Mapped[str] = mapped_column(String(64))
    failure_type: Mapped[str] = mapped_column(String(48))
    assigned_owner: Mapped[str] = mapped_column(String(48))
    retryable: Mapped[bool] = mapped_column(Boolean)

    __table_args__ = (
        UniqueConstraint("content_hash", "schema_version", name="uq_failure_events_hash_version"),
        Index("ix_failure_events_project_created", "project_id", "created_at"),
        Index("ix_failure_events_workspace_created", "workspace_id", "created_at"),
        Index("ix_failure_events_execution", "execution_instance_id", "created_at"),
        Index("ix_failure_events_taxonomy", "failure_type", "assigned_owner"),
        Index("ix_failure_events_supersedes", "supersedes_contract_id"),
    )


class RouterModelVersionRow(V04ContractRow):
    """SDD §7.12 router model version. §13: "scope, artifact digest, lineage, status".

    This table carries **the repository's first two partial unique indexes**, because
    §13.1's third and fourth bullets are conditional rules that no plain
    ``UniqueConstraint`` can express: uniqueness holds only over the rows whose ``status``
    is ``ACTIVE``, and a table that also stores every candidate, shadow, retired and
    rolled-back version would otherwise be unable to store a second candidate at all.

    * ``uq_router_versions_active_workspace`` — "one active workspace router per
      workspace" (§13.1). Scoped to ``scope = 'TEAM_WORKSPACE'`` so that a project
      adapter, which is also ``ACTIVE`` and also belongs to the workspace, does not
      collide with the workspace prior.
    * ``uq_router_versions_active_project_adapter`` — "one active adapter per
      project/router family" (§13.1), keyed on ``(project_id, algorithm_id)``:
      ``algorithm_id`` is what §7.12 calls the router family, and two adapters fitted by
      different algorithms for the same project are a comparison, not a conflict.

    ``postgresql_where`` is a PostgreSQL-only clause; the store also pre-checks each rule
    and raises ``ValueError`` before the insert, so the error a caller sees is the same
    on both backends and ``MemoryStore`` can mirror the rule exactly. The index is the
    backstop against a concurrent second writer, not the first line of defence.

    ``status`` is a promoted column on an append-only table, which reads like a
    contradiction and is not: a version is never updated in place, so retiring one and
    activating the next is two new rows whose ``parent_version_id`` chains them. That is
    also why §10.3's rollback works — the retired row is still there, still readable,
    still the artifact digest it always was.
    """

    __tablename__ = "router_model_versions"

    scope: Mapped[str] = mapped_column(String(32))
    algorithm_id: Mapped[str] = mapped_column(String(128))
    feature_schema_version: Mapped[str] = mapped_column(String(32))
    training_snapshot_id: Mapped[str] = mapped_column(String(64))
    artifact_digest: Mapped[str] = mapped_column(String(64))
    parent_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint("content_hash", "schema_version", name="uq_router_versions_hash_version"),
        Index(
            "uq_router_versions_active_workspace",
            "workspace_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE' AND scope = 'TEAM_WORKSPACE'"),
        ),
        Index(
            "uq_router_versions_active_project_adapter",
            "project_id",
            "algorithm_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE' AND scope = 'PROJECT_ADAPTER'"),
        ),
        Index("ix_router_versions_project_created", "project_id", "created_at"),
        Index("ix_router_versions_workspace_created", "workspace_id", "created_at"),
        Index("ix_router_versions_snapshot", "training_snapshot_id"),
        Index("ix_router_versions_parent", "parent_version_id"),
        Index("ix_router_versions_supersedes", "supersedes_contract_id"),
    )


class RouterTrainingSnapshotRow(V04ContractRow):
    """SDD §7.14 router training snapshot. §13: "included experience manifest and split
    definition".

    Both of §13's key fields are lists of ids — up to a hundred thousand experience ids,
    and three project-id lists — and both stay in ``payload``. Promoting the manifest to
    a join table would let a snapshot's membership be edited row by row after the fact,
    and a training snapshot whose contents can change is not a snapshot; it is the exact
    failure §10.1 is written to prevent. The window bounds are promoted instead, because
    "which snapshots covered this period" is the only question asked of the manifest from
    outside the snapshot itself.
    """

    __tablename__ = "router_training_snapshots"

    feature_schema_version: Mapped[str] = mapped_column(String(32))
    contract_schema_version: Mapped[str] = mapped_column(String(32))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "content_hash", "schema_version", name="uq_router_snapshots_hash_version"
        ),
        Index("ix_router_snapshots_project_created", "project_id", "created_at"),
        Index("ix_router_snapshots_workspace_created", "workspace_id", "created_at"),
        Index("ix_router_snapshots_window", "window_start", "window_end"),
        Index("ix_router_snapshots_supersedes", "supersedes_contract_id"),
    )


class RouterPromotionReportRow(V04ContractRow):
    """SDD §7.13 router promotion report. §13: "candidate/baseline metrics, cohorts,
    decision".

    §13.1's fifth bullet — "promotion reports are append-only" — is not implemented as a
    trigger or a check constraint. It is implemented by there being **no update and no
    delete method for this table anywhere in the store**, on either backend, and by a
    test that asserts the absence rather than trusting it. A rejected promotion that could
    later be edited into an approval is the single most valuable row in this schema to an
    attacker, and the cheapest way to make that impossible is to write no code that could
    do it.
    """

    __tablename__ = "router_promotion_reports"

    candidate_version: Mapped[str] = mapped_column(String(64))
    baseline_version: Mapped[str] = mapped_column(String(64))
    training_snapshot_id: Mapped[str] = mapped_column(String(64))
    holdout_definition_id: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(32))
    rollback_target: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint(
            "content_hash", "schema_version", name="uq_router_promotions_hash_version"
        ),
        Index("ix_router_promotions_project_created", "project_id", "created_at"),
        Index("ix_router_promotions_workspace_created", "workspace_id", "created_at"),
        Index("ix_router_promotions_candidate", "candidate_version", "created_at"),
        Index("ix_router_promotions_decision", "decision", "created_at"),
        Index("ix_router_promotions_supersedes", "supersedes_contract_id"),
    )


class ShadowDecisionRow(V04ContractRow):
    """SDD §7.15 shadow decision. §13: "executed receipt, shadow receipt, comparison".

    Neither receipt id is a foreign key to ``routing_receipts``. A shadow receipt is
    produced by a model that is not serving traffic and §10.2 explicitly forbids it from
    affecting execution; making this row unwritable until both receipts had been persisted
    would let the shadow pipeline's write ordering block the executed path, which is the
    one thing shadow evaluation must never do. The ids are indexed instead, and the
    comparison is what the row is for.
    """

    __tablename__ = "shadow_decisions"

    executed_receipt_id: Mapped[str] = mapped_column(String(64))
    shadow_receipt_id: Mapped[str] = mapped_column(String(64))
    shadow_router_version_id: Mapped[str] = mapped_column(String(64))
    agreement: Mapped[bool] = mapped_column(Boolean)

    __table_args__ = (
        UniqueConstraint(
            "content_hash", "schema_version", name="uq_shadow_decisions_hash_version"
        ),
        Index("ix_shadow_decisions_project_created", "project_id", "created_at"),
        Index("ix_shadow_decisions_workspace_created", "workspace_id", "created_at"),
        Index("ix_shadow_decisions_executed", "executed_receipt_id"),
        Index("ix_shadow_decisions_router_version", "shadow_router_version_id", "created_at"),
        Index("ix_shadow_decisions_supersedes", "supersedes_contract_id"),
    )


class ShadowRolloutResultRow(V04ContractRow):
    """ADR-060 shadow rollout result. Added by the freeze delta, created by migration 0018.

    SDD §13 names no table for this record because M0's §13 table predates the decision to
    score shadow choices by *branching the live run* rather than by replaying it. The key
    fields follow from the query M6.2's report is: "every rollout of this shadow decision,
    oldest first", which is the paired lookup that turns two rows into one measurement.

    ``shadow_decision_id`` is not a foreign key into ``shadow_decisions``, for exactly the
    reason ``ShadowDecisionRow`` gives for its two receipt ids: a rollout is produced by a
    fork that is forbidden from affecting execution, and a key that made this row
    unwritable until the decision had been persisted would let the shadow pipeline's write
    ordering block the executed path.

    ``kind`` is promoted beside it because the pair is the unit of evidence and "the
    ``SHADOW`` arm of decision X" is a two-column lookup, not a payload scan.
    ``configuration_hash`` is promoted because the report groups paired deltas by the
    configuration that was actually executed, and ``completed_at`` because the composite
    index below is what makes the ordering of a pair deterministic without deserialising
    either row.
    """

    __tablename__ = "shadow_rollout_results"

    shadow_decision_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    configuration_hash: Mapped[str] = mapped_column(String(64))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "content_hash", "schema_version", name="uq_shadow_rollouts_hash_version"
        ),
        Index("ix_shadow_rollouts_decision", "shadow_decision_id", "completed_at"),
        Index("ix_shadow_rollouts_workspace_created", "workspace_id", "created_at"),
        Index("ix_shadow_rollouts_project_created", "project_id", "created_at"),
        Index("ix_shadow_rollouts_supersedes", "supersedes_contract_id"),
    )


class RouterActivationRow(V04ContractRow):
    """ADR-061 router activation ledger. Added by the freeze delta, created by 0018.

    The table that replaces a mutable ``status`` column with a sequence. §13.1's "one
    active workspace router per workspace" is expressed here as
    ``uq_router_activations_sequence`` over ``(workspace_id, scope, family_key, sequence)``:
    a plain :class:`~sqlalchemy.UniqueConstraint` and *not* a third partial unique index,
    because the rule is now unconditional. Uniqueness over a contiguous sequence is what
    makes "the active version" the single row with the greatest ``sequence`` in its
    partition, and two writers racing to append the same next number is the one collision
    that must be impossible rather than merely unlikely.

    **The two M0 partial indexes on ``router_model_versions`` are deliberately untouched
    here.** M8.1 owns retiring them, in migration 0019, together with the composite
    ``activate_router_version`` that writes the version rows and the ledger row in one
    transaction. Between 0018 and 0019 a database satisfies both rules at once, which is
    the only ordering under which each migration is independently reversible.

    Every id column is a plain ``String(64)`` and none is a foreign key into
    ``router_model_versions`` or ``router_promotion_reports``. A rollback happens during an
    incident, and an activation that could not be recorded because the row it points at was
    written by a transaction that has not committed yet is an activation that fails when it
    is needed most.
    """

    __tablename__ = "router_activations"

    scope: Mapped[str] = mapped_column(String(32))
    family_key: Mapped[str] = mapped_column(String(128))
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    router_version_id: Mapped[str] = mapped_column(String(64))
    previous_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rollback_target_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    promotion_report_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "content_hash", "schema_version", name="uq_router_activations_hash_version"
        ),
        UniqueConstraint(
            "workspace_id",
            "scope",
            "family_key",
            "sequence",
            name="uq_router_activations_sequence",
        ),
        Index("ix_router_activations_workspace_created", "workspace_id", "created_at"),
        Index("ix_router_activations_project_created", "project_id", "created_at"),
        Index("ix_router_activations_version", "router_version_id", "created_at"),
        Index("ix_router_activations_supersedes", "supersedes_contract_id"),
    )


V04_M0_ROUTING_TABLES: tuple[str, ...] = (
    "objective_contracts",
    "node_contracts",
    "verification_specs",
    "routing_requests",
    "configuration_candidates",
    "compatibility_decisions",
    "routing_receipts",
    "routing_overrides",
    "verification_results",
    "experience_records",
    "failure_events",
    "router_model_versions",
    "router_training_snapshots",
    "router_promotion_reports",
    "shadow_decisions",
    # The freeze delta of 5 Sep 2026 (ADR-060, ADR-061) appended these two. They are
    # created by migration **0018** and not by 0017, which pins its own creation list to
    # everything above this comment through `V04_FREEZE_DELTA_TABLES` — a migration already
    # applied in the field cannot start creating tables it did not create the first time.
    "shadow_rollout_results",
    "router_activations",
)
"""The seventeen v0.4 routing tables, in creation order (ADR-058, ADR-060, ADR-061).

Declared here rather than only in the migrations so that one list is read by both of them,
by the store and by the tests. The order is a dependency order — nothing in it references
anything later in it — which is what makes ``reversed()`` a valid drop order and leaves
each ``downgrade`` nothing further to reason about.

The constant keeps its ``M0`` name on purpose. Every parity proof in the suite is written
against it — the ``MemoryStore`` bucket set, the derived ``put_``/``get_``/``list_`` method
names, the header-column and no-``updated_at`` checks — and renaming it would have made a
two-table addition touch every one of those call sites, which is exactly the kind of diff
that hides a real change. The fifteen it originally named are the fifteen 0017 creates, and
:data:`V04_FREEZE_DELTA_TABLES` is what says which two are not among them.
"""

V04_FREEZE_DELTA_TABLES: tuple[str, ...] = (
    "shadow_rollout_results",
    "router_activations",
)
"""The two tables migration 0018 creates, subtracted from 0017's list (ADR-060, ADR-061).

Read by ``0017_v04_m0_routing_contracts`` to pin its creation list to the fifteen it has
always created, and by ``0018_v04_freeze_delta_shadow_rollouts_router_activations`` as the
list it creates. Without it the two migrations would both read the same seventeen names,
0017 would silently start creating tables that did not exist when it was written, and a
database migrated to 0017 would no longer be reproducible from the revision it records.
"""
