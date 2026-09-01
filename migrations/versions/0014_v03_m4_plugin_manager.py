"""v0.3 M4 plugin version registry, workspace installations, and audit trail.

Revision ID: 0014_v03_m4_plugin_manager
Revises: 0013_v03_m3_remote_mcp
Create Date: 2026-08-26
"""

from collections.abc import Sequence

from alembic import op

from accretion.persistence.models import Base

revision: str = "0014_v03_m4_plugin_manager"
down_revision: str | None = "0013_v03_m3_remote_mcp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

M4_TABLES = (
    "plugin_versions",
    "plugin_installations",
    "plugin_audit_events",
)


def upgrade() -> None:
    bind = op.get_bind()
    for name in M4_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(M4_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
