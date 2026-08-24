from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from accretion.api.main import app
from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import ApprovalDecisionValue, ApprovalStatus, EventType, Provider, RunState
from accretion.orchestration.models import (
    GraphValidationStatus,
    ReplanReason,
)
from accretion.orchestration.service import DynamicWorkflowService
from accretion.persistence.store import MemoryStore
from accretion.runtimes.fake import FakeRuntime
from accretion.services.run_manager import RunManager
from accretion.workspace import WorktreeManager


def initialize_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Accretion Test"],
        check=True,
    )
    (path / "README.md").write_text("P5 fixture\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)


def build_service(
    tmp_path: Path, *, runtime: FakeRuntime | None = None
) -> tuple[RunManager, DynamicWorkflowService]:
    manager = RunManager(
        store=MemoryStore(),
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: runtime or FakeRuntime()},
        limiter=ConcurrencyLimiter(global_limit=2, provider_limit=2, project_limit=2),
        live_providers_enabled=False,
    )
    return manager, DynamicWorkflowService(
        manager, globally_enabled=True, operator_identity="p5-test"
    )


async def create_enabled_task(
    manager: RunManager, service: DynamicWorkflowService, repository: Path
) -> tuple[str, str]:
    project = await manager.create_project("P5", repository)
    current = await service.get_project_features(project.project_id)
    enabled = await service.update_project_features(
        project.project_id,
        dynamic_workflows=True,
        expected_revision=current.revision,
    )
    assert enabled.dynamic_workflows and enabled.revision == current.revision + 1
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Review the existing P5 fixture through a validated dynamic graph.",
        task_patch={
            "task_type": "REVIEW",
            "required_outputs": [{"path": "README.md", "kind": "file"}],
        },
    )
    return project.project_id, task.envelope.task_id


async def test_p5_proposal_validation_activation_and_runtime_evidence(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    manager, service = build_service(tmp_path)
    _, task_id = await create_enabled_task(manager, service, repository)

    proposal = await service.propose(task_id, execution_provider=Provider.FAKE)
    assert proposal.run_id is not None
    outcome = await service.validate(proposal.run_id, proposal.proposal_id)
    assert outcome.validation.status is GraphValidationStatus.ACCEPT
    activation = await service.activate(proposal.run_id, proposal.proposal_id)
    assert activation.revision.revision == 1
    assert activation.revision.proposal_id == proposal.proposal_id

    await manager.background[proposal.run_id]
    run = await manager.store.get_run(proposal.run_id)
    assert run is not None and run.state is RunState.SUCCEEDED
    revisions = await manager.store.list_graph_revisions(proposal.run_id)
    decisions = await manager.store.list_runtime_decisions(proposal.run_id)
    events = await manager.store.list_events(proposal.run_id)
    assert revisions == [activation.revision]
    assert decisions[0].selected_runtime is Provider.FAKE
    assert decisions[0].policy_version == "performance-router-v2"
    assert EventType.GRAPH_REVISION_ACTIVATED in {
        event.normalized_type for event in events
    }


@pytest.mark.acceptance("V02-P5-002")
async def test_invalid_proposal_falls_back_once_to_static_template(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    manager, service = build_service(tmp_path)
    _, task_id = await create_enabled_task(manager, service, repository)
    proposal = await service.propose(task_id, execution_provider=Provider.FAKE)
    assert proposal.run_id is not None
    invalid = proposal.model_copy(update={"required_capabilities": ["unknown-root-shell"]})
    assert isinstance(manager.store, MemoryStore)
    manager.store.workflow_proposals[proposal.proposal_id] = invalid

    outcome = await service.validate(proposal.run_id, proposal.proposal_id)

    assert outcome.validation.status is GraphValidationStatus.REJECT
    assert outcome.fallback_run_id is not None
    dynamic = await manager.store.get_run(proposal.run_id)
    fallback = await manager.store.get_run(outcome.fallback_run_id)
    assert dynamic is not None and dynamic.state is RunState.CANCELLED
    assert fallback is not None and fallback.workflow_template_id is not None
    await manager.background[outcome.fallback_run_id]


@pytest.mark.acceptance("V02-P5-004")
async def test_high_risk_dynamic_node_cannot_start_before_gate(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    manager, service = build_service(tmp_path)
    project = await manager.create_project("P5 high risk", repository)
    await service.update_project_features(
        project.project_id, dynamic_workflows=True, expected_revision=1
    )
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Apply a protected change only after explicit approval.",
        task_patch={
            "task_type": "REVIEW",
            "risk_level": "HIGH",
            "required_outputs": [{"path": "README.md", "kind": "file"}],
        },
    )
    proposal = await service.propose(
        task.envelope.task_id, execution_provider=Provider.FAKE
    )
    assert proposal.run_id is not None
    validation = await service.validate(proposal.run_id, proposal.proposal_id)
    assert validation.validation.status is GraphValidationStatus.ACCEPT
    await service.activate(proposal.run_id, proposal.proposal_id)
    approval_id: str | None = None
    for _ in range(200):
        approvals = await manager.store.list_approvals(
            proposal.run_id, ApprovalStatus.PENDING
        )
        if approvals:
            approval_id = approvals[0].approval_id
            break
        await asyncio.sleep(0.01)
    assert approval_id is not None
    events = await manager.store.list_events(proposal.run_id)
    assert not any(
        event.normalized_type is EventType.NODE_ENTERED
        and event.node_id == f"{proposal.run_id}:act"
        for event in events
    )
    await manager.resolve_approval(approval_id, ApprovalDecisionValue.DENY)
    await manager.background[proposal.run_id]
    run = await manager.store.get_run(proposal.run_id)
    assert run is not None and run.state is RunState.REQUIRES_HUMAN


@pytest.mark.acceptance("V02-P5-008")
async def test_mid_run_replan_creates_immutable_revision_and_preserves_state(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    manager, service = build_service(tmp_path, runtime=FakeRuntime(step_delay=0.1))
    _, task_id = await create_enabled_task(manager, service, repository)
    proposal = await service.propose(task_id, execution_provider=Provider.FAKE)
    assert proposal.run_id is not None
    validation = await service.validate(proposal.run_id, proposal.proposal_id)
    assert validation.validation.status is GraphValidationStatus.ACCEPT
    initial = await service.activate(proposal.run_id, proposal.proposal_id)

    for _ in range(200):
        events = await manager.store.list_events(proposal.run_id)
        if any(
            event.normalized_type is EventType.NODE_EXITED
            and event.node_id == f"{proposal.run_id}:start"
            for event in events
        ):
            break
        await asyncio.sleep(0.01)
    await manager.pause(proposal.run_id)
    background = manager.background.get(proposal.run_id)
    if background is not None:
        await background
    paused = await manager.store.get_run(proposal.run_id)
    assert paused is not None and paused.state is RunState.PAUSED

    replanned = await service.replan(
        proposal.run_id,
        reason=ReplanReason.HUMAN_REQUEST,
        evidence_refs=["operator:test-replan"],
    )
    assert replanned.revision is not None
    assert replanned.revision.revision == 2
    assert replanned.revision.parent_revision == 1
    assert replanned.revision.protected_state_refs
    assert initial.revision.activated_at == (
        await manager.store.get_graph_revision(proposal.run_id, 1)
    ).activated_at
    diff = await service.graph_diff(proposal.run_id, 1, 2)
    assert diff.removed_nodes == []
    assert diff.protected_state_refs == replanned.revision.protected_state_refs
    resumed = manager.background.get(proposal.run_id)
    if resumed is not None:
        await resumed


@pytest.mark.acceptance("V02-P5-001")
async def test_p5_api_surface_is_additive_and_project_gated(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    manager, service = build_service(tmp_path)
    app.state.manager = manager
    app.state.dynamic_workflows = service
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        project = (
            await client.post(
                "/api/v1/projects",
                json={"name": "P5 API", "repository_path": str(repository)},
            )
        ).json()
        features = await client.get(f"/api/v2/projects/{project['project_id']}/features")
        assert features.status_code == 200
        enabled = await client.patch(
            f"/api/v2/projects/{project['project_id']}/features",
            json={"dynamic_workflows": True, "expected_revision": 1},
        )
        assert enabled.status_code == 200
        task = (
            await client.post(
                "/api/v1/tasks",
                json={
                    "project_id": project["project_id"],
                    "objective": "Inspect the P5 API.",
                    "task_type": "REVIEW",
                    "required_outputs": [{"path": "README.md", "kind": "file"}],
                },
            )
        ).json()
        task_id = task["envelope"]["task_id"]
        proposed = await client.post(
            f"/api/v2/tasks/{task_id}/workflow/propose",
            json={"execution_provider": "FAKE", "planner_runtime": "DETERMINISTIC"},
        )
        assert proposed.status_code == 201
        proposal = proposed.json()
        run_id = proposal["run_id"]
        validated = await client.post(
            f"/api/v2/runs/{run_id}/workflow/proposals/{proposal['proposal_id']}/validate"
        )
        assert validated.status_code == 200
        assert validated.json()["validation"]["status"] == "ACCEPT"
        listed = await client.get(f"/api/v2/runs/{run_id}/workflow/proposals")
        decisions = await client.get(f"/api/v2/runs/{run_id}/runtime-decisions")
        assert len(listed.json()) == len(decisions.json()) == 1
