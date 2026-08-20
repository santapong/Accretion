from __future__ import annotations

import asyncio
from pathlib import Path

from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    TERMINAL_RUN_STATES,
    AgentEvent,
    AgentRuntime,
    ErrorSummary,
    EventType,
    Project,
    Provider,
    Run,
    RunRef,
    RunState,
    SessionConfig,
    Task,
    TaskEnvelope,
)
from accretion.ids import new_id
from accretion.persistence.store import StateStore
from accretion.runtimes.common import make_event
from accretion.workspace import WorktreeManager


class RunManager:
    def __init__(
        self,
        *,
        store: StateStore,
        worktrees: WorktreeManager,
        runtimes: dict[Provider, AgentRuntime],
        limiter: ConcurrencyLimiter,
        live_providers_enabled: bool,
    ) -> None:
        self.store = store
        self.worktrees = worktrees
        self.runtimes = runtimes
        self.limiter = limiter
        self.live_providers_enabled = live_providers_enabled
        self.background: dict[str, asyncio.Task[None]] = {}
        self.active_refs: dict[str, RunRef] = {}
        self.event_conditions: dict[str, asyncio.Condition] = {}

    async def create_project(self, name: str, repository_path: Path) -> Project:
        repository_path = repository_path.resolve(strict=True)
        project = Project(
            project_id=new_id("project"),
            name=name,
            repository_path=repository_path,
        )
        return await self.store.create_project(project)

    async def create_task(
        self,
        *,
        project_id: str,
        objective: str,
        task_patch: dict[str, object],
    ) -> Task:
        if await self.store.get_project(project_id) is None:
            raise KeyError(project_id)
        envelope = TaskEnvelope(
            task_id=new_id("task"),
            project_id=project_id,
            objective=objective,
            **task_patch,
        )
        return await self.store.create_task(Task(envelope=envelope))

    async def start_run(self, task_id: str, provider: Provider) -> Run:
        task = await self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if provider not in self.runtimes:
            raise ValueError(f"runtime {provider.value} is not configured")
        if provider in {Provider.CODEX, Provider.CLAUDE} and not self.live_providers_enabled:
            raise PermissionError(
                "live providers are disabled; set ACCRETION_ENABLE_LIVE_PROVIDERS=true"
            )
        run = Run(
            run_id=new_id("run"),
            task_id=task_id,
            project_id=task.envelope.project_id,
            provider=provider,
            state=RunState.PENDING,
        )
        await self.store.create_run(run)
        await self._append(
            make_event(
                run_id=run.run_id,
                session_id="ses_pending",
                provider=Provider.DETERMINISTIC,
                native_type="accretion/run-created",
                normalized_type=EventType.RUN_CREATED,
                adapter_version="control-plane-p0-v1",
            )
        )
        self.background[run.run_id] = asyncio.create_task(self._execute(run, task))
        return (await self.store.get_run(run.run_id)) or run

    async def _execute(self, run: Run, task: Task) -> None:
        runtime = self.runtimes[run.provider]
        lease = None
        session_id = "ses_pending"
        try:
            async with self.limiter.slot(run.provider, run.project_id):
                await self.store.update_run(run.run_id, RunState.STARTING)
                project = await self.store.get_project(run.project_id)
                if project is None:
                    raise RuntimeError("project disappeared before run start")
                lease = await self.worktrees.acquire(
                    project_id=run.project_id,
                    run_id=run.run_id,
                    repository=project.repository_path,
                )
                await self.store.save_lease(lease)
                session = await runtime.create_session(
                    SessionConfig(run_id=run.run_id, workspace=lease.path)
                )
                session_id = session.session_id
                await self.store.save_session(session)
                await self.store.update_run(
                    run.run_id,
                    RunState.RUNNING,
                    session_id=session.session_id,
                    workspace_lease_id=lease.lease_id,
                )
                native_run = await runtime.submit(session, task.envelope)
                self.active_refs[run.run_id] = native_run
                terminal_state: RunState | None = None
                async for event in runtime.events(native_run):
                    stored = await self._append(event)
                    if stored.normalized_type == EventType.APPROVAL_REQUIRED:
                        await self.store.update_run(run.run_id, RunState.PAUSED)
                    elif stored.normalized_type == EventType.RUN_COMPLETED:
                        terminal_state = RunState.SUCCEEDED
                    elif stored.normalized_type == EventType.RUN_FAILED:
                        terminal_state = RunState.FAILED
                    elif stored.normalized_type == EventType.RUN_CANCELLED:
                        terminal_state = RunState.CANCELLED
                if terminal_state is None:
                    terminal_state = RunState.REQUIRES_HUMAN
                await self.store.update_run(run.run_id, terminal_state)
                artifact = await self.worktrees.capture_diff(lease)
                if artifact:
                    await self.store.save_artifact(artifact)
                await self.worktrees.release(lease, successful=terminal_state == RunState.SUCCEEDED)
        except asyncio.CancelledError:
            await self.store.update_run(run.run_id, RunState.CANCELLED)
            raise
        except Exception as exc:
            error = ErrorSummary(code="RUN_EXECUTION_FAILED", message=str(exc)[:2000])
            await self.store.update_run(run.run_id, RunState.FAILED, error=error)
            await self._append(
                make_event(
                    run_id=run.run_id,
                    session_id=session_id,
                    provider=Provider.DETERMINISTIC,
                    native_type="accretion/run-error",
                    normalized_type=EventType.RUN_FAILED,
                    payload={"error": error.model_dump(mode="json")},
                    adapter_version="control-plane-p0-v1",
                )
            )
        finally:
            self.active_refs.pop(run.run_id, None)

    async def _append(self, event: AgentEvent) -> AgentEvent:
        stored = await self.store.append_event(event)
        condition = self.event_conditions.setdefault(event.run_id, asyncio.Condition())
        async with condition:
            condition.notify_all()
        return stored

    async def wait_for_events(
        self, run_id: str, after: int, timeout_seconds: float = 15.0
    ) -> None:
        condition = self.event_conditions.setdefault(run_id, asyncio.Condition())
        run = await self.store.get_run(run_id)
        if run is None or run.last_sequence > after or run.state in TERMINAL_RUN_STATES:
            return
        async with condition:
            try:
                await asyncio.wait_for(condition.wait(), timeout_seconds)
            except TimeoutError:
                return

    async def pause(self, run_id: str) -> Run:
        run = await self._require_run(run_id)
        ref = self.active_refs.get(run_id)
        if ref:
            await self.runtimes[run.provider].interrupt(ref)
        return await self.store.update_run(run_id, RunState.PAUSED)

    async def resume(self, run_id: str) -> Run:
        run = await self._require_run(run_id)
        ref = self.active_refs.get(run_id)
        if not ref:
            return await self.store.update_run(run_id, RunState.REQUIRES_HUMAN)
        await self.runtimes[run.provider].resume(ref)
        return await self.store.update_run(run_id, RunState.RUNNING)

    async def cancel(self, run_id: str) -> Run:
        run = await self._require_run(run_id)
        ref = self.active_refs.get(run_id)
        if ref:
            await self.runtimes[run.provider].terminate(ref)
        task = self.background.get(run_id)
        if task and not task.done():
            task.cancel()
        return await self.store.update_run(run_id, RunState.CANCELLED)

    async def reconcile(self) -> None:
        for run in await self.store.list_runs(limit=10_000):
            if run.state in TERMINAL_RUN_STATES or run.state == RunState.PENDING:
                continue
            await self.store.update_run(run.run_id, RunState.RECONCILING)
            if not run.workspace_lease_id:
                await self.store.update_run(run.run_id, RunState.REQUIRES_HUMAN)
                continue
            lease = await self.store.get_lease(run.workspace_lease_id)
            if lease is None or await self.worktrees.inspect(lease) != "CONSISTENT":
                await self.store.update_run(run.run_id, RunState.REQUIRES_HUMAN)
            else:
                await self.store.update_run(run.run_id, RunState.PAUSED)

    async def _require_run(self, run_id: str) -> Run:
        run = await self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run
