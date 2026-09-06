"""The deterministic compatibility rules (SDD §7.7, §9.1 stage 7).

One rule set, keyed by :class:`~accretion.contracts.routing.SubjectType`, evaluated against a
:class:`~accretion.routing.snapshot.RoutingSnapshot` and nothing else. Four properties hold,
and each of them is load-bearing rather than stylistic.

**The engine is pure.** It never touches the store, never calls a runtime and never reads a
clock it was not given. That is what makes a decision replayable: the same snapshot and the
same subject produce the same decision, byte for byte, including its ``contract_id`` and its
``content_hash``. Persistence is the caller's job — ``store.put_compatibility_decision`` —
and the separation means a test can prove replay without a database.

**Identity is derived, not minted.** A decision's ``contract_id`` comes from
:func:`~accretion.ids.derived_id` over the rule, the rule version, the subject, the four
snapshot ids, the status and the reason. Two evaluations that saw the same world and reached
the same verdict *are* the same decision and share an id; a change to any of those inputs is
a different decision with a different id. ``created_at`` is set from the same clock as
``evaluated_at`` rather than left to default, because the header digest covers it and a
wall-clock default would make replay unprovable while looking like it worked.

**UNKNOWN is never compatible.** SDD §7.7 states it as a MUST and
:meth:`~accretion.contracts.routing.CompatibilityDecision.is_compatible` implements it as a
single identity comparison. This module never asks whether a status "is not INCOMPATIBLE" —
that spelling admits UNKNOWN, and admitting UNKNOWN is how an unchecked assumption reaches
dispatch wearing a receipt. Eligibility for a required constraint is
``status is CompatibilityStatus.COMPATIBLE`` and nothing else.

**Each check has exactly one implementation.** The capability join is not re-derived here: it
is :class:`~accretion.resolver.CapabilityResolver`'s, read off the snapshot and *mapped* to a
reason code by :meth:`CompatibilityEngine.map_resolution`. Constraint comparison is one
function, :func:`_holds`, used by both the environment rule and the runtime-version rule. The
joint ``CONFIGURATION`` decision does not re-run the per-layer checks; it folds their verdicts
and adds only the questions that belong to no single layer.

**How the layers and the joint decision divide the work.** Registry §7.3 orders the hierarchy
environment → runtime → model → tools → skills → verifier, and
:meth:`CompatibilityEngine.evaluate_joint` emits one decision per layer in that order,
followed by exactly one ``CONFIGURATION`` decision. A per-layer decision answers "is this
piece admissible at all"; the joint decision answers "is this *tuple* an answer to *this
node*", which is a question no layer can see: whether the verifier binding enforces the spec
the node pinned, and whether the bindings cover every capability the node requires. Two
individually compatible layers can be jointly incompatible, which is exactly why SDD §9.1
gives stage 7 its own name.

**What a ``CapabilityRequirement`` asks for, and which half M1 can answer.** A requirement
carries a :class:`~accretion.contracts.refs.CapabilityRef`, a ``version_range`` and a
``required_scope``. The range is checked here, against the version the snapshot observed in
the registry, because a tuple that binds a capability at a version the node excluded is not a
weaker answer to the node's question — it is an answer the node already refused, and
admitting it silently is precisely the unchecked assumption this module exists to stop.

``required_scope`` is **not** checked in M1, and the omission is a decision rather than an
oversight. A scope is authority — "this capability may only be bound where that grant is
held" — and the two things that could settle it are both outside this engine. The connector's
declared scopes against a connection's granted scopes are already compared once, by
:class:`~accretion.resolver.CapabilityResolver`, and reach a decision through
``SCOPE_INSUFFICIENT`` in :meth:`CompatibilityEngine.map_resolution`; re-deriving that join
here would be the second implementation registry §21 forbids. The node's own
``required_scope`` is a different question, and answering it needs scope evidence
:class:`~accretion.routing.snapshot.RoutingSnapshot` deliberately does not carry — the
connection projection is ``(connection_id, connector_id, status)`` so that nothing a
connector may have filled with a credential enters a digest an operator will later
reproduce. Widening that projection is a snapshot-identity change, and the check itself is an
authority check, which is M1.2's ``PolicyGate.gate_permissions``. Until it lands, a complete
tuple that holds no such grant is admitted by stage 7;
``test_a_required_scope_is_not_yet_refused_by_the_joint_rule`` pins today's behaviour so the
day the gate arrives that test must change rather than stay quietly green.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from accretion.contracts import (
    CapabilityBackend,
    CapabilityResolutionOutcome,
    ConnectionStatus,
    EvidenceClass,
    McpServerState,
    PrincipalRef,
    ResolvedCapability,
    RuntimeStatus,
)
from accretion.contracts.refs import EvidenceRef, RuntimeRef, SkillRef
from accretion.contracts.routing import (
    CompatibilityDecision,
    CompatibilityStatus,
    EnvironmentBinding,
    EnvironmentConstraint,
    ExecutionConfiguration,
    MetricOperator,
    ModelBinding,
    NodeContract,
    SubjectType,
    ToolBinding,
    VerifierBinding,
)
from accretion.ids import derived_id
from accretion.routing.reasons import RULE_VERSION, ReasonCode
from accretion.routing.snapshot import RoutingSnapshot

RULE_ENVIRONMENT = "compat.environment.constraints"
RULE_RUNTIME = "compat.runtime.ready"
RULE_MODEL = "compat.model.provider"
RULE_CAPABILITY = "compat.capability.enabled"
RULE_SKILL = "compat.skill.available"
RULE_VERIFIER = "compat.verifier.registered"
RULE_CONFIGURATION = "compat.configuration.joint"

RUNTIME_VERSION_ATTRIBUTE = "runtime.runtime_version"
"""The one environment-constraint attribute the RUNTIME layer owns instead of ENVIRONMENT.

A :class:`~accretion.contracts.refs.RuntimeRef` pins one exact ``adapter_version`` and cannot
express a range, so a node that needs "any adapter at or above 2.0" has to say so through its
environment constraints. Those constraints are still evaluated exactly once, by exactly one
rule: the runtime rule claims every constraint on this attribute and the environment rule
skips them, because the value being compared is the runtime's *observed* version from the
snapshot rather than anything the configuration declares.
"""

_USABLE_CONNECTION_STATUSES = frozenset({ConnectionStatus.ACTIVE, ConnectionStatus.DEGRADED})
"""The statuses ``CapabilityResolver`` treats as usable, mirrored so the refinement below
distinguishes "the connection needs re-authorising" from "the connection is fine but its
scopes are short"."""


class _RuleBody(Protocol):
    """The shape every per-layer rule body shares, so the dispatch table can be typed.

    A ``Protocol`` rather than a ``Callable[...]`` alias because the bodies take keyword-only
    arguments, which a callable alias cannot express — and dropping to ``Any`` to work around
    that would let a rule with the wrong signature into the table silently.
    """

    def __call__(
        self,
        requirements: LayerRequirement,
        *,
        subject_type: SubjectType,
        subject_ref: str,
        snapshot: RoutingSnapshot,
        workspace_id: str,
        project_id: str | None,
        clock: Callable[[], datetime] | None,
    ) -> CompatibilityDecision: ...


@dataclass(frozen=True, slots=True)
class LayerRequirement:
    """What one layer's rule is allowed to read, and what the joint rule assembles.

    A single frozen carrier rather than seven signatures, because
    :meth:`CompatibilityEngine.evaluate` is one entry point per SDD §7.7 and a caller
    should not have to know which of seven private methods a ``SubjectType`` maps to.
    Every field defaults to absent and the rule for a subject raises if the field it
    needs is missing, so a caller that passed a ``SKILL`` subject with a runtime in hand
    gets an error rather than a decision about nothing.
    """

    environment: EnvironmentBinding | None = None
    runtime: RuntimeRef | None = None
    model: ModelBinding | None = None
    tool: ToolBinding | None = None
    skill: SkillRef | None = None
    verifier: VerifierBinding | None = None
    resolved: ResolvedCapability | None = None
    environment_constraints: tuple[EnvironmentConstraint, ...] = ()
    verification_spec_hash: str | None = None
    layer_decisions: tuple[CompatibilityDecision, ...] = field(default=())
    missing_capability_ids: tuple[str, ...] = ()
    unknown_capability_ids: tuple[str, ...] = ()
    out_of_range_capability_ids: tuple[str, ...] = ()
    undecidable_range_capability_ids: tuple[str, ...] = ()


def _numeric(value: str) -> float | None:
    """``value`` as a float, or ``None`` when it is not a number.

    Used to decide whether an ordered comparison is arithmetic or lexicographic. Returning
    ``None`` rather than raising is what lets ``GTE`` work for both ``"2"`` and ``"2.1.0"``
    without the rule needing a version-parsing library it would then have to freeze.
    """

    try:
        return float(value)
    except ValueError:
        return None


def _holds(constraint: EnvironmentConstraint, observed: str | None) -> bool | None:
    """Whether ``constraint`` holds for ``observed``; ``None`` when the rule cannot say.

    ``None`` is the third answer and the important one. An attribute this engine cannot
    resolve, and a ``CUSTOM`` operator whose evaluator lives in a contract M1 does not read,
    are both cases where the honest verdict is "unknown" — and SDD §7.7 then forbids treating
    that as compatible. Returning ``False`` instead would refuse working configurations while
    claiming to have checked them; returning ``True`` would be the unchecked assumption the
    whole module exists to prevent.

    Ordered comparisons are arithmetic when both sides parse as numbers and lexicographic
    otherwise, so a numeric threshold behaves numerically and a version string still orders.
    """

    if observed is None:
        return None
    if constraint.operator is MetricOperator.EQ:
        return observed == constraint.value
    if constraint.operator is MetricOperator.CUSTOM:
        return None
    left, right = _numeric(observed), _numeric(constraint.value)
    if left is None or right is None:
        left_value: str | float = observed
        right_value: str | float = constraint.value
    else:
        left_value, right_value = left, right
    if constraint.operator is MetricOperator.GTE:
        return left_value >= right_value  # type: ignore[operator]
    return left_value <= right_value  # type: ignore[operator]


_RANGE_OPERATORS: tuple[tuple[str, str], ...] = (
    (">=", "GTE"),
    ("<=", "LTE"),
    ("==", "EQ"),
    ("!=", "NE"),
    (">", "GT"),
    ("<", "LT"),
    ("=", "EQ"),
)
"""The closed operator vocabulary a ``version_range`` may use, longest prefix first.

A closed table and not a parser for one of the published range grammars: a caret or tilde
range means different things in different ecosystems, and a rule that guessed which one a
node meant would refuse or admit tuples on a convention nobody wrote down. Anything outside
this table is undecidable here, and undecidable is ``UNKNOWN`` rather than compatible.
"""


_WILDCARD_COMPONENTS = frozenset({"x", "X", "*"})
"""Version components that stand for "anything here", which this module does not expand."""


def _is_version_literal(literal: str) -> bool:
    """Whether ``literal`` names one exact version rather than a family of them.

    The guard that keeps an unrecognised range from degrading into an exact-match *refusal*.
    ``"^1.0.0"`` and ``"1.x"`` are ranges in ecosystems this repository has not chosen
    between, and comparing either as a string would refuse a working configuration while
    claiming to have checked it — the failure mode :func:`_holds` exists to avoid. So a
    literal must start with a digit and contain no wildcard component; anything else is
    undecidable and the caller reports ``UNKNOWN``.
    """

    if not literal or not literal[0].isdigit():
        return False
    return all(
        component and component not in _WILDCARD_COMPONENTS and "*" not in component
        for component in literal.split(".")
    )


def _version_key(value: str) -> tuple[int, ...] | None:
    """``value`` as an ordered tuple of integers, or ``None`` when it does not order.

    Dotted numeric components only. ``"1.10.0"`` must sort above ``"1.9.0"``, which string
    comparison gets wrong, and a pre-release suffix such as ``"1.0.0-rc1"`` has no ordering
    this module is entitled to invent — so it returns ``None`` and the caller reports
    ``UNKNOWN`` instead of guessing in either direction.
    """

    parts = value.split(".")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _version_clause_holds(clause: str, version: str) -> bool | None:
    """Whether one ``<operator><version>`` clause holds for ``version``; ``None`` if unknown.

    Equality is settled by string comparison, so a version this module cannot *order* — a
    date, a pre-release, a git describe — can still be pinned exactly. Only the ordered
    operators need :func:`_version_key`, and only they can answer ``None``.
    """

    clause = clause.strip()
    if not clause:
        return None
    operator = "EQ"
    literal = clause
    for prefix, name in _RANGE_OPERATORS:
        if clause.startswith(prefix):
            operator, literal = name, clause[len(prefix) :].strip()
            break
    if not _is_version_literal(literal):
        return None
    if operator == "EQ":
        return version == literal
    if operator == "NE":
        return version != literal
    left, right = _version_key(version), _version_key(literal)
    if left is None or right is None:
        return None
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    if operator == "GTE":
        return left >= right
    if operator == "LTE":
        return left <= right
    if operator == "GT":
        return left > right
    return left < right


def _version_range_holds(version_range: str, version: str) -> bool | None:
    """Whether ``version`` satisfies ``version_range``; ``None`` when the rule cannot say.

    A range is a comma-separated conjunction, which is the one composition every ecosystem
    spells the same way. A single undecidable clause makes the whole range undecidable only
    if no other clause already refused it: a definite refusal is still a refusal even when a
    second clause could not be read, and reporting ``UNKNOWN`` there would lose information
    the rule had.
    """

    undecidable = False
    for clause in version_range.split(","):
        verdict = _version_clause_holds(clause, version)
        if verdict is False:
            return False
        if verdict is None:
            undecidable = True
    return None if undecidable else True


def _configuration_attribute(
    attribute: str,
    *,
    environment: EnvironmentBinding | None,
    runtime: RuntimeRef | None,
    model: ModelBinding | None,
) -> str | None:
    """The value of ``attribute`` in this configuration, or ``None`` if it names nothing.

    A closed table rather than a ``getattr`` walk. An attribute path evaluated by reflection
    would silently start resolving whatever a future contract field happened to be called,
    and a constraint that quietly began comparing a different value is worse than one that
    fails as unknown.
    """

    table: dict[str, str | None] = {}
    if runtime is not None:
        table.update(
            {
                "runtime.provider": runtime.provider.value,
                "runtime.runtime_id": runtime.runtime_id,
                "runtime.adapter_version": runtime.adapter_version,
                "runtime.model": runtime.model,
                "runtime.capability_profile_digest": runtime.capability_profile_digest,
            }
        )
    if model is not None:
        table.update(
            {
                "model.provider": model.provider.value,
                "model.model_id": model.model_id,
            }
        )
    if environment is not None:
        table.update(
            {
                "environment.environment_id": environment.environment.environment_id,
                "environment.image_digest": environment.environment.image_digest,
                "environment.policy_profile": environment.environment.policy_profile,
                "environment.workspace_isolation": environment.workspace_isolation,
            }
        )
    return table.get(attribute)


class CompatibilityEngine:
    """Turns a snapshot and a subject into a decision, deterministically and without I/O.

    ``created_by`` is required and has no default. Every decision is a
    :class:`~accretion.contracts.canonical.CanonicalContract` and therefore carries a
    principal; fabricating a plausible-looking ``usr_`` id here would put an identity in the
    audit trail that no principal row backs, which is a worse answer than making the caller
    say who is routing.
    """

    def __init__(self, *, created_by: PrincipalRef) -> None:
        self.created_by = created_by

    # ---------------------------------------------------------------------------------
    # Construction
    # ---------------------------------------------------------------------------------

    def _decide(
        self,
        *,
        subject_type: SubjectType,
        subject_ref: str,
        status: CompatibilityStatus,
        rule_id: str,
        reason: ReasonCode,
        snapshot: RoutingSnapshot,
        workspace_id: str,
        project_id: str | None,
        clock: Callable[[], datetime] | None,
    ) -> CompatibilityDecision:
        """Seal one decision, with the derived identity every replay depends on.

        The digest input is the rule, the rule version, the subject, all four snapshot ids
        and the verdict. All four ids are in it deliberately: a decision that only pinned the
        registry digest would collide with the same verdict taken under a different policy or
        against a different set of connections, and the two would then be indistinguishable
        in the store.
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
        # `type: ignore[call-arg]` covers the eight registry §3 header fields only. The
        # pydantic mypy plugin does not synthesize `CanonicalContract`'s inherited fields
        # into a subclass `__init__` — reproducible for every one of the nineteen v0.4
        # contracts, and caused by the `TYPE_CHECKING` forward reference the header needs to
        # type `objective_contract_ref` without an import cycle. The subject fields below are
        # still checked; only the header names are invisible to the plugin.
        return CompatibilityDecision(  # type: ignore[call-arg]
            contract_id=contract_id,
            # Set from the same clock as `evaluated_at`. The header digest covers
            # `created_at`, so leaving it to `datetime.now(UTC)` would make two replays of
            # one decision differ in `content_hash` while agreeing on everything that matters.
            created_at=evaluated_at,
            created_by=self.created_by,
            workspace_id=workspace_id,
            project_id=project_id,
            # The three hex snapshot ids travel as evidence; `policy_snapshot_id` is an
            # `id@version` label rather than a digest, and `EvidenceRef.content_digest` is
            # constrained to 64 hex characters, so the policy identity travels as a label.
            # It is inside `contract_id` either way.
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

    # ---------------------------------------------------------------------------------
    # The single-subject entry point
    # ---------------------------------------------------------------------------------

    def evaluate(
        self,
        *,
        subject_type: SubjectType,
        subject_ref: str,
        requirements: LayerRequirement,
        snapshot: RoutingSnapshot,
        workspace_id: str,
        project_id: str | None,
        clock: Callable[[], datetime] | None = None,
    ) -> CompatibilityDecision:
        """Evaluate one subject of one layer against ``snapshot``.

        A dispatcher and nothing more: each ``SubjectType`` has exactly one rule body, and
        :meth:`evaluate_joint` reaches the same bodies through this method rather than around
        it, so a rule can never behave differently depending on which caller asked.
        """

        rules: dict[SubjectType, _RuleBody] = {
            SubjectType.ENVIRONMENT: self._environment_rule,
            SubjectType.RUNTIME: self._runtime_rule,
            SubjectType.MODEL: self._model_rule,
            SubjectType.TOOL: self._tool_rule,
            SubjectType.SKILL: self._skill_rule,
            SubjectType.VERIFIER: self._verifier_rule,
            SubjectType.CONFIGURATION: self._configuration_rule,
        }
        return rules[subject_type](
            requirements,
            subject_type=subject_type,
            subject_ref=subject_ref,
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=clock,
        )

    # ---------------------------------------------------------------------------------
    # The resolver mapping, which is the whole of the TOOL layer
    # ---------------------------------------------------------------------------------

    def map_resolution(
        self,
        resolved: ResolvedCapability | None,
        *,
        subject_ref: str,
        snapshot: RoutingSnapshot,
        workspace_id: str,
        project_id: str | None,
        clock: Callable[[], datetime] | None = None,
    ) -> CompatibilityDecision:
        """Map one :class:`~accretion.contracts.ResolvedCapability` to a decision.

        The resolver already knows how a capability, its bindings, a connector and a
        connection combine; re-deriving that here would be a second answer to "is this tool
        usable" and registry §21 forbids the duplicate. So this is a mapping and not a rule:
        five outcomes in, five verdicts out.

        ``None`` — a capability this snapshot never saw — is the sixth case and the one that
        matters most. It is ``UNKNOWN``/``COMPATIBILITY_UNKNOWN``, never ``INCOMPATIBLE``:
        "the registry says no" and "the registry was not asked" are different answers, and
        collapsing them would let a snapshot taken before a capability was registered look
        exactly like one taken after it was removed.

        Two refinements sharpen the resolver's coarser outcomes, both structurally rather
        than by reading its ``reason`` prose. A ``DISABLED`` outcome on an *enabled*
        capability with an *enabled* MCP-backed binding whose server the snapshot did not
        observe as ``READY`` came from the MCP readiness gate, so it reports
        ``MCP_SERVER_NOT_READY``; the same outcome with a ``READY`` server came from the
        plugin gate that runs before it, and stays ``CAPABILITY_DISABLED``. A
        ``REQUIRE_REAUTH`` outcome whose connection is nonetheless in a usable status can
        only have come from the missing-scopes branch, so it reports ``SCOPE_INSUFFICIENT``.
        Both codes would otherwise be vocabulary no rule could ever emit.
        """

        if resolved is None:
            return self._decide(
                subject_type=SubjectType.TOOL,
                subject_ref=subject_ref,
                status=CompatibilityStatus.UNKNOWN,
                rule_id=RULE_CAPABILITY,
                reason=ReasonCode.COMPATIBILITY_UNKNOWN,
                snapshot=snapshot,
                workspace_id=workspace_id,
                project_id=project_id,
                clock=clock,
            )

        outcome = resolved.outcome
        if outcome in {
            CapabilityResolutionOutcome.OK,
            CapabilityResolutionOutcome.NO_CONNECTOR_REQUIRED,
        }:
            status, reason = CompatibilityStatus.COMPATIBLE, ReasonCode.COMPATIBLE
        elif outcome is CapabilityResolutionOutcome.DISABLED:
            binding = resolved.binding
            mcp_gate = (
                resolved.capability.enabled
                and binding is not None
                and binding.enabled
                and binding.backend.type is CapabilityBackend.MCP
                # The four conditions above narrow `DISABLED` to "an enabled capability
                # behind an enabled MCP binding", which the resolver reaches from two
                # different gates: the MCP readiness gate, and the *plugin* gate that runs
                # before it (`resolver.py:189`) for a capability a disabled or reinstalled
                # plugin contributed. Both look identical from here, so the coarse test
                # labelled a plugin problem as a server problem and sent an operator to
                # restart a server that was never down. The snapshot's observed state is
                # what separates them, and it is read structurally rather than from the
                # resolver's prose: MCP_SERVER_NOT_READY only when the bound server is not
                # READY, which includes the server this snapshot never saw.
                and snapshot.mcp_server_state(binding.backend.server_ref)
                is not McpServerState.READY
            )
            status = CompatibilityStatus.INCOMPATIBLE
            reason = (
                ReasonCode.MCP_SERVER_NOT_READY if mcp_gate else ReasonCode.CAPABILITY_DISABLED
            )
        elif outcome is CapabilityResolutionOutcome.NO_CONNECTION:
            status, reason = (
                CompatibilityStatus.INCOMPATIBLE,
                ReasonCode.CAPABILITY_UNAVAILABLE,
            )
        else:
            connection = resolved.connection
            binding = resolved.binding
            status = CompatibilityStatus.INCOMPATIBLE
            if connection is None and binding is not None and binding.backend.type is (
                CapabilityBackend.MCP
            ):
                # The resolver's MCP branch is the only one that returns REQUIRE_REAUTH
                # without ever selecting a connection: an MCP server in AUTH_REQUIRED needs
                # the *server* re-authorised, which is a different operator action from
                # renewing a connection's credential.
                reason = ReasonCode.MCP_SERVER_NOT_READY
            elif connection is not None and connection.status in _USABLE_CONNECTION_STATUSES:
                reason = ReasonCode.SCOPE_INSUFFICIENT
            else:
                reason = ReasonCode.CONNECTION_REQUIRES_REAUTH

        return self._decide(
            subject_type=SubjectType.TOOL,
            subject_ref=subject_ref,
            status=status,
            rule_id=RULE_CAPABILITY,
            reason=reason,
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=clock,
        )

    # ---------------------------------------------------------------------------------
    # The per-layer rule bodies
    # ---------------------------------------------------------------------------------

    def _environment_rule(
        self,
        requirements: LayerRequirement,
        *,
        subject_type: SubjectType,
        subject_ref: str,
        snapshot: RoutingSnapshot,
        workspace_id: str,
        project_id: str | None,
        clock: Callable[[], datetime] | None,
    ) -> CompatibilityDecision:
        """Every declared environment constraint must hold against this configuration."""

        if requirements.environment is None:
            raise ValueError("an ENVIRONMENT subject requires an environment binding")
        status = CompatibilityStatus.COMPATIBLE
        reason = ReasonCode.COMPATIBLE
        for constraint in requirements.environment_constraints:
            if constraint.attribute == RUNTIME_VERSION_ATTRIBUTE:
                # Owned by the runtime rule, which compares against the *observed* version.
                continue
            observed = _configuration_attribute(
                constraint.attribute,
                environment=requirements.environment,
                runtime=requirements.runtime,
                model=requirements.model,
            )
            verdict = _holds(constraint, observed)
            if verdict is False:
                status = CompatibilityStatus.INCOMPATIBLE
                reason = ReasonCode.ENVIRONMENT_CONSTRAINT_UNMET
                break
            if verdict is None:
                status = CompatibilityStatus.UNKNOWN
                reason = ReasonCode.COMPATIBILITY_UNKNOWN
                # No break: a later constraint may be a definite refusal, and a definite
                # refusal is more informative than an unresolved one.
        return self._decide(
            subject_type=subject_type,
            subject_ref=subject_ref,
            status=status,
            rule_id=RULE_ENVIRONMENT,
            reason=reason,
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=clock,
        )

    def _runtime_rule(
        self,
        requirements: LayerRequirement,
        *,
        subject_type: SubjectType,
        subject_ref: str,
        snapshot: RoutingSnapshot,
        workspace_id: str,
        project_id: str | None,
        clock: Callable[[], datetime] | None,
    ) -> CompatibilityDecision:
        """The runtime must be in the snapshot, READY, and at a version the node accepts."""

        if requirements.runtime is None:
            raise ValueError("a RUNTIME subject requires a runtime reference")
        health = snapshot.runtime(requirements.runtime.runtime_id)
        if health is None or health.status is not RuntimeStatus.READY:
            return self._decide(
                subject_type=subject_type,
                subject_ref=subject_ref,
                status=CompatibilityStatus.INCOMPATIBLE,
                rule_id=RULE_RUNTIME,
                reason=ReasonCode.RUNTIME_UNAVAILABLE,
                snapshot=snapshot,
                workspace_id=workspace_id,
                project_id=project_id,
                clock=clock,
            )

        ranges = [
            constraint
            for constraint in requirements.environment_constraints
            if constraint.attribute == RUNTIME_VERSION_ATTRIBUTE
        ]
        status = CompatibilityStatus.COMPATIBLE
        reason = ReasonCode.COMPATIBLE
        if ranges:
            for constraint in ranges:
                verdict = _holds(constraint, health.runtime_version)
                if verdict is False:
                    status = CompatibilityStatus.INCOMPATIBLE
                    reason = ReasonCode.RUNTIME_VERSION_OUT_OF_RANGE
                    break
                if verdict is None:
                    status = CompatibilityStatus.UNKNOWN
                    reason = ReasonCode.COMPATIBILITY_UNKNOWN
        elif requirements.runtime.adapter_version != health.runtime_version:
            # No declared range, so the reference's own pin governs. A single version is a
            # range of one, and drifting off it is the same refusal by the same name — which
            # is what M2's pre-dispatch re-check will raise as RUNTIME_VERSION_DRIFT.
            status = CompatibilityStatus.INCOMPATIBLE
            reason = ReasonCode.RUNTIME_VERSION_OUT_OF_RANGE
        return self._decide(
            subject_type=subject_type,
            subject_ref=subject_ref,
            status=status,
            rule_id=RULE_RUNTIME,
            reason=reason,
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=clock,
        )

    def _model_rule(
        self,
        requirements: LayerRequirement,
        *,
        subject_type: SubjectType,
        subject_ref: str,
        snapshot: RoutingSnapshot,
        workspace_id: str,
        project_id: str | None,
        clock: Callable[[], datetime] | None,
    ) -> CompatibilityDecision:
        """The model's provider must be a provider this snapshot has a READY runtime for.

        A model is not a free-standing thing: it is served by a runtime, and a tuple that
        names a model from one provider beside a runtime from another names an execution that
        cannot happen. The refusal is ``RUNTIME_UNAVAILABLE`` rather than a code of its own,
        because that is precisely what is wrong — no runtime in this snapshot can serve this
        model — and inventing a second word for it would fork the vocabulary.
        """

        if requirements.model is None:
            raise ValueError("a MODEL subject requires a model binding")
        serving = [
            health
            for health in snapshot.runtime_health
            if health.provider is requirements.model.provider
            and health.status is RuntimeStatus.READY
        ]
        mismatch = (
            requirements.runtime is not None
            and requirements.runtime.provider is not requirements.model.provider
        )
        status = (
            CompatibilityStatus.COMPATIBLE
            if serving and not mismatch
            else CompatibilityStatus.INCOMPATIBLE
        )
        return self._decide(
            subject_type=subject_type,
            subject_ref=subject_ref,
            status=status,
            rule_id=RULE_MODEL,
            reason=(
                ReasonCode.COMPATIBLE
                if status is CompatibilityStatus.COMPATIBLE
                else ReasonCode.RUNTIME_UNAVAILABLE
            ),
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=clock,
        )

    def _tool_rule(
        self,
        requirements: LayerRequirement,
        *,
        subject_type: SubjectType,
        subject_ref: str,
        snapshot: RoutingSnapshot,
        workspace_id: str,
        project_id: str | None,
        clock: Callable[[], datetime] | None,
    ) -> CompatibilityDecision:
        """Delegates to :meth:`map_resolution`, which is the only capability rule there is."""

        if requirements.tool is None and requirements.resolved is None:
            raise ValueError("a TOOL subject requires a tool binding or a resolved capability")
        resolved = requirements.resolved
        if resolved is None and requirements.tool is not None:
            resolved = snapshot.resolved(requirements.tool.capability.capability_id)
        return self.map_resolution(
            resolved,
            subject_ref=subject_ref,
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=clock,
        )

    def _skill_rule(
        self,
        requirements: LayerRequirement,
        *,
        subject_type: SubjectType,
        subject_ref: str,
        snapshot: RoutingSnapshot,
        workspace_id: str,
        project_id: str | None,
        clock: Callable[[], datetime] | None,
    ) -> CompatibilityDecision:
        """A bound skill must be a registered skill or an allow-listed plugin.

        Lifted from the P7 engine's ``SKILL_OR_PLUGIN_UNAVAILABLE`` rule unchanged, including
        the disjunction: a plugin that contributes a skill makes that skill available, and a
        rule that only consulted the skill table would refuse working configurations.
        """

        if requirements.skill is None:
            raise ValueError("a SKILL subject requires a skill reference")
        available = (
            requirements.skill.skill_id in snapshot.skills
            or requirements.skill.skill_id in snapshot.plugins
        )
        return self._decide(
            subject_type=subject_type,
            subject_ref=subject_ref,
            status=(
                CompatibilityStatus.COMPATIBLE
                if available
                else CompatibilityStatus.INCOMPATIBLE
            ),
            rule_id=RULE_SKILL,
            reason=(
                ReasonCode.COMPATIBLE if available else ReasonCode.SKILL_OR_PLUGIN_UNAVAILABLE
            ),
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=clock,
        )

    def _verifier_rule(
        self,
        requirements: LayerRequirement,
        *,
        subject_type: SubjectType,
        subject_ref: str,
        snapshot: RoutingSnapshot,
        workspace_id: str,
        project_id: str | None,
        clock: Callable[[], datetime] | None,
    ) -> CompatibilityDecision:
        """The bound verifier must be registered in the registry this snapshot observed.

        Availability only. Whether the verifier enforces the *right* spec is a property of
        the tuple against the node, not of the verifier, so it belongs to the joint
        ``CONFIGURATION`` decision and is checked there once.
        """

        if requirements.verifier is None:
            raise ValueError("a VERIFIER subject requires a verifier binding")
        registered = (
            requirements.verifier.verifier.verifier_contract_id in snapshot.verifier_ids
        )
        return self._decide(
            subject_type=subject_type,
            subject_ref=subject_ref,
            status=(
                CompatibilityStatus.COMPATIBLE
                if registered
                else CompatibilityStatus.INCOMPATIBLE
            ),
            rule_id=RULE_VERIFIER,
            reason=ReasonCode.COMPATIBLE if registered else ReasonCode.VERIFIER_UNAVAILABLE,
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=clock,
        )

    def _configuration_rule(
        self,
        requirements: LayerRequirement,
        *,
        subject_type: SubjectType,
        subject_ref: str,
        snapshot: RoutingSnapshot,
        workspace_id: str,
        project_id: str | None,
        clock: Callable[[], datetime] | None,
    ) -> CompatibilityDecision:
        """SDD §9.1 stage 7: is this *tuple* an admissible answer to *this node*?

        Three inputs, in the order they are decided:

        1. **The verifier spec.** ADR-044 freezes verification semantics before routing, so a
           configuration whose verifier enforces a spec other than the one the node pinned is
           not a weaker answer to the node's question — it is an answer to a different
           question. ``VERIFIER_SPEC_HASH_MISMATCH``, and it is checked first because it is
           the attack §18.4 names.
        2. **Coverage.** Every capability the node requires must have a tool binding, and
           the bound capability must be at a version the requirement's ``version_range``
           admits. A requirement the snapshot knows and the tuple did not bind, and a
           binding whose registered version falls outside the declared range, are both
           ``CAPABILITY_UNAVAILABLE`` — in each case the registry knows the capability and
           nothing in this snapshot can serve *what the node asked for*. A requirement the
           snapshot has never heard of, and a range this module is not entitled to
           interpret, are both ``COMPATIBILITY_UNKNOWN``, for the same reason
           :meth:`map_resolution` distinguishes "the registry said no" from "the registry
           was not asked".

           ``required_scope`` is deliberately *not* checked here; see the module note on the
           M1.2 deferral. It is authority rather than availability, and the snapshot carries
           no scope evidence to decide it against.
        3. **The layers.** The per-layer verdicts are folded, never recomputed. A definite
           refusal outranks an unresolved one, because "this cannot work" tells an operator
           more than "this could not be checked" — and both are equally ineligible.
        """

        expected = requirements.verification_spec_hash
        actual = requirements.verifier.verification_spec_hash if requirements.verifier else None
        if expected is not None and actual is not None and expected != actual:
            return self._decide(
                subject_type=subject_type,
                subject_ref=subject_ref,
                status=CompatibilityStatus.INCOMPATIBLE,
                rule_id=RULE_CONFIGURATION,
                reason=ReasonCode.VERIFIER_SPEC_HASH_MISMATCH,
                snapshot=snapshot,
                workspace_id=workspace_id,
                project_id=project_id,
                clock=clock,
            )

        if requirements.missing_capability_ids or requirements.out_of_range_capability_ids:
            return self._decide(
                subject_type=subject_type,
                subject_ref=subject_ref,
                status=CompatibilityStatus.INCOMPATIBLE,
                rule_id=RULE_CONFIGURATION,
                reason=ReasonCode.CAPABILITY_UNAVAILABLE,
                snapshot=snapshot,
                workspace_id=workspace_id,
                project_id=project_id,
                clock=clock,
            )
        if requirements.unknown_capability_ids or requirements.undecidable_range_capability_ids:
            return self._decide(
                subject_type=subject_type,
                subject_ref=subject_ref,
                status=CompatibilityStatus.UNKNOWN,
                rule_id=RULE_CONFIGURATION,
                reason=ReasonCode.COMPATIBILITY_UNKNOWN,
                snapshot=snapshot,
                workspace_id=workspace_id,
                project_id=project_id,
                clock=clock,
            )

        incompatible = [
            decision
            for decision in requirements.layer_decisions
            if decision.status is CompatibilityStatus.INCOMPATIBLE
        ]
        unknown = [
            decision
            for decision in requirements.layer_decisions
            if decision.status is CompatibilityStatus.UNKNOWN
        ]
        if incompatible:
            status, reason = (
                CompatibilityStatus.INCOMPATIBLE,
                ReasonCode(incompatible[0].reason_code),
            )
        elif unknown:
            status, reason = CompatibilityStatus.UNKNOWN, ReasonCode.COMPATIBILITY_UNKNOWN
        else:
            status, reason = CompatibilityStatus.COMPATIBLE, ReasonCode.COMPATIBLE
        return self._decide(
            subject_type=subject_type,
            subject_ref=subject_ref,
            status=status,
            rule_id=RULE_CONFIGURATION,
            reason=reason,
            snapshot=snapshot,
            workspace_id=workspace_id,
            project_id=project_id,
            clock=clock,
        )

    # ---------------------------------------------------------------------------------
    # The joint entry point
    # ---------------------------------------------------------------------------------

    def evaluate_joint(
        self,
        *,
        configuration: ExecutionConfiguration,
        node_contract: NodeContract,
        snapshot: RoutingSnapshot,
        workspace_id: str,
        project_id: str | None,
        clock: Callable[[], datetime] | None = None,
    ) -> list[CompatibilityDecision]:
        """One decision per layer in registry §7.3 order, then exactly one for the tuple.

        The order is fixed and is part of what the test asserts: environment, runtime, model,
        one per tool binding, one per skill, verifier, configuration. Tool and skill
        decisions follow the configuration's own list order, which
        ``ExecutionConfiguration.configuration_hash`` already commits to, so two evaluations
        of one configuration produce the same list in the same order.
        """

        constraints = tuple(node_contract.environment_constraints)
        decisions: list[CompatibilityDecision] = []

        decisions.append(
            self.evaluate(
                subject_type=SubjectType.ENVIRONMENT,
                subject_ref=configuration.environment.environment.environment_id,
                requirements=LayerRequirement(
                    environment=configuration.environment,
                    runtime=configuration.runtime,
                    model=configuration.model,
                    environment_constraints=constraints,
                ),
                snapshot=snapshot,
                workspace_id=workspace_id,
                project_id=project_id,
                clock=clock,
            )
        )
        decisions.append(
            self.evaluate(
                subject_type=SubjectType.RUNTIME,
                subject_ref=configuration.runtime.runtime_id,
                requirements=LayerRequirement(
                    runtime=configuration.runtime,
                    environment_constraints=constraints,
                ),
                snapshot=snapshot,
                workspace_id=workspace_id,
                project_id=project_id,
                clock=clock,
            )
        )
        decisions.append(
            self.evaluate(
                subject_type=SubjectType.MODEL,
                subject_ref=configuration.model.model_id,
                requirements=LayerRequirement(
                    model=configuration.model, runtime=configuration.runtime
                ),
                snapshot=snapshot,
                workspace_id=workspace_id,
                project_id=project_id,
                clock=clock,
            )
        )
        for tool in configuration.tools:
            decisions.append(
                self.evaluate(
                    subject_type=SubjectType.TOOL,
                    subject_ref=tool.capability.capability_id,
                    requirements=LayerRequirement(tool=tool),
                    snapshot=snapshot,
                workspace_id=workspace_id,
                project_id=project_id,
                clock=clock,
                )
            )
        for skill in configuration.skills:
            decisions.append(
                self.evaluate(
                    subject_type=SubjectType.SKILL,
                    subject_ref=skill.skill_id,
                    requirements=LayerRequirement(skill=skill),
                    snapshot=snapshot,
                workspace_id=workspace_id,
                project_id=project_id,
                clock=clock,
                )
            )
        decisions.append(
            self.evaluate(
                subject_type=SubjectType.VERIFIER,
                subject_ref=configuration.verifier.verifier.verifier_contract_id,
                requirements=LayerRequirement(verifier=configuration.verifier),
                snapshot=snapshot,
                workspace_id=workspace_id,
                project_id=project_id,
                clock=clock,
            )
        )

        bound = {tool.capability.capability_id for tool in configuration.tools}
        missing: list[str] = []
        unknown: list[str] = []
        out_of_range: list[str] = []
        undecidable_range: list[str] = []
        for requirement in node_contract.required_capabilities:
            capability_id = requirement.capability.capability_id
            resolved = snapshot.resolved(capability_id)
            if capability_id not in bound:
                if resolved is None:
                    unknown.append(capability_id)
                else:
                    missing.append(capability_id)
                continue
            if resolved is None:
                # Bound, but the snapshot never saw it. The TOOL layer already reports that
                # as UNKNOWN and the fold below carries it into the joint decision; adding
                # it here as well would say one fact twice under two codes.
                continue
            # The range is checked against the version the *registry* would serve, not the
            # one the binding names. A tuple may declare whatever it likes; what would run
            # is what the snapshot observed, and stage 7 admits tuples on the second.
            verdict = _version_range_holds(requirement.version_range, resolved.capability.version)
            if verdict is False:
                out_of_range.append(capability_id)
            elif verdict is None:
                undecidable_range.append(capability_id)

        decisions.append(
            self.evaluate(
                subject_type=SubjectType.CONFIGURATION,
                subject_ref=configuration.contract_id,
                requirements=LayerRequirement(
                    verifier=configuration.verifier,
                    verification_spec_hash=node_contract.verification_spec_ref.content_hash,
                    layer_decisions=tuple(decisions),
                    missing_capability_ids=tuple(missing),
                    unknown_capability_ids=tuple(unknown),
                    out_of_range_capability_ids=tuple(out_of_range),
                    undecidable_range_capability_ids=tuple(undecidable_range),
                ),
                snapshot=snapshot,
                workspace_id=workspace_id,
                project_id=project_id,
                clock=clock,
            )
        )
        return decisions


def eligible(decisions: Sequence[CompatibilityDecision]) -> bool:
    """Whether every decision admits its subject for a required constraint.

    One place for the rule SDD §7.7 states, so no caller writes it a second time and gets it
    subtly wrong. ``all(... is COMPATIBLE)`` and never ``not any(... is INCOMPATIBLE)``: the
    second spelling admits ``UNKNOWN``.
    """

    return all(decision.is_compatible() for decision in decisions)


__all__ = [
    "RULE_CAPABILITY",
    "RULE_CONFIGURATION",
    "RULE_ENVIRONMENT",
    "RULE_MODEL",
    "RULE_RUNTIME",
    "RULE_SKILL",
    "RULE_VERIFIER",
    "RUNTIME_VERSION_ATTRIBUTE",
    "CompatibilityEngine",
    "LayerRequirement",
    "eligible",
]
