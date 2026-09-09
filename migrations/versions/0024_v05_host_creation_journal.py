"""Durable host creation identity and cleanup-only restart journal.

Revision ID: 0024_v05_host_creation_journal
Revises: 0023_v05_authority_inventory
"""

from collections.abc import Sequence

from alembic import op

from accretion.persistence.models import V05_HOST_JOURNAL_TABLES, Base

revision: str = "0024_v05_host_creation_journal"
down_revision: str | None = "0023_v05_authority_inventory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in V05_HOST_JOURNAL_TABLES:
        Base.metadata.tables[name].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    for name in reversed(V05_HOST_JOURNAL_TABLES):
        Base.metadata.tables[name].drop(bind=op.get_bind())
