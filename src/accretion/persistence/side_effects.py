from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from accretion.ids import new_id
from accretion.persistence.models import SideEffectOperationRow


class SideEffectStatus(StrEnum):
    INTENT_RECORDED = "INTENT_RECORDED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class SideEffectOperation(BaseModel):
    operation_id: str
    run_id: str
    idempotency_key: str
    capability_id: str
    status: SideEffectStatus
    intent_payload: dict[str, Any] = Field(default_factory=dict)
    result_payload: dict[str, Any] | None = None


class SideEffectLedger(Protocol):
    async def record_intent(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        capability_id: str,
        payload: dict[str, Any],
    ) -> tuple[SideEffectOperation, bool]: ...
    async def finish(
        self, idempotency_key: str, *, succeeded: bool, result: dict[str, Any]
    ) -> SideEffectOperation: ...
    async def reconcile_uncertain(self) -> list[SideEffectOperation]: ...


class MemorySideEffectLedger:
    def __init__(self) -> None:
        self.operations: dict[str, SideEffectOperation] = {}
        self._lock = asyncio.Lock()

    async def record_intent(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        capability_id: str,
        payload: dict[str, Any],
    ) -> tuple[SideEffectOperation, bool]:
        async with self._lock:
            existing = self.operations.get(idempotency_key)
            if existing:
                return existing, False
            operation = SideEffectOperation(
                operation_id=new_id("side_effect"),
                run_id=run_id,
                idempotency_key=idempotency_key,
                capability_id=capability_id,
                status=SideEffectStatus.INTENT_RECORDED,
                intent_payload=payload,
            )
            self.operations[idempotency_key] = operation
            return operation, True

    async def finish(
        self, idempotency_key: str, *, succeeded: bool, result: dict[str, Any]
    ) -> SideEffectOperation:
        current = self.operations[idempotency_key]
        updated = current.model_copy(
            update={
                "status": SideEffectStatus.SUCCEEDED if succeeded else SideEffectStatus.FAILED,
                "result_payload": result,
            }
        )
        self.operations[idempotency_key] = updated
        return updated

    async def reconcile_uncertain(self) -> list[SideEffectOperation]:
        uncertain = []
        for key, operation in self.operations.items():
            if operation.status in {SideEffectStatus.INTENT_RECORDED, SideEffectStatus.EXECUTING}:
                operation = operation.model_copy(update={"status": SideEffectStatus.UNKNOWN})
                self.operations[key] = operation
                uncertain.append(operation)
        return uncertain


class PostgresSideEffectLedger:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def record_intent(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        capability_id: str,
        payload: dict[str, Any],
    ) -> tuple[SideEffectOperation, bool]:
        operation_id = new_id("side_effect")
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            inserted_id = await session.scalar(
                insert(SideEffectOperationRow)
                .values(
                    id=operation_id,
                    run_id=run_id,
                    idempotency_key=idempotency_key,
                    capability_id=capability_id,
                    status=SideEffectStatus.INTENT_RECORDED.value,
                    intent_payload=payload,
                    result_payload=None,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
                .returning(SideEffectOperationRow.id)
            )
            if inserted_id is not None:
                return (
                    SideEffectOperation(
                        operation_id=operation_id,
                        run_id=run_id,
                        idempotency_key=idempotency_key,
                        capability_id=capability_id,
                        status=SideEffectStatus.INTENT_RECORDED,
                        intent_payload=payload,
                    ),
                    True,
                )
            existing = await session.scalar(
                select(SideEffectOperationRow).where(
                    SideEffectOperationRow.idempotency_key == idempotency_key
                )
            )
            if existing is None:  # defensive: the conflict row must be visible now
                raise RuntimeError("idempotency conflict row was not readable")
            return self._from_row(existing), False

    async def finish(
        self, idempotency_key: str, *, succeeded: bool, result: dict[str, Any]
    ) -> SideEffectOperation:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(SideEffectOperationRow)
                .where(SideEffectOperationRow.idempotency_key == idempotency_key)
                .with_for_update()
            )
            if row is None:
                raise KeyError(idempotency_key)
            row.status = (
                SideEffectStatus.SUCCEEDED.value if succeeded else SideEffectStatus.FAILED.value
            )
            row.result_payload = result
            row.updated_at = datetime.now(UTC)
            await session.flush()
            return self._from_row(row)

    async def reconcile_uncertain(self) -> list[SideEffectOperation]:
        async with self.sessions.begin() as session:
            rows = (
                await session.scalars(
                    select(SideEffectOperationRow)
                    .where(
                        SideEffectOperationRow.status.in_(
                            [
                                SideEffectStatus.INTENT_RECORDED.value,
                                SideEffectStatus.EXECUTING.value,
                            ]
                        )
                    )
                    .with_for_update()
                )
            ).all()
            now = datetime.now(UTC)
            for row in rows:
                row.status = SideEffectStatus.UNKNOWN.value
                row.updated_at = now
            await session.flush()
            return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: SideEffectOperationRow) -> SideEffectOperation:
        return SideEffectOperation(
            operation_id=row.id,
            run_id=row.run_id,
            idempotency_key=row.idempotency_key,
            capability_id=row.capability_id,
            status=SideEffectStatus(row.status),
            intent_payload=row.intent_payload,
            result_payload=row.result_payload,
        )
