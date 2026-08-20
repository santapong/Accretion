from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from accretion.contracts import (
    AgentEvent,
    ApprovalDecision,
    ApprovalRequest,
    ArtifactRef,
    AuthMode,
    EventType,
    Provider,
    RunRef,
    RuntimeHealth,
    RuntimeStatus,
    SessionConfig,
    SessionRef,
    TaskEnvelope,
    UsagePressure,
    UsageSnapshot,
)
from accretion.ids import new_id
from accretion.runtimes.common import make_event


class FakeRuntime:
    adapter_version = "fake-p0-v1"

    def __init__(self, *, step_delay: float = 0.0, fail: bool = False) -> None:
        self.step_delay = step_delay
        self.fail = fail
        self.queues: dict[str, asyncio.Queue[AgentEvent | None]] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}

    async def health(self) -> RuntimeHealth:
        return RuntimeHealth(
            runtime_id="runtime_fake",
            provider=Provider.FAKE,
            status=RuntimeStatus.READY,
            auth_mode=AuthMode.LOCAL,
            runtime_version=self.adapter_version,
            capabilities=["structured-events", "interrupt", "resume", "artifacts"],
            active_sessions=len(self.queues),
            active_runs=sum(not task.done() for task in self.tasks.values()),
            observed_usage_pressure=UsagePressure.LOW,
        )

    async def create_session(self, config: SessionConfig) -> SessionRef:
        return SessionRef(
            session_id=new_id("session"),
            run_id=config.run_id,
            provider=Provider.FAKE,
            native_session_id=f"fake-{config.run_id}",
            workspace=config.workspace,
        )

    async def submit(self, session: SessionRef, task: TaskEnvelope) -> RunRef:
        run = RunRef(run_id=session.run_id, session_id=session.session_id)
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        self.queues[run.run_id] = queue
        self.tasks[run.run_id] = asyncio.create_task(self._execute(run, session, task))
        return run

    async def _execute(self, run: RunRef, session: SessionRef, task: TaskEnvelope) -> None:
        queue = self.queues[run.run_id]
        event_types = [
            ("fake/start", EventType.RUN_STARTED, {"objective": task.objective}),
            ("fake/progress", EventType.RUN_PROGRESS, {"message": "fake runtime executing"}),
            (
                "fake/terminal",
                EventType.RUN_FAILED if self.fail else EventType.RUN_COMPLETED,
                {"status": "failed" if self.fail else "completed"},
            ),
        ]
        try:
            for native, normalized, payload in event_types:
                if self.step_delay:
                    await asyncio.sleep(self.step_delay)
                await queue.put(
                    make_event(
                        run_id=run.run_id,
                        session_id=session.session_id,
                        provider=Provider.FAKE,
                        native_type=native,
                        normalized_type=normalized,
                        payload=payload,
                        adapter_version=self.adapter_version,
                    )
                )
        except asyncio.CancelledError:
            await queue.put(
                make_event(
                    run_id=run.run_id,
                    session_id=session.session_id,
                    provider=Provider.FAKE,
                    native_type="fake/cancelled",
                    normalized_type=EventType.RUN_CANCELLED,
                    adapter_version=self.adapter_version,
                )
            )
        finally:
            await queue.put(None)

    async def events(self, run: RunRef) -> AsyncIterator[AgentEvent]:
        queue = self.queues[run.run_id]
        while (event := await queue.get()) is not None:
            yield event

    async def approve(self, request: ApprovalRequest, decision: ApprovalDecision) -> None:
        return None

    async def interrupt(self, run: RunRef) -> None:
        task = self.tasks.get(run.run_id)
        if task and not task.done():
            task.cancel()

    async def resume(self, run: RunRef) -> None:
        return None

    async def artifacts(self, run: RunRef) -> list[ArtifactRef]:
        return []

    async def usage(self, run: RunRef) -> UsageSnapshot:
        return UsageSnapshot(observed_usage_pressure=UsagePressure.LOW)

    async def terminate(self, run: RunRef) -> None:
        await self.interrupt(run)
