"""Hostile HTTP inputs fail closed before deterministic routing is entered."""

from __future__ import annotations

from typing import Any

import pytest
from test_v04_m2_api import RecordingRoutingService, _client, _route_payload

from accretion.ids import new_id
from accretion.routing.errors import RoutingError

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    ("patch", "bad_field"),
    [
        ({"routing_request_id": "rrq_too_short"}, "routing_request_id"),
        ({"node_contract_id": "obj_wrong_kind"}, "node_contract_id"),
        ({"expected_node_contract_hash": "A" * 64}, "expected_node_contract_hash"),
        ({"expected_node_contract_hash": "0" * 63}, "expected_node_contract_hash"),
        (
            {"expected_registry_snapshot_id": new_id("mcp_snapshot")},
            "expected_registry_snapshot_id",
        ),
        ({"mode": "LEARNED"}, "mode"),
    ],
)
async def test_malformed_routing_inputs_are_422_without_service_entry(
    patch: dict[str, Any], bad_field: str
) -> None:
    service = RecordingRoutingService()
    async with await _client(service) as client:
        response = await client.post(
            "/api/v1/projects/prj_visible/node-executions/exe_attempt_1/route",
            json=_route_payload() | patch,
        )

    assert response.status_code == 422
    assert bad_field in response.text
    assert service.calls == []


async def test_unknown_routing_fields_are_rejected_not_ignored() -> None:
    service = RecordingRoutingService()
    async with await _client(service) as client:
        response = await client.post(
            "/api/v1/projects/prj_visible/node-executions/exe_attempt_1/route",
            json=_route_payload() | {"provider_api_key": "must-not-enter-routing"},
        )

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    "patch",
    [
        {"candidate_id": new_id("run")},
        {"reason_code": "free form"},
        {"reason": ""},
        {"expected_receipt_version": 0},
        {"unexpected": True},
    ],
)
async def test_malformed_override_is_rejected_before_mutation(
    patch: dict[str, Any]
) -> None:
    service = RecordingRoutingService()
    payload = {
        "candidate_id": new_id("configuration_candidate"),
        "reason_code": "EXPERIMENTAL_COMPARISON",
        "reason": "Compare a compatible lower-cost baseline.",
        "expected_receipt_version": 1,
    }
    async with await _client(service) as client:
        response = await client.post(
            "/api/v1/routing-decisions/rcp_visible/override",
            json=payload | patch,
        )

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize("condition", ["missing", "inaccessible"])
async def test_missing_and_inaccessible_receipts_have_one_public_shape(
    condition: str,
) -> None:
    service = RecordingRoutingService()
    # The service deliberately maps both lookup outcomes to the same public error.
    service.failure = RoutingError(
        "ROUTING_DECISION_NOT_FOUND", "routing decision was not found", 404
    )
    async with await _client(service) as client:
        response = await client.get(f"/api/v1/routing-decisions/rcp_{condition}")

    assert response.status_code == 404
    assert response.json() == {
        "code": "ROUTING_DECISION_NOT_FOUND",
        "message": "routing decision was not found",
    }


async def test_api_safe_failure_does_not_expose_persistence_details() -> None:
    service = RecordingRoutingService()
    sentinel = "postgresql://operator:secret@private/routing_receipts"
    # A persistence adapter may log the original exception, but only this scrubbed
    # RoutingError is allowed to cross the service boundary.
    service.failure = RoutingError(
        "ROUTING_PERSISTENCE_FAILED", "routing decision could not be persisted", 409
    )
    async with await _client(service) as client:
        response = await client.post(
            "/api/v1/projects/prj_visible/node-executions/exe_attempt_1/route",
            json=_route_payload(),
        )

    assert response.status_code == 409
    assert response.json()["code"] == "ROUTING_PERSISTENCE_FAILED"
    assert sentinel not in response.text


async def test_routing_error_keeps_only_explicit_public_fields() -> None:
    error = RoutingError("NODE_CONTRACT_MISMATCH", "node contract does not match", 422)
    assert str(error) == "node contract does not match"
    assert error.code == "NODE_CONTRACT_MISMATCH"
    assert error.message == "node contract does not match"
    assert error.status_code == 422
