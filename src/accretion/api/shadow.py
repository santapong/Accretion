"""HTTP adapter for shadow-policy registration and the shadow report (SDD §11.4).

Two routes, and the asymmetry is the point: registering a shadow policy is the only shadow
operation that *changes* anything, because the decisions and the rollouts are written on the
evaluation path by :mod:`accretion.routing.rollout`. The second route is a pure read over
:func:`accretion.routing.shadow.shadow_report`, returning it unchanged as the ``response_model``
so that the number an operator reads on a dashboard and the number M8.2 gates a promotion on are
produced by one function.

Keeping both in their own ``APIRouter`` module rather than appending to ``main.py`` follows
``accretion.api.routing``, which is also where the refusal shapes come from — a ``RoutingError``
carries the code and the status, and ``main.py``'s handler renders the envelope, so this module
never builds a response by hand.
"""

from __future__ import annotations

from typing import Protocol, cast

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from accretion.api.auth import principal as current_principal
from accretion.contracts import PrincipalRef
from accretion.contracts.routing import RouterModelVersion, UtilityWeights
from accretion.identity import AuthorizationError
from accretion.ids import has_prefix
from accretion.persistence.store import StateStore
from accretion.routing.errors import RoutingError
from accretion.routing.selector import DEFAULT_UTILITY_WEIGHTS
from accretion.routing.shadow import (
    NON_INFERIORITY_GATE,
    PAIRED_RUNS_GATE,
    GateStatus,
    ShadowBudget,
    ShadowRegistrationError,
    ShadowReport,
    ShadowReportConfig,
    shadow_report,
)
from accretion.routing.train import RouterNotEvaluatedError

SHADOW_POLICIES_PATH = "/api/v1/shadow-policies"
SHADOW_REPORT_PATH = SHADOW_POLICIES_PATH + "/{version_id}/report"

REPORT_CONFIG = ShadowReportConfig(seed=940_517)
"""The constants every rendering of a shadow report over HTTP is computed under.

A module constant and not a query parameter, for the reason
:class:`~accretion.routing.shadow.ShadowReportConfig` gives: every field in it changes the
answer, and none of them may be chosen after the rows are seen. A caller that could pass its
own seed could re-roll the bootstrap until the lower bound cleared the floor, which is the one
way a pre-registered interval stops being one. M8.2 quotes the same object when it gates a
promotion, so a report and the promotion it supports are computed under identical constants.
"""

REPORT_WEIGHTS: UtilityWeights = DEFAULT_UTILITY_WEIGHTS
"""How the report prices quality, cost and latency against each other.

The deterministic selector's own weights, and deliberately not the objective contract's: one
shadow version spans many nodes and therefore many objectives, and averaging deltas computed
under different weight vectors would produce a verdict about no policy in particular. Naming
the selector's vector reuses the one already audited rather than inventing a second (§21).
"""


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
    store: StateStore

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


def _empty_report(version_id: str) -> ShadowReport:
    """The report for a version nothing has been shadowed against yet.

    :func:`~accretion.routing.shadow.shadow_report` refuses an empty decision list on purpose —
    a zeroed report would let a reader mistake "nothing was shadowed" for "the shadow was
    neutral" — and a route cannot pass that refusal on as a 500 to a dashboard polling a policy
    registered a minute ago. So the empty case is said here, in the shape a client already
    parses: ``paired_count`` zero, ``non_inferior`` false, and both gates present and unmet, so
    that "this policy owes two gates" reads the same before and after its first decision.
    """

    return ShadowReport(
        version_id=version_id,
        paired_count=0,
        agreement_rate=0.0,
        mean_delta=0.0,
        delta_lcb=0.0,
        non_inferior=False,
        pairs=[],
        remaining_gates=[
            GateStatus(
                gate=PAIRED_RUNS_GATE,
                met=False,
                evidence=(
                    f"0 complete pairs of the {REPORT_CONFIG.min_paired_runs} required "
                    "(0 shadow decisions recorded)"
                ),
            ),
            GateStatus(
                gate=NON_INFERIORITY_GATE,
                met=False,
                evidence=(
                    f"no interval: 0 pairs against a floor of {REPORT_CONFIG.delta_ni} "
                    f"(seed {REPORT_CONFIG.seed}, B={REPORT_CONFIG.bootstraps})"
                ),
            ),
        ],
    )


@router.get(SHADOW_REPORT_PATH, response_model=ShadowReport)
async def read_shadow_report(version_id: str, request: Request) -> ShadowReport:
    """What one shadow stage has shown so far, and what it still owes a promotion.

    Membership is enough here, unlike registration: reading what a policy has measured spends
    nothing and changes nothing, and an operator who cannot see the evidence cannot argue with
    the promotion it will be used to justify.

    A version in another workspace is a 404 and not a 403, and the refusal is produced by
    turning the membership error into a ``KeyError`` rather than by letting it through: this is
    the tenancy convention of the API, and a 403 here would confirm that the id names a real
    router version in somebody else's workspace.

    The rollout rows are listed for the version's whole workspace and filtered by
    ``shadow_report`` itself, which ignores rows belonging to another stage. Filtering here as
    well would put the join in two places, and the report is the party that defines it.
    """

    from accretion.api.main import _require_workspace_access

    store = _service(request).store
    version = await store.get_router_model_version(version_id)
    if version is None:
        raise KeyError(version_id)
    try:
        await _require_workspace_access(request, version.workspace_id)
    except AuthorizationError as exc:
        raise KeyError(version_id) from exc

    decisions = [
        decision
        for decision in await store.list_shadow_decisions(
            workspace_id=version.workspace_id, project_id=version.project_id
        )
        if decision.shadow_router_version_id == version_id
    ]
    if not decisions:
        return _empty_report(version_id)
    results = await store.list_shadow_rollout_results(
        workspace_id=version.workspace_id, project_id=version.project_id
    )
    return shadow_report(
        results, decisions, weights=REPORT_WEIGHTS, config=REPORT_CONFIG
    )


__all__ = [
    "REPORT_CONFIG",
    "REPORT_WEIGHTS",
    "SHADOW_POLICIES_PATH",
    "SHADOW_REPORT_PATH",
    "ShadowPolicyCreate",
    "router",
]
