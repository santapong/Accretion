"""Token Broker (v0.3 M2, SDD §13).

The sole credential authority (ADR3-004). It is the only component that receives or
decrypts refresh tokens, and it returns opaque handles to everything else (INV3-002).

``EphemeralCredential`` is deliberately not a contract model: it must never be
serializable into an ``AgentEvent`` or model-facing context (§13.2), so it has no
``model_dump`` and its repr never renders the value.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from accretion.contracts import (
    ConnectorDefinition,
    TokenHandle,
    TokenStatus,
)
from accretion.ids import new_id
from accretion.oauth import OAuthClient, OAuthError, OAuthTokenResponse
from accretion.persistence.store import StateStore
from accretion.secrets_store import SecretRecord, SecretStore, SecretStoreError

# Refresh a little before expiry so a call cannot start with a token that dies mid-flight.
_REFRESH_SKEW = timedelta(seconds=120)

#: A re-acquisition hook: given an expiring handle, obtain fresh access material for it
#: without any end-user interaction. Registered per connector by a subsystem that holds
#: a durable authority of its own — enterprise-managed authorization holds a retained
#: identity assertion (v0.3 M7) — and consulted only when the stored response carries no
#: refresh token, which is exactly the case for an RFC 7523 ``jwt-bearer`` grant.
Reacquirer = Callable[[TokenHandle], Awaitable[OAuthTokenResponse]]


class TokenBrokerError(RuntimeError):
    """Credential brokerage failed; never carries token material."""


@dataclass(frozen=True)
class EphemeralCredential:
    """Short-lived access material, valid only inside a gateway process boundary.

    Never persisted, never serialized, never logged.
    """

    token_handle_id: str
    _access_token: str
    token_type: str = "bearer"
    expires_at: datetime | None = None

    def authorization_header(self) -> str:
        return f"{self.token_type.capitalize()} {self._access_token}"

    def reveal(self) -> str:
        """Return the raw token. Only the tool-execution boundary may call this."""

        return self._access_token

    def __repr__(self) -> str:
        return f"EphemeralCredential(handle={self.token_handle_id!r}, redacted)"

    __str__ = __repr__


class TokenBroker(Protocol):
    async def store_authorization(
        self,
        *,
        connector: ConnectorDefinition,
        principal_id: str | None,
        workspace_id: str,
        response: OAuthTokenResponse,
    ) -> TokenHandle: ...

    async def get_access_material(
        self,
        handle: TokenHandle,
        *,
        audience: list[str],
        scopes: list[str],
        expected_issuer: str | None = None,
    ) -> EphemeralCredential: ...

    async def refresh(self, handle: TokenHandle) -> TokenHandle: ...

    def register_reacquirer(self, connector_id: str, reacquire: Reacquirer) -> None: ...

    async def revoke(self, handle: TokenHandle) -> None: ...

    async def status(self, handle: TokenHandle) -> TokenStatus: ...


class EncryptedTokenBroker:
    """Token broker backed by an encrypted secret store."""

    def __init__(
        self,
        store: StateStore,
        secrets: SecretStore,
        clients: dict[str, OAuthClient] | None = None,
    ) -> None:
        self.store = store
        self.secrets = secrets
        self.clients = clients or {}
        self._reacquirers: dict[str, Reacquirer] = {}

    def register_reacquirer(self, connector_id: str, reacquire: Reacquirer) -> None:
        """Nominate the authority that can renew this connector's tokens unattended.

        Last registration wins, so re-registering the same connector is idempotent
        and cannot accumulate stale hooks. The broker stays the single expiry
        authority (ADR3-004): it decides *when* material is stale, and the hook only
        answers *how* to obtain more.
        """

        self._reacquirers[connector_id] = reacquire

    # ------------------------------------------------------------------ storing

    async def store_authorization(
        self,
        *,
        connector: ConnectorDefinition,
        principal_id: str | None,
        workspace_id: str,
        response: OAuthTokenResponse,
    ) -> TokenHandle:
        handle_id = new_id("token_handle")
        record = await self.secrets.seal(
            _serialize(response), associated_id=handle_id
        )
        handle = TokenHandle(
            token_handle_id=handle_id,
            connector_id=connector.connector_id,
            principal_id=principal_id,
            workspace_id=workspace_id,
            issuer=connector.authorization_server or connector.connector_id,
            # Granted scopes come from the provider's answer, never from the request:
            # a server may grant fewer than were asked for.
            scopes=list(response.granted_scopes),
            audience=list(connector.resource_server and [connector.resource_server] or []),
            expires_at=_expiry(response.expires_in),
            secret_store_key=record.secret_store_key,
            status=TokenStatus.ACTIVE,
        )
        await self.store.upsert_secret_record(record)
        return await self.store.upsert_token_handle(handle)

    # ------------------------------------------------------------------ reading

    async def get_access_material(
        self,
        handle: TokenHandle,
        *,
        audience: list[str],
        scopes: list[str],
        expected_issuer: str | None = None,
    ) -> EphemeralCredential:
        if handle.status is not TokenStatus.ACTIVE:
            raise TokenBrokerError(f"token handle is {handle.status.value}")
        # AC3-CON-06. Both checks fail closed: a credential whose issuer or audience
        # cannot be *shown* to cover the request is refused, rather than allowed
        # because one side of the comparison happens to be empty.
        if expected_issuer is not None and handle.issuer != expected_issuer:
            raise TokenBrokerError("token issuer does not match the requesting connector")
        if audience:
            if not handle.audience:
                raise TokenBrokerError("token records no audience to check the request against")
            if not set(audience) <= set(handle.audience):
                raise TokenBrokerError("token audience does not cover the requested resource")
        if scopes and not set(scopes) <= set(handle.scopes):
            raise TokenBrokerError("token scopes do not cover the requested capability")
        if _is_stale(handle):
            handle = await self.refresh(handle)
        response = await self._open(handle)
        return EphemeralCredential(
            token_handle_id=handle.token_handle_id,
            _access_token=response.access_token,
            token_type=response.token_type,
            expires_at=handle.expires_at,
        )

    async def status(self, handle: TokenHandle) -> TokenStatus:
        if handle.status is TokenStatus.ACTIVE and _is_expired(handle):
            return TokenStatus.EXPIRED
        return handle.status

    # ------------------------------------------------------------- lifecycle

    async def refresh(self, handle: TokenHandle) -> TokenHandle:
        response = await self._open(handle)
        if not response.refresh_token:
            # No refresh token is not automatically the end of the credential: a
            # connector may have registered an authority that can re-acquire without
            # the end user. Only when there is none — or when it declines — does the
            # handle die, exactly as it did before (v0.3 M2 behaviour is unchanged
            # for every connector that registers nothing).
            reacquire = self._reacquirers.get(handle.connector_id)
            if reacquire is None:
                await self._mark(handle, TokenStatus.EXPIRED)
                raise TokenBrokerError("token handle has no refresh token")
            try:
                reacquired = await reacquire(handle)
            except Exception as exc:
                # The hook failed closed: the handle is as dead as it would have been
                # without one, and the reason stays with the subsystem that raised it.
                await self._mark(handle, TokenStatus.EXPIRED)
                raise TokenBrokerError(
                    "token handle could not be re-acquired without the end user"
                ) from exc
            return await self._reseal(handle, reacquired)
        client = self._client(handle.connector_id)
        try:
            refreshed = await client.refresh(response.refresh_token)
        except OAuthError as exc:
            await self._mark(handle, TokenStatus.ERROR)
            raise TokenBrokerError("token refresh was rejected") from exc
        # A refresh response often omits the refresh token; keep the existing one.
        if not refreshed.refresh_token:
            refreshed.refresh_token = response.refresh_token
        if not refreshed.granted_scopes:
            refreshed.granted_scopes = list(handle.scopes)
        return await self._reseal(handle, refreshed)

    async def _reseal(
        self, handle: TokenHandle, response: OAuthTokenResponse
    ) -> TokenHandle:
        """Replace a handle's sealed material in place, keeping its identity."""

        if not response.granted_scopes:
            response.granted_scopes = list(handle.scopes)
        record = await self.secrets.seal(
            _serialize(response), associated_id=handle.token_handle_id
        )
        await self.store.upsert_secret_record(record)
        return await self.store.upsert_token_handle(
            handle.model_copy(
                update={
                    "secret_store_key": record.secret_store_key,
                    "scopes": list(response.granted_scopes),
                    "expires_at": _expiry(response.expires_in),
                    "status": TokenStatus.ACTIVE,
                    "refreshed_at": datetime.now(UTC),
                }
            )
        )

    async def revoke(self, handle: TokenHandle) -> None:
        try:
            response = await self._open(handle)
        except TokenBrokerError:
            response = None
        if response is not None:
            client = self.clients.get(handle.connector_id)
            if client is not None:
                # Best effort: local revocation must succeed even if the provider is down.
                try:
                    await client.revoke(response.refresh_token or response.access_token)
                except OAuthError:
                    pass
        await self.store.delete_secret_record(handle.secret_store_key)
        await self._mark(handle, TokenStatus.REVOKED)

    # -------------------------------------------------------------- internals

    async def _open(self, handle: TokenHandle) -> OAuthTokenResponse:
        record = await self.store.get_secret_record(handle.secret_store_key)
        if record is None:
            raise TokenBrokerError("stored credential is missing")
        try:
            plaintext = await self.secrets.open(
                record, associated_id=handle.token_handle_id
            )
        except SecretStoreError as exc:
            raise TokenBrokerError("stored credential could not be opened") from exc
        return _deserialize(plaintext)

    async def _mark(self, handle: TokenHandle, status: TokenStatus) -> TokenHandle:
        return await self.store.upsert_token_handle(
            handle.model_copy(update={"status": status})
        )

    def _client(self, connector_id: str) -> OAuthClient:
        client = self.clients.get(connector_id)
        if client is None:
            raise TokenBrokerError(f"no OAuth client registered for {connector_id}")
        return client


def _serialize(response: OAuthTokenResponse) -> str:
    return json.dumps(
        {
            "access_token": response.access_token,
            "refresh_token": response.refresh_token,
            "expires_in": response.expires_in,
            "refresh_token_expires_in": response.refresh_token_expires_in,
            "granted_scopes": response.granted_scopes,
            "token_type": response.token_type,
        }
    )


def _deserialize(plaintext: str) -> OAuthTokenResponse:
    payload = json.loads(plaintext)
    return OAuthTokenResponse(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token"),
        expires_in=payload.get("expires_in"),
        refresh_token_expires_in=payload.get("refresh_token_expires_in"),
        granted_scopes=list(payload.get("granted_scopes") or []),
        token_type=payload.get("token_type", "bearer"),
    )


def _expiry(expires_in: int | None) -> datetime | None:
    return datetime.now(UTC) + timedelta(seconds=expires_in) if expires_in else None


def _is_expired(handle: TokenHandle) -> bool:
    return handle.expires_at is not None and handle.expires_at <= datetime.now(UTC)


def _is_stale(handle: TokenHandle) -> bool:
    return (
        handle.expires_at is not None
        and handle.expires_at - _REFRESH_SKEW <= datetime.now(UTC)
    )


__all__ = [
    "EncryptedTokenBroker",
    "EphemeralCredential",
    "SecretRecord",
    "Reacquirer",
    "TokenBroker",
    "TokenBrokerError",
]
