from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from accretion.contracts import (
    AuthorizationOutcome,
    CapabilityAuthorization,
    CapabilityExecutionResult,
    CapabilityExecutionStatus,
    CapabilityRequest,
    EvidenceCandidate,
    EvidenceClass,
    EvidenceProvenance,
    EvidenceRecord,
    EvidenceTrust,
)
from accretion.ids import new_id

RETRIEVED_AT = datetime(2026, 8, 26, 9, 30, tzinfo=UTC)


def _candidate(source_id: str, *, connector_id: str) -> EvidenceCandidate:
    return EvidenceCandidate(
        candidate_id=new_id("evidence_candidate"),
        evidence_class=EvidenceClass.EXTERNAL_SOURCE,
        title=f"A paper identified by {source_id}",
        content_digest=hashlib.sha256(source_id.encode()).hexdigest(),
        provenance=EvidenceProvenance(
            connector_id=connector_id,
            capability_id="research.literature.search",
            query="retrieval augmented verification",
            retrieved_at=RETRIEVED_AT,
            source_id=source_id,
        ),
    )


async def test_v03_m5_unverified_evidence_cannot_carry_a_trust_score() -> None:
    """AC3-RES-04's structural half: unverified evidence is unrankable, not low-ranked."""
    candidate = _candidate("src-unrankable", connector_id="research-openalex")
    for trust in (EvidenceTrust.UNVERIFIED, EvidenceTrust.QUARANTINED):
        with pytest.raises(ValueError, match="must not carry a trust score"):
            EvidenceRecord(
                evidence_id=new_id("evidence"),
                run_id="run_unrankable",
                candidate=candidate,
                trust=trust,
                trust_score=0.99,
            )


async def test_v03_m5_provenance_requires_all_five_fields() -> None:
    """AC3-RES-03 is enforced by the type: each field is required, none may be None."""
    complete = {
        "connector_id": "research-openalex",
        "capability_id": "research.literature.search",
        "query": "graph neural verification",
        "retrieved_at": RETRIEVED_AT,
        "source_id": "src-complete",
    }
    assert EvidenceProvenance(**complete).source_id == "src-complete"
    for field in complete:
        missing = {key: value for key, value in complete.items() if key != field}
        with pytest.raises(ValueError):
            EvidenceProvenance(**missing)
        with pytest.raises(ValueError):
            EvidenceProvenance(**{**complete, field: None})


async def test_v03_m5_old_shape_capability_result_still_validates() -> None:
    """Regression: results persisted before M5 carry none of the new keys.

    The literal below is what a v0.3 M4 database row deserializes to. If any of
    the four additive fields ever stops defaulting, this stops validating.
    """
    old_shape = json.loads(
        """
        {
          "request": {
            "schema_version": "1.0",
            "request_id": "cpr_legacy",
            "run_id": "run_legacy",
            "node_id": "node-1",
            "capability_id": "fs.read",
            "capability_version": "1.0.0",
            "arguments": {"path": "README.md"},
            "declared_reason": "read the file",
            "idempotency_key": null,
            "created_at": "2026-08-01T00:00:00Z"
          },
          "authorization": {
            "outcome": "ALLOW",
            "policy_id": "local-capability-policy",
            "policy_version": "1",
            "reason": "permitted",
            "approval_id": null
          },
          "status": "SUCCEEDED",
          "output": {"content": "hello"},
          "error": null,
          "side_effect_operation_id": null,
          "completed_at": "2026-08-01T00:00:01Z"
        }
        """
    )
    result = CapabilityExecutionResult.model_validate(old_shape)
    assert result.connector_id is None
    assert result.binding_id is None
    assert result.connection_id is None
    assert result.source_ids == []

    # And the new fields are genuinely additive, not merely tolerated.
    enriched = CapabilityExecutionResult(
        request=CapabilityRequest.model_validate(old_shape["request"]),
        authorization=CapabilityAuthorization(
            outcome=AuthorizationOutcome.ALLOW,
            policy_id="local-capability-policy",
            policy_version="1",
            reason="permitted",
        ),
        status=CapabilityExecutionStatus.SUCCEEDED,
        connector_id="research-openalex",
        binding_id="cbd_openalex",
        connection_id="con_openalex",
        source_ids=["src-a", "src-b"],
    )
    assert enriched.model_dump(mode="json")["source_ids"] == ["src-a", "src-b"]
