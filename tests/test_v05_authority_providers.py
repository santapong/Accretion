"""Synthetic inventory/transaction witnesses, never production conformance evidence."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, update
from test_v05_authority import Lab, change, rejected
from test_v05_authority import lab as authority_lab
from test_v05_registry import (
    Scope,
    SyntheticArtifactVerifier,
    SyntheticConformanceAuthority,
    file_report,
)

from accretion.contracts import Capability, CapabilityPolicy, PrincipalStatus
from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.refs import PolicyRef
from accretion.contracts.robotics import (
    AdapterConformanceReport,
    ObservationSpec,
    RobotAdapterManifest,
    SimulationDomainEvent,
)
from accretion.contracts.robotics.models import SIMULATION_CAPABILITIES
from accretion.ids import new_id
from accretion.persistence import models as db
from accretion.robotics.authority import AuthorityCheck
from accretion.robotics.authority_providers import (
    AttestationValidity,
    AuthorityInventory,
    InventoryConformanceAuthority,
    InventoryPolicyAuthority,
    checked_inventory_deadline,
)
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.registry import RoboticsRegistry
from accretion.robotics.runtime_store import (
    ConformanceAdmissionSpec,
    DispatchReservation,
    GrantedCapability,
    PolicyGrantSpec,
    RuntimeConformanceAdmission,
    RuntimeEpisode,
    RuntimePolicyGrant,
    RuntimeTransaction,
)

lab = authority_lab


class SyntheticIndependentAttestor:
    """Exact test allowlist, explicitly not a runner/signature/conformance proof."""

    def __init__(self, report: AdapterConformanceReport):
        self.report_hash = report.content_hash
        self.proof = canonical_json({"test_only_report": self.report_hash}).decode()
        self.validity = AttestationValidity(
            valid_from=datetime.now(UTC) - timedelta(minutes=1),
            valid_until=datetime.now(UTC) + timedelta(hours=2),
        )
        self.revoked = False
        self.calls = 0

    async def verify(
        self, tx: Any, report: Any, *, attestation_original_json: str, now: datetime
    ) -> AttestationValidity:
        self.calls += 1
        if (
            self.revoked
            or report.writer_content_hash != self.report_hash
            or attestation_original_json != self.proof
        ):
            raise ValueError("synthetic attestation not admitted")
        return self.validity


@dataclass
class InventoryLab:
    lab: Lab
    registry_scope: Scope
    manifest: RobotAdapterManifest
    report: AdapterConformanceReport
    attestor: SyntheticIndependentAttestor
    inventory: AuthorityInventory
    policy: CapabilityPolicy
    capabilities: list[Capability]
    policy_spec: PolicyGrantSpec
    conformance_spec: ConformanceAdmissionSpec
    grant: RuntimePolicyGrant | None = None
    admission: RuntimeConformanceAdmission | None = None

    async def install(self) -> None:
        self.grant = await self.inventory.install_policy(
            self.lab.scope(self.lab.human), spec=self.policy_spec
        )
        self.admission = await self.inventory.install_conformance(
            self.lab.scope(self.lab.human), spec=self.conformance_spec
        )

    async def bind(self) -> RuntimeEpisode:
        await self.install()
        self.lab.authority.policy = InventoryPolicyAuthority()
        self.lab.authority.conformance = InventoryConformanceAuthority(attestor=self.attestor)
        return await self.lab.bind()

    async def check(
        self, *, operation: str = "OBSERVE", update_check: dict[str, Any] | None = None
    ) -> datetime:
        ep = await self.lab.episode()
        check = AuthorityCheck(
            operation=operation,
            actor_id=self.lab.orchestrator.principal_id,
            now=datetime.now(UTC),
            episode=ep,
        )
        if update_check:
            check = check.model_copy(update=update_check)
        async with self.lab.authority.store.transaction(self.lab.project) as tx:
            return await checked_inventory_deadline(
                tx,
                check,
                policy=InventoryPolicyAuthority(),
                conformance=InventoryConformanceAuthority(attestor=self.attestor),
            )


@pytest.fixture
async def inventory(lab: Lab) -> InventoryLab:
    version = "1.0." + str(int.from_bytes(new_id("principal").encode()[-16:], "big"))
    caps = [
        Capability(
            capability_id=name,
            version=version,
            backend="NATIVE",
            risk="HIGH",
            side_effects=["simulation"],
            required_permissions=["simulation.operator"],
            idempotency="TRANSACTIONAL",
        )
        for name in sorted(SIMULATION_CAPABILITIES)
    ]
    for cap in caps:
        await lab.state.upsert_capability(cap)
    policy = CapabilityPolicy(policy_id=new_id("principal"), version="1.0.0")
    await lab.state.upsert_capability_policy(policy)
    ref = PolicyRef(
        policy_id=policy.policy_id,
        version=policy.version,
        content_digest=content_hash(policy, exclude=()),
    )
    registry = RoboticsRegistry(
        lab.authority.store.registry, artifact_verifier=SyntheticArtifactVerifier()
    )
    scope = Scope(lab.state, registry, lab.human, lab.workspace, lab.project)
    descriptor = change(
        lab.case.descriptor,
        contract_id=new_id(str(lab.case.descriptor.ID_KIND)),
        sensors=[{"sensor_id": "joint_state", "modality": "JOINT_STATE", "frame_id": "base"}],
    )
    observations = scope.build(ObservationSpec)
    fields = observations.model_dump(mode="python")["required"]
    for field in fields:
        field["shape"] = [1]
    observations = change(observations, required=fields)
    await scope.register(descriptor)
    await scope.register(observations)
    manifest = scope.build(
        RobotAdapterManifest,
        embodiment_descriptor_hashes=[descriptor.content_hash],
        observation_spec_hash=observations.content_hash,
        capabilities=[
            {"capability_id": c.capability_id, "capability_version": c.version} for c in caps
        ],
    )
    await scope.register(manifest)
    deps = lab.setup.dependencies.model_copy(
        update={
            "adapter_artifact_digest": manifest.artifact_digest,
            "observation_spec_hash": observations.content_hash,
            "robot_model_digest": descriptor.robot_model_ref.digest,
            "action_intent_schema_hash": manifest.action_intent_schema_hash,
            "prepared_command_schema_hash": manifest.prepared_command_schema_hash,
        }
    )
    report = scope.build(
        AdapterConformanceReport,
        adapter_manifest_hash=manifest.content_hash,
        dependencies=deps,
        result="PASS",
        tests_total=1,
        tests_passed=1,
        created_by={"principal_id": lab.evaluator.principal_id, "status": "ACTIVE"},
        verifier_principal={"principal_id": lab.evaluator.principal_id, "status": "ACTIVE"},
        adapter_producer_principal={"principal_id": lab.human.principal_id, "status": "ACTIVE"},
    )
    registry.conformance_authority = SyntheticConformanceAuthority(report)
    await file_report(scope, manifest, report)
    lab.setup = lab.setup.model_copy(
        update={
            "adapter_manifest_hash": manifest.content_hash,
            "descriptor_original": canonical_json(descriptor).decode(),
            "dependencies": deps,
            "policy_ref": ref,
        }
    )
    lab.task = lab.task.model_copy(
        update={
            "envelope": lab.task.envelope.model_copy(
                update={"allowed_capabilities": sorted(SIMULATION_CAPABILITIES)}
            )
        }
    )
    lab.case.approval = change(lab.case.approval, policy_ref=ref)
    attestor = SyntheticIndependentAttestor(report)
    service = AuthorityInventory(lab.authority.store, attestor=attestor)
    end = datetime.now(UTC) + timedelta(minutes=30)
    return InventoryLab(
        lab,
        scope,
        manifest,
        report,
        attestor,
        service,
        policy,
        caps,
        PolicyGrantSpec(
            operator_id=lab.human.principal_id,
            orchestrator_id=lab.orchestrator.principal_id,
            policy_ref=ref,
            capabilities=tuple(
                GrantedCapability(
                    capability_id=c.capability_id,
                    capability_version=c.version,
                    content_digest=content_hash(c, exclude=()),
                )
                for c in caps
            ),
            operator_permissions=("simulation.operator",),
            valid_until=end,
        ),
        ConformanceAdmissionSpec(
            adapter_contract_id=manifest.contract_id,
            report_id=report.contract_id,
            report_hash=report.content_hash,
            closure_hash=content_hash(deps, exclude=()),
            attestation_original_json=attestor.proof,
            valid_until=end + timedelta(minutes=1),
        ),
    )


async def snapshot(i: InventoryLab) -> tuple[Any, ...]:
    async with i.lab.authority.store.transaction(i.lab.project) as tx:
        values = []
        for model in [
            RuntimePolicyGrant,
            RuntimeConformanceAdmission,
            RuntimeEpisode,
            DispatchReservation,
        ]:
            rows = await tx.list_rows(
                model, workspace_id=i.lab.workspace, project_id=i.lab.project, limit=100
            )
            values.append(rows)
            for row in rows:
                values.append(
                    await tx.registry.events(getattr(row, "event_stream_id", row.id), 0, 100)
                )
        # Check project ledger as well, including refused operation absence.
        values.append(
            await tx.registry.list(
                i.lab.workspace, i.lab.project, SimulationDomainEvent.CONTRACT_TYPE, "", 100
            )
        )
        if tx.session:
            from sqlalchemy import select

            values.append(
                [
                    (r.id, r.request_hash, r.response_json)
                    for r in (
                        await tx.session.scalars(
                            select(db.RoboticsIdempotencyRow)
                            .where(db.RoboticsIdempotencyRow.project_id == i.lab.project)
                            .order_by(db.RoboticsIdempotencyRow.id)
                        )
                    ).all()
                ]
            )
        else:
            values.append(dict(i.lab.state.robotics_registry_state["ledger"]))
        return tuple(values)


async def test_exact_inventory_allows_eligibility_but_still_requires_episode_approval(
    inventory: InventoryLab,
) -> None:
    i = inventory
    ep = await i.bind()
    assert ep.status == "BOUND" and ep.approval_id is None
    assert await i.check() == i.policy_spec.valid_until
    async with i.lab.authority.store.transaction(i.lab.project) as tx:
        assert (
            await tx.list_rows(
                DispatchReservation, workspace_id=i.lab.workspace, project_id=i.lab.project
            )
            == []
        )
    assert await i.lab.state.get_run(i.lab.run.run_id) is not None


async def test_fresh_reset_reservation_carries_inventory_deadline(inventory: InventoryLab) -> None:
    i = inventory
    await i.bind()
    await i.lab.running()
    async with i.lab.authority.store.transaction(i.lab.project) as tx:
        rows = await tx.list_rows(
            DispatchReservation, workspace_id=i.lab.workspace, project_id=i.lab.project
        )
    assert len(rows) == 1 and rows[0].authority_valid_until == i.policy_spec.valid_until


async def test_missing_inventory_rolls_back_real_run_creation(inventory: InventoryLab) -> None:
    i = inventory
    i.lab.authority.policy = InventoryPolicyAuthority()
    i.lab.authority.conformance = InventoryConformanceAuthority(attestor=i.attestor)
    before = await snapshot(i)
    with rejected(Code.CAPABILITY_DENIED):
        await i.lab.bind()
    assert await snapshot(i) == before
    assert await i.lab.state.get_run(i.lab.run.run_id) is None


@pytest.mark.parametrize("kind", ["policy", "conformance"])
async def test_installation_requires_current_human_maintainer(
    inventory: InventoryLab, kind: str
) -> None:
    i = inventory
    before = await snapshot(i)
    with rejected(Code.CAPABILITY_DENIED):
        if kind == "policy":
            await i.inventory.install_policy(i.lab.scope(), spec=i.policy_spec)
        else:
            await i.inventory.install_conformance(i.lab.scope(), spec=i.conformance_spec)
    assert await snapshot(i) == before


async def test_inventory_idempotency_revisions_events_and_revoked_replay(
    inventory: InventoryLab,
) -> None:
    i = inventory
    scope = i.lab.scope(i.lab.human, key="install-policy")
    grant = await i.inventory.install_policy(scope, spec=i.policy_spec)
    before = await snapshot(i)
    assert await i.inventory.install_policy(scope, spec=i.policy_spec) == grant
    assert await snapshot(i) == before
    with rejected(Code.IDEMPOTENCY_CONFLICT):
        await i.inventory.install_policy(
            scope, spec=i.policy_spec.model_copy(update={"operator_permissions": ()})
        )
    with rejected(Code.REVISION_REQUIRED):
        await i.inventory.revoke_policy(i.lab.scope(i.lab.human), grant_id=grant.id)
    with rejected(Code.REVISION_CONFLICT):
        await i.inventory.revoke_policy(i.lab.scope(i.lab.human, revision=2), grant_id=grant.id)
    assert await snapshot(i) == before
    revoked = await i.inventory.revoke_policy(
        i.lab.scope(i.lab.human, revision=1), grant_id=grant.id
    )
    assert revoked.revision == 2 and revoked.disposition == "REVOKED"
    assert await i.inventory.install_policy(scope, spec=i.policy_spec) == grant  # historical only
    async with i.lab.authority.store.transaction(i.lab.project) as tx:
        current = await tx.get(RuntimePolicyGrant, grant.id)
        events = await tx.registry.events(grant.event_stream_id, 0, 100)
    assert current == revoked and len(events) == 2
    i.admission = await i.inventory.install_conformance(
        i.lab.scope(i.lab.human), spec=i.conformance_spec
    )
    i.lab.authority.policy = InventoryPolicyAuthority()
    with rejected(Code.CAPABILITY_DENIED):
        await i.lab.bind()


@pytest.mark.parametrize(
    "failure",
    [
        "missing_permission",
        "missing_capability",
        "wrong_version",
        "wrong_digest",
        "wrong_operator",
        "wrong_orchestrator",
        "policy_mismatch",
        "expired",
        "revoked_operator",
        "revoked_orchestrator",
        "unknown_operation",
    ],
)
async def test_policy_refusals_leave_state_unchanged(inventory: InventoryLab, failure: str) -> None:
    i = inventory
    if failure == "missing_permission":
        i.policy_spec = i.policy_spec.model_copy(update={"operator_permissions": ()})
    if failure == "missing_capability":
        i.policy_spec = i.policy_spec.model_copy(
            update={"capabilities": i.policy_spec.capabilities[1:]}
        )
    await i.install()
    # Bind through the fixture's synthetic trust to isolate current-provider refusals.
    ep = await i.lab.bind()
    changes: dict[str, Any] = {}
    if failure in {"wrong_operator", "wrong_orchestrator"}:
        async with i.lab.authority.store.transaction(i.lab.project) as tx:
            from accretion.robotics.runtime_store import RuntimeBinding

            binding = await tx.get(RuntimeBinding, ep.binding_id)
            assert binding
            field = "initiator_id" if failure == "wrong_operator" else "orchestrator_id"
            await tx.put(binding.model_copy(update={field: i.lab.host.principal_id}))
    if failure in {"policy_mismatch", "wrong_version", "wrong_digest"}:
        if failure == "policy_mismatch":
            ref = ep.setup.policy_ref.model_copy(update={"version": "2.0.0"})
            ep = ep.model_copy(update={"setup": ep.setup.model_copy(update={"policy_ref": ref})})
            changes["episode"] = ep
        else:
            cap = i.capabilities[0]
            async with i.lab.authority.store.transaction(i.lab.project) as tx:
                if tx.session:
                    await tx.session.execute(
                        update(db.CapabilityRow)
                        .where(
                            db.CapabilityRow.capability_id == cap.capability_id,
                            db.CapabilityRow.version == cap.version,
                        )
                        .values(
                            definition=cap.model_copy(
                                update={"description": "tampered"}
                            ).model_dump(mode="json")
                            if failure == "wrong_digest"
                            else cap.model_dump(mode="json"),
                            version=cap.version + ".gone"
                            if failure == "wrong_version"
                            else cap.version,
                        )
                    )
                else:
                    if failure == "wrong_version":
                        i.lab.state.capabilities.pop((cap.capability_id, cap.version))
                    else:
                        i.lab.state.capabilities[(cap.capability_id, cap.version)] = cap.model_copy(
                            update={"description": "tampered"}
                        )
    if failure == "expired":
        assert i.grant
        # Authoritative time, not caller check.now, controls expiry on both backends.
        original_now = RuntimeTransaction.now

        async def future(tx: RuntimeTransaction) -> datetime:
            await original_now(tx)
            return i.grant.valid_until + timedelta(seconds=1)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(RuntimeTransaction, "now", future)
            with rejected(Code.CAPABILITY_DENIED):
                await i.check()
        return
    if failure in {"revoked_operator", "revoked_orchestrator"}:
        person = i.lab.human if failure == "revoked_operator" else i.lab.orchestrator
        await i.lab.state.upsert_principal(
            person.model_copy(update={"status": PrincipalStatus.DISABLED})
        )
    before = await snapshot(i)
    with pytest.raises(RoboticsError):
        await i.check(
            operation="DO_ANYTHING" if failure == "unknown_operation" else "BIND_RUN",
            update_check=changes,
        )
    assert await snapshot(i) == before


@pytest.mark.parametrize(
    "failure", ["disabled", "policy_deny", "task_deny", "task_missing", "no_idempotency"]
)
async def test_existing_governance_denials_are_preserved(
    inventory: InventoryLab, failure: str
) -> None:
    i = inventory
    if failure in {"disabled", "no_idempotency"}:
        cap = i.capabilities[0]
        values = {"enabled": False} if failure == "disabled" else {"idempotency": "NONE"}
        cap = Capability.model_validate(cap.model_dump(mode="python") | values)
        async with i.lab.authority.store.transaction(i.lab.project) as tx:
            if tx.session:
                await tx.session.execute(
                    update(db.CapabilityRow)
                    .where(
                        db.CapabilityRow.capability_id == cap.capability_id,
                        db.CapabilityRow.version == cap.version,
                    )
                    .values(definition=cap.model_dump(mode="json"), enabled=cap.enabled)
                )
            else:
                i.lab.state.capabilities[(cap.capability_id, cap.version)] = cap
        pins = (
            i.policy_spec.capabilities[0].model_copy(
                update={"content_digest": content_hash(cap, exclude=())}
            ),
            *i.policy_spec.capabilities[1:],
        )
        i.policy_spec = i.policy_spec.model_copy(update={"capabilities": pins})
    if failure == "policy_deny":
        policy = i.policy.model_copy(
            update={"version": "2.0.0", "explicitly_denied": [i.capabilities[0].capability_id]}
        )
        await i.lab.state.upsert_capability_policy(policy)
        ref = PolicyRef(
            policy_id=policy.policy_id,
            version=policy.version,
            content_digest=content_hash(policy, exclude=()),
        )
        i.policy_spec = i.policy_spec.model_copy(update={"policy_ref": ref})
        i.lab.setup = i.lab.setup.model_copy(update={"policy_ref": ref})
    if failure in {"task_deny", "task_missing"}:
        values = (
            {"denied_capabilities": [i.capabilities[0].capability_id]}
            if failure == "task_deny"
            else {"allowed_capabilities": []}
        )
        i.lab.task = i.lab.task.model_copy(
            update={"envelope": i.lab.task.envelope.model_copy(update=values)}
        )
    with rejected(Code.CAPABILITY_DENIED):
        await i.bind()
    assert await i.lab.state.get_run(i.lab.run.run_id) is None


async def test_new_policy_version_is_not_implicitly_selected(inventory: InventoryLab) -> None:
    i = inventory
    await i.bind()
    await i.lab.state.upsert_capability_policy(
        i.policy.model_copy(
            update={"version": "999.0.0", "explicitly_denied": sorted(SIMULATION_CAPABILITIES)}
        )
    )
    assert await i.check() == i.policy_spec.valid_until


@pytest.mark.parametrize(
    "failure", ["coerced_boolean", "missing_default", "invalid_field", "policy_default"]
)
async def test_governance_reads_refuse_lossy_writer_projection(
    inventory: InventoryLab, failure: str
) -> None:
    i = inventory
    await i.bind()
    async with i.lab.authority.store.transaction(i.lab.project) as tx:
        cap = i.capabilities[0]
        if failure == "policy_default":
            payload = i.policy.model_dump(mode="json")
            payload.pop("description")
            if tx.session:
                await tx.session.execute(
                    update(db.CapabilityPolicyRow)
                    .where(db.CapabilityPolicyRow.policy_id == i.policy.policy_id)
                    .values(definition=payload)
                )
            else:
                i.lab.state.capability_policies[(i.policy.policy_id, i.policy.version)] = payload
        else:
            payload = cap.model_dump(mode="json")
            if failure == "coerced_boolean":
                payload["enabled"] = "true"
            elif failure == "missing_default":
                payload.pop("description")
            else:
                payload["required_permissions"] = None
            if tx.session:
                await tx.session.execute(
                    update(db.CapabilityRow)
                    .where(
                        db.CapabilityRow.capability_id == cap.capability_id,
                        db.CapabilityRow.version == cap.version,
                    )
                    .values(definition=payload)
                )
            else:
                i.lab.state.capabilities[(cap.capability_id, cap.version)] = payload
    before = await snapshot(i)
    with rejected(Code.INVALID_CONTRACT):
        await i.check()
    assert await snapshot(i) == before


async def test_foreign_scope_cannot_disclose_or_revoke_inventory(inventory: InventoryLab) -> None:
    i = inventory
    await i.bind()
    assert i.grant
    scope = i.lab.scope(i.lab.human, i.grant.revision).model_copy(
        update={"workspace_id": new_id("workspace_entity")}
    )
    before = await snapshot(i)
    for identity in (i.grant.id, "missing-row"):
        with rejected(Code.RESOURCE_NOT_FOUND):
            await i.inventory.revoke_policy(scope, grant_id=identity)
    assert await snapshot(i) == before


async def test_policy_update_has_same_identity_and_new_attributable_revision(
    inventory: InventoryLab,
) -> None:
    i = inventory
    await i.bind()
    assert i.grant
    changed = i.policy_spec.model_copy(update={"operator_permissions": ()})
    result = await i.inventory.install_policy(
        i.lab.scope(i.lab.human, i.grant.revision), spec=changed
    )
    assert result.id == i.grant.id and result.event_stream_id == i.grant.event_stream_id
    assert result.revision == 2 and result.created_at == i.grant.created_at
    assert result.updated_by == i.lab.human.principal_id
    with rejected(Code.CAPABILITY_DENIED):
        await i.check()


@pytest.mark.parametrize(
    "failure",
    [
        "missing_attestor",
        "invalid_attestation",
        "missing_provenance",
        "wrong_report",
        "wrong_closure",
        "expired",
    ],
)
async def test_conformance_install_refusals_are_atomic(
    inventory: InventoryLab, failure: str
) -> None:
    i = inventory
    spec = i.conformance_spec
    if failure == "missing_attestor":
        i.inventory.attestor = None
    elif failure == "invalid_attestation":
        spec = spec.model_copy(update={"attestation_original_json": "{}"})
    elif failure == "wrong_report":
        spec = spec.model_copy(update={"report_hash": "a" * 64})
    elif failure == "wrong_closure":
        spec = spec.model_copy(update={"closure_hash": "a" * 64})
    elif failure == "expired":
        i.attestor.validity = AttestationValidity(
            valid_from=datetime.now(UTC) - timedelta(days=1),
            valid_until=datetime.now(UTC) - timedelta(seconds=1),
        )
    else:
        async with i.lab.authority.store.transaction(i.lab.project) as tx:
            if tx.session:
                await tx.session.execute(
                    delete(db.RoboticsConformanceRow).where(
                        db.RoboticsConformanceRow.adapter_id == i.manifest.contract_id
                    )
                )
            else:
                i.lab.state.robotics_registry_state["conformance"].clear()
    before = await snapshot(i)
    with pytest.raises(RoboticsError):
        await i.inventory.install_conformance(i.lab.scope(i.lab.human), spec=spec)
    assert await snapshot(i) == before


@pytest.mark.parametrize(
    "failure",
    [
        "revoked",
        "quarantined",
        "attestor_revoked",
        "attestor_removed",
        "new_fail",
        "environment_changed",
        "event_missing",
        "report_corrupt",
        "inventory_event_missing",
        "resource_quarantined",
    ],
)
async def test_conformance_current_refusals(inventory: InventoryLab, failure: str) -> None:
    i = inventory
    await i.bind()
    assert i.admission
    if failure in {"revoked", "quarantined"}:
        await i.inventory.revoke_conformance(
            i.lab.scope(i.lab.human, i.admission.revision),
            admission_id=i.admission.id,
            quarantine=failure == "quarantined",
        )
    elif failure == "attestor_revoked":
        i.attestor.revoked = True
    elif failure == "attestor_removed":
        ep = await i.lab.episode()
        async with i.lab.authority.store.transaction(i.lab.project) as tx:
            with rejected(Code.VERIFIER_UNAVAILABLE):
                await InventoryConformanceAuthority().check(
                    tx,
                    AuthorityCheck(
                        operation="OBSERVE",
                        actor_id=i.lab.orchestrator.principal_id,
                        now=datetime.now(UTC),
                        episode=ep,
                    ),
                )
        return
    elif failure == "new_fail":
        newer = change(
            i.report,
            contract_id=new_id(str(AdapterConformanceReport.ID_KIND)),
            result="FAIL",
            tests_passed=0,
            supersedes_contract_id=i.report.contract_id,
        )
        i.registry_scope.registry.conformance_authority = SyntheticConformanceAuthority(newer)
        await file_report(
            i.registry_scope, i.manifest, newer, expected_revision=2, idempotency_key="new-fail"
        )
    elif failure in {"event_missing", "inventory_event_missing", "report_corrupt"}:
        async with i.lab.authority.store.transaction(i.lab.project) as tx:
            if failure == "report_corrupt":
                if tx.session:
                    await tx.session.execute(
                        update(db.RoboticsContractRow)
                        .where(db.RoboticsContractRow.id == i.report.contract_id)
                        .values(content_hash="a" * 64)
                    )
                else:
                    rows = i.lab.state.robotics_registry_state["records"]
                    rows[i.report.contract_id] = replace(
                        rows[i.report.contract_id], content_hash="a" * 64
                    )
            else:
                aggregate = (
                    i.admission.event_stream_id
                    if failure == "inventory_event_missing"
                    else i.manifest.contract_id
                )
                if tx.session:
                    await tx.session.execute(
                        delete(db.RoboticsEventRow).where(
                            db.RoboticsEventRow.aggregate_id == aggregate
                        )
                    )
                else:
                    events = i.lab.state.robotics_registry_state["events"]
                    for key in list(events):
                        if events[key].aggregate_id == aggregate:
                            del events[key]
    changes: dict[str, Any] = {}
    if failure == "environment_changed":
        ep = await i.lab.episode()
        deps = ep.setup.dependencies.model_copy(
            update={"physics_parameters_hash": content_hash("changed physics", exclude=())}
        )
        changes["episode"] = ep.model_copy(
            update={"setup": ep.setup.model_copy(update={"dependencies": deps})}
        )
    if failure == "resource_quarantined":
        from accretion.robotics.runtime_store import RuntimeResource

        changes["resource"] = RuntimeResource(
            id="synthetic-resource",
            workspace_id=i.lab.workspace,
            project_id=i.lab.project,
            host_principal_id=i.lab.host.principal_id,
            host_instance_id="test",
            quarantined=True,
        )
    before = await snapshot(i)
    with pytest.raises(RoboticsError):
        await i.check(update_check=changes)
    assert await snapshot(i) == before


async def test_expiry_after_final_write_rolls_back_inventory_event_and_ledger(
    inventory: InventoryLab, monkeypatch: pytest.MonkeyPatch
) -> None:
    i = inventory
    before = await snapshot(i)
    original_now = RuntimeTransaction.now
    calls = 0

    async def expired_after_writes(tx: RuntimeTransaction) -> datetime:
        nonlocal calls
        calls += 1
        actual = await original_now(tx)
        return actual if calls == 1 else i.policy_spec.valid_until + timedelta(seconds=1)

    monkeypatch.setattr(RuntimeTransaction, "now", expired_after_writes)
    with rejected(Code.CAPABILITY_DENIED):
        await i.inventory.install_policy(i.lab.scope(i.lab.human), spec=i.policy_spec)
    assert calls == 2
    monkeypatch.setattr(RuntimeTransaction, "now", original_now)
    assert await snapshot(i) == before


async def test_expiry_after_later_host_await_rolls_back_bound_run(
    inventory: InventoryLab, monkeypatch: pytest.MonkeyPatch
) -> None:
    i = inventory
    await i.install()
    i.lab.authority.policy = InventoryPolicyAuthority()
    # A test-controlled later collaborator advances the authoritative test clock.
    passed_host = False

    class LateHost:
        async def check(self, tx: RuntimeTransaction, check: AuthorityCheck) -> None:
            nonlocal passed_host
            passed_host = True

    i.lab.authority.host = LateHost()
    original_now = RuntimeTransaction.now

    async def late_now(tx: RuntimeTransaction) -> datetime:
        now = await original_now(tx)
        return i.policy_spec.valid_until + timedelta(seconds=1) if passed_host else now

    monkeypatch.setattr(RuntimeTransaction, "now", late_now)
    before = await snapshot(i)
    with rejected(Code.CAPABILITY_DENIED):
        await i.lab.bind()
    monkeypatch.setattr(RuntimeTransaction, "now", original_now)
    assert passed_host and await snapshot(i) == before
    assert await i.lab.state.get_run(i.lab.run.run_id) is None


async def test_revocation_serializes_with_authority_read(inventory: InventoryLab) -> None:
    i = inventory
    await i.bind()
    assert i.grant
    entered, release, revoke_started = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def held_check() -> None:
        ep = await i.lab.episode()
        async with i.lab.authority.store.transaction(i.lab.project) as tx:
            await InventoryPolicyAuthority().check(
                tx,
                AuthorityCheck(
                    operation="OBSERVE",
                    actor_id=i.lab.orchestrator.principal_id,
                    now=datetime.now(UTC),
                    episode=ep,
                ),
            )
            entered.set()
            await release.wait()

    async def revoke() -> None:
        revoke_started.set()
        await i.inventory.revoke_policy(
            i.lab.scope(i.lab.human, i.grant.revision), grant_id=i.grant.id
        )

    first = asyncio.create_task(held_check())
    await asyncio.wait_for(entered.wait(), 5)
    second = asyncio.create_task(revoke())
    await revoke_started.wait()
    await asyncio.sleep(0.03)
    assert not second.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 5)
    with rejected(Code.CAPABILITY_DENIED):
        await i.check()
