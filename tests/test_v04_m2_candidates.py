from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime

from accretion.contracts import (
    AuthMode,
    Capability,
    CapabilityBackend,
    CapabilityBinding,
    CapabilityBindingBackend,
    CapabilityPolicy,
    CapabilityResolutionOutcome,
    GraphNodeKind,
    MetaSkill,
    PrincipalRef,
    PrincipalStatus,
    Provider,
    ResolvedCapability,
    RiskLevel,
    Run,
    RunState,
    RuntimeHealth,
    RuntimeStatus,
    Task,
    TaskEnvelope,
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
    EnvironmentBinding,
    ExecutionConfiguration,
    ModelBinding,
    NodeContract,
    ObjectiveContractRef,
    ResourceBudget,
    RiskClass,
    ToolBinding,
    VerificationSpecRef,
    VerifierBinding,
)
from accretion.governance import CapabilityPolicyEngine
from accretion.ids import derived_id
from accretion.persistence.store import MemoryStore
from accretion.resolver import CapabilityResolver
from accretion.routing.candidates import CandidateBuilder
from accretion.routing.catalog import (
    ConfigurationCatalog,
    ConfigurationCatalogFactory,
    FallbackBundle,
    RuntimeModelOption,
    ToolCatalogEntry,
    VerifierCatalogEntry,
)
from accretion.routing.compatibility import CompatibilityEngine
from accretion.routing.gates import PolicyGate
from accretion.routing.snapshot import RegistrySnapshotBuilder, RoutingSnapshot
from accretion.runtimes.fake import FakeRuntime
from accretion.verifiers.output_contract import OutputContractVerifier
from accretion.verifiers.registry import VerifierRegistry

NOW = datetime(2026, 9, 6, 1, 0, tzinfo=UTC)
WORKSPACE = "wks_m2_selector"
PROJECT = "prj_m2_selector"
PRINCIPAL = PrincipalRef(
    principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    display_name="M2 selector",
    status=PrincipalStatus.ACTIVE,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def objective_ref(*, floor: float = 0.5) -> ObjectiveContractRef:
    return ObjectiveContractRef(  # type: ignore[call-arg]
        contract_id="objective-ref-m2-selector",
        created_at=NOW,
        created_by=PRINCIPAL,
        workspace_id=WORKSPACE,
        project_id=PROJECT,
        objective_contract_id=derived_id("objective_contract", "m2-selector"),
        revision=1,
        objective_contract_hash=digest("objective"),
        verified_success_floor=floor,
        utility_profile_id="quality-cost-latency-1-.25-.15",
        risk_policy=PolicyRef(
            policy_id="risk-policy", version="1.0.0", content_digest=digest("risk")
        ),
        approved_by=PRINCIPAL,
        approved_at=NOW,
    )


def node_contract(*, required: tuple[str, ...] = ("cap.search",)) -> NodeContract:
    return NodeContract(  # type: ignore[call-arg]
        contract_id=derived_id("node_contract", "m2-selector", *required),
        created_at=NOW,
        created_by=PRINCIPAL,
        workspace_id=WORKSPACE,
        project_id=PROJECT,
        objective_contract_ref=objective_ref(),
        node_id="agent",
        run_graph_id="rgr_m2_selector",
        graph_revision=1,
        execution_instance_id="execution-one",
        objective="route the fixture node",
        node_kind=GraphNodeKind.AGENT,
        required_capabilities=[
            CapabilityRequirement(
                capability=CapabilityRef(
                    capability_id=capability_id, capability_version="1.0.0"
                ),
                version_range=">=1.0.0",
                required_scope="read",
            )
            for capability_id in required
        ],
        allowed_risk_class=RiskClass.LOW_DIGITAL,
        resource_cap=ResourceBudget(
            maximum_cost="1.00",
            maximum_latency_ms=60_000,
            maximum_attempts=2,
            maximum_tool_calls=10,
        ),
        verification_spec_ref=VerificationSpecRef(
            verification_spec_id="vsp_m2_selector", content_hash=digest("spec")
        ),
        failure_policy_ref=PolicyRef(
            policy_id="failure", version="1.0.0", content_digest=digest("failure")
        ),
    )


def task(*, allowed: tuple[str, ...] = ("cap.search",)) -> Task:
    return Task(
        envelope=TaskEnvelope(
            task_id="tsk_m2_selector",
            project_id=PROJECT,
            objective="route",
            risk_level=RiskLevel.LOW,
            requested_skills=["skill.review"],
            allowed_capabilities=list(allowed),
        ),
        created_at=NOW,
    )


def capability(capability_id: str = "cap.search") -> ResolvedCapability:
    row = Capability(
        capability_id=capability_id,
        version="1.0.0",
        description="real registry row",
        backend=CapabilityBackend.PYTHON,
        created_at=NOW,
    )
    binding = CapabilityBinding(
        binding_id=f"binding-{capability_id}",
        capability_id=capability_id,
        connector_id="local-python",
        backend=CapabilityBindingBackend(
            type=CapabilityBackend.PYTHON, tool_name=f"tool.{capability_id}"
        ),
        created_at=NOW,
    )
    return ResolvedCapability(
        capability=row,
        outcome=CapabilityResolutionOutcome.NO_CONNECTOR_REQUIRED,
        binding=binding,
    )


ENVIRONMENT = EnvironmentBinding(
    environment=EnvironmentRef(
        environment_id="local-worktree",
        image_digest=digest("local-worktree"),
        policy_profile="restricted",
    ),
    workspace_isolation="WORKTREE",
)
SKILL = SkillRef(
    skill_id="skill.review", version="1.0.0", package_digest=digest("skill.review")
)
VERIFIER = VerifierCatalogEntry(
    verifier=VerifierRef(
        verifier_contract_id="output-contract", implementation_digest=digest("verifier")
    ),
    version="output-contract-v1",
)


def runtime_model(model: str = "fake-model") -> RuntimeModelOption:
    return RuntimeModelOption(
        runtime=RuntimeRef(
            runtime_id="runtime_fake",
            adapter_version="fake-p2-v1",
            provider=Provider.FAKE,
            model=model,
            capability_profile_digest=digest("fake-profile"),
        ),
        model=ModelBinding(model_id=model, provider=Provider.FAKE),
    )


def tool_entry(resolved: ResolvedCapability) -> ToolCatalogEntry:
    assert resolved.binding is not None
    assert resolved.binding.backend.tool_name is not None
    return ToolCatalogEntry(
        binding=ToolBinding(
            capability=CapabilityRef(
                capability_id=resolved.capability.capability_id,
                capability_version=resolved.capability.version,
            ),
            tool=ToolRef(
                tool_id=resolved.binding.backend.tool_name,
                implementation_digest=digest(resolved.binding.binding_id),
            ),
            binding_id=resolved.binding.binding_id,
            binding_version=resolved.binding.schema_version,
        )
    )


def fallback_configuration(node: NodeContract, tool: ToolCatalogEntry) -> ExecutionConfiguration:
    option = runtime_model()
    return ExecutionConfiguration(  # type: ignore[call-arg]
        contract_id=derived_id("execution_configuration", "fallback"),
        created_at=NOW,
        created_by=PRINCIPAL,
        workspace_id=WORKSPACE,
        project_id=PROJECT,
        objective_contract_ref=node.objective_contract_ref,
        environment=ENVIRONMENT,
        runtime=option.runtime,
        model=option.model,
        tools=[tool.binding],
        skills=[SKILL],
        verifier=VerifierBinding(
            verifier=VERIFIER.verifier,
            version=VERIFIER.version,
            verification_spec_hash=node.verification_spec_ref.content_hash,
        ),
    )


def world(*, models: tuple[str, ...] = ("fake-model",), fallback: bool = True):
    node = node_contract()
    resolved = capability()
    tool = tool_entry(resolved)
    bundle = FallbackBundle((fallback_configuration(node, tool),) if fallback else ())
    catalog = ConfigurationCatalog(
        runtime_models=tuple(runtime_model(model) for model in models),
        tools=(tool,),
        skills=(SKILL,),
        verifiers=(VERIFIER,),
        environments=(ENVIRONMENT,),
        fallback_bundle=bundle,
    )
    policy = CapabilityPolicy(
        policy_id="local-capability-policy",
        version="1.0.0",
        description="test",
        created_at=NOW,
    )
    snapshot = RoutingSnapshot(
        capability_registry_snapshot_id=digest("capabilities"),
        available_runtime_snapshot_id=digest("runtimes"),
        connection_availability_snapshot_id=digest("connections"),
        policy_snapshot_id="local-capability-policy@1.0.0",
        capabilities=(resolved,),
        skills=(SKILL.skill_id,),
        plugins=(),
        verifier_ids=(VERIFIER.verifier.verifier_contract_id,),
        runtime_health=(
            RuntimeHealth(
                runtime_id="runtime_fake",
                provider=Provider.FAKE,
                status=RuntimeStatus.READY,
                auth_mode=AuthMode.LOCAL,
                runtime_version="fake-p2-v1",
                capabilities=["repeatable-calls"],
                observed_at=NOW,
            ),
        ),
        mcp_server_states=(),
        policy=policy,
        fallback_bundle_digest=bundle.digest,
        taken_at=NOW,
    )
    builder = CandidateBuilder(
        gate=PolicyGate(CapabilityPolicyEngine(set()), policy, created_by=PRINCIPAL),
        evaluator=CompatibilityEngine(created_by=PRINCIPAL),
        catalog=catalog,
        created_by=PRINCIPAL,
    )
    return node, snapshot, catalog, builder


def build(builder: CandidateBuilder, node: NodeContract, snapshot: RoutingSnapshot, row: Task):
    return builder.build(
        routing_request_id=derived_id("routing_request", "m2-selector"),
        node_contract=node,
        task=row,
        principal=PRINCIPAL,
        entitled_workspace_id=WORKSPACE,
        snapshot=snapshot,
        workspace_id=WORKSPACE,
        project_id=PROJECT,
        clock=lambda: NOW,
    )


def test_behaviorally_equivalent_tuples_are_deduplicated_by_configuration_hash() -> None:
    node, snapshot, _, builder = world(models=("fake-model", "fake-model"))
    result = build(builder, node, snapshot, task())

    assert len(result.candidates) == 1
    assert result.candidates[0].fallback_eligible
    assert len({item.configuration.configuration_hash for item in result.candidates}) == 1


def test_unknown_required_capability_is_never_invented() -> None:
    node, snapshot, _, builder = world()
    missing = node_contract(required=("cap.unknown",))
    result = build(builder, missing, snapshot, task(allowed=("cap.unknown",)))

    assert result.candidates == ()
    assert [item.reason_code for item in result.rejected] == ["REQUIREMENT_UNAVAILABLE"]


def test_policy_refusal_removes_candidate_before_joint_compatibility() -> None:
    node, snapshot, _, builder = world()
    result = build(builder, node, snapshot, task(allowed=()))

    assert result.candidates == ()
    assert result.rejected[0].reason_code == "CAPABILITY_NOT_ALLOWED"
    # Permission, risk, capability: the compatibility engine was never called after refusal.
    assert len(result.compatibility_decisions) == 3


def test_audited_fallback_survives_the_agent_beam() -> None:
    models = tuple(f"model-{index:02d}" for index in range(12)) + ("fake-model",)
    node, snapshot, _, builder = world(models=models)
    result = build(builder, node, snapshot, task())

    assert len(result.candidates) <= 8
    assert sum(item.fallback_eligible for item in result.candidates) == 1


def test_catalog_digest_changes_when_a_real_entry_changes() -> None:
    _, _, first, _ = world(models=("fake-model",), fallback=False)
    _, _, second, _ = world(models=("fake-model-v2",), fallback=False)

    assert first.digest != second.digest


def test_fallback_digest_must_match_the_exact_snapshot() -> None:
    node, snapshot, _, builder = world()
    mismatched = replace(snapshot, fallback_bundle_digest=digest("different-fallback"))
    result = build(builder, node, mismatched, task())

    assert result.candidates
    assert not any(item.fallback_eligible for item in result.candidates)


async def test_fake_baseline_factory_uses_only_registered_execution_surfaces() -> None:
    store = MemoryStore()
    policy = CapabilityPolicy(
        policy_id="local-capability-policy",
        version="1.0.0",
        description="factory policy",
        created_at=NOW,
    )
    await store.upsert_capability_policy(policy)
    await store.upsert_skill(
        MetaSkill(
            skill_id="skill.review",
            version="1.0.0",
            description="registered skill",
            instructions="review",
            created_at=NOW,
        )
    )
    runtime = FakeRuntime()
    verifier_registry = VerifierRegistry((OutputContractVerifier(),))
    row = task(allowed=())
    snapshot = await RegistrySnapshotBuilder(
        store,
        CapabilityResolver(store),
        {Provider.FAKE: runtime},
        verifier_registry,
    ).build(
        workspace_id=WORKSPACE,
        project_id=PROJECT,
        task=row,
        clock=lambda: NOW,
    )
    node = node_contract(required=())
    catalog = await ConfigurationCatalogFactory.build_fake_baseline(
        store,
        {Provider.FAKE: runtime},
        verifier_registry,
        run=Run(
            run_id="run_m2_selector",
            task_id=row.envelope.task_id,
            project_id=PROJECT,
            provider=Provider.FAKE,
            state=RunState.RUNNING,
            created_at=NOW,
            updated_at=NOW,
        ),
        task=row,
        node_contract=node,
        snapshot=snapshot,
        environment=ENVIRONMENT,
        created_by=PRINCIPAL,
    )

    assert len(catalog.runtime_models) == 1
    assert catalog.runtime_models[0].model.model_id == "fake-model"
    assert len(catalog.fallback_bundle.configurations) == 1
    fallback = catalog.fallback_bundle.configurations[0]
    assert fallback.runtime.runtime_id == (await runtime.health()).runtime_id
    assert fallback.skills[0].skill_id == "skill.review"
    assert fallback.verifier.verifier.verifier_contract_id == "output-contract"
