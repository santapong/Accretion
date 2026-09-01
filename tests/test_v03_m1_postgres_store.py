from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from accretion.contracts import (
    AuthSession,
    AuthTransaction,
    Principal,
    PrincipalStatus,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.models import PrincipalRow
from accretion.persistence.store import PostgresStore

POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"),
]


async def test_v03_m1_identity_round_trips() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    store = PostgresStore(create_session_factory(engine))
    issuer = f"https://idp.test/{new_id('auth_transaction')[-8:]}"
    try:
        principal = await store.upsert_principal(
            Principal(
                principal_id=new_id("principal"),
                issuer=issuer,
                subject="alice",
                email="old@example.test",
            )
        )
        assert await store.get_principal(principal.principal_id) == principal
        assert await store.get_principal_by_identity(issuer, "alice") == principal

        # Same (issuer, subject) with new email updates in place.
        updated = await store.upsert_principal(
            Principal(
                principal_id=new_id("principal"),
                issuer=issuer,
                subject="alice",
                email="new@example.test",
            )
        )
        assert updated.principal_id == principal.principal_id
        assert updated.email == "new@example.test"

        # Direct duplicate insert violates the schema constraint.
        sessions = store.sessions
        with pytest.raises(IntegrityError):
            async with sessions.begin() as db:
                db.add(
                    PrincipalRow(
                        id=new_id("principal"),
                        principal_id=new_id("principal"),
                        issuer=issuer,
                        subject="alice",
                        status=PrincipalStatus.ACTIVE.value,
                        definition={},
                        created_at=datetime.now(UTC),
                    )
                )

        workspace_id = f"workspace_{principal.principal_id[-8:]}"
        await store.upsert_workspace(
            WorkspaceEntity(workspace_id=workspace_id, name="PG test workspace")
        )
        membership = await store.upsert_workspace_membership(
            WorkspaceMembership(
                membership_id=new_id("workspace_membership"),
                workspace_id=workspace_id,
                principal_id=principal.principal_id,
                role=WorkspaceRole.DEVELOPER,
            )
        )
        promoted = await store.upsert_workspace_membership(
            WorkspaceMembership(
                membership_id=new_id("workspace_membership"),
                workspace_id=workspace_id,
                principal_id=principal.principal_id,
                role=WorkspaceRole.ADMIN,
            )
        )
        assert promoted.membership_id == membership.membership_id
        assert promoted.revision == 2
        workspaces = await store.list_workspaces_for_principal(principal.principal_id)
        assert [w.workspace_id for w in workspaces] == [workspace_id]

        session = await store.create_auth_session(
            AuthSession(
                auth_session_id=new_id("auth_session"),
                principal_id=principal.principal_id,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        assert await store.get_auth_session(session.auth_session_id) is not None
        await store.revoke_auth_session(session.auth_session_id)
        assert await store.get_auth_session(session.auth_session_id) is None

        expired = await store.create_auth_session(
            AuthSession(
                auth_session_id=new_id("auth_session"),
                principal_id=principal.principal_id,
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        assert await store.get_auth_session(expired.auth_session_id) is None

        transaction = await store.create_auth_transaction(
            AuthTransaction(
                transaction_id=new_id("auth_transaction"),
                state=new_id("auth_transaction"),
                nonce="n",
                code_verifier="v",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
        consumed = await store.consume_auth_transaction(transaction.state)
        assert consumed is not None and consumed.state == transaction.state
        assert await store.consume_auth_transaction(transaction.state) is None
    finally:
        await engine.dispose()
