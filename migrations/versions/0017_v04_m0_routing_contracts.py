"""v0.4 M0 routing contract persistence freeze (SDD v0.4 §13, ADR-058).

Creates the fourteen tables SDD v0.4 §13 lists plus ``objective_contracts``, which §7.1
requires and ADR-058 counts — fifteen in all — and nothing else. §13's table begins at
``node_contracts``; the objective contract is the root every other record in the family
references, so it is stored here with them rather than left without a table. In creation
order:

``objective_contracts``, ``node_contracts``, ``verification_specs``,
``routing_requests``, ``configuration_candidates``, ``compatibility_decisions``,
``routing_receipts``, ``routing_overrides``, ``verification_results``,
``experience_records``, ``failure_events``, ``router_model_versions``,
``router_training_snapshots``, ``router_promotion_reports``, ``shadow_decisions``.

The §13.1 constraints these tables carry:

* **"Contract hash/version tuples are unique."** Every one of the fifteen tables gets a
  ``UniqueConstraint(content_hash, schema_version)``. All of them and not only the three
  named "contract", because a v0.4 record's ``contract_id`` is inside the digest input
  (only ``content_hash`` itself is excluded, ADR-056), so two rows can share a digest
  only if they are the same sealed document filed twice.
* **"One immutable receipt per routing request ID."** ``routing_receipts.routing_request_id``
  is unique — §8.2's idempotency key as a database constraint. Both stores pre-check the
  rule and raise ``ValueError`` before the insert, so a caller sees the same error on
  either backend instead of a ``ValueError`` in memory and an ``IntegrityError`` here;
  the column constraint is the backstop for the racing second writer.
* **"One active workspace router per workspace."** ``uq_router_versions_active_workspace``,
  a *partial* unique index over ``workspace_id`` where
  ``status = 'ACTIVE' AND scope = 'TEAM_WORKSPACE'``.
* **"One active adapter per project/router family."**
  ``uq_router_versions_active_project_adapter``, a partial unique index over
  ``(project_id, algorithm_id)`` where ``status = 'ACTIVE' AND scope = 'PROJECT_ADAPTER'``.
* **"Promotion reports are append-only."** Enforced by the absence of any update or
  delete method for these tables in ``StateStore``, ``MemoryStore`` and ``PostgresStore``,
  with a test that asserts the absence. No trigger; nothing to bypass.
* **"Evidence/experience deletion ... must not orphan provenance silently."** Every
  foreign key here is ``ON DELETE RESTRICT``. ``experience_records.id`` references
  ``experiences.id`` and every ``project_id`` references ``projects.id``. Nothing
  cascades into a receipt, an evidence reference or a promotion report. ``MemoryStore``
  mirrors the existence half of both keys in Python, so a record naming a project or an
  experience that does not exist is a ``ValueError`` there and an ``IntegrityError`` here
  rather than accepted in one backend and refused in the other.

Those two partial unique indexes are the first in this repository. They are emitted from
``postgresql_where`` clauses on the ``Index`` objects in ``persistence/models.py``, so
they arrive here through ``Base.metadata`` like every other index rather than as raw SQL,
and ``MemoryStore`` mirrors both rules in Python so the store-parity tests hold on both
backends. Five of the six rules above are therefore enforced twice — once by PostgreSQL,
once by a pre-insert check in each store, each check running inside the same transaction
or the same lock as the insert it guards — which is what keeps a §13.1 violation the same
kind of failure whichever backend a caller is running against. The sixth, append-only, is
enforced by the absence of a method that could break it.

**Additive only.** No existing table gains, loses or renames a column; no data is
backfilled and no column is dropped; nothing outside the fifteen is touched. A database
at 0016 keeps validating unchanged, which is what makes the down direction safe: dropping
these fifteen destroys no row that is the only copy of anything, because at 0017 nothing
in v0.1-v0.3 reads or writes them and no v0.4 milestone has shipped yet.

Revision ID: 0017_v04_m0_routing_contracts
Revises: 0016_v03_m7_enterprise_auth
Create Date: 2026-09-05
"""

from collections.abc import Sequence

from alembic import op

from accretion.persistence.models import (
    V04_FREEZE_DELTA_TABLES,
    V04_M0_ROUTING_TABLES,
    Base,
)

revision: str = "0017_v04_m0_routing_contracts"
down_revision: str | None = "0016_v03_m7_enterprise_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The list lives in ``persistence/models.py`` so that the migration, the store and the
# tests all read one declaration of what "the fifteen tables" means. It is in dependency
# order — nothing references anything later in it — which is what makes ``reversed()``
# a correct drop order below.
#
# ``V04_FREEZE_DELTA_TABLES`` is subtracted rather than the fifteen being restated here.
# The freeze delta of 5 Sep 2026 appended two tables to ``V04_M0_ROUTING_TABLES`` — the
# constant every store-parity proof in the suite reads — and migration **0018** creates
# them. This revision has already been applied in the field, and a migration that quietly
# started creating two more tables than it did the first time would make a database at
# 0017 no longer reproducible from the revision it records. Subtracting keeps one list in
# one place and keeps this revision's effect exactly what it always was.
M0_TABLES = tuple(
    name for name in V04_M0_ROUTING_TABLES if name not in V04_FREEZE_DELTA_TABLES
)


def upgrade() -> None:
    bind = op.get_bind()
    for name in M0_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(M0_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
