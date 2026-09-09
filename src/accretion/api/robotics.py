"""Bounded HTTP access to immutable, scoped simulation declarations.

Read the original UTF-8 writer document before any defaulting or projection.
Request IDs and headers confer no authority; the registry rechecks persisted
membership, project binding and writer provenance inside its transaction.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any, Literal, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import Field

from accretion.api.auth import AuthRuntime, principal
from accretion.api.robotics_preconditions import require_idempotency_key, revision_etag
from accretion.api.schemas import ErrorEnvelope
from accretion.config import Settings
from accretion.contracts import StrictModel
from accretion.contracts.robotics import (
    CanonicalWriterEnvelope,
    EmbodimentDescriptor,
    RobotAdapterManifest,
)
from accretion.identity import LOCAL_WORKSPACE_ID
from accretion.persistence.store import StateStore
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.registry import (
    MAX_WRITER_BYTES,
    RegistryEntry,
    RegistryPage,
    RoboticsRegistry,
)
from accretion.robotics.store import registry_store_for


class RegistryRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def bounded(request: Request) -> Response:
            try:
                response = await handler(request)
            except RequestValidationError:
                # Keep request validation in the same safe, stable domain shape.
                response = await error_response(request, RoboticsError(Code.INVALID_REQUEST))
            response.headers["Cache-Control"] = "no-store"
            return response

        return bounded


class RoboticsErrorResponse(StrictModel):
    code: Code
    message: str = Field(max_length=512)
    recovery_action: str = Field(max_length=512)
    correlation_id: str
    retryable: Literal[False] = False


router = APIRouter(
    tags=["simulation registry"],
    route_class=RegistryRoute,
    responses={
        **{status: {"model": RoboticsErrorResponse} for status in (404, 409, 412, 413, 422, 428)},
        401: {"model": ErrorEnvelope},
        403: {"model": RoboticsErrorResponse | ErrorEnvelope},
    },
)
MAX_BODY_CHUNKS = 1024
BODY_TIMEOUT_SECONDS = 10.0


async def build_registry(
    store: StateStore, settings: Settings, auth: AuthRuntime
) -> RoboticsRegistry | None:
    """Build declarations only; host and artifact trust must be configured separately."""
    if not settings.enable_simulation:
        return None
    if settings.simulation_local_project_ids and auth.mode != "LOCAL_PRINCIPAL":
        raise RoboticsError(Code.CAPABILITY_DENIED)
    persistence = registry_store_for(store)
    if settings.simulation_local_project_ids:
        await auth.identity.local_principal()
        for project_id in settings.simulation_local_project_ids:
            await persistence.bootstrap_bind_project(
                workspace_id=LOCAL_WORKSPACE_ID, project_id=project_id
            )
    return RoboticsRegistry(persistence)


def service(request: Request) -> RoboticsRegistry:
    registry = getattr(request.app.state, "robotics_registry", None)
    if registry is None:
        raise RoboticsError(Code.SIMULATION_UNAVAILABLE)
    return cast(RoboticsRegistry, registry)


@dataclass(frozen=True)
class Scope:
    workspace_id: str
    project_id: str


def scope(
    request: Request,
    workspace_id: Annotated[str, Query(min_length=1, max_length=64)],
    project_id: Annotated[str, Query(min_length=1, max_length=64)],
) -> Scope:
    for key in ("workspace_id", "project_id", "limit", "cursor", "version"):
        if len(request.query_params.getlist(key)) > 1:
            raise RoboticsError(Code.INVALID_REQUEST)
    return Scope(workspace_id, project_id)


Scoped = Annotated[Scope, Depends(scope)]


def _header(request: Request, name: str) -> str | None:
    values = request.headers.getlist(name)
    if len(values) > 1:
        raise RoboticsError(Code.INVALID_REQUEST)
    return values[0] if values else None


async def writer_json(request: Request, expected_type: str) -> str:
    media = _header(request, "content-type")
    if media is None or media.split(";", 1)[0].strip().lower() != "application/json":
        raise RoboticsError(Code.INVALID_REQUEST)
    if _header(request, "content-encoding") not in (None, "identity"):
        raise RoboticsError(Code.INVALID_REQUEST)
    declared = _header(request, "content-length")
    if declared is not None:
        if not declared.isascii() or not declared.isdigit() or len(declared) > 10:
            raise RoboticsError(Code.INVALID_REQUEST)
        if int(declared) > MAX_WRITER_BYTES:
            raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
    body = bytearray()
    count = 0
    try:
        async with asyncio.timeout(BODY_TIMEOUT_SECONDS):
            async for chunk in request.stream():
                count += 1
                if count > MAX_BODY_CHUNKS or len(body) + len(chunk) > MAX_WRITER_BYTES:
                    raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
                body.extend(chunk)
    except TimeoutError as error:
        raise RoboticsError(Code.INVALID_REQUEST) from error
    if declared is not None and len(body) != int(declared):
        raise RoboticsError(Code.INVALID_REQUEST)
    try:
        original = body.decode("utf-8")
        envelope = CanonicalWriterEnvelope(original)
        if envelope.payload()["contract_type"] != expected_type:
            raise ValueError("wrong registry collection")
        return original
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise RoboticsError(Code.INVALID_CONTRACT) from error


def _writer_body(contract_type: str) -> dict[str, Any]:
    # An opaque original writer envelope is intentional: a current reader DTO
    # would lose future-minor fields and JSON number spelling before sealing.
    return {
        "parameters": [
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "minLength": 1, "maxLength": 128},
            }
        ],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "description": f"Sealed {contract_type} writer JSON; maximum 1 MiB.",
                        "required": [
                            "contract_type",
                            "contract_id",
                            "schema_version",
                            "content_hash",
                            "created_at",
                            "created_by",
                            "workspace_id",
                            "project_id",
                        ],
                        "properties": {"contract_type": {"type": "string", "const": contract_type}},
                    }
                }
            },
        },
    }


async def _register(
    request: Request, response: Response, selected: Scope, contract_type: str
) -> RegistryEntry:
    registry = service(request)
    key = require_idempotency_key(_header(request, "idempotency-key"))
    # A new immutable declaration has no existing revision to condition on.
    if _header(request, "if-match") is not None:
        raise RoboticsError(Code.INVALID_REQUEST)
    original = await writer_json(request, contract_type)
    entry = await registry.register(
        actor_id=principal(request).principal_id,
        workspace_id=selected.workspace_id,
        project_id=selected.project_id,
        original_json=original,
        idempotency_key=key,
    )
    response.headers["ETag"] = revision_etag(entry.revision)
    response.headers["Cache-Control"] = "no-store"
    return entry


@router.post(
    "/api/v1/embodiments",
    response_model=RegistryEntry,
    status_code=201,
    openapi_extra=_writer_body(EmbodimentDescriptor.CONTRACT_TYPE),
)
async def register_embodiment(
    request: Request, response: Response, selected: Scoped
) -> RegistryEntry:
    return await _register(request, response, selected, EmbodimentDescriptor.CONTRACT_TYPE)


@router.post(
    "/api/v1/robot-adapters",
    response_model=RegistryEntry,
    status_code=201,
    openapi_extra=_writer_body(RobotAdapterManifest.CONTRACT_TYPE),
)
async def register_adapter(request: Request, response: Response, selected: Scoped) -> RegistryEntry:
    return await _register(request, response, selected, RobotAdapterManifest.CONTRACT_TYPE)


async def _list(
    request: Request, selected: Scope, contract_type: str, limit: int, cursor: str | None
) -> RegistryPage:
    return await service(request).list(
        actor_id=principal(request).principal_id,
        workspace_id=selected.workspace_id,
        project_id=selected.project_id,
        contract_type=contract_type,
        limit=limit,
        cursor=cursor,
    )


@router.get("/api/v1/embodiments", response_model=RegistryPage)
async def list_embodiments(
    request: Request,
    selected: Scoped,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> RegistryPage:
    return await _list(request, selected, EmbodimentDescriptor.CONTRACT_TYPE, limit, cursor)


@router.get("/api/v1/robot-adapters", response_model=RegistryPage)
async def list_adapters(
    request: Request,
    selected: Scoped,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> RegistryPage:
    return await _list(request, selected, RobotAdapterManifest.CONTRACT_TYPE, limit, cursor)


async def _get(
    request: Request,
    response: Response,
    selected: Scope,
    contract_type: str,
    contract_id: str,
    version: str | None = None,
) -> RegistryEntry:
    if len(contract_id) > 128 or (version is not None and len(version) > 64):
        raise RoboticsError(Code.INVALID_REQUEST)
    entry = await service(request).get(
        actor_id=principal(request).principal_id,
        workspace_id=selected.workspace_id,
        project_id=selected.project_id,
        contract_type=contract_type,
        contract_id=contract_id,
        version=version,
    )
    response.headers["ETag"] = revision_etag(entry.revision)
    response.headers["Cache-Control"] = "no-store"
    return entry


@router.get("/api/v1/embodiments/{contract_id}/versions/{version}", response_model=RegistryEntry)
async def get_embodiment(
    contract_id: str, version: str, request: Request, response: Response, selected: Scoped
) -> RegistryEntry:
    return await _get(
        request, response, selected, EmbodimentDescriptor.CONTRACT_TYPE, contract_id, version
    )


@router.get("/api/v1/robot-adapters/{contract_id}", response_model=RegistryEntry)
async def get_adapter(
    contract_id: str,
    request: Request,
    response: Response,
    selected: Scoped,
    version: Annotated[str | None, Query(max_length=64)] = None,
) -> RegistryEntry:
    return await _get(
        request, response, selected, RobotAdapterManifest.CONTRACT_TYPE, contract_id, version
    )


@router.post("/api/v1/robot-adapters/{contract_id}/conformance-runs", response_model=None)
async def run_conformance(
    contract_id: str, request: Request, response: Response, selected: Scoped
) -> None:
    # Scope is checked first; a foreign adapter never reveals configured host state.
    await _get(request, response, selected, RobotAdapterManifest.CONTRACT_TYPE, contract_id)
    require_idempotency_key(_header(request, "idempotency-key"))
    raise RoboticsError(Code.SIMULATION_UNAVAILABLE)


async def error_response(request: Request, error: RoboticsError) -> JSONResponse:
    body = RoboticsErrorResponse(
        code=error.code,
        message=error.message,
        recovery_action=error.recovery_action,
        correlation_id=str(uuid4()),
    )
    return JSONResponse(
        status_code=error.status_code,
        content=body.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )
