from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
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


class CodexProtocolError(RuntimeError):
    pass


class CodexRuntime:
    """Stable Codex App Server client over newline-delimited JSON stdio."""

    adapter_version = "codex-app-server-p0-v1"

    def __init__(self, command: str = "codex") -> None:
        self.command = command
        self.process: asyncio.subprocess.Process | None = None
        self.reader_task: asyncio.Task[None] | None = None
        self.request_id = 0
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.sessions: dict[str, SessionRef] = {}
        self.run_refs: dict[str, RunRef] = {}
        self.queues: dict[str, asyncio.Queue[AgentEvent | None]] = {}
        self.thread_to_run: dict[str, str] = {}
        self.turn_to_run: dict[str, str] = {}
        self.approval_routes: dict[str, tuple[int | str, str]] = {}
        self.stderr_tail: list[str] = []
        self.write_lock = asyncio.Lock()
        self.terminal_runs: set[str] = set()

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
            capabilities=["app-server", "threads", "approvals", "interrupt", "resume"],
            active_sessions=len(self.sessions),
            active_runs=len(self.run_refs),
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
        await self._ensure_server()
        session = SessionRef(
            session_id=new_id("session"),
            run_id=config.run_id,
            provider=Provider.CODEX,
            native_session_id=config.resume_native_session_id,
            workspace=config.workspace,
        )
        self.sessions[session.session_id] = session
        return session

    async def submit(self, session: SessionRef, task: TaskEnvelope) -> RunRef:
        await self._ensure_server()
        if session.native_session_id:
            response = await self._request(
                "thread/resume",
                {"threadId": session.native_session_id, "cwd": str(session.workspace)},
            )
        else:
            response = await self._request(
                "thread/start",
                {
                    "cwd": str(session.workspace),
                    "approvalPolicy": "on-request",
                    "sandbox": "workspace-write",
                },
            )
        thread = response.get("thread", {})
        thread_id = str(thread.get("id", ""))
        if not thread_id:
            raise CodexProtocolError("thread/start response did not include thread.id")
        run_id = session.run_id
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        run = RunRef(run_id=run_id, session_id=session.session_id, native_run_id=thread_id)
        self.queues[run_id] = queue
        self.terminal_runs.discard(run_id)
        self.run_refs[run_id] = run
        self.thread_to_run[thread_id] = run_id
        try:
            turn = await self._request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": self._prompt(task)}],
                    "cwd": str(session.workspace),
                },
            )
        except Exception as exc:
            await self._fail_run(run_id, f"turn/start failed: {exc}")
            raise
        turn_id = str(turn.get("turn", {}).get("id", ""))
        if turn_id:
            self.turn_to_run[turn_id] = run_id
        self.sessions[session.session_id] = session.model_copy(
            update={"native_session_id": thread_id}
        )
        return run

    @staticmethod
    def _prompt(task: TaskEnvelope) -> str:
        criteria = (
            "\n".join(f"- {item}" for item in task.success_criteria) or "- Complete objective"
        )
        constraints = "\n".join(f"- {item}" for item in task.constraints) or "- Stay in workspace"
        return (
            f"Objective:\n{task.objective}\n\nSuccess criteria:\n{criteria}"
            f"\n\nConstraints:\n{constraints}"
        )

    async def events(self, run: RunRef) -> AsyncIterator[AgentEvent]:
        queue = self.queues[run.run_id]
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
        thread_id = run.native_run_id
        turn_id = next(
            (turn for turn, owner in self.turn_to_run.items() if owner == run.run_id), None
        )
        if thread_id and turn_id:
            await self._request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    async def resume(self, run: RunRef) -> None:
        if run.native_run_id:
            await self._request("thread/resume", {"threadId": run.native_run_id})

    async def artifacts(self, run: RunRef) -> list[ArtifactRef]:
        return []

    async def usage(self, run: RunRef) -> UsageSnapshot:
        try:
            result = await self._request("account/rateLimits/read", {})
        except CodexProtocolError:
            return UsageSnapshot()
        serialized = json.dumps(result).lower()
        pressure = UsagePressure.EXHAUSTED if "exhaust" in serialized else UsagePressure.UNKNOWN
        return UsageSnapshot(observed_usage_pressure=pressure)

    async def terminate(self, run: RunRef) -> None:
        await self.interrupt(run)

    async def close(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.reader_task:
            self.reader_task.cancel()

    async def _ensure_server(self) -> None:
        if self.process and self.process.returncode is None:
            return
        self.process = await asyncio.create_subprocess_exec(
            self.command,
            "app-server",
            "--listen",
            "stdio://",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.reader_task = asyncio.create_task(self._reader())
        asyncio.create_task(self._stderr_reader())
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

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.request_id += 1
        request_id = self.request_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        await self._send({"method": method, "id": request_id, "params": params})
        try:
            return await asyncio.wait_for(future, 30)
        except TimeoutError as exc:
            self.pending.pop(request_id, None)
            raise CodexProtocolError(f"{method} timed out") from exc

    async def _send(self, message: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise CodexProtocolError("app-server is not running")
        data = json.dumps(message, separators=(",", ":")).encode() + b"\n"
        async with self.write_lock:
            self.process.stdin.write(data)
            await self.process.stdin.drain()

    async def _reader(self) -> None:
        assert self.process and self.process.stdout
        error: Exception = CodexProtocolError("Codex App Server exited unexpectedly")
        try:
            while line := await self.process.stdout.readline():
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
                            future.set_exception(
                                CodexProtocolError(
                                    redact_text(str(message["error"].get("message", "error")))
                                )
                            )
                        else:
                            future.set_result(message.get("result", {}))
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

    async def _stderr_reader(self) -> None:
        assert self.process and self.process.stderr
        while line := await self.process.stderr.readline():
            self.stderr_tail.append(redact_text(line.decode(errors="replace").strip()))
            self.stderr_tail = self.stderr_tail[-50:]

    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        method = str(message["method"])
        params = redact(message.get("params", {}))
        run_id = self._resolve_run(params)
        if not run_id or run_id not in self.queues:
            await self._send({"id": message["id"], "result": {"decision": "decline"}})
            return
        approval_id = new_id("approval")
        self.approval_routes[approval_id] = (message["id"], method)
        await self.queues[run_id].put(
            make_event(
                run_id=run_id,
                session_id=self.run_refs[run_id].session_id,
                provider=Provider.CODEX,
                native_type=method,
                normalized_type=EventType.APPROVAL_REQUIRED,
                payload={
                    "approval_id": approval_id,
                    "native_request_id": str(message["id"]),
                    "method": method,
                    "request": params,
                },
                adapter_version=self.adapter_version,
            )
        )

    async def _handle_notification(self, message: dict[str, Any]) -> None:
        method = str(message["method"])
        params = redact(message.get("params", {}))
        run_id = self._resolve_run(params)
        if not run_id or run_id not in self.queues:
            return
        normalized = self._normalize(method, params)
        event = make_event(
            run_id=run_id,
            session_id=self.run_refs[run_id].session_id,
            provider=Provider.CODEX,
            native_type=method,
            normalized_type=normalized,
            payload={"provider_extension": params},
            adapter_version=self.adapter_version,
        )
        if normalized in {EventType.RUN_COMPLETED, EventType.RUN_FAILED, EventType.RUN_CANCELLED}:
            await self._finish_run(run_id, event)
        else:
            await self.queues[run_id].put(event)

    async def _finish_run(self, run_id: str, event: AgentEvent) -> None:
        if run_id in self.terminal_runs:
            return
        self.terminal_runs.add(run_id)
        queue = self.queues.get(run_id)
        if queue is not None:
            await queue.put(event)
            await queue.put(None)

    async def _fail_run(self, run_id: str, message: str) -> None:
        run = self.run_refs.get(run_id)
        if run is None or run_id in self.terminal_runs:
            return
        await self._finish_run(
            run_id,
            make_event(
                run_id=run_id,
                session_id=run.session_id,
                provider=Provider.CODEX,
                native_type="process/exit",
                normalized_type=EventType.RUN_FAILED,
                payload={"error": redact_text(message), "stderr": self.stderr_tail[-10:]},
                adapter_version=self.adapter_version,
            ),
        )

    async def _fail_active_runs(self, message: str) -> None:
        for run_id in list(self.queues):
            await self._fail_run(run_id, message)

    def _resolve_run(self, params: dict[str, Any]) -> str | None:
        thread_id = params.get("threadId") or params.get("thread", {}).get("id")
        turn_id = params.get("turnId") or params.get("turn", {}).get("id")
        return self.thread_to_run.get(str(thread_id)) or self.turn_to_run.get(str(turn_id))

    @staticmethod
    def _normalize(method: str, params: dict[str, Any]) -> EventType:
        if method == "turn/started":
            return EventType.RUN_STARTED
        if method == "turn/completed":
            status = str(params.get("turn", {}).get("status", "completed")).lower()
            if status in {"failed", "error"}:
                return EventType.RUN_FAILED
            if status in {"interrupted", "cancelled"}:
                return EventType.RUN_CANCELLED
            return EventType.RUN_COMPLETED
        if method.endswith("requestApproval"):
            return EventType.APPROVAL_REQUIRED
        if "commandExecution" in method and method.endswith("/started"):
            return EventType.TOOL_STARTED
        if "commandExecution" in method and method.endswith("/completed"):
            return EventType.TOOL_COMPLETED
        if "fileChange" in method:
            return EventType.FILE_CHANGED
        if method in {"error", "turn/error"}:
            return EventType.RUN_FAILED
        return EventType.RUN_PROGRESS
