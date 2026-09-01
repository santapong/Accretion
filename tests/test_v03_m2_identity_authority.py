"""Identity that changes an outcome, not just a projection (v0.3 M2).

AC3-ID-04 and AC3-ID-05 were previously satisfied only at the identity projection and
the HTTP boundary. These pin the two places where identity must actually decide
something: workspace role gating a shared connection, and a disabled principal being
refused at the capability boundary.
"""

from __future__ import annotations

import pytest

from accretion.connections import ConnectionError, ConnectionService
from accretion.contracts import (
    CapabilityRequest,
    ConnectionScope,
    ConnectorAuthType,
    ConnectorDefinition,
    ConnectorKind,
    Principal,
    PrincipalStatus,
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
from accretion.governance import (
    CapabilityExecutor,
    CapabilityGateway,
    CapabilityPolicyEngine,
    CredentialBroker,
    default_capability_handlers,
    seed_governance,
)
from accretion.ids import new_id
from accretion.persistence.side_effects import MemorySideEffectLedger
from accretion.persistence.store import MemoryStore

WORKSPACE = "workspace_test"


async def workspace_connector_service(role: WorkspaceRole) -> tuple[ConnectionService, Principal]:
    store = MemoryStore()
    who = Principal(principal_id="prin_alice", issuer="accretion-local", subject="alice")
    await store.upsert_principal(who)
    await store.upsert_workspace(WorkspaceEntity(workspace_id=WORKSPACE, name="test"))
    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id="wsm_alice",
            workspace_id=WORKSPACE,
            principal_id=who.principal_id,
            role=role,
        )
    )
    await store.upsert_connector_definition(
        ConnectorDefinition(
            connector_id="conndef_shared",
            name="Shared",
            kind=ConnectorKind.REST,
            auth_type=ConnectorAuthType.OAUTH2,
            authorization_server="https://issuer.test",
            connection_scope=ConnectionScope.WORKSPACE,
            default_scopes=["read"],
        )
    )
    service = ConnectionService(store=store, broker=None, clients={})  # type: ignore[arg-type]
    return service, who


@pytest.mark.acceptance("AC3-ID-04")
async def test_a_role_change_takes_effect_immediately_with_no_reinstall() -> None:
    service, who = await workspace_connector_service(WorkspaceRole.DEVELOPER)

    # A DEVELOPER cannot open a connection that acts for the whole workspace.
    with pytest.raises(ConnectionError, match="OWNER or ADMIN"):
        await service.begin(
            connector_id="conndef_shared", principal=who, workspace_id=WORKSPACE
        )

    # Promote. Nothing is reinstalled, restarted, or re-registered.
    await service.store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id="wsm_alice",
            workspace_id=WORKSPACE,
            principal_id=who.principal_id,
            role=WorkspaceRole.ADMIN,
            revision=2,
        )
    )

    # The same call now passes the role gate and fails later, on the missing client,
    # which proves the gate is what changed.
    with pytest.raises(ConnectionError, match="no configured OAuth client"):
        await service.begin(
            connector_id="conndef_shared", principal=who, workspace_id=WORKSPACE
        )


async def test_a_user_scoped_connector_needs_no_workspace_role() -> None:
    service, who = await workspace_connector_service(WorkspaceRole.VIEWER)
    await service.store.upsert_connector_definition(
        ConnectorDefinition(
            connector_id="conndef_personal",
            name="Personal",
            kind=ConnectorKind.REST,
            auth_type=ConnectorAuthType.OAUTH2,
            authorization_server="https://issuer.test",
            connection_scope=ConnectionScope.USER,
            default_scopes=["read"],
        )
    )
    with pytest.raises(ConnectionError, match="no configured OAuth client"):
        await service.begin(
            connector_id="conndef_personal", principal=who, workspace_id=WORKSPACE
        )


async def gateway_for(status: PrincipalStatus) -> tuple[CapabilityGateway, Run, MemoryStore]:
    store = MemoryStore()
    await seed_governance(store)
    who = Principal(
        principal_id="prin_alice",
        issuer="accretion-local",
        subject="alice",
        status=status,
    )
    await store.upsert_principal(who)
    project = Project(project_id=new_id("project"), name="ID", repository_path=".")
    await store.create_project(project)
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective="Invoke a capability as a principal.",
            allowed_capabilities=["accretion.echo"],
        )
    )
    await store.create_task(task)
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project.project_id,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
        principal_id=who.principal_id,
    )
    await store.create_run(run)
    # accretion.echo is seeded by seed_governance and is immutable; reuse it.
    return (
        CapabilityGateway(
            store=store,
            side_effects=MemorySideEffectLedger(),
            broker=CredentialBroker(),
            executor=CapabilityExecutor(default_capability_handlers()),
            policy_engine=CapabilityPolicyEngine(),
        ),
        run,
        store,
    )


def echo(run: Run) -> CapabilityRequest:
    return CapabilityRequest(
        request_id=new_id("capability_request"),
        run_id=run.run_id,
        node_id=f"{run.run_id}:act",
        capability_id="accretion.echo",
        capability_version="1.0.0",
        arguments={"message": "hello"},
        declared_reason="identity authority test",
    )


@pytest.mark.acceptance("AC3-ID-05")
async def test_a_disabled_principal_is_refused_at_the_capability_boundary() -> None:
    """The gateway subprocess authenticates nobody, so the run must carry the owner."""

    gateway, run, store = await gateway_for(PrincipalStatus.ACTIVE)
    first = await gateway.execute(echo(run))
    assert first.authorization.outcome.value != "DENY"

    owner = await store.get_principal("prin_alice")
    assert owner is not None
    await store.upsert_principal(
        owner.model_copy(update={"status": PrincipalStatus.DISABLED})
    )

    # Same in-flight run, same capability: authority is re-read, not cached.
    with pytest.raises(PermissionError, match="may not invoke capabilities"):
        await gateway.execute(echo(run))


async def test_a_run_without_a_principal_still_executes() -> None:
    """Local single-user operation predates principals and must keep working."""

    gateway, run, store = await gateway_for(PrincipalStatus.ACTIVE)
    unowned = run.model_copy(update={"principal_id": None})
    store.runs[unowned.run_id] = unowned
    result = await gateway.execute(echo(unowned))
    assert result.authorization.outcome.value != "DENY"
