"""Gate regressions using synthetic reports, never simulation acceptance claims."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from accretion import acceptance as gate

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ROOT / "docs/sdd/Accretion_SDD_v0.5.md"
FROZEN = ROOT / "docs/sdd/future/v0.4-v1.0/02_SDDS/Accretion_SDD_v0.5.md"
COMMIT, TREE = "a" * 40, "b" * 40
NOW = datetime(2026, 9, 9, tzinfo=UTC)


def test_promotion_preserves_all_original_requirements_and_frozen_source() -> None:
    source = (
        FROZEN.read_text().split("## 22. Release acceptance criteria", 1)[1].split("## 23.", 1)[0]
    )
    original = re.findall(r"^- \[ \] (.+)$", source, re.MULTILINE)
    rows = [c for c in gate.load_criteria().values() if c.release == "v0.5"]
    assert len(rows) == 30
    assert [c.id for c in rows] == [f"AC5-{n:03d}" for n in range(1, 31)]
    assert [c.text for c in rows] == original
    assert {c.priority for c in rows} == {"MUST"}
    assert all(c.required_evidence for c in rows)
    assert hashlib.sha256(FROZEN.read_bytes()).hexdigest() == (
        "90d8ca2d909288f76d619f6db908e0fc87d8d0b286af9e1900361d3a0a3b2e02"
    )


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("| AC5-001 |", "| AC5-999 |", "unknown v0.5 criterion"),
        ("| AC5-001 |", "| AC5-002 |", "duplicate criterion"),
        ("| AC5-001 |", "| AC5-1 |", "malformed v0.5 criterion"),
        ("| AC5-001 |", "| AC6-001 |", "malformed v0.5 criterion"),
        ("| MUST | M0 |", "| SHOULD | M0 |", "malformed v0.5 criterion"),
        ("| MUST | M0 |", "| MUST | M10 |", "malformed v0.5 criterion"),
        ("| M0 | A |", "| M0 | X |", "malformed v0.5 criterion"),
        ("| M0 | A |", "| M0 | A+A |", "duplicate witness class"),
    ],
)
def test_bad_registration_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old: str,
    new: str,
    message: str,
) -> None:
    active = tmp_path / "v05.md"
    active.write_text(ACTIVE.read_text().replace(old, new, 1))
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "SDDS", {"v0.5": active})
    with pytest.raises(SystemExit, match=message):
        gate.load_criteria()


@pytest.mark.parametrize("remove_all", [False, True])
def test_missing_rows_cannot_become_empty_green_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remove_all: bool,
) -> None:
    active = tmp_path / "v05.md"
    active.write_text(
        "\n".join(
            line
            for line in ACTIVE.read_text().splitlines()
            if not line.startswith("| AC5-" if remove_all else "| AC5-030 |")
        )
    )
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "SDDS", {"v0.5": active})
    with pytest.raises(SystemExit, match="no acceptance criteria|missing v0.5 criteria"):
        gate.load_criteria()


def test_owner_changes_do_not_rename_criterion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = tmp_path / "v05.md"
    active.write_text(
        ACTIVE.read_text().replace("| AC5-001 | MUST | M0 |", "| AC5-001 | MUST | M6 |")
    )
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "SDDS", {"v0.5": active})
    assert gate.load_criteria()["AC5-001"].stage == "v0.5-M6"


def test_thirty_attributable_deferrals_are_not_thirty_passes() -> None:
    criteria = gate.load_criteria()
    assert gate.apply_policy(criteria) == []
    pending = [c for c in criteria.values() if c.release == "v0.5"]
    assert {gate.classify(c) for c in pending} == {"NOT_YET_DUE"}
    assert all(c.reason and (ROOT / c.issue).is_file() for c in pending)
    assert sum(c.in_scope for c in criteria.values()) == 167
    errors = gate.v05_release_evidence_errors(criteria, None, COMMIT, TREE, now=NOW)
    assert sum("not_yet_due cannot qualify" in error for error in errors) == 30
    assert "v0.5 release requires a composite evidence manifest" in errors


def test_deferral_cannot_hide_a_failing_claim() -> None:
    criterion = gate.load_criteria()["AC5-001"]
    criterion.verification = "not_yet_due"
    criterion.tests, criterion.outcomes = ["test::real_failure"], ["failed"]
    assert gate.classify(criterion) == "FAILING"


def test_deferral_only_stage_is_not_a_green_acceptance_result() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_acceptance.py", "--stage", "v0.5-M0", "--no-tests"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "all deferred" in result.stderr
    assert "PASS" not in result.stdout


@pytest.mark.parametrize(
    "field,value",
    [
        ("reason", ""),
        ("issue", "docs/no-such-program.md"),
    ],
)
def test_deferral_needs_attributable_program_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    policy = tmp_path / "policy.toml"
    entry = {
        "verification": "not_yet_due",
        "reason": "M0 program pending",
        "issue": "docs/releases/v0.5/completion-plan-2026-09-09.md",
    }
    entry[field] = value
    policy.write_text(
        "[criteria.AC5-001]\n"
        + "\n".join(f"{key} = {json.dumps(item)}" for key, item in entry.items())
    )
    monkeypatch.setattr(gate, "POLICY_PATH", policy)
    assert any(
        "deferral needs reason" in error for error in gate.apply_policy(gate.load_criteria())
    )


def pointer(path: Path, root: Path) -> dict[str, str]:
    return {
        "path": str(path.relative_to(root)),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


class Bundle:
    def __init__(self, root: Path) -> None:
        self.root, self.path, self.criteria = root, root / "manifest.json", gate.load_criteria()
        self.manifest: dict[str, Any] = {
            "schema_version": 1,
            "release": "v0.5",
            "candidate_commit": COMMIT,
            "candidate_tree": TREE,
            "criteria": {},
        }
        artifact = root / "synthetic-parser-input.txt"
        artifact.write_text("Synthetic parser fixture; not an executed simulator/study/protocol.\n")
        for criterion in self.criteria.values():
            if criterion.release != "v0.5":
                continue
            entry: dict[str, Any] = {}
            for kind in criterion.required_evidence:
                report = {
                    "schema_version": 1,
                    "criterion_id": criterion.id,
                    "witness_class": kind,
                    "candidate_commit": COMMIT,
                    "candidate_tree": TREE,
                    "outcome": "PASS",
                    "executed_at": "2026-09-08T00:00:00+00:00",
                    "command": ["synthetic-parser-fixture", "not-a-real-execution"],
                    "exit_code": 0,
                    "cases": [{"id": "synthetic-case", "outcome": "PASS"}],
                    "artifacts": [pointer(artifact, root)],
                    "evidence_type": "SIMULATION",
                    "producer_identity": "synthetic-producer",
                    "verifier_identity": "synthetic-verifier",
                    "simulator_digests": ["c" * 64],
                    "adapter_digests": ["d" * 64, "e" * 64],
                    "protocol": pointer(artifact, root),
                    "registered_at": "2026-09-01T00:00:00+00:00",
                    "first_outcome_at": "2026-09-02T00:00:00+00:00",
                    "claim_decision": "GO",
                    "planned_trials": 24,
                    "completed_trials": 24,
                    "treatments": ["B0", "B1", "B2", "B3"],
                    "task_families": ["reach", "pick_place", "declared_recovery"],
                }
                path = root / f"{criterion.id}-{kind}.json"
                path.write_text(json.dumps(report))
                entry[kind] = pointer(path, root)
            self.manifest["criteria"][criterion.id] = entry
        self.save()

    def save(self) -> None:
        self.path.write_text(json.dumps(self.manifest))

    def change(self, identifier: str, kind: str, field: str, value: Any) -> None:
        path = self.root / self.manifest["criteria"][identifier][kind]["path"]
        report = json.loads(path.read_text())
        report[field] = value
        path.write_text(json.dumps(report))
        self.manifest["criteria"][identifier][kind] = pointer(path, self.root)
        self.save()

    def errors(self) -> list[str]:
        return gate.v05_release_evidence_errors(self.criteria, self.path, COMMIT, TREE, now=NOW)


@pytest.fixture
def bundle(tmp_path: Path) -> Bundle:
    return Bundle(tmp_path)


def test_synthetic_records_only_validate_parser(bundle: Bundle) -> None:
    assert bundle.errors() == []
    assert gate.classify(bundle.criteria["AC5-001"]) == "UNCOVERED"


@pytest.mark.parametrize("mode", ["not_yet_due", "waived"])
def test_records_do_not_erase_deferred_or_waived_obligations(bundle: Bundle, mode: str) -> None:
    bundle.criteria["AC5-001"].verification = mode
    assert any(f"{mode} cannot qualify" in error for error in bundle.errors())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("candidate_commit", "c" * 40, "candidate_commit"),
        ("schema_version", True, "schema_version"),
        ("candidate_tree", "c" * 40, "candidate_tree"),
        ("criterion_id", "AC5-002", "criterion_id"),
        ("witness_class", "S", "witness_class"),
        ("outcome", "SKIPPED", "outcome"),
        ("exit_code", None, "observed zero command exit"),
        ("exit_code", False, "observed zero command exit"),
        ("command", [], "executed command"),
        ("cases", [], "no executed cases"),
        ("cases", [{"id": "x", "outcome": "SKIPPED"}], "actual PASS outcomes"),
        ("cases", [{"id": "x", "outcome": "PASS"}] * 2, "unique IDs"),
        ("artifacts", [], "original execution artifacts"),
        ("executed_at", "2026-09-10T00:00:00+00:00", "future-dated or stale"),
        ("executed_at", "2020-01-01T00:00:00+00:00", "future-dated or stale"),
        ("executed_at", "2026-09-08", "needs a timezone"),
    ],
)
def test_invalid_reports_fail(bundle: Bundle, field: str, value: Any, message: str) -> None:
    bundle.change("AC5-001", "A", field, value)
    assert any(message in error for error in bundle.errors())


@pytest.mark.parametrize(
    ("identifier", "kind", "field", "value", "message"),
    [
        ("AC5-006", "S", "adapter_digests", ["d" * 64], "at least 2 distinct"),
        ("AC5-006", "S", "adapter_digests", ["d" * 64] * 2, "at least 2 distinct"),
        ("AC5-006", "S", "simulator_digests", [], "simulator_digests"),
        ("AC5-006", "S", "evidence_type", "PHYSICAL", "SIMULATION evidence type"),
        ("AC5-006", "S", "verifier_identity", "synthetic-producer", "independent producer"),
        ("AC5-024", "B", "claim_decision", "NO-GO", "does not qualify"),
        ("AC5-024", "B", "claim_decision", "INCONCLUSIVE", "does not qualify"),
        ("AC5-024", "B", "planned_trials", 0, "empty or incomplete"),
        ("AC5-024", "B", "completed_trials", 23, "empty or incomplete"),
        ("AC5-024", "B", "treatments", ["B0"] * 4, "every required treatments"),
        ("AC5-024", "B", "task_families", ["reach"], "every required task_families"),
        ("AC5-024", "B", "registered_at", "2026-09-03T00:00:00+00:00", "before study outcome"),
    ],
)
def test_simulator_and_study_requirements(
    bundle: Bundle,
    identifier: str,
    kind: str,
    field: str,
    value: Any,
    message: str,
) -> None:
    bundle.change(identifier, kind, field, value)
    assert any(message in error for error in bundle.errors())


def test_all_composite_classes_are_required(bundle: Bundle) -> None:
    del bundle.manifest["criteria"]["AC5-019"]["B"]
    bundle.save()
    assert "AC5-019: missing or unknown composite witness classes" in bundle.errors()


@pytest.mark.parametrize("missing", [False, True])
def test_missing_or_extra_manifest_ids_fail(bundle: Bundle, missing: bool) -> None:
    if missing:
        del bundle.manifest["criteria"]["AC5-030"]
    else:
        bundle.manifest["criteria"]["AC5-031"] = {}
    bundle.save()
    assert "release manifest must cover exactly the 30 v0.5 IDs" in bundle.errors()


def test_tampered_or_empty_original_artifact_fails(bundle: Bundle) -> None:
    artifact = bundle.root / "synthetic-parser-input.txt"
    artifact.write_text("Changed input")
    assert any("artifact digest mismatch" in error for error in bundle.errors())
    artifact.write_text("")
    assert any("artifact is absent, empty" in error for error in bundle.errors())


def test_duplicate_json_cannot_hide_failure(bundle: Bundle) -> None:
    record = bundle.root / "AC5-001-A.json"
    record.write_text(
        record.read_text().replace('"outcome": "PASS"', '"outcome":"FAIL","outcome":"PASS"', 1)
    )
    bundle.manifest["criteria"]["AC5-001"]["A"] = pointer(record, bundle.root)
    bundle.save()
    assert any("duplicate JSON key" in error for error in bundle.errors())


@pytest.mark.parametrize("kind", ["symlink", "traversal"])
def test_evidence_cannot_use_symlink_or_traversal(bundle: Bundle, kind: str) -> None:
    artifact = bundle.root / "synthetic-parser-input.txt"
    if kind == "symlink":
        path = bundle.root / "alias.txt"
        path.symlink_to(artifact)
        item = pointer(path, bundle.root)
    else:
        nested = bundle.root / "nested"
        nested.mkdir()
        item = {
            "path": "nested/../synthetic-parser-input.txt",
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        }
    bundle.change("AC5-001", "A", "artifacts", [item])
    assert any("outside its bundle" in error for error in bundle.errors())


@pytest.mark.parametrize(
    "date_value,path",
    [
        ("2999-01-01", "docs/releases/v0.5/completion-plan-2026-09-09.md"),
        ("2026-09-09", "docs/missing.md"),
        ("not-a-date", "docs/releases/v0.5/completion-plan-2026-09-09.md"),
    ],
)
def test_v05_manual_record_needs_real_evidence_and_nonfuture_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    date_value: str,
    path: str,
) -> None:
    policy = tmp_path / "policy.toml"
    policy.write_text(
        f'[criteria.AC5-024]\nverification="manual"\nevidence={json.dumps(path)}\nlast_verified={json.dumps(date_value)}\n'
    )
    monkeypatch.setattr(gate, "POLICY_PATH", policy)
    assert gate.apply_policy(gate.load_criteria())


def test_release_bridge_requires_real_commit_with_identical_tree(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "acceptance_cli", ROOT / "scripts/check_acceptance.py"
    )
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    def git(*args: str) -> str:
        return subprocess.run(
            [
                "git",
                "-c",
                "user.name=Gate fixture",
                "-c",
                "user.email=fixture@example.invalid",
                *args,
            ],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

    git("init")
    (tmp_path / "source.txt").write_text("original")
    git("add", "source.txt")
    git("commit", "-m", "candidate")
    commit, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    git("commit", "--allow-empty", "-m", "identical-tree bridge")
    assert git("rev-parse", "HEAD") != commit
    assert cli.evidence_candidate(tmp_path, commit, git("rev-parse", "HEAD^{tree}")) == commit
    (tmp_path / "source.txt").write_text("changed")
    git("add", "source.txt")
    git("commit", "-m", "different tree")
    with pytest.raises(ValueError, match="exact current candidate tree"):
        cli.evidence_candidate(tmp_path, commit, git("rev-parse", "HEAD^{tree}"))
    with pytest.raises(ValueError, match="full commit ID"):
        cli.evidence_candidate(tmp_path, "HEAD", tree)


@pytest.mark.parametrize(
    "mutation,message",
    [
        ("candidate", "candidate changed while acceptance tests"),
        ("manifest", "evidence manifest changed while acceptance tests"),
        ("artifact", "artifact digest mismatch"),
    ],
)
def test_release_rechecks_candidate_and_artifacts_after_claiming_tests(
    bundle: Bundle,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mutation: str,
    message: str,
) -> None:
    spec = importlib.util.spec_from_file_location(
        "acceptance_cli_closure", ROOT / "scripts/check_acceptance.py"
    )
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    changed = False

    def candidate() -> tuple[str, str]:
        return ("c" * 40 if changed and mutation == "candidate" else COMMIT, TREE)

    def synthetic_test_runner(quiet: bool) -> SimpleNamespace:
        nonlocal changed
        changed = True
        if mutation == "manifest":
            bundle.manifest["unexpected_change"] = "during tests"
            bundle.save()
        elif mutation == "artifact":
            (bundle.root / "synthetic-parser-input.txt").write_text("changed during tests")
        claims = {identifier: [f"synthetic::{identifier}"] for identifier in bundle.criteria}
        return SimpleNamespace(
            claims=claims, outcomes={nodes[0]: "passed" for nodes in claims.values()}, exit_code=0
        )

    monkeypatch.setattr(cli, "load_criteria", lambda: bundle.criteria)
    monkeypatch.setattr(cli, "apply_policy", lambda criteria: [])
    monkeypatch.setattr(cli, "current_candidate", candidate)
    monkeypatch.setattr(cli, "evidence_candidate", lambda *args: COMMIT)
    monkeypatch.setattr(cli, "run_tests", synthetic_test_runner)
    monkeypatch.setattr(
        sys,
        "argv",
        ["check_acceptance.py", "--release", "v0.5", "--evidence-manifest", str(bundle.path)],
    )
    assert cli.main() == 1
    output = capsys.readouterr().out
    assert message in output
    assert "PASS: acceptance" not in output


@pytest.mark.parametrize(
    "arguments",
    [
        ["--release", "v0.5", "--no-tests"],
        ["--release", "v0.5", "--stage", "v0.5-M0"],
    ],
)
def test_release_cli_cannot_skip_tests_or_select_one_stage(arguments: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_acceptance.py", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "PASS" not in result.stdout


RELEASE_SUITE_RUNNER = """
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

root, manifest, suite, mode = sys.argv[1:]
spec = importlib.util.spec_from_file_location(
    "release_cli_test", Path(root) / "scripts/check_acceptance.py"
)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
real_pytest = pytest.main
observed = {}

def isolated_pytest(args, plugins):
    args = [*args, "-c", "/dev/null", "-p", "no:cacheprovider", suite]
    if mode == "usage":
        args.append("--nonexistent-release-witness-option")
    code = real_pytest(args, plugins=plugins)
    observed["pytest_exit"] = int(code)
    return code

# These synthetic records and claiming controls only isolate gate control flow.
# Neither the records nor the claims are robotics acceptance evidence.
with (
    patch.object(cli, "apply_policy", lambda _: []),
    patch.object(cli, "current_candidate", lambda: ("a" * 40, "b" * 40)),
    patch.object(cli, "evidence_candidate", lambda *args: "a" * 40),
    patch.object(pytest, "main", isolated_pytest),
    patch.object(sys, "argv", ["check_acceptance.py", "--release", "v0.5",
                              "--evidence-manifest", manifest]),
):
    try:
        observed["cli_exit"] = cli.main()
    except SystemExit as error:
        observed["cli_exit"] = error.code if isinstance(error.code, int) else 1
        print(str(error))
print("GATE_RESULT " + json.dumps(observed))
"""


@pytest.mark.parametrize(
    "mode,pytest_exit",
    [
        ("control", 0),
        ("unmarked_failure", 1),
        ("unmarked_setup", 1),
        ("unmarked_teardown", 1),
        ("claimed_failure", 1),
        ("collection", 2),
        ("internal", 3),
        ("usage", 4),
        ("empty", 5),
    ],
)
def test_release_requires_real_pytest_success_even_for_unmarked_tests(
    bundle: Bundle, tmp_path: Path, mode: str, pytest_exit: int
) -> None:
    """Exercise real pytest exits through the CLI without claiming any AC5 proof."""
    suite = tmp_path / "isolated-suite"
    suite.mkdir()
    source = (
        "import pytest\n"
        f"@pytest.mark.acceptance(*{list(bundle.criteria)!r})\n"
        "def test_claiming_control():\n"
        f"    assert {mode != 'claimed_failure'}\n"
    )
    if mode == "unmarked_failure":
        source += "def test_unmarked_regression():\n    assert False\n"
    elif mode == "unmarked_setup":
        source += (
            "@pytest.fixture\ndef broken():\n    raise RuntimeError('setup failure')\n"
            "def test_unmarked_regression(broken):\n    pass\n"
        )
    elif mode == "unmarked_teardown":
        source += (
            "@pytest.fixture\ndef broken():\n    yield\n"
            "    raise RuntimeError('teardown failure')\n"
            "def test_unmarked_regression(broken):\n    pass\n"
        )
    elif mode == "collection":
        source += "raise RuntimeError('collection failure')\n"
    elif mode == "internal":
        (suite / "conftest.py").write_text(
            "def pytest_sessionstart(session):\n    raise RuntimeError('session failure')\n"
        )
    elif mode == "empty":
        source = ""
    (suite / "test_suite.py").write_text(source)
    runner = tmp_path / "run_release_gate.py"
    runner.write_text(RELEASE_SUITE_RUNNER)
    completed = subprocess.run(
        [sys.executable, str(runner), str(ROOT), str(bundle.path), str(suite), mode],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    rows = [line for line in completed.stdout.splitlines() if line.startswith("GATE_RESULT ")]
    assert rows, completed.stdout + completed.stderr
    observed = json.loads(rows[-1].removeprefix("GATE_RESULT "))
    assert observed["pytest_exit"] == pytest_exit, completed.stdout + completed.stderr
    assert observed["cli_exit"] == (0 if mode == "control" else 1)
    if mode == "control":
        assert "PASS: acceptance and composite evidence integrity checks" in completed.stdout
    else:
        assert "PASS: acceptance" not in completed.stdout
    if pytest_exit == 1:
        assert "v0.5 release requires a zero pytest exit; observed 1" in completed.stdout
    if mode == "claimed_failure":
        assert "FAILING:" in completed.stdout
        assert "AC5-001" in completed.stdout


def test_m0_cannot_pass_explicit_release_mode() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_acceptance.py", "--release", "v0.5"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "FAIL: v0.5 release evidence is not complete" in result.stdout
    assert "PASS" not in result.stdout
