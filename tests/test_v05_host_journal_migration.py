"""0024 upgrade/downgrade is additive and rollback-preserving on disposable PG."""

import os

import pytest
import sqlalchemy as sa
from test_v05_registry_migration import direction, migration, names

from accretion.persistence.database import create_engine

URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not URL, reason="PostgreSQL URL absent")]


def schema(connection):
    inspector = sa.inspect(connection)
    return {
        "columns": [
            (c["name"], str(c["type"]), c["nullable"])
            for c in inspector.get_columns("simulation_host_creations")
        ],
        "fks": inspector.get_foreign_keys("simulation_host_creations"),
        "unique": inspector.get_unique_constraints("simulation_host_creations"),
    }


async def test_host_journal_migration_preserves_authority_and_exact_constraints():
    module = migration("0024_v05_host_creation_journal.py")
    assert module.down_revision == "0023_v05_authority_inventory"
    engine = create_engine(str(URL))
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                before = await connection.run_sync(schema)
                tables = await connection.run_sync(names)
                assert {tuple(u["column_names"]) for u in before["unique"]} >= {
                    ("lease_id",),
                    ("docker_name",),
                    ("container_id",),
                }
                assert {f["referred_table"] for f in before["fks"]} >= {
                    "simulation_project_bindings",
                    "simulation_leases",
                    "runs",
                    "principals",
                    "simulation_episode_state",
                    "simulation_resources",
                }
                await connection.run_sync(direction, module.downgrade)
                assert tables - await connection.run_sync(names) == {"simulation_host_creations"}
                await connection.run_sync(direction, module.upgrade)
                await connection.run_sync(direction, module.upgrade)
                assert await connection.run_sync(schema) == before
                assert await connection.run_sync(names) == tables
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
