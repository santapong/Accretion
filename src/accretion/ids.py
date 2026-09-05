from __future__ import annotations

import hashlib
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
    "principal": "usr",
    "workspace_entity": "wks",
    "workspace_membership": "wsm",
    "auth_session": "aus",
    "auth_transaction": "atx",
    # SDD 6.2 writes token handles as ``tokh_``; the prefix registry is three
    # characters wide, so the canonical form is ``tkh_``.
    "token_handle": "tkh",
    "oauth_transaction": "otx",
    "secret_record": "sec",
    "mcp_server": "mcs",
    "mcp_snapshot": "mcp",
    "mcp_event": "mce",
    "plugin_version": "plv",
    "plugin_installation": "pli",
    "plugin_event": "ple",
    "evidence": "evd",
    "evidence_candidate": "evc",
    "citation_check": "cck",
    "identity_assertion": "ida",
    "enterprise_auth_grant": "eag",
    # v0.4 routing contracts (ADR-055). "uuid" in the SDD reads as "globally unique
    # opaque id", so these records carry the same prefixed base32 identity as every
    # other aggregate here rather than introducing a second identity scheme. The
    # prefix registry is three characters wide, which is why the candidate prefix is
    # ``ccd`` and not ``cnd`` — ``cnd`` is already the connector definition — and why
    # the routing receipt is ``rcp`` beside the existing ``rpl`` and ``rtc``.
    # ``ExperienceRecord`` deliberately gains no prefix: it is a projection keyed by
    # the v0.2 P7 ``experience_id``, so it reuses ``exp`` above (ADR-054 b).
    "objective_contract": "obj",
    "node_contract": "nct",
    "verification_spec": "vsp",
    "routing_request": "rrq",
    "execution_configuration": "cfg",
    "configuration_candidate": "ccd",
    "compatibility_decision": "cmp",
    "routing_receipt": "rcp",
    "independent_verification_result": "ivr",
    "failure_event": "flr",
    "router_model_version": "rmv",
    "router_training_snapshot": "rts",
    "router_promotion_report": "rpr",
    "shadow_decision": "shd",
    # `routing_overrides` is the fifteenth SDD v0.4 §13 table and the one PR2 froze no
    # contract for, so it needs a record identity even though it has no model yet. It
    # cannot reuse `override` above: that kind is already minted by `planning.py` for the
    # v0.1/v0.2 strategy override, and sharing it would mean an `ovr_` id no longer told a
    # reader — or `has_prefix`, or M2's future `RoutingOverride.ID_KIND` check — which
    # record class or which table it names. So the routing override gets its own kind.
    "routing_override": "rov",
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


def derived_id(kind: str, *parts: str) -> str:
    """Return the identifier ``kind`` deterministically derives from ``parts``.

    :func:`new_id` mints a fresh identity every call, which is exactly right for a record
    that is created once and exactly wrong for one that must be *re-derivable*: a
    compatibility decision replayed from the same registry snapshot is the same decision,
    and giving it a new id on every evaluation would make replay unprovable and idempotent
    ingestion impossible. So this function keeps :func:`new_id`'s prefix table and its
    26-character base32 body — ``has_prefix`` and every ``CanonicalContract.ID_KIND`` check
    compare a prefix and a total length, and neither may learn that a second id shape
    exists — and replaces the timestamp-plus-randomness body with the leading bits of a
    SHA-256 digest over the canonical JSON of ``parts``.

    The digest input is :func:`~accretion.contracts.canonical.canonical_json` of the parts
    *as a list*, not their concatenation. Concatenation would let ``("ab", "c")`` and
    ``("a", "bc")`` derive one id, which is how two different decisions come to share an
    identity; the JSON encoding keeps the boundary between parts inside the hash. That
    import is deliberately function-local: :mod:`accretion.contracts.canonical` imports
    :func:`has_prefix` from this module, so a module-level import here would be a cycle.

    The body carries 130 of the digest's 256 bits, which is what a 26-character base32
    encoding holds. That is the same width :func:`new_id` uses and far beyond the
    collision budget of any id space in this repository.
    """

    from accretion.contracts.canonical import canonical_json

    prefix = _PREFIXES[kind]
    digest = hashlib.sha256(canonical_json(list(parts))).digest()
    return f"{prefix}_{_encode_base32(int.from_bytes(digest), 26)}"


def has_prefix(value: str, kind: str) -> bool:
    return value.startswith(f"{_PREFIXES[kind]}_") and len(value) == len(_PREFIXES[kind]) + 27
