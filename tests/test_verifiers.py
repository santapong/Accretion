from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from accretion.contracts import (
    AcceptancePolicy,
    Provider,
    RiskLevel,
    VerificationContext,
    VerificationResult,
    VerificationStatus,
    VerificationTarget,
    VerificationTargetKind,
)
from accretion.ids import new_id
from accretion.verifiers import (
    CommandVerifier,
    GitDiffVerifier,
    OutputContractVerifier,
    TrajectoryPolicyVerifier,
    VerifierRegistry,
    VerifierUnavailableError,
    evaluate_acceptance,
)
from accretion.workspace import WorkspaceError, WorktreeManager


def context(tmp_path: Path, **updates: object) -> VerificationContext:
    values: dict[str, object] = {
        "task_id": new_id("task"),
        "project_id": new_id("project"),
        "workspace": tmp_path,
    }
    values.update(updates)
    return VerificationContext.model_validate(values)


def target(kind: VerificationTargetKind, **updates: object) -> VerificationTarget:
    values: dict[str, object] = {
        "target_ref": "candidate-1",
        "kind": kind,
        "run_id": new_id("run"),
    }
    values.update(updates)
    return VerificationTarget.model_validate(values)


def result(verifier_id: str, status: VerificationStatus, score: float | None = None):
    return VerificationResult(
        verification_id=new_id("verification"),
        run_id=new_id("run"),
        verifier_id=verifier_id,
        verifier_version="test-v1",
        target_ref="candidate",
        status=status,
        score=score,
    )


async def test_output_contract_verifies_files_and_structured_json(tmp_path: Path) -> None:
    (tmp_path / "result.json").write_text('{"status":"ready"}')
    verification = await OutputContractVerifier().verify(
        target(
            VerificationTargetKind.OUTPUT_CONTRACT,
            required_outputs=[
                {
                    "path": "result.json",
                    "kind": "json",
                    "required_keys": ["status"],
                }
            ],
        ),
        context(tmp_path),
    )
    assert verification.status is VerificationStatus.PASS
    assert verification.evidence_refs[0].startswith("file-sha256:result.json:")


async def test_output_contract_fails_missing_and_is_inconclusive_without_contract(
    tmp_path: Path,
) -> None:
    verifier = OutputContractVerifier()
    missing = await verifier.verify(
        target(
            VerificationTargetKind.OUTPUT_CONTRACT,
            required_outputs=[{"path": "missing.txt"}],
        ),
        context(tmp_path),
    )
    unknown = await verifier.verify(
        target(VerificationTargetKind.OUTPUT_CONTRACT), context(tmp_path)
    )
    assert missing.status is VerificationStatus.FAIL
    assert unknown.status is VerificationStatus.INCONCLUSIVE


async def test_git_diff_requires_observable_workspace_changes(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "README.md").write_text("base\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "base"], check=True)
    verifier = GitDiffVerifier()
    no_change = await verifier.verify(
        target(VerificationTargetKind.GIT_DIFF), context(tmp_path)
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "new.py").write_text("value = 1\n")
    changed = await verifier.verify(
        target(
            VerificationTargetKind.GIT_DIFF,
            expected_changed_paths=["src/new.py"],
        ),
        context(tmp_path),
    )
    assert no_change.status is VerificationStatus.FAIL
    assert changed.status is VerificationStatus.PASS


async def test_untracked_file_patch_is_immutable_and_digest_bound(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Test"], check=True
    )
    (repository / "README.md").write_text("base\n")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "base"], check=True)
    worktrees = WorktreeManager(tmp_path / "worktrees", tmp_path / "artifacts")
    lease = await worktrees.acquire(
        project_id="project_untracked",
        run_id="run_untracked",
        repository=repository,
    )
    output = lease.path / "reports" / "result.json"
    output.parent.mkdir()
    output.write_text('{"status":"ready"}\n')

    artifact = await worktrees.capture_diff(
        lease,
        name="iteration-001.patch",
        kind="LOOP_ITERATION_GIT_DIFF",
    )

    assert artifact is not None
    patch = artifact.path.read_bytes()
    assert b"diff --git a/reports/result.json b/reports/result.json" in patch
    assert b'{"status":"ready"}' in patch
    assert artifact.sha256 == hashlib.sha256(patch).hexdigest()
    verification_target = target(
        VerificationTargetKind.GIT_DIFF,
        run_id=lease.run_id,
        target_ref=artifact.artifact_id,
        artifact_refs=[artifact.artifact_id],
        expected_changed_paths=["reports/result.json"],
        expected_diff_sha256=artifact.sha256,
    )
    bound = await GitDiffVerifier().verify(
        verification_target,
        context(tmp_path, workspace=lease.path),
    )
    assert bound.status is VerificationStatus.PASS
    assert f"git-diff-sha256:{artifact.sha256}" in bound.evidence_refs

    output.write_text('{"status":"changed-after-capture"}\n')
    changed_after_capture = await GitDiffVerifier().verify(
        verification_target,
        context(tmp_path, workspace=lease.path),
    )
    assert changed_after_capture.status is VerificationStatus.FAIL
    assert {item.code for item in changed_after_capture.findings} == {
        "DIFF_DIGEST_MISMATCH"
    }
    with pytest.raises(WorkspaceError, match="artifact already exists"):
        await worktrees.capture_diff(lease, name="iteration-001.patch")


async def test_allowlisted_command_verifier_has_time_and_output_fail_closed_bounds(
    tmp_path: Path,
) -> None:
    command_target = target(
        VerificationTargetKind.COMMAND_SUITE,
        command_suite_refs=["unit-tests"],
    )
    passed = await CommandVerifier(
        "unit-tests", (sys.executable, "-c", "raise SystemExit(0)")
    ).verify(command_target, context(tmp_path))
    failed = await CommandVerifier(
        "unit-tests", (sys.executable, "-c", "raise SystemExit(3)")
    ).verify(command_target, context(tmp_path))
    truncated = await CommandVerifier(
        "unit-tests",
        (sys.executable, "-c", "print('x' * 10000)"),
        max_output_bytes=32,
    ).verify(command_target, context(tmp_path))
    timed_out = await CommandVerifier(
        "unit-tests",
        (sys.executable, "-c", "import time; time.sleep(1)"),
        timeout_seconds=0.01,
    ).verify(command_target, context(tmp_path))
    assert passed.status is VerificationStatus.PASS
    assert failed.status is VerificationStatus.FAIL
    assert truncated.status is VerificationStatus.INCONCLUSIVE
    assert timed_out.status is VerificationStatus.INCONCLUSIVE


async def test_trajectory_policy_rejects_denied_actions_and_unresolved_approvals(
    tmp_path: Path,
) -> None:
    verification = await TrajectoryPolicyVerifier().verify(
        target(VerificationTargetKind.TRAJECTORY_POLICY),
        context(
            tmp_path,
            denied_capabilities=["deploy.production"],
            observed_capabilities=["deploy.production"],
            unresolved_approval_ids=["approval-1"],
        ),
    )
    assert verification.status is VerificationStatus.FAIL
    assert {finding.code for finding in verification.findings} == {
        "DENIED_CAPABILITY_OBSERVED",
        "UNRESOLVED_APPROVALS",
    }


def test_registry_is_explicit_and_rejects_missing_or_duplicate_verifiers() -> None:
    verifier = OutputContractVerifier()
    registry = VerifierRegistry([verifier])
    assert registry.get("output-contract") is verifier
    with pytest.raises(ValueError, match="already registered"):
        registry.register(verifier)
    with pytest.raises(VerifierUnavailableError):
        registry.get("not-configured")


def test_acceptance_policy_fails_closed_for_unknowns_and_enforces_risk() -> None:
    policy = AcceptancePolicy(
        policy_id=new_id("acceptance_policy"),
        required_verifiers=["output-contract"],
    )
    inconclusive = evaluate_acceptance(
        policy,
        [result("output-contract", VerificationStatus.INCONCLUSIVE)],
        risk=RiskLevel.LOW,
    )
    passed = evaluate_acceptance(
        policy,
        [result("output-contract", VerificationStatus.PASS, 1)],
        risk=RiskLevel.LOW,
    )
    high_risk = evaluate_acceptance(
        policy,
        [result("output-contract", VerificationStatus.PASS, 1)],
        risk=RiskLevel.HIGH,
    )
    assert inconclusive.requires_human is True
    assert inconclusive.accepted is False
    assert passed.accepted is True
    assert high_risk.requires_human is True


def test_acceptance_policy_honors_explicit_inconclusive_exception() -> None:
    policy = AcceptancePolicy(
        policy_id=new_id("acceptance_policy"),
        required_verifiers=["output-contract"],
        allow_inconclusive=True,
    )

    evaluation = evaluate_acceptance(
        policy,
        [result("output-contract", VerificationStatus.INCONCLUSIVE)],
        risk=RiskLevel.LOW,
    )

    assert evaluation.status is VerificationStatus.PASS
    assert evaluation.accepted is True
    assert evaluation.requires_human is False


def test_provider_enum_remains_available_to_verifier_consumers() -> None:
    assert Provider.DETERMINISTIC.value == "DETERMINISTIC"
