"""Scoped, attributable current simulation policy and conformance inventory.

Revision ID: 0023_v05_authority_inventory
Revises: 0022_v05_runtime_authority
"""

from collections.abc import Sequence

from alembic import op

from accretion.persistence.models import V05_AUTHORITY_INVENTORY_TABLES, Base

revision: str = "0023_v05_authority_inventory"
down_revision: str | None = "0022_v05_runtime_authority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in V05_AUTHORITY_INVENTORY_TABLES:
        Base.metadata.tables[name].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    for name in reversed(V05_AUTHORITY_INVENTORY_TABLES):
        Base.metadata.tables[name].drop(bind=op.get_bind())
