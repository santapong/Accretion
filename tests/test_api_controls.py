from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from accretion.api.main import SSE_TERMINAL_EVENTS, app
from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    AgentEvent,
    EventType,
    ExecutionMode,
    Provider,
    Run,
    RunState,
    SessionConfig,
    SessionRef,
    TaskEnvelope,
    TaskType,
)
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.runtimes.common import make_event
from accretion.runtimes.fake import FakeCallOutcome, FakeRuntime
from accretion.services.run_manager import RunManager
from accretion.workspace import WorktreeManager


class BlockingSessionRuntime(FakeRuntime):
    def __init__(self, *, scripted_outcomes: list[FakeCallOutcome]) -> None:
        super().__init__(scripted_outcomes=scripted_outcomes)
        self.first_session_started = asyncio.Event()
        self.release_first_session = asyncio.Event()
        self.session_configs: list[SessionConfig] = []

    async def create_session(self, config: SessionConfig) -> SessionRef:
        self.session_configs.append(config)
        if len(self.session_configs) == 1:
            self.first_session_started.set()
            await self.release_first_session.wait()
        return await super().create_session(config)


class ObserveTerminalGapStore(MemoryStore):
    """Expose the interval where terminal state precedes terminal event persistence."""

    def __init__(self) -> None:
        super().__init__()
        self.sse_read_during_gap = asyncio.Event()
        self.observe_gap = False

    async def list_events(self, run_id: str, after: int = 0) -> list[AgentEvent]:
        events = await super().list_events(run_id, after)
        if self.observe_gap:
            self.sse_read_during_gap.set()
        return events


def build_manager(
    tmp_path: Path,
    *,
    store: MemoryStore | None = None,
    runtime: FakeRuntime | None = None,
) -> RunManager:
    return RunManager(
        store=store or MemoryStore(),
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: runtime or FakeRuntime()},
        limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
        live_providers_enabled=False,
    )


def initialize_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Accretion Test"],
        check=True,
    )
    (path / "result.json").write_text('{"ok": false}\n')
    subprocess.run(["git", "-C", str(path), "add", "result.json"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)


async def test_sse_waits_for_durable_terminal_event_before_closing(tmp_path: Path) -> None:
    store = ObserveTerminalGapStore()
    run_manager = build_manager(tmp_path, store=store)
    run = await store.create_run(
        Run(
            run_id=new_id("run"),
            task_id=new_id("task"),
            project_id=new_id("project"),
            provider=Provider.FAKE,
            state=RunState.SUCCEEDED,
            execution_mode=ExecutionMode.DIRECT,
            workflow_template_id="direct-v1",
        )
    )
    store.observe_gap = True

    app.state.manager = run_manager
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response_task = asyncio.create_task(client.get(f"/api/v1/runs/{run.run_id}/events"))
        await asyncio.wait_for(store.sse_read_during_gap.wait(), 2)
        await asyncio.sleep(0)
        assert not response_task.done()

        store.observe_gap = False
        await store.append_event(
            make_event(
                run_id=run.run_id,
                session_id="ses_terminal",
                provider=Provider.DETERMINISTIC,
                native_type="fixture/run-completed",
                normalized_type=EventType.RUN_COMPLETED,
                adapter_version="fixture-v1",
            )
        )
        response = await asyncio.wait_for(response_task, 5)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"normalized_type":"RUN_COMPLETED"' in response.text


async def test_non_loop_projection_endpoints_return_consistent_conflict(
    tmp_path: Path,
) -> None:
    run_manager = build_manager(tmp_path)
    project = await run_manager.create_project("fixture", tmp_path)
    task = await run_manager.create_task(
        project_id=project.project_id,
        objective="Review one bounded result.",
        task_patch={"task_type": TaskType.REVIEW},
    )
    run = await run_manager.store.create_run(
        Run(
            run_id=new_id("run"),
            task_id=task.envelope.task_id,
            project_id=project.project_id,
            provider=Provider.FAKE,
            state=RunState.RUNNING,
            execution_mode=ExecutionMode.DIRECT,
            workflow_template_id="direct-v1",
        )
    )

    app.state.manager = run_manager
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        loop_response = await client.get(f"/api/v1/runs/{run.run_id}/loop")
        assert loop_response.status_code == 409
        loop_body = loop_response.json()
        assert loop_body["code"] == "EXECUTION_MODE_MISMATCH"
        assert "DIRECT/direct-v1" in loop_body["message"]
        assert "LOOP/feedback-loop-v1" in loop_body["message"]

        # A pre-P3 DIRECT fixture has no persisted run graph to project.
        graph_response = await client.get(f"/api/v1/runs/{run.run_id}/graph")
        assert graph_response.status_code == 409
        assert graph_response.json()["code"] == "PROJECTION_UNAVAILABLE"

        missing = await client.get(f"/api/v1/runs/{new_id('run')}/loop")
        assert missing.status_code == 404
        assert missing.json()["code"] == "NOT_FOUND"


async def test_invalid_sse_cursor_uses_error_envelope(tmp_path: Path) -> None:
    run_manager = build_manager(tmp_path)
    run = await run_manager.store.create_run(
        Run(
            run_id=new_id("run"),
            task_id=new_id("task"),
            project_id=new_id("project"),
            provider=Provider.FAKE,
            state=RunState.PENDING,
        )
    )
    app.state.manager = run_manager
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/runs/{run.run_id}/events",
            headers={"Last-Event-ID": "not-an-integer"},
        )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "INVALID_REQUEST"
    assert body["message"] == "Last-Event-ID must be an integer"
    assert body["correlation_id"]


@pytest.mark.acceptance("V01-P4-005")
async def test_sse_query_cursor_replays_only_events_after_snapshot(tmp_path: Path) -> None:
    run_manager = build_manager(tmp_path)
    run = await run_manager.store.create_run(
        Run(
            run_id=new_id("run"),
            task_id=new_id("task"),
            project_id=new_id("project"),
            provider=Provider.FAKE,
            state=RunState.SUCCEEDED,
        )
    )
    for index, event_type in enumerate(
        [EventType.RUN_STARTED, EventType.RUN_PROGRESS, EventType.RUN_COMPLETED],
        start=1,
    ):
        await run_manager.store.append_event(
            make_event(
                run_id=run.run_id,
                session_id="ses_cursor",
                provider=Provider.FAKE,
                native_type=f"fixture/{index}",
                normalized_type=event_type,
                adapter_version="fixture-v1",
            )
        )

    app.state.manager = run_manager
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/runs/{run.run_id}/events?after=1")

    assert response.status_code == 200
    assert "id: 1\n" not in response.text
    assert "id: 2\n" in response.text
    assert "id: 3\n" in response.text


async def test_direct_pause_during_session_creation_resumes_once_to_verified_success(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)

    def write_valid_output(session: SessionRef, _request: TaskEnvelope | object) -> None:
        (session.workspace / "result.json").write_text('{"ok": true}\n')

    runtime = BlockingSessionRuntime(scripted_outcomes=[FakeCallOutcome(hook=write_valid_output)])
    run_manager = build_manager(tmp_path, runtime=runtime)
    project = await run_manager.create_project("fixture", repository)
    task = await run_manager.create_task(
        project_id=project.project_id,
        objective="Review and produce a bounded result.",
        task_patch={
            "task_type": TaskType.REVIEW,
            "required_outputs": [{"path": "result.json", "kind": "json"}],
        },
    )

    app.state.manager = run_manager
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_response = await client.post(
            f"/api/v1/tasks/{task.envelope.task_id}/runs",
            json={"provider": "FAKE"},
        )
        assert start_response.status_code == 202
        run_id = start_response.json()["run_id"]
        initial_background = run_manager.background[run_id]
        await asyncio.wait_for(runtime.first_session_started.wait(), 2)

        try:
            pause_response = await client.post(f"/api/v1/runs/{run_id}/pause")
            assert pause_response.status_code == 200
            assert pause_response.json()["state"] == "PAUSED"
        finally:
            runtime.release_first_session.set()

        await asyncio.wait_for(initial_background, 5)
        paused = await run_manager.store.get_run(run_id)
        paused_events = await run_manager.store.list_events(run_id)
        assert paused is not None and paused.state is RunState.PAUSED
        assert sum(event.normalized_type is EventType.RUN_PAUSED for event in paused_events) == 1
        assert not any(event.normalized_type in SSE_TERMINAL_EVENTS for event in paused_events)
        assert runtime.run_refs == {}

        resume_response = await client.post(f"/api/v1/runs/{run_id}/resume")
        assert resume_response.status_code == 200
        assert resume_response.json()["state"] == "RUNNING"
        resumed_background = run_manager.background[run_id]
        await asyncio.wait_for(resumed_background, 5)

    completed = await run_manager.store.get_run(run_id)
    events = await run_manager.store.list_events(run_id)
    assert completed is not None and completed.state is RunState.SUCCEEDED
    assert sum(event.normalized_type is EventType.RUN_PAUSED for event in events) == 1
    assert sum(event.normalized_type is EventType.RUN_RESUMED for event in events) == 1
    assert sum(event.normalized_type is EventType.RUN_COMPLETED for event in events) == 1
    assert not any(
        event.normalized_type in {EventType.RUN_FAILED, EventType.RUN_CANCELLED} for event in events
    )
    assert len(runtime.session_configs) == 2
    assert runtime.session_configs[1].resume_native_session_id is not None


async def test_templates_trace_and_approvals_endpoints(tmp_path: Path) -> None:
    from accretion.contracts import ApprovalRecord, RiskLevel
    from accretion.ids import new_id as make_id
    from accretion.templates import seed_templates

    store = MemoryStore()
    await seed_templates(store)
    run_manager = build_manager(tmp_path, store=store)
    project = await run_manager.create_project("fixture", tmp_path)
    task = await run_manager.create_task(
        project_id=project.project_id,
        objective="Review one bounded result.",
        task_patch={"task_type": TaskType.REVIEW},
    )
    run = await store.create_run(
        Run(
            run_id=make_id("run"),
            task_id=task.envelope.task_id,
            project_id=project.project_id,
            provider=Provider.FAKE,
            state=RunState.RUNNING,
            execution_mode=ExecutionMode.DIRECT,
            workflow_template_id="direct-v1",
        )
    )
    approval = await store.save_approval(
        ApprovalRecord(
            approval_id=make_id("approval"),
            run_id=run.run_id,
            native_request_id="gate:approve-plan",
            method="accretion/gate",
            summary="Approve the plan.",
        )
    )

    app.state.manager = run_manager
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        templates = await client.get("/api/v1/templates")
        assert templates.status_code == 200
        listed = templates.json()
        assert {item["template_id"] for item in listed} == {
            "direct-v1",
            "feedback-loop-v1",
            "fixed-graph-v1",
            "hybrid-rd-v1",
            "safe-unknown-v1",
        }
        assert all(item["status"] == "VALIDATED" for item in listed)
        assert all("nodes" not in item and "edges" not in item for item in listed)

        bad_filter = await client.get("/api/v1/templates", params={"status": "BOGUS"})
        assert bad_filter.status_code == 400
        assert bad_filter.json()["code"] == "INVALID_REQUEST"

        trace = await client.get(f"/api/v1/runs/{run.run_id}/trace")
        assert trace.status_code == 200
        assert trace.json()["run_id"] == run.run_id

        missing_trace = await client.get(f"/api/v1/runs/{make_id('run')}/trace")
        assert missing_trace.status_code == 404

        pending = await client.get("/api/v1/approvals", params={"status": "PENDING"})
        assert pending.status_code == 200
        assert [item["approval_id"] for item in pending.json()] == [approval.approval_id]

        decision = await client.post(
            f"/api/v1/approvals/{approval.approval_id}/decision",
            json={"decision": "APPROVE"},
        )
        assert decision.status_code == 200
        assert decision.json()["status"] == "APPROVED"

        second = await client.post(
            f"/api/v1/approvals/{approval.approval_id}/decision",
            json={"decision": "DENY"},
        )
        assert second.status_code == 400
        assert "already decided" in second.json()["message"]

        assert RiskLevel.LOW.value == "LOW"
