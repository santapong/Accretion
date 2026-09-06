from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

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
    RUNTIME_STREAM_LIMIT,
    RuntimeSubmission,
    classify_runtime_health,
    make_event,
    probe_result,
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

# The server announces its bound address on stdout; the official SDK launcher parses
# the same line, so this is the supported startup handshake rather than a guess.
_LISTEN_PATTERN = re.compile(r"on\s+(https?://[^\s]+)")
_STARTUP_TIMEOUT_SECONDS = 15.0
_REQUEST_TIMEOUT_SECONDS = 30.0

# GET /event only ever yields server.connected and heartbeats; session activity is published
# exclusively on the global bus, wrapped as {directory, project, payload}. Verified against a
# live server, not inferred from the schema.
_EVENT_PATH = "/global/event"

# Per-token streaming deltas. They resolve to a session, but persisting one durable event per
# token would bury the audit trace; message.part.updated already carries the settled part.
_IGNORED_EVENTS = frozenset({"message.part.delta"})

_DEFAULT_MODEL = "opencode/x-preview-f-free"


class OpencodeProtocolError(RuntimeError):
    pass


class OpencodeRuntime:
    """Headless opencode server client with repeatable calls per logical session."""

    adapter_version = "opencode-server-p2-v1"

    def __init__(
        self,
        command: str = "opencode",
        gateway_environment: Mapping[str, str] | None = None,
        model: str = _DEFAULT_MODEL,
    ) -> None:
        self.command = command
        self.gateway_environment = dict(gateway_environment or {})
        self.model = model
        self.process: asyncio.subprocess.Process | None = None
        self.base_url: str | None = None
        self.password = secrets.token_urlsafe(32)
        self.client: httpx.AsyncClient | None = None
        self.reader_task: asyncio.Task[None] | None = None
        self.stderr_task: asyncio.Task[None] | None = None
        self.sessions: dict[str, SessionRef] = {}
        self.run_refs: dict[str, RunRef] = {}
        self.queues: dict[str, asyncio.Queue[AgentEvent | None]] = {}
        self.session_configs: dict[str, SessionConfig] = {}
        self.session_active_calls: dict[str, str] = {}
        self.native_to_call: dict[str, str] = {}
        self.call_natives: dict[str, str] = {}
        self.started_calls: set[str] = set()
        self.approval_routes: dict[str, tuple[str, str]] = {}
        self.timeout_tasks: dict[str, asyncio.Task[None]] = {}
        self.interrupted_calls: set[str] = set()
        self.terminal_calls: set[str] = set()
        self.stderr_tail: list[str] = []
        self.server_lock = asyncio.Lock()

    # ------------------------------------------------------------------ health

    async def health(self) -> RuntimeHealth:
        version_code, version_output = await probe_result([self.command, "--version"])
        auth_code, auth_output = await probe_result([self.command, "auth", "list"])
        status, pressure, error = classify_runtime_health(
            version_code=version_code,
            version_output=version_output,
            auth_code=auth_code,
            auth_output=auth_output,
            minimum=(1, 18, 0),
            maximum=(1, 19, 0),
        )
        if status is RuntimeStatus.READY:
            status, error = await self._classify_model(status, error)
        return self._health(status, error, version_output, pressure)

    async def _classify_model(
        self, status: RuntimeStatus, error: str | None
    ) -> tuple[RuntimeStatus, str | None]:
        """Fail closed when the configured model is gone.

        Free preview models are withdrawn without notice and `auth list` still exits 0,
        so an unavailable model must surface as health rather than as a mid-run failure.
        """

        models_code, models_output = await probe_result([self.command, "models"], 15.0)
        if models_code != 0:
            return RuntimeStatus.DEGRADED, f"could not list opencode models: {models_output}"
        available = {line.strip() for line in models_output.splitlines() if line.strip()}
        if self.model not in available:
            return RuntimeStatus.DEGRADED, (
                f"configured model {self.model} is not available to this opencode "
                f"installation; set ACCRETION_OPENCODE_MODEL to an available model"
            )
        return status, error

    def _health(
        self,
        status: RuntimeStatus,
        error: str | None,
        version: str = "unknown",
        pressure: UsagePressure = UsagePressure.UNKNOWN,
    ) -> RuntimeHealth:
        return RuntimeHealth(
            runtime_id="runtime_opencode",
            provider=Provider.OPENCODE,
            status=status,
            # opencode authenticates with a provider API key, not a subscription seat.
            auth_mode=AuthMode.API,
            runtime_version=version,
            capabilities=[
                "http-server",
                "sse-events",
                "repeatable-calls",
                "session-resume",
                "tool-policy",
                "approvals",
                "interrupt",
            ],
            active_sessions=len(self.sessions),
            active_runs=sum(call_id not in self.terminal_calls for call_id in self.run_refs),
            observed_usage_pressure=pressure,
            last_error=(
                ErrorSummary(
                    code=f"OPENCODE_{status.value}",
                    message=redact_text(error),
                    retryable=status is RuntimeStatus.RATE_LIMITED,
                )
                if error
                else None
            ),
        )

    # ----------------------------------------------------------------- session

    async def create_session(self, config: SessionConfig) -> SessionRef:
        # Server startup is deliberately lazy so submit can expose startup failures
        # through the provider call's terminal event stream.
        session = SessionRef(
            session_id=new_id("session"),
            run_id=config.run_id,
            provider=Provider.OPENCODE,
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
            raise RuntimeError("the opencode session already has an active provider call")

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
            native_id = await self._native_session(session)
            run = run.model_copy(update={"native_run_id": native_id})
            self.run_refs[call_id] = run
            self.native_to_call[native_id] = call_id
            self.call_natives[call_id] = native_id
            session = session.model_copy(update={"native_session_id": native_id})
            self.sessions[session.session_id] = session
            await self._request(
                "POST",
                f"/session/{native_id}/prompt_async",
                params={"directory": str(session.workspace)},
                json=self._prompt_body(session, request),
            )
            self.timeout_tasks[call_id] = asyncio.create_task(
                self._watchdog(call_id, submission_timeout_seconds(request))
            )
        except Exception as exc:
            await self._fail_call(call_id, f"provider call startup failed: {exc}")
        return self.run_refs[call_id]

    async def _native_session(self, session: SessionRef) -> str:
        """Reuse the provider session across iterations; opencode cannot pre-mint ids."""

        if session.native_session_id:
            return session.native_session_id
        created = await self._request(
            "POST",
            "/session",
            params={"directory": str(session.workspace)},
            json={"title": f"accretion {session.run_id}"},
        )
        native_id = str(created.get("id", ""))
        if not native_id:
            raise OpencodeProtocolError("session create response did not include id")
        return native_id

    def _prompt_body(self, session: SessionRef, request: RuntimeSubmission) -> dict[str, Any]:
        config = self.session_configs[session.session_id]
        if config.allowed_tools:
            # Refusing is the honest failure. Silently running without the gateway would let a
            # task believe it held governed capabilities it never actually had.
            raise OpencodeProtocolError(
                "the opencode runtime does not provide the Accretion capability gateway; "
                f"this task requests {sorted(config.allowed_tools)}. Route it to Claude or "
                "Codex, or clear the capability set."
            )
        selected = config.model or self.model
        provider_id, _, model_id = selected.partition("/")
        if not provider_id or not model_id:
            raise OpencodeProtocolError(
                f"model must be in providerID/modelID form, got {selected!r}"
            )
        return {
            "model": {"providerID": provider_id, "modelID": model_id},
            "tools": self._tool_policy(config),
            "parts": [{"type": "text", "text": self._prompt(request)}],
        }

    @staticmethod
    def _tool_policy(config: SessionConfig) -> dict[str, bool]:
        """Project the capability allow/deny lists onto opencode's per-call tool map."""

        policy: dict[str, bool] = {
            "read": True,
            "edit": True,
            "write": True,
            "glob": True,
            "grep": True,
            "bash": True,
            # Network reach is never granted implicitly; capabilities arrive via the gateway.
            "webfetch": False,
            "websearch": False,
        }
        for name in config.denied_tools:
            policy[name] = False
        return policy

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

    # ---------------------------------------------------------------- controls

    async def approve(self, request: ApprovalRequest, decision: ApprovalDecision) -> None:
        route = self.approval_routes.pop(request.approval_id, None)
        if route is None:
            raise OpencodeProtocolError("approval request is no longer pending")
        native_session_id, permission_id = route
        native_decision = {
            ApprovalDecisionValue.APPROVE: "once",
            ApprovalDecisionValue.APPROVE_SESSION: "always",
            ApprovalDecisionValue.DENY: "reject",
            ApprovalDecisionValue.CANCEL: "reject",
        }[decision.decision]
        await self._request(
            "POST",
            f"/session/{native_session_id}/permissions/{permission_id}",
            json={"response": native_decision},
        )

    async def interrupt(self, run: RunRef) -> None:
        call_id = self._call_id(run)
        if call_id in self.terminal_calls:
            return
        self.interrupted_calls.add(call_id)
        native_id = self.call_natives.get(call_id) or run.native_run_id
        if native_id and self.base_url:
            with contextlib.suppress(Exception):
                await self._request("POST", f"/session/{native_id}/abort")
        stored = self.run_refs.get(call_id)
        if stored is None:
            return
        await self._finish_call(
            call_id,
            make_event(
                run_id=stored.run_id,
                session_id=stored.session_id,
                provider=Provider.OPENCODE,
                native_type="session/abort",
                normalized_type=EventType.RUNTIME_CALL_CANCELLED,
                payload={"runtime_call_id": call_id},
                adapter_version=self.adapter_version,
                correlation_id=call_id,
            ),
        )

    async def resume(self, run: RunRef) -> None:
        if not run.native_run_id:
            raise OpencodeProtocolError("cannot resume an opencode run without a native session")

    async def artifacts(self, run: RunRef) -> list[ArtifactRef]:
        return []

    async def usage(self, run: RunRef) -> UsageSnapshot:
        return UsageSnapshot()

    async def terminate(self, run: RunRef) -> None:
        await self.interrupt(run)

    async def close(self) -> None:
        for task in list(self.timeout_tasks.values()):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self.timeout_tasks.clear()
        for stream_task in (self.reader_task, self.stderr_task):
            if stream_task and not stream_task.done():
                stream_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stream_task
        if self.client is not None:
            await self.client.aclose()
            self.client = None
        process = self.process
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
                await process.wait()
        self.base_url = None

    # ------------------------------------------------------------------ server

    async def _ensure_server(self) -> None:
        async with self.server_lock:
            if (
                self.process
                and self.process.returncode is None
                and self.client is not None
                and self.reader_task
                and not self.reader_task.done()
            ):
                return
            if self.process and self.process.returncode is None:
                self.process.terminate()
                await self.process.wait()
            process = await asyncio.create_subprocess_exec(
                self.command,
                "serve",
                "--hostname=127.0.0.1",
                "--port=0",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=provider_environment(
                    {
                        "OPENCODE_SERVER_PASSWORD": self.password,
                        "OPENCODE_CONFIG_CONTENT": json.dumps(
                            self._server_config(), separators=(",", ":")
                        ),
                    }
                ),
                limit=RUNTIME_STREAM_LIMIT,
            )
            self.process = process
            self.stderr_task = asyncio.create_task(self._stderr_reader(process))
            try:
                self.base_url = await self._await_listening(process)
            except Exception:
                if process.returncode is None:
                    process.terminate()
                    await process.wait()
                raise
            self.client = httpx.AsyncClient(
                base_url=self.base_url,
                auth=("opencode", self.password),
                # Ordinary requests stay bounded; the event stream opts out of the read
                # timeout explicitly. A shared read=None would let a hung prompt_async block
                # submit forever, before the per-call watchdog is even armed.
                timeout=httpx.Timeout(_REQUEST_TIMEOUT_SECONDS),
            )
            self.reader_task = asyncio.create_task(self._reader())

    def _server_config(self) -> dict[str, Any]:
        """Scope the server through inline config so the operator's own config is untouched.

        Deliberately carries no MCP gateway. opencode resolves `mcp` once per server process
        from OPENCODE_CONFIG_CONTENT, but the gateway pins a single ACCRETION_GATEWAY_RUN_ID
        for its lifetime (mcp_gateway.py:160-162). On a server shared by concurrent runs that
        would attribute every governed side effect to whichever run started first, so opencode
        runs are workspace-local and capability requests are refused in _prompt_body.
        """

        return {
            "permission": {
                "edit": "allow",
                "bash": {
                    "git status*": "allow",
                    "git diff*": "allow",
                    "pytest*": "allow",
                    "uv run*": "allow",
                    "npm test*": "allow",
                    "npm run*": "allow",
                    "*": "deny",
                },
                "webfetch": "deny",
                # opencode has no sandbox_workspace_write equivalent; this is what pins a run
                # inside its own worktree.
                "external_directory": "deny",
            }
        }

    async def _await_listening(self, process: asyncio.subprocess.Process) -> str:
        if not process.stdout:
            raise OpencodeProtocolError("opencode server did not expose stdout")

        async def scan() -> str:
            assert process.stdout is not None
            while line := await process.stdout.readline():
                text = line.decode(errors="replace").strip()
                if not text.startswith("opencode server listening"):
                    continue
                match = _LISTEN_PATTERN.search(text)
                if not match:
                    raise OpencodeProtocolError(f"could not parse server url from: {text}")
                return match.group(1)
            raise OpencodeProtocolError(
                "opencode server exited before announcing a listening address: "
                + " | ".join(self.stderr_tail[-5:])
            )

        try:
            return await asyncio.wait_for(scan(), _STARTUP_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            raise OpencodeProtocolError(
                f"opencode server did not start within {_STARTUP_TIMEOUT_SECONDS:.0f}s"
            ) from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.client is None:
            raise OpencodeProtocolError("opencode server is not running")
        try:
            response = await self.client.request(method, path, params=params, json=json)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OpencodeProtocolError(f"{method} {path} failed: {redact_text(str(exc))}") from exc
        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    async def _stderr_reader(self, process: asyncio.subprocess.Process) -> None:
        if not process.stderr:
            return
        while line := await process.stderr.readline():
            self.stderr_tail.append(redact_text(line.decode(errors="replace").strip()))
            self.stderr_tail = self.stderr_tail[-50:]

    async def _reader(self) -> None:
        error = "opencode event stream closed"
        try:
            client = self.client
            if client is None:
                raise OpencodeProtocolError("opencode server is not running")
            async with client.stream(
                "GET", _EVENT_PATH, timeout=httpx.Timeout(_REQUEST_TIMEOUT_SECONDS, read=None)
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[len("data:") :].strip()
                    if not raw:
                        continue
                    try:
                        message = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(message, dict):
                        await self._handle_event(self._unwrap(message))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = f"opencode event stream failed: {exc}"
        finally:
            await self._fail_active_runs(error)

    # ------------------------------------------------------------------ events

    @staticmethod
    def _unwrap(message: dict[str, Any]) -> dict[str, Any]:
        """Accept the global bus envelope or a bare event, so the path stays swappable."""

        payload = message.get("payload")
        if isinstance(payload, dict) and "type" in payload:
            return payload
        return message

    async def _handle_event(self, message: dict[str, Any]) -> None:
        event_type = str(message.get("type", ""))
        if event_type in _IGNORED_EVENTS:
            return
        raw = message.get("properties") or {}
        raw = raw if isinstance(raw, dict) else {}
        if event_type == "server.instance.disposed":
            await self._fail_active_runs("opencode server instance was disposed")
            return

        # Resolve and classify against the RAW properties. redact() scrubs any key matching
        # `session[_-]?id`, so redacting first would turn every session id into "[REDACTED]"
        # and silently drop the entire event stream.
        native_id = self._native_id(event_type, raw)
        if native_id is None:
            if event_type == "session.error":
                # A session-less error is a server-wide failure, not one call's problem.
                await self._fail_active_runs(self._error_detail(raw))
            return

        call_id = self.native_to_call.get(native_id)
        if not call_id or call_id not in self.queues or call_id in self.terminal_calls:
            return

        normalized = self._normalize(event_type, raw)
        if normalized is EventType.RUNTIME_CALL_STARTED:
            if call_id in self.started_calls:
                normalized = EventType.RUN_PROGRESS
            else:
                self.started_calls.add(call_id)
        elif normalized is EventType.RUNTIME_CALL_COMPLETED and call_id not in self.started_calls:
            # A session can report idle before it ever went busy, between session create and
            # the prompt being accepted. Completing the call there would be a false terminal.
            return

        properties = redact(raw)
        run = self.run_refs[call_id]
        payload: dict[str, Any] = {
            "runtime_call_id": call_id,
            "provider_extension": properties,
        }
        if normalized is EventType.APPROVAL_REQUIRED:
            approval_id = new_id("approval")
            permission_id = str(raw.get("id", ""))
            self.approval_routes[approval_id] = (native_id, permission_id)
            payload["approval_id"] = approval_id
            payload["native_request_id"] = permission_id
            payload["method"] = str(raw.get("type", "permission"))

        event = make_event(
            run_id=run.run_id,
            session_id=run.session_id,
            provider=Provider.OPENCODE,
            native_type=event_type,
            normalized_type=normalized,
            payload=payload,
            adapter_version=self.adapter_version,
            correlation_id=call_id,
        )
        if normalized in _CALL_TERMINALS:
            await self._finish_call(call_id, event)
        else:
            await self.queues[call_id].put(event)

    @staticmethod
    def _native_id(event_type: str, properties: dict[str, Any]) -> str | None:
        """Resolve the opencode session a bus event belongs to.

        The event stream is global, so anything without a session id cannot be attributed
        to a call. `file.edited` is the notable case: it carries only a path, so it is
        deliberately unattributable rather than guessed at.
        """

        if event_type == "message.part.updated":
            part = properties.get("part")
            if isinstance(part, dict):
                session_id = part.get("sessionID")
                return str(session_id) if session_id else None
            return None
        session_id = properties.get("sessionID")
        return str(session_id) if session_id else None

    @staticmethod
    def _error_detail(properties: dict[str, Any]) -> str:
        error = properties.get("error")
        if isinstance(error, dict):
            name = str(error.get("name", "UnknownError"))
            data = error.get("data")
            if isinstance(data, dict) and data.get("message"):
                return f"{name}: {data['message']}"
            return name
        return "opencode reported an unspecified session error"

    @classmethod
    def _normalize(cls, event_type: str, properties: dict[str, Any]) -> EventType:
        if event_type == "session.status":
            status = properties.get("status")
            kind = str(status.get("type", "")) if isinstance(status, dict) else ""
            # Only "busy" means the provider picked the turn up; retry/idle are progress.
            return (
                EventType.RUNTIME_CALL_STARTED if kind == "busy" else EventType.RUN_PROGRESS
            )
        if event_type == "session.created":
            return EventType.RUNTIME_CALL_STARTED
        if event_type == "session.idle":
            return EventType.RUNTIME_CALL_COMPLETED
        if event_type == "session.error":
            error = properties.get("error")
            name = error.get("name", "") if isinstance(error, dict) else ""
            # An aborted message is an operator interrupt, not a provider failure.
            if str(name) == "MessageAbortedError":
                return EventType.RUNTIME_CALL_CANCELLED
            return EventType.RUNTIME_CALL_FAILED
        if event_type == "permission.updated":
            return EventType.APPROVAL_REQUIRED
        if event_type == "session.diff":
            return EventType.FILE_CHANGED
        if event_type == "message.part.updated":
            part = properties.get("part")
            if isinstance(part, dict) and part.get("type") == "tool":
                state = part.get("state")
                status = str(state.get("status", "")) if isinstance(state, dict) else ""
                return {
                    "pending": EventType.TOOL_REQUESTED,
                    "running": EventType.TOOL_STARTED,
                    "completed": EventType.TOOL_COMPLETED,
                    "error": EventType.TOOL_FAILED,
                }.get(status, EventType.RUN_PROGRESS)
        return EventType.RUN_PROGRESS

    # --------------------------------------------------------------- terminals

    async def _watchdog(self, call_id: str, timeout_seconds: float) -> None:
        try:
            await asyncio.sleep(timeout_seconds)
        except asyncio.CancelledError:
            return
        await self._fail_call(
            call_id, f"opencode provider call timed out after {timeout_seconds:.3f} seconds"
        )

    async def _finish_call(self, call_id: str, event: AgentEvent) -> None:
        if call_id in self.terminal_calls:
            return
        self.terminal_calls.add(call_id)
        task = self.timeout_tasks.pop(call_id, None)
        if task and not task.done():
            task.cancel()
        run = self.run_refs.get(call_id)
        if run and self.session_active_calls.get(run.session_id) == call_id:
            self.session_active_calls.pop(run.session_id, None)
        native_id = self.call_natives.pop(call_id, None)
        if native_id:
            self.native_to_call.pop(native_id, None)
        queue = self.queues.get(call_id)
        if queue is not None:
            await queue.put(event)
            await queue.put(None)

    async def _fail_call(self, call_id: str, message: str) -> None:
        run = self.run_refs.get(call_id)
        if run is None or call_id in self.terminal_calls:
            return
        terminal = (
            EventType.RUNTIME_CALL_CANCELLED
            if call_id in self.interrupted_calls
            else EventType.RUNTIME_CALL_FAILED
        )
        await self._finish_call(
            call_id,
            make_event(
                run_id=run.run_id,
                session_id=run.session_id,
                provider=Provider.OPENCODE,
                native_type="process/exit",
                normalized_type=terminal,
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
        for call_id in list(self.run_refs):
            await self._fail_call(call_id, message)

    # ----------------------------------------------------------------- helpers

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
