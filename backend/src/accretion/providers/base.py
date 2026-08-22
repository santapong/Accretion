from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from accretion.models import ApprovalDecision, ProviderHealth, ProviderName


@dataclass(slots=True)
class ProviderEvent:
    local_session_id: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    provider_event_id: str | None = None


@dataclass(slots=True)
class ProviderHistoryItem:
    provider_session_id: str
    title: str
    cwd: str
    created_at: datetime
    updated_at: datetime


EventSink = Callable[[ProviderEvent], Awaitable[None]]


class ProviderAdapter(ABC):
    name: ProviderName

    def __init__(self, event_sink: EventSink) -> None:
        self.event_sink = event_sink

    @abstractmethod
    async def health(self) -> ProviderHealth:
        raise NotImplementedError

    @abstractmethod
    async def discover_history(self) -> list[ProviderHistoryItem]:
        raise NotImplementedError

    @abstractmethod
    async def start_session(self, local_session_id: str, cwd: str, prompt: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def resume_session(
        self, local_session_id: str, provider_session_id: str, prompt: str | None
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def send_message(self, local_session_id: str, prompt: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def interrupt(self, local_session_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def resolve_approval(
        self,
        local_session_id: str,
        provider_request_id: str,
        decision: ApprovalDecision,
    ) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        return None


class ProviderUnavailableError(RuntimeError):
    pass


class ProviderProtocolError(RuntimeError):
    pass
