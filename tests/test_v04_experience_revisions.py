"""Revisions of an experience record, through ``MemoryStore`` (SDD §7.10, §9.6, ADR-054 b).

M0 keyed ``experience_records`` by making its primary key *be* its foreign key into
``experiences``. That is right about the first projection of an experience and wrong about
every one after it: §7.10 revises a record — a recomputed ``attribution`` (§9.6), a
contradiction moving ``OPEN`` → ``RESOLVED``, a ``final_run_status`` that only exists once
the run has finished — by writing a **new row** with its own derived ``contract_id`` and a
``supersedes_contract_id`` naming the row it replaces, because registry §17 forbids
rewriting a historical record in place. Under the M0 key those rows were unstorable: their
ids name no ``experiences`` row. Migration 0020 gives the table a separate
``experience_id``, and this file is the proof that the whole chain now coexists and is
still anchored to a real experience.

The contracts are built from the committed golden fixture rather than assembled here, for
the reason ``test_v04_m0_store.py`` gives: a hand-built record would prove the store can
round-trip whatever this file thinks a record looks like, not what the freeze froze.

Nothing here carries an acceptance marker: M3a's criteria are claimed by the milestone that
writes these revisions, not by the store that can hold them.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from accretion.contracts import Project, Provider, TaskType
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
from accretion.persistence.store import MemoryStore

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"
FIXTURE = json.loads(
    (FIXTURE_ROOT / "experience_record" / "minimal.json").read_text(encoding="utf-8")
)
WORKSPACE_ID: str = FIXTURE["workspace_id"]
PROJECT_ID: str = FIXTURE["project_id"]

# Fixed rather than "now", so that the ordering assertions below are about the column and
# never about how fast the test ran.
SEALED_AT = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def build[C: CanonicalContract](model: type[C], **overrides: Any) -> C:
    """One golden ``minimal.json``, re-sealed after whatever this test changed."""

    path = FIXTURE_ROOT / snake_case(model.__name__) / "minimal.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    document.update(overrides)
    document.pop("content_hash", None)
    if "contract_id" not in overrides and model.ID_KIND is not None:
        document["contract_id"] = new_id(model.ID_KIND)
    return model.model_validate(document)


async def seed_experience(store: MemoryStore, experience_id: str) -> None:
    """The v0.2 P7 experience every projection in a chain points at (ADR-054 b).

    Seeded exactly once per chain and not once per record, which is the whole point of the
    change under test: three projections, one experience.
    """

    experience = Experience(
        experience_id=experience_id,
        project_id=PROJECT_ID,
        repository_identity=digest(PROJECT_ID),
        task_id=new_id("task"),
        task_type=TaskType.IMPLEMENT,
        task_family="python-service",
        source_kind=ExperienceSourceKind.RUN,
        source_run_id=new_id("run"),
        source_commit="b" * 40,
        architecture_version="2.0",
        manifest_digest=digest(f"manifest-{experience_id}"),
        policy_digest=digest(f"policy-{experience_id}"),
        verifier_digest=digest(f"verifier-{experience_id}"),
        prompt_digest=digest(f"prompt-{experience_id}"),
        context_digest=digest(f"context-{experience_id}"),
        tool_profile_digest=digest(f"tools-{experience_id}"),
        provider=Provider.FAKE,
        runtime_model="fake",
        runtime_version="test",
        trust=ExperienceTrust.HIGH,
        polarity=ExperiencePolarity.POSITIVE,
        outcome="VERIFIED_SUCCESS",
        content_digest=digest(f"experience-{experience_id}"),
    )
    segment = TrajectorySegment(
        segment_id=new_id("trajectory_segment"),
        experience_id=experience_id,
        ordinal=1,
        kind=TrajectorySegmentKind.WORKFLOW_PATH,
        content={"nodes": ["plan", "act", "verify"]},
        content_digest=digest(f"segment-{experience_id}"),
    )
    embedding = ExperienceEmbedding(
        embedding_id=new_id("experience_embedding"),
        experience_id=experience_id,
        input_digest=digest(f"embedding-{experience_id}"),
        vector=[1.0] + [0.0] * 383,
    )
    await store.save_experience(experience, (segment,), embedding)


def root_record(experience_id: str) -> ExperienceRecord:
    """The first projection of an experience: its own id *is* the experience's id."""

    return build(
        ExperienceRecord,
        contract_id=experience_id,
        created_at=SEALED_AT.isoformat(),
    )


def revision(
    root: ExperienceRecord,
    *,
    supersedes: ExperienceRecord,
    label: str,
    minutes: int,
    **overrides: Any,
) -> ExperienceRecord:
    """A §7.10 revision: a new row, a derived id, and a pointer at what it replaces.

    ``derived_id`` and not ``new_id`` because a revision must be re-derivable — the same
    recomputation of the same experience is the same revision, and a fresh id per attempt
    would make an idempotent re-put impossible to express (ADR-055).
    """

    return build(
        ExperienceRecord,
        contract_id=derived_id("experience", root.contract_id, label),
        supersedes_contract_id=supersedes.contract_id,
        created_at=(SEALED_AT + timedelta(minutes=minutes)).isoformat(),
        **overrides,
    )


async def setup_chain() -> tuple[MemoryStore, str, ExperienceRecord]:
    """A store holding the project and the one experience, plus the root projection.

    Returned as a tuple rather than assembled inside each test because the project row and
    the experience row are the *precondition* of a v0.4 write — both keys are mirrored in
    ``MemoryStore`` — and not the subject of one.
    """

    store = MemoryStore()
    await store.create_project(
        Project(
            project_id=PROJECT_ID,
            name="v0.4 experience revisions",
            repository_path=Path("/tmp/accretion-v04-experience-revisions"),
        )
    )
    experience_id = new_id("experience")
    await seed_experience(store, experience_id)
    return store, experience_id, root_record(experience_id)


async def test_a_root_projection_is_filed_under_its_own_id_as_its_experience() -> None:
    """The M0 behaviour, kept: no ``experience_id`` argument means "I am the root"."""

    store, experience_id, root = await setup_chain()

    await store.put_experience_record(root)

    assert await store.list_experience_record_revisions(
        experience_id, workspace_id=WORKSPACE_ID
    ) == [root]


async def test_a_revision_is_a_second_row_under_the_same_experience() -> None:
    """§9.6: recomputed attribution is a new row, not an edit of the old one."""

    store, experience_id, root = await setup_chain()
    await store.put_experience_record(root)
    second = revision(
        root,
        supersedes=root,
        label="attribution-2",
        minutes=30,
        attribution={"confidence": 0.9, "method_version": "dependency-heuristic-v2"},
    )

    await store.put_experience_record(second, experience_id=experience_id)

    stored = await store.get_experience_record(second.contract_id)
    assert stored is not None
    assert stored.supersedes_contract_id == root.contract_id
    assert stored.contract_id != root.contract_id
    # The row it supersedes is untouched: history is added to, never rewritten.
    assert await store.get_experience_record(root.contract_id) == root


async def test_a_revision_is_not_filed_under_its_own_derived_id() -> None:
    """The whole point of the split: the id that tells rows apart is not the parent key.

    Were ``experience_id`` still derived from ``contract_id`` for a revision, this listing
    would return the revision and the chain would have split into two one-record chains.
    """

    store, experience_id, root = await setup_chain()
    await store.put_experience_record(root)
    second = revision(root, supersedes=root, label="attribution-2", minutes=30)
    await store.put_experience_record(second, experience_id=experience_id)

    assert (
        await store.list_experience_record_revisions(
            second.contract_id, workspace_id=WORKSPACE_ID
        )
        == []
    )


async def test_both_the_root_and_its_revision_are_ordinary_rows_in_the_table() -> None:
    """``list_experience_records`` is unchanged and now returns every generation."""

    store, experience_id, root = await setup_chain()
    await store.put_experience_record(root)
    second = revision(root, supersedes=root, label="attribution-2", minutes=30)
    await store.put_experience_record(second, experience_id=experience_id)

    assert await store.list_experience_records(workspace_id=WORKSPACE_ID) == [
        root,
        second,
    ]
    assert await store.list_experience_records(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID
    ) == [root, second]


async def test_list_experience_records_returns_revisions_that_name_no_experience_row() -> None:
    """The hazard this PR hands to M3a.2, pinned as an assertion rather than as prose.

    ``SnapshotBuilder.build`` (``src/accretion/routing/training_snapshot.py``) is the only
    production consumer of ``list_experience_records``, and it resolves each record's P7
    experience with ``get_experience(record.contract_id)``. That lookup was total under the
    M0 key, where a record's id *was* its experience's id; migration 0020 ends it. A
    revision is an ordinary row in the listing — the test above says so — while its derived
    ``contract_id`` names no ``experiences`` row, so the builder reads ``None``, follows the
    ADR-054 b rule that a projection whose experience is gone is a record of nothing, and
    drops the revision while the superseded root stays eligible. Silently: no error, no
    counter. Nothing in this PR can fix that, because ``src/accretion/routing/**`` is not
    its surface; the two assertions below are the two halves of the hazard, so that M3a.2 —
    the milestone that first writes a §9.6 recomputed-attribution revision — cannot do so
    without first teaching the builder to resolve the experience through ``experience_id``.
    """

    store, experience_id, root = await setup_chain()
    await store.put_experience_record(root)
    second = revision(root, supersedes=root, label="attribution-2", minutes=30)
    await store.put_experience_record(second, experience_id=experience_id)

    listed = await store.list_experience_records(workspace_id=WORKSPACE_ID)

    # Half one: the revision is handed to the consumer...
    assert second in listed
    # ...half two: and the id the consumer resolves it by is not an experience.
    assert await store.get_experience(second.contract_id) is None
    # The superseded root still resolves, which is why dropping the revision is invisible
    # rather than emptying the snapshot: the stale generation survives the fresh one.
    assert await store.get_experience(root.contract_id) is not None
    assert await store.list_experience_record_revisions(
        experience_id, workspace_id=WORKSPACE_ID
    ) == [root, second]


async def test_the_revision_chain_is_ordered_by_when_it_was_sealed_not_by_id() -> None:
    """``(created_at, contract_id)`` — the sealing order, with the id only as a tie-break.

    The two revisions are given clocks that run *opposite* to their derived ids, and the
    test asserts that fact before asserting the order, so this cannot pass by luck: a
    listing that sorted by ``contract_id`` alone would return the newest revision in the
    middle. A derived id is a digest and carries no time, which is exactly why it cannot be
    the ordering key for a history.
    """

    store, experience_id, root = await setup_chain()
    lower, higher = sorted(
        (
            derived_id("experience", root.contract_id, "contradiction-resolved"),
            derived_id("experience", root.contract_id, "final-status"),
        )
    )
    # The earlier revision is given the *higher* id, so time order and id order disagree.
    second = build(
        ExperienceRecord,
        contract_id=higher,
        supersedes_contract_id=root.contract_id,
        created_at=(SEALED_AT + timedelta(minutes=30)).isoformat(),
        contradiction_status=ContradictionStatus.RESOLVED.value,
    )
    third = build(
        ExperienceRecord,
        contract_id=lower,
        supersedes_contract_id=second.contract_id,
        created_at=(SEALED_AT + timedelta(minutes=60)).isoformat(),
        final_run_status="PASS",
    )
    # The precondition the assertion below depends on: the id order is not the time order.
    assert second.contract_id > third.contract_id

    await store.put_experience_record(root)
    await store.put_experience_record(third, experience_id=experience_id)
    await store.put_experience_record(second, experience_id=experience_id)

    chain = await store.list_experience_record_revisions(
        experience_id, workspace_id=WORKSPACE_ID
    )

    assert chain == [root, second, third]
    assert [record.contract_id for record in chain] != sorted(
        record.contract_id for record in chain
    )


async def test_a_revision_naming_an_experience_that_was_never_captured_is_refused() -> None:
    """The moved key is still a key: §13.1's RESTRICT mirror follows ``experience_id``.

    Dropping the mirror from ``MemoryStore`` would let a unit test store a projection of
    nothing that PostgreSQL would refuse — the exact divergence the mirror exists to
    prevent — so this is asserted on the message the two backends agree on.
    """

    store, experience_id, root = await setup_chain()
    await store.put_experience_record(root)
    orphan = revision(root, supersedes=root, label="attribution-2", minutes=30)
    absent = new_id("experience")

    with pytest.raises(ValueError, match="is not in experiences") as refusal:
        await store.put_experience_record(orphan, experience_id=absent)

    assert str(refusal.value) == (
        f"experience record {orphan.contract_id} names experience_id {absent}, "
        "which is not in experiences; the foreign key would refuse this row in PostgreSQL"
    )
    assert await store.get_experience_record(orphan.contract_id) is None
    assert await store.list_experience_record_revisions(
        experience_id, workspace_id=WORKSPACE_ID
    ) == [root]


async def test_writing_the_same_revision_twice_is_a_no_op() -> None:
    """A retried write is free: §13.1 immutability is not "no second attempt"."""

    store, experience_id, root = await setup_chain()
    await store.put_experience_record(root)
    second = revision(root, supersedes=root, label="attribution-2", minutes=30)

    first_write = await store.put_experience_record(second, experience_id=experience_id)
    second_write = await store.put_experience_record(second, experience_id=experience_id)

    assert first_write == second_write == second
    assert await store.list_experience_record_revisions(
        experience_id, workspace_id=WORKSPACE_ID
    ) == [root, second]


async def test_a_second_revision_reusing_an_id_with_a_different_body_is_refused() -> None:
    """Registry §17: a record filed under an id is what that id means, permanently."""

    store, experience_id, root = await setup_chain()
    await store.put_experience_record(root)
    second = revision(root, supersedes=root, label="attribution-2", minutes=30)
    await store.put_experience_record(second, experience_id=experience_id)
    forged = build(
        ExperienceRecord,
        contract_id=second.contract_id,
        supersedes_contract_id=root.contract_id,
        created_at=second.created_at.isoformat(),
        source_node_execution_id="run_FORGEDFORGEDFORGEDFORGED12",
    )

    with pytest.raises(ValueError, match="is immutable"):
        await store.put_experience_record(forged, experience_id=experience_id)

    assert await store.get_experience_record(second.contract_id) == second


async def test_refiling_a_stored_record_under_a_different_experience_is_refused() -> None:
    """The one drift the payload comparison cannot see, so the one it is told about.

    ``ExperienceRecord`` declares no field naming its experience, so a re-put that changed
    only ``experience_id`` is byte-identical as a document. Left unchecked it would be
    accepted as a no-op and the row would silently keep its first parent — a projection
    attributed to the wrong experience, with nothing anywhere recording that it happened.
    """

    store, experience_id, root = await setup_chain()
    await store.put_experience_record(root)
    second = revision(root, supersedes=root, label="attribution-2", minutes=30)
    await store.put_experience_record(second, experience_id=experience_id)
    other = new_id("experience")
    await seed_experience(store, other)

    with pytest.raises(ValueError, match="experience_id differs from the stored record"):
        await store.put_experience_record(second, experience_id=other)

    assert await store.list_experience_record_revisions(
        other, workspace_id=WORKSPACE_ID
    ) == []
    assert await store.list_experience_record_revisions(
        experience_id, workspace_id=WORKSPACE_ID
    ) == [root, second]


async def test_another_workspace_cannot_read_a_revision_chain() -> None:
    """Every ``list_`` in this family scopes on the workspace, this one included."""

    store, experience_id, root = await setup_chain()
    await store.put_experience_record(root)
    second = revision(root, supersedes=root, label="attribution-2", minutes=30)
    await store.put_experience_record(second, experience_id=experience_id)

    assert (
        await store.list_experience_record_revisions(
            experience_id, workspace_id="wks_0000000000000000000000000"
        )
        == []
    )
