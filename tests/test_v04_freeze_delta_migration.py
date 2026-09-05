"""Migration 0018 against a real PostgreSQL database, and nothing else in this module.

**These tests live alone on purpose**, for the reason ``tests/test_v04_m0_migration.py``
gives about 0017: each of them calls ``downgrade()`` against the live shared database,
dropping ``shadow_rollout_results`` and ``router_activations`` mid-session and recreating
them in a ``finally``. That is safe only while nothing else writes one of those rows
between the drop and the restore, and a module of their own removes the ordering assumption
instead of documenting it.

What 0018 has to prove beyond "it runs" is that it is **narrow**. It creates two tables and
must leave 0017's fifteen — and, specifically, the two partial unique indexes M8.1's
migration 0019 will retire — untouched, because a database sitting between 0018 and 0019
has to satisfy the old "one ACTIVE router" rule and the new ledger rule at once. That is
the property that makes each of the two migrations independently reversible, and it is the
one an ``upgrade``/``downgrade`` smoke test would not notice going wrong.

Every id is uuid-suffixed, so the file is re-runnable against a database it has already
written to. Nothing here carries an acceptance marker: the freeze delta claims no criterion
(ADR-052).
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
from accretion.contracts.routing import RouterActivation, ShadowRolloutResult
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import PostgresStore

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT
    / "migrations"
    / "versions"
    / "0018_v04_freeze_delta_shadow_rollouts_router_activations.py"
)
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"
# Two tables created by 0017: the delta's downgrade must not touch either.
INHERITED_TABLES = ("router_model_versions", "shadow_decisions")
PARTIAL_INDEXES = (
    "uq_router_versions_active_workspace",
    "uq_router_versions_active_project_adapter",
)


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def build[C: CanonicalContract](model: type[C], **overrides: Any) -> C:
    """One golden ``minimal.json``, re-tenanted to this run's ids and re-sealed."""

    path = FIXTURE_ROOT / snake_case(model.__name__) / "minimal.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    document.update(overrides)
    document.pop("content_hash", None)
    if "contract_id" not in overrides and model.ID_KIND is not None:
        document["contract_id"] = new_id(model.ID_KIND)
    return model.model_validate(document)


def load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("delta_migration_live", MIGRATION_PATH)
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


def index_names(connection: sa.Connection, table: str) -> set[str]:
    return {index["name"] or "" for index in sa.inspect(connection).get_indexes(table)}


async def setup_project(store: PostgresStore, tmp_path: Path, marker: str) -> Project:
    """A real ``projects`` row: both delta tables carry a RESTRICT key into it."""

    project = Project(
        project_id=new_id("project"),
        name=f"v0.4 freeze delta {marker}",
        repository_path=tmp_path,
    )
    await store.create_project(project)
    return project


async def test_migration_0018_survives_an_up_down_up_cycle_and_drops_only_its_own(
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
        assert set(migration.FREEZE_DELTA_TABLES) <= observed["at_head"]
        for name in INHERITED_TABLES:
            assert name in observed["at_head"]

        # Rows written before the downgrade prove the downgrade really drops.
        project = await setup_project(store, tmp_path, marker)
        rollout = build(
            ShadowRolloutResult, project_id=project.project_id, workspace_id=f"wks_{marker}"
        )
        activation = build(RouterActivation, workspace_id=f"wks_{marker}")
        await store.put_shadow_rollout_result(rollout)
        await store.put_router_activation(activation)
        assert await store.get_shadow_rollout_result(rollout.contract_id) == rollout
        assert await store.get_router_activation(activation.contract_id) == activation

        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.downgrade)
            observed["after_down"] = await connection.run_sync(table_names)
        assert not set(migration.FREEZE_DELTA_TABLES) & observed["after_down"]
        # Exactly the two went, and nothing else in the schema moved — the fifteen 0017
        # created are all still there, which is what "additive and narrow" has to mean.
        assert observed["at_head"] - observed["after_down"] == set(
            migration.FREEZE_DELTA_TABLES
        )

        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.upgrade)
            observed["after_up"] = await connection.run_sync(table_names)
        assert observed["after_up"] == observed["at_head"]

        # Re-created empty, and usable again with no further intervention.
        assert await store.get_shadow_rollout_result(rollout.contract_id) is None
        assert await store.get_router_activation(activation.contract_id) is None
        await store.put_shadow_rollout_result(rollout)
        await store.put_router_activation(activation)
        assert await store.get_router_activation(activation.contract_id) == activation

        # Idempotent in both directions: a second upgrade is a no-op, not an error.
        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.upgrade)
            assert await connection.run_sync(table_names) == observed["at_head"]
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.upgrade)
        await engine.dispose()


async def test_migration_0018_leaves_the_two_partial_indexes_0019_will_retire() -> None:
    """The narrowness claim, stated against the objects M8.1 is going to remove.

    If 0018 ever started dropping them, the suite would stay green — nothing else asserts
    their presence across a downgrade — and M8.1's 0019 would then be a migration whose
    down direction could not restore what it claimed to.
    """

    assert POSTGRES_URL is not None
    migration = load_migration()
    engine = create_engine(POSTGRES_URL)
    try:
        async with engine.begin() as connection:
            before = await connection.run_sync(index_names, "router_model_versions")
            await connection.run_sync(run_direction, migration.downgrade)
            during = await connection.run_sync(index_names, "router_model_versions")
            await connection.run_sync(run_direction, migration.upgrade)
            after = await connection.run_sync(index_names, "router_model_versions")

        for name in PARTIAL_INDEXES:
            assert name in before
        assert during == before
        assert after == before
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.upgrade)
        await engine.dispose()


async def test_migration_0018_recreates_every_index_and_constraint_it_declares() -> None:
    assert POSTGRES_URL is not None
    migration = load_migration()
    engine = create_engine(POSTGRES_URL)

    def unique_constraints(connection: sa.Connection, name: str) -> set[str]:
        return {
            constraint["name"] or ""
            for constraint in sa.inspect(connection).get_unique_constraints(name)
        }

    try:
        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.downgrade)
            await connection.run_sync(run_direction, migration.upgrade)
            for name in migration.FREEZE_DELTA_TABLES:
                declared = {
                    index.name or "" for index in migration.Base.metadata.tables[name].indexes
                }
                assert declared
                assert declared <= await connection.run_sync(index_names, name)
                found = await connection.run_sync(unique_constraints, name)
                assert any(
                    constraint.endswith("_hash_version") for constraint in found
                ), (name, found)
            assert "uq_router_activations_sequence" in await connection.run_sync(
                unique_constraints, "router_activations"
            )
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.upgrade)
        await engine.dispose()
