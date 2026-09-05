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
    # Added by the v0.4 freeze delta (5 Sep 2026, ADR-060 and ADR-061), not by M0. The
    # rollout result is ``shr`` and not ``srr`` so that it reads beside ``shd`` as the
    # record that scores one, and the activation is ``rac`` beside the ``rmv`` version it
    # releases and the ``rpr`` report that authorised it.
    "shadow_rollout_result": "shr",
    "router_activation": "rac",
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


def has_prefix(value: str, kind: str) -> bool:
    return value.startswith(f"{_PREFIXES[kind]}_") and len(value) == len(_PREFIXES[kind]) + 27
