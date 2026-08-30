"""v0.3 M7 enterprise-managed authorization.

Revision ID: 0016_v03_m7_enterprise_auth
Revises: 0015_v03_m5_research_evidence
Create Date: 2026-08-29
"""

from collections.abc import Sequence

from alembic import op

from accretion.persistence.models import Base

revision: str = "0016_v03_m7_enterprise_auth"
down_revision: str | None = "0015_v03_m5_research_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Additive tables only. No existing persisted model gains a column, so a database
# at 0015 keeps validating and the down direction destroys nothing that is the
# only copy of anything: the sealed assertions live in ``secret_records``.
M7_TABLES = ("identity_assertions", "enterprise_auth_grants")


def upgrade() -> None:
    bind = op.get_bind()
    for name in M7_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(M7_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
