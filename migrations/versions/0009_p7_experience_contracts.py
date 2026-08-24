"""P7 verified-experience contracts and exact vector storage.

Revision ID: 0009_p7_experience_contracts
Revises: 0008_p6_search_contracts
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op

from accretion.persistence.models import Base

revision: str = "0009_p7_experience_contracts"
down_revision: str | None = "0008_p6_search_contracts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

P7_TABLES = (
    "experiences",
    "trajectory_segments",
    "experience_embeddings",
    "experience_queries",
    "experience_matches",
    "experience_selections",
    "experience_moderation_actions",
    "trajectory_replay_seeds",
)


def upgrade() -> None:
    # pgvector defaults to exact nearest-neighbor search. P7 intentionally creates
    # no HNSW or IVFFlat index so deterministic evidence gates retain perfect recall.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    bind = op.get_bind()
    for name in P7_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(P7_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
    # The shared extension may be used by other schemas. Downgrade removes only
    # Accretion-owned tables and deliberately leaves the extension installed.
