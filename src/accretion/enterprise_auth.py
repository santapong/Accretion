"""Enterprise-managed authorization (v0.3 M7, SDD §8, OQ3-08).

Optional and off by default. When an operator raises ``enable_enterprise_auth`` and
points ``enterprise_auth_token_exchange_url`` at an identity provider, a principal who
has signed in once can reach a centrally governed MCP server with no further end-user
authorization step: the retained identity assertion is exchanged for a single-audience
identity assertion grant (RFC 8693 ``id-jag``), and that grant is presented to the
connector's authorization server as an RFC 7523 ``jwt-bearer`` assertion.

Three properties this module exists to hold:

* **Nothing is cached.** No memoisation of assertions, grants, JWKS, or decisions —
  every acquisition re-reads the store and re-validates. AC3-EMA-04 depends on a
  revoked assertion being unusable on the very next call, and AC3-SEC-05 on there
  being no resolution cache to purge.
* **The grant is validated locally before the authorization server is called.** A
  wrong issuer, wrong audience, or expired lifetime is refused here, so a refusal
  costs the authorization server nothing and cannot be laundered into a token
  (AC3-EMA-03).
* **Every exit path writes exactly one ``EnterpriseAuthGrant``.** Refusals are
  evidence, so they are recorded with the same weight as a success.

Token material never leaves this module: the assertion is sealed by the secret store
under the auth session id, and the access token goes straight to the token broker,
which hands back an opaque handle (INV3-002).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import jwt

from accretion.config import Settings
from accretion.contracts import (
    AssertionStatus,
    AuthSession,
    Connection,
    ConnectionScope,
    ConnectionStatus,
    ConnectorDefinition,
    EnterpriseAuthGrant,
    EnterpriseAuthOutcome,
    IdentityAssertion,
    McpServerDefinition,
    TokenHandle,
)
from accretion.identity import IdentityClaims
from accretion.ids import new_id
from accretion.oauth import OAuthClient, OAuthError, OAuthTokenResponse
from accretion.persistence.store import StateStore
from accretion.secrets_store import SecretStore, SecretStoreError
from accretion.token_broker import TokenBroker

TOKEN_EXCHANGE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"
ID_JAG_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id-jag"
JWT_BEARER_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"

#: Grant rows that are not about one connector in one workspace — session-wide
#: revocation, for instance — record this instead of inventing a scope.
UNSCOPED = "*"


class EnterpriseAuthError(RuntimeError):
    """Enterprise authorization failed; never carries assertion or token material."""


class EnterpriseAuthDisabled(EnterpriseAuthError):
    """Enterprise authorization is switched off, or configured inert."""


def _claims_without_verification(token: str) -> dict[str, Any]:
    """Read a compact JWT's claims for local policy checks.

    The signature is deliberately not checked here: the subject id_token was already
    verified by ``OidcClient`` when it was retained, and the grant's signature is
    verified by the authorization server that will act on it. What this module owes
    is the *policy* check — issuer, audience, lifetime — before the grant travels.
    """

    try:
        decoded: dict[str, Any] = jwt.decode(
            token,
            options={"verify_signature": False, "verify_aud": False, "verify_exp": False},
            algorithms=["RS256", "ES256", "HS256"],
        )
    except jwt.PyJWTError as exc:
        raise EnterpriseAuthError("token could not be parsed") from exc
    return decoded


def _expiry_of(token: str) -> datetime:
    claims = _claims_without_verification(token)
    exp = claims.get("exp")
    if not isinstance(exp, int | float):
        raise EnterpriseAuthError("token carries no expiry")
    return datetime.fromtimestamp(float(exp), tz=UTC)


def _audience_values(claim: Any) -> list[str]:
    if isinstance(claim, str):
        return [claim]
    if isinstance(claim, list):
        return [value for value in claim if isinstance(value, str)]
    return []


def token_endpoint_for(connector: ConnectorDefinition) -> str:
    """Where this connector's authorization server takes a jwt-bearer grant.

    ``authorization_server`` is the issuer, as the token broker also reads it; the
    RFC 6749 token endpoint hangs off it unless the operator already named it.
    """

    base = (connector.authorization_server or "").rstrip("/")
    if not base:
        raise EnterpriseAuthError(
            f"connector {connector.connector_id} names no authorization server"
        )
    return base if base.endswith("/token") else f"{base}/token"


@dataclass
class IdentityAssertionClient:
    """RFC 8693 token exchange: an id_token in, an ID-JAG out.

    The exchange endpoint comes from ``Settings`` rather than from the connector, so
    an empty ``enterprise_auth_token_exchange_url`` leaves the subsystem inert even
    with the feature flag raised — enabling the flag alone cannot open egress.
    """

    settings: Settings
    http: httpx.AsyncClient
    client_id: str = ""
    client_secret: str = ""

    async def exchange(
        self, subject_token: str, *, audience: str, resource: str | None = None
    ) -> str:
        url = self.settings.enterprise_auth_token_exchange_url
        if not url:
            raise EnterpriseAuthDisabled("no token exchange endpoint is configured")
        form = {
            "grant_type": TOKEN_EXCHANGE_GRANT_TYPE,
            "subject_token": subject_token,
            "subject_token_type": SUBJECT_TOKEN_TYPE,
            "requested_token_type": ID_JAG_TOKEN_TYPE,
            "audience": audience,
        }
        if resource:
            form["resource"] = resource
        if self.client_id:
            form["client_id"] = self.client_id
        if self.client_secret:
            form["client_secret"] = self.client_secret
        try:
            response = await self.http.post(
                url, data=form, headers={"Accept": "application/json"}
            )
        except httpx.HTTPError as exc:
            raise EnterpriseAuthError("token exchange request failed") from exc
        if response.status_code != 200:
            # The body can echo the submitted assertion, so it is never surfaced.
            raise EnterpriseAuthError(
                f"token exchange was rejected with {response.status_code}"
            )
        payload: dict[str, Any] = response.json()
        issued_type = payload.get("issued_token_type")
        if issued_type != ID_JAG_TOKEN_TYPE:
            raise EnterpriseAuthError("token exchange returned the wrong token type")
        grant = payload.get("access_token")
        if not isinstance(grant, str) or not grant:
            raise EnterpriseAuthError("token exchange returned no assertion grant")
        return grant


@dataclass
class JwtBearerClient:
    """RFC 7523: present an ID-JAG to an authorization server for an access token.

    Returns the ``OAuthTokenResponse`` the M2 broker already speaks, so sealing and
    the redacting ``__repr__`` apply to enterprise tokens unchanged.
    """

    http: httpx.AsyncClient
    client_id: str = ""
    client_secret: str = ""

    async def grant(
        self, assertion: str, *, token_endpoint: str, scopes: list[str]
    ) -> OAuthTokenResponse:
        form = {"grant_type": JWT_BEARER_GRANT_TYPE, "assertion": assertion}
        if scopes:
            form["scope"] = " ".join(scopes)
        if self.client_id:
            form["client_id"] = self.client_id
        if self.client_secret:
            form["client_secret"] = self.client_secret
        try:
            response = await self.http.post(
                token_endpoint, data=form, headers={"Accept": "application/json"}
            )
        except httpx.HTTPError as exc:
            raise EnterpriseAuthError("enterprise token request failed") from exc
        if response.status_code != 200:
            raise EnterpriseAuthError(
                f"enterprise token endpoint rejected the grant with {response.status_code}"
            )
        try:
            return OAuthClient.parse_token_response(response.json())
        except OAuthError as exc:
            raise EnterpriseAuthError("enterprise token endpoint returned no token") from exc


class EnterpriseAuthManager:
    """Retains identity assertions and mints enterprise-authorized connections.

    Deliberately stateless between calls: it holds collaborators, never decisions.
    """

    def __init__(
        self,
        store: StateStore,
        secrets: SecretStore,
        broker: TokenBroker,
        settings: Settings,
        assertion_client: IdentityAssertionClient,
        grant_client: JwtBearerClient,
    ) -> None:
        self.store = store
        self.secrets = secrets
        self.broker = broker
        self.settings = settings
        self.assertion_client = assertion_client
        self.grant_client = grant_client

    @property
    def enabled(self) -> bool:
        """Both gates: the flag, and a configured exchange endpoint."""

        return bool(
            self.settings.enable_enterprise_auth
            and self.settings.enterprise_auth_token_exchange_url
        )

    # ----------------------------------------------------------------- retention

    async def retain_assertion(
        self, id_token: str, *, session: AuthSession, claims: IdentityClaims
    ) -> IdentityAssertion | None:
        """Seal a verified id_token for later exchange, or keep nothing.

        Returns ``None`` when enterprise authorization is off, which is what makes
        AC3-EMA-01 mechanical: with the flag down, signing in retains nothing at all.
        The assertion's own ``exp`` bounds the row, never the session TTL, so an
        assertion can never outlive the token it addresses. A token this module
        cannot bound is recorded and dropped rather than raised: retention is
        optional, login is not.
        """

        if not self.settings.enable_enterprise_auth:
            return None
        try:
            expires_at = _expiry_of(id_token)
        except EnterpriseAuthError as exc:
            # An optional subsystem must never break the mandatory login path: the
            # sign-in itself was already verified, so an unreadable or unbounded
            # token retains nothing and is recorded. Acquisition then degrades to
            # REFUSED_MISSING, which is the truthful reading of "kept nothing".
            await self._record(
                principal_id=session.principal_id,
                workspace_id=UNSCOPED,
                connector_id=UNSCOPED,
                outcome=EnterpriseAuthOutcome.REFUSED_CONFIGURATION,
                detail=(
                    "no identity assertion was retained: the verified token carries "
                    f"no usable expiry ({exc})"
                ),
            )
            return None
        record = await self.secrets.seal(id_token, associated_id=session.auth_session_id)
        await self.store.upsert_secret_record(record)
        assertion = IdentityAssertion(
            assertion_id=new_id("identity_assertion"),
            auth_session_id=session.auth_session_id,
            principal_id=session.principal_id,
            issuer=claims.issuer,
            subject=claims.subject,
            secret_store_key=record.secret_store_key,
            expires_at=expires_at,
        )
        return await self.store.upsert_identity_assertion(assertion)

    async def revoke_for_session(self, auth_session_id: str) -> IdentityAssertion | None:
        """Destroy the sealed assertion and mark the row revoked.

        The ciphertext goes; the row stays. There is no store method that deletes an
        identity assertion — the row is the evidence AC3-EMA-04 reads back, and
        ``delete_secret_record`` remains the single deletion surface (AC3-PLG-05).
        """

        assertion = await self.store.get_identity_assertion_for_session(auth_session_id)
        if assertion is None:
            return None
        await self.store.delete_secret_record(assertion.secret_store_key)
        revoked = await self.store.upsert_identity_assertion(
            assertion.model_copy(update={"status": AssertionStatus.REVOKED})
        )
        await self._record(
            principal_id=assertion.principal_id,
            workspace_id=UNSCOPED,
            connector_id=UNSCOPED,
            outcome=EnterpriseAuthOutcome.REVOKED,
            detail="the retained identity assertion was destroyed",
        )
        return revoked

    # ------------------------------------------------------------- acquisition

    async def ensure_access(
        self,
        connector: ConnectorDefinition,
        server: McpServerDefinition | None,
        *,
        principal_id: str,
        workspace_id: str,
    ) -> Connection:
        """Mint (or refresh) an ACTIVE enterprise-authorized connection.

        Raises ``EnterpriseAuthError`` on every refusal, having first recorded it.
        """

        mcp_server_id = server.mcp_server_id if server is not None else None
        # The connection is resolved first so that a revoked one is refused before
        # anything is exchanged or granted: a revocation costs the identity provider
        # and the authorization server nothing.
        connection = await self._connection_for(
            connector,
            principal_id=principal_id,
            workspace_id=workspace_id,
            mcp_server_id=mcp_server_id,
        )
        response = await self._acquire(
            connector,
            principal_id=principal_id,
            workspace_id=workspace_id,
            mcp_server_id=mcp_server_id,
            outcome=EnterpriseAuthOutcome.GRANTED,
        )
        superseded = connection.token_handle_ref
        handle = await self.broker.store_authorization(
            connector=connector,
            principal_id=principal_id,
            workspace_id=workspace_id,
            response=response,
        )
        # Re-acquiring for a connection that already holds material would otherwise
        # leave the previous handle ACTIVE and its sealed token undeleted, so
        # revoking the connection later would not destroy all of its live
        # credential material (AC3-EMA-04). One connection, one live handle.
        if superseded is not None and superseded != handle.token_handle_id:
            previous = await self.store.get_token_handle(superseded)
            if previous is not None:
                await self.broker.revoke(previous)
        stored = await self.store.upsert_connection(
            connection.model_copy(
                update={
                    "token_handle_ref": handle.token_handle_id,
                    # Granted scopes come from the authorization server's answer,
                    # never from the request.
                    "granted_scopes": list(response.granted_scopes),
                    "status": ConnectionStatus.ACTIVE,
                }
            )
        )
        await self._record(
            principal_id=principal_id,
            workspace_id=workspace_id,
            connector_id=connector.connector_id,
            outcome=EnterpriseAuthOutcome.GRANTED,
            connection_id=stored.connection_id,
            mcp_server_id=mcp_server_id,
            detail="enterprise authorization granted from the retained identity assertion",
        )
        return stored

    async def reacquire(self, handle: TokenHandle) -> OAuthTokenResponse:
        """Broker hook: fetch fresh access material without the end user.

        A jwt-bearer grant returns no refresh token, so this is how an enterprise
        connection survives token expiry inside a valid session (AC3-EMA-07). It
        fails closed when the assertion has expired, and the broker then marks the
        handle expired as it would for any credential it cannot renew.
        """

        if handle.principal_id is None:
            raise EnterpriseAuthError("enterprise re-acquisition needs a principal")
        connector = await self.store.get_connector_definition(handle.connector_id)
        if connector is None:
            raise EnterpriseAuthError(f"connector {handle.connector_id} is not registered")
        # Refuses (and records) when the connection behind this handle was revoked,
        # so renewal cannot outlive an operator's revocation either.
        await self._connection_for(
            connector,
            principal_id=handle.principal_id,
            workspace_id=handle.workspace_id,
        )
        return await self._acquire(
            connector,
            principal_id=handle.principal_id,
            workspace_id=handle.workspace_id,
            mcp_server_id=None,
            outcome=EnterpriseAuthOutcome.REFRESHED,
        )

    # ------------------------------------------------------------------ internal

    async def _acquire(
        self,
        connector: ConnectorDefinition,
        *,
        principal_id: str,
        workspace_id: str,
        mcp_server_id: str | None,
        outcome: EnterpriseAuthOutcome,
    ) -> OAuthTokenResponse:
        """Assertion -> exchange -> local validation -> authorization server.

        The validation sits between the exchange and the grant on purpose: an
        assertion grant the operator's policy rejects must never reach the
        authorization server.
        """

        async def refuse(
            refusal: EnterpriseAuthOutcome, detail: str
        ) -> EnterpriseAuthError:
            await self._record(
                principal_id=principal_id,
                workspace_id=workspace_id,
                connector_id=connector.connector_id,
                outcome=refusal,
                mcp_server_id=mcp_server_id,
                detail=detail,
            )
            if refusal is EnterpriseAuthOutcome.REFUSED_DISABLED:
                return EnterpriseAuthDisabled(detail)
            return EnterpriseAuthError(detail)

        if not self.enabled:
            raise await refuse(
                EnterpriseAuthOutcome.REFUSED_DISABLED,
                "enterprise authorization is not enabled",
            )
        audience = self.settings.enterprise_auth_audiences.get(connector.connector_id, "")
        if not audience:
            raise await refuse(
                EnterpriseAuthOutcome.REFUSED_AUDIENCE,
                "no enterprise audience is configured for this connector",
            )

        # Resolved before anything travels: a connector that names no authorization
        # server is a local misconfiguration, and recording it as REFUSED_UPSTREAM
        # would write an outage that never happened into an append-only trail.
        try:
            token_endpoint = token_endpoint_for(connector)
        except EnterpriseAuthError as exc:
            raise await refuse(
                EnterpriseAuthOutcome.REFUSED_CONFIGURATION,
                "the connector names no authorization server to present the grant to",
            ) from exc

        assertion = await self.store.get_identity_assertion_for_principal(principal_id)
        if assertion is None:
            raise await refuse(
                EnterpriseAuthOutcome.REFUSED_MISSING,
                "no active identity assertion is retained for this principal",
            )
        if assertion.expires_at <= datetime.now(UTC):
            await self.store.upsert_identity_assertion(
                assertion.model_copy(update={"status": AssertionStatus.EXPIRED})
            )
            raise await refuse(
                EnterpriseAuthOutcome.REFUSED_EXPIRED,
                "the retained identity assertion has expired",
            )
        record = await self.store.get_secret_record(assertion.secret_store_key)
        if record is None:
            raise await refuse(
                EnterpriseAuthOutcome.REFUSED_MISSING,
                "the retained identity assertion is no longer available",
            )
        try:
            id_token = await self.secrets.open(
                record, associated_id=assertion.auth_session_id
            )
        except SecretStoreError as exc:
            raise await refuse(
                EnterpriseAuthOutcome.REFUSED_MISSING,
                "the retained identity assertion could not be opened",
            ) from exc

        refusal = self._validate_assertion(id_token, retained_issuer=assertion.issuer)
        if refusal is not None:
            outcome_, detail = refusal
            raise await refuse(outcome_, detail)

        try:
            grant_token = await self.assertion_client.exchange(
                id_token,
                audience=audience,
                resource=connector.resource_server or audience,
            )
        except EnterpriseAuthDisabled as exc:
            raise await refuse(
                EnterpriseAuthOutcome.REFUSED_DISABLED,
                "enterprise authorization is not enabled",
            ) from exc
        except EnterpriseAuthError:
            # The exchange endpoint is unreachable, rejected the subject token, or
            # answered with the wrong material. Recorded like any other exit path,
            # in prose: the response body can echo the submitted assertion.
            await self._record(
                principal_id=principal_id,
                workspace_id=workspace_id,
                connector_id=connector.connector_id,
                outcome=EnterpriseAuthOutcome.REFUSED_UPSTREAM,
                mcp_server_id=mcp_server_id,
                detail=(
                    "the identity provider did not return an assertion grant for "
                    "the retained identity assertion"
                ),
            )
            raise

        refusal = self._validate_grant(
            grant_token, expected_issuer=assertion.issuer, expected_audience=audience
        )
        if refusal is not None:
            outcome_, detail = refusal
            raise await refuse(outcome_, detail)

        try:
            response = await self.grant_client.grant(
                grant_token,
                token_endpoint=token_endpoint,
                scopes=list(connector.default_scopes),
            )
        except EnterpriseAuthError:
            await self._record(
                principal_id=principal_id,
                workspace_id=workspace_id,
                connector_id=connector.connector_id,
                outcome=EnterpriseAuthOutcome.REFUSED_UPSTREAM,
                mcp_server_id=mcp_server_id,
                detail=(
                    "the connector authorization server did not return enterprise "
                    "access material"
                ),
            )
            raise
        if outcome is EnterpriseAuthOutcome.REFRESHED:
            await self._record(
                principal_id=principal_id,
                workspace_id=workspace_id,
                connector_id=connector.connector_id,
                outcome=EnterpriseAuthOutcome.REFRESHED,
                mcp_server_id=mcp_server_id,
                detail="enterprise access material was renewed without user interaction",
            )
        return response

    def _validate_assertion(
        self, id_token: str, *, retained_issuer: str
    ) -> tuple[EnterpriseAuthOutcome, str] | None:
        """Local policy check on the retained id_token, before any exchange.

        The token was verified when it was retained, but the retention may have been
        hours ago and the operator's OIDC configuration is the current authority: an
        assertion whose issuer, audience, or lifetime no longer satisfies it must be
        refused here rather than handed to the identity provider to refuse. That is
        what "refused, and the refusal is recorded" buys — a refusal that costs the
        identity provider nothing and cannot be laundered into an assertion grant
        (AC3-EMA-03).

        The configured issuer wins over the one stored on the row, so rotating
        ``oidc_issuer`` invalidates assertions minted under the old one; with no
        issuer configured the row's own issuer is the expectation. The audience is
        checked only when a client id is configured, since there is otherwise no
        audience to expect.
        """

        claims = _claims_without_verification(id_token)
        expected_issuer = self.settings.oidc_issuer or retained_issuer
        if claims.get("iss") != expected_issuer:
            return (
                EnterpriseAuthOutcome.REFUSED_ISSUER,
                "the retained identity assertion was issued by an unexpected issuer",
            )
        client_id = self.settings.oidc_client_id
        if client_id and client_id not in _audience_values(claims.get("aud")):
            return (
                EnterpriseAuthOutcome.REFUSED_AUDIENCE,
                "the retained identity assertion is not addressed to this client",
            )
        exp = claims.get("exp")
        if not isinstance(exp, int | float):
            return (
                EnterpriseAuthOutcome.REFUSED_EXPIRED,
                "the retained identity assertion carries no expiry",
            )
        if datetime.fromtimestamp(float(exp), tz=UTC) <= datetime.now(UTC):
            return (
                EnterpriseAuthOutcome.REFUSED_EXPIRED,
                "the retained identity assertion has expired",
            )
        return None

    def _validate_grant(
        self, grant_token: str, *, expected_issuer: str, expected_audience: str
    ) -> tuple[EnterpriseAuthOutcome, str] | None:
        """Local policy check on the ID-JAG, before the authorization server sees it.

        Fails closed on each axis: a claim that cannot be *shown* to match is a
        refusal, not a pass.
        """

        claims = _claims_without_verification(grant_token)
        if claims.get("iss") != expected_issuer:
            return (
                EnterpriseAuthOutcome.REFUSED_ISSUER,
                "the assertion grant was issued by an unexpected issuer",
            )
        if expected_audience not in _audience_values(claims.get("aud")):
            return (
                EnterpriseAuthOutcome.REFUSED_AUDIENCE,
                "the assertion grant does not carry the configured audience",
            )
        exp = claims.get("exp")
        if not isinstance(exp, int | float):
            return (
                EnterpriseAuthOutcome.REFUSED_EXPIRED,
                "the assertion grant carries no expiry",
            )
        if datetime.fromtimestamp(float(exp), tz=UTC) <= datetime.now(UTC):
            return (
                EnterpriseAuthOutcome.REFUSED_EXPIRED,
                "the assertion grant has expired",
            )
        return None

    async def _connection_for(
        self,
        connector: ConnectorDefinition,
        *,
        principal_id: str,
        workspace_id: str,
        mcp_server_id: str | None = None,
    ) -> Connection:
        """The principal's own connection for this connector, or a fresh one.

        USER scope only in v0.3: a workspace-shared enterprise connection needs the
        admin policy INV3-009 asks for, which is M8 work.

        ``REVOKED`` is operator-terminal here: an explicitly revoked connection is
        neither reused nor replaced by a fresh one, it is refused and the refusal is
        recorded. Minting a new connection instead would make revocation a no-op
        beyond one token lifetime, since enterprise acquisition needs no end-user
        step to run again (AC3-EMA-04). Re-enabling a revoked enterprise connection
        is therefore an operator action outside v0.3.
        """

        existing = await self.store.list_connections(connector_id=connector.connector_id)
        for connection in existing:
            if (
                connection.principal_id == principal_id
                and connection.workspace_id == workspace_id
                and connection.scope is ConnectionScope.USER
            ):
                if connection.status is ConnectionStatus.REVOKED:
                    detail = "the enterprise connection was revoked by an operator"
                    await self._record(
                        principal_id=principal_id,
                        workspace_id=workspace_id,
                        connector_id=connector.connector_id,
                        outcome=EnterpriseAuthOutcome.REFUSED_REVOKED,
                        connection_id=connection.connection_id,
                        mcp_server_id=mcp_server_id,
                        detail=detail,
                    )
                    raise EnterpriseAuthError(detail)
                return connection
        return Connection(
            connection_id=new_id("conn"),
            connector_id=connector.connector_id,
            workspace_id=workspace_id,
            principal_id=principal_id,
            scope=ConnectionScope.USER,
            status=ConnectionStatus.PENDING,
            workspace_shareable=False,
        )

    async def _record(
        self,
        *,
        principal_id: str,
        workspace_id: str,
        connector_id: str,
        outcome: EnterpriseAuthOutcome,
        detail: str,
        connection_id: str | None = None,
        mcp_server_id: str | None = None,
    ) -> EnterpriseAuthGrant:
        return await self.store.append_enterprise_auth_grant(
            EnterpriseAuthGrant(
                grant_id=new_id("enterprise_auth_grant"),
                principal_id=principal_id,
                workspace_id=workspace_id,
                connector_id=connector_id,
                mcp_server_id=mcp_server_id,
                connection_id=connection_id,
                outcome=outcome,
                detail=detail,
            )
        )


__all__ = [
    "EnterpriseAuthDisabled",
    "EnterpriseAuthError",
    "EnterpriseAuthManager",
    "IdentityAssertionClient",
    "JwtBearerClient",
    "token_endpoint_for",
]
