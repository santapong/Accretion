"""Integration evidence for the M2 routing service over the real MemoryStore."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NoReturn, cast

import pytest
from test_v04_m0_store import FIXTURE_PROJECT_ID, FIXTURE_WORKSPACE_ID, build

from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    AcceptancePolicy,
    AgentEvent,
    Principal,
    PrincipalRef,
    Project,
    Provider,
    Run,
    RunGraph,
    RunNode,
    RunState,
    RuntimeHealth,
    RuntimeStatus,
    Task,
    TaskEnvelope,
    WorkflowNodeSpec,
    WorkflowTemplate,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.contracts.refs import EnvironmentRef
from accretion.contracts.routing import (
    ConfigurationCandidate,
    DecisionType,
    EnvironmentBinding,
    NodeContract,
    RoutingContext,
    RoutingDecisionReceipt,
)
from accretion.governance import seed_governance
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.resolver import CapabilityResolver
from accretion.routing.catalog import (
    WORKSPACE_ROUTER_VERSION,
    ConfigurationCatalog,
    ConfigurationCatalogFactory,
    FallbackBundle,
)
from accretion.routing.errors import RoutingError
from accretion.routing.identity import routing_request_id
from accretion.routing.protocols import FrozenNode, RoutingMode
from accretion.routing.service import DefaultNodeRoutingService
from accretion.routing.snapshot import RegistrySnapshotBuilder, RoutingSnapshot
from accretion.runtimes.fake import FakeRuntime
from accretion.services.run_manager import RunManager
from accretion.templates import instantiate_run_graph, seed_templates
from accretion.verifiers.output_contract import OutputContractVerifier
from accretion.verifiers.registry import VerifierRegistry
from accretion.workspace import WorktreeManager

pytestmark = pytest.mark.asyncio

OPERATOR_ID = "usr_4CF33CQ2YNVSFEK71H8ETSCYE0"


async def _unused_catalog(
    frozen: FrozenNode, snapshot: RoutingSnapshot, run: Run, task: Task
) -> NoReturn:
    del frozen, snapshot, run, task
    raise AssertionError("this service test does not route a new decision")


class FailingEventStore(MemoryStore):
    """Inject failure after an amendment writes its rows but before publication."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_routing_event = False

    async def append_event(self, event: AgentEvent) -> AgentEvent:
        if self.fail_routing_event and event.native_type in {
            "accretion/routing/override",
            "accretion/routing/cancel",
        }:
            raise RuntimeError("injected event persistence failure")
        return await super().append_event(event)


class DriftedHealthRuntime(FakeRuntime):
    """Report a controlled post-routing health observation."""

    def __init__(self, **updates: object) -> None:
        super().__init__()
        self.updates = updates

    async def health(self) -> RuntimeHealth:
        health = await super().health()
        return health.model_copy(update=self.updates)


@dataclass(slots=True)
class SeededRouting:
    store: MemoryStore
    service: DefaultNodeRoutingService
    principal: PrincipalRef
    run: Run
    receipt: RoutingDecisionReceipt
    candidate: ConfigurationCandidate
    rejected_candidate: ConfigurationCandidate


@dataclass(slots=True)
class RoutableExecution:
    store: MemoryStore
    service: DefaultNodeRoutingService
    frozen: FrozenNode
    snapshot: RoutingSnapshot
    run: Run
    task: Task
    node: RunNode
    spec: WorkflowNodeSpec
    template: WorkflowTemplate
    policy: AcceptancePolicy


async def _routable_execution(
    tmp_path: Path,
    *,
    objective: str = "Route a deterministic fake-runtime node.",
) -> RoutableExecution:
    """Freeze a real planned graph node against the real snapshot/catalog stack."""

    store = MemoryStore()
    runtime = FakeRuntime()
    verifiers = VerifierRegistry((OutputContractVerifier(),))
    manager = RunManager(
        store=store,
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: runtime},
        limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
        live_providers_enabled=False,
        verifier_registry=verifiers,
    )
    await seed_templates(store)
    await seed_governance(store)
    principal = Principal(
        principal_id=new_id("principal"),
        issuer="test",
        subject="route-operator",
        display_name="Route Operator",
    )
    await store.upsert_principal(principal)
    workspace_id = new_id("workspace_entity")
    await store.upsert_workspace(WorkspaceEntity(workspace_id=workspace_id, name="Route workspace"))
    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=workspace_id,
            principal_id=principal.principal_id,
            role=WorkspaceRole.ADMIN,
        )
    )
    project = await manager.create_project("route project", tmp_path)
    task = await manager.create_task(
        project_id=project.project_id,
        objective=objective,
        task_patch={"risk_level": "LOW", "allowed_capabilities": []},
    )
    planning = await manager.get_task_planning(task.envelope.task_id)
    template = await store.get_workflow_template(planning.current_decision.selected_template_id)
    assert template is not None
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
        principal_id=principal.principal_id,
    )
    await store.create_run(run)
    graph = instantiate_run_graph(
        template,
        run_id=run.run_id,
        task_id=task.envelope.task_id,
        budgets=task.envelope.budgets,
    )
    await store.create_run_graph(graph)
    node = next(item for item in graph.nodes if item.kind.value == "AGENT")
    spec = next(item for item in template.nodes if item.key == node.key)
    policy = AcceptancePolicy(
        policy_id=new_id("acceptance_policy"),
        required_verifiers=["output-contract"],
        outcome_check="the output contract must pass",
    )
    await store.save_acceptance_policy(policy)
    snapshot_builder = RegistrySnapshotBuilder(
        store,
        CapabilityResolver(store),
        {Provider.FAKE: runtime},
        verifiers,
    )
    environment = EnvironmentBinding(
        environment=EnvironmentRef(
            environment_id="local-test",
            image_digest=hashlib.sha256(b"local-test").hexdigest(),
            policy_profile="restricted",
        ),
        workspace_isolation="WORKTREE",
    )
    principal_ref = PrincipalRef(
        principal_id=principal.principal_id,
        display_name=principal.display_name,
        status=principal.status,
    )

    async def catalog_factory(
        frozen: FrozenNode, snapshot: RoutingSnapshot, catalog_run: Run, catalog_task: Task
    ) -> ConfigurationCatalog:
        return await ConfigurationCatalogFactory.build_fake_baseline(
            store,
            {Provider.FAKE: runtime},
            verifiers,
            run=catalog_run,
            task=catalog_task,
            node_contract=frozen.node_contract,
            snapshot=snapshot,
            environment=environment,
            created_by=principal_ref,
        )

    service = DefaultNodeRoutingService(
        store=store,
        snapshots=snapshot_builder,
        catalog_factory=catalog_factory,
        runtimes={Provider.FAKE: runtime},
    )
    frozen = await service.freeze(
        run=run,
        task=task,
        node=node,
        spec=spec,
        template=template,
        policy=policy,
        graph_revision=graph.graph_revision,
        attempt=1,
    )
    snapshot = await service.snapshot(
        workspace_id=workspace_id, project_id=project.project_id, task=task
    )
    return RoutableExecution(
        store,
        service,
        frozen,
        snapshot,
        run,
        task,
        node,
        spec,
        template,
        policy,
    )


async def _seed(
    *,
    store: MemoryStore | None = None,
    member: bool = True,
    role: WorkspaceRole = WorkspaceRole.ADMIN,
) -> SeededRouting:
    store = store or MemoryStore()
    await store.create_project(
        Project(
            project_id=FIXTURE_PROJECT_ID,
            name="M2 routing service",
            repository_path=Path("."),
        )
    )
    await store.upsert_workspace(
        WorkspaceEntity(workspace_id=FIXTURE_WORKSPACE_ID, name="M2 workspace")
    )
    principal = Principal(
        principal_id=OPERATOR_ID,
        issuer="test",
        subject="m2-operator",
        display_name="M2 Operator",
    )
    await store.upsert_principal(principal)
    if member:
        await store.upsert_workspace_membership(
            WorkspaceMembership(
                membership_id=new_id("workspace_membership"),
                workspace_id=FIXTURE_WORKSPACE_ID,
                principal_id=principal.principal_id,
                role=role,
            )
        )

    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=FIXTURE_PROJECT_ID,
            objective="Exercise a persisted routing decision.",
        )
    )
    await store.create_task(task)
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=FIXTURE_PROJECT_ID,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
        principal_id=principal.principal_id,
    )
    await store.create_run(run)

    run_graph_id = new_id("run_graph")
    node_id = "agent-node"
    # `_run_for` is the service's durable lineage check.  The graph is assigned
    # directly because template validation is unrelated to routing persistence;
    # the object itself is the production RunGraph contract read by the service.
    store.run_graphs[run.run_id] = RunGraph(
        run_graph_id=run_graph_id,
        run_id=run.run_id,
        task_id=task.envelope.task_id,
        template_record_id="wft_m2_service",
        template_id="m2-service",
        template_version="1.0.0",
        template_checksum="a" * 64,
        nodes=[RunNode(node_id=node_id, key="agent", kind="AGENT", label="Agent")],
    )

    node = build(
        NodeContract,
        workspace_id=FIXTURE_WORKSPACE_ID,
        project_id=FIXTURE_PROJECT_ID,
        node_id=node_id,
        run_graph_id=run_graph_id,
        execution_instance_id=new_id("execution_instance"),
        labels={"run_id": run.run_id, "attempt": "1"},
    )
    await store.put_node_contract(node)

    request_id = new_id("routing_request")
    context = build(
        RoutingContext,
        contract_id=request_id,
        workspace_id=FIXTURE_WORKSPACE_ID,
        project_id=FIXTURE_PROJECT_ID,
        node_contract_ref={
            "node_contract_id": node.contract_id,
            "immutable_hash": node.immutable_hash,
        },
        labels={"run_id": run.run_id},
    )
    await store.put_routing_request(context)
    candidate = build(
        ConfigurationCandidate,
        workspace_id=FIXTURE_WORKSPACE_ID,
        project_id=FIXTURE_PROJECT_ID,
        routing_request_id=request_id,
    )
    await store.put_configuration_candidate(candidate)
    rejected_candidate = build(
        ConfigurationCandidate,
        workspace_id=FIXTURE_WORKSPACE_ID,
        project_id=FIXTURE_PROJECT_ID,
        routing_request_id=request_id,
        hard_eligible=False,
        fallback_eligible=False,
    )
    await store.put_configuration_candidate(rejected_candidate)
    receipt = build(
        RoutingDecisionReceipt,
        workspace_id=FIXTURE_WORKSPACE_ID,
        project_id=FIXTURE_PROJECT_ID,
        objective_contract_ref=node.objective_contract_ref,
        routing_request_id=request_id,
        node_contract_hash=node.immutable_hash,
        selected_configuration_id=candidate.configuration.contract_id,
        selected_configuration_hash=candidate.configuration.configuration_hash,
        decision_type=DecisionType.FALLBACK,
        candidate_summary_refs=[candidate.contract_id, rejected_candidate.contract_id],
        capability_registry_snapshot_id=context.capability_registry_snapshot_id,
        policy_snapshot_id=context.policy_snapshot_id,
        labels={
            "run_id": run.run_id,
            "decision_version": "1",
            "routing_status": "READY",
        },
    )
    await store.put_routing_receipt(receipt)

    service = DefaultNodeRoutingService(
        store=store,
        snapshots=cast(RegistrySnapshotBuilder, object()),
        catalog_factory=_unused_catalog,
        runtimes={Provider.FAKE: FakeRuntime()},
    )
    return SeededRouting(
        store=store,
        service=service,
        principal=PrincipalRef(
            principal_id=principal.principal_id,
            display_name=principal.display_name,
            status=principal.status,
        ),
        run=run,
        receipt=receipt,
        candidate=candidate,
        rejected_candidate=rejected_candidate,
    )


async def test_receipt_lookup_does_not_distinguish_absent_from_inaccessible() -> None:
    seeded = await _seed()
    stranger = Principal(
        principal_id=new_id("principal"),
        issuer="test",
        subject="stranger",
    )
    await seeded.store.upsert_principal(stranger)
    stranger_ref = PrincipalRef(
        principal_id=stranger.principal_id,
        display_name=None,
        status=stranger.status,
    )

    failures: list[RoutingError] = []
    for receipt_id in (seeded.receipt.contract_id, new_id("routing_receipt")):
        with pytest.raises(RoutingError) as excinfo:
            await seeded.service.get_receipt(receipt_id=receipt_id, principal=stranger_ref)
        failures.append(excinfo.value)

    assert {(failure.status_code, failure.code, failure.message) for failure in failures} == {
        (404, "RECEIPT_NOT_FOUND", "Routing resource not found")
    }


@pytest.mark.acceptance("AC4-M2-015")
async def test_override_accepts_only_eligible_candidate_and_is_attributed() -> None:
    seeded = await _seed()
    request = {
        "receipt_id": seeded.receipt.contract_id,
        "candidate_id": seeded.candidate.contract_id,
        "reason_code": "EXPERIMENTAL_COMPARISON",
        "reason": "Exercise the eligible deterministic fallback.",
        "expected_receipt_version": 1,
        "principal": seeded.principal,
    }

    first = await seeded.service.override(**request)
    replay = await seeded.service.override(**request)

    assert replay == first
    assert first.supersedes_contract_id == seeded.receipt.contract_id
    assert first.created_by == seeded.principal
    receipts = await seeded.store.list_routing_receipts(
        workspace_id=FIXTURE_WORKSPACE_ID, project_id=FIXTURE_PROJECT_ID
    )
    overrides = await seeded.store.list_routing_overrides(
        workspace_id=FIXTURE_WORKSPACE_ID, project_id=FIXTURE_PROJECT_ID
    )
    assert len(receipts) == 2
    assert len(overrides) == 1
    assert overrides[0]["principal_id"] == seeded.principal.principal_id
    assert overrides[0]["reason_code"] == request["reason_code"]
    assert overrides[0]["reason"] == request["reason"]
    assert overrides[0]["superseding_receipt_id"] == first.contract_id

    rejected = await _seed()
    with pytest.raises(RoutingError) as excinfo:
        await rejected.service.override(
            receipt_id=rejected.receipt.contract_id,
            candidate_id=rejected.rejected_candidate.contract_id,
            reason_code="EXPERIMENTAL_COMPARISON",
            reason="An ineligible candidate must never become executable.",
            expected_receipt_version=1,
            principal=rejected.principal,
        )

    assert (excinfo.value.status_code, excinfo.value.code) == (
        422,
        "CANDIDATE_NOT_ELIGIBLE",
    )
    assert (
        await rejected.store.list_routing_overrides(
            workspace_id=FIXTURE_WORKSPACE_ID, project_id=FIXTURE_PROJECT_ID
        )
        == []
    )
    assert await rejected.store.list_routing_receipts(
        workspace_id=FIXTURE_WORKSPACE_ID, project_id=FIXTURE_PROJECT_ID
    ) == [rejected.receipt]


async def test_viewer_can_read_but_cannot_override_or_cancel() -> None:
    seeded = await _seed(role=WorkspaceRole.VIEWER)

    assert (
        await seeded.service.get_receipt(
            receipt_id=seeded.receipt.contract_id, principal=seeded.principal
        )
        == seeded.receipt
    )
    for operation in ("override", "cancel"):
        with pytest.raises(RoutingError) as excinfo:
            if operation == "override":
                await seeded.service.override(
                    receipt_id=seeded.receipt.contract_id,
                    candidate_id=seeded.candidate.contract_id,
                    reason_code="EXPERIMENTAL_COMPARISON",
                    reason="A viewer may not mutate routing state.",
                    expected_receipt_version=1,
                    principal=seeded.principal,
                )
            else:
                await seeded.service.cancel(
                    receipt_id=seeded.receipt.contract_id, principal=seeded.principal
                )
        assert (excinfo.value.status_code, excinfo.value.code) == (
            404,
            "RECEIPT_NOT_FOUND",
        )


async def test_same_cancel_request_is_idempotent_and_append_only() -> None:
    seeded = await _seed()

    first = await seeded.service.cancel(
        receipt_id=seeded.receipt.contract_id, principal=seeded.principal
    )
    replay = await seeded.service.cancel(
        receipt_id=seeded.receipt.contract_id, principal=seeded.principal
    )

    assert replay == first
    assert first.supersedes_contract_id == seeded.receipt.contract_id
    assert first.labels["routing_status"] == "CANCELLED"
    assert first.decision_type is DecisionType.HUMAN_REVIEW_REQUIRED
    assert first.selected_configuration_id is None
    assert await seeded.store.get_routing_receipt(seeded.receipt.contract_id) == seeded.receipt
    events = await seeded.store.list_events(seeded.run.run_id)
    assert [event.native_type for event in events] == ["accretion/routing/cancel"]
    assert events[0].payload["receipt_id"] == first.contract_id


async def test_concurrent_override_and_cancel_create_only_one_executable_head() -> None:
    seeded = await _seed()
    override = seeded.service.override(
        receipt_id=seeded.receipt.contract_id,
        candidate_id=seeded.candidate.contract_id,
        reason_code="EXPERIMENTAL_COMPARISON",
        reason="Race an eligible operator selection.",
        expected_receipt_version=1,
        principal=seeded.principal,
    )
    cancel = seeded.service.cancel(
        receipt_id=seeded.receipt.contract_id, principal=seeded.principal
    )

    outcomes = await asyncio.gather(override, cancel, return_exceptions=True)

    assert sum(isinstance(outcome, RoutingDecisionReceipt) for outcome in outcomes) == 1
    conflicts = [outcome for outcome in outcomes if isinstance(outcome, RoutingError)]
    assert len(conflicts) == 1
    assert conflicts[0].code == "RECEIPT_VERSION_CONFLICT"
    receipts = await seeded.store.list_routing_receipts(
        workspace_id=FIXTURE_WORKSPACE_ID, project_id=FIXTURE_PROJECT_ID
    )
    successors = [
        item for item in receipts if item.supersedes_contract_id == seeded.receipt.contract_id
    ]
    assert len(receipts) == 2
    assert len(successors) == 1


@pytest.mark.parametrize("operation", ["override", "cancel"])
async def test_amendment_and_event_roll_back_together_on_interrupted_write(
    operation: str,
) -> None:
    store = FailingEventStore()
    seeded = await _seed(store=store)
    before_contexts = await store.list_routing_requests(
        workspace_id=FIXTURE_WORKSPACE_ID, project_id=FIXTURE_PROJECT_ID
    )
    store.fail_routing_event = True

    with pytest.raises(RuntimeError, match="injected event persistence failure"):
        if operation == "override":
            await seeded.service.override(
                receipt_id=seeded.receipt.contract_id,
                candidate_id=seeded.candidate.contract_id,
                reason_code="EXPERIMENTAL_COMPARISON",
                reason="Prove the transaction rolls back.",
                expected_receipt_version=1,
                principal=seeded.principal,
            )
        else:
            await seeded.service.cancel(
                receipt_id=seeded.receipt.contract_id, principal=seeded.principal
            )

    receipts = await store.list_routing_receipts(
        workspace_id=FIXTURE_WORKSPACE_ID, project_id=FIXTURE_PROJECT_ID
    )
    contexts = await store.list_routing_requests(
        workspace_id=FIXTURE_WORKSPACE_ID, project_id=FIXTURE_PROJECT_ID
    )
    overrides = await store.list_routing_overrides(
        workspace_id=FIXTURE_WORKSPACE_ID, project_id=FIXTURE_PROJECT_ID
    )
    assert receipts == [seeded.receipt]
    assert contexts == before_contexts
    assert overrides == []
    assert await store.list_events(seeded.run.run_id) == []


async def test_dispatch_refuses_a_receipt_that_was_never_persisted() -> None:
    seeded = await _seed()
    unpersisted = seeded.receipt.model_copy(update={"contract_id": new_id("routing_receipt")})

    with pytest.raises(RoutingError) as excinfo:
        await seeded.service.claim_dispatch(receipt=unpersisted, run=seeded.run)

    assert excinfo.value.code == "DISPATCH_WITHOUT_RECEIPT"
    assert await seeded.store.list_events(seeded.run.run_id) == []


@pytest.mark.acceptance("AC4-M2-011")
async def test_real_freeze_catalog_selection_and_receipt_replay_are_end_to_end(
    tmp_path: Path,
) -> None:
    execution = await _routable_execution(tmp_path)

    receipt = await execution.service.route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )
    replay = await execution.service.route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )

    assert replay == receipt
    assert receipt.decision_type is DecisionType.FALLBACK
    assert receipt.node_contract_hash == execution.frozen.node_contract.immutable_hash
    assert await execution.store.get_routing_receipt(receipt.contract_id) == receipt
    assert (
        await execution.store.get_routing_receipt_for_request(receipt.routing_request_id) == receipt
    )
    context = await execution.store.get_routing_request(receipt.routing_request_id)
    assert context is not None
    assert context.node_contract_ref.node_contract_id == execution.frozen.node_contract.contract_id
    candidates = await execution.store.list_configuration_candidates(
        workspace_id=receipt.workspace_id, project_id=receipt.project_id
    )
    assert len(candidates) == 1
    assert candidates[0].fallback_eligible
    configuration = await execution.service.configuration_for(receipt)
    assert configuration.runtime.provider is Provider.FAKE
    events = await execution.store.list_events(execution.run.run_id)
    assert [event.native_type for event in events] == ["accretion/routing/created"]


@pytest.mark.acceptance("AC4-M2-012")
async def test_receipt_pins_versions_and_identity_changes_with_immutable_inputs(
    tmp_path: Path,
) -> None:
    execution = await _routable_execution(tmp_path)
    first = await execution.service.route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )
    configuration = await execution.service.configuration_for(first)
    health = execution.snapshot.runtime_health[0]

    assert first.workspace_router_version == WORKSPACE_ROUTER_VERSION
    assert first.project_adapter_version is None
    assert first.objective_contract_version == execution.frozen.objective_ref.revision
    assert first.node_contract_hash == execution.frozen.node_contract.immutable_hash
    assert (
        first.capability_registry_snapshot_id == execution.snapshot.capability_registry_snapshot_id
    )
    assert first.policy_snapshot_id == execution.snapshot.policy_snapshot_id
    assert configuration.runtime.runtime_id == health.runtime_id
    assert configuration.runtime.adapter_version == health.runtime_version
    assert configuration.model.model_id == "fake-model"

    changed_registry_id = hashlib.sha256(b"changed registry snapshot").hexdigest()
    changed_snapshot = replace(
        execution.snapshot,
        capability_registry_snapshot_id=changed_registry_id,
    )
    changed_snapshot_receipt = await execution.service.route(
        frozen=execution.frozen,
        snapshot=changed_snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )
    assert changed_snapshot_receipt.contract_id != first.contract_id
    assert changed_snapshot_receipt.routing_request_id != first.routing_request_id
    assert changed_snapshot_receipt.supersedes_contract_id == first.contract_id
    assert changed_snapshot_receipt.capability_registry_snapshot_id == changed_registry_id

    changed_frozen = await execution.service.freeze(
        run=execution.run,
        task=execution.task,
        node=execution.node,
        spec=execution.spec,
        template=execution.template,
        policy=execution.policy,
        graph_revision=1,
        attempt=2,
    )
    changed_contract_receipt = await execution.service.route(
        frozen=changed_frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )
    assert changed_contract_receipt.contract_id not in {
        first.contract_id,
        changed_snapshot_receipt.contract_id,
    }
    assert (
        changed_contract_receipt.node_contract_hash == changed_frozen.node_contract.immutable_hash
    )
    assert changed_contract_receipt.node_contract_hash != first.node_contract_hash

    catalog = await execution.service.catalog_factory(
        execution.frozen, execution.snapshot, execution.run, execution.task
    )
    effective_snapshot = replace(
        execution.snapshot, fallback_bundle_digest=catalog.fallback_bundle.digest
    )
    changed_model_snapshot = replace(
        effective_snapshot,
        available_runtime_snapshot_id=hashlib.sha256(
            b"runtime snapshot with another model id"
        ).hexdigest(),
    )
    changed_model_bundle = replace(
        effective_snapshot,
        fallback_bundle_digest=hashlib.sha256(b"fallback bundle with fake-model-v2").hexdigest(),
    )
    original_identity = routing_request_id(
        execution.frozen.node_contract.immutable_hash,
        effective_snapshot,
        WORKSPACE_ROUTER_VERSION,
        None,
        RoutingMode.BASELINE_ONLY,
    )
    assert (
        routing_request_id(
            execution.frozen.node_contract.immutable_hash,
            changed_model_snapshot,
            WORKSPACE_ROUTER_VERSION,
            None,
            RoutingMode.BASELINE_ONLY,
        )
        != original_identity
    )
    assert (
        routing_request_id(
            execution.frozen.node_contract.immutable_hash,
            changed_model_bundle,
            WORKSPACE_ROUTER_VERSION,
            None,
            RoutingMode.BASELINE_ONLY,
        )
        != original_identity
    )


@pytest.mark.acceptance("AC4-M2-013")
async def test_real_route_never_copies_secret_objective_into_receipt(
    tmp_path: Path,
) -> None:
    secret = "Bearer abcdefghijklmnopqrstuvwxyz1234567890"
    execution = await _routable_execution(
        tmp_path,
        objective=f"Route this request while protecting {secret} from receipts.",
    )

    receipt = await execution.service.route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )

    serialized = receipt.model_dump_json()
    assert secret not in serialized
    assert "Bearer " not in serialized
    assert "private_reasoning" not in serialized
    assert "chain_of_thought" not in serialized
    assert receipt.explanation.summary


async def test_route_execution_validates_real_snapshot_identity_before_persisting(
    tmp_path: Path,
) -> None:
    execution = await _routable_execution(tmp_path)
    original_factory = execution.service.catalog_factory
    catalog = await original_factory(
        execution.frozen, execution.snapshot, execution.run, execution.task
    )
    catalog_calls = 0

    async def changing_catalog_factory(
        frozen: FrozenNode,
        routing_snapshot: RoutingSnapshot,
        run: Run,
        task: Task,
    ) -> ConfigurationCatalog:
        nonlocal catalog_calls
        del frozen, routing_snapshot, run, task
        catalog_calls += 1
        if catalog_calls == 1:
            return catalog
        return replace(catalog, fallback_bundle=FallbackBundle(), digest="")

    execution.service.catalog_factory = changing_catalog_factory
    snapshot = replace(execution.snapshot, fallback_bundle_digest=catalog.fallback_bundle.digest)
    request_id = routing_request_id(
        execution.frozen.node_contract.immutable_hash,
        snapshot,
        WORKSPACE_ROUTER_VERSION,
        None,
        RoutingMode.BASELINE_ONLY,
    )
    principal = execution.frozen.node_contract.created_by

    receipt = await execution.service.route_execution(
        project_id=execution.run.project_id,
        execution_instance_id=execution.frozen.execution_instance_id,
        routing_request_id=request_id,
        node_contract_id=execution.frozen.node_contract.contract_id,
        expected_node_contract_hash=execution.frozen.node_contract.immutable_hash,
        mode=RoutingMode.BASELINE_ONLY,
        expected_registry_snapshot_id=snapshot.capability_registry_snapshot_id,
        principal=principal,
    )

    assert receipt.routing_request_id == request_id
    assert catalog_calls == 1
    assert receipt.capability_registry_snapshot_id == snapshot.capability_registry_snapshot_id
    assert await execution.store.get_routing_receipt_for_request(request_id) == receipt


async def test_dispatch_claim_is_persisted_and_blocks_late_operator_changes(
    tmp_path: Path,
) -> None:
    execution = await _routable_execution(tmp_path)
    receipt = await execution.service.route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )

    configuration = await execution.service.claim_dispatch(receipt=receipt, run=execution.run)

    assert configuration.runtime.provider is Provider.FAKE
    events = await execution.store.list_events(execution.run.run_id)
    dispatches = [event for event in events if event.native_type == "accretion/routing/dispatch"]
    assert len(dispatches) == 1
    assert dispatches[0].causation_id == receipt.contract_id
    candidate_id = receipt.candidate_summary_refs[0]
    for operation in ("override", "cancel"):
        with pytest.raises(RoutingError) as excinfo:
            if operation == "override":
                await execution.service.override(
                    receipt_id=receipt.contract_id,
                    candidate_id=candidate_id,
                    reason_code="EXPERIMENTAL_COMPARISON",
                    reason="This is too late after the dispatch claim.",
                    expected_receipt_version=1,
                    principal=receipt.created_by,
                )
            else:
                await execution.service.cancel(
                    receipt_id=receipt.contract_id, principal=receipt.created_by
                )
        assert excinfo.value.code == "RECEIPT_ALREADY_DISPATCHED"
    receipts = await execution.store.list_routing_receipts(
        workspace_id=receipt.workspace_id, project_id=receipt.project_id
    )
    assert receipts == [receipt]


async def test_changed_snapshot_supersedes_old_head_and_allows_only_one_dispatch(
    tmp_path: Path,
) -> None:
    execution = await _routable_execution(tmp_path)
    first = await execution.service.route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )
    changed_snapshot = replace(
        execution.snapshot,
        capability_registry_snapshot_id=hashlib.sha256(
            b"registry state after first routing decision"
        ).hexdigest(),
    )
    second = await execution.service.route(
        frozen=execution.frozen,
        snapshot=changed_snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )

    assert second.supersedes_contract_id == first.contract_id
    with pytest.raises(RoutingError) as excinfo:
        await execution.service.claim_dispatch(receipt=first, run=execution.run)
    assert excinfo.value.code == "RECEIPT_VERSION_CONFLICT"

    await execution.service.claim_dispatch(receipt=second, run=execution.run)
    dispatches = [
        event
        for event in await execution.store.list_events(execution.run.run_id)
        if event.native_type == "accretion/routing/dispatch"
    ]
    assert [event.causation_id for event in dispatches] == [second.contract_id]


@pytest.mark.parametrize(
    ("health_updates", "error_code"),
    [
        ({"runtime_version": "fake-drifted-after-routing"}, "RUNTIME_VERSION_DRIFT"),
        ({"runtime_id": "runtime_fake_replaced"}, "RUNTIME_VERSION_DRIFT"),
        ({"capabilities": ["structured-events"]}, "RUNTIME_VERSION_DRIFT"),
        ({"status": RuntimeStatus.UNAVAILABLE}, "RUNTIME_UNAVAILABLE"),
    ],
    ids=("adapter-version", "runtime-id", "capability-profile", "unavailable"),
)
async def test_runtime_health_drift_prevents_dispatch_claim(
    tmp_path: Path,
    health_updates: dict[str, object],
    error_code: str,
) -> None:
    execution = await _routable_execution(tmp_path)
    receipt = await execution.service.route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )
    drifted = DriftedHealthRuntime(**health_updates)
    execution.service.runtimes = {Provider.FAKE: drifted}

    with pytest.raises(RoutingError) as excinfo:
        await execution.service.claim_dispatch(receipt=receipt, run=execution.run)

    assert excinfo.value.code == error_code
    events = await execution.store.list_events(execution.run.run_id)
    assert not any(event.native_type == "accretion/routing/dispatch" for event in events)
