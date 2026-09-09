"""Synthetic lifecycle/ownership witnesses; no simulator or acceptance claim."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from test_v05_authority import Lab, rejected
from test_v05_authority import lab as authority_lab

from accretion.api.auth import build_auth_runtime
from accretion.api.main import app
from accretion.concurrency import ConcurrencyLimiter
from accretion.config import Settings
from accretion.contracts import Principal, Provider, RunState
from accretion.contracts.canonical import canonical_json
from accretion.ids import new_id
from accretion.orchestration.models import ReplanReason
from accretion.orchestration.service import DynamicWorkflowService
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.protocol import (
    ErrorOutcome,
    ProtocolRequest,
    ProtocolResponse,
    SuccessOutcome,
    TerminateRequest,
    TerminateResult,
)
from accretion.robotics.runtime_store import (
    DispatchReservation,
    RuntimeApproval,
    RuntimeTransaction,
)
from accretion.services.run_manager import RunManager
from accretion.workspace import WorktreeManager

lab = authority_lab


def manager_for(lab: Lab, tmp_path: Path) -> RunManager:
    # Empty runtime inventory makes accidental runtime dispatch observable.
    return RunManager(
        store=lab.state,
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={},
        limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
        live_providers_enabled=False,
    )


async def termination(lab: Lab) -> tuple[Any, ProtocolRequest, Any, DispatchReservation]:
    ep = await lab.running()
    assert lab.approval
    pins = lab.authority.execution_pins(ep, lab.approval)
    request = ProtocolRequest.create(
        request_id="synthetic-normal-termination",
        sequence=1,
        scope=pins.command_scope(),
        payload=TerminateRequest(),
    )
    result = await lab.authority.reserve_dispatch(
        lab.scope(revision=ep.revision), episode_id=ep.id, request=request, pins=pins
    )
    assert result.fresh and result.reservation
    return await lab.episode(), request, pins, result.reservation


def acknowledgement(request: ProtocolRequest) -> ProtocolResponse:
    return ProtocolResponse.create(request, SuccessOutcome(payload=TerminateResult()))


async def state_snapshot(lab: Lab) -> tuple[Any, ...]:
    ep = await lab.episode()
    async with lab.authority.store.transaction(lab.project) as tx:
        rows = await tx.list_rows(
            DispatchReservation, workspace_id=lab.workspace, project_id=lab.project, limit=100
        )
        events = await tx.registry.events(ep.binding_id, 0, 100)
    return ep, rows, events, await lab.state.get_run(lab.run.run_id)


async def test_recorded_normal_termination_only_enters_verifying(lab: Lab) -> None:
    ep, request, pins, reservation = await termination(lab)
    assert ep.live and ep.live.budget.actions == 0 and ep.live.sequence == 0
    response = acknowledgement(request)
    completed = await lab.authority.finish_termination(
        lab.scope(revision=ep.revision),
        episode_id=ep.id,
        reservation_id=reservation.id,
        response=response,
    )
    current = await lab.episode()
    assert current.status == "VERIFYING" and current.in_flight is None
    assert current.live == ep.live
    assert completed.status == "COMPLETED"
    assert completed.request_json == canonical_json(request).decode()
    assert completed.response_json == canonical_json(response).decode()
    run = await lab.state.get_run(lab.run.run_id)
    assert run and run.state is RunState.PENDING  # No generic success/producer PASS.
    async with lab.authority.store.transaction(lab.project) as tx:
        events = await tx.registry.events(ep.binding_id, 0, 100)
    terminated = [e for e in events if '"simulation_episode.terminated"' in e.original_json]
    assert len(terminated) == 1
    assert request.request_digest in terminated[0].original_json
    assert response.response_digest in terminated[0].original_json
    assert '"response_json"' in terminated[0].original_json
    assert lab.lease
    lease = await lab.authority.get_lease(lab.scope(), lab.lease.id)
    await lab.authority.end_lease(
        lab.scope(revision=lease.revision),
        episode_id=ep.id,
        generation=lease.generation,
        disposition="RELEASED",
    )
    assert (await lab.episode()).status == "VERIFYING"
    before = await state_snapshot(lab)
    repeated = await lab.authority.finish_termination(
        lab.scope(revision=ep.revision),
        episode_id=ep.id,
        reservation_id=reservation.id,
        response=response,
    )
    assert repeated == completed and await state_snapshot(lab) == before


@pytest.mark.parametrize("fault", ["wrong_request", "error", "unreserved", "wrong_actor", "stale"])
async def test_termination_refusal_preserves_rows_events_and_run(lab: Lab, fault: str) -> None:
    ep, request, _, reservation = await termination(lab)
    response = acknowledgement(request)
    scope = lab.scope(revision=ep.revision)
    reservation_id = reservation.id
    code = Code.INVALID_REQUEST
    if fault == "wrong_request":
        request = ProtocolRequest.create(
            request_id="unrelated-termination",
            sequence=2,
            scope=request.scope,
            payload=TerminateRequest(),
        )
        response = acknowledgement(request)
    elif fault == "error":
        response = ProtocolResponse.create(request, ErrorOutcome(code=Code.ADAPTER_CRASH))
        code = Code.ACKNOWLEDGEMENT_UNCERTAIN
    elif fault == "unreserved":
        reservation_id = "a" * 64
        code = Code.RESOURCE_NOT_FOUND
    elif fault == "wrong_actor":
        scope = lab.scope(lab.evaluator, ep.revision)
        code = Code.CAPABILITY_DENIED
    else:
        scope = lab.scope(revision=ep.revision - 1)
        code = Code.REVISION_CONFLICT
    before = await state_snapshot(lab)
    with rejected(code):
        await lab.authority.finish_termination(
            scope, episode_id=ep.id, reservation_id=reservation_id, response=response
        )
    assert await state_snapshot(lab) == before


@pytest.mark.parametrize("fence", ["uncertain", "release", "revoke_approval"])
async def test_late_termination_ack_cannot_override_a_committed_fence(lab: Lab, fence: str) -> None:
    ep, request, _, reservation = await termination(lab)
    assert lab.lease and lab.approval
    if fence == "uncertain":
        await lab.authority.fence_uncertain(
            lab.scope(lab.host),
            episode_id=ep.id,
            lease_binding=lab.approval.pins.lease,
            request_digest=request.request_digest,
        )
    elif fence == "release":
        lease = await lab.authority.get_lease(lab.scope(), lab.lease.id)
        await lab.authority.end_lease(
            lab.scope(revision=lease.revision),
            episode_id=ep.id,
            generation=lease.generation,
            disposition="RELEASED",
        )
    else:
        async with lab.authority.store.transaction(lab.project) as tx:
            approval = await tx.get(RuntimeApproval, lab.approval.contract_id)
        assert approval
        await lab.authority.revoke_approval(
            lab.scope(lab.human, approval.revision), episode_id=ep.id, approval_id=approval.id
        )
    current = await lab.episode()
    before = await state_snapshot(lab)
    with rejected(
        Code.APPROVAL_INVALID if fence == "revoke_approval" else Code.EPISODE_STATE_CONFLICT
    ):
        await lab.authority.finish_termination(
            lab.scope(revision=current.revision),
            episode_id=ep.id,
            reservation_id=reservation.id,
            response=acknowledgement(request),
        )
    assert await state_snapshot(lab) == before
    assert current.status != "VERIFYING"


async def test_termination_final_event_wait_cannot_commit_after_expiry(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    ep, request, _, reservation = await termination(lab)
    assert lab.lease
    before = await state_snapshot(lab)
    clock: list[datetime | None] = [None]
    original_now, original_event = RuntimeTransaction.now, lab.authority._event

    async def now(tx: RuntimeTransaction) -> datetime:
        actual = await original_now(tx)
        return clock[0] or actual

    async def delayed_event(*args: Any, **kwargs: Any) -> Any:
        result = await original_event(*args, **kwargs)
        clock[0] = lab.lease.expires_at
        return result

    monkeypatch.setattr(RuntimeTransaction, "now", now)
    monkeypatch.setattr(lab.authority, "_event", delayed_event)
    with rejected(Code.LEASE_INVALID):
        await lab.authority.finish_termination(
            lab.scope(revision=ep.revision),
            episode_id=ep.id,
            reservation_id=reservation.id,
            response=acknowledgement(request),
        )
    assert await state_snapshot(lab) == before


async def test_cleanup_authorization_has_no_normal_termination_receipt(lab: Lab) -> None:
    ep = await lab.running()
    assert lab.approval and lab.lease
    pins = lab.authority.execution_pins(ep, lab.approval)
    request = ProtocolRequest.create(
        request_id="synthetic-cleanup",
        sequence=1,
        scope=pins.command_scope(),
        payload=TerminateRequest(),
    )
    lease = await lab.authority.get_lease(lab.scope(), lab.lease.id)
    await lab.authority.end_lease(
        lab.scope(revision=lease.revision),
        episode_id=ep.id,
        generation=lease.generation,
        disposition="REVOKED",
    )
    allowed = await lab.authority.authorize_read(
        lab.scope(), episode_id=ep.id, request=request, pins=pins
    )
    assert allowed.fresh and allowed.reservation is None
    with rejected(Code.RESOURCE_NOT_FOUND):
        await lab.authority.finish_termination(
            lab.scope(revision=(await lab.episode()).revision),
            episode_id=ep.id,
            reservation_id=request.request_digest,
            response=acknowledgement(request),
        )
    assert (await lab.episode()).status == "ABORTED"


async def test_existing_action_reservation_blocks_normal_termination(lab: Lab) -> None:
    ep, action, pins = await lab.execution()
    await lab.authority.reserve_dispatch(
        lab.scope(revision=ep.revision), episode_id=ep.id, request=action, pins=pins
    )
    ep = await lab.episode()
    request = ProtocolRequest.create(
        request_id="termination-during-action",
        sequence=2,
        scope=pins.command_scope(),
        payload=TerminateRequest(),
    )
    before = await state_snapshot(lab)
    with rejected(Code.EPISODE_STATE_CONFLICT):
        await lab.authority.reserve_dispatch(
            lab.scope(revision=ep.revision), episode_id=ep.id, request=request, pins=pins
        )
    assert await state_snapshot(lab) == before


async def test_concurrent_normal_termination_is_reserved_and_recorded_once(lab: Lab) -> None:
    ep = await lab.running()
    assert lab.approval
    pins = lab.authority.execution_pins(ep, lab.approval)
    request = ProtocolRequest.create(
        request_id="concurrent-termination",
        sequence=1,
        scope=pins.command_scope(),
        payload=TerminateRequest(),
    )
    scope = lab.scope(revision=ep.revision)
    admissions = await asyncio.gather(
        *(
            lab.authority.reserve_dispatch(scope, episode_id=ep.id, request=request, pins=pins)
            for _ in range(2)
        )
    )
    assert sorted(a.fresh for a in admissions) == [False, True]
    assert admissions[0].reservation == admissions[1].reservation
    row = admissions[0].reservation
    assert row
    ep = await lab.episode()
    completions = await asyncio.gather(
        *(
            lab.authority.finish_termination(
                lab.scope(revision=ep.revision),
                episode_id=ep.id,
                reservation_id=row.id,
                response=acknowledgement(request),
            )
            for _ in range(2)
        )
    )
    assert completions[0] == completions[1]
    current = await lab.episode()
    assert current.status == "VERIFYING" and current.event_sequence == ep.event_sequence + 1
    assert current.live == ep.live


@pytest.mark.parametrize(
    "operation",
    [
        "pause",
        "resume",
        "cancel",
        "launch_dynamic_run",
        "_execute_new",
        "_resume_direct",
        "_resume_graph",
        "_resume_loop",
        "_cancel_execution",
        "_fail_execution",
        "install_dynamic_graph",
        "install_dynamic_replan",
        "start_run",
        "prepare_dynamic_run",
        "replan",
        "propose",
        "validate",
        "activate",
    ],
)
async def test_generic_run_controls_and_workers_cannot_own_a_simulator(
    lab: Lab, tmp_path: Path, operation: str
) -> None:
    ep = await lab.bind()
    manager = manager_for(lab, tmp_path)
    before = await state_snapshot(lab)
    with rejected(Code.EPISODE_STATE_CONFLICT):
        if operation == "start_run":
            await manager.start_run(
                lab.task.envelope.task_id, Provider.FAKE, lab.human.principal_id
            )
        elif operation == "prepare_dynamic_run":
            await manager.prepare_dynamic_run(
                lab.task.envelope.task_id,
                Provider.FAKE,
                required_verifiers=[],
                has_approval_gates=False,
            )
        elif operation in {"replan", "propose", "validate", "activate"}:
            dynamic = DynamicWorkflowService(
                manager, globally_enabled=True, operator_identity="synthetic"
            )
            if operation == "replan":
                await dynamic.replan(lab.run.run_id, reason=ReplanReason.INITIAL, evidence_refs=[])
            elif operation == "propose":
                await dynamic.propose(lab.task.envelope.task_id, execution_provider=Provider.FAKE)
            else:
                await getattr(dynamic, operation)(lab.run.run_id, "unrelated-proposal")
        elif operation in {"install_dynamic_graph", "install_dynamic_replan"}:
            await getattr(manager, operation)(lab.run.run_id, None)
        elif operation == "_fail_execution":
            await manager._fail_execution(lab.run.run_id, RuntimeError("synthetic"))
        else:
            await getattr(manager, operation)(lab.run.run_id)
    assert await state_snapshot(lab) == before
    assert await lab.state.list_events(ep.run_id) == []
    assert (
        manager.background == {} and manager.active_refs == {} and manager.pause_requested == set()
    )


async def test_generic_reconcile_leaves_bound_run_and_episode_untouched(
    lab: Lab, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await lab.bind()
    manager = manager_for(lab, tmp_path)
    before = await state_snapshot(lab)

    # Scope this ownership witness to its actual persisted run. Separate
    # test-isolation regressions exercise unfiltered reconciliation and startup.
    async def inventory(limit: int = 100) -> list[Any]:
        return [await lab.state.get_run(lab.run.run_id)]

    monkeypatch.setattr(lab.state, "list_runs", inventory)
    await manager.reconcile()
    assert await state_snapshot(lab) == before
    assert await lab.state.list_events(lab.run.run_id) == []
    assert manager.background == {}


@pytest.mark.parametrize("operation", ["pause", "start"])
async def test_atomic_binding_published_during_resource_read_cannot_escape_ownership_guard(
    lab: Lab, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    manager = manager_for(lab, tmp_path)
    method = "get_run" if operation == "pause" else "get_task"
    original_read = getattr(lab.state, method)
    published = False

    async def publish_then_read(identity: str) -> Any:
        nonlocal published
        if not published:
            published = True
            await lab.bind()  # Commit the real atomic task/run/binding between reads.
        return await original_read(identity)

    monkeypatch.setattr(lab.state, method, publish_then_read)
    with rejected(Code.EPISODE_STATE_CONFLICT):
        if operation == "pause":
            await manager.pause(lab.run.run_id)
        else:
            await manager.start_run(
                lab.task.envelope.task_id, Provider.FAKE, lab.human.principal_id
            )
    run = await lab.state.get_run(lab.run.run_id)
    assert published and run and run.state is RunState.PENDING
    assert (await lab.episode()).status == "BOUND"
    assert await lab.state.get_task_planning(lab.task.envelope.task_id) is None
    assert await lab.state.list_events(lab.run.run_id) == []


async def test_http_binding_published_during_read_still_requires_current_membership(
    lab: Lab, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = manager_for(lab, tmp_path)
    outsider = await lab.state.upsert_principal(
        Principal(
            principal_id=new_id("principal"),
            issuer="synthetic.invalid",
            subject=new_id("principal"),
        )
    )
    auth = build_auth_runtime(lab.state, Settings(auth_mode="LOCAL_PRINCIPAL"))
    auth.local_principal_cache = outsider
    monkeypatch.setattr(app.state, "manager", manager, raising=False)
    monkeypatch.setattr(app.state, "auth", auth, raising=False)
    original_read = lab.state.get_run
    published = False

    async def publish_then_read(identity: str) -> Any:
        nonlocal published
        if not published:
            published = True
            await lab.bind()
        return await original_read(identity)

    monkeypatch.setattr(lab.state, "get_run", publish_then_read)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/runs/{lab.run.run_id}/pause")
    assert response.status_code == 404 and response.json()["code"] == "NOT_FOUND"
    assert lab.binding.episode_id not in response.text
    run = await original_read(lab.run.run_id)
    assert published and run and run.state is RunState.PENDING


@pytest.mark.parametrize("target", ["pause", "resume", "cancel", "audit", "replan", "start"])
async def test_generic_http_checks_membership_before_ownership_disclosure(
    lab: Lab, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    await lab.bind()
    manager = manager_for(lab, tmp_path)
    auth = build_auth_runtime(lab.state, Settings(auth_mode="LOCAL_PRINCIPAL"))
    auth.local_principal_cache = lab.human
    monkeypatch.setattr(app.state, "manager", manager, raising=False)
    monkeypatch.setattr(app.state, "auth", auth, raising=False)
    before = await state_snapshot(lab)
    path = f"/api/v1/runs/{lab.run.run_id}/{target}"
    if target == "replan":
        path = f"/api/v2/runs/{lab.run.run_id}/replan"
    elif target == "start":
        path = f"/api/v1/tasks/{lab.task.envelope.task_id}/runs"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.request("GET" if target == "audit" else "POST", path, json={})
        assert result.status_code == 409 and result.json()["code"] == "EPISODE_STATE_CONFLICT"
        outsider = await lab.state.upsert_principal(
            Principal(
                principal_id=new_id("principal"),
                issuer="synthetic.invalid",
                subject=new_id("principal"),
            )
        )
        auth.local_principal_cache = outsider
        foreign = await client.request("GET" if target == "audit" else "POST", path, json={})
        assert foreign.status_code == 404 and foreign.json()["code"] == "NOT_FOUND"
        assert lab.binding.episode_id not in foreign.text and lab.workspace not in foreign.text
        if target in {"pause", "resume", "cancel", "audit"}:
            missing_id = new_id("run")
            missing = await client.request(
                "GET" if target == "audit" else "POST",
                path.replace(lab.run.run_id, missing_id),
                json={},
            )
            assert missing.status_code == foreign.status_code
            assert missing.json()["code"] == foreign.json()["code"]
            assert missing.json()["message"].replace(missing_id, "ID") == (
                foreign.json()["message"].replace(lab.run.run_id, "ID")
            )
    assert await state_snapshot(lab) == before
