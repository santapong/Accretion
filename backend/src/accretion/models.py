from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class ProviderName(StrEnum):
    CODEX = "codex"
    CLAUDE = "claude"


class SessionStatus(StrEnum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    OFFLINE = "offline"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    APPROVE_SESSION = "approve_session"
    DENY = "deny"
    CANCEL = "cancel"


class ProviderCapabilities(BaseModel):
    history: bool = True
    start: bool = True
    resume: bool = True
    steer: bool = True
    interrupt: bool = True
    approvals: bool = True


class ProviderHealth(BaseModel):
    name: ProviderName
    available: bool
    authenticated: bool | None = None
    version: str | None = None
    detail: str | None = None
    capabilities: ProviderCapabilities = Field(default_factory=ProviderCapabilities)


class Session(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    provider: ProviderName
    provider_session_id: str | None = None
    title: str
    cwd: str
    status: SessionStatus = SessionStatus.COMPLETED
    managed: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_error: str | None = None


class TimelineEvent(BaseModel):
    id: int | None = None
    session_id: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    provider_event_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class Approval(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    provider_request_id: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    decision: ApprovalDecision | None = None
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None


class SessionDetail(Session):
    events: list[TimelineEvent] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)


class StartSessionRequest(BaseModel):
    provider: ProviderName
    cwd: str
    prompt: str = Field(min_length=1, max_length=100_000)
    title: str | None = Field(default=None, max_length=200)
    provider_session_id: str | None = None


class MessageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=100_000)


class ResumeRequest(BaseModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=100_000)


class ApprovalDecisionRequest(BaseModel):
    decision: ApprovalDecision


class EventEnvelope(BaseModel):
    sequence: int
    type: str
    data: dict[str, Any]
