from __future__ import annotations

import os
import uuid

import pytest

from accretion.contracts import (
    Capability,
    CapabilityBackend,
    MetaPluginManifest,
    PluginAuditEvent,
    PluginCapabilityDecision,
    PluginCapabilityGrant,
    PluginConnectorRequirement,
    PluginConnectorResolution,
    PluginConsent,
    PluginInstallation,
    PluginState,
    PluginTrustLevel,
    PluginVersionRecord,
    RiskLevel,
)
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import PostgresStore
from accretion.plugins.manifest import canonical_manifest_digest

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]


def _manifest(plugin_id: str, version: str = "1.0.0") -> MetaPluginManifest:
    return MetaPluginManifest(
        id=plugin_id,
        version=version,
        name="M4 PostgreSQL fixture",
        description="Round-trips the M4 plugin registry rows.",
        capabilities=[
            Capability(
                capability_id="fixture.echo",
                version=version,
                risk=RiskLevel.LOW,
                backend=CapabilityBackend.PYTHON,
            )
        ],
        required_connectors=[
            PluginConnectorRequirement(connector_id="github", scopes=["repo:read"])
        ],
        provider_projections={"CLAUDE": "projections/claude.json"},
    )


async def test_v03_m4_plugin_registry_round_trip() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    # Uuid-suffixed so the test is re-runnable against a database it already wrote to.
    suffix = uuid.uuid4().hex[:12]
    plugin_id = f"m4-fixture-{suffix}"
    workspace_id = f"wks_m4_{suffix}"
    manifest = _manifest(plugin_id)
    digest = canonical_manifest_digest(manifest)

    record = PluginVersionRecord(
        plugin_version_id=new_id("plugin_version"),
        plugin_id=plugin_id,
        version="1.0.0",
        manifest_digest=digest,
        trust_level=PluginTrustLevel.BUILTIN,
        manifest=manifest,
        source_uri="bundled://m4-fixture",
    )
    try:
        await store.upsert_plugin_version(record)
        assert await store.get_plugin_version(plugin_id, "1.0.0") == record
        assert await store.list_plugin_versions(plugin_id=plugin_id) == [record]

        # Re-upserting identical content is idempotent; drifted content is rejected.
        await store.upsert_plugin_version(record)
        drifted = record.model_copy(update={"source_uri": "bundled://tampered"})
        with pytest.raises(ValueError, match="immutable"):
            await store.upsert_plugin_version(drifted)

        upgrade_manifest = _manifest(plugin_id, version="1.1.0")
        upgrade = PluginVersionRecord(
            plugin_version_id=new_id("plugin_version"),
            plugin_id=plugin_id,
            version="1.1.0",
            manifest_digest=canonical_manifest_digest(upgrade_manifest),
            trust_level=PluginTrustLevel.BUILTIN,
            manifest=upgrade_manifest,
        )
        await store.upsert_plugin_version(upgrade)
        assert await store.list_plugin_versions(plugin_id=plugin_id) == [record, upgrade]
        # The old version still dereferences to its own manifest content.
        assert (await store.get_plugin_version(plugin_id, "1.0.0")) == record

        installation = PluginInstallation(
            installation_id=new_id("plugin_installation"),
            workspace_id=workspace_id,
            plugin_id=plugin_id,
            version="1.0.0",
            manifest_digest=digest,
            state=PluginState.INSTALLED,
            trust_level=PluginTrustLevel.BUILTIN,
            requested_capability_ids=["fixture.echo"],
            capability_grants=[
                PluginCapabilityGrant(
                    capability_id="fixture.echo",
                    requested_permissions=["echo"],
                    granted_permissions=["echo"],
                    decision=PluginCapabilityDecision.GRANTED,
                )
            ],
            connector_resolutions=[
                PluginConnectorResolution(connector_id="github", required=True, satisfied=False)
            ],
            consent=PluginConsent(
                granted_by_principal_id="usr_postgres",
                manifest_digest=digest,
                granted_capability_ids=["fixture.echo"],
            ),
            installed_by_principal_id="usr_postgres",
        )
        await store.upsert_plugin_installation(installation)
        assert await store.get_plugin_installation(workspace_id, plugin_id) == installation

        enabled = installation.model_copy(
            update={"state": PluginState.ENABLED, "revision": 2, "version": "1.1.0"}
        )
        await store.upsert_plugin_installation(enabled)
        assert await store.list_plugin_installations(workspace_id=workspace_id) == [enabled]
        assert await store.get_plugin_installation(workspace_id, plugin_id) == enabled

        events = [
            PluginAuditEvent(
                plugin_event_id=new_id("plugin_event"),
                plugin_id=plugin_id,
                installation_id=installation.installation_id,
                workspace_id=workspace_id,
                event_type="INSTALLED",
                to_state=PluginState.INSTALLED,
                actor_principal_id="usr_postgres",
                correlation_id=f"request-{suffix}",
            ),
            PluginAuditEvent(
                plugin_event_id=new_id("plugin_event"),
                plugin_id=plugin_id,
                installation_id=installation.installation_id,
                workspace_id=workspace_id,
                event_type="ENABLED",
                from_state=PluginState.INSTALLED,
                to_state=PluginState.ENABLED,
                actor_principal_id="usr_postgres",
            ),
        ]
        for event in events:
            await store.append_plugin_audit_event(event)
        assert await store.list_plugin_audit_events(plugin_id=plugin_id) == events
        assert (
            await store.list_plugin_audit_events(
                installation_id=installation.installation_id
            )
            == events
        )
        assert await store.list_plugin_audit_events(plugin_id=f"absent-{suffix}") == []
    finally:
        await engine.dispose()


async def test_v03_m4_tables_declare_no_cascading_delete() -> None:
    """Evidence must survive plugin removal even under a raw SQL delete.

    AC3-PLG-05 is a preservation property, so it must hold structurally and not
    only through the manager's behaviour. A future foreign key added with
    ``ON DELETE CASCADE`` would let a delete on a plugin row reach into run,
    evidence, or audit rows without any test noticing. ``confdeltype = 'c'`` is
    PostgreSQL's marker for that rule; this assertion is currently vacuous
    because the three M4 tables declare no foreign keys at all, which is the
    point: it fails the moment one is added carelessly.
    """
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    try:
        async with engine.connect() as connection:
            rows = await connection.exec_driver_sql(
                """
                SELECT rel.relname, con.conname, con.confdeltype
                FROM pg_constraint AS con
                JOIN pg_class AS rel ON rel.oid = con.conrelid
                WHERE con.contype = 'f'
                  AND rel.relname IN (
                      'plugin_versions', 'plugin_installations', 'plugin_audit_events'
                  )
                """
            )
            cascading = [
                (name, constraint)
                for name, constraint, deltype in rows.fetchall()
                if deltype == "c"
            ]
        assert cascading == [], f"cascading delete would destroy evidence: {cascading}"
    finally:
        await engine.dispose()
