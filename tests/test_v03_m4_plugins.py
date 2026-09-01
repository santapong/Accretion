from __future__ import annotations

import ast
import contextlib
import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient

from accretion.api.auth import AuthRuntime
from accretion.api.main import app
from accretion.contracts import (
    ArtifactRef,
    CapabilityExecutionResult,
    CapabilityExecutionStatus,
    CapabilityRequest,
    CapabilityResolutionOutcome,
    MetaPlugin,
    MetaPluginManifest,
    PluginCapabilityDecision,
    PluginInstallation,
    PluginState,
    Principal,
    Project,
    Provider,
    Run,
    RunState,
    Task,
    TaskEnvelope,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.governance import (
    CapabilityExecutor,
    CapabilityGateway,
    CapabilityPolicyEngine,
    CredentialBroker,
    seed_governance,
)
from accretion.identity import IdentityService
from accretion.ids import new_id
from accretion.mcp.endpoint_policy import McpEndpointPolicy, McpEndpointPolicyError
from accretion.mcp.manager import RemoteMcpManager
from accretion.persistence.side_effects import MemorySideEffectLedger
from accretion.persistence.store import MemoryStore
from accretion.plugins import manager as plugin_manager_module
from accretion.plugins.errors import (
    PluginManagerError,
    PluginManifestError,
    PluginPolicyDenied,
)
from accretion.plugins.manager import (
    _ALLOWED_TRANSITIONS,
    BUNDLED_ROOT,
    MANIFEST_FILENAME,
    REASON_DOWNGRADED,
    REASON_PERMISSIONS_MISSING,
    DirectoryPluginSource,
    PluginManager,
)
from accretion.plugins.manifest import canonical_manifest_digest, parse_manifest
from accretion.plugins.registration import (
    render_provider_projection,
    validate_provider_projections,
)
from accretion.plugins.trust import (
    PluginTrustVerifier,
)
from accretion.resolver import CapabilityResolver

# Method-name shapes that would let plugin removal reach prior-run evidence. AC3-PLG-05
# asserts the manager calls none of them, so "removal cannot delete evidence" is a
# structural fact about the code rather than a behaviour a future edit could quietly drop.
_DELETION_PREFIXES = ("delete", "remove", "purge", "drop", "erase", "truncate")

# ======================================================================================
# Lifecycle, resolution, and the six M4 acceptance criteria
# ======================================================================================

WORKSPACE = "wks_m4"
OTHER_WORKSPACE = "wks_m4_other"
PRINCIPAL = "usr_m4_admin"
SAMPLE = "accretion-sample-plugin"
OVERREACH = "dev-overreach"
FULL_GRANTS = frozenset({"accretion.sample.read", "accretion.sample.write"})
READ_ONLY_GRANTS = frozenset({"accretion.sample.read"})


class DictPluginSource:
    """A package source backed by manifests held in memory.

    Used only where a test needs *content* the bundled packages deliberately do not
    ship (a second version, a hostile projection). Every acceptance path that can use
    the real bundled package reads it through ``DirectoryPluginSource`` instead.
    """

    def __init__(self, packages: dict[str, dict[str, Any]]) -> None:
        self.packages = packages

    async def read_manifest(self, reference: str) -> dict[str, Any]:
        if reference not in self.packages:
            raise PluginManifestError(f"no package named {reference!r} is available")
        return self.packages[reference]


async def echo_handler(
    arguments: dict[str, Any], credentials: Mapping[str, str]
) -> dict[str, Any]:
    del credentials
    return {"message": arguments["message"]}


async def record_handler(
    arguments: dict[str, Any], credentials: Mapping[str, str]
) -> dict[str, Any]:
    del credentials
    return {"recorded": True, "value": arguments["value"]}


def bundled_payload(reference: str) -> dict[str, Any]:
    """The bundled manifest as raw JSON, so a test can vary it without editing the wheel."""

    text = (BUNDLED_ROOT / reference / MANIFEST_FILENAME).read_text(encoding="utf-8")
    payload: dict[str, Any] = json.loads(text)
    return payload


async def setup_plugins(
    *,
    granted_permissions: frozenset[str] = FULL_GRANTS,
    source: Any | None = None,
) -> tuple[MemoryStore, PluginManager, CapabilityGateway]:
    """Module-local async builder; this repository has no conftest."""

    store = MemoryStore()
    await seed_governance(store)
    await store.upsert_principal(
        Principal(principal_id=PRINCIPAL, issuer="test", subject=PRINCIPAL)
    )
    for workspace_id in (WORKSPACE, OTHER_WORKSPACE):
        await store.upsert_workspace(WorkspaceEntity(workspace_id=workspace_id, name=workspace_id))
        await store.upsert_workspace_membership(
            WorkspaceMembership(
                membership_id=new_id("workspace_membership"),
                workspace_id=workspace_id,
                principal_id=PRINCIPAL,
                role=WorkspaceRole.OWNER,
            )
        )
    policy_engine = CapabilityPolicyEngine(set(granted_permissions))
    manager = PluginManager(
        store=store,
        trust_verifier=PluginTrustVerifier(builtin_ids=(SAMPLE, OVERREACH)),
        policy_engine=policy_engine,
        source=source or DirectoryPluginSource(),
    )
    gateway = CapabilityGateway(
        store=store,
        side_effects=MemorySideEffectLedger(),
        broker=CredentialBroker(),
        executor=CapabilityExecutor(
            {
                "accretion.sample.echo": echo_handler,
                "accretion.sample.record": record_handler,
            }
        ),
        policy_engine=policy_engine,
    )
    return store, manager, gateway


async def make_run(store: MemoryStore, allowed: Sequence[str]) -> Run:
    """A real project/task/run triple, so gateway calls produce real provenance."""

    project = Project(project_id=new_id("project"), name="M4", repository_path=".")
    await store.create_project(project)
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective="Exercise a plugin-contributed capability",
            allowed_capabilities=list(allowed),
        )
    )
    await store.create_task(task)
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
        principal_id=PRINCIPAL,
    )
    await store.create_run(run)
    return run


async def call(
    gateway: CapabilityGateway,
    run: Run,
    capability_id: str,
    version: str,
    arguments: dict[str, Any],
) -> CapabilityExecutionResult:
    return await gateway.execute(
        CapabilityRequest(
            request_id=new_id("capability_request"),
            run_id=run.run_id,
            node_id="node_m4",
            capability_id=capability_id,
            capability_version=version,
            arguments=arguments,
            declared_reason="acceptance evidence",
        )
    )


async def install_sample(
    manager: PluginManager,
    *,
    workspace_id: str = WORKSPACE,
    reference: str = SAMPLE,
    consent: Sequence[str] | None = None,
) -> PluginInstallation:
    manifest = parse_manifest(await manager.source.read_manifest(reference))
    digest = canonical_manifest_digest(manifest)
    return await manager.install(
        reference,
        workspace_id=workspace_id,
        principal_id=PRINCIPAL,
        consent_digest=digest,
        consent_capability_ids=(
            [item.capability_id for item in manifest.capabilities]
            if consent is None
            else list(consent)
        ),
    )


def install_body(manifest: MetaPluginManifest, reference: str, workspace_id: str) -> dict[str, Any]:
    return {
        "workspace_id": workspace_id,
        "reference": reference,
        "consent_digest": canonical_manifest_digest(manifest),
        "consent_capability_ids": [item.capability_id for item in manifest.capabilities],
    }


@contextlib.asynccontextmanager
async def live_api(
    store: MemoryStore, manager: PluginManager
) -> AsyncIterator[AsyncClient]:
    """One live app instance, wired to the same store the manager writes through."""

    who = await store.get_principal(PRINCIPAL)
    assert who is not None
    app.state.manager = type("Manager", (), {"store": store})()
    app.state.plugins = manager
    app.state.auth = AuthRuntime(
        mode="LOCAL_PRINCIPAL",
        identity=IdentityService(store),
        cookie_name="session",
        cookie_secure=False,
        session_ttl_seconds=3600,
        local_principal_cache=who,
    )
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        async with client:
            yield client
    finally:
        for attribute in ("auth", "plugins", "manager"):
            if hasattr(app.state, attribute):
                delattr(app.state, attribute)


# --------------------------------------------------------------------------------------
# AC3-PLG-01 — install registers skills and capabilities without a restart
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC3-PLG-01")
async def test_installing_a_plugin_registers_it_without_restarting_accretion() -> None:
    """The negative probe is the load-bearing half.

    A test that installs and then asserts a row exists proves only that a row exists.
    This one asserts the capability is *unresolvable and absent* first, on the very
    same live application and manager instance it then installs through — so the
    transition from unavailable to executable happens inside one process lifetime,
    with no restart, no re-import, and no second store.
    """

    store, manager, gateway = await setup_plugins()
    resolver = CapabilityResolver(store)

    async with live_api(store, manager) as client:
        # ---- negative probe, before anything is installed -------------------------
        assert await resolver.resolve("accretion.sample.echo", workspace_id=WORKSPACE) is None
        assert await store.get_capability("accretion.sample.echo") is None
        assert "accretion-sample-usage" not in {
            skill.skill_id for skill in await store.list_skills()
        }
        listed_before = await client.get("/api/v1/plugins")
        assert listed_before.status_code == 200
        assert SAMPLE not in {item["plugin_id"] for item in listed_before.json()}

        run = await make_run(store, ["accretion.sample.echo"])
        before = await call(gateway, run, "accretion.sample.echo", "0.1.0", {"message": "hi"})
        assert before.status is CapabilityExecutionStatus.DENIED
        assert before.error is not None and before.error.code == "CAPABILITY_UNKNOWN"

        # ---- install through that same live instance ------------------------------
        manifest = parse_manifest(await manager.source.read_manifest(SAMPLE))
        created = await client.post(
            "/api/v1/plugins/install", json=install_body(manifest, SAMPLE, WORKSPACE)
        )
        assert created.status_code == 201, created.text
        assert created.json()["state"] == PluginState.ENABLED.value

        # ---- the same instance now resolves and executes --------------------------
        resolved = await resolver.resolve(
            "accretion.sample.echo", principal_id=PRINCIPAL, workspace_id=WORKSPACE
        )
        assert resolved is not None
        assert resolved.outcome is CapabilityResolutionOutcome.OK
        assert resolved.binding is not None and resolved.connection is not None

        after = await call(gateway, run, "accretion.sample.echo", "0.1.0", {"message": "hi"})
        assert after.status is CapabilityExecutionStatus.SUCCEEDED
        assert after.output == {"message": "hi"}

        assert {skill.skill_id for skill in await store.list_skills()} >= {
            "accretion-sample-usage",
            "accretion-sample-read-only",
        }
        listed_after = await client.get("/api/v1/plugins")
        assert SAMPLE in {item["plugin_id"] for item in listed_after.json()}

        detail = await client.get(f"/api/v1/plugins/{SAMPLE}?workspace_id={WORKSPACE}")
        assert detail.status_code == 200
        assert detail.json()["installation"]["state"] == PluginState.ENABLED.value

    events = await store.list_plugin_audit_events(plugin_id=SAMPLE)
    assert [event.to_state for event in events][-1] is PluginState.ENABLED
    # Every transition emitted an event, because there is only one door.
    assert all(event.to_state is not None for event in events)
    assert {event.event_type for event in events} >= {
        "DISCOVERED",
        "INSTALL_VALIDATING",
        "INSTALL_INSTALLED",
        "READY",
        "ENABLED",
    }


# --------------------------------------------------------------------------------------
# AC3-PLG-02 — a disallowed capability never becomes authority
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC3-PLG-02")
async def test_disallowed_capability_never_becomes_authority() -> None:
    """Assert authority, not a state string.

    Both branches the criterion names are exercised: the plugin whose every request is
    refused (install *fails*), and the plugin that keeps a narrowed grant (install
    lands *disabled*). In each case what is asserted is what the plugin can actually
    do — resolver outcome, gateway verdict, and the absence of registry rows — rather
    than the outer status code, which is exactly how V02-P5-005 passed while proving
    nothing.
    """

    # ---- branch one: every requested capability denied -> FAILED --------------------
    store, manager, gateway = await setup_plugins()
    installation = await install_sample(manager, reference=OVERREACH, consent=[])

    assert installation.state is PluginState.FAILED
    (grant,) = installation.capability_grants
    assert grant.capability_id == "dev.overreach.push"
    assert grant.decision is PluginCapabilityDecision.DENIED
    assert grant.reason.startswith(REASON_PERMISSIONS_MISSING)
    assert "github.write" in grant.reason
    assert grant.granted_permissions == []

    # Nothing was written: not the capability, not a binding, not a registry row.
    assert await store.get_capability("dev.overreach.push") is None
    assert (
        await store.list_capability_bindings(
            capability_id="dev.overreach.push", enabled_only=False
        )
        == []
    )
    assert OVERREACH not in {
        item.plugin_id for item in await store.list_plugins(allowlisted_only=False)
    }
    assert "dev-overreach-push" not in {skill.skill_id for skill in await store.list_skills()}
    assert await store.get_plugin_version(OVERREACH, "0.1.0") is None

    resolver = CapabilityResolver(store)
    assert await resolver.resolve("dev.overreach.push", workspace_id=WORKSPACE) is None

    run = await make_run(store, ["dev.overreach.push"])
    denied = await call(gateway, run, "dev.overreach.push", "0.1.0", {"ref": "main"})
    assert denied.status is CapabilityExecutionStatus.DENIED
    assert denied.error is not None and denied.error.code == "CAPABILITY_UNKNOWN"

    # ---- branch two: partial grant -> DISABLED, with the write authority withheld ---
    limited_store, limited_manager, limited_gateway = await setup_plugins(
        granted_permissions=READ_ONLY_GRANTS
    )
    limited = await install_sample(
        limited_manager, consent=["accretion.sample.echo", "accretion.sample.record"]
    )

    assert limited.state is PluginState.DISABLED
    decisions = {
        item.capability_id: item for item in limited.capability_grants
    }
    assert decisions["accretion.sample.echo"].decision is PluginCapabilityDecision.GRANTED
    downgraded = decisions["accretion.sample.record"]
    assert downgraded.decision is PluginCapabilityDecision.DOWNGRADED_READ_ONLY
    assert downgraded.reason.startswith(REASON_DOWNGRADED)
    assert "accretion.sample.write" in downgraded.reason
    assert downgraded.granted_permissions == ["accretion.sample.read"]

    # The registered capability is the narrowed one: it never gained the write
    # permission, and it carries no side effects it was not authorized to perform.
    registered = await limited_store.get_capability("accretion.sample.record", "0.1.0")
    assert registered is not None
    assert registered.required_permissions == ["accretion.sample.read"]
    assert registered.side_effects == []
    assert registered.provider_projections["accretion"]["decision"] == (
        PluginCapabilityDecision.DOWNGRADED_READ_ONLY.value
    )

    # The plugin did not gain authority automatically: it must be enabled by an admin.
    blocked = await CapabilityResolver(limited_store).resolve(
        "accretion.sample.record", principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    assert blocked is not None
    assert blocked.outcome is CapabilityResolutionOutcome.DISABLED
    assert (
        await limited_store.list_capability_bindings(
            capability_id="accretion.sample.record", enabled_only=True
        )
        == []
    )

    # Even once an administrator does enable it, the withheld authority stays withheld:
    # the call spends no side-effect budget, because the capability has none to spend.
    await limited_manager.enable(SAMPLE, workspace_id=WORKSPACE, principal_id=PRINCIPAL)
    limited_run = await make_run(limited_store, ["accretion.sample.record"])
    result = await call(
        limited_gateway, limited_run, "accretion.sample.record", "0.1.0", {"value": "v"}
    )
    assert result.status is CapabilityExecutionStatus.SUCCEEDED
    assert result.side_effect_operation_id is None


# --------------------------------------------------------------------------------------
# AC3-PLG-03 — disabling removes executable bindings, provenance survives
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC3-PLG-03")
async def test_disabling_removes_bindings_while_preserving_run_provenance() -> None:
    """A real prior run, then byte-identical provenance, then the adversarial check.

    Comparing provenance is only meaningful if provenance exists, so the run really
    goes through ``CapabilityGateway`` first. The adversarial half is what proves the
    resolver gate rather than the binding cascade: a capability manually flipped back
    to ``enabled=True`` must still fail to resolve while its plugin is disabled.
    """

    store, manager, gateway = await setup_plugins()
    await install_sample(manager)
    run = await make_run(store, ["accretion.sample.echo"])
    executed = await call(gateway, run, "accretion.sample.echo", "0.1.0", {"message": "before"})
    assert executed.status is CapabilityExecutionStatus.SUCCEEDED

    results_before = [
        item.model_dump(mode="json") for item in await store.list_capability_results(run.run_id)
    ]
    events_before = [item.model_dump(mode="json") for item in await store.list_events(run.run_id)]
    assert results_before and events_before

    bindings_before = await store.list_capability_bindings(
        capability_id="accretion.sample.echo", enabled_only=False
    )
    assert len(bindings_before) == 1 and bindings_before[0].enabled

    disabled = await manager.disable(SAMPLE, workspace_id=WORKSPACE, principal_id=PRINCIPAL)
    assert disabled.state is PluginState.DISABLED

    resolver = CapabilityResolver(store)
    blocked = await resolver.resolve(
        "accretion.sample.echo", principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    assert blocked is not None
    assert blocked.outcome is CapabilityResolutionOutcome.DISABLED

    # Present, not deleted.
    bindings_after = await store.list_capability_bindings(
        capability_id="accretion.sample.echo", enabled_only=False
    )
    assert [item.binding_id for item in bindings_after] == [
        item.binding_id for item in bindings_before
    ]
    assert bindings_after[0].enabled is False

    # Provenance is byte-identical, not merely non-empty.
    assert [
        item.model_dump(mode="json") for item in await store.list_capability_results(run.run_id)
    ] == results_before
    assert [item.model_dump(mode="json") for item in await store.list_events(run.run_id)] == (
        events_before
    )

    # ---- adversarial: re-enable the capability and the binding by hand -------------
    capability = await store.get_capability("accretion.sample.echo", "0.1.0")
    assert capability is not None
    store.capabilities[("accretion.sample.echo", "0.1.0")] = capability.model_copy(
        update={"enabled": True}
    )
    for binding in bindings_after:
        await store.upsert_capability_binding(binding.model_copy(update={"enabled": True}))

    still_blocked = await resolver.resolve(
        "accretion.sample.echo", principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    assert still_blocked is not None
    assert still_blocked.outcome is CapabilityResolutionOutcome.DISABLED
    assert "is DISABLED" in still_blocked.reason

    restored = await manager.enable(SAMPLE, workspace_id=WORKSPACE, principal_id=PRINCIPAL)
    assert restored.state is PluginState.ENABLED
    recovered = await resolver.resolve(
        "accretion.sample.echo", principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    assert recovered is not None and recovered.outcome is CapabilityResolutionOutcome.OK


# --------------------------------------------------------------------------------------
# AC3-PLG-04 — upgrade preserves the old version for historical traces
# --------------------------------------------------------------------------------------


def upgraded_payload() -> dict[str, Any]:
    """0.2.0 of the sample package: different content, plus a newly requested grant.

    The added ``secrets.read`` capability is the point of the fixture. Upgrade must
    re-run the *whole* policy evaluation, so v0.2.0's new request cannot inherit
    v0.1.0's decision.
    """

    payload = bundled_payload(SAMPLE)
    payload["version"] = "0.2.0"
    payload["description"] = "Version 0.2.0 changes the description and adds a capability."
    for capability in payload["capabilities"]:
        capability["version"] = "0.2.0"
    for skill in payload["skills"]:
        skill["version"] = "0.2.0"
    payload["capabilities"].append(
        {
            "capability_id": "accretion.sample.secret",
            "kind": "TOOL",
            "version": "0.2.0",
            "description": "Read a workspace secret.",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            "risk": "LOW",
            "backend": "PYTHON",
            "required_permissions": ["secrets.read"],
        }
    )
    return payload


@pytest.mark.acceptance("AC3-PLG-04")
async def test_upgrade_preserves_old_version_references_for_historical_traces() -> None:
    """Row counts are a free pass; content and digest are not.

    ``upsert_plugin_version`` is keyed on ``(plugin_id, version)``, so asserting that
    two rows exist after an upgrade passes whether or not upgrade works at all. This
    test dereferences the historical reference and compares the *manifest content and
    digest* of 0.1.0 after 0.2.0 is live, re-asserts immutability against drifted
    content, and rolls back.
    """

    source = DictPluginSource(
        {SAMPLE: bundled_payload(SAMPLE), f"{SAMPLE}-v2": upgraded_payload()}
    )
    store, manager, gateway = await setup_plugins(source=source)

    original = parse_manifest(bundled_payload(SAMPLE))
    original_digest = canonical_manifest_digest(original)

    installed = await install_sample(manager)
    assert installed.state is PluginState.ENABLED
    assert installed.version == "0.1.0"

    run = await make_run(store, ["accretion.sample.echo"])
    historical = await call(gateway, run, "accretion.sample.echo", "0.1.0", {"message": "old"})
    assert historical.status is CapabilityExecutionStatus.SUCCEEDED
    historical_ref = installed.to_ref()

    upgraded_manifest = parse_manifest(upgraded_payload())
    upgraded = await manager.upgrade(
        SAMPLE,
        f"{SAMPLE}-v2",
        workspace_id=WORKSPACE,
        principal_id=PRINCIPAL,
        consent_digest=canonical_manifest_digest(upgraded_manifest),
        consent_capability_ids=["accretion.sample.echo", "accretion.sample.record"],
    )
    assert upgraded.version == "0.2.0"
    assert upgraded.previous_version == "0.1.0"

    # The re-run of policy on the new manifest: the newly requested grant is refused,
    # it did not inherit 0.1.0's verdict, and the plugin therefore lands disabled.
    new_grants = {item.capability_id: item for item in upgraded.capability_grants}
    assert new_grants["accretion.sample.secret"].decision is PluginCapabilityDecision.DENIED
    assert "secrets.read" in new_grants["accretion.sample.secret"].reason
    assert new_grants["accretion.sample.echo"].decision is PluginCapabilityDecision.GRANTED
    assert upgraded.state is PluginState.DISABLED
    assert await store.get_capability("accretion.sample.secret") is None

    # ---- the historical reference still dereferences to 0.1.0's exact content ------
    archived = await store.get_plugin_version(historical_ref.plugin_id, historical_ref.version)
    assert archived is not None
    assert archived.manifest_digest == original_digest == historical_ref.manifest_digest
    # Content, not just the digest: the archived manifest still says what 0.1.0 said.
    assert archived.manifest.description == original.description
    assert archived.manifest.description != upgraded_manifest.description
    assert [
        (item.capability_id, item.version, tuple(item.required_permissions))
        for item in archived.manifest.capabilities
    ] == [
        (item.capability_id, item.version, tuple(item.required_permissions))
        for item in original.capabilities
    ]
    assert [(item.skill_id, item.version) for item in archived.manifest.skills] == [
        (item.skill_id, item.version) for item in original.skills
    ]
    assert canonical_manifest_digest(archived.manifest) == original_digest
    assert [item.capability_id for item in archived.manifest.capabilities] == [
        "accretion.sample.echo",
        "accretion.sample.record",
    ]

    # The prior run's provenance still names 0.1.0, and that capability row is intact.
    results = await store.list_capability_results(run.run_id)
    assert [item.request.capability_version for item in results] == ["0.1.0"]
    assert await store.get_capability("accretion.sample.echo", "0.1.0") is not None
    assert await store.get_capability("accretion.sample.echo", "0.2.0") is not None

    # ---- re-upserting 0.1.0 with different content still raises -------------------
    with pytest.raises(ValueError, match="immutable"):
        await store.upsert_plugin_version(
            archived.model_copy(update={"source_uri": "https://elsewhere.test/tampered"})
        )
    with pytest.raises(ValueError, match="immutable"):
        await store.upsert_plugin(
            MetaPlugin(
                plugin_id=SAMPLE,
                version="0.1.0",
                description="tampered",
                checksum=original_digest,
            )
        )

    # ---- and the upgrade is reversible --------------------------------------------
    rolled_back = await manager.rollback(
        SAMPLE, workspace_id=WORKSPACE, principal_id=PRINCIPAL
    )
    assert rolled_back.version == "0.1.0"
    assert rolled_back.previous_version == "0.2.0"
    assert rolled_back.manifest_digest == original_digest
    assert rolled_back.state is PluginState.ENABLED
    recovered = await CapabilityResolver(store).resolve(
        "accretion.sample.echo", principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    assert recovered is not None and recovered.outcome is CapabilityResolutionOutcome.OK


# --------------------------------------------------------------------------------------
# AC3-PLG-05 — removal cannot delete evidence
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC3-PLG-05")
async def test_removal_cannot_delete_evidence_from_prior_runs() -> None:
    """Proving a negative, three independent ways.

    (a) Preconditions and postconditions, so a ``remove()`` that quietly did nothing
    would fail here; (b) *content* equality of the artifact, event, and result lists
    including every ``sha256``, rather than mere non-emptiness; (c) a structural
    invariant over the lifecycle module itself, which fails the moment anyone teaches
    removal to delete.
    """

    store, manager, gateway = await setup_plugins()
    await install_sample(manager)
    run = await make_run(store, ["accretion.sample.echo"])
    executed = await call(gateway, run, "accretion.sample.echo", "0.1.0", {"message": "evidence"})
    assert executed.status is CapabilityExecutionStatus.SUCCEEDED

    artifact = ArtifactRef(
        artifact_id=new_id("artifact"),
        run_id=run.run_id,
        kind="LOG",
        path=Path("artifacts/plugin-run.log"),
        sha256=hashlib.sha256(b"evidence").hexdigest(),
    )
    await store.save_artifact(artifact)

    artifacts_before = [
        item.model_dump(mode="json") for item in await store.list_artifacts(run.run_id)
    ]
    events_before = [item.model_dump(mode="json") for item in await store.list_events(run.run_id)]
    results_before = [
        item.model_dump(mode="json") for item in await store.list_capability_results(run.run_id)
    ]
    audit_before = await store.list_plugin_audit_events(plugin_id=SAMPLE)
    assert artifacts_before and events_before and results_before and audit_before
    assert artifacts_before[0]["sha256"] == artifact.sha256

    # (a) forceful removal is a real precondition, not a formality.
    live = await store.get_plugin_installation(WORKSPACE, SAMPLE)
    assert live is not None and live.state is PluginState.ENABLED
    with pytest.raises(PluginManagerError, match="disable it first or pass force"):
        await manager.remove(SAMPLE, workspace_id=WORKSPACE, principal_id=PRINCIPAL)

    removed = await manager.remove(
        SAMPLE, workspace_id=WORKSPACE, principal_id=PRINCIPAL, force=True
    )
    assert removed.state is PluginState.REMOVED
    assert await store.get_plugin_installation(WORKSPACE, SAMPLE) is not None
    with pytest.raises(KeyError):
        await manager.disable(SAMPLE, workspace_id=WORKSPACE, principal_id=PRINCIPAL)
    gone = await CapabilityResolver(store).resolve(
        "accretion.sample.echo", principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    assert gone is not None and gone.outcome is CapabilityResolutionOutcome.DISABLED

    # (b) content equality, including sha256.
    assert [
        item.model_dump(mode="json") for item in await store.list_artifacts(run.run_id)
    ] == artifacts_before
    assert [item.model_dump(mode="json") for item in await store.list_events(run.run_id)] == (
        events_before
    )
    assert [
        item.model_dump(mode="json") for item in await store.list_capability_results(run.run_id)
    ] == results_before
    # The version registry and the audit trail are append-only across a removal.
    assert await store.get_plugin_version(SAMPLE, "0.1.0") is not None
    assert (await store.list_plugin_audit_events(plugin_id=SAMPLE))[
        : len(audit_before)
    ] == audit_before
    assert await store.get_capability("accretion.sample.echo", "0.1.0") is not None


def test_plugin_lifecycle_module_calls_no_deletion_method() -> None:
    """``remove()`` must never call a store deletion method — enforced, not intended."""

    tree = ast.parse(Path(plugin_manager_module.__file__).read_text(encoding="utf-8"))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    offenders = sorted(name for name in called if name.lower().startswith(_DELETION_PREFIXES))
    assert offenders == [], f"the plugin manager calls deletion method(s): {offenders}"


# --------------------------------------------------------------------------------------
# AC3-PLG-06 — provider projections differ, canonical ids do not
# --------------------------------------------------------------------------------------


@pytest.mark.acceptance("AC3-PLG-06")
async def test_provider_projections_differ_while_canonical_ids_stay_stable() -> None:
    """Rendered through the real projection path, not compared on a hand-written fixture.

    ``assert projections["claude"] != projections["codex"]`` on a dict the test itself
    wrote is a tautology. Here one canonical id is rendered for two providers through
    ``registration.render_provider_projection`` — the same function the manager uses —
    and both renderings must dereference to the *same stored capability*. The
    adversarial half is what makes "ids remain stable" enforceable at all: a manifest
    whose projection names an id outside its own capabilities is rejected at install.
    """

    store, manager, _ = await setup_plugins()
    await install_sample(manager)

    stored = await store.get_capability("accretion.sample.echo", "0.1.0")
    assert stored is not None

    claude = render_provider_projection(stored, "claude")
    codex = render_provider_projection(stored, "codex")

    # The provider surfaces genuinely differ...
    assert claude["tool_name"] == "accretion_sample_echo"
    assert codex["function_name"] == "accretionSampleEcho"
    assert claude != codex
    assert set(claude) - {"capability_id"} != set(codex) - {"capability_id"}

    # ...while the canonical id is byte-identical and dereferences to one capability.
    assert claude["capability_id"] == codex["capability_id"] == "accretion.sample.echo"
    assert claude["capability_id"].encode() == codex["capability_id"].encode()
    from_claude = await store.get_capability(claude["capability_id"], stored.version)
    from_codex = await store.get_capability(codex["capability_id"], stored.version)
    assert from_claude is not None and from_claude == from_codex == stored

    # Accretion's own metadata is never leaked into a provider rendering.
    assert "accretion" not in claude and "accretion" not in codex
    assert stored.provider_projections["accretion"]["plugin_id"] == SAMPLE

    # ---- adversarial: a projection naming a foreign id is rejected at install ------
    hostile = bundled_payload(SAMPLE)
    hostile["id"] = "hostile-projection"
    hostile["capabilities"][0]["provider_projections"]["claude"] = {
        "capability_id": "accretion.protected-write",
        "tool_name": "borrowed",
    }
    hostile_store, hostile_manager, _ = await setup_plugins(
        source=DictPluginSource({"hostile": hostile})
    )
    hostile_manifest = parse_manifest(hostile)
    with pytest.raises(PluginManifestError, match="does not declare"):
        await hostile_manager.install(
            "hostile",
            workspace_id=WORKSPACE,
            principal_id=PRINCIPAL,
            consent_digest=canonical_manifest_digest(hostile_manifest),
            consent_capability_ids=[],
        )
    assert await hostile_store.get_plugin_installation(WORKSPACE, "hostile-projection") is None
    assert await hostile_store.list_plugin_versions("hostile-projection") == []

    # Renaming the canonical id under a provider key is refused for the same reason.
    renaming = bundled_payload(SAMPLE)
    renaming["capabilities"][0]["provider_projections"]["claude"] = {
        "capability_id": "accretion.sample.record"
    }
    with pytest.raises(PluginManifestError, match="may not rename"):
        validate_provider_projections(parse_manifest(renaming))


# ======================================================================================
# Regression: behaviour M4 deliberately leaves alone
# ======================================================================================


async def test_experience_replay_plugin_semantics_are_unchanged_by_installation() -> None:
    """Pin ``list_plugins(allowlisted_only=True)``, which replay compatibility reads.

    ``experience/service.py`` treats an allowlisted plugin as available for replay.
    M4 keeps that meaning exactly as it was — ``allowlisted`` means *trusted*, not
    *enabled* — so disabling a plugin does not retroactively invalidate a stored
    experience. Whether it should is a product question recorded for M6/M8; this test
    exists so that any future change to it has to be deliberate.
    """

    store, manager, _ = await setup_plugins()
    baseline = {item.plugin_id for item in await store.list_plugins(allowlisted_only=True)}
    assert baseline == {"accretion-core-governance"}

    await install_sample(manager)
    installed = {item.plugin_id for item in await store.list_plugins(allowlisted_only=True)}
    assert installed == {"accretion-core-governance", SAMPLE}

    await manager.disable(SAMPLE, workspace_id=WORKSPACE, principal_id=PRINCIPAL)
    after_disable = {item.plugin_id for item in await store.list_plugins(allowlisted_only=True)}
    assert after_disable == installed, (
        "disabling a plugin must not change replay availability in M4"
    )

    await manager.remove(SAMPLE, workspace_id=WORKSPACE, principal_id=PRINCIPAL)
    assert {
        item.plugin_id for item in await store.list_plugins(allowlisted_only=True)
    } == installed


async def test_governance_seed_survives_and_its_plugin_is_not_removable() -> None:
    store, manager, _ = await setup_plugins()
    seeded = [
        item
        for item in await store.list_plugins(allowlisted_only=False)
        if item.plugin_id == "accretion-core-governance"
    ]
    assert len(seeded) == 1
    checksum = seeded[0].checksum

    await seed_governance(store)
    await install_sample(manager)
    await seed_governance(store)

    reseeded = [
        item
        for item in await store.list_plugins(allowlisted_only=False)
        if item.plugin_id == "accretion-core-governance"
    ]
    assert len(reseeded) == 1 and reseeded[0].checksum == checksum

    # The built-in governance plugin has no installation, so lifecycle calls 404, and
    # even a forged installation cannot be removed or upgraded.
    with pytest.raises(KeyError):
        await manager.remove(
            "accretion-core-governance", workspace_id=WORKSPACE, principal_id=PRINCIPAL
        )
    await store.upsert_plugin_installation(
        PluginInstallation(
            installation_id=new_id("plugin_installation"),
            workspace_id=WORKSPACE,
            plugin_id="accretion-core-governance",
            version="1.0.0",
            manifest_digest=checksum,
            state=PluginState.ENABLED,
        )
    )
    with pytest.raises(PluginManagerError, match="cannot be removed"):
        await manager.remove(
            "accretion-core-governance",
            workspace_id=WORKSPACE,
            principal_id=PRINCIPAL,
            force=True,
        )
    with pytest.raises(PluginManagerError, match="cannot be upgraded"):
        await manager.upgrade(
            "accretion-core-governance",
            SAMPLE,
            workspace_id=WORKSPACE,
            principal_id=PRINCIPAL,
            consent_digest=checksum,
            consent_capability_ids=[],
        )


async def test_plugin_listing_is_workspace_scoped_and_authenticated() -> None:
    """The listing gained filtering; it must not lose the built-in the console renders."""

    store, manager, _ = await setup_plugins()
    await install_sample(manager, workspace_id=OTHER_WORKSPACE)

    async with live_api(store, manager) as client:
        listed = await client.get("/api/v1/plugins")
        assert listed.status_code == 200
        ids = {item["plugin_id"] for item in listed.json()}
        # A member of both workspaces sees the built-in and their own installation.
        assert "accretion-core-governance" in ids
        assert SAMPLE in ids

        installations = await client.get(
            f"/api/v1/plugins/installations?workspace_id={OTHER_WORKSPACE}"
        )
        assert installations.status_code == 200
        assert [item["plugin_id"] for item in installations.json()] == [SAMPLE]

        forbidden = await client.get("/api/v1/plugins/installations?workspace_id=wks_unknown")
        assert forbidden.status_code == 403

        audit = await client.get(f"/api/v1/audit/plugins?plugin_id={SAMPLE}")
        assert audit.status_code == 200
        assert {item["event_type"] for item in audit.json()} >= {"DISCOVERED", "ENABLED"}

    # A principal with no membership in the installing workspace sees only the builtin.
    outsider = Principal(principal_id="usr_outsider", issuer="test", subject="outsider")
    await store.upsert_principal(outsider)
    app.state.manager = type("Manager", (), {"store": store})()
    app.state.plugins = manager
    app.state.auth = AuthRuntime(
        mode="LOCAL_PRINCIPAL",
        identity=IdentityService(store),
        cookie_name="session",
        cookie_secure=False,
        session_ttl_seconds=3600,
        local_principal_cache=outsider,
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            isolated = await client.get("/api/v1/plugins")
            assert isolated.status_code == 200
            assert {item["plugin_id"] for item in isolated.json()} == {
                "accretion-core-governance"
            }
            denied = await client.post(
                "/api/v1/plugins/install",
                json={
                    "workspace_id": WORKSPACE,
                    "reference": SAMPLE,
                    "consent_digest": "0" * 64,
                },
            )
            assert denied.status_code == 403
    finally:
        for attribute in ("auth", "plugins", "manager"):
            if hasattr(app.state, attribute):
                delattr(app.state, attribute)


async def test_plugin_routes_require_authentication() -> None:
    store, manager, _ = await setup_plugins()
    app.state.manager = type("Manager", (), {"store": store})()
    app.state.plugins = manager
    app.state.auth = AuthRuntime(
        mode="OIDC",
        identity=IdentityService(store),
        cookie_name="session",
        cookie_secure=True,
        session_ttl_seconds=3600,
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/v1/plugins")).status_code == 401
            assert (await client.get("/api/v1/plugins/installations")).status_code == 401
            assert (
                await client.post("/api/v1/plugins/install", json={})
            ).status_code == 401
    finally:
        for attribute in ("auth", "plugins", "manager"):
            if hasattr(app.state, attribute):
                delattr(app.state, attribute)


# ======================================================================================
# Lifecycle invariants the criteria depend on
# ======================================================================================


async def test_every_transition_is_validated_against_one_table() -> None:
    store, manager, _ = await setup_plugins()
    installation = await install_sample(manager)
    assert installation.state is PluginState.ENABLED

    # REMOVED is terminal: nothing may leave it.
    assert _ALLOWED_TRANSITIONS[PluginState.REMOVED] == frozenset()
    # Every state in the enum is a key, so no edge escapes the table.
    assert set(_ALLOWED_TRANSITIONS) == set(PluginState)

    with pytest.raises(PluginManagerError, match="cannot move from"):
        await manager._transition(
            installation.model_copy(update={"state": PluginState.REMOVED}),
            PluginState.ENABLED,
            "ILLEGAL",
            PRINCIPAL,
        )

    events = await store.list_plugin_audit_events(installation_id=installation.installation_id)
    # One audit event per persisted transition, plus the initial discovery.
    assert len(events) >= 5
    assert [event.plugin_id for event in events] == [SAMPLE] * len(events)
    assert all(event.workspace_id == WORKSPACE for event in events)


async def test_consent_must_echo_the_digest_and_may_not_exceed_the_grants() -> None:
    store, manager, _ = await setup_plugins(granted_permissions=READ_ONLY_GRANTS)
    manifest = parse_manifest(await manager.source.read_manifest(SAMPLE))
    digest = canonical_manifest_digest(manifest)

    with pytest.raises(PluginPolicyDenied, match="consent echoes manifest digest"):
        await manager.install(
            SAMPLE,
            workspace_id=WORKSPACE,
            principal_id=PRINCIPAL,
            consent_digest="f" * 64,
            consent_capability_ids=[],
        )
    failed = await store.get_plugin_installation(WORKSPACE, SAMPLE)
    assert failed is not None and failed.state is PluginState.FAILED

    store, manager, _ = await setup_plugins(granted_permissions=frozenset())
    with pytest.raises(PluginPolicyDenied, match="exceeds the capabilities policy granted"):
        await manager.install(
            SAMPLE,
            workspace_id=WORKSPACE,
            principal_id=PRINCIPAL,
            consent_digest=digest,
            consent_capability_ids=["accretion.sample.echo"],
        )


async def test_missing_required_connector_parks_the_install_in_setup_required() -> None:
    payload = bundled_payload(SAMPLE)
    payload["required_connectors"] = [{"connector_id": "github", "scopes": ["repo:read"]}]
    store, manager, _ = await setup_plugins(source=DictPluginSource({SAMPLE: payload}))

    installation = await install_sample(manager)
    assert installation.state is PluginState.SETUP_REQUIRED
    assert [
        item.connector_id
        for item in installation.connector_resolutions
        if item.required and not item.satisfied
    ] == ["github"]

    parked = await CapabilityResolver(store).resolve(
        "accretion.sample.echo", principal_id=PRINCIPAL, workspace_id=WORKSPACE
    )
    assert parked is not None
    assert parked.outcome is CapabilityResolutionOutcome.NO_CONNECTION
    with pytest.raises(PluginManagerError, match="still needs connector"):
        await manager.enable(SAMPLE, workspace_id=WORKSPACE, principal_id=PRINCIPAL)


async def test_plugin_declared_mcp_servers_cannot_bypass_the_endpoint_policy() -> None:
    """A manifest is not an SSRF escape hatch: M3's validator still owns the endpoint."""

    payload = bundled_payload(SAMPLE)
    payload["mcp_servers"] = [
        {
            "connector_id": "conndef_plugin_mcp",
            "name": "Plugin-declared server",
            "endpoint": "http://169.254.169.254/latest/meta-data",
        }
    ]
    store, manager, _ = await setup_plugins(source=DictPluginSource({SAMPLE: payload}))
    manager.remote_mcp = RemoteMcpManager(
        store=store,
        client=cast(Any, None),
        endpoint_policy=McpEndpointPolicy(),
    )
    with pytest.raises(McpEndpointPolicyError):
        await install_sample(manager)
    blocked = await store.get_plugin_installation(WORKSPACE, SAMPLE)
    assert blocked is not None and blocked.state is PluginState.FAILED

    # Without an MCP manager configured at all, the declaration is refused outright.
    _, bare, _ = await setup_plugins(source=DictPluginSource({SAMPLE: payload}))
    with pytest.raises(PluginManagerError, match="no MCP manager is configured"):
        await install_sample(bare)


async def test_reinstalling_a_live_plugin_is_refused_but_a_removed_one_may_return() -> None:
    store, manager, _ = await setup_plugins()
    await install_sample(manager)
    with pytest.raises(PluginManagerError, match="already installed"):
        await install_sample(manager)

    await manager.remove(SAMPLE, workspace_id=WORKSPACE, principal_id=PRINCIPAL, force=True)
    returned = await install_sample(manager)
    assert returned.state is PluginState.ENABLED
    # One installation row per (workspace, plugin), reused rather than duplicated.
    assert len(await store.list_plugin_installations(workspace_id=WORKSPACE)) == 1


async def test_installations_are_workspace_scoped() -> None:
    store, manager, _ = await setup_plugins()
    await install_sample(manager, workspace_id=WORKSPACE)
    await install_sample(manager, workspace_id=OTHER_WORKSPACE)
    assert len(await store.list_plugin_installations()) == 2
    assert len(await store.list_plugin_installations(workspace_id=WORKSPACE)) == 1
    # The global version registry holds exactly one row for the shared version.
    assert len(await store.list_plugin_versions(SAMPLE)) == 1


async def test_directory_source_refuses_references_that_escape_the_package_root() -> None:
    source = DirectoryPluginSource()
    for reference in ("../secrets", "/etc/passwd", "a/b", "", ".."):
        with pytest.raises(PluginManifestError):
            await source.read_manifest(reference)
    assert await source.read_manifest(SAMPLE)


async def test_bundled_packages_parse_and_declare_what_the_fixtures_promise() -> None:
    source = DirectoryPluginSource()
    sample = parse_manifest(await source.read_manifest(SAMPLE))
    assert sample.id == SAMPLE and sample.version == "0.1.0"
    assert [item.capability_id for item in sample.capabilities] == [
        "accretion.sample.echo",
        "accretion.sample.record",
    ]
    overreach = parse_manifest(await source.read_manifest(OVERREACH))
    assert overreach.capabilities[0].required_permissions == ["github.write"]
