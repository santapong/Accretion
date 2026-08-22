from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from uuid import uuid4

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from accretion import __version__
from accretion.api.schemas import (
    ApprovalDecisionCreate,
    ErrorEnvelope,
    ProjectCreate,
    RunCreate,
    StrategyOverrideCreate,
    TaskCreate,
    WorkflowTemplateSummary,
)
from accretion.concurrency import ConcurrencyLimiter
from accretion.config import get_settings
from accretion.contracts import (
    TERMINAL_RUN_STATES,
    AgentRuntime,
    ApprovalRecord,
    ApprovalStatus,
    ArtifactRef,
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
    RuntimeHealth,
    StrategyOverrideResult,
    Task,
    TaskPlanning,
    TemplateStatus,
    VerificationResult,
)
from accretion.governance import seed_governance
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.side_effects import PostgresSideEffectLedger
from accretion.persistence.store import PostgresStore
from accretion.runtimes import ClaudeRuntime, CodexRuntime, FakeRuntime
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
    await seed_templates(store)
    await seed_governance(store)
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
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Last-Event-ID"],
)


def manager(request: Request) -> RunManager:
    return cast(RunManager, request.app.state.manager)


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


@app.get("/api/v1/capabilities", response_model=list[Capability])
async def list_capabilities(request: Request) -> list[Capability]:
    return await manager(request).store.list_capabilities()


@app.get("/api/v1/skills", response_model=list[MetaSkill])
async def list_skills(request: Request) -> list[MetaSkill]:
    return await manager(request).store.list_skills()


@app.get("/api/v1/plugins", response_model=list[MetaPlugin])
async def list_plugins(request: Request) -> list[MetaPlugin]:
    return await manager(request).store.list_plugins()


@app.get("/api/v1/runs/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    run = await manager(request).store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    try:
        after = int(last_event_id or 0)
    except ValueError as exc:
        raise ValueError("Last-Event-ID must be an integer") from exc

    async def stream() -> AsyncIterator[str]:
        cursor = after
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
