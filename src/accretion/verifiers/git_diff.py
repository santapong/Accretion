from __future__ import annotations

import hashlib
import time

from accretion.contracts import (
    FindingSeverity,
    VerificationContext,
    VerificationResult,
    VerificationStatus,
    VerificationTarget,
    VerificationTargetKind,
)
from accretion.verifiers.process import run_bounded_process
from accretion.verifiers.results import finding, verification_result


class GitDiffVerifier:
    verifier_id = "git-diff"
    verifier_version = "git-diff-v1"

    async def verify(
        self, target: VerificationTarget, context: VerificationContext
    ) -> VerificationResult:
        started_at = time.monotonic()
        if target.kind is not VerificationTargetKind.GIT_DIFF:
            return verification_result(
                verifier_id=self.verifier_id,
                verifier_version=self.verifier_version,
                target=target,
                status=VerificationStatus.INCONCLUSIVE,
                started_at=started_at,
                findings=[
                    finding(
                        "TARGET_KIND_MISMATCH",
                        FindingSeverity.ERROR,
                        f"Expected GIT_DIFF target, received {target.kind.value}.",
                    )
                ],
            )

        output_limit = context.max_output_bytes
        status_result = await run_bounded_process(
            ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"),
            cwd=context.workspace,
            timeout_seconds=context.timeout_seconds,
            max_output_bytes=output_limit,
        )
        diff_result = await run_bounded_process(
            ("git", "diff", "--no-ext-diff", "--binary", "HEAD"),
            cwd=context.workspace,
            timeout_seconds=context.timeout_seconds,
            max_output_bytes=output_limit,
        )
        unavailable = status_result.startup_error or diff_result.startup_error
        timed_out = status_result.timed_out or diff_result.timed_out
        if unavailable or timed_out or status_result.returncode != 0 or diff_result.returncode != 0:
            reason = unavailable or (
                "Git inspection exceeded its time limit."
                if timed_out
                else "Git could not inspect the workspace."
            )
            return verification_result(
                verifier_id=self.verifier_id,
                verifier_version=self.verifier_version,
                target=target,
                status=VerificationStatus.INCONCLUSIVE,
                started_at=started_at,
                findings=[
                    finding(
                        "GIT_INSPECTION_UNAVAILABLE",
                        FindingSeverity.ERROR,
                        reason,
                    )
                ],
            )

        changed_paths, untracked_paths = self._changed_paths(status_result.stdout)
        diff_chunks = [diff_result.stdout]
        diff_truncated = diff_result.truncated
        diff_unavailable: str | None = None
        remaining_output = max(0, output_limit - len(diff_result.stdout))
        for relative_path in untracked_paths:
            if remaining_output <= 0:
                diff_truncated = True
                break
            untracked_result = await run_bounded_process(
                (
                    "git",
                    "diff",
                    "--no-ext-diff",
                    "--binary",
                    "--no-index",
                    "--",
                    "/dev/null",
                    relative_path,
                ),
                cwd=context.workspace,
                timeout_seconds=context.timeout_seconds,
                max_output_bytes=remaining_output,
            )
            if untracked_result.startup_error or untracked_result.timed_out:
                diff_unavailable = untracked_result.startup_error or (
                    "Git inspection exceeded its time limit."
                )
                break
            if untracked_result.returncode not in {0, 1}:
                diff_unavailable = "Git could not inspect an untracked output."
                break
            diff_chunks.append(untracked_result.stdout)
            remaining_output -= len(untracked_result.stdout)
            diff_truncated = diff_truncated or untracked_result.truncated

        if diff_unavailable is not None:
            return verification_result(
                verifier_id=self.verifier_id,
                verifier_version=self.verifier_version,
                target=target,
                status=VerificationStatus.INCONCLUSIVE,
                started_at=started_at,
                findings=[
                    finding(
                        "GIT_INSPECTION_UNAVAILABLE",
                        FindingSeverity.ERROR,
                        diff_unavailable,
                    )
                ],
            )

        combined_diff = b"".join(diff_chunks)
        status_digest = hashlib.sha256(status_result.stdout).hexdigest()
        diff_digest = hashlib.sha256(combined_diff).hexdigest()
        evidence_refs = [
            f"git-status-sha256:{status_digest}",
            f"git-diff-sha256:{diff_digest}",
        ]
        findings = []
        if status_result.truncated or diff_truncated:
            return verification_result(
                verifier_id=self.verifier_id,
                verifier_version=self.verifier_version,
                target=target,
                status=VerificationStatus.INCONCLUSIVE,
                started_at=started_at,
                findings=[
                    finding(
                        "GIT_EVIDENCE_TRUNCATED",
                        FindingSeverity.ERROR,
                        "Git evidence exceeded the configured output limit.",
                    )
                ],
                evidence_refs=evidence_refs,
            )
        if target.require_git_changes and not changed_paths:
            findings.append(
                finding(
                    "EXPECTED_CHANGES_MISSING",
                    FindingSeverity.ERROR,
                    "The workspace contains no changes.",
                    evidence_ref=evidence_refs[0],
                )
            )
        for expected_path in target.expected_changed_paths:
            normalized = expected_path.strip("/")
            if not normalized or not any(
                path == normalized or path.startswith(f"{normalized}/")
                for path in changed_paths
            ):
                findings.append(
                    finding(
                        "EXPECTED_PATH_UNCHANGED",
                        FindingSeverity.ERROR,
                        "An expected path was not changed.",
                        path=expected_path,
                        evidence_ref=evidence_refs[0],
                    )
                )
        if target.expected_diff_sha256 and target.expected_diff_sha256 != diff_digest:
            findings.append(
                finding(
                    "DIFF_DIGEST_MISMATCH",
                    FindingSeverity.ERROR,
                    "The Git diff digest does not match the expected candidate.",
                    evidence_ref=evidence_refs[1],
                )
            )
        status = VerificationStatus.FAIL if findings else VerificationStatus.PASS
        return verification_result(
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            target=target,
            status=status,
            started_at=started_at,
            findings=findings,
            evidence_refs=evidence_refs,
            score=0.0 if findings else 1.0,
            false_accept_risk_estimate=0.1 if not findings else None,
        )

    @staticmethod
    def _changed_paths(status: bytes) -> tuple[set[str], list[str]]:
        paths: set[str] = set()
        untracked_paths: list[str] = []
        records = status.decode(errors="replace").split("\0")
        skip_rename_target = False
        for record in records:
            if not record:
                continue
            if skip_rename_target:
                paths.add(record)
                skip_rename_target = False
                continue
            if len(record) < 4:
                continue
            state = record[:2]
            path = record[3:]
            paths.add(path)
            if state == "??":
                untracked_paths.append(path)
            skip_rename_target = "R" in state or "C" in state
        return paths, untracked_paths
