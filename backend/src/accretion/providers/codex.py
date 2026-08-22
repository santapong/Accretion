from __future__ import annotations

import asyncio
import json
import shutil
from asyncio.subprocess import Process
from datetime import UTC, datetime
from typing import Any

from accretion import __version__
from accretion.models import (
    ApprovalDecision,
    ProviderCapabilities,
    ProviderHealth,
    ProviderName,
)
from accretion.providers.base import (
    EventSink,
    ProviderAdapter,
    ProviderEvent,
    ProviderHistoryItem,
    ProviderProtocolError,
    ProviderUnavailableError,
)


class CodexAdapter(ProviderAdapter):
    name = ProviderName.CODEX

    def __init__(self, event_sink: EventSink, command: str = "codex") -> None:
        super().__init__(event_sink)
        self.command = command
        self._process: Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._request_id = 0
        self._write_lock = asyncio.Lock()
        self._thread_to_local: dict[str, str] = {}
        self._local_to_thread: dict[str, str] = {}
        self._active_turn: dict[str, str] = {}
        self._approval_methods: dict[str, str] = {}
        self._stderr_tail: list[str] = []

    async def health(self) -> ProviderHealth:
        executable = shutil.which(self.command)
        if not executable:
            return ProviderHealth(
                name=self.name,
                available=False,
                detail=f"{self.command!r} was not found on PATH",
                capabilities=ProviderCapabilities(),
            )
        version: str | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                self.command,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await asyncio.wait_for(process.communicate(), timeout=5)
            version = output.decode(errors="replace").strip()
        except (OSError, TimeoutError):
            pass
        return ProviderHealth(name=self.name, available=True, version=version)

    async def discover_history(self) -> list[ProviderHistoryItem]:
        await self._ensure_started()
        items: list[ProviderHistoryItem] = []
        cursor: str | None = None
        while True:
            result = await self._request(
                "thread/list",
                {"cursor": cursor, "limit": 100, "sortKey": "updated_at", "sortDirection": "desc"},
            )
            for thread in result.get("data", []):
                provider_id = thread.get("id")
                if not provider_id:
                    continue
                created = self._timestamp(thread.get("createdAt"))
                updated = self._timestamp(thread.get("updatedAt"), fallback=created)
                items.append(
                    ProviderHistoryItem(
                        provider_session_id=provider_id,
                        title=thread.get("name") or thread.get("preview") or "Codex session",
                        cwd=thread.get("cwd") or "",
                        created_at=created,
                        updated_at=updated,
                    )
                )
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return items

    async def start_session(self, local_session_id: str, cwd: str, prompt: str) -> str:
        await self._ensure_started()
        result = await self._request(
            "thread/start",
            {
                "cwd": cwd,
                "serviceName": "accretion",
                "approvalPolicy": "on-request",
                "sandbox": "workspace-write",
            },
        )
        thread_id = str(result["thread"]["id"])
        self._bind(local_session_id, thread_id)
        await self._start_turn(local_session_id, prompt)
        return thread_id

    async def resume_session(
        self, local_session_id: str, provider_session_id: str, prompt: str | None
    ) -> None:
        await self._ensure_started()
        result = await self._request("thread/resume", {"threadId": provider_session_id})
        thread_id = str(result.get("thread", {}).get("id", provider_session_id))
        self._bind(local_session_id, thread_id)
        if prompt:
            await self._start_turn(local_session_id, prompt)
        else:
            await self.event_sink(
                ProviderEvent(local_session_id, "session_resumed", {"threadId": thread_id})
            )

    async def send_message(self, local_session_id: str, prompt: str) -> None:
        thread_id = self._required_thread(local_session_id)
        active_turn = self._active_turn.get(local_session_id)
        if active_turn:
            await self._request(
                "turn/steer",
                {
                    "threadId": thread_id,
                    "turnId": active_turn,
                    "input": [{"type": "text", "text": prompt}],
                },
            )
        else:
            await self._start_turn(local_session_id, prompt)

    async def interrupt(self, local_session_id: str) -> None:
        thread_id = self._required_thread(local_session_id)
        turn_id = self._active_turn.get(local_session_id)
        if not turn_id:
            raise ProviderProtocolError("Codex session has no active turn")
        await self._request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    async def resolve_approval(
        self,
        local_session_id: str,
        provider_request_id: str,
        decision: ApprovalDecision,
    ) -> None:
        del local_session_id
        request_id = int(provider_request_id)
        if provider_request_id not in self._approval_methods:
            raise ProviderProtocolError("Codex approval request is no longer pending")
        wire_decision = {
            ApprovalDecision.APPROVE: "accept",
            ApprovalDecision.APPROVE_SESSION: "acceptForSession",
            ApprovalDecision.DENY: "decline",
            ApprovalDecision.CANCEL: "cancel",
        }[decision]
        await self._write({"id": request_id, "result": {"decision": wire_decision}})
        self._approval_methods.pop(provider_request_id, None)

    async def close(self) -> None:
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
        for task in (self._reader_task, self._stderr_task):
            if task:
                task.cancel()
        self._process = None

    async def _ensure_started(self) -> None:
        if self._process and self._process.returncode is None:
            return
        if not shutil.which(self.command):
            raise ProviderUnavailableError(f"{self.command!r} was not found on PATH")
        self._process = await asyncio.create_subprocess_exec(
            self.command,
            "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        await self._request(
            "initialize",
            {
                "clientInfo": {
                    "name": "accretion",
                    "title": "Accretion",
                    "version": __version__,
                }
            },
        )
        await self._write({"method": "initialized", "params": {}})

    async def _start_turn(self, local_session_id: str, prompt: str) -> None:
        thread_id = self._required_thread(local_session_id)
        result = await self._request(
            "turn/start",
            {"threadId": thread_id, "input": [{"type": "text", "text": prompt}]},
        )
        turn_id = result.get("turn", {}).get("id")
        if turn_id:
            self._active_turn[local_session_id] = turn_id

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._write({"method": method, "id": request_id, "params": params})
        try:
            return await asyncio.wait_for(future, timeout=30)
        finally:
            self._pending.pop(request_id, None)

    async def _write(self, message: dict[str, Any]) -> None:
        if not self._process or not self._process.stdin:
            raise ProviderUnavailableError("Codex App Server is not running")
        payload = (json.dumps(message, separators=(",", ":")) + "\n").encode()
        async with self._write_lock:
            self._process.stdin.write(payload)
            await self._process.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self._process and self._process.stdout
        try:
            while line := await self._process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                await self._dispatch(message)
        except asyncio.CancelledError:
            raise
        finally:
            error = ProviderUnavailableError(
                "Codex App Server exited"
                + (f": {self._stderr_tail[-1]}" if self._stderr_tail else "")
            )
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)

    async def _read_stderr(self) -> None:
        assert self._process and self._process.stderr
        while line := await self._process.stderr.readline():
            self._stderr_tail.append(line.decode(errors="replace").strip())
            self._stderr_tail = self._stderr_tail[-20:]

    async def _dispatch(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        method = message.get("method")
        if request_id in self._pending and not method:
            future = self._pending[request_id]
            if "error" in message:
                future.set_exception(ProviderProtocolError(str(message["error"])))
            else:
                future.set_result(message.get("result") or {})
            return
        if not method:
            return
        params = message.get("params") or {}
        local_id = self._resolve_local(params)
        if request_id is not None:
            if local_id:
                request_key = str(request_id)
                self._approval_methods[request_key] = method
                await self.event_sink(
                    ProviderEvent(
                        local_id,
                        "approval",
                        {
                            "provider_request_id": request_key,
                            "approval_kind": method,
                            **params,
                        },
                        provider_event_id=f"approval:{request_key}",
                    )
                )
            else:
                await self._write(
                    {"id": request_id, "error": {"code": -32602, "message": "Unknown thread"}}
                )
            return
        if not local_id:
            return
        kind = self._event_kind(method)
        if method == "turn/started":
            turn_id = (params.get("turn") or {}).get("id")
            if turn_id:
                self._active_turn[local_id] = turn_id
        elif method == "turn/completed":
            self._active_turn.pop(local_id, None)
            status = (params.get("turn") or {}).get("status")
            kind = "interrupted" if status == "interrupted" else "completed"
        await self.event_sink(
            ProviderEvent(
                local_id,
                kind,
                {"method": method, **params},
                provider_event_id=self._provider_event_id(method, params),
            )
        )

    def _resolve_local(self, params: dict[str, Any]) -> str | None:
        thread_id = params.get("threadId")
        if not thread_id:
            thread_id = (params.get("thread") or {}).get("id")
        if not thread_id:
            thread_id = (params.get("turn") or {}).get("threadId")
        return self._thread_to_local.get(thread_id) if thread_id else None

    @staticmethod
    def _event_kind(method: str) -> str:
        if method == "turn/started":
            return "running"
        if method.endswith("/delta"):
            return "message_delta"
        if method == "error":
            return "error"
        if method.startswith("item/"):
            return "item"
        return "provider_event"

    @staticmethod
    def _provider_event_id(method: str, params: dict[str, Any]) -> str | None:
        item_id = (params.get("item") or {}).get("id") or params.get("itemId")
        if item_id and not method.endswith("/delta"):
            return f"{method}:{item_id}"
        turn_id = (params.get("turn") or {}).get("id") or params.get("turnId")
        if turn_id and method in {"turn/started", "turn/completed"}:
            return f"{method}:{turn_id}"
        return None

    def _bind(self, local_session_id: str, thread_id: str) -> None:
        self._thread_to_local[thread_id] = local_session_id
        self._local_to_thread[local_session_id] = thread_id

    def _required_thread(self, local_session_id: str) -> str:
        try:
            return self._local_to_thread[local_session_id]
        except KeyError as error:
            raise ProviderProtocolError("Session is not attached to Codex App Server") from error

    @staticmethod
    def _timestamp(value: Any, fallback: datetime | None = None) -> datetime:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, UTC)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return fallback or datetime.now(UTC)
