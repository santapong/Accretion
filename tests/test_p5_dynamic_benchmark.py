from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from accretion.api.main import app
from accretion.dynamic_benchmark import DynamicWorkflowBenchmarkRunner


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    assert first.corpus_sha256 == digest(runner.tasks_path)
    assert first.trace_sha256 == digest(runner.traces_path)
    assert first.config_sha256 == digest(runner.config_path)


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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
