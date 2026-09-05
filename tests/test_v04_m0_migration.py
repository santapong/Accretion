"""Migration 0017 against a real PostgreSQL database, and nothing else in this module.

**These two tests live alone on purpose.** Each of them calls ``migration.downgrade()``
against the live shared database, which drops all fifteen v0.4 tables in the middle of the
session and recreates them in a ``finally``. That is safe only while nothing else writes a
v0.4 row between the drop and the restore. In ``test_v04_m0_postgres_store.py`` they were
safe by *file order* — collected first, so nothing had been written yet — which holds for a
whole-file run and fails for a ``-k`` selection that picks a later test alongside one of
these, and fails again the day another module writes v0.4 rows and sorts earlier. The
failure would surface as an unrelated test's rows having vanished, which is a hard thing to
diagnose from the traceback it produces. A module of their own removes the ordering
assumption instead of documenting it.

Every id is uuid-suffixed, so the file is re-runnable against a database it has already
written to. Nothing here carries an acceptance marker: M0 claims no criterion (ADR-052).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from accretion.contracts import Project
from accretion.contracts.canonical import CanonicalContract
from accretion.contracts.routing import ObjectiveContract
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import PostgresStore

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "versions" / "0017_v04_m0_routing_contracts.py"
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"
# A table created by 0016: the M0 downgrade must not touch it.
INHERITED_TABLE = "enterprise_auth_grants"


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def build[C: CanonicalContract](model: type[C], **overrides: Any) -> C:
    """One golden ``minimal.json``, re-tenanted to this run's ids and re-sealed.

    Only the top level is rewritten because only ``ObjectiveContract`` is built here, and
    it embeds no record that carries a workspace of its own. The full tree-walking version
    lives beside the round-trip tests that need it, in ``test_v04_m0_postgres_store.py``.
    """

    path = FIXTURE_ROOT / snake_case(model.__name__) / "minimal.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    document.update(overrides)
    document.pop("content_hash", None)
    if "contract_id" not in overrides and model.ID_KIND is not None:
        document["contract_id"] = new_id(model.ID_KIND)
    return model.model_validate(document)


def load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("m0_migration_live", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_direction(connection: sa.Connection, direction: Any) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        direction()


def table_names(connection: sa.Connection) -> set[str]:
    return set(sa.inspect(connection).get_table_names())


async def setup_project(store: PostgresStore, tmp_path: Path, marker: str) -> Project:
    """A real ``projects`` row, because every v0.4 table has a RESTRICT key into it."""

    project = Project(
        project_id=new_id("project"), name=f"v0.4 M0 {marker}", repository_path=tmp_path
    )
    await store.create_project(project)
    return project


async def test_migration_0017_survives_an_up_down_up_cycle_and_drops_only_its_own(
    tmp_path: Path,
) -> None:
    assert POSTGRES_URL is not None
    migration = load_migration()
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    marker = uuid.uuid4().hex[:12]
    observed: dict[str, set[str]] = {}
    try:
        async with engine.begin() as connection:
            observed["at_head"] = await connection.run_sync(table_names)
        assert set(migration.M0_TABLES) <= observed["at_head"]
        assert INHERITED_TABLE in observed["at_head"]

        # A row written before the downgrade proves the downgrade really drops.
        project = await setup_project(store, tmp_path, marker)
        contract = build(
            ObjectiveContract,
            project_id=project.project_id,
            workspace_id=f"wks_{marker}",
        )
        await store.put_objective_contract(contract)
        assert await store.get_objective_contract(contract.contract_id) == contract

        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.downgrade)
            observed["after_down"] = await connection.run_sync(table_names)
        assert not set(migration.M0_TABLES) & observed["after_down"]
        assert INHERITED_TABLE in observed["after_down"]
        # Exactly the fifteen went, and nothing else in the schema moved.
        assert observed["at_head"] - observed["after_down"] == set(migration.M0_TABLES)

        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.upgrade)
            observed["after_up"] = await connection.run_sync(table_names)
        assert observed["after_up"] == observed["at_head"]

        # Re-created empty, and usable again with no further intervention.
        assert await store.get_objective_contract(contract.contract_id) is None
        await store.put_objective_contract(contract)
        assert await store.get_objective_contract(contract.contract_id) == contract

        # Idempotent in both directions: a second upgrade is a no-op, not an error.
        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.upgrade)
            assert await connection.run_sync(table_names) == observed["at_head"]
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.upgrade)
        await engine.dispose()


async def test_migration_0017_recreates_every_index_and_constraint_it_declares() -> None:
    assert POSTGRES_URL is not None
    migration = load_migration()
    engine = create_engine(POSTGRES_URL)

    def indexes(connection: sa.Connection, name: str) -> set[str]:
        return {index["name"] or "" for index in sa.inspect(connection).get_indexes(name)}

    def unique_constraints(connection: sa.Connection, name: str) -> set[str]:
        return {
            constraint["name"] or ""
            for constraint in sa.inspect(connection).get_unique_constraints(name)
        }

    try:
        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.downgrade)
            await connection.run_sync(run_direction, migration.upgrade)
            for name in migration.M0_TABLES:
                declared = {
                    index.name or "" for index in migration.Base.metadata.tables[name].indexes
                }
                assert declared
                assert declared <= await connection.run_sync(indexes, name)
                assert f"uq_{name}_hash_version" in await connection.run_sync(
                    unique_constraints, name
                ) or any(
                    constraint.endswith("_hash_version")
                    for constraint in await connection.run_sync(unique_constraints, name)
                )
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.upgrade)
        await engine.dispose()
