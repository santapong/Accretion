from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from accretion.api.main import app
from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import ExecutionMode, Provider
from accretion.persistence.store import MemoryStore
from accretion.runtimes.fake import FakeRuntime
from accretion.services.run_manager import MilestoneDependencyError, RunManager
from accretion.workspace import WorktreeManager


def build_manager(tmp_path: Path) -> RunManager:
    return RunManager(
        store=MemoryStore(),
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: FakeRuntime()},
        limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
        live_providers_enabled=False,
    )


async def graph_decision_task(manager: RunManager, tmp_path: Path) -> str:
    """A HIGH-risk task deterministically selects GRAPH/fixed-graph-v1."""

    project = await manager.create_project("fixture", tmp_path)
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Apply an irreversible high-risk change.",
        task_patch={"task_type": "IMPLEMENT", "risk_level": "HIGH"},
    )
    planning = await manager.get_task_planning(task.envelope.task_id)
    assert planning.current_decision.selected_mode is ExecutionMode.GRAPH
    assert planning.current_decision.selected_template_id == "fixed-graph-v1"
    return task.envelope.task_id


async def test_graph_decision_start_raises_milestone_dependency(tmp_path: Path) -> None:
    manager = build_manager(tmp_path)
    task_id = await graph_decision_task(manager, tmp_path)
    with pytest.raises(MilestoneDependencyError) as excinfo:
        await manager.start_run(task_id, Provider.FAKE)
    assert "GRAPH" in str(excinfo.value)
    assert "fixed-graph-v1" in str(excinfo.value)


async def test_graph_decision_start_returns_409_milestone_dependency(tmp_path: Path) -> None:
    manager = build_manager(tmp_path)
    task_id = await graph_decision_task(manager, tmp_path)

    app.state.manager = manager
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/v1/tasks/{task_id}/runs", json={"provider": "FAKE"})

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "MILESTONE_DEPENDENCY"
    assert "fixed-graph-v1" in body["message"]
    assert body["correlation_id"]

    runs = await manager.store.list_runs()
    assert runs == []


async def test_safe_unknown_decision_start_returns_409_milestone_dependency(
    tmp_path: Path,
) -> None:
    manager = build_manager(tmp_path)
    project = await manager.create_project("fixture", tmp_path)
    task = await manager.create_task(
        project_id=project.project_id,
        objective="Do something with no typed classification.",
        task_patch={"task_type": "OTHER"},
    )
    planning = await manager.get_task_planning(task.envelope.task_id)
    assert planning.current_decision.selected_mode is ExecutionMode.HYBRID
    assert planning.current_decision.selected_template_id == "safe-unknown-v1"

    app.state.manager = manager
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/tasks/{task.envelope.task_id}/runs", json={"provider": "FAKE"}
        )

    assert response.status_code == 409
    assert response.json()["code"] == "MILESTONE_DEPENDENCY"
