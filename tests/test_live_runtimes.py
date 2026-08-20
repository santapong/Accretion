import os
import subprocess
from pathlib import Path

import pytest

from accretion.contracts import EventType, SessionConfig, TaskEnvelope
from accretion.ids import new_id
from accretion.runtimes.claude import ClaudeRuntime
from accretion.runtimes.codex import CodexRuntime

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("ACCRETION_LIVE_PROVIDERS") != "1",
        reason="set ACCRETION_LIVE_PROVIDERS=1 to use signed-in provider sessions",
    ),
]


@pytest.mark.parametrize("runtime", [CodexRuntime(), ClaudeRuntime()])
async def test_live_runtime_produces_terminal_event(runtime: object, tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    run_id = new_id("run")
    session = await runtime.create_session(SessionConfig(run_id=run_id, workspace=tmp_path))  # type: ignore[attr-defined]
    task = TaskEnvelope(
        task_id=new_id("task"),
        project_id=new_id("project"),
        objective="Create no files. Reply with exactly READY.",
    )
    run = await runtime.submit(session, task)  # type: ignore[attr-defined]
    events = [event async for event in runtime.events(run)]  # type: ignore[attr-defined]
    assert events[-1].normalized_type in {EventType.RUN_COMPLETED, EventType.RUN_FAILED}
