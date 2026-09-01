from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from accretion.contracts import (
    OAuthTransaction,
    OAuthTransactionPurpose,
    TokenHandle,
    TokenStatus,
)
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.models import SecretRecordRow
from accretion.persistence.store import PostgresStore
from accretion.secrets_store import SecretRecord

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]


async def test_v03_m2_token_handle_and_secret_record_round_trip() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    handle_id = new_id("token_handle")
    secret_key = new_id("secret_record")
    try:
        await store.upsert_secret_record(
            SecretRecord(
                secret_store_key=secret_key,
                key_id="env-1",
                nonce="bm9uY2U=",
                ciphertext="Y2lwaGVy",
            )
        )
        handle = TokenHandle(
            token_handle_id=handle_id,
            connector_id="conndef_github",
            principal_id="prin_alice",
            workspace_id="workspace_test",
            issuer="https://authorization.test",
            scopes=["repo:read"],
            audience=["https://api.test"],
            expires_at=datetime.now(UTC) + timedelta(hours=8),
            secret_store_key=secret_key,
        )
        await store.upsert_token_handle(handle)

        stored = await store.get_token_handle(handle_id)
        assert stored is not None
        assert stored.scopes == ["repo:read"]
        assert stored.status is TokenStatus.ACTIVE

        # Revoking must move the indexed status column, not just the JSON definition.
        await store.upsert_token_handle(
            handle.model_copy(update={"status": TokenStatus.REVOKED})
        )
        revoked = await store.get_token_handle(handle_id)
        assert revoked is not None
        assert revoked.status is TokenStatus.REVOKED

        await store.delete_secret_record(secret_key)
        assert await store.get_secret_record(secret_key) is None
        async with store.sessions() as session:
            row = await session.scalar(
                select(SecretRecordRow).where(
                    SecretRecordRow.secret_store_key == secret_key
                )
            )
        assert row is None
    finally:
        await engine.dispose()


async def test_v03_m2_oauth_transaction_is_single_use_under_postgres() -> None:
    """The row lock plus delete is what makes a replayed state fail closed."""

    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    state = f"state_{new_id('oauth_transaction')}"
    try:
        await store.create_oauth_transaction(
            OAuthTransaction(
                transaction_id=new_id("oauth_transaction"),
                purpose=OAuthTransactionPurpose.CONNECT,
                state=state,
                code_verifier="verifier",
                connector_id="conndef_github",
                principal_id="prin_alice",
                workspace_id="workspace_test",
                requested_scopes=["repo:read"],
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )

        first = await store.consume_oauth_transaction(state)
        assert first is not None
        assert first.purpose is OAuthTransactionPurpose.CONNECT
        assert await store.consume_oauth_transaction(state) is None
    finally:
        await engine.dispose()


async def test_v03_m2_expired_transaction_is_consumed_and_refused() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    state = f"state_{new_id('oauth_transaction')}"
    try:
        await store.create_oauth_transaction(
            OAuthTransaction(
                transaction_id=new_id("oauth_transaction"),
                purpose=OAuthTransactionPurpose.REAUTHORIZE,
                state=state,
                code_verifier="verifier",
                connector_id="conndef_github",
                principal_id="prin_alice",
                workspace_id="workspace_test",
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        assert await store.consume_oauth_transaction(state) is None
        # Consumed even though expired, so it cannot be retried.
        assert await store.consume_oauth_transaction(state) is None
    finally:
        await engine.dispose()
