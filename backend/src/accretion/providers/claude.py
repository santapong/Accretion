from __future__ import annotations

import asyncio
import dataclasses
import json
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

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


class ClaudeAdapter(ProviderAdapter):
    name = ProviderName.CLAUDE

    def __init__(
        self,
        event_sink: EventSink,
        projects_dir: Path,
        client_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        super().__init__(event_sink)
        self.projects_dir = projects_dir
        self._client_factory = client_factory
        self._clients: dict[str, Any] = {}
        self._receive_tasks: dict[str, asyncio.Task[None]] = {}
        self._provider_ids: dict[str, str] = {}
        self._approval_waiters: dict[str, asyncio.Future[ApprovalDecision]] = {}

    async def health(self) -> ProviderHealth:
        available = shutil.which("claude") is not None
        version: str | None = None
        detail: str | None = None
        try:
            import claude_agent_sdk  # type: ignore[import-not-found]

            sdk_version = getattr(claude_agent_sdk, "__version__", None)
        except ImportError:
            sdk_version = None
            available = False
            detail = "claude-agent-sdk is not installed"
        if shutil.which("claude"):
            try:
                process = await asyncio.create_subprocess_exec(
                    "claude",
                    "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                output, _ = await asyncio.wait_for(process.communicate(), timeout=5)
                version = output.decode(errors="replace").strip()
            except (OSError, TimeoutError):
                pass
        if sdk_version:
            version = f"{version or 'Claude Code'} · SDK {sdk_version}"
        return ProviderHealth(
            name=self.name,
            available=available,
            version=version,
            detail=detail,
            capabilities=ProviderCapabilities(),
        )

    async def discover_history(self) -> list[ProviderHistoryItem]:
        if not self.projects_dir.is_dir():
            return []
        items: dict[str, ProviderHistoryItem] = {}
        for path in self.projects_dir.rglob("*.jsonl"):
            item = self._read_history_file(path)
            if item and (
                item.provider_session_id not in items
                or item.updated_at > items[item.provider_session_id].updated_at
            ):
                items[item.provider_session_id] = item
        return sorted(items.values(), key=lambda item: item.updated_at, reverse=True)

    async def start_session(self, local_session_id: str, cwd: str, prompt: str) -> str:
        synthetic_id = f"pending:{local_session_id}"
        await self._connect(local_session_id, cwd=cwd, resume=None)
        self._provider_ids[local_session_id] = synthetic_id
        await self._query(local_session_id, prompt)
        return synthetic_id

    async def resume_session(
        self, local_session_id: str, provider_session_id: str, prompt: str | None
    ) -> None:
        await self._connect(local_session_id, cwd=None, resume=provider_session_id)
        self._provider_ids[local_session_id] = provider_session_id
        if prompt:
            await self._query(local_session_id, prompt)
        else:
            await self.event_sink(
                ProviderEvent(
                    local_session_id,
                    "session_resumed",
                    {"provider_session_id": provider_session_id},
                )
            )

    async def send_message(self, local_session_id: str, prompt: str) -> None:
        await self._query(local_session_id, prompt)

    async def interrupt(self, local_session_id: str) -> None:
        client = self._required_client(local_session_id)
        await client.interrupt()
        for request_id, waiter in list(self._approval_waiters.items()):
            if request_id.startswith(f"{local_session_id}:") and not waiter.done():
                waiter.set_result(ApprovalDecision.CANCEL)

    async def resolve_approval(
        self,
        local_session_id: str,
        provider_request_id: str,
        decision: ApprovalDecision,
    ) -> None:
        if not provider_request_id.startswith(f"{local_session_id}:"):
            raise ProviderProtocolError("Approval does not belong to this Claude session")
        waiter = self._approval_waiters.get(provider_request_id)
        if not waiter or waiter.done():
            raise ProviderProtocolError("Claude approval request is no longer pending")
        waiter.set_result(decision)

    async def close(self) -> None:
        for task in self._receive_tasks.values():
            task.cancel()
        for client in self._clients.values():
            try:
                await client.disconnect()
            except Exception:  # pragma: no cover - provider shutdown must be best effort
                pass
        self._receive_tasks.clear()
        self._clients.clear()

    async def _connect(self, local_session_id: str, cwd: str | None, resume: str | None) -> None:
        try:
            from claude_agent_sdk import (  # type: ignore[import-not-found]
                ClaudeAgentOptions,
                ClaudeSDKClient,
            )
        except ImportError as error:
            raise ProviderUnavailableError("claude-agent-sdk is not installed") from error

        options = ClaudeAgentOptions(
            cwd=cwd,
            resume=resume,
            include_partial_messages=True,
            can_use_tool=self._permission_handler(local_session_id),
        )
        factory = self._client_factory or (lambda value: ClaudeSDKClient(options=value))
        client = factory(options)
        await client.connect()
        self._clients[local_session_id] = client
        self._receive_tasks[local_session_id] = asyncio.create_task(
            self._receive(local_session_id, client)
        )

    async def _query(self, local_session_id: str, prompt: str) -> None:
        client = self._required_client(local_session_id)
        await client.query(prompt)
        await self.event_sink(ProviderEvent(local_session_id, "running", {"prompt": prompt}))

    async def _receive(self, local_session_id: str, client: Any) -> None:
        try:
            async for message in client.receive_messages():
                payload = self._serialize(message)
                class_name = message.__class__.__name__
                session_id = payload.get("session_id") or payload.get("sessionId")
                if session_id and session_id != self._provider_ids.get(local_session_id):
                    self._provider_ids[local_session_id] = str(session_id)
                    await self.event_sink(
                        ProviderEvent(
                            local_session_id,
                            "provider_session",
                            {"provider_session_id": str(session_id)},
                            provider_event_id=f"provider-session:{session_id}",
                        )
                    )
                if class_name == "AssistantMessage":
                    kind = "message"
                elif class_name in {"StreamEvent", "PartialAssistantMessage"}:
                    kind = "message_delta"
                elif class_name == "ResultMessage":
                    kind = "error" if payload.get("is_error") else "completed"
                elif class_name == "SystemMessage":
                    kind = "provider_event"
                else:
                    kind = "provider_event"
                await self.event_sink(
                    ProviderEvent(local_session_id, kind, {"message_type": class_name, **payload})
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self.event_sink(ProviderEvent(local_session_id, "error", {"message": str(error)}))

    def _permission_handler(self, local_session_id: str) -> Callable[..., Any]:
        async def can_use_tool(
            tool_name: str,
            input_data: dict[str, Any],
            *_args: Any,
            **_kwargs: Any,
        ) -> Any:
            from claude_agent_sdk import (  # type: ignore[import-not-found]
                PermissionResultAllow,
                PermissionResultDeny,
            )

            request_id = f"{local_session_id}:{uuid4()}"
            waiter: asyncio.Future[ApprovalDecision] = asyncio.get_running_loop().create_future()
            self._approval_waiters[request_id] = waiter
            await self.event_sink(
                ProviderEvent(
                    local_session_id,
                    "approval",
                    {
                        "provider_request_id": request_id,
                        "approval_kind": f"tool:{tool_name}",
                        "tool_name": tool_name,
                        "input": input_data,
                    },
                    provider_event_id=f"approval:{request_id}",
                )
            )
            try:
                decision = await waiter
            finally:
                self._approval_waiters.pop(request_id, None)
            if decision in {ApprovalDecision.APPROVE, ApprovalDecision.APPROVE_SESSION}:
                return PermissionResultAllow(updated_input=input_data)
            return PermissionResultDeny(
                message="Denied from Accretion",
                interrupt=decision == ApprovalDecision.CANCEL,
            )

        return can_use_tool

    def _required_client(self, local_session_id: str) -> Any:
        try:
            return self._clients[local_session_id]
        except KeyError as error:
            raise ProviderProtocolError("Session is not attached to Claude Agent SDK") from error

    @classmethod
    def _serialize(cls, value: Any) -> Any:
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return {key: cls._serialize(item) for key, item in dataclasses.asdict(value).items()}
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return {str(key): cls._serialize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._serialize(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    @staticmethod
    def _read_history_file(path: Path) -> ProviderHistoryItem | None:
        session_id = path.stem
        cwd = ""
        title = "Claude session"
        created: datetime | None = None
        updated: datetime | None = None
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    session_id = str(record.get("sessionId") or session_id)
                    cwd = str(record.get("cwd") or cwd)
                    stamp = ClaudeAdapter._parse_timestamp(record.get("timestamp"))
                    if stamp:
                        created = created or stamp
                        updated = stamp
                    if title == "Claude session" and record.get("type") == "user":
                        content = (record.get("message") or {}).get("content")
                        if isinstance(content, str):
                            title = content.strip().replace("\n", " ")[:120] or title
                        elif isinstance(content, list):
                            text = next(
                                (
                                    block.get("text", "")
                                    for block in content
                                    if isinstance(block, dict) and block.get("type") == "text"
                                ),
                                "",
                            )
                            title = text.strip().replace("\n", " ")[:120] or title
        except OSError:
            return None
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        return ProviderHistoryItem(
            provider_session_id=session_id,
            title=title,
            cwd=cwd,
            created_at=created or modified,
            updated_at=updated or modified,
        )

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
