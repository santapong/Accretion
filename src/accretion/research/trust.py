"""The trust model: what verifier results are allowed to do to a record (AC3-RES-04).

The criterion --- *unverified external text is marked lower trust than deterministic
verifier evidence* --- is met here by a mechanism rather than by a convention, and the
mechanism is deliberately harsher than "lower trust".

**Unverified evidence is structurally unrankable, not merely low-scored.** This mirrors
``CandidateScore.total_score``, which is ``None`` unless verifiers accepted the
candidate. ``EvidenceRecord`` carries the same rule as a model validator: a
``QUARANTINED`` or ``UNVERIFIED`` record must not carry a ``trust_score`` at all.
:func:`rank_evidence` then sorts every unscored record strictly after every scored one.

The difference matters. If unverified text were merely given a low score, a sufficiently
"relevant-looking" piece of external text could still out-rank verified evidence --- a
retrieval system's most familiar failure, and precisely the one an operator would never
see coming, because the ranking would look reasonable the whole way down. Making the
score ``None`` removes the axis on which that overtake could happen. The acceptance test
sets an unverified record's similarity to ``1.0`` and a verified record's to ``0.01``
and requires the verified one to sort first; under a low-score model that test fails.

**The ladder.**

===========================  ==================================================
Verifier outcome             Trust
===========================  ==================================================
any FAIL                     ``QUARANTINED`` --- a positive statement that a
                             verifier caught something, not an absence of proof
citation **and** provenance  ``VERIFIED``, scored in ``[0.7, 1.0]``
provenance only              ``CORROBORATED``, scored in ``[0.3, 0.65]``
neither                      ``UNVERIFIED``, ``trust_score = None``
===========================  ==================================================

``INCONCLUSIVE`` never raises trust. It is not a soft PASS: a citation that could not
be resolved has not been verified, and rewarding it for being unresolvable is exactly
how unverifiable text acquires a verified record's standing. It does not lower trust
either --- that is what FAIL is for.

The two tiers' score bands do not overlap, so ``CORROBORATED`` cannot reach a
``VERIFIED`` record's score no matter how good its quality signal is. Ordering is
therefore a property of the tier, and the score only orders *within* a tier.

**Trust is assigned here and never read from connector-supplied data.** Nothing in this
module reads the candidate's ``payload``. ``EvidenceCandidate`` has no trust field for a
connector to populate, and a payload claiming ``{"trust": "VERIFIED"}`` reaches this
module as inert data --- while :class:`~accretion.verifiers.research.ProvenanceVerifier`
independently FAILs any record where such a key survived normalization at all, which
turns the poisoning attempt into a quarantine rather than a promotion.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from accretion.contracts import (
    EvidenceRecord,
    EvidenceTrust,
    VerificationResult,
    VerificationStatus,
)
from accretion.verifiers.research import CITATION_VERIFIER_ID, PROVENANCE_VERIFIER_ID

__all__ = [
    "TRUST_ORDER",
    "assign_trust",
    "rank_evidence",
]

TRUST_ORDER: dict[EvidenceTrust, int] = {
    EvidenceTrust.QUARANTINED: 0,
    EvidenceTrust.UNVERIFIED: 1,
    EvidenceTrust.CORROBORATED: 2,
    EvidenceTrust.VERIFIED: 3,
}
"""Total order over the trust ladder, low to high. The enum's declaration order is
documentation; this mapping is the thing sorting actually reads."""

#: ``(floor, span)`` per scored tier. Disjoint by construction: ``CORROBORATED`` tops
#: out at 0.65 and ``VERIFIED`` starts at 0.7, so the quality signal can never let a
#: corroborated record overtake a verified one.
_BANDS: dict[EvidenceTrust, tuple[float, float]] = {
    EvidenceTrust.VERIFIED: (0.7, 0.3),
    EvidenceTrust.CORROBORATED: (0.3, 0.35),
}


def _latest_status(results: Iterable[VerificationResult]) -> dict[str, VerificationResult]:
    """One result per verifier, the most recently executed winning.

    A repair loop re-runs verifiers, and a record must be judged on the current
    verdict rather than on the union of every verdict it ever received. Ties on
    ``executed_at`` fall back to the later position in the sequence, so the caller's
    ordering stays authoritative for results stamped inside the same clock tick.
    """

    latest: dict[str, VerificationResult] = {}
    for result in results:
        current = latest.get(result.verifier_id)
        if current is None or result.executed_at >= current.executed_at:
            latest[result.verifier_id] = result
    return latest


def _quality(results: Iterable[VerificationResult]) -> float:
    """Mean score across PASSing verifiers that reported one; ``1.0`` if none did.

    Only PASS contributes. A FAIL has already forced ``QUARANTINED`` before this is
    reached, and an INCONCLUSIVE reports no score to average.
    """

    scores = [
        result.score
        for result in results
        if result.status is VerificationStatus.PASS and result.score is not None
    ]
    if not scores:
        return 1.0
    return sum(scores) / len(scores)


def assign_trust(
    record: EvidenceRecord, results: Sequence[VerificationResult]
) -> EvidenceRecord:
    """Return ``record`` re-labelled from the verifier results that judged *it*.

    The results must come from targets scoped to this record --- an
    ``EXTERNAL_EVIDENCE`` target naming this ``evidence_id`` in ``evidence_refs``.
    Passing a run-scoped result would let one bad record in a batch quarantine every
    record in it, which is a defensible policy but not this one.

    Returns a new record; the input is never mutated, so a caller can compare before
    and after. The trust label on the incoming record is ignored entirely --- it is
    recomputed from the results, so re-running this function is idempotent and a
    record cannot inherit a label it was handed rather than earned.
    """

    latest = _latest_status(results)
    if any(item.status is VerificationStatus.FAIL for item in latest.values()):
        return record.model_copy(
            update={"trust": EvidenceTrust.QUARANTINED, "trust_score": None}
        )

    def passed(verifier_id: str) -> bool:
        result = latest.get(verifier_id)
        return result is not None and result.status is VerificationStatus.PASS

    citation_passed = passed(CITATION_VERIFIER_ID)
    provenance_passed = passed(PROVENANCE_VERIFIER_ID)
    if citation_passed and provenance_passed:
        trust = EvidenceTrust.VERIFIED
    elif provenance_passed:
        # Provenance alone says the record is *attributable*, not that its citation
        # resolves. Attributable-but-unverified is a real, useful middle rung; it is
        # not verification, and the score band says so.
        trust = EvidenceTrust.CORROBORATED
    else:
        # Includes the citation-passed-but-provenance-did-not case. A record whose
        # citation resolves but whose provenance is unproven cannot be told apart
        # from external text, so it does not get scored.
        return record.model_copy(
            update={"trust": EvidenceTrust.UNVERIFIED, "trust_score": None}
        )

    floor, span = _BANDS[trust]
    score = round(floor + span * _quality(latest.values()), 6)
    return record.model_copy(update={"trust": trust, "trust_score": score})


def rank_evidence(records: Iterable[EvidenceRecord]) -> list[EvidenceRecord]:
    """Order evidence for consumption, unscored records strictly last.

    The first key is ``trust_score is None``, which places *every* scored record ahead
    of *every* unscored one before any other comparison is made. No relevance signal,
    similarity, recency, or count is consulted here at all --- there is no argument
    for one, because there is no weighting under which unverified text may lead.

    Within the scored group, higher score first, then higher trust tier, then
    ``evidence_id`` so the order is total and stable across processes. Within the
    unscored group, ``UNVERIFIED`` precedes ``QUARANTINED`` --- both are unrankable
    for citation purposes, but an operator reading the tail should see "we could not
    verify this" before "a verifier caught this".
    """

    return sorted(
        records,
        key=lambda record: (
            record.trust_score is None,
            -(record.trust_score or 0.0),
            -TRUST_ORDER[record.trust],
            record.evidence_id,
        ),
    )
