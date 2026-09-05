"""SDD §11.1 HTTP contract for M2 deterministic node routing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from accretion.api.routing import router
from accretion.contracts import Principal, PrincipalRef, PrincipalStatus
from accretion.contracts.routing import ConfigurationCandidate, RoutingDecisionReceipt
from accretion.ids import new_id
from accretion.routing.errors import RoutingError
from accretion.routing.protocols import RoutingMode

pytestmark = pytest.mark.asyncio

FIXTURES = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"
T = TypeVar("T")


def _fixture(model: type[Any], name: str) -> Any:
    return model.model_validate(json.loads((FIXTURES / name / "complete.json").read_text()))


class RecordingRoutingService:
    def __init__(self) -> None:
        self.receipt = _fixture(RoutingDecisionReceipt, "routing_decision_receipt")
        self.candidates = [_fixture(ConfigurationCandidate, "configuration_candidate")]
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.failure: RoutingError | None = None

    async def _answer(self, name: str, values: dict[str, Any], answer: T) -> T:
        self.calls.append((name, values))
        if self.failure is not None:
            raise self.failure
        return answer

    async def route_execution(self, **values: Any) -> RoutingDecisionReceipt:
        return await self._answer("route_execution", values, self.receipt)

    async def get_receipt(self, **values: Any) -> RoutingDecisionReceipt:
        return await self._answer("get_receipt", values, self.receipt)

    async def candidates_for(self, **values: Any) -> list[ConfigurationCandidate]:
        return await self._answer("candidates_for", values, self.candidates)

    async def override(self, **values: Any) -> RoutingDecisionReceipt:
        return await self._answer("override", values, self.receipt)

    async def cancel(self, **values: Any) -> RoutingDecisionReceipt:
        return await self._answer("cancel", values, self.receipt)


def _app(service: RecordingRoutingService | None = None) -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    if service is not None:
        application.state.node_routing = service

    @application.middleware("http")
    async def attach_principal(request: Request, call_next: Any) -> Any:
        request.state.principal = Principal(
            principal_id=new_id("principal"),
            issuer="test",
            subject="operator",
            display_name="Test Operator",
        )
        return await call_next(request)

    @application.exception_handler(RoutingError)
    async def routing_failure(request: Request, exc: RoutingError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
        )

    return application


async def _client(
    service: RecordingRoutingService | None = None,
) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=_app(service)), base_url="http://test"
    )


def _route_payload() -> dict[str, Any]:
    return {
        "routing_request_id": new_id("routing_request"),
        "node_contract_id": new_id("node_contract"),
        "expected_node_contract_hash": "a" * 64,
        "mode": "BASELINE_ONLY",
        "expected_registry_snapshot_id": new_id("mcp_snapshot"),
    }


async def test_route_delegates_every_pinned_input_and_attributed_principal() -> None:
    service = RecordingRoutingService()
    payload = _route_payload()
    async with await _client(service) as client:
        response = await client.post(
            "/api/v1/projects/prj_visible/node-executions/exe_attempt_1/route",
            json=payload,
        )

    assert response.status_code == 200
    assert response.json()["contract_id"] == service.receipt.contract_id
    name, values = service.calls.pop()
    assert name == "route_execution"
    assert values | {} == {
        "project_id": "prj_visible",
        "execution_instance_id": "exe_attempt_1",
        "routing_request_id": payload["routing_request_id"],
        "node_contract_id": payload["node_contract_id"],
        "expected_node_contract_hash": "a" * 64,
        "mode": RoutingMode.BASELINE_ONLY,
        "expected_registry_snapshot_id": payload["expected_registry_snapshot_id"],
        "principal": values["principal"],
    }
    assert isinstance(values["principal"], PrincipalRef)
    assert values["principal"].display_name == "Test Operator"
    assert values["principal"].status is PrincipalStatus.ACTIVE


async def test_receipt_and_candidates_are_read_through_attributed_service() -> None:
    service = RecordingRoutingService()
    async with await _client(service) as client:
        receipt = await client.get("/api/v1/routing-decisions/rcp_visible")
        candidates = await client.get(
            "/api/v1/routing-decisions/rcp_visible/candidates"
        )

    assert receipt.status_code == 200
    assert receipt.json()["contract_id"] == service.receipt.contract_id
    assert candidates.status_code == 200
    assert candidates.json()[0]["contract_id"] == service.candidates[0].contract_id
    assert [name for name, _ in service.calls] == ["get_receipt", "candidates_for"]
    assert all(isinstance(call[1]["principal"], PrincipalRef) for call in service.calls)


async def test_override_is_compare_and_set_and_cancel_is_attributed() -> None:
    service = RecordingRoutingService()
    candidate_id = new_id("configuration_candidate")
    async with await _client(service) as client:
        override = await client.post(
            "/api/v1/routing-decisions/rcp_visible/override",
            json={
                "candidate_id": candidate_id,
                "reason_code": "EXPERIMENTAL_COMPARISON",
                "reason": "Compare a compatible lower-cost baseline.",
                "expected_receipt_version": 3,
            },
        )
        cancel = await client.post("/api/v1/routing-decisions/rcp_visible/cancel")

    assert override.status_code == cancel.status_code == 200
    override_call = service.calls[0]
    assert override_call[0] == "override"
    assert override_call[1]["candidate_id"] == candidate_id
    assert override_call[1]["expected_receipt_version"] == 3
    assert service.calls[1][0] == "cancel"
    assert isinstance(service.calls[1][1]["principal"], PrincipalRef)


@pytest.mark.parametrize("mode", ["AUTO", "SHADOW"])
async def test_m2_explicitly_rejects_unavailable_routing_modes(mode: str) -> None:
    service = RecordingRoutingService()
    payload = _route_payload() | {"mode": mode}
    async with await _client(service) as client:
        response = await client.post(
            "/api/v1/projects/prj_visible/node-executions/exe_attempt_1/route",
            json=payload,
        )

    assert response.status_code == 422
    assert response.json()["code"] == "ROUTING_MODE_UNSUPPORTED"
    assert service.calls == []


async def test_service_failures_preserve_typed_status_and_code() -> None:
    service = RecordingRoutingService()
    service.failure = RoutingError("RECEIPT_VERSION_CONFLICT", "stale receipt", 409)
    async with await _client(service) as client:
        response = await client.post(
            "/api/v1/routing-decisions/rcp_visible/cancel"
        )

    assert response.status_code == 409
    assert response.json() == {
        "code": "RECEIPT_VERSION_CONFLICT",
        "message": "stale receipt",
    }


async def test_unwired_routing_service_is_a_typed_conflict() -> None:
    async with await _client() as client:
        response = await client.get("/api/v1/routing-decisions/rcp_anything")

    assert response.status_code == 409
    assert response.json()["code"] == "NODE_ROUTING_UNAVAILABLE"
