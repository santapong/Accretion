"""The freeze delta's two tables against a real PostgreSQL database (ADR-060, ADR-061).

The twin of ``tests/test_v04_freeze_delta.py``, and it does not repeat it.
``tests/test_v04_m0_postgres_store.py`` is parametrized over ``TABLE_CONTRACTS``, which the
delta grew, so round-tripping, ordering, the immutability refusal and the Memory/Postgres
equality claim already cover ``shadow_rollout_results`` and ``router_activations``. Three
things do not follow from that and are proved here:

* ``uq_router_activations_sequence`` is a **real database constraint** and not Python
  politeness — checked by writing rows straight through the session, past every guard the
  store owns, and watching PostgreSQL be the one that says no. A rule that lived only in a
  pre-check would look identical in every test until two writers raced for the same
  sequence number, which is exactly the moment a promotion is happening;
* the two backends refuse a conflicting re-put with the **same exception text**, which is
  what makes ``MemoryStore`` a usable stand-in rather than a near-miss; and
* the delta touched none of 0017's constraints: the two partial unique indexes on
  ``router_model_versions`` are still there, still partial, still saying what they said.

Every id is uuid-suffixed, so the file is re-runnable against a database it has already
written to, and no test asserts on a global row count. Nothing here carries an acceptance
marker: the freeze delta claims no criterion (ADR-052).
"""

from __future__ import annotations

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
from sqlalchemy.exc import IntegrityError

from accretion.contracts import Project
from accretion.contracts.canonical import CanonicalContract
from accretion.contracts.routing import (
    RouterActivation,
    RouterActivationKind,
    RouterScope,
    ShadowRolloutKind,
    ShadowRolloutResult,
)
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.models import (
    V04_FREEZE_DELTA_TABLES,
    RouterActivationRow,
    RouterModelVersionRow,
)
from accretion.persistence.store import MemoryStore, PostgresStore

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def build[C: CanonicalContract](
    model: type[C], fixture: str = "minimal", /, **overrides: Any
) -> C:
    """One golden fixture, re-tenanted to this run's ids and re-sealed.

    Only the top level is rewritten: neither of the delta's two records embeds a contract
    that carries a workspace of its own, so the tree-walking ``rescope`` the M0 twin needs
    would have nothing to walk.
    """

    path = FIXTURE_ROOT / snake_case(model.__name__) / f"{fixture}.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    document.pop("_expect", None)
    document.update(overrides)
    document.pop("content_hash", None)
    if "contract_id" not in overrides and model.ID_KIND is not None:
        document["contract_id"] = new_id(model.ID_KIND)
    return model.model_validate(document)


async def setup_project(store: PostgresStore, tmp_path: Path, marker: str) -> Project:
    """A real ``projects`` row, because every v0.4 table has a RESTRICT key into it."""

    project = Project(
        project_id=new_id("project"),
        name=f"v0.4 freeze delta {marker}",
        repository_path=tmp_path,
    )
    await store.create_project(project)
    return project


# ------------------------------------------------------------------ the constraint


async def test_the_database_itself_refuses_a_second_activation_at_one_sequence() -> None:
    """Bypasses ``PostgresStore`` entirely: PostgreSQL is the one that has to say no.

    Two writers appending "the next number" is not a hypothetical for this table — it is
    what a promotion and a concurrent rollback of the same family look like — and a
    uniqueness rule that lived only in a pre-check would pass every test in the suite right
    up to that moment and then let both rows land.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    sessions = create_session_factory(engine)
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"

    def raw_row(suffix: str, *, sequence: int, family_key: str) -> RouterActivationRow:
        return RouterActivationRow(
            id=new_id("router_activation"),
            workspace_id=workspace_id,
            project_id=None,
            scope=RouterScope.TEAM_WORKSPACE.value,
            family_key=family_key,
            sequence=sequence,
            kind=RouterActivationKind.PROMOTE.value,
            router_version_id=f"rmv_{marker}_{suffix}",
            previous_version_id=None,
            rollback_target_version_id=None,
            promotion_report_id=None,
            supersedes_contract_id=None,
            content_hash=digest(f"content-{marker}-{suffix}"),
            schema_version="1.0.0",
            payload={"marker": suffix},
            created_at=datetime.now(UTC),
        )

    try:
        async with sessions.begin() as session:
            session.add(raw_row("first", sequence=1, family_key=f"family-{marker}"))

        with pytest.raises(IntegrityError, match="uq_router_activations_sequence"):
            async with sessions.begin() as session:
                session.add(raw_row("second", sequence=1, family_key=f"family-{marker}"))

        # The partition is the whole key: a second family, and the next number in the
        # first one, are both ordinary appends.
        async with sessions.begin() as session:
            session.add(raw_row("other-family", sequence=1, family_key=f"other-{marker}"))
        async with sessions.begin() as session:
            session.add(raw_row("next", sequence=2, family_key=f"family-{marker}"))
    finally:
        await engine.dispose()


async def test_the_delta_left_the_two_m0_partial_indexes_exactly_as_they_were() -> None:
    """M8.1's migration 0019 retires them; 0018 must not have touched them.

    A database sitting between the two revisions has to satisfy the old rule and the new
    one at once, which is the only ordering under which each migration is independently
    reversible — and the only reason it is safe to add the ledger before removing what it
    replaces.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)

    def partial_indexes(connection: sa.Connection) -> dict[str, str]:
        rows = connection.execute(
            sa.text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = 'router_model_versions'"
            )
        )
        return {name: definition for name, definition in rows}

    try:
        async with engine.begin() as connection:
            found = await connection.run_sync(partial_indexes)
    finally:
        await engine.dispose()

    workspace = found["uq_router_versions_active_workspace"]
    assert "UNIQUE INDEX" in workspace
    assert "WHERE" in workspace and "ACTIVE" in workspace and "TEAM_WORKSPACE" in workspace

    adapter = found["uq_router_versions_active_project_adapter"]
    assert "UNIQUE INDEX" in adapter
    assert "WHERE" in adapter and "ACTIVE" in adapter and "PROJECT_ADAPTER" in adapter

    assert RouterModelVersionRow.__tablename__ not in V04_FREEZE_DELTA_TABLES


# ------------------------------------------------------------------ backend parity


async def test_both_backends_refuse_a_conflicting_re_put_with_the_same_words(
    tmp_path: Path,
) -> None:
    """Parity of the *refusal*, not only of the acceptance.

    ``MemoryStore`` is a stand-in for the database in several hundred tests, and a
    stand-in whose exception text differs is one that lets a caller write an
    ``except ValueError`` matcher that passes in the unit suite and misses in production.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    postgres = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        project = await setup_project(postgres, tmp_path, marker)
        await memory.create_project(project)

        rollout = build(
            ShadowRolloutResult,
            project_id=project.project_id,
            workspace_id=workspace_id,
            shadow_decision_id=f"shd_{marker}",
        )
        rewritten = build(
            ShadowRolloutResult,
            contract_id=rollout.contract_id,
            project_id=project.project_id,
            workspace_id=workspace_id,
            shadow_decision_id=f"shd_{marker}",
            fork_execution_id=f"rtc_{marker}_other",
        )

        for store in (postgres, memory):
            assert await store.put_shadow_rollout_result(rollout) == rollout

        failures: list[str] = []
        for store in (postgres, memory):
            with pytest.raises(ValueError) as error:
                await store.put_shadow_rollout_result(rewritten)
            failures.append(str(error.value))

        assert failures[0] == failures[1], failures
        assert "shadow rollout result" in failures[0]
        assert "is immutable" in failures[0]

        # The refusal left both stores usable and holding the original document.
        assert await postgres.get_shadow_rollout_result(rollout.contract_id) == rollout
        assert await memory.get_shadow_rollout_result(rollout.contract_id) == rollout
    finally:
        await engine.dispose()


async def test_both_backends_return_one_pair_and_one_ledger_in_the_same_order(
    tmp_path: Path,
) -> None:
    """Same objects, same order, on the two tables the delta added."""

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    postgres = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    base = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    try:
        project = await setup_project(postgres, tmp_path, marker)
        await memory.create_project(project)

        rollouts = [
            build(
                ShadowRolloutResult,
                project_id=project.project_id,
                workspace_id=workspace_id,
                shadow_decision_id=f"shd_{marker}",
                kind=kind.value,
                fork_execution_id=f"rtc_{marker}_{kind.value.lower()}",
                # Both arms sealed in the same minute on purpose: the ``(created_at, id)``
                # tie-break is the half of the ordering rule a clock cannot demonstrate.
                created_at=(base + timedelta(minutes=index // 2)).isoformat(),
            )
            for index, kind in enumerate((ShadowRolloutKind.SHADOW, ShadowRolloutKind.CONTROL))
        ]
        versions = [f"rmv_{marker}_{index}" for index in range(3)]
        ledger = [
            build(
                RouterActivation,
                workspace_id=workspace_id,
                family_key=f"family-{marker}",
                sequence=index + 1,
                router_version_id=versions[index],
                previous_version_id=None if index == 0 else versions[index - 1],
                created_at=(base + timedelta(days=index)).isoformat(),
            )
            for index in range(3)
        ]

        for store in (postgres, memory):
            for rollout in rollouts:
                await store.put_shadow_rollout_result(rollout)
            for entry in ledger:
                await store.put_router_activation(entry)

        from_postgres = (
            await postgres.list_shadow_rollout_results(workspace_id=workspace_id),
            await postgres.list_router_activations(workspace_id=workspace_id),
        )
        from_memory = (
            await memory.list_shadow_rollout_results(workspace_id=workspace_id),
            await memory.list_router_activations(workspace_id=workspace_id),
        )

        assert from_postgres == from_memory
        assert len(from_postgres[0]) == 2
        assert [entry.sequence for entry in from_postgres[1]] == [1, 2, 3]
        assert [item.contract_id for item in from_postgres[0]] == sorted(
            rollout.contract_id for rollout in rollouts
        )
    finally:
        await engine.dispose()
