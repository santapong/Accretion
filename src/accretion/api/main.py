from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from accretion import __version__
from accretion.api.schemas import (
    ErrorEnvelope,
    ProjectCreate,
    RunCreate,
    StrategyOverrideCreate,
    TaskCreate,
)
from accretion.concurrency import ConcurrencyLimiter
from accretion.config import get_settings
from accretion.contracts import (
    TERMINAL_RUN_STATES,
    AgentRuntime,
    ArtifactRef,
    Project,
    Provider,
    Run,
    RuntimeHealth,
    StrategyOverrideResult,
    Task,
    TaskPlanning,
)
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.side_effects import PostgresSideEffectLedger
from accretion.persistence.store import PostgresStore
from accretion.runtimes import ClaudeRuntime, CodexRuntime, FakeRuntime
from accretion.services.run_manager import MilestoneDependencyError, RunManager
from accretion.workspace import WorktreeManager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    store = PostgresStore(sessions)
    runtimes: dict[Provider, AgentRuntime] = {
        Provider.FAKE: FakeRuntime(),
        Provider.CODEX: CodexRuntime(settings.codex_command),
        Provider.CLAUDE: ClaudeRuntime(settings.claude_command),
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
    )
    app.state.engine = engine
    app.state.manager = manager
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


@app.exception_handler(MilestoneDependencyError)
async def milestone_dependency_handler(
    request: Request, exc: MilestoneDependencyError
) -> JSONResponse:
    return _error(409, "MILESTONE_DEPENDENCY", str(exc))


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
        raise HTTPException(status_code=400, detail="Last-Event-ID must be an integer") from exc

    async def stream() -> AsyncIterator[str]:
        cursor = after
        while not await request.is_disconnected():
            events = await manager(request).store.list_events(run_id, cursor)
            for event in events:
                cursor = event.sequence
                data = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
                yield f"id: {cursor}\nevent: agent_event\ndata: {data}\n\n"
            current = await manager(request).store.get_run(run_id)
            if current is None or (
                current.state in TERMINAL_RUN_STATES and cursor >= current.last_sequence
            ):
                break
            yield ": keepalive\n\n"
            await manager(request).wait_for_events(run_id, cursor)

    return StreamingResponse(stream(), media_type="text/event-stream")
