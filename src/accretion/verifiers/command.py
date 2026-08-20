from __future__ import annotations

import hashlib
import time
from pathlib import Path

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


class CommandVerifier:
    """Run one trusted, constructor-injected argv without a shell."""

    verifier_version = "allowlisted-command-v1"

    def __init__(
        self,
        verifier_id: str,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float = 300,
        max_output_bytes: int = 1_000_000,
        working_directory: Path | None = None,
    ) -> None:
        if not verifier_id:
            raise ValueError("verifier_id is required")
        if not argv or any(not item or "\0" in item for item in argv):
            raise ValueError("argv must contain non-empty, NUL-free arguments")
        if timeout_seconds <= 0 or timeout_seconds > 3600:
            raise ValueError("timeout_seconds must be between 0 and 3600")
        if max_output_bytes <= 0 or max_output_bytes > 10_000_000:
            raise ValueError("max_output_bytes must be between 1 and 10000000")
        if working_directory is not None and working_directory.is_absolute():
            raise ValueError("working_directory must be relative to the workspace")
        self.verifier_id = verifier_id
        self.argv = argv
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.working_directory = working_directory

    async def verify(
        self, target: VerificationTarget, context: VerificationContext
    ) -> VerificationResult:
        started_at = time.monotonic()
        if target.kind is not VerificationTargetKind.COMMAND_SUITE:
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
                        f"Expected COMMAND_SUITE target, received {target.kind.value}.",
                    )
                ],
            )
        if target.command_suite_refs and self.verifier_id not in target.command_suite_refs:
            return verification_result(
                verifier_id=self.verifier_id,
                verifier_version=self.verifier_version,
                target=target,
                status=VerificationStatus.INCONCLUSIVE,
                started_at=started_at,
                findings=[
                    finding(
                        "COMMAND_SUITE_NOT_REQUESTED",
                        FindingSeverity.ERROR,
                        "The trusted command verifier is not referenced by this target.",
                    )
                ],
            )
        workspace = context.workspace.resolve()
        cwd = (
            (workspace / self.working_directory).resolve()
            if self.working_directory
            else workspace
        )
        if not cwd.is_relative_to(workspace) or not cwd.is_dir():
            return verification_result(
                verifier_id=self.verifier_id,
                verifier_version=self.verifier_version,
                target=target,
                status=VerificationStatus.INCONCLUSIVE,
                started_at=started_at,
                findings=[
                    finding(
                        "COMMAND_WORKING_DIRECTORY_INVALID",
                        FindingSeverity.ERROR,
                        "The configured command working directory is unavailable.",
                    )
                ],
            )
        result = await run_bounded_process(
            self.argv,
            cwd=cwd,
            timeout_seconds=min(self.timeout_seconds, context.timeout_seconds),
            max_output_bytes=min(self.max_output_bytes, context.max_output_bytes),
        )
        stdout_digest = hashlib.sha256(result.stdout).hexdigest()
        stderr_digest = hashlib.sha256(result.stderr).hexdigest()
        evidence_refs = [
            f"command-stdout-sha256:{stdout_digest}",
            f"command-stderr-sha256:{stderr_digest}",
        ]
        if result.startup_error:
            status = VerificationStatus.INCONCLUSIVE
            code = "COMMAND_START_FAILED"
            message = "The configured verifier command could not be started."
        elif result.timed_out:
            status = VerificationStatus.INCONCLUSIVE
            code = "COMMAND_TIMEOUT"
            message = "The verifier command exceeded its time limit."
        elif result.truncated:
            status = VerificationStatus.INCONCLUSIVE
            code = "COMMAND_OUTPUT_LIMIT"
            message = "The verifier command exceeded its evidence output limit."
        elif result.returncode != 0:
            status = VerificationStatus.FAIL
            code = "COMMAND_FAILED"
            message = f"The verifier command exited with status {result.returncode}."
        else:
            return verification_result(
                verifier_id=self.verifier_id,
                verifier_version=self.verifier_version,
                target=target,
                status=VerificationStatus.PASS,
                started_at=started_at,
                evidence_refs=evidence_refs,
                score=1.0,
                false_accept_risk_estimate=0.05,
            )
        return verification_result(
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            target=target,
            status=status,
            started_at=started_at,
            findings=[finding(code, FindingSeverity.ERROR, message)],
            evidence_refs=evidence_refs,
            score=0.0 if status is VerificationStatus.FAIL else None,
        )
