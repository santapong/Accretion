"""M2 receipt-first dispatch wiring in the graph executor."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    EventType,
    GraphNodeKind,
    GraphNodeStatus,
    IterationDirective,
    IterationDirectiveKind,
    Provider,
    Run,
    RunNode,
    RunState,
    RuntimeHealth,
    RuntimeStatus,
    SessionRef,
    Task,
    TaskEnvelope,
    WorkspaceLease,
)
from accretion.contracts.canonical import content_hash
from accretion.contracts.routing import DecisionType
from accretion.persistence.store import MemoryStore
from accretion.routing.protocols import RoutingMode
from accretion.runtimes.fake import FakeRuntime
from accretion.services.run_manager import NodeOutcome, RunManager, _GraphCursor
from accretion.verifiers import GitDiffVerifier, VerifierRegistry

NOW = datetime(2026, 9, 6, 1, 0, tzinfo=UTC)
RUN_ID = "run_01K4FCM2QBTV7NS83WZ7P89Y0D"
PROJECT_ID = "prj_01K4FCM2QBTV7NS83WZ7P89Y0E"
TASK_ID = "tsk_01K4FCM2QBTV7NS83WZ7P89Y0F"
NODE_HASH = "a" * 64


def run_row() -> Run:
    return Run(
        run_id=RUN_ID,
        task_id=TASK_ID,
        project_id=PROJECT_ID,
        provider=Provider.FAKE,
        state=RunState.RUNNING,
        principal_id="usr_01K4FCM2QBTV7NS83WZ7P89Y0G",
        created_at=NOW,
        updated_at=NOW,
    )


def task_row() -> Task:
    return Task(
        envelope=TaskEnvelope(
            task_id=TASK_ID,
            project_id=PROJECT_ID,
            objective="execute the selected node",
            allowed_capabilities=["cap.repo.read"],
        ),
        created_at=NOW,
    )


def session_row() -> SessionRef:
    return SessionRef(
        session_id="ses_01K4FCM2QBTV7NS83WZ7P89Y0H",
        run_id=RUN_ID,
        provider=Provider.FAKE,
        native_session_id="fake-existing",
        workspace=Path("/tmp/accretion-m2-dispatch"),
    )


def lease_row() -> WorkspaceLease:
    return WorkspaceLease(
        lease_id="wsl_m2_dispatch",
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        base_revision="base",
        path=Path("/tmp/accretion-m2-dispatch"),
        branch_name="m2-dispatch",
        acquired_at=NOW,
    )


def receipt(*, decision: DecisionType = DecisionType.FALLBACK) -> SimpleNamespace:
    return SimpleNamespace(
        contract_id="rcp_01K4FCM2QBTV7NS83WZ7P89Y0J",
        node_contract_hash=NODE_HASH,
        decision_type=decision,
    )


FAKE_CAPABILITIES = [
    "structured-events",
    "repeatable-calls",
    "interrupt",
    "resume",
    "artifacts",
]


def runtime_digest(version: str, capabilities: list[str]) -> str:
    return content_hash(
        {
            "runtime_id": "runtime_fake",
            "provider": Provider.FAKE,
            "runtime_version": version,
            "capabilities": sorted(capabilities),
        },
        exclude=(),
    )


def configuration(*, version: str = FakeRuntime.adapter_version) -> SimpleNamespace:
    return SimpleNamespace(
        contract_id="cfg_01K4FCM2QBTV7NS83WZ7P89Y0P",
        configuration_hash="d" * 64,
        workspace_id="wks_m2_dispatch",
        runtime=SimpleNamespace(
            provider=Provider.FAKE,
            runtime_id="runtime_fake",
            adapter_version=version,
            capability_profile_digest=runtime_digest(version, FAKE_CAPABILITIES),
        ),
        model=SimpleNamespace(model_id="fake-model"),
        tools=[],
    )


def manager_with(runtime: object, store: MemoryStore | None = None) -> RunManager:
    manager = object.__new__(RunManager)
    manager.store = store or MemoryStore()
    manager.runtimes = {Provider.FAKE: runtime}
    manager.live_providers_enabled = False
    manager.active_refs = {}
    manager.event_conditions = {}
    manager.pause_requested = set()
    manager.limiter = ConcurrencyLimiter(
        global_limit=2, provider_limit=2, project_limit=2
    )
    return manager


async def test_route_is_restored_or_created_then_claimed_before_session_creation() -> None:
    calls: list[str] = []
    selected_receipt = receipt()
    selected_configuration = configuration()

    class RuntimeSpy:
        async def create_session(self, config):  # type: ignore[no-untyped-def]
            calls.append("create-session")
            assert config.model == "fake-model"
            return session_row()

    class RoutingSpy:
        async def latest_receipt(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("latest")
            return None

        async def snapshot(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("snapshot")
            return object()

        async def route(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("route")
            assert kwargs["mode"] is RoutingMode.BASELINE_ONLY
            return selected_receipt

        async def configuration_for(self, receipt):  # type: ignore[no-untyped-def]
            calls.append("configuration")
            return selected_configuration

        async def claim_dispatch(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("claim")
            return selected_configuration

    manager = manager_with(RuntimeSpy())
    manager.routing_service = RoutingSpy()
    node = RunNode(
        node_id=f"{RUN_ID}:act", key="act", kind=GraphNodeKind.AGENT, label="Act"
    )
    frozen = SimpleNamespace(
        node_contract=SimpleNamespace(workspace_id="wks_m2_dispatch")
    )
    cursor = _GraphCursor(
        statuses={}, entered_via={}, current_key=node.key, frozen={node.key: frozen}
    )

    routed_session, outcome = await manager._prepare_routed_node(
        run=run_row(),
        task=task_row(),
        lease=lease_row(),
        session=session_row(),
        node=node,
        cursor=cursor,
    )

    assert outcome is None
    assert routed_session.provider is Provider.FAKE
    assert calls == [
        "latest",
        "snapshot",
        "route",
        "configuration",
        "claim",
        "create-session",
    ]
    assert cursor.receipts[node.key] is selected_receipt
    assert cursor.configurations[node.key] is selected_configuration


async def test_existing_receipt_is_restored_without_rerouting() -> None:
    calls: list[str] = []
    existing = receipt()

    class RoutingSpy:
        async def latest_receipt(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("latest")
            return existing

        async def snapshot(self, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("a persisted receipt must not be snapshotted again")

        async def route(self, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("a persisted receipt must not be rerouted")

        async def claim_dispatch(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("claim")
            return configuration()

    manager = manager_with(object())
    manager.routing_service = RoutingSpy()
    node = RunNode(
        node_id=f"{RUN_ID}:tool", key="tool", kind=GraphNodeKind.TOOL, label="Tool"
    )
    frozen = SimpleNamespace(
        node_contract=SimpleNamespace(workspace_id="wks_m2_dispatch")
    )
    cursor = _GraphCursor(
        statuses={}, entered_via={}, current_key=node.key, frozen={node.key: frozen}
    )

    returned, outcome = await manager._prepare_routed_node(
        run=run_row(),
        task=task_row(),
        lease=lease_row(),
        session=session_row(),
        node=node,
        cursor=cursor,
    )

    assert returned == session_row()
    assert outcome is None
    assert calls == ["latest", "claim"]


async def test_human_review_waits_without_claiming_or_executing() -> None:
    calls: list[str] = []
    review = receipt(decision=DecisionType.HUMAN_REVIEW_REQUIRED)

    class RoutingSpy:
        async def latest_receipt(self, **kwargs):  # type: ignore[no-untyped-def]
            return review

        async def claim_dispatch(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("claim")
            raise AssertionError("human review is not executable")

    manager = manager_with(object())
    manager.routing_service = RoutingSpy()
    node = RunNode(
        node_id=f"{RUN_ID}:verify",
        key="verify",
        kind=GraphNodeKind.VERIFIER,
        label="Verify",
    )
    frozen = SimpleNamespace(
        node_contract=SimpleNamespace(workspace_id="wks_m2_dispatch")
    )
    cursor = _GraphCursor(
        statuses={}, entered_via={}, current_key=node.key, frozen={node.key: frozen}
    )

    returned, outcome = await manager._prepare_routed_node(
        run=run_row(),
        task=task_row(),
        lease=lease_row(),
        session=session_row(),
        node=node,
        cursor=cursor,
    )

    assert returned == session_row()
    assert outcome is NodeOutcome.INCONCLUSIVE
    assert cursor.statuses[node.key] is GraphNodeStatus.WAITING
    assert calls == []


async def test_human_review_refreshes_to_an_operator_override_on_reentry() -> None:
    review = receipt(decision=DecisionType.HUMAN_REVIEW_REQUIRED)
    override = receipt(decision=DecisionType.HUMAN_OVERRIDE)
    latest = [review, override]
    claims: list[object] = []

    class RoutingSpy:
        async def latest_receipt(self, **kwargs):  # type: ignore[no-untyped-def]
            return latest.pop(0)

        async def claim_dispatch(self, **kwargs):  # type: ignore[no-untyped-def]
            claims.append(kwargs["receipt"])
            return configuration()

    manager = manager_with(object())
    manager.routing_service = RoutingSpy()
    node = RunNode(
        node_id=f"{RUN_ID}:tool", key="tool", kind=GraphNodeKind.TOOL, label="Tool"
    )
    frozen = SimpleNamespace(
        node_contract=SimpleNamespace(workspace_id="wks_m2_dispatch")
    )
    cursor = _GraphCursor(
        statuses={}, entered_via={}, current_key=node.key, frozen={node.key: frozen}
    )

    _, first_outcome = await manager._prepare_routed_node(
        run=run_row(),
        task=task_row(),
        lease=lease_row(),
        session=session_row(),
        node=node,
        cursor=cursor,
    )
    _, second_outcome = await manager._prepare_routed_node(
        run=run_row(),
        task=task_row(),
        lease=lease_row(),
        session=session_row(),
        node=node,
        cursor=cursor,
    )

    assert first_outcome is NodeOutcome.INCONCLUSIVE
    assert second_outcome is None
    assert cursor.receipts[node.key] is override
    assert claims == [override]


async def test_an_uncertain_claim_is_never_resubmitted_from_the_cursor() -> None:
    class RoutingSpy:
        async def latest_receipt(self, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("the cursor already carries the persisted receipt")

        async def claim_dispatch(self, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("an uncertain claim must not be claimed or executed twice")

    manager = manager_with(object())
    manager.routing_service = RoutingSpy()
    node = RunNode(
        node_id=f"{RUN_ID}:tool", key="tool", kind=GraphNodeKind.TOOL, label="Tool"
    )
    selected_receipt = receipt()
    frozen = SimpleNamespace(
        node_contract=SimpleNamespace(workspace_id="wks_m2_dispatch")
    )
    cursor = _GraphCursor(
        statuses={},
        entered_via={},
        current_key=node.key,
        frozen={node.key: frozen},
        receipts={node.key: selected_receipt},
        configurations={node.key: configuration()},
    )

    with pytest.raises(RuntimeError, match="prior dispatch is uncertain"):
        await manager._prepare_routed_node(
            run=run_row(),
            task=task_row(),
            lease=lease_row(),
            session=session_row(),
            node=node,
            cursor=cursor,
        )


@pytest.mark.acceptance("AC4-M2-014")
async def test_failed_dispatch_claim_prevents_runtime_and_node_side_effects() -> None:
    calls: list[str] = []
    selected_receipt = receipt()

    class RuntimeSpy:
        async def create_session(self, config):  # type: ignore[no-untyped-def]
            calls.append("create-session")
            raise AssertionError("a failed claim must prevent runtime preparation")

    class RoutingSpy:
        async def latest_receipt(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("latest")
            return selected_receipt

        async def configuration_for(self, receipt):  # type: ignore[no-untyped-def]
            calls.append("configuration")
            return configuration()

        async def claim_dispatch(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("claim")
            raise RuntimeError("DISPATCH_CLAIM_NOT_PERSISTED")

    manager = manager_with(RuntimeSpy())
    manager.routing_service = RoutingSpy()
    node = RunNode(
        node_id=f"{RUN_ID}:act", key="act", kind=GraphNodeKind.AGENT, label="Act"
    )
    frozen = SimpleNamespace(
        node_contract=SimpleNamespace(workspace_id="wks_m2_dispatch")
    )
    cursor = _GraphCursor(
        statuses={}, entered_via={}, current_key=node.key, frozen={node.key: frozen}
    )

    with pytest.raises(RuntimeError, match="DISPATCH_CLAIM_NOT_PERSISTED"):
        await manager._prepare_routed_node(
            run=run_row(),
            task=task_row(),
            lease=lease_row(),
            session=session_row(),
            node=node,
            cursor=cursor,
        )

    assert calls == ["latest", "configuration", "claim"]
    assert cursor.configurations == {}


async def test_agent_with_selected_tools_fails_before_claim_or_session() -> None:
    calls: list[str] = []
    selected_configuration = configuration()
    selected_configuration.tools = [object()]

    class RuntimeSpy:
        async def create_session(self, config):  # type: ignore[no-untyped-def]
            calls.append("create-session")
            raise AssertionError("an unpinned agent tool must prevent session creation")

    class RoutingSpy:
        async def latest_receipt(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("latest")
            return receipt()

        async def configuration_for(self, receipt):  # type: ignore[no-untyped-def]
            calls.append("configuration")
            return selected_configuration

        async def claim_dispatch(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("claim")
            raise AssertionError("an unexecutable selection must not be claimed")

    manager = manager_with(RuntimeSpy())
    manager.routing_service = RoutingSpy()
    node = RunNode(
        node_id=f"{RUN_ID}:act", key="act", kind=GraphNodeKind.AGENT, label="Act"
    )
    frozen = SimpleNamespace(
        node_contract=SimpleNamespace(workspace_id="wks_m2_dispatch")
    )
    cursor = _GraphCursor(
        statuses={}, entered_via={}, current_key=node.key, frozen={node.key: frozen}
    )

    with pytest.raises(RuntimeError, match="SELECTED_AGENT_TOOL_BINDING_UNAVAILABLE"):
        await manager._prepare_routed_node(
            run=run_row(),
            task=task_row(),
            lease=lease_row(),
            session=session_row(),
            node=node,
            cursor=cursor,
        )

    assert calls == ["latest", "configuration"]
    assert cursor.configurations == {}


async def test_routed_tool_invokes_the_exact_selected_binding() -> None:
    calls: list[dict[str, object]] = []
    selected = SimpleNamespace(
        capability=SimpleNamespace(
            capability_id="cap.node.write", capability_version="2.1.0"
        ),
        binding_id="capbind_selected",
        binding_version="1.0",
        tool=SimpleNamespace(tool_id="selected-tool", implementation_digest="b" * 64),
    )

    class SelectedInvoker:
        async def invoke_selected(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(kwargs)
            return object()

    manager = manager_with(object())
    manager.capability_invoker = SelectedInvoker()  # type: ignore[assignment]
    node = RunNode(
        node_id=f"{RUN_ID}:tool", key="tool", kind=GraphNodeKind.TOOL, label="Tool"
    )
    spec = SimpleNamespace(
        capability_refs=["cap.node.write"],
        instruction="write the selected output",
        label="Tool",
    )

    await manager._invoke_node_capabilities(
        run_row(),
        node,
        spec,  # type: ignore[arg-type]
        executing_provider=Provider.FAKE,
        routing_configuration=SimpleNamespace(  # type: ignore[arg-type]
            tools=[selected], workspace_id="wks_m2_dispatch"
        ),
    )

    assert len(calls) == 1
    assert calls[0]["selected"] is selected
    assert calls[0]["workspace_id"] == "wks_m2_dispatch"
    assert calls[0]["arguments"] == {"query": "write the selected output"}


async def test_routed_tool_binding_mismatch_fails_before_invocation() -> None:
    calls: list[object] = []

    class InvokerSpy:
        async def invoke_selected(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(kwargs)
            return object()

    manager = manager_with(object())
    manager.capability_invoker = InvokerSpy()  # type: ignore[assignment]
    node = RunNode(
        node_id=f"{RUN_ID}:tool", key="tool", kind=GraphNodeKind.TOOL, label="Tool"
    )
    spec = SimpleNamespace(
        capability_refs=["cap.node.write"], instruction="write", label="Tool"
    )

    with pytest.raises(RuntimeError, match="SELECTED_TOOL_BINDING_MISMATCH"):
        await manager._invoke_node_capabilities(
            run_row(),
            node,
            spec,  # type: ignore[arg-type]
            routing_configuration=SimpleNamespace(tools=[]),  # type: ignore[arg-type]
        )

    assert calls == []


async def test_legacy_tool_invoker_fails_closed_for_a_selected_binding() -> None:
    calls: list[object] = []
    selected = SimpleNamespace(
        capability=SimpleNamespace(capability_id="cap.node.write"),
    )

    class LegacyInvoker:
        async def __call__(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(kwargs)
            return object()

    manager = manager_with(object())
    manager.capability_invoker = LegacyInvoker()
    node = RunNode(
        node_id=f"{RUN_ID}:tool", key="tool", kind=GraphNodeKind.TOOL, label="Tool"
    )
    spec = SimpleNamespace(
        capability_refs=["cap.node.write"], instruction="write", label="Tool"
    )

    with pytest.raises(RuntimeError, match="SELECTED_TOOL_BINDING_UNAVAILABLE"):
        await manager._invoke_node_capabilities(
            run_row(),
            node,
            spec,  # type: ignore[arg-type]
            routing_configuration=SimpleNamespace(tools=[selected]),  # type: ignore[arg-type]
        )

    assert calls == []


async def test_runtime_drift_is_refused_immediately_before_submit() -> None:
    class DriftedRuntime:
        submits = 0

        async def health(self) -> RuntimeHealth:
            return RuntimeHealth(
                runtime_id="runtime_fake",
                provider=Provider.FAKE,
                status=RuntimeStatus.READY,
                auth_mode="LOCAL",
                runtime_version="fake-p2-v2",
            )

        async def submit(self, session, request):  # type: ignore[no-untyped-def]
            self.submits += 1
            raise AssertionError("drifted runtime must not receive a submission")

    runtime = DriftedRuntime()
    manager = manager_with(runtime)

    with pytest.raises(RuntimeError, match="RUNTIME_VERSION_DRIFT"):
        await manager._runtime_call(
            run_row(),
            session_row(),
            task_row().envelope,
            runtime_call_id="rtc_01K4FCM2QBTV7NS83WZ7P89Y0K",
            deadline=NOW.timestamp() + 60,
            node_key="act",
            routing_receipt=receipt(),  # type: ignore[arg-type]
            routing_configuration=configuration(version="fake-p2-v1"),  # type: ignore[arg-type]
        )

    assert runtime.submits == 0


def test_selected_verifier_is_pinned_to_spec_version_and_implementation() -> None:
    verifier = GitDiffVerifier()
    manager = manager_with(object())
    manager.verifiers = VerifierRegistry([verifier])
    verifier_id = verifier.verifier_id
    implementation_identity = (
        f"{type(verifier).__module__}.{type(verifier).__qualname__}"
    )
    implementation_digest = content_hash(
        {
            "verifier_id": verifier_id,
            "version": verifier.verifier_version,
            "implementation": implementation_identity,
        },
        exclude=(),
    )
    selected = SimpleNamespace(
        verifier=SimpleNamespace(
            verifier_contract_id=verifier_id,
            implementation_digest=implementation_digest,
        ),
        version=verifier.verifier_version,
        verification_spec_hash=NODE_HASH,
    )
    frozen = SimpleNamespace(
        verification_spec=SimpleNamespace(content_hash=NODE_HASH)
    )

    assert (
        manager._selected_verifier_id(  # type: ignore[arg-type]
            SimpleNamespace(verifier=selected), frozen
        )
        == verifier_id
    )

    selected.verifier.implementation_digest = "c" * 64
    with pytest.raises(RuntimeError, match="SELECTED_VERIFIER_MISMATCH"):
        manager._selected_verifier_id(  # type: ignore[arg-type]
            SimpleNamespace(verifier=selected), frozen
        )


async def test_runtime_capability_drift_is_refused_immediately_before_submit() -> None:
    class CapabilityDriftRuntime:
        submits = 0

        async def health(self) -> RuntimeHealth:
            return RuntimeHealth(
                runtime_id="runtime_fake",
                provider=Provider.FAKE,
                status=RuntimeStatus.READY,
                auth_mode="LOCAL",
                runtime_version=FakeRuntime.adapter_version,
                capabilities=[*FAKE_CAPABILITIES, "unexpected-capability"],
            )

        async def submit(self, session, request):  # type: ignore[no-untyped-def]
            self.submits += 1
            raise AssertionError("drifted runtime must not receive a submission")

    runtime = CapabilityDriftRuntime()
    manager = manager_with(runtime)

    with pytest.raises(RuntimeError, match="RUNTIME_VERSION_DRIFT"):
        await manager._runtime_call(
            run_row(),
            session_row(),
            task_row().envelope,
            runtime_call_id="rtc_01K4FCM2QBTV7NS83WZ7P89Y0L",
            deadline=NOW.timestamp() + 60,
            node_key="act",
            routing_receipt=receipt(),  # type: ignore[arg-type]
            routing_configuration=configuration(),  # type: ignore[arg-type]
        )

    assert runtime.submits == 0


@pytest.mark.acceptance("AC4-M2-001")
async def test_routed_runtime_events_carry_receipt_and_node_hash() -> None:
    store = MemoryStore()
    run = run_row()
    await store.create_run(run)
    runtime = FakeRuntime()
    manager = manager_with(runtime, store)
    session = await runtime.create_session(
        SimpleNamespace(
            run_id=RUN_ID,
            workspace=Path("/tmp/accretion-m2-dispatch"),
            model="fake-model",
            allowed_tools=[],
            denied_tools=[],
            resume_native_session_id=None,
        )
    )
    selected_receipt = receipt()

    outcome = await manager._runtime_call(
        run,
        session,
        task_row().envelope,
        runtime_call_id="rtc_01K4FCM2QBTV7NS83WZ7P89Y0M",
        deadline=datetime.now(UTC).timestamp() + 60,
        node_key="act",
        directive=IterationDirective(
            kind=IterationDirectiveKind.INITIAL, objective="execute the selected node"
        ),
        routing_receipt=selected_receipt,  # type: ignore[arg-type]
        routing_configuration=configuration(),  # type: ignore[arg-type]
    )

    assert outcome.completed
    events = await store.list_events(RUN_ID)
    started = [event for event in events if event.normalized_type is EventType.RUNTIME_CALL_STARTED]
    assert len(started) == 1
    assert started[0].payload["routing_receipt_id"] == selected_receipt.contract_id
    assert started[0].payload["node_contract_hash"] == NODE_HASH


async def test_non_routed_runtime_event_payload_is_unchanged() -> None:
    store = MemoryStore()
    run = run_row()
    await store.create_run(run)
    runtime = FakeRuntime()
    manager = manager_with(runtime, store)
    session = await runtime.create_session(
        SimpleNamespace(
            run_id=RUN_ID,
            workspace=Path("/tmp/accretion-m2-dispatch"),
            model=None,
            allowed_tools=[],
            denied_tools=[],
            resume_native_session_id=None,
        )
    )

    outcome = await manager._runtime_call(
        run,
        session,
        task_row().envelope,
        runtime_call_id="rtc_01K4FCM2QBTV7NS83WZ7P89Y0N",
        deadline=datetime.now(UTC).timestamp() + 60,
        node_key="act",
    )

    assert outcome.completed
    started = next(
        event
        for event in await store.list_events(RUN_ID)
        if event.normalized_type is EventType.RUNTIME_CALL_STARTED
    )
    assert "routing_receipt_id" not in started.payload
    assert "node_contract_hash" not in started.payload
