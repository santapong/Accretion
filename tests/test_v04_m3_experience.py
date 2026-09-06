"""The experience projection: what it is keyed by, what it refuses, and what it never edits.

Five properties, and each one is a way a routing memory quietly stops being trustworthy.

**A projection is filed under an experience that exists.** ``experience_records.experience_id``
is a ``RESTRICT`` key into ``experiences``, so the projector materialises first and writes
second. A record filed under its own id — the M0 shape — would be storable for the first node of
a run and unstorable for the second, because a run has many nodes and exactly one experience.

**Eligibility is decided twice and can only ever narrow.** A ``FAIL`` local verdict is not
evidence a router may learn from, an ``OPEN`` contradiction is exactly the record §10.1 excludes,
and neither can be argued into eligibility by this layer, because the contract refuses it
independently.

**Sharing wider than permitted is refused before it is sealed.** The record's own validator
requires visibility and provenance scope to be *equal*; the projector refuses the wider case with
an error that says which direction leaked, because the two failures deserve different sentences
and only one of them is a leak.

**An open contradiction stays in the listing.** It is ineligible, not invisible. A reader that
filtered ``OPEN`` out would make the contradiction disappear rather than exclude it, and nothing
would ever be resolved because nothing could be found.

**Nothing is edited.** A contradiction discovered later, and its resolution, are both new rows
that supersede what they replace. The tests below read the superseded row back out of the store
and compare its bytes, which is the only assertion that can tell an append from a rewrite.

Every assertion is against what the store returns, never against the object handed to it, and the
store is a fresh ``MemoryStore``.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from accretion.contracts import (
    PrincipalRef,
    PrincipalStatus,
    Project,
    Provider,
    Run,
    RunState,
    TaskType,
)
from accretion.contracts.canonical import CanonicalContract
from accretion.contracts.routing import (
    ContradictionStatus,
    DecisionType,
    ExecutionConfiguration,
    ExperienceOutcomes,
    ExperienceRecord,
    IndependentVerificationResult,
    NodeContract,
    RoutingDecisionReceipt,
    VerificationState,
    Visibility,
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
from accretion.feedback.experience import (
    CONTRADICTS_LABEL,
    EXPERIENCE_ID_LABEL,
    PROJECTION_REVISION_LABEL,
    RESOLUTION_LABEL,
    ContradictionDetector,
    ExperienceProjector,
    record_signature_for,
)
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.features import summarize_evidence
from accretion.routing.identity import contract_signature_for, execution_instance_id

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"
FIXTURE: dict[str, Any] = json.loads(
    (FIXTURE_ROOT / "node_contract" / "minimal.json").read_text(encoding="utf-8")
)
WORKSPACE_ID: str = FIXTURE["workspace_id"]
PROJECT_ID: str = FIXTURE["project_id"]

SEALED_AT = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
VERIFIED_AT = datetime(2026, 3, 1, 9, 2, 30, tzinfo=UTC)
PRINCIPAL = PrincipalRef(
    principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    display_name="v0.4 M3 experience test",
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


def build[C: CanonicalContract](model: type[C], **overrides: Any) -> C:
    """One golden ``minimal.json``, re-sealed after whatever this test changed.

    The same helper ``test_v04_experience_revisions.py`` uses, for its reason: a hand-built
    contract proves the code round-trips whatever this file thinks a contract looks like, and a
    fixture-built one proves it round-trips what the freeze froze.
    """

    path = FIXTURE_ROOT / snake_case(model.__name__) / "minimal.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    document.update(overrides)
    document.pop("content_hash", None)
    if "contract_id" not in overrides and model.ID_KIND is not None:
        document["contract_id"] = new_id(model.ID_KIND)
    return model.model_validate(document)


class FakeMaterializer:
    """``ExperienceService.materialize`` with the run, the git subprocess and the gate removed.

    A hand-written fake and not a mock: it counts its calls, so a test can prove the projector
    materialises **once** per projection rather than once per read, and it carries a failure
    switch, so the "the experience layer refused" path is reachable without disabling a feature
    flag on a real service.
    """

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


class CountingStore(MemoryStore):
    """A ``MemoryStore`` that remembers every ``put_experience_record`` it was asked to make.

    The spy ``AC4-M3-026`` needs. "Attribution appended rather than rewrote" is not observable
    from the final table — an append and a rewrite-then-append leave the same rows — so the
    assertion has to be about the *calls*, and ``rewrites`` counts the ones that named a
    contract id the table already held.
    """

    def __init__(self) -> None:
        super().__init__()
        self.record_puts: list[str] = []
        self.rewrites: list[str] = []

    async def put_experience_record(
        self, record: ExperienceRecord, *, experience_id: str | None = None
    ) -> ExperienceRecord:
        if record.contract_id in self.v04_contracts["experience_records"]:
            self.rewrites.append(record.contract_id)
        self.record_puts.append(record.contract_id)
        return await super().put_experience_record(record, experience_id=experience_id)


async def seed_experience(store: MemoryStore, run: Run, marker: str) -> Experience:
    """The v0.2 P7 experience the projection is keyed by, as a real row.

    Seeded rather than faked, because ``MemoryStore`` mirrors the ``RESTRICT`` foreign key that
    PostgreSQL enforces: a projection of an experience with no row is refused by both backends,
    and a fake that skipped the row would let this file accept writes the database will not.
    """

    experience = Experience(
        experience_id=new_id("experience"),
        project_id=PROJECT_ID,
        repository_identity=digest(PROJECT_ID),
        task_id=run.task_id,
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


def make_node(
    run: Run,
    *,
    node_key: str,
    attempt: int = 1,
    objective: str | None = None,
    verification_spec_hash: str | None = None,
) -> NodeContract:
    """A frozen node contract with the three labels ``routing/freeze.py`` writes onto every one.

    ``execution_instance_id`` is the real derivation and not a fresh id: two attempts at one node
    must have different identities and two computations of one attempt the same one, and a test
    that minted them would prove nothing about either.
    """

    overrides: dict[str, Any] = {
        "contract_id": new_id("node_contract"),
        "node_id": node_key,
        "execution_instance_id": execution_instance_id(run.run_id, node_key, attempt),
        "labels": {"run_id": run.run_id, "node_key": node_key, "attempt": str(attempt)},
    }
    if objective is not None:
        overrides["objective"] = objective
    if verification_spec_hash is not None:
        overrides["verification_spec_ref"] = {
            **FIXTURE["verification_spec_ref"],
            "content_hash": verification_spec_hash,
        }
    return build(NodeContract, **overrides)


def make_configuration(marker: str) -> ExecutionConfiguration:
    """A sealed configuration whose ``configuration_hash`` differs per ``marker``.

    The model seals the hash over its six semantic fields, so the marker is written into one of
    them — the environment's policy profile — rather than into the hash directly. A test that set
    the hash by hand would be asserting against a value the contract would have refused.
    """

    document: dict[str, Any] = json.loads(
        (FIXTURE_ROOT / "execution_configuration" / "minimal.json").read_text(
            encoding="utf-8"
        )
    )
    document["environment"]["environment"]["policy_profile"] = f"profile-{marker}"
    document["contract_id"] = new_id("execution_configuration")
    document.pop("content_hash", None)
    document.pop("configuration_hash", None)
    return ExecutionConfiguration.model_validate(document)


def make_receipt(
    node: NodeContract,
    configuration: ExecutionConfiguration | None,
    *,
    created_at: datetime = SEALED_AT,
) -> RoutingDecisionReceipt:
    """The routing decision the projection reads its configuration signature out of.

    ``configuration`` is nullable so that the one receipt a node can never be projected from —
    ``HUMAN_REVIEW_REQUIRED``, which selects nothing — is buildable here rather than only
    describable.
    """

    overrides: dict[str, Any] = {
        "contract_id": new_id("routing_receipt"),
        "routing_request_id": new_id("routing_request"),
        "node_contract_hash": node.immutable_hash,
        "created_at": created_at.isoformat(),
    }
    if configuration is None:
        overrides["decision_type"] = DecisionType.HUMAN_REVIEW_REQUIRED.value
    else:
        overrides["decision_type"] = DecisionType.EXPLOIT.value
        overrides["selected_configuration_id"] = configuration.contract_id
        overrides["selected_configuration_hash"] = configuration.configuration_hash
    return build(RoutingDecisionReceipt, **overrides)


def make_local(
    node: NodeContract,
    status: VerificationState,
    *,
    signed_at: datetime = VERIFIED_AT,
    claim_statuses: tuple[VerificationState, ...] = (),
) -> IndependentVerificationResult:
    """One §7.9 verdict about one execution instance.

    ``claim_statuses`` drives the derived quality, which is the fraction of claim results that
    passed. Left empty the record carries no claims and the derived quality is ``None``, which is
    the honest reading of "nothing was decided" and is not the same as zero.
    """

    return build(
        IndependentVerificationResult,
        contract_id=new_id("independent_verification_result"),
        execution_instance_id=node.execution_instance_id,
        status=status.value,
        signed_at=signed_at.isoformat(),
        claim_results=[
            {
                "claim_id": f"claim-{index}",
                "status": claim.value,
                "coverage": 1.0,
            }
            for index, claim in enumerate(claim_statuses)
        ],
    )


def frozen_clock(moment: datetime = SEALED_AT):  # type: ignore[no-untyped-def]
    """The only clock the projector reads.

    Pinned because a revision's identity is derived from what it decided and its *bytes* include
    the stamp: two runs of one recomputation are a byte-identical no-op only if the clock agrees,
    and a wall clock would make idempotence untestable.
    """

    return lambda: moment


async def setup_projection(
    *, node_key: str = "implement-migration", marker: str = "a"
) -> tuple[
    CountingStore,
    ExperienceProjector,
    FakeMaterializer,
    Run,
    Experience,
    NodeContract,
    ExecutionConfiguration,
    RoutingDecisionReceipt,
]:
    """A store holding the project and the experience, plus everything one projection needs.

    Returned as a tuple rather than assembled per test because the project row and the experience
    row are the *precondition* of a v0.4 write — both keys are mirrored in ``MemoryStore`` — and
    not the subject of one.
    """

    store = CountingStore()
    await store.create_project(
        Project(
            project_id=PROJECT_ID,
            name="v0.4 M3 experience projection",
            repository_path=Path("/tmp/accretion-v04-m3-experience"),
        )
    )
    run = Run(
        run_id=new_id("run"),
        task_id=new_id("task"),
        project_id=PROJECT_ID,
        provider=Provider.FAKE,
        state=RunState.SUCCEEDED,
        principal_id=PRINCIPAL.principal_id,
    )
    experience = await seed_experience(store, run, marker)
    detail = ExperienceDetail(
        experience=experience,
        segments=list(await store.list_trajectory_segments(experience.experience_id)),
        embedding_version="v1",
        embedding_input_digest=digest(f"embedding-{marker}"),
    )
    materializer = FakeMaterializer(detail)
    projector = ExperienceProjector(store, materializer, frozen_clock(), OPERATOR)
    node = make_node(run, node_key=node_key)
    configuration = make_configuration(marker)
    receipt = make_receipt(node, configuration)
    return store, projector, materializer, run, experience, node, configuration, receipt


# --------------------------------------------------------------- contract signature


def test_the_contract_signature_of_two_nodes_differing_only_in_run_is_the_same() -> None:
    """The retrieval key must not contain the execution it was computed from.

    A signature that carried the run, the graph revision or the attempt would match nothing but
    itself, which is how a retrieval index becomes quietly useless: every lookup returns zero
    rows and no error is ever raised.
    """

    first = Run(
        run_id=new_id("run"),
        task_id=new_id("task"),
        project_id=PROJECT_ID,
        provider=Provider.FAKE,
        state=RunState.SUCCEEDED,
    )
    second = Run(
        run_id=new_id("run"),
        task_id=new_id("task"),
        project_id=PROJECT_ID,
        provider=Provider.FAKE,
        state=RunState.SUCCEEDED,
    )

    assert contract_signature_for(
        make_node(first, node_key="review")
    ) == contract_signature_for(make_node(second, node_key="review"))


def test_the_contract_signature_moves_when_the_objective_moves() -> None:
    """A digest of the objective, and not of nothing: two different jobs are not evidence
    about one another."""

    run = Run(
        run_id=new_id("run"),
        task_id=new_id("task"),
        project_id=PROJECT_ID,
        provider=Provider.FAKE,
        state=RunState.SUCCEEDED,
    )
    baseline = contract_signature_for(make_node(run, node_key="review"))
    moved = contract_signature_for(
        make_node(run, node_key="review", objective="Something else entirely.")
    )

    assert moved.objective_digest != baseline.objective_digest
    assert moved.verification_spec_hash == baseline.verification_spec_hash


# ------------------------------------------------------------------- projection


async def test_a_projection_is_filed_under_the_experience_the_run_materialized() -> None:
    """The record's own id is not its experience's id, and the revision listing proves it.

    Migration 0020 made this possible and ``record_final`` makes it necessary: one run has one
    experience and many nodes, so at most one node could ever have been filed the M0 way.
    """

    store, projector, materializer, run, experience, node, configuration, receipt = (
        await setup_projection()
    )

    record = await projector.project(
        run=run,
        node=node,
        receipt=receipt,
        configuration=configuration,
        local=make_local(node, VerificationState.PASS),
        final_status=VerificationState.PASS,
        principal=PRINCIPAL,
    )

    assert materializer.calls == [run.run_id]
    assert record.contract_id != experience.experience_id
    assert await store.list_experience_record_revisions(
        experience.experience_id, workspace_id=WORKSPACE_ID
    ) == [record]
    assert record.labels[EXPERIENCE_ID_LABEL] == experience.experience_id


async def test_a_passing_local_verdict_with_no_contradiction_is_eligible() -> None:
    """ADR-048's rule in its positive direction, read back from the store."""

    store, projector, _, run, experience, node, configuration, receipt = (
        await setup_projection()
    )

    await projector.project(
        run=run,
        node=node,
        receipt=receipt,
        configuration=configuration,
        local=make_local(node, VerificationState.PASS),
        final_status=VerificationState.PASS,
        principal=PRINCIPAL,
    )

    stored = await store.list_experience_records(workspace_id=WORKSPACE_ID)
    assert [record.eligible_for_learning for record in stored] == [True]
    assert stored[0].contradiction_status is ContradictionStatus.NONE


async def test_a_failing_local_verdict_can_never_be_eligible_for_learning() -> None:
    """Adversarial: the failure is not merely recorded as ineligible, it is unrepresentable.

    Two assertions and both are needed. The first says the projector produced an ineligible
    record; the second says the contract would have refused an eligible one anyway, so a bug in
    the projector cannot put a FAIL into a training snapshot.
    """

    store, projector, _, run, experience, node, configuration, receipt = (
        await setup_projection()
    )

    await projector.project(
        run=run,
        node=node,
        receipt=receipt,
        configuration=configuration,
        local=make_local(node, VerificationState.FAIL),
        final_status=VerificationState.FAIL,
        principal=PRINCIPAL,
    )

    stored = await store.list_experience_records(workspace_id=WORKSPACE_ID)
    assert [record.eligible_for_learning for record in stored] == [False]
    with pytest.raises(ValueError, match="only a verified outcome is"):
        forged = stored[0].model_dump(mode="python")
        forged.pop("content_hash")
        ExperienceRecord.model_validate({**forged, "eligible_for_learning": True})


async def test_a_node_with_no_verdict_yet_is_pending_and_ineligible() -> None:
    """A missing verdict is ``PENDING``: an absence, not a failure and not a pass."""

    store, projector, _, run, _, node, configuration, receipt = await setup_projection()

    await projector.project(
        run=run,
        node=node,
        receipt=receipt,
        configuration=configuration,
        local=None,
        final_status=None,
        principal=PRINCIPAL,
    )

    stored = await store.list_experience_records(workspace_id=WORKSPACE_ID)
    assert stored[0].local_verification_status is VerificationState.PENDING
    assert stored[0].eligible_for_learning is False
    assert stored[0].final_run_status is None


@pytest.mark.acceptance("AC4-M3-033")
async def test_a_visibility_wider_than_the_permission_scope_is_refused() -> None:
    """AC4-M3-033. Sharing beyond what the provenance grants is refused before it is sealed.

    The message is asserted and not just the type, because
    :class:`~accretion.contracts.routing.ExperienceRecord`'s own validator also raises a
    ``ValueError`` here — pydantic's is one — and a test that accepted either could not tell the
    projector's check from the contract's. Dropping the check below leaves the contract's
    equality error, whose wording this ``match`` does not accept.
    """

    store, projector, _, run, _, node, configuration, receipt = await setup_projection()

    with pytest.raises(ValueError, match="is wider than the scope"):
        await projector.project(
            run=run,
            node=node,
            receipt=receipt,
            configuration=configuration,
            local=make_local(node, VerificationState.PASS),
            final_status=VerificationState.PASS,
            principal=PRINCIPAL,
            visibility=Visibility.TEAM_WORKSPACE,
            permission_scope=Visibility.PROJECT,
        )

    assert await store.list_experience_records(workspace_id=WORKSPACE_ID) == []


async def test_a_record_shared_at_workspace_scope_carries_the_scope_that_permitted_it() -> None:
    """The permitted case, so the refusal above is not passing because nothing is ever shared."""

    store, projector, _, run, _, node, configuration, receipt = await setup_projection()

    await projector.project(
        run=run,
        node=node,
        receipt=receipt,
        configuration=configuration,
        local=make_local(node, VerificationState.PASS),
        final_status=VerificationState.PASS,
        principal=PRINCIPAL,
        visibility=Visibility.TEAM_WORKSPACE,
        permission_scope=Visibility.TEAM_WORKSPACE,
    )

    stored = await store.list_experience_records(workspace_id=WORKSPACE_ID)
    assert stored[0].visibility is Visibility.TEAM_WORKSPACE
    assert stored[0].permission_provenance.scope is Visibility.TEAM_WORKSPACE
    assert stored[0].permission_provenance.granted_by == PRINCIPAL


async def test_a_receipt_that_selected_no_configuration_cannot_be_projected() -> None:
    """A HUMAN_REVIEW_REQUIRED decision ran nothing, so there is no surface to be evidence about."""

    store, projector, _, run, _, node, _, _ = await setup_projection()
    receipt = make_receipt(node, None)

    with pytest.raises(ValueError, match="selected no configuration"):
        await projector.project(
            run=run,
            node=node,
            receipt=receipt,
            configuration=make_configuration("a"),
            local=None,
            final_status=None,
            principal=PRINCIPAL,
        )

    assert await store.list_experience_records(workspace_id=WORKSPACE_ID) == []


async def test_a_configuration_that_is_not_the_one_the_receipt_selected_is_refused() -> None:
    """Both values are valid digests, so nothing downstream could ever detect the swap."""

    store, projector, _, run, _, node, configuration, receipt = await setup_projection()

    with pytest.raises(ValueError, match="selected"):
        await projector.project(
            run=run,
            node=node,
            receipt=receipt,
            configuration=make_configuration("somewhere-else"),
            local=None,
            final_status=None,
            principal=PRINCIPAL,
        )

    assert store.record_puts == []


async def test_the_derived_latency_is_the_interval_from_the_receipt_to_the_signed_verdict() -> None:
    """The one duration the store actually witnessed, and the quality the spec actually decided."""

    store, projector, _, run, _, node, configuration, _ = await setup_projection()
    receipt = make_receipt(node, configuration, created_at=SEALED_AT)

    await projector.project(
        run=run,
        node=node,
        receipt=receipt,
        configuration=configuration,
        local=make_local(
            node,
            VerificationState.FAIL,
            signed_at=SEALED_AT + timedelta(seconds=90),
            claim_statuses=(
                VerificationState.PASS,
                VerificationState.PASS,
                VerificationState.FAIL,
                VerificationState.FAIL,
            ),
        ),
        final_status=VerificationState.FAIL,
        principal=PRINCIPAL,
    )

    stored = await store.list_experience_records(workspace_id=WORKSPACE_ID)
    assert stored[0].outcomes.latency_ms == 90_000
    assert stored[0].outcomes.quality == 0.5


async def test_measured_outcomes_supplied_by_the_caller_replace_the_derived_ones() -> None:
    """The seam M3b fills. Without it every projection would record a cost of zero forever."""

    store, projector, _, run, _, node, configuration, receipt = await setup_projection()

    await projector.project(
        run=run,
        node=node,
        receipt=receipt,
        configuration=configuration,
        local=make_local(node, VerificationState.PASS),
        final_status=VerificationState.PASS,
        principal=PRINCIPAL,
        outcomes=ExperienceOutcomes(
            quality=0.75, cost=__import__("decimal").Decimal("1.25"), latency_ms=4_200
        ),
    )

    stored = await store.list_experience_records(workspace_id=WORKSPACE_ID)
    assert stored[0].outcomes.latency_ms == 4_200
    assert float(stored[0].outcomes.cost) == 1.25


async def test_an_experience_layer_refusal_leaves_no_half_written_projection() -> None:
    """The P7 gate is upstream of the key, so its refusal must stop the write, not follow it.

    ``ExperienceService.materialize`` is gated by the global flag and by three project features,
    and a projector that wrote first and materialised second would file a record against an
    experience the deployment had just refused to create.
    """

    store, projector, materializer, run, _, node, configuration, receipt = (
        await setup_projection()
    )
    materializer.fail_with = PermissionError("experience retrieval is globally disabled")

    with pytest.raises(PermissionError):
        await projector.project(
            run=run,
            node=node,
            receipt=receipt,
            configuration=configuration,
            local=make_local(node, VerificationState.PASS),
            final_status=VerificationState.PASS,
            principal=PRINCIPAL,
        )

    assert materializer.calls == [run.run_id]
    assert store.record_puts == []
    assert await store.list_experience_records(workspace_id=WORKSPACE_ID) == []


# ---------------------------------------------------------------- contradictions


def test_a_detector_ignores_two_records_that_ran_on_different_configurations() -> None:
    """A pass here and a failure there is the most useful thing a router can learn, not a
    conflict."""

    detector = ContradictionDetector()
    left = build(
        ExperienceRecord,
        contract_id=new_id("experience"),
        configuration_hash=digest("left"),
        local_verification_status=VerificationState.PASS.value,
        source_node_execution_id="exe_left",
    )
    right = build(
        ExperienceRecord,
        contract_id=new_id("experience"),
        configuration_hash=digest("right"),
        local_verification_status=VerificationState.FAIL.value,
        source_node_execution_id="exe_right",
        eligible_for_learning=False,
    )

    assert detector.detect(right, [left]) == []


def test_a_detector_ignores_an_inconclusive_verdict_in_both_directions() -> None:
    """Registry §5.1: declining to decide is not disagreeing."""

    detector = ContradictionDetector()
    passed = build(
        ExperienceRecord,
        contract_id=new_id("experience"),
        local_verification_status=VerificationState.PASS.value,
        source_node_execution_id="exe_passed",
    )
    undecided = build(
        ExperienceRecord,
        contract_id=new_id("experience"),
        local_verification_status=VerificationState.INCONCLUSIVE.value,
        source_node_execution_id="exe_undecided",
        eligible_for_learning=False,
    )

    assert detector.detect(undecided, [passed]) == []
    assert detector.detect(passed, [undecided]) == []


@pytest.mark.acceptance("AC4-M3-034")
async def test_an_open_contradiction_is_ineligible_and_still_returned_by_the_listing() -> None:
    """AC4-M3-034. Excluded from learning, never excluded from the record.

    Two nodes, one signature, one configuration, opposite verdicts. The second projection is
    ``OPEN`` before it is stored — no generation of it was ever eligible — and it is still in
    ``list_experience_records``, which is what a resolver has to be able to find.
    """

    store, projector, _, run, experience, node, configuration, receipt = (
        await setup_projection()
    )
    await projector.project(
        run=run,
        node=node,
        receipt=receipt,
        configuration=configuration,
        local=make_local(node, VerificationState.PASS),
        final_status=VerificationState.PASS,
        principal=PRINCIPAL,
    )

    other = make_node(run, node_key=node.node_id, attempt=2)
    contradicting = await projector.project(
        run=run,
        node=other,
        receipt=make_receipt(other, configuration),
        configuration=configuration,
        local=make_local(other, VerificationState.FAIL),
        final_status=VerificationState.FAIL,
        principal=PRINCIPAL,
    )

    listed = await store.list_experience_records(workspace_id=WORKSPACE_ID)
    assert contradicting in listed
    assert contradicting.contradiction_status is ContradictionStatus.OPEN
    assert contradicting.eligible_for_learning is False
    assert [
        record.eligible_for_learning
        for record in listed
        if record.contradiction_status is ContradictionStatus.OPEN
    ] == [False, False]


async def test_the_older_side_of_a_contradiction_is_reopened_by_a_revision_not_an_edit() -> None:
    """The stored record's bytes are unchanged; a second row says the contradiction exists.

    Read back from the store before and after, because an append and a rewrite-followed-by-an-
    append leave the same *set* of rows and only the untouched bytes tell them apart.
    """

    store, projector, _, run, experience, node, configuration, receipt = (
        await setup_projection()
    )
    first = await projector.project(
        run=run,
        node=node,
        receipt=receipt,
        configuration=configuration,
        local=make_local(node, VerificationState.PASS),
        final_status=VerificationState.PASS,
        principal=PRINCIPAL,
    )
    before = await store.get_experience_record(first.contract_id)

    other = make_node(run, node_key=node.node_id, attempt=2)
    second = await projector.project(
        run=run,
        node=other,
        receipt=make_receipt(other, configuration),
        configuration=configuration,
        local=make_local(other, VerificationState.FAIL),
        final_status=VerificationState.FAIL,
        principal=PRINCIPAL,
    )

    assert await store.get_experience_record(first.contract_id) == before
    assert store.rewrites == []
    chain = await store.list_experience_record_revisions(
        experience.experience_id, workspace_id=WORKSPACE_ID
    )
    reopened = [
        record
        for record in chain
        if record.supersedes_contract_id == first.contract_id
    ]
    assert len(reopened) == 1
    assert reopened[0].contradiction_status is ContradictionStatus.OPEN
    assert reopened[0].eligible_for_learning is False
    assert reopened[0].labels[CONTRADICTS_LABEL] == second.contract_id
    assert reopened[0].labels[PROJECTION_REVISION_LABEL] == "2"
    # Written by the deployment, not by the principal whose run merely exposed it.
    assert reopened[0].created_by == OPERATOR


async def test_resolving_a_contradiction_appends_a_resolved_revision() -> None:
    """``OPEN`` → ``RESOLVED`` is a new row carrying the adjudication, and the old rows survive."""

    store, projector, _, run, experience, node, configuration, receipt = (
        await setup_projection()
    )
    first = await projector.project(
        run=run,
        node=node,
        receipt=receipt,
        configuration=configuration,
        local=make_local(node, VerificationState.PASS),
        final_status=VerificationState.PASS,
        principal=PRINCIPAL,
    )
    other = make_node(run, node_key=node.node_id, attempt=2)
    await projector.project(
        run=run,
        node=other,
        receipt=make_receipt(other, configuration),
        configuration=configuration,
        local=make_local(other, VerificationState.FAIL),
        final_status=VerificationState.FAIL,
        principal=PRINCIPAL,
    )
    before = len(
        await store.list_experience_record_revisions(
            experience.experience_id, workspace_id=WORKSPACE_ID
        )
    )

    # The root of the *first* node's line is named, and the resolver follows it forward to the
    # OPEN revision that the second projection appended to it.
    resolved = await projector.resolve_contradiction(
        experience_id=experience.experience_id,
        workspace_id=WORKSPACE_ID,
        record_id=first.contract_id,
        resolution="the failing attempt ran against a stale lockfile",
        principal=PRINCIPAL,
    )

    chain = await store.list_experience_record_revisions(
        experience.experience_id, workspace_id=WORKSPACE_ID
    )
    assert len(chain) == before + 1
    assert resolved in chain
    assert resolved.supersedes_contract_id is not None
    assert resolved.contradiction_status is ContradictionStatus.RESOLVED
    assert (
        resolved.labels[RESOLUTION_LABEL]
        == "the failing attempt ran against a stale lockfile"
    )
    assert store.rewrites == []


async def test_a_resolution_of_a_contradiction_that_is_not_open_is_refused() -> None:
    """Re-adjudicating a settled question would write a second answer to it."""

    store, projector, _, run, experience, node, configuration, receipt = (
        await setup_projection()
    )
    clean = await projector.project(
        run=run,
        node=node,
        receipt=receipt,
        configuration=configuration,
        local=make_local(node, VerificationState.PASS),
        final_status=VerificationState.PASS,
        principal=PRINCIPAL,
    )

    with pytest.raises(ValueError, match="only an OPEN contradiction"):
        await projector.resolve_contradiction(
            experience_id=experience.experience_id,
            workspace_id=WORKSPACE_ID,
            record_id=clean.contract_id,
            resolution="nothing was wrong",
            principal=PRINCIPAL,
        )


async def test_a_resolution_with_no_stated_reason_is_refused() -> None:
    """Registry §17 makes the revision the audit trail; an empty reason closes without settling."""

    store, projector, _, run, experience, node, configuration, receipt = (
        await setup_projection()
    )
    clean = await projector.project(
        run=run,
        node=node,
        receipt=receipt,
        configuration=configuration,
        local=make_local(node, VerificationState.PASS),
        final_status=VerificationState.PASS,
        principal=PRINCIPAL,
    )

    with pytest.raises(ValueError, match="must say something"):
        await projector.resolve_contradiction(
            experience_id=experience.experience_id,
            workspace_id=WORKSPACE_ID,
            record_id=clean.contract_id,
            resolution="   ",
            principal=PRINCIPAL,
        )


# ----------------------------------------------------------------- retrievability


async def test_an_experience_frozen_against_another_spec_is_not_retrievable() -> None:
    """Adversarial: a schema-incompatible record is counted once, as cross-domain, and used nowhere.

    "Not retrievable" is a property of :func:`~accretion.routing.features.summarize_evidence`,
    which is the only reader that turns stored records into evidence about a node. The
    incompatible record must not raise, must not be silently dropped, and must not contribute to
    a single mean — it must be *counted somewhere it cannot influence anything*, which is what
    ``n_cross_domain`` is for.

    The incompatibility is the verification spec hash, which is what "frozen against another
    spec" means and what :func:`~accretion.feedback.experience.record_signature_for` — the
    derivation the projector writes under and the router reads with — is sensitive to. Varying
    anything the signature ignores would make this pass for a record that *is* retrievable.
    """

    store, projector, _, run, _, node, configuration, receipt = await setup_projection()
    await projector.project(
        run=run,
        node=node,
        receipt=receipt,
        configuration=configuration,
        local=make_local(node, VerificationState.PASS),
        final_status=VerificationState.PASS,
        principal=PRINCIPAL,
    )

    incompatible = make_node(
        run, node_key="implement-migration", verification_spec_hash="e" * 64
    )
    summary = summarize_evidence(
        await store.list_experience_records(workspace_id=WORKSPACE_ID),
        signature=record_signature_for(incompatible),
        configuration_hash=configuration.configuration_hash,
        as_of=VERIFIED_AT,
    )

    assert summary.n_cross_domain == 1
    assert summary.n_same_signature == 0
    assert summary.verified_success_rate is None
    assert summary.mean_cost is None
