from __future__ import annotations

import asyncio
import inspect
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from accretion.contracts import (
    AgentEvent,
    ApprovalDecision,
    ApprovalRequest,
    ArtifactRef,
    AuthMode,
    EventType,
    Provider,
    RunRef,
    RuntimeExecutionRequest,
    RuntimeHealth,
    RuntimeStatus,
    SessionConfig,
    SessionRef,
    UsagePressure,
    UsageSnapshot,
)
from accretion.ids import new_id
from accretion.runtimes.common import (
    RuntimeSubmission,
    make_event,
    submission_call_id,
    submission_metadata,
    submission_task,
)

FakeCallHook = Callable[[SessionRef, RuntimeSubmission], Awaitable[None] | None]
_CALL_TERMINALS = {
    EventType.RUNTIME_CALL_COMPLETED,
    EventType.RUNTIME_CALL_FAILED,
    EventType.RUNTIME_CALL_CANCELLED,
}


@dataclass(slots=True)
class FakeCallOutcome:
    """One deterministic provider-call outcome for loop and recovery tests."""

    terminal: EventType = EventType.RUNTIME_CALL_COMPLETED
    progress_messages: tuple[str, ...] = ("fake runtime executing",)
    payload: dict[str, Any] = field(default_factory=dict)
    hook: FakeCallHook | None = None

    def __post_init__(self) -> None:
        if self.terminal not in _CALL_TERMINALS:
            raise ValueError("fake outcome terminal must be a RUNTIME_CALL_* terminal event")


class FakeRuntime:
    adapter_version = "fake-p2-v1"

    def __init__(
        self,
        *,
        step_delay: float = 0.0,
        fail: bool = False,
        scripted_outcomes: Sequence[FakeCallOutcome] | None = None,
    ) -> None:
        self.step_delay = step_delay
        fallback_terminal = (
            EventType.RUNTIME_CALL_FAILED if fail else EventType.RUNTIME_CALL_COMPLETED
        )
        self.fallback_outcome = FakeCallOutcome(terminal=fallback_terminal)
        self.scripted_outcomes = deque(scripted_outcomes or ())
        self.sessions: dict[str, SessionRef] = {}
        self.run_refs: dict[str, RunRef] = {}
        self.queues: dict[str, asyncio.Queue[AgentEvent | None]] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.call_sessions: dict[str, str] = {}
        self.session_active_calls: dict[str, str] = {}
        self.terminal_calls: set[str] = set()

    async def health(self) -> RuntimeHealth:
        return RuntimeHealth(
            runtime_id="runtime_fake",
            provider=Provider.FAKE,
            status=RuntimeStatus.READY,
            auth_mode=AuthMode.LOCAL,
            runtime_version=self.adapter_version,
            capabilities=[
                "structured-events",
                "repeatable-calls",
                "interrupt",
                "resume",
                "artifacts",
            ],
            active_sessions=len(self.sessions),
            active_runs=sum(not task.done() for task in self.tasks.values()),
            observed_usage_pressure=UsagePressure.LOW,
        )

    async def create_session(self, config: SessionConfig) -> SessionRef:
        session = SessionRef(
            session_id=new_id("session"),
            run_id=config.run_id,
            provider=Provider.FAKE,
            native_session_id=config.resume_native_session_id or f"fake-{config.run_id}",
            workspace=config.workspace,
        )
        self.sessions[session.session_id] = session
        return session

    async def submit(self, session: SessionRef, request: RuntimeSubmission) -> RunRef:
        session = self._canonical_session(session)
        if isinstance(request, RuntimeExecutionRequest) and request.run_id != session.run_id:
            raise ValueError("runtime request run_id does not match the session")
        active_call = self.session_active_calls.get(session.session_id)
        if active_call and active_call not in self.terminal_calls:
            raise RuntimeError("the fake session already has an active provider call")

        call_id = submission_call_id(request)
        if call_id in self.queues:
            raise ValueError(f"runtime call already exists: {call_id}")
        run = RunRef(
            run_id=session.run_id,
            session_id=session.session_id,
            native_run_id=session.native_session_id,
            runtime_call_id=call_id,
        )
        self.run_refs[call_id] = run
        self.queues[call_id] = asyncio.Queue()
        self.call_sessions[call_id] = session.session_id
        self.session_active_calls[session.session_id] = call_id
        outcome = (
            self.scripted_outcomes.popleft() if self.scripted_outcomes else self.fallback_outcome
        )
        self.tasks[call_id] = asyncio.create_task(
            self._execute(call_id, run, session, request, outcome)
        )
        return run

    async def _execute(
        self,
        call_id: str,
        run: RunRef,
        session: SessionRef,
        request: RuntimeSubmission,
        outcome: FakeCallOutcome,
    ) -> None:
        task = submission_task(request)
        metadata = submission_metadata(request)
        try:
            await self._put_event(
                call_id,
                run,
                session,
                native_type="fake/call-started",
                normalized_type=EventType.RUNTIME_CALL_STARTED,
                payload={"objective": task.objective, **metadata},
            )
            if outcome.hook is not None:
                hook_result = outcome.hook(session, request)
                if inspect.isawaitable(hook_result):
                    await hook_result
            for message in outcome.progress_messages:
                await self._put_event(
                    call_id,
                    run,
                    session,
                    native_type="fake/progress",
                    normalized_type=EventType.RUN_PROGRESS,
                    payload={"message": message, **metadata},
                )
            status = {
                EventType.RUNTIME_CALL_COMPLETED: "completed",
                EventType.RUNTIME_CALL_FAILED: "failed",
                EventType.RUNTIME_CALL_CANCELLED: "cancelled",
            }[outcome.terminal]
            await self._finish_call(
                call_id,
                run,
                session,
                native_type="fake/call-terminal",
                normalized_type=outcome.terminal,
                payload={"status": status, **outcome.payload, **metadata},
            )
        except asyncio.CancelledError:
            await self._finish_call(
                call_id,
                run,
                session,
                native_type="fake/call-cancelled",
                normalized_type=EventType.RUNTIME_CALL_CANCELLED,
                payload=metadata,
            )
            raise
        except Exception as exc:
            await self._finish_call(
                call_id,
                run,
                session,
                native_type="fake/call-failed",
                normalized_type=EventType.RUNTIME_CALL_FAILED,
                payload={"error": str(exc), **metadata},
            )
        finally:
            if self.session_active_calls.get(session.session_id) == call_id:
                self.session_active_calls.pop(session.session_id, None)

    async def _put_event(
        self,
        call_id: str,
        run: RunRef,
        session: SessionRef,
        *,
        native_type: str,
        normalized_type: EventType,
        payload: dict[str, Any],
    ) -> None:
        if self.step_delay:
            await asyncio.sleep(self.step_delay)
        await self.queues[call_id].put(
            make_event(
                run_id=run.run_id,
                session_id=session.session_id,
                provider=Provider.FAKE,
                native_type=native_type,
                normalized_type=normalized_type,
                payload=payload,
                adapter_version=self.adapter_version,
                correlation_id=call_id,
            )
        )

    async def _finish_call(
        self,
        call_id: str,
        run: RunRef,
        session: SessionRef,
        *,
        native_type: str,
        normalized_type: EventType,
        payload: dict[str, Any],
    ) -> None:
        if call_id in self.terminal_calls:
            return
        self.terminal_calls.add(call_id)
        if self.session_active_calls.get(session.session_id) == call_id:
            self.session_active_calls.pop(session.session_id, None)
        await self._put_event(
            call_id,
            run,
            session,
            native_type=native_type,
            normalized_type=normalized_type,
            payload=payload,
        )
        await self.queues[call_id].put(None)

    async def events(self, run: RunRef) -> AsyncIterator[AgentEvent]:
        queue = self.queues[self._call_id(run)]
        while (event := await queue.get()) is not None:
            yield event

    async def approve(self, request: ApprovalRequest, decision: ApprovalDecision) -> None:
        return None

    async def interrupt(self, run: RunRef) -> None:
        call_id = self._call_id(run)
        task = self.tasks.get(call_id)
        if task and not task.done():
            task.cancel()
            session = self.sessions[run.session_id]
            await self._finish_call(
                call_id,
                self.run_refs[call_id],
                session,
                native_type="fake/call-cancelled",
                normalized_type=EventType.RUNTIME_CALL_CANCELLED,
                payload={"runtime_call_id": call_id},
            )

    async def resume(self, run: RunRef) -> None:
        return None

    async def artifacts(self, run: RunRef) -> list[ArtifactRef]:
        return []

    async def usage(self, run: RunRef) -> UsageSnapshot:
        return UsageSnapshot(observed_usage_pressure=UsagePressure.LOW)

    async def terminate(self, run: RunRef) -> None:
        await self.interrupt(run)

    def _canonical_session(self, session: SessionRef) -> SessionRef:
        current = self.sessions.get(session.session_id)
        if current is None:
            self.sessions[session.session_id] = session
            return session
        native_session_id = session.native_session_id or current.native_session_id
        canonical = session.model_copy(update={"native_session_id": native_session_id})
        self.sessions[session.session_id] = canonical
        return canonical

    @staticmethod
    def _call_id(run: RunRef) -> str:
        return run.runtime_call_id or run.run_id
