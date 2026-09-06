"""v0.4 M8.1: retire the two partial unique indexes the activation ledger replaces.

Drops ``uq_router_versions_active_workspace`` and
``uq_router_versions_active_project_adapter`` from ``router_model_versions``, and nothing
else. No table is created, dropped, renamed or backfilled; no column changes type or
nullability; no row is written or deleted.

**Why they go.** 0017 read SDD §13.1's third and fourth bullets — "one active workspace
router per workspace", "one active adapter per project/router family" — as conditional
uniqueness over ``status = 'ACTIVE'``. The rules are right and the implementation of them
is unreachable: the v0.4 contract family has no ``update_`` method on any table, by design,
so the first ``ACTIVE`` row can never be retired and, with the index in place, a second can
never be inserted. A workspace could be activated exactly once, forever, and §10.3's
"atomic and reversible" promotion had nowhere to happen. 0018 added
``router_activations`` (ADR-061), whose ``uq_router_activations_sequence`` over
``(workspace_id, scope, family_key, sequence)`` states the same requirement
unconditionally: "active" is the head of an append-only sequence rather than a value in a
column, and several ``ACTIVE`` rows in one workspace are the ordinary record of a promotion
followed by a rollback rather than a violation.

**Why it is a separate revision from 0018.** A database at 0018 satisfies the old rule and
the new one at once. That is the only ordering under which each migration is independently
reversible: 0018's downgrade has nothing to restore beyond the two tables it created, and
this revision's downgrade restores exactly the two indexes it dropped.

**Reversible, and exactly so.** ``downgrade`` recreates both indexes with the columns, the
uniqueness and the ``postgresql_where`` clause 0017 gave them, spelled out here rather than
read from ``Base.metadata``: the model no longer declares them, so metadata is no longer a
record of what a pre-0019 database looked like. Both directions are guarded by an
inspection of the live schema, so this revision states an *end state* rather than a diff
and is correct from either starting point — the same technique 0002, 0003, 0004, 0015 and
0020 use, and what makes CI's ``upgrade head; downgrade base; upgrade head`` cycle pass on a
database built from the current models (which never created the indexes at 0017) as well as
on one that was upgraded through 0017 before this change shipped.

Nothing is dropped that holds the only copy of anything: an index holds no data, and the
``status`` column it filtered on is untouched.

Revision ID: 0019_v04_m8_activation
Revises: 0020_v04_experience_fk
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_v04_m8_activation"
down_revision: str | None = "0020_v04_experience_fk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "router_model_versions"

# name -> (columns, the partial predicate 0017 gave it). Dropped in this order on the way
# up and recreated in ``reversed()`` order on the way down, matching the convention 0013
# and 0014 set for their table tuples.
M8_PARTIAL_INDEXES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "uq_router_versions_active_workspace",
        ("workspace_id",),
        "status = 'ACTIVE' AND scope = 'TEAM_WORKSPACE'",
    ),
    (
        "uq_router_versions_active_project_adapter",
        ("project_id", "algorithm_id"),
        "status = 'ACTIVE' AND scope = 'PROJECT_ADAPTER'",
    ),
)


def _index_names(bind: sa.engine.Connection) -> set[str]:
    return {
        str(index["name"])
        for index in sa.inspect(bind).get_indexes(TABLE)
        if index.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    present = _index_names(bind)
    for name, _columns, _where in M8_PARTIAL_INDEXES:
        if name in present:
            op.drop_index(name, table_name=TABLE)


def downgrade() -> None:
    bind = op.get_bind()
    present = _index_names(bind)
    for name, columns, where in reversed(M8_PARTIAL_INDEXES):
        if name not in present:
            op.create_index(
                name,
                TABLE,
                list(columns),
                unique=True,
                postgresql_where=sa.text(where),
            )
