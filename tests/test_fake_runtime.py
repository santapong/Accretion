from pathlib import Path

from accretion.contracts import EventType, SessionConfig, TaskEnvelope
from accretion.ids import new_id
from accretion.runtimes.fake import FakeRuntime


async def test_fake_runtime_emits_normalized_lifecycle(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    run_id = new_id("run")
    session = await runtime.create_session(SessionConfig(run_id=run_id, workspace=tmp_path))
    task = TaskEnvelope(
        task_id=new_id("task"),
        project_id=new_id("project"),
        objective="exercise the fake runtime",
    )
    run = await runtime.submit(session, task)
    events = [event async for event in runtime.events(run)]
    assert [event.normalized_type for event in events] == [
        EventType.RUN_STARTED,
        EventType.RUN_PROGRESS,
        EventType.RUN_COMPLETED,
    ]
    assert all(event.run_id == run_id for event in events)
