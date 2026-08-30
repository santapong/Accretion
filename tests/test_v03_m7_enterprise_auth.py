"""Enterprise-managed authorization: the manager and its clients (v0.3 M7, SDD §8).

Nothing is wired into identity or the MCP manager yet; this exercises the module
itself against a real in-process identity provider and a real in-process enterprise
authorization server, so the token exchange, the local policy check, and the
jwt-bearer grant are all genuinely performed.

Claims AC3-EMA-03: an identity assertion *or* an enterprise grant with a wrong
issuer, wrong audience, or expired lifetime is refused, and the refusal is recorded.
Both axes are exercised. Each case is well-formed and wrong on exactly one thing, so
the refusals are distinguishable rather than one blanket failure, and each is refused
before it can cost anything upstream: a bad identity assertion never reaches the
identity provider's exchange endpoint, and a bad grant never reaches the connector's
authorization server.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fake_enterprise_as import ISSUER as AS_ISSUER
from fake_enterprise_as import FakeEnterpriseAuthorizationServer
from fake_idp import CLIENT_ID, ISSUER, FakeIdp, FakeUser, jwks_document
from httpx import ASGITransport

from accretion.config import Settings
from accretion.contracts import (
    AssertionStatus,
    AuthSession,
    ConnectionScope,
    ConnectionStatus,
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
    EnterpriseAuthOutcome,
    IdentityAssertion,
    McpServerDefinition,
    TokenStatus,
)
from accretion.enterprise_auth import (
    EnterpriseAuthDisabled,
    EnterpriseAuthError,
    EnterpriseAuthManager,
    IdentityAssertionClient,
    JwtBearerClient,
    token_endpoint_for,
)
from accretion.identity import IdentityService, OidcClient, OidcProviderConfig
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.secrets_store import EnvelopeSecretStore, KeyProvider
from accretion.token_broker import EncryptedTokenBroker

CONNECTOR_ID = "conndef_enterprise_github"
AUDIENCE = "https://mcp.enterprise.test"
WORKSPACE_ID = "workspace_test"

#: Three long base64url runs separated by dots — the shape of any JWT, so a recorded
#: detail can be shown to be prose without depending on a particular token's prefix.
JWT_SHAPE = re.compile(r"[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}")


class StaticKeyProvider(KeyProvider):
    """A master key that lives only for the duration of one test."""

    def __init__(self) -> None:
        self._material = os.urandom(32)

    @property
    def key_id(self) -> str:
        return "test-1"

    def material(self) -> bytes:
        return self._material


def enterprise_connector() -> ConnectorDefinition:
    return ConnectorDefinition(
        connector_id=CONNECTOR_ID,
        name="Enterprise GitHub",
        kind=ConnectorKind.MCP,
        auth_type=ConnectorAuthType.EMA,
        authorization_server=AS_ISSUER,
        resource_server=AUDIENCE,
        default_scopes=["mcp:invoke"],
        connection_scope=ConnectionScope.USER,
    )


def enterprise_server(connector_id: str = CONNECTOR_ID) -> McpServerDefinition:
    return McpServerDefinition(
        mcp_server_id=new_id("mcp_server"),
        workspace_id=WORKSPACE_ID,
        connector_id=connector_id,
        name="enterprise-github",
        endpoint="https://mcp.enterprise.test/mcp",
        owner_principal_id="prin_admin",
        enabled=True,
    )


class Fixture:
    """Everything one enterprise-auth scenario needs, all of it real."""

    def __init__(
        self,
        *,
        manager: EnterpriseAuthManager,
        store: MemoryStore,
        idp: FakeIdp,
        authorization_server: FakeEnterpriseAuthorizationServer,
        identity: IdentityService,
        connector: ConnectorDefinition,
        server: McpServerDefinition,
    ) -> None:
        self.manager = manager
        self.store = store
        self.idp = idp
        self.authorization_server = authorization_server
        self.identity = identity
        self.connector = connector
        self.server = server
        self.last_id_token: str = ""

    async def sign_in(self, subject: str = "alice") -> tuple[str, str]:
        """Run the real OIDC code flow and retain the assertion it produces."""

        url = await self.identity.begin_login()
        query = parse_qs(urlparse(url).query)
        nonce = query["nonce"][0]
        code = self.idp.issue_code(FakeUser(subject, email=f"{subject}@test"), nonce)
        oidc = self.identity.oidc
        assert oidc is not None
        id_token = await oidc.exchange_code(
            code=code, code_verifier=self._verifier(query["state"][0])
        )
        claims = await oidc.validate_id_token(id_token, nonce=nonce)
        principal, session = await self.identity_principal(claims.subject)
        await self.manager.retain_assertion(id_token, session=session, claims=claims)
        #: The plaintext this sign-in produced, so a test can search for it verbatim.
        self.last_id_token = id_token
        return principal, session.auth_session_id

    async def retain_directly(
        self,
        id_token: str,
        *,
        subject: str = "alice",
        expires_at: datetime | None = None,
    ) -> tuple[str, str]:
        """Retain an assertion the real code flow would never have produced.

        The sealing and the row are written exactly as ``retain_assertion`` writes
        them; only the token differs. ``expires_at`` is stated independently of the
        token so the row's own expiry cannot stand in for reading the token's
        claims — an expired token under a live row must still be refused.
        """

        principal_id, session = await self.identity_principal(subject)
        record = await self.manager.secrets.seal(
            id_token, associated_id=session.auth_session_id
        )
        await self.store.upsert_secret_record(record)
        await self.store.upsert_identity_assertion(
            IdentityAssertion(
                assertion_id=new_id("identity_assertion"),
                auth_session_id=session.auth_session_id,
                principal_id=principal_id,
                issuer=ISSUER,
                subject=subject,
                secret_store_key=record.secret_store_key,
                expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
            )
        )
        self.last_id_token = id_token
        return principal_id, session.auth_session_id

    def _verifier(self, state: str) -> str:
        for transaction in self.store.auth_transactions.values():
            if transaction.state == state:
                return transaction.code_verifier
        raise AssertionError("no auth transaction was created for the login")

    async def identity_principal(self, subject: str) -> tuple[str, AuthSession]:
        principal_id = f"prin_{subject}"
        session = AuthSession(
            auth_session_id=new_id("auth_session"),
            principal_id=principal_id,
            expires_at=datetime.now(UTC) + timedelta(hours=8),
        )
        return principal_id, await self.store.create_auth_session(session)


async def setup_enterprise_auth(
    *,
    enabled: bool = True,
    token_exchange_url: str | None = None,
    audiences: dict[str, str] | None = None,
    jag_issuer: str | None = None,
    jag_audience: str | None = None,
    jag_lifetime: int = 300,
) -> Fixture:
    idp = FakeIdp(
        jag_issuer=jag_issuer, jag_audience=jag_audience, jag_lifetime=jag_lifetime
    )
    idp_http = httpx.AsyncClient(
        transport=ASGITransport(app=idp.app()), base_url=ISSUER
    )
    authorization_server = FakeEnterpriseAuthorizationServer(
        jwks=jwks_document(), expected_issuer=ISSUER, expected_audience=AUDIENCE
    )
    as_http = httpx.AsyncClient(
        transport=ASGITransport(app=authorization_server.app()), base_url=AS_ISSUER
    )
    store = MemoryStore()
    settings = Settings(
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        enable_enterprise_auth=enabled,
        enterprise_auth_token_exchange_url=(
            f"{ISSUER}/token-exchange" if token_exchange_url is None else token_exchange_url
        ),
        enterprise_auth_audiences=(
            {CONNECTOR_ID: AUDIENCE} if audiences is None else audiences
        ),
    )
    connector = enterprise_connector()
    await store.upsert_connector_definition(connector)
    server = enterprise_server()
    secrets_store = EnvelopeSecretStore(StaticKeyProvider())
    manager = EnterpriseAuthManager(
        store,
        secrets_store,
        EncryptedTokenBroker(store, secrets_store),
        settings,
        IdentityAssertionClient(settings=settings, http=idp_http),
        JwtBearerClient(http=as_http),
    )
    identity = IdentityService(
        store,
        OidcClient(
            config=OidcProviderConfig(issuer=ISSUER, client_id=CLIENT_ID),
            http=httpx.AsyncClient(
                transport=ASGITransport(app=idp.app()), base_url=ISSUER
            ),
        ),
    )
    return Fixture(
        manager=manager,
        store=store,
        idp=idp,
        authorization_server=authorization_server,
        identity=identity,
        connector=connector,
        server=server,
    )


# ------------------------------------------------------------------ retention


async def test_signing_in_retains_nothing_while_the_feature_flag_is_down() -> None:
    fixture = await setup_enterprise_auth(enabled=False)

    principal_id, session_id = await fixture.sign_in()

    assert await fixture.store.get_identity_assertion_for_session(session_id) is None
    assert await fixture.store.get_identity_assertion_for_principal(principal_id) is None
    assert fixture.idp.exchange_calls == 0


async def test_a_retained_assertion_stores_ciphertext_and_expires_with_its_token() -> None:
    fixture = await setup_enterprise_auth()

    principal_id, session_id = await fixture.sign_in()

    assertion = await fixture.store.get_identity_assertion_for_session(session_id)
    assert assertion is not None
    assert assertion.principal_id == principal_id
    assert assertion.issuer == ISSUER
    assert assertion.status is AssertionStatus.ACTIVE
    # The fake provider's id_token lives 300 seconds; the row must not outlive it.
    assert assertion.expires_at <= datetime.now(UTC) + timedelta(seconds=300)
    assert "id_token" not in assertion.model_dump_json()
    record = await fixture.store.get_secret_record(assertion.secret_store_key)
    assert record is not None
    # The plaintext itself must be absent, whole and in parts. A JWT-prefix probe
    # would be a coin flip: the ciphertext is base64 of random bytes, so any three
    # given characters turn up in it every few hundred runs.
    id_token = fixture.last_id_token
    assert id_token not in record.ciphertext
    for segment in id_token.split("."):
        assert segment not in record.ciphertext


async def test_revoking_a_session_destroys_the_secret_and_keeps_the_row_as_evidence() -> None:
    fixture = await setup_enterprise_auth()
    principal_id, session_id = await fixture.sign_in()
    original = await fixture.store.get_identity_assertion_for_session(session_id)
    assert original is not None

    await fixture.manager.revoke_for_session(session_id)

    stored = await fixture.store.get_identity_assertion_for_session(session_id)
    assert stored is not None
    assert stored.status is AssertionStatus.REVOKED
    assert await fixture.store.get_secret_record(original.secret_store_key) is None
    assert await fixture.store.get_identity_assertion_for_principal(principal_id) is None
    grants = await fixture.store.list_enterprise_auth_grants(principal_id=principal_id)
    assert [grant.outcome for grant in grants] == [EnterpriseAuthOutcome.REVOKED]


# ------------------------------------------------------------------ acquisition


async def test_a_signed_in_principal_reaches_the_server_with_no_further_authorization() -> (
    None
):
    fixture = await setup_enterprise_auth()
    principal_id, _ = await fixture.sign_in()

    connection = await fixture.manager.ensure_access(
        fixture.connector,
        fixture.server,
        principal_id=principal_id,
        workspace_id=WORKSPACE_ID,
    )

    stored = await fixture.store.get_connection(connection.connection_id)
    assert stored is not None
    assert stored.status is ConnectionStatus.ACTIVE
    assert stored.principal_id == principal_id
    assert stored.granted_scopes == ["mcp:invoke"]
    assert stored.token_handle_ref is not None
    handle = await fixture.store.get_token_handle(stored.token_handle_ref)
    assert handle is not None
    assert handle.principal_id == principal_id
    assert fixture.idp.exchange_calls == 1
    assert fixture.authorization_server.grant_calls == 1
    grants = await fixture.store.list_enterprise_auth_grants(principal_id=principal_id)
    assert [grant.outcome for grant in grants] == [EnterpriseAuthOutcome.GRANTED]
    assert grants[0].mcp_server_id == fixture.server.mcp_server_id
    assert grants[0].connection_id == stored.connection_id


async def test_the_granted_scopes_come_from_the_authorization_server_not_the_request() -> (
    None
):
    fixture = await setup_enterprise_auth()
    fixture.authorization_server.downgrade_scopes_to = []
    principal_id, _ = await fixture.sign_in()

    connection = await fixture.manager.ensure_access(
        fixture.connector,
        fixture.server,
        principal_id=principal_id,
        workspace_id=WORKSPACE_ID,
    )

    stored = await fixture.store.get_connection(connection.connection_id)
    assert stored is not None
    assert stored.granted_scopes == []


async def test_acquisition_with_the_flag_down_is_refused_before_any_exchange() -> None:
    fixture = await setup_enterprise_auth(enabled=False)
    principal_id, _ = await fixture.identity_principal("alice")

    with pytest.raises(EnterpriseAuthDisabled):
        await fixture.manager.ensure_access(
            fixture.connector,
            fixture.server,
            principal_id=principal_id,
            workspace_id=WORKSPACE_ID,
        )

    assert fixture.idp.exchange_calls == 0
    assert fixture.authorization_server.grant_calls == 0
    grants = await fixture.store.list_enterprise_auth_grants(principal_id=principal_id)
    assert [grant.outcome for grant in grants] == [EnterpriseAuthOutcome.REFUSED_DISABLED]


async def test_an_empty_exchange_endpoint_leaves_the_subsystem_inert() -> None:
    """The flag alone cannot open egress: the endpoint is the second gate."""

    fixture = await setup_enterprise_auth(token_exchange_url="")
    principal_id, _ = await fixture.sign_in()

    with pytest.raises(EnterpriseAuthDisabled):
        await fixture.manager.ensure_access(
            fixture.connector,
            fixture.server,
            principal_id=principal_id,
            workspace_id=WORKSPACE_ID,
        )

    assert fixture.idp.exchange_calls == 0


async def test_a_principal_with_no_retained_assertion_is_refused() -> None:
    fixture = await setup_enterprise_auth()
    principal_id, _ = await fixture.identity_principal("alice")

    with pytest.raises(EnterpriseAuthError):
        await fixture.manager.ensure_access(
            fixture.connector,
            fixture.server,
            principal_id=principal_id,
            workspace_id=WORKSPACE_ID,
        )

    assert fixture.idp.exchange_calls == 0
    grants = await fixture.store.list_enterprise_auth_grants(principal_id=principal_id)
    # Nothing was ever retained, so the recorded reason says exactly that: an
    # expiry that never happened would be a false audit trail.
    assert [grant.outcome for grant in grants] == [EnterpriseAuthOutcome.REFUSED_MISSING]


async def test_a_connector_with_no_configured_audience_cannot_be_authorized() -> None:
    fixture = await setup_enterprise_auth(audiences={})
    principal_id, _ = await fixture.sign_in()

    with pytest.raises(EnterpriseAuthError):
        await fixture.manager.ensure_access(
            fixture.connector,
            fixture.server,
            principal_id=principal_id,
            workspace_id=WORKSPACE_ID,
        )

    assert fixture.idp.exchange_calls == 0
    grants = await fixture.store.list_enterprise_auth_grants(principal_id=principal_id)
    assert [grant.outcome for grant in grants] == [EnterpriseAuthOutcome.REFUSED_AUDIENCE]


def test_the_token_endpoint_hangs_off_the_connectors_authorization_server() -> None:
    assert token_endpoint_for(enterprise_connector()) == f"{AS_ISSUER}/token"
    with pytest.raises(EnterpriseAuthError):
        token_endpoint_for(
            ConnectorDefinition(
                connector_id="conndef_nowhere", name="Nowhere", kind=ConnectorKind.MCP
            )
        )


# --------------------------------------------------------------- AC3-EMA-03


@pytest.mark.acceptance("AC3-EMA-03")
async def test_a_wrong_issuer_audience_or_expired_grant_is_refused_and_recorded() -> None:
    """Both axes of the criterion: the enterprise grant, and the identity assertion.

    Three identity-provider knobs mint an assertion grant that is well-formed and
    wrong on exactly one axis; a fourth case expires the *retained identity
    assertion* itself. Each case runs the whole real path on its own fresh fixture
    and asserts the ``outcome`` read back from the store, because a blanket "it
    raised" would pass even if the manager could not tell the axes apart. Where the
    refusal is local, the authorization server must still be at zero calls.

    Nothing here reads the wall clock for a boundary decision: the expired grant is
    an hour stale and the expired assertion is stamped an hour in the past, so no
    scheduling delay can turn a refusal into a pass or the reverse.
    """

    # ---- Axis one: the retained identity assertion itself.
    #
    # These tokens are minted directly rather than through the code flow, because
    # ``OidcClient`` would refuse a wrong issuer or audience at sign-in and there
    # would be nothing retained to refuse later. The row's ``expires_at`` is left an
    # hour in the future in every case, including the expired one, so a pass here
    # can only come from reading the token's own claims.
    assertion_cases: list[tuple[dict[str, object], EnterpriseAuthOutcome]] = [
        ({"issuer": "https://attacker.test"}, EnterpriseAuthOutcome.REFUSED_ISSUER),
        ({"audience": "some-other-client"}, EnterpriseAuthOutcome.REFUSED_AUDIENCE),
        ({"lifetime": -3600}, EnterpriseAuthOutcome.REFUSED_EXPIRED),
    ]
    assertion_observed: list[EnterpriseAuthOutcome] = []

    for mint, expected in assertion_cases:
        fixture = await setup_enterprise_auth()
        id_token = fixture.idp.mint_id_token("alice", **mint)  # type: ignore[arg-type]
        principal_id, session_id = await fixture.retain_directly(id_token)
        retained = await fixture.store.get_identity_assertion_for_session(session_id)
        assert retained is not None
        assert retained.status is AssertionStatus.ACTIVE, mint
        assert retained.expires_at > datetime.now(UTC), mint

        with pytest.raises(EnterpriseAuthError):
            await fixture.manager.ensure_access(
                fixture.connector,
                fixture.server,
                principal_id=principal_id,
                workspace_id=WORKSPACE_ID,
            )

        grants = await fixture.store.list_enterprise_auth_grants(
            principal_id=principal_id, connector_id=CONNECTOR_ID
        )
        assert [grant.outcome for grant in grants] == [expected], mint
        # Refused locally, before the assertion travelled anywhere: the identity
        # provider would have refused these too, and that refusal would be its
        # record rather than ours.
        assert fixture.idp.exchange_calls == 0, mint
        assert fixture.authorization_server.grant_calls == 0, mint
        assert await fixture.store.list_connections(connector_id=CONNECTOR_ID) == []
        assert fixture.store.token_handles == {}
        assert JWT_SHAPE.search(grants[0].detail) is None, mint
        assertion_observed.append(grants[0].outcome)

    assert len(set(assertion_observed)) == 3

    # ---- Axis two: the enterprise grant the exchange returns.
    cases = [
        (
            {"jag_issuer": "https://attacker.test"},
            EnterpriseAuthOutcome.REFUSED_ISSUER,
        ),
        (
            {"jag_audience": "https://other-service.test"},
            EnterpriseAuthOutcome.REFUSED_AUDIENCE,
        ),
        (
            {"jag_lifetime": -3600},
            EnterpriseAuthOutcome.REFUSED_EXPIRED,
        ),
    ]
    observed: list[EnterpriseAuthOutcome] = []

    for knob, expected in cases:
        fixture = await setup_enterprise_auth(**knob)  # type: ignore[arg-type]
        principal_id, session_id = await fixture.sign_in()
        retained = await fixture.store.get_identity_assertion_for_session(session_id)
        assert retained is not None
        assert retained.status is AssertionStatus.ACTIVE, knob

        with pytest.raises(EnterpriseAuthError):
            await fixture.manager.ensure_access(
                fixture.connector,
                fixture.server,
                principal_id=principal_id,
                workspace_id=WORKSPACE_ID,
            )

        grants = await fixture.store.list_enterprise_auth_grants(
            principal_id=principal_id, connector_id=CONNECTOR_ID
        )
        assert [grant.outcome for grant in grants] == [expected], knob
        # The refusal is local: the authorization server was never asked.
        assert fixture.authorization_server.grant_calls == 0, knob
        assert fixture.idp.exchange_calls == 1, knob
        # No connection and no token handle survive a refusal.
        assert await fixture.store.list_connections(connector_id=CONNECTOR_ID) == []
        assert fixture.store.token_handles == {}
        # The refusal record is prose for an operator, never the grant itself.
        assert JWT_SHAPE.search(grants[0].detail) is None, knob
        observed.append(grants[0].outcome)

    assert len(set(observed)) == 3

    # The other half of the criterion: the retained identity assertion has expired.
    fixture = await setup_enterprise_auth()
    principal_id, session_id = await fixture.sign_in()
    retained = await fixture.store.get_identity_assertion_for_session(session_id)
    assert retained is not None
    await fixture.store.upsert_identity_assertion(
        retained.model_copy(
            update={"expires_at": datetime.now(UTC) - timedelta(hours=1)}
        )
    )

    with pytest.raises(EnterpriseAuthError):
        await fixture.manager.ensure_access(
            fixture.connector,
            fixture.server,
            principal_id=principal_id,
            workspace_id=WORKSPACE_ID,
        )

    stale = await fixture.store.get_identity_assertion_for_session(session_id)
    assert stale is not None
    assert stale.status is AssertionStatus.EXPIRED
    grants = await fixture.store.list_enterprise_auth_grants(
        principal_id=principal_id, connector_id=CONNECTOR_ID
    )
    assert [grant.outcome for grant in grants] == [EnterpriseAuthOutcome.REFUSED_EXPIRED]
    assert JWT_SHAPE.search(grants[0].detail) is None
    # Refused before anything travelled: no exchange, and no call to the AS.
    assert fixture.idp.exchange_calls == 0
    assert fixture.authorization_server.grant_calls == 0
    assert await fixture.store.list_connections(connector_id=CONNECTOR_ID) == []


async def test_an_authorization_server_failure_is_not_recorded_as_an_issuer_refusal() -> (
    None
):
    """An upstream outage and an attacker-issued grant must not look alike.

    ``REFUSED_ISSUER`` means the operator's issuer policy rejected the grant here.
    When the connector's authorization server refuses or cannot be reached, the only
    truthful record is that the upstream did not return material.
    """

    fixture = await setup_enterprise_auth()
    fixture.authorization_server.should_reject = True
    principal_id, _ = await fixture.sign_in()

    with pytest.raises(EnterpriseAuthError):
        await fixture.manager.ensure_access(
            fixture.connector,
            fixture.server,
            principal_id=principal_id,
            workspace_id=WORKSPACE_ID,
        )

    grants = await fixture.store.list_enterprise_auth_grants(principal_id=principal_id)
    assert [grant.outcome for grant in grants] == [EnterpriseAuthOutcome.REFUSED_UPSTREAM]
    assert JWT_SHAPE.search(grants[0].detail) is None
    # The grant did travel: this refusal is the upstream's, not the policy check's.
    assert fixture.authorization_server.grant_calls == 1


async def test_re_acquiring_leaves_exactly_one_live_handle_on_the_connection() -> None:
    """A second acquisition must not orphan the first handle.

    ``store_authorization`` mints a fresh handle every call while the connection is
    reused, so without supersession the earlier handle would stay ACTIVE holding
    sealed enterprise material that revoking the connection would never reach.
    """

    fixture = await setup_enterprise_auth()
    principal_id, _ = await fixture.sign_in()
    first = await fixture.manager.ensure_access(
        fixture.connector,
        fixture.server,
        principal_id=principal_id,
        workspace_id=WORKSPACE_ID,
    )
    assert first.token_handle_ref is not None
    superseded = await fixture.store.get_token_handle(first.token_handle_ref)
    assert superseded is not None

    second = await fixture.manager.ensure_access(
        fixture.connector,
        fixture.server,
        principal_id=principal_id,
        workspace_id=WORKSPACE_ID,
    )

    assert second.connection_id == first.connection_id
    assert second.token_handle_ref != first.token_handle_ref
    stale = await fixture.store.get_token_handle(first.token_handle_ref)
    assert stale is not None
    assert stale.status is TokenStatus.REVOKED
    # Its sealed access material is gone, not merely marked.
    assert await fixture.store.get_secret_record(superseded.secret_store_key) is None
    live = [
        handle
        for handle in fixture.store.token_handles.values()
        if handle.status is TokenStatus.ACTIVE
    ]
    assert [handle.token_handle_id for handle in live] == [second.token_handle_ref]


async def test_a_token_exchange_failure_is_recorded_before_it_propagates() -> None:
    """Every exit path writes exactly one grant row — including this one.

    The exchange sits between the store read and the authorization server, so a
    provider that is unreachable, rejects the subject token, or answers with the
    wrong material would otherwise leave no evidence at all that an enterprise
    authorization was attempted and refused.
    """

    fixture = await setup_enterprise_auth()
    fixture.idp.exchange_should_reject = True
    principal_id, _ = await fixture.sign_in()

    with pytest.raises(EnterpriseAuthError):
        await fixture.manager.ensure_access(
            fixture.connector,
            fixture.server,
            principal_id=principal_id,
            workspace_id=WORKSPACE_ID,
        )

    grants = await fixture.store.list_enterprise_auth_grants(
        principal_id=principal_id, connector_id=CONNECTOR_ID
    )
    assert [grant.outcome for grant in grants] == [EnterpriseAuthOutcome.REFUSED_UPSTREAM]
    assert JWT_SHAPE.search(grants[0].detail) is None
    assert fixture.idp.exchange_calls == 1
    # Nothing was minted, so the connector's authorization server was never asked.
    assert fixture.authorization_server.grant_calls == 0
    assert await fixture.store.list_connections(connector_id=CONNECTOR_ID) == []
    assert fixture.store.token_handles == {}


async def test_a_connector_naming_no_authorization_server_is_not_an_upstream_outage() -> (
    None
):
    """A local misconfiguration must not be written down as an upstream fault.

    The grant table is append-only, so a mislabelled row can never be corrected;
    ``REFUSED_UPSTREAM`` therefore has to mean an actual upstream interaction.
    """

    misconfigured = ConnectorDefinition(
        connector_id="conndef_nowhere",
        name="Nowhere",
        kind=ConnectorKind.MCP,
        auth_type=ConnectorAuthType.EMA,
        resource_server=AUDIENCE,
        connection_scope=ConnectionScope.USER,
    )
    fixture = await setup_enterprise_auth(
        audiences={CONNECTOR_ID: AUDIENCE, misconfigured.connector_id: AUDIENCE}
    )
    await fixture.store.upsert_connector_definition(misconfigured)
    principal_id, _ = await fixture.sign_in()

    with pytest.raises(EnterpriseAuthError):
        await fixture.manager.ensure_access(
            misconfigured,
            enterprise_server(misconfigured.connector_id),
            principal_id=principal_id,
            workspace_id=WORKSPACE_ID,
        )

    grants = await fixture.store.list_enterprise_auth_grants(
        principal_id=principal_id, connector_id=misconfigured.connector_id
    )
    assert [grant.outcome for grant in grants] == [
        EnterpriseAuthOutcome.REFUSED_CONFIGURATION
    ]
    assert JWT_SHAPE.search(grants[0].detail) is None
    # Refused locally: neither the identity provider nor an authorization server
    # was involved, which is exactly what makes the label truthful.
    assert fixture.idp.exchange_calls == 0
    assert fixture.authorization_server.grant_calls == 0


async def test_a_revoked_connection_is_not_resurrected_by_enterprise_acquisition() -> None:
    """Revocation has to outlive one token lifetime.

    Enterprise acquisition needs no end-user step, so if it reused the revoked row
    and wrote ``ACTIVE`` back onto it, the very next invocation would undo the
    operator's decision.
    """

    fixture = await setup_enterprise_auth()
    principal_id, _ = await fixture.sign_in()
    granted = await fixture.manager.ensure_access(
        fixture.connector,
        fixture.server,
        principal_id=principal_id,
        workspace_id=WORKSPACE_ID,
    )
    await fixture.store.upsert_connection(
        granted.model_copy(
            update={"status": ConnectionStatus.REVOKED, "granted_scopes": []}
        )
    )

    with pytest.raises(EnterpriseAuthError):
        await fixture.manager.ensure_access(
            fixture.connector,
            fixture.server,
            principal_id=principal_id,
            workspace_id=WORKSPACE_ID,
        )

    connections = await fixture.store.list_connections(connector_id=CONNECTOR_ID)
    # Neither resurrected nor quietly replaced by a second ACTIVE one.
    assert [connection.connection_id for connection in connections] == [
        granted.connection_id
    ]
    assert connections[0].status is ConnectionStatus.REVOKED
    active = [
        connection
        for connection in connections
        if connection.status is ConnectionStatus.ACTIVE
    ]
    assert active == []
    grants = await fixture.store.list_enterprise_auth_grants(
        principal_id=principal_id, connector_id=CONNECTOR_ID
    )
    assert [grant.outcome for grant in grants] == [
        EnterpriseAuthOutcome.GRANTED,
        EnterpriseAuthOutcome.REFUSED_REVOKED,
    ]
    assert grants[-1].connection_id == granted.connection_id
    # Refused before anything travelled: the counters are still the first grant's.
    assert fixture.idp.exchange_calls == 1
    assert fixture.authorization_server.grant_calls == 1


async def test_a_token_this_module_cannot_bound_still_leaves_a_usable_session() -> None:
    """An optional subsystem may not break the mandatory login path.

    The provider mints an ``exp`` that is a numeric string: PyJWT accepts it, so
    sign-in is genuinely successful and verified, while this module cannot bound the
    retention. It must keep nothing and record that, not fail the login.
    """

    fixture = await setup_enterprise_auth()
    fixture.idp.exp_as_string = True

    principal_id, session_id = await fixture.sign_in()

    session = await fixture.store.get_auth_session(session_id)
    assert session is not None
    assert session.principal_id == principal_id
    assert session.expires_at > datetime.now(UTC)
    # Nothing retained, and no sealed material left behind either.
    assert await fixture.store.get_identity_assertion_for_session(session_id) is None
    assert fixture.store.secret_records == {}
    grants = await fixture.store.list_enterprise_auth_grants(principal_id=principal_id)
    assert [grant.outcome for grant in grants] == [
        EnterpriseAuthOutcome.REFUSED_CONFIGURATION
    ]
    assert JWT_SHAPE.search(grants[0].detail) is None

    # Acquisition then degrades to the truthful reason: nothing was ever retained.
    with pytest.raises(EnterpriseAuthError):
        await fixture.manager.ensure_access(
            fixture.connector,
            fixture.server,
            principal_id=principal_id,
            workspace_id=WORKSPACE_ID,
        )

    grants = await fixture.store.list_enterprise_auth_grants(
        principal_id=principal_id, connector_id=CONNECTOR_ID
    )
    assert [grant.outcome for grant in grants] == [EnterpriseAuthOutcome.REFUSED_MISSING]
    assert fixture.idp.exchange_calls == 0
