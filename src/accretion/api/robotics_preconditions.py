"""Strict request preconditions for the v0.5 simulation API.

These parsers validate syntax only. The domain store must still compare revisions
and request digests atomically with the actual write or admission.
"""

from __future__ import annotations

import re

from accretion.robotics.errors import RoboticsError, RoboticsErrorCode

_ETAG = re.compile(r'"(0|[1-9][0-9]{0,18})"', re.ASCII)
_REQUEST_KEY = re.compile(r"[!-~]{1,128}", re.ASCII)
MAX_REVISION = (1 << 63) - 1


def parse_if_match(value: str | None, *, required: bool = True) -> int | None:
    if value is None:
        if required:
            raise RoboticsError(RoboticsErrorCode.REVISION_REQUIRED)
        return None
    match = _ETAG.fullmatch(value)
    if match is None:
        raise RoboticsError(RoboticsErrorCode.INVALID_REQUEST)
    revision = int(match[1])
    if revision > MAX_REVISION:
        raise RoboticsError(RoboticsErrorCode.INVALID_REQUEST)
    return revision


def revision_etag(revision: int) -> str:
    if type(revision) is not int or not 0 <= revision <= MAX_REVISION:
        raise ValueError("revision must be a nonnegative signed 64-bit integer")
    return f'"{revision}"'


def require_idempotency_key(value: str | None) -> str:
    if value is None:
        raise RoboticsError(RoboticsErrorCode.IDEMPOTENCY_REQUIRED)
    if _REQUEST_KEY.fullmatch(value) is None:
        raise RoboticsError(RoboticsErrorCode.INVALID_REQUEST)
    return value
