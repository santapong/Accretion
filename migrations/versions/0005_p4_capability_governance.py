"""P4 governed capability registry, policies, plugins, and requests.

Revision ID: 0005_p4_capability_governance
Revises: 0004_p3_graphs_checkpoints
Create Date: 2026-08-22
"""

from collections.abc import Sequence

from alembic import op

from accretion.persistence.models import Base

revision: str = "0005_p4_capability_governance"
down_revision: str | None = "0004_p3_graphs_checkpoints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

P4_CAPABILITY_TABLES = (
    "capabilities",
    "skills",
    "plugins",
    "policies",
    "capability_requests",
)


def upgrade() -> None:
    bind = op.get_bind()
    for name in P4_CAPABILITY_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(P4_CAPABILITY_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
