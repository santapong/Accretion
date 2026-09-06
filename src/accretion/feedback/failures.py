"""SDD §7.11's failure taxonomy as a deterministic rule table (registry §5.4).

A failure is only actionable once two separate questions have been answered: *what kind of
thing went wrong* (:class:`~accretion.contracts.routing.FailureType`) and *which layer may
fix it* (:class:`~accretion.contracts.routing.FailureOwner`). §9.7 routes recovery by the
second one — configuration failures back to the router, structural failures to the planner,
verification conflicts to evidence resolution, policy and risk failures to a human — so a
classifier that guessed, or that answered differently on two identical inputs, would move
authority around by accident. Everything here is therefore a table lookup:

* **The rules are an ordered tuple and the first match wins.** Order is data, not an
  implementation detail: ``CAPABILITY_UNKNOWN`` raised *because a policy denied the call*
  is typed ``CAPABILITY`` and owned by ``AUTHORITY`` (the case
  :class:`~accretion.contracts.routing.FailureType` names in its own docstring), and it is
  only distinguishable from an ordinary capability miss by sitting ahead of it. A test
  writes the whole table out and pins the order, and overlapping signals appear in it, so
  swapping two rows changes an answer rather than merely a listing.
* **Nothing is derived from the message text.** Free-text matching is how a taxonomy starts
  drifting with a provider's wording. Rules read the structured signals only; the message is
  carried into the event's rationale for a human and is never matched on.
* **No rule invents an owner.** ``affected_layer`` and the recommended ``action_code`` are
  total maps over :class:`~accretion.contracts.routing.FailureOwner`, because registry §5.4
  defines the owner as an ownership *class* over a layer — the layer is a property of the
  owner, not a free field a rule could spell two ways.
* **An unclassified failure is not a transient one.** With no rule matched the event is
  owned by ``UNKNOWN`` at confidence 0.2 and is not retryable, which registry §5.4 turns
  into a hard stop: :class:`~accretion.contracts.routing.FailureEvent` refuses to let an
  ``UNKNOWN`` owner be marked retryable at all. Its *type* is ``OBJECTIVE`` because the
  sealed ``FailureType`` has no "unknown" member and ``OBJECTIVE`` is the one member that
  claims nothing about a subsystem: the run did not meet its objective, and that is the
  only thing known. The confidence and the owner carry the ignorance.

The classifier is pure. It takes its clock as an argument and its principal at
construction, so two runs over the same signals produce byte-identical events — including
the ``contract_id``, which is derived from the signals rather than minted from the wall
clock. The run-manager wiring that calls this lives in a later PR (M3b).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from accretion.contracts import AuthorizationOutcome, LoopStopReason, PrincipalRef
from accretion.contracts.routing import (
    FailureEvent,
    FailureOwner,
    FailureType,
    RecommendedAction,
    VerificationState,
)

# Imported rather than re-spelled. `new_id` mints from the wall clock, which a derived id
# must not do, but the *shape* it produces — a three-character prefix, an underscore and 26
# Crockford base32 characters — is what `has_prefix` and ADR-055 check, and duplicating the
# alphabet or the prefix here would be a second source of truth for it (registry §21). A
# later PR promotes this derivation to a shared `accretion.ids.derived_id`; this module then
# imports that instead and nothing else changes.
from accretion.ids import _ALPHABET, _PREFIXES

_ID_BODY_LENGTH = 26
"""Characters after the prefix, matching :func:`~accretion.ids.new_id` and ``has_prefix``."""

_FIELD_SEPARATOR = "\x1f"
"""ASCII unit separator: it cannot occur in an id, a digest or an enum value, so the
derivation cannot be made ambiguous by a field that contains the separator."""

CODE_PROVIDER_FAILURE = LoopStopReason.PROVIDER_FAILURE.value
"""Reused from v0.1's stop reasons rather than re-spelled (registry §21)."""

TIMEOUT_ERROR_CODES: frozenset[str] = frozenset(
    {"TIMEOUT", "NODE_TIMEOUT_EXCEEDED", "COMMAND_TIMEOUT"}
)
"""Every timeout code this repository actually emits, plus the generic spelling.

``NODE_TIMEOUT_EXCEEDED`` comes from the graph validator and ``COMMAND_TIMEOUT`` from the
command verifier; both mean the same thing to a router, which is that the work did not
finish rather than that it failed.
"""

CODE_RUNTIME_VERSION_DRIFT = "RUNTIME_VERSION_DRIFT"
CODE_DISPATCH_WITHOUT_RECEIPT = "DISPATCH_WITHOUT_RECEIPT"
CODE_CAPABILITY_UNKNOWN = "CAPABILITY_UNKNOWN"
CODE_REQUIRE_REAUTH = "REQUIRE_REAUTH"

CAPABILITY_ERROR_CODES: frozenset[str] = frozenset(
    {CODE_CAPABILITY_UNKNOWN, CODE_REQUIRE_REAUTH}
)

AFFECTED_LAYER_BY_OWNER: dict[FailureOwner, str] = {
    FailureOwner.CONFIGURATION: "execution-configuration",
    FailureOwner.STRUCTURAL: "plan-graph",
    FailureOwner.CAPABILITY: "capability-binding",
    FailureOwner.VERIFICATION: "verification",
    FailureOwner.ENVIRONMENT: "environment",
    FailureOwner.SAFETY: "safety-policy",
    FailureOwner.AUTHORITY: "authority-policy",
    FailureOwner.RESOURCE: "resource-budget",
    FailureOwner.UNKNOWN: "unknown",
}
"""Total over :class:`FailureOwner`: the layer an owner owns (registry §5.4)."""

ACTION_CODE_BY_OWNER: dict[FailureOwner, str] = {
    FailureOwner.CONFIGURATION: "RESELECT_CONFIGURATION",
    FailureOwner.STRUCTURAL: "REPLAN_GRAPH",
    FailureOwner.CAPABILITY: "REBIND_CAPABILITY",
    FailureOwner.VERIFICATION: "RESOLVE_EVIDENCE",
    FailureOwner.ENVIRONMENT: "RETRY_AFTER_BACKOFF",
    FailureOwner.SAFETY: "ESCALATE_TO_HUMAN",
    FailureOwner.AUTHORITY: "ESCALATE_TO_HUMAN",
    FailureOwner.RESOURCE: "RAISE_RESOURCE_BUDGET",
    FailureOwner.UNKNOWN: "ESCALATE_TO_HUMAN",
}
"""Total over :class:`FailureOwner`: the step §9.7 hands to that owner."""


@dataclass(frozen=True, slots=True)
class FailureSignals:
    """The structured evidence a classifier is allowed to read.

    Every field is something the executor already knows at the moment a node fails, and
    nothing here is free text a rule matches on: ``error_message`` exists to be shown to a
    human in the event's rationale, and no rule reads it.

    ``schema_findings`` counts findings against **required** output claims only. A finding
    on an optional claim is a quality signal and not a structural failure, and the caller
    that holds the claim contract is the only layer that can tell them apart, so the
    distinction is made before the count reaches this record.
    """

    error_code: str | None = None
    error_message: str = ""
    local_status: VerificationState | None = None
    conflict_count: int = 0
    policy_decision: str | None = None
    schema_findings: int = 0
    attempted_configuration_hashes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FailureRule:
    """One row of the taxonomy: a predicate over signals and the verdict it produces.

    ``confidence`` is the classifier's own certainty and is stored on the event, so a
    downstream consumer can tell a rule that read an unambiguous error code (0.95) from one
    that inferred a cause from a weaker signal (0.70) without re-deriving either.
    """

    rule_id: str
    matches: Callable[[FailureSignals], bool]
    failure_type: FailureType
    owner: FailureOwner
    retryable: bool
    confidence: float


RULES: tuple[FailureRule, ...] = (
    # Governance first. A quarantined verification is an append-only state a human or a
    # policy applied, and no automatic recovery may look past it at whatever else the
    # signals happen to say.
    FailureRule(
        rule_id="VERIFICATION_QUARANTINED",
        matches=lambda signals: signals.local_status is VerificationState.QUARANTINED,
        failure_type=FailureType.POLICY_RISK,
        owner=FailureOwner.SAFETY,
        retryable=False,
        confidence=0.99,
    ),
    # Ahead of `CAPABILITY_UNAVAILABLE`, and the reason the table is ordered: the same error
    # code means "rebind the capability" on its own and "a policy said no" when a denial
    # accompanies it. Typed CAPABILITY, owned by AUTHORITY — the split `FailureType`'s
    # docstring exists for.
    FailureRule(
        rule_id="CAPABILITY_DENIED_BY_POLICY",
        matches=lambda signals: (
            signals.error_code in CAPABILITY_ERROR_CODES
            and signals.policy_decision == AuthorizationOutcome.DENY.value
        ),
        failure_type=FailureType.CAPABILITY,
        owner=FailureOwner.AUTHORITY,
        retryable=False,
        confidence=0.90,
    ),
    FailureRule(
        rule_id="POLICY_DENIED",
        matches=lambda signals: signals.policy_decision == AuthorizationOutcome.DENY.value,
        failure_type=FailureType.POLICY_RISK,
        owner=FailureOwner.AUTHORITY,
        retryable=False,
        confidence=0.95,
    ),
    FailureRule(
        rule_id="HUMAN_APPROVAL_REQUIRED",
        matches=lambda signals: (
            signals.policy_decision == AuthorizationOutcome.REQUIRE_APPROVAL.value
        ),
        failure_type=FailureType.POLICY_RISK,
        owner=FailureOwner.AUTHORITY,
        retryable=False,
        confidence=0.90,
    ),
    # Before every execution rule: when independent verifiers disagree materially, what the
    # node's own error code says is a symptom of the disagreement, not its cause. Not
    # retryable, because repeating the identical execution cannot resolve a conflict —
    # §9.7 hands it to evidence resolution instead, which is a hand-off and not a retry.
    FailureRule(
        rule_id="VERIFICATION_CONFLICT",
        matches=lambda signals: signals.conflict_count > 0,
        failure_type=FailureType.VERIFICATION_CONFLICT,
        owner=FailureOwner.VERIFICATION,
        retryable=False,
        confidence=0.90,
    ),
    FailureRule(
        rule_id="RUNTIME_VERSION_DRIFT",
        matches=lambda signals: signals.error_code == CODE_RUNTIME_VERSION_DRIFT,
        failure_type=FailureType.CONFIGURATION,
        owner=FailureOwner.CONFIGURATION,
        retryable=True,
        confidence=0.95,
    ),
    FailureRule(
        rule_id="DISPATCH_WITHOUT_RECEIPT",
        matches=lambda signals: signals.error_code == CODE_DISPATCH_WITHOUT_RECEIPT,
        failure_type=FailureType.CONFIGURATION,
        owner=FailureOwner.CONFIGURATION,
        retryable=True,
        confidence=0.95,
    ),
    FailureRule(
        rule_id="CAPABILITY_UNAVAILABLE",
        matches=lambda signals: signals.error_code == CODE_CAPABILITY_UNKNOWN,
        failure_type=FailureType.CAPABILITY,
        owner=FailureOwner.CAPABILITY,
        retryable=True,
        confidence=0.90,
    ),
    FailureRule(
        rule_id="CAPABILITY_REAUTH_REQUIRED",
        matches=lambda signals: signals.error_code == CODE_REQUIRE_REAUTH,
        failure_type=FailureType.CAPABILITY,
        owner=FailureOwner.CAPABILITY,
        retryable=True,
        confidence=0.90,
    ),
    FailureRule(
        rule_id="PROVIDER_FAILURE",
        matches=lambda signals: signals.error_code == CODE_PROVIDER_FAILURE,
        failure_type=FailureType.TRANSIENT,
        owner=FailureOwner.ENVIRONMENT,
        retryable=True,
        confidence=0.85,
    ),
    FailureRule(
        rule_id="EXECUTION_TIMEOUT",
        matches=lambda signals: signals.error_code in TIMEOUT_ERROR_CODES,
        failure_type=FailureType.TRANSIENT,
        owner=FailureOwner.ENVIRONMENT,
        retryable=True,
        confidence=0.80,
    ),
    # After the provider rules: an `ERROR` state means the verifier itself broke, which is
    # the environment's problem, but a node that also reported a provider failure has a
    # better-attributed cause already.
    FailureRule(
        rule_id="VERIFIER_ERROR",
        matches=lambda signals: signals.local_status is VerificationState.ERROR,
        failure_type=FailureType.TRANSIENT,
        owner=FailureOwner.ENVIRONMENT,
        retryable=True,
        confidence=0.70,
    ),
    # Last of the matching rules. A schema finding against a required output claim says the
    # node cannot produce what its contract promises under any configuration, which is the
    # planner's problem — but only once no better-attributed cause has claimed the failure.
    FailureRule(
        rule_id="REQUIRED_OUTPUT_SCHEMA_FINDINGS",
        matches=lambda signals: signals.schema_findings > 0,
        failure_type=FailureType.STRUCTURAL,
        owner=FailureOwner.STRUCTURAL,
        retryable=True,
        confidence=0.85,
    ),
)
"""The taxonomy, in precedence order. First match wins; :data:`UNCLASSIFIED_RULE` is the
fallback and is deliberately *not* a member — a table whose last row matched everything
would make "no rule fired" unobservable."""

UNCLASSIFIED_RULE = FailureRule(
    rule_id="UNCLASSIFIED",
    matches=lambda signals: True,
    failure_type=FailureType.OBJECTIVE,
    owner=FailureOwner.UNKNOWN,
    retryable=False,
    confidence=0.2,
)
"""Used when no rule in :data:`RULES` matches (SDD §7.11; registry §5.4 stops recovery)."""


def _derived_contract_id(parts: Sequence[str]) -> str:
    """A ``flr_`` id that depends only on ``parts``, in :func:`~accretion.ids.new_id`'s shape.

    ``new_id`` draws 48 bits of wall clock and 80 bits of randomness, which is right for a
    record whose identity is its own creation and wrong for one that must be reproducible:
    replaying the same signals through the same clock has to produce the same event, digest
    included, or a replay cannot be compared with the original at all. So the 130 bits of
    the body come off a sha256 of the joined parts instead. Truncating a digest to 130 bits
    is not a weakening: the id is an identity, the ``content_hash`` is the integrity claim,
    and they are different fields for that reason.
    """

    digest = hashlib.sha256(_FIELD_SEPARATOR.join(parts).encode("utf-8")).digest()
    value = int.from_bytes(digest, "big") >> (256 - 5 * _ID_BODY_LENGTH)
    body = ["0"] * _ID_BODY_LENGTH
    for index in range(_ID_BODY_LENGTH - 1, -1, -1):
        body[index] = _ALPHABET[value & 31]
        value >>= 5
    return f"{_PREFIXES['failure_event']}_{''.join(body)}"


@dataclass(frozen=True)
class FailureClassifier:
    """Turns :class:`FailureSignals` into a sealed :class:`FailureEvent` (SDD §7.11).

    ``created_by`` is held here rather than passed per call because it is the classifier's
    identity, not the failure's: every event this service writes was written by the same
    principal, and threading it through each call site would invite two of them.

    ``rules`` is injectable so a test can drive a deliberately reordered or truncated table
    without monkeypatching module state; production always uses :data:`RULES`.
    """

    created_by: PrincipalRef
    rules: tuple[FailureRule, ...] = RULES

    def select_rule(self, signals: FailureSignals) -> FailureRule:
        """The first rule that matches, or :data:`UNCLASSIFIED_RULE`.

        Public because the rule is the decision: a caller (and the golden-table test)
        checking classification does not want to reconstruct a whole ``FailureEvent``,
        which needs a workspace, a project and a clock it has no opinion about.
        """

        for rule in self.rules:
            if rule.matches(signals):
                return rule
        return UNCLASSIFIED_RULE

    def classify(
        self,
        *,
        signals: FailureSignals,
        execution_instance_id: str,
        workspace_id: str,
        project_id: str,
        clock: Callable[[], datetime],
    ) -> FailureEvent:
        """Classify ``signals`` into a sealed failure event.

        The matched ``rule_id`` goes into ``labels`` and not into a rationale sentence: it
        is how an operator asks "which row decided this?" over a table of stored events,
        and a value buried in prose is not queryable.
        """

        rule = self.select_rule(signals)
        created_at = clock()
        contract_id = _derived_contract_id(
            (
                execution_instance_id,
                workspace_id,
                project_id,
                rule.rule_id,
                rule.failure_type.value,
                rule.owner.value,
                created_at.isoformat(),
                *signals.attempted_configuration_hashes,
            )
        )
        # `model_validate` and not the keyword constructor, and not by preference: the
        # pydantic mypy plugin cannot see the registry §3 header fields any subclass of
        # `CanonicalContract` inherits, because `canonical` and `routing` import each other
        # (the `ObjectiveContractRef` forward reference) and the plugin gives up on the
        # cycle, leaving a generated `__init__` that rejects `contract_id`, `created_by`,
        # `workspace_id`, `project_id`, `created_at` and `labels` under `mypy --strict`.
        # Validating a mapping is the same construction path every store read already takes
        # and runs every validator identically, header seal included. Restoring the keyword
        # form would reintroduce six `call-arg` errors, so it is not an accident to tidy.
        return FailureEvent.model_validate(
            {
                "contract_id": contract_id,
                "created_at": created_at,
                "created_by": self.created_by,
                "workspace_id": workspace_id,
                "project_id": project_id,
                "execution_instance_id": execution_instance_id,
                "failure_type": rule.failure_type,
                "affected_layer": AFFECTED_LAYER_BY_OWNER[rule.owner],
                "retryable": rule.retryable,
                "classification_confidence": rule.confidence,
                "attempted_configuration_hashes": list(signals.attempted_configuration_hashes),
                "assigned_owner": rule.owner,
                "recommended_action": RecommendedAction(
                    action_code=ACTION_CODE_BY_OWNER[rule.owner],
                    owner=rule.owner,
                    rationale=_rationale(rule, signals),
                    retry_allowed=rule.retryable,
                ),
                "labels": {"rule_id": rule.rule_id},
            }
        )


def _rationale(rule: FailureRule, signals: FailureSignals) -> str:
    """A one-sentence explanation carrying the operator's evidence, bounded to 1000 chars.

    The error message is quoted here — the one place free text is allowed — because a human
    reading the event needs the provider's own words even though no rule may match on them.
    """

    detail = signals.error_message.strip() or "no message reported"
    sentence = (
        f"Rule {rule.rule_id} classified this failure as {rule.failure_type.value} "
        f"owned by {rule.owner.value} (SDD §7.11, registry §5.4); reported: {detail}"
    )
    return sentence[:1_000]
