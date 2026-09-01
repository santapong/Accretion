from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from accretion.contracts import (
    CitationCheck,
    EvidenceCandidate,
    EvidenceClass,
    EvidenceProvenance,
    EvidenceRecord,
    EvidenceTrust,
    VerificationStatus,
)
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import PostgresStore

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]

RETRIEVED_AT = datetime(2026, 8, 26, 9, 30, tzinfo=UTC)


def _digest(source_id: str) -> str:
    return hashlib.sha256(source_id.encode()).hexdigest()


def _candidate(
    source_id: str,
    *,
    connector_id: str,
    capability_id: str = "research.literature.search",
    query: str = "retrieval augmented verification",
) -> EvidenceCandidate:
    return EvidenceCandidate(
        candidate_id=new_id("evidence_candidate"),
        evidence_class=EvidenceClass.EXTERNAL_SOURCE,
        title=f"A paper identified by {source_id}",
        snippet="Abstract fragment retained for the operator to read.",
        authors=["A. Author", "B. Coauthor"],
        identifiers={"doi": f"10.1000/{source_id}"},
        published_at=datetime(2026, 1, 4, tzinfo=UTC),
        content_digest=_digest(source_id),
        provenance=EvidenceProvenance(
            connector_id=connector_id,
            capability_id=capability_id,
            query=query,
            retrieved_at=RETRIEVED_AT,
            source_id=source_id,
            binding_id=f"cbd_{connector_id}",
            connection_id=f"con_{connector_id}",
            source_uri=f"https://example.invalid/{source_id}",
        ),
        payload={"raw": {"id": source_id}},
    )


async def test_v03_m5_research_evidence_round_trip() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    # Uuid-suffixed so the file is re-runnable against a database it already wrote to.
    suffix = uuid.uuid4().hex[:12]
    run_id = f"run_m5_{suffix}"
    other_run_id = f"run_m5_other_{suffix}"

    unverified = EvidenceRecord(
        evidence_id=new_id("evidence"),
        run_id=run_id,
        node_id="research-search",
        candidate=_candidate(f"src-a-{suffix}", connector_id="research-openalex"),
        trust=EvidenceTrust.UNVERIFIED,
        created_at=RETRIEVED_AT,
    )
    verified = EvidenceRecord(
        evidence_id=new_id("evidence"),
        run_id=run_id,
        node_id="research-verify",
        candidate=_candidate(
            f"src-b-{suffix}",
            connector_id="research-crossref",
            capability_id="research.citation.verify",
            query="doi 10.1000/src-b",
        ),
        trust=EvidenceTrust.VERIFIED,
        trust_score=0.92,
        citation_checks=[
            CitationCheck(
                check_id=new_id("citation_check"),
                verifier_id="research.citation.resolver",
                claimed_identifier=f"10.1000/src-b-{suffix}",
                resolved_identifier=f"10.1000/src-b-{suffix}",
                status=VerificationStatus.PASS,
                detail="resolver returned the claimed identifier",
                checked_at=RETRIEVED_AT + timedelta(seconds=5),
            )
        ],
        verification_ids=[new_id("verification")],
        created_at=RETRIEVED_AT + timedelta(seconds=10),
    )
    try:
        assert await store.list_research_evidence(run_id) == []
        await store.save_research_evidence(unverified)
        await store.save_research_evidence(verified)

        # Deterministic order: (created_at, evidence_id), so the earlier write leads.
        assert await store.list_research_evidence(run_id) == [unverified, verified]
        assert await store.list_research_evidence(
            run_id, capability_id="research.citation.verify"
        ) == [verified]
        assert await store.list_research_evidence(other_run_id) == []

        stored = await store.get_research_evidence_by_digest(
            run_id, _digest(f"src-a-{suffix}")
        )
        assert stored == unverified
        # AC3-RES-03's five fields survive the round trip as themselves.
        assert stored is not None
        assert stored.candidate.provenance.connector_id == "research-openalex"
        assert stored.candidate.provenance.capability_id == "research.literature.search"
        assert stored.candidate.provenance.query == "retrieval augmented verification"
        assert stored.candidate.provenance.retrieved_at == RETRIEVED_AT
        assert stored.candidate.provenance.source_id == f"src-a-{suffix}"

        # A digest is scoped to its run: the same content under another run is absent.
        assert (
            await store.get_research_evidence_by_digest(
                other_run_id, _digest(f"src-a-{suffix}")
            )
            is None
        )
        assert await store.get_research_evidence_by_digest(run_id, "0" * 64) is None

        # Re-labelling after verification updates in place, keeping identity.
        promoted = unverified.model_copy(
            update={"trust": EvidenceTrust.CORROBORATED, "trust_score": 0.41}
        )
        await store.save_research_evidence(promoted)
        assert await store.list_research_evidence(run_id) == [promoted, verified]
    finally:
        await engine.dispose()
