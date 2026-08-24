"""v0.3 M0 connector, connection, and capability-binding contracts.

Revision ID: 0010_v03_m0_connection_contracts
Revises: 0009_p7_experience_contracts
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op

from accretion.persistence.models import Base

revision: str = "0010_v03_m0_connection_contracts"
down_revision: str | None = "0009_p7_experience_contracts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

M0_TABLES = (
    "connector_definitions",
    "connections",
    "capability_bindings",
)


def upgrade() -> None:
    bind = op.get_bind()
    for name in M0_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(M0_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
