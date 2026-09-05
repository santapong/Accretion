"""The v0.4 contract family: node objective, routing, verification feedback, learned router.

This module is the whole of the v0.4 contract freeze: the nineteen M0 froze on
5 Sep 2026 and the two the freeze delta added the same day, twenty-one in all, listed in
:data:`CONTRACT_INVENTORY`, every one of them a
:class:`~accretion.contracts.canonical.CanonicalContract` — carrying the registry §3
header, sealed with the ADR-056 digest. Nothing here routes,
predicts, persists or serves; M0's claim is only that these shapes exist, validate, hash
and round-trip, so that the milestones that *do* route have something they cannot quietly
redefine underneath themselves.

**Where the definitions come from, and who wins.** SDD v0.4 §7 gives thirteen illustrative
YAML schemas; the cross-release registry §3-§7 gives the canonical header, the typed
references, the stable enums and the minimum field sets. Document precedence (registry §2)
puts the registry above the SDD, so where the two differ the registry's spelling is the one
implemented and the SDD's is recorded in the model's docstring. There are five such places
and each is named where it happens: ``NodeContract`` takes the registry's ``node_kind``,
``input_contracts``/``output_contracts``, ``allowed_risk_class``, ``resource_cap`` and
``verification_spec_ref`` rather than the SDD's ``node_type``, ``input_schema``/
``output_schema``, ``risk_class``, ``budget`` and an embedded ``verification_spec``;
``FailureEvent.assigned_owner`` takes registry §5.4's ownership classes rather than the
SDD's component names; the SDD's bare ``uuid`` reference fields become the repository's
prefixed base32 ids (ADR-055) or typed references from :mod:`accretion.contracts.refs`
(registry §3.1); ``ExperienceRecord`` is a projection rather than a record (ADR-054 b);
and ``VerificationResult`` is implemented under the name ``IndependentVerificationResult``
because v0.1 already owns the other one (ADR-054 a).

**What is deliberately not here.** No field is typed ``bytes``, ``set``, ``frozenset`` or
``timedelta``, and no mapping is keyed by anything but ``str``. Those are unhashable by
construction under :func:`~accretion.contracts.canonical.content_hash`, which is to say a
contract declaring one could be built and could never be sealed — a fail-closed trap set at
runtime for a mistake that belongs at design time. Digests are 64-character lowercase hex
strings, collections are lists, and free-form payloads are ``str``-keyed dicts of scalars.

**Names checked against the existing vocabulary** (registry §21). Every ``StrEnum`` in
:mod:`accretion.contracts` was inventoried before a name was chosen here. Four of the
fifteen new enums sit deliberately beside an older one and each says why in its own
docstring: ``VerificationState`` beside ``VerificationStatus``, ``RiskClass`` beside
``RiskLevel``, ``CompatibilityStatus`` beside the v0.2 ``MatchDisposition``, and
``FailureType`` beside ``LoopStopReason``. Three older enums are *reused* rather than
twinned — ``EvidenceClass`` (already equal to registry §5.2), ``GraphNodeKind`` (nine kinds,
unchanged) and ``FindingSeverity`` — and ``Provider``, ``WorkspaceRole``, ``RiskLevel`` and
``ExpectedHorizon`` appear as field types. Nothing in this module is re-exported through
the package root (ADR-053): a v0.4 name must be imported from
``accretion.contracts.routing`` so that it can never be mistaken for one of the v0.1-v0.3
names the root spreads across the codebase.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar, Literal, Self

from pydantic import Field, model_validator

from accretion.contracts import (
    EvidenceClass,
    ExpectedHorizon,
    FindingSeverity,
    GraphNodeKind,
    PrincipalRef,
    Provider,
    RiskLevel,
    StrictModel,
    WorkspaceRole,
)
from accretion.contracts.canonical import (
    CONTRACT_SCHEMA_VERSION,
    CanonicalContract,
    content_hash,
)
from accretion.contracts.refs import (
    ApprovalArtifactRef,
    CapabilityRef,
    EnvironmentRef,
    EvidenceRef,
    PolicyRef,
    RuntimeRef,
    SkillRef,
    ToolRef,
    VerifierRef,
)
from accretion.redaction import redact

# The digest shape, spelled the same way `refs.py` spells it, so that a digest written by
# any part of this repository is comparable with any other.
_DIGEST = r"^[0-9a-f]{64}$"

FEATURE_SCHEMA_VERSION = "1.0.0"
"""The version of the ``TaskFeatures``/``ProjectFeatures`` vocabulary a router trained on.

Separate from ``schema_version`` and deliberately so: a contract's schema version says how
to *parse* it, this says what the numbers *mean*. Adding a feature changes what a trained
model's weights are about even when every stored document still parses, so a router model
records the feature schema it learned under and refuses evidence gathered under another
(SDD §7.12, §10.1).
"""


# --------------------------------------------------------------------------------------
# Enums (registry §5 and SDD §7). Each one states, in its docstring, which older enum it
# was checked against, because registry §21 makes an accidental synonym a stop-and-reconcile
# event rather than a merge conflict.
# --------------------------------------------------------------------------------------


class VerificationState(StrEnum):
    """Registry §5.1. The v0.4 verification vocabulary, beside v0.1's ``VerificationStatus``.

    ``VerificationStatus`` (``PASS | FAIL | INCONCLUSIVE``) is API-exposed on the v0.1-v0.3
    run and iteration paths and keeps its name and its three values (ADR-054 a). This enum
    is not a rename of it: it adds the three states an *independent* verifier needs and the
    older one cannot express.

    * ``PENDING`` — the verifier has been dispatched and has not answered. v0.1 had nowhere
      to put this because its verification rows were written only at the end.
    * ``ERROR`` — the verifier itself failed. Registry §5.1 is explicit that ``ERROR`` is
      not ``INCONCLUSIVE`` and neither is ``PASS``: an inconclusive verdict is a *judgement*
      about the evidence, an error is the absence of a judgement, and collapsing them would
      let a broken verifier read as a cautious one.
    * ``QUARANTINED`` — append-only governance state applied after a material concern. It is
      set by a human or a policy, never by a verifier, and it never converts back to a
      verdict; registry §3.2 forbids any migration that turns ``FAIL`` or ``INCONCLUSIVE``
      into ``PASS``, and quarantine is the state that survives that rule.

    A required verifier answering ``FAIL``, ``ERROR``, or an unresolved ``INCONCLUSIVE``
    blocks acceptance.
    """

    PENDING = "PENDING"
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    ERROR = "ERROR"
    QUARANTINED = "QUARANTINED"


TERMINAL_VERIFICATION_STATES: frozenset[VerificationState] = frozenset(
    {VerificationState.PASS, VerificationState.FAIL, VerificationState.INCONCLUSIVE}
)
"""The three verdicts a :class:`VerificationSpec` may declare acceptable (SDD §7.3).

``PENDING`` is not an outcome, and ``ERROR`` and ``QUARANTINED`` are states a spec may not
pre-accept: a spec that listed ``ERROR`` among its accepted outcomes would be a spec that
accepts its own verifier crashing.
"""


class RiskClass(StrEnum):
    """Registry §5.3. The routing risk vocabulary, beside v0.1's ``RiskLevel``.

    ``RiskLevel`` (``LOW | MEDIUM | HIGH | CRITICAL``) is the human-approval ladder used by
    planning and governance and it stays exactly as it is (ADR-054 d). ``RiskClass`` answers
    a different question — not "how much authority does this need" but "what kind of world
    does this act on" — which is why ``SIMULATION`` and ``PHYSICAL_HIGH`` are values here
    and could never be values there. The two are joined by the total mapping
    :func:`risk_level_for`.

    Project policy may make a class stricter. It may not reduce ``PHYSICAL_HIGH`` through a
    plugin, a learned policy or a runtime request; ``PROHIBITED`` is not a level at all but
    a refusal, and it maps to no approval ladder because nothing approves it.
    """

    LOW_DIGITAL = "LOW_DIGITAL"
    MEDIUM_DIGITAL = "MEDIUM_DIGITAL"
    HIGH_DIGITAL = "HIGH_DIGITAL"
    SIMULATION = "SIMULATION"
    PHYSICAL_HIGH = "PHYSICAL_HIGH"
    PROHIBITED = "PROHIBITED"


_RISK_LEVEL_BY_CLASS: dict[RiskClass, RiskLevel] = {
    RiskClass.LOW_DIGITAL: RiskLevel.LOW,
    RiskClass.MEDIUM_DIGITAL: RiskLevel.MEDIUM,
    RiskClass.HIGH_DIGITAL: RiskLevel.HIGH,
    # A simulated physical action is not a digital one: it can be wrong in ways a digital
    # action cannot, and its evidence never transfers to the physical world (registry §5.2).
    # HIGH, not MEDIUM, is the honest ladder position for it.
    RiskClass.SIMULATION: RiskLevel.HIGH,
    RiskClass.PHYSICAL_HIGH: RiskLevel.CRITICAL,
}


def risk_level_for(risk_class: RiskClass) -> RiskLevel:
    """Map a v0.4 :class:`RiskClass` onto the v0.1 :class:`RiskLevel` ladder (ADR-054 d).

    Total over the five actionable classes and *deliberately partial* over the sixth:
    ``PROHIBITED`` raises :class:`ValueError`. A prohibited action has no approval level
    because no approval exists for it, and returning ``CRITICAL`` — the nearest thing the
    ladder has — would turn "never" into "ask a senior human", which is precisely the
    weakening registry §3.2 forbids. Callers that reach this function with ``PROHIBITED``
    have already made a mistake upstream, and an exception is how they find out.

    The mapping is a module-level dict rather than a chain of comparisons because
    ``StrEnum`` members compare alphabetically, which is how ``CRITICAL < HIGH < LOW``
    happens to be true and how a plausible-looking ordering bug gets written.
    """

    if risk_class is RiskClass.PROHIBITED:
        raise ValueError(
            "RiskClass.PROHIBITED has no RiskLevel: a prohibited action is refused, not "
            "escalated, and mapping it onto the approval ladder would convert a denial "
            "into a request (registry §3.2, ADR-054 d)"
        )
    return _RISK_LEVEL_BY_CLASS[risk_class]


class FailureOwner(StrEnum):
    """Registry §5.4. Which layer owns a failure, and therefore who may recover from it.

    SDD §7.11 spells ``assigned_owner`` with component names — ``RECOVERY_CONTROLLER``,
    ``ROUTER``, ``CAPABILITY_RESOLVER``, ``EVIDENCE_RESOLVER``, ``PLANNER``, ``HUMAN`` —
    and registry §5.4 spells it with ownership *classes*. The registry wins (registry §2),
    and the SDD's component names are recovered by the fixed rules §5.4 states: the router
    owns ``CONFIGURATION`` when the graph and contract remain valid, the planner owns
    ``STRUCTURAL``, the capability manager owns ``CAPABILITY``, evidence resolution owns
    ``VERIFICATION``. The advantage of the registry's spelling is that it survives a
    reorganisation of the components, which a stored value must.

    ``SAFETY``, ``AUTHORITY`` and an unresolved ``UNKNOWN`` stop automatic recovery
    outright, which :class:`FailureEvent` enforces rather than merely documents.
    """

    CONFIGURATION = "CONFIGURATION"
    STRUCTURAL = "STRUCTURAL"
    CAPABILITY = "CAPABILITY"
    VERIFICATION = "VERIFICATION"
    ENVIRONMENT = "ENVIRONMENT"
    SAFETY = "SAFETY"
    AUTHORITY = "AUTHORITY"
    RESOURCE = "RESOURCE"
    UNKNOWN = "UNKNOWN"


NON_RECOVERABLE_FAILURE_OWNERS: frozenset[FailureOwner] = frozenset(
    {FailureOwner.SAFETY, FailureOwner.AUTHORITY, FailureOwner.UNKNOWN}
)
"""Owners for which registry §5.4 stops automatic recovery. Enforced by :class:`FailureEvent`."""


class FailureType(StrEnum):
    """SDD §7.11's failure taxonomy, beside registry §5.4's ownership (ADR-054 e).

    Type and owner are two questions, not one spelled twice: ``CONFIGURATION`` as a *type*
    says the configuration was wrong, and ``CONFIGURATION`` as an *owner* says the router
    may fix it. They usually agree and are allowed to disagree — a capability failure whose
    real cause is a policy denial is typed ``CAPABILITY`` and owned by ``AUTHORITY`` — and
    collapsing them into one enum would make that case unsayable.

    Checked against v0.1's ``LoopStopReason`` (``BUDGET_EXHAUSTED``, ``PROVIDER_FAILURE``,
    ``OPERATOR_CANCELLED``, ...), which classifies why a *loop stopped* rather than what
    kind of thing went wrong; the two vocabularies do not overlap.
    """

    TRANSIENT = "TRANSIENT"
    CONFIGURATION = "CONFIGURATION"
    CAPABILITY = "CAPABILITY"
    EVIDENCE = "EVIDENCE"
    VERIFICATION_CONFLICT = "VERIFICATION_CONFLICT"
    STRUCTURAL = "STRUCTURAL"
    POLICY_RISK = "POLICY_RISK"
    OBJECTIVE = "OBJECTIVE"


class DecisionType(StrEnum):
    """SDD §7.8. How a routing decision was reached, which is what makes it replayable.

    The distinction between ``EXPLOIT`` and ``EXPLORE`` is not cosmetic: off-policy
    evaluation needs to know which decisions were drawn from the behaviour policy, and a
    receipt that recorded only the chosen configuration would make every historical decision
    look deliberate. ``FALLBACK`` records that no confident candidate existed,
    ``HUMAN_OVERRIDE`` that a person replaced the choice, and ``HUMAN_REVIEW_REQUIRED``
    that there was no safe fallback either — the one decision type that selects nothing.
    """

    EXPLOIT = "EXPLOIT"
    EXPLORE = "EXPLORE"
    FALLBACK = "FALLBACK"
    HUMAN_OVERRIDE = "HUMAN_OVERRIDE"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"


class ConstructionStage(StrEnum):
    """SDD §9.1's eleven candidate-construction stages, as a stored value.

    A candidate records the stage it reached and a rejected candidate records the stage that
    rejected it, which is the difference between "this configuration was not chosen" and
    "this configuration was never eligible, and here is the gate that said so".
    """

    VALIDATE_NODE_CONTRACT = "VALIDATE_NODE_CONTRACT"
    RESOLVE_REQUIREMENTS = "RESOLVE_REQUIREMENTS"
    ENUMERATE_RUNTIME_MODEL = "ENUMERATE_RUNTIME_MODEL"
    BIND_TOOLS_AND_SKILLS = "BIND_TOOLS_AND_SKILLS"
    BIND_VERIFIER = "BIND_VERIFIER"
    CONSTRUCT_TUPLE = "CONSTRUCT_TUPLE"
    JOINT_COMPATIBILITY = "JOINT_COMPATIBILITY"
    PREDICT_OUTCOME = "PREDICT_OUTCOME"
    SUCCESS_GATE = "SUCCESS_GATE"
    RANK_BY_UTILITY = "RANK_BY_UTILITY"
    SELECT_BEHAVIOR = "SELECT_BEHAVIOR"


class CompatibilityStatus(StrEnum):
    """SDD §7.7. Whether a configuration subject is admissible for a required constraint.

    Three values and not two, because "we do not know" is a real answer and pretending it is
    a "no" would throw away working configurations while pretending it is a "yes" would
    route on an unchecked assumption. SDD §7.7 settles which way the ambiguity falls:
    ``UNKNOWN`` MUST NOT be treated as compatible for a required constraint, which
    :meth:`CompatibilityDecision.is_compatible` implements as a single identity comparison.

    Distinct from the v0.2 ``MatchDisposition`` (``ACCEPTED``/``DOWNRANKED``/``REJECTED``),
    which grades how usable a *past experience* is; this grades whether a *candidate
    configuration* may be built at all (ADR-054 c). Both stay.
    """

    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    UNKNOWN = "UNKNOWN"


class SubjectType(StrEnum):
    """SDD §7.7. Which layer of the configuration hierarchy a compatibility decision judged.

    The order mirrors registry §7.3's hierarchy — environment, runtime, model, tools,
    skills, verifier — with ``CONFIGURATION`` for the joint check that §9.1 stage 7 runs
    over the complete tuple. Per-layer decisions are not sufficient on their own: two
    individually compatible layers can be jointly incompatible, which is exactly why the
    joint subject exists.
    """

    RUNTIME = "RUNTIME"
    MODEL = "MODEL"
    TOOL = "TOOL"
    SKILL = "SKILL"
    VERIFIER = "VERIFIER"
    ENVIRONMENT = "ENVIRONMENT"
    CONFIGURATION = "CONFIGURATION"


class Visibility(StrEnum):
    """SDD §7.10. How widely an experience record may be read.

    Two values only. There is no ``PUBLIC``: nothing in v0.4 crosses a workspace boundary,
    and a value that no code can produce is a value someone eventually produces by hand.
    """

    PROJECT = "PROJECT"
    TEAM_WORKSPACE = "TEAM_WORKSPACE"


class ContradictionStatus(StrEnum):
    """SDD §7.10. Whether an experience contradicts other evidence, and whether that is settled.

    ``NONE`` is not the same as ``RESOLVED``: the first says no contradiction was ever
    found, the second says one was found and adjudicated. A training snapshot that
    deduplicated them would silently change what its evidence means (§10.1).
    """

    NONE = "NONE"
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class RouterScope(StrEnum):
    """SDD §7.12. The two learning scopes of ADR-047: a workspace prior and a project adapter.

    Shares the token ``TEAM_WORKSPACE`` with :class:`Visibility` and means something
    different by it — there, who may read a record; here, what a model was fitted over.
    They are kept as two enums rather than one because their value sets are not the same
    and never will be: a project adapter is not a visibility and ``PROJECT`` is not a scope.
    """

    TEAM_WORKSPACE = "TEAM_WORKSPACE"
    PROJECT_ADAPTER = "PROJECT_ADAPTER"


class RouterStatus(StrEnum):
    """SDD §7.12. The lifecycle of a router artifact, and the reason rollback is possible.

    ``RETIRED`` is not deletion: §10.3 requires the prior active model to stay
    rollback-eligible, so a retired version is a live rollback target and
    ``ROLLED_BACK`` records that the target was actually used. ``SHADOW`` sits between
    ``CANDIDATE`` and ``ACTIVE`` because ADR-046 makes shadow evaluation a required stage
    rather than an optional one.
    """

    CANDIDATE = "CANDIDATE"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"
    ROLLED_BACK = "ROLLED_BACK"


class RouterPromotionDecision(StrEnum):
    """SDD §7.13. The outcome of a promotion evaluation.

    ``REQUIRE_REVIEW`` is a first-class outcome and not an absence of one: §10.3 allows
    non-critical tradeoffs to pass only with explicit bounds and disclosure, and that is a
    human decision the report must be able to *request* rather than assume.

    Named ``RouterPromotionDecision``, not ``PromotionDecision``, because registry §13
    reserves the bare name for a *v0.10 canonical contract* — "human-reviewed canary/release/
    reject decision and rollback metadata" about a capability candidate, not an enum about a
    router model. SDD §7.13 writes the three values inline and names no enum, so nothing
    forced the collision. Taking the name here would have left ``accretion.contracts``
    owning two artifacts called ``PromotionDecision`` with different owners and different
    kinds, which registry §19's "every contract has one owner and schema version" gate
    cannot express; and by the time v0.10 lands this enum is frozen into every persisted
    ``RouterPromotionReport.decision``, so the rename would then be a registry §3.2 Major
    change requiring a §17 migration. Registry §21 calls that a stop-and-reconcile event and
    ADR-054 records no reconciliation for this name, so v0.4 yields it.
    """

    PROMOTE = "PROMOTE"
    REJECT = "REJECT"
    REQUIRE_REVIEW = "REQUIRE_REVIEW"


class Criticality(StrEnum):
    """SDD §7.3. Whether a verification claim is required for acceptance or merely supports it.

    Distinct from v0.1's ``FindingSeverity`` (``INFO``/``WARNING``/``ERROR``), which grades
    an observation a verifier made; this grades the *claim* the spec asked about. A
    ``SUPPORTING`` claim that fails is evidence; a ``REQUIRED`` claim that fails blocks.
    """

    REQUIRED = "REQUIRED"
    SUPPORTING = "SUPPORTING"


class MetricOperator(StrEnum):
    """SDD §7.3. How a metric threshold is compared.

    ``CUSTOM`` names a comparison the evaluator contract implements, and it exists because
    the alternative — inventing an expression language inside a frozen schema — would put
    an interpreter in a contract. A ``CUSTOM`` metric without an ``evaluator_contract`` is
    therefore rejected by :class:`MetricThreshold`.
    """

    GTE = "GTE"
    LTE = "LTE"
    EQ = "EQ"
    CUSTOM = "CUSTOM"


class ShadowRolloutKind(StrEnum):
    """ADR-060. Which arm of a paired branched rollout produced an observed outcome.

    Added by the freeze delta rather than by M0, because M0 froze ``ShadowDecision`` with
    no observed-outcome fields at all and M6.2 scores a shadow choice by *running* it:
    ``SHADOW`` is the fork that executed the candidate router's configuration, ``CONTROL``
    is the sibling fork that re-ran the configuration the live router actually chose, with
    the same seed policy and the same resource cap. The score M6.2 reports is
    ``U(SHADOW) - U(CONTROL)``, so the pair is the unit of evidence and the arm has to be
    on the record; a rollout that did not say which arm it was would make the difference
    unattributable.

    Checked against the existing vocabulary (registry §21) before the name was chosen.
    ``RouterStatus.SHADOW`` grades a *router version* — whether that model is allowed to
    serve — and this grades one *execution* of one node under one of two configurations.
    They are deliberately spelled the same way in the one place they coincide, because a
    ``SHADOW`` rollout is by definition produced by a ``SHADOW`` version, and a second
    word for the same idea would have been the accidental synonym registry §21 forbids.
    """

    SHADOW = "SHADOW"
    CONTROL = "CONTROL"


class RouterActivationKind(StrEnum):
    """ADR-061. Why a router version became the head of the activation ledger.

    §10.3 makes promotion "atomic and reversible", and M0 implemented "one active router"
    as two partial unique indexes over ``router_model_versions.status``. That composes with
    a first activation and with nothing after it: the store has no ``update_`` for any v0.4
    table, so a second ``ACTIVE`` row can never be inserted and the first one can never be
    retired. The ledger replaces the index — "active" becomes the head of an append-only
    sequence — and this enum is why each entry was appended.

    ``ROLLBACK`` is not ``PROMOTE`` with a different target. A promotion is a release and a
    rollback is a withdrawal, they are approved under different circumstances, and §10.3's
    reversibility claim is worth nothing if the ledger cannot distinguish the two after the
    fact. Distinct from :class:`RouterPromotionDecision`, which grades an *evaluation*
    (``PROMOTE``/``REJECT``/``REQUIRE_REVIEW``); this records an *act*, and a rejected
    evaluation produces no activation row at all.
    """

    PROMOTE = "PROMOTE"
    ROLLBACK = "ROLLBACK"


# --------------------------------------------------------------------------------------
# Value objects. None of these is a contract: they carry no header, they have no identity
# of their own, and they exist only inside the contract that declares them. They are typed
# rather than left as ``object``/``dict`` wherever the SDD wrote "object", because a frozen
# schema whose interesting halves are untyped blobs freezes nothing.
# --------------------------------------------------------------------------------------


class UtilityWeights(StrictModel):
    """Registry §7.1 ``utility_weights``: how a project trades quality, cost and latency.

    Weights are non-negative and are not required to sum to one. Normalisation is the
    ranker's business (§9.1 stage 10) and forcing it here would mean a project could not
    express "cost is irrelevant" without also restating the other two.
    """

    quality: float = Field(ge=0)
    cost: float = Field(ge=0)
    latency: float = Field(ge=0)

    @model_validator(mode="after")
    def _at_least_one_weight_is_positive(self) -> Self:
        if self.quality == 0 and self.cost == 0 and self.latency == 0:
            raise ValueError(
                "utility weights are all zero, which expresses no preference at all; a "
                "ranker given this could justify any candidate"
            )
        return self


class ResourceBudget(StrictModel):
    """Registry §7.1 ``resource_budget`` and §7.2 ``resource_cap``; SDD §7.2 ``budget``.

    ``maximum_cost`` is a :class:`~decimal.Decimal` and not a float. Money is the one
    quantity in this family where binary rounding is a defect rather than a nuisance, and
    :func:`~accretion.contracts.canonical.content_hash` serialises a decimal as its exact
    digit string, so ``0.10`` stays ``0.10`` in the digest as well as in the arithmetic.
    """

    maximum_cost: Decimal = Field(ge=0)
    maximum_latency_ms: int = Field(ge=0)
    maximum_attempts: int = Field(ge=1)
    maximum_tool_calls: int = Field(ge=0)


class HumanApprovalRequirement(StrictModel):
    """Registry §7.1 ``required_human_approvals``, typed instead of left as ``[object]``.

    ``required_role`` reuses v0.3's :class:`~accretion.contracts.WorkspaceRole` rather than
    inventing an approval-role vocabulary: the workspace roles are what the authorization
    layer actually checks, and a second list of role names would be a second source of
    truth about who may approve (registry §21).
    """

    approval_id: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1_000)
    required_role: WorkspaceRole
    applies_above_risk_class: RiskClass


class SchemaRef(StrictModel):
    """Registry §7.2 ``input_contracts``/``output_contracts``: a schema by id and digest.

    Registry §7.2 asks for ``[schema-ref]`` where SDD §7.2 wrote an inline ``json_schema``
    body. The reference wins for the reason registry §3.1 gives generally — a reference
    pins, a copy drifts — and specifically because two nodes exchanging a payload must be
    able to prove they meant the *same* schema, which comparing two embedded copies cannot
    do once either is reformatted. ``version`` is the human-facing label and
    ``content_digest`` is what actually changed.
    """

    schema_id: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=64)
    content_digest: str = Field(pattern=_DIGEST)


class CapabilityRequirement(StrictModel):
    """SDD §7.2 ``required_capabilities`` reconciled with registry §7.2's ``[CapabilityRef]``.

    The registry requires a typed :class:`~accretion.contracts.refs.CapabilityRef`; the SDD
    additionally carries a ``version_range`` and a ``required_scope``, neither of which the
    reference can express because a reference names one exact thing and a requirement names
    an acceptable set. Both survive: the reference is the canonical identity, the range is
    what the resolver may substitute within, and the scope is the authority the capability
    must be granted before it may be bound.
    """

    capability: CapabilityRef
    version_range: str = Field(min_length=1, max_length=64)
    required_scope: str = Field(min_length=1, max_length=255)


class EvidenceRequirement(StrictModel):
    """SDD §7.2 ``evidence_requirements``. Named there, never given a shape; defined here.

    ``evidence_class`` reuses :class:`~accretion.contracts.EvidenceClass` (registry §5.2 and
    the v0.3 M5 enum are already the same set, ADR-054 e), which is what makes the field
    load-bearing: registry §19 requires simulation and physical evidence to stay
    type-distinct, so a node that requires ``PHYSICAL`` evidence cannot be satisfied by a
    ``SIMULATION`` artifact no matter how many of them are produced.
    """

    requirement_id: str = Field(min_length=1, max_length=64)
    evidence_class: EvidenceClass
    minimum_count: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=1_000)


class EnvironmentConstraint(StrictModel):
    """SDD §7.2 ``environment_constraints``; registry §7.2 leaves it as ``object``.

    A constraint is an ``attribute``/``operator``/``value`` triple rather than a free-form
    expression, for the reason :class:`MetricOperator` gives: an expression language inside
    a frozen contract is an interpreter inside a frozen contract. The operator vocabulary is
    reused from the metric thresholds instead of being duplicated under another name.
    """

    constraint_id: str = Field(min_length=1, max_length=64)
    attribute: str = Field(min_length=1, max_length=255)
    operator: MetricOperator
    value: str = Field(min_length=1, max_length=1_000)
    rationale: str = Field(min_length=1, max_length=1_000)


class VerificationSpecRef(StrictModel):
    """Registry §7.2 ``verification_spec_ref``: ``{id, hash}``.

    Its ``content_hash`` is the referenced spec's header digest, and it is deliberately
    *inside* whatever hashes this reference: :func:`~accretion.contracts.canonical.content_hash`
    excludes only the top-level field, so a node contract's digest changes when the spec it
    points at changes. That is the property ADR-044 needs — verification semantics frozen
    before routing — expressed as arithmetic rather than as a rule someone has to remember.
    """

    verification_spec_id: str = Field(min_length=1, max_length=64)
    content_hash: str = Field(pattern=_DIGEST)


class NodeContractRef(StrictModel):
    """The exact node contract a routing context was built from (SDD §7.4, §8.3).

    SDD §7.4 writes ``node_contract_ref: uuid``. An id alone would not satisfy §8.3, which
    requires routing to use an *exact snapshot*: the id would still resolve after a
    revision, and the decision would silently claim to have been made against a contract it
    never saw. Pinning ``immutable_hash`` is what makes the claim checkable.
    """

    node_contract_id: str = Field(min_length=1, max_length=64)
    immutable_hash: str = Field(pattern=_DIGEST)


class Claim(StrictModel):
    """SDD §7.3 ``claims``: one thing a verifier must decide, and how much it matters.

    ``required_evidence_types`` is typed as a list of :class:`~accretion.contracts.EvidenceClass`
    rather than the SDD's ``[string]``. The SDD wrote strings because it had not yet fixed a
    vocabulary; registry §5.2 has, the repository already implements it, and leaving the
    field stringly would have let ``"physical"``, ``"PHYSICAL"`` and ``"phys"`` all mean the
    same requirement to a human and three different things to a hash.
    """

    claim_id: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=2_000)
    criticality: Criticality
    required_evidence_types: list[EvidenceClass] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def _evidence_classes_are_distinct(self) -> Self:
        if len(set(self.required_evidence_types)) != len(self.required_evidence_types):
            raise ValueError(
                f"claim {self.claim_id!r} lists an evidence class twice; a repeated class "
                "reads as a stronger requirement than it is"
            )
        return self


class MetricThreshold(StrictModel):
    """SDD §7.3 ``metrics``: a numeric or symbolic acceptance threshold.

    ``threshold`` is ``float | str`` exactly as the SDD writes it, because a ``CUSTOM``
    comparison may be against a label rather than a number. The union is safe to hash — both
    arms are canonical JSON primitives, and ``1.0`` and ``"1.0"`` hash differently, which is
    correct: they are different thresholds.
    """

    metric_id: str = Field(min_length=1, max_length=64)
    operator: MetricOperator
    threshold: float | str
    evaluator_contract: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def _custom_comparisons_name_their_evaluator(self) -> Self:
        if self.operator is MetricOperator.CUSTOM and self.evaluator_contract is None:
            raise ValueError(
                f"metric {self.metric_id!r} uses the CUSTOM operator without naming an "
                "evaluator_contract, which leaves the comparison undefined"
            )
        return self


class IndependenceRequirements(StrictModel):
    """SDD §7.3 ``independence``; OQ-418 decided at M0 design.

    Two of the three flags are :class:`~typing.Literal` ``True`` and not ``bool``. That is
    the whole point of the type. OQ-418 settles that a separate context is *mandatory* and a
    distinct runtime is *preferred*, and a boolean would have made the mandatory half
    negotiable by a writer — a spec could be persisted with
    ``producer_cannot_self_accept: false`` and every downstream check would read it as a
    legitimate configuration rather than as a forgery. Typing it ``Literal[True]`` moves the
    refusal to parse time, where a stored document that says otherwise cannot be loaded at
    all. Relaxing either flag to ``bool`` is a registry §3.2 Major change to verification
    semantics, not a tweak.

    ``distinct_runtime_preferred`` is a real ``bool``, defaulting to ``True``: a preference
    a caller may decline is exactly what OQ-418 says it is.
    """

    producer_cannot_self_accept: Literal[True] = True
    separate_context_required: Literal[True] = True
    distinct_runtime_preferred: bool = True


class GraphFeatures(StrictModel):
    """SDD §7.4 ``graph_features``: where in the graph this node sits.

    The neighbour types are :class:`~accretion.contracts.GraphNodeKind` values — the v0.1
    enum, still nine kinds, reused rather than restated — so a feature vector cannot record a
    neighbour kind the graph engine cannot produce.
    """

    parent_node_types: list[GraphNodeKind] = Field(default_factory=list, max_length=64)
    child_node_types: list[GraphNodeKind] = Field(default_factory=list, max_length=64)
    depth: int = Field(ge=0)
    critical_path: bool
    retry_number: int = Field(ge=0)


class ModelBinding(StrictModel):
    """SDD §7.5 ``model``: which model, from which provider, configured how.

    ``provider`` reuses the repository's :class:`~accretion.contracts.Provider` enum instead
    of the SDD's free ``provider_id`` string, so a configuration cannot name a provider the
    runtime layer has never heard of. ``inference_profile`` is a ``str``-keyed dict of
    scalars: free enough to carry a temperature or a thinking budget, constrained enough to
    canonicalise (a non-string key is unhashable by construction).
    """

    model_id: str = Field(min_length=1, max_length=255)
    provider: Provider
    inference_profile: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class ToolBinding(StrictModel):
    """SDD §7.5 ``tools``, reconciled with registry §4's ``ToolRef``.

    Three identities, none of them redundant. ``capability`` is what the node *asked* for,
    ``tool`` is the normalized implementation that answered it with its digest, and the
    binding pair is the workspace-local record that connected the two. Dropping the
    capability would lose the requirement; dropping the tool would lose what actually ran.
    """

    capability: CapabilityRef
    tool: ToolRef
    binding_id: str = Field(min_length=1, max_length=64)
    binding_version: str = Field(min_length=1, max_length=64)


class VerifierBinding(StrictModel):
    """SDD §7.5 ``verifier``: the independent verifier implementation and the spec it enforces.

    ``verification_spec_hash`` inside the configuration is what makes ADR-044 checkable at
    the configuration layer: a configuration is only a valid answer to a node whose spec
    hashes to this value, so swapping the spec after the fact invalidates the configuration
    rather than silently re-purposing it.
    """

    verifier: VerifierRef
    version: str = Field(min_length=1, max_length=64)
    verification_spec_hash: str = Field(pattern=_DIGEST)


class EnvironmentBinding(StrictModel):
    """SDD §7.5 ``environment``, reconciled with registry §4's ``EnvironmentRef``.

    The reference already carries the environment id, the image digest and the policy
    profile; ``workspace_isolation`` is the one thing it does not, and it belongs to the
    *configuration* rather than to the environment because the same environment can be
    entered with different isolation.
    """

    environment: EnvironmentRef
    workspace_isolation: str = Field(min_length=1, max_length=64)


class DistributionEstimate(StrictModel):
    """SDD §7.6 ``predicted``; §9.3 requires calibrated distributions or intervals.

    An interval and not a point. ADR-045 says the predictor emits a vector rather than one
    permanent scalar reward, and §9.5's exploration gate is defined over a *lower confidence
    bound*, which a point estimate cannot supply. ``method`` names how the interval was
    produced, because OQ-405 is explicitly undecided and a stored interval whose method is
    unknown cannot be recalibrated later.
    """

    mean: float
    lower_bound: float
    upper_bound: float
    confidence: float = Field(ge=0, le=1)
    method: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _bounds_bracket_the_mean(self) -> Self:
        if not self.lower_bound <= self.mean <= self.upper_bound:
            raise ValueError(
                f"estimate mean {self.mean} lies outside its interval "
                f"[{self.lower_bound}, {self.upper_bound}]"
            )
        return self


class PredictedOutcomes(StrictModel):
    """SDD §7.6 ``predicted``: the five estimates a candidate is ranked on.

    ``node_verified_success`` and ``run_verified_success`` are separate because ADR-045's
    vector is the point: a configuration that reliably passes its own node while degrading
    the run is exactly the reward-hacking shape §14.3 exists to catch, and one combined
    number would hide it.
    """

    quality: DistributionEstimate
    cost: DistributionEstimate
    latency: DistributionEstimate
    node_verified_success: DistributionEstimate
    run_verified_success: DistributionEstimate


class UncertaintySummary(StrictModel):
    """SDD §7.8 ``uncertainty``, typed instead of left as ``object``.

    ``lower_confidence_success`` is repeated from the candidate onto the receipt on purpose:
    the receipt is the replayable record, and a bound that had to be recomputed from a
    candidate row would be a bound that changes when the estimator does.
    """

    epistemic_uncertainty: float = Field(ge=0)
    lower_confidence_success: float = Field(ge=0, le=1)
    calibration_version: str = Field(min_length=1, max_length=64)


class ExplanationFactor(StrictModel):
    """One weighted reason inside a :class:`StructuredExplanation`.

    ``weight`` is signed: a factor that argued *against* the selected configuration and lost
    is part of an honest explanation, and clamping it to non-negative would turn the
    explanation into a summary of the winning side.
    """

    factor_id: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1_000)
    weight: float
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=32)


class RejectedCandidate(StrictModel):
    """Why one candidate did not win, in a form a machine can group by.

    ``reason_code`` is a screaming-snake token rather than prose so that "rejected for the
    same reason" is a query and not a text search; ``detail`` carries the prose. ``stage``
    records which of §9.1's eleven gates rejected it, which is the difference between a
    candidate that was never eligible and one that was outranked.
    """

    candidate_id: str = Field(min_length=1, max_length=64)
    stage: ConstructionStage
    reason_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")
    detail: str = Field(min_length=1, max_length=1_000)


class ClaimResult(StrictModel):
    """SDD §7.9 ``claim_results``: one verifier verdict about one claim.

    ``coverage`` and ``confidence`` are separate quantities: coverage says how much of the
    claim was actually examined, confidence says how sure the verifier is about what it
    examined. A model reviewer with high confidence over 10% coverage and a deterministic
    check with total coverage are both useful and are not the same evidence.
    ``limitations`` is required to be sayable and allowed to be empty, because "nothing
    limited this verdict" is a claim worth being able to make explicitly.
    """

    claim_id: str = Field(min_length=1, max_length=64)
    status: VerificationState
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=64)
    coverage: float = Field(ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    limitations: list[str] = Field(default_factory=list, max_length=32)


class ContractSignature(StrictModel):
    """SDD §7.10 ``contract_signature``, typed instead of left as ``object``.

    The signature is what makes an experience *retrievable* for a future node: it is the
    small set of properties two nodes must share before one's outcome is evidence about the
    other. Digests rather than bodies, because the question is only ever "the same or not".
    """

    node_kind: GraphNodeKind
    objective_digest: str = Field(pattern=_DIGEST)
    capability_digest: str = Field(pattern=_DIGEST)
    verification_spec_hash: str = Field(pattern=_DIGEST)
    risk_class: RiskClass


class AttributionSummary(StrictModel):
    """SDD §7.10 ``attribution``: how much of the run's outcome this node is credited with.

    ``score`` is nullable because §9.6 makes attribution a *derived, versioned view*: before
    any attributor has run, the honest value is absent rather than zero. ``method_version``
    is mandatory even when the score is null, so that a later re-attribution can tell which
    records it has already replaced.
    """

    score: float | None = Field(default=None, ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    method_version: str = Field(min_length=1, max_length=64)


class ExperienceOutcomes(StrictModel):
    """SDD §7.10 ``outcomes``: what the node actually cost and achieved.

    ``quality`` is nullable — not every node has a quality metric — while cost and latency
    are not, because every executed node consumed both. ``cost`` is a decimal for the same
    reason :class:`ResourceBudget` uses one.
    """

    quality: float | None = Field(default=None, ge=0, le=1)
    cost: Decimal = Field(ge=0)
    latency_ms: int = Field(ge=0)


class PermissionProvenance(StrictModel):
    """SDD §7.10 ``permission_provenance``, typed instead of left as ``object``.

    §10.1 requires a training snapshot to carry "permission and visibility proof", which is
    only a proof if it names the policy under which the record was shared, the principal who
    shared it and the scope granted. A boolean "allowed" would be an assertion.
    """

    scope: Visibility
    policy: PolicyRef
    granted_by: PrincipalRef
    justification: str = Field(min_length=1, max_length=1_000)


class RecommendedAction(StrictModel):
    """SDD §7.11 ``recommended_action``, typed instead of left as ``object``.

    ``owner`` is repeated from the event's ``assigned_owner`` because a recommendation may
    hand the failure on: the event says who owns the failure now, the action says who the
    recommended next step belongs to, and §9.7's routing rules are exactly about that hand-off.
    """

    action_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")
    owner: FailureOwner
    rationale: str = Field(min_length=1, max_length=1_000)
    retry_allowed: bool


class MetricComparison(StrictModel):
    """One candidate-versus-baseline metric in a promotion report (SDD §7.13, §10.2).

    The interval is on the *delta* and not on either value, because non-regression is a
    statement about the difference: a candidate whose point estimate improved but whose
    interval crosses zero has not been shown to be better, and ``passed`` records the
    decision that was actually made against the bounds recorded beside it.
    """

    metric_id: str = Field(min_length=1, max_length=64)
    baseline_value: float
    candidate_value: float
    delta: float
    delta_lower_bound: float
    delta_upper_bound: float
    passed: bool

    @model_validator(mode="after")
    def _bounds_bracket_the_delta(self) -> Self:
        if not self.delta_lower_bound <= self.delta <= self.delta_upper_bound:
            raise ValueError(
                f"metric {self.metric_id!r} reports delta {self.delta} outside its interval "
                f"[{self.delta_lower_bound}, {self.delta_upper_bound}]"
            )
        return self


class CohortResult(StrictModel):
    """One §10.2 evaluation cohort. ``critical`` is what blocks promotion, not the metric.

    OQ-413 names the critical cohorts — correctness, policy, secrets, high-risk, verifier
    conflict — and leaves the list open. Carrying ``critical`` on the cohort rather than
    hard-coding a set of ids means the promotion rule ("a critical regression blocks") stays
    true when the list changes.
    """

    cohort_id: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1_000)
    sample_size: int = Field(ge=0)
    critical: bool
    comparison: MetricComparison


class ShadowSummary(StrictModel):
    """SDD §7.13 ``shadow_result``: what shadow evaluation showed before promotion.

    ``sample_size`` sits beside ``agreement_rate`` because OQ-409 leaves the minimum shadow
    evidence to a power analysis: a 100% agreement rate over four decisions and over four
    thousand are the same number and different evidence, and a report that recorded only the
    rate could not tell them apart afterwards.
    """

    decision_count: int = Field(ge=0)
    agreement_rate: float = Field(ge=0, le=1)
    projected_utility_delta: float
    sample_size: int = Field(ge=0)


class RegressionFinding(StrictModel):
    """A §7.13 ``critical_regressions``/``noncritical_tradeoffs`` entry.

    ``severity`` reuses v0.1's :class:`~accretion.contracts.FindingSeverity` rather than
    introducing a fourth severity vocabulary. ``disclosed_bound`` is required for a
    non-critical tradeoff because §10.3 allows one only "with explicit bounds and
    disclosure" — an undisclosed tradeoff is not a tradeoff, it is a regression.
    """

    finding_id: str = Field(min_length=1, max_length=64)
    metric_id: str = Field(min_length=1, max_length=64)
    severity: FindingSeverity
    description: str = Field(min_length=1, max_length=1_000)
    disclosed_bound: str | None = Field(default=None, min_length=1, max_length=255)


class SnapshotSplit(StrictModel):
    """SDD §10.1's "training, validation, and holdout project groups".

    Split by *project* and not by record. Two nodes from one project share an objective, a
    repository and a policy set, so records from the same project on both sides of a split
    leak, and a holdout that leaks measures memorisation. The validator refuses any project
    that appears in two groups, which is the cheap half of that guarantee.
    """

    training_project_ids: list[str] = Field(min_length=1, max_length=4_096)
    validation_project_ids: list[str] = Field(default_factory=list, max_length=4_096)
    holdout_project_ids: list[str] = Field(min_length=1, max_length=4_096)

    @model_validator(mode="after")
    def _groups_are_disjoint(self) -> Self:
        groups = {
            "training": set(self.training_project_ids),
            "validation": set(self.validation_project_ids),
            "holdout": set(self.holdout_project_ids),
        }
        names = sorted(groups)
        for index, first in enumerate(names):
            for second in names[index + 1 :]:
                shared = groups[first] & groups[second]
                if shared:
                    raise ValueError(
                        f"projects {sorted(shared)!r} appear in both the {first} and "
                        f"{second} groups; a project on both sides of a split leaks"
                    )
        return self


class ExplorationPolicy(StrictModel):
    """OQ-410, ADR-062. The exploration budget an objective is willing to spend.

    M0 froze :class:`ObjectiveContract` with no exploration field, so M7's guarded bandit
    had nowhere authoritative to read alpha from and would have had to invent one. The
    safety inequality M7 enforces per (workspace, node class) is

    ``sum(cost_ucb over explored) + cost_ucb(a) <= (1 + alpha) * sum(cost_lcb(a0))``

    where ``a0`` is what the deterministic baseline would have chosen, so ``alpha`` is the
    fraction of the baseline's cost the objective will pay for information and nothing
    else. It is a property of the *objective* rather than of the router because the person
    who approved the goal is the person entitled to say how much of the budget may be spent
    learning, and a router that chose its own exploration rate would be answerable to
    nobody.

    A fraction alone is not a budget. ``max_explore_count`` and ``max_cost`` are absolute
    per-period caps, because a proportional bound scales with traffic: a workspace whose
    volume doubles would double its exploration spend under ``alpha`` alone while the
    approver's intent had not changed. All three bind, and the tightest one wins.

    ``alpha = 0`` is meaningful and is the default posture a project inherits by leaving
    ``ObjectiveContract.exploration_policy`` unset: no exploration, deterministic routing
    only. That is why the field is optional rather than defaulted to a policy object — an
    absent policy and a zero policy say the same operational thing, and only one of them
    claims somebody decided it.
    """

    alpha: float = Field(ge=0, le=1)
    max_explore_count: int = Field(ge=0)
    max_cost: float = Field(ge=0)


class ServingWindow(StrictModel):
    """OQ-415, ADR-060. The serving configuration an observed outcome was produced under.

    R7 and OQ-415 both say the same thing from different directions: an outcome is only
    evidence about a configuration if the configuration is stated completely, and
    "provider, model" is not complete. Quantization, temperature and the sampling seed all
    move quality and cost without moving any field the router selected, so a rollout scored
    against a provider version alone is a measurement of an unknown.

    ``serving_labels`` carries those knobs as a ``str``-keyed map of scalars rather than as
    named fields, and deliberately: the set is provider-specific and grows, and freezing
    ``temperature``/``quantization``/``seed`` as columns in a frozen contract would make
    every new knob a registry §3.2 change. The map is inside the digest, so a rollout that
    was served under different knobs is a different sealed document either way — which is
    the property the drift window in OQ-415 actually needs.

    ``provider`` reuses v0.1's :class:`~accretion.contracts.Provider` rather than
    introducing a parallel vocabulary (registry §21): the runtime that served a fork is the
    same enum the run, the session and the concurrency limiter already speak.
    """

    provider: Provider
    runtime_version: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=128)
    serving_labels: dict[str, str] = Field(default_factory=dict)


class ObservedOutcome(StrictModel):
    """ADR-060. What one arm of a branched rollout actually produced.

    The counterpart to :class:`PredictedOutcomes`, and the reason the freeze delta exists:
    ``ShadowDecision`` records only what a candidate router *would have chosen* and its
    *projected* utility delta, which is a claim by the model about itself. §10.2 evaluates
    a shadow stage on evidence, and evidence is a number somebody measured.

    ``quality`` is normalised to ``[0, 1]`` so the two arms of a pair are comparable
    without knowing the node's metric; ``cost`` and ``latency_ms`` are absolute, because
    normalising them would discard the units the safety inequality in
    :class:`ExplorationPolicy` is stated in.

    ``verified`` is the independent verifier's verdict and ``false_accept`` is whether that
    verdict was later found to be wrong. ``false_accept`` is nullable because it is usually
    unknown at the moment the rollout completes — a false acceptance is discovered, not
    measured — and a boolean that had to be guessed would put a fabricated number into
    ``ObjectiveContract.false_acceptance_ceiling``'s evidence. The validator refuses the
    one combination that cannot mean anything: an acceptance that was never made cannot
    have been false.
    """

    quality: float = Field(ge=0, le=1)
    cost: float = Field(ge=0)
    latency_ms: float = Field(ge=0)
    verified: bool
    false_accept: bool | None = None

    @model_validator(mode="after")
    def _a_false_acceptance_requires_an_acceptance(self) -> Self:
        if not self.verified and self.false_accept is not None:
            raise ValueError(
                f"false_accept is {self.false_accept} on an outcome the verifier did not "
                "accept; a false acceptance is an acceptance that turned out wrong, and "
                "the verdict here was not an acceptance at all"
            )
        return self


def _find_secret_shaped_values(payload: object) -> bool:
    """Return ``True`` if :mod:`accretion.redaction` would change any part of ``payload``.

    The comparison is the test. Reimplementing the secret patterns here would give the
    freeze a second, slowly-diverging copy of a security rule (registry §21) and would mean
    a pattern added to ``redaction.py`` protected the event stream but not the receipts.
    Running the real redactor and asking whether it changed anything keeps exactly one
    definition of "secret-shaped" in the repository.

    Redaction fires on two things: a key whose *name* looks like a credential
    (``token``, ``secret``, ``authorization``, ``api_key``, ``nonce``, ``state``, ...) and a
    *value* shaped like a bearer token, a JWT or an ``sk-``-style key. Both matter for a
    receipt, whose free-form ``labels`` and ``inference_profile`` maps are exactly where a
    caller would put one by accident.
    """

    # `redact` is annotated `Any -> Any`, so the comparison is narrowed here rather than
    # returned straight through: the `bool()` is not defensive style, it is what keeps
    # this function's declared contract honest at the boundary of an untyped helper.
    return bool(redact(payload) != payload)


# --------------------------------------------------------------------------------------
# The nineteen contracts, in SDD §7 order. Two departures from that order are forced by
# Python rather than chosen: `ObjectiveContractRef` comes first because the registry §3
# header is typed with it, and `VerificationSpec` follows `NodeContract` (rather than being
# embedded in it) because registry §7.2 makes the node carry a *reference* to the spec.
# --------------------------------------------------------------------------------------


class ObjectiveContractRef(CanonicalContract):
    """SDD §7.1. The exact objective revision a node was authorised against.

    This is the type of the registry §3 optional header field ``objective_contract_ref``,
    which is why it is defined before every other contract in this module and why
    :class:`~accretion.contracts.canonical.CanonicalContract` is rebuilt immediately below
    it.

    **Field coverage (SDD §7.1 → here).** ``project_id`` → the header's ``project_id``.
    ``objective_contract_id`` → ``objective_contract_id``. ``version`` → ``revision``, the
    registry §7.1 spelling, which is also what the ``ObjectiveContract`` aggregate calls its
    own counter. ``content_hash`` → ``objective_contract_hash``. ``verified_success_floor``,
    ``utility_profile_id``, ``approved_at`` → unchanged. ``risk_policy_id`` → ``risk_policy``,
    a typed :class:`~accretion.contracts.refs.PolicyRef` (registry §3.1 requires policies to
    use immutable typed references, and an authority decision audited against "policy v3" is
    audited against a label). ``approved_by`` → a typed
    :class:`~accretion.contracts.PrincipalRef` for the same reason.

    **Why the target's identity is qualified.** Registry §3 describes the header field as
    ``{contract_id, revision, content_hash}``, but inside a :class:`CanonicalContract` those
    two unqualified names already mean *this reference's own* id and digest. The target's
    are therefore spelled ``objective_contract_id`` and ``objective_contract_hash``. Nothing
    is lost and the ambiguity that would otherwise sit in every receipt is.

    ``ID_KIND`` is ``None``: ADR-055 mints no prefix for a reference, because a reference is
    created and owned by the contract that embeds it and has no id space to collide in.
    ``verified_success_floor`` is copied onto the reference rather than dereferenced because
    §8.3 requires routing to run against an exact snapshot — a router that re-read the floor
    from the live objective would be routing against a number the receipt cannot prove.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.objective-contract-ref"
    ID_KIND: ClassVar[str | None] = None

    contract_type: Literal["accretion.objective-contract-ref"] = "accretion.objective-contract-ref"
    objective_contract_id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=1)
    objective_contract_hash: str = Field(pattern=_DIGEST)
    verified_success_floor: float = Field(ge=0, le=1)
    utility_profile_id: str = Field(min_length=1, max_length=64)
    risk_policy: PolicyRef
    approved_by: PrincipalRef
    approved_at: datetime


# Resolves the forward reference the header field carries. `ObjectiveContractRef` first,
# because its own inherited `objective_contract_ref` names the class being defined; then the
# base, so that every subclass built after this point resolves without deferring. Both calls
# take the module namespace of *this* frame, which is why they sit at module level.
ObjectiveContractRef.model_rebuild(force=True)
CanonicalContract.model_rebuild(force=True)


class ObjectiveContract(CanonicalContract):
    """Registry §7.1. The human-approved goal a project's routing is answerable to.

    The registry owns this contract outright — SDD §7 never gives it a schema, only a
    reference to it — so the field list is registry §7.1's minimum, unabridged.

    **Field coverage (registry §7.1 → here).** ``goal``, ``scope_in``, ``scope_out``,
    ``verified_success_floor``, ``false_acceptance_ceiling``, ``utility_weights``,
    ``resource_budget``, ``required_human_approvals``, ``revision`` → unchanged.
    ``risk_policy_ref`` → ``risk_policy_ref``, typed as a
    :class:`~accretion.contracts.refs.PolicyRef`. ``approval_receipt_ref`` → typed as an
    :class:`~accretion.contracts.refs.ApprovalArtifactRef` and *not* as ``ArtifactRef``
    (ADR-054 f): an approval is not run-scoped, and ``ArtifactRef.run_id`` stays required
    for the execution traces that depend on it.

    ``false_acceptance_ceiling`` is the field that makes this contract more than a wish.
    ``verified_success_floor`` alone can be satisfied by a verifier that says yes too often,
    so the objective states both the success it demands and the wrongness it will tolerate,
    and §10.2's promotion evaluation checks non-regression on both.

    Changing an active objective creates a new ``revision`` with impact analysis and human
    approval, and affects only new runs and nodes. That is a process rule the schema
    supports rather than enforces: ``supersedes_contract_id`` on the header records the
    lineage, and the append-only store PR3 adds is what makes a revision a second row rather
    than an edit.

    **``exploration_policy`` is the one field M0 did not freeze** (OQ-410, ADR-062, added by
    the freeze delta of 5 Sep 2026). It is optional with a default of ``None``, which makes
    it a registry §3.2 **Minor** change *of shape*: the field list of a document written
    before it existed is still accepted, and a reader on an older minor ignores a field it
    does not know. The seal is a separate promise, and this field does break that one —
    ``None`` is a value in the canonical form (ADR-056 keeps nulls, so that ``{"a": null}``
    and ``{}`` cannot collide), so every ``ObjectiveContract`` sealed after this field exists
    carries a different ``content_hash`` from a byte-identical body sealed before it. The
    committed fixtures were therefore re-sealed and the schema digest re-recorded in
    ``docs/releases/v0.4/m0-freeze.md``, and a document presented with the digest it was
    sealed with *before* the delta is refused by ``model_validate`` as edited-after-sealing —
    at the store's read boundary as much as anywhere else. No such row exists outside the
    tests: migration 0017 is unreleased and no code outside the test suite writes an
    ``objective_contracts`` row, so a developer database already at 0017 is recreated rather
    than migrated, and registry §20.5's read-boundary upcaster (M8) owns the general case.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.objective-contract"
    ID_KIND: ClassVar[str | None] = "objective_contract"

    contract_type: Literal["accretion.objective-contract"] = "accretion.objective-contract"
    goal: str = Field(min_length=1, max_length=4_000)
    scope_in: list[str] = Field(min_length=1, max_length=64)
    scope_out: list[str] = Field(default_factory=list, max_length=64)
    verified_success_floor: float = Field(ge=0, le=1)
    false_acceptance_ceiling: float = Field(ge=0, le=1)
    utility_weights: UtilityWeights
    risk_policy_ref: PolicyRef
    resource_budget: ResourceBudget
    required_human_approvals: list[HumanApprovalRequirement] = Field(
        default_factory=list, max_length=32
    )
    revision: int = Field(ge=1)
    approval_receipt_ref: ApprovalArtifactRef
    exploration_policy: ExplorationPolicy | None = None

    @model_validator(mode="after")
    def _scopes_do_not_overlap(self) -> Self:
        overlap = set(self.scope_in) & set(self.scope_out)
        if overlap:
            raise ValueError(
                f"scope items {sorted(overlap)!r} are declared both in and out of scope; an "
                "objective that says both cannot adjudicate anything"
            )
        return self


class NodeContract(CanonicalContract):
    """SDD §7.2 and registry §7.2. One routable graph-node execution, fully specified.

    ADR-041 makes one graph-node execution instance one routable action, and this contract
    is that action's requirements *before* any configuration is chosen. Everything a router
    may consider is here; nothing a router decides is.

    **Field coverage (SDD §7.2 → here).** ``schema_version`` → the header's semver
    ``schema_version`` (the SDD's ``accretion.node-contract/v1`` string is replaced by
    ``contract_type`` plus a semver, per registry §3). ``node_contract_id`` → the header's
    ``contract_id``. ``project_id`` → header. ``run_graph_id``, ``graph_revision``,
    ``node_id``, ``execution_instance_id``, ``objective``, ``evidence_requirements``,
    ``environment_constraints``, ``immutable_hash``, ``created_at`` → unchanged.
    ``objective_contract_ref`` → the header's field, and required here by a validator.
    ``node_type`` → ``node_kind`` (registry §7.2), typed as the v0.1
    :class:`~accretion.contracts.GraphNodeKind`. ``input_schema``/``output_schema`` →
    ``input_contracts``/``output_contracts`` as ``[SchemaRef]`` (registry §7.2).
    ``required_capabilities`` → ``[CapabilityRequirement]``, which carries the registry's
    ``CapabilityRef`` plus the SDD's version range and scope. ``risk_class`` →
    ``allowed_risk_class`` as a :class:`RiskClass` (registry §7.2 and §5.3; the SDD's
    ``LOW|MEDIUM|HIGH|PHYSICAL`` is superseded). ``budget`` → ``resource_cap`` (registry
    §7.2). ``verification_spec`` → ``verification_spec_ref`` (registry §7.2): the spec is a
    contract with its own id and digest, and embedding a copy of it inside every node would
    create the second source of truth registry §21 forbids. ``failure_policy_ref`` is a
    registry §7.2 minimum with no SDD counterpart and is present.

    **How a graph revision is spelled.** Registry §7.2 asks for ``graph_revision_id: uuid``.
    This repository does not have one: v0.2 represents a revision as a monotonic integer
    (``RunGraph.graph_revision``, ``run_graph_revisions.revision``, both ``ge=1``) scoped by
    ``run_graph_id``, and the unique constraint that makes a revision identifiable is the
    pair. So the pair is what this contract carries. Minting a synthetic revision *id* here
    would have created an identifier with no row behind it, which registry §18's "adapt the
    layout, keep the semantics" allowance exists precisely to avoid.

    **The two digests.** ``immutable_hash`` is computed over the whole contract excluding
    both digests; the header's ``content_hash`` is then computed over everything including
    ``immutable_hash``. They are not redundant. ``immutable_hash`` is the value other
    contracts pin — a receipt's ``node_contract_hash``, an experience signature — and it must
    stay stable under the registry's future header additions, while ``content_hash`` is the
    §3 header digest of this document as stored. Sealing them in that order means tampering
    with either one is detected by the other.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.node-contract"
    ID_KIND: ClassVar[str | None] = "node_contract"
    DERIVED_HASH_FIELDS: ClassVar[tuple[str, ...]] = ("immutable_hash",)

    contract_type: Literal["accretion.node-contract"] = "accretion.node-contract"
    node_id: str = Field(min_length=1, max_length=64)
    run_graph_id: str = Field(min_length=1, max_length=64)
    graph_revision: int = Field(ge=1)
    execution_instance_id: str = Field(min_length=1, max_length=64)
    objective: str = Field(min_length=1, max_length=4_000)
    node_kind: GraphNodeKind
    input_contracts: list[SchemaRef] = Field(default_factory=list, max_length=32)
    output_contracts: list[SchemaRef] = Field(default_factory=list, max_length=32)
    required_capabilities: list[CapabilityRequirement] = Field(
        default_factory=list, max_length=64
    )
    evidence_requirements: list[EvidenceRequirement] = Field(default_factory=list, max_length=32)
    environment_constraints: list[EnvironmentConstraint] = Field(
        default_factory=list, max_length=32
    )
    allowed_risk_class: RiskClass
    resource_cap: ResourceBudget
    verification_spec_ref: VerificationSpecRef
    failure_policy_ref: PolicyRef
    immutable_hash: str = Field(default="", max_length=64)

    def seal_derived_hashes(self) -> None:
        """Seal ``immutable_hash`` before the header digest commits to it."""

        computed = content_hash(self, exclude=("content_hash", "immutable_hash"))
        if not self.immutable_hash:
            self.immutable_hash = computed
        elif self.immutable_hash != computed:
            raise ValueError(
                f"immutable_hash {self.immutable_hash!r} does not match the digest of this "
                f"node contract ({computed!r}); the contract was edited after it was sealed"
            )

    @model_validator(mode="after")
    def _authority_is_named(self) -> Self:
        if self.objective_contract_ref is None:
            raise ValueError(
                "a node contract must name the objective revision it was authorised "
                "against; routing without one would be routing without authority "
                "(SDD §7.2, ADR-044)"
            )
        if self.allowed_risk_class is RiskClass.PROHIBITED:
            raise ValueError(
                "allowed_risk_class PROHIBITED describes a node that may never run; a "
                "prohibition is expressed by not creating the node, not by contracting for it"
            )
        return self


class VerificationSpec(CanonicalContract):
    """SDD §7.3. What must be verified, by whom, and what counts as accepted.

    ADR-044 freezes verification semantics *before* routing, which is why this is a contract
    with its own identity and digest rather than a block inside :class:`NodeContract`: a
    node pins the spec by hash, and a spec that changed would change the node's hash rather
    than quietly re-defining what its verdict meant.

    **Field coverage (SDD §7.3 → here).** ``spec_id`` → the header's ``contract_id``, which
    carries the ADR-055 ``vsp`` prefix. ``version`` → ``revision``, matching the counter
    spelling used by ``ObjectiveContract`` and the v0.2 graph revisions rather than adding a
    third word for the same idea. ``claims``, ``metrics``, ``independence``,
    ``accepted_outcomes`` → unchanged, with the claim and metric shapes typed as
    :class:`Claim` and :class:`MetricThreshold`. ``content_hash`` → the header's
    ``content_hash``: §7.3's field and registry §3's field are the same digest of the same
    document, and carrying two would invite them to disagree.

    ``accepted_outcomes`` is constrained to the three terminal verdicts. A spec that
    pre-accepted ``ERROR`` would be a spec that accepts its own verifier crashing, and one
    that pre-accepted ``PENDING`` would accept never having asked.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.verification-spec"
    ID_KIND: ClassVar[str | None] = "verification_spec"

    contract_type: Literal["accretion.verification-spec"] = "accretion.verification-spec"
    revision: int = Field(ge=1)
    claims: list[Claim] = Field(min_length=1, max_length=64)
    metrics: list[MetricThreshold] = Field(default_factory=list, max_length=64)
    independence: IndependenceRequirements = Field(default_factory=IndependenceRequirements)
    accepted_outcomes: list[VerificationState] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _claims_metrics_and_outcomes_are_well_formed(self) -> Self:
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("verification spec repeats a claim_id; claim ids identify results")
        metric_ids = [metric.metric_id for metric in self.metrics]
        if len(set(metric_ids)) != len(metric_ids):
            raise ValueError("verification spec repeats a metric_id")
        if not any(claim.criticality is Criticality.REQUIRED for claim in self.claims):
            raise ValueError(
                "a verification spec with no REQUIRED claim can never block acceptance, "
                "which makes it a report rather than a verification (ADR-044)"
            )
        outcomes = set(self.accepted_outcomes)
        if len(outcomes) != len(self.accepted_outcomes):
            raise ValueError("accepted_outcomes repeats a state")
        disallowed = sorted(state.value for state in outcomes - TERMINAL_VERIFICATION_STATES)
        if disallowed:
            raise ValueError(
                f"accepted_outcomes may not contain {disallowed!r}: PENDING is not an "
                "outcome, and ERROR and QUARANTINED are states a spec may not pre-accept "
                "(registry §5.1)"
            )
        return self


class TaskFeatures(CanonicalContract):
    """The task half of a routing context's feature vector, derived from v0.1's ``TaskProfile``.

    SDD §7.4 writes ``task_features: object``. An untyped blob is the one thing a feature
    schema must not be: §7.12 makes a router model record the ``feature_schema_version`` it
    learned under, and a version number over a free-form dict versions nothing. So the
    features are named, and every one of them is *taken* from the deterministic profiler
    rather than invented here — a feature the profiler cannot produce is a feature no
    training snapshot could ever contain.

    **Field provenance — every field names its source on**
    :class:`~accretion.contracts.TaskProfile`:

    * ``source_profile_id`` ← ``TaskProfile.profile_id`` (provenance, not a feature).
    * ``complexity`` ← ``TaskProfile.complexity``.
    * ``structure_certainty`` ← ``TaskProfile.structure_certainty``.
    * ``feedback_dependency`` ← ``TaskProfile.feedback_dependency``.
    * ``dependency_complexity`` ← ``TaskProfile.dependency_complexity``.
    * ``parallelism_potential`` ← ``TaskProfile.parallelism_potential``.
    * ``uncertainty`` ← ``TaskProfile.uncertainty``.
    * ``verifier_strength`` ← ``TaskProfile.verifier_strength``.
    * ``risk`` ← ``TaskProfile.risk`` (v0.1 :class:`~accretion.contracts.RiskLevel`; the
      node's :class:`RiskClass` is a different axis and lives on ``NodeContract``).
    * ``irreversible_actions`` ← ``TaskProfile.irreversible_actions``.
    * ``expected_horizon`` ← ``TaskProfile.expected_horizon``.
    * ``profile_confidence`` ← ``TaskProfile.profile_confidence``.

    The seven scored dimensions keep ``TaskProfile``'s ``float | None`` typing rather than
    defaulting a missing score to zero. ``TaskProfile`` leaves a dimension null when the
    profiler could not observe it, and zero is a *confident* score of "none of this"; the
    difference matters to a model and would be erased on the way in.

    ``ID_KIND`` is ``None``: ADR-055 mints no prefix, because a feature vector is minted and
    owned by the :class:`RoutingContext` that embeds it.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.task-features"
    ID_KIND: ClassVar[str | None] = None

    contract_type: Literal["accretion.task-features"] = "accretion.task-features"
    source_profile_id: str = Field(min_length=1, max_length=64)
    complexity: float | None = Field(default=None, ge=0, le=1)
    structure_certainty: float | None = Field(default=None, ge=0, le=1)
    feedback_dependency: float | None = Field(default=None, ge=0, le=1)
    dependency_complexity: float | None = Field(default=None, ge=0, le=1)
    parallelism_potential: float | None = Field(default=None, ge=0, le=1)
    uncertainty: float | None = Field(default=None, ge=0, le=1)
    verifier_strength: float | None = Field(default=None, ge=0, le=1)
    risk: RiskLevel
    irreversible_actions: bool
    expected_horizon: ExpectedHorizon
    profile_confidence: float = Field(ge=0, le=1)


class ProjectFeatures(CanonicalContract):
    """The project half of a routing context's feature vector, aggregated over ``TaskProfile``.

    SDD §7.4 writes ``project_features: object`` and names no fields, so the same rule
    applies as for :class:`TaskFeatures`: nothing is invented. Every field here is an
    aggregate over the same :class:`~accretion.contracts.TaskProfile` rows the task features
    are drawn from, computed over an explicit window, because a project-level prior that
    could not say *what it summarised* would be unreproducible from a training snapshot.

    **Field provenance — the aggregation and its source field:**

    * ``feature_window_days`` — the window every aggregate below is computed over. Recorded
      rather than assumed, because the same project has different features under a 30-day
      and a 365-day window.
    * ``observed_task_count`` — the number of ``TaskProfile`` rows in the window. This is the
      denominator of every other field, and a prior computed over three tasks deserves to be
      recognisable as one.
    * ``mean_complexity`` ← mean of ``TaskProfile.complexity``.
    * ``mean_uncertainty`` ← mean of ``TaskProfile.uncertainty``.
    * ``mean_verifier_strength`` ← mean of ``TaskProfile.verifier_strength``.
    * ``irreversible_action_rate`` ← fraction of rows with
      ``TaskProfile.irreversible_actions`` true.
    * ``maximum_risk`` ← the maximum of ``TaskProfile.risk`` under
      :data:`~accretion.contracts.RISK_RANK` (and *not* under ``StrEnum`` ordering, which
      sorts ``CRITICAL`` below ``LOW``).
    * ``dominant_expected_horizon`` ← the modal ``TaskProfile.expected_horizon``.

    The four means and the two summaries are nullable for the same reason the task features
    are: an empty window yields no mean, and zero would be a claim.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.project-features"
    ID_KIND: ClassVar[str | None] = None

    contract_type: Literal["accretion.project-features"] = "accretion.project-features"
    feature_window_days: int = Field(ge=1, le=3_650)
    observed_task_count: int = Field(ge=0)
    mean_complexity: float | None = Field(default=None, ge=0, le=1)
    mean_uncertainty: float | None = Field(default=None, ge=0, le=1)
    mean_verifier_strength: float | None = Field(default=None, ge=0, le=1)
    irreversible_action_rate: float | None = Field(default=None, ge=0, le=1)
    maximum_risk: RiskLevel | None = None
    dominant_expected_horizon: ExpectedHorizon | None = None

    @model_validator(mode="after")
    def _an_empty_window_summarises_nothing(self) -> Self:
        if self.observed_task_count == 0:
            populated = sorted(
                name
                for name in (
                    "mean_complexity",
                    "mean_uncertainty",
                    "mean_verifier_strength",
                    "irreversible_action_rate",
                    "maximum_risk",
                    "dominant_expected_horizon",
                )
                if getattr(self, name) is not None
            )
            if populated:
                raise ValueError(
                    f"observed_task_count is 0 but {populated!r} carry values; an aggregate "
                    "over no tasks is not a summary, it is a guess"
                )
        return self


class RoutingContext(CanonicalContract):
    """SDD §7.4. The immutable snapshot one routing decision was made against.

    §8.2 makes this contract's id the idempotency key for routing, and §8.3 makes it the
    place where "exact snapshot" stops being a principle and becomes a field list: a changed
    registry, model, policy or contract snapshot MUST use a new request id, so every
    snapshot the decision depended on is pinned here rather than looked up later.

    **Field coverage (SDD §7.4 → here).** ``routing_request_id`` → the header's
    ``contract_id``, carrying the ADR-055 ``rrq`` prefix. ``node_contract_ref`` →
    :class:`NodeContractRef`, which pins the node's ``immutable_hash`` beside its id because
    an id alone would still resolve after a revision and §8.3 would be unenforceable.
    ``task_features`` → :class:`TaskFeatures`; ``project_features`` →
    :class:`ProjectFeatures`; ``graph_features`` → :class:`GraphFeatures` — the SDD's two
    ``object`` fields and its inline graph block, all three typed.
    ``available_runtime_snapshot_id``, ``capability_registry_snapshot_id``,
    ``connection_availability_snapshot_id``, ``policy_snapshot_id``,
    ``workspace_router_version``, ``project_adapter_version``,
    ``historical_experience_refs``, ``requested_at`` → unchanged.

    ``feature_schema_version`` is added by this implementation and is not in SDD §7.4. It is
    required by §7.12, which makes a router model record the feature schema it was trained
    under; without the same version on the context, a stored decision could not be matched
    to the model that made it, and the freeze would have versioned the model but not its
    input. It defaults to :data:`FEATURE_SCHEMA_VERSION`.

    ``requested_at`` and the header's ``created_at`` are both kept and are not the same
    instant: ``requested_at`` is when the caller asked, ``created_at`` is when this record
    was written. They differ under retry and under replay, and collapsing them would make
    latency measured from the record rather than from the request.

    ``historical_experience_refs`` is a list of v0.2 P7 ``experience_id`` strings, not typed
    references. That is the existing convention for experience ids across v0.2-v0.3 and
    re-typing it would be a registry §3.2 Major change to schemas that already store them.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.routing-context"
    ID_KIND: ClassVar[str | None] = "routing_request"

    contract_type: Literal["accretion.routing-context"] = "accretion.routing-context"
    node_contract_ref: NodeContractRef
    feature_schema_version: str = Field(
        default=FEATURE_SCHEMA_VERSION, pattern=r"^\d+\.\d+\.\d+$"
    )
    task_features: TaskFeatures
    graph_features: GraphFeatures
    project_features: ProjectFeatures
    available_runtime_snapshot_id: str = Field(min_length=1, max_length=64)
    capability_registry_snapshot_id: str = Field(min_length=1, max_length=64)
    connection_availability_snapshot_id: str = Field(min_length=1, max_length=64)
    policy_snapshot_id: str = Field(min_length=1, max_length=64)
    workspace_router_version: str = Field(min_length=1, max_length=64)
    project_adapter_version: str | None = Field(default=None, min_length=1, max_length=64)
    historical_experience_refs: list[str] = Field(default_factory=list, max_length=64)
    requested_at: datetime

    @model_validator(mode="after")
    def _features_share_this_context_scope(self) -> Self:
        if self.task_features.workspace_id != self.workspace_id:
            raise ValueError(
                "task_features were computed in a different workspace from this routing "
                "context; a feature vector cannot cross a tenancy boundary"
            )
        if self.project_features.workspace_id != self.workspace_id:
            raise ValueError(
                "project_features were computed in a different workspace from this routing "
                "context; a feature vector cannot cross a tenancy boundary"
            )
        if len(set(self.historical_experience_refs)) != len(self.historical_experience_refs):
            raise ValueError(
                "historical_experience_refs repeats an experience id, which would weight "
                "one past outcome twice"
            )
        return self


class ExecutionConfiguration(CanonicalContract):
    """SDD §7.5. The complete tuple a router selects: environment through verifier.

    ADR-042 makes the router select a *complete* configuration rather than a runtime and
    some defaults, and registry §7.3 fixes the hierarchy — environment → runtime → model →
    tools/capabilities → skills/plugin implementations → independent verifier. Every layer
    is required here; there is no partial configuration, because §9.2 says final selection
    always operates on complete tuples.

    **Field coverage (SDD §7.5 → here).** ``configuration_id`` → the header's
    ``contract_id`` (``cfg``). ``runtime`` → a :class:`~accretion.contracts.refs.RuntimeRef`,
    which carries the SDD's ``runtime_id`` and ``adapter_version`` plus the capability
    profile digest registry §4 requires. ``model`` → :class:`ModelBinding`. ``tools`` →
    ``[ToolBinding]``, carrying the SDD's ``capability_id``/``binding_id``/
    ``binding_version`` and the registry's typed ``ToolRef``. ``skills`` → ``[SkillRef]``
    directly: registry §4 asks for skill id, version *and* package digest, and the reference
    already is exactly that. ``verifier`` → :class:`VerifierBinding`. ``environment`` →
    :class:`EnvironmentBinding`. ``configuration_hash`` → unchanged, but see below.

    **What ``configuration_hash`` covers, and why it is not the header digest.** §9.2
    requires behaviourally equivalent candidates to be canonicalised by configuration
    signature, and §7.10 keys reusable experience by ``configuration_hash``. Both need the
    same configuration built twice, in two projects, on two days, to produce the *same*
    value — which the header digest cannot do, because it covers ``contract_id`` and
    ``created_at``. So ``configuration_hash`` is computed over exactly the six semantic
    fields named in :data:`_CONFIGURATION_SIGNATURE_FIELDS` and nothing else, and the header
    ``content_hash`` then commits to it. Two configurations with the same signature are the
    same execution surface; two with the same ``content_hash`` are the same *document*.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.execution-configuration"
    ID_KIND: ClassVar[str | None] = "execution_configuration"
    DERIVED_HASH_FIELDS: ClassVar[tuple[str, ...]] = ("configuration_hash",)

    contract_type: Literal["accretion.execution-configuration"] = (
        "accretion.execution-configuration"
    )
    environment: EnvironmentBinding
    runtime: RuntimeRef
    model: ModelBinding
    tools: list[ToolBinding] = Field(default_factory=list, max_length=64)
    skills: list[SkillRef] = Field(default_factory=list, max_length=64)
    verifier: VerifierBinding
    configuration_hash: str = Field(default="", max_length=64)

    def configuration_signature(self) -> dict[str, Any]:
        """The six semantic fields ``configuration_hash`` is computed over."""

        return {name: getattr(self, name) for name in _CONFIGURATION_SIGNATURE_FIELDS}

    def seal_derived_hashes(self) -> None:
        """Seal ``configuration_hash`` over the signature, before the header digest."""

        computed = content_hash(self.configuration_signature(), exclude=())
        if not self.configuration_hash:
            self.configuration_hash = computed
        elif self.configuration_hash != computed:
            raise ValueError(
                f"configuration_hash {self.configuration_hash!r} does not match the digest "
                f"of this configuration's signature ({computed!r}); the tuple was edited "
                "after it was sealed"
            )


_CONFIGURATION_SIGNATURE_FIELDS: tuple[str, ...] = (
    "environment",
    "runtime",
    "model",
    "tools",
    "skills",
    "verifier",
)
"""The registry §7.3 hierarchy, in order, and the exact input to ``configuration_hash``.

Named as a module constant rather than inlined so that the schema export, the tests and any
future migration all read the same list. Adding a field to
:class:`ExecutionConfiguration` without adding it here would produce two configurations that
behave differently and share a signature, which is why the fixture tests assert the tuple
covers every non-header field of the model.
"""


class ConfigurationCandidate(CanonicalContract):
    """SDD §7.6. One complete configuration with its predicted outcomes and its verdicts.

    **Field coverage (SDD §7.6 → here).** ``candidate_id`` → the header's ``contract_id``
    (``ccd``; ADR-055 chose ``ccd`` because ``cnd`` is already the connector definition).
    ``configuration``, ``construction_stage``, ``hard_eligible``,
    ``compatibility_decision_refs``, ``uncertainty_score``, ``lower_confidence_success``,
    ``utility_score``, ``pareto_dominated``, ``fallback_eligible`` → unchanged. ``predicted``
    → :class:`PredictedOutcomes`, its five ``DistributionEstimate`` members typed.

    ``utility_score`` is nullable because §9.1 ranks by utility at stage 10: a candidate
    captured before that stage has no score, and zero would rank it as the worst rather than
    as unranked.

    The validator enforces the one rule §9.2 states in words and no type can state alone: a
    hard-incompatible candidate is removed immediately, so it cannot also be the audited
    fallback that §9.2 requires to be retained. A candidate that claimed both would let an
    incompatible configuration reach dispatch by the fallback path.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.configuration-candidate"
    ID_KIND: ClassVar[str | None] = "configuration_candidate"

    contract_type: Literal["accretion.configuration-candidate"] = (
        "accretion.configuration-candidate"
    )
    routing_request_id: str = Field(min_length=1, max_length=64)
    configuration: ExecutionConfiguration
    construction_stage: ConstructionStage
    hard_eligible: bool
    compatibility_decision_refs: list[str] = Field(default_factory=list, max_length=64)
    predicted: PredictedOutcomes
    uncertainty_score: float = Field(ge=0)
    lower_confidence_success: float = Field(ge=0, le=1)
    utility_score: float | None = None
    pareto_dominated: bool = False
    fallback_eligible: bool = False

    @model_validator(mode="after")
    def _an_ineligible_candidate_is_not_a_fallback(self) -> Self:
        if self.fallback_eligible and not self.hard_eligible:
            raise ValueError(
                "a candidate that is not hard-eligible cannot be the audited fallback; "
                "§9.2 removes hard-incompatible candidates immediately, and marking one "
                "fallback-eligible would route around that removal"
            )
        return self


class CompatibilityDecision(CanonicalContract):
    """SDD §7.7. A deterministic admissibility receipt for one configuration subject.

    Distinct from the v0.2 ``CompatibilityAssessment``, which grades how usable a *past
    experience* is; this decides whether a *candidate configuration* may be built at all,
    and both stay (ADR-054 c).

    **Field coverage (SDD §7.7 → here).** ``decision_id`` → the header's ``contract_id``
    (``cmp``). ``subject_type``, ``subject_ref``, ``status``, ``rule_id``, ``rule_version``,
    ``reason_code``, ``evaluated_at`` → unchanged. ``evidence_refs`` → ``[EvidenceRef]``:
    typed rather than bare ids, because registry §19 requires simulation and physical
    evidence to stay type-distinct at every boundary and a consumer holding this decision
    should not have to dereference anything to honour that.

    ``rule_id`` *and* ``rule_version`` are both required: a decision replayed years later is
    only explicable against the exact rule that produced it, and "rule 7" is a label.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.compatibility-decision"
    ID_KIND: ClassVar[str | None] = "compatibility_decision"

    contract_type: Literal["accretion.compatibility-decision"] = "accretion.compatibility-decision"
    subject_type: SubjectType
    subject_ref: str = Field(min_length=1, max_length=255)
    status: CompatibilityStatus
    rule_id: str = Field(min_length=1, max_length=64)
    rule_version: str = Field(min_length=1, max_length=64)
    reason_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=64)
    evaluated_at: datetime

    def is_compatible(self) -> bool:
        """``True`` only for :attr:`CompatibilityStatus.COMPATIBLE`.

        SDD §7.7: "``UNKNOWN`` MUST NOT be treated as compatible for a required
        constraint." The method is a single identity comparison rather than a
        ``status is not INCOMPATIBLE`` test, and the difference between those two spellings
        is the whole rule: the second admits ``UNKNOWN``, which is how an unchecked
        assumption reaches dispatch wearing a receipt. Written as an identity comparison on
        the enum member, never as ``==`` against a string, because ``StrictModel`` keeps
        enum members as members.
        """

        return self.status is CompatibilityStatus.COMPATIBLE


class StructuredExplanation(CanonicalContract):
    """Why a routing decision came out the way it did — v0.4-owned, defined by this freeze.

    SDD §7.8 names ``explanation: StructuredExplanation`` and never gives it a shape, so
    this contract is owned by v0.4 and this docstring is its specification. Three parts,
    each answering a question an operator actually asks:

    * ``summary`` — the one-sentence reason, for the panel §17.1 renders.
    * ``factors`` — what argued for and against, with signed weights and the evidence each
      appealed to. Signed, because an explanation that lists only the winning arguments is
      a justification.
    * ``rejected_candidates`` — what else was considered and the coded reason it was not
      chosen, so "why not the cheaper one" has an answer that does not require re-running
      the router.

    It is a contract rather than a plain value object because it inherits the registry §3
    header like everything else in this family, and because §17.1 shows it beside the
    receipt: a rendered explanation that could not be hashed could not be shown to be the
    explanation that was actually recorded.

    ``ID_KIND`` is ``None``: an explanation is minted by the receipt that carries it and has
    no id space of its own (ADR-055 lists no prefix for it).
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.structured-explanation"
    ID_KIND: ClassVar[str | None] = None

    contract_type: Literal["accretion.structured-explanation"] = "accretion.structured-explanation"
    summary: str = Field(min_length=1, max_length=2_000)
    factors: list[ExplanationFactor] = Field(default_factory=list, max_length=64)
    rejected_candidates: list[RejectedCandidate] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def _factors_and_rejections_are_distinct(self) -> Self:
        factor_ids = [factor.factor_id for factor in self.factors]
        if len(set(factor_ids)) != len(factor_ids):
            raise ValueError("explanation repeats a factor_id, which double-counts a reason")
        rejected_ids = [item.candidate_id for item in self.rejected_candidates]
        if len(set(rejected_ids)) != len(rejected_ids):
            raise ValueError("explanation rejects the same candidate twice")
        return self


class RoutingDecisionReceipt(CanonicalContract):
    """SDD §7.8. The immutable record of one routing decision, and the thing replay reads.

    §8.2 makes this the durable answer to a ``routing_request_id``: repeated requests with
    identical immutable inputs return the same receipt, and dispatch must reference a
    persisted one. Everything needed to explain, replay and off-policy-evaluate the decision
    is here, and nothing that could not be recomputed from it is trusted elsewhere.

    **Field coverage (SDD §7.8 → here).** ``receipt_id`` → the header's ``contract_id``
    (``rcp``). ``routing_request_id``, ``node_contract_hash``, ``selected_configuration_id``,
    ``selected_configuration_hash``, ``decision_type``, ``selection_propensity``,
    ``candidate_summary_refs``, ``experience_refs``, ``workspace_router_version``,
    ``project_adapter_version``, ``objective_contract_version``,
    ``capability_registry_snapshot_id``, ``policy_snapshot_id``,
    ``fallback_configuration_id``, ``explanation``, ``created_at`` → unchanged.
    ``predicted_outcomes`` → :class:`PredictedOutcomes` (nullable: a decision that selected
    nothing predicted nothing). ``uncertainty`` → :class:`UncertaintySummary`.
    ``rejected_candidate_reasons`` → ``[RejectedCandidate]``.

    ``node_contract_hash`` is the node contract's ``immutable_hash`` and not its header
    ``content_hash``, for the reason :class:`NodeContract` gives: the immutable hash is the
    value other contracts pin and is stable under later header additions.

    **Receipts refuse secret-shaped values.** §12 requires events to exclude tokens,
    secrets, hidden provider payloads and private reasoning, and §14.2's controls say the
    same about what is stored. A receipt is the most likely place for one to arrive by
    accident: its ``labels`` are free-form and its explanation quotes whatever the router
    was reasoning over. The validator runs the repository's real redactor over the whole
    payload and refuses the record if redaction would change anything — key-shaped
    (``authorization``, ``api_key``, ``nonce``) or value-shaped (a bearer token, a JWT, an
    ``sk-`` key). Refusing is deliberate and is not the same as redacting: a receipt is
    hashed and replayed, so silently rewriting one would produce a document that no longer
    matches its own digest and an audit trail that had been edited.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.routing-decision-receipt"
    ID_KIND: ClassVar[str | None] = "routing_receipt"

    contract_type: Literal["accretion.routing-decision-receipt"] = (
        "accretion.routing-decision-receipt"
    )
    routing_request_id: str = Field(min_length=1, max_length=64)
    node_contract_hash: str = Field(pattern=_DIGEST)
    selected_configuration_id: str | None = Field(default=None, min_length=1, max_length=64)
    selected_configuration_hash: str | None = Field(default=None, pattern=_DIGEST)
    decision_type: DecisionType
    selection_propensity: float | None = Field(default=None, ge=0, le=1)
    predicted_outcomes: PredictedOutcomes | None = None
    uncertainty: UncertaintySummary
    candidate_summary_refs: list[str] = Field(default_factory=list, max_length=256)
    rejected_candidate_reasons: list[RejectedCandidate] = Field(
        default_factory=list, max_length=256
    )
    experience_refs: list[str] = Field(default_factory=list, max_length=64)
    workspace_router_version: str = Field(min_length=1, max_length=64)
    project_adapter_version: str | None = Field(default=None, min_length=1, max_length=64)
    objective_contract_version: int = Field(ge=1)
    capability_registry_snapshot_id: str = Field(min_length=1, max_length=64)
    policy_snapshot_id: str = Field(min_length=1, max_length=64)
    fallback_configuration_id: str | None = Field(default=None, min_length=1, max_length=64)
    explanation: StructuredExplanation

    @model_validator(mode="after")
    def _selection_is_coherent_and_carries_no_secret(self) -> Self:
        selected = (self.selected_configuration_id, self.selected_configuration_hash)
        if (selected[0] is None) != (selected[1] is None):
            raise ValueError(
                "selected_configuration_id and selected_configuration_hash must be present "
                "or absent together; an id without a hash cannot be replayed and a hash "
                "without an id cannot be dispatched"
            )
        if selected[0] is None and self.decision_type is not DecisionType.HUMAN_REVIEW_REQUIRED:
            raise ValueError(
                f"decision_type {self.decision_type.value} selected no configuration; only "
                "HUMAN_REVIEW_REQUIRED may select nothing (SDD §8.1)"
            )
        if selected[0] is not None and self.decision_type is DecisionType.HUMAN_REVIEW_REQUIRED:
            raise ValueError(
                "HUMAN_REVIEW_REQUIRED means no configuration was selected, so it may not "
                "carry one"
            )
        if self.decision_type is DecisionType.EXPLORE and self.selection_propensity is None:
            raise ValueError(
                "an EXPLORE decision must record its behaviour propensity; §9.5 requires it "
                "for off-policy evaluation, and a missing propensity makes the decision "
                "unusable as evidence"
            )
        if _find_secret_shaped_values(self.model_dump(mode="python")):
            raise ValueError(
                "this receipt carries a secret-shaped value: a key or a value that "
                "accretion.redaction would redact. Receipts are hashed and replayed, so a "
                "secret is refused rather than rewritten (SDD §12, §14.2)"
            )
        return self


class IndependentVerificationResult(CanonicalContract):
    """SDD §7.9's ``VerificationResult``, under the code name ADR-054 (a) assigns it.

    v0.1 already owns ``VerificationResult``: it is the run/iteration verifier outcome,
    stored in the ``verifications`` table and exposed through the API, and renaming it would
    be a registry §3.2 Major change to a schema with live readers. So the v0.4 contract —
    a *node-scoped, independent* verification tied to a spec hash and its evidence — takes
    the explicit name, and the two live side by side with ``source_verification_id`` as the
    link between them.

    **Field coverage (SDD §7.9 → here).** ``verification_result_id`` → the header's
    ``contract_id`` (``ivr``). ``execution_instance_id``, ``verification_spec_hash``,
    ``claim_results``, ``conflict_refs``, ``signed_at`` → unchanged. ``status`` →
    :class:`VerificationState` (registry §5.1, per the §7.9 code-name note), which is what
    lets an independent verifier report ``ERROR`` or ``QUARANTINED`` at all.
    ``verifier_implementation_id`` → ``verifier``, a typed
    :class:`~accretion.contracts.refs.VerifierRef` carrying the contract id *and* the
    implementation digest registry §4 requires; ``verifier_version`` → unchanged beside it,
    because the digest says what ran and the version says what it was called.
    ``deterministic_evidence_refs`` and ``model_review_refs`` → ``[EvidenceRef]``, typed.

    ``source_verification_id`` is added by ADR-054 (a) and is nullable: an independent
    verification may be produced from a v0.1 result, or it may be the first verdict on a
    node that no v0.1 path ever touched.

    ``deterministic_evidence_refs`` and ``model_review_refs`` are two fields rather than one
    list with a flag because §14.3's reward-hacking controls treat them differently: a model
    review is an opinion and a deterministic check is a measurement, and a verdict that
    could not say which kind it rested on would let the weaker one masquerade as the
    stronger.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.independent-verification-result"
    ID_KIND: ClassVar[str | None] = "independent_verification_result"

    contract_type: Literal["accretion.independent-verification-result"] = (
        "accretion.independent-verification-result"
    )
    execution_instance_id: str = Field(min_length=1, max_length=64)
    verification_spec_hash: str = Field(pattern=_DIGEST)
    verifier: VerifierRef
    verifier_version: str = Field(min_length=1, max_length=64)
    status: VerificationState
    claim_results: list[ClaimResult] = Field(default_factory=list, max_length=64)
    deterministic_evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=64)
    model_review_refs: list[EvidenceRef] = Field(default_factory=list, max_length=64)
    conflict_refs: list[str] = Field(default_factory=list, max_length=64)
    source_verification_id: str | None = Field(default=None, min_length=1, max_length=64)
    signed_at: datetime

    @model_validator(mode="after")
    def _a_pass_cannot_contain_a_failure(self) -> Self:
        claim_ids = [result.claim_id for result in self.claim_results]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("a verification result reports the same claim_id twice")
        if self.status is VerificationState.PASS:
            blocking = sorted(
                result.claim_id
                for result in self.claim_results
                if result.status is not VerificationState.PASS
            )
            if blocking:
                raise ValueError(
                    f"status is PASS while claims {blocking!r} did not pass; registry §5.1 "
                    "makes a FAIL, an ERROR or an unresolved INCONCLUSIVE blocking, and a "
                    "claim result carries no criticality of its own, so the aggregate cannot "
                    "know that an inconclusive claim was merely supporting. Overriding them "
                    "here would be the reward-hacking path §14.3 exists to close; a verdict "
                    "that resolved a conflict says so by restating the claim, not by "
                    "outvoting it"
                )
        return self


class ExperienceRecord(CanonicalContract):
    """SDD §7.10. A routing-scoped **projection** over the v0.2 P7 ``Experience`` (ADR-054 b).

    This record declares **none** of ``Experience``'s fields. It is keyed by the same
    ``experience_id`` — carried as the header's ``contract_id``, which is why ``ID_KIND`` is
    the existing ``experience`` prefix (``exp``) and ADR-055 mints no new one — and
    everything the P7 record already knows is *read from it* rather than copied here.
    Copying would have produced the duplicate source of truth registry §21 forbids, and the
    two copies would have diverged the first time an experience was retracted.

    **Read from** :class:`~accretion.experience.models.Experience` **(never re-declared):**
    ``project_id`` (the header's ``project_id`` is the same project and is the only place it
    appears — SDD §7.10's ``source_project_id`` is that field), ``source_run_id`` and
    ``source_candidate_id`` (SDD §7.10's ``source_run_id``), ``source_kind``,
    ``repository_identity``, ``source_commit``, ``architecture_version``, ``task_id``,
    ``task_type``, ``task_family``, the seven digests (``manifest_digest``,
    ``policy_digest``, ``verifier_digest``, ``prompt_digest``, ``context_digest``,
    ``tool_profile_digest``, ``content_digest``), ``manifest_paths``, ``requested_skills``,
    ``allowed_capabilities``, ``denied_capabilities``, ``verifier_ids``,
    ``protected_side_effects``, ``provider``, ``runtime_model``, ``runtime_version``,
    ``trust``, ``polarity``, ``outcome``, ``failure_taxonomy``, ``revision``, ``retracted``
    and P7's own ``created_at``.

    SDD §7.10 spells ``final_run_status`` as ``PASS | FAIL | INCONCLUSIVE | NOT_AVAILABLE``; the
    fourth value is spelled ``null`` here, because registry §5.1 fixes ``VerificationState`` at
    six values and a seventh may not be minted for a projection's convenience.

    **Added here, because the P7 record has nowhere to put them:**
    ``visibility`` — P7 experience is project-local and v0.4 is the first release that
    shares it; ``source_node_execution_id`` — P7 is run-scoped and ADR-041 makes routing
    node-scoped; ``contract_signature`` — the retrieval key a node matches on;
    ``configuration_hash`` — the ``ExecutionConfiguration`` signature this outcome is
    evidence about; ``local_verification_status`` and ``final_run_status`` — P7 carries a
    free-text ``outcome`` and a ``polarity``, not a :class:`VerificationState`;
    ``attribution`` — §9.6's derived, versioned credit; ``outcomes`` — the measured quality,
    cost and latency P7 never recorded; ``failure_type`` — the typed §7.11 taxonomy beside
    P7's free-string ``failure_taxonomy``; ``contradiction_status``; ``evidence_refs`` —
    typed §4 references; ``permission_provenance`` — the §10.1 sharing proof;
    ``eligible_for_learning``.

    **How P7's vocabulary maps onto this record** (ADR-054 b). ``ExperienceTrust`` and
    ``ExperiencePolarity`` remain the P7 vocabulary and are not restated: a record is
    eligible for learning only when its own verification passed and no contradiction is
    open, and the P7 rules — ``POSITIVE`` requires ``HIGH`` trust, ``NEGATIVE`` cannot have
    it — continue to govern the ``Experience`` row this projection is keyed by. A retracted
    ``Experience`` makes this projection ineligible by the same dereference.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.experience-record"
    ID_KIND: ClassVar[str | None] = "experience"

    contract_type: Literal["accretion.experience-record"] = "accretion.experience-record"
    visibility: Visibility
    source_node_execution_id: str = Field(min_length=1, max_length=64)
    contract_signature: ContractSignature
    configuration_hash: str = Field(pattern=_DIGEST)
    local_verification_status: VerificationState
    final_run_status: VerificationState | None = None
    attribution: AttributionSummary
    outcomes: ExperienceOutcomes
    failure_type: FailureType | None = None
    contradiction_status: ContradictionStatus = ContradictionStatus.NONE
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=64)
    permission_provenance: PermissionProvenance
    eligible_for_learning: bool = False

    @model_validator(mode="after")
    def _eligibility_requires_a_clean_verified_outcome(self) -> Self:
        if self.visibility is not self.permission_provenance.scope:
            raise ValueError(
                f"visibility {self.visibility.value} does not match the scope "
                f"{self.permission_provenance.scope.value} the permission provenance grants; "
                "a record cannot be shared more widely than it was permitted"
            )
        if not self.eligible_for_learning:
            return self
        if self.local_verification_status is not VerificationState.PASS:
            raise ValueError(
                f"eligible_for_learning is true while local verification is "
                f"{self.local_verification_status.value}; only a verified outcome is "
                "evidence a router may learn from (ADR-048)"
            )
        if self.contradiction_status is ContradictionStatus.OPEN:
            raise ValueError(
                "eligible_for_learning is true while a contradiction is OPEN; an unresolved "
                "contradiction is exactly the record a training snapshot must exclude (§10.1)"
            )
        return self


class FailureEvent(CanonicalContract):
    """SDD §7.11. A typed failure and the layer that owns recovering from it.

    §9.7 routes recovery by this record: configuration failures go back to the router,
    structural failures to the planner, verification conflicts to evidence resolution, and
    policy or risk failures to human authority. That routing is only as good as the
    classification, which is why ``classification_confidence`` is stored beside the type
    rather than discarded.

    **Field coverage (SDD §7.11 → here).** ``failure_event_id`` → the header's
    ``contract_id`` (``flr``). ``execution_instance_id``, ``failure_type``,
    ``affected_layer``, ``retryable``, ``classification_confidence``,
    ``attempted_configuration_hashes`` → unchanged. ``evidence_refs`` → ``[EvidenceRef]``,
    typed. ``assigned_owner`` → registry §5.4's :class:`FailureOwner` rather than the SDD's
    component names, per registry §2 precedence and for the reason that enum's docstring
    gives. ``recommended_action`` → :class:`RecommendedAction`, typed instead of ``object``.

    ``attempted_configuration_hashes`` is what makes §9.7's last rule enforceable —
    "equivalent failed configurations MUST NOT repeat without new evidence" — because the
    hashes are ``ExecutionConfiguration.configuration_hash`` signatures, which are equal
    exactly when two configurations are the same execution surface.

    The validator refuses ``retryable`` for the three owners registry §5.4 says stop
    automatic recovery. A safety, authority or unknown failure marked retryable is not a
    slightly optimistic record; it is a record that would drive an automatic retry past the
    stop that exists to prevent one.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.failure-event"
    ID_KIND: ClassVar[str | None] = "failure_event"

    contract_type: Literal["accretion.failure-event"] = "accretion.failure-event"
    execution_instance_id: str = Field(min_length=1, max_length=64)
    failure_type: FailureType
    affected_layer: str = Field(min_length=1, max_length=128)
    retryable: bool
    classification_confidence: float = Field(ge=0, le=1)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=64)
    attempted_configuration_hashes: list[str] = Field(default_factory=list, max_length=64)
    assigned_owner: FailureOwner
    recommended_action: RecommendedAction

    @model_validator(mode="after")
    def _safety_authority_and_unknown_stop_automatic_recovery(self) -> Self:
        for digest in self.attempted_configuration_hashes:
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError(
                    f"attempted configuration hash {digest!r} is not a lowercase sha256 "
                    "digest; §9.7 compares these for equality, and two spellings of one "
                    "digest would let an equivalent failed configuration repeat"
                )
        if len(set(self.attempted_configuration_hashes)) != len(
            self.attempted_configuration_hashes
        ):
            raise ValueError("attempted_configuration_hashes repeats a configuration signature")
        if self.assigned_owner in NON_RECOVERABLE_FAILURE_OWNERS:
            if self.retryable:
                raise ValueError(
                    f"assigned_owner {self.assigned_owner.value} stops automatic recovery "
                    "(registry §5.4), so this failure cannot be marked retryable"
                )
            if self.recommended_action.retry_allowed:
                raise ValueError(
                    f"assigned_owner {self.assigned_owner.value} stops automatic recovery "
                    "(registry §5.4), so its recommended action cannot allow a retry"
                )
        return self


class RouterModelVersion(CanonicalContract):
    """SDD §7.12. An immutable router artifact with its data, its config and its lineage.

    ADR-049 makes promotion an offline, versioned, reversible release, which means a router
    version must be a record rather than a file path: the artifact digest, the snapshot it
    was trained on, the feature schema it assumes and the parent it descends from are all
    part of what "this router" means, and a rollback that restored only the file would
    restore the wrong thing.

    **Field coverage (SDD §7.12 → here).** ``router_version_id`` → the header's
    ``contract_id`` (``rmv``). ``workspace_id`` and ``project_id`` → header.
    ``algorithm_id``, ``feature_schema_version``, ``training_snapshot_id``,
    ``artifact_digest``, ``calibration_artifact_digest``, ``parent_version_id``,
    ``created_at`` → unchanged. ``scope`` → :class:`RouterScope`; ``status`` →
    :class:`RouterStatus`.

    ``PROJECT_SCOPED`` is ``False`` here — one of three contracts in the family where it is.
    SDD §7.12 makes ``project_id`` explicitly nullable, because a ``TEAM_WORKSPACE`` prior
    belongs to the workspace and to no project; the validator makes the nullability exact
    rather than merely permitted, requiring a project for a ``PROJECT_ADAPTER`` and refusing
    one for a workspace prior.

    ``calibration_artifact_digest`` is separate from ``artifact_digest`` because §9.3 and
    OQ-405 make calibration a distinct, replaceable component: recalibrating a model without
    retraining it produces a new router version, and one combined digest could not say which
    half changed.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.router-model-version"
    ID_KIND: ClassVar[str | None] = "router_model_version"
    PROJECT_SCOPED: ClassVar[bool] = False

    contract_type: Literal["accretion.router-model-version"] = "accretion.router-model-version"
    scope: RouterScope
    algorithm_id: str = Field(min_length=1, max_length=128)
    feature_schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    training_snapshot_id: str = Field(min_length=1, max_length=64)
    artifact_digest: str = Field(pattern=_DIGEST)
    calibration_artifact_digest: str = Field(pattern=_DIGEST)
    parent_version_id: str | None = Field(default=None, min_length=1, max_length=64)
    status: RouterStatus

    @model_validator(mode="after")
    def _scope_and_project_agree(self) -> Self:
        if self.scope is RouterScope.PROJECT_ADAPTER and self.project_id is None:
            raise ValueError(
                "a PROJECT_ADAPTER router version must name the project it adapts to"
            )
        if self.scope is RouterScope.TEAM_WORKSPACE and self.project_id is not None:
            raise ValueError(
                "a TEAM_WORKSPACE router version is the workspace prior and belongs to no "
                "single project; naming one would make the prior look project-specific"
            )
        if self.parent_version_id == self.contract_id:
            raise ValueError("a router version cannot be its own parent")
        return self


class RouterTrainingSnapshot(CanonicalContract):
    """SDD §10.1. Exactly which evidence a router version was fitted on, and under what rules.

    §10.1 lists seven things a snapshot MUST record and this contract is that list, typed.
    The reason it is a contract rather than a query is reproducibility: a snapshot described
    as "everything eligible as of March" cannot be rebuilt once eligibility changes, and a
    promotion report that pointed at an unrebuildable snapshot would be an unfalsifiable
    claim.

    **Field coverage (SDD §10.1 → here).** "Included experience IDs" →
    ``included_experience_ids``. "Permission and visibility proof" →
    ``permission_proof`` (:class:`PermissionProvenance`). "Contract/feature schema versions"
    → ``contract_schema_version`` and ``feature_schema_version``. "Contradiction treatment"
    → ``excluded_contradiction_statuses`` plus the prose ``contradiction_treatment`` — the
    machine-checkable rule and the human explanation of it, because the list alone cannot
    say *why* and the prose alone cannot be enforced. "Deduplication rules" →
    ``deduplication_rule``. "Time/provider/model version boundaries" → ``window_start``,
    ``window_end`` and ``provider_version_boundaries``. "Training, validation, and holdout
    project groups" → ``split`` (:class:`SnapshotSplit`).

    ``PROJECT_SCOPED`` is ``False``: a snapshot spans the workspace by construction, and
    its per-project structure is in ``split``.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.router-training-snapshot"
    ID_KIND: ClassVar[str | None] = "router_training_snapshot"
    PROJECT_SCOPED: ClassVar[bool] = False

    contract_type: Literal["accretion.router-training-snapshot"] = (
        "accretion.router-training-snapshot"
    )
    included_experience_ids: list[str] = Field(min_length=1, max_length=100_000)
    permission_proof: PermissionProvenance
    contract_schema_version: str = Field(
        default=CONTRACT_SCHEMA_VERSION, pattern=r"^\d+\.\d+\.\d+$"
    )
    feature_schema_version: str = Field(
        default=FEATURE_SCHEMA_VERSION, pattern=r"^\d+\.\d+\.\d+$"
    )
    excluded_contradiction_statuses: list[ContradictionStatus] = Field(
        default_factory=list, max_length=3
    )
    contradiction_treatment: str = Field(min_length=1, max_length=2_000)
    deduplication_rule: str = Field(min_length=1, max_length=2_000)
    window_start: datetime
    window_end: datetime
    provider_version_boundaries: dict[str, str] = Field(default_factory=dict)
    split: SnapshotSplit

    @model_validator(mode="after")
    def _the_window_and_the_manifest_are_well_formed(self) -> Self:
        if self.window_end <= self.window_start:
            raise ValueError(
                f"window_end {self.window_end.isoformat()} is not after window_start "
                f"{self.window_start.isoformat()}; a snapshot over an empty window includes "
                "nothing it could have observed"
            )
        if len(set(self.included_experience_ids)) != len(self.included_experience_ids):
            raise ValueError(
                "included_experience_ids repeats an id, which would weight one experience "
                "twice and silently break the deduplication rule this snapshot declares"
            )
        if len(set(self.excluded_contradiction_statuses)) != len(
            self.excluded_contradiction_statuses
        ):
            raise ValueError("excluded_contradiction_statuses repeats a status")
        return self


class RouterPromotionReport(CanonicalContract):
    """SDD §7.13. The holdout, cohort, safety, rollback and human record of one promotion.

    §10.3 makes promotion atomic and reversible, and this is the document that authorises
    it. ADR-049's "reversible" is why ``rollback_target`` is required even for a rejection:
    the report states what would be restored, and a report that named a rollback target only
    on success would leave the failure path undocumented.

    **Field coverage (SDD §7.13 → here).** ``report_id`` → the header's ``contract_id``
    (``rpr``). ``candidate_version``, ``baseline_version``, ``training_snapshot_id``,
    ``holdout_definition_id``, ``rollback_target``, ``created_at`` → unchanged.
    ``primary_metric_result``, ``verified_success_non_regression``,
    ``false_acceptance_non_regression``, ``calibration_result`` → four
    :class:`MetricComparison` values, typed instead of ``object``. ``cohort_results`` →
    ``[CohortResult]``; ``shadow_result`` → :class:`ShadowSummary`;
    ``critical_regressions`` and ``noncritical_tradeoffs`` → ``[RegressionFinding]``.
    ``decision`` → :class:`RouterPromotionDecision`; ``approved_by`` → a typed
    :class:`~accretion.contracts.PrincipalRef`, nullable because a rejection needs no
    approver.

    The validator enforces the two rules §10.3 states in prose: a critical regression blocks
    promotion, and a promotion is a human act. A critical *cohort* that did not pass counts
    as a critical regression whether or not anyone wrote it into
    ``critical_regressions`` — otherwise the block could be avoided by leaving a list empty.

    ``PROJECT_SCOPED`` is ``False``: promotion is a workspace release (OQ-411 puts approval
    with a workspace admin or research owner), and a report scoped to one project would
    misdescribe what was promoted.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.router-promotion-report"
    ID_KIND: ClassVar[str | None] = "router_promotion_report"
    PROJECT_SCOPED: ClassVar[bool] = False

    contract_type: Literal["accretion.router-promotion-report"] = (
        "accretion.router-promotion-report"
    )
    candidate_version: str = Field(min_length=1, max_length=64)
    baseline_version: str = Field(min_length=1, max_length=64)
    training_snapshot_id: str = Field(min_length=1, max_length=64)
    holdout_definition_id: str = Field(min_length=1, max_length=64)
    primary_metric_result: MetricComparison
    verified_success_non_regression: MetricComparison
    false_acceptance_non_regression: MetricComparison
    calibration_result: MetricComparison
    cohort_results: list[CohortResult] = Field(default_factory=list, max_length=256)
    shadow_result: ShadowSummary
    critical_regressions: list[RegressionFinding] = Field(default_factory=list, max_length=64)
    noncritical_tradeoffs: list[RegressionFinding] = Field(default_factory=list, max_length=64)
    rollback_target: str = Field(min_length=1, max_length=64)
    decision: RouterPromotionDecision
    approved_by: PrincipalRef | None = None

    @model_validator(mode="after")
    def _a_critical_regression_blocks_promotion(self) -> Self:
        if self.candidate_version == self.baseline_version:
            raise ValueError(
                "candidate_version equals baseline_version; a promotion report compares two "
                "router versions and this one compares a version with itself"
            )
        undisclosed = sorted(
            finding.finding_id
            for finding in self.noncritical_tradeoffs
            if finding.disclosed_bound is None
        )
        if undisclosed:
            raise ValueError(
                f"non-critical tradeoffs {undisclosed!r} carry no disclosed bound; §10.3 "
                "allows a tradeoff only with explicit bounds and disclosure, and an "
                "undisclosed tradeoff is a regression"
            )
        if self.decision is not RouterPromotionDecision.PROMOTE:
            return self
        if self.critical_regressions:
            raise ValueError(
                "decision is PROMOTE while critical regressions are recorded; §10.3 makes a "
                "critical correctness or safety regression a block, not a tradeoff"
            )
        failed_critical_cohorts = sorted(
            cohort.cohort_id
            for cohort in self.cohort_results
            if cohort.critical and not cohort.comparison.passed
        )
        if failed_critical_cohorts:
            raise ValueError(
                f"decision is PROMOTE while critical cohorts {failed_critical_cohorts!r} did "
                "not pass; a failed critical cohort blocks whether or not it was also "
                "written into critical_regressions"
            )
        for name in (
            "verified_success_non_regression",
            "false_acceptance_non_regression",
        ):
            comparison: MetricComparison = getattr(self, name)
            if not comparison.passed:
                raise ValueError(
                    f"decision is PROMOTE while {name} did not pass; §10.2 makes both "
                    "non-regression checks mandatory gates"
                )
        if self.approved_by is None:
            raise ValueError(
                "decision is PROMOTE without an approver; §10.3 requires the promotion "
                "record to name the human who authorised it (OQ-411)"
            )
        return self


class ShadowDecision(CanonicalContract):
    """SDD §11.4 and the §13 ``shadow_decisions`` table: what a candidate router *would* have done.

    ADR-046 stages v0.4 as offline, then shadow, then guarded bandit, and this is the shadow
    stage's unit of evidence: for one executed decision, the decision a candidate router
    produced without being allowed to act on it. Nothing here dispatches; a shadow decision
    that could affect execution would not be a shadow.

    **Field coverage.** SDD §7 gives this record no schema — §11.4 defines the endpoints and
    §13 names the table's key fields as "executed receipt, shadow receipt, comparison" — so
    the shape is owned by this freeze. ``executed_receipt_id`` and ``shadow_receipt_id`` are
    the two receipts; ``shadow_router_version_id`` names the candidate that produced the
    second one, without which a batch of shadow decisions could not be attributed to a
    model; the two configuration hashes and ``agreement`` are the comparison; and
    ``projected_utility_delta`` is what §10.2's "shadow-decision agreement and projected
    utility" evaluation aggregates.

    ``agreement`` is stored rather than derived, and then checked against the hashes it
    claims to summarise. Storing it makes the common query cheap; checking it means the
    stored value cannot drift from the two digests beside it, which is the only way a
    summary field in a frozen contract is safe.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.shadow-decision"
    ID_KIND: ClassVar[str | None] = "shadow_decision"

    contract_type: Literal["accretion.shadow-decision"] = "accretion.shadow-decision"
    executed_receipt_id: str = Field(min_length=1, max_length=64)
    shadow_receipt_id: str = Field(min_length=1, max_length=64)
    shadow_router_version_id: str = Field(min_length=1, max_length=64)
    executed_configuration_hash: str | None = Field(default=None, pattern=_DIGEST)
    shadow_configuration_hash: str | None = Field(default=None, pattern=_DIGEST)
    agreement: bool
    projected_utility_delta: float
    comparison_notes: str = Field(min_length=1, max_length=2_000)
    evaluated_at: datetime

    @model_validator(mode="after")
    def _agreement_matches_the_hashes_it_summarises(self) -> Self:
        if self.executed_receipt_id == self.shadow_receipt_id:
            raise ValueError(
                "the executed and shadow receipts are the same record; a shadow decision "
                "compares two decisions"
            )
        same_configuration = (
            self.executed_configuration_hash is not None
            and self.executed_configuration_hash == self.shadow_configuration_hash
        )
        if self.agreement != same_configuration:
            raise ValueError(
                f"agreement is {self.agreement} but the executed configuration hash "
                f"{self.executed_configuration_hash!r} and the shadow one "
                f"{self.shadow_configuration_hash!r} say otherwise; two decisions that "
                "selected nothing have not agreed on anything"
            )
        return self


class ShadowRolloutResult(CanonicalContract):
    """ADR-060. What one arm of a branched rollout of a shadow decision actually produced.

    **Added by the freeze delta of 5 Sep 2026, not by M0.** M0 froze :class:`ShadowDecision`
    with ``agreement`` and ``projected_utility_delta`` and no observed outcome, which is
    exactly enough to say what a candidate router would have chosen and nothing at all
    about whether choosing it would have been better. §10.2 gates the shadow stage on
    evidence, and R7's answer to "where does the evidence come from" is to *branch the live
    run*: fork the workspace at the node, execute the shadow configuration in one sandbox
    and the executed configuration in a sibling sandbox under the same seed policy, and
    report the paired difference. Each fork writes one of these.

    **Why two rows and not one.** A single record holding both arms would make the pair
    atomic, which sounds like a feature until one fork fails: the surviving arm would have
    nowhere to be written, and a rollout that produced a real measurement would be
    discarded because its partner did not. Two rows joined by ``shadow_decision_id`` and
    distinguished by ``kind`` let the report count complete pairs and say how many were
    incomplete, which is the number OQ-409's power analysis actually needs.

    **Fields.** ``shadow_decision_id`` names the ``shd_`` record this scores — a plain
    constrained string, as every cross-record reference in this family is, because a
    foreign key here would let the shadow pipeline's write ordering block the executed
    path, which is the one thing shadow evaluation must never do (the same reasoning
    ``ShadowDecisionRow`` gives for its two receipt ids). ``fork_execution_id`` is the
    execution instance the fork ran as, so the trajectory is reachable.
    ``configuration_hash`` is the configuration that was *executed in this fork* and not
    the one the router recommended: on the ``CONTROL`` arm those differ, and recording the
    recommendation would misattribute the measurement. ``serving`` is the OQ-415 window the
    fork was served under. ``verification_result_id`` names the ``ivr_`` record behind
    ``observed.verified``. ``budget_consumed`` is what this fork spent against the per-policy
    daily budget, ``trial_index`` its position in that policy's sequence, and ``seed`` the
    seed policy both arms shared — without which "the same seed policy" is an unverifiable
    claim in a paragraph rather than a field in a document. ``completed_at`` is when the
    fork finished, which is not the header's ``created_at``: a rollout row is written when
    the fork is scored and a pair whose two arms were sealed at the same instant can still
    have run for very different lengths of time, which is precisely what
    ``observed.latency_ms`` is being compared against.

    The validator holds the one rule that keeps ``observed.verified`` from being a
    self-report: a fork that claims verification names the independent verification result
    that produced it. Everything else about the pair is checked by the M6.2 report, which
    is a service, not a schema.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.shadow-rollout-result"
    ID_KIND: ClassVar[str | None] = "shadow_rollout_result"

    contract_type: Literal["accretion.shadow-rollout-result"] = "accretion.shadow-rollout-result"
    shadow_decision_id: str = Field(min_length=1, max_length=64)
    kind: ShadowRolloutKind
    fork_execution_id: str = Field(min_length=1, max_length=64)
    configuration_hash: str = Field(pattern=_DIGEST)
    serving: ServingWindow
    verification_result_id: str | None = Field(default=None, min_length=1, max_length=64)
    observed: ObservedOutcome
    budget_consumed: float = Field(ge=0)
    trial_index: int = Field(ge=0)
    seed: int
    completed_at: datetime

    @model_validator(mode="after")
    def _a_verified_outcome_names_the_verification_that_produced_it(self) -> Self:
        if self.observed.verified and self.verification_result_id is None:
            raise ValueError(
                "observed.verified is true but no verification_result_id is named; §8.4 "
                "makes verification independent of the executor, so a rollout that scored "
                "itself as verified without pointing at the verification result is a "
                "self-report and cannot be evidence for a promotion"
            )
        return self


class RouterActivation(CanonicalContract):
    """ADR-061. One append-only entry in the ledger whose head is the active router.

    **Added by the freeze delta of 5 Sep 2026, not by M0.** §13.1 says "one active
    workspace router per workspace" and M0 implemented it as the partial unique index
    ``uq_router_versions_active_workspace`` over ``router_model_versions.status``. That
    rule is correct and its implementation does not compose with §10.3: this family has no
    ``update_`` method on any table, by design, so the first ``ACTIVE`` row can never be
    retired and a second one can never be inserted. A workspace could be activated exactly
    once, forever.

    The ledger is the fix, and it is a better statement of the requirement than the index
    was. "Active" stops being a mutable column and becomes *the head of a sequence*: the
    activation with the highest ``sequence`` for a ``(workspace_id, scope, family_key)``
    names the version now serving. Promotion appends; rollback appends; nothing is edited;
    the history of who activated what, when, and why is the table itself rather than a
    reconstruction from timestamps. M8.1 owns the migration that retires the two partial
    indexes (0019); this freeze adds the contract and its table and touches neither index,
    so a database between the two migrations is consistent under both rules at once.

    **Fields.** ``scope`` and ``family_key`` are the ledger's partition — ``scope`` is
    :class:`RouterScope`, the same enum :class:`RouterModelVersion` carries, and
    ``family_key`` is the router family within it (``algorithm_id`` for a workspace prior,
    and the project-and-algorithm pair for an adapter), so that two families promoting on
    the same day are two sequences rather than one contested one. ``sequence`` is
    contiguous from 1 and unique per partition — a database constraint, not a hope, and it
    is what makes "the head" a query rather than a scan. ``router_version_id`` is the
    ``rmv_`` version being activated, ``previous_version_id`` the one it displaces,
    ``rollback_target_version_id`` what a withdrawal would restore, and
    ``promotion_report_id`` the ``rpr_`` evaluation that authorised it — nullable because a
    rollback is authorised by an incident and not by a report. ``approved_by`` is required
    on **every** entry, rollbacks included (OQ-411): §10.3 makes activation a human act, and
    a rollback performed by nobody is the activation nobody can be asked about afterwards.

    ``PROJECT_SCOPED`` is ``False``, exactly as it is on :class:`RouterModelVersion` and for
    the same reason: a ``TEAM_WORKSPACE`` activation belongs to the workspace and to no
    project. The validator makes the nullability exact rather than merely permitted.

    The two ledger rules the validator holds are the ones a database constraint cannot
    state. A ``ROLLBACK`` names both what it restores and why — §10.3's reversibility is
    worth nothing if the ledger records that something was withdrawn but not what it was
    withdrawn to, and an unexplained withdrawal is the row an incident review most needs to
    read. And the first entry in a sequence displaces nothing, so a ``sequence`` of 1 that
    claims a predecessor is describing a history that does not exist.
    """

    CONTRACT_TYPE: ClassVar[str] = "accretion.router-activation"
    ID_KIND: ClassVar[str | None] = "router_activation"
    PROJECT_SCOPED: ClassVar[bool] = False

    contract_type: Literal["accretion.router-activation"] = "accretion.router-activation"
    scope: RouterScope
    family_key: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    kind: RouterActivationKind
    router_version_id: str = Field(min_length=1, max_length=64)
    previous_version_id: str | None = Field(default=None, min_length=1, max_length=64)
    rollback_target_version_id: str | None = Field(default=None, min_length=1, max_length=64)
    promotion_report_id: str | None = Field(default=None, min_length=1, max_length=64)
    approved_by: PrincipalRef
    cause: str | None = Field(default=None, min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def _the_ledger_entry_is_self_explaining(self) -> Self:
        if self.scope is RouterScope.PROJECT_ADAPTER and self.project_id is None:
            raise ValueError(
                "a PROJECT_ADAPTER activation must name the project whose adapter it "
                "activates"
            )
        if self.scope is RouterScope.TEAM_WORKSPACE and self.project_id is not None:
            raise ValueError(
                "a TEAM_WORKSPACE activation releases the workspace prior and belongs to "
                "no single project; naming one would make the release look project-specific"
            )
        if self.kind is RouterActivationKind.ROLLBACK:
            missing = sorted(
                name
                for name in ("cause", "rollback_target_version_id")
                if getattr(self, name) is None
            )
            if missing:
                raise ValueError(
                    f"a ROLLBACK activation leaves {missing!r} unset; §10.3 makes promotion "
                    "reversible, and a withdrawal that records neither what it restored nor "
                    "why it happened is not a reversal anyone can audit"
                )
        if self.sequence == 1 and self.previous_version_id is not None:
            raise ValueError(
                f"sequence 1 names previous_version_id "
                f"{self.previous_version_id!r}; the first entry in an activation ledger "
                "displaces nothing, so a predecessor here describes a history that never "
                "happened"
            )
        if self.router_version_id == self.previous_version_id:
            raise ValueError(
                "the activated version and the version it displaces are the same record; "
                "an activation that changes nothing is not an activation"
            )
        return self


CONTRACT_INVENTORY: tuple[type[CanonicalContract], ...] = (
    ObjectiveContract,
    ObjectiveContractRef,
    NodeContract,
    VerificationSpec,
    TaskFeatures,
    ProjectFeatures,
    RoutingContext,
    ExecutionConfiguration,
    ConfigurationCandidate,
    CompatibilityDecision,
    StructuredExplanation,
    RoutingDecisionReceipt,
    IndependentVerificationResult,
    ExperienceRecord,
    FailureEvent,
    RouterModelVersion,
    RouterTrainingSnapshot,
    RouterPromotionReport,
    ShadowDecision,
    ShadowRolloutResult,
    RouterActivation,
)
"""The twenty-one contracts of the v0.4 freeze, in the order the plan enumerates them.

This tuple is the single list every proof reads. The fixture tests parametrize over it
crossed with the four fixture kinds, the schema export writes one file per entry and its
``--check`` mode compares them, and the hash-sensitivity test walks every field of every
entry. A contract added to this module without being added here would therefore have no
fixtures, no committed schema and no hash coverage — and the completeness test below turns
that silent gap into a red test by comparing the tuple against every
:class:`~accretion.contracts.canonical.CanonicalContract` subclass the module defines.

Ordering is the plan's enumeration and not alphabetical, because it is also roughly the
order a decision flows through them: objective, node, spec, features, context, configuration,
candidate, compatibility, explanation, receipt, verification, experience, failure, and then
the four router-learning records.

The last two arrived with the freeze delta of 5 Sep 2026 rather than with M0, and are
appended rather than filed beside their relatives on purpose: the tuple is also the
migration's creation order, ``ShadowRolloutResult`` and ``RouterActivation`` are created by
0018 and not by 0017, and re-ordering the tuple would have moved fifteen tables that are
already in the field. They keep the flow reading, too — a shadow decision is scored by its
rollouts, and a scored router is what an activation releases.
"""
