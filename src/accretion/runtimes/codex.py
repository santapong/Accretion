from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from collections.abc import AsyncIterator, Mapping
from typing import Any

from accretion.contracts import (
    AgentEvent,
    ApprovalDecision,
    ApprovalDecisionValue,
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
)

_CALL_TERMINALS = {
    EventType.RUNTIME_CALL_COMPLETED,
    EventType.RUNTIME_CALL_FAILED,
    EventType.RUNTIME_CALL_CANCELLED,
}


class CodexProtocolError(RuntimeError):
    pass


class CodexRuntime:
    """Stable Codex App Server client with repeatable turns per logical session."""

    adapter_version = "codex-app-server-p2-v1"

    def __init__(
        self,
        command: str = "codex",
        gateway_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.command = command
        self.gateway_environment = dict(gateway_environment or {})
        self.process: asyncio.subprocess.Process | None = None
        self.reader_task: asyncio.Task[None] | None = None
        self.stderr_task: asyncio.Task[None] | None = None
        self.request_id = 0
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.sessions: dict[str, SessionRef] = {}
        self.run_refs: dict[str, RunRef] = {}
        self.queues: dict[str, asyncio.Queue[AgentEvent | None]] = {}
        self.thread_to_call: dict[str, str] = {}
        self.turn_to_call: dict[str, str] = {}
        self.call_turns: dict[str, str] = {}
        self.session_active_calls: dict[str, str] = {}
        self.approval_routes: dict[str, tuple[int | str, str]] = {}
        self.loaded_threads: set[str] = set()
        self.stderr_tail: list[str] = []
        self.write_lock = asyncio.Lock()
        self.server_lock = asyncio.Lock()
        self.terminal_calls: set[str] = set()
        self.session_configs: dict[str, SessionConfig] = {}

    async def health(self) -> RuntimeHealth:
        version_code, version_output = await command_result([self.command, "--version"])
        auth_code, auth_output = await command_result([self.command, "login", "status"])
        status, pressure, error = classify_runtime_health(
            version_code=version_code,
            version_output=version_output,
            auth_code=auth_code,
            auth_output=auth_output,
            minimum=(0, 148, 0),
            maximum=(0, 149, 0),
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
            runtime_id="runtime_codex",
            provider=Provider.CODEX,
            status=status,
            auth_mode=AuthMode.SUBSCRIPTION,
            runtime_version=version,
            capabilities=[
                "app-server",
                "threads",
                "repeatable-calls",
                "approvals",
                "interrupt",
                "resume",
            ],
            active_sessions=len(self.sessions),
            active_runs=sum(call_id not in self.terminal_calls for call_id in self.run_refs),
            observed_usage_pressure=pressure,
            last_error=(
                ErrorSummary(
                    code=f"CODEX_{status.value}",
                    message=redact_text(error),
                    retryable=status is RuntimeStatus.RATE_LIMITED,
                )
                if error
                else None
            ),
        )

    async def create_session(self, config: SessionConfig) -> SessionRef:
        # App Server startup is deliberately lazy so submit can expose startup
        # failures through the provider call's terminal event stream.
        session = SessionRef(
            session_id=new_id("session"),
            run_id=config.run_id,
            provider=Provider.CODEX,
            native_session_id=config.resume_native_session_id,
            workspace=config.workspace,
        )
        self.sessions[session.session_id] = session
        self.session_configs[session.session_id] = config
        return session

    async def submit(self, session: SessionRef, request: RuntimeSubmission) -> RunRef:
        session = self._canonical_session(session)
        if isinstance(request, RuntimeExecutionRequest) and request.run_id != session.run_id:
            raise ValueError("runtime request run_id does not match the session")
        active_call = self.session_active_calls.get(session.session_id)
        if active_call and active_call not in self.terminal_calls:
            raise RuntimeError("the Codex session already has an active provider call")

        call_id = submission_call_id(request)
        if call_id in self.queues:
            raise ValueError(f"runtime call already exists: {call_id}")
        run = RunRef(
            run_id=session.run_id,
            session_id=session.session_id,
            native_run_id=session.native_session_id,
            runtime_call_id=call_id,
        )
        self.queues[call_id] = asyncio.Queue()
        self.run_refs[call_id] = run
        self.session_active_calls[session.session_id] = call_id

        try:
            await self._ensure_server()
            if call_id in self.terminal_calls:
                return self.run_refs[call_id]
            thread_id = await self._thread_for_session(session)
            run = run.model_copy(update={"native_run_id": thread_id})
            self.run_refs[call_id] = run
            self.thread_to_call[thread_id] = call_id
            session = session.model_copy(update={"native_session_id": thread_id})
            self.sessions[session.session_id] = session
            turn = await self._request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": self._prompt(request)}],
                    "cwd": str(session.workspace),
                },
            )
            turn_id = str(turn.get("turn", {}).get("id", ""))
            if not turn_id:
                raise CodexProtocolError("turn/start response did not include turn.id")
            self.turn_to_call[turn_id] = call_id
            self.call_turns[call_id] = turn_id
        except Exception as exc:
            await self._fail_call(call_id, f"provider call startup failed: {exc}")
        return self.run_refs[call_id]

    async def _thread_for_session(self, session: SessionRef) -> str:
        if session.native_session_id:
            thread_id = session.native_session_id
            if thread_id not in self.loaded_threads:
                response = await self._request(
                    "thread/resume",
                    {"threadId": thread_id, "cwd": str(session.workspace)},
                )
                thread_id = str(response.get("thread", {}).get("id", thread_id))
                self.loaded_threads.add(thread_id)
            return thread_id

        gateway_env = {
            **self.gateway_environment,
            "ACCRETION_GATEWAY_RUN_ID": session.run_id,
        }
        response = await self._request(
            "thread/start",
            {
                "cwd": str(session.workspace),
                "approvalPolicy": "on-request",
                "sandbox": "workspace-write",
                "developerInstructions": (
                    "Use native tools only for local workspace operations. "
                    "All consequential external actions must use the Accretion MCP gateway."
                ),
                "config": {
                    "mcp_servers": {
                        "accretion": {
                            "command": sys.executable,
                            "args": ["-m", "accretion.mcp_gateway"],
                            "env": gateway_env,
                            "enabled": True,
                            # Direct adapter users (including provider health/live
                            # probes) do not have an orchestrator store behind the
                            # gateway. Keep the server optional in that restricted
                            # no-capability mode; API-managed runs always provide
                            # gateway_environment and therefore fail closed.
                            "required": bool(self.gateway_environment),
                        }
                    },
                    "sandbox_workspace_write": {"network_access": False},
                    "shell_environment_policy": {"inherit": "core"},
                },
            },
        )
        thread_id = str(response.get("thread", {}).get("id", ""))
        if not thread_id:
            raise CodexProtocolError("thread/start response did not include thread.id")
        self.loaded_threads.add(thread_id)
        return thread_id

    @staticmethod
    def _prompt(request: RuntimeSubmission) -> str:
        task = submission_task(request)
        criteria = (
            "\n".join(f"- {item}" for item in task.success_criteria) or "- Complete objective"
        )
        constraints = "\n".join(f"- {item}" for item in task.constraints) or "- Stay in workspace"
        prompt = (
            f"Objective:\n{task.objective}\n\nSuccess criteria:\n{criteria}"
            f"\n\nConstraints:\n{constraints}"
        )
        metadata = submission_metadata(request)
        if metadata:
            prompt += "\n\nIteration directive:\n" + json.dumps(metadata, ensure_ascii=False)
        return prompt

    async def events(self, run: RunRef) -> AsyncIterator[AgentEvent]:
        queue = self.queues[self._call_id(run)]
        while (event := await queue.get()) is not None:
            yield event

    async def approve(self, request: ApprovalRequest, decision: ApprovalDecision) -> None:
        route = self.approval_routes.pop(request.approval_id, None)
        if route is None:
            raise CodexProtocolError("approval request is no longer pending")
        native_id, _method = route
        native_decision = {
            ApprovalDecisionValue.APPROVE: "accept",
            ApprovalDecisionValue.APPROVE_SESSION: "acceptForSession",
            ApprovalDecisionValue.DENY: "decline",
            ApprovalDecisionValue.CANCEL: "cancel",
        }[decision.decision]
        await self._send({"id": native_id, "result": {"decision": native_decision}})

    async def interrupt(self, run: RunRef) -> None:
        call_id = self._call_id(run)
        thread_id = run.native_run_id
        turn_id = self.call_turns.get(call_id)
        if thread_id and turn_id:
            await self._request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    async def resume(self, run: RunRef) -> None:
        if run.native_run_id:
            await self._request("thread/resume", {"threadId": run.native_run_id})
            self.loaded_threads.add(run.native_run_id)

    async def artifacts(self, run: RunRef) -> list[ArtifactRef]:
        return []

    async def usage(self, run: RunRef) -> UsageSnapshot:
        try:
            result = await self._request("account/rateLimits/read", {})
        except (CodexProtocolError, OSError):
            return UsageSnapshot()
        serialized = json.dumps(result).lower()
        pressure = UsagePressure.EXHAUSTED if "exhaust" in serialized else UsagePressure.UNKNOWN
        return UsageSnapshot(observed_usage_pressure=pressure)

    async def terminate(self, run: RunRef) -> None:
        await self.interrupt(run)

    async def close(self) -> None:
        process = self.process
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
                await process.wait()
        if self.reader_task and not self.reader_task.done():
            self.reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.reader_task
        if self.stderr_task and not self.stderr_task.done():
            self.stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.stderr_task

    async def _ensure_server(self) -> None:
        async with self.server_lock:
            if (
                self.process
                and self.process.returncode is None
                and self.reader_task
                and not self.reader_task.done()
            ):
                return
            if self.process and self.process.returncode is None:
                self.process.terminate()
                await self.process.wait()
            self.loaded_threads.clear()
            process = await asyncio.create_subprocess_exec(
                self.command,
                "app-server",
                "--listen",
                "stdio://",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=provider_environment(),
            )
            self.process = process
            self.reader_task = asyncio.create_task(self._reader(process))
            self.stderr_task = asyncio.create_task(self._stderr_reader(process))
            try:
                await self._request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "accretion",
                            "title": "Accretion",
                            "version": "0.1.0",
                        }
                    },
                )
                await self._send({"method": "initialized", "params": {}})
            except Exception:
                if process.returncode is None:
                    process.terminate()
                    await process.wait()
                raise

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.request_id += 1
        request_id = self.request_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self._send({"method": method, "id": request_id, "params": params})
            return await asyncio.wait_for(future, 30)
        except TimeoutError as exc:
            self.pending.pop(request_id, None)
            raise CodexProtocolError(f"{method} timed out") from exc
        except Exception:
            self.pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            raise

    async def _send(self, message: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin or self.process.returncode is not None:
            raise CodexProtocolError("app-server is not running")
        data = json.dumps(message, separators=(",", ":")).encode() + b"\n"
        async with self.write_lock:
            self.process.stdin.write(data)
            await self.process.stdin.drain()

    async def _reader(self, process: asyncio.subprocess.Process) -> None:
        if not process.stdout:
            await self._fail_active_runs("Codex App Server did not expose stdout")
            return
        error: Exception = CodexProtocolError("Codex App Server exited unexpectedly")
        try:
            while line := await process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                if "id" in message and ("result" in message or "error" in message):
                    request_id = message["id"]
                    future = self.pending.pop(request_id, None)
                    if future:
                        if "error" in message:
                            native_error = message["error"]
                            detail = (
                                native_error.get("message", "error")
                                if isinstance(native_error, dict)
                                else native_error
                            )
                            future.set_exception(CodexProtocolError(redact_text(str(detail))))
                        else:
                            result = message.get("result", {})
                            future.set_result(result if isinstance(result, dict) else {})
                    continue
                if "id" in message and "method" in message:
                    await self._handle_server_request(message)
                elif "method" in message:
                    await self._handle_notification(message)
        except asyncio.CancelledError:
            error = CodexProtocolError("Codex App Server reader was cancelled")
            raise
        except Exception as exc:
            error = CodexProtocolError(f"Codex App Server protocol failure: {exc}")
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
            self.pending.clear()
            await self._fail_active_runs(str(error))

    async def _stderr_reader(self, process: asyncio.subprocess.Process) -> None:
        if not process.stderr:
            return
        while line := await process.stderr.readline():
            self.stderr_tail.append(redact_text(line.decode(errors="replace").strip()))
            self.stderr_tail = self.stderr_tail[-50:]

    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        method = str(message["method"])
        params = redact(message.get("params", {}))
        call_id = self._resolve_call(params)
        if not call_id or call_id not in self.queues or call_id in self.terminal_calls:
            await self._send({"id": message["id"], "result": {"decision": "decline"}})
            return
        approval_id = new_id("approval")
        self.approval_routes[approval_id] = (message["id"], method)
        run = self.run_refs[call_id]
        await self.queues[call_id].put(
            make_event(
                run_id=run.run_id,
                session_id=run.session_id,
                provider=Provider.CODEX,
                native_type=method,
                normalized_type=EventType.APPROVAL_REQUIRED,
                payload={
                    "runtime_call_id": call_id,
                    "approval_id": approval_id,
                    "native_request_id": str(message["id"]),
                    "method": method,
                    "request": params,
                },
                adapter_version=self.adapter_version,
                correlation_id=call_id,
            )
        )

    async def _handle_notification(self, message: dict[str, Any]) -> None:
        method = str(message["method"])
        params = redact(message.get("params", {}))
        call_id = self._resolve_call(params)
        if not call_id or call_id not in self.queues or call_id in self.terminal_calls:
            return
        normalized = self._normalize(method, params)
        run = self.run_refs[call_id]
        event = make_event(
            run_id=run.run_id,
            session_id=run.session_id,
            provider=Provider.CODEX,
            native_type=method,
            normalized_type=normalized,
            payload={"runtime_call_id": call_id, "provider_extension": params},
            adapter_version=self.adapter_version,
            correlation_id=call_id,
        )
        if normalized in _CALL_TERMINALS:
            await self._finish_call(call_id, event)
        else:
            await self.queues[call_id].put(event)

    async def _finish_call(self, call_id: str, event: AgentEvent) -> None:
        if call_id in self.terminal_calls:
            return
        self.terminal_calls.add(call_id)
        run = self.run_refs.get(call_id)
        if run and self.session_active_calls.get(run.session_id) == call_id:
            self.session_active_calls.pop(run.session_id, None)
        queue = self.queues.get(call_id)
        if queue is not None:
            await queue.put(event)
            await queue.put(None)

    async def _fail_call(self, call_id: str, message: str) -> None:
        run = self.run_refs.get(call_id)
        if run is None or call_id in self.terminal_calls:
            return
        await self._finish_call(
            call_id,
            make_event(
                run_id=run.run_id,
                session_id=run.session_id,
                provider=Provider.CODEX,
                native_type="process/exit",
                normalized_type=EventType.RUNTIME_CALL_FAILED,
                payload={
                    "runtime_call_id": call_id,
                    "error": redact_text(message),
                    "stderr": self.stderr_tail[-10:],
                },
                adapter_version=self.adapter_version,
                correlation_id=call_id,
            ),
        )

    async def _fail_active_runs(self, message: str) -> None:
        # Retain the P0 method name because recovery tests exercise it directly.
        for call_id in list(self.run_refs):
            await self._fail_call(call_id, message)

    def _resolve_call(self, params: dict[str, Any]) -> str | None:
        thread = params.get("thread")
        turn = params.get("turn")
        thread_id = params.get("threadId") or (
            thread.get("id") if isinstance(thread, dict) else None
        )
        turn_id = params.get("turnId") or (turn.get("id") if isinstance(turn, dict) else None)
        return self.turn_to_call.get(str(turn_id)) or self.thread_to_call.get(str(thread_id))

    def _canonical_session(self, session: SessionRef) -> SessionRef:
        current = self.sessions.get(session.session_id)
        if current is None:
            self.sessions[session.session_id] = session
            return session
        native_session_id = current.native_session_id or session.native_session_id
        canonical = session.model_copy(update={"native_session_id": native_session_id})
        self.sessions[session.session_id] = canonical
        return canonical

    @staticmethod
    def _call_id(run: RunRef) -> str:
        return run.runtime_call_id or run.run_id

    @staticmethod
    def _normalize(method: str, params: dict[str, Any]) -> EventType:
        if method == "turn/started":
            return EventType.RUNTIME_CALL_STARTED
        if method == "turn/completed":
            turn = params.get("turn")
            status = str(turn.get("status", "completed") if isinstance(turn, dict) else "completed")
            status = status.lower()
            if status in {"failed", "error"}:
                return EventType.RUNTIME_CALL_FAILED
            if status in {"interrupted", "cancelled"}:
                return EventType.RUNTIME_CALL_CANCELLED
            return EventType.RUNTIME_CALL_COMPLETED
        if method.endswith("requestApproval"):
            return EventType.APPROVAL_REQUIRED
        if "commandExecution" in method and method.endswith("/started"):
            return EventType.TOOL_STARTED
        if "commandExecution" in method and method.endswith("/completed"):
            return EventType.TOOL_COMPLETED
        if "fileChange" in method:
            return EventType.FILE_CHANGED
        if method in {"error", "turn/error"}:
            return EventType.RUNTIME_CALL_FAILED
        return EventType.RUN_PROGRESS
