"""HTTP adapter for deterministic v0.4 node routing (SDD §11.1)."""

from __future__ import annotations

from typing import Protocol, cast

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from accretion.api.auth import principal as current_principal
from accretion.contracts import PrincipalRef
from accretion.contracts.routing import ConfigurationCandidate, RoutingDecisionReceipt
from accretion.ids import has_prefix
from accretion.routing.errors import RoutingError
from accretion.routing.protocols import RoutingMode

_DIGEST = r"^[0-9a-f]{64}$"
_REASON_CODE = r"^[A-Z][A-Z0-9_]*$"


class RoutingRequestCreate(BaseModel):
    """Immutable inputs the caller pins when asking M2 to route one node."""

    model_config = ConfigDict(extra="forbid")

    routing_request_id: str = Field(min_length=1, max_length=64)
    node_contract_id: str = Field(min_length=1, max_length=64)
    expected_node_contract_hash: str = Field(pattern=_DIGEST)
    mode: RoutingMode
    expected_registry_snapshot_id: str = Field(min_length=1, max_length=64)

    @field_validator("routing_request_id")
    @classmethod
    def _routing_request_id_is_canonical(cls, value: str) -> str:
        if not has_prefix(value, "routing_request"):
            raise ValueError("routing_request_id must be a canonical rrq identifier")
        return value

    @field_validator("node_contract_id")
    @classmethod
    def _node_contract_id_is_canonical(cls, value: str) -> str:
        if not has_prefix(value, "node_contract"):
            raise ValueError("node_contract_id must be a canonical nct identifier")
        return value

    @field_validator("expected_registry_snapshot_id")
    @classmethod
    def _registry_snapshot_id_is_canonical(cls, value: str) -> str:
        if not has_prefix(value, "mcp_snapshot"):
            raise ValueError(
                "expected_registry_snapshot_id must be a canonical mcp identifier"
            )
        return value


class RoutingOverrideCreate(BaseModel):
    """An attributed compare-and-set replacement of a persisted selection."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=64)
    reason_code: str = Field(min_length=1, max_length=64, pattern=_REASON_CODE)
    reason: str = Field(min_length=1, max_length=2_000)
    expected_receipt_version: int = Field(ge=1)

    @field_validator("candidate_id")
    @classmethod
    def _candidate_id_is_canonical(cls, value: str) -> str:
        if not has_prefix(value, "configuration_candidate"):
            raise ValueError("candidate_id must be a canonical ccd identifier")
        return value


class _NodeRoutingApi(Protocol):
    async def route_execution(
        self,
        *,
        project_id: str,
        execution_instance_id: str,
        routing_request_id: str,
        node_contract_id: str,
        expected_node_contract_hash: str,
        mode: RoutingMode,
        expected_registry_snapshot_id: str,
        principal: PrincipalRef,
    ) -> RoutingDecisionReceipt: ...

    async def get_receipt(
        self, *, receipt_id: str, principal: PrincipalRef
    ) -> RoutingDecisionReceipt: ...

    async def candidates_for(
        self, *, receipt_id: str, principal: PrincipalRef
    ) -> list[ConfigurationCandidate]: ...

    async def override(
        self,
        *,
        receipt_id: str,
        candidate_id: str,
        reason_code: str,
        reason: str,
        expected_receipt_version: int,
        principal: PrincipalRef,
    ) -> RoutingDecisionReceipt: ...

    async def cancel(
        self, *, receipt_id: str, principal: PrincipalRef
    ) -> RoutingDecisionReceipt: ...


router = APIRouter(tags=["routing"])


def _service(request: Request) -> _NodeRoutingApi:
    service = getattr(request.app.state, "node_routing", None)
    if service is None:
        raise RoutingError(
            "NODE_ROUTING_UNAVAILABLE",
            "node routing is unavailable",
            status_code=409,
        )
    return cast(_NodeRoutingApi, service)


def _principal_ref(request: Request) -> PrincipalRef:
    who = current_principal(request)
    return PrincipalRef(
        principal_id=who.principal_id,
        display_name=who.display_name,
        status=who.status,
    )


@router.post(
    "/api/v1/projects/{project_id}/node-executions/{execution_instance_id}/route",
    response_model=RoutingDecisionReceipt,
)
async def route_node_execution(
    project_id: str,
    execution_instance_id: str,
    payload: RoutingRequestCreate,
    request: Request,
) -> RoutingDecisionReceipt:
    # M2 intentionally ships only the deterministic baseline.  Rejecting these
    # explicitly is safer than accepting a mode the service might silently degrade.
    if payload.mode is not RoutingMode.BASELINE_ONLY:
        raise RoutingError(
            "ROUTING_MODE_UNSUPPORTED",
            f"routing mode {payload.mode.value} is not available in M2",
            status_code=422,
        )
    return await _service(request).route_execution(
        project_id=project_id,
        execution_instance_id=execution_instance_id,
        routing_request_id=payload.routing_request_id,
        node_contract_id=payload.node_contract_id,
        expected_node_contract_hash=payload.expected_node_contract_hash,
        mode=payload.mode,
        expected_registry_snapshot_id=payload.expected_registry_snapshot_id,
        principal=_principal_ref(request),
    )


@router.get(
    "/api/v1/routing-decisions/{receipt_id}",
    response_model=RoutingDecisionReceipt,
)
async def get_routing_decision(
    receipt_id: str, request: Request
) -> RoutingDecisionReceipt:
    return await _service(request).get_receipt(
        receipt_id=receipt_id, principal=_principal_ref(request)
    )


@router.get(
    "/api/v1/routing-decisions/{receipt_id}/candidates",
    response_model=list[ConfigurationCandidate],
)
async def get_routing_candidates(
    receipt_id: str, request: Request
) -> list[ConfigurationCandidate]:
    return await _service(request).candidates_for(
        receipt_id=receipt_id, principal=_principal_ref(request)
    )


@router.post(
    "/api/v1/routing-decisions/{receipt_id}/override",
    response_model=RoutingDecisionReceipt,
)
async def override_routing_decision(
    receipt_id: str,
    payload: RoutingOverrideCreate,
    request: Request,
) -> RoutingDecisionReceipt:
    return await _service(request).override(
        receipt_id=receipt_id,
        candidate_id=payload.candidate_id,
        reason_code=payload.reason_code,
        reason=payload.reason,
        expected_receipt_version=payload.expected_receipt_version,
        principal=_principal_ref(request),
    )


@router.post(
    "/api/v1/routing-decisions/{receipt_id}/cancel",
    response_model=RoutingDecisionReceipt,
)
async def cancel_routing_decision(
    receipt_id: str, request: Request
) -> RoutingDecisionReceipt:
    return await _service(request).cancel(
        receipt_id=receipt_id, principal=_principal_ref(request)
    )


__all__ = [
    "RoutingOverrideCreate",
    "RoutingRequestCreate",
    "router",
]
