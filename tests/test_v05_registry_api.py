"""Scoped HTTP and bounded input witnesses; no runtime/conformance acceptance claim."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from test_v05_registry import Scope, chain
from test_v05_registry import scope as shared_scope

from accretion.api.auth import build_auth_runtime
from accretion.api.main import app
from accretion.api.robotics import build_registry, writer_json
from accretion.config import Settings
from accretion.contracts import PrincipalStatus, Project
from accretion.contracts.canonical import content_hash
from accretion.contracts.robotics import CanonicalWriterEnvelope, EmbodimentDescriptor
from accretion.identity import LOCAL_WORKSPACE_ID
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code

registry_scope = shared_scope


@pytest.fixture
async def api(registry_scope: Scope, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    auth = build_auth_runtime(registry_scope.state, Settings(auth_mode="LOCAL_PRINCIPAL"))
    auth.local_principal_cache = registry_scope.actor
    monkeypatch.setattr(app.state, "auth", auth, raising=False)
    monkeypatch.setattr(app.state, "robotics_registry", registry_scope.registry, raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def params(scope: Scope) -> dict[str, str]:
    return {"workspace_id": scope.workspace, "project_id": scope.project}


def raw(scope: Scope) -> str:
    descriptor = scope.build(EmbodimentDescriptor)
    return CanonicalWriterEnvelope.from_contract(descriptor).original_json


async def post(api: AsyncClient, scope: Scope, original: str, **overrides: Any) -> Any:
    args: dict[str, Any] = {
        "params": params(scope),
        "content": original,
        "headers": {"Content-Type": "application/json", "Idempotency-Key": "registry-api-test"},
    }
    args.update(overrides)
    return await api.post("/api/v1/embodiments", **args)


async def test_original_minor_writer_survives_http_storage_pagination_and_idempotency(
    api: AsyncClient, registry_scope: Scope
) -> None:
    payload = json.loads(raw(registry_scope))
    payload.update(schema_version="1.1.0", future_minor_value={"counter": 7})
    payload["content_hash"] = content_hash(payload)
    original = json.dumps(payload, indent=3) + "\n"
    created = await post(api, registry_scope, original)
    assert created.status_code == 201, created.text
    assert created.headers["etag"] == '"1"'
    assert created.json()["original_json"] == original
    replay = await post(api, registry_scope, original)
    assert replay.json() == created.json()
    changed = await post(api, registry_scope, raw(registry_scope))
    assert changed.status_code == 409
    assert changed.json()["code"] == "IDEMPOTENCY_CONFLICT"
    for index in range(2):
        result = await post(
            api,
            registry_scope,
            raw(registry_scope),
            headers={"Content-Type": "application/json", "Idempotency-Key": f"new-{index}"},
        )
        # New logical/version slots must remain immutable even under new IDs.
        assert result.status_code == 409
    listing = await api.get("/api/v1/embodiments", params={**params(registry_scope), "limit": 1})
    assert listing.status_code == 200 and len(listing.json()["items"]) == 1
    assert listing.headers["cache-control"] == "no-store"
    item = listing.json()["items"][0]
    detail = await api.get(
        f"/api/v1/embodiments/{item['contract_id']}/versions/{item['version']}",
        params=params(registry_scope),
    )
    assert detail.status_code == 200 and detail.json()["original_json"] == original
    wrong = await api.get(
        f"/api/v1/embodiments/{item['contract_id']}/versions/99.0.0", params=params(registry_scope)
    )
    assert wrong.status_code == 404 and item["contract_id"] not in wrong.text


@pytest.mark.parametrize(
    "change, expected",
    [
        ({"headers": {"Content-Type": "application/json"}}, 428),
        (
            {
                "headers": {
                    "Content-Type": "application/json",
                    "Idempotency-Key": " ",
                }
            },
            422,
        ),
        (
            {
                "headers": {
                    "Content-Type": "application/json",
                    "Idempotency-Key": "x",
                    "If-Match": '"1"',
                }
            },
            422,
        ),
        ({"headers": {"Content-Type": "text/plain", "Idempotency-Key": "x"}}, 422),
        (
            {
                "headers": {
                    "Content-Type": "application/json",
                    "Idempotency-Key": "x",
                    "Content-Encoding": "gzip",
                }
            },
            422,
        ),
        ({"content": "{"}, 422),
        ({"content": b"\xff"}, 422),
        ({"content": "x" * 1_048_577}, 413),
    ],
)
async def test_bad_request_is_refused_without_writing(
    api: AsyncClient, registry_scope: Scope, change: dict[str, Any], expected: int
) -> None:
    response = await post(api, registry_scope, raw(registry_scope), **change)
    assert response.status_code == expected, response.text
    assert response.json()["recovery_action"] and response.json()["correlation_id"]
    assert not (await api.get("/api/v1/embodiments", params=params(registry_scope))).json()["items"]


async def test_authority_collection_and_ambiguous_headers_fail_closed(
    api: AsyncClient, registry_scope: Scope
) -> None:
    original = raw(registry_scope)
    duplicate = await post(
        api,
        registry_scope,
        original,
        headers=[
            ("Content-Type", "application/json"),
            ("Idempotency-Key", "a"),
            ("Idempotency-Key", "b"),
        ],
    )
    assert duplicate.status_code == 422
    duplicate_scope = await post(
        api,
        registry_scope,
        original,
        params=[
            ("workspace_id", registry_scope.workspace),
            ("workspace_id", "another"),
            ("project_id", registry_scope.project),
        ],
    )
    assert duplicate_scope.status_code == 422
    unknown = await post(
        api,
        registry_scope,
        original,
        params={**params(registry_scope), "project_id": new_id("project")},
    )
    assert unknown.status_code == 404 and registry_scope.project not in unknown.text
    wrong_collection = await api.post(
        "/api/v1/robot-adapters",
        params=params(registry_scope),
        content=original,
        headers={"Content-Type": "application/json", "Idempotency-Key": "x"},
    )
    assert wrong_collection.status_code == 422
    await registry_scope.state.upsert_principal(
        registry_scope.actor.model_copy(update={"status": PrincipalStatus.DISABLED})
    )
    disabled = await post(api, registry_scope, original)
    assert disabled.status_code == 403


async def test_unavailable_subsystem_and_foreign_conformance_do_not_grant_authority(
    api: AsyncClient, registry_scope: Scope, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, adapter = await chain(registry_scope)
    route = f"/api/v1/robot-adapters/{adapter.contract_id}/conformance-runs"
    unavailable = await api.post(
        route, params=params(registry_scope), headers={"Idempotency-Key": "x"}
    )
    assert unavailable.status_code == 409 and unavailable.json()["code"] == "SIMULATION_UNAVAILABLE"
    foreign = await api.post(route, params={**params(registry_scope), "workspace_id": "foreign"})
    assert foreign.status_code == 404 and adapter.contract_id not in foreign.text
    monkeypatch.setattr(app.state, "robotics_registry", None)
    disabled = await api.get("/api/v1/embodiments", params=params(registry_scope))
    assert disabled.status_code == 409 and disabled.json()["code"] == "SIMULATION_UNAVAILABLE"


@pytest.mark.parametrize("query", [{}, {"limit": "101"}, {"limit": "0"}, {"cursor": "x" * 2049}])
async def test_query_validation_has_safe_domain_shape(
    api: AsyncClient, registry_scope: Scope, query: dict[str, str]
) -> None:
    values = {**params(registry_scope), **query} if query else {}
    response = await api.get("/api/v1/embodiments", params=values)
    assert response.status_code == 422 and response.json()["code"] == "INVALID_REQUEST"


async def test_local_bootstrap_is_explicit_and_oidc_cannot_claim_legacy_projects(
    tmp_path: Path,
) -> None:
    state = MemoryStore()
    project = Project(project_id=new_id("project"), name="Local", repository_path=tmp_path)
    await state.create_project(project)
    settings = Settings(enable_simulation=False, simulation_local_project_ids=[project.project_id])
    auth = build_auth_runtime(state, settings)
    assert await build_registry(state, settings, auth) is None
    settings.enable_simulation = True
    auth.mode = "OIDC"
    with pytest.raises(RoboticsError) as refused:
        await build_registry(state, settings, auth)
    assert refused.value.code is Code.CAPABILITY_DENIED
    auth.mode = "LOCAL_PRINCIPAL"
    registry = await build_registry(state, settings, auth)
    assert registry is not None
    who = await auth.identity.local_principal()
    page = await registry.list(
        actor_id=who.principal_id,
        workspace_id=LOCAL_WORKSPACE_ID,
        project_id=project.project_id,
        contract_type=EmbodimentDescriptor.CONTRACT_TYPE,
    )
    assert page.items == []
    assert registry.artifact_verifier is None and registry.conformance_authority is None


async def test_streamed_body_limit_and_deadline_apply_without_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from accretion.api import robotics

    def request(receive: Any) -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/",
                "headers": [(b"content-type", b"application/json")],
            },
            receive=receive,
        )

    async def flood() -> dict[str, Any]:
        return {"type": "http.request", "body": b"x" * 65_536, "more_body": True}

    with pytest.raises(RoboticsError) as caught:
        await writer_json(request(flood), EmbodimentDescriptor.CONTRACT_TYPE)
    assert caught.value.code is Code.PAYLOAD_TOO_LARGE
    monkeypatch.setattr(robotics, "BODY_TIMEOUT_SECONDS", 0.01)

    async def stalled() -> dict[str, Any]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    with pytest.raises(RoboticsError) as caught:
        await writer_json(request(stalled), EmbodimentDescriptor.CONTRACT_TYPE)
    assert caught.value.code is Code.INVALID_REQUEST
