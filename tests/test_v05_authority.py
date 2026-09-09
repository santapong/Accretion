"""Synthetic authority/storage witnesses only; no simulator or real approval issuance."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update
from test_v05_safety import case as safety_case
from test_v05_safety import change

from accretion.contracts import (
    Principal,
    PrincipalRef,
    PrincipalStatus,
    PrincipalType,
    Project,
    Provider,
    RiskLevel,
    Run,
    RunState,
    Task,
    TaskEnvelope,
    TaskType,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.contracts.canonical import canonical_json
from accretion.contracts.robotics import (
    SimulationEpisodeApproval,
    SimulationPreflightReceipt,
    SimulationRunBinding,
    TrustedSafetyKey,
)
from accretion.ids import new_id
from accretion.persistence.database import create_engine, create_session_factory
from accretion.persistence.models import RoboticsContractRow, SimulationLeaseRow
from accretion.persistence.store import MemoryStore, PostgresStore
from accretion.robotics.authority import AuthorityScope, SimulationAuthority
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.observations import ObservationBatch, ObservationSample
from accretion.robotics.protocol import (
    ExecuteRequest,
    ExecuteResult,
    IntentRecord,
    ObservationResult,
    ObserveRequest,
    PreparedRecord,
    ProtocolRequest,
    ProtocolResponse,
    ResetRequest,
    SafetyRecord,
    SuccessOutcome,
)
from accretion.robotics.runtime_store import (
    DispatchReservation,
    EpisodeSetup,
    LiveState,
    RuntimeApproval,
    RuntimeBinding,
    RuntimeEpisode,
    RuntimeLease,
    RuntimeResource,
    RuntimeTransaction,
    runtime_store_for,
)

ROOT = Path(__file__).resolve().parents[1]
POSTGRES_URL = os.getenv("ACCRETION_TEST_POSTGRES_URL")


def fixture(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "tests/fixtures/contracts/v0.5" / name / "minimal.json").read_text())


@contextmanager
def rejected(code: Code):
    with pytest.raises(RoboticsError) as captured:
        yield
    assert captured.value.code is code


class SyntheticTrust:
    """Explicit test-only collaborator; never installed in production composition."""

    def __init__(self) -> None:
        self.denied: str | None = None
        self.calls: list[str] = []
        self.entered: asyncio.Event | None = None
        self.resume: asyncio.Event | None = None
        self.key_map: dict[str, TrustedSafetyKey] = {}

    async def check(self, tx: Any, check: Any) -> None:
        self.calls.append(check.operation)
        if self.entered and self.resume:
            self.entered.set()
            await self.resume.wait()
        if self.denied in {"ALL", check.operation}:
            raise RoboticsError(Code.CAPABILITY_DENIED)

    async def keys(self, tx: Any, check: Any) -> dict[str, TrustedSafetyKey]:
        return self.key_map

    async def verify(self, tx: Any, resource: Any, lease: Any) -> None:
        if self.denied == "CLEANUP":
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE)


@dataclass
class Lab:
    state: Any
    authority: SimulationAuthority
    trust: SyntheticTrust
    workspace: str
    project: str
    human: Principal
    orchestrator: Principal
    host: Principal
    adapter: Principal
    evaluator: Principal
    case: Any
    task: Task
    run: Run
    binding: SimulationRunBinding
    setup: EpisodeSetup
    ep: RuntimeEpisode | None = None
    lease: RuntimeLease | None = None
    approval: SimulationEpisodeApproval | None = None
    lease_lifetime_seconds: int = 300
    heartbeat_timeout_seconds: int = 300
    preflight_lifetime_seconds: int | None = None
    approval_lifetime_seconds: int | None = None

    def scope(
        self, actor: Principal | None = None, revision: int | None = None, key: str | None = None
    ) -> AuthorityScope:
        return AuthorityScope(
            actor_id=(actor or self.orchestrator).principal_id,
            workspace_id=self.workspace,
            project_id=self.project,
            expected_revision=revision,
            idempotency_key=key or new_id("principal"),
        )

    async def episode(self) -> RuntimeEpisode:
        return await self.authority.get_episode(self.scope(), self.binding.episode_id)

    async def bind(self) -> RuntimeEpisode:
        self.ep = await self.authority.create_bound_run(
            self.scope(),
            task=self.task,
            run=self.run,
            binding_original=canonical_json(self.binding).decode(),
            setup=self.setup,
        )
        return self.ep

    async def acquire(self) -> RuntimeLease:
        ep = self.ep or await self.bind()
        resource = RuntimeResource(
            id=new_id("principal"),
            workspace_id=self.workspace,
            project_id=self.project,
            host_principal_id=self.host.principal_id,
            host_instance_id="synthetic-host-instance",
        )
        await self.authority.bootstrap_resource(self.scope(), resource=resource)
        self.lease = await self.authority.acquire_lease(
            self.scope(revision=ep.revision),
            episode_id=ep.id,
            resource_id=resource.id,
            lifetime_seconds=self.lease_lifetime_seconds,
            heartbeat_timeout_seconds=self.heartbeat_timeout_seconds,
        )
        return self.lease

    def batch(self, *, sim_time: int = 0, sequence: int = 0) -> ObservationBatch:
        assert self.lease
        field = fixture("ObservationSpec")["required"][0]
        field["shape"] = [1]
        sample = ObservationSample(
            field=field,
            sequence=sequence,
            sim_time_ns=sim_time,
            artifact=fixture("EpisodeRecord")["trajectory_ref"],
        )
        return ObservationBatch(
            episode_id=self.binding.episode_id,
            lease={"lease_id": self.lease.id, "generation": self.lease.generation},
            sequence=sequence,
            sim_time_ns=sim_time,
            frame_transform_digest=self.case.context.state.frame_transform_digest,
            samples=(sample,),
        )

    def live(self, batch: ObservationBatch, *, sequence: int = 0, budget: Any = None) -> LiveState:
        return LiveState(
            state=batch.state_binding(),
            sequence=sequence,
            budget=budget or self.case.context.budget,
            phase="APPROACH",
            physics_step_ns=1_000_000_000,
            last_action_sim_time_ns=batch.sim_time_ns,
            episode_start_sim_time_ns=0,
            joint_positions_rad=[0.0],
            joint_velocities_rad_s=[0.0],
            joint_accelerations_rad_s2=[0.0],
            gripper_opening_m=0.04,
        )

    async def preflight(self) -> RuntimeEpisode:
        lease = self.lease or await self.acquire()
        ep = await self.episode()
        values = fixture("SimulationPreflightReceipt")
        values.pop("content_hash")
        values.update(
            contract_id=new_id("simulation_preflight"),
            workspace_id=self.workspace,
            project_id=self.project,
            created_at=datetime.now(UTC),
            created_by=PrincipalRef(principal_id=self.orchestrator.principal_id, status="ACTIVE"),
            episode_id=ep.id,
            experiment_contract_hash=self.setup.experiment_contract_hash,
            environment_snapshot_hash=self.setup.environment_snapshot_hash,
            adapter_manifest_hash=self.setup.adapter_manifest_hash,
            safety_envelope_hash=self.setup.safety_envelope_hash,
            verification_spec_hash=self.setup.verification_spec_hash,
            lease={"lease_id": lease.id, "generation": lease.generation},
            seed=self.setup.seed,
            randomization_sample_hash=self.setup.randomization_sample_hash,
            result="PASS",
            valid_until=min(
                lease.expires_at,
                datetime.now(UTC)
                + timedelta(seconds=self.preflight_lifetime_seconds or self.lease_lifetime_seconds),
            ),
        )
        for check in values["checks"]:
            check.update(passed=True, reason_code="SYNTHETIC_TRUST_CONTROL")
        preflight = SimulationPreflightReceipt.model_validate(values)
        return await self.authority.record_preflight(
            self.scope(revision=ep.revision),
            episode_id=ep.id,
            original_json=canonical_json(preflight).decode(),
            live=self.live(self.batch()),
        )

    async def approve(self) -> RuntimeApproval:
        ep = await self.preflight()
        assert self.lease
        async with self.authority.store.transaction(self.project) as tx:
            preflight = await self.authority._preflight(tx, self.scope(), ep, self.lease)
        self.approval = change(
            self.case.approval,
            contract_id=new_id("simulation_episode_approval"),
            created_at=datetime.now(UTC),
            expires_at=min(
                self.lease.expires_at,
                preflight.valid_until,
                datetime.now(UTC)
                + timedelta(seconds=self.approval_lifetime_seconds or self.lease_lifetime_seconds),
            ),
            pins=self.authority._pins(ep, self.lease, preflight),
        )
        return await self.authority.approve_episode(
            self.scope(self.human, ep.revision),
            episode_id=ep.id,
            original_json=canonical_json(self.approval).decode(),
        )

    async def admitted(self) -> RuntimeEpisode:
        approval = await self.approve()
        ep = await self.episode()
        return await self.authority.consume_approval(
            self.scope(revision=ep.revision), episode_id=ep.id, approval_id=approval.id
        )

    async def running(self) -> RuntimeEpisode:
        ep = await self.admitted()
        assert self.approval
        pins = self.authority.execution_pins(ep, self.approval)
        request = ProtocolRequest.create(
            request_id="synthetic-reset",
            sequence=0,
            scope=pins.command_scope(),
            payload=ResetRequest(
                seed=self.setup.seed, randomization_sample_hash=self.setup.randomization_sample_hash
            ),
        )
        admission = await self.authority.reserve_dispatch(
            self.scope(revision=ep.revision), episode_id=ep.id, request=request, pins=pins
        )
        row = admission.reservation
        assert row and admission.fresh
        batch = self.batch()
        response = ProtocolResponse.create(
            request, SuccessOutcome(payload=ObservationResult(op="RESET", observation=batch))
        )
        await self.authority.finish_dispatch(
            self.scope(revision=row.revision),
            episode_id=ep.id,
            reservation_id=row.id,
            response=response,
            live=self.live(batch),
        )
        return await self.episode()

    async def execution(self) -> tuple[RuntimeEpisode, ProtocolRequest, Any]:
        ep = await self.running()
        assert self.lease and self.approval and ep.live
        c = self.case
        c.intent = change(c.intent, state=ep.live.state)
        c.prepared = change(
            c.prepared,
            state=ep.live.state,
            lease=self.approval.pins.lease,
            action_intent_hash=c.intent.content_hash,
        )
        c.approval = self.approval
        c.context = self.authority.safety_context(
            ep,
            self.lease,
            self.approval,
            receipt_id=new_id("safety_decision"),
            now=datetime.now(UTC),
        )
        issuance = c.issuance()
        assert issuance.receipt.decision.decision == "ALLOW"
        await self.authority.record_safety(
            self.scope(self.evaluator, ep.revision), episode_id=ep.id, issuance=issuance
        )
        ep = await self.episode()
        pins = self.authority.execution_pins(ep, self.approval)
        request = ProtocolRequest.create(
            request_id="synthetic-execute",
            sequence=1,
            scope=pins.command_scope(),
            payload=ExecuteRequest(
                intent=IntentRecord.from_contract(c.intent),
                prepared=PreparedRecord.from_contract(c.prepared),
                safety=SafetyRecord.from_contract(issuance.receipt),
            ),
        )
        return ep, request, pins


@pytest.fixture(
    params=[
        "memory",
        pytest.param(
            "postgres",
            marks=[
                pytest.mark.integration,
                pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL URL absent"),
            ],
        ),
    ]
)
async def lab(request: Any, tmp_path: Path) -> Any:
    engine = create_engine(str(POSTGRES_URL)) if request.param == "postgres" else None
    state = PostgresStore(create_session_factory(engine)) if engine else MemoryStore()
    workspace, project = new_id("workspace_entity"), new_id("project")
    await state.upsert_workspace(
        WorkspaceEntity(workspace_id=workspace, name="Synthetic authority")
    )
    await state.create_project(
        Project(project_id=project, name="Synthetic authority", repository_path=tmp_path)
    )
    people = []
    for n in range(5):
        person = await state.upsert_principal(
            Principal(
                principal_id=new_id("principal"),
                issuer="authority-test.invalid",
                subject=new_id("principal"),
                type=PrincipalType.HUMAN if n == 0 else PrincipalType.SERVICE,
            )
        )
        await state.upsert_workspace_membership(
            WorkspaceMembership(
                membership_id=new_id("workspace_membership"),
                workspace_id=workspace,
                principal_id=person.principal_id,
                role=WorkspaceRole.OWNER if n == 0 else WorkspaceRole.SERVICE,
            )
        )
        people.append(person)
    human, orc, host, adapter, evaluator = people
    trust = SyntheticTrust()
    store = runtime_store_for(state)
    await store.registry.bootstrap_bind_project(workspace_id=workspace, project_id=project)
    authority = SimulationAuthority(
        store, policy=trust, host=trust, conformance=trust, safety_keys=trust, cleanup=trust
    )
    c = safety_case.__wrapped__()
    episode_id = new_id("simulation_episode")
    for field in ["descriptor", "envelope", "intent", "prepared", "approval"]:
        value = getattr(c, field)
        actor = adapter if field == "prepared" else human
        updates = dict(
            workspace_id=workspace,
            project_id=project,
            created_by=PrincipalRef(principal_id=actor.principal_id, status="ACTIVE"),
        )
        if field in {"intent", "prepared"}:
            updates["episode_id"] = episode_id
        if field == "approval":
            updates["approved_by"] = PrincipalRef(principal_id=human.principal_id, status="ACTIVE")
        setattr(c, field, change(value, **updates))
    c.rebind()
    c.signer = replace(
        c.signer,
        principal=PrincipalRef(principal_id=evaluator.principal_id, status="ACTIVE"),
        trusted_key=TrustedSafetyKey(
            principal_id=evaluator.principal_id,
            public_key=c.signer.private_key.public_key().public_bytes_raw(),
        ),
    )
    trust.key_map = {c.signer.key_id: c.signer.trusted_key}
    setup = EpisodeSetup(
        **{
            k: getattr(c.approval.pins, k)
            for k in [
                "experiment_contract_hash",
                "environment_snapshot_hash",
                "adapter_manifest_hash",
                "safety_envelope_hash",
                "verification_spec_hash",
                "seed",
                "randomization_sample_hash",
            ]
        },
        dependencies=c.context.closure,
        descriptor_original=canonical_json(c.descriptor).decode(),
        adapter_principal_id=adapter.principal_id,
        evaluator_principal_id=evaluator.principal_id,
        evaluator=c.signer.evaluator,
        policy_ref=c.approval.policy_ref,
    )
    task = Task(
        envelope=TaskEnvelope(
            task_id=new_id("task"),
            project_id=project,
            objective="Synthetic authority transaction witness",
            task_type=TaskType.EXPERIMENT,
            risk_level=RiskLevel.HIGH,
        )
    )
    run = Run(
        run_id=new_id("run"),
        task_id=task.envelope.task_id,
        project_id=project,
        provider=Provider.DETERMINISTIC,
        state=RunState.PENDING,
        principal_id=human.principal_id,
    )
    binding = SimulationRunBinding(
        contract_id=new_id("simulation_run_binding"),
        workspace_id=workspace,
        project_id=project,
        created_by=PrincipalRef(principal_id=orc.principal_id, status="ACTIVE"),
        run_id=run.run_id,
        episode_id=episode_id,
        experiment_contract_hash=setup.experiment_contract_hash,
        orchestrator_principal=PrincipalRef(principal_id=orc.principal_id, status="ACTIVE"),
    )
    try:
        yield Lab(
            state,
            authority,
            trust,
            workspace,
            project,
            human,
            orc,
            host,
            adapter,
            evaluator,
            c,
            task,
            run,
            binding,
            setup,
        )
    finally:
        if engine:
            await engine.dispose()


async def test_bound_run_persists_owner_and_lease(lab: Lab) -> None:
    lease = await lab.acquire()
    restarted = SimulationAuthority(
        runtime_store_for(lab.state), policy=lab.trust, host=lab.trust, conformance=lab.trust
    )
    assert (await lab.state.get_run(lab.run.run_id)).principal_id == lab.human.principal_id
    assert await restarted.get_lease(lab.scope(), lease.id) == lease
    assert (await restarted.get_episode(lab.scope(), lab.binding.episode_id)).status == "LEASED"


async def test_complete_synthetic_reservation_and_idempotent_replay(lab: Lab) -> None:
    ep, request, pins = await lab.execution()
    scope = lab.scope(revision=ep.revision, key="exact-execute")
    first = await lab.authority.reserve_dispatch(
        scope, episode_id=ep.id, request=request, pins=pins
    )
    assert first.fresh and first.reservation
    replay = await lab.authority.reserve_dispatch(
        scope, episode_id=ep.id, request=request, pins=pins
    )
    assert not replay.fresh and replay.reservation == first.reservation
    current = await lab.episode()
    assert current.live and current.live.budget.actions == 1
    batch = lab.batch(sim_time=2_000_000_000, sequence=1)
    result = ExecuteResult(
        observation=batch,
        prepared_command_hash=lab.case.prepared.content_hash,
        safety_decision_hash=lab.case.issuance().receipt.content_hash,
    )
    # Stored receipt is exact, including its ID and issuance time.
    result = result.model_copy(
        update={
            "safety_decision_hash": request.payload.safety.for_execution(
                type(lab.case.issuance().receipt)
            ).content_hash
        }
    )
    response = ProtocolResponse.create(request, SuccessOutcome(payload=result))
    completed = await lab.authority.finish_dispatch(
        lab.scope(revision=first.reservation.revision),
        episode_id=ep.id,
        reservation_id=first.reservation.id,
        response=response,
        live=lab.live(batch, sequence=1, budget=current.live.budget),
    )
    assert completed.status == "COMPLETED"
    assert (await lab.episode()).in_flight is None


@pytest.mark.parametrize("missing", ["policy", "host", "conformance"])
async def test_absent_trust_rolls_back_real_task_run_binding(lab: Lab, missing: str) -> None:
    setattr(lab.authority, missing, None)
    with rejected(Code.SIMULATION_UNAVAILABLE):
        await lab.bind()
    assert await lab.state.get_run(lab.run.run_id) is None
    assert await lab.state.get_task(lab.task.envelope.task_id) is None
    async with lab.authority.store.transaction(lab.project) as tx:
        assert await tx.registry.get(lab.binding.contract_id) is None
        assert await tx.get(RuntimeBinding, lab.binding.contract_id) is None


@pytest.mark.parametrize("participant", ["human", "orchestrator", "host", "adapter", "evaluator"])
async def test_current_disabled_identity_cannot_acquire_or_spend(
    lab: Lab, participant: str
) -> None:
    await lab.bind()
    target = getattr(lab, participant)
    await lab.state.upsert_principal(target.model_copy(update={"status": PrincipalStatus.DISABLED}))
    with pytest.raises(RoboticsError):
        await lab.acquire()
    assert (await lab.episode()).status == "BOUND" if participant != "orchestrator" else True


async def test_missing_initiator_no_dummy_or_service_owned_run(lab: Lab) -> None:
    for who in [None, lab.orchestrator.principal_id, lab.host.principal_id]:
        lab.run = lab.run.model_copy(update={"principal_id": who})
        with pytest.raises(RoboticsError):
            await lab.bind()
    assert await lab.state.get_run(lab.run.run_id) is None


async def test_cross_scope_and_no_first_write_project_claim(lab: Lab) -> None:
    other = new_id("workspace_entity")
    bad = lab.scope().model_copy(update={"workspace_id": other})
    with pytest.raises(RoboticsError):
        await lab.authority.create_bound_run(
            bad,
            task=lab.task,
            run=lab.run,
            binding_original=canonical_json(lab.binding).decode(),
            setup=lab.setup,
        )
    assert await lab.state.get_run(lab.run.run_id) is None


async def test_lease_exclusive_race_idempotency_and_stale_revision(lab: Lab) -> None:
    ep = await lab.bind()
    resource = RuntimeResource(
        id=new_id("principal"),
        workspace_id=lab.workspace,
        project_id=lab.project,
        host_principal_id=lab.host.principal_id,
        host_instance_id="synthetic",
    )
    await lab.authority.bootstrap_resource(lab.scope(), resource=resource)
    scope = lab.scope(revision=ep.revision, key="lease-race")

    async def acquire(s: AuthorityScope, lifetime: int = 200) -> RuntimeLease:
        return await lab.authority.acquire_lease(
            s,
            episode_id=ep.id,
            resource_id=resource.id,
            lifetime_seconds=lifetime,
            heartbeat_timeout_seconds=100,
        )

    one, two = await asyncio.gather(acquire(scope), acquire(scope))
    assert one == two and one.generation == 1
    with rejected(Code.IDEMPOTENCY_CONFLICT):
        await acquire(scope, 201)
    with rejected(Code.REVISION_CONFLICT):
        await acquire(lab.scope(revision=ep.revision))
    ep = await lab.episode()
    assert ep.lease_id == one.id


async def test_heartbeat_revocation_and_cleanup_preserve_generation(lab: Lab) -> None:
    lease = await lab.acquire()
    heartbeat = await lab.authority.heartbeat(
        lab.scope(revision=lease.revision), episode_id=lease.episode_id, generation=lease.generation
    )
    assert heartbeat.generation == lease.generation and heartbeat.expires_at == lease.expires_at
    with rejected(Code.LEASE_INVALID):
        await lab.authority.heartbeat(
            lab.scope(revision=heartbeat.revision),
            episode_id=lease.episode_id,
            generation=lease.generation + 1,
        )
    revoked = await lab.authority.end_lease(
        lab.scope(revision=heartbeat.revision),
        episode_id=lease.episode_id,
        generation=lease.generation,
        disposition="REVOKED",
    )
    with rejected(Code.LEASE_INVALID):
        await lab.authority.heartbeat(
            lab.scope(revision=revoked.revision),
            episode_id=lease.episode_id,
            generation=lease.generation,
        )
    async with lab.authority.store.transaction(lab.project) as tx:
        resource = await tx.get(RuntimeResource, lease.resource_id)
    assert resource and resource.quarantined
    lab.trust.denied = "CLEANUP"
    with pytest.raises(RoboticsError):
        await lab.authority.confirm_cleanup(
            lab.scope(lab.host, resource.revision),
            episode_id=lease.episode_id,
            generation=lease.generation,
        )
    lab.trust.denied = None
    cleaned = await lab.authority.confirm_cleanup(
        lab.scope(lab.host, resource.revision),
        episode_id=lease.episode_id,
        generation=lease.generation,
    )
    assert cleaned.generation == 1 and cleaned.current_lease_id is None and not cleaned.quarantined
    assert (await lab.episode()).status == "ABORTED"
    # New identity+real run, not revival of the failed episode, obtains generation2.
    lab.ep = None
    lab.run = lab.run.model_copy(update={"run_id": new_id("run"), "task_id": new_id("task")})
    lab.task = lab.task.model_copy(
        update={"envelope": lab.task.envelope.model_copy(update={"task_id": lab.run.task_id})}
    )
    lab.binding = change(
        lab.binding,
        contract_id=new_id("simulation_run_binding"),
        run_id=lab.run.run_id,
        episode_id=new_id("simulation_episode"),
    )
    ep = await lab.bind()
    next_lease = await lab.authority.acquire_lease(
        lab.scope(revision=ep.revision),
        episode_id=ep.id,
        resource_id=resource.id,
        lifetime_seconds=200,
        heartbeat_timeout_seconds=100,
    )
    assert next_lease.generation == 2
    with rejected(Code.LEASE_INVALID):
        await lab.authority.fence_uncertain(
            lab.scope(),
            episode_id=ep.id,
            lease_binding={"lease_id": lease.id, "generation": lease.generation},
            request_digest="a" * 64,
        )


async def test_actual_expiry_cannot_heartbeat_or_cleanup_without_revocation(lab: Lab) -> None:
    ep = await lab.bind()
    resource = RuntimeResource(
        id=new_id("principal"),
        workspace_id=lab.workspace,
        project_id=lab.project,
        host_principal_id=lab.host.principal_id,
        host_instance_id="synthetic",
    )
    await lab.authority.bootstrap_resource(lab.scope(), resource=resource)
    lease = await lab.authority.acquire_lease(
        lab.scope(revision=ep.revision),
        episode_id=ep.id,
        resource_id=resource.id,
        lifetime_seconds=1,
        heartbeat_timeout_seconds=1,
    )
    await asyncio.sleep(1.05)
    with rejected(Code.LEASE_INVALID):
        await lab.authority.heartbeat(
            lab.scope(revision=lease.revision), episode_id=ep.id, generation=lease.generation
        )
    expired = await lab.authority.end_lease(
        lab.scope(revision=lease.revision),
        episode_id=ep.id,
        generation=lease.generation,
        disposition="EXPIRED",
    )
    assert expired.status == "EXPIRED"


async def test_exact_approval_cannot_be_minted_by_service_or_reused(lab: Lab) -> None:
    row = await lab.approve()
    assert lab.approval
    ep = await lab.episode()
    with rejected(Code.CAPABILITY_DENIED):
        await lab.authority.approve_episode(
            lab.scope(revision=ep.revision),
            episode_id=ep.id,
            original_json=canonical_json(lab.approval).decode(),
        )
    scope = lab.scope(revision=ep.revision, key="approve-once")
    one, two = await asyncio.gather(
        lab.authority.consume_approval(scope, episode_id=ep.id, approval_id=row.id),
        lab.authority.consume_approval(scope, episode_id=ep.id, approval_id=row.id),
    )
    assert one == two
    with pytest.raises(RoboticsError):
        await lab.authority.consume_approval(
            lab.scope(revision=one.revision), episode_id=ep.id, approval_id=row.id
        )


async def test_revoked_approval_blocks_nonmutation_and_execution(lab: Lab) -> None:
    ep, request, pins = await lab.execution()
    assert lab.approval
    async with lab.authority.store.transaction(lab.project) as tx:
        row = await tx.get(RuntimeApproval, lab.approval.contract_id)
    assert row
    await lab.authority.revoke_approval(
        lab.scope(lab.human, row.revision), episode_id=ep.id, approval_id=row.id
    )
    ep = await lab.episode()
    with rejected(Code.APPROVAL_INVALID):
        await lab.authority.reserve_dispatch(
            lab.scope(revision=ep.revision), episode_id=ep.id, request=request, pins=pins
        )
    read = ProtocolRequest.create(
        request_id="read", sequence=5, scope=pins.command_scope(), payload=ObserveRequest()
    )
    with rejected(Code.APPROVAL_INVALID):
        await lab.authority.authorize_read(lab.scope(), episode_id=ep.id, request=read, pins=pins)


async def test_unknown_ack_fences_without_reservation_id_and_keeps_budget(lab: Lab) -> None:
    ep, request, pins = await lab.execution()
    admit = await lab.authority.reserve_dispatch(
        lab.scope(revision=ep.revision), episode_id=ep.id, request=request, pins=pins
    )
    assert admit.reservation
    # Supervisor has no reservation id; only exact original command scope/digest.
    aborted = await lab.authority.fence_uncertain(
        lab.scope(),
        episode_id=ep.id,
        lease_binding=request.scope.lease,
        request_digest=request.request_digest,
    )
    assert aborted.status == "ABORTED" and aborted.live and aborted.live.budget.actions == 1
    async with lab.authority.store.transaction(lab.project) as tx:
        row = await tx.get(DispatchReservation, admit.reservation.id)
        resource = await tx.get(RuntimeResource, lab.lease.resource_id)
    assert row.status == "UNCERTAIN" and resource.quarantined
    with rejected(Code.LEASE_INVALID):
        await lab.authority.reserve_dispatch(
            lab.scope(revision=aborted.revision), episode_id=ep.id, request=request, pins=pins
        )


@pytest.mark.parametrize("drift", ["state", "closure", "phase", "keys", "policy"])
async def test_signed_receipt_requires_current_exact_context_and_trust(
    lab: Lab, drift: str
) -> None:
    ep, request, pins = await lab.execution()
    if drift == "keys":
        lab.trust.key_map = {}
    elif drift == "policy":
        lab.trust.denied = "RESERVE_EXECUTE"
    elif drift == "phase":
        async with lab.authority.store.transaction(lab.project) as tx:
            changed = ep.model_copy(update={"live": ep.live.model_copy(update={"phase": "GRASP"})})
            await tx.put(changed)
    else:
        if drift == "state":
            pins = pins.model_copy(
                update={"state": pins.state.model_copy(update={"observation_digest": "a" * 64})}
            )
        else:
            pins = pins.model_copy(
                update={
                    "dependencies": pins.dependencies.model_copy(
                        update={"controller_digest": "b" * 64}
                    )
                }
            )
    with pytest.raises(RoboticsError):
        await lab.authority.reserve_dispatch(
            lab.scope(revision=ep.revision), episode_id=ep.id, request=request, pins=pins
        )
    assert (await lab.episode()).live.budget.actions == 0


async def test_competing_real_episodes_cannot_share_one_global_resource(lab: Lab) -> None:
    first = await lab.bind()
    run = lab.run.model_copy(update={"run_id": new_id("run"), "task_id": new_id("task")})
    other = replace(
        lab,
        ep=None,
        run=run,
        task=lab.task.model_copy(
            update={"envelope": lab.task.envelope.model_copy(update={"task_id": run.task_id})}
        ),
        binding=change(
            lab.binding,
            contract_id=new_id("simulation_run_binding"),
            run_id=run.run_id,
            episode_id=new_id("simulation_episode"),
        ),
    )
    second = await other.bind()
    resource = RuntimeResource(
        id=new_id("principal"),
        workspace_id=lab.workspace,
        project_id=lab.project,
        host_principal_id=lab.host.principal_id,
        host_instance_id="synthetic-shared-resource",
    )
    await lab.authority.bootstrap_resource(lab.scope(), resource=resource)

    async def acquire(ep: RuntimeEpisode):
        return await lab.authority.acquire_lease(
            lab.scope(revision=ep.revision),
            episode_id=ep.id,
            resource_id=resource.id,
            lifetime_seconds=200,
            heartbeat_timeout_seconds=100,
        )

    results = await asyncio.gather(acquire(first), acquire(second), return_exceptions=True)
    assert sum(isinstance(x, RuntimeLease) for x in results) == 1
    failures = [x for x in results if isinstance(x, RoboticsError)]
    assert len(failures) == 1 and failures[0].code is Code.LEASE_BUSY
    async with lab.authority.store.transaction(lab.project) as tx:
        assert (await tx.get(RuntimeResource, resource.id)).generation == 1


async def test_disabled_initiator_update_serializes_with_authority_commit(lab: Lab) -> None:
    lease = await lab.acquire()
    lab.trust.entered, lab.trust.resume = asyncio.Event(), asyncio.Event()
    pending = asyncio.create_task(
        lab.authority.heartbeat(
            lab.scope(revision=lease.revision),
            episode_id=lease.episode_id,
            generation=lease.generation,
        )
    )
    await asyncio.wait_for(lab.trust.entered.wait(), 2)
    revoke = asyncio.create_task(
        lab.state.upsert_principal(
            lab.human.model_copy(update={"status": PrincipalStatus.DISABLED})
        )
    )
    await asyncio.sleep(0.02)
    assert not revoke.done()
    lab.trust.resume.set()
    latest = await asyncio.wait_for(pending, 2)
    await asyncio.wait_for(revoke, 2)
    with rejected(Code.CAPABILITY_DENIED):
        await lab.authority.heartbeat(
            lab.scope(revision=latest.revision),
            episode_id=lease.episode_id,
            generation=lease.generation,
        )


async def test_current_membership_downgrade_blocks_authority(lab: Lab) -> None:
    lease = await lab.acquire()
    memberships = await lab.state.list_workspace_memberships(lab.workspace, lab.human.principal_id)
    await lab.state.upsert_workspace_membership(
        memberships[0].model_copy(update={"role": WorkspaceRole.VIEWER})
    )
    with rejected(Code.CAPABILITY_DENIED):
        await lab.authority.heartbeat(
            lab.scope(revision=lease.revision),
            episode_id=lease.episode_id,
            generation=lease.generation,
        )


async def test_runtime_index_corruption_is_rejected(lab: Lab) -> None:
    lease = await lab.acquire()
    if isinstance(lab.state, MemoryStore):
        original = lab.state.robotics_runtime_state["RuntimeLease"][lease.id]["generation"]
    else:
        async with lab.state.sessions() as session:
            stored = await session.get(SimulationLeaseRow, lease.id)
            assert stored is not None
            original = stored.generation

    async def write_generation(value: int) -> None:
        if isinstance(lab.state, MemoryStore):
            # A refused UoW restores copied dictionaries; never retain a stale
            # nested dictionary reference across that rollback.
            lab.state.robotics_runtime_state["RuntimeLease"][lease.id]["generation"] = value
            return
        async with lab.state.sessions.begin() as session:
            await session.execute(
                update(SimulationLeaseRow)
                .where(SimulationLeaseRow.id == lease.id)
                .values(generation=value)
            )

    try:
        await write_generation(original + 1)
        with rejected(Code.INVALID_CONTRACT):
            await lab.authority.get_lease(lab.scope(), lease.id)
    finally:
        await write_generation(original)


async def test_original_binding_corruption_cannot_allocate(lab: Lab) -> None:
    await lab.bind()
    raw = json.loads(canonical_json(lab.binding))
    raw["experiment_contract_hash"] = "a" * 64
    corrupted = json.dumps(raw)
    if isinstance(lab.state, MemoryStore):
        original = lab.state.robotics_registry_state["records"][
            lab.binding.contract_id
        ].original_json
    else:
        async with lab.state.sessions() as session:
            stored = await session.get(RoboticsContractRow, lab.binding.contract_id)
            assert stored is not None
            original = stored.original_json

    async def write_original(value: str) -> None:
        if isinstance(lab.state, MemoryStore):
            rows = lab.state.robotics_registry_state["records"]
            rows[lab.binding.contract_id] = replace(
                rows[lab.binding.contract_id], original_json=value
            )
            return
        async with lab.state.sessions.begin() as session:
            await session.execute(
                update(RoboticsContractRow)
                .where(RoboticsContractRow.id == lab.binding.contract_id)
                .values(original_json=value)
            )

    try:
        await write_original(corrupted)
        with rejected(Code.INVALID_CONTRACT):
            await lab.acquire()
    finally:
        # CI runs pytest, acceptance and release gates against one database.
        # Preserve its original writer bytes even when the assertion fails.
        await write_original(original)


async def test_legacy_null_run_owner_remains_unknown(lab: Lab) -> None:
    await lab.state.create_task(lab.task)
    await lab.state.create_run(
        lab.run.model_copy(update={"principal_id": None, "provider": Provider.FAKE})
    )
    loaded = await lab.state.get_run(lab.run.run_id)
    assert loaded.principal_id is None and loaded.provider is Provider.FAKE
    with pytest.raises(RoboticsError):
        await lab.bind()


async def test_nonmutation_authorization_never_reserves_or_advances(lab: Lab) -> None:
    ep = await lab.running()
    pins = lab.authority.execution_pins(ep, lab.approval)
    request = ProtocolRequest.create(
        request_id="observe", sequence=1, scope=pins.command_scope(), payload=ObserveRequest()
    )
    grant = await lab.authority.authorize_read(
        lab.scope(), episode_id=ep.id, request=request, pins=pins
    )
    assert grant.fresh and grant.reservation is None
    assert await lab.episode() == ep
    lab.trust.denied = "OBSERVE"
    with rejected(Code.CAPABILITY_DENIED):
        await lab.authority.authorize_read(
            lab.scope(), episode_id=ep.id, request=request, pins=pins
        )


async def test_expiry_clock_is_read_after_waiting_for_commit_locks(lab: Lab) -> None:
    ep = await lab.bind()
    resource = RuntimeResource(
        id=new_id("principal"),
        workspace_id=lab.workspace,
        project_id=lab.project,
        host_principal_id=lab.host.principal_id,
        host_instance_id="synthetic-clock",
    )
    await lab.authority.bootstrap_resource(lab.scope(), resource=resource)
    lease = await lab.authority.acquire_lease(
        lab.scope(revision=ep.revision),
        episode_id=ep.id,
        resource_id=resource.id,
        lifetime_seconds=1,
        heartbeat_timeout_seconds=1,
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold():
        async with lab.authority.store.transaction(lab.project):
            entered.set()
            await release.wait()

    held = asyncio.create_task(hold())
    await entered.wait()
    heartbeat = asyncio.create_task(
        lab.authority.heartbeat(
            lab.scope(revision=lease.revision), episode_id=ep.id, generation=lease.generation
        )
    )
    await asyncio.sleep(1.05)
    release.set()
    await held
    with rejected(Code.LEASE_INVALID):
        await heartbeat


async def test_internal_run_ownership_and_bounded_restart_listing(lab: Lab) -> None:
    assert await lab.authority.store.lookup_run_owner(lab.run.run_id) is None
    ep = await lab.bind()
    binding = await lab.authority.store.lookup_run_owner(lab.run.run_id)
    assert (
        binding and binding.episode_id == ep.id and binding.initiator_id == lab.human.principal_id
    )
    assert await lab.authority.list_episodes(lab.scope(), limit=1) == [ep]
    assert await lab.authority.list_episodes(lab.scope(), after=ep.id) == []
    with rejected(Code.INVALID_REQUEST):
        await lab.authority.list_episodes(lab.scope(), limit=101)


async def test_release_is_not_a_verification_or_completion_claim(lab: Lab) -> None:
    ep = await lab.running()
    lease = await lab.authority.get_lease(lab.scope(), lab.lease.id)
    released = await lab.authority.end_lease(
        lab.scope(revision=lease.revision),
        episode_id=ep.id,
        generation=lease.generation,
        disposition="RELEASED",
    )
    assert released.status == "RELEASED"
    assert (await lab.episode()).status == "ABORTED"


async def test_safety_context_requires_current_owner_and_independent_evaluator(lab: Lab) -> None:
    ep = await lab.running()
    receipt_id = new_id("safety_decision")
    context = await lab.authority.current_safety_context(
        lab.scope(lab.evaluator), episode_id=ep.id, receipt_id=receipt_id
    )
    assert context.state == ep.live.state and context.receipt_id == receipt_id
    with rejected(Code.CAPABILITY_DENIED):
        await lab.authority.current_safety_context(
            lab.scope(lab.adapter), episode_id=ep.id, receipt_id=receipt_id
        )


async def dispatch_candidate(
    lab: Lab, operation: str
) -> tuple[RuntimeEpisode, ProtocolRequest, Any]:
    if operation == "EXECUTE":
        return await lab.execution()
    ep = await lab.admitted()
    assert lab.approval
    pins = lab.authority.execution_pins(ep, lab.approval)
    return (
        ep,
        ProtocolRequest.create(
            request_id="synthetic-late-reset",
            sequence=0,
            scope=pins.command_scope(),
            payload=ResetRequest(
                seed=lab.setup.seed,
                randomization_sample_hash=lab.setup.randomization_sample_hash,
            ),
        ),
        pins,
    )


async def assert_dispatch_rollback(
    lab: Lab,
    scope: AuthorityScope,
    ep: RuntimeEpisode,
    request: ProtocolRequest,
    pins: Any,
    code: Code,
) -> None:
    async with lab.authority.store.transaction(lab.project) as tx:
        before_events = await tx.registry.events(ep.binding_id, 0, 100)
    with rejected(code):
        await lab.authority.reserve_dispatch(scope, episode_id=ep.id, request=request, pins=pins)
    assert await lab.episode() == ep  # Includes budget, revision, flight and event sequence.
    async with lab.authority.store.transaction(lab.project) as tx:
        assert await tx.get(DispatchReservation, request.request_digest) is None
        assert await tx.registry.events(ep.binding_id, 0, 100) == before_events
        assert (
            await tx.registry.ledger(lab.authority._ledger_scope(scope, "dispatch", ep.id)) is None
        )


@pytest.mark.parametrize(
    ("operation", "late_boundary"),
    [("EXECUTE", "trusted_keys"), ("EXECUTE", "ledger"), ("RESET", "ledger")],
)
@pytest.mark.parametrize(
    ("deadline", "code"),
    [
        ("hard_expiry", Code.LEASE_INVALID),
        ("heartbeat", Code.LEASE_INVALID),
        ("approval", Code.APPROVAL_INVALID),
        ("preflight", Code.PREFLIGHT_FAILED),
        ("clock_regression", Code.SAFETY_DENIED),
    ],
)
async def test_dispatch_rechecks_deadlines_after_final_await_and_rolls_back(
    lab: Lab,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    late_boundary: str,
    deadline: str,
    code: Code,
) -> None:
    if operation == "RESET" and deadline == "clock_regression":
        pytest.skip("RESET has no signed safety issuance clock")
    if deadline == "heartbeat":
        lab.heartbeat_timeout_seconds = 100
    elif deadline == "approval":
        lab.approval_lifetime_seconds = 100
    elif deadline == "preflight":
        lab.preflight_lifetime_seconds = 100
    ep, request, pins = await dispatch_candidate(lab, operation)
    assert lab.lease and lab.approval
    async with lab.authority.store.transaction(lab.project) as tx:
        preflight = await lab.authority._preflight(tx, lab.scope(), ep, lab.lease)
    target = {
        "hard_expiry": lab.lease.expires_at,
        "heartbeat": lab.lease.heartbeat_deadline,
        "approval": lab.approval.expires_at,
        "preflight": preflight.valid_until,
        "clock_regression": lab.case.context.now - timedelta(microseconds=1),
    }[deadline]
    # Inject only the wall clock after a successful awaited operation. PostgreSQL
    # still runs its real clock query, locks, writes and rollback; no simulator runs.
    clock: list[datetime | None] = [None]
    original_now = RuntimeTransaction.now

    async def now(tx: RuntimeTransaction) -> datetime:
        real_now = await original_now(tx)
        return clock[0] or real_now

    monkeypatch.setattr(RuntimeTransaction, "now", now)
    original_keys = lab.trust.keys
    original_remember = lab.authority._remember

    async def late_keys(tx: Any, check: Any) -> dict[str, TrustedSafetyKey]:
        keys = await original_keys(tx, check)
        clock[0] = target
        return keys

    async def late_remember(*args: Any, **kwargs: Any) -> None:
        await original_remember(*args, **kwargs)
        clock[0] = target

    if late_boundary == "trusted_keys":
        monkeypatch.setattr(lab.trust, "keys", late_keys)
    else:
        monkeypatch.setattr(lab.authority, "_remember", late_remember)
    await assert_dispatch_rollback(lab, lab.scope(revision=ep.revision), ep, request, pins, code)
    assert clock[0] == target


async def test_dispatch_refuses_actual_expiry_during_trusted_key_lookup(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    lab.lease_lifetime_seconds = lab.heartbeat_timeout_seconds = 5
    ep, request, pins = await lab.execution()
    assert lab.lease
    deadline = lab.lease.expires_at
    original_keys = lab.trust.keys

    async def slow_keys(tx: Any, check: Any) -> dict[str, TrustedSafetyKey]:
        keys = await original_keys(tx, check)
        await asyncio.sleep(max(0.0, (deadline - datetime.now(UTC)).total_seconds()) + 0.02)
        return keys

    monkeypatch.setattr(lab.trust, "keys", slow_keys)
    await assert_dispatch_rollback(
        lab, lab.scope(revision=ep.revision), ep, request, pins, Code.LEASE_INVALID
    )


async def test_dispatch_allows_fresh_context_after_trusted_key_lookup(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    ep, request, pins = await lab.execution()
    original_keys = lab.trust.keys

    async def current_keys(tx: Any, check: Any) -> dict[str, TrustedSafetyKey]:
        await asyncio.sleep(0)
        return await original_keys(tx, check)

    monkeypatch.setattr(lab.trust, "keys", current_keys)
    admission = await lab.authority.reserve_dispatch(
        lab.scope(revision=ep.revision), episode_id=ep.id, request=request, pins=pins
    )
    assert admission.fresh and admission.reservation
    current = await lab.episode()
    assert current.in_flight == admission.reservation.id
    assert current.live and ep.live and current.live.budget.actions == ep.live.budget.actions + 1
