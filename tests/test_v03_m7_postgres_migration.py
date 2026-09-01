"""The 0016 migration is reversible against a real PostgreSQL database.

Run before ``test_v03_m7_postgres_store.py`` (alphabetical collection order), and
self-restoring: the cycle ends with the M7 tables created, so the schema matches
``alembic upgrade head`` again and the file passes twice in a row.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from accretion.contracts import IdentityAssertion
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import PostgresStore

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0016_v03_m7_enterprise_auth.py"
)
# A table created by 0015: the M7 downgrade must not touch it.
INHERITED_TABLE = "research_evidence"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("m7_migration_live", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(connection: sa.Connection, direction: Any) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        direction()


def _tables(connection: sa.Connection) -> set[str]:
    return set(sa.inspect(connection).get_table_names())


async def test_v03_m7_migration_0016_survives_an_up_down_up_cycle() -> None:
    assert POSTGRES_URL is not None
    migration = _load_migration()
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    suffix = uuid.uuid4().hex[:12]
    created_at = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
    assertion = IdentityAssertion(
        assertion_id=new_id("identity_assertion"),
        auth_session_id=f"aus_mig_{suffix}",
        principal_id=f"usr_mig_{suffix}",
        issuer="https://idp.example.invalid",
        subject=f"subject-{suffix}",
        secret_store_key=new_id("secret_record"),
        expires_at=created_at + timedelta(minutes=5),
        created_at=created_at,
    )
    observed: dict[str, set[str]] = {}
    try:
        async with engine.begin() as connection:
            # The database is at head, so the M7 tables are already there.
            observed["at_head"] = await connection.run_sync(_tables)
        assert set(migration.M7_TABLES) <= observed["at_head"]
        assert INHERITED_TABLE in observed["at_head"]

        # A row written before the downgrade proves the downgrade really drops.
        await store.upsert_identity_assertion(assertion)
        assert (
            await store.get_identity_assertion_for_session(assertion.auth_session_id)
        ) == assertion

        async with engine.begin() as connection:
            await connection.run_sync(_run, migration.downgrade)
            observed["after_down"] = await connection.run_sync(_tables)
        assert not set(migration.M7_TABLES) & observed["after_down"]
        assert INHERITED_TABLE in observed["after_down"]
        # Exactly the M7 tables went, and nothing else in the schema moved.
        assert observed["at_head"] - observed["after_down"] == set(migration.M7_TABLES)

        async with engine.begin() as connection:
            await connection.run_sync(_run, migration.upgrade)
            observed["after_up"] = await connection.run_sync(_tables)
        assert observed["after_up"] == observed["at_head"]

        # Re-created empty: the down direction destroyed the rows, and the store
        # works against the re-created tables without any further intervention.
        assert (
            await store.get_identity_assertion_for_session(assertion.auth_session_id)
        ) is None
        await store.upsert_identity_assertion(assertion)
        read_back = await store.get_identity_assertion_for_session(
            assertion.auth_session_id
        )
        assert read_back == assertion
        # The row is left behind on purpose: the store exposes no deletion surface for
        # assertions (AC3-PLG-05), and the uuid-suffixed ids keep this test re-runnable.

        # Idempotent in both directions: a second upgrade is a no-op, not an error.
        async with engine.begin() as connection:
            await connection.run_sync(_run, migration.upgrade)
            assert await connection.run_sync(_tables) == observed["at_head"]
    finally:
        # Leave the schema at head whatever happened above.
        async with engine.begin() as connection:
            await connection.run_sync(_run, migration.upgrade)
        await engine.dispose()


async def test_v03_m7_migration_0016_recreates_the_indexes_it_declares() -> None:
    assert POSTGRES_URL is not None
    migration = _load_migration()
    engine = create_engine(POSTGRES_URL)

    def _indexes(connection: sa.Connection, table: str) -> set[str]:
        return {index["name"] or "" for index in sa.inspect(connection).get_indexes(table)}

    try:
        declared = {
            name: {index.name or "" for index in migration.Base.metadata.tables[name].indexes}
            for name in migration.M7_TABLES
        }
        async with engine.begin() as connection:
            await connection.run_sync(_run, migration.downgrade)
            await connection.run_sync(_run, migration.upgrade)
            for name in migration.M7_TABLES:
                assert declared[name] <= await connection.run_sync(_indexes, name)
                assert declared[name]
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(_run, migration.upgrade)
        await engine.dispose()
