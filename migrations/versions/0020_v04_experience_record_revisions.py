"""v0.4 M3a: give ``experience_records`` a separate ``experience_id`` foreign key.

Moves the key into ``experiences`` off the primary key and onto a column of its own, so
that the revisions SDD §7.10 and §9.6 describe can be stored at all. Nothing else in the
schema is touched: no other table gains, loses or renames a column, and no table is
created or dropped.

**Why the primary key could not stay the foreign key.** 0017 declared
``experience_records.id`` as both, on the reading that a projection and the experience it
projects share one identity (ADR-054 b). That reading is right about the first projection
of an experience and wrong about every one after it. §7.10 revises a record — a recomputed
``attribution`` (§9.6), a contradiction moving ``OPEN`` → ``RESOLVED``, a
``final_run_status`` that only exists once the run has finished — by writing a **new row**
with its own derived ``contract_id`` and a ``supersedes_contract_id`` naming the row it
replaces, because registry §17 forbids rewriting a historical record in place. Such a row's
id is a fresh ``exp_`` id that names no ``experiences`` row, so the 0017 key refused
precisely the records the frozen contract was designed to produce. After this migration
every revision of one experience carries the same ``experience_id`` and is told apart by
its own ``id``.

**The four steps of the upgrade**, in the only order that is safe on a populated table:

1. ``experience_id`` is added **nullable**, because ``ADD COLUMN ... NOT NULL`` without a
   default is rejected outright by PostgreSQL on a table that already has rows.
2. It is backfilled with ``id``. Every row that exists at 0018 *is* a root projection —
   there was no other kind that could be stored — so ``experience_id = id`` is not a guess
   about the data, it is the identity the old schema enforced, restated in the new column.
   No release has shipped a row here (M3a is the first milestone that writes one), so in
   practice the statement updates nothing; it is written correctly anyway because a
   migration that is only correct on an empty database is a migration nobody can trust the
   day it meets a database that is not.
3. Only then is the column made ``NOT NULL``, and only then is the old key dropped and the
   new one and its index created. The reference therefore never disappears from the schema
   for longer than this transaction: the row's parent is spelled twice for the length of a
   statement and never zero times.
4. Nothing is dropped that holds the only copy of anything. ``id`` keeps every value it
   had; it merely stops being a foreign key.

**Reversible, and it refuses when reversing would lose history.** ``downgrade`` restores
the key on ``id`` and drops ``experience_id`` — but only after checking that no row has
``experience_id <> id``. A row that fails that check is a revision, and a revision's parent
is recorded *nowhere else*: dropping the column would discard which experience it projects
and the restored key on ``id`` would then refuse the row anyway. Refusing loudly with a
count of the offending rows is the only honest option, and it is why this downgrade raises
instead of deleting. On a database at 0020 that has written no revision — every database
the CI ``upgrade head; downgrade base; upgrade head`` cycle sees — the check passes and the
reversal is exact.

**Every step is guarded by an inspection of the live schema.** ``experience_records`` is
created by 0017 from ``Base.metadata``, which now carries the new column, so on a database
built from scratch the table arrives at 0017 already shaped the way this revision would
shape it. Adding the column unconditionally would then fail with a duplicate-column error
in the middle of ``upgrade head``. Guarding on the inspector makes this revision a
statement of the *end state* rather than of a diff, which is what makes it correct from
either starting point — the same technique 0002, 0003, 0004 and 0015 use.

Revision ID: 0020_v04_experience_fk
Revises: 0018_v04_freeze_delta
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_v04_experience_fk"
down_revision: str | None = "0018_v04_freeze_delta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "experience_records"
COLUMN = "experience_id"
NEW_CONSTRAINT = "fk_experience_records_experience"
# What PostgreSQL named 0017's unnamed column-level key. Recorded so the downgrade can
# restore the constraint under the name a database at 0018 actually carries.
OLD_CONSTRAINT = "experience_records_id_fkey"
INDEX = "ix_experience_records_experience_id"


def _column_names(bind: sa.engine.Connection) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}


def _foreign_keys(bind: sa.engine.Connection) -> dict[str, list[str]]:
    """Constraint name -> the columns it constrains, for the keys on this table."""

    return {
        str(key["name"]): list(key["constrained_columns"])
        for key in sa.inspect(bind).get_foreign_keys(TABLE)
        if key.get("name") and key.get("referred_table") == "experiences"
    }


def _index_names(bind: sa.engine.Connection) -> set[str]:
    return {
        str(index["name"])
        for index in sa.inspect(bind).get_indexes(TABLE)
        if index.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()

    if COLUMN not in _column_names(bind):
        op.add_column(TABLE, sa.Column(COLUMN, sa.String(length=40), nullable=True))
        op.execute(sa.text(f"UPDATE {TABLE} SET {COLUMN} = id WHERE {COLUMN} IS NULL"))
        op.alter_column(TABLE, COLUMN, existing_type=sa.String(length=40), nullable=False)

    keys = _foreign_keys(bind)
    for name, columns in keys.items():
        if columns == ["id"]:
            op.drop_constraint(name, TABLE, type_="foreignkey")

    if NEW_CONSTRAINT not in _foreign_keys(bind):
        op.create_foreign_key(
            NEW_CONSTRAINT, TABLE, "experiences", [COLUMN], ["id"], ondelete="RESTRICT"
        )

    if INDEX not in _index_names(bind):
        op.create_index(INDEX, TABLE, [COLUMN])


def downgrade() -> None:
    bind = op.get_bind()

    if COLUMN not in _column_names(bind):
        return

    divergent = bind.execute(
        sa.text(f"SELECT count(*) FROM {TABLE} WHERE {COLUMN} <> id")
    ).scalar_one()
    if divergent:
        raise RuntimeError(
            f"cannot downgrade {revision}: {divergent} row(s) in {TABLE} have "
            f"{COLUMN} <> id. Those rows are revisions of an experience record, and the "
            "experience each one projects is recorded in no other column; dropping "
            f"{COLUMN} would discard it and the restored key on id would refuse the row. "
            "Remove the revisions deliberately, or stay at this revision."
        )

    if INDEX in _index_names(bind):
        op.drop_index(INDEX, table_name=TABLE)
    if NEW_CONSTRAINT in _foreign_keys(bind):
        op.drop_constraint(NEW_CONSTRAINT, TABLE, type_="foreignkey")
    op.drop_column(TABLE, COLUMN)
    if OLD_CONSTRAINT not in _foreign_keys(bind):
        op.create_foreign_key(
            OLD_CONSTRAINT, TABLE, "experiences", ["id"], ["id"], ondelete="RESTRICT"
        )
