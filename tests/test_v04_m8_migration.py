"""Migration 0019 against a real PostgreSQL database, and nothing else in this module.

**These tests live alone on purpose**, for the reason ``tests/test_v04_freeze_delta_migration.py``
gives about 0018: each of them calls ``downgrade()`` against the live shared database, which
*creates* the two partial unique indexes 0019 retired. While they exist, a second ``ACTIVE``
``router_model_versions`` row in one workspace is refused by PostgreSQL — so any other module
promoting a router between the downgrade and the ``finally: upgrade`` would fail for a reason
that has nothing to do with what it was testing. A module of their own removes the ordering
assumption instead of documenting it.

Living alone is necessary and not sufficient: ``CREATE UNIQUE INDEX`` is evaluated against
the rows already in the table, so a module that ran *earlier*, wrote two ``ACTIVE`` rows in
one family and left them there breaks the downgrade just as surely as one running
alongside. Two ``ACTIVE`` rows are legal at head — that is what 0019 is for — so the
obligation belongs to whoever writes them: every test in this suite that leaves such a pair
deletes its own rows afterwards (``forget_router_versions``, here and in
``tests/test_v04_m0_postgres_store.py`` and ``tests/test_v04_m8_postgres_store.py``), and
``assert_downgradeable`` below states that as a precondition so that a future breach names
the offending family instead of arriving as a ``UniqueViolationError`` inside a ``CREATE
UNIQUE INDEX``.

What 0019 has to prove beyond "it runs" is that it is **exactly reversible**. Its whole
content is two ``DROP INDEX`` statements, and the down direction has to put back the columns,
the uniqueness and the ``postgresql_where`` predicate that 0017 gave them — from a hard-coded
declaration, because the model no longer carries one. A downgrade that recreated a *plain*
unique index, or one without the ``scope`` clause, would leave a pre-0019 database silently
enforcing a rule nobody wrote, and every ordinary migration smoke test would pass.

Every id is uuid-suffixed, so the file is re-runnable against a database it has already
written to. Nothing here carries an acceptance marker: a marker sits on a ``MemoryStore``
test, and a migration has no memory backend.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from accretion.contracts.routing import RouterScope, RouterStatus
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.models import RouterModelVersionRow

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT / "migrations" / "versions" / "0019_v04_m8_router_activation_ledger.py"
)
TABLE = "router_model_versions"
RETIRED = (
    "uq_router_versions_active_workspace",
    "uq_router_versions_active_project_adapter",
)


def load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("m8_migration_live", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_direction(connection: sa.Connection, direction: Any) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        direction()


def index_definitions(connection: sa.Connection) -> dict[str, str]:
    rows = connection.execute(
        sa.text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :table"
        ),
        {"table": TABLE},
    )
    return {name: definition for name, definition in rows}


def active_family_collisions(connection: sa.Connection) -> list[tuple[str, ...]]:
    """The families that would make 0019's ``downgrade`` fail, named rather than guessed.

    The downgrade recreates two *unique* partial indexes over rows that are already in the
    table, so it succeeds only on a database that still satisfies the rule 0019 retired. A
    module that legitimately wrote two ``ACTIVE`` rows and left them there — legal at head,
    and every such test in this suite deletes its own rows for exactly this reason — would
    otherwise surface here as ``UniqueViolationError`` inside ``CREATE UNIQUE INDEX``, which
    names a key and not the test that wrote it. Asked first, the same fact arrives as a
    readable list.
    """

    workspaces = connection.execute(
        sa.text(
            "SELECT workspace_id FROM router_model_versions"
            " WHERE status = 'ACTIVE' AND scope = 'TEAM_WORKSPACE'"
            " GROUP BY workspace_id HAVING count(*) > 1"
        )
    )
    adapters = connection.execute(
        sa.text(
            "SELECT project_id, algorithm_id FROM router_model_versions"
            " WHERE status = 'ACTIVE' AND scope = 'PROJECT_ADAPTER'"
            " GROUP BY project_id, algorithm_id HAVING count(*) > 1"
        )
    )
    return [tuple(row) for row in workspaces] + [tuple(row) for row in adapters]


def assert_downgradeable(connection: sa.Connection) -> None:
    collisions = active_family_collisions(connection)
    assert not collisions, (
        "another module left several ACTIVE router_model_versions rows in one family, so"
        " 0019's downgrade cannot recreate the partial indexes: "
        f"{collisions}. Whichever test wrote them has to delete its own rows (they are"
        " legal at head and not one revision below it)."
    )


async def forget_router_versions(engine: AsyncEngine, workspace_id: str) -> None:
    """Delete the ``router_model_versions`` rows the calling test wrote.

    The rule this module applies to everybody else applies to it first: a test here that
    leaves two ``ACTIVE`` rows behind would break the *next* run of this same file against
    the same database, and re-runnability is a property this suite claims out loud.
    """

    async with engine.begin() as connection:
        await connection.execute(
            sa.delete(RouterModelVersionRow).where(
                RouterModelVersionRow.workspace_id == workspace_id
            )
        )


def raw_version(workspace_id: str, marker: str, suffix: str) -> RouterModelVersionRow:
    """An ``ACTIVE`` workspace router written straight through the session.

    The payload is a marker rather than a sealed document on purpose: these tests are about
    what the *database* accepts, and going through ``PostgresStore`` would put its Python
    guards between the assertion and the constraint under test.
    """

    return RouterModelVersionRow(
        id=new_id("router_model_version"),
        workspace_id=workspace_id,
        project_id=None,
        scope=RouterScope.TEAM_WORKSPACE.value,
        algorithm_id="gradient-boosted-ranker",
        feature_schema_version="1.0.0",
        training_snapshot_id=f"rts_{marker}",
        artifact_digest=sha256(f"artifact-{marker}-{suffix}".encode()).hexdigest(),
        parent_version_id=None,
        status=RouterStatus.ACTIVE.value,
        supersedes_contract_id=None,
        content_hash=sha256(f"content-{marker}-{suffix}".encode()).hexdigest(),
        schema_version="1.0.0",
        payload={"marker": suffix},
        created_at=datetime.now(UTC),
    )


async def test_the_two_partial_unique_indexes_are_absent_at_head() -> None:
    """Migration 0019's whole upgrade, observed in the catalogue.

    Read from ``pg_indexes`` rather than from ``Base.metadata``: metadata is what 0017
    builds a fresh database from, so an index the model stopped declaring disappears from
    both at once and metadata cannot say whether the *migration* did anything.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    try:
        async with engine.begin() as connection:
            found = await connection.run_sync(index_definitions)
    finally:
        await engine.dispose()

    for name in RETIRED:
        assert name not in found
    assert not any("WHERE" in definition for definition in found.values())
    # The ordinary indexes 0019 must not have touched.
    assert "ix_router_versions_workspace_created" in found
    assert "ix_router_versions_parent" in found


async def test_a_second_active_workspace_router_is_accepted_at_head() -> None:
    """What retiring the index was *for*, stated as behaviour rather than as a catalogue.

    With ``uq_router_versions_active_workspace`` in place this insert failed, and because
    nothing in the v0.4 family has an ``update_``, the ``ACTIVE`` row it collided with could
    never be retired either — so a workspace was activatable exactly once, forever.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    sessions = create_session_factory(engine)
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        async with sessions.begin() as session:
            session.add(raw_version(workspace_id, marker, "first"))
        async with sessions.begin() as session:
            session.add(raw_version(workspace_id, marker, "second"))

        async with sessions() as session:
            statuses = sorted(
                (
                    await session.scalars(
                        sa.select(RouterModelVersionRow.status).where(
                            RouterModelVersionRow.workspace_id == workspace_id
                        )
                    )
                ).all()
            )
        assert statuses == ["ACTIVE", "ACTIVE"]
    finally:
        await forget_router_versions(engine, workspace_id)
        await engine.dispose()


async def test_migration_0019_survives_an_up_down_up_cycle() -> None:
    """The cycle CI runs, on the one table this revision touches.

    ``down`` restores both indexes and ``up`` removes them again, and the index set is
    compared before and after so that a downgrade which recreated a *third* index, or an
    upgrade which dropped a fourth, is visible rather than absorbed.
    """

    assert POSTGRES_URL is not None
    migration = load_migration()
    engine = create_engine(POSTGRES_URL)
    observed: dict[str, dict[str, str]] = {}
    try:
        async with engine.begin() as connection:
            observed["at_head"] = await connection.run_sync(index_definitions)
            await connection.run_sync(assert_downgradeable)
            await connection.run_sync(run_direction, migration.downgrade)
            observed["after_down"] = await connection.run_sync(index_definitions)
            await connection.run_sync(run_direction, migration.upgrade)
            observed["after_up"] = await connection.run_sync(index_definitions)

        for name in RETIRED:
            assert name not in observed["at_head"]
            assert name in observed["after_down"]
            assert name not in observed["after_up"]
        assert observed["after_up"] == observed["at_head"]
        assert set(observed["after_down"]) == set(observed["at_head"]) | set(RETIRED)
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.upgrade)
        await engine.dispose()


async def test_the_downgrade_restores_the_indexes_exactly_as_0017_declared_them() -> None:
    """Partial, unique, on the right columns, with the right predicate.

    A downgrade that recreated a plain unique index would restore a *stricter* rule than
    0017 had — one candidate per workspace, forever — and one that dropped the ``scope``
    clause would make an active project adapter collide with the workspace prior. Both would
    pass an ``upgrade``/``downgrade`` smoke test, and both would break a database that
    downgraded for an unrelated reason.
    """

    assert POSTGRES_URL is not None
    migration = load_migration()
    engine = create_engine(POSTGRES_URL)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(assert_downgradeable)
            await connection.run_sync(run_direction, migration.downgrade)
            restored = await connection.run_sync(index_definitions)

        workspace = restored["uq_router_versions_active_workspace"]
        assert "CREATE UNIQUE INDEX" in workspace
        assert "(workspace_id)" in workspace
        assert "ACTIVE" in workspace and "TEAM_WORKSPACE" in workspace

        adapter = restored["uq_router_versions_active_project_adapter"]
        assert "CREATE UNIQUE INDEX" in adapter
        assert "(project_id, algorithm_id)" in adapter
        assert "ACTIVE" in adapter and "PROJECT_ADAPTER" in adapter
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.upgrade)
        await engine.dispose()


async def test_the_restored_index_still_refuses_a_second_active_router() -> None:
    """The downgrade restores a rule and not only a catalogue entry.

    An index whose predicate had been mangled would still show up in ``pg_indexes`` under
    the right name, so the reversal is checked by writing the row it is supposed to refuse.
    """

    assert POSTGRES_URL is not None
    migration = load_migration()
    engine = create_engine(POSTGRES_URL)
    sessions = create_session_factory(engine)
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        async with engine.begin() as connection:
            await connection.run_sync(assert_downgradeable)
            await connection.run_sync(run_direction, migration.downgrade)

        async with sessions.begin() as session:
            session.add(raw_version(workspace_id, marker, "first"))

        with pytest.raises(IntegrityError, match="uq_router_versions_active_workspace"):
            async with sessions.begin() as session:
                session.add(raw_version(workspace_id, marker, "second"))

        # Still partial: a CANDIDATE beside the ACTIVE one is fine under the restored rule.
        async with sessions.begin() as session:
            candidate = raw_version(workspace_id, marker, "candidate")
            candidate.status = RouterStatus.CANDIDATE.value
            session.add(candidate)
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.upgrade)
        await engine.dispose()


async def test_the_upgrade_is_idempotent_on_a_database_that_never_had_the_indexes() -> None:
    """0019 states an end state, not a diff.

    A database built from the current models arrives at 0017 with no partial indexes at all,
    so ``upgrade`` must find nothing to drop and say so quietly. An unguarded ``DROP INDEX``
    would fail there and take the whole ``upgrade head`` with it.
    """

    assert POSTGRES_URL is not None
    migration = load_migration()
    engine = create_engine(POSTGRES_URL)
    try:
        async with engine.begin() as connection:
            before = await connection.run_sync(index_definitions)
            await connection.run_sync(run_direction, migration.upgrade)
            await connection.run_sync(run_direction, migration.upgrade)
            after = await connection.run_sync(index_definitions)
        assert after == before
    finally:
        await engine.dispose()
