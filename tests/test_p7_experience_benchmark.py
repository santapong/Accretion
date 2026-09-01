from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from accretion.api.main import app
from accretion.experience_benchmark import ExperienceBenchmarkRunner


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
    # Literal digests, not `sha256(runner.tasks_path)`: a self-hashing assertion
    # re-derives the expected value from the same bytes the runner read, so it holds
    # even when the frozen corpus is edited. Pinning the constants means any change
    # to the replayed inputs fails here and has to be re-argued.
    assert first.corpus_sha256 == (
        "4913e7d6d7fc5c676a009ecee328f9e13d225d02b67fdc846ada5caefa3917ff"
    )
    assert first.source_sha256 == (
        "968898ea94cb9d1633680ab9a80c4ca92e3b975d5c629069458d851467b713b3"
    )
    assert first.trace_sha256 == (
        "38f1c0b5a1832b8472c63d87ad20a825fd83bca05d3a306d4c087372889ed7a9"
    )
    assert first.config_sha256 == (
        "42c21144b551edaaaed08d6976807e771da82b055c0455678b9b78c02531be9c"
    )


async def test_experience_benchmark_api_is_replay_only() -> None:
    # Enter the app lifespan here rather than relying on another test having
    # initialised the module-level `app` singleton: the session middleware reads
    # `app.state.manager`, which only `lifespan()` sets.
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
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
