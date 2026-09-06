"""HTTP adapter for router promotion and rollback (SDD §11.3, §10.3).

Two routes, both mutating, both workspace-admin, both idempotent. They are a thin projection
of :mod:`accretion.routing.promotion`: every refusal it raises is a
:class:`~accretion.routing.errors.RoutingError` carrying its own code and status, so
``main.py``'s existing handler turns a rejected promotion into the
``{code, message, correlation_id, retryable}`` envelope rather than a 500, and this module
contains no ``try``/``except`` at all.

**Administering the workspace is required, not membership.** Promotion changes which policy
serves every project in the workspace, and rollback withdraws it during an incident;
§10.3 (OQ-411) makes both a human act by someone accountable for the workspace. The check
runs before anything is read, and a caller who is not a member of the named workspace is
refused before the route can confirm that the report or the version exists.

**Tenancy is a 404 and not a 403.** ``workspace_id`` is a required query parameter and the
resource must belong to it; a report in another workspace raises a bare ``KeyError``, which
``main.py`` renders as "not found". A caller who administers workspace A therefore cannot
use these routes to discover that a report id exists in workspace B.
"""

from __future__ import annotations

from typing import Protocol, cast

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from accretion.api.auth import principal as current_principal
from accretion.contracts import PrincipalRef, WorkspaceRole
from accretion.contracts.routing import RouterActivation
from accretion.identity import AuthorizationError
from accretion.persistence.store import StateStore
from accretion.routing.errors import RoutingError

ADMIN_ROLES = frozenset({WorkspaceRole.OWNER, WorkspaceRole.ADMIN})


class RollbackCreate(BaseModel):
    """Why the active router is being withdrawn.

    ``cause`` is required and has no default. §10.3's reversibility is worth nothing if the
    ledger records that something was withdrawn but not what it was withdrawn *for*, and an
    incident review reads this field first. A plain ``BaseModel`` rather than
    ``StrictModel`` because it is a request body and not a persisted contract; ``extra`` is
    still forbidden, so a misspelt field is a 422 rather than a silently ignored one.
    """

    model_config = ConfigDict(extra="forbid")

    cause: str = Field(min_length=1, max_length=2_000)
    run_id: str | None = Field(default=None, min_length=1, max_length=64)


class _PromotionApi(Protocol):
    """What this adapter needs of :class:`accretion.routing.promotion.PromotionService`."""

    store: StateStore

    async def promote(
        self,
        report_id: str,
        approved_by: PrincipalRef,
        run_id: str | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> RouterActivation: ...

    async def rollback(
        self,
        version_id: str,
        cause: str,
        approved_by: PrincipalRef,
        run_id: str | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> RouterActivation: ...


router = APIRouter(tags=["router-admin"])


def _service(request: Request) -> _PromotionApi:
    service = getattr(request.app.state, "router_admin", None)
    if service is None:
        raise RoutingError(
            "ROUTER_ADMIN_UNAVAILABLE",
            "router promotion is unavailable",
            status_code=409,
        )
    return cast(_PromotionApi, service)


def _principal_ref(request: Request) -> PrincipalRef:
    who = current_principal(request)
    return PrincipalRef(
        principal_id=who.principal_id,
        display_name=who.display_name,
        status=who.status,
    )


async def _require_workspace_admin(
    request: Request, service: _PromotionApi, workspace_id: str
) -> None:
    """Refuse anyone who does not administer ``workspace_id``.

    Reads memberships off the promotion service's own store rather than off the run
    manager's, because they are the same store and depending on the manager here would make
    an admin route unusable in a deployment that has not started one.
    """

    who = current_principal(request)
    memberships = await service.store.list_workspace_memberships(
        workspace_id=workspace_id, principal_id=who.principal_id
    )
    if not memberships:
        raise AuthorizationError("principal is not a member of the requested workspace")
    if memberships[0].role not in ADMIN_ROLES:
        raise AuthorizationError("workspace owner or admin role is required")


def _require_idempotency_key(key: str | None, route: str) -> str:
    """SDD §11: a mutating endpoint states its idempotency key or is refused.

    Promotion and rollback are idempotent on their own natural keys — a report authorises
    one release, a version is withdrawn once — so the header cannot change the outcome. It
    is required anyway, because a client that has not thought about what it would do on a
    timeout is a client that will eventually retry something that is not safe to retry, and
    the ledger records the key so an operator can join an entry to the request that made it.
    """

    if not key:
        raise RoutingError(
            "IDEMPOTENCY_KEY_REQUIRED",
            f"{route} requires an Idempotency-Key header",
            status_code=400,
        )
    return key


@router.post(
    "/api/v1/router-promotions/{report_id}/promote",
    response_model=RouterActivation,
    status_code=201,
)
async def promote_router_version(
    report_id: str,
    request: Request,
    workspace_id: str,
    run_id: str | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RouterActivation:
    """Activate the candidate a ``PROMOTE`` report names, and record the act (§10.3).

    The response is the ledger entry, which is the whole of what changed that a caller can
    act on: the sequence it took, the version it activated, the version it displaced, the
    target a withdrawal would restore and the drill digest that target passed. The version
    rows are readable through ``GET /api/v1/router-models``.

    A report that decided ``REJECT`` or ``REQUIRE_REVIEW``, and a report whose rollback
    target cannot be loaded and scored, are both refused with their own code and leave no
    row behind (AC4-M8-038). Replaying a successful promotion returns the same activation.
    """

    service = _service(request)
    await _require_workspace_admin(request, service, workspace_id)
    key = _require_idempotency_key(
        idempotency_key, "POST /api/v1/router-promotions/{report_id}/promote"
    )
    report = await service.store.get_router_promotion_report(report_id)
    if report is None or report.workspace_id != workspace_id:
        raise KeyError(report_id)
    return await service.promote(
        report_id, _principal_ref(request), run_id, idempotency_key=key
    )


@router.post(
    "/api/v1/router-models/{version_id}/rollback",
    response_model=RouterActivation,
    status_code=201,
)
async def rollback_router_version(
    version_id: str,
    payload: RollbackCreate,
    request: Request,
    workspace_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RouterActivation:
    """Withdraw the active router version and restore what its activation named (§10.3).

    ``version_id`` must be the head of its family's ledger; naming an older version is a
    conflict rather than a deeper rollback, because the operator asking for it is looking at
    a ledger that has moved on. The restored target is drilled before anything is written,
    so a rollback never replaces a bad router with a dead one.
    """

    service = _service(request)
    await _require_workspace_admin(request, service, workspace_id)
    key = _require_idempotency_key(
        idempotency_key, "POST /api/v1/router-models/{version_id}/rollback"
    )
    version = await service.store.get_router_model_version(version_id)
    if version is None or version.workspace_id != workspace_id:
        raise KeyError(version_id)
    return await service.rollback(
        version_id,
        payload.cause,
        _principal_ref(request),
        payload.run_id,
        idempotency_key=key,
    )


__all__ = [
    "ADMIN_ROLES",
    "RollbackCreate",
    "router",
]
