"""Independent verification: claim-level coverage, structural independence, INCONCLUSIVE.

Four properties are under test, and each is one of the ways a verification layer usually
quietly stops verifying.

**Independence is structural, not nominal.** The producer must not be able to accept its own
work by being invoked under a second name, so the check is on the *session* the verifier ran in.
A test that only compared verifier ids would pass a producer that re-entered itself.

**Coverage is per claim, and absence is not a pass.** A claim whose required evidence class was
never produced is INCONCLUSIVE with coverage 0, and it stays queryable per claim after a
round-trip through the store, because a coverage number that only exists in memory cannot be
audited later.

**INCONCLUSIVE is a third state.** It is neither a PASS the pipeline can accept nor a FAIL the
recovery path can classify, and no reduction in what the verifier examined may ever turn it into
a PASS. The seeded property below shrinks the produced evidence set every way it can and
requires PASS to survive only when every REQUIRED claim is still covered.

**Ingesting the same v0.1 result twice writes one record.** The identity is derived from the
source verification id, so a retried ingest lands on the record that is already stored instead
of forking the history of one verification into two.

Every test asserts against what the store gives back, not against the object handed to it, and
the store is a fresh ``MemoryStore``: no Postgres, no network, no clock but the injected one.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from accretion.contracts import (
    EvidenceClass,
    PrincipalRef,
    PrincipalStatus,
    Project,
    VerificationResult,
    VerificationStatus,
)
from accretion.contracts.refs import EvidenceRef, VerifierRef
from accretion.contracts.routing import (
    Claim,
    Criticality,
    IndependentVerificationResult,
    VerificationSpec,
    VerificationState,
)
from accretion.feedback.verification import (
    EVIDENCE_CLASS_NOT_PRODUCED,
    PRODUCER_IS_VERIFIER,
    SAME_RUNTIME_AS_PRODUCER,
    ClaimCoverageMapper,
    ConflictDetector,
    IndependenceCheck,
    IndependentVerificationRecorder,
)
from accretion.ids import has_prefix, new_id
from accretion.persistence.store import MemoryStore

WORKSPACE_ID = "wks_8G33T24F686H6EJPBHRSFYCC3C"
PROJECT_ID = "prj_8W5DH3HW6DPAFFPBHQ47R21DK9"
PRODUCER_RUNTIME = "claude-code"
VERIFIER_RUNTIME = "deterministic-local"
SIGNED_AT = datetime(2026, 3, 1, 9, 14, tzinfo=UTC)
PRINCIPAL = PrincipalRef(
    principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    display_name="v0.4 M3 feedback test",
    status=PrincipalStatus.ACTIVE,
)
VERIFIER = VerifierRef(
    verifier_contract_id="diff-and-suite",
    implementation_digest="9" * 64,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def frozen_clock(moment: datetime = SIGNED_AT) -> Callable[[], datetime]:
    """The only clock any of this reads.

    ``record`` takes the clock as an argument precisely so a test can pin it: two ingests of
    one verification are byte-identical only if the stamps are, and a library that called
    ``datetime.now()`` could never be idempotent.
    """

    return lambda: moment


def make_claim(
    claim_id: str, criticality: Criticality, *classes: EvidenceClass
) -> Claim:
    return Claim(
        claim_id=claim_id,
        description=f"claim {claim_id}",
        criticality=criticality,
        required_evidence_types=list(classes),
    )


def make_spec(claims: Sequence[Claim]) -> VerificationSpec:
    return VerificationSpec(
        contract_id=new_id("verification_spec"),
        created_by=PRINCIPAL,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        revision=1,
        claims=list(claims),
        accepted_outcomes=[VerificationState.PASS],
    )


def make_evidence(*classes: EvidenceClass) -> dict[str, EvidenceRef]:
    """One sealed reference per evidence class, keyed by id the way a resolver would hand it over.

    A v0.1 result carries evidence *ids*; the class and the digest live on the stored record, so
    the mapper is given the resolved references rather than being left to guess a class.
    """

    evidence: dict[str, EvidenceRef] = {}
    for evidence_class in classes:
        evidence_id = f"evd_{evidence_class.value.lower()}"
        evidence[evidence_id] = EvidenceRef(
            evidence_id=evidence_id,
            evidence_class=evidence_class,
            content_digest=digest(evidence_id),
        )
    return evidence


def make_result(
    *,
    verifier_id: str = "git-diff",
    status: VerificationStatus = VerificationStatus.PASS,
    evidence_ids: Sequence[str] = (),
    verification_id: str | None = None,
) -> VerificationResult:
    return VerificationResult(
        verification_id=verification_id or new_id("verification"),
        run_id="run_9ZAQAYEBNE6NQ3P27YWG8M082Y",
        verifier_id=verifier_id,
        verifier_version="4.2.0",
        target_ref="node-execution",
        status=status,
        evidence_refs=list(evidence_ids),
    )


async def new_store() -> MemoryStore:
    """A fresh store holding the project every v0.4 record here is scoped to.

    ``verification_results.project_id`` is a foreign key into ``projects`` on both backends and
    ``MemoryStore`` mirrors it, so seeding the project is part of setting up the test rather
    than an incidental detail.
    """

    store = MemoryStore()
    await store.create_project(
        Project(
            project_id=PROJECT_ID,
            name="v0.4 M3 feedback",
            repository_path=Path("/tmp/accretion-v04-m3"),
        )
    )
    return store


async def setup_recorded(
    *,
    claims: Sequence[Claim],
    results: Sequence[VerificationResult],
    evidence: Mapping[str, EvidenceRef],
    producer_session_id: str = "ses_producer",
    verifier_session_ids: Mapping[str, str | None] | None = None,
    verifier_runtimes: Mapping[str, str | None] | None = None,
    prior_results: Sequence[IndependentVerificationResult] = (),
    execution_instance_id: str = "run_9ZAQAYEBNE6NQ3P27YWG8M082Y",
    store: MemoryStore | None = None,
) -> tuple[MemoryStore, VerificationSpec, IndependentVerificationResult]:
    """Record one independent verification and persist it. Returns the store, spec and record."""

    spec = make_spec(claims)
    record = IndependentVerificationRecorder().record(
        spec=spec,
        results=results,
        execution_instance_id=execution_instance_id,
        producer_session_id=producer_session_id,
        verifier_session_ids=verifier_session_ids or {},
        verification_spec_hash=spec.content_hash,
        verifier=VERIFIER,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=frozen_clock(),
        created_by=PRINCIPAL,
        evidence=evidence,
        producer_runtime=PRODUCER_RUNTIME,
        verifier_runtimes=verifier_runtimes or {},
        prior_results=prior_results,
    )
    store = store or await new_store()
    await store.put_verification_result(record)
    return store, spec, record


@pytest.mark.acceptance("AC4-M3-003")
async def test_a_verifier_bound_to_the_producer_session_cannot_accept() -> None:
    """The verdict of a verifier running in the producer's session is ERROR, not PASS.

    Both halves are asserted from one setup that differs in a single value — the session the
    verifier ran in — so the test cannot pass by accident: the same verifier, the same evidence
    and the same PASS from the v0.1 layer accept when the session differs and cannot accept when
    it does not. ERROR is also checked against the spec's ``accepted_outcomes``, which
    :class:`VerificationSpec` will not let contain it, so "cannot accept" is a fact about the
    document and not only about this assertion.
    """

    claims = [make_claim("migration-reverses", Criticality.REQUIRED, EvidenceClass.DIGITAL)]
    evidence = make_evidence(EvidenceClass.DIGITAL)
    ids = list(evidence)
    producer_session = "ses_2M9DHDCE4X22HZ9J4B2Y0S3ZP4"

    store, spec, bound = await setup_recorded(
        claims=claims,
        results=[make_result(evidence_ids=ids)],
        evidence=evidence,
        producer_session_id=producer_session,
        verifier_session_ids={"git-diff": producer_session},
        verifier_runtimes={"git-diff": VERIFIER_RUNTIME},
    )
    stored = await store.get_verification_result(bound.contract_id)
    assert stored is not None
    assert stored.status is VerificationState.ERROR
    assert stored.status not in spec.accepted_outcomes
    assert [claim.limitations for claim in stored.claim_results] == [[PRODUCER_IS_VERIFIER]]

    _, _, separate = await setup_recorded(
        claims=claims,
        results=[make_result(evidence_ids=ids)],
        evidence=evidence,
        producer_session_id=producer_session,
        verifier_session_ids={"git-diff": "ses_7Q0R7T8VXKQ1N2C6J5B3W4Y8ZE"},
        verifier_runtimes={"git-diff": VERIFIER_RUNTIME},
    )
    assert separate.status is VerificationState.PASS
    assert separate.claim_results[0].limitations == []


async def test_a_deterministic_verifier_with_no_session_is_independent_and_the_runtime_is_a_warning(
) -> None:
    """OQ-418's two halves, apart: no session is not a violation, and same runtime is a warning.

    A deterministic in-process verifier has no agent session at all. Failing it closed would
    make every v0.1 check structurally dependent by definition, so ``None`` is independent. The
    runtime preference is recorded as a limitation on a verdict that still passes, which is the
    difference between "preferred" and "required".
    """

    evidence = make_evidence(EvidenceClass.DIGITAL)
    _, _, record = await setup_recorded(
        claims=[make_claim("reverses", Criticality.REQUIRED, EvidenceClass.DIGITAL)],
        results=[make_result(evidence_ids=list(evidence))],
        evidence=evidence,
        verifier_session_ids={"git-diff": None},
        verifier_runtimes={"git-diff": PRODUCER_RUNTIME},
    )
    assert record.status is VerificationState.PASS
    assert record.claim_results[0].limitations == [SAME_RUNTIME_AS_PRODUCER]

    verdict = IndependenceCheck().check(
        make_spec([make_claim("c", Criticality.REQUIRED, EvidenceClass.DIGITAL)]).independence,
        "ses_producer",
        None,
        VERIFIER_RUNTIME,
        PRODUCER_RUNTIME,
    )
    assert verdict.independent is True
    assert verdict.limitations == ()
    assert verdict.warnings == ()


@pytest.mark.acceptance("AC4-M3-023")
async def test_claim_level_coverage_is_persisted_per_claim_and_queryable_by_execution_instance(
) -> None:
    """Coverage survives the store per claim, and the records of one execution are findable.

    Two records from two executions go into one workspace; the query is by workspace and the
    filter is by execution instance, which is the shape SDD §11.2's endpoint has to serve. Every
    claim in the spec comes back with its own coverage value, so "which claim was examined, and
    how far" is answerable from storage rather than from the object that was written.

    The spec deliberately mixes covered and uncovered claims. A verifier that produced only
    ``DIGITAL`` evidence reaches two of the three claims, so the persisted coverage has to
    *differ* per claim: an implementation that returned a constant — or that reported the
    unexamined ``PHYSICAL`` claim as fully covered — would be indistinguishable from a correct
    one if every claim in the spec were covered. The covered claim is also checked to carry the
    evidence that covered it, by id and by content digest, because "claim-level evidence
    coverage" without the link back to the evidence is only a number.
    """

    claims = [
        make_claim("migration-reverses", Criticality.REQUIRED, EvidenceClass.DIGITAL),
        make_claim("no-data-loss", Criticality.SUPPORTING, EvidenceClass.DIGITAL),
        make_claim("bench-measured", Criticality.REQUIRED, EvidenceClass.PHYSICAL),
    ]
    evidence = make_evidence(EvidenceClass.DIGITAL)
    first_execution = "run_9ZAQAYEBNE6NQ3P27YWG8M082Y"
    second_execution = "run_1B2C3D4E5F6G7H8J9K0M1N2P3Q"

    store, spec, first = await setup_recorded(
        claims=claims,
        results=[make_result(evidence_ids=list(evidence))],
        evidence=evidence,
        execution_instance_id=first_execution,
    )
    await setup_recorded(
        claims=claims,
        results=[make_result(evidence_ids=list(evidence))],
        evidence=evidence,
        execution_instance_id=second_execution,
        store=store,
    )

    listed = await store.list_verification_results(workspace_id=WORKSPACE_ID)
    assert len(listed) == 2
    for_execution = [
        record for record in listed if record.execution_instance_id == first_execution
    ]
    assert [record.contract_id for record in for_execution] == [first.contract_id]

    stored = for_execution[0]
    assert [claim.claim_id for claim in stored.claim_results] == [
        claim.claim_id for claim in spec.claims
    ]
    by_claim = {claim.claim_id: claim for claim in stored.claim_results}
    assert {claim_id: claim.coverage for claim_id, claim in by_claim.items()} == {
        "migration-reverses": 1.0,
        "no-data-loss": 1.0,
        "bench-measured": 0.0,
    }
    assert all(isinstance(claim.coverage, float) for claim in stored.claim_results)

    covered = by_claim["migration-reverses"]
    assert [ref.evidence_id for ref in covered.evidence_refs] == ["evd_digital"]
    assert covered.evidence_refs[0].content_digest == digest("evd_digital")
    assert by_claim["bench-measured"].evidence_refs == []

    # The uncovered REQUIRED claim is what the record's own status has to answer for.
    assert stored.status is VerificationState.INCONCLUSIVE


@pytest.mark.acceptance("AC4-M3-024")
async def test_inconclusive_is_neither_pass_nor_fail_and_a_lower_coverage_never_flips_it() -> None:
    """A seeded shrink over the produced evidence: less coverage never buys a better verdict.

    The full evidence set covers every claim and the record passes. Each trial then hands the
    same verifier a random subset of that evidence and requires three things: per-claim coverage
    never rises when evidence is removed; the record passes only while every REQUIRED claim is
    still covered; and when it does not pass it is INCONCLUSIVE — not FAIL, because nothing
    failed, and not PASS, because nothing looked.
    """

    claims = [
        make_claim("migration-reverses", Criticality.REQUIRED, EvidenceClass.DIGITAL),
        make_claim("model-agrees", Criticality.REQUIRED, EvidenceClass.SIMULATION),
        make_claim("bench-measured", Criticality.REQUIRED, EvidenceClass.PHYSICAL),
        make_claim("reviewer-signed", Criticality.SUPPORTING, EvidenceClass.HUMAN_ATTESTATION),
    ]
    required_classes = {
        EvidenceClass.DIGITAL,
        EvidenceClass.SIMULATION,
        EvidenceClass.PHYSICAL,
    }
    evidence = make_evidence(*required_classes, EvidenceClass.HUMAN_ATTESTATION)
    every_id = sorted(evidence)

    store, spec, complete = await setup_recorded(
        claims=claims,
        results=[make_result(evidence_ids=every_id)],
        evidence=evidence,
    )
    assert complete.status is VerificationState.PASS
    full_coverage = {claim.claim_id: claim.coverage for claim in complete.claim_results}
    assert full_coverage == dict.fromkeys(full_coverage, 1.0)

    # The direct path: the verifier reached every claim and *reported* INCONCLUSIVE. Coverage
    # is 1.0 throughout, so the translation of the v0.1 verdict is the only thing that can move
    # the result, and neither a positive label nor a negative one may be substituted for it.
    _, reported_spec, reported = await setup_recorded(
        claims=claims,
        results=[make_result(status=VerificationStatus.INCONCLUSIVE, evidence_ids=every_id)],
        evidence=evidence,
        verifier_session_ids={"git-diff": None},
        verifier_runtimes={"git-diff": VERIFIER_RUNTIME},
        store=store,
    )
    said_so = await store.get_verification_result(reported.contract_id)
    assert said_so is not None
    assert said_so.claim_results[0].status is VerificationState.INCONCLUSIVE
    assert said_so.claim_results[0].coverage == 1.0
    assert said_so.status is VerificationState.INCONCLUSIVE
    assert said_so.status is not VerificationState.PASS
    assert said_so.status is not VerificationState.FAIL
    assert said_so.status not in reported_spec.accepted_outcomes

    recorder = IndependentVerificationRecorder()
    rng = random.Random(20260905)
    seen_inconclusive = 0
    for _ in range(64):
        subset = [
            evidence_id for evidence_id in every_id if rng.random() < 0.6
        ]
        record = recorder.record(
            spec=spec,
            results=[make_result(evidence_ids=subset)],
            execution_instance_id="run_9ZAQAYEBNE6NQ3P27YWG8M082Y",
            producer_session_id="ses_producer",
            verifier_session_ids={"git-diff": None},
            verification_spec_hash=spec.content_hash,
            verifier=VERIFIER,
            workspace_id=WORKSPACE_ID,
            project_id=PROJECT_ID,
            clock=frozen_clock(),
            created_by=PRINCIPAL,
            evidence=evidence,
            producer_runtime=PRODUCER_RUNTIME,
            verifier_runtimes={"git-diff": VERIFIER_RUNTIME},
        )
        await store.put_verification_result(record)
        stored = await store.get_verification_result(record.contract_id)
        assert stored is not None

        coverage = {claim.claim_id: claim.coverage for claim in stored.claim_results}
        for claim_id, value in coverage.items():
            assert value <= full_coverage[claim_id]
        produced = {evidence[evidence_id].evidence_class for evidence_id in subset}
        if required_classes <= produced:
            assert stored.status is VerificationState.PASS
            continue
        seen_inconclusive += 1
        assert stored.status is VerificationState.INCONCLUSIVE
        assert stored.status is not VerificationState.PASS
        assert stored.status is not VerificationState.FAIL
        uncovered = [
            claim
            for claim in stored.claim_results
            if claim.claim_id in {"migration-reverses", "model-agrees", "bench-measured"}
            and claim.coverage == 0.0
        ]
        assert uncovered, "a missing required class must still be reported, with zero coverage"
        assert all(claim.status is VerificationState.INCONCLUSIVE for claim in uncovered)
    assert seen_inconclusive >= 16, "the seeded shrink must actually reach the uncovered cases"


async def test_recording_the_same_source_result_twice_is_idempotent() -> None:
    """One v0.1 verification, ingested twice, is one stored record.

    The identity is derived from the source verification id, so the second ingest computes the
    id the first one already wrote; the append-only store then accepts a byte-identical re-put
    as a no-op instead of forking one verification into two histories.
    """

    evidence = make_evidence(EvidenceClass.DIGITAL, EvidenceClass.SIMULATION)
    result = make_result(evidence_ids=list(evidence))
    store, spec, first = await setup_recorded(
        claims=[make_claim("reverses", Criticality.REQUIRED, EvidenceClass.DIGITAL)],
        results=[result],
        evidence=evidence,
    )
    assert has_prefix(first.contract_id, "independent_verification_result")
    assert first.source_verification_id == result.verification_id

    again = IndependentVerificationRecorder().record(
        spec=spec,
        results=[result],
        execution_instance_id="run_9ZAQAYEBNE6NQ3P27YWG8M082Y",
        producer_session_id="ses_producer",
        verifier_session_ids={},
        verification_spec_hash=spec.content_hash,
        verifier=VERIFIER,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=frozen_clock(),
        created_by=PRINCIPAL,
        evidence=evidence,
        producer_runtime=PRODUCER_RUNTIME,
    )
    assert again.contract_id == first.contract_id
    assert again.content_hash == first.content_hash

    await store.put_verification_result(again)
    listed = await store.list_verification_results(workspace_id=WORKSPACE_ID)
    assert [record.contract_id for record in listed] == [first.contract_id]

    # The one surviving record still carries the provenance of what the verifier rested on:
    # every resolvable v0.1 evidence id, sorted, with its digest, filed as *deterministic*
    # evidence. §14.3 keeps model reviews in their own field so an opinion cannot be counted
    # as a measurement, so the empty one is asserted too rather than assumed.
    stored = listed[0]
    assert [ref.evidence_id for ref in stored.deterministic_evidence_refs] == sorted(evidence)
    assert [ref.content_digest for ref in stored.deterministic_evidence_refs] == [
        digest(evidence_id) for evidence_id in sorted(evidence)
    ]
    assert [ref.evidence_class for ref in stored.deterministic_evidence_refs] == [
        EvidenceClass.DIGITAL,
        EvidenceClass.SIMULATION,
    ]
    assert stored.model_review_refs == []


async def test_material_conflict_between_two_verifiers_yields_inconclusive_with_conflict_refs(
) -> None:
    """PASS against FAIL on one REQUIRED claim is unresolved, and says which record it clashes with.

    The failing verdict is recorded *second*, so the assertion is not satisfiable by a recorder
    that simply reports whatever this verifier said: its own claim result is FAIL and the record
    is INCONCLUSIVE, because a contradiction is not a settled failure. A disagreement about a
    SUPPORTING claim is left alone, which is what makes the conflict *material* rather than any
    disagreement at all.
    """

    claims = [
        make_claim("migration-reverses", Criticality.REQUIRED, EvidenceClass.DIGITAL),
        make_claim("no-data-loss", Criticality.SUPPORTING, EvidenceClass.SIMULATION),
    ]
    evidence = make_evidence(EvidenceClass.DIGITAL, EvidenceClass.SIMULATION)
    ids = sorted(evidence)

    store, spec, passing = await setup_recorded(
        claims=claims,
        results=[make_result(verifier_id="git-diff", evidence_ids=ids)],
        evidence=evidence,
    )
    assert passing.status is VerificationState.PASS
    assert passing.conflict_refs == []

    failing = IndependentVerificationRecorder().record(
        spec=spec,
        results=[
            make_result(
                verifier_id="command-suite", status=VerificationStatus.FAIL, evidence_ids=ids
            )
        ],
        execution_instance_id="run_9ZAQAYEBNE6NQ3P27YWG8M082Y",
        producer_session_id="ses_producer",
        verifier_session_ids={"command-suite": "ses_7Q0R7T8VXKQ1N2C6J5B3W4Y8ZE"},
        verification_spec_hash=spec.content_hash,
        verifier=VERIFIER,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=frozen_clock(),
        created_by=PRINCIPAL,
        evidence=evidence,
        producer_runtime=PRODUCER_RUNTIME,
        verifier_runtimes={"command-suite": VERIFIER_RUNTIME},
        prior_results=[passing],
    )
    await store.put_verification_result(failing)
    stored = await store.get_verification_result(failing.contract_id)
    assert stored is not None
    assert stored.conflict_refs == [passing.contract_id]
    assert stored.status is VerificationState.INCONCLUSIVE
    assert {claim.claim_id: claim.status for claim in stored.claim_results} == {
        "migration-reverses": VerificationState.FAIL,
        "no-data-loss": VerificationState.FAIL,
    }

    immaterial = ConflictDetector().detect(
        {
            "ivr_left": [
                claim.model_copy(update={"status": VerificationState.PASS})
                for claim in stored.claim_results
                if claim.claim_id == "no-data-loss"
            ],
            "ivr_right": [
                claim
                for claim in stored.claim_results
                if claim.claim_id == "no-data-loss"
            ],
        },
        spec=spec,
    )
    assert immaterial == []


async def test_an_uncovered_required_claim_is_inconclusive_with_zero_coverage() -> None:
    """An unexamined REQUIRED claim blocks; an unexamined SUPPORTING claim is not a verdict.

    The asymmetry is the whole design. Silence on a REQUIRED claim is recorded, with coverage 0,
    and it makes the record INCONCLUSIVE. Silence on a SUPPORTING claim produces no claim result
    at all: marking it INCONCLUSIVE would let supporting evidence block acceptance, and marking
    it PASS would be the pass-by-absence the reward-hacking controls exist to close.
    """

    claims = [
        make_claim("bench-measured", Criticality.REQUIRED, EvidenceClass.PHYSICAL),
        make_claim("reviewer-signed", Criticality.SUPPORTING, EvidenceClass.HUMAN_ATTESTATION),
        make_claim("migration-reverses", Criticality.REQUIRED, EvidenceClass.DIGITAL),
    ]
    evidence = make_evidence(EvidenceClass.DIGITAL)
    store, _, record = await setup_recorded(
        claims=claims,
        results=[make_result(evidence_ids=list(evidence))],
        evidence=evidence,
        # A distinct runtime, so the only limitation left on a claim is the coverage one.
        verifier_runtimes={"git-diff": VERIFIER_RUNTIME},
    )
    stored = await store.get_verification_result(record.contract_id)
    assert stored is not None
    assert stored.status is VerificationState.INCONCLUSIVE

    by_claim = {claim.claim_id: claim for claim in stored.claim_results}
    assert set(by_claim) == {"bench-measured", "migration-reverses"}
    uncovered = by_claim["bench-measured"]
    assert uncovered.status is VerificationState.INCONCLUSIVE
    assert uncovered.coverage == 0.0
    assert uncovered.evidence_refs == []
    assert uncovered.limitations == [EVIDENCE_CLASS_NOT_PRODUCED]
    assert by_claim["migration-reverses"].status is VerificationState.PASS
    assert by_claim["migration-reverses"].coverage == 1.0


async def test_evidence_the_mapper_cannot_resolve_covers_nothing() -> None:
    """An evidence id with no resolved class is not evidence of the class the claim wanted.

    ``EvidenceRef`` refuses to default its class so that an unstated one cannot quietly become
    the weakest; the mapper honours that by treating an unresolvable id as absent rather than
    as whatever would have been convenient.
    """

    spec = make_spec([make_claim("reverses", Criticality.REQUIRED, EvidenceClass.DIGITAL)])
    mapped = ClaimCoverageMapper(evidence={}).map(
        spec, [make_result(evidence_ids=["evd_unknown_to_this_resolver"])]
    )
    assert [claim.status for claim in mapped] == [VerificationState.INCONCLUSIVE]
    assert [claim.coverage for claim in mapped] == [0.0]


async def test_a_spec_hash_that_is_not_the_digest_of_the_mapped_spec_is_refused() -> None:
    """Coverage may not be filed under the digest of a document it was never computed against."""

    spec = make_spec([make_claim("reverses", Criticality.REQUIRED, EvidenceClass.DIGITAL)])
    evidence = make_evidence(EvidenceClass.DIGITAL)
    with pytest.raises(ValueError, match="is not the digest of the spec"):
        IndependentVerificationRecorder().record(
            spec=spec,
            results=[make_result(evidence_ids=list(evidence))],
            execution_instance_id="run_9ZAQAYEBNE6NQ3P27YWG8M082Y",
            producer_session_id="ses_producer",
            verifier_session_ids={},
            verification_spec_hash="0" * 64,
            verifier=VERIFIER,
            workspace_id=WORKSPACE_ID,
            project_id=PROJECT_ID,
            clock=frozen_clock(),
            created_by=PRINCIPAL,
            evidence=evidence,
            producer_runtime=PRODUCER_RUNTIME,
        )
