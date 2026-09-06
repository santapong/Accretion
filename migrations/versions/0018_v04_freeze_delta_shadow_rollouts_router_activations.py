"""v0.4 freeze delta: branched-rollout results and the router activation ledger.

Creates the two tables the freeze delta of 5 Sep 2026 added to the v0.4 family, and
nothing else, in creation order:

``shadow_rollout_results``, ``router_activations``.

Neither table is in SDD §13's list, because §13 was written before two facts about M0 were
established against the code:

* **A shadow decision has no observed outcome.** ``ShadowDecision`` (0017,
  ``shadow_decisions``) records what a candidate router *would* have chosen and the utility
  it *projected*, which is a claim a model makes about itself. §10.2 gates the shadow stage
  on evidence, so M6.2 scores a shadow choice by forking the live run — the candidate's
  configuration in one sandbox, the executed configuration in a sibling sandbox under the
  same seed policy — and each fork writes one ``shadow_rollout_results`` row (ADR-060).
  Two rows and not one: a pair whose second arm failed still contains a real measurement,
  and a schema that made the pair atomic would throw it away.
* **"One active workspace router" could only ever fire once.** 0017's partial unique index
  ``uq_router_versions_active_workspace`` plus this family's deliberate absence of any
  ``update_`` method means the first ``ACTIVE`` row can never be retired and a second can
  never be inserted. ``router_activations`` moves the rule onto an append-only ledger whose
  head is the active version (ADR-061), with
  ``uq_router_activations_sequence`` over ``(workspace_id, scope, family_key, sequence)`` —
  an ordinary unique constraint, because the rule is unconditional once "active" is a
  position in a sequence rather than a value in a column.

**The two partial indexes 0017 created are untouched here.** Retiring them belongs to
M8.1's migration 0019, together with the composite activation write that needs them gone.
Splitting it that way is what keeps each migration independently reversible: a database at
0018 satisfies the old rule and the new one at once, so ``downgrade`` has nothing to
restore beyond the two tables it created.

**Additive only.** No existing table gains, loses or renames a column; no data is
backfilled and no column is dropped. A database at 0017 keeps validating unchanged, which
is what makes the down direction safe: dropping these two destroys no row that is the only
copy of anything, because at 0018 no v0.4 milestone that writes them has shipped.

**The revision id is shorter than the file name**, which is the one place this migration
departs from the convention 0013-0017 follow. Alembic's ``alembic_version.version_num`` is
``VARCHAR(32)`` and this file's stem is fifty-five characters, so a matching id fails at
stamp time with a driver-level truncation error that names no migration. The file keeps the
descriptive name a reviewer reads; the id is what the database stores.

Revision ID: 0018_v04_freeze_delta
Revises: 0017_v04_m0_routing_contracts
Create Date: 2026-09-05
"""

from collections.abc import Sequence

from alembic import op

from accretion.persistence.models import V04_FREEZE_DELTA_TABLES, Base

revision: str = "0018_v04_freeze_delta"
down_revision: str | None = "0017_v04_m0_routing_contracts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The list lives in ``persistence/models.py`` so that this migration, 0017 (which
# subtracts it), the store and the tests all read one declaration of which two tables the
# freeze delta added. It is in dependency order — neither references the other — which is
# what makes ``reversed()`` a correct drop order below.
FREEZE_DELTA_TABLES = V04_FREEZE_DELTA_TABLES


def upgrade() -> None:
    bind = op.get_bind()
    for name in FREEZE_DELTA_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(FREEZE_DELTA_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
