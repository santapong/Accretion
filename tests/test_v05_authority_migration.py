"""Transactional DDL proof, with rollback preserving pre-existing disposable rows."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_v05_registry_migration import migration, names

from accretion.persistence.database import create_engine
from accretion.persistence.models import V05_AUTHORITY_INVENTORY_TABLES, V05_AUTHORITY_TABLES

URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not URL, reason="PostgreSQL URL absent")]


def inspect_schema(connection: sa.Connection) -> dict[str, Any]:
    inspector = sa.inspect(connection)
    return {
        name: {
            # PostgreSQL appends a re-added column. Physical ordinal position
            # does not change named ORM queries or the schema's constraints.
            "columns": sorted(
                (c["name"], str(c["type"]), c["nullable"]) for c in inspector.get_columns(name)
            ),
            "fk": sorted(inspector.get_foreign_keys(name), key=lambda row: row["name"]),
            "unique": sorted(inspector.get_unique_constraints(name), key=lambda row: row["name"]),
            "indexes": sorted(inspector.get_indexes(name), key=lambda row: row["name"]),
            "pk": inspector.get_pk_constraint(name),
        }
        for name in (*V05_AUTHORITY_TABLES, "runs")
    }


def apply(connection: sa.Connection, function: Any) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        function()


async def test_authority_upgrade_downgrade_is_additive_and_preserves_constraints() -> None:
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0022_v05_runtime_authority.py"
    spec = importlib.util.spec_from_file_location("v05_authority_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "0021_v05_robotics_registry"
    engine = create_engine(str(URL))
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                if "simulation_host_creations" in await connection.run_sync(names):
                    journal = migration("0024_v05_host_creation_journal.py")
                    await connection.run_sync(apply, journal.downgrade)
                if set(V05_AUTHORITY_INVENTORY_TABLES) <= await connection.run_sync(names):
                    inventory = migration("0023_v05_authority_inventory.py")
                    await connection.run_sync(apply, inventory.downgrade)
                before = await connection.run_sync(inspect_schema)
                await connection.run_sync(apply, module.downgrade)
                tables = await connection.run_sync(lambda c: set(sa.inspect(c).get_table_names()))
                assert not set(V05_AUTHORITY_TABLES) & tables
                assert {
                    "runs",
                    "tasks",
                    "principals",
                    "robotics_contracts",
                    "simulation_project_bindings",
                } <= tables
                columns = await connection.run_sync(
                    lambda c: {x["name"] for x in sa.inspect(c).get_columns("runs")}
                )
                assert "principal_id" not in columns
                await connection.run_sync(apply, module.upgrade)
                # Legacy0001 creates current metadata on a fresh database.
                await connection.run_sync(apply, module.upgrade)
                assert await connection.run_sync(inspect_schema) == before
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
