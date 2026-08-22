from accretion.contracts import (
    AgentEvent,
    EventType,
    Project,
    Provider,
    Run,
    RunState,
    Task,
    TaskEnvelope,
)
from accretion.ids import new_id
from accretion.persistence.side_effects import MemorySideEffectLedger, SideEffectStatus
from accretion.persistence.store import MemoryStore


async def test_event_sequences_are_monotonic() -> None:
    store = MemoryStore()
    project = Project(project_id=new_id("project"), name="fixture", repository_path=".")
    await store.create_project(project)
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"), project_id=project.project_id, objective="test"
        )
    )
    await store.create_task(task)
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
    )
    await store.create_run(run)
    for index in range(3):
        await store.append_event(
            AgentEvent(
                event_id=new_id("event"),
                run_id=run.run_id,
                session_id="ses_fixture",
                provider=Provider.FAKE,
                native_type=f"fake/{index}",
                normalized_type=EventType.RUN_PROGRESS,
                correlation_id=run.run_id,
                adapter_version="test",
            )
        )
    assert [event.sequence for event in await store.list_events(run.run_id)] == [1, 2, 3]
    assert (await store.get_run(run.run_id)).last_sequence == 3  # type: ignore[union-attr]


async def test_side_effect_intent_is_idempotent_and_uncertain_work_is_not_retried() -> None:
    ledger = MemorySideEffectLedger()
    first, created = await ledger.record_intent(
        run_id=new_id("run"),
        idempotency_key="deploy:fixture:1",
        capability_id="deploy.test",
        payload={"target": "fixture"},
    )
    duplicate, duplicate_created = await ledger.record_intent(
        run_id=first.run_id,
        idempotency_key="deploy:fixture:1",
        capability_id="deploy.test",
        payload={"target": "fixture"},
    )
    assert created is True
    assert duplicate_created is False
    assert duplicate.operation_id == first.operation_id
    uncertain = await ledger.reconcile_uncertain()
    assert uncertain[0].status == SideEffectStatus.UNKNOWN
