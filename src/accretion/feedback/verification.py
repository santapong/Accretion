"""Independent verification results (SDD §7.9, §9.6; ADR-054 a): the pure recorder.

Every v0.1 :class:`~accretion.contracts.VerificationResult` is a verdict with no way to say
*what it examined*, *who examined it* or *how much of the claim it reached*. This module turns
one — or several from the same verifier implementation — into a sealed
:class:`~accretion.contracts.routing.IndependentVerificationResult`, which can say all three:

* **Claim-level coverage.** :class:`ClaimCoverageMapper` reads the frozen
  :class:`~accretion.contracts.routing.VerificationSpec` and answers, per claim, whether the
  evidence the verifier actually produced is of the classes the claim requires.
* **Structural independence.** :class:`IndependenceCheck` compares *sessions*, not verifier
  ids: OQ-418 makes a separate context mandatory, so a verifier that ran inside the producer's
  session has not verified anything no matter what it is called. A distinct *runtime* is only
  preferred, so it is recorded as a limitation on the verdict rather than as a violation of it.
* **INCONCLUSIVE as a third state.** Registry §5.1 is explicit that an inconclusive verdict is a
  judgement about the evidence and an error is the absence of one. Neither is folded into FAIL
  here, and neither can be rounded up to PASS: an uncovered REQUIRED claim is INCONCLUSIVE with
  coverage 0, and no aggregate rule in this module can promote a non-PASS claim to a PASS record.

Nothing here touches a store, a clock or a runtime. ``record`` takes the clock as an argument and
returns a sealed document; persisting it is the caller's job, which is what makes ingesting the
same v0.1 result twice a byte-identical no-op against the append-only v0.4 tables (SDD §8.2).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from accretion.contracts import (
    EvidenceClass,
    PrincipalRef,
    VerificationResult,
    VerificationStatus,
)
from accretion.contracts.refs import EvidenceRef, VerifierRef
from accretion.contracts.routing import (
    ClaimResult,
    Criticality,
    IndependenceRequirements,
    IndependentVerificationResult,
    VerificationSpec,
    VerificationState,
)
from accretion.ids import _PREFIXES, _encode_base32

__all__ = [
    "EVIDENCE_CLASS_NOT_PRODUCED",
    "PRODUCER_IS_VERIFIER",
    "SAME_RUNTIME_AS_PRODUCER",
    "ClaimCoverageMapper",
    "ConflictDetector",
    "IndependenceCheck",
    "IndependenceVerdict",
    "IndependentVerificationRecorder",
]

PRODUCER_IS_VERIFIER = "PRODUCER_IS_VERIFIER"
"""The verifier ran in the session that produced the work (OQ-418 violation)."""

SAME_RUNTIME_AS_PRODUCER = "SAME_RUNTIME_AS_PRODUCER"
"""The verifier ran on the producer's runtime. OQ-418 prefers a distinct one; it does not
require one, so this is a limitation recorded on the verdict and never a violation."""

EVIDENCE_CLASS_NOT_PRODUCED = "EVIDENCE_CLASS_NOT_PRODUCED"
"""No result produced every evidence class the claim requires, so nothing examined it."""

_V01_STATES: Mapping[VerificationStatus, VerificationState] = {
    VerificationStatus.PASS: VerificationState.PASS,
    VerificationStatus.FAIL: VerificationState.FAIL,
    VerificationStatus.INCONCLUSIVE: VerificationState.INCONCLUSIVE,
}

# How bad a claim verdict is. Used only to combine several verdicts about *one* claim, where
# the worst one wins: two results that disagree do not average out, and a failure found by one
# check is not cancelled by another check that did not find it.
_SEVERITY: Mapping[VerificationState, int] = {
    VerificationState.PASS: 0,
    VerificationState.PENDING: 1,
    VerificationState.INCONCLUSIVE: 2,
    VerificationState.FAIL: 3,
    VerificationState.QUARANTINED: 4,
    VerificationState.ERROR: 5,
}


def _derived_id(kind: str, *parts: str) -> str:
    """A ``new_id``-shaped identifier that is a function of its inputs, not of the clock.

    Same shape as :func:`accretion.ids.new_id` — the three-character ADR-055 prefix and 26
    base32 characters — and the same prefix table, read from :mod:`accretion.ids` rather than
    copied, so a renamed prefix cannot leave a second spelling behind here. The digest replaces
    the timestamp *and* the randomness, which is the whole point: re-ingesting one v0.1
    verification has to land on the record that is already stored, and an id containing
    ``time.time()`` would mint a second record of the same event instead.
    """

    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).digest()
    return f"{_PREFIXES[kind]}_{_encode_base32(int.from_bytes(digest, 'big'), 26)}"


def _worst(states: Sequence[VerificationState]) -> VerificationState:
    return max(states, key=lambda state: _SEVERITY[state])


@dataclass(frozen=True, slots=True)
class ClaimCoverageMapper:
    """Claim-level coverage of a frozen spec by what a verifier actually produced.

    ``evidence`` resolves a v0.1 result's ``evidence_refs`` — which are bare ids — into sealed
    :class:`~accretion.contracts.refs.EvidenceRef` values carrying the class and the digest.
    The mapping is injected rather than guessed because
    :class:`~accretion.contracts.refs.EvidenceRef` deliberately refuses to default its class:
    an unstated class must not quietly become the weakest one. An id this mapping does not
    know contributes no class, so a claim it was the only support for comes out *uncovered*
    rather than covered by something unidentified.
    """

    evidence: Mapping[str, EvidenceRef]

    def map(
        self, spec: VerificationSpec, results: Sequence[VerificationResult]
    ) -> list[ClaimResult]:
        """One :class:`ClaimResult` per claim the results reached, in the spec's claim order.

        A claim is *covered* when at least one result produced every evidence class the claim
        requires; coverage is then 1.0 and the claim carries that result's verdict. Coverage is
        binary because the spec's requirement is: a claim asking for ``DIGITAL`` *and*
        ``HUMAN_ATTESTATION`` is not half-examined by a diff, it is unexamined by the standard
        it set, and a fraction would let a comfortable-looking 0.5 stand in for a claim nobody
        attested.

        An uncovered claim's treatment depends on its criticality, and the asymmetry is the
        point. An uncovered ``REQUIRED`` claim is reported INCONCLUSIVE with coverage 0: the
        spec demanded an answer and there is none, and silence must block. An uncovered
        ``SUPPORTING`` claim yields *no result at all* — recording it as INCONCLUSIVE would make
        supporting evidence block acceptance (registry §5.1 makes an unresolved INCONCLUSIVE
        blocking), which is exactly what ``SUPPORTING`` means not to do, and recording it as
        PASS would be the pass-by-absence §14.3 exists to close.

        ``confidence`` is left null. The v0.1 result carries a ``score`` and a
        ``false_accept_risk_estimate``, and neither is a confidence: promoting one would be
        inventing a number the verifier never reported.
        """

        classes: list[frozenset[EvidenceClass]] = []
        refs: list[tuple[EvidenceRef, ...]] = []
        for result in results:
            resolved = tuple(
                self.evidence[evidence_id]
                for evidence_id in result.evidence_refs
                if evidence_id in self.evidence
            )
            refs.append(resolved)
            classes.append(frozenset(ref.evidence_class for ref in resolved))

        claim_results: list[ClaimResult] = []
        for claim in spec.claims:
            required = frozenset(claim.required_evidence_types)
            covering = [index for index, produced in enumerate(classes) if required <= produced]
            if not covering:
                if claim.criticality is Criticality.REQUIRED:
                    claim_results.append(
                        ClaimResult(
                            claim_id=claim.claim_id,
                            status=VerificationState.INCONCLUSIVE,
                            coverage=0.0,
                            limitations=[EVIDENCE_CLASS_NOT_PRODUCED],
                        )
                    )
                continue
            supporting = {
                ref.evidence_id: ref
                for index in covering
                for ref in refs[index]
                if ref.evidence_class in required
            }
            claim_results.append(
                ClaimResult(
                    claim_id=claim.claim_id,
                    status=_worst([_V01_STATES[results[index].status] for index in covering]),
                    evidence_refs=[supporting[key] for key in sorted(supporting)],
                    coverage=1.0,
                )
            )
        return claim_results


@dataclass(frozen=True, slots=True)
class IndependenceVerdict:
    """Whether a verifier was structurally independent of the producer, and what qualified it.

    ``independent`` is the only field a caller may gate on. ``limitations`` are the tokens that
    made it false; ``warnings`` are tokens that qualify a verdict which is still independent,
    and are kept apart so that a preference can never be mistaken for a requirement.
    """

    independent: bool
    limitations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IndependenceCheck:
    """OQ-418's structural producer ≠ verifier rule, as a pure comparison."""

    def check(
        self,
        spec_independence: IndependenceRequirements,
        producer_session_id: str,
        verifier_session_id: str | None,
        verifier_runtime: str | None,
        producer_runtime: str,
    ) -> IndependenceVerdict:
        """Compare *sessions*, then runtimes.

        The violation is "the verifier ran in the producer's session", not "the verifier and the
        producer have the same id": ids are about who, sessions are about where, and OQ-418's
        ``separate_context_required`` is a statement about context. Comparing ids instead would
        pass a producer that re-entered itself under a second name and fail a second, genuinely
        separate invocation of the same verifier implementation — wrong in both directions.

        ``verifier_session_id`` is ``None`` for a deterministic in-process verifier, which has no
        agent session at all. That is not a missing value to fail closed on: a verifier that never
        entered a session cannot have entered the producer's, and treating it as a violation would
        make every v0.1 deterministic check structurally dependent by definition.

        The runtime comparison is a *preference* (OQ-418), so it produces a warning even when it
        cannot be evaluated — an undeclared verifier runtime is not evidence of a distinct one.
        """

        limitations: list[str] = []
        warnings: list[str] = []
        if (
            spec_independence.producer_cannot_self_accept
            and spec_independence.separate_context_required
            and verifier_session_id is not None
            and verifier_session_id == producer_session_id
        ):
            limitations.append(PRODUCER_IS_VERIFIER)
        if spec_independence.distinct_runtime_preferred and (
            verifier_runtime is None or verifier_runtime == producer_runtime
        ):
            warnings.append(SAME_RUNTIME_AS_PRODUCER)
        return IndependenceVerdict(
            independent=not limitations,
            limitations=tuple(limitations),
            warnings=tuple(warnings),
        )


@dataclass(frozen=True, slots=True)
class ConflictDetector:
    """Material disagreement between verifiers about the same claim (SDD §7.9 ``conflict_refs``)."""

    def detect(
        self,
        claim_results_by_verifier: Mapping[str, Sequence[ClaimResult]],
        *,
        spec: VerificationSpec,
    ) -> list[str]:
        """Return, sorted, every key that took part in a PASS-versus-FAIL on a REQUIRED claim.

        The key is the caller's identifier for the report the verdicts came from;
        :class:`IndependentVerificationRecorder` passes the ``contract_id`` of each record, which
        is what ``conflict_refs`` has to point at for the conflict to be followable.

        Only PASS against FAIL counts, and only on a ``REQUIRED`` claim. INCONCLUSIVE beside
        either is not a conflict — it is one verifier declining to decide, which is information
        rather than a contradiction — and a disagreement about a ``SUPPORTING`` claim is
        immaterial by the spec's own definition of what its acceptance depends on. ``spec`` is
        a keyword argument rather than something recovered from the claim results because a
        :class:`ClaimResult` carries no criticality of its own, and a detector that guessed at
        it would decide materiality without the document that defines it.
        """

        required = {
            claim.claim_id for claim in spec.claims if claim.criticality is Criticality.REQUIRED
        }
        passed: dict[str, set[str]] = {}
        failed: dict[str, set[str]] = {}
        for key in sorted(claim_results_by_verifier):
            for claim_result in claim_results_by_verifier[key]:
                if claim_result.claim_id not in required:
                    continue
                if claim_result.status is VerificationState.PASS:
                    passed.setdefault(claim_result.claim_id, set()).add(key)
                elif claim_result.status is VerificationState.FAIL:
                    failed.setdefault(claim_result.claim_id, set()).add(key)
        conflicted: set[str] = set()
        for claim_id, passing in passed.items():
            failing = failed.get(claim_id, set())
            if failing:
                conflicted |= passing | failing
        return sorted(conflicted)


@dataclass(frozen=True, slots=True)
class IndependentVerificationRecorder:
    """v0.1 verification results in, one sealed :class:`IndependentVerificationResult` out."""

    independence: IndependenceCheck = field(default_factory=IndependenceCheck)
    conflicts: ConflictDetector = field(default_factory=ConflictDetector)

    def record(
        self,
        *,
        spec: VerificationSpec,
        results: Sequence[VerificationResult],
        execution_instance_id: str,
        producer_session_id: str,
        verifier_session_ids: Mapping[str, str | None],
        verification_spec_hash: str,
        verifier: VerifierRef,
        workspace_id: str,
        project_id: str,
        clock: Callable[[], datetime],
        created_by: PrincipalRef,
        evidence: Mapping[str, EvidenceRef],
        producer_runtime: str,
        verifier_runtimes: Mapping[str, str | None] | None = None,
        prior_results: Sequence[IndependentVerificationResult] = (),
    ) -> IndependentVerificationResult:
        """Record what the verifier examined, whether it was allowed to, and what it decided.

        ``results`` are the v0.1 results of the single verifier implementation ``verifier``
        names; they are keyed into ``verifier_session_ids`` and ``verifier_runtimes`` by their
        ``verifier_id``, because one implementation can be invoked more than once and the
        session is a property of the invocation.

        **Identity.** ``source_verification_id`` is the lowest v0.1 ``verification_id`` in
        ``results``. The ids are ULID-shaped and time-ordered, so that is the earliest one, and
        picking it by value rather than by position makes the id — and therefore
        ``contract_id``, derived from it — independent of the order the caller happened to
        collect the results in. Ingesting the same verification twice produces the same
        ``contract_id`` and, given the same clock, the same bytes, which the append-only store
        accepts as a no-op instead of writing a second history (SDD §8.2).

        **Status**, in order: ERROR if any claim came back ERROR, which is what an independence
        violation produces and which voids the verdict whatever it said; INCONCLUSIVE if a
        material conflict is open; FAIL if a REQUIRED claim failed; INCONCLUSIVE if a REQUIRED
        claim is inconclusive; PASS only when *every* claim result passed; INCONCLUSIVE
        otherwise.

        An open conflict outranks a FAIL rather than the other way round. When one verifier
        passed a REQUIRED claim and another failed it, what is known is that the evidence
        contradicts itself; reporting the failing side as a settled FAIL would state a verdict
        the record itself is evidence against, and the disagreement has to be resolved rather
        than won. Nothing is weakened by this: FAIL and an unresolved INCONCLUSIVE are both
        blocking under registry §5.1, so the conflict still stops acceptance.

        The final clause is not a formality either — it is the case where a SUPPORTING claim did
        not pass while every REQUIRED one did, and it is why the record cannot be a PASS carrying
        a non-passing claim, which
        :class:`~accretion.contracts.routing.IndependentVerificationResult` refuses outright.

        ``verification_spec_hash`` must be the digest of ``spec``. Recording coverage computed
        against one document under the digest of another would be false provenance, and the
        whole record is only worth reading because the two agree.
        """

        if not results:
            raise ValueError(
                "an independent verification result records at least one v0.1 verification; "
                "with none there is no source id to derive an identity from and nothing to "
                "have been independent about"
            )
        if verification_spec_hash != spec.content_hash:
            raise ValueError(
                f"verification_spec_hash {verification_spec_hash!r} is not the digest of the "
                f"spec whose claims were mapped ({spec.content_hash!r}); the coverage below "
                "would be attributed to a document it was never computed against"
            )

        runtimes: Mapping[str, str | None] = verifier_runtimes or {}
        mapper = ClaimCoverageMapper(evidence=evidence)
        by_verifier: dict[str, list[VerificationResult]] = {}
        for result in results:
            by_verifier.setdefault(result.verifier_id, []).append(result)

        # An unstated session is not a declared absence. A caller that omits a verifier from
        # the mapping has not said "this verifier ran without a session"; treating the gap as
        # independence would let a verifier that ran in the producer's session record a PASS.
        missing = sorted(vid for vid in by_verifier if vid not in verifier_session_ids)
        if missing:
            raise ValueError(
                f"verifier_session_ids declares no session for {missing!r}; an unstated "
                "session is not a declared absence, and treating it as one lets a verifier "
                "that ran in the producer's session record a PASS"
            )

        merged: dict[str, ClaimResult] = {}
        order: list[str] = []
        for verifier_id in sorted(by_verifier):
            verdict = self.independence.check(
                spec.independence,
                producer_session_id,
                verifier_session_ids.get(verifier_id),
                runtimes.get(verifier_id),
                producer_runtime,
            )
            for claim_result in mapper.map(spec, by_verifier[verifier_id]):
                qualified = claim_result.model_copy(
                    update={
                        "status": (
                            VerificationState.ERROR
                            if not verdict.independent
                            else claim_result.status
                        ),
                        "limitations": sorted(
                            {*claim_result.limitations, *verdict.limitations, *verdict.warnings}
                        ),
                    }
                )
                previous = merged.get(qualified.claim_id)
                if previous is None:
                    order.append(qualified.claim_id)
                    merged[qualified.claim_id] = qualified
                    continue
                merged[qualified.claim_id] = previous.model_copy(
                    update={
                        "status": _worst([previous.status, qualified.status]),
                        "coverage": max(previous.coverage, qualified.coverage),
                        "evidence_refs": _merge_refs(
                            previous.evidence_refs, qualified.evidence_refs
                        ),
                        "limitations": sorted({*previous.limitations, *qualified.limitations}),
                    }
                )

        claim_results = [merged[claim_id] for claim_id in order]
        source_verification_id = min(result.verification_id for result in results)
        contract_id = _derived_id("independent_verification_result", source_verification_id)
        conflict_refs = [
            reference
            for reference in self.conflicts.detect(
                {
                    contract_id: claim_results,
                    **{prior.contract_id: prior.claim_results for prior in prior_results},
                },
                spec=spec,
            )
            if reference != contract_id
        ]

        required = {
            claim.claim_id for claim in spec.claims if claim.criticality is Criticality.REQUIRED
        }
        blocking = {
            claim_result.status
            for claim_result in claim_results
            if claim_result.claim_id in required
        }
        if any(result.status is VerificationState.ERROR for result in claim_results):
            status = VerificationState.ERROR
        elif conflict_refs:
            status = VerificationState.INCONCLUSIVE
        elif VerificationState.FAIL in blocking:
            status = VerificationState.FAIL
        elif VerificationState.INCONCLUSIVE in blocking:
            status = VerificationState.INCONCLUSIVE
        elif all(result.status is VerificationState.PASS for result in claim_results):
            status = VerificationState.PASS
        else:
            status = VerificationState.INCONCLUSIVE

        stamped = clock()
        # Built as a payload and validated rather than passed to the constructor as keywords.
        # `canonical.py` types the header's `objective_contract_ref` with a TYPE_CHECKING
        # forward reference into `routing.py`, which imports it back: pydantic resolves the
        # cycle at runtime through `model_rebuild`, but mypy's pydantic plugin gives up on the
        # base class inside it and synthesises an `__init__` carrying only each subclass's own
        # fields — so `contract_id=`, `created_by=` and every other header keyword are reported
        # as unexpected arguments on *every* v0.4 contract. `model_validate` runs exactly the
        # same validation, including `extra="forbid"`, so a misspelled key is still refused.
        payload: dict[str, object] = {
            "contract_id": contract_id,
            "created_at": stamped,
            "created_by": created_by,
            "workspace_id": workspace_id,
            "project_id": project_id,
            "execution_instance_id": execution_instance_id,
            "verification_spec_hash": verification_spec_hash,
            "verifier": verifier,
            "verifier_version": results[0].verifier_version,
            "status": status,
            "claim_results": claim_results,
            "deterministic_evidence_refs": _deterministic_refs(results, evidence),
            "conflict_refs": conflict_refs,
            "source_verification_id": source_verification_id,
            "signed_at": stamped,
        }
        return IndependentVerificationResult.model_validate(payload)


def _merge_refs(left: Sequence[EvidenceRef], right: Sequence[EvidenceRef]) -> list[EvidenceRef]:
    combined = {ref.evidence_id: ref for ref in (*left, *right)}
    return [combined[key] for key in sorted(combined)]


def _deterministic_refs(
    results: Sequence[VerificationResult], evidence: Mapping[str, EvidenceRef]
) -> list[EvidenceRef]:
    """Every resolvable piece of evidence the v0.1 results rested on, sorted and deduplicated.

    All of it is deterministic evidence and none of it is a model review, because every v0.1
    verifier is a deterministic check — a diff, a command suite, an output contract, a
    trajectory policy, a citation check. ``model_review_refs`` stays empty rather than being
    filled from the same list: §14.3 keeps the two apart precisely so that an opinion cannot
    be counted as a measurement, and a recorder that split one source of refs across both
    fields would be inventing the distinction instead of carrying it.
    """

    resolved = {
        evidence_id: evidence[evidence_id]
        for result in results
        for evidence_id in result.evidence_refs
        if evidence_id in evidence
    }
    return [resolved[key] for key in sorted(resolved)]
