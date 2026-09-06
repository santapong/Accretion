"""Migration 0020 against a real PostgreSQL database, and nothing else in this module.

**These tests live alone on purpose**, for the reason ``tests/test_v04_m0_migration.py``
gives about 0017: each of them calls ``downgrade()`` against the live shared database,
which removes ``experience_records.experience_id`` mid-session and restores it in a
``finally``. That is safe only while nothing else writes an experience record between the
drop and the restore, and a module of their own removes the ordering assumption instead of
documenting it.

What 0020 has to prove beyond "it runs" is the pair of claims its docstring makes and an
``upgrade``/``downgrade`` smoke test would not notice going wrong:

* **the backfill is right** — a row written under the old shape comes back with
  ``experience_id = id``, which is the identity the old primary key enforced, so no
  projection loses the experience it names while the column is added; and
* **the downgrade refuses rather than discards** — a revision's parent is recorded in no
  other column, so a downgrade that dropped ``experience_id`` while one existed would
  silently destroy the only copy of which experience it projects.

Every id is minted fresh, so the file is re-runnable against a database it has already
written to, and every row it writes is deleted in a ``finally``. Nothing here carries an
acceptance marker: the store change claims no criterion.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from accretion.contracts import Project, Provider, Run, RunState, Task, TaskEnvelope, TaskType
from accretion.contracts.canonical import CanonicalContract
from accretion.contracts.routing import ExperienceRecord
from accretion.experience.models import (
    Experience,
    ExperienceEmbedding,
    ExperiencePolarity,
    ExperienceSourceKind,
    ExperienceTrust,
    TrajectorySegment,
    TrajectorySegmentKind,
)
from accretion.ids import derived_id, new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import PostgresStore

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT / "migrations" / "versions" / "0020_v04_experience_record_revisions.py"
)
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"
TABLE = "experience_records"
COLUMN = "experience_id"
NEW_CONSTRAINT = "fk_experience_records_experience"
INDEX = "ix_experience_records_experience_id"
SEALED_AT = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


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
    spec = importlib.util.spec_from_file_location("experience_fk_migration_live", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_direction(connection: sa.Connection, direction: Any) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        direction()


def column_names(connection: sa.Connection) -> set[str]:
    return {column["name"] for column in sa.inspect(connection).get_columns(TABLE)}


def experience_keys(connection: sa.Connection) -> dict[str, list[str]]:
    """Constraint name -> constrained columns, for this table's keys into ``experiences``."""

    return {
        str(key["name"]): list(key["constrained_columns"])
        for key in sa.inspect(connection).get_foreign_keys(TABLE)
        if key.get("name") and key.get("referred_table") == "experiences"
    }


def index_names(connection: sa.Connection) -> set[str]:
    return {
        str(index["name"])
        for index in sa.inspect(connection).get_indexes(TABLE)
        if index.get("name")
    }


def table_names(connection: sa.Connection) -> set[str]:
    return set(sa.inspect(connection).get_table_names())


async def setup_project(store: PostgresStore, tmp_path: Path, marker: str) -> Project:
    """A real ``projects`` row, because every v0.4 table has a RESTRICT key into it."""

    project = Project(
        project_id=new_id("project"),
        name=f"v0.4 experience revisions {marker}",
        repository_path=tmp_path,
    )
    await store.create_project(project)
    return project


async def setup_experience(store: PostgresStore, project: Project, marker: str) -> Experience:
    """A real ``experiences`` row: ``experience_records.experience_id`` keys into it."""

    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective=f"Anchor the v0.4 projection {marker}.",
            task_type=TaskType.IMPLEMENT,
        )
    )
    await store.create_task(task)
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.SUCCEEDED,
    )
    await store.create_run(run)
    experience = Experience(
        experience_id=new_id("experience"),
        project_id=project.project_id,
        repository_identity=digest(project.project_id),
        task_id=task.envelope.task_id,
        task_type=TaskType.IMPLEMENT,
        task_family="python-service",
        source_kind=ExperienceSourceKind.RUN,
        source_run_id=run.run_id,
        source_commit="b" * 40,
        architecture_version="2.0",
        manifest_digest=digest(f"manifest-{marker}"),
        policy_digest=digest(f"policy-{marker}"),
        verifier_digest=digest(f"verifier-{marker}"),
        prompt_digest=digest(f"prompt-{marker}"),
        context_digest=digest(f"context-{marker}"),
        tool_profile_digest=digest(f"tools-{marker}"),
        provider=Provider.FAKE,
        runtime_model="fake",
        runtime_version="test",
        trust=ExperienceTrust.HIGH,
        polarity=ExperiencePolarity.POSITIVE,
        outcome="VERIFIED_SUCCESS",
        content_digest=digest(f"experience-{marker}"),
    )
    segment = TrajectorySegment(
        segment_id=new_id("trajectory_segment"),
        experience_id=experience.experience_id,
        ordinal=1,
        kind=TrajectorySegmentKind.WORKFLOW_PATH,
        content={"nodes": ["plan", "act", "verify"]},
        content_digest=digest(f"segment-{marker}"),
    )
    embedding = ExperienceEmbedding(
        embedding_id=new_id("experience_embedding"),
        experience_id=experience.experience_id,
        input_digest=digest(f"embedding-{marker}"),
        vector=[1.0] + [0.0] * 383,
    )
    await store.save_experience(experience, (segment,), embedding)
    return experience


async def discard_records(engine: Any, experience_id: str) -> None:
    """Leave the shared database as this module found it."""

    async with engine.begin() as connection:
        await connection.execute(
            sa.text(f"DELETE FROM {TABLE} WHERE {COLUMN} = :id"),
            {"id": experience_id},
        )


async def test_migration_0020_survives_an_up_down_up_cycle_and_moves_only_its_own_key(
    tmp_path: Path,
) -> None:
    """The key is on ``experience_id`` at head, back on ``id`` after the downgrade.

    Asserted as the *shape of the schema* and not merely as "it ran": a downgrade that
    dropped the column and forgot to restore the key on ``id`` would leave a database at
    0018 with no reference into ``experiences`` at all, and every later up/down cycle would
    keep passing while the RESTRICT rule §13.1 requires had quietly stopped existing.
    """

    assert POSTGRES_URL is not None
    migration = load_migration()
    engine = create_engine(POSTGRES_URL)
    observed: dict[str, Any] = {}
    try:
        async with engine.begin() as connection:
            observed["tables"] = await connection.run_sync(table_names)
            observed["columns"] = await connection.run_sync(column_names)
            observed["keys"] = await connection.run_sync(experience_keys)
            observed["indexes"] = await connection.run_sync(index_names)
        assert COLUMN in observed["columns"]
        assert observed["keys"] == {NEW_CONSTRAINT: [COLUMN]}
        assert INDEX in observed["indexes"]

        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.downgrade)
            after_down = {
                "tables": await connection.run_sync(table_names),
                "columns": await connection.run_sync(column_names),
                "keys": await connection.run_sync(experience_keys),
                "indexes": await connection.run_sync(index_names),
            }
        assert COLUMN not in after_down["columns"]
        assert observed["columns"] - after_down["columns"] == {COLUMN}
        assert list(after_down["keys"].values()) == [["id"]]
        assert INDEX not in after_down["indexes"]
        # It creates and drops no table: this migration is not additive-by-table.
        assert after_down["tables"] == observed["tables"]

        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.upgrade)
            after_up = {
                "tables": await connection.run_sync(table_names),
                "columns": await connection.run_sync(column_names),
                "keys": await connection.run_sync(experience_keys),
                "indexes": await connection.run_sync(index_names),
            }
        assert after_up == observed

        # Idempotent in both directions: a second upgrade is a no-op, not an error.
        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.upgrade)
            assert await connection.run_sync(column_names) == observed["columns"]
            assert await connection.run_sync(experience_keys) == observed["keys"]
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.upgrade)
        await engine.dispose()


async def test_a_root_projection_survives_the_cycle_with_its_experience_backfilled(
    tmp_path: Path,
) -> None:
    """Step 2 of the upgrade, proved on a row rather than on an empty table.

    A root projection is exactly the row a database at 0018 can already hold, and the
    backfill's claim is that ``experience_id = id`` restates the identity the old primary
    key enforced. If the backfill were dropped, the ``NOT NULL`` would fail here; if it
    wrote the wrong value, the record would come back attributed to another experience.
    """

    assert POSTGRES_URL is not None
    migration = load_migration()
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    marker = uuid.uuid4().hex[:12]
    experience: Experience | None = None
    try:
        project = await setup_project(store, tmp_path, marker)
        experience = await setup_experience(store, project, marker)
        root = build(
            ExperienceRecord,
            contract_id=experience.experience_id,
            project_id=project.project_id,
            workspace_id=f"wks_{marker}",
            created_at=SEALED_AT.isoformat(),
        )
        await store.put_experience_record(root)

        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.downgrade)
            surviving = (
                await connection.execute(
                    sa.text(f"SELECT id FROM {TABLE} WHERE id = :id"),
                    {"id": root.contract_id},
                )
            ).scalar_one()
            assert surviving == root.contract_id
            await connection.run_sync(run_direction, migration.upgrade)
            backfilled = (
                await connection.execute(
                    sa.text(f"SELECT {COLUMN} FROM {TABLE} WHERE id = :id"),
                    {"id": root.contract_id},
                )
            ).scalar_one()

        assert backfilled == experience.experience_id
        assert await store.get_experience_record(root.contract_id) == root
        assert await store.list_experience_record_revisions(
            experience.experience_id, workspace_id=root.workspace_id
        ) == [root]
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.upgrade)
        if experience is not None:
            await discard_records(engine, experience.experience_id)
        await engine.dispose()


async def test_the_downgrade_refuses_while_a_revision_would_lose_its_experience(
    tmp_path: Path,
) -> None:
    """The one thing this migration must not do quietly.

    A revision's ``experience_id`` differs from its ``id`` and is the only record of which
    experience it projects. A downgrade that dropped the column would destroy that, and the
    restored key on ``id`` would refuse the row on the way back up — so the failure would
    arrive one migration later, as a foreign-key violation naming a row nobody could
    explain. Refusing here, with a count, is the only honest reversal.
    """

    assert POSTGRES_URL is not None
    migration = load_migration()
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    marker = uuid.uuid4().hex[:12]
    experience: Experience | None = None
    try:
        project = await setup_project(store, tmp_path, marker)
        experience = await setup_experience(store, project, marker)
        workspace_id = f"wks_{marker}"
        root = build(
            ExperienceRecord,
            contract_id=experience.experience_id,
            project_id=project.project_id,
            workspace_id=workspace_id,
            created_at=SEALED_AT.isoformat(),
        )
        await store.put_experience_record(root)
        second = build(
            ExperienceRecord,
            contract_id=derived_id("experience", root.contract_id, f"attribution-{marker}"),
            project_id=project.project_id,
            workspace_id=workspace_id,
            supersedes_contract_id=root.contract_id,
            created_at=(SEALED_AT + timedelta(minutes=30)).isoformat(),
            attribution={"confidence": 0.9, "method_version": "dependency-heuristic-v2"},
        )
        await store.put_experience_record(second, experience_id=experience.experience_id)

        with pytest.raises(RuntimeError, match="cannot downgrade") as refusal:
            async with engine.begin() as connection:
                await connection.run_sync(run_direction, migration.downgrade)

        assert f"{COLUMN} <> id" in str(refusal.value)
        # And it refused *before* touching anything: the schema is exactly as it was.
        async with engine.begin() as connection:
            assert COLUMN in await connection.run_sync(column_names)
            assert await connection.run_sync(experience_keys) == {NEW_CONSTRAINT: [COLUMN]}
            assert INDEX in await connection.run_sync(index_names)
        assert await store.list_experience_record_revisions(
            experience.experience_id, workspace_id=workspace_id
        ) == [root, second]
    finally:
        if experience is not None:
            await discard_records(engine, experience.experience_id)
        async with engine.begin() as connection:
            await connection.run_sync(run_direction, migration.upgrade)
        await engine.dispose()
