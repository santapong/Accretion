"""v0.3 M3 remote MCP lifecycle, discovery cache, and audit contracts.

Revision ID: 0013_v03_m3_remote_mcp
Revises: 0012_v03_m2_token_broker
Create Date: 2026-08-26
"""

from collections.abc import Sequence

from alembic import op

from accretion.persistence.models import Base

revision: str = "0013_v03_m3_remote_mcp"
down_revision: str | None = "0012_v03_m2_token_broker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

M3_TABLES = (
    "mcp_servers",
    "mcp_server_discovery_snapshots",
    "mcp_server_events",
)


def upgrade() -> None:
    bind = op.get_bind()
    for name in M3_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(M3_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
