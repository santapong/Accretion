from httpx import AsyncClient


async def test_health_and_session_flow(client: AsyncClient, settings) -> None:  # type: ignore[no-untyped-def]
    health = await client.get("/api/v1/health")
    assert health.status_code == 200
    workspace = settings.workspace_roots[0] / "workspace"
    response = await client.post(
        "/api/v1/sessions",
        json={"provider": "codex", "cwd": str(workspace), "prompt": "Hello agent"},
    )
    assert response.status_code == 201, response.text
    session = response.json()
    detail = await client.get(f"/api/v1/sessions/{session['id']}")
    assert detail.status_code == 200
    assert detail.json()["managed"] is True


async def test_workspace_boundary_is_enforced(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/sessions",
        json={"provider": "codex", "cwd": "/", "prompt": "Not allowed"},
    )
    assert response.status_code == 422
