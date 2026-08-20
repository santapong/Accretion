from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PREFIXES = {
    "project": "prj",
    "task": "tsk",
    "run": "run",
    "session": "ses",
    "workspace": "wsp",
    "artifact": "art",
    "approval": "apr",
    "event": "evt",
    "checkpoint": "chk",
    "side_effect": "sfx",
    "prompt": "pmt",
    "context": "ctx",
    "profile": "prf",
    "decision": "dec",
    "override": "ovr",
    "loop": "lop",
    "loop_execution": "lpx",
    "iteration": "itr",
    "verification": "ver",
    "acceptance_policy": "acp",
    "runtime_call": "rtc",
}


def _encode_base32(value: int, length: int) -> str:
    encoded = ["0"] * length
    for index in range(length - 1, -1, -1):
        encoded[index] = _ALPHABET[value & 31]
        value >>= 5
    return "".join(encoded)


def new_id(kind: str) -> str:
    """Return a sortable, ULID-shaped identifier with an operator-friendly prefix."""
    prefix = _PREFIXES[kind]
    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    randomness = int.from_bytes(os.urandom(10))
    return f"{prefix}_{_encode_base32(timestamp_ms, 10)}{_encode_base32(randomness, 16)}"


def has_prefix(value: str, kind: str) -> bool:
    return value.startswith(f"{_PREFIXES[kind]}_") and len(value) == len(_PREFIXES[kind]) + 27
