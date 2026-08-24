"""P0 authoritative state and append-only events.

Revision ID: 0001_p0_foundation
Revises: None
Create Date: 2026-08-20
"""

from collections.abc import Sequence

from alembic import op

from accretion.persistence.models import Base

revision: str = "0001_p0_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # This legacy bootstrap revision creates current metadata on a fresh database.
    # Install shared types needed by later metadata before that create_all call;
    # the owning P7 revision repeats this idempotently for existing databases.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
