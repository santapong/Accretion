"""Memory/PostgreSQL authority and transaction witnesses; no live conformance claims."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update

from accretion.contracts import (
    Principal,
    PrincipalRef,
    PrincipalStatus,
    PrincipalType,
    Project,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.contracts.canonical import CanonicalContract, canonical_json, content_hash
from accretion.contracts.robotics import (
    AdapterConformanceReport,
    CanonicalWriterEnvelope,
    EmbodiedVerificationSpec,
    EmbodimentDescriptor,
    ObservationSpec,
    RobotAdapterManifest,
    SafetyEnvelope,
    SimulationDomainEvent,
)
from accretion.contracts.robotics.values import DependencyClosure
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.models import RoboticsContractRow, RoboticsEventRow
from accretion.persistence.store import MemoryStore, PostgresStore
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.registry import RoboticsRegistry
from accretion.robotics.store import registry_store_for

ROOT = Path(__file__).resolve().parents[1]
POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")


class SyntheticArtifactVerifier:
    """Test-owned injected trust control, never a production signature verifier."""

    def __init__(self) -> None:
        self.calls = 0

    async def verify(self, envelope: CanonicalWriterEnvelope) -> None:
        envelope.for_execution(RobotAdapterManifest)
        self.calls += 1


class SyntheticConformanceAuthority:
    """An in-process attestation stub only for exercising the registry boundary."""

    def __init__(self, report: AdapterConformanceReport) -> None:
        self.principal_id = report.created_by.principal_id
        self.verifier = report.verifier.model_copy(deep=True)
        self.suite_version = report.suite_version
        self.suite_artifact_digest = report.suite_artifact_digest
        self.allowed_hashes = {report.content_hash}

    async def verify(self, envelope: CanonicalWriterEnvelope) -> None:
        if envelope.writer_content_hash not in self.allowed_hashes:
            raise ValueError("test attestation is not admitted")


@dataclass
class Scope:
    state: MemoryStore | PostgresStore
    registry: RoboticsRegistry
    actor: Principal
    workspace: str
    project: str

    @property
    def args(self) -> dict[str, str]:
        return {
            "actor_id": self.actor.principal_id,
            "workspace_id": self.workspace,
            "project_id": self.project,
        }

    def build[C: CanonicalContract](self, model: type[C], **overrides: Any) -> C:
        payload = json.loads(
            (ROOT / "tests/fixtures/contracts/v0.5" / model.__name__ / "minimal.json").read_text()
        )
        payload.update(
            contract_id=new_id(str(model.ID_KIND)),
            workspace_id=self.workspace,
            project_id=self.project,
            created_by=PrincipalRef(
                principal_id=self.actor.principal_id, status=PrincipalStatus.ACTIVE
            ).model_dump(),
        )
        payload.pop("content_hash")
        payload.update(overrides)
        return model.model_validate(payload)

    async def register(self, model: CanonicalContract, key: str | None = None) -> Any:
        return await self.registry.register(
            **self.args,
            original_json=canonical_json(model).decode(),
            idempotency_key=key or model.contract_id,
        )


@pytest.fixture(
    params=[
        "memory",
        pytest.param(
            "postgres",
            marks=[
                pytest.mark.integration,
                pytest.mark.skipif(
                    not POSTGRES_URL, reason="ACCRETION_TEST_POSTGRES_URL is not set"
                ),
            ],
        ),
    ]
)
async def scope(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    engine = create_engine(str(POSTGRES_URL)) if request.param == "postgres" else None
    state = PostgresStore(create_session_factory(engine)) if engine else MemoryStore()
    actor = await state.upsert_principal(
        Principal(
            principal_id=new_id("principal"),
            issuer="registry-test.invalid",
            subject=new_id("principal"),
        )
    )
    workspace, project = new_id("workspace_entity"), new_id("project")
    await state.upsert_workspace(WorkspaceEntity(workspace_id=workspace, name="Registry test"))
    await state.create_project(
        Project(project_id=project, name="Registry test", repository_path=tmp_path)
    )
    await state.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=workspace,
            principal_id=actor.principal_id,
            role=WorkspaceRole.OWNER,
        )
    )
    store = registry_store_for(state)
    await store.bootstrap_bind_project(workspace_id=workspace, project_id=project)
    registry = RoboticsRegistry(store, artifact_verifier=SyntheticArtifactVerifier())
    try:
        yield Scope(state, registry, actor, workspace, project)
    finally:
        if engine:
            await engine.dispose()


async def chain(scope: Scope) -> tuple[EmbodimentDescriptor, ObservationSpec, RobotAdapterManifest]:
    descriptor = scope.build(
        EmbodimentDescriptor,
        sensors=[{"sensor_id": "joint_state", "modality": "JOINT_STATE", "frame_id": "base"}],
    )
    observations = scope.build(ObservationSpec)
    await scope.register(descriptor)
    await scope.register(observations)
    adapter = scope.build(
        RobotAdapterManifest,
        embodiment_descriptor_hashes=[descriptor.content_hash],
        observation_spec_hash=observations.content_hash,
    )
    await scope.register(adapter)
    return descriptor, observations, adapter


async def conformance(scope: Scope) -> tuple[RobotAdapterManifest, AdapterConformanceReport]:
    descriptor, observations, adapter = await chain(scope)
    service = await scope.state.upsert_principal(
        Principal(
            principal_id=new_id("principal"),
            issuer="registry-test.invalid",
            subject=new_id("principal"),
            type=PrincipalType.SERVICE,
        )
    )
    await scope.state.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=scope.workspace,
            principal_id=service.principal_id,
            role=WorkspaceRole.SERVICE,
        )
    )
    dependencies = json.loads(
        (ROOT / "tests/fixtures/contracts/v0.5/AdapterConformanceReport/minimal.json").read_text()
    )["dependencies"]
    dependencies.update(
        adapter_artifact_digest=adapter.artifact_digest,
        observation_spec_hash=observations.content_hash,
        robot_model_digest=descriptor.robot_model_ref.digest,
        action_intent_schema_hash=adapter.action_intent_schema_hash,
        prepared_command_schema_hash=adapter.prepared_command_schema_hash,
    )
    report = scope.build(
        AdapterConformanceReport,
        result="PASS",
        tests_total=1,
        tests_passed=1,
        adapter_manifest_hash=adapter.content_hash,
        dependencies=dependencies,
        created_by={"principal_id": service.principal_id, "status": "ACTIVE"},
        verifier_principal={"principal_id": service.principal_id, "status": "ACTIVE"},
        adapter_producer_principal={"principal_id": scope.actor.principal_id, "status": "ACTIVE"},
    )
    scope.registry.conformance_authority = SyntheticConformanceAuthority(report)
    return adapter, report


async def file_report(
    scope: Scope, adapter: RobotAdapterManifest, report: AdapterConformanceReport, **overrides: Any
) -> Any:
    args = {
        **scope.args,
        "actor_id": report.created_by.principal_id,
        "adapter_contract_id": adapter.contract_id,
        "original_json": canonical_json(report).decode(),
        "dependencies": report.dependencies,
        "expected_revision": 1,
        "idempotency_key": "report-key",
    }
    args.update(overrides)
    return await scope.registry.record_conformance(**args)


async def test_registration_preserves_writer_and_one_atomic_event_across_wrappers(
    scope: Scope,
) -> None:
    descriptor = scope.build(EmbodimentDescriptor)
    original = json.dumps(json.loads(canonical_json(descriptor)), ensure_ascii=False, indent=3)
    entry = await scope.registry.register(
        **scope.args, original_json=original, idempotency_key="create"
    )
    another = RoboticsRegistry(registry_store_for(scope.state))
    read = await another.get(**scope.args, contract_id=descriptor.contract_id)
    assert read == entry and read.original_json == original and read.revision == 1
    retry = await another.register(
        **scope.args, original_json=canonical_json(descriptor).decode(), idempotency_key="create"
    )
    assert retry == entry
    events = await another.events(**scope.args, aggregate_id=descriptor.contract_id)
    assert len(events) == 1
    event = events[0].for_execution(SimulationDomainEvent)
    assert event.payload["content_hash"] == descriptor.content_hash
    assert event.event_type == "embodiment.registered" and event.sequence == 1


async def test_legacy_project_cannot_be_claimed_by_first_registration(
    scope: Scope, tmp_path: Path
) -> None:
    project = new_id("project")
    await scope.state.create_project(
        Project(project_id=project, name="Unbound legacy", repository_path=tmp_path)
    )
    descriptor = scope.build(EmbodimentDescriptor, project_id=project)
    with pytest.raises(RoboticsError) as error:
        await scope.registry.register(
            **{**scope.args, "project_id": project},
            original_json=canonical_json(descriptor).decode(),
            idempotency_key="hijack",
        )
    assert error.value.code is Code.RESOURCE_NOT_FOUND
    other = new_id("workspace_entity")
    await scope.state.upsert_workspace(WorkspaceEntity(workspace_id=other, name="Other"))
    with pytest.raises(RoboticsError) as error:
        await scope.registry.store.bootstrap_bind_project(
            workspace_id=other, project_id=scope.project
        )
    assert error.value.code is Code.CONTRACT_CONFLICT


@pytest.mark.parametrize(
    "change", ["disabled", "viewer", "researcher", "service", "absent", "forged_actor"]
)
async def test_current_identity_and_roles_override_caller_labels(scope: Scope, change: str) -> None:
    descriptor = scope.build(EmbodimentDescriptor)
    args = scope.args
    if change == "disabled":
        await scope.state.upsert_principal(
            scope.actor.model_copy(update={"status": PrincipalStatus.DISABLED})
        )
    elif change == "forged_actor":
        args = {**args, "actor_id": new_id("principal")}
    else:
        role = {
            "viewer": WorkspaceRole.VIEWER,
            "researcher": WorkspaceRole.RESEARCHER,
            "service": WorkspaceRole.SERVICE,
            "absent": WorkspaceRole.VIEWER,
        }[change]
        await scope.state.upsert_workspace_membership(
            WorkspaceMembership(
                membership_id=new_id("workspace_membership"),
                workspace_id=scope.workspace,
                principal_id=scope.actor.principal_id,
                role=role,
            )
        )
        if change == "absent":
            args = {**args, "workspace_id": new_id("workspace_entity")}
    with pytest.raises(RoboticsError) as error:
        await scope.registry.register(
            **args, original_json=canonical_json(descriptor).decode(), idempotency_key="register"
        )
    assert error.value.code in {Code.CAPABILITY_DENIED, Code.RESOURCE_NOT_FOUND}


async def test_cross_scope_read_reference_and_cursor_rejected(scope: Scope) -> None:
    descriptor, observations, _ = await chain(scope)
    other = new_id("project")
    await scope.state.create_project(
        Project(project_id=other, name="Other", repository_path=Path("/tmp"))
    )
    await scope.registry.store.bootstrap_bind_project(
        workspace_id=scope.workspace, project_id=other
    )
    with pytest.raises(RoboticsError) as error:
        await scope.registry.get(
            **{**scope.args, "project_id": other}, contract_id=descriptor.contract_id
        )
    assert error.value.code is Code.RESOURCE_NOT_FOUND
    adapter = scope.build(
        RobotAdapterManifest,
        project_id=other,
        observation_spec_hash=observations.content_hash,
        embodiment_descriptor_hashes=[descriptor.content_hash],
    )
    with pytest.raises(RoboticsError) as error:
        await scope.registry.register(
            **{**scope.args, "project_id": other},
            original_json=canonical_json(adapter).decode(),
            idempotency_key="foreign",
        )
    assert error.value.code is Code.RESOURCE_NOT_FOUND


@pytest.mark.parametrize("mutation", ["idempotency", "same_id", "same_slot"])
async def test_immutable_identity_and_key_conflicts(scope: Scope, mutation: str) -> None:
    original = scope.build(EmbodimentDescriptor)
    await scope.register(original, "same-key")
    values = original.model_dump()
    values.pop("content_hash")
    values["labels"] = {"changed": "yes"}
    if mutation == "same_slot":
        values["contract_id"] = new_id("embodiment_descriptor")
    changed = EmbodimentDescriptor.model_validate(values)
    with pytest.raises(RoboticsError) as error:
        await scope.register(changed, "same-key" if mutation == "idempotency" else "new-key")
    assert error.value.code is (
        Code.IDEMPOTENCY_CONFLICT if mutation == "idempotency" else Code.CONTRACT_CONFLICT
    )
    assert len(await scope.registry.events(**scope.args, aggregate_id=original.contract_id)) == 1


async def test_racing_duplicate_and_different_body_have_one_committed_effect(scope: Scope) -> None:
    descriptor = scope.build(EmbodimentDescriptor)
    results = await asyncio.gather(*(scope.register(descriptor, "race") for _ in range(8)))
    assert all(result == results[0] for result in results)
    assert len(await scope.registry.events(**scope.args, aggregate_id=descriptor.contract_id)) == 1
    left = scope.build(EmbodimentDescriptor, embodiment_id="another")
    right = scope.build(EmbodimentDescriptor, embodiment_id="another")
    results = await asyncio.gather(
        scope.register(left, "racing-change"),
        scope.register(right, "racing-change"),
        return_exceptions=True,
    )
    assert (
        sum(isinstance(r, RoboticsError) and r.code is Code.IDEMPOTENCY_CONFLICT for r in results)
        == 1
    )


async def test_event_failure_rolls_back_contract_and_key(
    scope: Scope, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_emit = scope.registry._emit

    async def crash(*args: Any, **kwargs: Any) -> None:
        await original_emit(*args, **kwargs)
        raise RuntimeError("injected failure after outbox append")

    descriptor = scope.build(EmbodimentDescriptor)
    monkeypatch.setattr(scope.registry, "_emit", crash)
    with pytest.raises(RuntimeError):
        await scope.register(descriptor, "retry-after-rollback")
    async with scope.registry.store.transaction(scope.project) as tx:
        assert await tx.get(descriptor.contract_id) is None
        assert await tx.events(descriptor.contract_id, 0, 100) == []
    monkeypatch.setattr(scope.registry, "_emit", original_emit)
    assert (await scope.register(descriptor, "retry-after-rollback")).revision == 1


async def test_supporting_refs_and_sensor_mapping_are_resolved(scope: Scope) -> None:
    descriptor, observations, _ = await chain(scope)
    safety = scope.build(SafetyEnvelope, embodiment_descriptor_hash=descriptor.content_hash)
    await scope.register(safety)
    await scope.register(scope.build(EmbodiedVerificationSpec))
    events = await scope.registry.events(**scope.args, aggregate_id=observations.contract_id)
    assert (
        events[0].for_execution(SimulationDomainEvent).event_type
        == "simulation_contract.registered"
    )
    changed = scope.build(
        RobotAdapterManifest,
        adapter_id="bad.observation",
        observation_spec_hash="e" * 64,
        embodiment_descriptor_hashes=[descriptor.content_hash],
    )
    with pytest.raises(RoboticsError) as error:
        await scope.register(changed)
    assert error.value.code is Code.RESOURCE_NOT_FOUND


@pytest.mark.parametrize("mutation", ["missing_hash", "tamper", "major", "extra", "oversize"])
async def test_raw_writer_refusals_cannot_auto_reseal(scope: Scope, mutation: str) -> None:
    payload = json.loads(canonical_json(scope.build(EmbodimentDescriptor)))
    if mutation == "missing_hash":
        payload.pop("content_hash")
    elif mutation == "tamper":
        payload["embodiment_id"] = "tampered"
    elif mutation == "major":
        payload["schema_version"] = "2.0.0"
        payload["content_hash"] = content_hash(payload)
    elif mutation == "extra":
        payload["grant_hardware"] = True
        payload["content_hash"] = content_hash(payload)
    original = json.dumps(payload) if mutation != "oversize" else " " * 1_048_577
    with pytest.raises(RoboticsError) as error:
        await scope.registry.register(**scope.args, original_json=original, idempotency_key="bad")
    assert error.value.code is (
        Code.PAYLOAD_TOO_LARGE if mutation == "oversize" else Code.INVALID_CONTRACT
    )


async def test_unknown_minor_roundtrip_preserves_original_and_refuses_execution(
    scope: Scope,
) -> None:
    payload = json.loads(canonical_json(scope.build(EmbodimentDescriptor)))
    payload.update(schema_version="1.1.0", future_optional={"kept": [1, 2]})
    payload["content_hash"] = content_hash(payload)
    original = json.dumps(payload)
    created = await scope.registry.register(
        **scope.args, original_json=original, idempotency_key="future"
    )
    assert (
        await scope.registry.get(**scope.args, contract_id=created.contract_id)
    ).original_json == original
    with pytest.raises(ValueError):
        created.envelope.for_execution(EmbodimentDescriptor)


@pytest.mark.parametrize("field", ["content_hash", "logical_name", "created_by", "original_json"])
async def test_corrupt_index_or_original_is_not_returned(scope: Scope, field: str) -> None:
    descriptor = scope.build(EmbodimentDescriptor)
    await scope.register(descriptor)
    value = "corrupt" if field != "created_by" else scope.actor.principal_id + "x"
    if isinstance(scope.state, MemoryStore):
        records = scope.state.robotics_registry_state["records"]
        records[descriptor.contract_id] = replace(records[descriptor.contract_id], **{field: value})
    else:
        # Preserve the FK while still falsifying creator attribution.
        if field == "created_by":
            other = await scope.state.upsert_principal(
                Principal(
                    principal_id=new_id("principal"), issuer="test", subject=new_id("principal")
                )
            )
            value = other.principal_id
        async with scope.state.sessions.begin() as session:
            await session.execute(
                update(RoboticsContractRow)
                .where(RoboticsContractRow.id == descriptor.contract_id)
                .values(**{field: value})
            )
    with pytest.raises(RoboticsError) as error:
        await scope.registry.get(**scope.args, contract_id=descriptor.contract_id)
    assert error.value.code is Code.INVALID_CONTRACT


async def test_artifact_trust_absence_and_permission_change_fail_closed(scope: Scope) -> None:
    descriptor = scope.build(
        EmbodimentDescriptor,
        sensors=[{"sensor_id": "joint_state", "modality": "JOINT_STATE", "frame_id": "base"}],
    )
    obs = scope.build(ObservationSpec)
    await scope.register(descriptor)
    await scope.register(obs)
    adapter = scope.build(
        RobotAdapterManifest,
        embodiment_descriptor_hashes=[descriptor.content_hash],
        observation_spec_hash=obs.content_hash,
    )
    scope.registry.artifact_verifier = None
    with pytest.raises(RoboticsError) as error:
        await scope.register(adapter)
    assert error.value.code is Code.ARTIFACT_UNAVAILABLE

    class RevokingVerifier:
        async def verify(self, envelope: CanonicalWriterEnvelope) -> None:
            await scope.state.upsert_principal(
                scope.actor.model_copy(update={"status": PrincipalStatus.DISABLED})
            )

    scope.registry.artifact_verifier = RevokingVerifier()
    with pytest.raises(RoboticsError) as error:
        await scope.register(adapter)
    assert error.value.code is Code.CAPABILITY_DENIED
    async with scope.registry.store.transaction(scope.project) as tx:
        assert await tx.get(adapter.contract_id) is None


async def test_conformance_is_internal_trusted_and_requires_entire_current_closure(
    scope: Scope,
) -> None:
    adapter, report = await conformance(scope)
    with pytest.raises(RoboticsError) as error:
        await scope.register(report)
    assert error.value.code is Code.INVALID_CONTRACT
    result = await file_report(scope, adapter, report)
    assert await file_report(scope, adapter, report) == result
    pending = await scope.registry.get(**scope.args, contract_id=adapter.contract_id)
    assert pending.conformance_status == "PENDING" and pending.revision == 2
    exact = await scope.registry.get(
        **scope.args, contract_id=adapter.contract_id, dependencies=report.dependencies
    )
    assert exact.conformance_status == "PASS" and len(exact.conformance_reports) == 1
    for key in DependencyClosure.model_fields:
        changed = report.dependencies.model_copy(update={key: "f" * 64})
        view = await scope.registry.get(
            **scope.args, contract_id=adapter.contract_id, dependencies=changed
        )
        assert view.conformance_status == "PENDING", key
    events = await scope.registry.events(**scope.args, aggregate_id=adapter.contract_id)
    assert [event.for_execution(SimulationDomainEvent).sequence for event in events] == [1, 2]
    assert len(pending.conformance_reports) == 1


@pytest.mark.parametrize(
    "mutation",
    ["no_trust", "wrong_actor", "disabled", "suite", "closure", "revision", "unattested"],
)
async def test_report_attestation_identity_scope_and_revision_refused(
    scope: Scope, mutation: str
) -> None:
    adapter, report = await conformance(scope)
    args: dict[str, Any] = {}
    if mutation == "no_trust":
        scope.registry.conformance_authority = None
    elif mutation == "wrong_actor":
        args["actor_id"] = scope.actor.principal_id
    elif mutation == "disabled":
        principal = await scope.state.get_principal(report.created_by.principal_id)
        await scope.state.upsert_principal(
            principal.model_copy(update={"status": PrincipalStatus.DISABLED})
        )
    elif mutation == "closure":
        args["dependencies"] = report.dependencies.model_copy(update={"world_digest": "d" * 64})
    elif mutation == "revision":
        args["expected_revision"] = 2
    else:
        values = report.model_dump()
        values.pop("content_hash")
        values["suite_version" if mutation == "suite" else "labels"] = (
            "9.0.0" if mutation == "suite" else {"changed": "yes"}
        )
        report = AdapterConformanceReport.model_validate(values)
    with pytest.raises(RoboticsError):
        await file_report(scope, adapter, report, **args)
    view = await scope.registry.get(**scope.args, contract_id=adapter.contract_id)
    assert view.revision == 1 and view.conformance_reports == []


async def test_revocation_removes_current_qualification_without_erasing_report(
    scope: Scope,
) -> None:
    adapter, report = await conformance(scope)
    await file_report(scope, adapter, report)
    principal = await scope.state.get_principal(report.created_by.principal_id)
    await scope.state.upsert_principal(
        principal.model_copy(update={"status": PrincipalStatus.DISABLED})
    )
    view = await scope.registry.get(
        **scope.args, contract_id=adapter.contract_id, dependencies=report.dependencies
    )
    assert view.conformance_status == "PENDING" and len(view.conformance_reports) == 1


async def test_bounded_pagination_and_logical_version_lookup(scope: Scope) -> None:
    values = [scope.build(EmbodimentDescriptor, descriptor_version=f"1.0.{i}") for i in range(4)]
    for value in values:
        await scope.register(value)
    first = await scope.registry.list(
        **scope.args, contract_type=EmbodimentDescriptor.CONTRACT_TYPE, limit=2
    )
    second = await scope.registry.list(
        **scope.args,
        contract_type=EmbodimentDescriptor.CONTRACT_TYPE,
        limit=2,
        cursor=first.next_cursor,
    )
    assert len(first.items) == len(second.items) == 2 and second.next_cursor is None
    assert len({v.contract_id for v in first.items + second.items}) == 4
    found = await scope.registry.get(
        **scope.args,
        contract_id=values[0].embodiment_id,
        version=values[0].descriptor_version,
        contract_type=EmbodimentDescriptor.CONTRACT_TYPE,
    )
    assert found.content_hash == values[0].content_hash
    with pytest.raises(RoboticsError):
        await scope.registry.list(
            **scope.args, contract_type=EmbodimentDescriptor.CONTRACT_TYPE, limit=101
        )
    with pytest.raises(RoboticsError):
        await scope.registry.list(
            **scope.args, contract_type=ObservationSpec.CONTRACT_TYPE, cursor=first.next_cursor
        )


async def test_corrupt_event_payload_pin_cannot_be_resealed_by_projection(scope: Scope) -> None:
    descriptor = scope.build(EmbodimentDescriptor)
    await scope.register(descriptor)
    event = (await scope.registry.events(**scope.args, aggregate_id=descriptor.contract_id))[0]
    payload = event.payload()
    payload["payload_hash"] = "f" * 64
    payload["content_hash"] = content_hash(payload)
    original = json.dumps(payload)
    if isinstance(scope.state, MemoryStore):
        rows = scope.state.robotics_registry_state["events"]
        key = (descriptor.contract_id, 1)
        rows[key] = replace(rows[key], original_json=original, content_hash=payload["content_hash"])
    else:
        async with scope.state.sessions.begin() as session:
            await session.execute(
                update(RoboticsEventRow)
                .where(RoboticsEventRow.id == payload["contract_id"])
                .values(original_json=original, content_hash=payload["content_hash"])
            )
    with pytest.raises(RoboticsError) as error:
        await scope.registry.events(**scope.args, aggregate_id=descriptor.contract_id)
    assert error.value.code is Code.INVALID_CONTRACT


def test_supporting_registry_event_fixture_retains_strict_event_inventory() -> None:
    original = (ROOT / "tests/fixtures/robotics/registry/contract_registered.json").read_text()
    event = CanonicalWriterEnvelope(original).for_execution(SimulationDomainEvent)
    assert event.event_type == "simulation_contract.registered"
    payload = event.model_dump()
    payload.pop("content_hash")
    payload["event_type"] = "simulation_contract.activate_everything"
    with pytest.raises(ValueError):
        SimulationDomainEvent.model_validate(payload)
