from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from accretion.models import (
    Approval,
    ApprovalDecision,
    ApprovalStatus,
    ProviderName,
    Session,
    SessionDetail,
    SessionStatus,
    TimelineEvent,
    utc_now,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_session_id TEXT,
    title TEXT NOT NULL,
    cwd TEXT NOT NULL,
    status TEXT NOT NULL,
    managed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_error TEXT,
    UNIQUE(provider, provider_session_id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    provider_event_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, provider_event_id)
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    provider_request_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    decision TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE(session_id, provider_request_id)
);

CREATE INDEX IF NOT EXISTS idx_approvals_session ON approvals(session_id, created_at DESC);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self._connect() as connection:
            await connection.executescript(SCHEMA)
            await connection.commit()

    def _connect(self) -> aiosqlite.Connection:
        return aiosqlite.connect(self.path)

    async def create_session(self, session: Session) -> Session:
        async with self._connect() as connection:
            await connection.execute(
                """
                INSERT INTO sessions (
                    id, provider, provider_session_id, title, cwd, status, managed,
                    created_at, updated_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._session_values(session),
            )
            await connection.commit()
        return session

    async def import_session(self, session: Session) -> tuple[Session, bool]:
        existing = await self.get_by_provider_id(session.provider, session.provider_session_id)
        if existing:
            return existing, False
        try:
            await self.create_session(session)
        except aiosqlite.IntegrityError:
            existing = await self.get_by_provider_id(session.provider, session.provider_session_id)
            if existing:
                return existing, False
            raise
        return session, True

    async def get_by_provider_id(
        self, provider: ProviderName, provider_session_id: str | None
    ) -> Session | None:
        if not provider_session_id:
            return None
        async with self._connect() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                "SELECT * FROM sessions WHERE provider = ? AND provider_session_id = ?",
                (provider.value, provider_session_id),
            )
            row = await cursor.fetchone()
        return self._row_to_session(row) if row else None

    async def get_session(self, session_id: str) -> Session | None:
        async with self._connect() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = await cursor.fetchone()
        return self._row_to_session(row) if row else None

    async def get_session_detail(self, session_id: str) -> SessionDetail | None:
        session = await self.get_session(session_id)
        if not session:
            return None
        return SessionDetail(
            **session.model_dump(),
            events=await self.list_events(session_id),
            approvals=await self.list_approvals(session_id),
        )

    async def list_sessions(
        self,
        *,
        provider: ProviderName | None = None,
        status: SessionStatus | None = None,
        limit: int = 100,
    ) -> list[Session]:
        clauses: list[str] = []
        values: list[Any] = []
        if provider:
            clauses.append("provider = ?")
            values.append(provider.value)
        if status:
            clauses.append("status = ?")
            values.append(status.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        async with self._connect() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                f"SELECT * FROM sessions{where} ORDER BY updated_at DESC LIMIT ?",  # noqa: S608
                values,
            )
            rows = await cursor.fetchall()
        return [self._row_to_session(row) for row in rows]

    async def update_session(self, session_id: str, **changes: Any) -> Session | None:
        allowed = {
            "provider_session_id",
            "title",
            "cwd",
            "status",
            "managed",
            "last_error",
        }
        clean = {key: value for key, value in changes.items() if key in allowed}
        if not clean:
            return await self.get_session(session_id)
        clean["updated_at"] = utc_now()
        columns = ", ".join(f"{key} = ?" for key in clean)
        values = [self._db_value(value) for value in clean.values()]
        values.append(session_id)
        async with self._connect() as connection:
            await connection.execute(f"UPDATE sessions SET {columns} WHERE id = ?", values)  # noqa: S608
            await connection.commit()
        return await self.get_session(session_id)

    async def add_event(self, event: TimelineEvent) -> TimelineEvent:
        async with self._connect() as connection:
            cursor = await connection.execute(
                """
                INSERT OR IGNORE INTO events (
                    session_id, kind, payload, provider_event_id, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.session_id,
                    event.kind,
                    json.dumps(event.payload, default=str),
                    event.provider_event_id,
                    event.created_at.isoformat(),
                ),
            )
            await connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (event.created_at.isoformat(), event.session_id),
            )
            await connection.commit()
            if cursor.lastrowid:
                event.id = int(cursor.lastrowid)
        return event

    async def list_events(self, session_id: str, *, after: int = 0) -> list[TimelineEvent]:
        async with self._connect() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                "SELECT * FROM events WHERE session_id = ? AND id > ? ORDER BY id",
                (session_id, after),
            )
            rows = await cursor.fetchall()
        return [self._row_to_event(row) for row in rows]

    async def create_approval(self, approval: Approval) -> Approval:
        async with self._connect() as connection:
            await connection.execute(
                """
                INSERT OR IGNORE INTO approvals (
                    id, session_id, provider_request_id, kind, payload, status,
                    decision, created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.id,
                    approval.session_id,
                    approval.provider_request_id,
                    approval.kind,
                    json.dumps(approval.payload, default=str),
                    approval.status.value,
                    approval.decision.value if approval.decision else None,
                    approval.created_at.isoformat(),
                    approval.resolved_at.isoformat() if approval.resolved_at else None,
                ),
            )
            await connection.commit()
        return approval

    async def get_approval(self, approval_id: str) -> Approval | None:
        async with self._connect() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            )
            row = await cursor.fetchone()
        return self._row_to_approval(row) if row else None

    async def list_approvals(self, session_id: str) -> list[Approval]:
        async with self._connect() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                "SELECT * FROM approvals WHERE session_id = ? ORDER BY created_at DESC",
                (session_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_approval(row) for row in rows]

    async def resolve_approval(
        self, approval_id: str, decision: ApprovalDecision
    ) -> Approval | None:
        status = {
            ApprovalDecision.APPROVE: ApprovalStatus.APPROVED,
            ApprovalDecision.APPROVE_SESSION: ApprovalStatus.APPROVED,
            ApprovalDecision.DENY: ApprovalStatus.DENIED,
            ApprovalDecision.CANCEL: ApprovalStatus.CANCELLED,
        }[decision]
        resolved_at = utc_now()
        async with self._connect() as connection:
            await connection.execute(
                """
                UPDATE approvals SET status = ?, decision = ?, resolved_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    status.value,
                    decision.value,
                    resolved_at.isoformat(),
                    approval_id,
                    ApprovalStatus.PENDING.value,
                ),
            )
            await connection.commit()
        return await self.get_approval(approval_id)

    async def delete_session(self, session_id: str) -> bool:
        async with self._connect() as connection:
            await connection.execute("PRAGMA foreign_keys = ON")
            cursor = await connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            await connection.commit()
        return cursor.rowcount > 0

    async def clear_history(self) -> int:
        async with self._connect() as connection:
            cursor = await connection.execute(
                "DELETE FROM sessions WHERE status NOT IN (?, ?)",
                (SessionStatus.RUNNING.value, SessionStatus.WAITING_APPROVAL.value),
            )
            await connection.commit()
        return cursor.rowcount

    async def mark_active_offline(self) -> None:
        async with self._connect() as connection:
            await connection.execute(
                """
                UPDATE sessions SET status = ?, updated_at = ?
                WHERE status IN (?, ?)
                """,
                (
                    SessionStatus.OFFLINE.value,
                    utc_now().isoformat(),
                    SessionStatus.RUNNING.value,
                    SessionStatus.WAITING_APPROVAL.value,
                ),
            )
            await connection.commit()

    @staticmethod
    def _session_values(session: Session) -> tuple[Any, ...]:
        return (
            session.id,
            session.provider.value,
            session.provider_session_id,
            session.title,
            session.cwd,
            session.status.value,
            int(session.managed),
            session.created_at.isoformat(),
            session.updated_at.isoformat(),
            session.last_error,
        )

    @staticmethod
    def _db_value(value: Any) -> Any:
        if isinstance(value, (SessionStatus, ProviderName)):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, bool):
            return int(value)
        return value

    @staticmethod
    def _row_to_session(row: aiosqlite.Row) -> Session:
        return Session(
            id=row["id"],
            provider=row["provider"],
            provider_session_id=row["provider_session_id"],
            title=row["title"],
            cwd=row["cwd"],
            status=row["status"],
            managed=bool(row["managed"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_error=row["last_error"],
        )

    @staticmethod
    def _row_to_event(row: aiosqlite.Row) -> TimelineEvent:
        return TimelineEvent(
            id=row["id"],
            session_id=row["session_id"],
            kind=row["kind"],
            payload=json.loads(row["payload"]),
            provider_event_id=row["provider_event_id"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_approval(row: aiosqlite.Row) -> Approval:
        return Approval(
            id=row["id"],
            session_id=row["session_id"],
            provider_request_id=row["provider_request_id"],
            kind=row["kind"],
            payload=json.loads(row["payload"]),
            status=row["status"],
            decision=row["decision"],
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
        )
