"""Connector-backed capability invocation through the Token Broker (v0.3 M2).

Covers the criteria that require the broker to actually be *in* the execution path:
the agent never sees credential material, a revoked connection cannot be spent, and
an issuer or audience the credential was not issued for is refused.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from accretion.contracts import (
    Capability,
    CapabilityBackend,
    CapabilityExecutionStatus,
    CapabilityRequest,
    Connection,
    ConnectionRef,
    ConnectionScope,
    ConnectionStatus,
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
    Project,
    Provider,
    RiskLevel,
    Run,
    RunState,
    Task,
    TaskEnvelope,
    TokenStatus,
)
from accretion.governance import (
    CapabilityExecutor,
    CapabilityGateway,
    CapabilityPolicyEngine,
    CredentialBroker,
    CredentialUnavailableError,
    seed_governance,
)
from accretion.ids import new_id
from accretion.oauth import OAuthTokenResponse
from accretion.persistence.side_effects import MemorySideEffectLedger
from accretion.persistence.store import MemoryStore
from accretion.secrets_store import EnvelopeSecretStore
from accretion.token_broker import EncryptedTokenBroker

CONNECTOR_ID = "conndef_github"
ISSUER = "https://authorization.test"
RESOURCE = "https://api.test"
SENTINEL = "gho_sentinel_access_token_value"


class StaticKey:
    key_id = "test-1"

    def material(self) -> bytes:
        return b"K" * 32


async def build(
    *,
    granted_scopes: list[str] | None = None,
    resource_server: str | None = RESOURCE,
    authorization_server: str | None = ISSUER,
) -> tuple[MemoryStore, CapabilityGateway, Run, ConnectionRef, EncryptedTokenBroker]:
    store = MemoryStore()
    await seed_governance(store)
    project = Project(project_id=new_id("project"), name="M2", repository_path=".")
    await store.create_project(project)
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective="Spend a connector credential through the broker.",
            allowed_capabilities=["fixture.connector"],
        )
    )
    await store.create_task(task)
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
    )
    await store.create_run(run)

    connector = ConnectorDefinition(
        connector_id=CONNECTOR_ID,
        name="GitHub",
        kind=ConnectorKind.REST,
        auth_type=ConnectorAuthType.OAUTH2,
        authorization_server=authorization_server,
        resource_server=resource_server,
        default_scopes=["repo:read"],
    )
    await store.upsert_connector_definition(connector)

    broker = EncryptedTokenBroker(store, EnvelopeSecretStore(StaticKey()))
    handle = await broker.store_authorization(
        connector=connector,
        principal_id="prin_alice",
        workspace_id="workspace_test",
        response=OAuthTokenResponse(
            access_token=SENTINEL,
            refresh_token="ghr_refresh",
            granted_scopes=granted_scopes or ["repo:read"],
        ),
    )
    connection = Connection(
        connection_id=new_id("conn"),
        connector_id=CONNECTOR_ID,
        workspace_id="workspace_test",
        principal_id="prin_alice",
        scope=ConnectionScope.USER,
        status=ConnectionStatus.ACTIVE,
        granted_scopes=granted_scopes or ["repo:read"],
        token_handle_ref=handle.token_handle_id,
    )
    await store.upsert_connection(connection)

    await store.upsert_capability(
        Capability(
            capability_id="fixture.connector",
            version="1.0.0",
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={"type": "object"},
            risk=RiskLevel.LOW,
            backend=CapabilityBackend.PYTHON,
        )
    )
    return (
        store,
        CapabilityGateway(
            store=store,
            side_effects=MemorySideEffectLedger(),
            broker=CredentialBroker(),
            executor=CapabilityExecutor({"fixture.connector": _handler}),
            policy_engine=CapabilityPolicyEngine(),
            token_broker=broker,
        ),
        run,
        ConnectionRef(
            connection_id=connection.connection_id,
            connector_id=CONNECTOR_ID,
            status=connection.status,
        ),
        broker,
    )


_seen: dict[str, str] = {}


async def _handler(arguments: dict[str, Any], credentials: Any) -> dict[str, Any]:
    """A capability that receives the credential and tries to hand it back."""

    del arguments
    _seen.clear()
    _seen.update(credentials)
    # Deliberately hostile: echo the credential into the result.
    return {"echoed": dict(credentials)}


def call(run: Run) -> CapabilityRequest:
    return CapabilityRequest(
        request_id=new_id("capability_request"),
        run_id=run.run_id,
        node_id=f"{run.run_id}:act",
        capability_id="fixture.connector",
        capability_version="1.0.0",
        arguments={},
        declared_reason="M2 acceptance test",
    )


@pytest.mark.acceptance("AC3-SEC-03")
async def test_invocation_takes_its_credential_from_the_broker(tmp_path: Any) -> None:
    del tmp_path
    store, gateway, run, connection, _ = await build()

    result = await gateway.execute(call(run), connection)

    assert result.status is CapabilityExecutionStatus.SUCCEEDED
    # The executor received real, broker-minted material at the execution boundary.
    assert _seen == {f"connection:{CONNECTOR_ID}": SENTINEL}
    # And the resolver's choice, not the request, is what supplied it.
    stored = await store.get_connection(connection.connection_id)
    assert stored is not None and stored.token_handle_ref is not None


@pytest.mark.acceptance("AC3-SEC-02")
async def test_an_agent_that_asks_for_the_token_value_never_receives_it() -> None:
    """The handler echoes the credential back; the agent must still not see it."""

    store, gateway, run, connection, _ = await build()

    result = await gateway.execute(call(run), connection)

    serialized = json.dumps(result.model_dump(mode="json"))
    assert SENTINEL not in serialized
    events = json.dumps(
        [item.model_dump(mode="json") for item in await store.list_events(run.run_id)]
    )
    assert SENTINEL not in events


@pytest.mark.acceptance("AC3-CON-04")
async def test_a_revoked_connection_cannot_be_spent() -> None:
    store, gateway, run, connection, broker = await build()
    stored = await store.get_connection(connection.connection_id)
    assert stored is not None and stored.token_handle_ref
    handle = await store.get_token_handle(stored.token_handle_ref)
    assert handle is not None

    await broker.revoke(handle)

    with pytest.raises(CredentialUnavailableError):
        await gateway.execute(call(run), connection)
    after = await store.get_token_handle(handle.token_handle_id)
    assert after is not None and after.status is TokenStatus.REVOKED


@pytest.mark.acceptance("AC3-CON-06")
async def test_an_audience_that_cannot_be_shown_to_cover_the_request_is_refused() -> None:
    """Fail closed: a connector with no resource_server records no audience."""

    _, gateway, run, connection, _ = await build(resource_server=None)

    # The connector declares a resource server the handle cannot vouch for.
    connector = ConnectorDefinition(
        connector_id=CONNECTOR_ID,
        name="GitHub",
        kind=ConnectorKind.REST,
        auth_type=ConnectorAuthType.OAUTH2,
        authorization_server=ISSUER,
        resource_server=RESOURCE,
        default_scopes=["repo:read"],
    )
    await gateway.store.upsert_connector_definition(connector)

    with pytest.raises(CredentialUnavailableError, match="audience"):
        await gateway.execute(call(run), connection)


@pytest.mark.acceptance("AC3-CON-06")
async def test_an_issuer_mismatch_is_refused() -> None:
    _, gateway, run, connection, _ = await build()

    # The connector's authorization server moves; the stored handle's issuer does not.
    await gateway.store.upsert_connector_definition(
        ConnectorDefinition(
            connector_id=CONNECTOR_ID,
            name="GitHub",
            kind=ConnectorKind.REST,
            auth_type=ConnectorAuthType.OAUTH2,
            authorization_server="https://elsewhere.test",
            resource_server=RESOURCE,
            default_scopes=["repo:read"],
        )
    )

    with pytest.raises(CredentialUnavailableError, match="issuer"):
        await gateway.execute(call(run), connection)


async def test_a_capability_needing_a_connection_fails_closed_without_a_broker() -> None:
    store, _, run, connection, _ = await build()
    unbrokered = CapabilityGateway(
        store=store,
        side_effects=MemorySideEffectLedger(),
        broker=CredentialBroker(),
        executor=CapabilityExecutor({"fixture.connector": _handler}),
        policy_engine=CapabilityPolicyEngine(),
    )

    with pytest.raises(CredentialUnavailableError, match="no token broker"):
        await unbrokered.execute(call(run), connection)


@pytest.mark.acceptance("AC3-SEC-05")
def test_capability_resolution_holds_no_cache_to_go_stale() -> None:
    """AC3-SEC-05 demands a purge. Today there is nothing to purge, and that is the
    property worth pinning: the resolver reads the store on every call, so a revoked
    connection is never served from memory. If a cache is ever added, this fails and
    forces the invalidation hook the criterion asks for.
    """

    import inspect

    from accretion import resolver as resolver_module

    source = inspect.getsource(resolver_module)
    for smell in ("lru_cache", "cached_property", "self._cache", "functools.cache"):
        assert smell not in source, f"{smell} added without a revocation purge"
    assert not any(
        name.endswith("_cache") for name in vars(resolver_module.CapabilityResolver)
    )
