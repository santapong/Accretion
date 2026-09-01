"""Deterministic verifiers over gathered research evidence (v0.3 M5, AC3-RES-04).

Three verifiers, all shaped exactly like :mod:`accretion.verifiers.output_contract`:
a ``verifier_id``, a ``verifier_version``, and one ``async verify(target, context)``
returning a :class:`~accretion.contracts.VerificationResult`. They join the same
:class:`~accretion.verifiers.registry.VerifierRegistry` as the pre-M5 three and are
selected by the same acceptance policy machinery. Nothing about the verification
seam is special-cased for research.

**The load-bearing decision.** :class:`CitationVerifier` verifies a claimed citation
against the *retrieved record already in the Evidence Store* --- never against the
live internet. Two consequences follow, and both are the point:

* it is **deterministic**. The same stored record yields the same verdict on every
  run, on every machine, forever. A verifier that reached out to a DOI resolver would
  be a network probe wearing a verifier's clothes, and its PASS would expire the
  moment the upstream did;
* it is **evidence rather than external text**. The thing being compared was itself
  retrieved through the capability gateway, with AC3-RES-03 provenance stamped on it
  by the gateway's own clock. The verifier's judgement therefore inherits that
  provenance instead of introducing a fresh, unprovenanced fact.

**Connector verdicts are data, not authority.** Both upstream backends helpfully
volunteer their own answer --- backend A returns ``resolves``, backend B returns
``title_agrees``. Neither is read as a verdict. The verifier recomputes the comparison
from the identifiers on the record, because a connector that could grade its own
citation could also grade it ``true``. Where such a self-graded key survives into the
stored payload at all, :class:`ProvenanceVerifier` raises it as an ERROR: the
normalizer's allowlist is supposed to have dropped it, and its presence means the
allowlist leaked.

Evidence is read through an injected :class:`EvidenceLookup` --- the constructor
injection :class:`~accretion.verifiers.command.CommandVerifier` already establishes as
the house pattern for a verifier that needs a collaborator. In production that
collaborator is the real :class:`~accretion.persistence.store.StateStore`.
"""

from __future__ import annotations

import time
from typing import Protocol

from accretion.contracts import (
    CitationCheck,
    EvidenceRecord,
    Finding,
    FindingSeverity,
    VerificationContext,
    VerificationResult,
    VerificationStatus,
    VerificationTarget,
    VerificationTargetKind,
)
from accretion.ids import new_id
from accretion.verifiers.base import Verifier
from accretion.verifiers.results import finding, verification_result

__all__ = [
    "CITATION_VERIFIER_ID",
    "EVIDENCE_QUALITY_VERIFIER_ID",
    "PROVENANCE_VERIFIER_ID",
    "RESEARCH_VERIFIER_IDS",
    "CitationVerifier",
    "EvidenceLookup",
    "EvidenceQualityVerifier",
    "ProvenanceVerifier",
    "citation_check",
    "research_verifiers",
]

CITATION_VERIFIER_ID = "research-citation"
PROVENANCE_VERIFIER_ID = "research-provenance"
EVIDENCE_QUALITY_VERIFIER_ID = "research-evidence-quality"

RESEARCH_VERIFIER_IDS: frozenset[str] = frozenset(
    {CITATION_VERIFIER_ID, PROVENANCE_VERIFIER_ID, EVIDENCE_QUALITY_VERIFIER_ID}
)
"""The ids the run manager maps onto ``EXTERNAL_EVIDENCE`` targets.

Without this set a research verifier id falls through to the ``COMMAND_SUITE``
default, and every research verifier would then reject its own target as a kind
mismatch --- an INCONCLUSIVE that looks like a configuration problem and is really a
missing branch.
"""

_MIN_SNIPPET = 16
"""Below this, a snippet cannot support a claim; it is a title echoed twice."""

#: Payload keys a connector must never be able to author. The normalizer's allowlist
#: is supposed to drop them; finding one here means the allowlist leaked.
_SELF_GRADED_KEYS: tuple[str, ...] = ("trust", "trust_score", "verified", "verification")


class EvidenceLookup(Protocol):
    """The slice of the state store these verifiers read.

    A Protocol rather than the whole ``StateStore`` so a verifier cannot reach for
    anything else, and so the injected collaborator stays substitutable.
    """

    async def list_research_evidence(
        self, run_id: str, capability_id: str | None = None
    ) -> list[EvidenceRecord]: ...


def _identifier(record: EvidenceRecord, key: str) -> str | None:
    value = record.candidate.identifiers.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _payload_identifier(record: EvidenceRecord, key: str) -> str | None:
    value = record.candidate.payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def citation_check(record: EvidenceRecord) -> CitationCheck:
    """Compare one record's *claimed* identifier against its *resolved* one.

    Both operands come off the stored record. The claimed identifier is what the
    citing side asserted; the resolved identifier is what the retrieved record
    actually registers. Where the record carries only one of the two there is nothing
    to compare and the check is INCONCLUSIVE --- which, by the trust model, never
    raises trust and never lowers it either.
    """

    claimed = _payload_identifier(record, "claimed_doi") or _identifier(record, "doi")
    resolved = _payload_identifier(record, "registered_doi") or _identifier(record, "doi")
    if claimed is None:
        status = VerificationStatus.INCONCLUSIVE
        detail = "The record claims no citation identifier, so there is nothing to verify."
    elif resolved is None:
        status = VerificationStatus.INCONCLUSIVE
        detail = "The retrieved record registers no identifier to resolve the claim against."
    elif claimed.casefold() == resolved.casefold():
        status = VerificationStatus.PASS
        detail = "The claimed identifier matches the one the retrieved record registers."
    else:
        status = VerificationStatus.FAIL
        detail = (
            f"The record claims {claimed!r} but the retrieved record registers {resolved!r}."
        )
    return CitationCheck(
        check_id=new_id("citation_check"),
        verifier_id=CITATION_VERIFIER_ID,
        claimed_identifier=claimed or "(none)",
        resolved_identifier=resolved,
        status=status,
        detail=detail,
        checked_at=record.candidate.provenance.retrieved_at,
    )


class _EvidenceVerifierBase:
    """Shared target handling: reject the wrong kind, then scope to the run."""

    verifier_id: str
    verifier_version: str

    def __init__(self, evidence: EvidenceLookup) -> None:
        self._evidence = evidence

    def _mismatch(
        self, target: VerificationTarget, started_at: float
    ) -> VerificationResult:
        return verification_result(
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            target=target,
            status=VerificationStatus.INCONCLUSIVE,
            started_at=started_at,
            findings=[
                finding(
                    "TARGET_KIND_MISMATCH",
                    FindingSeverity.ERROR,
                    f"Expected EXTERNAL_EVIDENCE target, received {target.kind.value}.",
                )
            ],
        )

    def _empty(self, target: VerificationTarget, started_at: float) -> VerificationResult:
        return verification_result(
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            target=target,
            status=VerificationStatus.INCONCLUSIVE,
            started_at=started_at,
            findings=[
                finding(
                    "EVIDENCE_MISSING",
                    FindingSeverity.WARNING,
                    "No stored research evidence matched the target.",
                )
            ],
        )

    async def _records(self, target: VerificationTarget) -> list[EvidenceRecord]:
        """Every record the run gathered, narrowed to the target's ``evidence_refs``.

        An empty ``evidence_refs`` means the whole run, which is what a run-scoped
        acceptance policy builds. A populated one names individual records, which is
        what the per-record trust assignment in :mod:`accretion.research.trust` needs.
        Deterministic store ordering is preserved either way.
        """

        records = await self._evidence.list_research_evidence(target.run_id)
        if not target.evidence_refs:
            return records
        wanted = set(target.evidence_refs)
        return [record for record in records if record.evidence_id in wanted]

    @staticmethod
    def _evidence_ref(record: EvidenceRecord) -> str:
        return f"evidence-sha256:{record.evidence_id}:{record.content_digest}"


class CitationVerifier(_EvidenceVerifierBase):
    """Verify claimed citations against the retrieved records, not the internet."""

    verifier_id = CITATION_VERIFIER_ID
    verifier_version = "research-citation-v1"

    async def verify(
        self, target: VerificationTarget, context: VerificationContext
    ) -> VerificationResult:
        started_at = time.monotonic()
        if target.kind is not VerificationTargetKind.EXTERNAL_EVIDENCE:
            return self._mismatch(target, started_at)
        records = await self._records(target)
        if not records:
            return self._empty(target, started_at)

        findings: list[Finding] = []
        evidence_refs: list[str] = []
        checks = [(record, citation_check(record)) for record in records]
        for record, check in checks:
            ref = self._evidence_ref(record)
            evidence_refs.append(ref)
            if check.status is VerificationStatus.FAIL:
                findings.append(
                    finding(
                        "CITATION_IDENTIFIER_MISMATCH",
                        FindingSeverity.ERROR,
                        check.detail,
                        path=record.evidence_id,
                        evidence_ref=ref,
                    )
                )
            elif check.status is VerificationStatus.INCONCLUSIVE:
                findings.append(
                    finding(
                        "CITATION_NOT_RESOLVABLE",
                        FindingSeverity.WARNING,
                        check.detail,
                        path=record.evidence_id,
                        evidence_ref=ref,
                    )
                )

        passed = [check for _, check in checks if check.status is VerificationStatus.PASS]
        if any(check.status is VerificationStatus.FAIL for _, check in checks):
            status = VerificationStatus.FAIL
            score: float | None = 0.0
        elif not passed:
            # Nothing was actually verified. INCONCLUSIVE, and by the trust model an
            # INCONCLUSIVE never raises trust — an unresolvable citation must not be
            # rewarded for being unresolvable.
            status = VerificationStatus.INCONCLUSIVE
            score = None
        else:
            status = VerificationStatus.PASS
            score = round(len(passed) / len(checks), 6)
        return verification_result(
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            target=target,
            status=status,
            started_at=started_at,
            findings=findings,
            evidence_refs=evidence_refs,
            score=score,
            false_accept_risk_estimate=0.05 if status is VerificationStatus.PASS else None,
        )


class ProvenanceVerifier(_EvidenceVerifierBase):
    """Check that every record can say where it came from, and that nobody forged it.

    AC3-RES-03's five fields are already required by ``EvidenceProvenance``, so this
    verifier's job is not to re-run pydantic. It is to catch the two things the type
    cannot: a provenance field that is present but vacuous, and a *connector-authored*
    trust claim that survived normalization into the stored payload.
    """

    verifier_id = PROVENANCE_VERIFIER_ID
    verifier_version = "research-provenance-v1"

    async def verify(
        self, target: VerificationTarget, context: VerificationContext
    ) -> VerificationResult:
        started_at = time.monotonic()
        if target.kind is not VerificationTargetKind.EXTERNAL_EVIDENCE:
            return self._mismatch(target, started_at)
        records = await self._records(target)
        if not records:
            return self._empty(target, started_at)

        findings: list[Finding] = []
        evidence_refs: list[str] = []
        for record in records:
            ref = self._evidence_ref(record)
            evidence_refs.append(ref)
            provenance = record.candidate.provenance
            missing = sorted(
                name
                for name, value in (
                    ("connector_id", provenance.connector_id),
                    ("capability_id", provenance.capability_id),
                    ("query", provenance.query),
                    ("source_id", provenance.source_id),
                )
                if not value.strip()
            )
            if missing:
                findings.append(
                    finding(
                        "PROVENANCE_FIELD_EMPTY",
                        FindingSeverity.ERROR,
                        f"Provenance fields are present but empty: {', '.join(missing)}.",
                        path=record.evidence_id,
                        evidence_ref=ref,
                    )
                )
            if provenance.retrieved_at.tzinfo is None:
                findings.append(
                    finding(
                        "PROVENANCE_CLOCK_NAIVE",
                        FindingSeverity.ERROR,
                        "retrieved_at carries no timezone, so the retrieval instant "
                        "is not a point in time.",
                        path=record.evidence_id,
                        evidence_ref=ref,
                    )
                )
            leaked = sorted(
                key for key in _SELF_GRADED_KEYS if key in record.candidate.payload
            )
            if leaked:
                findings.append(
                    finding(
                        "CONNECTOR_SUPPLIED_TRUST",
                        FindingSeverity.ERROR,
                        "Connector-authored trust keys survived normalization: "
                        f"{', '.join(leaked)}. Trust is assigned here, never received.",
                        path=record.evidence_id,
                        evidence_ref=ref,
                    )
                )

        if any(item.severity is FindingSeverity.ERROR for item in findings):
            status = VerificationStatus.FAIL
            score: float | None = 0.0
        else:
            status = VerificationStatus.PASS
            score = 1.0
        return verification_result(
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            target=target,
            status=status,
            started_at=started_at,
            findings=findings,
            evidence_refs=evidence_refs,
            score=score,
            false_accept_risk_estimate=0.05 if status is VerificationStatus.PASS else None,
        )


class EvidenceQualityVerifier(_EvidenceVerifierBase):
    """Grade whether a record is substantive enough to be cited at all.

    Distinct from provenance: a record can say exactly where it came from and still
    be a bare title with no identifier and no text. Such a record is retrievable but
    not citable, and the score it contributes is what keeps it below a record that is
    both.
    """

    verifier_id = EVIDENCE_QUALITY_VERIFIER_ID
    verifier_version = "research-evidence-quality-v1"

    async def verify(
        self, target: VerificationTarget, context: VerificationContext
    ) -> VerificationResult:
        started_at = time.monotonic()
        if target.kind is not VerificationTargetKind.EXTERNAL_EVIDENCE:
            return self._mismatch(target, started_at)
        records = await self._records(target)
        if not records:
            return self._empty(target, started_at)

        findings: list[Finding] = []
        evidence_refs: list[str] = []
        digests: dict[str, str] = {}
        substantive = 0
        for record in records:
            ref = self._evidence_ref(record)
            evidence_refs.append(ref)
            candidate = record.candidate
            defects: list[str] = []
            if not candidate.identifiers:
                defects.append("carries no identifier")
            if len(candidate.snippet.strip()) < _MIN_SNIPPET:
                defects.append("carries no usable snippet")
            if not candidate.authors:
                defects.append("names no author")
            first = digests.setdefault(candidate.content_digest, record.evidence_id)
            if first != record.evidence_id:
                findings.append(
                    finding(
                        "EVIDENCE_DUPLICATED",
                        FindingSeverity.ERROR,
                        f"Content digest is already stored as {first}; the Evidence "
                        "Store should have collapsed these into one record.",
                        path=record.evidence_id,
                        evidence_ref=ref,
                    )
                )
            if defects:
                findings.append(
                    finding(
                        "EVIDENCE_NOT_SUBSTANTIVE",
                        FindingSeverity.WARNING,
                        f"Record {', '.join(defects)}.",
                        path=record.evidence_id,
                        evidence_ref=ref,
                    )
                )
            else:
                substantive += 1

        if any(item.severity is FindingSeverity.ERROR for item in findings):
            status = VerificationStatus.FAIL
            score: float | None = 0.0
        elif substantive == 0:
            status = VerificationStatus.INCONCLUSIVE
            score = None
        else:
            status = VerificationStatus.PASS
            score = round(substantive / len(records), 6)
        return verification_result(
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            target=target,
            status=status,
            started_at=started_at,
            findings=findings,
            evidence_refs=evidence_refs,
            score=score,
            false_accept_risk_estimate=0.1 if status is VerificationStatus.PASS else None,
        )


def research_verifiers(evidence: EvidenceLookup) -> tuple[Verifier, ...]:
    """The three research verifiers, in registry order.

    Returned as a group so the API process cannot wire two of the three and leave the
    third dark --- which is the shape the standing gap took before M5: the API built
    its registry from a hardcoded list and every research verifier id resolved to
    ``VerifierUnavailableError`` in production while passing in tests.
    """

    return (
        CitationVerifier(evidence),
        EvidenceQualityVerifier(evidence),
        ProvenanceVerifier(evidence),
    )
