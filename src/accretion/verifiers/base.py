from __future__ import annotations

from typing import Protocol

from accretion.contracts import VerificationContext, VerificationResult, VerificationTarget


class Verifier(Protocol):
    """Provider-independent deterministic verifier contract."""

    verifier_id: str
    verifier_version: str

    async def verify(
        self, target: VerificationTarget, context: VerificationContext
    ) -> VerificationResult: ...
