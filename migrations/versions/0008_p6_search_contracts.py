"""P6 candidate-search contracts and durable evidence.

Revision ID: 0008_p6_search_contracts
Revises: 0007_p5_dynamic_workflows
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op

from accretion.persistence.models import Base

revision: str = "0008_p6_search_contracts"
down_revision: str | None = "0007_p5_dynamic_workflows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

P6_TABLES = (
    "search_plans",
    "search_candidates",
    "candidate_scores",
    "search_promotions",
)


def upgrade() -> None:
    bind = op.get_bind()
    for name in P6_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(P6_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
