"""Real-stack witnesses for M2 receipt-first graph execution."""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from accretion.concurrency import ConcurrencyLimiter
from accretion.config import Settings
from accretion.contracts import (
    ApprovalDecisionValue,
    ApprovalStatus,
    EventType,
    GraphNodeKind,
    Principal,
    Provider,
    RunState,
    SessionRef,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.governance import seed_governance
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.bootstrap import build_node_routing
from accretion.runtimes.fake import FakeCallOutcome, FakeRuntime
from accretion.services.run_manager import RunManager
from accretion.templates import seed_templates
from accretion.workspace import WorktreeManager


def _initialize_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Accretion Test"],
        check=True,
    )
    (path / "result.json").write_text('{"ok": false}\n')
    subprocess.run(["git", "-C", str(path), "add", "result.json"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)


def _write_valid_output(session: SessionRef, _request: object) -> None:
    (session.workspace / "result.json").write_text('{"ok": true}\n')


class ReceiptAuditRuntime(FakeRuntime):
    """Assert the real store already carries a claimed receipt at submit time."""

    def __init__(self, store: MemoryStore, workspace_id: str) -> None:
        super().__init__(
            scripted_outcomes=[
                FakeCallOutcome(),
                FakeCallOutcome(hook=_write_valid_output),
            ]
        )
        self.store = store
        self.workspace_id = workspace_id
        self.submit_receipt_ids: list[str] = []

    async def submit(self, session, request):  # type: ignore[no-untyped-def]
        receipts = await self.store.list_routing_receipts(workspace_id=self.workspace_id)
        events = await self.store.list_events(session.run_id)
        dispatch_ids = {
            str(event.payload["receipt_id"])
            for event in events
            if event.native_type == "accretion/routing/dispatch"
        }
        assert len(receipts) == len(self.submit_receipt_ids) + 1
        assert {receipt.contract_id for receipt in receipts} == dispatch_ids
        current = receipts[-1]
        self.submit_receipt_ids.append(current.contract_id)
        return await super().submit(session, request)


class SubmitCountingRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.submit_count = 0

    async def submit(self, session, request):  # type: ignore[no-untyped-def]
        self.submit_count += 1
        return await super().submit(session, request)


class FailingReceiptStore(MemoryStore):
    """Fail inside the real routing transaction before a receipt can publish."""

    async def put_routing_receipt(self, record):  # type: ignore[no-untyped-def]
        raise RuntimeError("injected receipt persistence failure")


@dataclass(slots=True)
class RoutedGraphFixture:
    manager: RunManager
    store: MemoryStore
    runtime: FakeRuntime
    task_id: str
    principal_id: str
    workspace_id: str


async def _routed_graph(
    tmp_path: Path,
    *,
    store: MemoryStore,
    runtime_factory,  # type: ignore[no-untyped-def]
) -> RoutedGraphFixture:
    repository = tmp_path / "repository"
    repository.mkdir()
    _initialize_repository(repository)
    await seed_templates(store)
    await seed_governance(store)

    principal = Principal(
        principal_id=new_id("principal"),
        issuer="test",
        subject="m2-end-to-end",
        display_name="M2 end-to-end operator",
    )
    workspace_id = new_id("workspace_entity")
    await store.upsert_principal(principal)
    await store.upsert_workspace(
        WorkspaceEntity(workspace_id=workspace_id, name="M2 end-to-end")
    )
    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=workspace_id,
            principal_id=principal.principal_id,
            role=WorkspaceRole.ADMIN,
        )
    )

    runtime = runtime_factory(store, workspace_id)
    manager = RunManager(
        store=store,
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: runtime},
        limiter=ConcurrencyLimiter(global_limit=2, provider_limit=2, project_limit=2),
        live_providers_enabled=False,
    )
    manager.routing_service = build_node_routing(
        manager,
        policy_id="local-capability-policy",
        granted_permissions=set(),
    )
    project = await manager.create_project("M2 routed graph", repository)
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Apply an approved change and verify the result.",
        task_patch={
            "task_type": "OTHER",
            "risk_level": "HIGH",
            "required_outputs": [{"path": "result.json", "kind": "json"}],
        },
    )
    return RoutedGraphFixture(
        manager=manager,
        store=store,
        runtime=runtime,
        task_id=task.envelope.task_id,
        principal_id=principal.principal_id,
        workspace_id=workspace_id,
    )


async def _wait_for_approval(manager: RunManager, run_id: str) -> str:
    for _ in range(250):
        pending = await manager.store.list_approvals(run_id, ApprovalStatus.PENDING)
        if pending:
            return pending[0].approval_id
        await asyncio.sleep(0.02)
    run = await manager.store.get_run(run_id)
    events = await manager.store.list_events(run_id)
    raise AssertionError(
        "no pending approval appeared; "
        f"state={run.state.value if run else 'missing'}, "
        f"events={[(event.native_type, event.payload) for event in events[-5:]]!r}"
    )


@pytest.mark.acceptance("AC4-M2-001", "AC4-M2-014")
async def test_real_routed_graph_claims_every_receipt_before_execution(
    tmp_path: Path,
) -> None:
    fixture = await _routed_graph(
        tmp_path,
        store=MemoryStore(),
        runtime_factory=lambda store, workspace_id: ReceiptAuditRuntime(
            store, workspace_id
        ),
    )
    manager = fixture.manager
    run = await manager.start_run(
        fixture.task_id, Provider.FAKE, principal_id=fixture.principal_id
    )
    background = manager.background[run.run_id]

    plan_approval = await _wait_for_approval(manager, run.run_id)
    await manager.resolve_approval(plan_approval, ApprovalDecisionValue.APPROVE)
    outcome_approval = await _wait_for_approval(manager, run.run_id)
    assert outcome_approval != plan_approval
    await manager.resolve_approval(outcome_approval, ApprovalDecisionValue.APPROVE)
    await asyncio.wait_for(background, 10)

    final = await fixture.store.get_run(run.run_id)
    assert final is not None and final.state is RunState.SUCCEEDED
    receipts = await fixture.store.list_routing_receipts(
        workspace_id=fixture.workspace_id, project_id=run.project_id
    )
    contracts = await fixture.store.list_node_contracts(
        workspace_id=fixture.workspace_id, project_id=run.project_id
    )
    by_hash = {contract.immutable_hash: contract for contract in contracts}
    assert {by_hash[receipt.node_contract_hash].node_kind for receipt in receipts} >= {
        GraphNodeKind.AGENT,
        GraphNodeKind.VERIFIER,
    }

    events = await fixture.store.list_events(run.run_id)
    dispatches = {
        str(event.payload["receipt_id"]): event
        for event in events
        if event.native_type == "accretion/routing/dispatch"
    }
    assert set(dispatches) == {receipt.contract_id for receipt in receipts}
    started = [
        event
        for event in events
        if event.normalized_type is EventType.RUNTIME_CALL_STARTED
    ]
    runtime = fixture.runtime
    assert isinstance(runtime, ReceiptAuditRuntime)
    assert [event.payload["routing_receipt_id"] for event in started] == (
        runtime.submit_receipt_ids
    )
    for event in started:
        receipt_id = str(event.payload["routing_receipt_id"])
        assert dispatches[receipt_id].sequence < event.sequence
        assert event.payload["node_contract_hash"] == next(
            receipt.node_contract_hash
            for receipt in receipts
            if receipt.contract_id == receipt_id
        )

    # The selected verifier remains the pinned primary, but it never weakens the frozen
    # multi-verifier policy: all mandatory checks still execute.
    verifier_ids = {
        str(event.payload["verifier_id"])
        for event in events
        if event.normalized_type is EventType.VERIFICATION_RESULT
    }
    assert verifier_ids == {"git-diff", "output-contract", "trajectory-policy"}


@pytest.mark.acceptance("AC4-M2-014")
async def test_receipt_persistence_failure_prevents_runtime_submission(
    tmp_path: Path,
) -> None:
    fixture = await _routed_graph(
        tmp_path,
        store=FailingReceiptStore(),
        runtime_factory=lambda _store, _workspace_id: SubmitCountingRuntime(),
    )
    run = await fixture.manager.start_run(
        fixture.task_id, Provider.FAKE, principal_id=fixture.principal_id
    )
    background = fixture.manager.background[run.run_id]
    await asyncio.wait_for(background, 10)

    runtime = fixture.runtime
    assert isinstance(runtime, SubmitCountingRuntime)
    assert runtime.submit_count == 0
    events = await fixture.store.list_events(run.run_id)
    assert not any(
        event.normalized_type is EventType.RUNTIME_CALL_STARTED for event in events
    )
    assert not await fixture.store.list_routing_receipts(
        workspace_id=fixture.workspace_id, project_id=run.project_id
    )


def test_node_routing_remains_disabled_by_default() -> None:
    assert Settings.model_fields["enable_node_routing"].default is False
