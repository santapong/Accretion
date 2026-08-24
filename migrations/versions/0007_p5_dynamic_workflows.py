"""P5 validated dynamic workflow proposals and immutable revisions.

Revision ID: 0007_p5_dynamic_workflows
Revises: 0006_p4_benchmark_research
Create Date: 2026-08-23
"""

from collections.abc import Sequence

from alembic import op

from accretion.persistence.models import Base

revision: str = "0007_p5_dynamic_workflows"
down_revision: str | None = "0006_p4_benchmark_research"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

P5_TABLES = (
    "project_feature_settings",
    "workflow_proposals",
    "graph_validation_results",
    "run_graph_revisions",
    "replan_requests",
    "runtime_decisions",
)


def upgrade() -> None:
    bind = op.get_bind()
    for name in P5_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(P5_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
