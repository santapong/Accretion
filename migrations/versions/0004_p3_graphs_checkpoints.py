"""P3 workflow templates, run graphs, checkpoints, and approvals.

Revision ID: 0004_p3_graphs_checkpoints
Revises: 0003_p2_loops_verifiers
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from accretion.persistence.models import Base

revision: str = "0004_p3_graphs_checkpoints"
down_revision: str | None = "0003_p2_loops_verifiers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

P3_TABLES = (
    "workflow_templates",
    "run_graphs",
    "run_graph_nodes",
    "run_graph_edges",
)


def _columns(bind: sa.engine.Connection, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _unique_constraints(bind: sa.engine.Connection, table: str) -> dict[str, list[str]]:
    return {
        constraint["name"]: list(constraint["column_names"])
        for constraint in sa.inspect(bind).get_unique_constraints(table)
        if constraint.get("name")
    }


def _indexes(bind: sa.engine.Connection, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(bind).get_indexes(table) if index.get("name")}


def upgrade() -> None:
    bind = op.get_bind()

    # P3 node ids (run_id + ":" + key) exceed the original 40-character column.
    op.alter_column(
        "agent_events",
        "node_id",
        existing_type=sa.String(length=40),
        type_=sa.String(length=96),
        existing_nullable=True,
    )

    for name in P3_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)

    loop_columns = _columns(bind, "loop_executions")
    if "node_key" not in loop_columns:
        op.add_column(
            "loop_executions", sa.Column("node_key", sa.String(length=64), nullable=True)
        )
    if "attempt" not in loop_columns:
        op.add_column("loop_executions", sa.Column("attempt", sa.Integer(), nullable=True))
    # Every pre-P3 row is the feedback-loop-v1 whole-run loop whose LOOP node
    # key is "evaluate"; backfill before NOT NULL so Postgres uniqueness cannot
    # be voided by NULLs.
    op.execute(
        "UPDATE loop_executions SET node_key = 'evaluate' WHERE node_key IS NULL"
    )
    op.execute("UPDATE loop_executions SET attempt = 1 WHERE attempt IS NULL")
    op.alter_column(
        "loop_executions", "node_key", existing_type=sa.String(length=64), nullable=False
    )
    op.alter_column("loop_executions", "attempt", existing_type=sa.Integer(), nullable=False)
    for name, columns in _unique_constraints(bind, "loop_executions").items():
        if columns == ["run_id"]:
            op.drop_constraint(name, "loop_executions", type_="unique")
    if (
        "uq_loop_executions_run_node_attempt"
        not in _unique_constraints(bind, "loop_executions")
    ):
        op.create_unique_constraint(
            "uq_loop_executions_run_node_attempt",
            "loop_executions",
            ["run_id", "node_key", "attempt"],
        )

    if "uq_checkpoints_run_sequence" not in _unique_constraints(bind, "checkpoints"):
        op.create_unique_constraint(
            "uq_checkpoints_run_sequence", "checkpoints", ["run_id", "sequence"]
        )
    if "ix_checkpoints_run_sequence" not in _indexes(bind, "checkpoints"):
        op.create_index(
            "ix_checkpoints_run_sequence", "checkpoints", ["run_id", "sequence"]
        )

    approval_columns = _columns(bind, "approvals")
    if "node_id" not in approval_columns:
        op.add_column("approvals", sa.Column("node_id", sa.String(length=96), nullable=True))
    if "summary" not in approval_columns:
        op.add_column(
            "approvals",
            sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        )
    if "uq_approvals_run_native" not in _unique_constraints(bind, "approvals"):
        op.create_unique_constraint(
            "uq_approvals_run_native", "approvals", ["run_id", "native_request_id"]
        )

    if "budget_spent" not in _columns(bind, "runs"):
        op.add_column("runs", sa.Column("budget_spent", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()

    if "budget_spent" in _columns(bind, "runs"):
        op.drop_column("runs", "budget_spent")

    if "uq_approvals_run_native" in _unique_constraints(bind, "approvals"):
        op.drop_constraint("uq_approvals_run_native", "approvals", type_="unique")
    approval_columns = _columns(bind, "approvals")
    if "summary" in approval_columns:
        op.drop_column("approvals", "summary")
    if "node_id" in approval_columns:
        op.drop_column("approvals", "node_id")

    if "ix_checkpoints_run_sequence" in _indexes(bind, "checkpoints"):
        op.drop_index("ix_checkpoints_run_sequence", table_name="checkpoints")
    if "uq_checkpoints_run_sequence" in _unique_constraints(bind, "checkpoints"):
        op.drop_constraint("uq_checkpoints_run_sequence", "checkpoints", type_="unique")

    multi_loop_runs = bind.execute(
        sa.text(
            "SELECT run_id FROM loop_executions GROUP BY run_id HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if multi_loop_runs is not None:
        raise RuntimeError(
            "cannot downgrade: at least one run owns multiple loop executions; "
            "the pre-P3 schema cannot represent this history"
        )
    if (
        "uq_loop_executions_run_node_attempt"
        in _unique_constraints(bind, "loop_executions")
    ):
        op.drop_constraint(
            "uq_loop_executions_run_node_attempt", "loop_executions", type_="unique"
        )
    loop_columns = _columns(bind, "loop_executions")
    if "attempt" in loop_columns:
        op.drop_column("loop_executions", "attempt")
    if "node_key" in loop_columns:
        op.drop_column("loop_executions", "node_key")
    if not any(
        columns == ["run_id"]
        for columns in _unique_constraints(bind, "loop_executions").values()
    ):
        op.create_unique_constraint(
            "uq_loop_executions_run_id", "loop_executions", ["run_id"]
        )

    for name in reversed(P3_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)

    overlong = bind.execute(
        sa.text("SELECT id FROM agent_events WHERE LENGTH(node_id) > 40 LIMIT 1")
    ).first()
    if overlong is not None:
        raise RuntimeError(
            "cannot downgrade: agent_events.node_id contains values longer than "
            "40 characters"
        )
    op.alter_column(
        "agent_events",
        "node_id",
        existing_type=sa.String(length=96),
        type_=sa.String(length=40),
        existing_nullable=True,
    )
