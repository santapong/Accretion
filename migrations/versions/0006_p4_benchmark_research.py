"""P4 benchmark, project version, and research evidence records.

Revision ID: 0006_p4_benchmark_research
Revises: 0005_p4_capability_governance
Create Date: 2026-08-22
"""

from collections.abc import Sequence

from alembic import op

from accretion.persistence.models import Base

revision: str = "0006_p4_benchmark_research"
down_revision: str | None = "0005_p4_capability_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

P4_BENCHMARK_TABLES = (
    "project_versions",
    "evidence",
    "claims",
    "theories",
    "hypotheses",
    "experiments",
    "experiment_runs",
    "results",
    "decisions",
    "benchmark_tasks",
    "benchmark_runs",
    "architecture_metrics",
)


def upgrade() -> None:
    bind = op.get_bind()
    for name in P4_BENCHMARK_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(P4_BENCHMARK_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
