from __future__ import annotations

import subprocess
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from accretion.api.main import app
from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import Provider
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
    (path / "README.md").write_text("fixture\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)


def manager(tmp_path: Path) -> RunManager:
    return RunManager(
        store=MemoryStore(),
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: FakeRuntime()},
        limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
        live_providers_enabled=False,
    )


async def test_planning_api_and_p2_loop_execution(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    run_manager = manager(tmp_path)
    app.state.manager = run_manager
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        project_response = await client.post(
            "/api/v1/projects",
            json={"name": "fixture", "repository_path": str(repository)},
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["project_id"]
        projects_response = await client.get("/api/v1/projects")
        assert [item["project_id"] for item in projects_response.json()] == [project_id]
        task_response = await client.post(
            "/api/v1/tasks",
            json={
                "project_id": project_id,
                "objective": "Investigate a bounded question.",
                "task_type": "RESEARCH",
                "risk_level": "LOW",
            },
        )
        assert task_response.status_code == 201
        task_id = task_response.json()["envelope"]["task_id"]

        planning_response = await client.get(f"/api/v1/tasks/{task_id}/planning")
        assert planning_response.status_code == 200
        assert planning_response.json()["current_decision"]["selected_mode"] == "LOOP"

        run_response = await client.post(f"/api/v1/tasks/{task_id}/runs", json={"provider": "FAKE"})
        assert run_response.status_code == 202
        run_id = run_response.json()["run_id"]
        await run_manager.background[run_id]

        run_snapshot = await client.get(f"/api/v1/runs/{run_id}")
        assert run_snapshot.json()["state"] == "REQUIRES_HUMAN"
        loop_response = await client.get(f"/api/v1/runs/{run_id}/loop")
        assert loop_response.status_code == 200
        assert loop_response.json()["state"]["iteration"] == 1
        assert loop_response.json()["stop_reason"] == "POLICY_ESCALATION"
        graph_response = await client.get(f"/api/v1/runs/{run_id}/graph")
        assert graph_response.status_code == 200
        loop_edges = [
            edge for edge in graph_response.json()["edges"] if edge["kind"] == "LOOP_BACK"
        ]
        assert loop_edges[0]["traversal_count"] == 0
        verifications = await client.get(f"/api/v1/runs/{run_id}/verifications")
        assert {item["status"] for item in verifications.json()} == {
            "PASS",
            "INCONCLUSIVE",
        }


async def test_denied_override_is_returned_and_current_decision_is_unchanged(
    tmp_path: Path,
) -> None:
    app.state.manager = manager(tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        project = (
            await client.post(
                "/api/v1/projects", json={"name": "fixture", "repository_path": str(tmp_path)}
            )
        ).json()
        task = (
            await client.post(
                "/api/v1/tasks",
                json={
                    "project_id": project["project_id"],
                    "objective": "Review a critical release.",
                    "task_type": "REVIEW",
                    "risk_level": "HIGH",
                },
            )
        ).json()
        task_id = task["envelope"]["task_id"]
        response = await client.post(
            f"/api/v1/tasks/{task_id}/strategy-overrides",
            json={
                "requested_mode": "DIRECT",
                "requested_template_id": "direct-v1",
                "reason": "Prefer a shorter execution.",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["override"]["accepted"] is False
        assert body["current_decision"]["selected_template_id"] == "fixed-graph-v1"
