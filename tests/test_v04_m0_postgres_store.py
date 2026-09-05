"""The same append-only contract store against a real PostgreSQL database.

Two things can only be proved here and are proved here: that ``PostgresStore`` and
``MemoryStore`` return *equal* objects in the *same order* for the same writes; and that
the §13.1 constraints are real database constraints rather than Python politeness — checked
by writing rows that bypass the store's own guards entirely and watching the database refuse
them. The third — that migration 0017 survives an up/down/up cycle without touching
anything it did not create — is proved in ``tests/test_v04_m0_migration.py``, which is a
module of its own because it drops these tables mid-session; see the comment where those
tests used to be.

Every id is uuid-suffixed, so the file is re-runnable against a database it has already
written to, and no test asserts on a global row count. Nothing here carries an acceptance
marker: M0 claims no criterion (ADR-052).
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
from accretion.contracts.routing import (
    CompatibilityDecision,
    ConfigurationCandidate,
    ExperienceRecord,
    FailureEvent,
    IndependentVerificationResult,
    NodeContract,
    ObjectiveContract,
    RouterActivation,
    RouterModelVersion,
    RouterPromotionReport,
    RouterScope,
    RouterStatus,
    RouterTrainingSnapshot,
    RoutingContext,
    RoutingDecisionReceipt,
    ShadowDecision,
    ShadowRolloutResult,
    VerificationSpec,
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
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.models import (
    V04_M0_ROUTING_TABLES,
    ObjectiveContractRow,
    RouterModelVersionRow,
    RoutingReceiptRow,
)
from accretion.persistence.store import (
    ROUTING_OVERRIDE_DOCUMENT_TYPE,
    MemoryStore,
    PostgresStore,
)

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"

TABLE_CONTRACTS: dict[str, type[CanonicalContract]] = {
    "objective_contracts": ObjectiveContract,
    "node_contracts": NodeContract,
    "verification_specs": VerificationSpec,
    "routing_requests": RoutingContext,
    "configuration_candidates": ConfigurationCandidate,
    "compatibility_decisions": CompatibilityDecision,
    "routing_receipts": RoutingDecisionReceipt,
    "verification_results": IndependentVerificationResult,
    "experience_records": ExperienceRecord,
    "failure_events": FailureEvent,
    "router_model_versions": RouterModelVersion,
    "router_training_snapshots": RouterTrainingSnapshot,
    "router_promotion_reports": RouterPromotionReport,
    "shadow_decisions": ShadowDecision,
    # Added by the freeze delta (ADR-060, ADR-061). They join `TABLE_CONTRACTS` rather than
    # getting a twin file of their own so that the Memory/Postgres parity assertion below —
    # same objects, same order, same refusal — covers seventeen tables instead of fifteen.
    "shadow_rollout_results": ShadowRolloutResult,
    "router_activations": RouterActivation,
}


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def rescope(value: Any, *, workspace_id: str | None, project_id: str | None) -> Any:
    """Re-tenant a fixture document, nested contracts included.

    Several v0.4 contracts refuse a document whose *embedded* records were computed in a
    different workspace or project from the one the header claims — ``RoutingContext``
    checks it on ``TaskFeatures`` and ``ProjectFeatures`` explicitly, because a feature
    vector that crossed a tenancy boundary would be exactly the leak §10.1 exists to
    prevent. Rewriting only the top level would therefore produce a document that cannot
    be built, so this walks the whole tree. Every nested ``content_hash`` is dropped for
    the same reason the top-level one is: each record reseals itself over its new body.
    """

    if isinstance(value, dict):
        # Only a record that declares a `contract_type` seals its own digest. A registry
        # §4 reference such as `VerificationSpecRef` also has a field called
        # `content_hash`, but it is the *required* digest of something else and dropping
        # it would make the reference unbuildable rather than resealable.
        sealed = "contract_type" in value
        rewritten: dict[str, Any] = {}
        for key, item in value.items():
            if key == "content_hash" and sealed:
                continue
            if key == "workspace_id" and workspace_id is not None:
                rewritten[key] = workspace_id
            elif key == "project_id" and project_id is not None and isinstance(item, str):
                rewritten[key] = project_id
            else:
                rewritten[key] = rescope(
                    item, workspace_id=workspace_id, project_id=project_id
                )
        return rewritten
    if isinstance(value, list):
        return [
            rescope(item, workspace_id=workspace_id, project_id=project_id) for item in value
        ]
    return value


def build[C: CanonicalContract](model: type[C], **overrides: Any) -> C:
    """One golden ``minimal.json``, re-tenanted to this run's ids and re-sealed."""

    path = FIXTURE_ROOT / snake_case(model.__name__) / "minimal.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    document = rescope(
        document,
        workspace_id=overrides.pop("workspace_id", None),
        project_id=overrides.pop("project_id", None),
    )
    document.update(overrides)
    document.pop("content_hash", None)
    if "contract_id" not in overrides and model.ID_KIND is not None:
        document["contract_id"] = new_id(model.ID_KIND)
    return model.model_validate(document)


def table_names(connection: sa.Connection) -> set[str]:
    return set(sa.inspect(connection).get_table_names())


async def setup_project(store: PostgresStore, tmp_path: Path, marker: str) -> Project:
    """A real ``projects`` row, because every v0.4 table has a RESTRICT key into it."""

    project = Project(
        project_id=new_id("project"), name=f"v0.4 M0 {marker}", repository_path=tmp_path
    )
    await store.create_project(project)
    return project


async def setup_experience(store: PostgresStore, project: Project, marker: str) -> Experience:
    """A real ``experiences`` row: ``experience_records.id`` is a key into it (ADR-054 b)."""

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


def contracts_for(
    project: Project, experience: Experience, workspace_id: str, marker: str
) -> dict[str, CanonicalContract]:
    """One contract per table, all scoped to ids this run minted.

    ``routing_request_id`` is re-minted per run and not left at the fixture's value,
    because it is the one field in this family with a UNIQUE column of its own
    (§13.1, §8.2). A fixture value would make the *second* run of this test against the
    same database a second receipt for a routing request that already has one — the file
    would look re-runnable and would not be, and the failure would arrive as a §13.1
    violation in a test that is not about §13.1. The candidate is given the same value so
    that the two records still answer the same request.
    """

    routing_request_id = f"rrq_{marker}"
    records: dict[str, CanonicalContract] = {}
    for table, model in TABLE_CONTRACTS.items():
        overrides: dict[str, Any] = {"workspace_id": workspace_id}
        if model.PROJECT_SCOPED:
            overrides["project_id"] = project.project_id
        if table == "experience_records":
            overrides["contract_id"] = experience.experience_id
        if table in {"routing_receipts", "configuration_candidates"}:
            overrides["routing_request_id"] = routing_request_id
        records[table] = build(model, **overrides)
    return records


# ------------------------------------------------------------------ the indexes
# The two migration tests that used to open this file now live in
# ``tests/test_v04_m0_migration.py``. They call ``migration.downgrade()`` against the live
# database, which drops all seventeen tables mid-session, and correctness here depended on
# their being collected first in this file — true for a whole-file run and false for a
# ``-k`` selection that picks one of them plus a later test, and false again for any future
# module that writes v0.4 rows and sorts before this one. In their own module nothing can
# be interleaved between the drop and the ``finally: upgrade`` that restores it.


async def test_the_two_partial_unique_indexes_exist_in_the_live_database() -> None:
    """§13.1's conditional rules, as PostgreSQL actually holds them."""

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


# ------------------------------------------------------------------ round trip


async def test_every_v04_contract_round_trips_through_postgres_unchanged(
    tmp_path: Path,
) -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        project = await setup_project(store, tmp_path, marker)
        experience = await setup_experience(store, project, marker)
        records = contracts_for(project, experience, workspace_id, marker)

        for table, record in records.items():
            assert await getattr(store, f"put_{table[:-1]}")(record) == record

        for table, record in records.items():
            read_back = await getattr(store, f"get_{table[:-1]}")(record.contract_id)
            assert read_back == record, table
            assert read_back.content_hash == record.content_hash
            listed = await getattr(store, f"list_{table}")(workspace_id=workspace_id)
            assert listed == [record], table
    finally:
        await engine.dispose()


async def test_postgres_and_memory_agree_on_content_and_on_order(tmp_path: Path) -> None:
    """The parity claim, made on real writes rather than on signatures alone."""

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    postgres = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    base = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    try:
        project = await setup_project(postgres, tmp_path, marker)
        # The same project in both backends: ``MemoryStore`` mirrors
        # ``project_id -> projects.id``, so a twin with no project row would refuse every
        # record PostgreSQL accepts — which is the divergence this test exists to deny.
        await memory.create_project(project)
        written = [
            build(
                ObjectiveContract,
                project_id=project.project_id,
                workspace_id=workspace_id,
                goal=f"Ordering probe {index}.",
                created_at=(base + timedelta(minutes=index % 2)).isoformat(),
            )
            for index in range(4)
        ]
        for record in written:
            await postgres.put_objective_contract(record)
            await memory.put_objective_contract(record)

        from_postgres = await postgres.list_objective_contracts(workspace_id=workspace_id)
        from_memory = await memory.list_objective_contracts(workspace_id=workspace_id)

        assert from_postgres == from_memory
        assert len(from_postgres) == 4
        assert [item.contract_id for item in from_postgres] == sorted(
            (item.contract_id for item in written),
            key=lambda contract_id: (
                next(item.created_at for item in written if item.contract_id == contract_id),
                contract_id,
            ),
        )
    finally:
        await engine.dispose()


async def test_postgres_refuses_a_rewrite_and_accepts_an_identical_repeat(
    tmp_path: Path,
) -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        project = await setup_project(store, tmp_path, marker)
        record = build(
            ObjectiveContract, project_id=project.project_id, workspace_id=workspace_id
        )
        await store.put_objective_contract(record)

        assert await store.put_objective_contract(record) == record
        tampered = build(
            ObjectiveContract,
            contract_id=record.contract_id,
            project_id=project.project_id,
            workspace_id=workspace_id,
            goal="Rewritten after the fact.",
        )
        with pytest.raises(ValueError, match="is immutable"):
            await store.put_objective_contract(tampered)

        assert await store.get_objective_contract(record.contract_id) == record
        assert await store.list_objective_contracts(workspace_id=workspace_id) == [record]
    finally:
        await engine.dispose()


async def test_postgres_stores_a_revision_as_a_second_row(tmp_path: Path) -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        project = await setup_project(store, tmp_path, marker)
        parent = build(
            ObjectiveContract,
            project_id=project.project_id,
            workspace_id=workspace_id,
            created_at=datetime(2026, 3, 1, 9, 0, tzinfo=UTC).isoformat(),
        )
        await store.put_objective_contract(parent)
        revision = build(
            ObjectiveContract,
            project_id=project.project_id,
            workspace_id=workspace_id,
            goal="Revised after review.",
            revision=2,
            supersedes_contract_id=parent.contract_id,
            created_at=datetime(2026, 3, 2, 9, 0, tzinfo=UTC).isoformat(),
        )
        await store.put_objective_contract(revision)

        listed = await store.list_objective_contracts(workspace_id=workspace_id)

        assert [item.contract_id for item in listed] == [
            parent.contract_id,
            revision.contract_id,
        ]
        assert await store.get_objective_contract(parent.contract_id) == parent
        # The link survives the round trip as a promoted column, not only in the payload.
        async with create_session_factory(engine)() as session:
            row = await session.get(ObjectiveContractRow, revision.contract_id)
            assert row is not None
            assert row.supersedes_contract_id == parent.contract_id
            assert row.revision == 2
    finally:
        await engine.dispose()


async def test_postgres_refuses_the_same_document_under_a_new_id(tmp_path: Path) -> None:
    """§13.1's hash/version uniqueness, through the store's pre-check."""

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        project = await setup_project(store, tmp_path, marker)
        record = build(
            ObjectiveContract, project_id=project.project_id, workspace_id=workspace_id
        )
        await store.put_objective_contract(record)
        forged = record.model_copy(update={"contract_id": new_id("objective_contract")})

        with pytest.raises(ValueError, match="is immutable"):
            await store.put_objective_contract(forged)

        assert await store.list_objective_contracts(workspace_id=workspace_id) == [record]
    finally:
        await engine.dispose()


async def test_a_receipt_is_unique_per_routing_request_in_the_database(
    tmp_path: Path,
) -> None:
    """§13.1 and §8.2, through the store, which must fail the same way ``MemoryStore`` does.

    The twin of ``test_a_second_receipt_for_one_routing_request_is_refused``. This used to
    assert ``IntegrityError``, which froze a divergence as intended behaviour: a caller
    handling the memory store's ``ValueError`` got a driver error out of a poisoned
    transaction here. The database constraint is still real and still the backstop — see
    ``test_the_database_itself_refuses_a_second_receipt_for_one_routing_request``, which
    bypasses the store entirely to prove it.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    sessions = create_session_factory(engine)
    store = PostgresStore(sessions)
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        project = await setup_project(store, tmp_path, marker)
        first = build(
            RoutingDecisionReceipt,
            project_id=project.project_id,
            workspace_id=workspace_id,
            routing_request_id=f"rrq_{marker}",
        )
        await store.put_routing_receipt(first)
        second = build(
            RoutingDecisionReceipt,
            project_id=project.project_id,
            workspace_id=workspace_id,
            routing_request_id=f"rrq_{marker}",
            policy_snapshot_id=f"pol_{marker}_second",
        )

        with pytest.raises(ValueError, match="already has receipt"):
            await store.put_routing_receipt(second)

        assert await store.get_routing_receipt_for_request(f"rrq_{marker}") == first
        assert await store.get_routing_receipt(second.contract_id) is None
        # The refusal left the session usable: an ``IntegrityError`` would have poisoned
        # the transaction, and the next write is how that shows up.
        third = build(
            RoutingDecisionReceipt,
            project_id=project.project_id,
            workspace_id=workspace_id,
            routing_request_id=f"rrq_{marker}_other",
        )
        assert await store.put_routing_receipt(third) == third
    finally:
        await engine.dispose()


async def test_the_database_itself_refuses_a_second_receipt_for_one_routing_request(
    tmp_path: Path,
) -> None:
    """Bypasses ``PostgresStore``'s Python guard, exactly as the index tests below do.

    The store pre-checks so that a caller gets a readable ``ValueError``; the UNIQUE column
    is what holds when two writers race, and a rule that lived only in the pre-check would
    look identical in every test until the day it mattered. So this writes rows straight
    through the session and asserts that PostgreSQL is the one saying no.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    sessions = create_session_factory(engine)
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"

    def raw_row(suffix: str, routing_request_id: str) -> RoutingReceiptRow:
        return RoutingReceiptRow(
            id=new_id("routing_receipt"),
            workspace_id=workspace_id,
            project_id=None,
            routing_request_id=routing_request_id,
            node_contract_hash=digest(f"node-{marker}"),
            selected_configuration_id=None,
            selected_configuration_hash=None,
            decision_type="MODEL",
            selection_propensity=None,
            workspace_router_version=f"rmv_{marker}",
            project_adapter_version=None,
            supersedes_contract_id=None,
            content_hash=digest(f"content-{marker}-{suffix}"),
            schema_version="1.0.0",
            payload={"marker": suffix},
            created_at=datetime.now(UTC),
        )

    try:
        async with sessions.begin() as session:
            session.add(raw_row("first", f"rrq_{marker}"))

        with pytest.raises(IntegrityError):
            async with sessions.begin() as session:
                session.add(raw_row("second", f"rrq_{marker}"))

        # A receipt for a *different* request is fine: the constraint is per request.
        async with sessions.begin() as session:
            session.add(raw_row("third", f"rrq_{marker}_other"))
    finally:
        await engine.dispose()


# ---------------------------------------------- the partial indexes, unassisted


async def test_the_database_itself_refuses_a_second_active_workspace_router(
    tmp_path: Path,
) -> None:
    """Bypasses ``PostgresStore``'s Python guard entirely.

    The store pre-checks so that a caller gets a readable ``ValueError``; the index is
    what holds when two writers race, and a rule that lived only in the pre-check would
    look identical in every test until the day it mattered. So this writes rows straight
    through the session and asserts that PostgreSQL is the one saying no.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    sessions = create_session_factory(engine)
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"

    def raw_row(suffix: str) -> RouterModelVersionRow:
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

    try:
        async with sessions.begin() as session:
            session.add(raw_row("first"))

        with pytest.raises(IntegrityError, match="uq_router_versions_active_workspace"):
            async with sessions.begin() as session:
                session.add(raw_row("second"))

        # A CANDIDATE beside the ACTIVE one is fine: the index is partial.
        async with sessions.begin() as session:
            candidate = raw_row("candidate")
            candidate.status = RouterStatus.CANDIDATE.value
            session.add(candidate)
    finally:
        await engine.dispose()


async def test_the_database_itself_refuses_a_second_active_project_adapter(
    tmp_path: Path,
) -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    sessions = create_session_factory(engine)
    store = PostgresStore(sessions)
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        project = await setup_project(store, tmp_path, marker)

        def raw_row(suffix: str, algorithm: str) -> RouterModelVersionRow:
            return RouterModelVersionRow(
                id=new_id("router_model_version"),
                workspace_id=workspace_id,
                project_id=project.project_id,
                scope=RouterScope.PROJECT_ADAPTER.value,
                algorithm_id=algorithm,
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

        async with sessions.begin() as session:
            session.add(raw_row("first", "gradient-boosted-ranker"))

        with pytest.raises(
            IntegrityError, match="uq_router_versions_active_project_adapter"
        ):
            async with sessions.begin() as session:
                session.add(raw_row("second", "gradient-boosted-ranker"))

        # A different algorithm for the same project is a comparison, not a conflict.
        async with sessions.begin() as session:
            session.add(raw_row("other-algorithm", "linear-thompson"))
    finally:
        await engine.dispose()


async def test_the_store_guard_and_the_index_agree_about_the_active_router(
    tmp_path: Path,
) -> None:
    """Through the store, the same rule surfaces as the ``MemoryStore`` message."""

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        active = build(
            RouterModelVersion,
            workspace_id=workspace_id,
            status="ACTIVE",
            artifact_digest=digest(f"active-{marker}"),
        )
        await store.put_router_model_version(active)

        with pytest.raises(ValueError, match="already has an ACTIVE workspace router"):
            await store.put_router_model_version(
                build(
                    RouterModelVersion,
                    workspace_id=workspace_id,
                    status="ACTIVE",
                    artifact_digest=digest(f"contender-{marker}"),
                )
            )

        assert await store.put_router_model_version(active) == active
        assert await store.list_router_model_versions(workspace_id=workspace_id) == [active]
    finally:
        await engine.dispose()


# ------------------------------------------------------------ retention keys


async def test_a_project_with_routing_provenance_cannot_be_deleted(tmp_path: Path) -> None:
    """§13.1's last bullet, live: ``RESTRICT`` and not ``CASCADE``.

    The delete is attempted directly against ``projects`` — there is no store method that
    could do it — because the claim under test is about the schema, not about the API.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    sessions = create_session_factory(engine)
    store = PostgresStore(sessions)
    marker = uuid.uuid4().hex[:12]
    try:
        project = await setup_project(store, tmp_path, marker)
        await store.put_objective_contract(
            build(
                ObjectiveContract,
                project_id=project.project_id,
                workspace_id=f"wks_{marker}",
            )
        )

        with pytest.raises(IntegrityError):
            async with sessions.begin() as session:
                await session.execute(
                    sa.text("DELETE FROM projects WHERE id = :id"),
                    {"id": project.project_id},
                )
    finally:
        await engine.dispose()


async def test_an_experience_with_a_routing_projection_cannot_be_deleted(
    tmp_path: Path,
) -> None:
    """ADR-054 b plus §13.1: the projection is the provenance that would be orphaned."""

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    sessions = create_session_factory(engine)
    store = PostgresStore(sessions)
    marker = uuid.uuid4().hex[:12]
    try:
        project = await setup_project(store, tmp_path, marker)
        experience = await setup_experience(store, project, marker)
        await store.put_experience_record(
            build(
                ExperienceRecord,
                contract_id=experience.experience_id,
                project_id=project.project_id,
                workspace_id=f"wks_{marker}",
            )
        )

        with pytest.raises(IntegrityError):
            async with sessions.begin() as session:
                await session.execute(
                    sa.text("DELETE FROM experiences WHERE id = :id"),
                    {"id": experience.experience_id},
                )
    finally:
        await engine.dispose()


async def test_a_routing_override_round_trips_and_is_immutable(tmp_path: Path) -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        project = await setup_project(store, tmp_path, marker)
        arguments: dict[str, Any] = {
            "override_id": new_id("routing_override"),
            "workspace_id": workspace_id,
            "project_id": project.project_id,
            "receipt_id": f"rcp_{marker}",
            "principal_id": f"usr_{marker}",
            "candidate_id": f"ccd_{marker}",
            "reason_code": "EXPERIMENTAL_COMPARISON",
            "reason": "Testing a compatible lower-cost runtime.",
            "created_at": datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
        }
        await memory.create_project(project)
        stored = await store.put_routing_override(**arguments)
        mirrored = await memory.put_routing_override(**arguments)

        assert stored == mirrored
        assert await store.get_routing_override(stored["contract_id"]) == stored
        assert await store.list_routing_overrides(workspace_id=workspace_id) == [stored]
        assert await store.put_routing_override(**arguments) == stored
        assert stored["contract_id"].startswith("rov_")
        assert stored["document_type"] == ROUTING_OVERRIDE_DOCUMENT_TYPE
        assert "contract_type" not in stored

        with pytest.raises(ValueError, match="is immutable: reason differ"):
            await store.put_routing_override(**{**arguments, "reason": "Something else."})
    finally:
        await engine.dispose()


async def test_a_routing_override_retried_without_a_clock_is_a_no_op(
    tmp_path: Path,
) -> None:
    """The PostgreSQL twin of ``test_a_routing_override_retried_without_a_clock_is_a_no_op``.

    ``routing_overrides`` is the one table whose body the *store* builds, so ``created_at``
    defaults to the wall clock and lands inside the hashed body. Comparing whole documents
    would make the ordinary at-least-once redelivery of §11.1's override endpoint look like
    a rewrite. Every other override test here pins the clock, which is why this one must
    not — and it must fail the same way, or not fail at all, on both backends.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        project = await setup_project(store, tmp_path, marker)
        arguments: dict[str, Any] = {
            "override_id": new_id("routing_override"),
            "workspace_id": workspace_id,
            "project_id": project.project_id,
            "receipt_id": f"rcp_{marker}",
            "principal_id": f"usr_{marker}",
            "candidate_id": f"ccd_{marker}",
            "reason_code": "EXPERIMENTAL_COMPARISON",
            "reason": "Testing a compatible lower-cost runtime.",
        }

        first = await store.put_routing_override(**arguments)
        second = await store.put_routing_override(**arguments)

        assert first == second
        assert await store.list_routing_overrides(workspace_id=workspace_id) == [first]

        await memory.create_project(project)
        await memory.put_routing_override(**arguments)
        assert await memory.put_routing_override(**arguments) == (
            await memory.get_routing_override(arguments["override_id"])
        )
        assert len(await memory.list_routing_overrides(workspace_id=workspace_id)) == 1
    finally:
        await engine.dispose()


async def test_the_v04_tables_are_all_present_at_head() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    try:
        async with engine.begin() as connection:
            present = await connection.run_sync(table_names)
    finally:
        await engine.dispose()

    assert set(V04_M0_ROUTING_TABLES) <= present
