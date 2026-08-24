"""Identity service and OIDC Authorization Code + PKCE client (v0.3 M1, SDD §5).

SSO answers "who are you in Accretion"; it never authorizes external tools
(ADR3-003). Principals are keyed by ``(issuer, subject)`` — never email —
so a changed email can never mint a duplicate identity (AC3-ID-02). The
``LOCAL_PRINCIPAL`` mode gives single-user local operation a deterministic
principal without an identity provider (OQ3-17).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from accretion.contracts import (
    AuthSession,
    AuthTransaction,
    Principal,
    PrincipalStatus,
    PrincipalType,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.ids import new_id
from accretion.persistence.store import StateStore

LOCAL_ISSUER = "accretion-local"
LOCAL_WORKSPACE_ID = "workspace_local"
_TRANSACTION_TTL = timedelta(minutes=10)


class AuthenticationError(Exception):
    """Authentication failed; never carries token material."""


class AuthorizationError(Exception):
    """The authenticated principal may not perform this action."""


@dataclass(frozen=True)
class IdentityClaims:
    issuer: str
    subject: str
    email: str | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class OidcProviderConfig:
    issuer: str
    client_id: str
    client_secret: str = ""
    redirect_url: str = "http://localhost:8000/api/v1/auth/callback"
    scopes: str = "openid profile email"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def code_challenge_s256(code_verifier: str) -> str:
    return _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())


@dataclass
class OidcClient:
    config: OidcProviderConfig
    http: httpx.AsyncClient
    _metadata: dict[str, Any] | None = field(default=None, init=False)
    _jwks: dict[str, Any] | None = field(default=None, init=False)

    async def discover(self) -> dict[str, Any]:
        if self._metadata is None:
            url = self.config.issuer.rstrip("/") + "/.well-known/openid-configuration"
            response = await self.http.get(url)
            response.raise_for_status()
            metadata: dict[str, Any] = response.json()
            if metadata.get("issuer") != self.config.issuer:
                raise AuthenticationError("identity provider issuer mismatch")
            self._metadata = metadata
        return self._metadata

    async def authorization_url(self, *, state: str, nonce: str, code_verifier: str) -> str:
        metadata = await self.discover()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_url,
                "scope": self.config.scopes,
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge_s256(code_verifier),
                "code_challenge_method": "S256",
            }
        )
        return f"{metadata['authorization_endpoint']}?{query}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> str:
        metadata = await self.discover()
        response = await self.http.post(
            metadata["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_url,
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "code_verifier": code_verifier,
            },
        )
        if response.status_code != 200:
            raise AuthenticationError("authorization code exchange failed")
        payload: dict[str, Any] = response.json()
        id_token = payload.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise AuthenticationError("identity provider returned no id_token")
        return id_token

    async def _signing_key(self, id_token: str) -> Any:
        metadata = await self.discover()
        if self._jwks is None:
            response = await self.http.get(metadata["jwks_uri"])
            response.raise_for_status()
            self._jwks = response.json()
        assert self._jwks is not None
        header = jwt.get_unverified_header(id_token)
        for key_data in self._jwks.get("keys", []):
            if key_data.get("kid") == header.get("kid"):
                return jwt.PyJWK(key_data).key
        raise AuthenticationError("no matching signing key for id_token")

    async def validate_id_token(self, id_token: str, *, nonce: str) -> IdentityClaims:
        key = await self._signing_key(id_token)
        try:
            claims = jwt.decode(
                id_token,
                key=key,
                algorithms=["RS256"],
                audience=self.config.client_id,
                issuer=self.config.issuer,
                options={"require": ["iss", "aud", "exp", "sub"]},
            )
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError(f"id_token validation failed: {type(exc).__name__}") from exc
        if claims.get("nonce") != nonce:
            raise AuthenticationError("id_token nonce mismatch")
        return IdentityClaims(
            issuer=str(claims["iss"]),
            subject=str(claims["sub"]),
            email=claims.get("email"),
            display_name=claims.get("name"),
        )


class IdentityService:
    def __init__(
        self,
        store: StateStore,
        oidc: OidcClient | None = None,
        *,
        session_ttl_seconds: int = 28_800,
        local_subject: str = "local-operator",
    ) -> None:
        self.store = store
        self.oidc = oidc
        self.session_ttl = timedelta(seconds=session_ttl_seconds)
        self.local_subject = local_subject

    def _require_oidc(self) -> OidcClient:
        if self.oidc is None:
            raise AuthenticationError("OIDC is not configured for this deployment")
        return self.oidc

    async def begin_login(self, redirect_target: str = "/") -> str:
        oidc = self._require_oidc()
        transaction = AuthTransaction(
            transaction_id=new_id("auth_transaction"),
            state=secrets.token_urlsafe(32),
            nonce=secrets.token_urlsafe(32),
            code_verifier=secrets.token_urlsafe(48),
            redirect_target=redirect_target,
            expires_at=datetime.now(UTC) + _TRANSACTION_TTL,
        )
        await self.store.create_auth_transaction(transaction)
        return await oidc.authorization_url(
            state=transaction.state,
            nonce=transaction.nonce,
            code_verifier=transaction.code_verifier,
        )

    async def complete_login(self, *, state: str, code: str) -> tuple[Principal, AuthSession]:
        oidc = self._require_oidc()
        transaction = await self.store.consume_auth_transaction(state)
        if transaction is None:
            raise AuthenticationError("unknown, reused, or expired login state")
        id_token = await oidc.exchange_code(
            code=code, code_verifier=transaction.code_verifier
        )
        claims = await oidc.validate_id_token(id_token, nonce=transaction.nonce)
        principal = await self.store.upsert_principal(
            Principal(
                principal_id=new_id("principal"),
                type=PrincipalType.HUMAN,
                issuer=claims.issuer,
                subject=claims.subject,
                email=claims.email,
                display_name=claims.display_name,
            )
        )
        if principal.status is PrincipalStatus.DISABLED:
            raise AuthorizationError("principal is disabled")
        await self._ensure_workspace_membership(principal, WorkspaceRole.DEVELOPER)
        session = await self.store.create_auth_session(
            AuthSession(
                auth_session_id=new_id("auth_session"),
                principal_id=principal.principal_id,
                expires_at=datetime.now(UTC) + self.session_ttl,
            )
        )
        return principal, session

    async def resolve_session(self, auth_session_id: str) -> Principal | None:
        session = await self.store.get_auth_session(auth_session_id)
        if session is None:
            return None
        return await self.store.get_principal(session.principal_id)

    async def logout(self, auth_session_id: str) -> None:
        await self.store.revoke_auth_session(auth_session_id)

    async def local_principal(self) -> Principal:
        principal = await self.store.get_principal_by_identity(
            LOCAL_ISSUER, self.local_subject
        )
        if principal is None:
            principal = await self.store.upsert_principal(
                Principal(
                    principal_id=new_id("principal"),
                    type=PrincipalType.HUMAN,
                    issuer=LOCAL_ISSUER,
                    subject=self.local_subject,
                    display_name=self.local_subject,
                )
            )
        await self._ensure_workspace_membership(principal, WorkspaceRole.OWNER)
        return principal

    async def _ensure_workspace_membership(
        self, principal: Principal, role: WorkspaceRole
    ) -> None:
        memberships = await self.store.list_workspace_memberships(
            principal_id=principal.principal_id
        )
        if memberships:
            return
        await self.store.upsert_workspace(
            WorkspaceEntity(workspace_id=LOCAL_WORKSPACE_ID, name="Local workspace")
        )
        await self.store.upsert_workspace_membership(
            WorkspaceMembership(
                membership_id=new_id("workspace_membership"),
                workspace_id=LOCAL_WORKSPACE_ID,
                principal_id=principal.principal_id,
                role=role,
            )
        )
