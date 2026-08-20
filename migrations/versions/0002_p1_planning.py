"""P1 immutable prompt, context, profile, decision, and override history.

Revision ID: 0002_p1_planning
Revises: 0001_p0_foundation
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from accretion.persistence.models import Base

revision: str = "0002_p1_planning"
down_revision: str | None = "0001_p0_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLANNING_TABLES = (
    "prompt_contracts",
    "context_bundles",
    "task_profiles",
    "strategy_decisions",
    "strategy_overrides",
)
TASK_REFERENCE_COLUMNS = (
    "prompt_contract_id",
    "context_bundle_id",
    "current_profile_id",
    "current_strategy_decision_id",
)


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("tasks")}
    for name in TASK_REFERENCE_COLUMNS:
        if name not in columns:
            op.add_column("tasks", sa.Column(name, sa.String(length=40), nullable=True))
    for name in PLANNING_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(PLANNING_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
    columns = {column["name"] for column in sa.inspect(bind).get_columns("tasks")}
    for name in reversed(TASK_REFERENCE_COLUMNS):
        if name in columns:
            op.drop_column("tasks", name)
