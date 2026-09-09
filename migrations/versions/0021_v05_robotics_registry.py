"""Add immutable simulation registry, explicit scope binding and transactional outbox.

Revision ID: 0021_v05_robotics_registry
Revises: 0019_v04_m8_activation
"""

from collections.abc import Sequence

from alembic import op

from accretion.persistence.models import V05_REGISTRY_TABLES, Base

revision: str = "0021_v05_robotics_registry"
down_revision: str | None = "0019_v04_m8_activation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in V05_REGISTRY_TABLES:
        Base.metadata.tables[name].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    for name in reversed(V05_REGISTRY_TABLES):
        Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
