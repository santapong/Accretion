from __future__ import annotations

import hashlib
import os
from collections import Counter
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from accretion.api.main import app
from accretion.benchmark import AcrArchRunner, acr_arch_summary, seed_acr_arch
from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import BenchmarkCategory, Provider
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import MemoryStore, PostgresStore
from accretion.runtimes.fake import FakeRuntime
from accretion.services.run_manager import RunManager
from accretion.workspace import WorktreeManager

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.acceptance("V01-BENCH-001", "V01-BENCH-003")
def test_frozen_corpus_has_required_composition_and_reproducible_hashes() -> None:
    runner = AcrArchRunner()
    tasks = runner.tasks()
    assert len(tasks) == 30
    assert Counter(task.category for task in tasks) == {
        BenchmarkCategory.DIRECT_SIMPLE: 5,
        BenchmarkCategory.FEEDBACK_REFINEMENT: 8,
        BenchmarkCategory.PREDICTABLE_GRAPH: 7,
        BenchmarkCategory.HYBRID_ENGINEERING: 7,
        BenchmarkCategory.SAFETY_RECOVERY: 3,
    }
    assert all(len(task.applicable_modes) >= 2 for task in tasks)
    assert digest(runner.tasks_path) == (
        "9251bb918912e73a2dade20189f93cc26cd7bc217a0dea03713ef252843b9dd7"
    )
    assert digest(runner.traces_path) == (
        "2f62f87eaf079914d41f47bea57a4dd04ce469d0e54f1d6a38faa6de0dd6f051"
    )


@pytest.mark.acceptance("V01-BENCH-002")
def test_replay_computes_raw_dimensions_utility_and_regret_for_every_task() -> None:
    run, metrics = AcrArchRunner().replay()
    assert run.scenario_count == len(metrics) == 68
    assert Counter(metric.provider for metric in metrics) == {
        Provider.CLAUDE: 34,
        Provider.CODEX: 34,
    }
    assert len({metric.benchmark_task_id for metric in metrics}) == 30
    assert all(0 <= metric.quality <= 1 for metric in metrics)
    assert all(0 <= metric.cost <= 1 for metric in metrics)
    assert all(0 <= metric.latency <= 1 for metric in metrics)
    assert all(0 <= metric.risk <= 1 for metric in metrics)
    assert all(0 <= metric.human_burden <= 1 for metric in metrics)
    assert all(metric.trace_ref.startswith("evals/acr_arch/") for metric in metrics)
    regrets = {
        task_id: {
            metric.architecture_regret
            for metric in metrics
            if metric.benchmark_task_id == task_id
        }
        for task_id in {metric.benchmark_task_id for metric in metrics}
    }
    assert all(len(values) == 1 for values in regrets.values())
    assert any(next(iter(values)) > 0 for values in regrets.values())


@pytest.mark.acceptance("V01-BENCH-004")
async def test_summary_filters_and_task_detail_api(tmp_path: Path) -> None:
    store = MemoryStore()
    first = await seed_acr_arch(store)
    second = await seed_acr_arch(store)
    assert first == second
    filtered = await acr_arch_summary(
        store,
        provider=Provider.CLAUDE,
        verifier="command-suite",
        selector_version="selector-v1",
    )
    assert filtered.metrics
    assert all(item.provider is Provider.CLAUDE for item in filtered.metrics)
    assert all(item.verifier_id == "command-suite" for item in filtered.metrics)

    app.state.manager = RunManager(
        store=store,
        worktrees=WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts"),
        runtimes={Provider.FAKE: FakeRuntime()},
        limiter=ConcurrencyLimiter(global_limit=1, provider_limit=1, project_limit=1),
        live_providers_enabled=False,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        summary = await client.get(
            "/api/v1/benchmarks/acr-arch?mode=LOOP&task_type=IMPLEMENT"
        )
        assert summary.status_code == 200
        assert summary.json()["metrics"]
        assert {item["mode"] for item in summary.json()["metrics"]} == {"LOOP"}
        detail = await client.get("/api/v1/benchmarks/acr-arch/tasks/acr-001")
        assert detail.status_code == 200
        assert detail.json()["task"]["benchmark_task_id"] == "acr-001"
        replay = await client.post(
            "/api/v1/benchmarks/acr-arch/run", json={"execution_source": "REPLAY"}
        )
        assert replay.status_code == 201
        live = await client.post(
            "/api/v1/benchmarks/acr-arch/run", json={"execution_source": "LIVE"}
        )
        assert live.status_code == 400


@pytest.mark.integration
@pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set")
async def test_postgres_benchmark_round_trip() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    try:
        run = await seed_acr_arch(store)
        tasks = await store.list_benchmark_tasks()
        metrics = await store.list_architecture_metrics(run.benchmark_run_id)
        assert len(tasks) == 30
        assert len(metrics) == 68
        assert (await store.list_benchmark_runs(1))[0] == run
    finally:
        await engine.dispose()
