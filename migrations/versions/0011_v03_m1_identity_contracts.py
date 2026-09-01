"""v0.3 M1 principal, workspace, and auth-session contracts.

Revision ID: 0011_v03_m1_identity_contracts
Revises: 0010_v03_m0_connection_contracts
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op

from accretion.persistence.models import Base

revision: str = "0011_v03_m1_identity_contracts"
down_revision: str | None = "0010_v03_m0_connection_contracts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

M1_TABLES = (
    "principals",
    "workspaces",
    "workspace_memberships",
    "auth_sessions",
    "auth_transactions",
)


def upgrade() -> None:
    bind = op.get_bind()
    for name in M1_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(M1_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
