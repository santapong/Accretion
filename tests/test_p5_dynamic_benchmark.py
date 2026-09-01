from __future__ import annotations

import json
import shutil
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from accretion.api.main import app
from accretion.dynamic_benchmark import DynamicWorkflowBenchmarkRunner


def test_frozen_dynamic_gate_reports_benefit_fallback_and_non_inferiority() -> None:
    runner = DynamicWorkflowBenchmarkRunner()
    first = runner.run()
    second = runner.run()

    assert first == second
    assert first.task_count == 12
    assert first.trace_count == 24
    assert [item.treatment.value for item in first.treatments] == ["STATIC", "DYNAMIC"]
    assert {item.cohort.value: item.task_count for item in first.cohorts} == {
        "PREDICTABLE": 4,
        "HETEROGENEOUS": 4,
        "UNCERTAIN": 4,
    }
    assert first.gate.passed
    assert first.gate.research_classification == "POSITIVE"
    assert first.gate.benefit_passed
    assert first.gate.predictable_non_inferiority_passed
    assert first.gate.success_rate_not_regressed
    assert first.gate.safety_invariants_passed
    assert first.gate.static_fallback_operational
    assert first.gate.heterogeneous_uncertain_uplift >= 0.02
    assert first.gate.predictable_uplift >= -0.02
    invalid = [item for item in first.tasks if item.dynamic_invalid_proposal]
    assert len(invalid) == 1
    assert invalid[0].dynamic_fallback_used
    # Literal digests, not `sha256(runner.tasks_path)`: a self-hashing assertion
    # re-derives the expected value from the same bytes the runner read, so it holds
    # even when the frozen corpus is edited. Pinning the constants means any change
    # to the replayed inputs fails here and has to be re-argued.
    assert first.corpus_sha256 == (
        "b411b0573d514a496b81b82e25ccee146b66af7fd990187ede6e7ea4c1c399db"
    )
    assert first.trace_sha256 == (
        "77645b41f35430bb886fae558a6ee684664d87b7adcb755c68a92c3db6dd3616"
    )
    assert first.config_sha256 == (
        "55678342830491bc20ceea16332b6385c3f6afba3f8fd35fee6342d1260da8de"
    )


def test_dynamic_gate_fails_when_research_benefit_is_below_threshold(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dynamic_workflow"
    shutil.copytree(DynamicWorkflowBenchmarkRunner().root, root)
    trace_path = root / "replay-traces.v1.json"
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    static_by_task = {
        trace["task_id"]: trace for trace in payload["traces"] if trace["treatment"] == "STATIC"
    }
    for trace in payload["traces"]:
        if trace["treatment"] != "DYNAMIC":
            continue
        replacement = {**static_by_task[trace["task_id"]], "treatment": "DYNAMIC"}
        if trace["task_id"] == "p5-008":
            replacement.update({"invalid_proposal": True, "fallback_used": True})
        trace.clear()
        trace.update(replacement)
    trace_path.write_text(json.dumps(payload), encoding="utf-8")

    gate = DynamicWorkflowBenchmarkRunner(root).run().gate

    assert not gate.benefit_passed
    assert gate.predictable_non_inferiority_passed
    assert gate.success_rate_not_regressed
    assert gate.safety_invariants_passed
    assert gate.static_fallback_operational
    assert not gate.passed


async def test_dynamic_benchmark_api_is_replay_only() -> None:
    # Enter the app lifespan here rather than relying on another test having
    # initialised the module-level `app` singleton: the session middleware reads
    # `app.state.manager`, which only `lifespan()` sets.
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        summary = await client.get("/api/v2/benchmarks/dynamic")
        replay = await client.post(
            "/api/v2/benchmarks/dynamic/run", json={"execution_source": "REPLAY"}
        )
        live = await client.post(
            "/api/v2/benchmarks/dynamic/run", json={"execution_source": "LIVE"}
        )

    assert summary.status_code == 200
    assert replay.status_code == 200
    assert summary.json() == replay.json()
    assert summary.json()["gate"]["passed"] is True
    assert live.status_code == 400
