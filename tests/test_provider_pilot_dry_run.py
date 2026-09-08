"""Actual fake routing exports, denial-before-submit and tamper witnesses."""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
from pathlib import Path
from typing import Any, NoReturn

import pytest
from jsonschema import ValidationError as SchemaError

from accretion import provider_pilot as pilot
from accretion.routing import bootstrap
from accretion.routing.promotion import PromotionService
from accretion.runtimes.claude import ClaudeRuntime
from accretion.runtimes.codex import CodexRuntime
from accretion.runtimes.opencode import OpencodeRuntime


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())  # type: ignore[no-any-return]


def write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value) + "\n")


def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
    raise AssertionError("a forbidden learned/budget/provider/network path was entered")


@pytest.fixture(scope="module")
def evidence(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("pilot") / "actual-evidence"
    with pytest.MonkeyPatch.context() as patch:
        for name in (
            "ColdStartScorer",
            "ShadowRoutingHook",
            "BranchedRolloutExecutor",
            "LedgerRegistry",
            "GuardedBandit",
            "ExplorationSettlement",
        ):
            patch.setattr(bootstrap, name, forbidden)
        for runtime in (ClaudeRuntime, CodexRuntime, OpencodeRuntime, PromotionService):
            patch.setattr(runtime, "__init__", forbidden)
        patch.setattr(socket.socket, "connect", forbidden)
        patch.setattr(socket, "create_connection", forbidden)
        asyncio.run(pilot.run_dry_run(output))
    return output


def test_three_outcomes_are_real_independent_native_records(evidence: Path) -> None:
    report = pilot.validate_export(evidence)
    assert report["cases"] == {
        "valid-output": "PASS",
        "incorrect-output": "FAIL",
        "missing-required-evidence": "INCONCLUSIVE",
    }
    assert report["provider_calls"] == 0
    assert report["live_execution_authority"] == "NOT_AUTHORIZED"
    for case, status in report["cases"].items():
        native = evidence / "native" / case
        record = read(evidence / "records" / f"{case}.json")
        result = read(native / "independent-verification.json")
        receipt = read(native / "receipt.json")
        session = read(native / "session.json")
        assert result["status"] == status
        assert record["verification"]["record_id"] == result["contract_id"]
        assert record["execution"]["producer_session_id"] == session["session_id"]
        assert read(native / "verification-input.json")["verifier_session_ids"] == {
            "output-contract": None,
        }
        dispatches = json.loads((native / "dispatch-events.json").read_text())
        events = json.loads((native / "events.json").read_text())
        dispatch = next(
            row for row in dispatches if row["native_type"] == "accretion/routing/dispatch"
        )
        assert dispatch["payload"]["receipt_id"] == receipt["contract_id"]
        assert dispatch["sequence"] < events[0]["sequence"]
        assert record["accounting"]["local_compute_ms"] is None
        assert record["accounting"]["currency"] is None
        assert "source_status_porcelain" in report


@pytest.mark.parametrize(
    "field,value",
    [
        ("routing_mode", "AUTO"),
        ("routing_mode", "SHADOW"),
        ("allowed_decision_kinds", ["EXPLORE"]),
        ("allowed_runtime_providers", ["CODEX"]),
        ("selected_agent_tools", ["tool-1"]),
        ("exploration_enabled", True),
        ("external_network_enabled", True),
    ],
)
async def test_invalid_recipe_refuses_before_runtime_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any,
) -> None:
    recipe = read(pilot.STUDY / "fake-instrumentation-manifest.json")
    recipe[field] = value
    monkeypatch.setattr(pilot, "FakeRuntime", forbidden)
    with pytest.raises(pilot.PilotRefused):
        await pilot.run_dry_run(tmp_path / "denial", recipe)
    denial = read(tmp_path / "denial/admission-denied.json")
    assert denial["executions"] == denial["provider_calls"] == 0
    assert denial["state"] == "REFUSED_BEFORE_RUNTIME_CONSTRUCTION"


@pytest.mark.parametrize(
    "key,value",
    [
        ("ACCRETION_NODE_ROUTING_MODE", "AUTO"),
        ("ACCRETION_ENABLE_LIVE_PROVIDERS", "true"),
        ("ACCRETION_LIVE_PROVIDERS", "1"),
    ],
)
async def test_ambient_mode_cannot_enable_a_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
) -> None:
    monkeypatch.setenv(key, value)
    monkeypatch.setattr(pilot, "FakeRuntime", forbidden)
    with pytest.raises(pilot.PilotRefused):
        await pilot.run_dry_run(tmp_path / "denial")


@pytest.mark.parametrize(
    "field,value",
    [
        (("configuration", "id"), "different-configuration"),
        (("configuration", "model"), "different-model"),
        (("configuration", "runtime_version"), "different-runtime"),
        (("routing", "receipt_hash"), "0" * 64),
        (("verification", "execution_instance_id"), "different-execution"),
        (("verification", "required_claim_coverage"), 0.5),
        (("verification", "evidence_hashes"), []),
        (("verification", "independent"), False),
        (("verification", "deterministic"), False),
        (("execution", "status"), "CANCELLED"),
        (("input_revision",), "different-revision"),
        (("accounting", "provider_calls"), 1),
        (("measurement_status",), "INCONCLUSIVE"),
    ],
)
def test_exported_claims_cannot_diverge_from_native_evidence(
    evidence: Path,
    field: tuple[str, ...],
    value: Any,
) -> None:
    record = read(evidence / "records/valid-output.json")
    target = record
    for key in field[:-1]:
        target = target[key]
    target[field[-1]] = value
    with pytest.raises((ValueError, SchemaError)):
        pilot.validate_record(
            record, evidence / "native/valid-output", evidence / "artifacts/valid-output.json"
        )


def test_missing_accounting_is_not_a_complete_measurement(evidence: Path) -> None:
    record = read(evidence / "records/valid-output.json")
    del record["accounting"]
    with pytest.raises(SchemaError):
        pilot.validate_record(
            record, evidence / "native/valid-output", evidence / "artifacts/valid-output.json"
        )


@pytest.mark.parametrize(
    "name,field,value",
    [
        ("events", "normalized_type", "RUNTIME_CALL_FAILED"),
        ("events", "session_id", "different-session"),
        ("events", "adapter_version", "different-adapter"),
        ("dispatch-events", "sequence", 100),
    ],
)
def test_native_runtime_terminal_and_dispatch_joins(
    evidence: Path,
    tmp_path: Path,
    name: str,
    field: str,
    value: Any,
) -> None:
    native = tmp_path / "native"
    shutil.copytree(evidence / "native/valid-output", native)
    rows = json.loads((native / f"{name}.json").read_text())
    rows[-1][field] = value
    write(native / f"{name}.json", rows)
    with pytest.raises(pilot.PilotRefused):
        pilot.validate_record(
            read(evidence / "records/valid-output.json"),
            native,
            evidence / "artifacts/valid-output.json",
        )


@pytest.mark.parametrize(
    "name,key,value",
    [
        ("configuration", "labels", {"tampered": "yes"}),
        ("receipt", "selected_configuration_hash", "0" * 64),
        ("node-contract", "objective", "tampered objective"),
        ("verification-spec", "labels", {"tampered": "yes"}),
        ("independent-verification", "status", "FAIL"),
    ],
)
def test_native_contract_seals_are_actually_validated(
    evidence: Path,
    tmp_path: Path,
    name: str,
    key: str,
    value: Any,
) -> None:
    native = tmp_path / "native"
    shutil.copytree(evidence / "native/valid-output", native)
    raw = read(native / f"{name}.json")
    raw[key] = value
    write(native / f"{name}.json", raw)
    with pytest.raises(ValueError):
        pilot.validate_record(
            read(evidence / "records/valid-output.json"),
            native,
            evidence / "artifacts/valid-output.json",
        )


def test_producer_session_cannot_be_relabelled_independent(evidence: Path, tmp_path: Path) -> None:
    native = tmp_path / "native"
    shutil.copytree(evidence / "native/valid-output", native)
    inputs = read(native / "verification-input.json")
    inputs["verifier_session_ids"]["output-contract"] = read(native / "session.json")["session_id"]
    write(native / "verification-input.json", inputs)
    with pytest.raises(pilot.PilotRefused):
        pilot.validate_record(
            read(evidence / "records/valid-output.json"),
            native,
            evidence / "artifacts/valid-output.json",
        )


def test_missing_evidence_cannot_be_relabelled_complete(evidence: Path) -> None:
    record = read(evidence / "records/missing-required-evidence.json")
    record["measurement_status"] = "COMPLETE_FAKE_RECORD"
    with pytest.raises(pilot.PilotRefused):
        pilot.validate_record(
            record,
            evidence / "native/missing-required-evidence",
            evidence / "artifacts/missing-required-evidence.json",
        )


def test_artifact_and_manifest_corruption_are_detected(evidence: Path, tmp_path: Path) -> None:
    artifact = tmp_path / "changed.json"
    artifact.write_text('{"different": true}')
    with pytest.raises(pilot.PilotRefused):
        pilot.validate_record(
            read(evidence / "records/valid-output.json"), evidence / "native/valid-output", artifact
        )
    copied = tmp_path / "export"
    shutil.copytree(evidence, copied)
    (copied / "extra.json").write_text("{}")
    with pytest.raises(pilot.PilotRefused, match="manifest"):
        pilot.validate_export(copied)


async def test_existing_evidence_is_never_overwritten(evidence: Path) -> None:
    before = (evidence / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        await pilot.run_dry_run(evidence)
    assert (evidence / "manifest.json").read_bytes() == before


async def test_interrupted_case_keeps_evidence_and_never_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_calls = 0
    guards: list[float | None] = []
    real_timeout = asyncio.timeout

    async def stuck_case(*_args: object, **_kwargs: object) -> dict[str, Any]:
        nonlocal case_calls
        case_calls += 1
        await asyncio.Event().wait()
        raise AssertionError("the per-case timeout must interrupt this case")

    def short_timeout(seconds: float | None) -> asyncio.Timeout:
        guards.append(seconds)
        return real_timeout(0.01 if seconds == 30 else 1)

    monkeypatch.setattr(pilot, "_case", stuck_case)
    monkeypatch.setattr(pilot.asyncio, "timeout", short_timeout)
    with pytest.raises(TimeoutError):
        await pilot.run_dry_run(tmp_path / "stopped")
    failure = read(tmp_path / "stopped/failure.json")
    assert case_calls == 1
    assert guards == [120, 30]
    assert failure["automatic_retry"] is False
    assert failure["completed_case_count"] == 0
