from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from accretion import __version__
from accretion.config import Settings, get_settings
from accretion.models import (
    Approval,
    ApprovalDecisionRequest,
    MessageRequest,
    ProviderHealth,
    ProviderName,
    ResumeRequest,
    Session,
    SessionDetail,
    SessionStatus,
    StartSessionRequest,
)
from accretion.service import AccretionService, ConflictError, NotFoundError


def create_app(
    settings: Settings | None = None, *, service: AccretionService | None = None
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_service = service or AccretionService(resolved_settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await resolved_service.initialize()
        try:
            yield
        finally:
            await resolved_service.close()

    app = FastAPI(
        title="Accretion",
        version=__version__,
        description="Local-first control plane for Codex and Claude Code",
        lifespan=lifespan,
    )
    app.state.service = resolved_service

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/v1/config")
    async def public_config() -> dict[str, object]:
        return {
            "workspace_roots": [str(path) for path in resolved_settings.normalized_roots()],
            "history_storage": "full_local",
        }

    @app.get("/api/v1/providers", response_model=list[ProviderHealth])
    async def providers() -> list[ProviderHealth]:
        return await resolved_service.provider_health()

    @app.get("/api/v1/sessions", response_model=list[Session])
    async def sessions(
        provider: ProviderName | None = None,
        session_status: Annotated[SessionStatus | None, Query(alias="status")] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[Session]:
        return await resolved_service.list_sessions(provider, session_status, limit)

    @app.get("/api/v1/sessions/{session_id}", response_model=SessionDetail)
    async def session_detail(session_id: str) -> SessionDetail:
        return await _translate_errors(resolved_service.get_session(session_id))

    @app.post("/api/v1/sessions", response_model=Session, status_code=status.HTTP_201_CREATED)
    async def start_session(request: StartSessionRequest) -> Session:
        return await _translate_errors(
            resolved_service.start_session(
                provider=request.provider,
                cwd=request.cwd,
                prompt=request.prompt,
                title=request.title,
                provider_session_id=request.provider_session_id,
            )
        )

    @app.post("/api/v1/sessions/{session_id}/resume", response_model=Session)
    async def resume_session(session_id: str, request: ResumeRequest) -> Session:
        return await _translate_errors(resolved_service.resume_session(session_id, request.prompt))

    @app.post("/api/v1/sessions/{session_id}/messages", response_model=Session)
    async def send_message(session_id: str, request: MessageRequest) -> Session:
        return await _translate_errors(resolved_service.send_message(session_id, request.prompt))

    @app.post("/api/v1/sessions/{session_id}/interrupt", response_model=Session)
    async def interrupt_session(session_id: str) -> Session:
        return await _translate_errors(resolved_service.interrupt(session_id))

    @app.post("/api/v1/approvals/{approval_id}/decision", response_model=Approval)
    async def decide_approval(approval_id: str, request: ApprovalDecisionRequest) -> Approval:
        return await _translate_errors(
            resolved_service.decide_approval(approval_id, request.decision)
        )

    @app.delete("/api/v1/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_session(session_id: str) -> None:
        await _translate_errors(resolved_service.delete_session(session_id))

    @app.delete("/api/v1/history")
    async def clear_history() -> dict[str, int]:
        return {"deleted": await resolved_service.clear_history()}

    @app.post("/api/v1/history/import")
    async def import_history() -> dict[str, int]:
        return {"imported": await resolved_service.import_history()}

    @app.websocket("/api/v1/events")
    async def events(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json(
            {
                "sequence": 0,
                "type": "snapshot",
                "data": {
                    "sessions": [
                        item.model_dump(mode="json")
                        for item in await resolved_service.list_sessions(limit=500)
                    ]
                },
            }
        )
        try:
            async with resolved_service.broker.subscribe() as queue:
                while True:
                    event_task = asyncio.create_task(queue.get())
                    receive_task = asyncio.create_task(websocket.receive())
                    done, pending = await asyncio.wait(
                        {event_task, receive_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    if receive_task in done:
                        message = receive_task.result()
                        if message["type"] == "websocket.disconnect":
                            return
                    if event_task in done:
                        envelope = event_task.result()
                        await websocket.send_json(envelope.model_dump(mode="json"))
        except WebSocketDisconnect:
            return

    frontend_dist = resolved_settings.frontend_dist or (
        Path(__file__).resolve().parents[3] / "frontend" / "dist"
    )
    assets = frontend_dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str) -> FileResponse:
        requested = frontend_dist / path
        if path and requested.is_file() and requested.is_relative_to(frontend_dist):
            return FileResponse(requested)
        index = frontend_dist / "index.html"
        if index.is_file():
            return FileResponse(index)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Frontend has not been built. Run `pnpm --dir frontend build`.",
        )

    return app


async def _translate_errors[ResponseT](awaitable: Awaitable[ResponseT]) -> ResponseT:
    try:
        return await awaitable
    except NotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {error}") from error
