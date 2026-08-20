"""P2 loop execution and verifier evidence.

Revision ID: 0003_p2_loops_verifiers
Revises: 0002_p1_planning
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from accretion.persistence.models import Base

revision: str = "0003_p2_loops_verifiers"
down_revision: str | None = "0002_p1_planning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

P2_TABLES = (
    "acceptance_policies",
    "loop_executions",
    "loop_iterations",
    "verifications",
)
RUN_REFERENCE_COLUMNS = {
    "strategy_decision_id": sa.String(length=40),
    "execution_mode": sa.String(length=32),
    "workflow_template_id": sa.String(length=64),
    "acceptance_policy_id": sa.String(length=40),
    "loop_execution_id": sa.String(length=40),
}


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("runs")}
    for name, type_ in RUN_REFERENCE_COLUMNS.items():
        if name not in columns:
            op.add_column("runs", sa.Column(name, type_, nullable=True))
    for name in P2_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(P2_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
    columns = {column["name"] for column in sa.inspect(bind).get_columns("runs")}
    for name in reversed(RUN_REFERENCE_COLUMNS):
        if name in columns:
            op.drop_column("runs", name)
