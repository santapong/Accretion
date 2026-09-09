"""Inventory migration rollback preserves existing rows and canonical event FKs."""

from __future__ import annotations

import os
from typing import Any

import pytest
import sqlalchemy as sa
from test_v05_registry_migration import direction, migration, names

from accretion.persistence.database import create_engine
from accretion.persistence.models import V05_AUTHORITY_INVENTORY_TABLES, V05_AUTHORITY_TABLES

URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not URL, reason="PostgreSQL URL absent")]


def schema(connection: sa.Connection) -> dict[str, Any]:
    inspector = sa.inspect(connection)
    return {
        name: {
            "columns": sorted(
                (c["name"], str(c["type"]), c["nullable"]) for c in inspector.get_columns(name)
            ),
            "fk": inspector.get_foreign_keys(name),
            "unique": inspector.get_unique_constraints(name),
        }
        for name in V05_AUTHORITY_INVENTORY_TABLES
    }


async def test_inventory_migration_only_owns_two_additive_tables() -> None:
    module = migration("0023_v05_authority_inventory.py")
    assert module.down_revision == "0022_v05_runtime_authority"
    engine = create_engine(str(URL))
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                before_names = await connection.run_sync(names)
                before_schema = await connection.run_sync(schema)
                for table in before_schema.values():
                    assert any(
                        f["referred_table"] == "robotics_contracts"
                        and f["constrained_columns"] == ["event_stream_id"]
                        for f in table["fk"]
                    )
                    assert any(
                        f["referred_table"] == "simulation_project_bindings" for f in table["fk"]
                    )
                    assert len(table["unique"]) >= 2
                await connection.run_sync(direction, module.downgrade)
                remaining = await connection.run_sync(names)
                assert before_names - remaining == set(V05_AUTHORITY_INVENTORY_TABLES)
                assert set(V05_AUTHORITY_TABLES) <= remaining
                assert {
                    "runs",
                    "principals",
                    "capabilities",
                    "policies",
                    "robotics_contracts",
                    "robotics_domain_events",
                } <= remaining
                await connection.run_sync(direction, module.upgrade)
                await connection.run_sync(direction, module.upgrade)
                assert await connection.run_sync(schema) == before_schema
                assert await connection.run_sync(names) == before_names
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
