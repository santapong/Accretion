"""HTTP adapter for shadow-policy registration (SDD §11.4).

One route, because registering a shadow policy is the only shadow operation that changes
anything: the decisions and the rollouts are written on the evaluation path, and M6.2 adds the
read side. Keeping it in its own ``APIRouter`` module rather than appending to ``main.py``
follows ``accretion.api.routing``, which is also where the refusal shapes come from — a
``RoutingError`` carries the code and the status, and ``main.py``'s handler renders the
envelope, so this module never builds a response by hand.
"""

from __future__ import annotations

from typing import Protocol, cast

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from accretion.api.auth import principal as current_principal
from accretion.contracts import PrincipalRef
from accretion.contracts.routing import RouterModelVersion
from accretion.ids import has_prefix
from accretion.routing.errors import RoutingError
from accretion.routing.shadow import ShadowBudget, ShadowRegistrationError
from accretion.routing.train import RouterNotEvaluatedError

SHADOW_POLICIES_PATH = "/api/v1/shadow-policies"


class ShadowPolicyCreate(BaseModel):
    """The candidate to shadow and the budget the workspace agrees to spend on it.

    The budget is flattened into the body rather than nested, because ADR-060's two limits are
    the whole of it and a one-field wrapper object would make every client build a document to
    send two numbers. The service reassembles them into a :class:`ShadowBudget`, which is what
    seals into the registered version's id.
    """

    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1, max_length=64)
    candidate_version_id: str = Field(min_length=1, max_length=64)
    daily_cost_cap: float = Field(gt=0)
    max_trials_per_day: int = Field(ge=1)
    run_id: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("candidate_version_id")
    @classmethod
    def _candidate_version_id_is_canonical(cls, value: str) -> str:
        if not has_prefix(value, "router_model_version"):
            raise ValueError("candidate_version_id must be a canonical rmv identifier")
        return value


class _ShadowApi(Protocol):
    async def register(
        self,
        *,
        candidate_version_id: str,
        budget: ShadowBudget,
        principal: PrincipalRef,
        workspace_id: str,
        run_id: str | None = None,
    ) -> RouterModelVersion: ...


router = APIRouter(tags=["shadow"])


def _service(request: Request) -> _ShadowApi:
    service = getattr(request.app.state, "shadow", None)
    if service is None:
        raise RoutingError(
            "SHADOW_EVALUATION_UNAVAILABLE",
            "shadow evaluation is unavailable",
            status_code=409,
        )
    return cast(_ShadowApi, service)


def _principal_ref(request: Request) -> PrincipalRef:
    who = current_principal(request)
    return PrincipalRef(
        principal_id=who.principal_id,
        display_name=who.display_name,
        status=who.status,
    )


@router.post(SHADOW_POLICIES_PATH, response_model=RouterModelVersion, status_code=201)
async def register_shadow_policy(
    payload: ShadowPolicyCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RouterModelVersion:
    """Register one CANDIDATE router version for shadow evaluation.

    Administering the workspace is required rather than membership, for the reason
    ``POST /api/v1/router-models/train-candidate`` gives: a shadow policy spends the
    workspace's budget branching its live runs, and the version it writes is the artefact the
    workspace will later be asked to promote.

    ``Idempotency-Key`` is required by SDD §11 and, here, is a *retry* token rather than part
    of the policy's identity: the registered version's id is derived from the candidate and
    the budget, so a replay under any key returns the same version instead of writing a second
    one. Requiring the header anyway keeps every mutating v0.4 endpoint answering the same
    question the same way.

    A candidate in another workspace is a 404 and not a 403 — the tenancy convention of this
    API, where a resource the caller may not see is absent rather than refused, so that an
    error cannot confirm an id.
    """

    # Imported inside the handler, not at module scope: ``main`` imports this router, so a
    # top-level import would close the cycle. The same technique, and the same reason,
    # as ``ids.derived_id``'s function-local import of ``contracts.canonical``. Restating
    # the membership rule here instead would give the repository a second copy of an
    # authorization decision (registry §21).
    from accretion.api.main import IdempotencyKeyRequiredError, _require_workspace_access

    await _require_workspace_access(request, payload.workspace_id, administer=True)
    if not idempotency_key:
        raise IdempotencyKeyRequiredError(
            f"POST {SHADOW_POLICIES_PATH} requires an Idempotency-Key header"
        )
    budget = ShadowBudget(
        daily_cost_cap=payload.daily_cost_cap,
        max_trials_per_day=payload.max_trials_per_day,
    )
    try:
        return await _service(request).register(
            candidate_version_id=payload.candidate_version_id,
            budget=budget,
            principal=_principal_ref(request),
            workspace_id=payload.workspace_id,
            run_id=payload.run_id,
        )
    except RouterNotEvaluatedError:
        # Re-raised untouched so AC4-M4-016's 409 body is the trainer's wording on both
        # routes rather than two paraphrases of one refusal.
        raise
    except ShadowRegistrationError as exc:
        raise RoutingError(
            "SHADOW_CANDIDATE_NOT_REGISTRABLE", str(exc), status_code=409
        ) from exc


__all__ = [
    "SHADOW_POLICIES_PATH",
    "ShadowPolicyCreate",
    "router",
]
