from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from accretion.contracts import (
    Finding,
    FindingSeverity,
    VerificationContext,
    VerificationResult,
    VerificationStatus,
    VerificationTarget,
    VerificationTargetKind,
)
from accretion.verifiers.results import finding, verification_result


class OutputContractVerifier:
    verifier_id = "output-contract"
    verifier_version = "output-contract-v1"

    async def verify(
        self, target: VerificationTarget, context: VerificationContext
    ) -> VerificationResult:
        started_at = time.monotonic()
        if target.kind is not VerificationTargetKind.OUTPUT_CONTRACT:
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
                        f"Expected OUTPUT_CONTRACT target, received {target.kind.value}.",
                    )
                ],
            )
        if not target.required_outputs:
            return verification_result(
                verifier_id=self.verifier_id,
                verifier_version=self.verifier_version,
                target=target,
                status=VerificationStatus.INCONCLUSIVE,
                started_at=started_at,
                findings=[
                    finding(
                        "OUTPUT_CONTRACT_MISSING",
                        FindingSeverity.WARNING,
                        "No required outputs were declared.",
                    )
                ],
            )

        workspace = context.workspace.resolve()
        findings: list[Finding] = []
        evidence_refs: list[str] = []
        configuration_error = False
        for raw_requirement in target.required_outputs:
            requirement_findings, requirement_evidence, invalid = self._verify_requirement(
                workspace, raw_requirement, context.max_output_bytes
            )
            findings.extend(requirement_findings)
            evidence_refs.extend(requirement_evidence)
            configuration_error = configuration_error or invalid or any(
                item.code == "OUTPUT_REQUIREMENT_INVALID" for item in requirement_findings
            )

        if configuration_error:
            status = VerificationStatus.INCONCLUSIVE
            score = None
        elif any(item.severity is FindingSeverity.ERROR for item in findings):
            status = VerificationStatus.FAIL
            score = 0.0
        else:
            status = VerificationStatus.PASS
            score = 1.0
        return verification_result(
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            target=target,
            status=status,
            started_at=started_at,
            findings=findings,
            evidence_refs=evidence_refs,
            score=score,
            false_accept_risk_estimate=0.05 if status is VerificationStatus.PASS else None,
        )

    @staticmethod
    def _verify_requirement(
        workspace: Path, requirement: dict[str, Any], max_output_bytes: int
    ) -> tuple[list[Finding], list[str], bool]:
        raw_path = requirement.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return (
                [
                    finding(
                        "OUTPUT_REQUIREMENT_INVALID",
                        FindingSeverity.ERROR,
                        "Every output requirement must contain a non-empty path.",
                    )
                ],
                [],
                True,
            )
        relative = Path(raw_path)
        candidate = (workspace / relative).resolve()
        if relative.is_absolute() or not candidate.is_relative_to(workspace):
            return (
                [
                    finding(
                        "OUTPUT_PATH_OUTSIDE_WORKSPACE",
                        FindingSeverity.ERROR,
                        "Required output resolves outside the workspace.",
                        path=raw_path,
                    )
                ],
                [],
                False,
            )

        kind = requirement.get("kind", requirement.get("type", "file"))
        if kind not in {"file", "directory", "json"}:
            return (
                [
                    finding(
                        "OUTPUT_REQUIREMENT_INVALID",
                        FindingSeverity.ERROR,
                        f"Unsupported output kind {kind!r}.",
                        path=raw_path,
                    )
                ],
                [],
                True,
            )
        if not candidate.exists():
            return (
                [
                    finding(
                        "REQUIRED_OUTPUT_MISSING",
                        FindingSeverity.ERROR,
                        "Required output does not exist.",
                        path=raw_path,
                    )
                ],
                [],
                False,
            )
        if kind == "directory":
            if not candidate.is_dir():
                return (
                    [
                        finding(
                            "OUTPUT_KIND_MISMATCH",
                            FindingSeverity.ERROR,
                            "Required output is not a directory.",
                            path=raw_path,
                        )
                    ],
                    [],
                    False,
                )
            if requirement.get("non_empty", False) and not any(candidate.iterdir()):
                return (
                    [
                        finding(
                            "REQUIRED_OUTPUT_EMPTY",
                            FindingSeverity.ERROR,
                            "Required output directory is empty.",
                            path=raw_path,
                        )
                    ],
                    [],
                    False,
                )
            return [], [f"directory:{raw_path}"], False

        if not candidate.is_file():
            return (
                [
                    finding(
                        "OUTPUT_KIND_MISMATCH",
                        FindingSeverity.ERROR,
                        "Required output is not a file.",
                        path=raw_path,
                    )
                ],
                [],
                False,
            )
        if candidate.stat().st_size > max_output_bytes:
            return (
                [
                    finding(
                        "OUTPUT_EVIDENCE_LIMIT",
                        FindingSeverity.ERROR,
                        "Required output exceeds the configured evidence size limit.",
                        path=raw_path,
                    )
                ],
                [],
                True,
            )
        content = candidate.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        evidence_ref = f"file-sha256:{raw_path}:{digest}"
        output_findings: list[Finding] = []
        if requirement.get("non_empty", True) and not content:
            output_findings.append(
                finding(
                    "REQUIRED_OUTPUT_EMPTY",
                    FindingSeverity.ERROR,
                    "Required output file is empty.",
                    path=raw_path,
                    evidence_ref=evidence_ref,
                )
            )
        expected_sha = requirement.get("sha256")
        if expected_sha is not None and expected_sha != digest:
            output_findings.append(
                finding(
                    "OUTPUT_DIGEST_MISMATCH",
                    FindingSeverity.ERROR,
                    "Required output digest does not match the contract.",
                    path=raw_path,
                    evidence_ref=evidence_ref,
                )
            )
        if kind == "json" or requirement.get("content_type") == "application/json":
            output_findings.extend(
                OutputContractVerifier._verify_json(raw_path, content, requirement, evidence_ref)
            )
        return output_findings, [evidence_ref], False

    @staticmethod
    def _verify_json(
        raw_path: str,
        content: bytes,
        requirement: dict[str, Any],
        evidence_ref: str,
    ) -> list[Finding]:
        try:
            value = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return [
                finding(
                    "OUTPUT_JSON_INVALID",
                    FindingSeverity.ERROR,
                    "Required JSON output could not be parsed.",
                    path=raw_path,
                    evidence_ref=evidence_ref,
                )
            ]
        required_keys = requirement.get("required_keys", [])
        if not isinstance(required_keys, list) or any(
            not isinstance(key, str) for key in required_keys
        ):
            return [
                finding(
                    "OUTPUT_REQUIREMENT_INVALID",
                    FindingSeverity.ERROR,
                    "required_keys must be a list of strings.",
                    path=raw_path,
                )
            ]
        if required_keys and not isinstance(value, dict):
            return [
                finding(
                    "OUTPUT_JSON_SHAPE_MISMATCH",
                    FindingSeverity.ERROR,
                    "Required JSON output must be an object.",
                    path=raw_path,
                    evidence_ref=evidence_ref,
                )
            ]
        missing = [key for key in required_keys if key not in value]
        if missing:
            return [
                finding(
                    "OUTPUT_JSON_KEYS_MISSING",
                    FindingSeverity.ERROR,
                    f"Required JSON keys are missing: {', '.join(missing)}.",
                    path=raw_path,
                    evidence_ref=evidence_ref,
                )
            ]
        return []
