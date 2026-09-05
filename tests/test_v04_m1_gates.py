"""The policy, risk and permission gates, and the identity every routing attempt derives.

Two claims are proved here and they are different in kind.

**AC4-M1-005 — the gates are outside the learned router.** Proved twice, because either half
alone is weak. The *structural* half walks the abstract syntax tree of
``routing/gates.py`` and ``routing/compatibility.py`` and fails on the mere presence of a
selector, candidate-builder, ranker, GBDT, adapter or experience module name in an import
statement. It asserts on names and never on importability, deliberately: most of those
modules do not exist on develop yet, so a test that tried to import them to check the edge
would pass vacuously today and would keep passing on the day the ranker landed. The
*behavioural* half drives :func:`~accretion.routing.gates.gate_then_evaluate` with a spy
gate and a spy evaluator sharing one call log, and asserts both directions of the order: every
gate decision exists before the joint evaluation is consulted, and a refusal means it is never
consulted at all.

Then the SDD §18.2 property: appending an unauthorized capability can never *increase* how
many capabilities are eligible. It is a property and not an example because the failure mode
it guards — a gate that consults the union of what the task allows and what the router asked
for — looks correct on every hand-written case where the two sets already agree.

**Identity is deterministic.** A routing request id that did not move when the policy moved,
or an execution instance id that two computations disagreed about, would break SDD §8.2's
guarantee silently: the receipt would still be produced, and it would be the wrong one.

Everything runs offline against a :class:`~accretion.persistence.store.MemoryStore`, the
repository's ``FakeRuntime`` and hand-written spies with call logs. There is no
``conftest.py`` in this repository, so the world one test needs is built by a module-local
``setup_*`` builder whose keywords each flip exactly one fact.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import random
from datetime import UTC, datetime
from pathlib import Path

import pytest

from accretion.contracts import (
    AcceptancePolicy,
    Capability,
    CapabilityBackend,
    CapabilityPolicy,
    GraphNodeKind,
    IdempotencyMode,
    PrincipalRef,
    PrincipalStatus,
    Project,
    Provider,
    RiskLevel,
    Run,
    RunState,
    Task,
    TaskEnvelope,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.contracts.refs import (
    CapabilityRef,
    EnvironmentRef,
    PolicyRef,
    RuntimeRef,
    SkillRef,
    ToolRef,
    VerifierRef,
)
from accretion.contracts.routing import (
    CapabilityRequirement,
    CompatibilityDecision,
    CompatibilityStatus,
    Criticality,
    EnvironmentBinding,
    ExecutionConfiguration,
    ModelBinding,
    NodeContract,
    ObjectiveContractRef,
    ResourceBudget,
    RiskClass,
    SubjectType,
    ToolBinding,
    VerificationSpec,
    VerificationSpecRef,
    VerificationState,
    VerifierBinding,
)
from accretion.governance import CapabilityPolicyEngine
from accretion.identity import LOCAL_WORKSPACE_ID
from accretion.ids import derived_id, has_prefix
from accretion.persistence.store import MemoryStore
from accretion.resolver import CapabilityResolver
from accretion.routing.gates import (
    RULE_CAPABILITY_AUTHORIZED,
    RULE_PERMISSION_WORKSPACE,
    RULE_RISK_CLASS,
    PolicyGate,
    gate_then_evaluate,
    is_gate_decision,
)
from accretion.routing.identity import (
    VerificationSpecBuilder,
    execution_instance_id,
    principal_ref_for_run,
    routing_request_id,
    workspace_for_run,
)
from accretion.routing.protocols import RoutingMode
from accretion.routing.reasons import RULE_VERSION, ReasonCode
from accretion.routing.snapshot import RegistrySnapshotBuilder, RoutingSnapshot
from accretion.runtimes.fake import FakeRuntime
from accretion.verifiers.registry import VerifierRegistry

WORKSPACE_ID = "wks_8G33T24F686H6EJPBHRSFYCC3C"
OTHER_WORKSPACE_ID = "wks_8G33T24F686H6EJPBHRSFYCC3D"
PROJECT_ID = "prj_8W5DH3HW6DPAFFPBHQ47R21DK9"
TASK_ID = "tsk_8W5DH3HW6DPAFFPBHQ47R21DKA"
POLICY_ID = "local-capability-policy"
FIXED_TIME = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
PRINCIPAL = PrincipalRef(
    principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    display_name="M1 gates test",
    status=PrincipalStatus.ACTIVE,
)
ROOT = Path(__file__).resolve().parents[1]


def digest(seed: str) -> str:
    """A stable 64-character digest for a reference field, keyed by a readable name."""

    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def clock() -> datetime:
    """The injected clock. A constant, so ``created_at`` cannot fork a replay's digest."""

    return FIXED_TIME


# --------------------------------------------------------------------------------------
# Fixtures. The gates read a task, a capability and a policy; the snapshot is needed only
# for the four ids a decision's identity pins, which is why the registry behind it is empty.
# --------------------------------------------------------------------------------------


def capability_row(
    capability_id: str,
    *,
    enabled: bool = True,
    risk: RiskLevel = RiskLevel.LOW,
    side_effects: tuple[str, ...] = (),
    idempotency: IdempotencyMode = IdempotencyMode.NONE,
    required_permissions: tuple[str, ...] = (),
) -> Capability:
    return Capability(
        capability_id=capability_id,
        version="1.0.0",
        description="M1 gates fixture",
        backend=CapabilityBackend.PYTHON,
        risk=risk,
        side_effects=list(side_effects),
        idempotency=idempotency,
        required_permissions=list(required_permissions),
        enabled=enabled,
        created_at=FIXED_TIME,
    )


def task_row(
    *,
    allowed: tuple[str, ...] = ("cap.search",),
    denied: tuple[str, ...] = (),
    risk_level: RiskLevel = RiskLevel.LOW,
    required_outputs: tuple[dict[str, str], ...] = (),
) -> Task:
    return Task(
        envelope=TaskEnvelope(
            task_id=TASK_ID,
            project_id=PROJECT_ID,
            objective="route one node deterministically",
            risk_level=risk_level,
            allowed_capabilities=list(allowed),
            denied_capabilities=list(denied),
            required_outputs=[dict(item) for item in required_outputs],
        ),
        created_at=FIXED_TIME,
    )


async def setup_gate(
    *,
    explicitly_denied: tuple[str, ...] = (),
    require_approval_at_risk: RiskLevel = RiskLevel.HIGH,
    granted_permissions: frozenset[str] = frozenset(),
) -> tuple[MemoryStore, RoutingSnapshot, PolicyGate]:
    """The whole offline world the gates need: a store, one snapshot, one gate.

    The capability registry behind the snapshot is deliberately empty. A gate asks about
    *authority* — what this task, this policy and this operator permit — and never about
    availability, so a gate that needed a registry row to answer would be a gate reading the
    wrong question. The snapshot is here only because a decision's identity pins its four ids.
    """

    store = MemoryStore()
    await store.create_project(
        Project(
            project_id=PROJECT_ID,
            name="m1-gates",
            repository_path=Path("/tmp/accretion-m1-gates"),
            created_at=FIXED_TIME,
        )
    )
    policy = CapabilityPolicy(
        policy_id=POLICY_ID,
        version="1.0.0",
        description="M1 gates fixture policy",
        explicitly_denied=list(explicitly_denied),
        require_approval_at_risk=require_approval_at_risk,
        created_at=FIXED_TIME,
    )
    await store.upsert_capability_policy(policy)
    builder = RegistrySnapshotBuilder(
        store,
        CapabilityResolver(store),
        {Provider.FAKE: FakeRuntime()},
        VerifierRegistry([]),
        policy_id=POLICY_ID,
    )
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    gate = PolicyGate(
        CapabilityPolicyEngine(set(granted_permissions)), policy, created_by=PRINCIPAL
    )
    return store, snapshot, gate


def objective_ref() -> ObjectiveContractRef:
    return ObjectiveContractRef(
        contract_id="objective-contract-ref-embedded",
        created_at=FIXED_TIME,
        created_by=PRINCIPAL,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        objective_contract_id=derived_id("objective_contract", "m1-gates"),
        revision=1,
        objective_contract_hash=digest("objective-contract"),
        verified_success_floor=0.9,
        utility_profile_id="balanced-delivery",
        risk_policy=PolicyRef(
            policy_id="workspace-risk-policy",
            version="1.0.0",
            content_digest=digest("risk-policy"),
        ),
        approved_by=PRINCIPAL,
        approved_at=FIXED_TIME,
    )


def node_contract(
    *, allowed_risk_class: RiskClass = RiskClass.LOW_DIGITAL
) -> NodeContract:
    return NodeContract(
        contract_id=derived_id("node_contract", "m1-gates", allowed_risk_class.value),
        created_at=FIXED_TIME,
        created_by=PRINCIPAL,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        objective_contract_ref=objective_ref(),
        node_id="node-1",
        run_graph_id="rgr_m1_gates",
        graph_revision=1,
        execution_instance_id="node-1#1",
        objective="apply the reviewed change",
        node_kind=GraphNodeKind.AGENT,
        required_capabilities=[
            CapabilityRequirement(
                capability=CapabilityRef(capability_id="cap.search", capability_version="1.0.0"),
                version_range=">=1.0.0",
                required_scope="read",
            )
        ],
        allowed_risk_class=allowed_risk_class,
        resource_cap=ResourceBudget(
            maximum_cost="1.00",
            maximum_latency_ms=60_000,
            maximum_attempts=2,
            maximum_tool_calls=10,
        ),
        verification_spec_ref=VerificationSpecRef(
            verification_spec_id="vsp_m1_gates", content_hash=digest("spec")
        ),
        failure_policy_ref=PolicyRef(
            policy_id="failure-policy",
            version="1.0.0",
            content_digest=digest("failure-policy"),
        ),
    )


def execution_configuration() -> ExecutionConfiguration:
    return ExecutionConfiguration(
        contract_id=derived_id("execution_configuration", "m1-gates"),
        created_at=FIXED_TIME,
        created_by=PRINCIPAL,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        environment=EnvironmentBinding(
            environment=EnvironmentRef(
                environment_id="env.local",
                image_digest=digest("env.local"),
                policy_profile="local-worktree",
            ),
            workspace_isolation="WORKTREE",
        ),
        runtime=RuntimeRef(
            runtime_id="runtime_fake",
            adapter_version=FakeRuntime.adapter_version,
            provider=Provider.FAKE,
            model="fake-model",
            capability_profile_digest=digest("runtime-profile"),
        ),
        model=ModelBinding(model_id="fake-model", provider=Provider.FAKE),
        tools=[
            ToolBinding(
                capability=CapabilityRef(capability_id="cap.search", capability_version="1.0.0"),
                tool=ToolRef(
                    tool_id="tool.cap.search",
                    implementation_digest=digest("tool.cap.search"),
                ),
                binding_id="capbind_cap.search_conndef_search",
                binding_version="1.0.0",
            )
        ],
        skills=[
            SkillRef(skill_id="skill.review", version="1.0.0", package_digest=digest("skill"))
        ],
        verifier=VerifierBinding(
            verifier=VerifierRef(
                verifier_contract_id="stub-verifier",
                implementation_digest=digest("stub-verifier"),
            ),
            version="1.0.0",
            verification_spec_hash=digest("spec"),
        ),
    )


class SpyGate(PolicyGate):
    """A gate that records which of its three questions was asked, and when.

    A subclass with a shared log rather than a mock, per this repository's convention. It
    overrides nothing about the verdict: every method delegates to the real gate, so the
    call-order test is run against the behaviour that ships and not against a stand-in.
    """

    def __init__(self, log: list[str], inner: PolicyGate) -> None:
        super().__init__(inner.engine, inner.policy, created_by=inner.created_by)
        self.log = log

    def gate_permissions(self, *args: object, **kwargs: object) -> CompatibilityDecision:
        self.log.append(RULE_PERMISSION_WORKSPACE)
        return super().gate_permissions(*args, **kwargs)  # type: ignore[arg-type]

    def gate_risk(self, *args: object, **kwargs: object) -> CompatibilityDecision:
        self.log.append(RULE_RISK_CLASS)
        return super().gate_risk(*args, **kwargs)  # type: ignore[arg-type]

    def gate_capability(self, *args: object, **kwargs: object) -> CompatibilityDecision:
        self.log.append(RULE_CAPABILITY_AUTHORIZED)
        return super().gate_capability(*args, **kwargs)  # type: ignore[arg-type]


class SpyEvaluator:
    """A ``JointEvaluator`` that records that it was consulted, and returns one verdict.

    ``calls`` counts consultations rather than merely recording that one happened, so the
    "never consulted after a refusal" assertion is ``== 0`` and not ``is False`` — a
    difference that matters the day someone makes the refusal path call it once for logging.
    """

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.calls = 0

    def evaluate_joint(self, **kwargs: object) -> list[CompatibilityDecision]:
        self.log.append("evaluate_joint")
        self.calls += 1
        return []


# --------------------------------------------------------------------------------------
# AC4-M1-005
# --------------------------------------------------------------------------------------

FORBIDDEN_MODULES: tuple[str, ...] = (
    "accretion.routing.selector",
    "accretion.routing.candidates",
    "accretion.routing.ranker",
    "accretion.routing.gbdt",
    "accretion.routing.adapter",
    "accretion.experience",
)
"""The module names a gate may not import, written out rather than discovered.

Four of the six do not exist on develop; ``routing.adapter`` and ``experience`` do. The list
is uniform anyway, and the assertion is made on *names in import statements* rather than on
whether the name resolves, because the day M2 lands ``routing.candidates`` is exactly the day
a discovery-based test would start passing for the wrong reason.
"""

GATED_MODULES: tuple[str, ...] = (
    "src/accretion/routing/gates.py",
    "src/accretion/routing/compatibility.py",
    # gates.py reaches snapshot.py, so the walk must include it or a ranker could reach a
    # gate through the one intra-package edge the assertion did not look at.
    "src/accretion/routing/snapshot.py",
)


def imported_module_names(path: Path) -> set[str]:
    """Every module name ``path`` imports, from both statement forms.

    ``ast`` and not a regular expression over the text, because a comment or a docstring
    mentioning a forbidden module is not an import and a text search cannot tell the
    difference — this file's own module docstring names all six.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # `node.module` is None for `from . import x`; the level prefix keeps a relative
            # import from silently reading as no import at all.
            names.add("." * node.level + (node.module or ""))
    return names


@pytest.mark.acceptance("AC4-M1-005")
async def test_gates_import_no_selector_and_run_before_any_scoring() -> None:
    """Nothing learned can reach a gate, and no gate result arrives after a score.

    Two proofs of one criterion. The import graph makes the separation structural: a ranker
    cannot influence a rule in a module that does not import it, and the assertion fails on
    the presence of the *name*, so it keeps working after M2 and M4 create the modules it
    names. The call log makes the ordering behavioural: every gate decision is produced
    before ``evaluate_joint`` is consulted, and a refusal means it is not consulted at all.

    Mutations that flip this and nothing else: importing a ranker into ``gates.py``; moving
    the ``evaluate_joint`` call in ``gate_then_evaluate`` above the gate loop; making the
    refusal path evaluate anyway.
    """

    for relative in GATED_MODULES:
        names = imported_module_names(ROOT / relative)
        # The walker must be seeing imports at all, or every assertion below is vacuous.
        assert any(name.startswith("accretion") for name in names), relative
        for forbidden in FORBIDDEN_MODULES:
            offenders = [name for name in names if name == forbidden or name.startswith(
                f"{forbidden}."
            )]
            assert not offenders, f"{relative} imports {offenders!r}"

    # The closure stays honest: every intra-package edge out of gates.py must be a walked
    # module, so a new edge is red until it is added to GATED_MODULES.
    assert {
        name
        for name in imported_module_names(ROOT / "src/accretion/routing/gates.py")
        if name.startswith("accretion.routing.")
    } == {"accretion.routing.reasons", "accretion.routing.snapshot"}

    _, snapshot, real_gate = await setup_gate()
    log: list[str] = []
    gate = SpyGate(log, real_gate)
    evaluator = SpyEvaluator(log)

    admitted = gate_then_evaluate(
        gate=gate,
        evaluator=evaluator,
        task=task_row(),
        principal=PRINCIPAL,
        entitled_workspace_id=WORKSPACE_ID,
        capabilities=[capability_row("cap.search")],
        node_contract=node_contract(),
        configuration=execution_configuration(),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )

    assert admitted.admitted() is True
    assert evaluator.calls == 1
    assert log == [
        RULE_PERMISSION_WORKSPACE,
        RULE_RISK_CLASS,
        RULE_CAPABILITY_AUTHORIZED,
        "evaluate_joint",
    ]
    assert all(is_gate_decision(decision) for decision in admitted.gate_decisions)

    log.clear()
    evaluator = SpyEvaluator(log)
    refused = gate_then_evaluate(
        gate=SpyGate(log, real_gate),
        evaluator=evaluator,
        # One fact changed: the capability the tuple would bind is not on the allow list.
        task=task_row(allowed=("cap.other",)),
        principal=PRINCIPAL,
        entitled_workspace_id=WORKSPACE_ID,
        capabilities=[capability_row("cap.search")],
        node_contract=node_contract(),
        configuration=execution_configuration(),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )

    assert refused.admitted() is False
    assert refused.eligible() is False
    assert refused.compatibility_decisions == ()
    assert evaluator.calls == 0
    assert "evaluate_joint" not in log
    # Every gate still ran: an operator fixing a refusal needs all of the reasons, not the
    # first one. A short-circuiting implementation would produce two decisions here.
    assert len(refused.gate_decisions) == 3


@pytest.mark.acceptance("AC4-M1-005")
async def test_adding_an_unauthorized_capability_never_increases_eligibility() -> None:
    """SDD §18.2's monotonicity property, over seeded random capability sets.

    Appending a capability the task never allowed must add zero eligible capabilities. Stated
    as ``<=`` rather than ``==`` on purpose: the property that matters is that eligibility
    cannot *grow* from an unauthorized request, and a future gate that also refused something
    else on seeing it would still be safe.

    The mutation this exists for: a gate that consults ``allowed ∪ requested`` instead of
    ``allowed``. That change is invisible on every example where the requested set is already
    a subset of the allow list — which is every hand-written happy path — and it makes the
    count jump here on the first iteration.

    Seeded, because an unreproducible counterexample is a bug report nobody can act on.
    """

    _, snapshot, gate = await setup_gate()
    pool = tuple(f"cap.{index}" for index in range(8))
    allowed = pool[:4]
    unauthorized = "cap.smuggled"
    rng = random.Random(20260905)

    def eligible_count(task: Task, requested: tuple[str, ...]) -> int:
        decisions = [
            gate.gate_capability(
                task,
                capability_row(capability_id),
                snapshot=snapshot,
                workspace_id=WORKSPACE_ID,
                project_id=PROJECT_ID,
                clock=clock,
            )
            for capability_id in requested
        ]
        return sum(
            1 for decision in decisions if decision.status is CompatibilityStatus.COMPATIBLE
        )

    for _ in range(200):
        requested = tuple(
            capability_id for capability_id in pool if rng.random() < 0.5
        )
        task = task_row(allowed=allowed)
        before = eligible_count(task, requested)
        after = eligible_count(task, (*requested, unauthorized))
        assert after <= before, (requested, before, after)
        # And the appended id is itself refused with the code that names why, so the
        # property cannot be satisfied by a gate that refuses everything.
        assert before == len(set(requested) & set(allowed))

    refusal = gate.gate_capability(
        task_row(allowed=allowed),
        capability_row(unauthorized),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    assert refusal.status is CompatibilityStatus.INCOMPATIBLE
    assert refusal.reason_code == ReasonCode.CAPABILITY_NOT_ALLOWED.value


# --------------------------------------------------------------------------------------
# The individual gates
# --------------------------------------------------------------------------------------


async def test_require_approval_is_incompatible_with_approval_required() -> None:
    """Routing does not pre-approve, and says so in a code of its own.

    A capability at or above the policy's approval threshold is *protected*, and
    ``CapabilityPolicyEngine.authorize`` answers ``REQUIRE_APPROVAL`` for it when no approval
    record is bound. The gate turns that into INCOMPATIBLE rather than into a soft yes,
    because a v0.1 approval is content-bound to a request and no request exists while a
    configuration is still being chosen.

    ``APPROVAL_REQUIRED`` and not ``CAPABILITY_DENIED``: the two need different operator
    actions — ask someone, versus stop asking — and a single code would hide the difference.
    Mutating the gate to map ``REQUIRE_APPROVAL`` onto COMPATIBLE flips this and nothing else.
    """

    _, snapshot, gate = await setup_gate()
    decision = gate.gate_capability(
        task_row(),
        capability_row("cap.search", risk=RiskLevel.HIGH),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )

    assert decision.status is CompatibilityStatus.INCOMPATIBLE
    assert decision.reason_code == ReasonCode.APPROVAL_REQUIRED.value
    assert decision.rule_id == RULE_CAPABILITY_AUTHORIZED
    assert decision.is_compatible() is False


async def test_a_side_effecting_capability_is_gated_on_the_registry_and_not_on_the_probe(
) -> None:
    """The synthesized probe suppresses one refusal and must not suppress the other.

    ``authorize`` refuses a side-effecting capability twice over: because the *capability*
    declares ``IdempotencyMode.NONE``, and because the *request* carries no idempotency key.
    The first is a fact about the registry row and is answerable at routing time; the second
    is a fact about a call nobody has made yet, so the gate's probe supplies a derived key.

    Both halves are asserted, because suppressing the wrong one would let a capability that
    may never be invoked idempotently be routed to — and the failure would arrive at the
    gateway, at the last possible moment, instead of the first.
    """

    _, snapshot, gate = await setup_gate()

    def gated(capability: Capability) -> CompatibilityDecision:
        return gate.gate_capability(
            task_row(),
            capability,
            snapshot=snapshot,
            workspace_id=WORKSPACE_ID,
            project_id=PROJECT_ID,
            clock=clock,
        )

    refused = gated(
        capability_row(
            "cap.search", side_effects=("writes files",), idempotency=IdempotencyMode.NONE
        )
    )
    assert refused.status is CompatibilityStatus.INCOMPATIBLE
    assert refused.reason_code == ReasonCode.PROTECTED_SIDE_EFFECT_STATE.value

    # Keyed idempotency plus a policy that does not demand approval for a side effect at this
    # risk: the probe's derived key is what lets the gate answer at all.
    _, permissive_snapshot, permissive = await setup_gate(
        require_approval_at_risk=RiskLevel.CRITICAL
    )
    admitted = permissive.gate_capability(
        task_row(),
        capability_row(
            "cap.search", side_effects=("writes files",), idempotency=IdempotencyMode.KEYED
        ),
        snapshot=permissive_snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    # Still protected — a side effect is protected regardless of risk — so the honest answer
    # is APPROVAL_REQUIRED, not COMPATIBLE. What the probe changed is that the refusal is
    # about the missing human and not about a missing key.
    assert admitted.reason_code == ReasonCode.APPROVAL_REQUIRED.value


async def test_a_denied_capability_and_an_unlisted_one_are_told_apart() -> None:
    """One code per fix. Both refusals are DENY; they are not the same problem.

    ``CAPABILITY_DENIED`` means someone put the capability on a deny list and an operator has
    to take it off. ``CAPABILITY_NOT_ALLOWED`` means the task was never authorised with it
    and the *task* has to change. Collapsing them would send half the operators to the wrong
    file. The denial reason is chosen in ``authorize``'s own order, so the code names the
    check that actually stopped the request rather than a later one that also would have.
    """

    _, snapshot, policy_denied = await setup_gate(explicitly_denied=("cap.search",))
    by_policy = policy_denied.gate_capability(
        task_row(),
        capability_row("cap.search"),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    assert by_policy.reason_code == ReasonCode.CAPABILITY_DENIED.value

    _, plain_snapshot, gate = await setup_gate()
    by_task = gate.gate_capability(
        task_row(allowed=("cap.search",), denied=("cap.search",)),
        capability_row("cap.search"),
        snapshot=plain_snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    assert by_task.reason_code == ReasonCode.CAPABILITY_DENIED.value

    unlisted = gate.gate_capability(
        task_row(allowed=("cap.other",)),
        capability_row("cap.search"),
        snapshot=plain_snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    assert unlisted.reason_code == ReasonCode.CAPABILITY_NOT_ALLOWED.value

    disabled = gate.gate_capability(
        task_row(),
        capability_row("cap.search", enabled=False),
        snapshot=plain_snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    assert disabled.reason_code == ReasonCode.CAPABILITY_DISABLED.value

    ungranted = gate.gate_capability(
        task_row(),
        capability_row("cap.search", required_permissions=("operator.admin",)),
        snapshot=plain_snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    assert ungranted.reason_code == ReasonCode.POLICY_INCOMPATIBLE.value


async def test_risk_above_the_allowed_class_is_incompatible() -> None:
    """A task riskier than its node's contract admits is refused, on the mapped ladder.

    The comparison crosses two vocabularies — registry §5.3's ``RiskClass`` on the node and
    v0.1's ``RiskLevel`` on the task — and goes through ``risk_level_for`` and ``RISK_RANK``.
    Comparing the enum members directly is the bug the mapping exists to prevent: ``StrEnum``
    compares alphabetically, which makes ``"CRITICAL" < "HIGH" < "LOW"`` true and would admit
    exactly the wrong tasks.

    Both directions are asserted. A gate that always refused would satisfy the first half.
    """

    _, snapshot, gate = await setup_gate()

    def gated(*, allowed: RiskClass, requested: RiskLevel) -> CompatibilityDecision:
        return gate.gate_risk(
            node_contract(allowed_risk_class=allowed),
            task_row(risk_level=requested),
            snapshot=snapshot,
            workspace_id=WORKSPACE_ID,
            project_id=PROJECT_ID,
            clock=clock,
        )

    above = gated(allowed=RiskClass.LOW_DIGITAL, requested=RiskLevel.HIGH)
    assert above.status is CompatibilityStatus.INCOMPATIBLE
    assert above.reason_code == ReasonCode.POLICY_INCOMPATIBLE.value
    assert above.rule_id == RULE_RISK_CLASS

    equal = gated(allowed=RiskClass.LOW_DIGITAL, requested=RiskLevel.LOW)
    assert equal.status is CompatibilityStatus.COMPATIBLE

    below = gated(allowed=RiskClass.HIGH_DIGITAL, requested=RiskLevel.MEDIUM)
    assert below.status is CompatibilityStatus.COMPATIBLE

    # SIMULATION maps to HIGH and not to MEDIUM (registry §5.2): a simulated physical action
    # can be wrong in ways a digital one cannot, so a CRITICAL task is still refused.
    simulation = gated(allowed=RiskClass.SIMULATION, requested=RiskLevel.CRITICAL)
    assert simulation.status is CompatibilityStatus.INCOMPATIBLE


async def test_a_disabled_principal_or_a_foreign_workspace_may_not_route() -> None:
    """Authority does not end at the HTTP boundary, and tenancy is not decorative.

    Two refusals in one gate because both are about the same thing — whether this principal
    may act here — and both would otherwise be nobody's job. A ``DISABLED`` principal is
    refused for the reason AC3-ID-05 refuses one at capability invocation: a run whose
    principal was disabled mid-flight must not keep routing on the strength of having started.
    A principal entitled to one workspace routing in another is the tenancy leak §10.1 exists
    to prevent, and it is checkable only because the entitlement and the target are two
    parameters rather than one.
    """

    _, snapshot, gate = await setup_gate()

    admitted = gate.gate_permissions(
        PRINCIPAL,
        WORKSPACE_ID,
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    assert admitted.status is CompatibilityStatus.COMPATIBLE
    assert admitted.subject_type is SubjectType.CONFIGURATION
    assert admitted.subject_ref == PRINCIPAL.principal_id

    disabled = gate.gate_permissions(
        PrincipalRef(principal_id=PRINCIPAL.principal_id, status=PrincipalStatus.DISABLED),
        WORKSPACE_ID,
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    assert disabled.status is CompatibilityStatus.INCOMPATIBLE
    assert disabled.reason_code == ReasonCode.POLICY_INCOMPATIBLE.value

    foreign = gate.gate_permissions(
        PRINCIPAL,
        OTHER_WORKSPACE_ID,
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    assert foreign.status is CompatibilityStatus.INCOMPATIBLE
    assert foreign.rule_id == RULE_PERMISSION_WORKSPACE


async def test_a_gate_decision_carries_the_derived_identity_a_replay_can_recompute() -> None:
    """A gate seals the same record shape a rule seals, from the same digest input.

    Derived here by hand from the documented parts rather than compared to the gate's own
    output twice, because a test that asked the gate for the id and then asked it again would
    be green through any change to the derivation. Two evaluations of one gate against one
    snapshot must produce one identity, and a decision must survive the store.
    """

    store, snapshot, gate = await setup_gate()
    decision = gate.gate_capability(
        task_row(allowed=("cap.other",)),
        capability_row("cap.search"),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )

    assert decision.contract_id == derived_id(
        "compatibility_decision",
        RULE_CAPABILITY_AUTHORIZED,
        RULE_VERSION,
        SubjectType.TOOL.value,
        "cap.search",
        snapshot.capability_registry_snapshot_id,
        snapshot.available_runtime_snapshot_id,
        snapshot.connection_availability_snapshot_id,
        snapshot.policy_snapshot_id,
        CompatibilityStatus.INCOMPATIBLE.value,
        ReasonCode.CAPABILITY_NOT_ALLOWED.value,
    )
    assert decision.labels == {"policy_snapshot_id": snapshot.policy_snapshot_id}
    assert decision.rule_version == RULE_VERSION
    assert [ref.content_digest for ref in decision.evidence_refs] == [
        snapshot.capability_registry_snapshot_id,
        snapshot.available_runtime_snapshot_id,
        snapshot.connection_availability_snapshot_id,
    ]

    await store.put_compatibility_decision(decision)
    # Asserted against what the store returns, never against the object handed to it.
    stored = await store.list_compatibility_decisions(workspace_id=WORKSPACE_ID)
    assert stored == [decision]
    assert is_gate_decision(stored[0]) is True
    assert stored[0].reason_code == ReasonCode.CAPABILITY_NOT_ALLOWED.value


# --------------------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------------------


def base_snapshot() -> RoutingSnapshot:
    """A snapshot built by hand, so each of the four ids can be moved one at a time."""

    return RoutingSnapshot(
        capability_registry_snapshot_id=digest("registry"),
        available_runtime_snapshot_id=digest("runtimes"),
        connection_availability_snapshot_id=digest("connections"),
        policy_snapshot_id=f"{POLICY_ID}@1.0.0",
        capabilities=(),
        skills=(),
        plugins=(),
        verifier_ids=(),
        runtime_health=(),
        mcp_server_states=(),
        policy=CapabilityPolicy(
            policy_id=POLICY_ID, version="1.0.0", description="", created_at=FIXED_TIME
        ),
        fallback_bundle_digest="fallback-bundle/0",
        taken_at=FIXED_TIME,
    )


async def test_routing_request_id_changes_when_any_snapshot_id_changes() -> None:
    """§8.2's identity covers all four snapshot ids, not just the registry digest.

    Each of the four is moved on its own and each must produce a different request id. The
    policy id is the one that matters most and is the one it is easiest to omit: it is a
    label rather than a hex digest, so a derivation that collected "the digests" would drop
    it silently and every decision made under a changed policy would replay to the receipt
    taken under the old one. Removing ``policy_snapshot_id`` from the derivation fails this
    test and nothing else.
    """

    snapshot = base_snapshot()

    def request_id(candidate: RoutingSnapshot, **overrides: object) -> str:
        arguments: dict[str, object] = {
            "workspace_router_version": "router/1",
            "project_adapter_version": None,
            "mode": RoutingMode.BASELINE_ONLY,
        }
        arguments.update(overrides)
        return routing_request_id(
            digest("node-contract"),
            candidate,
            str(arguments["workspace_router_version"]),
            arguments["project_adapter_version"],  # type: ignore[arg-type]
            arguments["mode"],  # type: ignore[arg-type]
        )

    baseline = request_id(snapshot)
    assert has_prefix(baseline, "routing_request")
    # Same inputs, same id: §8.2's whole guarantee.
    assert request_id(base_snapshot()) == baseline

    moved = {
        field: request_id(dataclasses.replace(snapshot, **{field: digest(field)}))
        for field in (
            "capability_registry_snapshot_id",
            "available_runtime_snapshot_id",
            "connection_availability_snapshot_id",
            "policy_snapshot_id",
        )
    }
    assert len(set(moved.values()) | {baseline}) == 5, moved

    # The other three inputs move it too: who routed, with which adapter, under which mode.
    assert request_id(snapshot, workspace_router_version="router/2") != baseline
    assert request_id(snapshot, project_adapter_version="adapter/1") != baseline
    assert request_id(snapshot, mode=RoutingMode.SHADOW) != baseline
    # "No adapter" and an adapter literally named the empty string are different worlds.
    assert request_id(snapshot, project_adapter_version="") != baseline
    # And the thing being routed: a different node contract is a different request.
    assert routing_request_id(
        digest("another-node"),
        snapshot,
        "router/1",
        None,
        RoutingMode.BASELINE_ONLY,
    ) != baseline


async def test_execution_instance_id_is_deterministic_per_attempt() -> None:
    """ADR-041: one attempt at one node is one routable action, and it has one id.

    Determinism is asserted first because everything else depends on it — a receipt, a
    failure event and an experience record are all keyed by this value, and two computations
    that disagreed would file one attempt under two identities. Then the three ways it must
    *not* collide: a different attempt is a different action, and so are a different node and
    a different run.
    """

    first = execution_instance_id("run_A", "review", 1)
    assert first == execution_instance_id("run_A", "review", 1)
    assert first != execution_instance_id("run_A", "review", 2)
    assert first != execution_instance_id("run_A", "implement", 1)
    assert first != execution_instance_id("run_B", "review", 1)
    assert len(first) <= 64

    # Zero is refused rather than accepted as "the try before the first": an attempt counter
    # that starts at zero would give the first attempt an identity a retry could reuse.
    with pytest.raises(ValueError, match="counted from 1"):
        execution_instance_id("run_A", "review", 0)


async def test_the_workspace_of_a_run_is_its_principals_membership_or_the_local_default(
) -> None:
    """ADR4-M1-001, both branches, against a real store rather than a stub.

    ``Project`` has no workspace column, so the workspace a decision is filed under is derived
    from the principal's memberships and falls back to the local workspace the identity
    service seeds. A run with no principal and a run whose principal joined nothing take the
    same fallback, which is what makes single-user local operation work at all.
    """

    store = MemoryStore()
    membered = Run(
        run_id="run_membered",
        task_id=TASK_ID,
        project_id=PROJECT_ID,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
        principal_id=PRINCIPAL.principal_id,
    )
    assert await workspace_for_run(store, membered) == LOCAL_WORKSPACE_ID

    await store.upsert_workspace(WorkspaceEntity(workspace_id=WORKSPACE_ID, name="Team"))
    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id="wsm_m1_gates",
            workspace_id=WORKSPACE_ID,
            principal_id=PRINCIPAL.principal_id,
            role=WorkspaceRole.OWNER,
        )
    )
    assert await workspace_for_run(store, membered) == WORKSPACE_ID

    anonymous = membered.model_copy(update={"run_id": "run_anon", "principal_id": None})
    assert await workspace_for_run(store, anonymous) == LOCAL_WORKSPACE_ID

    # And the author of a decision is the run's principal, never a fabricated one.
    assert principal_ref_for_run(membered) == PrincipalRef(
        principal_id=PRINCIPAL.principal_id, status=PrincipalStatus.ACTIVE
    )
    with pytest.raises(ValueError, match="names no principal"):
        principal_ref_for_run(anonymous)


async def test_the_verification_spec_builder_is_idempotent_and_sealed() -> None:
    """ADR-044: the same node freezes the same spec, byte for byte, every time.

    Idempotence here is not a nicety. A node contract pins its spec by ``content_hash``, and
    its own ``immutable_hash`` covers that pin, so a spec whose digest moved between two
    freezes would move the node's identity and break §8.2's "identical inputs, identical
    receipt" for a node nobody edited.

    Three properties. The document is *sealed* — ``content_hash`` is computed, not blank.
    The identity is *derived from the body*, so a re-put is a no-op on the append-only store
    rather than a second row or a rejection. And a changed policy is a *different* spec, so
    idempotence never becomes "quietly reuse the old rules". Making the builder mint
    ``new_id`` instead flips the second and third of those immediately.
    """

    store = MemoryStore()
    await store.create_project(
        Project(
            project_id=PROJECT_ID,
            name="m1-gates-spec",
            repository_path=Path("/tmp/accretion-m1-spec"),
            created_at=FIXED_TIME,
        )
    )
    builder = VerificationSpecBuilder(created_by=PRINCIPAL, workspace_id=WORKSPACE_ID)
    task = task_row(required_outputs=({"path": "README.md", "kind": "file"},))
    policy = AcceptancePolicy(
        policy_id="acp_m1_gates",
        required_verifiers=["git-diff", "output-contract"],
        score_thresholds={"coverage": 0.8},
        created_at=FIXED_TIME,
    )

    spec = builder.build(task, policy)
    again = builder.build(task, policy)

    assert spec == again
    assert spec.content_hash == again.content_hash
    assert spec.content_hash, "the spec must be sealed, not left with a blank digest"
    assert has_prefix(spec.contract_id, "verification_spec")

    # One REQUIRED claim per verifier and one per required output, and every one of them
    # REQUIRED: a SUPPORTING claim cannot block acceptance, which would make the freeze a
    # report rather than a verification.
    assert [claim.claim_id for claim in spec.claims] == [
        "verifier.git-diff",
        "verifier.output-contract",
        "output.README.md",
    ]
    assert all(claim.criticality is Criticality.REQUIRED for claim in spec.claims)
    assert [metric.metric_id for metric in spec.metrics] == ["coverage"]
    assert spec.accepted_outcomes == [VerificationState.PASS]

    await store.put_verification_spec(spec)
    assert await store.put_verification_spec(again) == spec
    assert await store.list_verification_specs(workspace_id=WORKSPACE_ID) == [spec]

    # A different policy is a different spec and therefore a second row, never a silent
    # overwrite of the rules a node was already frozen against.
    stricter = builder.build(
        task, policy.model_copy(update={"score_thresholds": {"coverage": 0.95}})
    )
    assert stricter.contract_id != spec.contract_id
    await store.put_verification_spec(stricter)
    assert len(await store.list_verification_specs(workspace_id=WORKSPACE_ID)) == 2

    # Nothing to verify is not an empty spec; it is a node that may not be routed.
    with pytest.raises(ValueError, match="nothing to verify"):
        builder.build(task_row(), AcceptancePolicy(policy_id="acp_empty", created_at=FIXED_TIME))


async def test_the_frozen_spec_is_what_a_node_contract_can_pin() -> None:
    """The builder's output is usable where ADR-044 says it must be used.

    A spec that could not be referenced by a ``NodeContract`` would be a freeze of nothing.
    Asserted by building the reference from the spec's own identity and digest and checking
    the node seals over it, which is the arithmetic that makes "verification frozen before
    routing" a fact about hashes rather than a rule someone has to remember.
    """

    builder = VerificationSpecBuilder(created_by=PRINCIPAL, workspace_id=WORKSPACE_ID)
    spec: VerificationSpec = builder.build(
        task_row(),
        AcceptancePolicy(policy_id="acp_m1", required_verifiers=["git-diff"],
                         created_at=FIXED_TIME),
    )
    contract = node_contract().model_copy(
        update={
            "verification_spec_ref": VerificationSpecRef(
                verification_spec_id=spec.contract_id, content_hash=spec.content_hash
            ),
            "immutable_hash": "",
            "content_hash": "",
        }
    )
    resealed = NodeContract.model_validate(contract.model_dump(mode="json"))
    assert resealed.verification_spec_ref.content_hash == spec.content_hash
    assert resealed.immutable_hash
