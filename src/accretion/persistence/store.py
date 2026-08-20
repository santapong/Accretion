from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from accretion.contracts import (
    AgentEvent,
    ArtifactRef,
    ErrorSummary,
    Project,
    Provider,
    Run,
    RunState,
    SessionRef,
    Task,
    TaskEnvelope,
    WorkspaceLease,
)
from accretion.persistence.models import (
    AgentEventRow,
    ProjectRow,
    RunRow,
    TaskRow,
    WorkspaceLeaseRow,
)


class StateStore(Protocol):
    async def create_project(self, project: Project) -> Project: ...
    async def get_project(self, project_id: str) -> Project | None: ...
    async def create_task(self, task: Task) -> Task: ...
    async def get_task(self, task_id: str) -> Task | None: ...
    async def create_run(self, run: Run) -> Run: ...
    async def get_run(self, run_id: str) -> Run | None: ...
    async def list_runs(self, limit: int = 100) -> list[Run]: ...
    async def update_run(
        self,
        run_id: str,
        state: RunState,
        *,
        session_id: str | None = None,
        workspace_lease_id: str | None = None,
        error: ErrorSummary | None = None,
    ) -> Run: ...
    async def save_lease(self, lease: WorkspaceLease) -> None: ...
    async def get_lease(self, lease_id: str) -> WorkspaceLease | None: ...
    async def save_session(self, session: SessionRef) -> None: ...
    async def save_artifact(self, artifact: ArtifactRef) -> None: ...
    async def list_artifacts(self, run_id: str) -> list[ArtifactRef]: ...
    async def append_event(self, event: AgentEvent) -> AgentEvent: ...
    async def list_events(self, run_id: str, after: int = 0) -> list[AgentEvent]: ...


class MemoryStore:
    """Deterministic store for unit tests and protocol development, never production."""

    def __init__(self) -> None:
        self.projects: dict[str, Project] = {}
        self.tasks: dict[str, Task] = {}
        self.runs: dict[str, Run] = {}
        self.leases: dict[str, WorkspaceLease] = {}
        self.sessions: dict[str, SessionRef] = {}
        self.artifacts: dict[str, list[ArtifactRef]] = {}
        self.run_events: dict[str, list[AgentEvent]] = {}
        self._lock = asyncio.Lock()

    async def create_project(self, project: Project) -> Project:
        self.projects[project.project_id] = project
        return project

    async def get_project(self, project_id: str) -> Project | None:
        return self.projects.get(project_id)

    async def create_task(self, task: Task) -> Task:
        self.tasks[task.envelope.task_id] = task
        return task

    async def get_task(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    async def create_run(self, run: Run) -> Run:
        self.runs[run.run_id] = run
        return run

    async def get_run(self, run_id: str) -> Run | None:
        return self.runs.get(run_id)

    async def list_runs(self, limit: int = 100) -> list[Run]:
        return sorted(self.runs.values(), key=lambda run: run.created_at, reverse=True)[:limit]

    async def update_run(
        self,
        run_id: str,
        state: RunState,
        *,
        session_id: str | None = None,
        workspace_lease_id: str | None = None,
        error: ErrorSummary | None = None,
    ) -> Run:
        current = self.runs[run_id]
        updated = current.model_copy(
            update={
                "state": state,
                "session_id": session_id if session_id is not None else current.session_id,
                "workspace_lease_id": (
                    workspace_lease_id
                    if workspace_lease_id is not None
                    else current.workspace_lease_id
                ),
                "error": error,
                "revision": current.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self.runs[run_id] = updated
        return updated

    async def save_lease(self, lease: WorkspaceLease) -> None:
        self.leases[lease.lease_id] = lease

    async def get_lease(self, lease_id: str) -> WorkspaceLease | None:
        return self.leases.get(lease_id)

    async def save_session(self, session: SessionRef) -> None:
        self.sessions[session.session_id] = session

    async def save_artifact(self, artifact: ArtifactRef) -> None:
        self.artifacts.setdefault(artifact.run_id, []).append(artifact)

    async def list_artifacts(self, run_id: str) -> list[ArtifactRef]:
        return self.artifacts.get(run_id, [])

    async def append_event(self, event: AgentEvent) -> AgentEvent:
        async with self._lock:
            events = self.run_events.setdefault(event.run_id, [])
            stored = event.model_copy(update={"sequence": len(events) + 1})
            events.append(stored)
            run = self.runs.get(event.run_id)
            if run is not None:
                self.runs[event.run_id] = run.model_copy(update={"last_sequence": stored.sequence})
            return stored

    async def list_events(self, run_id: str, after: int = 0) -> list[AgentEvent]:
        return [event for event in self.run_events.get(run_id, []) if event.sequence > after]


class PostgresStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def create_project(self, project: Project) -> Project:
        async with self.sessions.begin() as session:
            session.add(
                ProjectRow(
                    id=project.project_id,
                    name=project.name,
                    repository_path=str(project.repository_path),
                    created_at=project.created_at,
                )
            )
        return project

    async def get_project(self, project_id: str) -> Project | None:
        async with self.sessions() as session:
            row = await session.get(ProjectRow, project_id)
        if row is None:
            return None
        return Project(
            project_id=row.id,
            name=row.name,
            repository_path=row.repository_path,
            created_at=row.created_at,
        )

    async def create_task(self, task: Task) -> Task:
        async with self.sessions.begin() as session:
            session.add(
                TaskRow(
                    id=task.envelope.task_id,
                    project_id=task.envelope.project_id,
                    envelope=task.envelope.model_dump(mode="json"),
                    created_at=task.created_at,
                )
            )
        return task

    async def get_task(self, task_id: str) -> Task | None:
        async with self.sessions() as session:
            row = await session.get(TaskRow, task_id)
        if row is None:
            return None
        return Task(envelope=TaskEnvelope.model_validate(row.envelope), created_at=row.created_at)

    async def create_run(self, run: Run) -> Run:
        async with self.sessions.begin() as session:
            session.add(self._run_to_row(run))
        return run

    async def get_run(self, run_id: str) -> Run | None:
        async with self.sessions() as session:
            row = await session.get(RunRow, run_id)
        return self._row_to_run(row) if row else None

    async def list_runs(self, limit: int = 100) -> list[Run]:
        async with self.sessions() as session:
            rows: Sequence[RunRow] = (
                await session.scalars(
                    select(RunRow).order_by(RunRow.created_at.desc()).limit(limit)
                )
            ).all()
        return [self._row_to_run(row) for row in rows]

    async def update_run(
        self,
        run_id: str,
        state: RunState,
        *,
        session_id: str | None = None,
        workspace_lease_id: str | None = None,
        error: ErrorSummary | None = None,
    ) -> Run:
        async with self.sessions.begin() as session:
            row = await session.scalar(select(RunRow).where(RunRow.id == run_id).with_for_update())
            if row is None:
                raise KeyError(run_id)
            row.state = state.value
            row.session_id = session_id if session_id is not None else row.session_id
            row.workspace_lease_id = (
                workspace_lease_id if workspace_lease_id is not None else row.workspace_lease_id
            )
            row.error = error.model_dump(mode="json") if error else None
            row.revision += 1
            row.updated_at = datetime.now(UTC)
            await session.flush()
            result = self._row_to_run(row)
        return result

    async def save_lease(self, lease: WorkspaceLease) -> None:
        async with self.sessions.begin() as session:
            session.add(
                WorkspaceLeaseRow(
                    id=lease.lease_id,
                    project_id=lease.project_id,
                    run_id=lease.run_id,
                    base_revision=lease.base_revision,
                    path=str(lease.path),
                    branch_name=lease.branch_name,
                    cleanup_policy=lease.cleanup_policy,
                    acquired_at=lease.acquired_at,
                    expires_at=lease.expires_at,
                )
            )

    async def get_lease(self, lease_id: str) -> WorkspaceLease | None:
        async with self.sessions() as session:
            row = await session.get(WorkspaceLeaseRow, lease_id)
        if row is None:
            return None
        return WorkspaceLease(
            lease_id=row.id,
            project_id=row.project_id,
            run_id=row.run_id,
            base_revision=row.base_revision,
            path=row.path,
            branch_name=row.branch_name,
            cleanup_policy=row.cleanup_policy,
            acquired_at=row.acquired_at,
            expires_at=row.expires_at,
        )

    async def save_session(self, runtime_session: SessionRef) -> None:
        from accretion.persistence.models import RuntimeSessionRow

        async with self.sessions.begin() as session:
            session.add(
                RuntimeSessionRow(
                    id=runtime_session.session_id,
                    run_id=runtime_session.run_id,
                    provider=runtime_session.provider.value,
                    native_session_id=runtime_session.native_session_id,
                    workspace_path=str(runtime_session.workspace),
                    created_at=datetime.now(UTC),
                )
            )

    async def save_artifact(self, artifact: ArtifactRef) -> None:
        from accretion.persistence.models import ArtifactRow

        async with self.sessions.begin() as session:
            session.add(
                ArtifactRow(
                    id=artifact.artifact_id,
                    run_id=artifact.run_id,
                    kind=artifact.kind,
                    path=str(artifact.path),
                    sha256=artifact.sha256,
                    created_at=datetime.now(UTC),
                )
            )

    async def list_artifacts(self, run_id: str) -> list[ArtifactRef]:
        from accretion.persistence.models import ArtifactRow

        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(ArtifactRow)
                    .where(ArtifactRow.run_id == run_id)
                    .order_by(ArtifactRow.created_at)
                )
            ).all()
        return [
            ArtifactRef(
                artifact_id=row.id,
                run_id=row.run_id,
                kind=row.kind,
                path=row.path,
                sha256=row.sha256,
            )
            for row in rows
        ]

    async def append_event(self, event: AgentEvent) -> AgentEvent:
        async with self.sessions.begin() as session:
            run = await session.scalar(
                select(RunRow).where(RunRow.id == event.run_id).with_for_update()
            )
            if run is None:
                raise KeyError(event.run_id)
            run.last_sequence += 1
            sequence = run.last_sequence
            stored = event.model_copy(update={"sequence": sequence})
            session.add(
                AgentEventRow(
                    id=stored.event_id,
                    run_id=stored.run_id,
                    session_id=stored.session_id,
                    provider=stored.provider.value,
                    native_type=stored.native_type,
                    normalized_type=stored.normalized_type.value,
                    sequence=sequence,
                    occurred_at=stored.timestamp,
                    correlation_id=stored.correlation_id,
                    causation_id=stored.causation_id,
                    node_id=stored.node_id,
                    payload=stored.payload,
                    adapter_version=stored.adapter_version,
                )
            )
        return stored

    async def list_events(self, run_id: str, after: int = 0) -> list[AgentEvent]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(AgentEventRow)
                    .where(AgentEventRow.run_id == run_id, AgentEventRow.sequence > after)
                    .order_by(AgentEventRow.sequence)
                )
            ).all()
        return [self._row_to_event(row) for row in rows]

    @staticmethod
    def _run_to_row(run: Run) -> RunRow:
        return RunRow(
            id=run.run_id,
            task_id=run.task_id,
            project_id=run.project_id,
            provider=run.provider.value,
            state=run.state.value,
            last_sequence=run.last_sequence,
            revision=run.revision,
            session_id=run.session_id,
            workspace_lease_id=run.workspace_lease_id,
            error=run.error.model_dump(mode="json") if run.error else None,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    @staticmethod
    def _row_to_run(row: RunRow) -> Run:
        return Run(
            run_id=row.id,
            task_id=row.task_id,
            project_id=row.project_id,
            provider=Provider(row.provider),
            state=RunState(row.state),
            last_sequence=row.last_sequence,
            revision=row.revision,
            session_id=row.session_id,
            workspace_lease_id=row.workspace_lease_id,
            error=ErrorSummary.model_validate(row.error) if row.error else None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _row_to_event(row: AgentEventRow) -> AgentEvent:
        return AgentEvent(
            event_id=row.id,
            run_id=row.run_id,
            session_id=row.session_id,
            provider=Provider(row.provider),
            native_type=row.native_type,
            normalized_type=row.normalized_type,
            sequence=row.sequence,
            timestamp=row.occurred_at,
            correlation_id=row.correlation_id,
            causation_id=row.causation_id,
            node_id=row.node_id,
            payload=row.payload,
            adapter_version=row.adapter_version,
        )
