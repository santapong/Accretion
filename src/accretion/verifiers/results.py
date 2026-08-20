from __future__ import annotations

import hashlib
import time
from collections.abc import Sequence

from accretion.contracts import (
    Finding,
    FindingSeverity,
    VerificationResult,
    VerificationStatus,
    VerificationTarget,
)
from accretion.ids import new_id


def finding(
    code: str,
    severity: FindingSeverity,
    message: str,
    *,
    path: str | None = None,
    line: int | None = None,
    evidence_ref: str | None = None,
) -> Finding:
    signature = "\0".join((code, severity.value, message, path or "", str(line or "")))
    return Finding(
        code=code,
        severity=severity,
        message=message,
        path=path,
        line=line,
        evidence_ref=evidence_ref,
        fingerprint=hashlib.sha256(signature.encode()).hexdigest(),
    )


def verification_result(
    *,
    verifier_id: str,
    verifier_version: str,
    target: VerificationTarget,
    status: VerificationStatus,
    started_at: float,
    findings: Sequence[Finding] = (),
    evidence_refs: Sequence[str] = (),
    score: float | None = None,
    false_accept_risk_estimate: float | None = None,
) -> VerificationResult:
    duration_ms = (
        max(0, int((time.monotonic() - started_at) * 1000)) if started_at > 0 else 0
    )
    return VerificationResult(
        verification_id=new_id("verification"),
        run_id=target.run_id,
        iteration_id=target.iteration_id,
        verifier_id=verifier_id,
        verifier_version=verifier_version,
        target_ref=target.target_ref,
        status=status,
        score=score,
        findings=list(findings),
        evidence_refs=list(evidence_refs),
        false_accept_risk_estimate=false_accept_risk_estimate,
        duration_ms=duration_ms,
    )
