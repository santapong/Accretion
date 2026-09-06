"""HTTP adapter for the v0.4 feedback pipeline (SDD §11.2).

The five §11.2 endpoints, as an :class:`~fastapi.APIRouter` and on
:mod:`accretion.api.routing`'s pattern: the module is a projection of a service that has
already decided everything, so a handler here resolves the caller, forwards the call and
translates a refusal — it never applies a rule of its own.

**What the two POSTs are for.** A verification result may arrive from outside a run: §7.9's
independence is structural, and the strongest form of it is a verifier that never ran inside
the executor at all. ``/verification-results`` is that door, and it is idempotent by the v0.1
``verification_id`` the caller mints, because a verifier retrying a submission must not create
a second history of one verdict. ``/final-verification`` is ADR-048's ordering made callable:
an experience may be projected only once the *run* has been graded, and the grader is not
always the scheduler.

**Why the reads are here rather than on the P7 experience routes.** ``/api/v2/experiences``
serves the v0.2 :class:`~accretion.experience.models.Experience`; these serve the v0.4
:class:`~accretion.contracts.routing.ExperienceRecord` *projection* of it, which is keyed by
the same id and answers a different question — "what may a router learn from this?" rather
than "what happened in this run?". Two questions, two representations, two paths; collapsing
them onto one path would make the response type depend on a query parameter.

**Tenancy failures are 404 and never 403.** A caller outside the workspace is told the
resource does not exist, because "you may not read this record" and "this record is not
yours" leak the same fact, and the second one leaks it more quietly. Every handler that names
a stored record therefore raises a bare ``KeyError`` — the app's handler turns it into the
``NOT_FOUND`` envelope — rather than a permission error.
"""

from __future__ import annotations

from typing import Protocol, cast

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from accretion.api.auth import principal as current_principal
from accretion.contracts import (
    AcceptancePolicy,
    GraphNodeKind,
    PrincipalRef,
    Run,
    Task,
    VerificationResult,
    VerificationStatus,
)
from accretion.contracts.routing import (
    ContractSignature,
    ExperienceRecord,
    IndependentVerificationResult,
    RiskClass,
    VerificationState,
    Visibility,
)
from accretion.experience.models import ExperienceSourceKind
from accretion.feedback.experience import EXPERIENCE_ID_LABEL, ExperienceProjector
from accretion.persistence.store import StateStore
from accretion.routing.errors import RoutingError
from accretion.routing.identity import workspace_for_run

_DIGEST = r"^[0-9a-f]{64}$"

FEEDBACK_UNAVAILABLE = "FEEDBACK_PIPELINE_UNAVAILABLE"
"""§15.1-style refusal when no pipeline was wired, mirroring ``NODE_ROUTING_UNAVAILABLE``.

A 409 and not a 501: whether the pipeline exists is a property of how *this* deployment was
assembled and can change without a release, which is a conflict with the current state of the
server rather than a statement about the API.
"""

CONTRADICTION_NOT_OPEN = "CONTRADICTION_NOT_OPEN"
"""Adjudicating a settled contradiction is refused, not repeated (§7.10)."""

VERIFICATION_RESULT_IMMUTABLE = "VERIFICATION_RESULT_IMMUTABLE"
"""A second, differing verdict submitted under one ``verification_id`` (§8.2, append-only)."""


class VerificationResultCreate(BaseModel):
    """One v0.1 verifier verdict, offered for §7.9 ingestion against an execution instance."""

    model_config = ConfigDict(extra="forbid")

    verification_result_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    verifier_id: str = Field(min_length=1, max_length=64)
    verifier_version: str = Field(min_length=1, max_length=64)
    target_ref: str = Field(min_length=1, max_length=255)
    status: VerificationStatus
    configuration_hash: str = Field(pattern=_DIGEST)
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)
    producer_session_id: str | None = Field(default=None, min_length=1, max_length=64)
    verifier_session_id: str | None = Field(default=None, min_length=1, max_length=64)


class FinalVerificationCreate(BaseModel):
    """The run-level verdict that permits projection, and the scope it may be shared at."""

    model_config = ConfigDict(extra="forbid")

    status: VerificationState
    source: ExperienceSourceKind = ExperienceSourceKind.RUN
    visibility: Visibility = Visibility.PROJECT


class ContradictionResolutionCreate(BaseModel):
    """The adjudication text registry §17 requires a resolution to carry."""

    model_config = ConfigDict(extra="forbid")

    resolution: str = Field(min_length=1, max_length=1_000)


class _FeedbackApi(Protocol):
    """The pipeline as this adapter uses it: two writers, the store and the projector."""

    store: StateStore
    projector: ExperienceProjector

    async def record_local(
        self,
        *,
        run: Run,
        task: Task,
        execution_instance_id: str,
        session_id: str | None,
        results: list[VerificationResult],
        policy: AcceptancePolicy,
        configuration_hash: str,
        verifier_session_ids: dict[str, str | None] | None = ...,
    ) -> IndependentVerificationResult: ...

    async def record_final(
        self,
        *,
        run: Run,
        status: VerificationState,
        source: ExperienceSourceKind,
        principal: PrincipalRef,
        visibility: Visibility = ...,
    ) -> list[ExperienceRecord]: ...


router = APIRouter(tags=["feedback"])


def _pipeline(request: Request) -> _FeedbackApi:
    manager = getattr(request.app.state, "manager", None)
    pipeline = getattr(manager, "feedback_pipeline", None) if manager else None
    if pipeline is None:
        raise RoutingError(
            FEEDBACK_UNAVAILABLE,
            "the feedback pipeline is unavailable",
            status_code=409,
        )
    return cast(_FeedbackApi, pipeline)


def _principal_ref(request: Request) -> PrincipalRef:
    who = current_principal(request)
    return PrincipalRef(
        principal_id=who.principal_id,
        display_name=who.display_name,
        status=who.status,
    )


async def _readable(store: StateStore, workspace_id: str, request: Request) -> None:
    """Refuse a workspace the caller is not a member of, as an absence.

    ``KeyError`` and not ``PermissionError``: SDD §10.1 scopes every record in this family to
    a workspace, and a cross-tenant read must be indistinguishable from a read of something
    that was never written.
    """

    memberships = await store.list_workspace_memberships(
        workspace_id=workspace_id, principal_id=_principal_ref(request).principal_id
    )
    if not memberships:
        raise KeyError(workspace_id)


async def _run_of(store: StateStore, run_id: str) -> Run:
    """The run named in the path, or an absence."""

    run = await store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    return run


async def _graded_against(store: StateStore, run: Run) -> tuple[Task, AcceptancePolicy]:
    """The two frozen documents a §7.9 record is computed against.

    Both are required and neither is defaulted. A verdict recorded against an invented policy
    would name a verifier contract nobody froze, and the acceptance policy is exactly what
    ``record_local`` digests into the record's ``VerifierRef`` — so a run that names none is a
    run this verdict cannot be filed against, which is a 404 about that policy.
    """

    task = await store.get_task(run.task_id)
    if task is None:
        raise KeyError(run.task_id)
    policy = await store.get_acceptance_policy(run.acceptance_policy_id or "")
    if policy is None:
        raise KeyError(run.acceptance_policy_id or run.run_id)
    return task, policy


@router.post(
    "/api/v1/node-executions/{execution_instance_id}/verification-results",
    response_model=IndependentVerificationResult,
    status_code=201,
)
async def record_verification_result(
    execution_instance_id: str,
    payload: VerificationResultCreate,
    request: Request,
) -> IndependentVerificationResult:
    """Ingest one verdict, or return the record a prior identical submission already made.

    Idempotence is decided by looking for the ``source_verification_id`` rather than by
    letting the append-only store refuse the second write: the two calls are seconds apart, so
    their records differ in ``signed_at`` and the store would reject the retry as a mutation
    of an immutable row — a 409 for a caller who did exactly the right thing.
    """

    pipeline = _pipeline(request)
    store = pipeline.store
    run = await _run_of(store, payload.run_id)
    await _readable(store, await workspace_for_run(store, run), request)
    task, policy = await _graded_against(store, run)
    existing = [
        record
        for record in await store.list_verification_results(
            workspace_id=await workspace_for_run(store, run), project_id=run.project_id
        )
        if record.source_verification_id == payload.verification_result_id
    ]
    if existing:
        return existing[0]
    result = VerificationResult(
        verification_id=payload.verification_result_id,
        run_id=run.run_id,
        verifier_id=payload.verifier_id,
        verifier_version=payload.verifier_version,
        target_ref=payload.target_ref,
        status=payload.status,
        evidence_refs=list(payload.evidence_refs),
    )
    try:
        return await pipeline.record_local(
            run=run,
            task=task,
            execution_instance_id=execution_instance_id,
            session_id=payload.producer_session_id,
            results=[result],
            policy=policy,
            configuration_hash=payload.configuration_hash,
            verifier_session_ids={payload.verifier_id: payload.verifier_session_id},
        )
    except ValueError as exc:
        raise RoutingError(VERIFICATION_RESULT_IMMUTABLE, str(exc), status_code=409) from exc


@router.post(
    "/api/v1/runs/{run_id}/final-verification",
    response_model=list[ExperienceRecord],
    status_code=201,
)
async def record_final_verification(
    run_id: str, payload: FinalVerificationCreate, request: Request
) -> list[ExperienceRecord]:
    """Project one experience record per routed node of a graded run (ADR-048)."""

    pipeline = _pipeline(request)
    store = pipeline.store
    run = await _run_of(store, run_id)
    await _readable(store, await workspace_for_run(store, run), request)
    return await pipeline.record_final(
        run=run,
        status=payload.status,
        source=payload.source,
        principal=_principal_ref(request),
        visibility=payload.visibility,
    )


@router.get("/api/v1/experiences/search", response_model=list[ExperienceRecord])
async def search_experience_records(
    request: Request,
    workspace_id: str,
    project_id: str | None = None,
    node_kind: GraphNodeKind | None = None,
    objective_digest: str | None = None,
    capability_digest: str | None = None,
    verification_spec_hash: str | None = None,
    risk_class: RiskClass | None = None,
    eligible_only: bool = False,
) -> list[ExperienceRecord]:
    """The §7.10 records for a workspace, narrowed by any part of the retrieval key.

    Declared before ``/{experience_record_id}`` because FastAPI matches in declaration order
    and ``search`` would otherwise be read as a record id — a 404 for a working query.

    The signature filters are separate parameters rather than one packed key so that a
    partial question is askable: "every record for this objective" is what an operator
    investigating a regression asks, and it is not a whole ``ContractSignature``.
    """

    store = _pipeline(request).store
    await _readable(store, workspace_id, request)
    records = await store.list_experience_records(
        workspace_id=workspace_id, project_id=project_id
    )
    return [
        record
        for record in records
        if _matches(
            record.contract_signature,
            node_kind=node_kind,
            objective_digest=objective_digest,
            capability_digest=capability_digest,
            verification_spec_hash=verification_spec_hash,
            risk_class=risk_class,
        )
        and (record.eligible_for_learning or not eligible_only)
    ]


@router.get(
    "/api/v1/experiences/{experience_record_id}", response_model=ExperienceRecord
)
async def get_experience_record(
    experience_record_id: str, request: Request
) -> ExperienceRecord:
    """One projection by id, or a 404 for one that is not this caller's to read."""

    store = _pipeline(request).store
    record = await store.get_experience_record(experience_record_id)
    if record is None:
        raise KeyError(experience_record_id)
    try:
        await _readable(store, record.workspace_id, request)
    except KeyError as exc:
        raise KeyError(experience_record_id) from exc
    return record


@router.post(
    "/api/v1/experiences/{experience_record_id}/resolve-contradiction",
    response_model=ExperienceRecord,
    status_code=201,
)
async def resolve_experience_contradiction(
    experience_record_id: str,
    payload: ContradictionResolutionCreate,
    request: Request,
) -> ExperienceRecord:
    """Append the ``RESOLVED`` revision that adjudicates one open contradiction.

    A 201 because the answer is a *new row* and not an edit of the one named in the path:
    §7.10's history is append-only, and a 200 would suggest the record the caller addressed
    now reads differently.
    """

    pipeline = _pipeline(request)
    store = pipeline.store
    record = await store.get_experience_record(experience_record_id)
    if record is None:
        raise KeyError(experience_record_id)
    try:
        await _readable(store, record.workspace_id, request)
    except KeyError as exc:
        raise KeyError(experience_record_id) from exc
    try:
        return await pipeline.projector.resolve_contradiction(
            experience_id=record.labels.get(EXPERIENCE_ID_LABEL, record.contract_id),
            workspace_id=record.workspace_id,
            record_id=experience_record_id,
            resolution=payload.resolution,
            principal=_principal_ref(request),
        )
    except ValueError as exc:
        raise RoutingError(CONTRADICTION_NOT_OPEN, str(exc), status_code=409) from exc


def _matches(
    signature: ContractSignature,
    *,
    node_kind: GraphNodeKind | None,
    objective_digest: str | None,
    capability_digest: str | None,
    verification_spec_hash: str | None,
    risk_class: RiskClass | None,
) -> bool:
    """Every stated filter must agree; an unstated one constrains nothing."""

    return all(
        expected is None or actual == expected
        for actual, expected in (
            (signature.node_kind, node_kind),
            (signature.objective_digest, objective_digest),
            (signature.capability_digest, capability_digest),
            (signature.verification_spec_hash, verification_spec_hash),
            (signature.risk_class, risk_class),
        )
    )


__all__ = [
    "ContradictionResolutionCreate",
    "FinalVerificationCreate",
    "VerificationResultCreate",
    "router",
]
