"""Transactional PostgreSQL DDL witness; rolls back to preserve other test rows."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from accretion.persistence.database import create_engine
from accretion.persistence.models import (
    V05_AUTHORITY_INVENTORY_TABLES,
    V05_AUTHORITY_TABLES,
    V05_REGISTRY_TABLES,
)

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]


def migration(filename: str = "0021_v05_robotics_registry.py") -> Any:
    path = Path(__file__).resolve().parents[1] / "migrations/versions" / filename
    spec = importlib.util.spec_from_file_location("v05_registry_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def names(connection: sa.Connection) -> set[str]:
    return set(sa.inspect(connection).get_table_names())


def direction(connection: sa.Connection, function: Any) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        function()


def constraints(connection: sa.Connection) -> dict[str, Any]:
    inspector = sa.inspect(connection)
    return {
        name: {
            "fk": inspector.get_foreign_keys(name),
            "unique": inspector.get_unique_constraints(name),
            "indexes": inspector.get_indexes(name),
            "columns": [
                (c["name"], str(c["type"]), c["nullable"]) for c in inspector.get_columns(name)
            ],
        }
        for name in V05_REGISTRY_TABLES
    }


async def test_additive_registry_migration_restores_same_constraints_and_only_own_tables() -> None:
    module = migration()
    assert module.down_revision == "0019_v04_m8_activation"
    engine = create_engine(str(POSTGRES_URL))
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                if "simulation_host_creations" in await connection.run_sync(names):
                    journal = migration("0024_v05_host_creation_journal.py")
                    await connection.run_sync(direction, journal.downgrade)
                if set(V05_AUTHORITY_INVENTORY_TABLES) <= await connection.run_sync(names):
                    inventory = migration("0023_v05_authority_inventory.py")
                    await connection.run_sync(direction, inventory.downgrade)
                # Match actual Alembic downgrade order: remove the0022 child
                # inside this rollback-only transaction before exercising0021.
                if set(V05_AUTHORITY_TABLES) <= await connection.run_sync(names):
                    child = migration("0022_v05_runtime_authority.py")
                    await connection.run_sync(direction, child.downgrade)
                before = await connection.run_sync(names)
                original = await connection.run_sync(constraints)
                assert set(V05_REGISTRY_TABLES) <= before
                await connection.run_sync(direction, module.downgrade)
                after = await connection.run_sync(names)
                assert before - after == set(V05_REGISTRY_TABLES)
                assert {"projects", "principals", "workspaces", "objective_contracts"} <= after
                await connection.run_sync(direction, module.downgrade)
                await connection.run_sync(direction, module.upgrade)
                await connection.run_sync(direction, module.upgrade)
                assert await connection.run_sync(names) == before
                assert await connection.run_sync(constraints) == original
            finally:
                # PostgreSQL restores the original tables AND their rows.
                await transaction.rollback()
    finally:
        await engine.dispose()
