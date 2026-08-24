from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_SECRET_KEY = re.compile(
    r"(token|secret|password|authorization|api[_-]?key|cookie"
    r"|code[_-]?verifier|code[_-]?challenge|nonce|\bstate\b|session[_-]?id)",
    re.I,
)
# Opaque indirection handles are not secrets and must stay readable: INV3-011 needs
# them as the correlation key for connection, refresh, and revocation audit events.
_SAFE_KEY = re.compile(r"token[_-]?handle", re.I)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_LIKELY_KEY = re.compile(r"\b(?:sk|api|key)-[A-Za-z0-9_-]{16,}\b")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")


def redact_text(value: str) -> str:
    value = _BEARER.sub("Bearer [REDACTED]", value)
    value = _JWT.sub("[REDACTED]", value)
    return _LIKELY_KEY.sub("[REDACTED]", value)


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if _SECRET_KEY.search(str(key)) and not _SAFE_KEY.search(str(key))
                else redact(item)
            )
            for key, item in value.items()
        }
    return value


def scrub_values(value: Any, secrets: Iterable[str]) -> Any:
    """Remove specific known secret values wherever they appear.

    Key-name redaction cannot catch a credential a capability chose to return under a
    harmless key, so the executor boundary also scrubs by value: it knows exactly what
    it injected. Short values are ignored to avoid mangling ordinary text.
    """

    targets = [item for item in secrets if item and len(item) >= 8]
    if not targets:
        return value
    if isinstance(value, str):
        for secret in targets:
            value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, list):
        return [scrub_values(item, targets) for item in value]
    if isinstance(value, dict):
        return {key: scrub_values(item, targets) for key, item in value.items()}
    return value
