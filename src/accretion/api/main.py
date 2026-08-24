from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from uuid import uuid4

from fastapi import FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from accretion import __version__
from accretion.api.schemas import (
    ApprovalDecisionCreate,
    BenchmarkRunCreate,
    ErrorEnvelope,
    ExperienceMaterializeCreate,
    ExperienceQueryCreate,
    ExperienceRetractCreate,
    ExperienceSelectionCreate,
    ProjectCreate,
    ProjectFeatureUpdate,
    ReplanCreate,
    RunCreate,
    SearchCreate,
    StrategyOverrideCreate,
    TaskCreate,
    WorkflowProposeCreate,
    WorkflowTemplateSummary,
)
from accretion.benchmark import (
    AcrArchRunner,
    acr_arch_summary,
    acr_arch_task_detail,
    seed_acr_arch,
)
from accretion.concurrency import ConcurrencyLimiter
from accretion.config import get_settings
from accretion.contracts import (
    TERMINAL_RUN_STATES,
    AcrArchSummary,
    AgentRuntime,
    ApprovalRecord,
    ApprovalStatus,
    ArtifactRef,
    BenchmarkExecutionSource,
    BenchmarkRun,
    BenchmarkTaskDetail,
    Capability,
    EventType,
    ExecutionMode,
    ExecutionTrace,
    GraphProjection,
    LoopExecution,
    MetaPlugin,
    MetaSkill,
    Project,
    Provider,
    Run,
    RunAudit,
    RuntimeHealth,
    SessionRef,
    StrategyOverrideResult,
    Task,
    TaskPlanning,
    TaskProfile,
    TaskType,
    TemplateStatus,
    VerificationResult,
)
from accretion.experience.models import (
    Experience,
    ExperienceDetail,
    ExperienceMatch,
    ExperienceSelection,
    TrajectorySeed,
)
from accretion.experience.service import (
    ExperienceConflictError,
    ExperienceDisabledError,
    ExperienceService,
)
from accretion.experience_benchmark import (
    ExperienceBenchmarkRunner,
    ExperienceBenchmarkSummary,
)
from accretion.governance import seed_governance
from accretion.orchestration.models import (
    CandidateScore,
    CandidateTrajectory,
    GraphRevisionDiff,
    GraphValidationResult,
    ProjectFeatureSettings,
    ReplanOutcome,
    ReplanRequest,
    RunGraphRevision,
    RuntimeDecision,
    SearchRecord,
    WorkflowActivationOutcome,
    WorkflowProposal,
    WorkflowValidationOutcome,
)
from accretion.orchestration.search import (
    CandidateSearchConflictError,
    CandidateSearchDisabledError,
    SearchService,
)
from accretion.orchestration.service import (
    DynamicWorkflowConflictError,
    DynamicWorkflowDisabledError,
    DynamicWorkflowService,
)
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.side_effects import PostgresSideEffectLedger
from accretion.persistence.store import PostgresStore
from accretion.runtimes import ClaudeRuntime, CodexRuntime, FakeRuntime
from accretion.search_benchmark import SearchBenchmarkRunner, SearchBenchmarkSummary
from accretion.services.run_manager import (
    ProjectionUnavailableError,
    RunManager,
    WorkflowTemplateError,
)
from accretion.templates import seed_templates
from accretion.verifiers.registry import VerifierUnavailableError
from accretion.workspace import WorktreeManager

SSE_TERMINAL_EVENTS = {
    EventType.RUN_COMPLETED,
    EventType.RUN_FAILED,
    EventType.RUN_CANCELLED,
}


class ExecutionModeMismatchError(RuntimeError):
    def __init__(self, run: Run) -> None:
        mode = run.execution_mode.value if run.execution_mode is not None else "UNKNOWN"
        template = run.workflow_template_id or "unknown-template"
        super().__init__(
            f"Run {run.run_id} uses {mode}/{template}; this endpoint requires LOOP/feedback-loop-v1"
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    store = PostgresStore(sessions)
    gateway_environment = {
        "ACCRETION_DATABASE_URL": settings.database_url,
        "ACCRETION_CAPABILITY_POLICY_ID": settings.capability_policy_id,
        "ACCRETION_GRANTED_PERMISSIONS": json.dumps(settings.granted_permissions),
        "ACCRETION_CREDENTIAL_ENV_MAP": json.dumps(settings.credential_env_map),
    }
    runtimes: dict[Provider, AgentRuntime] = {
        Provider.FAKE: FakeRuntime(),
        Provider.CODEX: CodexRuntime(settings.codex_command, gateway_environment),
        Provider.CLAUDE: ClaudeRuntime(settings.claude_command, gateway_environment),
    }
    manager = RunManager(
        store=store,
        worktrees=WorktreeManager(settings.worktree_dir, settings.artifact_dir),
        runtimes=runtimes,
        limiter=ConcurrencyLimiter(
            global_limit=settings.global_max_runs,
            provider_limit=settings.provider_max_runs,
            project_limit=settings.project_max_runs,
        ),
        live_providers_enabled=settings.enable_live_providers,
        side_effect_ledger=PostgresSideEffectLedger(sessions),
        operator_identity=settings.operator_identity,
        auto_resume_on_reconcile=settings.auto_resume_on_reconcile,
    )
    app.state.engine = engine
    app.state.manager = manager
    app.state.dynamic_workflows = DynamicWorkflowService(
        manager,
        globally_enabled=settings.enable_dynamic_workflows,
        operator_identity=settings.operator_identity,
    )
    experience = ExperienceService(
        manager,
        globally_enabled=settings.enable_experience_retrieval,
        operator_identity=settings.operator_identity,
    )
    app.state.experience = experience
    search_service = SearchService(
        manager,
        globally_enabled=settings.enable_candidate_search,
        operator_identity=settings.operator_identity,
        experience_service=experience,
    )
    app.state.candidate_search = search_service
    await seed_templates(store)
    await seed_governance(store)
    await seed_acr_arch(store)
    await search_service.reconcile()
    await manager.reconcile()
    yield
    for task in manager.background.values():
        if not task.done():
            task.cancel()
    codex = runtimes[Provider.CODEX]
    if isinstance(codex, CodexRuntime):
        await codex.close()
    await engine.dispose()


app = FastAPI(title="Accretion API", version=__version__, lifespan=lifespan)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "Last-Event-ID"],
)


def manager(request: Request) -> RunManager:
    return cast(RunManager, request.app.state.manager)


def dynamic_workflows(request: Request) -> DynamicWorkflowService:
    return cast(DynamicWorkflowService, request.app.state.dynamic_workflows)


def candidate_search(request: Request) -> SearchService:
    return cast(SearchService, request.app.state.candidate_search)


def experience_service(request: Request) -> ExperienceService:
    return cast(ExperienceService, request.app.state.experience)


@app.exception_handler(KeyError)
async def key_error_handler(request: Request, exc: KeyError) -> JSONResponse:
    return _error(404, "NOT_FOUND", f"Resource {exc.args[0]} was not found")


@app.exception_handler(PermissionError)
async def permission_error_handler(request: Request, exc: PermissionError) -> JSONResponse:
    return _error(403, "PROVIDER_DISABLED", str(exc))


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return _error(400, "INVALID_REQUEST", str(exc))


@app.exception_handler(WorkflowTemplateError)
async def workflow_template_handler(
    request: Request, exc: WorkflowTemplateError
) -> JSONResponse:
    return _error(409, exc.code, str(exc))


@app.exception_handler(ProjectionUnavailableError)
async def projection_unavailable_handler(
    request: Request, exc: ProjectionUnavailableError
) -> JSONResponse:
    return _error(409, "PROJECTION_UNAVAILABLE", str(exc))


@app.exception_handler(VerifierUnavailableError)
async def verifier_unavailable_handler(
    request: Request, exc: VerifierUnavailableError
) -> JSONResponse:
    return _error(409, "VERIFIER_UNAVAILABLE", str(exc))


@app.exception_handler(ExecutionModeMismatchError)
async def execution_mode_mismatch_handler(
    request: Request, exc: ExecutionModeMismatchError
) -> JSONResponse:
    return _error(409, "EXECUTION_MODE_MISMATCH", str(exc))


@app.exception_handler(DynamicWorkflowDisabledError)
async def dynamic_workflow_disabled_handler(
    request: Request, exc: DynamicWorkflowDisabledError
) -> JSONResponse:
    return _error(403, "DYNAMIC_WORKFLOWS_DISABLED", str(exc))


@app.exception_handler(DynamicWorkflowConflictError)
async def dynamic_workflow_conflict_handler(
    request: Request, exc: DynamicWorkflowConflictError
) -> JSONResponse:
    return _error(409, "DYNAMIC_WORKFLOW_CONFLICT", str(exc))


@app.exception_handler(CandidateSearchDisabledError)
async def candidate_search_disabled_handler(
    request: Request, exc: CandidateSearchDisabledError
) -> JSONResponse:
    return _error(403, "CANDIDATE_SEARCH_DISABLED", str(exc))


@app.exception_handler(CandidateSearchConflictError)
async def candidate_search_conflict_handler(
    request: Request, exc: CandidateSearchConflictError
) -> JSONResponse:
    code = (
        "REPLAY_BRANCH_REQUIRES_P7"
        if str(exc) == "REPLAY_BRANCH_REQUIRES_P7"
        else "CANDIDATE_SEARCH_CONFLICT"
    )
    return _error(409, code, str(exc))


@app.exception_handler(ExperienceDisabledError)
async def experience_disabled_handler(
    request: Request, exc: ExperienceDisabledError
) -> JSONResponse:
    return _error(403, "EXPERIENCE_RETRIEVAL_DISABLED", str(exc))


@app.exception_handler(ExperienceConflictError)
async def experience_conflict_handler(
    request: Request, exc: ExperienceConflictError
) -> JSONResponse:
    return _error(409, "EXPERIENCE_CONFLICT", str(exc))


def _error(status: int, code: str, message: str, retryable: bool = False) -> JSONResponse:
    body = ErrorEnvelope(
        code=code,
        message=message,
        correlation_id=str(uuid4()),
        retryable=retryable,
    )
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.post("/api/v1/projects", response_model=Project, status_code=201)
async def create_project(payload: ProjectCreate, request: Request) -> Project:
    return await manager(request).create_project(payload.name, payload.repository_path)


@app.get("/api/v1/projects", response_model=list[Project])
async def list_projects(request: Request) -> list[Project]:
    return await manager(request).store.list_projects()


@app.get("/api/v1/projects/{project_id}", response_model=Project)
async def get_project(project_id: str, request: Request) -> Project:
    project = await manager(request).store.get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    return project


@app.get(
    "/api/v2/projects/{project_id}/features",
    response_model=ProjectFeatureSettings,
)
async def get_project_features(
    project_id: str, request: Request
) -> ProjectFeatureSettings:
    return await dynamic_workflows(request).get_project_features(project_id)


@app.patch(
    "/api/v2/projects/{project_id}/features",
    response_model=ProjectFeatureSettings,
)
async def update_project_features(
    project_id: str, payload: ProjectFeatureUpdate, request: Request
) -> ProjectFeatureSettings:
    return await dynamic_workflows(request).update_project_features(
        project_id,
        dynamic_workflows=payload.dynamic_workflows,
        candidate_search=payload.candidate_search,
        experience_retrieval=payload.experience_retrieval,
        expected_revision=payload.expected_revision,
    )


@app.post("/api/v1/tasks", response_model=Task, status_code=201)
async def create_task(payload: TaskCreate, request: Request) -> Task:
    return await manager(request).create_task(
        project_id=payload.project_id,
        objective=payload.objective,
        task_patch=payload.envelope_patch(),
    )


@app.get("/api/v1/tasks/{task_id}", response_model=Task)
async def get_task(task_id: str, request: Request) -> Task:
    task = await manager(request).store.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    return task


@app.get("/api/v1/tasks/{task_id}/planning", response_model=TaskPlanning)
async def get_task_planning(task_id: str, request: Request) -> TaskPlanning:
    return await manager(request).get_task_planning(task_id)


@app.get("/api/v1/tasks/{task_id}/profile", response_model=TaskProfile)
async def get_task_profile(task_id: str, request: Request) -> TaskProfile:
    return (await manager(request).get_task_planning(task_id)).current_profile


@app.post(
    "/api/v2/tasks/{task_id}/workflow/propose",
    response_model=WorkflowProposal,
    status_code=201,
)
async def propose_dynamic_workflow(
    task_id: str, payload: WorkflowProposeCreate, request: Request
) -> WorkflowProposal:
    return await dynamic_workflows(request).propose(
        task_id,
        execution_provider=payload.execution_provider,
        planner_runtime=payload.planner_runtime,
    )


@app.post(
    "/api/v1/tasks/{task_id}/strategy/override",
    response_model=StrategyOverrideResult,
    status_code=201,
)
@app.post(
    "/api/v1/tasks/{task_id}/strategy-overrides",
    response_model=StrategyOverrideResult,
    status_code=201,
)
async def create_strategy_override(
    task_id: str, payload: StrategyOverrideCreate, request: Request
) -> StrategyOverrideResult:
    return await manager(request).override_strategy(
        task_id=task_id,
        requested_mode=payload.requested_mode,
        requested_template_id=payload.requested_template_id,
        reason=payload.reason,
    )


@app.post("/api/v1/tasks/{task_id}/runs", response_model=Run, status_code=202)
async def start_run(task_id: str, payload: RunCreate, request: Request) -> Run:
    return await manager(request).start_run(task_id, payload.provider)


@app.get("/api/v1/runs", response_model=list[Run])
async def list_runs(request: Request, limit: int = 100) -> list[Run]:
    return await manager(request).store.list_runs(min(max(limit, 1), 500))


@app.get("/api/v1/runs/{run_id}", response_model=Run)
async def get_run(run_id: str, request: Request) -> Run:
    run = await manager(request).store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    return run


@app.post(
    "/api/v2/runs/{run_id}/experiences",
    response_model=ExperienceDetail,
    status_code=201,
)
async def materialize_experience(
    run_id: str, payload: ExperienceMaterializeCreate, request: Request
) -> ExperienceDetail:
    return await experience_service(request).materialize(
        run_id, candidate_id=payload.candidate_id
    )


@app.get("/api/v2/experiences", response_model=list[Experience])
async def list_experiences(
    request: Request,
    project_id: str | None = None,
    repository_identity: str | None = None,
    include_retracted: bool = False,
) -> list[Experience]:
    return await experience_service(request).list_experiences(
        project_id=project_id,
        repository_identity=repository_identity,
        include_retracted=include_retracted,
    )


@app.get("/api/v2/experiences/{experience_id}", response_model=ExperienceDetail)
async def get_experience(experience_id: str, request: Request) -> ExperienceDetail:
    return await experience_service(request).detail(experience_id)


@app.post("/api/v2/experiences/{experience_id}/retract", response_model=Experience)
async def retract_experience(
    experience_id: str, payload: ExperienceRetractCreate, request: Request
) -> Experience:
    return await experience_service(request).retract(
        experience_id,
        reason=payload.reason,
        expected_revision=payload.expected_revision,
    )


@app.post("/api/v2/experiences/query", response_model=list[ExperienceMatch])
async def query_experience(
    payload: ExperienceQueryCreate, request: Request
) -> list[ExperienceMatch]:
    return await experience_service(request).query(
        payload.task_id,
        include_failures=payload.include_failures,
        top_k=payload.top_k,
        max_age_days=payload.max_age_days,
    )


@app.post(
    "/api/v2/tasks/{task_id}/experience-selections",
    response_model=ExperienceSelection,
    status_code=201,
)
async def create_experience_selection(
    task_id: str, payload: ExperienceSelectionCreate, request: Request
) -> ExperienceSelection:
    return await experience_service(request).select(
        task_id,
        query_id=payload.query_id,
        match_ids=payload.match_ids,
        expected_context_bundle_id=payload.expected_context_bundle_id,
    )


@app.get(
    "/api/v2/tasks/{task_id}/experience-selections",
    response_model=list[ExperienceSelection],
)
async def list_experience_selections(
    task_id: str, request: Request
) -> list[ExperienceSelection]:
    return await experience_service(request).selections(task_id)


@app.get(
    "/api/v2/tasks/{task_id}/experience-matches",
    response_model=list[ExperienceMatch],
)
async def list_selected_experience_matches(
    task_id: str, request: Request
) -> list[ExperienceMatch]:
    return await experience_service(request).selected_matches(task_id)


@app.get(
    "/api/v2/runs/{run_id}/workflow/proposals",
    response_model=list[WorkflowProposal],
)
async def list_dynamic_workflow_proposals(
    run_id: str, request: Request
) -> list[WorkflowProposal]:
    if await manager(request).store.get_run(run_id) is None:
        raise KeyError(run_id)
    return await manager(request).store.list_workflow_proposals(run_id=run_id)


@app.get(
    "/api/v2/runs/{run_id}/workflow/proposals/{proposal_id}",
    response_model=WorkflowProposal,
)
async def get_dynamic_workflow_proposal(
    run_id: str, proposal_id: str, request: Request
) -> WorkflowProposal:
    proposal = await manager(request).store.get_workflow_proposal(proposal_id)
    if proposal is None:
        raise KeyError(proposal_id)
    if proposal.run_id != run_id:
        raise DynamicWorkflowConflictError(
            f"proposal {proposal_id} does not belong to run {run_id}"
        )
    return proposal


@app.get(
    "/api/v2/runs/{run_id}/workflow/proposals/{proposal_id}/validations",
    response_model=list[GraphValidationResult],
)
async def list_dynamic_workflow_validations(
    run_id: str, proposal_id: str, request: Request
) -> list[GraphValidationResult]:
    await get_dynamic_workflow_proposal(run_id, proposal_id, request)
    return await manager(request).store.list_graph_validations(proposal_id)


@app.post(
    "/api/v2/runs/{run_id}/workflow/proposals/{proposal_id}/validate",
    response_model=WorkflowValidationOutcome,
)
async def validate_dynamic_workflow(
    run_id: str, proposal_id: str, request: Request
) -> WorkflowValidationOutcome:
    return await dynamic_workflows(request).validate(run_id, proposal_id)


@app.post(
    "/api/v2/runs/{run_id}/workflow/proposals/{proposal_id}/activate",
    response_model=WorkflowActivationOutcome,
    status_code=202,
)
async def activate_dynamic_workflow(
    run_id: str, proposal_id: str, request: Request
) -> WorkflowActivationOutcome:
    return await dynamic_workflows(request).activate(run_id, proposal_id)


@app.post(
    "/api/v2/runs/{run_id}/replan",
    response_model=ReplanOutcome,
    status_code=202,
)
async def replan_dynamic_workflow(
    run_id: str, payload: ReplanCreate, request: Request
) -> ReplanOutcome:
    return await dynamic_workflows(request).replan(
        run_id,
        reason=payload.reason,
        evidence_refs=payload.evidence_refs,
    )


@app.get(
    "/api/v2/runs/{run_id}/replans",
    response_model=list[ReplanRequest],
)
async def list_replan_requests(run_id: str, request: Request) -> list[ReplanRequest]:
    if await manager(request).store.get_run(run_id) is None:
        raise KeyError(run_id)
    return await manager(request).store.list_replan_requests(run_id)


@app.get(
    "/api/v2/runs/{run_id}/graph/revisions",
    response_model=list[RunGraphRevision],
)
async def list_graph_revisions(run_id: str, request: Request) -> list[RunGraphRevision]:
    if await manager(request).store.get_run(run_id) is None:
        raise KeyError(run_id)
    return await manager(request).store.list_graph_revisions(run_id)


@app.get(
    "/api/v2/runs/{run_id}/graph/revisions/{revision}",
    response_model=RunGraphRevision,
)
async def get_graph_revision(
    run_id: str, revision: int, request: Request
) -> RunGraphRevision:
    result = await manager(request).store.get_graph_revision(run_id, revision)
    if result is None:
        raise KeyError(f"{run_id}/revision/{revision}")
    return result


@app.get(
    "/api/v2/runs/{run_id}/graph/diff",
    response_model=GraphRevisionDiff,
)
async def get_graph_revision_diff(
    run_id: str,
    request: Request,
    from_revision: int = Query(alias="from", ge=1),
    to_revision: int = Query(alias="to", ge=1),
) -> GraphRevisionDiff:
    return await dynamic_workflows(request).graph_diff(
        run_id, from_revision, to_revision
    )


@app.get(
    "/api/v2/runs/{run_id}/runtime-decisions",
    response_model=list[RuntimeDecision],
)
async def list_runtime_decisions(run_id: str, request: Request) -> list[RuntimeDecision]:
    if await manager(request).store.get_run(run_id) is None:
        raise KeyError(run_id)
    return await manager(request).store.list_runtime_decisions(run_id)


@app.post(
    "/api/v2/runs/{run_id}/search",
    response_model=SearchRecord,
    status_code=201,
)
async def create_candidate_search(
    run_id: str, payload: SearchCreate, request: Request
) -> SearchRecord:
    return await candidate_search(request).create_plan(
        run_id,
        parent_node_id=payload.parent_node_id,
        mode=payload.mode,
        branch_count=payload.branch_count,
        max_parallel=payload.max_parallel,
        per_branch_budget=payload.per_branch_budget,
        total_budget=payload.total_budget,
        candidate_directives=payload.candidate_directives,
        replay_seed_match_ids=payload.replay_seed_match_ids,
        negative_guidance_match_ids=payload.negative_guidance_match_ids,
    )


@app.get(
    "/api/v2/runs/{run_id}/searches",
    response_model=list[SearchRecord],
)
async def list_candidate_searches(run_id: str, request: Request) -> list[SearchRecord]:
    if await manager(request).store.get_run(run_id) is None:
        raise KeyError(run_id)
    return await manager(request).store.list_searches(run_id)


@app.get("/api/v2/search/{search_id}", response_model=SearchRecord)
async def get_candidate_search(search_id: str, request: Request) -> SearchRecord:
    return await candidate_search(request).get(search_id)


@app.get(
    "/api/v2/search/{search_id}/candidates",
    response_model=list[CandidateTrajectory],
)
async def list_search_candidates(
    search_id: str, request: Request
) -> list[CandidateTrajectory]:
    await candidate_search(request).get(search_id)
    return await manager(request).store.list_search_candidates(search_id)


@app.get(
    "/api/v2/search/{search_id}/scores",
    response_model=list[CandidateScore],
)
async def list_search_scores(search_id: str, request: Request) -> list[CandidateScore]:
    await candidate_search(request).get(search_id)
    return await manager(request).store.list_candidate_scores(search_id)


@app.get(
    "/api/v2/search/{search_id}/replay-seeds",
    response_model=list[TrajectorySeed],
)
async def list_replay_seeds(search_id: str, request: Request) -> list[TrajectorySeed]:
    await candidate_search(request).get(search_id)
    return await manager(request).store.list_trajectory_seeds(search_id)


@app.post("/api/v2/search/{search_id}/cancel", response_model=SearchRecord)
async def cancel_candidate_search(search_id: str, request: Request) -> SearchRecord:
    return await candidate_search(request).cancel(search_id)


@app.get("/api/v1/runs/{run_id}/artifacts", response_model=list[ArtifactRef])
async def list_artifacts(run_id: str, request: Request) -> list[ArtifactRef]:
    if await manager(request).store.get_run(run_id) is None:
        raise KeyError(run_id)
    return await manager(request).store.list_artifacts(run_id)


@app.get("/api/v1/runs/{run_id}/loop", response_model=LoopExecution)
async def get_loop_execution(run_id: str, request: Request) -> LoopExecution:
    await _require_loop_run(run_id, request)
    return await manager(request).get_loop(run_id)


@app.get("/api/v1/runs/{run_id}/graph", response_model=GraphProjection)
async def get_run_graph(run_id: str, request: Request) -> GraphProjection:
    return await manager(request).get_graph(run_id)


@app.get("/api/v1/runs/{run_id}/trace", response_model=ExecutionTrace)
async def get_run_trace(run_id: str, request: Request) -> ExecutionTrace:
    return await manager(request).get_trace(run_id)


@app.get("/api/v1/runs/{run_id}/audit", response_model=RunAudit)
async def get_run_audit(run_id: str, request: Request) -> RunAudit:
    service = manager(request)
    run = await service.store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    task = await service.store.get_task(run.task_id)
    planning = await service.store.get_task_planning(run.task_id)
    template = (
        await service.store.get_workflow_template(run.workflow_template_id)
        if run.workflow_template_id
        else None
    )
    if task is None or planning is None or template is None:
        raise RuntimeError(f"run {run_id} has incomplete planning provenance")
    runtime = await service.runtimes[run.provider].health()
    return RunAudit(
        run=run,
        task=task,
        profile=planning.current_profile,
        strategy=planning.current_decision,
        template=template,
        runtime=runtime,
        session=await service.store.get_session_for_run(run_id),
        events=await service.store.list_events(run_id),
        artifacts=await service.store.list_artifacts(run_id),
        verifications=await service.store.list_verifications(run_id),
        capability_results=await service.store.list_capability_results(run_id),
        trace=await service.get_trace(run_id),
    )


@app.get("/api/v1/templates", response_model=list[WorkflowTemplateSummary])
async def list_templates(
    request: Request, status: str | None = None
) -> list[WorkflowTemplateSummary]:
    parsed: TemplateStatus | None
    if status is None:
        parsed = TemplateStatus.VALIDATED
    else:
        try:
            parsed = TemplateStatus(status)
        except ValueError as exc:
            raise ValueError(f"unknown template status {status!r}") from exc
    templates = await manager(request).store.list_workflow_templates(parsed)
    return [
        WorkflowTemplateSummary(
            template_id=template.template_id,
            version=template.version,
            mode=template.mode,
            status=template.status,
            checksum=template.checksum,
        )
        for template in templates
    ]


@app.get("/api/v1/approvals", response_model=list[ApprovalRecord])
async def list_approvals(
    request: Request, run_id: str | None = None, status: str | None = None
) -> list[ApprovalRecord]:
    parsed: ApprovalStatus | None = None
    if status is not None:
        try:
            parsed = ApprovalStatus(status)
        except ValueError as exc:
            raise ValueError(f"unknown approval status {status!r}") from exc
    return await manager(request).store.list_approvals(run_id, parsed)


@app.post("/api/v1/approvals/{approval_id}/decision", response_model=ApprovalRecord)
async def decide_approval(
    approval_id: str, payload: ApprovalDecisionCreate, request: Request
) -> ApprovalRecord:
    return await manager(request).resolve_approval(approval_id, payload.decision)


@app.get(
    "/api/v1/runs/{run_id}/verifications",
    response_model=list[VerificationResult],
)
async def list_verifications(run_id: str, request: Request) -> list[VerificationResult]:
    if await manager(request).store.get_run(run_id) is None:
        raise KeyError(run_id)
    return await manager(request).store.list_verifications(run_id)


@app.get(
    "/api/v1/verifications/{verification_id}",
    response_model=VerificationResult,
)
async def get_verification(verification_id: str, request: Request) -> VerificationResult:
    result = await manager(request).store.get_verification(verification_id)
    if result is None:
        raise KeyError(verification_id)
    return result


@app.post("/api/v1/runs/{run_id}/pause", response_model=Run)
async def pause_run(run_id: str, request: Request) -> Run:
    return await manager(request).pause(run_id)


@app.post("/api/v1/runs/{run_id}/resume", response_model=Run)
async def resume_run(run_id: str, request: Request) -> Run:
    return await manager(request).resume(run_id)


@app.post("/api/v1/runs/{run_id}/cancel", response_model=Run)
async def cancel_run(run_id: str, request: Request) -> Run:
    return await manager(request).cancel(run_id)


@app.get("/api/v1/runtimes", response_model=list[RuntimeHealth])
async def list_runtimes(request: Request) -> list[RuntimeHealth]:
    runtimes = manager(request).runtimes.values()
    return await asyncio.gather(*(runtime.health() for runtime in runtimes))


@app.get("/api/v1/runtimes/{runtime_id}/health", response_model=RuntimeHealth)
async def runtime_health(runtime_id: str, request: Request) -> RuntimeHealth:
    for runtime in manager(request).runtimes.values():
        health = await runtime.health()
        if health.runtime_id == runtime_id:
            return health
    raise KeyError(runtime_id)


@app.get("/api/v1/runtimes/{runtime_id}/sessions", response_model=list[SessionRef])
async def runtime_sessions(runtime_id: str, request: Request) -> list[SessionRef]:
    for runtime in manager(request).runtimes.values():
        health = await runtime.health()
        if health.runtime_id == runtime_id:
            return await manager(request).store.list_sessions(health.provider)
    raise KeyError(runtime_id)


@app.get("/api/v1/capabilities", response_model=list[Capability])
async def list_capabilities(request: Request) -> list[Capability]:
    return await manager(request).store.list_capabilities()


@app.get("/api/v1/skills", response_model=list[MetaSkill])
async def list_skills(request: Request) -> list[MetaSkill]:
    return await manager(request).store.list_skills()


@app.get("/api/v1/plugins", response_model=list[MetaPlugin])
async def list_plugins(request: Request) -> list[MetaPlugin]:
    return await manager(request).store.list_plugins()


@app.get("/api/v1/benchmarks/acr-arch", response_model=AcrArchSummary)
async def get_acr_arch(
    request: Request,
    mode: ExecutionMode | None = None,
    provider: Provider | None = None,
    task_type: TaskType | None = None,
    verifier: str | None = None,
    selector_version: str | None = None,
) -> AcrArchSummary:
    return await acr_arch_summary(
        manager(request).store,
        mode=mode,
        provider=provider,
        task_type=task_type,
        verifier=verifier,
        selector_version=selector_version,
    )


@app.post(
    "/api/v1/benchmarks/acr-arch/run",
    response_model=BenchmarkRun,
    status_code=201,
)
async def run_acr_arch(payload: BenchmarkRunCreate, request: Request) -> BenchmarkRun:
    if payload.execution_source is not BenchmarkExecutionSource.REPLAY:
        raise ValueError("live benchmark runs require the explicit local CLI release gate")
    return await AcrArchRunner().persist(manager(request).store)


@app.get(
    "/api/v1/benchmarks/acr-arch/tasks/{task_id}",
    response_model=BenchmarkTaskDetail,
)
async def get_acr_arch_task(task_id: str, request: Request) -> BenchmarkTaskDetail:
    detail = await acr_arch_task_detail(manager(request).store, task_id)
    if detail is None:
        raise KeyError(task_id)
    return detail


@app.get("/api/v2/benchmarks/search", response_model=SearchBenchmarkSummary)
async def get_search_benchmark() -> SearchBenchmarkSummary:
    return SearchBenchmarkRunner().run()


@app.post(
    "/api/v2/benchmarks/search/run",
    response_model=SearchBenchmarkSummary,
)
async def run_search_benchmark(payload: BenchmarkRunCreate) -> SearchBenchmarkSummary:
    if payload.execution_source is not BenchmarkExecutionSource.REPLAY:
        raise ValueError("live search calibration requires the explicit local release gate")
    return SearchBenchmarkRunner().run()


@app.get(
    "/api/v2/benchmarks/experience",
    response_model=ExperienceBenchmarkSummary,
)
async def get_experience_benchmark() -> ExperienceBenchmarkSummary:
    return ExperienceBenchmarkRunner().run()


@app.post(
    "/api/v2/benchmarks/experience/run",
    response_model=ExperienceBenchmarkSummary,
)
async def run_experience_benchmark(
    payload: BenchmarkRunCreate,
) -> ExperienceBenchmarkSummary:
    if payload.execution_source is not BenchmarkExecutionSource.REPLAY:
        raise ValueError("live experience calibration requires the explicit local release gate")
    return ExperienceBenchmarkRunner().run()


@app.get("/api/v1/runs/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    run = await manager(request).store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    try:
        cursor_start = int(last_event_id) if last_event_id is not None else after
    except ValueError as exc:
        raise ValueError("Last-Event-ID must be an integer") from exc

    async def stream() -> AsyncIterator[str]:
        cursor = cursor_start
        terminal_event_seen = any(
            event.sequence <= cursor and event.normalized_type in SSE_TERMINAL_EVENTS
            for event in await manager(request).store.list_events(run_id)
        )
        while not await request.is_disconnected():
            events = await manager(request).store.list_events(run_id, cursor)
            for event in events:
                cursor = event.sequence
                terminal_event_seen = (
                    terminal_event_seen or event.normalized_type in SSE_TERMINAL_EVENTS
                )
                data = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
                yield f"id: {cursor}\nevent: agent_event\ndata: {data}\n\n"
            current = await manager(request).store.get_run(run_id)
            if current is None or (
                current.state in TERMINAL_RUN_STATES
                and cursor >= current.last_sequence
                and terminal_event_seen
            ):
                break
            if current.state in TERMINAL_RUN_STATES:
                # Defensively tolerate a store or recovery path that exposes terminal state
                # just before its durable terminal event becomes visible.
                await asyncio.sleep(0.01)
                continue
            yield ": keepalive\n\n"
            await manager(request).wait_for_events(run_id, cursor)

    return StreamingResponse(stream(), media_type="text/event-stream")


async def _require_loop_run(run_id: str, request: Request) -> Run:
    run = await manager(request).store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    if run.execution_mode is not ExecutionMode.LOOP:
        raise ExecutionModeMismatchError(run)
    return run
