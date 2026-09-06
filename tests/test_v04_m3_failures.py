"""The failure taxonomy: one owner per code, and a classification that reproduces exactly.

Two properties are worth proving about a rule table, and neither of them is "the rules run".

**The table is what it says it is.** The golden table below is written out here, by hand, as
literal rule ids, types, owners, layers, action codes, retry flags and confidences. It is
deliberately *not* derived from :data:`~accretion.feedback.failures.RULES`, because a test
that reads the table it is checking proves only that the module agrees with itself: it would
stay green through a reordering, a re-owned code, or a rule that quietly became retryable.
Several rows carry overlapping signals — a capability error accompanied by a policy denial, a
verification conflict on top of a provider failure — so precedence is observable in the
outcome and swapping two rows turns a value red rather than merely reshuffling a listing.

**A classification reproduces.** §9.7 compares a failure against earlier ones, which is only
meaningful if the same signals through the same clock produce the same event — the same
``contract_id`` and the same ``content_hash``, not merely the same owner. That is what makes
the derived id worth having instead of :func:`~accretion.ids.new_id`, and the last test
proves it, together with the seal that makes the record tamper-evident.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from accretion.contracts import PrincipalRef, PrincipalStatus
from accretion.contracts.canonical import content_hash
from accretion.contracts.routing import FailureEvent, FailureOwner, FailureType, VerificationState
from accretion.feedback.failures import (
    RULES,
    FailureClassifier,
    FailureSignals,
)
from accretion.ids import has_prefix

WORKSPACE_ID = "wks_8G33T24F686H6EJPBHRSFYCC3C"
PROJECT_ID = "prj_8W5DH3HW6DPAFFPBHQ47R21DK9"
EXECUTION_INSTANCE_ID = "run_9ZAQAYEBNE6NQ3P27YWG8M082Y"
FIXED_TIME = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
HASH_A = "73828f6546f6e3b4730d76d7cf0530c39e50d9b0c6b605e42c52148ff6c91a44"
HASH_B = "f7bdc2df4e6677e0bc988e36e64b69691b293547c43f93dfbf3523db55c2652b"


def principal() -> PrincipalRef:
    return PrincipalRef(
        principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
        display_name="v0.4 feedback service",
        status=PrincipalStatus.ACTIVE,
    )


def classifier() -> FailureClassifier:
    return FailureClassifier(created_by=principal())


def fixed_clock() -> datetime:
    return FIXED_TIME


def classify(signals: FailureSignals) -> FailureEvent:
    return classifier().classify(
        signals=signals,
        execution_instance_id=EXECUTION_INSTANCE_ID,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=fixed_clock,
    )


@dataclass(frozen=True)
class GoldenRow:
    """One hand-written expectation. Every value here is literal, none is imported."""

    case: str
    signals: FailureSignals
    rule_id: str
    failure_type: FailureType
    owner: FailureOwner
    affected_layer: str
    action_code: str
    retryable: bool
    confidence: float


GOLDEN_TABLE: tuple[GoldenRow, ...] = (
    GoldenRow(
        case="a quarantined verification outranks everything, including a denial",
        signals=FailureSignals(
            local_status=VerificationState.QUARANTINED,
            policy_decision="DENY",
            error_code="PROVIDER_FAILURE",
        ),
        rule_id="VERIFICATION_QUARANTINED",
        failure_type=FailureType.POLICY_RISK,
        owner=FailureOwner.SAFETY,
        affected_layer="safety-policy",
        action_code="ESCALATE_TO_HUMAN",
        retryable=False,
        confidence=0.99,
    ),
    GoldenRow(
        case="a capability error caused by a denial is typed CAPABILITY, owned by AUTHORITY",
        signals=FailureSignals(error_code="CAPABILITY_UNKNOWN", policy_decision="DENY"),
        rule_id="CAPABILITY_DENIED_BY_POLICY",
        failure_type=FailureType.CAPABILITY,
        owner=FailureOwner.AUTHORITY,
        affected_layer="authority-policy",
        action_code="ESCALATE_TO_HUMAN",
        retryable=False,
        confidence=0.90,
    ),
    GoldenRow(
        case="a reauth error caused by a denial takes the same precedence",
        signals=FailureSignals(error_code="REQUIRE_REAUTH", policy_decision="DENY"),
        rule_id="CAPABILITY_DENIED_BY_POLICY",
        failure_type=FailureType.CAPABILITY,
        owner=FailureOwner.AUTHORITY,
        affected_layer="authority-policy",
        action_code="ESCALATE_TO_HUMAN",
        retryable=False,
        confidence=0.90,
    ),
    GoldenRow(
        case="a bare policy denial is a policy risk owned by AUTHORITY",
        signals=FailureSignals(policy_decision="DENY", error_message="policy refused the call"),
        rule_id="POLICY_DENIED",
        failure_type=FailureType.POLICY_RISK,
        owner=FailureOwner.AUTHORITY,
        affected_layer="authority-policy",
        action_code="ESCALATE_TO_HUMAN",
        retryable=False,
        confidence=0.95,
    ),
    GoldenRow(
        case="a required human approval is a policy risk, never an automatic retry",
        signals=FailureSignals(policy_decision="REQUIRE_APPROVAL"),
        rule_id="HUMAN_APPROVAL_REQUIRED",
        failure_type=FailureType.POLICY_RISK,
        owner=FailureOwner.AUTHORITY,
        affected_layer="authority-policy",
        action_code="ESCALATE_TO_HUMAN",
        retryable=False,
        confidence=0.90,
    ),
    GoldenRow(
        case="a verifier conflict outranks the provider failure and the schema findings",
        signals=FailureSignals(
            conflict_count=2,
            schema_findings=3,
            error_code="PROVIDER_FAILURE",
        ),
        rule_id="VERIFICATION_CONFLICT",
        failure_type=FailureType.VERIFICATION_CONFLICT,
        owner=FailureOwner.VERIFICATION,
        affected_layer="verification",
        action_code="RESOLVE_EVIDENCE",
        retryable=False,
        confidence=0.90,
    ),
    GoldenRow(
        case="runtime version drift is the router's to fix",
        signals=FailureSignals(error_code="RUNTIME_VERSION_DRIFT"),
        rule_id="RUNTIME_VERSION_DRIFT",
        failure_type=FailureType.CONFIGURATION,
        owner=FailureOwner.CONFIGURATION,
        affected_layer="execution-configuration",
        action_code="RESELECT_CONFIGURATION",
        retryable=True,
        confidence=0.95,
    ),
    GoldenRow(
        case="a dispatch without a receipt is the router's to fix",
        signals=FailureSignals(error_code="DISPATCH_WITHOUT_RECEIPT"),
        rule_id="DISPATCH_WITHOUT_RECEIPT",
        failure_type=FailureType.CONFIGURATION,
        owner=FailureOwner.CONFIGURATION,
        affected_layer="execution-configuration",
        action_code="RESELECT_CONFIGURATION",
        retryable=True,
        confidence=0.95,
    ),
    GoldenRow(
        case="an unknown capability with no denial belongs to the capability manager",
        signals=FailureSignals(error_code="CAPABILITY_UNKNOWN"),
        rule_id="CAPABILITY_UNAVAILABLE",
        failure_type=FailureType.CAPABILITY,
        owner=FailureOwner.CAPABILITY,
        affected_layer="capability-binding",
        action_code="REBIND_CAPABILITY",
        retryable=True,
        confidence=0.90,
    ),
    GoldenRow(
        case="a reauth requirement with an ALLOW decision belongs to the capability manager",
        signals=FailureSignals(error_code="REQUIRE_REAUTH", policy_decision="ALLOW"),
        rule_id="CAPABILITY_REAUTH_REQUIRED",
        failure_type=FailureType.CAPABILITY,
        owner=FailureOwner.CAPABILITY,
        affected_layer="capability-binding",
        action_code="REBIND_CAPABILITY",
        retryable=True,
        confidence=0.90,
    ),
    GoldenRow(
        case="a provider failure outranks a broken verifier for attribution",
        signals=FailureSignals(
            error_code="PROVIDER_FAILURE",
            local_status=VerificationState.ERROR,
        ),
        rule_id="PROVIDER_FAILURE",
        failure_type=FailureType.TRANSIENT,
        owner=FailureOwner.ENVIRONMENT,
        affected_layer="environment",
        action_code="RETRY_AFTER_BACKOFF",
        retryable=True,
        confidence=0.85,
    ),
    GoldenRow(
        case="a node timeout is transient and owned by the environment",
        signals=FailureSignals(error_code="NODE_TIMEOUT_EXCEEDED"),
        rule_id="EXECUTION_TIMEOUT",
        failure_type=FailureType.TRANSIENT,
        owner=FailureOwner.ENVIRONMENT,
        affected_layer="environment",
        action_code="RETRY_AFTER_BACKOFF",
        retryable=True,
        confidence=0.80,
    ),
    GoldenRow(
        case="a verifier that errored is the environment's problem, at lower confidence",
        signals=FailureSignals(local_status=VerificationState.ERROR),
        rule_id="VERIFIER_ERROR",
        failure_type=FailureType.TRANSIENT,
        owner=FailureOwner.ENVIRONMENT,
        affected_layer="environment",
        action_code="RETRY_AFTER_BACKOFF",
        retryable=True,
        confidence=0.70,
    ),
    GoldenRow(
        case="findings against a required output claim are the planner's",
        signals=FailureSignals(schema_findings=1, local_status=VerificationState.FAIL),
        rule_id="REQUIRED_OUTPUT_SCHEMA_FINDINGS",
        failure_type=FailureType.STRUCTURAL,
        owner=FailureOwner.STRUCTURAL,
        affected_layer="plan-graph",
        action_code="REPLAN_GRAPH",
        retryable=True,
        confidence=0.85,
    ),
    GoldenRow(
        case="no rule fires: unknown owner, confidence 0.2, and no automatic retry",
        signals=FailureSignals(
            error_code="SOMETHING_NOBODY_HAS_SEEN",
            error_message="the runtime exited 137",
            local_status=VerificationState.PASS,
            policy_decision="ALLOW",
        ),
        rule_id="UNCLASSIFIED",
        failure_type=FailureType.OBJECTIVE,
        owner=FailureOwner.UNKNOWN,
        affected_layer="unknown",
        action_code="ESCALATE_TO_HUMAN",
        retryable=False,
        confidence=0.2,
    ),
)

RULE_ORDER: tuple[str, ...] = (
    "VERIFICATION_QUARANTINED",
    "CAPABILITY_DENIED_BY_POLICY",
    "POLICY_DENIED",
    "HUMAN_APPROVAL_REQUIRED",
    "VERIFICATION_CONFLICT",
    "RUNTIME_VERSION_DRIFT",
    "DISPATCH_WITHOUT_RECEIPT",
    "CAPABILITY_UNAVAILABLE",
    "CAPABILITY_REAUTH_REQUIRED",
    "PROVIDER_FAILURE",
    "EXECUTION_TIMEOUT",
    "VERIFIER_ERROR",
    "REQUIRED_OUTPUT_SCHEMA_FINDINGS",
)
"""Precedence, written out. The overlapping rows above are what make a swap change an answer."""


@pytest.mark.acceptance("AC4-M3-028")
def test_the_rule_table_assigns_one_owner_per_code_and_unknown_when_no_rule_fires() -> None:
    """SDD §7.11 and registry §5.4: the taxonomy is a table, and this is the table."""

    assert tuple(rule.rule_id for rule in RULES) == RULE_ORDER
    assert len({rule.rule_id for rule in RULES}) == len(RULES)
    assert {row.rule_id for row in GOLDEN_TABLE} == {*RULE_ORDER, "UNCLASSIFIED"}

    owner_by_rule: dict[str, FailureOwner] = {}
    for row in GOLDEN_TABLE:
        event = classify(row.signals)
        assert event.labels["rule_id"] == row.rule_id, row.case
        assert event.failure_type is row.failure_type, row.case
        assert event.assigned_owner is row.owner, row.case
        assert event.affected_layer == row.affected_layer, row.case
        assert event.recommended_action.action_code == row.action_code, row.case
        assert event.recommended_action.owner is row.owner, row.case
        assert event.retryable is row.retryable, row.case
        assert event.recommended_action.retry_allowed is row.retryable, row.case
        assert event.classification_confidence == pytest.approx(row.confidence), row.case
        # One owner per code: a rule id that classified two ways would mean the table is
        # being read as a suggestion rather than as a decision.
        assert owner_by_rule.setdefault(row.rule_id, row.owner) is row.owner, row.case


@pytest.mark.acceptance("AC4-M3-028")
def test_classification_is_deterministic_and_the_failure_event_seals() -> None:
    """§9.7 compares failures across attempts, so two identical inputs must be one record."""

    signals = FailureSignals(
        error_code="RUNTIME_VERSION_DRIFT",
        error_message="runtime pinned 1.4.0, resolved 1.5.2",
        attempted_configuration_hashes=(HASH_A, HASH_B),
    )
    first = classify(signals)
    second = classify(signals)

    assert first.model_dump() == second.model_dump()
    assert first.contract_id == second.contract_id
    assert has_prefix(first.contract_id, "failure_event")
    assert first.content_hash == content_hash(first)
    assert first.attempted_configuration_hashes == [HASH_A, HASH_B]

    # A different execution instance is a different failure, even at the same instant.
    elsewhere = classifier().classify(
        signals=signals,
        execution_instance_id="run_QQ1MPFXHNSSGV9GEZ4RM4V0FGE",
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=fixed_clock,
    )
    assert elsewhere.contract_id != first.contract_id

    # The seal is a real seal: the payload round-trips, and an edited one is refused.
    payload = first.model_dump(mode="json")
    assert FailureEvent.model_validate(payload).content_hash == first.content_hash
    tampered = dict(payload)
    tampered["classification_confidence"] = 0.99
    with pytest.raises(ValidationError):
        FailureEvent.model_validate(tampered)
