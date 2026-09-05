"""The deterministic compatibility engine (SDD §7.7, §9.1 stage 7; AC4-M1-006/007/008).

Everything here runs offline: a :class:`~accretion.persistence.store.MemoryStore`, the
repository's ``FakeRuntime`` for health, hand-written registry rows and a hand-written
verifier stub. No live provider, no database, no network. That is not only a speed
preference — the engine's whole claim is that it reads a frozen snapshot and nothing else,
and a test that reached a live service could not tell a snapshot read from a store read.

The proofs fall into four groups.

**The happy path is complete** (AC4-M1-006). A valid tuple produces one decision per layer of
registry §7.3's hierarchy *and* the joint ``CONFIGURATION`` decision, every one of them
``COMPATIBLE``, and every one of them survives a round trip through
``put_compatibility_decision``/``list_compatibility_decisions``. The joint subject is asserted
by name: a router that emitted six per-layer decisions and skipped stage 7 would pass a test
that only counted them.

**UNKNOWN is not a soft yes** (AC4-M1-007). A required capability the snapshot never saw is
``UNKNOWN``, ``is_compatible()`` is ``False``, and the joint decision is not ``COMPATIBLE``.
Writing eligibility as "not INCOMPATIBLE" would flip exactly this test and nothing else.

**The vocabulary is closed** (AC4-M1-008). The golden list is written out here, by hand, and
compared against ``ALL_REASON_CODES``. A test that imported the catalogue and compared it to
itself would pass through any rename.

**Every refusal path is exercised.** One test per resolver outcome the engine lifts, one per
new v0.4 code, and one that mutates the store after the snapshot was taken to prove the
engine never looks at it.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from accretion.contracts import (
    Capability,
    CapabilityBackend,
    CapabilityBinding,
    CapabilityBindingBackend,
    CapabilityPolicy,
    CapabilityResolutionOutcome,
    Connection,
    ConnectionScope,
    ConnectionStatus,
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
    EvidenceClass,
    McpServerDefinition,
    McpServerState,
    MetaPlugin,
    MetaSkill,
    PrincipalRef,
    PrincipalStatus,
    Project,
    Provider,
    Task,
    TaskEnvelope,
    VerificationContext,
    VerificationResult,
    VerificationStatus,
    VerificationTarget,
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
    Claim,
    CompatibilityDecision,
    CompatibilityStatus,
    Criticality,
    EnvironmentBinding,
    EnvironmentConstraint,
    ExecutionConfiguration,
    MetricOperator,
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
from accretion.ids import derived_id
from accretion.persistence.store import MemoryStore
from accretion.resolver import CapabilityResolver
from accretion.routing.compatibility import (
    RULE_CAPABILITY,
    RUNTIME_VERSION_ATTRIBUTE,
    CompatibilityEngine,
    LayerRequirement,
    eligible,
)
from accretion.routing.reasons import ALL_REASON_CODES, RULE_VERSION, ReasonCode
from accretion.routing.snapshot import (
    FALLBACK_BUNDLE_DIGEST_M1,
    RegistrySnapshotBuilder,
    RoutingSnapshot,
)
from accretion.runtimes.fake import FakeRuntime
from accretion.verifiers.registry import VerifierRegistry

WORKSPACE_ID = "wks_8G33T24F686H6EJPBHRSFYCC3C"
PROJECT_ID = "prj_8W5DH3HW6DPAFFPBHQ47R21DK9"
TASK_ID = "tsk_8W5DH3HW6DPAFFPBHQ47R21DKA"
POLICY_ID = "local-capability-policy"
FIXED_TIME = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
PRINCIPAL = PrincipalRef(
    principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    display_name="M1 compatibility test",
    status=PrincipalStatus.ACTIVE,
)


def digest(seed: str) -> str:
    """A stable 64-character digest for a reference field, keyed by a readable name."""

    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def clock() -> datetime:
    """The injected clock. A constant, so ``created_at`` cannot fork a replay's digest."""

    return FIXED_TIME


class StubVerifier:
    """A verifier that exists to be *registered*, which is all the M1 rules ask of one.

    Hand-written rather than mocked, per the repository's testing convention. ``verify`` is
    implemented rather than raising because a stub that cannot honour its own protocol is a
    stub that hides a wiring bug; ``calls`` records that M1 never invokes it, which is the
    point — compatibility asks whether a verifier is available, never what it would say.
    """

    verifier_id = "stub-verifier"
    verifier_version = "1.0.0"

    def __init__(self) -> None:
        self.calls = 0

    async def verify(
        self, target: VerificationTarget, context: VerificationContext
    ) -> VerificationResult:
        self.calls += 1
        return VerificationResult(
            verification_id="ver_stub",
            run_id="run_stub",
            target=target,
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            status=VerificationStatus.PASS,
        )


# --------------------------------------------------------------------------------------
# Registry fixtures. Built by hand rather than by a factory that hides which field matters:
# every negative-path test below flips exactly one of these values.
# --------------------------------------------------------------------------------------


def capability_row(
    capability_id: str, *, enabled: bool = True, version: str = "1.0.0"
) -> Capability:
    return Capability(
        capability_id=capability_id,
        version=version,
        description="M1 compatibility fixture",
        backend=CapabilityBackend.PYTHON,
        enabled=enabled,
        created_at=FIXED_TIME,
    )


def connector_row(
    connector_id: str,
    *,
    auth_type: ConnectorAuthType = ConnectorAuthType.NONE,
    default_scopes: list[str] | None = None,
) -> ConnectorDefinition:
    return ConnectorDefinition(
        connector_id=connector_id,
        name=connector_id,
        kind=ConnectorKind.LOCAL,
        auth_type=auth_type,
        default_scopes=default_scopes or [],
        created_at=FIXED_TIME,
    )


def binding_row(
    capability_id: str,
    connector_id: str,
    *,
    enabled: bool = True,
    backend: CapabilityBackend = CapabilityBackend.PYTHON,
    server_ref: str | None = None,
) -> CapabilityBinding:
    return CapabilityBinding(
        binding_id=f"capbind_{capability_id}_{connector_id}",
        capability_id=capability_id,
        connector_id=connector_id,
        backend=CapabilityBindingBackend(type=backend, server_ref=server_ref),
        enabled=enabled,
        created_at=FIXED_TIME,
    )


def connection_row(
    connection_id: str,
    connector_id: str,
    *,
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
    granted_scopes: list[str] | None = None,
    token_handle_ref: str | None = None,
    metadata: dict[str, str] | None = None,
) -> Connection:
    return Connection(
        connection_id=connection_id,
        connector_id=connector_id,
        workspace_id=WORKSPACE_ID,
        principal_id=None,
        scope=ConnectionScope.WORKSPACE,
        workspace_shareable=True,
        status=status,
        granted_scopes=granted_scopes or [],
        token_handle_ref=token_handle_ref,
        metadata=dict(metadata or {}),
        created_at=FIXED_TIME,
    )


def task_row() -> Task:
    return Task(
        envelope=TaskEnvelope(
            task_id=TASK_ID,
            project_id=PROJECT_ID,
            objective="route one node deterministically",
            requested_skills=["skill.review"],
            allowed_capabilities=["cap.search"],
        ),
        created_at=FIXED_TIME,
    )


async def setup_registry(
    *,
    capability_enabled: bool = True,
    capability_version: str = "1.0.0",
    binding_enabled: bool = True,
    connection_status: ConnectionStatus = ConnectionStatus.ACTIVE,
    connector_auth: ConnectorAuthType = ConnectorAuthType.NONE,
    connector_scopes: list[str] | None = None,
    granted_scopes: list[str] | None = None,
    mcp_backed: bool = False,
    mcp_state: McpServerState = McpServerState.READY,
    register_capability: bool = True,
    register_connection: bool = True,
    register_skill: bool = True,
    register_verifier: bool = True,
    token_handle_ref: str | None = None,
    connection_metadata: dict[str, str] | None = None,
) -> tuple[MemoryStore, RegistrySnapshotBuilder, CompatibilityEngine, FakeRuntime]:
    """Build the whole offline world one test needs, and return the four handles it drives.

    A module-local builder rather than a fixture, because there is no ``conftest.py`` in this
    repository and because every keyword here corresponds to exactly one negative path: a
    reader can see from the call site which single fact a test changed.
    """

    store = MemoryStore()
    await store.create_project(
        Project(
            project_id=PROJECT_ID,
            name="m1-compatibility",
            repository_path=Path("/tmp/accretion-m1"),
            created_at=FIXED_TIME,
        )
    )
    if register_capability:
        await store.upsert_capability(
            capability_row(
                "cap.search", enabled=capability_enabled, version=capability_version
            )
        )
        await store.upsert_connector_definition(
            connector_row(
                "conndef_search",
                auth_type=connector_auth,
                default_scopes=connector_scopes,
            )
        )
        await store.upsert_capability_binding(
            binding_row(
                "cap.search",
                "conndef_search",
                enabled=binding_enabled,
                backend=CapabilityBackend.MCP if mcp_backed else CapabilityBackend.PYTHON,
                server_ref="mcs_search" if mcp_backed else None,
            )
        )
        if register_connection:
            await store.upsert_connection(
                connection_row(
                    "conn_search",
                    "conndef_search",
                    status=connection_status,
                    granted_scopes=granted_scopes,
                    token_handle_ref=token_handle_ref,
                    metadata=connection_metadata,
                )
            )
    if mcp_backed:
        await store.upsert_mcp_server(
            McpServerDefinition(
                mcp_server_id="mcs_search",
                workspace_id=WORKSPACE_ID,
                connector_id="conndef_search",
                name="search-server",
                endpoint="https://mcp.example.invalid/search",
                owner_principal_id=PRINCIPAL.principal_id,
                enabled=mcp_state is not McpServerState.DISABLED,
                state=mcp_state,
            )
        )
    if register_skill:
        await store.upsert_skill(
            MetaSkill(
                skill_id="skill.review",
                version="1.0.0",
                description="review skill",
                instructions="review the change",
                created_at=FIXED_TIME,
            )
        )
    await store.upsert_plugin(
        MetaPlugin(
            plugin_id="plugin.reporting",
            version="1.0.0",
            checksum=digest("plugin.reporting"),
            allowlisted=True,
            created_at=FIXED_TIME,
        )
    )
    await store.upsert_capability_policy(
        CapabilityPolicy(
            policy_id=POLICY_ID,
            version="1.0.0",
            description="M1 compatibility fixture policy",
            created_at=FIXED_TIME,
        )
    )
    runtime = FakeRuntime()
    verifiers = VerifierRegistry([StubVerifier()] if register_verifier else [])
    builder = RegistrySnapshotBuilder(
        store,
        CapabilityResolver(store),
        {Provider.FAKE: runtime},
        verifiers,
        policy_id=POLICY_ID,
    )
    return store, builder, CompatibilityEngine(created_by=PRINCIPAL), runtime


# --------------------------------------------------------------------------------------
# Contract fixtures. `verification_spec` is a real sealed spec, so the spec-hash rule is
# compared against a digest something actually produced rather than a literal.
# --------------------------------------------------------------------------------------


def verification_spec() -> VerificationSpec:
    return VerificationSpec(
        contract_id=derived_id("verification_spec", "m1", "spec"),
        created_at=FIXED_TIME,
        created_by=PRINCIPAL,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        revision=1,
        claims=[
            Claim(
                claim_id="claim.builds",
                description="the change builds",
                criticality=Criticality.REQUIRED,
                required_evidence_types=[EvidenceClass.DIGITAL],
            )
        ],
        accepted_outcomes=[VerificationState.PASS],
    )


def objective_ref() -> ObjectiveContractRef:
    return ObjectiveContractRef(
        contract_id="objective-contract-ref-embedded",
        created_at=FIXED_TIME,
        created_by=PRINCIPAL,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        objective_contract_id=derived_id("objective_contract", "m1"),
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
    *,
    spec_hash: str,
    required_capability_ids: tuple[str, ...] = ("cap.search",),
    environment_constraints: tuple[EnvironmentConstraint, ...] = (),
    version_range: str = ">=1.0.0",
    required_scope: str = "read",
) -> NodeContract:
    from accretion.contracts import GraphNodeKind
    from accretion.contracts.routing import CapabilityRequirement

    return NodeContract(
        contract_id=derived_id("node_contract", "m1", "node"),
        created_at=FIXED_TIME,
        created_by=PRINCIPAL,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        objective_contract_ref=objective_ref(),
        node_id="node-1",
        run_graph_id="rgr_m1",
        graph_revision=1,
        execution_instance_id="node-1#1",
        objective="apply the reviewed change",
        node_kind=GraphNodeKind.AGENT,
        required_capabilities=[
            CapabilityRequirement(
                capability=CapabilityRef(capability_id=capability_id, capability_version="1.0.0"),
                version_range=version_range,
                required_scope=required_scope,
            )
            for capability_id in required_capability_ids
        ],
        environment_constraints=list(environment_constraints),
        allowed_risk_class=RiskClass.LOW_DIGITAL,
        resource_cap=ResourceBudget(
            maximum_cost="1.00",
            maximum_latency_ms=60_000,
            maximum_attempts=2,
            maximum_tool_calls=10,
        ),
        verification_spec_ref=VerificationSpecRef(
            verification_spec_id="vsp_m1", content_hash=spec_hash
        ),
        failure_policy_ref=PolicyRef(
            policy_id="failure-policy",
            version="1.0.0",
            content_digest=digest("failure-policy"),
        ),
    )


def execution_configuration(
    *,
    spec_hash: str,
    tool_capability_ids: tuple[str, ...] = ("cap.search",),
    skill_ids: tuple[str, ...] = ("skill.review",),
    verifier_id: str = "stub-verifier",
    runtime_id: str = "runtime_fake",
    adapter_version: str = FakeRuntime.adapter_version,
    provider: Provider = Provider.FAKE,
    model_provider: Provider = Provider.FAKE,
) -> ExecutionConfiguration:
    return ExecutionConfiguration(
        contract_id=derived_id("execution_configuration", "m1", "cfg"),
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
            runtime_id=runtime_id,
            adapter_version=adapter_version,
            provider=provider,
            model="fake-model",
            capability_profile_digest=digest("runtime-profile"),
        ),
        model=ModelBinding(model_id="fake-model", provider=model_provider),
        tools=[
            ToolBinding(
                capability=CapabilityRef(capability_id=capability_id, capability_version="1.0.0"),
                tool=ToolRef(
                    tool_id=f"tool.{capability_id}",
                    implementation_digest=digest(f"tool.{capability_id}"),
                ),
                binding_id=f"capbind_{capability_id}_conndef_search",
                binding_version="1.0.0",
            )
            for capability_id in tool_capability_ids
        ],
        skills=[
            SkillRef(skill_id=skill_id, version="1.0.0", package_digest=digest(skill_id))
            for skill_id in skill_ids
        ],
        verifier=VerifierBinding(
            verifier=VerifierRef(
                verifier_contract_id=verifier_id,
                implementation_digest=digest(verifier_id),
            ),
            version="1.0.0",
            verification_spec_hash=spec_hash,
        ),
    )


def by_subject(
    decisions: list[CompatibilityDecision], subject_type: SubjectType
) -> list[CompatibilityDecision]:
    return [decision for decision in decisions if decision.subject_type is subject_type]


# --------------------------------------------------------------------------------------
# AC4-M1-006
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M1-006")
async def test_every_complete_tuple_carries_a_joint_decision_per_layer_and_all_are_compatible(
) -> None:
    """One decision per registry §7.3 layer, plus stage 7's joint decision, all COMPATIBLE.

    The layer *sequence* is asserted, not just the set: SDD §9.1 orders the hierarchy, and a
    router that evaluated the verifier before the runtime would be checking a tuple it had not
    yet established could run. The joint ``CONFIGURATION`` subject is asserted by name, so
    dropping stage 7 fails here rather than passing a test that merely counted six decisions.
    """

    store, builder, engine, _ = await setup_registry()
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(spec_hash=spec.content_hash),
        node_contract=node_contract(spec_hash=spec.content_hash),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )

    assert [decision.subject_type for decision in decisions] == [
        SubjectType.ENVIRONMENT,
        SubjectType.RUNTIME,
        SubjectType.MODEL,
        SubjectType.TOOL,
        SubjectType.SKILL,
        SubjectType.VERIFIER,
        SubjectType.CONFIGURATION,
    ]
    assert all(decision.status is CompatibilityStatus.COMPATIBLE for decision in decisions)
    assert all(decision.reason_code == ReasonCode.COMPATIBLE.value for decision in decisions)
    assert all(decision.rule_version == RULE_VERSION for decision in decisions)
    assert eligible(decisions)

    for decision in decisions:
        await store.put_compatibility_decision(decision)
    stored = await store.list_compatibility_decisions(workspace_id=WORKSPACE_ID)

    # Asserted against what the store returns, never against the objects handed to it.
    assert {decision.contract_id for decision in stored} == {
        decision.contract_id for decision in decisions
    }
    assert SubjectType.CONFIGURATION in {decision.subject_type for decision in stored}
    assert all(decision.is_compatible() for decision in stored)


# --------------------------------------------------------------------------------------
# AC4-M1-007
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M1-007")
async def test_a_required_capability_absent_from_the_snapshot_is_unknown_and_unknown_is_ineligible(
) -> None:
    """SDD §7.7's MUST, proved on the only path that can produce ``UNKNOWN`` from a registry.

    The capability is bound by the configuration and required by the node, and the snapshot
    has simply never heard of it — the registry was not asked, rather than having said no.
    ``is_compatible()`` must be ``False`` and the joint decision must not be ``COMPATIBLE``.
    Rewriting eligibility as ``status is not INCOMPATIBLE`` flips exactly this test.
    """

    _, builder, engine, _ = await setup_registry(register_capability=False)
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(spec_hash=spec.content_hash),
        node_contract=node_contract(spec_hash=spec.content_hash),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )

    tool = by_subject(decisions, SubjectType.TOOL)[0]
    assert tool.status is CompatibilityStatus.UNKNOWN
    assert tool.reason_code == ReasonCode.COMPATIBILITY_UNKNOWN.value
    assert tool.is_compatible() is False

    joint = by_subject(decisions, SubjectType.CONFIGURATION)[0]
    assert joint.status is not CompatibilityStatus.COMPATIBLE
    assert joint.is_compatible() is False
    assert eligible(decisions) is False


@pytest.mark.acceptance("AC4-M1-006")
async def test_a_required_capability_the_tuple_never_bound_is_reported_by_the_joint_decision(
) -> None:
    """Coverage is a joint property: no layer can see a requirement that has no binding.

    Marked for AC4-M1-006 as well as read under 007, because the criterion says complete
    tuples must *pass joint validation* and a rule that validated nothing would pass the
    happy-path test above unchanged. This is the half that fails when stage 7 stops
    checking coverage: every per-layer decision here is COMPATIBLE and only the joint
    decision can see that a required capability was never bound.
    """

    _, builder, engine, _ = await setup_registry()
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(spec_hash=spec.content_hash, tool_capability_ids=()),
        node_contract=node_contract(spec_hash=spec.content_hash),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    joint = by_subject(decisions, SubjectType.CONFIGURATION)[0]
    assert joint.status is CompatibilityStatus.INCOMPATIBLE
    assert joint.reason_code == ReasonCode.CAPABILITY_UNAVAILABLE.value


async def test_an_unbound_requirement_the_snapshot_never_saw_is_unknown_not_unavailable() -> None:
    """The two coverage failures are different facts and carry different codes."""

    _, builder, engine, _ = await setup_registry(register_capability=False)
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(spec_hash=spec.content_hash, tool_capability_ids=()),
        node_contract=node_contract(spec_hash=spec.content_hash),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    joint = by_subject(decisions, SubjectType.CONFIGURATION)[0]
    assert joint.status is CompatibilityStatus.UNKNOWN
    assert joint.reason_code == ReasonCode.COMPATIBILITY_UNKNOWN.value


# --------------------------------------------------------------------------------------
# The declared requirement: `version_range` is enforced by stage 7, `required_scope` is not.
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M1-006")
async def test_a_bound_capability_below_the_declared_version_range_is_capability_unavailable(
) -> None:
    """A tuple binding a version the node excluded is not a complete tuple.

    The registry serves ``cap.search`` at 0.1.0, the node requires ``>=1.0.0`` and the tuple
    binds it anyway. Every per-layer decision is COMPATIBLE — the capability resolves, the
    runtime is READY, the verifier is registered — so only stage 7 can see that the version
    on offer is one the node already refused. Dropping the range comparison makes this the
    happy path again, which is why the assertion below is on the reason code and not merely
    on ineligibility.
    """

    _, builder, engine, _ = await setup_registry(capability_version="0.1.0")
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(spec_hash=spec.content_hash),
        node_contract=node_contract(spec_hash=spec.content_hash, version_range=">=1.0.0"),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )

    assert all(
        decision.status is CompatibilityStatus.COMPATIBLE
        for decision in decisions
        if decision.subject_type is not SubjectType.CONFIGURATION
    )
    joint = by_subject(decisions, SubjectType.CONFIGURATION)[0]
    assert joint.status is CompatibilityStatus.INCOMPATIBLE
    assert joint.reason_code == ReasonCode.CAPABILITY_UNAVAILABLE.value
    assert eligible(decisions) is False


async def test_a_bound_capability_inside_a_two_sided_range_is_compatible() -> None:
    """The range admits as well as refuses, and it orders numerically rather than as text.

    ``1.10.0`` is above ``1.9.0`` and below ``2.0.0``; string comparison gets the first of
    those backwards, so a rule that compared the versions as text would refuse this tuple.
    """

    _, builder, engine, _ = await setup_registry(capability_version="1.10.0")
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(spec_hash=spec.content_hash),
        node_contract=node_contract(
            spec_hash=spec.content_hash, version_range=">=1.9.0,<2.0.0"
        ),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    joint = by_subject(decisions, SubjectType.CONFIGURATION)[0]
    assert joint.status is CompatibilityStatus.COMPATIBLE
    assert joint.reason_code == ReasonCode.COMPATIBLE.value
    assert eligible(decisions) is True


async def test_a_version_range_the_rule_may_not_interpret_is_unknown_not_compatible() -> None:
    """A caret range belongs to an ecosystem's convention, not to this rule's vocabulary.

    ``^1.0.0`` would be satisfied under the npm reading and is not a range this module has
    been told how to expand. Both other answers are wrong: COMPATIBLE would admit a tuple on
    a convention nobody wrote down, and INCOMPATIBLE would refuse a working configuration
    while claiming to have checked it. SDD §7.7 makes UNKNOWN ineligible, so the tuple is
    still refused — but for the reason that is actually true.
    """

    _, builder, engine, _ = await setup_registry()
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(spec_hash=spec.content_hash),
        node_contract=node_contract(spec_hash=spec.content_hash, version_range="^1.0.0"),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    joint = by_subject(decisions, SubjectType.CONFIGURATION)[0]
    assert joint.status is CompatibilityStatus.UNKNOWN
    assert joint.reason_code == ReasonCode.COMPATIBILITY_UNKNOWN.value
    assert eligible(decisions) is False


async def test_a_required_scope_is_not_yet_refused_by_the_joint_rule() -> None:
    """Pins the M1 deferral of ``CapabilityRequirement.required_scope``, deliberately.

    The node demands the ``admin:write`` authority before ``cap.search`` may be bound and
    the connection behind it grants nothing at all, yet stage 7 admits the tuple today. That
    is not an accident: a scope is authority rather than availability, and the evidence that
    would settle it — a connection's granted scopes — is deliberately absent from
    :class:`RoutingSnapshot`, whose connection projection is ``(connection_id,
    connector_id, status)`` so that no credential material enters a reproducible digest.
    The check belongs to M1.2's ``PolicyGate.gate_permissions``.

    This test exists so the deferral cannot stay green by accident: the day the gate lands,
    the joint status changes and this test has to be rewritten rather than quietly passing.
    """

    _, builder, engine, _ = await setup_registry(granted_scopes=[])
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(spec_hash=spec.content_hash),
        node_contract=node_contract(spec_hash=spec.content_hash, required_scope="admin:write"),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    joint = by_subject(decisions, SubjectType.CONFIGURATION)[0]
    assert joint.status is CompatibilityStatus.COMPATIBLE
    assert eligible(decisions) is True


# --------------------------------------------------------------------------------------
# AC4-M1-008
# --------------------------------------------------------------------------------------

GOLDEN_REASON_CODES: tuple[str, ...] = (
    "COMPATIBLE",
    "CAPABILITY_DENIED",
    "CAPABILITY_NOT_ALLOWED",
    "CAPABILITY_UNAVAILABLE",
    "POLICY_INCOMPATIBLE",
    "PROTECTED_SIDE_EFFECT_STATE",
    "SKILL_OR_PLUGIN_UNAVAILABLE",
    "VERIFIER_INCOMPATIBLE",
    "VERIFIER_UNAVAILABLE",
    "ARCHITECTURE_MAJOR_INCOMPATIBLE",
    "CAPABILITY_DISABLED",
    "CONNECTION_REQUIRES_REAUTH",
    "MCP_SERVER_NOT_READY",
    "SCOPE_INSUFFICIENT",
    "RUNTIME_UNAVAILABLE",
    "RUNTIME_VERSION_OUT_OF_RANGE",
    "VERIFIER_SPEC_HASH_MISMATCH",
    "ENVIRONMENT_CONSTRAINT_UNMET",
    "COMPATIBILITY_UNKNOWN",
)
"""Every reason code ``compat-rules/1`` may emit, transcribed by hand.

Written out rather than imported on purpose: the catalogue is a *published* vocabulary that
persisted decisions point at, so renaming a member has to be a red test somewhere, and a test
that read ``ALL_REASON_CODES`` and compared it to itself would be green through any rename.
"""


@pytest.mark.acceptance("AC4-M1-008")
async def test_reason_codes_are_stable_screaming_snake_and_enumerated() -> None:
    """The catalogue is closed, spelled one way, and every rule path draws from it.

    Three assertions, because the criterion has three halves: the list has not changed, each
    code matches the pattern ``CompatibilityDecision.reason_code`` enforces, and the codes the
    engine actually emits are a subset of the list rather than free text that happens to be
    upper case. The last one is what stops the catalogue from being decorative.
    """

    assert ALL_REASON_CODES == GOLDEN_REASON_CODES
    for code in ALL_REASON_CODES:
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", code), f"{code!r} is not SCREAMING_SNAKE"

    emitted: set[str] = set()
    spec = verification_spec()

    # One decision per rule path the engine can take, gathered from the negative-path
    # registries below plus the happy path, so "every code a rule emits is in the golden
    # set" is proved over the rules rather than asserted about them.
    for kwargs in (
        {},
        {"capability_enabled": False},
        {"binding_enabled": False},
        {"connection_status": ConnectionStatus.REVOKED},
        {"register_capability": False},
        {"connector_auth": ConnectorAuthType.API_KEY, "register_connection": False},
        {"register_skill": False},
        {"register_verifier": False},
        {
            "connector_auth": ConnectorAuthType.API_KEY,
            "connector_scopes": ["search.read"],
            "granted_scopes": [],
        },
        {"mcp_backed": True, "mcp_state": McpServerState.AUTH_REQUIRED},
    ):
        _, builder, engine, _ = await setup_registry(**kwargs)  # type: ignore[arg-type]
        snapshot = await builder.build(
            workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
        )
        for configuration, contract in (
            (
                execution_configuration(spec_hash=spec.content_hash),
                node_contract(spec_hash=spec.content_hash),
            ),
            (
                execution_configuration(spec_hash=digest("another-spec")),
                node_contract(spec_hash=spec.content_hash),
            ),
            (
                execution_configuration(spec_hash=spec.content_hash, runtime_id="runtime_absent"),
                node_contract(spec_hash=spec.content_hash),
            ),
            (
                execution_configuration(
                    spec_hash=spec.content_hash, adapter_version="fake-p2-v9"
                ),
                node_contract(spec_hash=spec.content_hash),
            ),
            (
                execution_configuration(spec_hash=spec.content_hash),
                node_contract(
                    spec_hash=spec.content_hash,
                    environment_constraints=(
                        EnvironmentConstraint(
                            constraint_id="env.isolation",
                            attribute="environment.workspace_isolation",
                            operator=MetricOperator.EQ,
                            value="CONTAINER",
                            rationale="the node needs container isolation",
                        ),
                    ),
                ),
            ),
        ):
            for decision in engine.evaluate_joint(
                configuration=configuration,
                node_contract=contract,
                snapshot=snapshot,
                workspace_id=WORKSPACE_ID,
                project_id=PROJECT_ID,
                clock=clock,
            ):
                emitted.add(decision.reason_code)

    assert emitted <= set(GOLDEN_REASON_CODES), f"codes outside the catalogue: {emitted}"
    # Not a subset check alone: a rule set that emitted only COMPATIBLE would satisfy that.
    assert {
        "COMPATIBLE",
        "CAPABILITY_DISABLED",
        "CAPABILITY_UNAVAILABLE",
        "COMPATIBILITY_UNKNOWN",
        "CONNECTION_REQUIRES_REAUTH",
        "ENVIRONMENT_CONSTRAINT_UNMET",
        "MCP_SERVER_NOT_READY",
        "RUNTIME_UNAVAILABLE",
        "RUNTIME_VERSION_OUT_OF_RANGE",
        "SCOPE_INSUFFICIENT",
        "SKILL_OR_PLUGIN_UNAVAILABLE",
        "VERIFIER_SPEC_HASH_MISMATCH",
        "VERIFIER_UNAVAILABLE",
    } <= emitted


# --------------------------------------------------------------------------------------
# Determinism and replay
# --------------------------------------------------------------------------------------


async def test_the_same_snapshot_replays_byte_identical_decisions() -> None:
    """Two builds of an unchanged world, two evaluations, one answer — down to the digest.

    The strongest form of the claim: ``model_dump()`` equality element-wise, which covers
    ``contract_id``, ``content_hash``, ``created_at`` and ``evaluated_at``. A ``datetime.now``
    anywhere in the engine — in ``created_at``, which the header digest covers, or in the id
    derivation — turns this red and nothing else does.
    """

    store, builder, engine, _ = await setup_registry()
    spec = verification_spec()
    first_snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    second_builder = RegistrySnapshotBuilder(
        store,
        CapabilityResolver(store),
        {Provider.FAKE: FakeRuntime()},
        VerifierRegistry([StubVerifier()]),
        policy_id=POLICY_ID,
    )
    second_snapshot = await second_builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )

    assert (
        first_snapshot.capability_registry_snapshot_id
        == second_snapshot.capability_registry_snapshot_id
    )
    assert (
        first_snapshot.available_runtime_snapshot_id
        == second_snapshot.available_runtime_snapshot_id
    )
    assert (
        first_snapshot.connection_availability_snapshot_id
        == second_snapshot.connection_availability_snapshot_id
    )
    assert first_snapshot.policy_snapshot_id == second_snapshot.policy_snapshot_id

    def evaluate(snapshot: RoutingSnapshot) -> list[CompatibilityDecision]:
        return engine.evaluate_joint(
            configuration=execution_configuration(spec_hash=spec.content_hash),
            node_contract=node_contract(spec_hash=spec.content_hash),
            snapshot=snapshot,
            workspace_id=WORKSPACE_ID,
            project_id=PROJECT_ID,
            clock=clock,
        )

    first, second = evaluate(first_snapshot), evaluate(second_snapshot)
    assert len(first) == len(second)
    for left, right in zip(first, second, strict=True):
        assert left.model_dump() == right.model_dump()
        assert left.contract_id == right.contract_id
        assert left.content_hash == right.content_hash


async def test_a_changed_snapshot_derives_a_different_decision_id() -> None:
    """The negative control for replay: identity must move when the world moves.

    Without it, "the same snapshot replays identically" would also be satisfied by an id that
    ignored the snapshot entirely.
    """

    store, builder, engine, _ = await setup_registry()
    spec = verification_spec()
    before = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    await store.upsert_capability(capability_row("cap.extra"))
    after = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )

    assert before.capability_registry_snapshot_id != after.capability_registry_snapshot_id

    def joint(snapshot: RoutingSnapshot) -> CompatibilityDecision:
        return by_subject(
            engine.evaluate_joint(
                configuration=execution_configuration(spec_hash=spec.content_hash),
                node_contract=node_contract(spec_hash=spec.content_hash),
                snapshot=snapshot,
                workspace_id=WORKSPACE_ID,
                project_id=PROJECT_ID,
                clock=clock,
            ),
            SubjectType.CONFIGURATION,
        )[0]

    assert joint(before).contract_id != joint(after).contract_id


async def test_the_engine_reads_only_the_snapshot_so_a_later_store_change_cannot_move_a_decision(
) -> None:
    """Purity, proved by mutating the world the snapshot was taken from.

    Disabling the capability after the snapshot exists must change nothing: an engine that
    reached back to the store would now refuse, and SDD §8.3's "exact snapshot" requirement
    would be a comment rather than a property.
    """

    store, builder, engine, _ = await setup_registry()
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    before = engine.evaluate_joint(
        configuration=execution_configuration(spec_hash=spec.content_hash),
        node_contract=node_contract(spec_hash=spec.content_hash),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )

    # A capability row is immutable per ``(capability_id, version)``, so the registry is
    # changed the way it can be changed: a new, disabled revision of the same capability.
    await store.upsert_capability(
        Capability(
            capability_id="cap.search",
            version="2.0.0",
            description="a disabled successor",
            backend=CapabilityBackend.PYTHON,
            enabled=False,
            created_at=FIXED_TIME,
        )
    )

    after = engine.evaluate_joint(
        configuration=execution_configuration(spec_hash=spec.content_hash),
        node_contract=node_contract(spec_hash=spec.content_hash),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    assert [decision.model_dump() for decision in before] == [
        decision.model_dump() for decision in after
    ]


# --------------------------------------------------------------------------------------
# Secrets
# --------------------------------------------------------------------------------------


async def test_snapshot_ids_carry_no_secret() -> None:
    """A token anywhere on a connection must not reach a digest, a field or a repr.

    ``Connection`` carries ``token_handle_ref``, ``granted_scopes`` and a free-form
    ``metadata`` dict a connector may fill with anything. The snapshot's connection projection
    is three named fields, so none of that travels — and the assertion covers the dataclass
    ``repr`` as well as the four ids, because a snapshot is passed to a rule, logged in a
    traceback and pretty-printed in a debugger long before anyone hashes it.
    """

    token = "sk-live-9f3c1d7e-DO-NOT-PERSIST"
    _, builder, _, _ = await setup_registry(
        connector_auth=ConnectorAuthType.API_KEY,
        token_handle_ref=f"tkh_{token}",
        connection_metadata={"access_token": token, "refresh_token": f"{token}-refresh"},
    )
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )

    for snapshot_id in (
        snapshot.capability_registry_snapshot_id,
        snapshot.available_runtime_snapshot_id,
        snapshot.connection_availability_snapshot_id,
        snapshot.policy_snapshot_id,
    ):
        assert token not in snapshot_id

    rendered = repr(snapshot)
    assert token not in rendered
    assert "access_token" not in rendered
    assert "token_handle_ref" not in rendered

    # The assertions above are necessary and not sufficient: a digest of a secret does not
    # *contain* the secret, so a builder that hashed the whole connection row would pass them
    # while writing token material into a hash input an operator will one day want to
    # reproduce. The checkable form of the property is insensitivity — a world that differs
    # only in token material must produce the same four ids. Widening the projection to
    # `model_dump()` flips this and nothing else.
    _, plain_builder, _, _ = await setup_registry(connector_auth=ConnectorAuthType.API_KEY)
    plain = await plain_builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    assert (
        snapshot.connection_availability_snapshot_id
        == plain.connection_availability_snapshot_id
    )
    assert (
        snapshot.capability_registry_snapshot_id == plain.capability_registry_snapshot_id
    )
    assert snapshot.available_runtime_snapshot_id == plain.available_runtime_snapshot_id
    assert snapshot.policy_snapshot_id == plain.policy_snapshot_id


async def test_a_connection_status_change_still_moves_the_connection_snapshot_id() -> None:
    """The negative control for the secret test: the narrow projection is not an empty one.

    Without this, a builder that hashed nothing at all would pass the secret assertion.
    """

    store, builder, _, _ = await setup_registry()
    before = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    await store.upsert_connection(
        connection_row("conn_search", "conndef_search", status=ConnectionStatus.REVOKED)
    )
    after = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    assert (
        before.connection_availability_snapshot_id != after.connection_availability_snapshot_id
    )


# --------------------------------------------------------------------------------------
# One negative path per resolver outcome the engine lifts
# --------------------------------------------------------------------------------------


async def tool_decision(**kwargs: object) -> CompatibilityDecision:
    """Build the world with one fact changed and return the TOOL decision it produces."""

    _, builder, engine, _ = await setup_registry(**kwargs)  # type: ignore[arg-type]
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(spec_hash=spec.content_hash),
        node_contract=node_contract(spec_hash=spec.content_hash),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    return by_subject(decisions, SubjectType.TOOL)[0]


async def test_a_disabled_capability_is_incompatible_with_capability_disabled() -> None:
    """Resolver ``DISABLED``. Switched off is a definite no, never an UNKNOWN."""

    decision = await tool_decision(capability_enabled=False)
    assert decision.status is CompatibilityStatus.INCOMPATIBLE
    assert decision.reason_code == ReasonCode.CAPABILITY_DISABLED.value


async def test_a_capability_whose_bindings_are_all_disabled_is_capability_disabled() -> None:
    """The resolver's second ``DISABLED`` branch reaches the same code, not a second one."""

    decision = await tool_decision(binding_enabled=False)
    assert decision.status is CompatibilityStatus.INCOMPATIBLE
    assert decision.reason_code == ReasonCode.CAPABILITY_DISABLED.value


async def test_a_capability_with_no_usable_connection_is_capability_unavailable() -> None:
    """Resolver ``NO_CONNECTION``: the registry knows the capability, nothing can serve it.

    Distinct from ``CAPABILITY_DISABLED`` — nobody switched anything off — and distinct from
    ``COMPATIBILITY_UNKNOWN``, because the registry *was* asked and the answer was no.
    """

    decision = await tool_decision(
        connector_auth=ConnectorAuthType.API_KEY, register_connection=False
    )
    assert decision.status is CompatibilityStatus.INCOMPATIBLE
    assert decision.reason_code == ReasonCode.CAPABILITY_UNAVAILABLE.value


async def test_a_revoked_connection_is_incompatible_with_connection_requires_reauth() -> None:
    """Resolver ``REQUIRE_REAUTH`` by status: the credential itself has to be renewed."""

    decision = await tool_decision(connection_status=ConnectionStatus.REVOKED)
    assert decision.status is CompatibilityStatus.INCOMPATIBLE
    assert decision.reason_code == ReasonCode.CONNECTION_REQUIRES_REAUTH.value


async def test_a_usable_connection_short_of_a_scope_is_scope_insufficient() -> None:
    """The resolver's other ``REQUIRE_REAUTH`` branch, told apart structurally.

    Both branches return one outcome; the connection's own status is what distinguishes
    "renew this credential" from "this credential is fine but was never granted the scope",
    and an operator needs the difference. AC3-CON-03 forbids the scope widening silently, so
    the refusal must be visible rather than folded into the coarser code.
    """

    decision = await tool_decision(
        connector_auth=ConnectorAuthType.API_KEY,
        connector_scopes=["search.read"],
        granted_scopes=[],
    )
    assert decision.status is CompatibilityStatus.INCOMPATIBLE
    assert decision.reason_code == ReasonCode.SCOPE_INSUFFICIENT.value


async def test_an_mcp_backed_capability_behind_an_unready_server_is_mcp_server_not_ready() -> None:
    """The MCP readiness gate is told apart from a plain disablement, structurally."""

    decision = await tool_decision(mcp_backed=True, mcp_state=McpServerState.AUTH_REQUIRED)
    assert decision.status is CompatibilityStatus.INCOMPATIBLE
    assert decision.reason_code == ReasonCode.MCP_SERVER_NOT_READY.value


async def test_map_resolution_maps_a_capability_needing_no_connector_to_compatible() -> None:
    """``NO_CONNECTOR_REQUIRED`` is the v0.1/v0.2 compatibility guarantee and stays a yes."""

    store, builder, engine, _ = await setup_registry(register_capability=False)
    await store.upsert_capability(capability_row("cap.plain"))
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    resolved = snapshot.resolved("cap.plain")
    assert resolved is not None
    assert resolved.outcome is CapabilityResolutionOutcome.NO_CONNECTOR_REQUIRED
    decision = engine.map_resolution(
        resolved,
        subject_ref="cap.plain",
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    assert decision.status is CompatibilityStatus.COMPATIBLE
    assert decision.rule_id == RULE_CAPABILITY


# --------------------------------------------------------------------------------------
# The remaining layers
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC4-M1-006")
async def test_a_verifier_enforcing_another_spec_is_a_verifier_spec_hash_mismatch() -> None:
    """ADR-044's attack, at the joint layer where it is visible.

    Also marked for AC4-M1-006: joint *validation* is only proven by a tuple that every
    layer admits and stage 7 refuses. Neutering the spec-hash check into a rubber stamp
    leaves the happy-path 006 test green and turns this one red, which is the direction the
    criterion needs.

    The verifier is registered and available, so no per-layer rule can object; what is wrong
    is that this tuple answers a different node's question. §18.4 names substituting a weaker
    verifier as an attack, and the spec hash is what makes the substitution detectable.
    """

    _, builder, engine, _ = await setup_registry()
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(spec_hash=digest("weaker-spec")),
        node_contract=node_contract(spec_hash=spec.content_hash),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    verifier = by_subject(decisions, SubjectType.VERIFIER)[0]
    assert verifier.status is CompatibilityStatus.COMPATIBLE

    joint = by_subject(decisions, SubjectType.CONFIGURATION)[0]
    assert joint.status is CompatibilityStatus.INCOMPATIBLE
    assert joint.reason_code == ReasonCode.VERIFIER_SPEC_HASH_MISMATCH.value
    assert eligible(decisions) is False


async def test_an_unregistered_verifier_is_verifier_unavailable() -> None:
    _, builder, engine, _ = await setup_registry(register_verifier=False)
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(spec_hash=spec.content_hash),
        node_contract=node_contract(spec_hash=spec.content_hash),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    verifier = by_subject(decisions, SubjectType.VERIFIER)[0]
    assert verifier.status is CompatibilityStatus.INCOMPATIBLE
    assert verifier.reason_code == ReasonCode.VERIFIER_UNAVAILABLE.value


async def test_a_skill_that_is_neither_registered_nor_allow_listed_is_unavailable() -> None:
    _, builder, engine, _ = await setup_registry(register_skill=False)
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(spec_hash=spec.content_hash),
        node_contract=node_contract(spec_hash=spec.content_hash),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    skill = by_subject(decisions, SubjectType.SKILL)[0]
    assert skill.status is CompatibilityStatus.INCOMPATIBLE
    assert skill.reason_code == ReasonCode.SKILL_OR_PLUGIN_UNAVAILABLE.value


async def test_a_skill_contributed_by_an_allow_listed_plugin_is_available() -> None:
    """The P7 disjunction, lifted intact: a plugin makes its skill available.

    Dropping the ``or plugins`` half would refuse working configurations, and no other test
    here would notice.
    """

    _, builder, engine, _ = await setup_registry(register_skill=False)
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(
            spec_hash=spec.content_hash, skill_ids=("plugin.reporting",)
        ),
        node_contract=node_contract(spec_hash=spec.content_hash),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    skill = by_subject(decisions, SubjectType.SKILL)[0]
    assert skill.status is CompatibilityStatus.COMPATIBLE


async def test_a_runtime_the_snapshot_never_saw_is_runtime_unavailable() -> None:
    _, builder, engine, _ = await setup_registry()
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(
            spec_hash=spec.content_hash, runtime_id="runtime_absent"
        ),
        node_contract=node_contract(spec_hash=spec.content_hash),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    runtime = by_subject(decisions, SubjectType.RUNTIME)[0]
    assert runtime.status is CompatibilityStatus.INCOMPATIBLE
    assert runtime.reason_code == ReasonCode.RUNTIME_UNAVAILABLE.value


async def test_a_runtime_pinned_to_a_version_it_no_longer_reports_is_out_of_range() -> None:
    """The pin case: a ``RuntimeRef`` names one adapter version and the runtime moved."""

    _, builder, engine, _ = await setup_registry()
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(
            spec_hash=spec.content_hash, adapter_version="fake-p2-v9"
        ),
        node_contract=node_contract(spec_hash=spec.content_hash),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    runtime = by_subject(decisions, SubjectType.RUNTIME)[0]
    assert runtime.status is CompatibilityStatus.INCOMPATIBLE
    assert runtime.reason_code == ReasonCode.RUNTIME_VERSION_OUT_OF_RANGE.value


async def test_a_declared_runtime_version_range_governs_instead_of_the_pin() -> None:
    """The range case: when the node declares a range, the pin does not also have to hold.

    The two mechanisms are one rule, and the range is the more specific statement. A
    configuration pinned to an older adapter is admissible when the node asked for "at or
    above" and the observed runtime satisfies it.
    """

    _, builder, engine, _ = await setup_registry()
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    contract = node_contract(
        spec_hash=spec.content_hash,
        environment_constraints=(
            EnvironmentConstraint(
                constraint_id="runtime.min-version",
                attribute=RUNTIME_VERSION_ATTRIBUTE,
                operator=MetricOperator.GTE,
                value="fake-p2-v0",
                rationale="the node needs at least the v0 fake adapter",
            ),
        ),
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(
            spec_hash=spec.content_hash, adapter_version="fake-p2-v9"
        ),
        node_contract=contract,
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    runtime = by_subject(decisions, SubjectType.RUNTIME)[0]
    assert runtime.status is CompatibilityStatus.COMPATIBLE
    # The environment layer must not evaluate the same constraint a second time against a
    # configuration attribute it does not name.
    environment = by_subject(decisions, SubjectType.ENVIRONMENT)[0]
    assert environment.status is CompatibilityStatus.COMPATIBLE


async def test_an_unsatisfied_runtime_version_range_is_out_of_range() -> None:
    _, builder, engine, _ = await setup_registry()
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    contract = node_contract(
        spec_hash=spec.content_hash,
        environment_constraints=(
            EnvironmentConstraint(
                constraint_id="runtime.min-version",
                attribute=RUNTIME_VERSION_ATTRIBUTE,
                operator=MetricOperator.GTE,
                value="fake-p9-v0",
                rationale="the node needs a newer adapter than this one",
            ),
        ),
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(spec_hash=spec.content_hash),
        node_contract=contract,
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    runtime = by_subject(decisions, SubjectType.RUNTIME)[0]
    assert runtime.status is CompatibilityStatus.INCOMPATIBLE
    assert runtime.reason_code == ReasonCode.RUNTIME_VERSION_OUT_OF_RANGE.value


async def test_an_unmet_environment_constraint_is_incompatible() -> None:
    _, builder, engine, _ = await setup_registry()
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    contract = node_contract(
        spec_hash=spec.content_hash,
        environment_constraints=(
            EnvironmentConstraint(
                constraint_id="env.isolation",
                attribute="environment.workspace_isolation",
                operator=MetricOperator.EQ,
                value="CONTAINER",
                rationale="the node needs container isolation",
            ),
        ),
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(spec_hash=spec.content_hash),
        node_contract=contract,
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    environment = by_subject(decisions, SubjectType.ENVIRONMENT)[0]
    assert environment.status is CompatibilityStatus.INCOMPATIBLE
    assert environment.reason_code == ReasonCode.ENVIRONMENT_CONSTRAINT_UNMET.value


async def test_a_constraint_on_an_attribute_the_engine_cannot_resolve_is_unknown() -> None:
    """Fail closed. An attribute nothing can evaluate is not silently satisfied.

    The attribute table is closed on purpose; reflection over field names would let a
    constraint quietly start comparing whatever a future contract field happened to be
    called. The honest answer for an unresolvable attribute is ``UNKNOWN``, which SDD §7.7
    then forbids treating as compatible.
    """

    _, builder, engine, _ = await setup_registry()
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    contract = node_contract(
        spec_hash=spec.content_hash,
        environment_constraints=(
            EnvironmentConstraint(
                constraint_id="env.gpu",
                attribute="environment.gpu_count",
                operator=MetricOperator.GTE,
                value="1",
                rationale="the node needs a GPU this engine cannot observe",
            ),
        ),
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(spec_hash=spec.content_hash),
        node_contract=contract,
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    environment = by_subject(decisions, SubjectType.ENVIRONMENT)[0]
    assert environment.status is CompatibilityStatus.UNKNOWN
    assert environment.reason_code == ReasonCode.COMPATIBILITY_UNKNOWN.value
    assert eligible(decisions) is False


async def test_a_model_from_a_provider_no_ready_runtime_serves_is_runtime_unavailable() -> None:
    """A model is served by a runtime; a tuple that names two providers names no execution."""

    _, builder, engine, _ = await setup_registry()
    spec = verification_spec()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    decisions = engine.evaluate_joint(
        configuration=execution_configuration(
            spec_hash=spec.content_hash, model_provider=Provider.CLAUDE
        ),
        node_contract=node_contract(spec_hash=spec.content_hash),
        snapshot=snapshot,
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        clock=clock,
    )
    model = by_subject(decisions, SubjectType.MODEL)[0]
    assert model.status is CompatibilityStatus.INCOMPATIBLE
    assert model.reason_code == ReasonCode.RUNTIME_UNAVAILABLE.value


# --------------------------------------------------------------------------------------
# The snapshot builder's own rules
# --------------------------------------------------------------------------------------


async def test_a_snapshot_may_not_be_taken_for_a_task_from_another_project() -> None:
    """Otherwise every decision derived from it is filed under an unauthorised project."""

    _, builder, _, _ = await setup_registry()
    with pytest.raises(ValueError, match="belongs to project"):
        await builder.build(
            workspace_id=WORKSPACE_ID,
            project_id="prj_someone_else",
            task=task_row(),
            clock=clock,
        )


async def test_a_snapshot_refuses_to_exist_without_the_policy_it_was_taken_under() -> None:
    """A decision that cannot name its authority is not an auditable decision."""

    store, _, _, _ = await setup_registry()
    builder = RegistrySnapshotBuilder(
        store,
        CapabilityResolver(store),
        {Provider.FAKE: FakeRuntime()},
        VerifierRegistry([StubVerifier()]),
        policy_id="policy-that-was-never-seeded",
    )
    with pytest.raises(RuntimeError, match="is unavailable"):
        await builder.build(
            workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
        )


async def test_a_snapshot_reports_the_policy_identity_and_the_m1_fallback_bundle() -> None:
    _, builder, _, _ = await setup_registry()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    assert snapshot.policy_snapshot_id == f"{POLICY_ID}@1.0.0"
    assert snapshot.fallback_bundle_digest == FALLBACK_BUNDLE_DIGEST_M1
    assert snapshot.taken_at == FIXED_TIME
    assert snapshot.verifier_ids == ("stub-verifier",)
    assert snapshot.skills == ("skill.review",)
    assert snapshot.plugins == ("plugin.reporting",)


async def test_evaluate_refuses_a_subject_whose_requirement_is_missing() -> None:
    """A caller that asked the wrong question gets an error, not a decision about nothing."""

    _, builder, engine, _ = await setup_registry()
    snapshot = await builder.build(
        workspace_id=WORKSPACE_ID, project_id=PROJECT_ID, task=task_row(), clock=clock
    )
    with pytest.raises(ValueError, match="requires a skill reference"):
        engine.evaluate(
            subject_type=SubjectType.SKILL,
            subject_ref="skill.review",
            requirements=LayerRequirement(),
            snapshot=snapshot,
            workspace_id=WORKSPACE_ID,
            project_id=PROJECT_ID,
            clock=clock,
        )
