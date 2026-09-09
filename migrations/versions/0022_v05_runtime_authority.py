"""Durable simulator authority and preserved run attribution.

Revision ID: 0022_v05_runtime_authority
Revises: 0021_v05_robotics_registry
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from accretion.persistence.models import V05_AUTHORITY_TABLES, Base

revision: str = "0022_v05_runtime_authority"
down_revision: str | None = "0021_v05_robotics_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing unknown owners remain null. Never infer ownership from a provider.
    if "principal_id" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("runs")}:
        op.add_column("runs", sa.Column("principal_id", sa.String(255), nullable=True))
        op.create_foreign_key(
            "fk_runs_principal",
            "runs",
            "principals",
            ["principal_id"],
            ["principal_id"],
            ondelete="RESTRICT",
        )
    for name in V05_AUTHORITY_TABLES:
        Base.metadata.tables[name].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    for name in reversed(V05_AUTHORITY_TABLES):
        Base.metadata.tables[name].drop(bind=op.get_bind())
    op.drop_constraint("fk_runs_principal", "runs", type_="foreignkey")
    op.drop_column("runs", "principal_id")
