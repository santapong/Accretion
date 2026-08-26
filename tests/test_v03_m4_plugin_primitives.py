from __future__ import annotations

import base64
import json
from typing import Any, get_type_hints

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from accretion.contracts import (
    Capability,
    CapabilityBackend,
    MetaPlugin,
    MetaPluginManifest,
    MetaSkill,
    PluginConnectorRequirement,
    PluginInstallation,
    PluginSignature,
    PluginSignatureAlgorithm,
    PluginState,
    PluginTrustLevel,
    RiskLevel,
)
from accretion.governance import (
    seed_governance,
)
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore, StateStore
from accretion.plugins.dependencies import (
    check_constraints,
    parse_version,
    resolve_connector_requirements,
    satisfies_constraint,
    unsatisfied_required_connectors,
    validate_acyclic,
)
from accretion.plugins.errors import (
    PluginDependencyError,
    PluginManagerError,
    PluginManifestError,
    PluginSignatureError,
    PluginTrustError,
)
from accretion.plugins.manifest import canonical_manifest_digest, parse_manifest
from accretion.plugins.trust import (
    PluginTrustVerifier,
    load_trusted_keys,
    manifest_min_trust,
    min_trust_for_risk,
    satisfies,
)

# The only deletion method the state store is permitted to expose. AC3-PLG-05 turns
# "removal cannot delete evidence" into a structural fact rather than a behavioural hope.
ALLOWED_DELETION_METHODS = frozenset({"delete_secret_record"})
_DELETION_PREFIXES = ("delete", "remove", "purge", "drop", "erase", "truncate")


def manifest_payload(
    plugin_id: str = "acme-sample",
    version: str = "1.0.0",
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": plugin_id,
        "version": version,
        "name": "Acme Sample",
        "description": "A conformant SDD 9.1 package declaration.",
        "capabilities": [
            {
                "capability_id": "acme.sample.read",
                "version": version,
                "risk": "LOW",
                "backend": "PYTHON",
                "required_permissions": ["acme.read"],
            }
        ],
        "skills": [
            {
                "skill_id": "acme.sample.skill",
                "version": version,
                "description": "Use the sample capability.",
                "instructions": "Call acme.sample.read.",
                "required_capabilities": ["acme.sample.read"],
            }
        ],
        "required_connectors": [{"connector_id": "github", "scopes": ["repo:read"]}],
        "optional_connectors": [{"connector_id": "slack", "scopes": []}],
        "verifiers": ["acme.sample.verifier"],
        "policies": ["local-capability-policy"],
        "provider_projections": {"CLAUDE": "projections/claude.json"},
    }
    payload.update(overrides)
    return payload


async def setup_plugin_store() -> tuple[MemoryStore, MetaPluginManifest]:
    """Module-local async builder; this repository has no conftest."""

    store = MemoryStore()
    await seed_governance(store)
    return store, parse_manifest(manifest_payload())


def signed_manifest(
    private_key: Ed25519PrivateKey,
    key_id: str,
    **overrides: Any,
) -> MetaPluginManifest:
    manifest = parse_manifest(manifest_payload(**overrides))
    digest = canonical_manifest_digest(manifest)
    signature = base64.b64encode(private_key.sign(digest.encode())).decode()
    return manifest.model_copy(
        update={
            "signature": PluginSignature(
                algorithm=PluginSignatureAlgorithm.ED25519,
                key_id=key_id,
                value=signature,
            )
        }
    )


def encoded_public_key(private_key: Ed25519PrivateKey) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    return base64.b64encode(
        private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()


# --------------------------------------------------------------------------------------
# AC3-PLG-05 structural invariant
# --------------------------------------------------------------------------------------


def test_state_store_exposes_no_deletion_method_beyond_secrets() -> None:
    offenders = {
        name
        for name in dir(StateStore)
        if not name.startswith("_")
        and name.lower().startswith(_DELETION_PREFIXES)
        and name not in ALLOWED_DELETION_METHODS
    }
    assert offenders == set(), (
        "the state store must not be able to delete evidence; "
        f"unexpected deletion method(s): {sorted(offenders)}"
    )
    assert "delete_secret_record" in dir(StateStore)


def test_store_implementations_expose_no_extra_deletion_methods() -> None:
    for implementation in (MemoryStore, StateStore):
        offenders = {
            name
            for name in dir(implementation)
            if not name.startswith("_")
            and name.lower().startswith(_DELETION_PREFIXES)
            and name not in ALLOWED_DELETION_METHODS
        }
        assert offenders == set(), f"{implementation.__name__} exposes {sorted(offenders)}"


def test_plugin_audit_event_row_is_append_only() -> None:
    from accretion.persistence.models import PluginAuditEventRow

    hints = get_type_hints(PluginAuditEventRow)
    assert "updated_at" not in hints
    assert "created_at" in hints


# --------------------------------------------------------------------------------------
# manifest parsing and digest stability
# --------------------------------------------------------------------------------------


def test_parse_manifest_accepts_a_conformant_package() -> None:
    manifest = parse_manifest(manifest_payload())
    assert manifest.id == "acme-sample"
    assert manifest.signature is None
    assert [item.capability_id for item in manifest.capabilities] == ["acme.sample.read"]
    assert manifest.provider_projections == {"CLAUDE": "projections/claude.json"}


def test_parse_manifest_accepts_raw_json_bytes() -> None:
    payload = json.dumps(manifest_payload()).encode()
    from_bytes = parse_manifest(payload)
    from_mapping = parse_manifest(manifest_payload())
    assert from_bytes.model_dump(mode="json", exclude={"capabilities", "skills"}) == (
        from_mapping.model_dump(mode="json", exclude={"capabilities", "skills"})
    )
    assert canonical_manifest_digest(from_bytes) == canonical_manifest_digest(from_mapping)


def test_parse_manifest_rejects_malformed_json() -> None:
    with pytest.raises(PluginManifestError, match="not valid JSON"):
        parse_manifest("{not json")


def test_parse_manifest_rejects_unknown_fields() -> None:
    with pytest.raises(PluginManifestError, match="schema validation"):
        parse_manifest(manifest_payload(surprise="extra"))


def test_parse_manifest_rejects_duplicate_capability_ids() -> None:
    payload = manifest_payload()
    payload["capabilities"] = payload["capabilities"] * 2
    with pytest.raises(PluginManifestError, match="duplicate capability id"):
        parse_manifest(payload)


def test_parse_manifest_rejects_duplicate_skill_ids() -> None:
    payload = manifest_payload()
    payload["skills"] = payload["skills"] * 2
    with pytest.raises(PluginManifestError, match="duplicate skill id"):
        parse_manifest(payload)


def test_parse_manifest_rejects_connector_in_both_lists() -> None:
    payload = manifest_payload()
    payload["optional_connectors"] = [{"connector_id": "github", "scopes": []}]
    with pytest.raises(PluginManifestError, match="both required and optional"):
        parse_manifest(payload)


@pytest.mark.parametrize(
    "capability_id",
    ["Acme.Sample", "acme..sample", "1acme", "acme sample", "acme-", "_acme"],
)
def test_parse_manifest_rejects_non_canonical_capability_ids(capability_id: str) -> None:
    payload = manifest_payload()
    payload["capabilities"][0]["capability_id"] = capability_id
    with pytest.raises(PluginManifestError, match="canonical capability id"):
        parse_manifest(payload)


@pytest.mark.parametrize(
    "path",
    ["../escape.json", "/etc/passwd", "nested/../../escape.json", "~/escape.json", "C:/x.json"],
)
def test_parse_manifest_rejects_escaping_projection_paths(path: str) -> None:
    payload = manifest_payload()
    payload["provider_projections"] = {"CLAUDE": path}
    with pytest.raises(PluginManifestError):
        parse_manifest(payload)


def test_parse_manifest_allows_nested_projection_paths() -> None:
    payload = manifest_payload()
    payload["provider_projections"] = {"CODEX": "projections/nested/codex.json"}
    assert parse_manifest(payload).provider_projections["CODEX"].endswith("codex.json")


def test_manifest_digest_is_stable_and_signature_independent() -> None:
    manifest = parse_manifest(manifest_payload())
    digest = canonical_manifest_digest(manifest)
    assert len(digest) == 64
    # Two independently constructed manifests digest identically: the nested
    # ``created_at`` stamps are construction noise, not declared content.
    assert canonical_manifest_digest(parse_manifest(manifest_payload())) == digest
    # Key ordering must not perturb the digest.
    reordered = dict(reversed(list(manifest_payload().items())))
    assert canonical_manifest_digest(parse_manifest(reordered)) == digest
    # A detached signature is a claim *about* the digest, so it cannot change it.
    pinned = manifest.model_copy(
        update={"signature": PluginSignature(value=digest)},
    )
    assert canonical_manifest_digest(pinned) == digest
    # Any content change does move it.
    assert canonical_manifest_digest(parse_manifest(manifest_payload(version="1.0.1"))) != digest


def test_provider_projection_types_stay_asymmetric() -> None:
    """The manifest maps provider -> path; Capability maps provider -> arbitrary JSON."""

    manifest = parse_manifest(manifest_payload())
    assert all(isinstance(value, str) for value in manifest.provider_projections.values())
    capability = Capability(
        capability_id="acme.sample.read",
        version="1.0.0",
        backend=CapabilityBackend.PYTHON,
        provider_projections={"CLAUDE": {"tool_name": "acme_read"}},
    )
    assert capability.provider_projections["CLAUDE"] == {"tool_name": "acme_read"}


# --------------------------------------------------------------------------------------
# trust
# --------------------------------------------------------------------------------------


def test_min_trust_for_risk_rises_with_risk() -> None:
    assert min_trust_for_risk(RiskLevel.LOW) is PluginTrustLevel.UNVERIFIED_DEV
    assert min_trust_for_risk(RiskLevel.MEDIUM) is PluginTrustLevel.UNVERIFIED_DEV
    assert min_trust_for_risk(RiskLevel.HIGH) is PluginTrustLevel.WORKSPACE_APPROVED
    assert min_trust_for_risk(RiskLevel.CRITICAL) is PluginTrustLevel.SIGNED_THIRD_PARTY
    assert satisfies(PluginTrustLevel.BUILTIN, PluginTrustLevel.SIGNED_THIRD_PARTY)
    assert not satisfies(PluginTrustLevel.UNVERIFIED_DEV, PluginTrustLevel.WORKSPACE_APPROVED)
    assert not satisfies(PluginTrustLevel.BLOCKED, PluginTrustLevel.UNVERIFIED_DEV)


def test_manifest_min_trust_uses_the_riskiest_capability() -> None:
    payload = manifest_payload()
    payload["capabilities"].append(
        {
            "capability_id": "acme.sample.write",
            "version": "1.0.0",
            "risk": "CRITICAL",
            "backend": "PYTHON",
        }
    )
    assert manifest_min_trust(parse_manifest(payload)) is PluginTrustLevel.SIGNED_THIRD_PARTY


def test_builtin_ids_are_trusted_without_a_signature() -> None:
    verifier = PluginTrustVerifier(builtin_ids=["acme-sample"])
    manifest = parse_manifest(manifest_payload())
    assert verifier.verify(manifest) is PluginTrustLevel.BUILTIN


def test_builtin_hash_pin_must_match_the_manifest() -> None:
    manifest = parse_manifest(manifest_payload())
    digest = canonical_manifest_digest(manifest)
    verifier = PluginTrustVerifier(builtin_ids=["acme-sample"])
    pinned = manifest.model_copy(update={"signature": PluginSignature(value=digest)})
    assert verifier.verify(pinned) is PluginTrustLevel.BUILTIN
    tampered = manifest.model_copy(update={"signature": PluginSignature(value="0" * 64)})
    with pytest.raises(PluginSignatureError, match="does not match"):
        verifier.verify(tampered)


def test_expected_digest_pin_is_enforced() -> None:
    verifier = PluginTrustVerifier(builtin_ids=["acme-sample"])
    with pytest.raises(PluginSignatureError, match="pinned"):
        verifier.verify(parse_manifest(manifest_payload()), expected_digest="0" * 64)


def test_unsigned_package_requires_the_dev_escape_hatch() -> None:
    manifest = parse_manifest(manifest_payload())
    with pytest.raises(PluginTrustError, match="UNVERIFIED_DEV"):
        PluginTrustVerifier().verify(manifest)
    assert (
        PluginTrustVerifier(allow_unverified_dev=True).verify(manifest)
        is PluginTrustLevel.UNVERIFIED_DEV
    )


def test_sha256_pin_alone_never_attests_authorship() -> None:
    manifest = parse_manifest(manifest_payload())
    digest = canonical_manifest_digest(manifest)
    pinned = manifest.model_copy(update={"signature": PluginSignature(value=digest)})
    verifier = PluginTrustVerifier(allow_unverified_dev=True)
    assert verifier.verify(pinned) is PluginTrustLevel.UNVERIFIED_DEV


def test_ed25519_signature_confers_the_keys_trust_level() -> None:
    private_key = Ed25519PrivateKey.generate()
    keys = load_trusted_keys({"acme-release": encoded_public_key(private_key)})
    verifier = PluginTrustVerifier(trusted_keys=keys)
    manifest = signed_manifest(private_key, "acme-release")
    assert verifier.verify(manifest) is PluginTrustLevel.SIGNED_THIRD_PARTY

    workspace_keys = load_trusted_keys(
        {"ws-key": f"WORKSPACE_APPROVED:{encoded_public_key(private_key)}"}
    )
    assert (
        PluginTrustVerifier(trusted_keys=workspace_keys).verify(
            signed_manifest(private_key, "ws-key")
        )
        is PluginTrustLevel.WORKSPACE_APPROVED
    )


def test_ed25519_signature_from_an_unknown_key_is_rejected() -> None:
    private_key = Ed25519PrivateKey.generate()
    manifest = signed_manifest(private_key, "not-configured")
    with pytest.raises(PluginTrustError, match="unknown key"):
        PluginTrustVerifier(trusted_keys={}).verify(manifest)


def test_ed25519_signature_over_tampered_content_is_rejected() -> None:
    private_key = Ed25519PrivateKey.generate()
    keys = load_trusted_keys({"acme-release": encoded_public_key(private_key)})
    manifest = signed_manifest(private_key, "acme-release")
    tampered = manifest.model_copy(update={"description": "silently altered"})
    with pytest.raises(PluginSignatureError, match="does not verify"):
        PluginTrustVerifier(trusted_keys=keys).verify(tampered)


def test_ed25519_signature_from_the_wrong_key_is_rejected() -> None:
    signer = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    keys = load_trusted_keys({"acme-release": encoded_public_key(other)})
    with pytest.raises(PluginSignatureError, match="does not verify"):
        PluginTrustVerifier(trusted_keys=keys).verify(signed_manifest(signer, "acme-release"))


def test_load_trusted_keys_rejects_bad_material_and_levels() -> None:
    with pytest.raises(PluginTrustError, match="valid base64"):
        load_trusted_keys({"bad": "not-base64!!"})
    with pytest.raises(PluginTrustError, match="32-byte"):
        load_trusted_keys({"short": base64.b64encode(b"too short").decode()})
    with pytest.raises(PluginTrustError, match="unknown trust level"):
        load_trusted_keys({"weird": "NONSENSE:" + base64.b64encode(b"\x00" * 32).decode()})
    with pytest.raises(PluginTrustError, match="may only confer"):
        load_trusted_keys({"builtin": "BUILTIN:" + base64.b64encode(b"\x00" * 32).decode()})


def test_verify_for_install_enforces_the_risk_floor() -> None:
    payload = manifest_payload()
    payload["capabilities"][0]["risk"] = "CRITICAL"
    manifest = parse_manifest(payload)
    verifier = PluginTrustVerifier(allow_unverified_dev=True)
    with pytest.raises(PluginTrustError, match="SIGNED_THIRD_PARTY"):
        verifier.verify_for_install(manifest)
    builtin = PluginTrustVerifier(builtin_ids=["acme-sample"])
    assert builtin.verify_for_install(manifest) is PluginTrustLevel.BUILTIN


def test_every_plugin_error_shares_one_base_class() -> None:
    for error in (
        PluginManifestError,
        PluginSignatureError,
        PluginTrustError,
        PluginDependencyError,
    ):
        assert issubclass(error, PluginManagerError)
    assert issubclass(PluginManagerError, RuntimeError)


# --------------------------------------------------------------------------------------
# dependencies
# --------------------------------------------------------------------------------------


def test_parse_version_rejects_non_semver() -> None:
    assert parse_version("1.20.3") == (1, 20, 3)
    for bad in ("1.2", "v1.2.3", "1.2.3-rc1", ""):
        with pytest.raises(PluginDependencyError):
            parse_version(bad)


@pytest.mark.parametrize(
    ("version", "constraint", "expected"),
    [
        ("1.2.3", "1.2.3", True),
        ("1.2.3", "==1.2.3", True),
        ("1.2.4", "==1.2.3", False),
        ("1.2.4", "!=1.2.3", True),
        ("1.2.3", ">=1.0.0", True),
        ("0.9.9", ">=1.0.0", False),
        ("2.0.0", ">=1.0.0,<2.0.0", False),
        ("1.9.9", ">=1.0.0,<2.0.0", True),
        ("1.9.9", "^1.0.0", True),
        ("2.0.0", "^1.0.0", False),
        ("1.2.9", "~1.2.0", True),
        ("1.3.0", "~1.2.0", False),
        ("1.2.3", ">1.2.3", False),
        ("1.2.3", "<=1.2.3", True),
    ],
)
def test_satisfies_constraint(version: str, constraint: str, expected: bool) -> None:
    assert satisfies_constraint(version, constraint) is expected


def test_satisfies_constraint_rejects_unsupported_syntax() -> None:
    for bad in ("~=1.2.3", "1.2.*", ""):
        with pytest.raises(PluginDependencyError):
            satisfies_constraint("1.2.3", bad)


def test_check_constraints_reports_missing_and_unsatisfied() -> None:
    check_constraints({"core": ">=1.0.0"}, {"core": "1.4.0"})
    with pytest.raises(PluginDependencyError, match="is not installed"):
        check_constraints({"core": ">=1.0.0"}, {})
    with pytest.raises(PluginDependencyError, match="does not satisfy"):
        check_constraints({"core": ">=2.0.0"}, {"core": "1.4.0"})


def test_validate_acyclic_orders_dependencies() -> None:
    order = validate_acyclic({"a": ["b"], "b": ["c"], "c": []})
    assert order.index("c") < order.index("b") < order.index("a")
    # A diamond is not a cycle.
    assert set(validate_acyclic({"a": ["b", "c"], "b": ["d"], "c": ["d"]})) == {"a", "b", "c", "d"}


def test_validate_acyclic_detects_cycles() -> None:
    with pytest.raises(PluginDependencyError, match="cycle"):
        validate_acyclic({"a": ["b"], "b": ["a"]})
    with pytest.raises(PluginDependencyError, match="cycle"):
        validate_acyclic({"a": ["a"]})
    with pytest.raises(PluginDependencyError, match="cycle"):
        validate_acyclic({"a": ["b"], "b": ["c"], "c": ["a"]})


def test_validate_acyclic_handles_a_deep_chain_without_recursion() -> None:
    depth = 5_000
    edges = {f"p{index}": [f"p{index + 1}"] for index in range(depth)}
    order = validate_acyclic(edges)
    assert len(order) == depth + 1
    assert order[0] == f"p{depth}"


def test_resolve_connector_requirements_marks_required_gaps() -> None:
    manifest = parse_manifest(manifest_payload())
    resolutions = resolve_connector_requirements(
        manifest,
        connections={"github": "con_123"},
        granted_scopes={"github": ["repo:read"]},
    )
    assert [item.connector_id for item in resolutions] == ["github", "slack"]
    github, slack = resolutions
    assert github.required and github.satisfied and github.connection_id == "con_123"
    assert not slack.required and not slack.satisfied
    assert unsatisfied_required_connectors(resolutions) == []


def test_missing_scope_leaves_the_required_connector_unsatisfied() -> None:
    manifest = parse_manifest(manifest_payload())
    resolutions = resolve_connector_requirements(
        manifest, connections={"github": "con_123"}, granted_scopes={"github": []}
    )
    github = resolutions[0]
    assert not github.satisfied
    assert github.missing_scopes == ["repo:read"]
    assert unsatisfied_required_connectors(resolutions) == ["github"]


def test_absent_connection_leaves_the_required_connector_unsatisfied() -> None:
    manifest = parse_manifest(manifest_payload())
    resolutions = resolve_connector_requirements(manifest, connections={})
    assert unsatisfied_required_connectors(resolutions) == ["github"]


# --------------------------------------------------------------------------------------
# regressions: MetaPlugin is untouched and the governance seed stays idempotent
# --------------------------------------------------------------------------------------


async def test_seed_governance_is_idempotent_against_one_store() -> None:
    store, _ = await setup_plugin_store()
    await seed_governance(store)
    await seed_governance(store)
    plugins = await store.list_plugins(allowlisted_only=False)
    assert [item.plugin_id for item in plugins] == ["accretion-core-governance"]
    assert len(await store.list_plugin_versions()) == 0


def test_old_shape_meta_plugin_json_still_validates() -> None:
    """A literal blob written by an earlier release must survive the M4 additions."""

    legacy = {
        "schema_version": "1.0",
        "plugin_id": "accretion-core-governance",
        "version": "1.0.0",
        "description": "Built-in, locally allowlisted v0.1 governance plugin.",
        "capability_refs": ["accretion.echo", "accretion.protected-write"],
        "skill_refs": ["accretion.governed-echo"],
        "verifier_refs": [],
        "policy_refs": ["local-capability-policy"],
        "provider_projections": {},
        "checksum": "a" * 64,
        "allowlisted": True,
        "created_at": "2026-08-22T00:00:00Z",
    }
    plugin = MetaPlugin.model_validate(legacy)
    assert plugin.plugin_id == "accretion-core-governance"
    assert plugin.allowlisted is True
    assert set(MetaPlugin.model_fields) == set(legacy)


def test_meta_plugin_manifest_is_a_separate_contract_from_meta_plugin() -> None:
    assert "state" not in MetaPluginManifest.model_fields
    assert "state" not in MetaPlugin.model_fields
    assert "plugin_id" not in MetaPluginManifest.model_fields
    assert MetaPluginManifest.model_config["extra"] == "forbid"
    assert list(PluginState) == [
        PluginState.DISCOVERED,
        PluginState.VALIDATING,
        PluginState.INSTALLED,
        PluginState.SETUP_REQUIRED,
        PluginState.READY,
        PluginState.ENABLED,
        PluginState.DISABLED,
        PluginState.FAILED,
        PluginState.REMOVED,
    ]


def test_plugin_ref_is_exactly_the_locked_triple() -> None:
    from accretion.contracts import PluginRef

    assert set(PluginRef.model_fields) == {"plugin_id", "version", "manifest_digest"}


async def test_memory_store_plugin_registry_round_trip() -> None:
    from accretion.contracts import PluginAuditEvent, PluginVersionRecord

    store, manifest = await setup_plugin_store()
    digest = canonical_manifest_digest(manifest)
    record = PluginVersionRecord(
        plugin_version_id=new_id("plugin_version"),
        plugin_id=manifest.id,
        version=manifest.version,
        manifest_digest=digest,
        trust_level=PluginTrustLevel.BUILTIN,
        manifest=manifest,
    )
    await store.upsert_plugin_version(record)
    await store.upsert_plugin_version(record)
    assert await store.get_plugin_version(manifest.id, manifest.version) == record
    assert await store.list_plugin_versions(plugin_id=manifest.id) == [record]
    with pytest.raises(ValueError, match="immutable"):
        await store.upsert_plugin_version(record.model_copy(update={"source_uri": "elsewhere"}))

    installation = PluginInstallation(
        installation_id=new_id("plugin_installation"),
        workspace_id="wks_memory",
        plugin_id=manifest.id,
        version=manifest.version,
        manifest_digest=digest,
        state=PluginState.SETUP_REQUIRED,
        trust_level=PluginTrustLevel.BUILTIN,
    )
    await store.upsert_plugin_installation(installation)
    enabled = installation.model_copy(update={"state": PluginState.ENABLED, "revision": 2})
    await store.upsert_plugin_installation(enabled)
    assert await store.get_plugin_installation("wks_memory", manifest.id) == enabled
    assert await store.list_plugin_installations(workspace_id="wks_memory") == [enabled]
    assert await store.list_plugin_installations(workspace_id="wks_other") == []

    event = PluginAuditEvent(
        plugin_event_id=new_id("plugin_event"),
        plugin_id=manifest.id,
        installation_id=installation.installation_id,
        workspace_id="wks_memory",
        event_type="ENABLED",
        from_state=PluginState.SETUP_REQUIRED,
        to_state=PluginState.ENABLED,
    )
    await store.append_plugin_audit_event(event)
    assert await store.list_plugin_audit_events(plugin_id=manifest.id) == [event]
    assert await store.list_plugin_audit_events(plugin_id="absent") == []
    # Installing a plugin must not have touched the legacy MetaPlugin registry.
    assert [item.plugin_id for item in await store.list_plugins(allowlisted_only=False)] == [
        "accretion-core-governance"
    ]


def test_manifest_skills_are_full_declarations() -> None:
    manifest = parse_manifest(manifest_payload())
    assert isinstance(manifest.skills[0], MetaSkill)
    assert manifest.skills[0].required_capabilities == ["acme.sample.read"]
    assert isinstance(manifest.required_connectors[0], PluginConnectorRequirement)
