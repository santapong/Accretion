from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any

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
    RuntimeExecutionRequest,
    RuntimeHealth,
    RuntimeStatus,
    SessionConfig,
    SessionRef,
    UsagePressure,
    UsageSnapshot,
)
from accretion.ids import new_id
from accretion.redaction import redact, redact_text
from accretion.runtimes.common import (
    RuntimeSubmission,
    classify_runtime_health,
    command_result,
    make_event,
    provider_environment,
    submission_call_id,
    submission_metadata,
    submission_task,
    submission_timeout_seconds,
)

_CALL_TERMINALS = {
    EventType.RUNTIME_CALL_COMPLETED,
    EventType.RUNTIME_CALL_FAILED,
    EventType.RUNTIME_CALL_CANCELLED,
}


class ClaudeRuntime:
    adapter_version = "claude-stream-json-p2-v1"

    def __init__(
        self,
        command: str = "claude",
        gateway_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.command = command
        self.gateway_environment = dict(gateway_environment or {})
        self.sessions: dict[str, SessionRef] = {}
        self.run_refs: dict[str, RunRef] = {}
        self.queues: dict[str, asyncio.Queue[AgentEvent | None]] = {}
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.call_sessions: dict[str, str] = {}
        self.session_active_calls: dict[str, str] = {}
        self.started_sessions: set[str] = set()
        self.interrupted_calls: set[str] = set()
        self.terminal_calls: set[str] = set()
        self.session_configs: dict[str, SessionConfig] = {}

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
            capabilities=[
                "stream-json",
                "session-resume",
                "repeatable-calls",
                "tool-policy",
                "interrupt",
            ],
            active_sessions=len(self.sessions),
            active_runs=sum(not task.done() for task in self.tasks.values()),
            observed_usage_pressure=pressure,
            last_error=(
                ErrorSummary(
                    code=f"CLAUDE_{status.value}",
                    message=redact_text(error),
                    retryable=status is RuntimeStatus.RATE_LIMITED,
                )
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
            self.started_sessions.add(session.session_id)
        self.sessions[session.session_id] = session
        self.session_configs[session.session_id] = config
        return session

    async def submit(self, session: SessionRef, request: RuntimeSubmission) -> RunRef:
        session = self._canonical_session(session)
        if isinstance(request, RuntimeExecutionRequest) and request.run_id != session.run_id:
            raise ValueError("runtime request run_id does not match the session")
        active_call = self.session_active_calls.get(session.session_id)
        if active_call and active_call not in self.terminal_calls:
            raise RuntimeError("the Claude session already has an active provider call")

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
        self.tasks[call_id] = asyncio.create_task(self._execute(call_id, run, session, request))
        return run

    async def _execute(
        self,
        call_id: str,
        run: RunRef,
        session: SessionRef,
        request: RuntimeSubmission,
    ) -> None:
        command = [
            self.command,
            "-p",
            self._prompt(request),
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "dontAsk",
        ]
        config = self.session_configs[session.session_id]
        gateway_env = {
            **self.gateway_environment,
            "ACCRETION_GATEWAY_RUN_ID": session.run_id,
        }
        mcp_config = {
            "mcpServers": {
                "accretion": {
                    "command": sys.executable,
                    "args": ["-m", "accretion.mcp_gateway"],
                    "env": gateway_env,
                }
            }
        }
        native_tools = ["Read", "Edit", "Write", "Glob", "Grep", "Bash"]
        gateway_tools = [f"mcp__accretion__{name}" for name in config.allowed_tools]
        allowed_tools = [
            "Read",
            "Edit",
            "Write",
            "Glob",
            "Grep",
            "Bash(git status*)",
            "Bash(git diff*)",
            "Bash(pytest*)",
            "Bash(uv run*)",
            "Bash(npm test*)",
            "Bash(npm run*)",
            *gateway_tools,
        ]
        command.extend(
            [
                "--strict-mcp-config",
                "--mcp-config",
                json.dumps(mcp_config, separators=(",", ":")),
                "--tools",
                ",".join([*native_tools, *gateway_tools]),
                "--allowedTools",
                ",".join(allowed_tools),
                "--disable-slash-commands",
                "--no-chrome",
            ]
        )
        if session.session_id in self.started_sessions and session.native_session_id:
            command.extend(["--resume", session.native_session_id])
        elif session.native_session_id:
            command.extend(["--session-id", session.native_session_id])

        process: asyncio.subprocess.Process | None = None
        stderr_task: asyncio.Task[bytes] | None = None
        terminal_message: tuple[str, EventType, dict[str, Any]] | None = None
        timeout_seconds = submission_timeout_seconds(request)
        metadata = submission_metadata(request)
        try:
            async with asyncio.timeout(timeout_seconds):
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=session.workspace,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=provider_environment(),
                )
                self.processes[call_id] = process
                self.started_sessions.add(session.session_id)
                if process.stderr:
                    stderr_task = asyncio.create_task(process.stderr.read())
                if not process.stdout:
                    raise RuntimeError("Claude process did not expose stdout")
                while line := await process.stdout.readline():
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(message, dict):
                        continue
                    native_type = str(message.get("type", "unknown"))
                    normalized = self._normalize(message)
                    payload = {
                        "runtime_call_id": call_id,
                        "provider_extension": redact(message),
                        **metadata,
                    }
                    if normalized in _CALL_TERMINALS:
                        if terminal_message is None:
                            terminal_message = (native_type, normalized, payload)
                        continue
                    if terminal_message is None:
                        await self._put_event(
                            call_id,
                            run,
                            native_type=native_type,
                            normalized_type=normalized,
                            payload=payload,
                        )

                return_code = await process.wait()
                stderr = await self._stderr(stderr_task)
                if terminal_message is not None:
                    native_type, normalized, payload = terminal_message
                    await self._finish_call(
                        call_id,
                        run,
                        native_type=native_type,
                        normalized_type=normalized,
                        payload=payload,
                    )
                elif call_id in self.interrupted_calls:
                    await self._finish_call(
                        call_id,
                        run,
                        native_type="process/cancelled",
                        normalized_type=EventType.RUNTIME_CALL_CANCELLED,
                        payload={"runtime_call_id": call_id, **metadata},
                    )
                else:
                    detail = f"Claude process exited with code {return_code}"
                    if return_code == 0:
                        detail = "Claude process reached EOF without a terminal result"
                    await self._terminal_failure(
                        call_id,
                        run,
                        detail,
                        stderr,
                        return_code,
                        metadata,
                    )
        except asyncio.CancelledError:
            await self._stop_process(process, kill=False)
            if terminal_message is not None:
                await self._finish_terminal_message(call_id, run, terminal_message)
            else:
                await self._finish_call(
                    call_id,
                    run,
                    native_type="process/cancelled",
                    normalized_type=EventType.RUNTIME_CALL_CANCELLED,
                    payload={"runtime_call_id": call_id, **metadata},
                )
            raise
        except TimeoutError:
            await self._stop_process(process, kill=True)
            if terminal_message is not None:
                await self._finish_terminal_message(call_id, run, terminal_message)
            else:
                await self._terminal_failure(
                    call_id,
                    run,
                    f"Claude process timed out after {timeout_seconds:.3f} seconds",
                    metadata=metadata,
                )
        except Exception as exc:
            await self._stop_process(process, kill=True)
            if terminal_message is not None:
                await self._finish_terminal_message(call_id, run, terminal_message)
            else:
                await self._terminal_failure(call_id, run, str(exc), metadata=metadata)
        finally:
            if stderr_task and not stderr_task.done():
                stderr_task.cancel()
            self.processes.pop(call_id, None)
            self.interrupted_calls.discard(call_id)
            if self.session_active_calls.get(session.session_id) == call_id:
                self.session_active_calls.pop(session.session_id, None)
            if call_id not in self.terminal_calls:
                await self._terminal_failure(
                    call_id,
                    run,
                    "Claude provider call ended without a terminal event",
                    metadata=metadata,
                )

    async def _finish_terminal_message(
        self,
        call_id: str,
        run: RunRef,
        terminal_message: tuple[str, EventType, dict[str, Any]],
    ) -> None:
        native_type, normalized, payload = terminal_message
        await self._finish_call(
            call_id,
            run,
            native_type=native_type,
            normalized_type=normalized,
            payload=payload,
        )

    async def _terminal_failure(
        self,
        call_id: str,
        run: RunRef,
        message: str,
        stderr: str = "",
        return_code: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._finish_call(
            call_id,
            run,
            native_type="process/exit",
            normalized_type=EventType.RUNTIME_CALL_FAILED,
            payload={
                "runtime_call_id": call_id,
                "error": redact_text(message),
                "return_code": return_code,
                "stderr": stderr[-2000:],
                **(metadata or {}),
            },
        )

    async def _put_event(
        self,
        call_id: str,
        run: RunRef,
        *,
        native_type: str,
        normalized_type: EventType,
        payload: dict[str, Any],
    ) -> None:
        await self.queues[call_id].put(
            make_event(
                run_id=run.run_id,
                session_id=run.session_id,
                provider=Provider.CLAUDE,
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
        *,
        native_type: str,
        normalized_type: EventType,
        payload: dict[str, Any],
    ) -> None:
        if call_id in self.terminal_calls:
            return
        self.terminal_calls.add(call_id)
        run_session = self.call_sessions.get(call_id)
        if run_session and self.session_active_calls.get(run_session) == call_id:
            self.session_active_calls.pop(run_session, None)
        await self._put_event(
            call_id,
            run,
            native_type=native_type,
            normalized_type=normalized_type,
            payload=payload,
        )
        await self.queues[call_id].put(None)

    @staticmethod
    async def _stop_process(process: asyncio.subprocess.Process | None, *, kill: bool) -> None:
        if process and process.returncode is None:
            if kill:
                process.kill()
            else:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
                await process.wait()

    @staticmethod
    async def _stderr(stderr_task: asyncio.Task[bytes] | None) -> str:
        if stderr_task is None:
            return ""
        return redact_text((await stderr_task).decode(errors="replace"))

    @staticmethod
    def _prompt(request: RuntimeSubmission) -> str:
        task = submission_task(request)
        prompt: dict[str, Any] = {
            "objective": task.objective,
            "constraints": task.constraints,
            "success_criteria": task.success_criteria,
            "risk_level": task.risk_level.value,
        }
        prompt.update(submission_metadata(request))
        return json.dumps(prompt, ensure_ascii=False)

    @staticmethod
    def _normalize(message: dict[str, object]) -> EventType:
        kind = str(message.get("type", ""))
        subtype = str(message.get("subtype", ""))
        if kind == "system" and subtype == "init":
            return EventType.RUNTIME_CALL_STARTED
        if kind == "result":
            return (
                EventType.RUNTIME_CALL_FAILED
                if message.get("is_error")
                else EventType.RUNTIME_CALL_COMPLETED
            )
        if kind == "tool_use":
            return EventType.TOOL_REQUESTED
        return EventType.RUN_PROGRESS

    async def events(self, run: RunRef) -> AsyncIterator[AgentEvent]:
        queue = self.queues[self._call_id(run)]
        while (event := await queue.get()) is not None:
            yield event

    async def approve(self, request: ApprovalRequest, decision: ApprovalDecision) -> None:
        raise NotImplementedError("Claude uses a precomputed non-interactive tool policy")

    async def interrupt(self, run: RunRef) -> None:
        call_id = self._call_id(run)
        if call_id in self.terminal_calls:
            return
        self.interrupted_calls.add(call_id)
        process = self.processes.get(call_id)
        if process and process.returncode is None:
            process.terminate()
        task = self.tasks.get(call_id)
        if task and not task.done():
            task.cancel()
        await self._finish_call(
            call_id,
            self.run_refs[call_id],
            native_type="process/cancelled",
            normalized_type=EventType.RUNTIME_CALL_CANCELLED,
            payload={"runtime_call_id": call_id},
        )

    async def resume(self, run: RunRef) -> None:
        if not run.native_run_id:
            raise RuntimeError("Claude run has no resumable native session")

    async def artifacts(self, run: RunRef) -> list[ArtifactRef]:
        return []

    async def usage(self, run: RunRef) -> UsageSnapshot:
        return UsageSnapshot()

    async def terminate(self, run: RunRef) -> None:
        await self.interrupt(run)

    def _canonical_session(self, session: SessionRef) -> SessionRef:
        current = self.sessions.get(session.session_id)
        if current is None:
            self.sessions[session.session_id] = session
            if session.native_session_id:
                self.started_sessions.add(session.session_id)
            return session
        native_session_id = session.native_session_id or current.native_session_id
        canonical = session.model_copy(update={"native_session_id": native_session_id})
        self.sessions[session.session_id] = canonical
        return canonical

    @staticmethod
    def _call_id(run: RunRef) -> str:
        return run.runtime_call_id or run.run_id
