from __future__ import annotations

import hashlib
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from accretion.api.main import app
from accretion.search_benchmark import SearchBenchmarkRunner


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_search_replay_produces_monotonic_n_1_2_4_curve() -> None:
    runner = SearchBenchmarkRunner()
    first = runner.run()
    second = runner.run()

    assert first == second
    assert first.task_count == 12
    assert first.candidate_counts == [1, 2, 4]
    assert [item.candidate_count for item in first.curve] == [1, 2, 4]
    assert [item.mean_turns for item in first.curve] == [1, 2, 4]
    assert [item.mean_quality for item in first.curve] == sorted(
        item.mean_quality for item in first.curve
    )
    assert [item.accepted_tasks for item in first.curve] == [8, 10, 12]
    assert {item.provider.value for item in first.provider_comparison} == {
        "CLAUDE",
        "CODEX",
    }
    assert first.null_gain_task_ids
    assert first.corpus_sha256 == digest(runner.tasks_path)
    assert first.trace_sha256 == digest(runner.traces_path)
    assert first.config_sha256 == digest(runner.config_path)
    assert first.corpus_sha256 == (
        "11fcdcfb2a698dec4c7aa00af125345cccfb15efb7edf5441cc29530dde4a63f"
    )
    assert first.trace_sha256 == (
        "ffb2085c69931a6af1881ab0f16c44c0bfc19c30b4d77a740290b6faa42e6810"
    )
    assert first.config_sha256 == (
        "9b910c71729ef6bfef5299cb0b8f22f9c75706268ab59185ca17aacc86c8804a"
    )


async def test_search_benchmark_api_replays_but_rejects_implicit_live_run() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        summary = await client.get("/api/v2/benchmarks/search")
        replay = await client.post(
            "/api/v2/benchmarks/search/run", json={"execution_source": "REPLAY"}
        )
        live = await client.post("/api/v2/benchmarks/search/run", json={"execution_source": "LIVE"})

    assert summary.status_code == 200
    assert replay.status_code == 200
    assert summary.json() == replay.json()
    assert summary.json()["task_count"] == 12
    assert live.status_code == 400
