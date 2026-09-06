"""M3's three record families against a real PostgreSQL database, beside ``MemoryStore``.

The twin of ``test_v04_m3_experience.py``, ``test_v04_m3_attribution.py`` and
``test_v04_m3_pipeline.py``. Those files prove what the feedback layer *decides*; this one proves
that the decisions survive a database, and that the two backends answer identically — same
objects, same order, same refusals.

The records here are not hand-built. Every experience record is produced by the real
:class:`~accretion.feedback.experience.ExperienceProjector`, every verification result by the
real :class:`~accretion.feedback.verification.IndependentVerificationRecorder`, and every failure
event by the real :class:`~accretion.feedback.failures.FailureClassifier`, all three driven by a
pinned clock. That matters: the parity claim is about the rows M3 actually writes, and a
fixture-shaped row would prove the store round-trips something nothing produces. The projector is
run against ``MemoryStore`` and the sealed contracts it produced are then written to PostgreSQL,
so the two backends are compared on *identical bytes* rather than on two independent
constructions that might differ for a reason no assertion would name.

Every id is minted fresh from a uuid, so the file is re-runnable against a database it has already
written to, and every row it writes is deleted in a ``finally``. Nothing here carries an
acceptance marker: M3a's criteria are claimed by the ``MemoryStore`` tests.
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

from accretion.contracts import (
    EvidenceClass,
    PrincipalRef,
    PrincipalStatus,
    Project,
    Provider,
    Run,
    RunState,
    Task,
    TaskEnvelope,
    TaskType,
    VerificationResult,
    VerificationStatus,
)
from accretion.contracts.canonical import CanonicalContract
from accretion.contracts.refs import EvidenceRef, VerifierRef
from accretion.contracts.routing import (
    ContradictionStatus,
    DecisionType,
    ExecutionConfiguration,
    ExperienceRecord,
    FailureEvent,
    IndependentVerificationResult,
    NodeContract,
    RoutingDecisionReceipt,
    VerificationSpec,
    VerificationState,
)
from accretion.experience.models import (
    Experience,
    ExperienceDetail,
    ExperienceEmbedding,
    ExperiencePolarity,
    ExperienceSourceKind,
    ExperienceTrust,
    TrajectorySegment,
    TrajectorySegmentKind,
)
from accretion.feedback.attribution import DependencyAttributor
from accretion.feedback.experience import ExperienceProjector
from accretion.feedback.failures import FailureClassifier, FailureSignals
from accretion.feedback.verification import IndependentVerificationRecorder
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import MemoryStore, PostgresStore
from accretion.routing.identity import execution_instance_id

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"
SEALED_AT = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
PRINCIPAL = PrincipalRef(
    principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    display_name="v0.4 M3 postgres twin",
    status=PrincipalStatus.ACTIVE,
)
OPERATOR = PrincipalRef(
    principal_id="usr_OPERATOR000000000000000000",
    display_name="v0.4 M3 deployment operator",
    status=PrincipalStatus.ACTIVE,
)


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def build[C: CanonicalContract](
    model: type[C], *, workspace_id: str, project_id: str, **overrides: Any
) -> C:
    """One golden ``minimal.json``, re-tenanted into this run's workspace and project."""

    path = FIXTURE_ROOT / snake_case(model.__name__) / "minimal.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    document.update(overrides)
    document["workspace_id"] = workspace_id
    document["project_id"] = project_id
    document.pop("content_hash", None)
    if "contract_id" not in overrides and model.ID_KIND is not None:
        document["contract_id"] = new_id(model.ID_KIND)
    return model.model_validate(document)


class FakeMaterializer:
    """``materialize`` with a call counter and a failure switch, and no P7 service behind it."""

    def __init__(self, detail: ExperienceDetail) -> None:
        self.detail = detail
        self.calls: list[str] = []
        self.fail_with: Exception | None = None

    async def materialize(
        self, run_id: str, *, candidate_id: str | None = None
    ) -> ExperienceDetail:
        self.calls.append(run_id)
        if self.fail_with is not None:
            raise self.fail_with
        return self.detail


def make_configuration(marker: str, workspace_id: str, project_id: str) -> ExecutionConfiguration:
    document: dict[str, Any] = json.loads(
        (FIXTURE_ROOT / "execution_configuration" / "minimal.json").read_text(
            encoding="utf-8"
        )
    )
    document["environment"]["environment"]["policy_profile"] = f"profile-{marker}"
    document["contract_id"] = new_id("execution_configuration")
    document["workspace_id"] = workspace_id
    document["project_id"] = project_id
    document.pop("content_hash", None)
    document.pop("configuration_hash", None)
    return ExecutionConfiguration.model_validate(document)


async def setup_project(store: PostgresStore, tmp_path: Path, marker: str) -> Project:
    """A real ``projects`` row: every v0.4 table has a RESTRICT key into it."""

    project = Project(
        project_id=new_id("project"),
        name=f"v0.4 M3 postgres twin {marker}",
        repository_path=tmp_path,
    )
    await store.create_project(project)
    return project


async def setup_experience(
    store: PostgresStore, project: Project, marker: str
) -> tuple[Experience, TrajectorySegment, ExperienceEmbedding, Run]:
    """A real ``experiences`` row and the task and run it is keyed by."""

    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective=f"Anchor the v0.4 M3 projection {marker}.",
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
        principal_id=PRINCIPAL.principal_id,
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
        content={"nodes": ["plan", "verify"]},
        content_digest=digest(f"segment-{marker}"),
    )
    embedding = ExperienceEmbedding(
        embedding_id=new_id("experience_embedding"),
        experience_id=experience.experience_id,
        input_digest=digest(f"embedding-{marker}"),
        vector=[1.0] + [0.0] * 383,
    )
    await store.save_experience(experience, (segment,), embedding)
    return experience, segment, embedding, run


async def project_in_memory(
    project: Project,
    experience: Experience,
    segment: TrajectorySegment,
    embedding: ExperienceEmbedding,
    run: Run,
    workspace_id: str,
) -> tuple[MemoryStore, list[ExperienceRecord], list[ExperienceRecord]]:
    """Drive the real projector and attributor against ``MemoryStore``, and keep what they wrote.

    The roots and the re-attribution revisions are returned separately, because the parity claim
    is not only that the rows survive: it is that a revision is filed under its **parent's**
    experience and not under its own derived id, which is the whole of migration 0020 and is only
    checkable if the test knows which rows are which.
    """

    memory = MemoryStore()
    await memory.create_project(project)
    await memory.save_experience(experience, (segment,), embedding)
    materializer = FakeMaterializer(
        ExperienceDetail(
            experience=experience,
            segments=[segment],
            embedding_version="v1",
            embedding_input_digest=embedding.input_digest,
        )
    )
    projector = ExperienceProjector(memory, materializer, lambda: SEALED_AT, OPERATOR)

    roots: list[ExperienceRecord] = []
    for key in ("plan", "verify"):
        node = build(
            NodeContract,
            workspace_id=workspace_id,
            project_id=project.project_id,
            contract_id=new_id("node_contract"),
            node_id=key,
            objective=f"do the {key} work",
            execution_instance_id=execution_instance_id(run.run_id, key, 1),
            labels={"run_id": run.run_id, "node_key": key, "attempt": "1"},
        )
        configuration = make_configuration(key, workspace_id, project.project_id)
        receipt = build(
            RoutingDecisionReceipt,
            workspace_id=workspace_id,
            project_id=project.project_id,
            contract_id=new_id("routing_receipt"),
            routing_request_id=new_id("routing_request"),
            node_contract_hash=node.immutable_hash,
            decision_type=DecisionType.EXPLOIT.value,
            selected_configuration_id=configuration.contract_id,
            selected_configuration_hash=configuration.configuration_hash,
        )
        local = build(
            IndependentVerificationResult,
            workspace_id=workspace_id,
            project_id=project.project_id,
            contract_id=new_id("independent_verification_result"),
            execution_instance_id=node.execution_instance_id,
            status=VerificationState.PASS.value,
        )
        roots.append(
            await projector.project(
                run=run,
                node=node,
                receipt=receipt,
                configuration=configuration,
                local=local,
                final_status=VerificationState.PASS,
                principal=PRINCIPAL,
            )
        )

    revisions = await DependencyAttributor(memory).reattribute(
        run=run, records=roots, projector=projector, principal=PRINCIPAL
    )
    return memory, roots, revisions


def make_verification_results(
    spec: VerificationSpec, run: Run, workspace_id: str, project_id: str
) -> list[IndependentVerificationResult]:
    """Two sealed §7.9 verdicts from the real recorder, one per node, ordered by their own ids."""

    recorder = IndependentVerificationRecorder()
    evidence_id = f"git-diff-sha256:{digest('diff')}"
    evidence = {
        evidence_id: EvidenceRef(
            evidence_id=evidence_id,
            evidence_class=EvidenceClass.DIGITAL,
            content_digest=digest("diff"),
        )
    }
    results: list[IndependentVerificationResult] = []
    for key in ("plan", "verify"):
        results.append(
            recorder.record(
                spec=spec,
                results=[
                    VerificationResult(
                        verification_id=new_id("verification"),
                        run_id=run.run_id,
                        verifier_id="git-diff",
                        verifier_version="1.0.0",
                        target_ref=key,
                        status=VerificationStatus.PASS,
                        evidence_refs=[evidence_id],
                        executed_at=SEALED_AT,
                    )
                ],
                execution_instance_id=execution_instance_id(run.run_id, key, 1),
                producer_session_id="ses_producer000000000000000",
                verifier_session_ids={"git-diff": None},
                verification_spec_hash=spec.content_hash,
                verifier=VerifierRef(
                    verifier_contract_id="git-diff", implementation_digest=digest("impl")
                ),
                workspace_id=workspace_id,
                project_id=project_id,
                clock=lambda: SEALED_AT,
                created_by=OPERATOR,
                evidence=evidence,
                producer_runtime="fake",
            )
        )
    return results


def make_failure_events(
    run: Run, workspace_id: str, project_id: str
) -> list[FailureEvent]:
    """Two sealed §7.11 events from the real classifier, typed by two different rules."""

    classifier = FailureClassifier(created_by=OPERATOR)
    signals = (
        FailureSignals(
            error_code="RUNTIME_VERSION_DRIFT",
            error_message="the runtime moved under the node",
            attempted_configuration_hashes=(digest("attempt-one"),),
        ),
        FailureSignals(
            local_status=VerificationState.QUARANTINED,
            error_message="a human quarantined this verification",
        ),
    )
    return [
        classifier.classify(
            signals=signal,
            execution_instance_id=execution_instance_id(run.run_id, key, 1),
            workspace_id=workspace_id,
            project_id=project_id,
            clock=lambda: SEALED_AT,
        )
        for key, signal in zip(("plan", "verify"), signals, strict=True)
    ]


async def discard(engine: Any, workspace_id: str) -> None:
    """Leave the shared database as this module found it, in foreign-key order."""

    async with engine.begin() as connection:
        for table in ("experience_records", "verification_results", "failure_events"):
            await connection.execute(
                sa.text(f"DELETE FROM {table} WHERE workspace_id = :workspace"),
                {"workspace": workspace_id},
            )


async def test_the_projection_and_its_revisions_round_trip_and_match_the_memory_store(
    tmp_path: Path,
) -> None:
    """Same objects, same order, out of both backends — for rows the projector really wrote.

    The revisions are written to PostgreSQL in the *opposite* order to the roots, so the ordering
    under test is the ``(created_at, contract_id)`` the two stores share and not the order the
    rows were inserted in.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        project = await setup_project(store, tmp_path, marker)
        experience, segment, embedding, run = await setup_experience(store, project, marker)
        memory, roots, revisions = await project_in_memory(
            project, experience, segment, embedding, run, workspace_id
        )
        assert len(roots) == 2
        assert len(revisions) == 2

        for record in reversed([*roots, *revisions]):
            await store.put_experience_record(
                record, experience_id=experience.experience_id
            )

        listed = await store.list_experience_records(workspace_id=workspace_id)
        assert listed == await memory.list_experience_records(workspace_id=workspace_id)
        chain = await store.list_experience_record_revisions(
            experience.experience_id, workspace_id=workspace_id
        )
        assert chain == await memory.list_experience_record_revisions(
            experience.experience_id, workspace_id=workspace_id
        )
        assert sorted(record.contract_id for record in chain) == sorted(
            record.contract_id for record in [*roots, *revisions]
        )
        for record in [*roots, *revisions]:
            assert await store.get_experience_record(record.contract_id) == record
        # A revision is filed under its parent, never under its own derived id.
        for revision in revisions:
            assert (
                await store.list_experience_record_revisions(
                    revision.contract_id, workspace_id=workspace_id
                )
                == []
            )
    finally:
        await discard(engine, workspace_id)
        await engine.dispose()


async def test_a_projection_of_an_experience_that_was_never_captured_is_refused_alike(
    tmp_path: Path,
) -> None:
    """The moved foreign key, live: PostgreSQL refuses the row ``MemoryStore`` refuses in Python.

    An ``IntegrityError`` here and a ``ValueError`` there, which is the established shape of every
    key in this family. What the two backends must agree on is *which* writes are refused, and
    that is what both halves assert.
    """

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        project = await setup_project(store, tmp_path, marker)
        experience, segment, embedding, run = await setup_experience(store, project, marker)
        memory, roots, _ = await project_in_memory(
            project, experience, segment, embedding, run, workspace_id
        )
        absent = new_id("experience")

        with pytest.raises(IntegrityError):
            await store.put_experience_record(roots[0], experience_id=absent)
        with pytest.raises(ValueError, match="which is not in experiences"):
            await MemoryStore().put_experience_record(roots[0], experience_id=absent)
    finally:
        await discard(engine, workspace_id)
        await engine.dispose()


async def test_verification_results_round_trip_and_match_the_memory_store(
    tmp_path: Path,
) -> None:
    """§7.9 verdicts: same objects, same order, and one query that is not the experience query."""

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        project = await setup_project(store, tmp_path, marker)
        await memory.create_project(project)
        experience, segment, embedding, run = await setup_experience(store, project, marker)
        spec = build(
            VerificationSpec,
            workspace_id=workspace_id,
            project_id=project.project_id,
            contract_id=new_id("verification_spec"),
        )
        results = make_verification_results(spec, run, workspace_id, project.project_id)

        for record in reversed(results):
            await store.put_verification_result(record)
            await memory.put_verification_result(record)

        listed = await store.list_verification_results(workspace_id=workspace_id)
        assert listed == await memory.list_verification_results(workspace_id=workspace_id)
        assert sorted(record.contract_id for record in listed) == sorted(
            record.contract_id for record in results
        )
        for record in results:
            assert await store.get_verification_result(record.contract_id) == record
        # The other family's query does not answer this one.
        assert await store.list_experience_records(workspace_id=workspace_id) == []
    finally:
        await discard(engine, workspace_id)
        await engine.dispose()


async def test_failure_events_round_trip_and_match_the_memory_store(
    tmp_path: Path,
) -> None:
    """§7.11 events, including the attempted-hash list §9.7's last rule depends on."""

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        project = await setup_project(store, tmp_path, marker)
        await memory.create_project(project)
        _, _, _, run = await setup_experience(store, project, marker)
        events = make_failure_events(run, workspace_id, project.project_id)

        for event in reversed(events):
            await store.put_failure_event(event)
            await memory.put_failure_event(event)

        listed = await store.list_failure_events(workspace_id=workspace_id)
        assert listed == await memory.list_failure_events(workspace_id=workspace_id)
        assert sorted(event.contract_id for event in listed) == sorted(
            event.contract_id for event in events
        )
        for event in events:
            assert await store.get_failure_event(event.contract_id) == event
        stored = {event.contract_id: event for event in listed}
        assert stored[events[0].contract_id].attempted_configuration_hashes == list(
            events[0].attempted_configuration_hashes
        )
        assert stored[events[1].contract_id].retryable is False
    finally:
        await discard(engine, workspace_id)
        await engine.dispose()


async def test_an_open_contradiction_survives_the_round_trip_as_an_ineligible_row(
    tmp_path: Path,
) -> None:
    """Ineligible, never invisible: the row PostgreSQL returns is still in the listing."""

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    marker = uuid.uuid4().hex[:12]
    workspace_id = f"wks_{marker}"
    try:
        project = await setup_project(store, tmp_path, marker)
        experience, segment, embedding, run = await setup_experience(store, project, marker)
        memory, roots, revisions = await project_in_memory(
            project, experience, segment, embedding, run, workspace_id
        )
        # The projector wrote to MemoryStore only; give PostgreSQL the same rows first, so the
        # parity claim below compares two stores holding the same history.
        for record in [*roots, *revisions]:
            await store.put_experience_record(
                record, experience_id=experience.experience_id
            )
        payload = roots[0].model_dump(mode="python")
        payload.pop("content_hash")
        payload["contract_id"] = new_id("experience")
        payload["contradiction_status"] = ContradictionStatus.OPEN
        payload["eligible_for_learning"] = False
        contradicted = ExperienceRecord.model_validate(payload)

        await store.put_experience_record(
            contradicted, experience_id=experience.experience_id
        )
        await memory.put_experience_record(
            contradicted, experience_id=experience.experience_id
        )

        listed = await store.list_experience_records(workspace_id=workspace_id)
        assert listed == await memory.list_experience_records(workspace_id=workspace_id)
        open_rows = [
            record
            for record in listed
            if record.contradiction_status is ContradictionStatus.OPEN
        ]
        assert [record.contract_id for record in open_rows] == [contradicted.contract_id]
        assert open_rows[0].eligible_for_learning is False
    finally:
        await discard(engine, workspace_id)
        await engine.dispose()
