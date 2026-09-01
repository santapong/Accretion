from __future__ import annotations

import json

import pytest

from accretion.contracts import (
    ApprovalDecisionValue,
    Capability,
    CapabilityBackend,
    CapabilityExecutionStatus,
    CapabilityRequest,
    Project,
    Provider,
    RiskLevel,
    Run,
    RunState,
    Task,
    TaskEnvelope,
)
from accretion.governance import (
    CapabilityExecutor,
    CapabilityGateway,
    CapabilityPolicyEngine,
    CredentialBroker,
    approval_binding,
    default_capability_handlers,
    seed_governance,
)
from accretion.ids import new_id
from accretion.mcp_gateway import StdioMcpGateway
from accretion.persistence.side_effects import MemorySideEffectLedger
from accretion.persistence.store import MemoryStore
from accretion.runtimes.common import provider_environment


async def setup_gateway(
    *,
    allowed: list[str],
    denied: list[str] | None = None,
    executor: CapabilityExecutor | None = None,
    broker: CredentialBroker | None = None,
) -> tuple[MemoryStore, CapabilityGateway, Run]:
    store = MemoryStore()
    await seed_governance(store)
    project = Project(
        project_id=new_id("project"), name="P4 governance", repository_path="."
    )
    await store.create_project(project)
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project.project_id,
            objective="Exercise the governed capability boundary.",
            allowed_capabilities=allowed,
            denied_capabilities=denied or [],
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
    gateway = CapabilityGateway(
        store=store,
        side_effects=MemorySideEffectLedger(),
        broker=broker or CredentialBroker(),
        executor=executor or CapabilityExecutor(default_capability_handlers()),
        policy_engine=CapabilityPolicyEngine(),
    )
    return store, gateway, run


def request(
    run: Run,
    capability_id: str,
    arguments: dict[str, object],
    *,
    idempotency_key: str | None = None,
) -> CapabilityRequest:
    return CapabilityRequest(
        request_id=new_id("capability_request"),
        run_id=run.run_id,
        node_id=f"{run.run_id}:act",
        capability_id=capability_id,
        capability_version="1.0.0",
        arguments=arguments,
        declared_reason="P4 acceptance test",
        idempotency_key=idempotency_key,
    )


@pytest.mark.acceptance("AC3-MCP-01")
async def test_registry_is_versioned_immutable_and_mcp_inventory_is_task_scoped() -> None:
    store, gateway, run = await setup_gateway(
        allowed=["accretion.echo", "accretion.protected-write"],
        denied=["accretion.protected-write"],
    )
    assert [item.capability_id for item in await store.list_capabilities()] == [
        "accretion.echo",
        "accretion.protected-write",
    ]
    await seed_governance(store)
    mcp = StdioMcpGateway(gateway, store, run.run_id)
    response = await mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response is not None
    assert [item["name"] for item in response["result"]["tools"]] == ["accretion.echo"]


async def test_unknown_and_task_denied_capabilities_fail_closed_without_execution() -> None:
    calls = 0

    async def should_not_run(
        arguments: dict[str, object], credentials: object
    ) -> dict[str, object]:
        nonlocal calls
        del arguments, credentials
        calls += 1
        return {}

    store, gateway, run = await setup_gateway(
        allowed=["accretion.echo"],
        denied=["accretion.echo"],
        executor=CapabilityExecutor({"accretion.echo": should_not_run}),
    )
    denied = await gateway.execute(request(run, "accretion.echo", {"message": "blocked"}))
    unknown = await gateway.execute(request(run, "external.missing", {}))
    assert denied.status is CapabilityExecutionStatus.DENIED
    assert unknown.status is CapabilityExecutionStatus.DENIED
    assert calls == 0
    assert len(await store.list_capability_results(run.run_id)) == 2


@pytest.mark.acceptance("V01-P0-006")
@pytest.mark.acceptance("AC3-MCP-01")
async def test_low_risk_call_executes_and_protected_call_requires_bound_approval_once() -> None:
    calls = 0

    async def protected(
        arguments: dict[str, object], credentials: object
    ) -> dict[str, object]:
        nonlocal calls
        del credentials
        calls += 1
        return {"recorded": True, "value": arguments["value"]}

    store, gateway, run = await setup_gateway(
        allowed=["accretion.echo", "accretion.protected-write"],
        executor=CapabilityExecutor(
            {**default_capability_handlers(), "accretion.protected-write": protected}
        ),
    )
    echo = await gateway.execute(request(run, "accretion.echo", {"message": "ready"}))
    assert echo.status is CapabilityExecutionStatus.SUCCEEDED
    assert echo.output == {"message": "ready"}

    first = request(
        run,
        "accretion.protected-write",
        {"value": "durable"},
        idempotency_key="protected:fixture:1",
    )
    pending = await gateway.execute(first)
    assert pending.status is CapabilityExecutionStatus.REQUIRES_APPROVAL
    approval = (await store.list_approvals(run.run_id))[0]
    assert approval.native_request_id == approval_binding(first)
    await store.decide_approval(approval.approval_id, ApprovalDecisionValue.APPROVE)

    completed = await gateway.execute(
        request(
            run,
            "accretion.protected-write",
            {"value": "durable"},
            idempotency_key="protected:fixture:1",
        )
    )
    duplicate = await gateway.execute(
        request(
            run,
            "accretion.protected-write",
            {"value": "durable"},
            idempotency_key="protected:fixture:1",
        )
    )
    assert completed.status is CapabilityExecutionStatus.SUCCEEDED
    assert duplicate.status is CapabilityExecutionStatus.SUCCEEDED
    assert completed.side_effect_operation_id == duplicate.side_effect_operation_id
    assert calls == 1


@pytest.mark.acceptance("V01-P4-002")
async def test_credentials_are_injected_only_at_execution_and_redacted_everywhere() -> None:
    secret = "sk-super-secret-value-123456789"
    seen: dict[str, str] = {}

    async def credential_handler(
        arguments: dict[str, object], credentials: object
    ) -> dict[str, object]:
        del arguments
        assert isinstance(credentials, dict)
        seen.update(credentials)
        return {"authorization": f"Bearer {credentials['fixture.secret']}"}

    store, gateway, run = await setup_gateway(
        allowed=["fixture.credential"],
        executor=CapabilityExecutor({"fixture.credential": credential_handler}),
        broker=CredentialBroker(
            {"fixture.secret": "FIXTURE_SECRET"}, {"FIXTURE_SECRET": secret}
        ),
    )
    await store.upsert_capability(
        Capability(
            capability_id="fixture.credential",
            version="1.0.0",
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={"type": "object"},
            risk=RiskLevel.LOW,
            credential_refs=["fixture.secret"],
            backend=CapabilityBackend.PYTHON,
        )
    )
    result = await gateway.execute(request(run, "fixture.credential", {}))
    assert seen == {"fixture.secret": secret}
    assert secret not in json.dumps(result.model_dump(mode="json"))
    assert secret not in json.dumps(
        [item.model_dump(mode="json") for item in await store.list_events(run.run_id)]
    )


@pytest.mark.acceptance("V01-P0-001")
def test_provider_environment_drops_secret_bearing_variables() -> None:
    environment = provider_environment(
        {"ACCRETION_GATEWAY_RUN_ID": "run_fixture", "SAFE_METADATA": "ready"}
    )
    assert environment["ACCRETION_GATEWAY_RUN_ID"] == "run_fixture"
    assert environment["SAFE_METADATA"] == "ready"
    assert not any("TOKEN" in key or "SECRET" in key or "PASSWORD" in key for key in environment)


@pytest.mark.acceptance("V01-P4-003")
async def test_side_effect_without_idempotency_key_is_denied() -> None:
    _, gateway, run = await setup_gateway(allowed=["accretion.protected-write"])
    result = await gateway.execute(
        request(run, "accretion.protected-write", {"value": "unsafe"})
    )
    assert result.status is CapabilityExecutionStatus.DENIED
    assert result.error is not None
    assert "idempotency" in result.error.message
