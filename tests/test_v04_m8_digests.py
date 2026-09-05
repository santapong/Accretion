"""M8: the seven pre-v0.4 JSON digest sites, converged or proven byte-frozen.

Every byte-equality claim below is made against a payload the repository already commits
— the built-in governance plugin row, the five built-in workflow templates, a proposal
from the real fragment planner, a discovery snapshot from the real MCP manager, the three
bundled plugin manifests, and the ten frozen live-sample assignments — and never against a
synthetic ASCII dict invented to make the claim come out true.

Two kinds of assertion do the work, and they fail to different mutations:

* The **pinned digests** are literal hex, not re-derived. Change ``_LEGACY_SEPARATORS`` in
  ``accretion.digests`` (or the separators in ``contracts/canonical.py``) and these fail
  immediately, which is the point of writing them out.
* The **non-ASCII probes** call the real site function and assert it still returns the
  *legacy* bytes. Converge one of the four frozen sites by hand and its probe fails, with
  no fixture edit able to hide it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from accretion.benchmark import AcrArchRunner
from accretion.contracts import (
    CapabilityRequest,
    ExpectedHorizon,
    McpDiscoveryPolicy,
    McpServerDefinition,
    RiskLevel,
    Task,
    TaskBudgets,
    TaskEnvelope,
    TaskProfile,
    TaskType,
)
from accretion.contracts.canonical import CanonicalizationError, canonical_json, content_hash
from accretion.digests import legacy_json_bytes, legacy_json_digest
from accretion.experience.embedding import canonical_digest
from accretion.governance import approval_binding, seed_governance
from accretion.ids import new_id
from accretion.live_sample import expected_artifact, select_live_sample, verify_artifact
from accretion.mcp.manager import RemoteMcpManager
from accretion.mcp.remote_client import RemoteDiscovery
from accretion.orchestration.fragments import FragmentWorkflowPlanner
from accretion.orchestration.validator import GraphValidator
from accretion.persistence.store import MemoryStore
from accretion.plugins.manifest import canonical_manifest_digest, parse_manifest
from accretion.templates import ALL_TEMPLATES, compute_template_checksum

GOVERNANCE_PLUGIN_ID = "accretion-core-governance"
GOVERNANCE_PLUGIN_VERSION = "1.0.0"

# The checksum on the immutable `plugins` row for accretion-core-governance@1.0.0, as
# every deployment that has ever run `seed_governance` already persisted it. `upsert_plugin`
# rejects drift for an existing (plugin_id, version), so this literal is the whole reason
# the convergence at that site had to be proven rather than assumed.
GOVERNANCE_PLUGIN_CHECKSUM = "3328cb725381ba3162caa2d189cebf631518a953d3e16e4d054887f5fcb87f3a"

# The five built-in template checksums, persisted on every workflow_templates row and
# re-verified by `templates.instantiate_run_graph` and `RunManager` before a run starts.
BUILTIN_TEMPLATE_CHECKSUMS = {
    "direct-v1": "2f7a892ca43ee950e9aa34966f9efc0ced74cc9bcec8cd47e4a94167ca24336a",
    "feedback-loop-v1": "4781dd511c809b96bc271bbcab8105216a54ae4dd2af0e63e5e521222a4ddd57",
    "fixed-graph-v1": "bc414c04ddd55cf0e0e81586049fc6efb9515d00f87e3c2f603b3be1d62d6eb4",
    "hybrid-rd-v1": "f0e2785bcf18d9f3549a16951fabbe57e12fdf2fe211cfb55b5cc61b96af8174",
    "safe-unknown-v1": "676da1b8eebcb8901ac4d02f948e4c25397cfc21add5b7a77cbbca5ce76591fe",
}

# The three bundled plugin manifests, digested through `experience.embedding.canonical_digest`
# — the site M8 converged. If the convergence moved a byte, these move with it.
BUNDLED_PLUGIN_ROOT = (
    Path(__file__).resolve().parents[1] / "src" / "accretion" / "plugins" / "bundled"
)


def build_task_and_profile() -> tuple[Task, TaskProfile]:
    """A task/profile pair with the shape the fragment planner is exercised with elsewhere."""

    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=new_id("project"),
            objective="Exercise the M8 digest convergence proofs.",
            task_type=TaskType.REVIEW,
            risk_level=RiskLevel.LOW,
            allowed_capabilities=["repo.read"],
            budgets=TaskBudgets(max_parallel_runs=1),
        ),
        prompt_contract_id=new_id("prompt"),
    )
    profile = TaskProfile(
        profile_id=new_id("profile"),
        task_id=task.envelope.task_id,
        complexity=0.5,
        structure_certainty=0.5,
        feedback_dependency=0.2,
        dependency_complexity=0.5,
        parallelism_potential=0.2,
        uncertainty=0.5,
        verifier_strength=0.8,
        risk=RiskLevel.LOW,
        irreversible_actions=False,
        expected_horizon=ExpectedHorizon.MEDIUM,
        profile_confidence=0.9,
        semantic_rationale="M8 digest fixture",
    )
    return task, profile


def normalized_graph_payload(proposal: object) -> dict[str, object]:
    """Rebuild the exact dict `GraphValidator.normalized_hash` hashes.

    Duplicated from the site on purpose: comparing the site's *output* against a digest of
    this payload is what proves the site still spells the bytes the frozen way.
    """

    nodes = sorted(proposal.nodes, key=lambda item: item.local_id)  # type: ignore[attr-defined]
    edges = sorted(proposal.edges, key=lambda item: item.local_id)  # type: ignore[attr-defined]
    return {
        "nodes": [item.model_dump(mode="json", exclude={"schema_version"}) for item in nodes],
        "edges": [item.model_dump(mode="json", exclude={"schema_version"}) for item in edges],
        "capabilities": sorted(proposal.required_capabilities),  # type: ignore[attr-defined]
        "verifiers": sorted(proposal.expected_verifiers),  # type: ignore[attr-defined]
        "gates": sorted(proposal.expected_approval_gates),  # type: ignore[attr-defined]
    }


class StubRemoteClient:
    """`_snapshot` never touches the transport; a stub keeps the test off the network."""


class StubEndpointPolicy:
    """`_snapshot` never validates an endpoint either."""

    async def validate(self, endpoint: str) -> str:
        return endpoint


def build_mcp_manager() -> RemoteMcpManager:
    return RemoteMcpManager(
        store=MemoryStore(),
        client=StubRemoteClient(),  # type: ignore[arg-type]
        endpoint_policy=StubEndpointPolicy(),  # type: ignore[arg-type]
    )


def build_mcp_server() -> McpServerDefinition:
    return McpServerDefinition(
        mcp_server_id="mcps_m8digestfixture000",
        workspace_id="ws_m8_digests",
        connector_id="conndef_m8_digests",
        name="M8 digest fixture",
        endpoint="https://mcp.example.test/mcp",
        owner_principal_id="prin_m8_digests",
        discovery_policy=McpDiscoveryPolicy(default_ttl_ms=60_000),
    )


def build_discovery(server: McpServerDefinition) -> RemoteDiscovery:
    return RemoteDiscovery(
        protocol_version=sorted(server.protocol_versions)[0],
        server_info={"name": "fake-remote", "version": "1.0.0"},
        tools=[{"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}}],
        resources=[{"uri": "https://mcp.example.test/readme", "name": "readme"}],
        resource_templates=[],
        prompts=[{"name": "summarize", "description": "Summarize text"}],
        cache_hints={
            kind: (60_000, "private")
            for kind in ("tools", "resources", "resource_templates", "prompts")
        },
    )


def snapshot_content_payload(snapshot: object) -> dict[str, object]:
    """Rebuild the exact dict the MCP manager hashes into `content_sha256`."""

    return {
        "protocol_version": snapshot.protocol_version,  # type: ignore[attr-defined]
        "server_info": snapshot.server_info,  # type: ignore[attr-defined]
        "tools": snapshot.tools,  # type: ignore[attr-defined]
        "resources": snapshot.resources,  # type: ignore[attr-defined]
        "resource_templates": snapshot.resource_templates,  # type: ignore[attr-defined]
        "prompts": snapshot.prompts,  # type: ignore[attr-defined]
        "cache_hints": {
            key: value.model_dump(mode="json")
            for key, value in snapshot.cache_hints.items()  # type: ignore[attr-defined]
        },
        "schema_errors": snapshot.schema_errors,  # type: ignore[attr-defined]
    }


def build_capability_request(arguments: dict[str, object]) -> CapabilityRequest:
    """Fixed ids, so the resulting binding is a constant this file can pin."""

    return CapabilityRequest(
        request_id="capreq_m8digestfixture0",
        run_id="run_m8digestfixture000",
        node_id="run_m8digestfixture000:act",
        capability_id="accretion.echo",
        capability_version="1.0.0",
        arguments=arguments,
        declared_reason="M8 digest convergence proof",
        idempotency_key="m8-digest-key",
    )


# ---------------------------------------------------------------------------
# Converged: governance.py seed_governance (the built-in plugin checksum)
# ---------------------------------------------------------------------------


async def test_built_in_governance_plugin_payload_hashes_identically_under_canonical_json() -> None:
    store = MemoryStore()
    await seed_governance(store)

    plugin = next(
        item
        for item in await store.list_plugins()
        if item.plugin_id == GOVERNANCE_PLUGIN_ID and item.version == GOVERNANCE_PLUGIN_VERSION
    )
    payload = {
        "plugin_id": GOVERNANCE_PLUGIN_ID,
        "version": GOVERNANCE_PLUGIN_VERSION,
        "capabilities": list(plugin.capability_refs),
        "skills": list(plugin.skill_refs),
    }

    # The payload is four code literals, so the domain is closed and entirely ASCII: this
    # is the proof that the convergence is a no-op rather than a rehash.
    assert legacy_json_bytes(payload) == canonical_json(payload)
    assert plugin.checksum == GOVERNANCE_PLUGIN_CHECKSUM
    assert hashlib.sha256(canonical_json(payload)).hexdigest() == GOVERNANCE_PLUGIN_CHECKSUM


async def test_seeding_governance_over_a_store_that_already_holds_the_plugin_succeeds() -> None:
    store = MemoryStore()
    await seed_governance(store)
    first = next(
        item for item in await store.list_plugins() if item.plugin_id == GOVERNANCE_PLUGIN_ID
    )

    # `upsert_plugin` refuses any drift for an existing (plugin_id, version), so a second
    # seed is the deployment-time detector for a moved checksum. It must not raise.
    await seed_governance(store)

    second = next(
        item for item in await store.list_plugins() if item.plugin_id == GOVERNANCE_PLUGIN_ID
    )
    assert second.checksum == first.checksum == GOVERNANCE_PLUGIN_CHECKSUM


# ---------------------------------------------------------------------------
# Converged: experience/embedding.py canonical_digest
# ---------------------------------------------------------------------------


def test_experience_canonical_digest_agrees_with_canonical_json_on_non_ascii_payloads() -> None:
    # `canonical_digest` already passed ensure_ascii=False, which is why it is the one
    # site whose agreement holds for *every* payload rather than only the committed ones.
    payloads: list[object] = [
        {"path": "pyproject.toml", "sha256": "0" * 64},
        [("task-family:review:no-manifest", 3), ("text:unigram:refactor", 1)],
        {"version": "experience-embedding-v1", "features": [("profile:risk:low", 2)]},
        {"objective": "ตรวจสอบสัญญา", "label": "emoji ✅"},
        {"count": 1, "ratio": 1.5, "flag": True, "missing": None},
    ]
    for payload in payloads:
        assert canonical_digest(payload) == hashlib.sha256(canonical_json(payload)).hexdigest()

    # The narrowing is deliberate: the two classes ``json.dumps`` tolerated but canonical JSON
    # has no form for are refused loudly rather than digested.
    for refused in ({1: "a"}, {"x": float("nan")}):
        with pytest.raises(CanonicalizationError):
            canonical_digest(refused)

    # ``canonical_json`` and not ``content_hash``: a top-level ``content_hash`` key in
    # arbitrary parsed content must still be committed to by this digest.
    with_hash = {"content_hash": "deadbeef", "body": "x"}
    assert canonical_digest(with_hash) == hashlib.sha256(canonical_json(with_hash)).hexdigest()
    assert canonical_digest(with_hash) != content_hash(with_hash)


def test_every_bundled_plugin_manifest_digest_survives_the_embedding_convergence() -> None:
    manifests = sorted(BUNDLED_PLUGIN_ROOT.glob("*/plugin.json"))
    assert len(manifests) == 3

    for path in manifests:
        manifest = parse_manifest(path.read_text(encoding="utf-8"))
        payload = manifest.model_dump(
            mode="json",
            exclude={
                "signature": True,
                "capabilities": {"__all__": {"created_at"}},
                "skills": {"__all__": {"created_at"}},
            },
        )
        assert legacy_json_bytes(payload) == canonical_json(payload)
        assert canonical_manifest_digest(manifest) == hashlib.sha256(
            canonical_json(payload)
        ).hexdigest()


# ---------------------------------------------------------------------------
# Converged: live_sample.py (a prompt, not a digest)
# ---------------------------------------------------------------------------


def test_every_frozen_live_sample_assignment_serializes_to_identical_prompt_bytes() -> None:
    assignments = select_live_sample(AcrArchRunner().tasks())
    assert len(assignments) == 10

    for assignment in assignments:
        expected = expected_artifact(assignment)
        assert legacy_json_bytes(expected) == canonical_json(expected)


def test_live_sample_verification_accepts_either_json_spelling_of_a_non_ascii_expectation(
    tmp_path: Path,
) -> None:
    # The site carries no persisted digest: `verify_artifact` compares *parsed* objects,
    # so the escaping of the prompt cannot change a verdict. That is what makes converging
    # a site with an open string domain safe here and nowhere else in this file.
    expected = {"benchmark_task_id": "acr-thai", "result": "PASS", "title": "ตรวจสอบ ✅"}
    assert legacy_json_bytes(expected) != canonical_json(expected)

    escaped = tmp_path / "escaped.json"
    escaped.write_bytes(legacy_json_bytes(expected))
    literal = tmp_path / "literal.json"
    literal.write_bytes(canonical_json(expected))

    assert verify_artifact(escaped, expected) == hashlib.sha256(
        legacy_json_bytes(expected)
    ).hexdigest()
    assert verify_artifact(literal, expected) == hashlib.sha256(
        canonical_json(expected)
    ).hexdigest()


# ---------------------------------------------------------------------------
# Byte-frozen: templates.py compute_template_checksum
# ---------------------------------------------------------------------------


def test_every_built_in_template_payload_is_byte_identical_under_canonical_json() -> None:
    assert len(ALL_TEMPLATES) == 5

    for template in ALL_TEMPLATES:
        payload = template.model_dump(
            mode="json",
            exclude={"template_record_id", "checksum", "status", "created_at"},
        )
        # Recorded, not acted on: the committed templates agree, but the site stays legacy
        # because a materialized template body carries planner prose.
        assert legacy_json_bytes(payload) == canonical_json(payload)
        assert compute_template_checksum(template) == template.checksum


def test_every_built_in_template_checksum_is_the_value_earlier_releases_persisted() -> None:
    assert {item.template_id: item.checksum for item in ALL_TEMPLATES} == (
        BUILTIN_TEMPLATE_CHECKSUMS
    )
    for template in ALL_TEMPLATES:
        assert compute_template_checksum(template) == BUILTIN_TEMPLATE_CHECKSUMS[
            template.template_id
        ]


def test_a_template_carrying_non_ascii_node_text_keeps_the_legacy_checksum() -> None:
    direct = next(item for item in ALL_TEMPLATES if item.template_id == "direct-v1")
    translated = direct.model_copy(
        update={
            "nodes": [
                direct.nodes[0].model_copy(update={"label": "ดำเนินการ ✅"}),
                *direct.nodes[1:],
            ]
        }
    )
    payload = translated.model_dump(
        mode="json",
        exclude={"template_record_id", "checksum", "status", "created_at"},
    )

    assert legacy_json_bytes(payload) != canonical_json(payload)
    assert compute_template_checksum(translated) == legacy_json_digest(payload)
    assert compute_template_checksum(translated) != hashlib.sha256(
        canonical_json(payload)
    ).hexdigest()


# ---------------------------------------------------------------------------
# Byte-frozen: governance.py approval_binding
# ---------------------------------------------------------------------------


def test_an_ascii_approval_binding_is_byte_identical_under_canonical_json() -> None:
    request = build_capability_request({"message": "hello"})
    bound = {
        "run_id": request.run_id,
        "node_id": request.node_id,
        "capability_id": request.capability_id,
        "capability_version": request.capability_version,
        "arguments": request.arguments,
        "idempotency_key": request.idempotency_key,
    }

    assert legacy_json_bytes(bound) == canonical_json(bound)
    assert approval_binding(request) == f"capability:{legacy_json_digest(bound)}"
    assert approval_binding(request) == (
        "capability:c8159242e6bc09ecee547a7e2209496987f9d8e204c177dd6b5a53ae50687c93"
    )


def test_an_approval_binding_over_non_ascii_arguments_keeps_the_legacy_digest() -> None:
    # `CapabilityRequest.arguments` is arbitrary caller-supplied JSON, and this digest is
    # persisted as an approval's `native_request_id`. Converging the site would orphan
    # every approval an earlier release recorded for a non-ASCII argument.
    request = build_capability_request({"message": "สวัสดี ✅"})
    bound = {
        "run_id": request.run_id,
        "node_id": request.node_id,
        "capability_id": request.capability_id,
        "capability_version": request.capability_version,
        "arguments": request.arguments,
        "idempotency_key": request.idempotency_key,
    }

    assert legacy_json_bytes(bound) != canonical_json(bound)
    assert approval_binding(request) == f"capability:{legacy_json_digest(bound)}"
    assert approval_binding(request) != (
        f"capability:{hashlib.sha256(canonical_json(bound)).hexdigest()}"
    )


# ---------------------------------------------------------------------------
# Byte-frozen: orchestration/validator.py normalized_hash
# ---------------------------------------------------------------------------


def test_a_planned_proposal_normalizes_to_identical_bytes_under_canonical_json() -> None:
    task, profile = build_task_and_profile()
    proposal = FragmentWorkflowPlanner().propose(task, profile)
    payload = normalized_graph_payload(proposal)

    assert legacy_json_bytes(payload) == canonical_json(payload)
    assert GraphValidator.normalized_hash(proposal) == legacy_json_digest(payload)
    assert GraphValidator.normalized_hash(proposal) == (
        "21301ee63fb571fe8b1bacd16ab41051bde72d010317cccab04609d8eaa9840a"
    )


def test_a_proposal_with_a_non_ascii_node_objective_keeps_the_legacy_graph_hash() -> None:
    # `DynamicWorkflowNodeSpec.objective` is four thousand characters of free planner text
    # and the digest is persisted as `GraphValidationResult.normalized_graph_hash`.
    task, profile = build_task_and_profile()
    proposal = FragmentWorkflowPlanner().propose(task, profile)
    translated = proposal.model_copy(
        update={
            "nodes": [
                proposal.nodes[0].model_copy(update={"objective": "ทดสอบกราฟ ✅"}),
                *proposal.nodes[1:],
            ]
        }
    )
    payload = normalized_graph_payload(translated)

    assert legacy_json_bytes(payload) != canonical_json(payload)
    assert GraphValidator.normalized_hash(translated) == legacy_json_digest(payload)
    assert (
        GraphValidator.normalized_hash(translated)
        != hashlib.sha256(canonical_json(payload)).hexdigest()
    )


# ---------------------------------------------------------------------------
# Byte-frozen: mcp/manager.py discovery snapshot content_sha256
# ---------------------------------------------------------------------------


def test_an_ascii_discovery_snapshot_is_byte_identical_under_canonical_json() -> None:
    server = build_mcp_server()
    snapshot = build_mcp_manager()._snapshot(server, None, build_discovery(server))
    payload = snapshot_content_payload(snapshot)

    assert legacy_json_bytes(payload) == canonical_json(payload)
    assert snapshot.content_sha256 == legacy_json_digest(payload)
    assert snapshot.content_sha256 == (
        "f476e8984c363930e3fd614745dc2a5389e63d91d409414db5a6f0467cd5a3dd"
    )


def test_a_discovery_snapshot_over_non_ascii_server_info_keeps_the_legacy_digest() -> None:
    # `server_info`, tool descriptions, resource names and prompt descriptions all come
    # from a *remote* server, which makes this the most likely non-ASCII payload in the
    # repository — and `content_sha256` is persisted on the snapshot row.
    server = build_mcp_server()
    discovery = replace(
        build_discovery(server),
        server_info={"name": "リモート ✅", "version": "1.0.0"},
    )
    snapshot = build_mcp_manager()._snapshot(server, None, discovery)
    payload = snapshot_content_payload(snapshot)

    assert legacy_json_bytes(payload) != canonical_json(payload)
    assert snapshot.content_sha256 == legacy_json_digest(payload)
    assert snapshot.content_sha256 != hashlib.sha256(canonical_json(payload)).hexdigest()


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


def test_the_legacy_helper_escapes_non_ascii_where_canonical_json_writes_it_literally() -> None:
    payload = {"label": "ไทย"}

    assert legacy_json_bytes(payload) == b'{"label":"\\u0e44\\u0e17\\u0e22"}'
    assert canonical_json(payload) == '{"label":"ไทย"}'.encode()
    assert legacy_json_digest(payload) == hashlib.sha256(legacy_json_bytes(payload)).hexdigest()


def test_the_legacy_helper_reproduces_the_expression_the_seven_sites_hand_rolled() -> None:
    payload = {"b": [1, 2], "a": {"z": None, "y": True}, "c": "ตรวจ"}

    assert legacy_json_bytes(payload) == json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode()
