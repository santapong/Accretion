"""v0.3 M2 token handle, secret record, and OAuth transaction contracts.

Revision ID: 0012_v03_m2_token_broker
Revises: 0011_v03_m1_identity_contracts
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op

from accretion.persistence.models import Base

revision: str = "0012_v03_m2_token_broker"
down_revision: str | None = "0011_v03_m1_identity_contracts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

M2_TABLES = (
    "oauth_transactions",
    "token_handles",
    "secret_records",
)


def upgrade() -> None:
    bind = op.get_bind()
    for name in M2_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(M2_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
