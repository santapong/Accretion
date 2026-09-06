"""A trained candidate and its snapshot, against a real PostgreSQL database.

Two things can only be proved here. That the documents M4's trainer produces survive the
round trip through the real column types — a ``RouterTrainingSnapshot`` carries a list of
experience ids and a sealed split, a ``RouterModelVersion`` carries two digests and a label
map, and all of it lives in a JSON column that a dialect could reorder. And that
``PostgresStore`` and ``MemoryStore`` return *equal* documents in the *same order* for the
same writes, which is the property every reader of a version list depends on and which no
single-backend test can see.

The predictor is then assembled from the Postgres-backed store, because AC4-M4-016's refusal
must hold wherever the version is read from: a loader that gated correctly against a
dictionary and not against the database would leave the criterion true only in tests.

Every id is derived from a fresh uuid, so the file is re-runnable against a database it has
already written to, and no test asserts on a global row count. Nothing here carries an
acceptance marker: a criterion whose only claiming test skips without PostgreSQL would
classify ``SKIPPED_ONLY``, and the claim for AC4-M4-016 is made in
``tests/test_v04_m4_train.py`` against the memory store.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest
from test_v04_m4_train import TEST_CONFIG, Corpus, setup_corpus, train

from accretion.contracts.routing import RouterModelVersion
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import PostgresStore
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.train import (
    ACCEPTANCE_LABEL,
    CALIBRATION_REPORT_LABEL,
    LearnedPredictorLoader,
    RouterNotEvaluatedError,
    TrainedCandidate,
)

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]


async def setup_two_candidates(
    tmp_path: Path,
) -> tuple[Corpus, ArtifactStore, tuple[TrainedCandidate, TrainedCandidate]]:
    """One workspace, one snapshot, and two versions of the router fitted from it.

    Two versions and not one, because ordering is the thing under test and a single row is
    in order by construction. They share a ``created_at`` — the clock is frozen — so the
    list order is decided entirely by the ``contract_id`` tie-break, which is exactly the
    comparison that could differ between a Python sort and an ``ORDER BY``.
    """

    corpus = await setup_corpus()
    artifacts = ArtifactStore(tmp_path / "artifacts")
    first = await train(corpus, artifacts)
    second = await train(
        corpus, artifacts, config=replace(TEST_CONFIG, n_trees=TEST_CONFIG.n_trees + 1)
    )
    assert first.version.contract_id != second.version.contract_id
    assert first.snapshot.contract_id == second.snapshot.contract_id
    return corpus, artifacts, (first, second)


async def test_a_trained_version_and_its_snapshot_round_trip_through_postgres_unchanged(
    tmp_path: Path,
) -> None:
    """What the trainer sealed is what the database gives back, field for field."""

    assert POSTGRES_URL is not None
    corpus, _, (first, second) = await setup_two_candidates(tmp_path)
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    try:
        await store.put_router_training_snapshot(first.snapshot)
        # Written newest-id-first so that a backend returning insertion order would be
        # visible rather than lucky.
        for candidate in sorted(
            (first, second), key=lambda item: item.version.contract_id, reverse=True
        ):
            await store.put_router_model_version(candidate.version)

        snapshot = await store.get_router_training_snapshot(first.snapshot.contract_id)
        assert snapshot == first.snapshot
        assert snapshot is not None
        assert snapshot.included_experience_ids == first.snapshot.included_experience_ids
        assert snapshot.split == first.snapshot.split
        assert snapshot.labels == first.snapshot.labels

        version = await store.get_router_model_version(first.version.contract_id)
        assert version == first.version
        assert version is not None
        assert version.labels[ACCEPTANCE_LABEL] == first.holdout.digest()
        assert version.labels[CALIBRATION_REPORT_LABEL]
        assert version.artifact_digest == first.version.artifact_digest
    finally:
        await engine.dispose()


async def test_both_backends_list_the_same_versions_in_the_same_order(
    tmp_path: Path,
) -> None:
    """Memory and PostgreSQL agree on the documents and on their sequence.

    The workspace filter is checked in the same breath: a second workspace's versions must
    not appear, and both backends must agree about that too.
    """

    assert POSTGRES_URL is not None
    corpus, _, (first, second) = await setup_two_candidates(tmp_path)
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    try:
        await store.put_router_training_snapshot(first.snapshot)
        for candidate in sorted(
            (first, second), key=lambda item: item.version.contract_id, reverse=True
        ):
            await store.put_router_model_version(candidate.version)

        from_memory = await corpus.store.list_router_model_versions(
            workspace_id=corpus.workspace_id
        )
        from_postgres = await store.list_router_model_versions(
            workspace_id=corpus.workspace_id
        )
        assert from_postgres == from_memory
        assert [version.contract_id for version in from_postgres] == sorted(
            [first.version.contract_id, second.version.contract_id]
        )

        memory_snapshots = await corpus.store.list_router_training_snapshots(
            workspace_id=corpus.workspace_id
        )
        postgres_snapshots = await store.list_router_training_snapshots(
            workspace_id=corpus.workspace_id
        )
        assert postgres_snapshots == memory_snapshots

        assert (
            await store.list_router_model_versions(workspace_id=f"{corpus.workspace_id}-other")
            == []
        )
    finally:
        await engine.dispose()


async def test_the_same_version_written_twice_is_a_no_op_and_a_changed_one_is_refused(
    tmp_path: Path,
) -> None:
    """The append-only rule is the database's, not the store's politeness.

    Both backends must answer identically: a byte-identical re-put is the retry it looks
    like, and a different body under the same id is history being rewritten.
    """

    assert POSTGRES_URL is not None
    corpus, _, (first, _) = await setup_two_candidates(tmp_path)
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    try:
        await store.put_router_training_snapshot(first.snapshot)
        await store.put_router_model_version(first.version)
        assert await store.put_router_model_version(first.version) == first.version

        # Rebuilt through `model_validate` rather than `model_copy`, because a copy skips
        # validation and would leave the document carrying the *original* digest — the
        # store would then refuse it for the wrong reason and this test would prove nothing.
        document = first.version.model_dump(mode="json")
        document.pop("content_hash")
        document["labels"] = {**first.version.labels, "seed": "999"}
        edited = RouterModelVersion.model_validate(document)
        for backend in (store, corpus.store):
            with pytest.raises(ValueError, match="is immutable"):
                await backend.put_router_model_version(edited)
    finally:
        await engine.dispose()


async def test_a_predictor_loads_from_postgres_and_still_refuses_an_unevaluated_version(
    tmp_path: Path,
) -> None:
    """AC4-M4-016's refusal is about the record, so it holds against the real database too."""

    assert POSTGRES_URL is not None
    corpus, artifacts, (first, _) = await setup_two_candidates(tmp_path)
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    try:
        await store.put_router_training_snapshot(first.snapshot)
        await store.put_router_model_version(first.version)

        loader = LearnedPredictorLoader(store, artifacts)
        predictor = await loader.load(first.version.contract_id)
        assert predictor.version_id
        assert predictor.artifact.feature_schema_version == first.version.feature_schema_version

        document = first.version.model_dump(mode="json")
        document.pop("content_hash")
        document["contract_id"] = new_id("router_model_version")
        document["labels"] = {
            key: value
            for key, value in first.version.labels.items()
            if key != ACCEPTANCE_LABEL
        }
        stripped = RouterModelVersion.model_validate(document)
        await store.put_router_model_version(stripped)
        with pytest.raises(RouterNotEvaluatedError, match=ACCEPTANCE_LABEL):
            await loader.load(stripped.contract_id)
    finally:
        await engine.dispose()
