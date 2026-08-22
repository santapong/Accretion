from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from accretion.api import create_app
from accretion.config import Settings
from accretion.models import (
    ApprovalDecision,
    ProviderCapabilities,
    ProviderHealth,
    ProviderName,
)
from accretion.providers.base import ProviderAdapter, ProviderEvent, ProviderHistoryItem
from accretion.service import AccretionService
from httpx import ASGITransport, AsyncClient


class FakeAdapter(ProviderAdapter):
    def __init__(self, name: ProviderName) -> None:
        super().__init__(self._discard)
        self.name = name
        self.history: list[ProviderHistoryItem] = []
        self.actions: list[tuple[str, Any]] = []
        self.sessions: dict[str, str] = {}

    async def _discard(self, _event: ProviderEvent) -> None:
        return None

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            name=self.name,
            available=True,
            version="test",
            capabilities=ProviderCapabilities(),
        )

    async def discover_history(self) -> list[ProviderHistoryItem]:
        return self.history

    async def start_session(self, local_session_id: str, cwd: str, prompt: str) -> str:
        provider_id = f"{self.name.value}-provider-{local_session_id}"
        self.sessions[local_session_id] = provider_id
        self.actions.append(("start", (local_session_id, cwd, prompt)))
        await self.event_sink(ProviderEvent(local_session_id, "running", {"prompt": prompt}))
        return provider_id

    async def resume_session(
        self, local_session_id: str, provider_session_id: str, prompt: str | None
    ) -> None:
        self.sessions[local_session_id] = provider_session_id
        self.actions.append(("resume", (local_session_id, provider_session_id, prompt)))

    async def send_message(self, local_session_id: str, prompt: str) -> None:
        self.actions.append(("message", (local_session_id, prompt)))

    async def interrupt(self, local_session_id: str) -> None:
        self.actions.append(("interrupt", local_session_id))

    async def resolve_approval(
        self,
        local_session_id: str,
        provider_request_id: str,
        decision: ApprovalDecision,
    ) -> None:
        self.actions.append(("approval", (local_session_id, provider_request_id, decision)))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return Settings(data_dir=tmp_path / "data", workspace_roots=[tmp_path])


@pytest.fixture
async def service(settings: Settings) -> AsyncIterator[AccretionService]:
    codex = FakeAdapter(ProviderName.CODEX)
    claude = FakeAdapter(ProviderName.CLAUDE)
    instance = AccretionService(
        settings,
        adapters={ProviderName.CODEX: codex, ProviderName.CLAUDE: claude},
    )
    codex.event_sink = instance.handle_provider_event
    claude.event_sink = instance.handle_provider_event
    await instance.initialize()
    yield instance
    await instance.close()


@pytest.fixture
async def client(settings: Settings, service: AccretionService) -> AsyncIterator[AsyncClient]:
    app = create_app(settings, service=service)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value
