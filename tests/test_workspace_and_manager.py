import asyncio
import subprocess
from pathlib import Path

from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import Provider, RunState
from accretion.persistence.store import MemoryStore
from accretion.runtimes.fake import FakeRuntime
from accretion.services.run_manager import RunManager
from accretion.workspace import WorktreeManager


def initialize_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Accretion Test"], check=True)
    (path / "README.md").write_text("fixture\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)


async def test_worktrees_are_isolated_and_successful_run_completes(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_repository(repository)
    store = MemoryStore()
    worktrees = WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts")
    manager = RunManager(
        store=store,
        worktrees=worktrees,
        runtimes={Provider.FAKE: FakeRuntime(step_delay=0.01)},
        limiter=ConcurrencyLimiter(global_limit=2, provider_limit=2, project_limit=2),
        live_providers_enabled=False,
    )
    project = await manager.create_project("fixture", repository)
    task = await manager.create_task(
        project_id=project.project_id,
        objective="complete deterministically",
        task_patch={},
    )
    first = await manager.start_run(task.envelope.task_id, Provider.FAKE)
    second = await manager.start_run(task.envelope.task_id, Provider.FAKE)
    await asyncio.gather(manager.background[first.run_id], manager.background[second.run_id])
    first_result = await store.get_run(first.run_id)
    second_result = await store.get_run(second.run_id)
    assert first_result and first_result.state == RunState.SUCCEEDED
    assert second_result and second_result.state == RunState.SUCCEEDED
    assert store.leases[first_result.workspace_lease_id].path != store.leases[
        second_result.workspace_lease_id
    ].path
    assert [event.normalized_type.value for event in await store.list_events(first.run_id)] == [
        "RUN_CREATED",
        "RUN_STARTED",
        "RUN_PROGRESS",
        "RUN_COMPLETED",
    ]
