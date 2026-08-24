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
    "workflow_template": "wft",
    "run_graph": "rgr",
    "iteration": "itr",
    "verification": "ver",
    "acceptance_policy": "acp",
    "runtime_call": "rtc",
    "capability": "cap",
    "skill": "skl",
    "plugin": "plg",
    "policy": "pol",
    "capability_request": "cpr",
    "benchmark_run": "bnr",
    "architecture_metric": "acm",
    "workflow_proposal": "wfp",
    "graph_validation": "gvl",
    "graph_revision": "grv",
    "replan_request": "rpl",
    "runtime_decision": "rtd",
    "search": "src",
    "search_candidate": "scn",
    "candidate_score": "scr",
    "search_promotion": "spr",
    "experience": "exp",
    "trajectory_segment": "tgs",
    "experience_embedding": "emb",
    "experience_query": "exq",
    "experience_match": "exm",
    "experience_selection": "exs",
    "moderation_action": "mod",
    "trajectory_seed": "tsd",
    "conndef": "cnd",
    "conn": "con",
    "capbind": "cbd",
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
