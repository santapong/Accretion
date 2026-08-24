from __future__ import annotations

import hashlib
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from accretion.api.main import app
from accretion.experience_benchmark import ExperienceBenchmarkRunner


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_experience_gate_reports_uplift_and_negative_transfer() -> None:
    runner = ExperienceBenchmarkRunner()
    first = runner.run()
    second = runner.run()

    assert first == second
    assert first.task_count == 20
    assert first.source_count == 50
    assert first.trace_count == 80
    assert first.source_counts == {
        "POSITIVE": 20,
        "NEGATIVE": 10,
        "STALE_INCOMPATIBLE": 20,
    }
    assert [item.treatment.value for item in first.treatments] == [
        "FRESH",
        "SUCCESS_ONLY",
        "SUCCESS_FAILURE",
        "REPLAY",
    ]
    assert first.gate.passed
    assert first.gate.false_accepts_not_increased
    assert first.gate.stale_rejection_rate == 0.95
    assert first.gate.negative_transfer_rate == 0.033333
    assert first.gate.replay_quality_uplift >= 0.03
    assert first.gate.replay_tool_call_reduction >= 0.10
    assert sum(
        len(item.negative_transfer_treatments) for item in first.tasks
    ) == 2
    assert first.corpus_sha256 == digest(runner.tasks_path)
    assert first.source_sha256 == digest(runner.sources_path)
    assert first.trace_sha256 == digest(runner.traces_path)
    assert first.config_sha256 == digest(runner.config_path)


async def test_experience_benchmark_api_is_replay_only() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        summary = await client.get("/api/v2/benchmarks/experience")
        replay = await client.post(
            "/api/v2/benchmarks/experience/run", json={"execution_source": "REPLAY"}
        )
        live = await client.post(
            "/api/v2/benchmarks/experience/run", json={"execution_source": "LIVE"}
        )

    assert summary.status_code == 200
    assert replay.status_code == 200
    assert summary.json() == replay.json()
    assert summary.json()["gate"]["passed"] is True
    assert live.status_code == 400
