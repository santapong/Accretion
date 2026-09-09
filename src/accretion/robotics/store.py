"""Focused transactional registry storage over Accretion's existing identities.

These are trusted persistence primitives, not caller-facing authorization APIs.
Every registry transaction locks its project; PostgreSQL additionally locks the
persisted principal and membership while authorizing a write. The in-memory
adapter shares state and its lock across wrappers of the same MemoryStore.
"""

from __future__ import annotations

import asyncio
import builtins
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Protocol, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from accretion.contracts import (
    Principal,
    PrincipalStatus,
    PrincipalType,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.contracts.canonical import content_hash
from accretion.persistence.models import (
    PrincipalRow,
    ProjectRow,
    RoboticsConformanceRow,
    RoboticsContractRow,
    RoboticsEventRow,
    RoboticsIdempotencyRow,
    SimulationProjectBindingRow,
    WorkspaceMembershipRow,
    WorkspaceRow,
)
from accretion.persistence.store import MemoryStore, PostgresStore, StateStore
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code

MAINTAINER_ROLES = frozenset({WorkspaceRole.OWNER, WorkspaceRole.ADMIN, WorkspaceRole.DEVELOPER})

_ACTIVE_UNITS: ContextVar[tuple[tuple[object, asyncio.Task[Any]], ...]] = ContextVar(
    "robotics_active_registry_units", default=()
)


@contextmanager
def _unit_of_work(store_key: object) -> Iterator[None]:
    """Refuse a second same-store UoW, not ordinary reentrant store methods.

    Runtime transactions enter through this registry boundary too. Independent
    snapshots cannot report an inner commit that an outer rollback then erases.
    A child task inherits context variables but not its parent's UoW ownership;
    its independent transaction still waits for the normal backend lock.
    """
    owner = asyncio.current_task()
    active = _ACTIVE_UNITS.get()
    if owner is None or any(key is store_key and task is owner for key, task in active):
        raise RoboticsError(Code.SIMULATION_UNAVAILABLE)
    token = _ACTIVE_UNITS.set((*active, (store_key, owner)))
    try:
        yield
    finally:
        _ACTIVE_UNITS.reset(token)


@dataclass(frozen=True)
class RegistryRecord:
    id: str
    workspace_id: str
    project_id: str
    contract_type: str
    logical_name: str
    version: str
    schema_version: str
    content_hash: str
    original_json: str
    created_by: str
    created_at: datetime
    revision: int = 1


@dataclass(frozen=True)
class IdempotencyScope:
    workspace_id: str
    project_id: str
    principal_id: str
    operation: str
    resource: str
    key: str

    @property
    def identity(self) -> str:
        return content_hash(asdict(self), exclude=())


@dataclass(frozen=True)
class LedgerRecord:
    scope: IdempotencyScope
    request_hash: str
    response_json: str


@dataclass(frozen=True)
class StoredEvent:
    id: str
    aggregate_id: str
    workspace_id: str
    project_id: str
    sequence: int
    content_hash: str
    original_json: str


@dataclass(frozen=True)
class ConformanceLink:
    report_id: str
    adapter_id: str
    closure_hash: str
    revision: int


def _authorize(
    principal: Principal | None,
    membership: WorkspaceMembership | None,
    binding: str | None,
    workspace_id: str,
    *,
    write: bool,
    service: bool,
) -> Principal:
    if principal is None or principal.status is not PrincipalStatus.ACTIVE:
        raise RoboticsError(Code.CAPABILITY_DENIED)
    # A mismatched/unbound project reveals no resource or ownership metadata.
    if binding != workspace_id or membership is None:
        raise RoboticsError(Code.RESOURCE_NOT_FOUND)
    if service:
        if (
            principal.type is not PrincipalType.SERVICE
            or membership.role is not WorkspaceRole.SERVICE
        ):
            raise RoboticsError(Code.CAPABILITY_DENIED)
    elif write and (
        principal.type is not PrincipalType.HUMAN or membership.role not in MAINTAINER_ROLES
    ):
        raise RoboticsError(Code.CAPABILITY_DENIED)
    return principal.model_copy(deep=True)


class RegistryTransaction(Protocol):
    def require_active(self) -> None: ...
    async def authorize(
        self,
        actor_id: str,
        workspace_id: str,
        project_id: str,
        *,
        write: bool = False,
        service: bool = False,
    ) -> Principal: ...
    async def bootstrap_bind(self, workspace_id: str, project_id: str) -> None: ...
    async def get(self, contract_id: str) -> RegistryRecord | None: ...
    async def lookup(
        self,
        workspace_id: str,
        project_id: str,
        contract_type: str,
        *,
        logical_name: str | None = None,
        version: str | None = None,
        digest: str | None = None,
    ) -> RegistryRecord | None: ...
    async def list(
        self, workspace_id: str, project_id: str, contract_type: str, after: str, limit: int
    ) -> builtins.list[RegistryRecord]: ...
    async def insert(self, record: RegistryRecord) -> None: ...
    async def set_revision(self, contract_id: str, revision: int) -> None: ...
    async def ledger(self, scope: IdempotencyScope) -> LedgerRecord | None: ...
    async def remember(self, record: LedgerRecord) -> None: ...
    async def append_event(self, event: StoredEvent) -> None: ...
    async def events(
        self, aggregate_id: str, after: int, limit: int
    ) -> builtins.list[StoredEvent]: ...
    async def link_conformance(self, link: ConformanceLink) -> None: ...
    async def conformance(
        self, adapter_id: str, closure_hash: str | None = None, limit: int = 100
    ) -> builtins.list[ConformanceLink]: ...


class RoboticsStore(Protocol):
    def transaction(self, project_id: str) -> AbstractAsyncContextManager[RegistryTransaction]: ...
    async def bootstrap_bind_project(self, *, workspace_id: str, project_id: str) -> None: ...


def registry_store_for(state: StateStore) -> RoboticsStore:
    """Compose the configured state backend; unsupported stores fail closed."""
    if isinstance(state, MemoryStore):
        return MemoryRoboticsStore(state)
    if isinstance(state, PostgresStore):
        return PostgresRoboticsStore(state)
    raise RoboticsError(Code.SIMULATION_UNAVAILABLE)


class MemoryRoboticsStore:
    def __init__(self, state: MemoryStore) -> None:
        self.state = state
        if not state.robotics_registry_state:
            state.robotics_registry_state.update(
                bindings={}, records={}, ledger={}, events={}, conformance={}
            )

    @asynccontextmanager
    async def transaction(self, project_id: str) -> AsyncIterator[RegistryTransaction]:
        # The mutex identifies the backing state across registry wrappers and
        # MemoryStore's scoped copies, unlike the wrapper object's identity.
        with _unit_of_work(self.state.robotics_registry_lock):
            async with self.state.robotics_registry_lock:
                saved = deepcopy(self.state.robotics_registry_state)
                tx = _MemoryTransaction(self.state, project_id)
                try:
                    yield tx
                except BaseException:
                    self.state.robotics_registry_state.clear()
                    self.state.robotics_registry_state.update(saved)
                    raise
                finally:
                    tx.close()

    async def bootstrap_bind_project(self, *, workspace_id: str, project_id: str) -> None:
        """Deployment-only binding; never call from a registration/header handler."""
        async with self.transaction(project_id) as tx:
            await tx.bootstrap_bind(workspace_id, project_id)


class _RegistryLifetime:
    """A retained registry handle never outlives its lock or moves to another task."""

    def __init__(self) -> None:
        self._owner = asyncio.current_task()
        self._active = True

    def require_active(self) -> None:
        if not self._active or self._owner is None or asyncio.current_task() is not self._owner:
            raise RoboticsError(Code.SIMULATION_UNAVAILABLE)

    def close(self) -> None:
        self._active = False


class _MemoryTransaction(_RegistryLifetime):
    def __init__(self, state: MemoryStore, project_id: str) -> None:
        super().__init__()
        self.state, self.project_id = state, project_id
        self.data = state.robotics_registry_state

    async def authorize(
        self,
        actor_id: str,
        workspace_id: str,
        project_id: str,
        *,
        write: bool = False,
        service: bool = False,
    ) -> Principal:
        self.require_active()
        if project_id != self.project_id or project_id not in self.state.projects:
            raise RoboticsError(Code.RESOURCE_NOT_FOUND)
        principal = self.state.principals.get(actor_id)
        membership = self.state.workspace_memberships.get((workspace_id, actor_id))
        if principal is not None and principal.principal_id != actor_id:
            raise RoboticsError(Code.INVALID_CONTRACT)
        if membership is not None and (
            membership.workspace_id != workspace_id or membership.principal_id != actor_id
        ):
            raise RoboticsError(Code.INVALID_CONTRACT)
        return deepcopy(
            _authorize(
                principal,
                membership,
                self.data["bindings"].get(project_id),
                workspace_id,
                write=write,
                service=service,
            )
        )

    async def bootstrap_bind(self, workspace_id: str, project_id: str) -> None:
        self.require_active()
        if (
            project_id != self.project_id
            or project_id not in self.state.projects
            or workspace_id not in self.state.workspaces
        ):
            raise RoboticsError(Code.RESOURCE_NOT_FOUND)
        existing = self.data["bindings"].get(project_id)
        if existing is not None and existing != workspace_id:
            raise RoboticsError(Code.CONTRACT_CONFLICT)
        self.data["bindings"][project_id] = workspace_id

    async def get(self, contract_id: str) -> RegistryRecord | None:
        self.require_active()
        return cast(RegistryRecord | None, self.data["records"].get(contract_id))

    async def lookup(
        self,
        workspace_id: str,
        project_id: str,
        contract_type: str,
        *,
        logical_name: str | None = None,
        version: str | None = None,
        digest: str | None = None,
    ) -> RegistryRecord | None:
        self.require_active()
        matches = [
            r
            for r in self.data["records"].values()
            if (r.workspace_id, r.project_id, r.contract_type)
            == (workspace_id, project_id, contract_type)
            and (logical_name is None or r.logical_name == logical_name)
            and (version is None or r.version == version)
            and (digest is None or r.content_hash == digest)
        ]
        if len(matches) > 1:
            raise RoboticsError(Code.INVALID_CONTRACT)
        return cast(RegistryRecord | None, matches[0] if matches else None)

    async def list(
        self, workspace_id: str, project_id: str, contract_type: str, after: str, limit: int
    ) -> builtins.list[RegistryRecord]:
        self.require_active()
        records: builtins.list[RegistryRecord] = sorted(
            (
                r
                for r in self.data["records"].values()
                if (r.workspace_id, r.project_id, r.contract_type)
                == (workspace_id, project_id, contract_type)
                and r.id > after
            ),
            key=lambda r: r.id,
        )
        return records[:limit]

    async def insert(self, record: RegistryRecord) -> None:
        self.require_active()
        if record.id in self.data["records"] or await self.lookup(
            record.workspace_id,
            record.project_id,
            record.contract_type,
            logical_name=record.logical_name,
            version=record.version,
        ):
            raise RoboticsError(Code.CONTRACT_CONFLICT)
        self.data["records"][record.id] = record

    async def set_revision(self, contract_id: str, revision: int) -> None:
        self.require_active()
        record = self.data["records"][contract_id]
        self.data["records"][contract_id] = RegistryRecord(
            **{**asdict(record), "revision": revision}
        )

    async def ledger(self, scope: IdempotencyScope) -> LedgerRecord | None:
        self.require_active()
        record = cast(LedgerRecord | None, self.data["ledger"].get(scope.identity))
        if record and record.scope != scope:
            raise RoboticsError(Code.INVALID_CONTRACT)
        return record

    async def remember(self, record: LedgerRecord) -> None:
        self.require_active()
        if record.scope.identity in self.data["ledger"]:
            raise RoboticsError(Code.IDEMPOTENCY_CONFLICT)
        self.data["ledger"][record.scope.identity] = record

    async def append_event(self, event: StoredEvent) -> None:
        self.require_active()
        key = (event.aggregate_id, event.sequence)
        if key in self.data["events"]:
            raise RoboticsError(Code.REVISION_CONFLICT)
        self.data["events"][key] = event

    async def events(self, aggregate_id: str, after: int, limit: int) -> builtins.list[StoredEvent]:
        self.require_active()
        events: builtins.list[StoredEvent] = sorted(
            (
                e
                for e in self.data["events"].values()
                if e.aggregate_id == aggregate_id and e.sequence > after
            ),
            key=lambda e: e.sequence,
        )
        return events[:limit]

    async def link_conformance(self, link: ConformanceLink) -> None:
        self.require_active()
        if link.report_id in self.data["conformance"]:
            raise RoboticsError(Code.CONTRACT_CONFLICT)
        self.data["conformance"][link.report_id] = link

    async def conformance(
        self, adapter_id: str, closure_hash: str | None = None, limit: int = 100
    ) -> builtins.list[ConformanceLink]:
        self.require_active()
        return sorted(
            (
                r
                for r in self.data["conformance"].values()
                if r.adapter_id == adapter_id
                and (closure_hash is None or r.closure_hash == closure_hash)
            ),
            key=lambda r: r.revision,
            reverse=True,
        )[:limit]


class PostgresRoboticsStore:
    def __init__(self, state: PostgresStore) -> None:
        self.state = state

    @asynccontextmanager
    async def transaction(self, project_id: str) -> AsyncIterator[RegistryTransaction]:
        lock = int.from_bytes(
            sha256(("robotics-registry:" + project_id).encode()).digest()[:8], "big", signed=True
        )
        with _unit_of_work(self.state):
            async with self.state.sessions.begin() as session:
                await session.execute(select(func.pg_advisory_xact_lock(lock)))
                tx = _PostgresTransaction(session, project_id)
                try:
                    yield tx
                finally:
                    tx.close()

    async def bootstrap_bind_project(self, *, workspace_id: str, project_id: str) -> None:
        """Deployment-only binding; no ordinary caller can claim a legacy project."""
        async with self.transaction(project_id) as tx:
            await tx.bootstrap_bind(workspace_id, project_id)


def _record(row: RoboticsContractRow) -> RegistryRecord:
    return RegistryRecord(**{key: getattr(row, key) for key in RegistryRecord.__dataclass_fields__})


class _PostgresTransaction(_RegistryLifetime):
    def __init__(self, session: AsyncSession, project_id: str) -> None:
        super().__init__()
        self.session, self.project_id = session, project_id

    async def authorize(
        self,
        actor_id: str,
        workspace_id: str,
        project_id: str,
        *,
        write: bool = False,
        service: bool = False,
    ) -> Principal:
        self.require_active()
        if project_id != self.project_id:
            raise RoboticsError(Code.RESOURCE_NOT_FOUND)
        principal_row = await self.session.scalar(
            select(PrincipalRow)
            .where(PrincipalRow.principal_id == actor_id)
            .with_for_update(read=True)
        )
        membership_row = await self.session.scalar(
            select(WorkspaceMembershipRow)
            .where(
                WorkspaceMembershipRow.workspace_id == workspace_id,
                WorkspaceMembershipRow.principal_id == actor_id,
            )
            .with_for_update(read=True)
        )
        binding = await self.session.get(SimulationProjectBindingRow, project_id)
        principal = Principal.model_validate(principal_row.definition) if principal_row else None
        membership = (
            WorkspaceMembership.model_validate(membership_row.definition)
            if membership_row
            else None
        )
        if (
            principal_row
            and principal
            and (
                principal.principal_id != principal_row.principal_id
                or principal.status.value != principal_row.status
            )
        ):
            raise RoboticsError(Code.INVALID_CONTRACT)
        if (
            membership_row
            and membership
            and (
                membership.principal_id != membership_row.principal_id
                or membership.workspace_id != membership_row.workspace_id
                or membership.role.value != membership_row.role
            )
        ):
            raise RoboticsError(Code.INVALID_CONTRACT)
        return _authorize(
            principal,
            membership,
            binding.workspace_id if binding else None,
            workspace_id,
            write=write,
            service=service,
        )

    async def bootstrap_bind(self, workspace_id: str, project_id: str) -> None:
        self.require_active()
        if project_id != self.project_id:
            raise RoboticsError(Code.RESOURCE_NOT_FOUND)
        project = await self.session.get(ProjectRow, project_id)
        workspace = await self.session.scalar(
            select(WorkspaceRow).where(WorkspaceRow.workspace_id == workspace_id)
        )
        if project is None or workspace is None:
            raise RoboticsError(Code.RESOURCE_NOT_FOUND)
        existing = await self.session.get(SimulationProjectBindingRow, project_id)
        if existing and existing.workspace_id != workspace_id:
            raise RoboticsError(Code.CONTRACT_CONFLICT)
        if existing is None:
            self.session.add(
                SimulationProjectBindingRow(
                    project_id=project_id, workspace_id=workspace_id, created_at=datetime.now(UTC)
                )
            )
            await self.session.flush()

    async def get(self, contract_id: str) -> RegistryRecord | None:
        self.require_active()
        row = await self.session.get(RoboticsContractRow, contract_id)
        return _record(row) if row else None

    async def lookup(
        self,
        workspace_id: str,
        project_id: str,
        contract_type: str,
        *,
        logical_name: str | None = None,
        version: str | None = None,
        digest: str | None = None,
    ) -> RegistryRecord | None:
        self.require_active()
        query = select(RoboticsContractRow).where(
            RoboticsContractRow.workspace_id == workspace_id,
            RoboticsContractRow.project_id == project_id,
            RoboticsContractRow.contract_type == contract_type,
        )
        if logical_name is not None:
            query = query.where(RoboticsContractRow.logical_name == logical_name)
        if version is not None:
            query = query.where(RoboticsContractRow.version == version)
        if digest is not None:
            query = query.where(RoboticsContractRow.content_hash == digest)
        rows = (await self.session.scalars(query.limit(2))).all()
        if len(rows) > 1:
            raise RoboticsError(Code.INVALID_CONTRACT)
        return _record(rows[0]) if rows else None

    async def list(
        self, workspace_id: str, project_id: str, contract_type: str, after: str, limit: int
    ) -> builtins.list[RegistryRecord]:
        self.require_active()
        rows = await self.session.scalars(
            select(RoboticsContractRow)
            .where(
                RoboticsContractRow.workspace_id == workspace_id,
                RoboticsContractRow.project_id == project_id,
                RoboticsContractRow.contract_type == contract_type,
                RoboticsContractRow.id > after,
            )
            .order_by(RoboticsContractRow.id)
            .limit(limit)
        )
        return [_record(row) for row in rows]

    async def insert(self, record: RegistryRecord) -> None:
        self.require_active()
        if await self.get(record.id) or await self.lookup(
            record.workspace_id,
            record.project_id,
            record.contract_type,
            logical_name=record.logical_name,
            version=record.version,
        ):
            raise RoboticsError(Code.CONTRACT_CONFLICT)
        self.session.add(RoboticsContractRow(**asdict(record)))
        await self.session.flush()

    async def set_revision(self, contract_id: str, revision: int) -> None:
        self.require_active()
        row = await self.session.get(RoboticsContractRow, contract_id)
        if row is None:
            raise RoboticsError(Code.RESOURCE_NOT_FOUND)
        row.revision = revision
        await self.session.flush()

    async def ledger(self, scope: IdempotencyScope) -> LedgerRecord | None:
        self.require_active()
        row = await self.session.get(RoboticsIdempotencyRow, scope.identity)
        if row is None:
            return None
        if any(getattr(row, key) != value for key, value in asdict(scope).items()):
            raise RoboticsError(Code.INVALID_CONTRACT)
        return LedgerRecord(scope, row.request_hash, row.response_json)

    async def remember(self, record: LedgerRecord) -> None:
        self.require_active()
        if await self.ledger(record.scope):
            raise RoboticsError(Code.IDEMPOTENCY_CONFLICT)
        self.session.add(
            RoboticsIdempotencyRow(
                id=record.scope.identity,
                **asdict(record.scope),
                request_hash=record.request_hash,
                response_json=record.response_json,
            )
        )
        await self.session.flush()

    async def append_event(self, event: StoredEvent) -> None:
        self.require_active()
        self.session.add(RoboticsEventRow(**asdict(event)))
        await self.session.flush()

    async def events(self, aggregate_id: str, after: int, limit: int) -> builtins.list[StoredEvent]:
        self.require_active()
        rows = await self.session.scalars(
            select(RoboticsEventRow)
            .where(
                RoboticsEventRow.aggregate_id == aggregate_id,
                RoboticsEventRow.sequence > after,
            )
            .order_by(RoboticsEventRow.sequence)
            .limit(limit)
        )
        return [
            StoredEvent(**{key: getattr(row, key) for key in StoredEvent.__dataclass_fields__})
            for row in rows
        ]

    async def link_conformance(self, link: ConformanceLink) -> None:
        self.require_active()
        self.session.add(RoboticsConformanceRow(**asdict(link)))
        await self.session.flush()

    async def conformance(
        self, adapter_id: str, closure_hash: str | None = None, limit: int = 100
    ) -> builtins.list[ConformanceLink]:
        self.require_active()
        query = select(RoboticsConformanceRow).where(
            RoboticsConformanceRow.adapter_id == adapter_id
        )
        if closure_hash is not None:
            query = query.where(RoboticsConformanceRow.closure_hash == closure_hash)
        rows = await self.session.scalars(
            query.order_by(RoboticsConformanceRow.revision.desc()).limit(limit)
        )
        return [
            ConformanceLink(
                **{key: getattr(row, key) for key in ConformanceLink.__dataclass_fields__}
            )
            for row in rows
        ]
