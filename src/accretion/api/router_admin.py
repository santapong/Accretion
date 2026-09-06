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

from datetime import datetime
from typing import Literal, Protocol, cast

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from accretion.api.auth import principal as current_principal
from accretion.contracts import PrincipalRef, StrictModel, WorkspaceRole
from accretion.contracts.routing import (
    RouterActivation,
    RouterModelVersion,
    RouterPromotionReport,
    RouterScope,
    RouterStatus,
)
from accretion.identity import AuthorizationError
from accretion.persistence.store import StateStore
from accretion.routing.activation import family_key_for
from accretion.routing.errors import RoutingError
from accretion.routing.promotion import RollbackDrill, build_promotion_evaluator

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
    """What this adapter needs of :class:`accretion.routing.promotion.PromotionService`.

    ``drill`` is here so that the evaluation route can build its gate out of the collaborators
    the deployment already wired rather than out of a second ``app.state`` entry: the drill
    holds the loader and the artefact store, and an evaluator that rehearsed the rollback
    target through one artefact store while scoring the candidate through another would be
    making two claims about two deployments.
    """

    store: StateStore
    drill: RollbackDrill

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


async def _require_workspace_member(
    request: Request, service: _PromotionApi, workspace_id: str
) -> WorkspaceRole:
    """Refuse anyone who is not a member of ``workspace_id``, and return their role.

    Reads memberships off the promotion service's own store rather than off the run
    manager's, because they are the same store and depending on the manager here would make
    an admin route unusable in a deployment that has not started one.

    The two read routes stop here. Reading why a router was promoted, or what a version
    descends from, is something every member of the workspace the router serves is entitled
    to do; §10.3's "human act" is the mutation, not the audit of it, and a lineage view that
    only administrators could open would make the promotion record unreviewable by the people
    whose work it routes.
    """

    who = current_principal(request)
    memberships = await service.store.list_workspace_memberships(
        workspace_id=workspace_id, principal_id=who.principal_id
    )
    if not memberships:
        raise AuthorizationError("principal is not a member of the requested workspace")
    return memberships[0].role


async def _require_workspace_admin(
    request: Request, service: _PromotionApi, workspace_id: str
) -> None:
    """Refuse anyone who does not administer ``workspace_id``.

    Membership first and the role second, in that order, so that a non-member and a member
    without the role get the same two-step treatment and neither learns anything about the
    workspace from which refusal they received.
    """

    role = await _require_workspace_member(request, service, workspace_id)
    if role not in ADMIN_ROLES:
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


class PromotionEvaluationCreate(BaseModel):
    """Which two versions to compare, and on which sealed holdout snapshot.

    A plain ``BaseModel`` with ``extra`` forbidden, for the reason :class:`RollbackCreate`
    gives: it is a request body and not a persisted contract, so a misspelt field is a 422
    rather than a silently ignored one.

    All three ids are required and none of them has a default. A route that defaulted the
    baseline to "whatever is active" would let a caller evaluate against a version they were
    not looking at, and the report would then name a comparison nobody chose.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_version_id: str = Field(min_length=1, max_length=64)
    baseline_version_id: str = Field(min_length=1, max_length=64)
    holdout_snapshot_id: str = Field(min_length=1, max_length=64)
    run_id: str | None = Field(default=None, min_length=1, max_length=64)


class RouterLineageEntry(StrictModel):
    """One version on a lineage chain, flattened to what an inspector renders (§17.3)."""

    schema_version: Literal["1.0"] = "1.0"
    version_id: str = Field(min_length=1, max_length=64)
    parent_version_id: str | None = Field(default=None, min_length=1, max_length=64)
    status: RouterStatus
    scope: RouterScope
    algorithm_id: str = Field(min_length=1, max_length=128)
    training_snapshot_id: str = Field(min_length=1, max_length=64)
    artifact_digest: str = Field(min_length=1, max_length=128)
    created_at: datetime


class RouterLineage(StrictModel):
    """AC4-M8-042: where a router version came from, and everything that happened to it.

    Three joins a caller would otherwise make by hand and could make inconsistently: the
    ``parent_version_id`` chain from this version back to the first ancestor stored, the
    activation entries for its family in ledger order, and the promotion reports those
    entries cite. A response model rather than a contract because it is a *projection* —
    nothing here is persisted in this shape, every field is a copy of something that is, and
    a stored lineage document would be a fourth place for the same facts to disagree.

    ``active_version_id`` is the ledger head and not "the row whose status is ACTIVE": after
    a rollback those are different answers, and the head is the one routing reads (ADR-061).
    """

    schema_version: Literal["1.0"] = "1.0"
    version_id: str = Field(min_length=1, max_length=64)
    workspace_id: str = Field(min_length=1, max_length=64)
    scope: RouterScope
    family_key: str = Field(min_length=1, max_length=128)
    parent_chain: list[RouterLineageEntry] = Field(default_factory=list, max_length=256)
    activations: list[RouterActivation] = Field(default_factory=list, max_length=1_024)
    promotion_report_ids: list[str] = Field(default_factory=list, max_length=1_024)
    active_version_id: str | None = Field(default=None, min_length=1, max_length=64)
    rollback_target_version_id: str | None = Field(default=None, min_length=1, max_length=64)


@router.get(
    "/api/v1/router-promotions/{report_id}",
    response_model=RouterPromotionReport,
)
async def get_router_promotion(
    report_id: str,
    request: Request,
    workspace_id: str,
) -> RouterPromotionReport:
    """The sealed evaluation that authorises — or refuses — one promotion (AC4-M8-042).

    Membership and not administration: reading why a router was promoted is something every
    member of the workspace it routes for is entitled to do, and §10.3's human act is the
    ``POST`` beside this, not the ``GET``. A report belonging to another workspace raises a
    bare ``KeyError`` and is a 404, so this route cannot be used to enumerate report ids.
    """

    service = _service(request)
    await _require_workspace_member(request, service, workspace_id)
    report = await service.store.get_router_promotion_report(report_id)
    if report is None or report.workspace_id != workspace_id:
        raise KeyError(report_id)
    return report


@router.post(
    "/api/v1/router-promotions",
    response_model=RouterPromotionReport,
    status_code=201,
)
async def evaluate_router_promotion(
    payload: PromotionEvaluationCreate,
    request: Request,
    workspace_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RouterPromotionReport:
    """Run the CSPI-MT gate and seal the verdict, without activating anything (§10.2).

    Evaluating is a separate act from promoting and requires the same authority, because the
    report it writes is what a later ``POST .../promote`` will accept as authorisation: a
    caller who could produce reports but not act on them could still choose which comparison
    the workspace's next promotion would be judged by.

    ``Idempotency-Key`` is what makes a retry a retry here rather than a second sealed claim.
    Under a key the report's id is derived from the four inputs, so a replayed request finds
    the sealed report and returns it; a request whose evidence has moved since gets the
    conflict rather than a stale verdict.

    A holdout that is not project-disjoint from the candidate's training evidence is refused
    with its own code and nothing is written (AC4-M8-036); every other refusal — a regression,
    an unreadable artefact, a rollback target that will not drill — is a *finding inside* the
    report, because those are measurements an operator needs recorded.
    """

    service = _service(request)
    await _require_workspace_admin(request, service, workspace_id)
    key = _require_idempotency_key(idempotency_key, "POST /api/v1/router-promotions")
    for version_id in (payload.candidate_version_id, payload.baseline_version_id):
        version = await service.store.get_router_model_version(version_id)
        if version is None or version.workspace_id != workspace_id:
            raise KeyError(version_id)
    snapshot = await service.store.get_router_training_snapshot(payload.holdout_snapshot_id)
    if snapshot is None or snapshot.workspace_id != workspace_id:
        raise KeyError(payload.holdout_snapshot_id)
    return await build_promotion_evaluator(service.store, service.drill).evaluate(
        payload.candidate_version_id,
        payload.baseline_version_id,
        payload.holdout_snapshot_id,
        _principal_ref(request),
        payload.run_id,
        idempotency_key=key,
    )


@router.get(
    "/api/v1/router-models/{version_id}/lineage",
    response_model=RouterLineage,
)
async def get_router_lineage(
    version_id: str,
    request: Request,
    workspace_id: str,
) -> RouterLineage:
    """The parent chain, the activation history and the reports behind them (AC4-M8-042).

    The chain walks ``parent_version_id`` upwards from the named version and stops at the
    first ancestor that is not stored, which is the honest end of a lineage rather than an
    error: promotion mints a new row parented on the candidate, so a chain reaches back
    through every promotion and rollback of the same artefact to the version that was fitted.
    A cycle — which nothing can currently write — terminates the walk instead of hanging.

    Activations are the whole family's, in ledger order, and not only the ones naming this
    version: "what happened to this router" includes the promotion that displaced it, and a
    history filtered to entries mentioning the version would omit exactly that.
    """

    service = _service(request)
    await _require_workspace_member(request, service, workspace_id)
    version = await service.store.get_router_model_version(version_id)
    if version is None or version.workspace_id != workspace_id:
        raise KeyError(version_id)

    chain: list[RouterLineageEntry] = []
    seen: set[str] = set()
    cursor: RouterModelVersion | None = version
    while cursor is not None and cursor.contract_id not in seen:
        seen.add(cursor.contract_id)
        chain.append(
            RouterLineageEntry(
                version_id=cursor.contract_id,
                parent_version_id=cursor.parent_version_id,
                status=cursor.status,
                scope=cursor.scope,
                algorithm_id=cursor.algorithm_id,
                training_snapshot_id=cursor.training_snapshot_id,
                artifact_digest=cursor.artifact_digest,
                created_at=cursor.created_at,
            )
        )
        parent = cursor.parent_version_id
        if parent is None or parent in seen:
            break
        cursor = await service.store.get_router_model_version(parent)

    family_key = family_key_for(version)
    activations = [
        entry
        for entry in await service.store.list_router_activations(workspace_id=workspace_id)
        if entry.scope is version.scope and entry.family_key == family_key
    ]
    activations.sort(key=lambda entry: entry.sequence)
    head = activations[-1] if activations else None
    return RouterLineage(
        version_id=version.contract_id,
        workspace_id=workspace_id,
        scope=version.scope,
        family_key=family_key,
        parent_chain=chain,
        activations=activations,
        promotion_report_ids=[
            entry.promotion_report_id
            for entry in activations
            if entry.promotion_report_id is not None
        ],
        active_version_id=None if head is None else head.router_version_id,
        rollback_target_version_id=(
            None if head is None else head.rollback_target_version_id
        ),
    )


__all__ = [
    "ADMIN_ROLES",
    "PromotionEvaluationCreate",
    "RollbackCreate",
    "RouterLineage",
    "RouterLineageEntry",
    "router",
]
