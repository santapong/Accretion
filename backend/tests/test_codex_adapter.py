from accretion.providers.base import ProviderEvent
from accretion.providers.codex import CodexAdapter


async def test_codex_notification_mapping() -> None:
    events: list[ProviderEvent] = []

    async def sink(event: ProviderEvent) -> None:
        events.append(event)

    adapter = CodexAdapter(sink)
    adapter._bind("local-1", "thread-1")
    await adapter._dispatch(
        {
            "method": "turn/started",
            "params": {"threadId": "thread-1", "turn": {"id": "turn-1"}},
        }
    )
    await adapter._dispatch(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-1", "status": "interrupted"},
            },
        }
    )
    assert [event.kind for event in events] == ["running", "interrupted"]


async def test_codex_approval_mapping() -> None:
    events: list[ProviderEvent] = []

    async def sink(event: ProviderEvent) -> None:
        events.append(event)

    adapter = CodexAdapter(sink)
    adapter._bind("local-1", "thread-1")
    await adapter._dispatch(
        {
            "id": 9,
            "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "thread-1", "command": "pytest"},
        }
    )
    assert events[0].kind == "approval"
    assert events[0].payload["provider_request_id"] == "9"
