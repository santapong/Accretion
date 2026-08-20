from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
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
        UniqueConstraint(
            "loop_execution_id", "number", name="uq_loop_iterations_execution_number"
        ),
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

    __table_args__ = (
        UniqueConstraint("run_graph_id", "key", name="uq_run_graph_edges_graph_key"),
    )
