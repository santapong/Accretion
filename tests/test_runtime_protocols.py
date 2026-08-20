from accretion.contracts import EventType
from accretion.runtimes.claude import ClaudeRuntime
from accretion.runtimes.codex import CodexRuntime


def test_codex_stable_notifications_normalize_to_run_lifecycle() -> None:
    assert CodexRuntime._normalize("turn/started", {"turn": {"status": "inProgress"}}) == (
        EventType.RUN_STARTED
    )
    assert CodexRuntime._normalize("turn/completed", {"turn": {"status": "completed"}}) == (
        EventType.RUN_COMPLETED
    )
    assert CodexRuntime._normalize("turn/completed", {"turn": {"status": "interrupted"}}) == (
        EventType.RUN_CANCELLED
    )
    assert CodexRuntime._normalize("turn/completed", {"turn": {"status": "failed"}}) == (
        EventType.RUN_FAILED
    )


def test_claude_stream_json_normalizes_to_run_lifecycle() -> None:
    assert ClaudeRuntime._normalize({"type": "system", "subtype": "init"}) == (
        EventType.RUN_STARTED
    )
    assert ClaudeRuntime._normalize({"type": "assistant"}) == EventType.RUN_PROGRESS
    assert ClaudeRuntime._normalize({"type": "result", "is_error": False}) == (
        EventType.RUN_COMPLETED
    )
    assert ClaudeRuntime._normalize({"type": "result", "is_error": True}) == EventType.RUN_FAILED
