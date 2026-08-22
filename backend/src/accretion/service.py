from __future__ import annotations

import asyncio

from accretion.config import Settings
from accretion.database import Database
from accretion.events import EventBroker
from accretion.models import (
    Approval,
    ApprovalDecision,
    ApprovalStatus,
    ProviderHealth,
    ProviderName,
    Session,
    SessionDetail,
    SessionStatus,
    TimelineEvent,
)
from accretion.providers import ClaudeAdapter, CodexAdapter, ProviderAdapter, ProviderEvent


class NotFoundError(RuntimeError):
    pass


class ConflictError(RuntimeError):
    pass


class AccretionService:
    def __init__(
        self,
        settings: Settings,
        *,
        database: Database | None = None,
        broker: EventBroker | None = None,
        adapters: dict[ProviderName, ProviderAdapter] | None = None,
    ) -> None:
        self.settings = settings
        self.database = database or Database(settings.database_path)
        self.broker = broker or EventBroker()
        self.adapters = adapters or {
            ProviderName.CODEX: CodexAdapter(self.handle_provider_event, settings.codex_command),
            ProviderName.CLAUDE: ClaudeAdapter(
                self.handle_provider_event, settings.claude_projects_dir
            ),
        }

    async def initialize(self) -> None:
        await self.database.initialize()
        await self.database.mark_active_offline()
        await self.import_history()

    async def close(self) -> None:
        await asyncio.gather(
            *(adapter.close() for adapter in self.adapters.values()), return_exceptions=True
        )

    async def provider_health(self) -> list[ProviderHealth]:
        return list(await asyncio.gather(*(adapter.health() for adapter in self.adapters.values())))

    async def import_history(self) -> int:
        imported = 0
        for provider, adapter in self.adapters.items():
            try:
                history = await adapter.discover_history()
            except Exception:
                continue
            for item in history:
                session = Session(
                    provider=provider,
                    provider_session_id=item.provider_session_id,
                    title=item.title,
                    cwd=item.cwd,
                    status=SessionStatus.COMPLETED,
                    managed=False,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
                _, created = await self.database.import_session(session)
                imported += int(created)
        if imported:
            await self.broker.publish("history.imported", {"count": imported})
        return imported

    async def list_sessions(
        self,
        provider: ProviderName | None = None,
        status: SessionStatus | None = None,
        limit: int = 100,
    ) -> list[Session]:
        return await self.database.list_sessions(provider=provider, status=status, limit=limit)

    async def get_session(self, session_id: str) -> SessionDetail:
        detail = await self.database.get_session_detail(session_id)
        if not detail:
            raise NotFoundError("Session not found")
        return detail

    async def start_session(
        self,
        *,
        provider: ProviderName,
        cwd: str,
        prompt: str,
        title: str | None = None,
        provider_session_id: str | None = None,
    ) -> Session:
        workspace = self.settings.validate_workspace(cwd)
        if provider_session_id:
            existing = await self.database.get_by_provider_id(provider, provider_session_id)
            if existing:
                return await self.resume_session(existing.id, prompt)
        session = Session(
            provider=provider,
            provider_session_id=provider_session_id,
            title=title or self._title_from_prompt(prompt),
            cwd=str(workspace),
            status=SessionStatus.RUNNING,
            managed=True,
        )
        await self.database.create_session(session)
        try:
            adapter = self.adapters[provider]
            if provider_session_id:
                await adapter.resume_session(session.id, provider_session_id, prompt)
            else:
                returned_id = await adapter.start_session(session.id, str(workspace), prompt)
                session.provider_session_id = returned_id
            updated = await self.database.update_session(
                session.id,
                provider_session_id=session.provider_session_id,
                managed=True,
                status=SessionStatus.RUNNING,
            )
            assert updated
            await self.broker.publish("session.updated", updated.model_dump(mode="json"))
            return updated
        except Exception as error:
            await self.database.update_session(
                session.id, status=SessionStatus.FAILED, last_error=str(error)
            )
            raise

    async def resume_session(self, session_id: str, prompt: str | None = None) -> Session:
        session = await self._required_session(session_id)
        if not session.provider_session_id or session.provider_session_id.startswith("pending:"):
            raise ConflictError("Session does not have a resumable provider session ID")
        workspace = self.settings.validate_workspace(session.cwd)
        await self.adapters[session.provider].resume_session(
            session.id, session.provider_session_id, prompt
        )
        updated = await self.database.update_session(
            session.id,
            cwd=str(workspace),
            managed=True,
            status=SessionStatus.RUNNING if prompt else SessionStatus.COMPLETED,
            last_error=None,
        )
        assert updated
        await self.broker.publish("session.updated", updated.model_dump(mode="json"))
        return updated

    async def send_message(self, session_id: str, prompt: str) -> Session:
        session = await self._required_session(session_id)
        if not session.managed:
            raise ConflictError("Resume this imported session before sending input")
        await self.adapters[session.provider].send_message(session.id, prompt)
        updated = await self.database.update_session(
            session.id, status=SessionStatus.RUNNING, last_error=None
        )
        assert updated
        return updated

    async def interrupt(self, session_id: str) -> Session:
        session = await self._required_session(session_id)
        if not session.managed:
            raise ConflictError("Imported sessions cannot be interrupted")
        await self.adapters[session.provider].interrupt(session.id)
        updated = await self.database.update_session(session.id, status=SessionStatus.INTERRUPTED)
        assert updated
        await self.broker.publish("session.updated", updated.model_dump(mode="json"))
        return updated

    async def decide_approval(self, approval_id: str, decision: ApprovalDecision) -> Approval:
        approval = await self.database.get_approval(approval_id)
        if not approval:
            raise NotFoundError("Approval not found")
        if approval.status != ApprovalStatus.PENDING:
            raise ConflictError("Approval is no longer pending")
        session = await self._required_session(approval.session_id)
        await self.adapters[session.provider].resolve_approval(
            session.id, approval.provider_request_id, decision
        )
        resolved = await self.database.resolve_approval(approval.id, decision)
        assert resolved
        status = (
            SessionStatus.INTERRUPTED
            if decision == ApprovalDecision.CANCEL
            else SessionStatus.RUNNING
        )
        await self.database.update_session(session.id, status=status)
        await self.broker.publish("approval.resolved", resolved.model_dump(mode="json"))
        return resolved

    async def delete_session(self, session_id: str) -> None:
        session = await self._required_session(session_id)
        if session.status in {SessionStatus.RUNNING, SessionStatus.WAITING_APPROVAL}:
            raise ConflictError("Interrupt the active session before deleting it")
        await self.database.delete_session(session_id)
        await self.broker.publish("session.deleted", {"id": session_id})

    async def clear_history(self) -> int:
        count = await self.database.clear_history()
        await self.broker.publish("history.cleared", {"count": count})
        return count

    async def handle_provider_event(self, provider_event: ProviderEvent) -> None:
        session = await self.database.get_session(provider_event.local_session_id)
        if not session:
            return
        payload = provider_event.payload
        if provider_event.kind == "provider_session" and payload.get("provider_session_id"):
            await self.database.update_session(
                session.id, provider_session_id=str(payload["provider_session_id"])
            )
        if provider_event.kind == "approval":
            approval = Approval(
                session_id=session.id,
                provider_request_id=str(payload["provider_request_id"]),
                kind=str(payload.get("approval_kind", "permission")),
                payload=payload,
            )
            await self.database.create_approval(approval)
            await self.database.update_session(session.id, status=SessionStatus.WAITING_APPROVAL)
            await self.broker.publish("approval.created", approval.model_dump(mode="json"))
        else:
            new_status = {
                "running": SessionStatus.RUNNING,
                "completed": SessionStatus.COMPLETED,
                "interrupted": SessionStatus.INTERRUPTED,
                "error": SessionStatus.FAILED,
            }.get(provider_event.kind)
            if new_status:
                await self.database.update_session(
                    session.id,
                    status=new_status,
                    last_error=(
                        str(payload.get("message")) if new_status == SessionStatus.FAILED else None
                    ),
                )
        event = await self.database.add_event(
            TimelineEvent(
                session_id=session.id,
                kind=provider_event.kind,
                payload=payload,
                provider_event_id=provider_event.provider_event_id,
            )
        )
        await self.broker.publish("timeline.event", event.model_dump(mode="json"))

    async def _required_session(self, session_id: str) -> Session:
        session = await self.database.get_session(session_id)
        if not session:
            raise NotFoundError("Session not found")
        return session

    @staticmethod
    def _title_from_prompt(prompt: str) -> str:
        title = " ".join(prompt.strip().split())
        return title[:80] + ("…" if len(title) > 80 else "")
