"""Policy, risk and permission gates, outside the learned router (AC4-M1-005, SDD §9.1).

SDD §9.1 orders routing in stages and puts authority first: a configuration is refused for
policy, risk or permission reasons *before* anything scores it. AC4-M1-005 turns that order
into a MUST — "policy/risk/permission gates remain outside the learned router" — and this
module is where the MUST is made structural rather than remembered.

**What "outside" means here, concretely.** This module imports nothing from a selector, a
candidate builder, a ranker, a gradient-boosted model, a project adapter or the experience
layer. Not "does not call"; does not *import*. A ranker cannot influence a gate it cannot
reach, and :func:`gate_then_evaluate` below is the only place a gate result and a
compatibility result meet — gates first, always, and the joint evaluation is not consulted
at all when a gate refuses. ``tests/test_v04_m1_gates.py`` walks this file's AST and fails on
the mere presence of a forbidden module name, so the property survives a refactor that
would otherwise quietly re-introduce the dependency.

It also means this module does not import
:class:`~accretion.routing.compatibility.CompatibilityEngine`. The joint evaluator arrives as
the :class:`JointEvaluator` structural protocol, which is both stricter — there is no import
edge to the engine at all — and what makes the call-order spy in the tests an ordinary
object rather than a patch.

**Why the gates return decisions and not booleans.** A refusal that is not a
:class:`~accretion.contracts.routing.CompatibilityDecision` is a refusal with no reason code,
no rule version, no snapshot pin and nothing to persist, which is exactly the state SDD §7.7
and AC4-M1-008 exist to prevent. So a gate seals the same record the compatibility engine
seals, from the same derived id over the same digest inputs, and a reader cannot tell from
the record's *shape* whether authority or availability refused — only from its ``rule_id``,
which is the field that is supposed to say so.

**What is deliberately not decided here.** Routing never pre-approves. When
``CapabilityPolicyEngine`` answers ``REQUIRE_APPROVAL``, the gate returns INCOMPATIBLE with
:attr:`~accretion.routing.reasons.ReasonCode.APPROVAL_REQUIRED` rather than passing an
approval of its own: a v0.1 approval is *content-bound* to a request, and no request exists
while a configuration is still being chosen. Manufacturing one would let the router grant
itself the authority SDD §5.3 reserves for a human.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from accretion.contracts import (
    RISK_RANK,
    AuthorizationOutcome,
    Capability,
    CapabilityPolicy,
    CapabilityRequest,
    EvidenceClass,
    PrincipalRef,
    PrincipalStatus,
    Task,
)
from accretion.contracts.refs import EvidenceRef
from accretion.contracts.routing import (
    CompatibilityDecision,
    CompatibilityStatus,
    ExecutionConfiguration,
    NodeContract,
    SubjectType,
    risk_level_for,
)
from accretion.governance import CapabilityPolicyEngine
from accretion.ids import derived_id
from accretion.routing.reasons import RULE_VERSION, ReasonCode
from accretion.routing.snapshot import RoutingSnapshot, policy_snapshot_id

# `policy_snapshot_id` is imported for re-export, not for use here: SDD §8.2 makes it one of
# the four ids a routing request derives from, and a caller that holds a gate and a policy
# but no snapshot — which is every caller of `PolicyGate` before the snapshot is taken —
# should not have to know that the spelling lives beside the three digests it belongs with.
# There is exactly one implementation, in `accretion.routing.snapshot`.

RULE_CAPABILITY_AUTHORIZED = "gate.capability.authorized"
"""The policy gate: may this task bind this capability at all (SDD §5.3, §9.1 stage 1)."""

RULE_RISK_CLASS = "gate.risk.class"
"""The risk gate: is the task's risk level within the node's ``allowed_risk_class``."""

RULE_PERMISSION_WORKSPACE = "gate.permissions.workspace"
"""The permission gate: is this principal entitled to route in this workspace."""

_GATE_RULE_IDS: frozenset[str] = frozenset(
    {RULE_CAPABILITY_AUTHORIZED, RULE_RISK_CLASS, RULE_PERMISSION_WORKSPACE}
)
"""Every rule id a gate may stamp, so a caller can separate authority from availability.

Exported through :func:`is_gate_decision` rather than as a bare set, because the question a
caller actually asks is "did authority refuse this, or did the world" — and a caller that
answered it by string-matching ``"gate."`` would be reading a prefix rather than a fact.
"""


def is_gate_decision(decision: CompatibilityDecision) -> bool:
    """Whether ``decision`` came from a gate rather than from a compatibility rule."""

    return decision.rule_id in _GATE_RULE_IDS


def _decide(
    *,
    subject_type: SubjectType,
    subject_ref: str,
    status: CompatibilityStatus,
    rule_id: str,
    reason: ReasonCode,
    created_by: PrincipalRef,
    snapshot: RoutingSnapshot,
    workspace_id: str,
    project_id: str | None,
    clock: Callable[[], datetime] | None,
) -> CompatibilityDecision:
    """Seal one gate decision, with the same derived identity a rule decision carries.

    Deliberately a second copy of ``CompatibilityEngine._decide``'s digest input rather than
    a call into it. Importing the engine here would create the edge AC4-M1-005 forbids, and
    reaching into another class's private method to avoid the import would be the same edge
    with worse manners. What must not diverge is the *digest input*, and that is pinned by a
    test which derives a gate decision's id by hand from the documented parts: rule, rule
    version, subject, all four snapshot ids, verdict, reason.

    Because ``rule_id`` is inside the digest and every gate rule id starts ``gate.``, a gate
    decision can never collide with a compatibility decision about the same subject.
    """

    evaluated_at = clock() if clock is not None else snapshot.taken_at
    contract_id = derived_id(
        "compatibility_decision",
        rule_id,
        RULE_VERSION,
        subject_type.value,
        subject_ref,
        snapshot.capability_registry_snapshot_id,
        snapshot.available_runtime_snapshot_id,
        snapshot.connection_availability_snapshot_id,
        snapshot.policy_snapshot_id,
        status.value,
        reason.value,
    )
    # `type: ignore[call-arg]` covers the eight registry §3 header fields only, for the
    # reason `CompatibilityEngine._decide` gives at length: the pydantic mypy plugin does not
    # synthesize `CanonicalContract`'s inherited fields into a subclass `__init__`.
    return CompatibilityDecision(  # type: ignore[call-arg]
        contract_id=contract_id,
        created_at=evaluated_at,
        created_by=created_by,
        workspace_id=workspace_id,
        project_id=project_id,
        labels={"policy_snapshot_id": snapshot.policy_snapshot_id},
        subject_type=subject_type,
        subject_ref=subject_ref,
        status=status,
        rule_id=rule_id,
        rule_version=RULE_VERSION,
        reason_code=reason.value,
        evidence_refs=[
            EvidenceRef(
                evidence_id="capability-registry-snapshot",
                evidence_class=EvidenceClass.DIGITAL,
                content_digest=snapshot.capability_registry_snapshot_id,
            ),
            EvidenceRef(
                evidence_id="available-runtime-snapshot",
                evidence_class=EvidenceClass.DIGITAL,
                content_digest=snapshot.available_runtime_snapshot_id,
            ),
            EvidenceRef(
                evidence_id="connection-availability-snapshot",
                evidence_class=EvidenceClass.DIGITAL,
                content_digest=snapshot.connection_availability_snapshot_id,
            ),
        ],
        evaluated_at=evaluated_at,
    )


class PolicyGate:
    """The three authority questions, asked before anything is scored.

    ``engine`` is the v0.1 :class:`~accretion.governance.CapabilityPolicyEngine` and is the
    *only* thing that decides a capability verdict here. This class does not re-implement
    authorization; it asks the existing engine and translates its answer into the v0.4
    vocabulary. Registry §21 forbids the second implementation, and a second one would be
    worse than duplicated code: two authorities that disagreed would let a capability the
    gateway refuses at execution time be routed to anyway, and the run would fail at the
    last possible moment instead of the first.

    ``policy`` is the :class:`~accretion.contracts.CapabilityPolicy` in force. It is held on
    the gate rather than passed per call because every decision this gate seals pins
    ``policy_snapshot_id`` into its identity, and a gate whose policy could change between
    two calls would produce two decisions that claim to have been made under one authority.

    ``created_by`` has no default for the reason ``CompatibilityEngine`` gives: every
    decision is a :class:`~accretion.contracts.canonical.CanonicalContract` and carries a
    principal, and inventing a plausible ``usr_`` id would put an identity in the audit trail
    that no principal row backs.
    """

    def __init__(
        self,
        engine: CapabilityPolicyEngine,
        policy: CapabilityPolicy,
        *,
        created_by: PrincipalRef,
    ) -> None:
        self.engine = engine
        self.policy = policy
        self.created_by = created_by

    # ---------------------------------------------------------------------------------
    # The capability gate
    # ---------------------------------------------------------------------------------

    def gate_capability(
        self,
        task: Task,
        capability: Capability,
        *,
        snapshot: RoutingSnapshot,
        workspace_id: str,
        project_id: str | None,
        request: CapabilityRequest | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> CompatibilityDecision:
        """May ``task`` bind ``capability``? The policy engine decides; this labels it.

        ``request`` is optional and is normally absent, because routing runs *before* a
        capability request exists: stage 1 asks whether a tuple containing this capability
        may be built at all, and the arguments that will eventually be sent are not chosen
        yet. When it is absent a probe request is synthesized with a derived idempotency
        key, and that key is the only thing the synthesis changes about the verdict.

        The distinction matters and is narrow. ``CapabilityPolicyEngine.authorize`` refuses a
        side-effecting capability on two different grounds: because the *capability* declares
        ``IdempotencyMode.NONE``, which is a property of the registry row and is therefore
        answerable now; and because the *request* carries no ``idempotency_key``, which is a
        property of a call that has not been made. Only the second is suppressed by the
        probe. A capability that may never be invoked idempotently is still refused here, and
        the gateway still enforces the key at execution time — this gate cannot and does not
        exempt anything from that.

        ``approval`` is deliberately not a parameter. See the module docstring: routing does
        not pre-approve, so ``authorize`` is always called with ``approval=None`` and
        ``REQUIRE_APPROVAL`` becomes ``APPROVAL_REQUIRED``.
        """

        probe = request if request is not None else self._probe_request(task, capability)
        authorization = self.engine.authorize(
            task=task,
            capability=capability,
            request=probe,
            policy=self.policy,
            approval=None,
        )
        if authorization.outcome is AuthorizationOutcome.ALLOW:
            status, reason = CompatibilityStatus.COMPATIBLE, ReasonCode.COMPATIBLE
        elif authorization.outcome is AuthorizationOutcome.REQUIRE_APPROVAL:
            status = CompatibilityStatus.INCOMPATIBLE
            reason = ReasonCode.APPROVAL_REQUIRED
        else:
            status = CompatibilityStatus.INCOMPATIBLE
            reason = self._denial_reason(task, capability)
        return _decide(
            subject_type=SubjectType.TOOL,
            subject_ref=capability.capability_id,
            status=status,
            rule_id=RULE_CAPABILITY_AUTHORIZED,
            reason=reason,
            created_by=self.created_by,
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=clock,
        )

    def _probe_request(self, task: Task, capability: Capability) -> CapabilityRequest:
        """A request that exists only to be authorized, never to be executed or stored.

        Every field is derived from the task and the capability, so two gate evaluations of
        one pair build the same probe and the gate stays replayable. It is not persisted and
        carries no arguments: a probe with arguments would be a request nobody made, and its
        digest would collide with the real one the gateway later builds.
        """

        probe_id = derived_id(
            "capability_request",
            "routing-gate",
            task.envelope.task_id,
            capability.capability_id,
            capability.version,
        )
        return CapabilityRequest(
            request_id=probe_id,
            run_id=task.envelope.task_id,
            node_id="routing-gate",
            capability_id=capability.capability_id,
            capability_version=capability.version,
            arguments={},
            declared_reason=(
                "routing admissibility probe; no capability is invoked by this request"
            ),
            idempotency_key=probe_id,
            created_at=task.created_at,
        )

    def _denial_reason(self, task: Task, capability: Capability) -> ReasonCode:
        """Which code explains a ``DENY``, decided structurally rather than from prose.

        The *verdict* is the policy engine's and is never recomputed here; this reads the
        same inputs the engine read, in the same order, only to choose the word an operator
        sees. It is the pattern ``CompatibilityEngine.map_resolution`` already uses on the
        resolver's outcomes, and it exists for the same reason: parsing the engine's English
        ``reason`` string would make a sentence load-bearing.

        The order mirrors ``CapabilityPolicyEngine.authorize`` because a capability can be
        refused on several grounds at once and the first one the engine would have hit is the
        one that explains its answer. A code that came second would send an operator to fix
        something that was not what stopped them.
        """

        if not capability.enabled:
            return ReasonCode.CAPABILITY_DISABLED
        if capability.capability_id in self.policy.explicitly_denied:
            return ReasonCode.CAPABILITY_DENIED
        if capability.capability_id in task.envelope.denied_capabilities:
            return ReasonCode.CAPABILITY_DENIED
        if capability.capability_id not in task.envelope.allowed_capabilities:
            return ReasonCode.CAPABILITY_NOT_ALLOWED
        if set(capability.required_permissions) - self.engine.granted_permissions:
            # The operator running this deployment was never granted a permission the
            # capability demands. POLICY_INCOMPATIBLE rather than CAPABILITY_DENIED: nothing
            # denied it, the grant is simply absent, and the two need different fixes.
            return ReasonCode.POLICY_INCOMPATIBLE
        if capability.side_effects:
            return ReasonCode.PROTECTED_SIDE_EFFECT_STATE
        # Unreachable through `authorize`'s current branches: every DENY it can return is
        # covered above. Kept because the alternative to a total function here is a `None`
        # that some caller eventually reads as "allowed".
        return ReasonCode.POLICY_INCOMPATIBLE

    # ---------------------------------------------------------------------------------
    # The risk gate
    # ---------------------------------------------------------------------------------

    def gate_risk(
        self,
        node_contract: NodeContract,
        task: Task,
        *,
        snapshot: RoutingSnapshot,
        workspace_id: str,
        project_id: str | None,
        clock: Callable[[], datetime] | None = None,
    ) -> CompatibilityDecision:
        """Is the task's risk level within what this node's contract admits?

        The comparison is between two different vocabularies and goes through the one total
        mapping that joins them: :func:`~accretion.contracts.routing.risk_level_for` turns
        the node's registry §5.3 ``RiskClass`` into the v0.1 ``RiskLevel`` ladder the task
        envelope uses. Comparing the enums directly would be the ordering bug that mapping
        exists to prevent — ``StrEnum`` members compare alphabetically, which makes
        ``CRITICAL < HIGH < LOW`` true — so the ranks come from ``RISK_RANK``.

        ``RiskClass.PROHIBITED`` cannot arrive: ``NodeContract`` refuses to be built with it,
        because a prohibition is expressed by not creating the node. That is why this method
        does not catch the ``ValueError`` ``risk_level_for`` raises for it — there is no
        reachable path, and swallowing the exception would convert a broken contract into a
        quiet refusal.
        """

        allowed = risk_level_for(node_contract.allowed_risk_class)
        requested = task.envelope.risk_level
        within = RISK_RANK[requested] <= RISK_RANK[allowed]
        return _decide(
            subject_type=SubjectType.CONFIGURATION,
            subject_ref=node_contract.contract_id,
            status=(
                CompatibilityStatus.COMPATIBLE if within else CompatibilityStatus.INCOMPATIBLE
            ),
            rule_id=RULE_RISK_CLASS,
            # POLICY_INCOMPATIBLE is the catalogue's authority refusal, and the risk gate is
            # an authority gate. It is told apart from the permission gate's refusal by
            # `rule_id`, which is the field that names which rule spoke; SDD §5.1 fixes
            # `SubjectType` at seven members and none of them is "authority", so minting one
            # for a nicer-looking record would be a registry §3.2 change to a frozen enum.
            reason=ReasonCode.COMPATIBLE if within else ReasonCode.POLICY_INCOMPATIBLE,
            created_by=self.created_by,
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=clock,
        )

    # ---------------------------------------------------------------------------------
    # The permission gate
    # ---------------------------------------------------------------------------------

    def gate_permissions(
        self,
        principal: PrincipalRef,
        workspace: str,
        *,
        snapshot: RoutingSnapshot,
        workspace_id: str,
        project_id: str | None,
        clock: Callable[[], datetime] | None = None,
    ) -> CompatibilityDecision:
        """May ``principal`` route in ``workspace_id``, given the workspace they hold?

        ``workspace`` is the workspace the principal is actually entitled to — what
        :func:`accretion.routing.identity.workspace_for_run` resolved from their memberships
        — and ``workspace_id`` is the one this routing decision is being filed under. They
        are two parameters and not one on purpose: passing only the entitlement would make
        the gate unable to detect the case it exists for, which is a decision being recorded
        against a workspace its principal was never a member of.

        A ``DISABLED`` principal is refused for the same reason AC3-ID-05 refuses one at
        capability invocation: authority does not end at the HTTP boundary, and a run whose
        principal was disabled mid-flight must not keep routing on the strength of having
        started.
        """

        if principal.status is not PrincipalStatus.ACTIVE:
            status, reason = CompatibilityStatus.INCOMPATIBLE, ReasonCode.POLICY_INCOMPATIBLE
        elif workspace != workspace_id:
            status, reason = CompatibilityStatus.INCOMPATIBLE, ReasonCode.POLICY_INCOMPATIBLE
        else:
            status, reason = CompatibilityStatus.COMPATIBLE, ReasonCode.COMPATIBLE
        return _decide(
            subject_type=SubjectType.CONFIGURATION,
            subject_ref=principal.principal_id,
            status=status,
            rule_id=RULE_PERMISSION_WORKSPACE,
            reason=reason,
            created_by=self.created_by,
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=clock,
        )


class JointEvaluator(Protocol):
    """What :func:`gate_then_evaluate` needs from a compatibility engine, and no more.

    A structural protocol rather than
    :class:`~accretion.routing.compatibility.CompatibilityEngine` itself, so that this module
    has no import edge to the rules at all. Two things follow. The AST assertion in
    ``tests/test_v04_m1_gates.py`` becomes a statement about *this file* rather than about a
    call graph someone has to trace; and the call-order spy the same test uses is an ordinary
    hand-written object with a call log, not a patch over a real class.
    """

    def evaluate_joint(
        self,
        *,
        configuration: ExecutionConfiguration,
        node_contract: NodeContract,
        snapshot: RoutingSnapshot,
        workspace_id: str,
        project_id: str | None,
        clock: Callable[[], datetime] | None = None,
    ) -> list[CompatibilityDecision]: ...


@dataclass(frozen=True, slots=True)
class GatedEvaluation:
    """What one pass of :func:`gate_then_evaluate` produced, gates and rules kept apart.

    ``compatibility_decisions`` is empty exactly when a gate refused. That is not a missing
    result: it is the record that the joint evaluation was never consulted, which is the
    order AC4-M1-005 requires. A caller that wants "everything that was decided" reads
    :meth:`decisions`; a caller that wants "may this run" reads :meth:`eligible`.
    """

    gate_decisions: tuple[CompatibilityDecision, ...]
    compatibility_decisions: tuple[CompatibilityDecision, ...]
    admitted_capability_ids: tuple[str, ...]

    def admitted(self) -> bool:
        """Whether every gate admitted, which is the condition for evaluating at all."""

        return all(
            decision.status is CompatibilityStatus.COMPATIBLE for decision in self.gate_decisions
        )

    def decisions(self) -> tuple[CompatibilityDecision, ...]:
        """Gate decisions first, then compatibility decisions, in the order produced."""

        return self.gate_decisions + self.compatibility_decisions

    def eligible(self) -> bool:
        """Whether every decision admits its subject.

        ``all(... is COMPATIBLE)`` and never ``not any(... is INCOMPATIBLE)``, because the
        second spelling admits ``UNKNOWN`` and SDD §7.7 makes that a MUST-not. An evaluation
        that a gate stopped is never eligible: ``admitted()`` is false, and the empty
        ``compatibility_decisions`` cannot make it true.
        """

        return self.admitted() and all(
            decision.status is CompatibilityStatus.COMPATIBLE
            for decision in self.compatibility_decisions
        )


def gate_then_evaluate(
    *,
    gate: PolicyGate,
    evaluator: JointEvaluator,
    task: Task,
    principal: PrincipalRef,
    entitled_workspace_id: str,
    capabilities: Sequence[Capability],
    node_contract: NodeContract,
    configuration: ExecutionConfiguration,
    snapshot: RoutingSnapshot,
    workspace_id: str,
    project_id: str | None,
    clock: Callable[[], datetime] | None = None,
) -> GatedEvaluation:
    """Run every gate, then — only if all of them admit — the joint compatibility rules.

    This is the whole of AC4-M1-005 expressed as control flow, and it is deliberately the
    smallest thing that can express it. Three properties, in the order they matter:

    * **Gates first.** Every gate decision exists before ``evaluate_joint`` is called. Not
      "the first refusal short-circuits": all three gates run and all their decisions are
      returned, because an operator fixing a refusal needs every reason and not the earliest
      one. The permission gate runs first, then risk, then one capability gate per
      capability, so the record reads outward from authority to detail.
    * **A refusal stops the evaluation.** When any gate refuses, ``evaluate_joint`` is not
      consulted at all. Scoring a tuple that authority has already refused would produce a
      prediction about something that may not run, and §9.1's stage order exists precisely so
      that never happens.
    * **Nothing learned can reach a gate.** ``evaluator`` arrives as a
      :class:`JointEvaluator`; the gates are constructed from the policy engine and a policy.
      There is no path by which the second could inform the first.

    ``capabilities`` are the registry rows for the capabilities the tuple would bind; they
    are passed in rather than read from ``snapshot`` because the gate asks about *authority*
    over a capability, which is a fact about the task and the policy, while the snapshot's
    resolutions are facts about availability. Conflating them is how an unavailable
    capability comes to look unauthorized.
    """

    gate_decisions: list[CompatibilityDecision] = [
        gate.gate_permissions(
            principal,
            entitled_workspace_id,
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=clock,
        ),
        gate.gate_risk(
            node_contract,
            task,
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=clock,
        ),
    ]
    admitted: list[str] = []
    for capability in capabilities:
        decision = gate.gate_capability(
            task,
            capability,
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=clock,
        )
        gate_decisions.append(decision)
        if decision.status is CompatibilityStatus.COMPATIBLE:
            admitted.append(capability.capability_id)

    stopped = any(
        decision.status is not CompatibilityStatus.COMPATIBLE for decision in gate_decisions
    )
    compatibility_decisions: tuple[CompatibilityDecision, ...] = ()
    if not stopped:
        compatibility_decisions = tuple(
            evaluator.evaluate_joint(
                configuration=configuration,
                node_contract=node_contract,
                snapshot=snapshot,
                workspace_id=workspace_id,
                project_id=project_id,
                clock=clock,
            )
        )
    return GatedEvaluation(
        gate_decisions=tuple(gate_decisions),
        compatibility_decisions=compatibility_decisions,
        admitted_capability_ids=tuple(admitted),
    )


__all__ = [
    "RULE_CAPABILITY_AUTHORIZED",
    "RULE_PERMISSION_WORKSPACE",
    "RULE_RISK_CLASS",
    "GatedEvaluation",
    "JointEvaluator",
    "PolicyGate",
    "gate_then_evaluate",
    "is_gate_decision",
    "policy_snapshot_id",
]
