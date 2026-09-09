"""Pure packet source/evidence checks; no Docker, database, or simulator access."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest
from test_v05_host_lease_probe import Evidence
from v05_sdk_fixtures import Harness

from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.protocol import ErrorOutcome, HeartbeatRequest, ProtocolResponse

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    path = ROOT / "scripts/robotics/m2_host_probe" / (name + ".py")
    spec = importlib.util.spec_from_file_location("probe_" + name, path)
    loaded = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(loaded)
    return loaded


def test_probe_binary_evidence_preserves_partial_non_utf8(tmp_path):
    evidence = Evidence(tmp_path)
    original = b'{"partial":\xff\x00'
    evidence.write("witness.json", {"response": original, "other": [b"", b"\xff"]})
    stored = json.loads((tmp_path / "witness.json").read_bytes())
    assert base64.b64decode(stored["response"]["base64"]) == original
    assert stored["response"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert stored["response"]["size_bytes"] == len(original)
    assert evidence.events[0]["bytes"] == (tmp_path / "witness.json").stat().st_size


def test_context_hashes_exact_copied_watchdog_and_full_package(tmp_path):
    target = tmp_path / "context"
    hashes = module("prepare_context").prepare(ROOT, target)
    assert len(hashes) == 8
    for name, digest in hashes.items():
        assert hashlib.sha256((target / name).read_bytes()).hexdigest() == digest
    assert (target / "accretion/robotics/host/watchdog.py").read_bytes() == (
        ROOT / "src/accretion/robotics/host/watchdog.py"
    ).read_bytes()
    assert b"COPY --chmod=0555 accretion" in (target / "Dockerfile").read_bytes()
    with pytest.raises(FileExistsError):
        module("prepare_context").prepare(ROOT, target)


def test_counter_error_wire_matches_exact_protocol_seal():
    worker = module("worker")
    request = Harness().request(HeartbeatRequest())
    raw = worker.error_response(json.loads(request.model_dump_json()), "LEASE_INVALID")
    parsed = ProtocolResponse.model_validate_json(raw)
    assert parsed == ProtocolResponse.create(request, ErrorOutcome(code=Code.LEASE_INVALID))


@pytest.mark.parametrize("raw", [b"", b"{}", b"12345\n", b"\xff\x00"])
def test_counter_rejects_incomplete_or_oversized_frame(raw):
    with pytest.raises(ValueError):
        module("worker").bounded_line(io.BytesIO(raw), 4)


def test_counter_preserves_exact_complete_frame():
    assert module("worker").bounded_line(io.BytesIO(b"{}\n"), 4) == b"{}"
