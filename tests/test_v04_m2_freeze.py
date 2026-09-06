"""M2 PR1: deterministic, ordered node-contract freezing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from accretion.contracts import (
    AcceptancePolicy,
    Capability,
    CapabilityBackend,
    GraphNodeKind,
    PrincipalRef,
    PrincipalStatus,
    Project,
    Provider,
    RiskLevel,
    Run,
    RunGraph,
    RunNode,
    RunState,
    Task,
    TaskEnvelope,
    TemplateStatus,
    WorkflowNodeSpec,
    WorkflowTemplate,
)
from accretion.contracts.routing import DecisionType, RoutingDecisionReceipt
from accretion.ids import has_prefix
from accretion.persistence.store import MemoryStore
from accretion.routing.freeze import NodeContractFreezer, ObjectiveContractMinter
from accretion.routing.identity import execution_instance_id
from accretion.routing.protocols import FrozenNode
from accretion.services.run_manager import NodeOutcome, RunManager, _GraphCursor
from accretion.templates import compute_template_checksum

FIXED_TIME = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)
WORKSPACE_ID = "wks_8G33T24F686H6EJPBHRSFYCC3C"
PROJECT_ID = "prj_8W5DH3HW6DPAFFPBHQ47R21DK9"
RUN_ID = "run_01K4DQ9HVJXBQBN3YF83E5Y9TB"
TASK_ID = "tsk_01K4DQ9HVJXBQBN3YF83E5Y9TC"
PRINCIPAL = PrincipalRef(
    principal_id="usr_01K4DQ9HVJXBQBN3YF83E5Y9TD",
    display_name="M2 freeze",
    status=PrincipalStatus.ACTIVE,
)


class OrderingStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.freeze_writes: list[str] = []

    async def put_verification_spec(self, record):  # type: ignore[no-untyped-def]
        self.freeze_writes.append("verification-spec")
        return await super().put_verification_spec(record)

    async def put_node_contract(self, record):  # type: ignore[no-untyped-def]
        self.freeze_writes.append("node-contract")
        return await super().put_node_contract(record)


def task_row(*, created_at: datetime = FIXED_TIME) -> Task:
    return Task(
        envelope=TaskEnvelope(
            task_id=TASK_ID,
            project_id=PROJECT_ID,
            objective="implement the approved routing node",
            allowed_capabilities=["cap.repo.read"],
            risk_level=RiskLevel.HIGH,
            required_outputs=[{"path": "result.json"}],
        ),
        created_at=created_at,
    )


def policy_row() -> AcceptancePolicy:
    return AcceptancePolicy(
        policy_id="acceptance-m2-freeze",
        required_verifiers=["output-contract"],
        score_thresholds={"quality": 0.5},
        created_at=FIXED_TIME,
    )


def run_row() -> Run:
    return Run(
        run_id=RUN_ID,
        task_id=TASK_ID,
        project_id=PROJECT_ID,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
        principal_id=PRINCIPAL.principal_id,
        created_at=FIXED_TIME,
        updated_at=FIXED_TIME,
    )


def template_row(spec: WorkflowNodeSpec) -> WorkflowTemplate:
    template = WorkflowTemplate(
        template_record_id="wft_01K4DQ9HVJXBQBN3YF83E5Y9TE",
        template_id="m2-freeze",
        version="1.0.0",
        mode="GRAPH",
        nodes=[spec],
        required_verifiers=["git-diff"],
        checksum="pending",
        status=TemplateStatus.DRAFT,
        created_at=FIXED_TIME,
    )
    return template.model_copy(
        update={
            "checksum": compute_template_checksum(template),
            "status": TemplateStatus.VALIDATED,
        }
    )


async def freeze_setup(
    store: MemoryStore, *, kind: GraphNodeKind = GraphNodeKind.AGENT
) -> tuple[NodeContractFreezer, Run, Task, RunNode, WorkflowNodeSpec, WorkflowTemplate]:
    run = run_row()
    task = task_row()
    spec = WorkflowNodeSpec(
        key="implement",
        kind=kind,
        label="Implement",
        instruction="make the scoped change",
        capability_refs=["cap.node.write"],
    )
    node = RunNode(
        node_id=f"{run.run_id}:{spec.key}",
        key=spec.key,
        kind=kind,
        label=spec.label,
    )
    template = template_row(spec)
    await store.create_project(
        Project(
            project_id=PROJECT_ID,
            name="M2 freeze",
            repository_path=Path("/tmp/accretion-m2-freeze"),
            created_at=FIXED_TIME,
        )
    )
    await store.create_run(run)
    await store.create_task(task)
    await store.upsert_workflow_template(template)
    await store.create_run_graph(
        RunGraph(
            run_graph_id="rgr_01K4DQ9HVJXBQBN3YF83E5Y9TF",
            run_id=run.run_id,
            task_id=task.envelope.task_id,
            template_record_id=template.template_record_id,
            template_id=template.template_id,
            template_version=template.version,
            template_checksum=template.checksum,
            nodes=[node],
            graph_revision=1,
            instantiated_at=FIXED_TIME,
        )
    )
    await store.upsert_capability(
        Capability(
            capability_id="cap.node.write",
            version="2.1.0",
            backend=CapabilityBackend.PYTHON,
            required_permissions=["repo:write"],
            created_at=FIXED_TIME,
        )
    )
    return (
        NodeContractFreezer(store=store, created_by=PRINCIPAL, workspace_id=WORKSPACE_ID),
        run,
        task,
        node,
        spec,
        template,
    )


async def test_objective_is_one_persisted_revision_per_task() -> None:
    store = MemoryStore()
    await store.create_project(
        Project(
            project_id=PROJECT_ID,
            name="M2 objective",
            repository_path=Path("/tmp/accretion-m2-objective"),
            created_at=FIXED_TIME,
        )
    )
    minter = ObjectiveContractMinter(
        store=store, created_by=PRINCIPAL, workspace_id=WORKSPACE_ID
    )
    first = await minter.for_task(task_row(), policy_row())
    # A reconstructed Task object with a later local timestamp must retrieve the original
    # authority record, not silently reseal it under the wall clock.
    second = await minter.for_task(
        task_row(created_at=FIXED_TIME + timedelta(days=1)), policy_row()
    )

    assert first == second
    assert first.created_at == FIXED_TIME
    objectives = await store.list_objective_contracts(workspace_id=WORKSPACE_ID)
    assert len(objectives) == 1
    assert objectives[0].utility_weights.model_dump() == {
        "quality": 1.0,
        "cost": 0.25,
        "latency": 0.15,
    }
    assert objectives[0].verified_success_floor == 0.5


@pytest.mark.acceptance("AC4-M2-002")
async def test_freeze_persists_spec_first_and_replays_byte_identically() -> None:
    store = OrderingStore()
    freezer, run, task, node, spec, template = await freeze_setup(store)
    arguments = dict(
        run=run,
        task=task,
        node=node,
        spec=spec,
        template=template,
        policy=policy_row(),
        graph_revision=1,
        attempt=1,
    )

    frozen = await freezer.freeze(**arguments)
    again = await freezer.freeze(**arguments)

    assert store.freeze_writes == ["verification-spec", "node-contract"]
    assert frozen == again
    assert frozen.node_contract.immutable_hash
    assert frozen.node_contract.content_hash
    assert frozen.verification_spec.content_hash
    assert (
        frozen.node_contract.verification_spec_ref.content_hash
        == frozen.verification_spec.content_hash
    )
    assert has_prefix(frozen.execution_instance_id, "execution_instance")
    assert not has_prefix(frozen.execution_instance_id, "run")
    assert frozen.node_contract.allowed_risk_class.value == "HIGH_DIGITAL"
    assert frozen.node_contract.labels == {
        "run_id": RUN_ID,
        "node_key": "implement",
        "attempt": "1",
    }
    assert {
        item.capability.capability_id for item in frozen.node_contract.required_capabilities
    } == {"cap.node.write", "cap.repo.read"}
    assert frozen.node_contract.environment_constraints == []


@pytest.mark.acceptance("AC4-M2-004")
async def test_graph_revision_supersedes_without_mutating_active_freeze() -> None:
    store = MemoryStore()
    freezer, run, task, node, spec, template = await freeze_setup(store)
    common = dict(
        run=run,
        task=task,
        node=node,
        spec=spec,
        template=template,
        policy=policy_row(),
        attempt=1,
    )
    active = await freezer.freeze(**common, graph_revision=1)
    active_hash = active.node_contract.immutable_hash
    replacement = await freezer.freeze(**common, graph_revision=2)

    assert replacement.node_contract.contract_id != active.node_contract.contract_id
    assert replacement.node_contract.immutable_hash != active_hash
    assert replacement.node_contract.supersedes_contract_id == active.node_contract.contract_id
    assert active.node_contract.immutable_hash == active_hash
    assert (await store.get_node_contract(active.node_contract.contract_id)) == active.node_contract


@pytest.mark.parametrize(
    "kind", [GraphNodeKind.AGENT, GraphNodeKind.TOOL, GraphNodeKind.VERIFIER]
)
async def test_run_manager_freezes_every_routable_kind_before_execution(
    kind: GraphNodeKind,
) -> None:
    class FreezeReached(RuntimeError):
        pass

    class RoutingSpy:
        async def freeze(self, **kwargs):  # type: ignore[no-untyped-def]
            assert kwargs["node"].kind is kind
            assert kwargs["graph_revision"] == 7
            assert kwargs["attempt"] == 1
            raise FreezeReached

    manager = object.__new__(RunManager)
    manager.routing_service = RoutingSpy()
    spec = WorkflowNodeSpec(key="route", kind=kind, label="Route")
    node = RunNode(node_id=f"{RUN_ID}:route", key="route", kind=kind, label="Route")
    cursor = _GraphCursor(statuses={}, entered_via={}, current_key="route")

    with pytest.raises(FreezeReached):
        await manager._run_graph_node(
            run_row(),
            task_row(),
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            node=node,
            template_node=spec,
            template=template_row(spec),
            gate=None,
            policy=policy_row(),
            deadline=FIXED_TIME.timestamp() + 60,
            cursor=cursor,
            graph_revision=7,
        )


async def test_cursor_keeps_an_existing_freeze_for_the_active_execution() -> None:
    class RoutingSpy:
        calls = 0

        async def freeze(self, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            raise AssertionError("an active cursor must not be refrozen")

        async def latest_receipt(self, **kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(decision_type=DecisionType.HUMAN_REVIEW_REQUIRED)

    store = MemoryStore()
    freezer, run, task, node, spec, template = await freeze_setup(store)
    frozen = await freezer.freeze(
        run=run,
        task=task,
        node=node,
        spec=spec,
        template=template,
        policy=policy_row(),
        graph_revision=1,
        attempt=1,
    )
    manager = object.__new__(RunManager)
    spy = RoutingSpy()
    manager.routing_service = spy
    cursor = _GraphCursor(
        statuses={}, entered_via={}, current_key=node.key, frozen={node.key: frozen}
    )

    session = object()
    outcome, returned = await manager._run_graph_node(
        run,
        task,
        object(),  # type: ignore[arg-type]
        session,  # type: ignore[arg-type]
        node=node,
        template_node=spec,
        template=template,
        gate=None,
        policy=policy_row(),
        deadline=FIXED_TIME.timestamp() + 60,
        cursor=cursor,
        graph_revision=2,
    )

    assert outcome is NodeOutcome.INCONCLUSIVE
    assert returned is session
    assert spy.calls == 0
    assert (
        cursor.frozen[node.key].node_contract.immutable_hash
        == frozen.node_contract.immutable_hash
    )
    assert cursor.receipts[node.key].decision_type is DecisionType.HUMAN_REVIEW_REQUIRED


def test_execution_instance_identity_is_attempt_scoped() -> None:
    first = execution_instance_id(RUN_ID, "implement", 1)
    assert first == execution_instance_id(RUN_ID, "implement", 1)
    assert first != execution_instance_id(RUN_ID, "implement", 2)
    assert has_prefix(first, "execution_instance")
    with pytest.raises(ValueError, match="counted from 1"):
        execution_instance_id(RUN_ID, "implement", 0)


# Keep the new cursor field's intended public value type visible to static checking.  The
# dispatch PR populates it; PR1 deliberately proves that it begins empty.
def _receipt_type_witness(value: RoutingDecisionReceipt | None) -> RoutingDecisionReceipt | None:
    return value


def _frozen_type_witness(value: FrozenNode) -> FrozenNode:
    return value
