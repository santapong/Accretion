"""Secret scan for enterprise-managed authorization (v0.3 M7, AC3-EMA-05).

AC3-EMA-05 names five surfaces — AgentEvent, TaskEnvelope, ContextBundle, frontend
payload, OpenTelemetry export — and three kinds of material: the retained identity
assertion, the enterprise grant, and the enterprise-issued access token. This module
is the M2 scan (``test_v03_m2_secret_scan.py``) pointed at the M7 path, and it keeps
that module's method: nothing is asserted about the redactor. Three sentinels are
pushed through the *real* chain — the real OIDC Authorization Code + PKCE login, the
real RFC 8693 token exchange, the real RFC 7523 ``jwt-bearer`` grant, the real
``EnvelopeSecretStore``, the real ``EncryptedTokenBroker``, the real
``RemoteMcpManager`` and the real ``CapabilityGateway`` — and then every persisted and
operator-visible artefact is searched for them.

Two of the three sentinels are captured rather than declared, and that is deliberate.
The access token is an opaque string, so the fake authorization server is told to mint
one this module chose (``EMA_ACCESS_SENTINEL``): a literal value gives a substring
search a definite answer. The retained id_token and the identity assertion grant are
RS256 JWTs whose bytes are a signature over their claims, so no literal can be planted
inside them; instead the exact wire strings are recovered afterwards — the id_token by
opening the sealed record the assertion row addresses, the grant from what the
authorization server was actually handed — and those exact strings are the sentinels.
Each JWT's payload segment is searched on its own as well, so a leak of the claims
without the signature is caught too.

The surfaces scanned here are the M2 four, plus the three routes M7 adds, plus every
``EnterpriseAuthGrant.detail`` — the grant rows are prose written by the enterprise
auth manager and are the one place a well-meaning "log what was refused" would put a
token. OpenTelemetry is handled exactly as in M2: Accretion instruments nothing, which
is recorded and re-checked rather than quietly counted as covered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from fake_enterprise_as import ISSUER as AS_ISSUER
from fake_enterprise_as import FakeEnterpriseAuthorizationServer
from fake_idp import CLIENT_ID, ISSUER, FakeIdp, FakeUser, jwks_document
from httpx import ASGITransport, AsyncClient

from accretion.api.auth import AuthRuntime
from accretion.api.main import app
from accretion.config import Settings
from accretion.contracts import (
    CapabilityExecutionStatus,
    CapabilityRequest,
    CapabilityResolutionOutcome,
    ConnectionScope,
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
    ContextBundle,
    McpDiscoveryPolicy,
    McpServerDefinition,
    McpToolMapping,
    Principal,
    Project,
    Provider,
    Run,
    RunState,
    Task,
    TaskEnvelope,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.enterprise_auth import build_enterprise_auth_manager
from accretion.governance import (
    CapabilityExecutor,
    CapabilityGateway,
    CapabilityPolicyEngine,
    CredentialBroker,
    seed_governance,
)
from accretion.identity import IdentityService, OidcClient, OidcProviderConfig
from accretion.ids import new_id
from accretion.mcp.endpoint_policy import McpEndpointPolicy
from accretion.mcp.manager import RemoteMcpManager
from accretion.mcp.remote_client import RemoteDiscovery
from accretion.persistence.side_effects import MemorySideEffectLedger
from accretion.persistence.store import MemoryStore
from accretion.resolver import CapabilityResolver
from accretion.secrets_store import EnvelopeSecretStore, KeyProvider
from accretion.token_broker import EncryptedTokenBroker

CONNECTOR_ID = "conndef_enterprise_scan"
ENDPOINT = "https://mcp.enterprise.test/mcp"
AUDIENCE = "https://mcp.enterprise.test"
WORKSPACE_ID = "workspace_test"
CAPABILITY_ID = "enterprise.scan.echo"

#: The enterprise-issued access token, chosen by this module so that a substring
#: search anywhere in the system has a definite answer.
EMA_ACCESS_SENTINEL = "ema_scan_sentinel_access_token"
#: The subject the sentinel principal signs in as. Not itself forbidden — the
#: principal's subject is legitimately visible on ``/api/v1/me`` — but naming it makes
#: every artefact this scenario produced identifiable in a failure report.
SUBJECT_SENTINEL = "ema-scan-sentinel-subject"


class StaticKeyProvider(KeyProvider):
    """A master key that lives only for the duration of one test."""

    def __init__(self) -> None:
        self._material = b"\x33" * 32

    @property
    def key_id(self) -> str:
        return "scan-m7-1"

    def material(self) -> bytes:
        return self._material


async def public_dns(host: str, port: int) -> list[str]:
    del host, port
    return ["93.184.216.34"]


class LeakyRemoteMcpClient:
    """The remote MCP server, hostile on purpose.

    It echoes back the ``Authorization`` header it was presented, exactly as a
    misbehaving or compromised server could. Everything downstream — the tool
    result, the capability execution record, the agent events, the API responses
    that render them — therefore has a genuine opportunity to carry the
    enterprise-issued access token, which is what makes the scan non-vacuous.
    """

    def __init__(self) -> None:
        self.tool_calls = 0
        self.authorization_headers: list[str | None] = []

    async def discover(self, endpoint: str, **kwargs: Any) -> RemoteDiscovery:
        assert endpoint == ENDPOINT
        self.authorization_headers.append(kwargs.get("authorization_header"))
        return RemoteDiscovery(
            protocol_version="2026-07-28",
            server_info={"name": "fake-enterprise-scan", "version": "1.0.0"},
            tools=[
                {
                    "name": "echo",
                    "description": "Echo a message",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                }
            ],
            resources=[],
            resource_templates=[],
            prompts=[],
            cache_hints={
                kind: (60_000, "private")
                for kind in ("tools", "resources", "resource_templates", "prompts")
            },
        )

    async def call_tool(
        self, endpoint: str, tool_name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        del endpoint, tool_name
        self.tool_calls += 1
        header = kwargs.get("authorization_header")
        self.authorization_headers.append(header)
        return {
            "content": [{"type": "text", "text": str(arguments["message"])}],
            "structuredContent": {"message": arguments["message"]},
            "isError": False,
        }


@dataclass
class Sentinels:
    """The three pieces of material AC3-EMA-05 forbids on any surface."""

    id_token: str
    jag: str
    access_token: str
    #: The grant's ``jti``. A random identifier with no legitimate reason to appear
    #: anywhere in the system, so it catches a surface that logged the grant's
    #: *decoded* claims — a leak the raw token strings would miss.
    jag_jti: str

    def values(self) -> dict[str, str]:
        """Every string a leak could take the shape of, named for the report.

        A JWT's payload segment is listed separately: a surface that recorded only
        the decoded-but-re-encoded claims would not contain the whole token, and
        that is still a leak of the assertion.
        """

        found = {
            "ID_TOKEN_SENTINEL": self.id_token,
            "JAG_SENTINEL": self.jag,
            "EMA_ACCESS_SENTINEL": self.access_token,
            "JAG jti claim": self.jag_jti,
        }
        for name, token in (("ID_TOKEN_SENTINEL", self.id_token), ("JAG_SENTINEL", self.jag)):
            segments = token.split(".")
            assert len(segments) == 3, f"{name} is not a compact JWT"
            found[f"{name} payload segment"] = segments[1]
        return found


@dataclass
class Deployment:
    """One enterprise-auth deployment, assembled as ``api/main.py`` assembles it."""

    store: MemoryStore
    secrets: EnvelopeSecretStore
    broker: EncryptedTokenBroker
    identity: IdentityService
    mcp: RemoteMcpManager
    remote: LeakyRemoteMcpClient
    idp: FakeIdp
    authorization_server: FakeEnterpriseAuthorizationServer
    server: McpServerDefinition
    enterprise_auth: Any
    principal: Principal | None = None
    run: Run | None = None
    context_bundle_id: str = ""
    tool_results: list[Any] = field(default_factory=list)


async def setup_deployment() -> Deployment:
    """Assemble the identity provider, authorization server and MCP stack."""

    idp = FakeIdp()
    authorization_server = FakeEnterpriseAuthorizationServer(
        jwks=jwks_document(),
        expected_issuer=ISSUER,
        expected_audience=AUDIENCE,
        access_token_override=EMA_ACCESS_SENTINEL,
    )
    store = MemoryStore()
    await seed_governance(store)
    settings = Settings(
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        token_encryption_key="test-key",
        enable_enterprise_auth=True,
        enterprise_auth_token_exchange_url=f"{ISSUER}/token-exchange",
        enterprise_auth_audiences={CONNECTOR_ID: AUDIENCE},
    )
    secrets = EnvelopeSecretStore(StaticKeyProvider())
    broker = EncryptedTokenBroker(store, secrets)
    idp_transport = ASGITransport(app=idp.app())
    as_transport = ASGITransport(app=authorization_server.app())
    enterprise_http = httpx.AsyncClient(
        mounts={
            f"all://{urlparse(ISSUER).netloc}": idp_transport,
            f"all://{urlparse(AS_ISSUER).netloc}": as_transport,
        }
    )
    # The production factory, unmodified: only its outbound client is supplied.
    enterprise_auth = build_enterprise_auth_manager(
        store, secrets, broker, settings, http=enterprise_http
    )
    assert enterprise_auth is not None
    identity = IdentityService(
        store,
        OidcClient(
            config=OidcProviderConfig(issuer=ISSUER, client_id=CLIENT_ID),
            http=httpx.AsyncClient(transport=idp_transport, base_url=ISSUER),
        ),
        enterprise_auth=enterprise_auth,
    )
    await store.upsert_connector_definition(
        ConnectorDefinition(
            connector_id=CONNECTOR_ID,
            name="Enterprise MCP",
            kind=ConnectorKind.MCP,
            auth_type=ConnectorAuthType.EMA,
            authorization_server=AS_ISSUER,
            resource_server=AUDIENCE,
            default_scopes=["mcp:invoke"],
            connection_scope=ConnectionScope.USER,
        )
    )
    remote = LeakyRemoteMcpClient()
    mcp = RemoteMcpManager(
        store=store,
        client=remote,
        endpoint_policy=McpEndpointPolicy(resolver=public_dns),
        token_broker=broker,
        enterprise_auth=enterprise_auth,
    )
    server = await mcp.register(
        McpServerDefinition(
            mcp_server_id=new_id("mcp_server"),
            workspace_id=WORKSPACE_ID,
            connector_id=CONNECTOR_ID,
            name="enterprise-scan",
            endpoint=ENDPOINT,
            owner_principal_id="prin_admin",
            discovery_policy=McpDiscoveryPolicy(default_ttl_ms=60_000),
            tool_mappings=[McpToolMapping(capability_id=CAPABILITY_ID, tool_name="echo")],
        )
    )
    return Deployment(
        store=store,
        secrets=secrets,
        broker=broker,
        identity=identity,
        mcp=mcp,
        remote=remote,
        idp=idp,
        authorization_server=authorization_server,
        server=server,
        enterprise_auth=enterprise_auth,
    )


async def drive_an_enterprise_invocation() -> tuple[Deployment, Sentinels]:
    """Sign in for real, invoke a governed tool for real, then capture the material.

    Nothing is seeded: no connection, no token handle, no assertion. Every piece of
    material the scan looks for was produced by the code under test.
    """

    deployment = await setup_deployment()
    store = deployment.store
    await store.upsert_workspace(WorkspaceEntity(workspace_id=WORKSPACE_ID, name="scan"))

    # 1. The real OIDC Authorization Code + PKCE login.
    url = await deployment.identity.begin_login()
    query = parse_qs(urlparse(url).query)
    code = deployment.idp.issue_code(
        FakeUser(SUBJECT_SENTINEL, email=f"{SUBJECT_SENTINEL}@test"), query["nonce"][0]
    )
    principal, session = await deployment.identity.complete_login(
        state=query["state"][0], code=code
    )
    deployment.principal = principal
    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=WORKSPACE_ID,
            principal_id=principal.principal_id,
            role=WorkspaceRole.ADMIN,
        )
    )

    # 2. Discovery and enable both cross the enterprise branch; the connection and
    #    token handle are minted there, never here.
    await deployment.mcp.refresh_discovery(
        deployment.server.mcp_server_id,
        principal_id=principal.principal_id,
        workspace_id=WORKSPACE_ID,
        force=True,
    )
    await deployment.mcp.enable(
        deployment.server.mcp_server_id,
        principal_id=principal.principal_id,
        workspace_id=WORKSPACE_ID,
    )

    # 3. A real run, a real task envelope, a real context bundle.
    project = Project(project_id=new_id("project"), name="ema-scan", repository_path=".")
    await store.create_project(project)
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective="Invoke a centrally managed MCP server and scan every surface.",
            allowed_capabilities=[CAPABILITY_ID],
        )
    )
    await store.create_task(task)
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
        principal_id=principal.principal_id,
    )
    await store.create_run(run)
    deployment.run = run
    bundle = ContextBundle(
        context_bundle_id=new_id("context"),
        task_ref=task.envelope.task_id,
        project_summary="context that must never carry an assertion or a token",
    )
    # Context bundles are normally written by planning; seed one directly so the scan
    # has a real bundle to search.
    store.contexts[bundle.context_bundle_id] = bundle
    deployment.context_bundle_id = bundle.context_bundle_id

    # 4. The real resolver and the real gateway, calling the tool.
    resolved = await CapabilityResolver(store).resolve(
        CAPABILITY_ID, principal_id=principal.principal_id, workspace_id=WORKSPACE_ID
    )
    assert resolved is not None
    assert resolved.outcome is CapabilityResolutionOutcome.OK
    assert resolved.binding is not None and resolved.connection is not None
    gateway = CapabilityGateway(
        store=store,
        side_effects=MemorySideEffectLedger(),
        broker=CredentialBroker(),
        executor=CapabilityExecutor(),
        policy_engine=CapabilityPolicyEngine(),
        token_broker=deployment.broker,
        remote_mcp=deployment.mcp,
    )
    result = await gateway.execute(
        CapabilityRequest(
            request_id=new_id("capability_request"),
            run_id=run.run_id,
            node_id="act",
            capability_id=CAPABILITY_ID,
            capability_version="1.0.0",
            arguments={"message": "hello"},
            declared_reason="enterprise secret scan",
        ),
        resolved.connection,
        resolved.binding,
    )
    assert result.status is CapabilityExecutionStatus.SUCCEEDED
    deployment.tool_results.append(result)

    # 5. Capture the material. The access token is the value this module chose; the
    #    id_token and the grant are recovered from where they really went.
    assertion = await store.get_identity_assertion_for_session(session.auth_session_id)
    assert assertion is not None
    record = await store.get_secret_record(assertion.secret_store_key)
    assert record is not None
    id_token = await deployment.secrets.open(
        record, associated_id=session.auth_session_id
    )
    assert deployment.authorization_server.assertions, "no grant reached the AS"
    jag = deployment.authorization_server.assertions[-1]
    assert deployment.authorization_server.issued == [EMA_ACCESS_SENTINEL]
    jti = jwt.decode(jag, options={"verify_signature": False})["jti"]
    sentinels = Sentinels(
        id_token=id_token,
        jag=jag,
        access_token=EMA_ACCESS_SENTINEL,
        jag_jti=jti,
    )

    # The chain really ran, and the hostile server really was handed the token — so
    # "no leak" is a statement about the system, not about an inert fixture.
    assert deployment.idp.exchange_calls == 1
    assert deployment.authorization_server.grant_calls == 1
    assert deployment.remote.tool_calls == 1
    assert f"Bearer {EMA_ACCESS_SENTINEL}" in deployment.remote.authorization_headers
    assert id_token.count(".") == 2 and jag.count(".") == 2
    return deployment, sentinels


@pytest.mark.acceptance("AC3-EMA-05")
async def test_no_assertion_grant_or_enterprise_token_reaches_any_surface() -> None:
    """AC3-EMA-05, proven by search rather than by trusting the redactor."""

    deployment, sentinels = await drive_an_enterprise_invocation()
    store = deployment.store
    run = deployment.run
    principal = deployment.principal
    assert run is not None and principal is not None
    surfaces: dict[str, str] = {}

    # 1. AgentEvent — the durable trace.
    surfaces["AgentEvent"] = json.dumps(
        [item.model_dump(mode="json") for item in await store.list_events(run.run_id)]
    )
    # 2. TaskEnvelope — what reaches model context.
    task = await store.get_task(run.task_id)
    assert task is not None
    surfaces["TaskEnvelope"] = task.envelope.model_dump_json()
    # 3. ContextBundle.
    surfaces["ContextBundle"] = json.dumps(
        [item.model_dump(mode="json") for item in store.contexts.values()]
    )
    # 4. Capability results, which the agent reads directly.
    surfaces["CapabilityExecutionResult"] = json.dumps(
        [
            item.model_dump(mode="json")
            for item in await store.list_capability_results(run.run_id)
        ]
    )
    # 5. Every enterprise auth grant row, detail included. These are prose an
    #    operator reads, and the one place a "log what happened" would leak.
    grants = await store.list_enterprise_auth_grants()
    assert grants, "no enterprise auth grant was recorded"
    surfaces["EnterpriseAuthGrant"] = json.dumps(
        [item.model_dump(mode="json") for item in grants]
    )
    surfaces["EnterpriseAuthGrant.detail"] = json.dumps([item.detail for item in grants])

    # 6. Frontend payload — every API response the operator UI consumes, including
    #    the three routes M7 adds.
    app.state.manager = type("M", (), {"store": store})()
    app.state.remote_mcp = deployment.mcp
    app.state.enterprise_auth = deployment.enterprise_auth
    app.state.auth = AuthRuntime(
        mode="LOCAL_PRINCIPAL",
        identity=deployment.identity,
        cookie_name="accretion_session",
        cookie_secure=False,
        session_ttl_seconds=3600,
        local_principal_cache=principal,
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            body = ""
            reached = 0
            # The POST first: it re-authorizes through the whole enterprise chain, so
            # its response is the most likely place for material to surface.
            authorize = await client.post(
                f"/api/v1/mcp/servers/{deployment.server.mcp_server_id}"
                "/enterprise-authorize"
            )
            assert authorize.status_code == 200, authorize.text
            assert authorize.json()["connection_id"]
            body += authorize.text
            reached += 1
            for path in (
                "/api/v1/enterprise-auth/profile",
                "/api/v1/audit/enterprise-auth",
                "/api/v1/connections",
                "/api/v1/connectors",
                "/api/v1/me",
                "/openapi.json",
            ):
                response = await client.get(path)
                if response.status_code == 200:
                    body += response.text
                    reached += 1
            # A scan that silently reached nothing would pass vacuously.
            assert reached >= 4, f"only {reached} frontend surfaces responded"
            surfaces["frontend payload"] = body
    finally:
        del app.state.auth
        del app.state.manager
        del app.state.remote_mcp
        del app.state.enterprise_auth

    # The POST above ran the chain a second time, so re-read the grant rows: a
    # refusal or a success recorded after the first read must be scanned too.
    surfaces["EnterpriseAuthGrant (after routes)"] = json.dumps(
        [
            item.model_dump(mode="json")
            for item in await store.list_enterprise_auth_grants()
        ]
    )

    leaks = {
        (surface, name): value[:16]
        for surface, blob in surfaces.items()
        for name, value in sentinels.values().items()
        if value in blob
    }
    assert not leaks, f"enterprise material reached: {sorted(leaks)}"
    # The scan must actually have looked at something.
    assert all(len(blob) > 2 for blob in surfaces.values()), surfaces.keys()


@pytest.mark.acceptance("AC3-EMA-05")
async def test_the_enterprise_profile_route_describes_state_without_revealing_it() -> None:
    """The profile is the route most tempted to say too much, so pin its keys.

    A response that grew a token, a ``secret_store_key`` or an assertion id would
    still pass a substring scan on the day the sentinel changed shape. Asserting the
    exact key set is what makes that impossible, and the live-assertion fields are
    checked to be truthful so the route is not merely silent.
    """

    deployment, _ = await drive_an_enterprise_invocation()
    principal = deployment.principal
    assert principal is not None
    assertion = await deployment.store.get_identity_assertion_for_principal(
        principal.principal_id
    )
    assert assertion is not None

    app.state.manager = type("M", (), {"store": deployment.store})()
    app.state.enterprise_auth = deployment.enterprise_auth
    app.state.auth = AuthRuntime(
        mode="LOCAL_PRINCIPAL",
        identity=deployment.identity,
        cookie_name="accretion_session",
        cookie_secure=False,
        session_ttl_seconds=3600,
        local_principal_cache=principal,
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/enterprise-auth/profile")
    finally:
        del app.state.auth
        del app.state.manager
        del app.state.enterprise_auth

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "enabled",
        "token_exchange_configured",
        "audiences",
        "has_live_assertion",
        "assertion_expires_at",
    }
    assert payload["enabled"] is True
    assert payload["token_exchange_configured"] is True
    assert payload["audiences"] == {CONNECTOR_ID: AUDIENCE}
    assert payload["has_live_assertion"] is True
    assert payload["assertion_expires_at"] is not None


def test_the_scan_covers_every_surface_the_criterion_names() -> None:
    """OpenTelemetry is only a transitive dependency of the MCP SDK; Accretion
    never instruments it, so no span export surface exists to scan. Record that
    rather than imply it, and fail the moment either fact changes."""

    import importlib.util
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "accretion"
    importers = sorted(
        str(path.relative_to(src.parent))
        for path in src.rglob("*.py")
        if "opentelemetry" in path.read_text(encoding="utf-8")
    )
    assert not importers, (
        f"Accretion now instruments OpenTelemetry in {importers}: add its span export "
        "to the surfaces scanned by "
        "test_no_assertion_grant_or_enterprise_token_reaches_any_surface"
    )
    if importlib.util.find_spec("opentelemetry") is None:
        return
    from opentelemetry import trace

    provider = trace.get_tracer_provider()
    assert type(provider).__name__ in {"ProxyTracerProvider", "NoOpTracerProvider"}, (
        f"a real tracer provider ({type(provider).__name__}) is configured: add its span "
        "export to the surfaces scanned by "
        "test_no_assertion_grant_or_enterprise_token_reaches_any_surface"
    )
    assert importlib.util.find_spec("opentelemetry.sdk") is None, (
        "opentelemetry-sdk is installed, so spans can be exported: add span export to the "
        "surfaces scanned by "
        "test_no_assertion_grant_or_enterprise_token_reaches_any_surface"
    )
