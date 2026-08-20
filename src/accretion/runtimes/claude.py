from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from accretion.contracts import (
    AgentEvent,
    ApprovalDecision,
    ApprovalRequest,
    ArtifactRef,
    AuthMode,
    ErrorSummary,
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
from accretion.redaction import redact, redact_text
from accretion.runtimes.common import classify_runtime_health, command_result, make_event


class ClaudeRuntime:
    adapter_version = "claude-stream-json-p0-v1"

    def __init__(self, command: str = "claude") -> None:
        self.command = command
        self.sessions: dict[str, SessionRef] = {}
        self.queues: dict[str, asyncio.Queue[AgentEvent | None]] = {}
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.resuming_sessions: set[str] = set()

    async def health(self) -> RuntimeHealth:
        version_code, version_output = await command_result([self.command, "--version"])
        auth_code, auth_output = await command_result([self.command, "auth", "status"])
        status, pressure, error = classify_runtime_health(
            version_code=version_code,
            version_output=version_output,
            auth_code=auth_code,
            auth_output=auth_output,
            minimum=(2, 1, 231),
            maximum=(2, 2, 0),
        )
        return self._health(status, error, version_output, pressure)

    def _health(
        self,
        status: RuntimeStatus,
        error: str | None,
        version: str = "unknown",
        pressure: UsagePressure = UsagePressure.UNKNOWN,
    ) -> RuntimeHealth:
        return RuntimeHealth(
            runtime_id="runtime_claude",
            provider=Provider.CLAUDE,
            status=status,
            auth_mode=AuthMode.SUBSCRIPTION,
            runtime_version=version,
            capabilities=["stream-json", "session-resume", "tool-policy", "interrupt"],
            active_sessions=len(self.sessions),
            active_runs=sum(process.returncode is None for process in self.processes.values()),
            observed_usage_pressure=pressure,
            last_error=(
                ErrorSummary(code="CLAUDE_UNAVAILABLE", message=redact_text(error))
                if error
                else None
            ),
        )

    async def create_session(self, config: SessionConfig) -> SessionRef:
        session = SessionRef(
            session_id=new_id("session"),
            run_id=config.run_id,
            provider=Provider.CLAUDE,
            native_session_id=config.resume_native_session_id or str(uuid.uuid4()),
            workspace=config.workspace,
        )
        if config.resume_native_session_id:
            self.resuming_sessions.add(session.session_id)
        self.sessions[session.session_id] = session
        return session

    async def submit(self, session: SessionRef, task: TaskEnvelope) -> RunRef:
        run_id = session.run_id
        run = RunRef(
            run_id=run_id,
            session_id=session.session_id,
            native_run_id=session.native_session_id,
        )
        self.queues[run_id] = asyncio.Queue()
        self.tasks[run_id] = asyncio.create_task(self._execute(run, session, task))
        return run

    async def _execute(self, run: RunRef, session: SessionRef, task: TaskEnvelope) -> None:
        command = [
            self.command,
            "-p",
            self._prompt(task),
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "dontAsk",
        ]
        if session.session_id in self.resuming_sessions and session.native_session_id:
            command.extend(["--resume", session.native_session_id])
        elif session.native_session_id:
            command.extend(["--session-id", session.native_session_id])
        process: asyncio.subprocess.Process | None = None
        terminal_seen = False
        try:
            async with asyncio.timeout(task.budgets.wall_time_seconds):
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=session.workspace,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                self.processes[run.run_id] = process
                assert process.stdout
                while line := await process.stdout.readline():
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(message, dict):
                        continue
                    native_type = str(message.get("type", "unknown"))
                    normalized = self._normalize(message)
                    if terminal_seen and normalized in {
                        EventType.RUN_COMPLETED,
                        EventType.RUN_FAILED,
                        EventType.RUN_CANCELLED,
                    }:
                        continue
                    terminal_seen = terminal_seen or normalized in {
                        EventType.RUN_COMPLETED,
                        EventType.RUN_FAILED,
                        EventType.RUN_CANCELLED,
                    }
                    await self.queues[run.run_id].put(
                        make_event(
                            run_id=run.run_id,
                            session_id=session.session_id,
                            provider=Provider.CLAUDE,
                            native_type=native_type,
                            normalized_type=normalized,
                            payload={"provider_extension": redact(message)},
                            adapter_version=self.adapter_version,
                        )
                    )
                return_code = await process.wait()
                stderr = ""
                if process.stderr:
                    stderr = redact_text((await process.stderr.read()).decode(errors="replace"))
                if not terminal_seen:
                    detail = f"Claude process exited with code {return_code}"
                    if return_code == 0:
                        detail = "Claude process reached EOF without a terminal result"
                    await self._terminal_failure(run, session, detail, stderr, return_code)
                    terminal_seen = True
        except asyncio.CancelledError:
            if process and process.returncode is None:
                process.terminate()
                await process.wait()
            if not terminal_seen:
                await self.queues[run.run_id].put(
                    make_event(
                        run_id=run.run_id,
                        session_id=session.session_id,
                        provider=Provider.CLAUDE,
                        native_type="process/cancelled",
                        normalized_type=EventType.RUN_CANCELLED,
                        adapter_version=self.adapter_version,
                    )
                )
            raise
        except TimeoutError:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            if not terminal_seen:
                await self._terminal_failure(
                    run,
                    session,
                    f"Claude process timed out after {task.budgets.wall_time_seconds} seconds",
                )
        except Exception as exc:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            if not terminal_seen:
                await self._terminal_failure(run, session, str(exc))
        finally:
            await self.queues[run.run_id].put(None)

    async def _terminal_failure(
        self,
        run: RunRef,
        session: SessionRef,
        message: str,
        stderr: str = "",
        return_code: int | None = None,
    ) -> None:
        await self.queues[run.run_id].put(
            make_event(
                run_id=run.run_id,
                session_id=session.session_id,
                provider=Provider.CLAUDE,
                native_type="process/exit",
                normalized_type=EventType.RUN_FAILED,
                payload={
                    "error": redact_text(message),
                    "return_code": return_code,
                    "stderr": stderr[-2000:],
                },
                adapter_version=self.adapter_version,
            )
        )

    @staticmethod
    def _prompt(task: TaskEnvelope) -> str:
        return json.dumps(
            {
                "objective": task.objective,
                "constraints": task.constraints,
                "success_criteria": task.success_criteria,
                "risk_level": task.risk_level.value,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _normalize(message: dict[str, object]) -> EventType:
        kind = str(message.get("type", ""))
        subtype = str(message.get("subtype", ""))
        if kind == "system" and subtype == "init":
            return EventType.RUN_STARTED
        if kind == "result":
            return EventType.RUN_FAILED if message.get("is_error") else EventType.RUN_COMPLETED
        if kind == "tool_use":
            return EventType.TOOL_REQUESTED
        return EventType.RUN_PROGRESS

    async def events(self, run: RunRef) -> AsyncIterator[AgentEvent]:
        queue = self.queues[run.run_id]
        while (event := await queue.get()) is not None:
            yield event

    async def approve(self, request: ApprovalRequest, decision: ApprovalDecision) -> None:
        raise NotImplementedError("Claude P0 uses a precomputed non-interactive tool policy")

    async def interrupt(self, run: RunRef) -> None:
        process = self.processes.get(run.run_id)
        if process and process.returncode is None:
            process.terminate()

    async def resume(self, run: RunRef) -> None:
        if not run.native_run_id:
            raise RuntimeError("Claude run has no resumable native session")

    async def artifacts(self, run: RunRef) -> list[ArtifactRef]:
        return []

    async def usage(self, run: RunRef) -> UsageSnapshot:
        return UsageSnapshot()

    async def terminate(self, run: RunRef) -> None:
        await self.interrupt(run)
