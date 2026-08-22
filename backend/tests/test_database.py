from pathlib import Path

from accretion.database import Database
from accretion.models import Approval, ApprovalDecision, ProviderName, Session, TimelineEvent


async def test_database_session_event_and_approval_round_trip(tmp_path: Path) -> None:
    database = Database(tmp_path / "state" / "accretion.db")
    await database.initialize()
    session = Session(provider=ProviderName.CODEX, title="Test", cwd=str(tmp_path))
    await database.create_session(session)

    event = await database.add_event(
        TimelineEvent(session_id=session.id, kind="message", payload={"text": "hello"})
    )
    approval = await database.create_approval(
        Approval(
            session_id=session.id,
            provider_request_id="42",
            kind="command",
            payload={"command": "pytest"},
        )
    )
    resolved = await database.resolve_approval(approval.id, ApprovalDecision.APPROVE)
    detail = await database.get_session_detail(session.id)

    assert event.id == 1
    assert detail is not None
    assert detail.events[0].payload == {"text": "hello"}
    assert resolved is not None
    assert resolved.status == "approved"


async def test_duplicate_provider_event_is_ignored(tmp_path: Path) -> None:
    database = Database(tmp_path / "accretion.db")
    await database.initialize()
    session = Session(provider=ProviderName.CODEX, title="Test", cwd=str(tmp_path))
    await database.create_session(session)
    for _ in range(2):
        await database.add_event(
            TimelineEvent(
                session_id=session.id,
                kind="item",
                provider_event_id="item:one",
            )
        )
    assert len(await database.list_events(session.id)) == 1
