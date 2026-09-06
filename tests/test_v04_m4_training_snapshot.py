"""The router training snapshot: what it includes, what it declines, and that it rebuilds.

SDD §10.1 makes a snapshot a *reproducibility* claim rather than a description, and every
test here is one way that claim is normally lost.

**Determinism has two halves and they need different witnesses.** Building twice from one
store must give the same document, which the first test checks through ``content_hash`` —
the strongest available statement, because the digest covers every field including the
derived ``contract_id`` and the injected ``created_at``. But a build that read the store in
whatever order the store returned would *also* pass that test, since one store returns one
order. So the second test builds from two stores that received the same records in
different orders, and — because both backends already list by ``(created_at, contract_id)``
— gives those records ids that run *opposite* to their timestamps. That is what makes the
lexicographic sort load-bearing: without it the manifest would come out in timestamp order,
which the test names and refuses.

**An inconclusive verdict is not a failure, and a snapshot must say so out loud.** Registry
§5.1 keeps ``INCONCLUSIVE`` distinct from ``FAIL`` on purpose. The third test requires an
included record whose run ended inconclusively to carry *no* final label rather than a
zero, and requires the snapshot to publish a non-zero tally of the records it dropped for
that reason — a snapshot that silently discarded them would look identical to one that
never saw them.

**Excluded means excluded.** The fourth test covers the two exclusions that are not about
labels at all: an open contradiction, and a projection whose underlying v0.2 experience was
retracted. The second is only checkable by dereference — ``ExperienceRecord`` declares no
``retracted`` field (ADR-054 b) — so a builder that trusted the projection alone would
train on retracted evidence and this test is what notices.

**Rebuild and compare.** The fifth test writes the snapshot to the append-only store, reads
it back, and materialises it: the digest recomputed from the store must equal the one the
snapshot published. That is the whole of §10.1 stated as an equality.

There is no ``conftest.py``. Each test calls a module-local ``setup_`` builder that returns
a fresh :class:`MemoryStore` and the records it holds, and every assertion is made against
what the store gives back rather than against the objects handed to it.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from accretion.contracts import PrincipalRef, Project, Provider, TaskType
from accretion.contracts.routing import (
    ContradictionStatus,
    ExperienceRecord,
    SnapshotSplit,
    VerificationState,
    Visibility,
)
from accretion.experience.models import (
    Experience,
    ExperienceEmbedding,
    ExperiencePolarity,
    ExperienceSourceKind,
    ExperienceTrust,
    TrajectorySegment,
    TrajectorySegmentKind,
)
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.features import Vocabulary
from accretion.routing.training_snapshot import (
    DEDUPLICATION_RULE,
    SnapshotBuilder,
    SnapshotRules,
    materialize,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"
RECORD_TEMPLATE: dict[str, Any] = json.loads(
    (FIXTURE_ROOT / "experience_record" / "complete.json").read_text(encoding="utf-8")
)
WORKSPACE_ID: str = RECORD_TEMPLATE["workspace_id"]
PROJECT_ID: str = RECORD_TEMPLATE["project_id"]
CREATED_BY = PrincipalRef.model_validate(RECORD_TEMPLATE["created_by"])

WINDOW = (
    datetime(2026, 1, 1, tzinfo=UTC),
    datetime(2026, 4, 1, tzinfo=UTC),
)
INSIDE_WINDOW = datetime(2026, 2, 1, 9, 0, tzinfo=UTC)
SPLIT = SnapshotSplit(
    training_project_ids=[PROJECT_ID],
    holdout_project_ids=["prj_FHYW7NW5TND9QGZTYTXBP73EME"],
)
VOCAB = Vocabulary.frozen_over(model_ids=["fake", "some-other-model"])
RULES = SnapshotRules.over(
    provider_version_boundaries={"CLAUDE": "<=claude-opus-4"},
    vocabulary=VOCAB,
)


def frozen_clock() -> datetime:
    """The one instant every snapshot in this file is stamped with.

    A snapshot's ``created_at`` is inside its ``content_hash``, so two builds can only be
    byte-identical if the clock is. Injecting it is what makes that possible; ``build``
    never reads a real clock.
    """

    return datetime(2026, 4, 1, 12, 0, tzinfo=UTC)


def digest_of(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def build_record(**overrides: Any) -> ExperienceRecord:
    """One committed ``ExperienceRecord`` fixture, re-sealed after this test's changes."""

    document = {**RECORD_TEMPLATE, **overrides}
    document.pop("content_hash", None)
    document.setdefault("created_at", INSIDE_WINDOW.isoformat())
    if "contract_id" not in overrides:
        document["contract_id"] = new_id("experience")
    document.setdefault(
        "source_node_execution_id", document["contract_id"].replace("exp_", "run_")
    )
    return ExperienceRecord.model_validate(document)


async def seed_experience(
    store: MemoryStore,
    experience_id: str,
    *,
    retracted: bool = False,
    revision: int = 1,
) -> None:
    """The v0.2 P7 experience an ``ExperienceRecord`` of that id projects (ADR-054 b).

    ``experience_records.id`` *is* the foreign key into ``experiences``, so a projection of
    an experience that was never captured is a record of nothing — and ``retracted`` and
    ``revision``, which the projection does not declare, live only here.
    """

    experience = Experience(
        experience_id=experience_id,
        project_id=PROJECT_ID,
        repository_identity=digest_of(PROJECT_ID),
        task_id=new_id("task"),
        task_type=TaskType.IMPLEMENT,
        task_family="python-service",
        source_kind=ExperienceSourceKind.RUN,
        source_run_id=new_id("run"),
        source_commit="b" * 40,
        architecture_version="2.0",
        manifest_digest=digest_of(f"manifest-{experience_id}"),
        policy_digest=digest_of(f"policy-{experience_id}"),
        verifier_digest=digest_of(f"verifier-{experience_id}"),
        prompt_digest=digest_of(f"prompt-{experience_id}"),
        context_digest=digest_of(f"context-{experience_id}"),
        tool_profile_digest=digest_of(f"tools-{experience_id}"),
        provider=Provider.FAKE,
        runtime_model="fake",
        runtime_version="test",
        trust=ExperienceTrust.HIGH,
        polarity=ExperiencePolarity.POSITIVE,
        outcome="VERIFIED_SUCCESS",
        content_digest=digest_of(f"experience-{experience_id}"),
        revision=revision,
        retracted=retracted,
    )
    segment = TrajectorySegment(
        segment_id=new_id("trajectory_segment"),
        experience_id=experience_id,
        ordinal=1,
        kind=TrajectorySegmentKind.WORKFLOW_PATH,
        content={"nodes": ["plan", "act", "verify"]},
        content_digest=digest_of(f"segment-{experience_id}"),
    )
    embedding = ExperienceEmbedding(
        embedding_id=new_id("experience_embedding"),
        experience_id=experience_id,
        input_digest=digest_of(f"embedding-{experience_id}"),
        vector=[1.0] + [0.0] * 383,
    )
    await store.save_experience(experience, (segment,), embedding)


async def setup_store(
    records: list[ExperienceRecord],
    *,
    retracted_ids: frozenset[str] = frozenset(),
    revisions: dict[str, int] | None = None,
) -> MemoryStore:
    """A fresh store holding the project, the P7 experiences and the projections, in order.

    ``records`` is written in the order given, which is what the shuffling test varies.
    ``retracted_ids`` and ``revisions`` set the two fields the projection does not declare
    and the builder must therefore dereference (ADR-054 b).
    """

    store = MemoryStore()
    await store.create_project(
        Project(
            project_id=PROJECT_ID,
            name="v0.4 M4 training snapshot",
            repository_path=Path("/tmp/accretion-v04-m4"),
        )
    )
    for record in records:
        await seed_experience(
            store,
            record.contract_id,
            retracted=record.contract_id in retracted_ids,
            revision=(revisions or {}).get(record.contract_id, 1),
        )
        await store.put_experience_record(record)
    return store


def setup_time_reversed_records(count: int) -> list[ExperienceRecord]:
    """Records whose ids run opposite to their timestamps, so sorting is not a no-op.

    Both store backends list a v0.4 table by ``(created_at, contract_id)``. If the ids
    happened to ascend with the timestamps, a builder that dropped its lexicographic sort
    would still produce a sorted manifest and the test that is supposed to catch it would
    pass. Assigning the *latest* instant to the *lowest* id removes that coincidence.
    """

    ids = sorted(new_id("experience") for _ in range(count))
    return [
        build_record(
            contract_id=experience_id,
            created_at=(INSIDE_WINDOW + timedelta(days=count - position)).isoformat(),
            configuration_hash=digest_of(f"configuration-{position}"),
        )
        for position, experience_id in enumerate(ids)
    ]


async def build_snapshot(store: MemoryStore, *, rules: SnapshotRules = RULES) -> Any:
    return await SnapshotBuilder(store).build(
        workspace_id=WORKSPACE_ID,
        window=WINDOW,
        split=SPLIT,
        rules=rules,
        created_by=CREATED_BY,
        clock=frozen_clock,
    )


async def test_building_the_snapshot_twice_gives_the_same_content_hash() -> None:
    """The same store, window and rules produce one document, not two dated copies."""

    records = setup_time_reversed_records(4)
    store = await setup_store(records)

    first = await build_snapshot(store)
    second = await build_snapshot(store)

    assert first.content_hash == second.content_hash
    assert first.contract_id == second.contract_id
    assert first == second

    # The declared rules canonicalise their inputs: a set and a differently ordered list of
    # the same statuses are the same rules, so the same digest and the same derived id.
    assert (
        SnapshotRules.over(
            excluded_contradiction_statuses={ContradictionStatus.OPEN, ContradictionStatus.RESOLVED}
        ).digest()
        == SnapshotRules.over(
            excluded_contradiction_statuses=[ContradictionStatus.RESOLVED, ContradictionStatus.OPEN]
        ).digest()
    )

    # A rebuild under a live clock is the same id with a different body, which the
    # append-only store refuses: the precondition build() documents, pinned here.
    later = await SnapshotBuilder(store).build(
        workspace_id=WORKSPACE_ID,
        window=WINDOW,
        split=SPLIT,
        rules=RULES,
        created_by=CREATED_BY,
        clock=lambda: frozen_clock() + timedelta(days=1),
    )
    assert later.contract_id == first.contract_id
    assert later.content_hash != first.content_hash
    await store.put_router_training_snapshot(first)
    with pytest.raises(ValueError, match="is immutable"):
        await store.put_router_training_snapshot(later)

    # The id is *derived*, not minted, which is what lets a rebuild be the no-op the
    # append-only store already knows how to recognise rather than a second row.
    await store.put_router_training_snapshot(second)
    stored = await store.list_router_training_snapshots(workspace_id=WORKSPACE_ID)
    assert [snapshot.contract_id for snapshot in stored] == [first.contract_id]

    # And a snapshot cut under different declared rules is a different snapshot.
    other = await build_snapshot(
        store, rules=SnapshotRules.over(provider_version_boundaries={"CODEX": "<=codex-1.4"})
    )
    assert other.contract_id != first.contract_id

    # A closed window is not a closed set of records. A projection backfilled — or
    # re-projected after an attribution pass, which is what `DEDUPLICATION_RULE` exists
    # for — lands inside a window that was already cut, and the next cut sees different
    # evidence under the same workspace, window and rules. That cut is a *different*
    # snapshot: if the id ignored the evidence it would collide with `first` under
    # different content and the append-only store would refuse it as immutable, leaving
    # the operator unable to record the corrected evidence at all.
    late_arrival = build_record(configuration_hash=digest_of("backfilled"))
    await seed_experience(store, late_arrival.contract_id)
    await store.put_experience_record(late_arrival)

    third = await build_snapshot(store)

    assert late_arrival.contract_id in third.included_experience_ids
    assert third.labels["row_count"] == "5"
    assert third.labels["manifest_digest"] != first.labels["manifest_digest"]
    assert third.contract_id != first.contract_id
    await store.put_router_training_snapshot(third)
    stored_ids = [
        snapshot.contract_id
        for snapshot in await store.list_router_training_snapshots(workspace_id=WORKSPACE_ID)
    ]
    assert sorted(stored_ids) == sorted([first.contract_id, third.contract_id])


async def test_shuffled_experience_insertion_order_gives_the_same_manifest() -> None:
    """Two stores that received the same evidence in different orders name it identically."""

    records = setup_time_reversed_records(5)
    shuffler = random.Random(20260401)
    first_order = list(records)
    second_order = list(records)
    shuffler.shuffle(first_order)
    shuffler.shuffle(second_order)
    assert first_order != second_order

    first = await build_snapshot(await setup_store(first_order))
    store = await setup_store(second_order)
    second = await build_snapshot(store)

    assert first.labels["manifest_digest"] == second.labels["manifest_digest"]
    assert first.included_experience_ids == second.included_experience_ids
    assert first.content_hash == second.content_hash

    # The sort is doing real work: the store hands the records back in timestamp order,
    # which these ids deliberately contradict. A builder that kept the store's order would
    # publish that order instead, so the two assertions below are what a dropped sort fails.
    listed = [
        record.contract_id
        for record in await store.list_experience_records(workspace_id=WORKSPACE_ID)
    ]
    assert second.included_experience_ids == sorted(second.included_experience_ids)
    assert second.included_experience_ids != listed
    assert sorted(listed) == second.included_experience_ids


async def test_inconclusive_records_are_in_neither_label_set_and_are_tallied() -> None:
    """An inconclusive verdict yields no label at all, and the snapshot counts what it dropped."""

    passed = build_record(configuration_hash=digest_of("passed"))
    inconclusive_run = build_record(
        configuration_hash=digest_of("inconclusive-run"),
        final_run_status=VerificationState.INCONCLUSIVE.value,
    )
    inconclusive_locally = build_record(
        configuration_hash=digest_of("inconclusive-locally"),
        local_verification_status=VerificationState.INCONCLUSIVE.value,
        final_run_status=None,
        eligible_for_learning=False,
    )
    store = await setup_store([passed, inconclusive_run, inconclusive_locally])

    snapshot = await build_snapshot(store)

    # A locally inconclusive record is not evidence a router may learn from (ADR-048), so
    # it is excluded outright — and the snapshot says how many it excluded, because a
    # silent drop is indistinguishable from never having seen the record.
    assert snapshot.labels["excluded_inconclusive"] == "1"
    assert inconclusive_locally.contract_id not in snapshot.included_experience_ids
    assert sorted(snapshot.included_experience_ids) == sorted(
        [passed.contract_id, inconclusive_run.contract_id]
    )

    table = await materialize(snapshot, store, VOCAB)
    labels = {
        row.experience_id: (local, final)
        for row, local, final in zip(
            table.rows, table.labels_local, table.labels_final, strict=True
        )
    }
    assert labels[passed.contract_id] == (1.0, 1.0)
    # Locally verified, so the local label stands; the run ended inconclusively, so there
    # is no final label. Mapping INCONCLUSIVE onto FAIL would put a 0.0 here and teach the
    # router that "we could not tell" looks exactly like "it did not work".
    assert labels[inconclusive_run.contract_id] == (1.0, None)


async def test_open_contradictions_and_retracted_records_are_excluded() -> None:
    """An unresolved contradiction and a retracted experience are both refused, and counted."""

    kept = build_record(configuration_hash=digest_of("kept"))
    contradicted = build_record(
        configuration_hash=digest_of("contradicted"),
        contradiction_status=ContradictionStatus.OPEN.value,
        eligible_for_learning=False,
    )
    retracted = build_record(configuration_hash=digest_of("retracted"))
    outside_window = build_record(
        configuration_hash=digest_of("outside"),
        created_at=(WINDOW[1] + timedelta(days=1)).isoformat(),
    )
    # The two instants the half-open window turns on, which nothing else in this file
    # exercises: `window_start` is inside and `window_end` is not.
    on_lower_bound = build_record(
        configuration_hash=digest_of("on-lower-bound"),
        created_at=WINDOW[0].isoformat(),
    )
    on_boundary = build_record(
        configuration_hash=digest_of("on-boundary"),
        created_at=WINDOW[1].isoformat(),
    )
    store = await setup_store(
        [kept, contradicted, retracted, outside_window, on_lower_bound, on_boundary],
        retracted_ids=frozenset({retracted.contract_id}),
    )

    snapshot = await build_snapshot(store)

    assert snapshot.included_experience_ids == sorted(
        [kept.contract_id, on_lower_bound.contract_id]
    )
    assert snapshot.labels["excluded_open_contradictions"] == "1"
    assert snapshot.labels["row_count"] == "2"
    assert snapshot.excluded_contradiction_statuses == [ContradictionStatus.OPEN]
    assert snapshot.deduplication_rule == DEDUPLICATION_RULE

    # The retracted record is invisible to the projection itself — `ExperienceRecord`
    # declares no `retracted` field — so a builder that never dereferenced the v0.2
    # experience would have included it here.
    assert retracted.contract_id not in snapshot.included_experience_ids
    assert (await store.get_experience_record(retracted.contract_id)) is not None

    # The window is half-open, so a record written after `window_end` belongs to the next
    # snapshot rather than to both.
    assert outside_window.contract_id not in snapshot.included_experience_ids
    # And half-open at exactly the shared instant, which is the only place the claim can
    # actually fail: a record stamped `window_end` belongs to the *next* window alone. If
    # the bound were `<=` it would be evidence in both of two adjacent snapshots and the
    # same outcome would be weighted twice, while `window_start` — the closed end — must
    # stay included or the two windows would leave a gap instead of partitioning time.
    assert on_boundary.contract_id not in snapshot.included_experience_ids
    assert on_lower_bound.contract_id in snapshot.included_experience_ids


async def test_materialize_from_the_stored_snapshot_reproduces_the_manifest_digest() -> None:
    """Reading the snapshot back and rebuilding its rows recomputes the digest it published."""

    records = setup_time_reversed_records(4)
    # One record shares its (execution, configuration) key with another, so the
    # deduplication rule has something to resolve and `row_count` is not just `len(ids)`.
    duplicate = build_record(
        source_node_execution_id=records[0].source_node_execution_id,
        configuration_hash=records[0].configuration_hash,
        created_at=records[0].created_at.isoformat(),
    )
    # And one is shared at project scope only, so the permission proof has a genuine
    # minimum to find rather than one scope repeated five times.
    project_scoped = build_record(
        configuration_hash=digest_of("project-scoped"),
        visibility=Visibility.PROJECT.value,
        permission_provenance={
            **RECORD_TEMPLATE["permission_provenance"],
            "scope": Visibility.PROJECT.value,
        },
    )
    store = await setup_store(
        [*records, project_scoped, duplicate],
        # The *first* record is the higher revision — the same node execution
        # re-projected after an attribution pass — even though the duplicate row was
        # written later and carries the larger id. That opposition is deliberate: it is
        # what makes "highest revision" decide the collision rather than write order.
        revisions={records[0].contract_id: 2},
    )

    snapshot = await build_snapshot(store)
    await store.put_router_training_snapshot(snapshot)
    stored = await store.get_router_training_snapshot(snapshot.contract_id)
    assert stored is not None

    table = await materialize(stored, store, VOCAB)

    assert table.manifest_digest == stored.labels["manifest_digest"]
    assert len(table.rows) == int(stored.labels["row_count"]) == 5
    assert [row.experience_id for row in table.rows] == stored.included_experience_ids
    assert len(table.labels_local) == len(table.labels_final) == len(table.weights) == 5
    assert stored.labels["vocab_digest"] == VOCAB.digest()

    # Of the two records sharing a deduplication key, the higher revision is the one kept —
    # one node execution re-projected is one outcome, not two — and it wins even though the
    # other row was written later and sorts after it.
    assert records[0].contract_id in stored.included_experience_ids
    assert duplicate.contract_id not in stored.included_experience_ids
    assert duplicate.contract_id > records[0].contract_id

    # The permission proof is the *narrowest* scope every included record allows, not the
    # widest. Four of these records were shared at team scope and one only within its
    # project; a proof claiming TEAM_WORKSPACE here would be letting the four license the
    # sharing of the fifth.
    assert stored.permission_proof.scope is Visibility.PROJECT
    assert {record.visibility for record in await store.list_experience_records(
        workspace_id=WORKSPACE_ID
    )} == {Visibility.PROJECT, Visibility.TEAM_WORKSPACE}

    # A different vocabulary re-maps every categorical index, so materialization refuses it
    # rather than handing back rows that no longer mean what the snapshot says.
    with pytest.raises(ValueError, match="vocabulary"):
        await materialize(stored, store, Vocabulary.frozen_over(model_ids=["something-else"]))
