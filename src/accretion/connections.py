"""Connection lifecycle for OAuth-backed connectors (v0.3 M2, SDD sections 6 and 17).

Owns the authorize/callback/reauthorize/revoke flow and nothing else: credentials are
the Token Broker's business (ADR3-004), and this module never sees a token value. What
it does own is the short-lived transaction record that makes the callback safe.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from accretion.contracts import (
    Connection,
    ConnectionScope,
    ConnectionStatus,
    ConnectorDefinition,
    OAuthTransaction,
    OAuthTransactionPurpose,
    Principal,
    TokenStatus,
    WorkspaceRole,
)
from accretion.ids import new_id
from accretion.oauth import OAuthClient, OAuthError
from accretion.persistence.store import StateStore
from accretion.token_broker import TokenBroker, TokenBrokerError

# Short by design: the transaction is the CSRF defence, so it should not outlive the
# consent screen (SDD 19.1, "short-lived transaction record").
_TRANSACTION_TTL = timedelta(minutes=10)


class ConnectionError(Exception):
    """Connection lifecycle failed; never carries token material."""


@dataclass
class ConnectionService:
    store: StateStore
    broker: TokenBroker
    clients: dict[str, OAuthClient]

    # ------------------------------------------------------------------ authorize

    async def begin(
        self,
        *,
        connector_id: str,
        principal: Principal,
        workspace_id: str,
        scopes: list[str] | None = None,
        connection_id: str | None = None,
        redirect_target: str = "/",
    ) -> str:
        """Start an authorization and return the URL the operator must visit."""

        connector = await self._connector(connector_id)
        requested = scopes if scopes is not None else list(connector.default_scopes)
        # Authorization before configuration: refusing for lack of a role must not be
        # masked by a missing client, and must not depend on one being present.
        self._check_scopes(connector, requested)
        await self._check_role(connector, principal, workspace_id)
        client = self._client(connector_id)
        transaction = OAuthTransaction(
            transaction_id=new_id("oauth_transaction"),
            purpose=(
                OAuthTransactionPurpose.REAUTHORIZE
                if connection_id
                else OAuthTransactionPurpose.CONNECT
            ),
            state=secrets.token_urlsafe(32),
            code_verifier=secrets.token_urlsafe(48),
            connector_id=connector_id,
            principal_id=principal.principal_id,
            workspace_id=workspace_id,
            connection_id=connection_id,
            requested_scopes=requested,
            redirect_target=redirect_target,
            expires_at=datetime.now(UTC) + _TRANSACTION_TTL,
        )
        await self.store.create_oauth_transaction(transaction)
        return client.authorization_url(
            state=transaction.state,
            scopes=requested,
            code_verifier=transaction.code_verifier,
        )

    # ------------------------------------------------------------------- callback

    async def complete(
        self, *, connector_id: str, state: str, code: str, principal: Principal
    ) -> Connection:
        """Redeem an authorization code and bind the credential to a connection."""

        transaction = await self.store.consume_oauth_transaction(state)
        if transaction is None:
            # Unknown, replayed, or expired all fail the same way (AC3-SEC-04).
            raise ConnectionError("unknown, reused, or expired authorization state")
        if transaction.connector_id != connector_id:
            raise ConnectionError("authorization state belongs to a different connector")
        if transaction.principal_id != principal.principal_id:
            # A second, independent binding beyond `state`: the browser that returns
            # must be the session that started (INV3-008).
            raise ConnectionError("authorization state belongs to a different principal")

        connector = await self._connector(connector_id)
        client = self._client(connector_id)
        try:
            response = await client.exchange_code(
                code=code, code_verifier=transaction.code_verifier
            )
        except OAuthError as exc:
            raise ConnectionError("authorization code exchange failed") from exc

        handle = await self.broker.store_authorization(
            connector=connector,
            principal_id=transaction.principal_id,
            workspace_id=transaction.workspace_id,
            response=response,
        )
        existing = (
            await self.store.get_connection(transaction.connection_id)
            if transaction.connection_id
            else None
        )
        connection = Connection(
            connection_id=existing.connection_id if existing else new_id("conn"),
            connector_id=connector_id,
            workspace_id=transaction.workspace_id,
            principal_id=transaction.principal_id,
            scope=connector.connection_scope or ConnectionScope.USER,
            token_handle_ref=handle.token_handle_id,
            # Recorded from the provider's answer, never the request.
            granted_scopes=list(handle.scopes),
            status=ConnectionStatus.ACTIVE,
            created_at=existing.created_at if existing else datetime.now(UTC),
        )
        return await self.store.upsert_connection(connection)

    # -------------------------------------------------------------------- revoke

    async def revoke(self, *, connection_id: str, principal: Principal) -> Connection:
        connection = await self._owned(connection_id, principal)
        if connection.token_handle_ref:
            handle = await self.store.get_token_handle(connection.token_handle_ref)
            if handle is not None:
                try:
                    await self.broker.revoke(handle)
                except TokenBrokerError:
                    # Local revocation is authoritative even when the provider is down.
                    pass
        revoked = connection.model_copy(
            update={"status": ConnectionStatus.REVOKED, "granted_scopes": []}
        )
        return await self.store.upsert_connection(revoked)

    # -------------------------------------------------------------------- health

    async def health(self, *, connection_id: str, principal: Principal) -> dict[str, object]:
        connection = await self._owned(connection_id, principal)
        token_status: str | None = None
        if connection.token_handle_ref:
            handle = await self.store.get_token_handle(connection.token_handle_ref)
            if handle is not None:
                token_status = (await self.broker.status(handle)).value
        return {
            "connection_id": connection.connection_id,
            "connector_id": connection.connector_id,
            "status": connection.status.value,
            "granted_scopes": list(connection.granted_scopes),
            # The handle id is an opaque correlation key, never a credential.
            "token_handle_ref": connection.token_handle_ref,
            "token_status": token_status or TokenStatus.ERROR.value,
            "last_health_check": datetime.now(UTC).isoformat(),
        }

    # ----------------------------------------------------------------- internals

    async def _connector(self, connector_id: str) -> ConnectorDefinition:
        connector = await self.store.get_connector_definition(connector_id)
        if connector is None:
            raise ConnectionError(f"connector {connector_id} is not registered")
        return connector

    def _client(self, connector_id: str) -> OAuthClient:
        client = self.clients.get(connector_id)
        if client is None:
            raise ConnectionError(f"connector {connector_id} has no configured OAuth client")
        return client

    async def _owned(self, connection_id: str, principal: Principal) -> Connection:
        connection = await self.store.get_connection(connection_id)
        if connection is None:
            raise ConnectionError(f"connection {connection_id} does not exist")
        if connection.principal_id != principal.principal_id:
            # Refuse rather than disclose that another principal holds it.
            raise ConnectionError(f"connection {connection_id} does not exist")
        return connection

    async def _check_role(
        self, connector: ConnectorDefinition, principal: Principal, workspace_id: str
    ) -> None:
        """Gate workspace-shared connections on workspace role (INV3-009, AC3-ID-04).

        A USER connection is the principal's own business. A WORKSPACE connection acts
        for everyone in the workspace, so the SDD requires explicit admin policy. Role
        is read from the store on every call, so a change takes effect immediately and
        needs no reinstall of anything.
        """

        if connector.connection_scope is not ConnectionScope.WORKSPACE:
            return
        memberships = await self.store.list_workspace_memberships(
            workspace_id=workspace_id, principal_id=principal.principal_id
        )
        roles = {item.role for item in memberships}
        if not roles & {WorkspaceRole.OWNER, WorkspaceRole.ADMIN}:
            raise ConnectionError(
                "a workspace connection requires the OWNER or ADMIN role in "
                f"{workspace_id}"
            )

    @staticmethod
    def _check_scopes(connector: ConnectorDefinition, requested: list[str]) -> None:
        allowed = set(connector.default_scopes) | set(connector.optional_scopes)
        extra = sorted(set(requested) - allowed)
        if extra:
            # A connector cannot be talked into scopes it never declared (INV3-007).
            raise ConnectionError(f"connector does not declare scopes: {', '.join(extra)}")
