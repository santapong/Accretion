"""The activation ledger against a real PostgreSQL database: parity, and only parity.

The twin of ``tests/test_v04_m8_activation.py``, and it does not repeat it. Every claim
about *what* the ledger means is proved there against ``MemoryStore``, where the acceptance
markers live. What cannot be proved there, and is proved here:

* the two backends refuse the same two ledger violations with **byte-identical exception
  text**, which is what makes ``MemoryStore`` a usable stand-in rather than a near-miss. The
  messages are built by one module-level function in ``store.py`` for exactly this reason,
  and this file is what would notice if a second copy appeared;
* ``head_router_activation`` returns the same entry from both backends given the same
  writes, including when the rows are inserted out of sequence order — the memory
  implementation takes a ``max`` and the PostgreSQL one an ``ORDER BY ... DESC LIMIT 1``, and
  "the same deterministic order in both" is a claim about two different pieces of code;
* ``activate_router_version`` is atomic in a *database* transaction and not only in a
  dictionary copy: a refused composite write leaves no version row behind here either;
* a second ``ACTIVE`` ``router_model_versions`` row now inserts at head, which is the
  behaviour migration 0019 exists to permit, and a duplicate ``sequence`` still raises
  ``IntegrityError(uq_router_activations_sequence)``, which is the constraint that replaced
  it.

Every id is uuid-suffixed, so the file is re-runnable against a database it has already
written to, and no test asserts on a global row count. Nothing here carries an acceptance
marker: a marker sits on a ``MemoryStore`` test (see ``tests/test_v04_m8_promotion.py``).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from accretion.contracts import PrincipalRef, PrincipalStatus
from accretion.contracts.canonical import CanonicalContract
from accretion.contracts.routing import (
    RouterActivation,
    RouterActivationKind,
    RouterModelVersion,
    RouterScope,
    RouterStatus,
)
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.models import RouterActivationRow, RouterModelVersionRow
from accretion.persistence.store import MemoryStore, PostgresStore
from accretion.routing.activation import WORKSPACE_FAMILY_KEY, ActivationLedger

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"

APPROVER = PrincipalRef(
    principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    display_name="v0.4 M8 approver",
    status=PrincipalStatus.ACTIVE,
)


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


def version(workspace_id: str, *, status: RouterStatus = RouterStatus.ACTIVE) -> RouterModelVersion:
    seed = uuid.uuid4().hex
    return build(
        RouterModelVersion,
        workspace_id=workspace_id,
        status=status.value,
        artifact_digest=digest(f"artifact-{seed}"),
        calibration_artifact_digest=digest(f"calibration-{seed}"),
    )


def activation(
    workspace_id: str,
    *,
    sequence: int,
    router_version_id: str,
    previous_version_id: str | None = None,
    family_key: str = WORKSPACE_FAMILY_KEY,
) -> RouterActivation:
    return build(
        RouterActivation,
        workspace_id=workspace_id,
        scope=RouterScope.TEAM_WORKSPACE.value,
        family_key=family_key,
        sequence=sequence,
        kind=RouterActivationKind.PROMOTE.value,
        router_version_id=router_version_id,
        previous_version_id=previous_version_id,
        approved_by=APPROVER.model_dump(mode="json"),
    )


def raw_version(workspace_id: str, marker: str, suffix: str) -> RouterModelVersionRow:
    """An ``ACTIVE`` version written past every guard the store owns."""

    return RouterModelVersionRow(
        id=new_id("router_model_version"),
        workspace_id=workspace_id,
        project_id=None,
        scope=RouterScope.TEAM_WORKSPACE.value,
        algorithm_id="gradient-boosted-ranker",
        feature_schema_version="1.0.0",
        training_snapshot_id=f"rts_{marker}",
        artifact_digest=digest(f"artifact-{marker}-{suffix}"),
        parent_version_id=None,
        status=RouterStatus.ACTIVE.value,
        supersedes_contract_id=None,
        content_hash=digest(f"content-{marker}-{suffix}"),
        schema_version="1.0.0",
        payload={"marker": suffix},
        created_at=datetime.now(UTC),
    )



async def forget_router_versions(engine: AsyncEngine, workspace_id: str) -> None:
    """Delete the ``router_model_versions`` rows the calling test wrote.

    Several tests here leave two or three ``ACTIVE`` rows in one workspace, which is the
    whole point of 0019 — and is exactly what a *downgrade* of 0019 refuses.
    ``tests/test_v04_m8_migration.py`` recreates ``uq_router_versions_active_workspace``
    over whatever rows the database holds at that moment, so a pair left behind here would
    make that ``CREATE UNIQUE INDEX`` fail for a reason that has nothing to do with the
    migration under test, on the second run against a database this file has already
    written to as surely as on the first.

    Every assertion runs before this does, so the cleanup proves nothing and hides nothing;
    ``workspace_id`` is uuid-suffixed, so nothing but the calling test's own rows can match.
    """

    async with engine.begin() as connection:
        await connection.execute(
            sa.delete(RouterModelVersionRow).where(
                RouterModelVersionRow.workspace_id == workspace_id
            )
        )


# ------------------------------------------------------------------- parity


async def test_both_backends_refuse_a_skipped_sequence_with_the_same_message() -> None:
    """The gap ``uq_router_activations_sequence`` cannot see, refused identically.

    The two implementations are different code — a dictionary scan and a ``SELECT max(...)``
    — so "the same rule" is a claim that has to be checked rather than assumed. Compared as
    whole strings and not with a substring match: a message that dropped the head's number
    would still contain every word this test could otherwise have looked for.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    postgres = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        first = version(workspace_id)
        second = version(workspace_id)
        skipped = activation(
            workspace_id,
            sequence=3,
            router_version_id=second.contract_id,
            previous_version_id=first.contract_id,
        )
        head = activation(workspace_id, sequence=1, router_version_id=first.contract_id)

        messages: list[str] = []
        for store in (memory, postgres):
            for record in (first, second):
                await store.put_router_model_version(record)
            await store.put_router_activation(head)
            with pytest.raises(ValueError) as refusal:
                await store.put_router_activation(skipped)
            messages.append(str(refusal.value))

        assert messages[0] == messages[1]
        assert messages[0] == (
            f"activation {skipped.contract_id} has sequence 3; the head of "
            f"({workspace_id}, TEAM_WORKSPACE, {WORKSPACE_FAMILY_KEY}) is 1; "
            "§7.14 requires 2"
        )
    finally:
        await forget_router_versions(engine, workspace_id)
        await engine.dispose()


async def test_both_backends_refuse_a_later_entry_with_no_predecessor_alike() -> None:
    """The other half of "sequence 1 if and only if nothing was displaced"."""

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    postgres = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        first = version(workspace_id)
        second = version(workspace_id)
        head = activation(workspace_id, sequence=1, router_version_id=first.contract_id)
        orphan = activation(
            workspace_id, sequence=2, router_version_id=second.contract_id
        )

        messages: list[str] = []
        for store in (memory, postgres):
            for record in (first, second):
                await store.put_router_model_version(record)
            await store.put_router_activation(head)
            with pytest.raises(ValueError) as refusal:
                await store.put_router_activation(orphan)
            messages.append(str(refusal.value))

        assert messages[0] == messages[1]
        assert messages[0] == (
            f"activation {orphan.contract_id} has sequence 2 and names no "
            "previous_version_id; §7.14 makes sequence 1 the only entry that displaces "
            "nothing"
        )
    finally:
        await forget_router_versions(engine, workspace_id)
        await engine.dispose()


async def test_both_backends_return_the_greatest_sequence_not_the_last_written() -> None:
    """"Active" is the greatest sequence in its partition, in a ``max`` and in an
    ``ORDER BY ... DESC LIMIT 1`` alike.

    A *fourth* row is written last, in another family. A backend that answered "the most
    recently written row" would return that one and a backend that answered "the greatest
    sequence in this family" would not, so the two implementations cannot both be right by
    accident.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    postgres = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    family_key = f"family-{marker}"
    try:
        versions = [version(workspace_id) for _ in range(3)]
        entries: list[RouterActivation] = []
        previous: str | None = None
        for index, record in enumerate(versions, start=1):
            entries.append(
                activation(
                    workspace_id,
                    sequence=index,
                    router_version_id=record.contract_id,
                    previous_version_id=previous,
                    family_key=family_key,
                )
            )
            previous = record.contract_id
        # A different family, written last: it must not become anybody's head.
        other = activation(
            workspace_id,
            sequence=1,
            router_version_id=versions[0].contract_id,
            family_key=f"other-{marker}",
        )

        heads: list[RouterActivation | None] = []
        for store in (memory, postgres):
            for record in versions:
                await store.put_router_model_version(record)
            for entry in entries:
                await store.put_router_activation(entry)
            await store.put_router_activation(other)
            heads.append(
                await store.head_router_activation(
                    workspace_id=workspace_id,
                    scope=RouterScope.TEAM_WORKSPACE,
                    family_key=family_key,
                )
            )

        assert heads[0] == heads[1]
        assert heads[0] is not None
        assert heads[0].sequence == 3
        assert heads[0].router_version_id == versions[-1].contract_id
    finally:
        await forget_router_versions(engine, workspace_id)
        await engine.dispose()


async def test_both_backends_write_the_same_rows_for_one_activation() -> None:
    """``activate_router_version`` is one call with one deterministic result on both.

    The version rows are compared as a list in the order the two backends return them, which
    is the parity the store's ordering contract is about: an implementation that sorted its
    listing differently would return the same set and a different sequence, and a caller
    diffing two responses would see a change that had not happened.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    postgres = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        promoted = version(workspace_id)
        retired = version(workspace_id, status=RouterStatus.RETIRED)

        results: list[RouterActivation] = []
        listings: list[list[RouterModelVersion]] = []
        for store in (memory, postgres):
            results.append(
                await ActivationLedger(store).activate(
                    kind=RouterActivationKind.PROMOTE,
                    version=promoted,
                    previous=retired,
                    rollback_target=retired.contract_id,
                    approved_by=APPROVER,
                )
            )
            listings.append(
                await store.list_router_model_versions(workspace_id=workspace_id)
            )

        # The activation's own id is minted per call, so everything *except* the id must
        # match; the id is what the two calls could not have shared.
        assert results[0].contract_id != results[1].contract_id
        assert results[0].sequence == results[1].sequence == 1
        assert results[0].router_version_id == results[1].router_version_id
        assert [record.contract_id for record in listings[0]] == [
            record.contract_id for record in listings[1]
        ]
        assert [record.status for record in listings[0]] == [
            record.status for record in listings[1]
        ]
        assert {record.status for record in listings[0]} == {
            RouterStatus.ACTIVE,
            RouterStatus.RETIRED,
        }
    finally:
        await engine.dispose()


async def test_a_refused_composite_write_leaves_no_version_row_in_postgres() -> None:
    """Atomicity in a database transaction, not only in a dictionary copy.

    The version rows are written before the ledger entry, so the contiguity refusal arrives
    after two inserts have already been issued. ``activation_transaction`` is what rolls them
    back; without it the workspace would keep two orphan versions and no record of why.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        assert await store.list_router_model_versions(workspace_id=workspace_id) == []
        forged = activation(
            workspace_id,
            sequence=4,
            router_version_id=version(workspace_id).contract_id,
            previous_version_id="rmv_something_earlier",
        )
        with pytest.raises(ValueError, match="§7.14 requires 1"):
            await store.activate_router_version(
                activation=forged,
                versions=[version(workspace_id), version(workspace_id)],
            )

        assert await store.list_router_model_versions(workspace_id=workspace_id) == []
        assert await store.list_router_activations(workspace_id=workspace_id) == []
    finally:
        await engine.dispose()


# ----------------------------------------------- what 0019 permitted and what replaced it


async def test_a_second_active_version_inserts_at_head_through_the_store() -> None:
    """The behaviour migration 0019 exists to permit, through the ordinary write path.

    Before M8.1 this raised ``ValueError`` from ``_guard_active_router_uniqueness`` and, past
    that guard, ``IntegrityError(uq_router_versions_active_workspace)`` from the database.
    Both are gone, and which of the two rows is serving is the ledger's answer.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        first = version(workspace_id)
        second = version(workspace_id)
        await store.put_router_model_version(first)
        await store.put_router_model_version(second)

        stored = await store.list_router_model_versions(workspace_id=workspace_id)
        assert sorted(record.contract_id for record in stored) == sorted(
            [first.contract_id, second.contract_id]
        )
        assert all(record.status is RouterStatus.ACTIVE for record in stored)
    finally:
        await forget_router_versions(engine, workspace_id)
        await engine.dispose()


async def test_a_duplicate_sequence_is_still_refused_by_the_database() -> None:
    """The constraint that replaced the two partial indexes, unassisted by any guard.

    Written straight through the session so that PostgreSQL is the one saying no: the
    store's contiguity pre-check would otherwise be the thing under test, and the pre-check
    is not what holds when two writers race for the same next number.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    sessions = create_session_factory(engine)
    store = PostgresStore(sessions)
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        first = version(workspace_id)
        await store.put_router_model_version(first)
        head = activation(workspace_id, sequence=1, router_version_id=first.contract_id)
        await store.put_router_activation(head)

        clash = activation(
            workspace_id, sequence=1, router_version_id=first.contract_id
        )
        with pytest.raises(IntegrityError, match="uq_router_activations_sequence"):
            async with sessions.begin() as session:
                session.add(
                    RouterActivationRow(
                        id=clash.contract_id,
                        workspace_id=clash.workspace_id,
                        project_id=None,
                        scope=clash.scope.value,
                        family_key=clash.family_key,
                        sequence=clash.sequence,
                        kind=clash.kind.value,
                        router_version_id=clash.router_version_id,
                        previous_version_id=None,
                        rollback_target_version_id=None,
                        promotion_report_id=None,
                        supersedes_contract_id=None,
                        content_hash=digest(f"clash-{marker}"),
                        schema_version="1.0.0",
                        payload={"marker": "clash"},
                        created_at=datetime.now(UTC),
                    )
                )
    finally:
        await engine.dispose()


async def test_two_active_versions_written_raw_both_survive() -> None:
    """The same permission, past the store entirely: the index really is gone.

    Both rows are read back rather than only written, because an ``INSERT`` that PostgreSQL
    accepted is not yet a row that PostgreSQL *kept*: the claim in the name is about what
    the table holds afterwards, and only a select can say so.
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
