from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from accretion.api.main import app
from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    ApprovalDecisionValue,
    ApprovalStatus,
    EventType,
    Provider,
    RunState,
    SessionRef,
    TemplateStatus,
)
from accretion.persistence.store import MemoryStore
from accretion.runtimes.fake import FakeCallOutcome, FakeRuntime
from accretion.services.run_manager import RunManager, WorkflowTemplateError
from accretion.templates import seed_templates
from accretion.workspace import WorktreeManager


def build_manager(
    tmp_path: Path,
    *,
    store: MemoryStore | None = None,
    runtime: FakeRuntime | None = None,
) -> RunManager:
    return RunManager(
        store=store or MemoryStore(),
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: runtime or FakeRuntime()},
        limiter=ConcurrencyLimiter(global_limit=2, provider_limit=2, project_limit=2),
        live_providers_enabled=False,
    )


def initialize_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Accretion Test"], check=True)
    (path / "result.json").write_text('{"ok": false}\n')
    subprocess.run(["git", "-C", str(path), "add", "result.json"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)


def write_valid_output(session: SessionRef, _request: object) -> None:
    (session.workspace / "result.json").write_text('{"ok": true}\n')


def write_invalid_output(session: SessionRef, _request: object) -> None:
    (session.workspace / "result.json").write_text("not-json\n")


async def wait_for_approval(manager: RunManager, run_id: str) -> str:
    for _ in range(200):
        pending = await manager.store.list_approvals(run_id, ApprovalStatus.PENDING)
        if pending:
            return pending[0].approval_id
        await asyncio.sleep(0.02)
    raise AssertionError("no pending approval appeared")


async def graph_task(manager: RunManager, repository: Path) -> str:
    project = await manager.create_project("fixture", repository)
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Apply a high-risk change with plan and outcome approval.",
        task_patch={
            "task_type": "OTHER",
            "risk_level": "HIGH",
            "required_outputs": [{"path": "result.json", "kind": "json"}],
        },
    )
    planning = await manager.get_task_planning(task.envelope.task_id)
    assert planning.current_decision.selected_template_id == "fixed-graph-v1"
    return task.envelope.task_id


# --- fail-closed template guards (permanent form of the former 409 pin) ---


async def test_unknown_template_fails_closed(tmp_path: Path) -> None:
    store = MemoryStore()
    manager = build_manager(tmp_path, store=store)
    project = await manager.create_project("fixture", tmp_path)
    task = await manager.create_task(
        project_id=project.project_id,
        objective="A corrupted decision names a ghost template.",
        task_patch={"task_type": "REVIEW"},
    )
    task_id = task.envelope.task_id
    planning = await manager.get_task_planning(task_id)
    ghost = planning.current_decision.model_copy(
        update={"selected_template_id": "ghost-v1"}
    )
    store.decisions[task_id] = [ghost]
    store.tasks[task_id] = store.tasks[task_id].model_copy(
        update={"current_strategy_decision_id": ghost.decision_id}
    )
    with pytest.raises(WorkflowTemplateError) as excinfo:
        await manager.start_run(task_id, Provider.FAKE)
    assert excinfo.value.code == "TEMPLATE_UNKNOWN"
    assert await manager.store.list_runs() == []


@pytest.mark.acceptance("V01-P3-001")
async def test_non_validated_template_returns_409(tmp_path: Path) -> None:
    store = MemoryStore()
    manager = build_manager(tmp_path, store=store)
    await seed_templates(store)
    direct = store.workflow_templates[("direct-v1", "1.0.0")]
    store.workflow_templates[("direct-v1", "1.0.0")] = direct.model_copy(
        update={"status": TemplateStatus.RETIRED}
    )
    project = await manager.create_project("fixture", tmp_path)
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Deterministic review.",
        task_patch={"task_type": "REVIEW"},
    )
    planning = await manager.get_task_planning(task.envelope.task_id)
    assert planning.current_decision.selected_template_id == "direct-v1"

    app.state.manager = manager
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/tasks/{task.envelope.task_id}/runs", json={"provider": "FAKE"}
        )
    assert response.status_code == 409
    assert response.json()["code"] == "TEMPLATE_NOT_VALIDATED"
    assert await manager.store.list_runs() == []


async def test_checksum_drift_fails_closed(tmp_path: Path) -> None:
    store = MemoryStore()
    manager = build_manager(tmp_path, store=store)
    await seed_templates(store)
    direct = store.workflow_templates[("direct-v1", "1.0.0")]
    store.workflow_templates[("direct-v1", "1.0.0")] = direct.model_copy(
        update={"required_verifiers": ["tampered"]}
    )
    project = await manager.create_project("fixture", tmp_path)
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Deterministic review.",
        task_patch={"task_type": "REVIEW"},
    )
    with pytest.raises(WorkflowTemplateError) as excinfo:
        await manager.start_run(task.envelope.task_id, Provider.FAKE)
    assert excinfo.value.code == "TEMPLATE_CHECKSUM_MISMATCH"


# --- fixed-graph-v1: the risk template executes end to end ---


async def test_fixed_graph_traverses_gates_to_verified_success(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    store = MemoryStore()
    await seed_templates(store)
    runtime = FakeRuntime(
        scripted_outcomes=[
            FakeCallOutcome(),  # plan
            FakeCallOutcome(hook=write_valid_output),  # act
        ]
    )
    manager = build_manager(tmp_path, store=store, runtime=runtime)
    task_id = await graph_task(manager, repository)

    run = await manager.start_run(task_id, Provider.FAKE)
    plan_approval = await wait_for_approval(manager, run.run_id)
    decided = await manager.resolve_approval(plan_approval, ApprovalDecisionValue.APPROVE)
    assert decided.status is ApprovalStatus.APPROVED

    outcome_approval = await wait_for_approval(manager, run.run_id)
    assert outcome_approval != plan_approval
    await manager.resolve_approval(outcome_approval, ApprovalDecisionValue.APPROVE)
    await asyncio.wait_for(manager.background[run.run_id], 10)

    final = await store.get_run(run.run_id)
    assert final is not None and final.state is RunState.SUCCEEDED

    events = await store.list_events(run.run_id)
    entered = [
        event.node_id.rsplit(":", 1)[-1]
        for event in events
        if event.normalized_type is EventType.NODE_ENTERED and event.node_id
    ]
    assert entered == [
        "initialize",
        "plan",
        "approve-plan",
        "act",
        "observe",
        "verify",
        "approve-outcome",
    ]
    assert sum(1 for e in events if e.normalized_type is EventType.CHECKPOINT_SAVED) >= 5
    entered_via = [
        event.payload.get("entered_via")
        for event in events
        if event.normalized_type is EventType.NODE_ENTERED and event.payload.get("entered_via")
    ]
    assert "plan-approve-plan" in entered_via
    assert "verify-approve-outcome" in entered_via
    assert events[-1].normalized_type is EventType.RUN_COMPLETED
    approvals = await store.list_approvals(run.run_id)
    assert {record.status for record in approvals} == {ApprovalStatus.APPROVED}
    assert len(approvals) == 2


async def test_fixed_graph_gate_denial_requires_human(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    store = MemoryStore()
    await seed_templates(store)
    manager = build_manager(tmp_path, store=store)
    task_id = await graph_task(manager, repository)

    run = await manager.start_run(task_id, Provider.FAKE)
    plan_approval = await wait_for_approval(manager, run.run_id)
    await manager.resolve_approval(plan_approval, ApprovalDecisionValue.DENY)
    await asyncio.wait_for(manager.background[run.run_id], 10)

    final = await store.get_run(run.run_id)
    assert final is not None and final.state is RunState.REQUIRES_HUMAN
    events = await store.list_events(run.run_id)
    assert sum(1 for e in events if e.normalized_type is EventType.RUN_FAILED) == 1
    entered = {
        event.node_id.rsplit(":", 1)[-1]
        for event in events
        if event.normalized_type is EventType.NODE_ENTERED and event.node_id
    }
    assert "act" not in entered


async def test_gate_blocks_execution_until_decision(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    store = MemoryStore()
    await seed_templates(store)
    manager = build_manager(tmp_path, store=store)
    task_id = await graph_task(manager, repository)

    run = await manager.start_run(task_id, Provider.FAKE)
    await wait_for_approval(manager, run.run_id)
    await asyncio.sleep(0.1)
    waiting = await store.get_run(run.run_id)
    assert waiting is not None and waiting.state is RunState.RUNNING
    assert not manager.background[run.run_id].done()
    await manager.cancel(run.run_id)


# --- hybrid-rd-v1: nested bounded loops ---


async def test_hybrid_owns_two_bounded_node_scoped_loops(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    store = MemoryStore()
    await seed_templates(store)
    runtime = FakeRuntime(
        scripted_outcomes=[
            FakeCallOutcome(),  # research
            FakeCallOutcome(),  # theorize
            FakeCallOutcome(),  # experiment iteration
            FakeCallOutcome(hook=write_valid_output),  # develop iteration
        ]
    )
    manager = build_manager(tmp_path, store=store, runtime=runtime)
    project = await manager.create_project("fixture", repository)
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Research then develop.",
        task_patch={
            "task_type": "IMPLEMENT",
            "required_outputs": [{"path": "result.json", "kind": "json"}],
        },
    )
    planning = await manager.get_task_planning(task.envelope.task_id)
    assert planning.current_decision.selected_template_id == "hybrid-rd-v1"

    run = await manager.start_run(task.envelope.task_id, Provider.FAKE)
    await asyncio.wait_for(manager.background[run.run_id], 10)

    final = await store.get_run(run.run_id)
    assert final is not None and final.state is RunState.SUCCEEDED
    executions = await store.list_loop_executions_for_run(run.run_id)
    assert sorted(execution.node_key for execution in executions) == ["develop", "experiment"]
    assert all(execution.state.iteration == 1 for execution in executions)
    assert all(execution.status.value == "SUCCEEDED" for execution in executions)


# --- safe-unknown-v1: exactly one bounded replan ---


def safe_unknown_runtime(*, second_attempt_valid: bool) -> FakeRuntime:
    return FakeRuntime(
        scripted_outcomes=[
            FakeCallOutcome(),  # plan
            FakeCallOutcome(hook=write_invalid_output),  # loop attempt 1
            FakeCallOutcome(),  # replan
            FakeCallOutcome(
                hook=write_valid_output if second_attempt_valid else write_invalid_output
            ),  # loop attempt 2
        ]
    )


async def safe_unknown_task(manager: RunManager, repository: Path) -> str:
    project = await manager.create_project("fixture", repository)
    task = await manager.create_task(
        project_id=project.project_id,
        objective="No typed classification is available.",
        task_patch={
            "task_type": "OTHER",
            "required_outputs": [{"path": "result.json", "kind": "json"}],
        },
    )
    planning = await manager.get_task_planning(task.envelope.task_id)
    assert planning.current_decision.selected_template_id == "safe-unknown-v1"
    return task.envelope.task_id


async def test_safe_unknown_replans_exactly_once_then_succeeds(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    store = MemoryStore()
    await seed_templates(store)
    manager = build_manager(
        tmp_path, store=store, runtime=safe_unknown_runtime(second_attempt_valid=True)
    )
    task_id = await safe_unknown_task(manager, repository)

    run = await manager.start_run(task_id, Provider.FAKE)
    await asyncio.wait_for(manager.background[run.run_id], 10)

    final = await store.get_run(run.run_id)
    assert final is not None and final.state is RunState.SUCCEEDED
    executions = await store.list_loop_executions_for_run(run.run_id)
    assert [(execution.node_key, execution.attempt) for execution in executions] == [
        ("loop", 1),
        ("loop", 2),
    ]
    events = await store.list_events(run.run_id)
    replan_entries = sum(
        1
        for event in events
        if event.normalized_type is EventType.NODE_ENTERED
        and event.node_id == f"{run.run_id}:replan"
    )
    assert replan_entries == 1


async def test_safe_unknown_replan_exhaustion_escalates(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    store = MemoryStore()
    await seed_templates(store)
    manager = build_manager(
        tmp_path, store=store, runtime=safe_unknown_runtime(second_attempt_valid=False)
    )
    task_id = await safe_unknown_task(manager, repository)

    run = await manager.start_run(task_id, Provider.FAKE)
    await asyncio.wait_for(manager.background[run.run_id], 10)

    final = await store.get_run(run.run_id)
    assert final is not None and final.state is RunState.REQUIRES_HUMAN
    executions = await store.list_loop_executions_for_run(run.run_id)
    assert [execution.attempt for execution in executions] == [1, 2]
    events = await store.list_events(run.run_id)
    assert events[-1].payload.get("guard") == "ON_REPLAN_EXHAUSTED"


# --- V01-P3-002: no API accepts executable topology ---


@pytest.mark.acceptance("V01-P3-002", "V01-P4-006")
def test_openapi_exposes_no_writable_topology() -> None:
    schema = app.openapi()
    components = schema.get("components", {}).get("schemas", {})

    def walk(node: object, seen: set[str], origin: str) -> None:
        """Transitively assert no reachable request schema exposes nodes/edges."""

        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                name = ref.rsplit("/", 1)[-1]
                if name not in seen:
                    seen.add(name)
                    walk(components.get(name, {}), seen, origin)
                return
            properties = node.get("properties")
            if isinstance(properties, dict):
                assert not {"nodes", "edges"} & set(properties), (
                    f"{origin} accepts executable topology"
                )
                for value in properties.values():
                    walk(value, seen, origin)
            for key in ("items", "additionalProperties"):
                if key in node:
                    walk(node[key], seen, origin)
            for key in ("anyOf", "oneOf", "allOf"):
                for variant in node.get(key, []) or []:
                    walk(variant, seen, origin)

    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method.lower() not in {"post", "put", "patch"}:
                continue
            for content in operation.get("requestBody", {}).get("content", {}).values():
                walk(content.get("schema", {}), set(), f"{method.upper()} {path}")
