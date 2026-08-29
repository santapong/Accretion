from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from accretion.contracts import (
    AssertionStatus,
    EnterpriseAuthGrant,
    EnterpriseAuthOutcome,
    IdentityAssertion,
)
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.store import MemoryStore, PostgresStore

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]

ISSUED_AT = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)


def _assertion(
    *,
    auth_session_id: str,
    principal_id: str,
    created_at: datetime,
) -> IdentityAssertion:
    return IdentityAssertion(
        assertion_id=new_id("identity_assertion"),
        auth_session_id=auth_session_id,
        principal_id=principal_id,
        issuer="https://idp.example.invalid",
        subject=f"subject-of-{principal_id}",
        secret_store_key=new_id("secret_record"),
        expires_at=created_at + timedelta(minutes=5),
        created_at=created_at,
    )


def _grant(
    *,
    principal_id: str,
    connector_id: str,
    outcome: EnterpriseAuthOutcome,
    created_at: datetime,
    detail: str,
    connection_id: str | None,
) -> EnterpriseAuthGrant:
    return EnterpriseAuthGrant(
        grant_id=new_id("enterprise_auth_grant"),
        principal_id=principal_id,
        workspace_id="wsp_alpha",
        connector_id=connector_id,
        mcp_server_id="mcs_reporting",
        connection_id=connection_id,
        outcome=outcome,
        detail=detail,
        created_at=created_at,
    )


async def test_v03_m7_identity_assertion_round_trip() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    # Uuid-suffixed so the file is re-runnable against a database it already wrote to.
    suffix = uuid.uuid4().hex[:12]
    principal_id = f"usr_m7_{suffix}"
    session_one = f"aus_m7_one_{suffix}"
    session_two = f"aus_m7_two_{suffix}"

    first = _assertion(
        auth_session_id=session_one, principal_id=principal_id, created_at=ISSUED_AT
    )
    second = _assertion(
        auth_session_id=session_two,
        principal_id=principal_id,
        created_at=ISSUED_AT + timedelta(minutes=1),
    )
    try:
        assert await store.get_identity_assertion_for_principal(principal_id) is None
        await store.upsert_identity_assertion(first)
        await store.upsert_identity_assertion(second)

        assert await store.get_identity_assertion_for_session(session_one) == first
        assert await store.get_identity_assertion_for_session(session_two) == second
        assert await store.get_identity_assertion_for_session(f"aus_absent_{suffix}") is None
        # Latest ACTIVE wins for the principal.
        assert await store.get_identity_assertion_for_principal(principal_id) == second

        revoked = second.model_copy(update={"status": AssertionStatus.REVOKED})
        await store.upsert_identity_assertion(revoked)
        assert await store.get_identity_assertion_for_principal(principal_id) == first
        read_back = await store.get_identity_assertion_for_session(session_two)
        assert read_back is not None
        assert read_back.status is AssertionStatus.REVOKED

        # The revoked row stays: it is evidence, and the store exposes no deletion
        # surface for assertions (AC3-PLG-05, ADR3-M7-004).
        await store.upsert_identity_assertion(
            first.model_copy(update={"status": AssertionStatus.REVOKED})
        )
        assert await store.get_identity_assertion_for_principal(principal_id) is None
        still_there = await store.get_identity_assertion_for_session(session_one)
        assert still_there is not None
        assert still_there.status is AssertionStatus.REVOKED
    finally:
        await engine.dispose()


async def test_v03_m7_enterprise_auth_grants_round_trip_in_append_order() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    suffix = uuid.uuid4().hex[:12]
    alice = f"usr_alice_{suffix}"
    bob = f"usr_bob_{suffix}"
    reporting = f"reporting_{suffix}"
    ledger = f"ledger_{suffix}"

    granted = _grant(
        principal_id=alice,
        connector_id=reporting,
        outcome=EnterpriseAuthOutcome.GRANTED,
        created_at=ISSUED_AT,
        detail="token exchange succeeded",
        connection_id=f"con_{suffix}",
    )
    refused = _grant(
        principal_id=bob,
        connector_id=reporting,
        outcome=EnterpriseAuthOutcome.REFUSED_AUDIENCE,
        created_at=ISSUED_AT + timedelta(seconds=30),
        detail="assertion audience did not match the configured audience",
        connection_id=None,
    )
    refreshed = _grant(
        principal_id=alice,
        connector_id=ledger,
        outcome=EnterpriseAuthOutcome.REFRESHED,
        created_at=ISSUED_AT + timedelta(seconds=60),
        detail="access token renewed without end-user interaction",
        connection_id=f"con_ledger_{suffix}",
    )
    try:
        assert await store.list_enterprise_auth_grants(principal_id=alice) == []
        # Written out of order: the read order is the store's, not the caller's.
        for grant in (refreshed, granted, refused):
            await store.append_enterprise_auth_grant(grant)

        assert await store.list_enterprise_auth_grants(connector_id=reporting) == [
            granted,
            refused,
        ]
        assert await store.list_enterprise_auth_grants(principal_id=alice) == [
            granted,
            refreshed,
        ]
        assert await store.list_enterprise_auth_grants(
            principal_id=alice, connector_id=ledger
        ) == [refreshed]
        assert (
            await store.list_enterprise_auth_grants(principal_id=bob, connector_id=ledger)
            == []
        )
    finally:
        await engine.dispose()


async def test_v03_m7_memory_and_postgres_stores_agree() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    postgres = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    suffix = uuid.uuid4().hex[:12]
    principal_id = f"usr_parity_{suffix}"
    connector_id = f"parity_{suffix}"
    session_id = f"aus_parity_{suffix}"

    assertion = _assertion(
        auth_session_id=session_id, principal_id=principal_id, created_at=ISSUED_AT
    )
    grants = [
        _grant(
            principal_id=principal_id,
            connector_id=connector_id,
            outcome=outcome,
            created_at=ISSUED_AT + timedelta(seconds=index),
            detail=f"outcome {outcome.value} recorded",
            connection_id=None if outcome.value.startswith("REFUSED") else f"con_{index}",
        )
        for index, outcome in enumerate(EnterpriseAuthOutcome)
    ]
    try:
        for store in (memory, postgres):
            await store.upsert_identity_assertion(assertion)
            for grant in reversed(grants):
                await store.append_enterprise_auth_grant(grant)

        assert await memory.get_identity_assertion_for_session(
            session_id
        ) == await postgres.get_identity_assertion_for_session(session_id)
        assert await memory.get_identity_assertion_for_principal(
            principal_id
        ) == await postgres.get_identity_assertion_for_principal(principal_id)
        assert await memory.list_enterprise_auth_grants(
            principal_id=principal_id
        ) == await postgres.list_enterprise_auth_grants(principal_id=principal_id)
        assert (
            await postgres.list_enterprise_auth_grants(connector_id=connector_id) == grants
        )
    finally:
        await engine.dispose()


async def test_v03_m7_a_duplicate_grant_id_is_refused_by_both_stores() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    postgres = PostgresStore(create_session_factory(engine))
    memory = MemoryStore()
    suffix = uuid.uuid4().hex[:12]
    principal_id = f"usr_repeat_{suffix}"
    connector_id = f"repeat_{suffix}"

    grant = _grant(
        principal_id=principal_id,
        connector_id=connector_id,
        outcome=EnterpriseAuthOutcome.GRANTED,
        created_at=ISSUED_AT,
        detail="token exchange succeeded",
        connection_id=f"con_{suffix}",
    )
    try:
        # grant_id is unique in PostgreSQL; the in-memory layer must refuse identically
        # so that parity holds on the error, not only on the happy path.
        for store in (memory, postgres):
            await store.append_enterprise_auth_grant(grant)
            with pytest.raises(ValueError):
                await store.append_enterprise_auth_grant(grant)

        assert await memory.list_enterprise_auth_grants(
            principal_id=principal_id
        ) == await postgres.list_enterprise_auth_grants(principal_id=principal_id)
        assert await postgres.list_enterprise_auth_grants(connector_id=connector_id) == [
            grant
        ]
    finally:
        await engine.dispose()
