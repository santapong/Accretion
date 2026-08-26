"""v0.3 M5 research evidence store.

Revision ID: 0015_v03_m5_research_evidence
Revises: 0014_v03_m4_plugin_manager
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from accretion.persistence.models import Base

revision: str = "0015_v03_m5_research_evidence"
down_revision: str | None = "0014_v03_m4_plugin_manager"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

M5_TABLES = ("research_evidence",)

# Tables are created from ``Base.metadata``, so a database built from scratch
# already gets ``capability_requests.provenance`` back at 0005. Only a database
# that ran 0005 before this column existed needs the ALTER, so both directions
# are conditional and the up/down/up cycle is idempotent either way.
PROVENANCE_TABLE = "capability_requests"
PROVENANCE_COLUMN = "provenance"


def _has_provenance_column(bind: object) -> bool:
    inspector = sa.inspect(bind)
    return any(
        column["name"] == PROVENANCE_COLUMN
        for column in inspector.get_columns(PROVENANCE_TABLE)
    )


def upgrade() -> None:
    bind = op.get_bind()
    for name in M5_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)
    # Additive and nullable: existing capability results carry no provenance and
    # must keep validating, so the column cannot be made NOT NULL.
    if not _has_provenance_column(bind):
        op.add_column(PROVENANCE_TABLE, sa.Column(PROVENANCE_COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_provenance_column(bind):
        op.drop_column(PROVENANCE_TABLE, PROVENANCE_COLUMN)
    for name in reversed(M5_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
