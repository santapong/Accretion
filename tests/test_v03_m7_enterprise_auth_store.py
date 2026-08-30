from __future__ import annotations

import importlib.util
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from accretion.config import Settings
from accretion.contracts import (
    AssertionStatus,
    EnterpriseAuthGrant,
    EnterpriseAuthOutcome,
    IdentityAssertion,
)
from accretion.ids import new_id
from accretion.persistence.models import (
    Base,
    EnterpriseAuthGrantRow,
    IdentityAssertionRow,
)
from accretion.persistence.store import MemoryStore, PostgresStore, StateStore
from accretion.secrets_store import SecretRecord

ISSUED_AT = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)


def _assertion(
    *,
    auth_session_id: str,
    principal_id: str,
    created_at: datetime = ISSUED_AT,
    status: AssertionStatus = AssertionStatus.ACTIVE,
    assertion_id: str | None = None,
) -> IdentityAssertion:
    return IdentityAssertion(
        assertion_id=assertion_id or new_id("identity_assertion"),
        auth_session_id=auth_session_id,
        principal_id=principal_id,
        issuer="https://idp.example.invalid",
        subject=f"subject-of-{principal_id}",
        secret_store_key=new_id("secret_record"),
        expires_at=created_at + timedelta(minutes=5),
        status=status,
        created_at=created_at,
    )


def _grant(
    *,
    principal_id: str,
    connector_id: str,
    outcome: EnterpriseAuthOutcome,
    created_at: datetime = ISSUED_AT,
    detail: str = "",
) -> EnterpriseAuthGrant:
    return EnterpriseAuthGrant(
        grant_id=new_id("enterprise_auth_grant"),
        principal_id=principal_id,
        workspace_id="wsp_alpha",
        connector_id=connector_id,
        mcp_server_id="mcs_reporting",
        connection_id="con_reporting",
        outcome=outcome,
        detail=detail,
        created_at=created_at,
    )


async def setup_store() -> tuple[MemoryStore, IdentityAssertion, IdentityAssertion]:
    store = MemoryStore()
    first = _assertion(auth_session_id="aus_one", principal_id="usr_alice")
    second = _assertion(
        auth_session_id="aus_two",
        principal_id="usr_alice",
        created_at=ISSUED_AT + timedelta(minutes=1),
    )
    await store.upsert_identity_assertion(first)
    await store.upsert_identity_assertion(second)
    return store, first, second


def test_enterprise_auth_is_disabled_and_inert_by_default() -> None:
    settings = Settings()

    assert settings.enable_enterprise_auth is False
    assert settings.enterprise_auth_token_exchange_url == ""
    assert settings.enterprise_auth_audiences == {}


def test_identity_assertion_carries_no_token_material() -> None:
    assertion = _assertion(auth_session_id="aus_one", principal_id="usr_alice")

    dumped = assertion.model_dump(mode="json")

    assert "secret_store_key" in dumped
    assert not {"token", "id_token", "assertion", "access_token"} & set(dumped)
    with pytest.raises(ValueError):
        IdentityAssertion.model_validate({**dumped, "id_token": "eyJ-secret"})


def test_enterprise_auth_grant_rejects_undeclared_fields() -> None:
    grant = _grant(
        principal_id="usr_alice",
        connector_id="reporting",
        outcome=EnterpriseAuthOutcome.GRANTED,
    )

    with pytest.raises(ValueError):
        EnterpriseAuthGrant.model_validate(
            {**grant.model_dump(mode="json"), "access_token": "eyJ-secret"}
        )


async def test_an_assertion_is_read_back_by_its_session() -> None:
    store, first, second = await setup_store()

    assert await store.get_identity_assertion_for_session("aus_one") == first
    assert await store.get_identity_assertion_for_session("aus_two") == second
    assert await store.get_identity_assertion_for_session("aus_absent") is None


async def test_the_latest_active_assertion_is_returned_for_a_principal() -> None:
    store, _first, second = await setup_store()

    assert await store.get_identity_assertion_for_principal("usr_alice") == second
    assert await store.get_identity_assertion_for_principal("usr_bob") is None


async def test_a_revoked_assertion_is_no_longer_active_for_its_principal() -> None:
    store, first, second = await setup_store()

    await store.upsert_identity_assertion(
        second.model_copy(update={"status": AssertionStatus.REVOKED})
    )

    assert await store.get_identity_assertion_for_principal("usr_alice") == first
    read_back = await store.get_identity_assertion_for_session("aus_two")
    assert read_back is not None
    assert read_back.status is AssertionStatus.REVOKED


async def test_revocation_destroys_the_material_and_keeps_the_row_as_evidence() -> None:
    store, first, second = await setup_store()
    await store.upsert_secret_record(
        SecretRecord(
            secret_store_key=second.secret_store_key,
            key_id="key-1",
            nonce="nonce-1",
            ciphertext="sealed-assertion",
        )
    )

    # The whole of revocation: mark the row, destroy the sealed material. The store
    # offers no assertion deletion, and none is needed (AC3-PLG-05, ADR3-M7-004).
    await store.upsert_identity_assertion(
        second.model_copy(update={"status": AssertionStatus.REVOKED})
    )
    await store.delete_secret_record(second.secret_store_key)

    assert await store.get_secret_record(second.secret_store_key) is None
    assert await store.get_identity_assertion_for_principal("usr_alice") == first
    evidence = await store.get_identity_assertion_for_session("aus_two")
    assert evidence is not None
    assert evidence.status is AssertionStatus.REVOKED
    assert evidence.principal_id == second.principal_id


async def test_grants_are_listed_in_creation_order_and_filtered() -> None:
    store = MemoryStore()
    granted = _grant(
        principal_id="usr_alice",
        connector_id="reporting",
        outcome=EnterpriseAuthOutcome.GRANTED,
        detail="exchange succeeded",
    )
    refused = _grant(
        principal_id="usr_bob",
        connector_id="reporting",
        outcome=EnterpriseAuthOutcome.REFUSED_AUDIENCE,
        created_at=ISSUED_AT + timedelta(seconds=30),
        detail="audience did not match the configured audience",
    )
    refreshed = _grant(
        principal_id="usr_alice",
        connector_id="ledger",
        outcome=EnterpriseAuthOutcome.REFRESHED,
        created_at=ISSUED_AT + timedelta(seconds=60),
    )
    for grant in (refreshed, granted, refused):
        await store.append_enterprise_auth_grant(grant)

    assert await store.list_enterprise_auth_grants() == [granted, refused, refreshed]
    assert await store.list_enterprise_auth_grants(principal_id="usr_alice") == [
        granted,
        refreshed,
    ]
    assert await store.list_enterprise_auth_grants(connector_id="reporting") == [
        granted,
        refused,
    ]
    assert (
        await store.list_enterprise_auth_grants(
            principal_id="usr_bob", connector_id="ledger"
        )
        == []
    )


async def test_appending_a_duplicate_grant_id_is_refused() -> None:
    store = MemoryStore()
    grant = _grant(
        principal_id="usr_alice",
        connector_id="reporting",
        outcome=EnterpriseAuthOutcome.GRANTED,
    )
    await store.append_enterprise_auth_grant(grant)

    # grant_id is unique in PostgreSQL; the in-memory layer must refuse identically
    # rather than silently double-count the audit log.
    with pytest.raises(ValueError):
        await store.append_enterprise_auth_grant(grant)
    with pytest.raises(ValueError):
        await store.append_enterprise_auth_grant(
            grant.model_copy(update={"outcome": EnterpriseAuthOutcome.REVOKED})
        )

    assert await store.list_enterprise_auth_grants() == [grant]


def test_the_state_store_exposes_no_deletion_surface_for_identity_assertions() -> None:
    for implementation in (StateStore, MemoryStore, PostgresStore):
        surface = {
            name
            for name in dir(implementation)
            if "identity_assertion" in name and not name.startswith("_")
        }
        assert surface == {
            "upsert_identity_assertion",
            "get_identity_assertion_for_session",
            "get_identity_assertion_for_principal",
        }


def test_the_state_store_exposes_no_mutation_of_enterprise_grants_beyond_append() -> None:
    names = {
        name
        for name, _member in inspect.getmembers(StateStore, predicate=inspect.isfunction)
        if "enterprise_auth_grant" in name
    }

    assert names == {"append_enterprise_auth_grant", "list_enterprise_auth_grants"}
    for implementation in (StateStore, MemoryStore, PostgresStore):
        surface = {
            name
            for name in dir(implementation)
            if "enterprise_auth_grant" in name and not name.startswith("_")
        }
        assert not {
            name
            for name in surface
            if name.startswith(("update_", "delete_", "upsert_", "save_", "purge_"))
        }
        assert "append_enterprise_auth_grant" in surface
        assert "list_enterprise_auth_grants" in surface


def test_the_m7_migration_follows_0015_and_drops_exactly_what_it_creates() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0016_v03_m7_enterprise_auth.py"
    )
    spec = importlib.util.spec_from_file_location("m7_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0016_v03_m7_enterprise_auth"
    assert module.down_revision == "0015_v03_m5_research_evidence"
    assert module.M7_TABLES == ("identity_assertions", "enterprise_auth_grants")
    # Every table the upgrade creates exists in the metadata the migration builds
    # from, so the downgrade can drop exactly the same set in reverse.
    for name in module.M7_TABLES:
        assert name in Base.metadata.tables
    upgrade_source = inspect.getsource(module.upgrade)
    downgrade_source = inspect.getsource(module.downgrade)
    assert "checkfirst=True" in upgrade_source
    assert "reversed(M7_TABLES)" in downgrade_source
    assert "add_column" not in upgrade_source
    assert "drop_column" not in downgrade_source


def test_the_grant_row_is_append_only_and_the_assertion_row_is_mutable() -> None:
    grant_columns = set(EnterpriseAuthGrantRow.__table__.columns.keys())
    assertion_columns = set(IdentityAssertionRow.__table__.columns.keys())

    assert "updated_at" not in grant_columns
    assert "updated_at" in assertion_columns
    # Neither row stores token material; the sealed assertion lives in secret_records.
    assert "secret_store_key" not in assertion_columns
    assert not {"token", "id_token", "access_token", "assertion"} & (
        grant_columns | assertion_columns
    )
