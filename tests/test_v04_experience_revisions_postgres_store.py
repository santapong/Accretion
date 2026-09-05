"""Revisions of an experience record against a real PostgreSQL database.

The twin of ``tests/test_v04_experience_revisions.py``. Two things can only be proved here
and are proved here: that ``PostgresStore`` and ``MemoryStore`` return **equal** objects in
the **same order** for the same chain of revisions, and that the moved foreign key is a real
database constraint rather than Python politeness — checked by writing a projection of an
experience that does not exist and watching PostgreSQL refuse it.

The refusal is an ``IntegrityError`` here and a ``ValueError`` in ``MemoryStore``, which is
the established shape of every foreign key in this family (``project_id`` behaves the same
way): the database key is the rule, and the in-memory mirror exists so that a unit test
written against ``MemoryStore`` cannot accept a row the database would refuse. What the two
backends must agree on is *which writes are refused*, and that is what both files assert.

Every id is minted fresh, so the file is re-runnable against a database it has already
written to, and every row it writes is deleted in a ``finally``. Nothing here carries an
acceptance marker.
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

from accretion.contracts import Project, Provider, Run, RunState, Task, TaskEnvelope, TaskType
from accretion.contracts.canonical import CanonicalContract
from accretion.contracts.routing import ContradictionStatus, ExperienceRecord
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
from accretion.persistence.store import MemoryStore, PostgresStore

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"
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


def chain_for(
    project: Project, experience: Experience, workspace_id: str, marker: str
) -> tuple[ExperienceRecord, ExperienceRecord, ExperienceRecord]:
    """A root projection and two revisions of it, with clocks that run against their ids.

    The two revisions' ids are derived digests and carry no time, so they are assigned to
    the two clocks in *descending* id order. A listing that ordered by ``contract_id``
    alone would therefore return them the wrong way round, and the ordering assertions
    below could not pass by luck.
    """

    root = build(
        ExperienceRecord,
        contract_id=experience.experience_id,
        project_id=project.project_id,
        workspace_id=workspace_id,
        created_at=SEALED_AT.isoformat(),
    )
    lower, higher = sorted(
        (
            derived_id("experience", root.contract_id, f"contradiction-{marker}"),
            derived_id("experience", root.contract_id, f"final-status-{marker}"),
        )
    )
    second = build(
        ExperienceRecord,
        contract_id=higher,
        project_id=project.project_id,
        workspace_id=workspace_id,
        supersedes_contract_id=root.contract_id,
        created_at=(SEALED_AT + timedelta(minutes=30)).isoformat(),
        contradiction_status=ContradictionStatus.RESOLVED.value,
    )
    third = build(
        ExperienceRecord,
        contract_id=lower,
        project_id=project.project_id,
        workspace_id=workspace_id,
        supersedes_contract_id=second.contract_id,
        created_at=(SEALED_AT + timedelta(minutes=60)).isoformat(),
        final_run_status="PASS",
    )
    return root, second, third


async def mirror_experience(memory: MemoryStore, experience: Experience) -> None:
    """The same ``experiences`` row in memory, through the same public method.

    Written with ``save_experience`` rather than by reaching into ``MemoryStore``'s dicts,
    so that the mirror of the foreign key is being asked about a row the store itself
    accepted.
    """

    marker = experience.experience_id
    segment = TrajectorySegment(
        segment_id=new_id("trajectory_segment"),
        experience_id=marker,
        ordinal=1,
        kind=TrajectorySegmentKind.WORKFLOW_PATH,
        content={"nodes": ["plan", "act", "verify"]},
        content_digest=digest(f"segment-{marker}"),
    )
    embedding = ExperienceEmbedding(
        embedding_id=new_id("experience_embedding"),
        experience_id=marker,
        input_digest=digest(f"embedding-{marker}"),
        vector=[1.0] + [0.0] * 383,
    )
    await memory.save_experience(experience, (segment,), embedding)


async def seed_memory_twin(
    project: Project, experience: Experience, records: tuple[ExperienceRecord, ...]
) -> MemoryStore:
    """The same writes into ``MemoryStore``, so the two answers can be compared.

    The first record given is the root and is written with no explicit parent, exactly as
    a caller with one projection would write it; every later one is a revision and names
    the experience it still projects.
    """

    memory = MemoryStore()
    await memory.create_project(project)
    await mirror_experience(memory, experience)
    for index, record in enumerate(records):
        await memory.put_experience_record(
            record,
            experience_id=None if index == 0 else experience.experience_id,
        )
    return memory


async def discard_records(engine: Any, experience_id: str) -> None:
    """Leave the shared database as this module found it."""

    async with engine.begin() as connection:
        await connection.execute(
            sa.text("DELETE FROM experience_records WHERE experience_id = :id"),
            {"id": experience_id},
        )


async def test_a_revision_chain_round_trips_and_matches_the_memory_store(
    tmp_path: Path,
) -> None:
    """Same objects, same order, out of both backends — the parity claim, on a chain.

    Written in the order 3, 1, 2 so that the ordering under test is the ``created_at``
    column and not the order the rows were inserted in.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    experience: Experience | None = None
    try:
        project = await setup_project(store, tmp_path, marker)
        experience = await setup_experience(store, project, marker)
        root, second, third = chain_for(project, experience, workspace_id, marker)
        assert second.contract_id > third.contract_id

        await store.put_experience_record(third, experience_id=experience.experience_id)
        await store.put_experience_record(root)
        await store.put_experience_record(second, experience_id=experience.experience_id)
        memory = await seed_memory_twin(project, experience, (root, third, second))

        chain = await store.list_experience_record_revisions(
            experience.experience_id, workspace_id=workspace_id
        )

        assert chain == [root, second, third]
        assert chain == await memory.list_experience_record_revisions(
            experience.experience_id, workspace_id=workspace_id
        )
        assert await store.list_experience_records(workspace_id=workspace_id) == [
            root,
            second,
            third,
        ]
        assert await store.list_experience_records(
            workspace_id=workspace_id
        ) == await memory.list_experience_records(workspace_id=workspace_id)
        assert await store.get_experience_record(second.contract_id) == second
        # A revision is filed under its parent, never under its own derived id.
        assert (
            await store.list_experience_record_revisions(
                second.contract_id, workspace_id=workspace_id
            )
            == []
        )
    finally:
        if experience is not None:
            await discard_records(engine, experience.experience_id)
        await engine.dispose()


async def test_a_revision_naming_an_experience_that_was_never_captured_is_refused(
    tmp_path: Path,
) -> None:
    """The moved key, live: PostgreSQL refuses the row ``MemoryStore`` refuses in Python."""

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    experience: Experience | None = None
    try:
        project = await setup_project(store, tmp_path, marker)
        await memory.create_project(project)
        experience = await setup_experience(store, project, marker)
        root, second, _third = chain_for(project, experience, workspace_id, marker)
        await store.put_experience_record(root)
        absent = new_id("experience")

        with pytest.raises(IntegrityError, match="fk_experience_records_experience"):
            await store.put_experience_record(second, experience_id=absent)

        # The same write, refused by the in-memory mirror before it ever reaches a driver.
        await mirror_experience(memory, experience)
        await memory.put_experience_record(root)
        with pytest.raises(ValueError, match="is not in experiences"):
            await memory.put_experience_record(second, experience_id=absent)

        assert await memory.list_experience_record_revisions(
            experience.experience_id, workspace_id=workspace_id
        ) == [root]
    finally:
        if experience is not None:
            await discard_records(engine, experience.experience_id)
        await engine.dispose()


async def test_an_experience_with_a_revision_chain_cannot_be_deleted(
    tmp_path: Path,
) -> None:
    """§13.1's last bullet still holds after the key moved — for every generation.

    Under the old key only the root projection pinned the experience. Now each revision
    does, which is what "evidence deletion must not orphan provenance silently" has to mean
    once a projection can have more than one row.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    sessions = create_session_factory(engine)
    store = PostgresStore(sessions)
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    experience: Experience | None = None
    try:
        project = await setup_project(store, tmp_path, marker)
        experience = await setup_experience(store, project, marker)
        root, second, _third = chain_for(project, experience, workspace_id, marker)
        await store.put_experience_record(root)
        await store.put_experience_record(second, experience_id=experience.experience_id)

        with pytest.raises(IntegrityError):
            async with sessions.begin() as session:
                await session.execute(
                    sa.text("DELETE FROM experiences WHERE id = :id"),
                    {"id": experience.experience_id},
                )
    finally:
        if experience is not None:
            await discard_records(engine, experience.experience_id)
        await engine.dispose()


async def test_rewriting_a_stored_revision_is_refused_on_both_backends(
    tmp_path: Path,
) -> None:
    """A retry is free; an edit is not. §13.1 immutability, with the id reused."""

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    experience: Experience | None = None
    try:
        project = await setup_project(store, tmp_path, marker)
        experience = await setup_experience(store, project, marker)
        root, second, _third = chain_for(project, experience, workspace_id, marker)
        await store.put_experience_record(root)
        await store.put_experience_record(second, experience_id=experience.experience_id)
        memory = await seed_memory_twin(project, experience, (root, second))

        # A byte-identical re-put is a no-op on both backends.
        assert (
            await store.put_experience_record(
                second, experience_id=experience.experience_id
            )
            == second
        )
        forged = build(
            ExperienceRecord,
            contract_id=second.contract_id,
            project_id=project.project_id,
            workspace_id=workspace_id,
            supersedes_contract_id=root.contract_id,
            created_at=second.created_at.isoformat(),
            source_node_execution_id=f"run_{marker.upper()}FORGED0000000",
        )

        with pytest.raises(ValueError, match="is immutable"):
            await store.put_experience_record(
                forged, experience_id=experience.experience_id
            )
        with pytest.raises(ValueError, match="is immutable"):
            await memory.put_experience_record(
                forged, experience_id=experience.experience_id
            )

        assert await store.get_experience_record(second.contract_id) == second
        assert await memory.get_experience_record(second.contract_id) == second
    finally:
        if experience is not None:
            await discard_records(engine, experience.experience_id)
        await engine.dispose()


async def test_refiling_a_stored_record_under_a_different_experience_is_refused(
    tmp_path: Path,
) -> None:
    """The drift the payload comparison cannot see, refused the same way on both backends.

    ``ExperienceRecord`` declares no field naming its experience, so this re-put is a
    byte-identical document. Left unchecked, both stores would treat it as a retry and the
    row would silently keep its first parent.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    experience: Experience | None = None
    other: Experience | None = None
    try:
        project = await setup_project(store, tmp_path, marker)
        experience = await setup_experience(store, project, marker)
        other = await setup_experience(store, project, f"{marker}-other")
        root, second, _third = chain_for(project, experience, workspace_id, marker)
        await store.put_experience_record(root)
        await store.put_experience_record(second, experience_id=experience.experience_id)
        memory = await seed_memory_twin(project, experience, (root, second))
        await mirror_experience(memory, other)

        for backend in (store, memory):
            with pytest.raises(
                ValueError, match="experience_id differs from the stored record"
            ):
                await backend.put_experience_record(
                    second, experience_id=other.experience_id
                )

        assert await store.list_experience_record_revisions(
            other.experience_id, workspace_id=workspace_id
        ) == []
        assert await store.list_experience_record_revisions(
            experience.experience_id, workspace_id=workspace_id
        ) == [root, second]
    finally:
        if experience is not None:
            await discard_records(engine, experience.experience_id)
        if other is not None:
            await discard_records(engine, other.experience_id)
        await engine.dispose()
