"""Durable simulation authority. No host launch, transport or physics is performed.

All collaborators are trusted backend configuration, never caller-selected. They
must validate current policy/host/conformance pins using this same transaction.
No missing collaborator, cached PASS label or manifest capability is a permit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from secrets import token_hex
from typing import Any, Literal, Protocol

from pydantic import ConfigDict, Field, TypeAdapter
from sqlalchemy.exc import IntegrityError

from accretion.contracts import (
    Principal,
    PrincipalRef,
    PrincipalStatus,
    Provider,
    RiskLevel,
    Run,
    RunState,
    StrictModel,
    Task,
    TaskType,
)
from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics import (
    ActionIntent,
    CanonicalWriterEnvelope,
    EmbodimentDescriptor,
    PreparedCommand,
    SafetyDecisionReceipt,
    SimulationDomainEvent,
    SimulationEpisodeApproval,
    SimulationLease,
    SimulationPreflightReceipt,
    SimulationRunBinding,
    TrustedSafetyKey,
    verify_safety_signature,
)
from accretion.contracts.robotics.models import RoboticsContract
from accretion.contracts.robotics.values import Digest, EpisodePins, Identifier, LeaseBinding
from accretion.ids import new_id
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.protocol import (
    ErrorOutcome,
    ExecuteRequest,
    ExecuteResult,
    ObservationResult,
    PrepareRequest,
    ProtocolRequest,
    ProtocolResponse,
    ResetRequest,
    TerminateRequest,
    TerminateResult,
    parse_message,
    validate_response_binding,
)
from accretion.robotics.runtime_store import (
    DispatchReservation,
    EpisodeSetup,
    LiveState,
    RuntimeApproval,
    RuntimeBinding,
    RuntimeDTO,
    RuntimeEpisode,
    RuntimeIssuance,
    RuntimeLease,
    RuntimeResource,
    RuntimeStore,
    RuntimeTransaction,
)
from accretion.robotics.safety import SafetyContext, SafetyEvaluation
from accretion.robotics.sdk import ExecutionPins, validate_execution
from accretion.robotics.store import IdempotencyScope, LedgerRecord, RegistryRecord, StoredEvent


class AuthorityScope(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    actor_id: Identifier
    workspace_id: Identifier
    project_id: Identifier
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_revision: int | None = Field(default=None, ge=1, strict=True)


class AuthorityCheck(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation: str
    actor_id: str
    now: datetime
    episode: RuntimeEpisode
    lease: RuntimeLease | None = None
    resource: RuntimeResource | None = None
    request_json: str | None = None
    response_json: str | None = None
    live: LiveState | None = None


@dataclass(frozen=True)
class AuthorityAdmission:
    # Only fresh=True may authorize a new supervisor send. A replayed RESERVED
    # response is historical evidence, never a reusable execution token.
    fresh: bool
    reservation: DispatchReservation | None = None


class CurrentAuthority(Protocol):
    async def check(self, tx: RuntimeTransaction, check: AuthorityCheck) -> None:
        """Recheck current configured trust/pins under commit locks; otherwise raise.

        Policy checks task/capability restrictions and current grants without
        changing SIMULATION→HIGH. Host checks admitted resource/instance/isolation
        pins. Conformance resolves the exact original report and full closure.
        These are separate mandatory injected implementations of this interface.
        No host, artifact or provider execution may occur while holding locks.
        """
        ...


class SafetyKeyAuthority(Protocol):
    async def keys(
        self, tx: RuntimeTransaction, check: AuthorityCheck
    ) -> Mapping[str, TrustedSafetyKey]:
        """Current admitted evaluator keys; no caller key or permissive fallback."""
        ...


class CleanupAuthority(Protocol):
    async def verify(
        self, tx: RuntimeTransaction, resource: RuntimeResource, lease: RuntimeLease
    ) -> None:
        """Verify exact owning host instance/process-tree cleanup before reuse."""
        ...


def require(condition: bool, code: Code = Code.EPISODE_STATE_CONFLICT) -> None:
    if not condition:
        raise RoboticsError(code)


def copy_update[C: RuntimeDTO](value: C, **updates: Any) -> C:
    body = value.model_dump(mode="python")
    body.update(updates)
    body["revision"] += 1
    return type(value).model_validate(body)


def original[C: RoboticsContract](raw: str, model: type[C]) -> C:
    require(len(raw.encode()) <= 1_048_576, Code.PAYLOAD_TOO_LARGE)
    try:
        return CanonicalWriterEnvelope(raw).for_execution(model)
    except (ValueError, TypeError) as exc:
        raise RoboticsError(Code.INVALID_CONTRACT) from exc


class SimulationAuthority:
    def __init__(
        self,
        store: RuntimeStore,
        *,
        policy: CurrentAuthority | None = None,
        host: CurrentAuthority | None = None,
        conformance: CurrentAuthority | None = None,
        safety_keys: SafetyKeyAuthority | None = None,
        cleanup: CleanupAuthority | None = None,
    ):
        self.store, self.policy, self.host, self.conformance = store, policy, host, conformance
        self.safety_keys, self.cleanup = safety_keys, cleanup

    @asynccontextmanager
    async def _transaction(self, scope: AuthorityScope) -> AsyncIterator[RuntimeTransaction]:
        scope = AuthorityScope.model_validate_json(canonical_json(scope))
        try:
            async with self.store.transaction(scope.project_id) as tx:
                yield tx
        except IntegrityError as exc:
            raise RoboticsError(Code.CONTRACT_CONFLICT) from exc

    async def _actor(
        self,
        tx: RuntimeTransaction,
        scope: AuthorityScope,
        *,
        human: bool = False,
        service: bool = False,
    ) -> Principal:
        return await tx.registry.authorize(
            scope.actor_id, scope.workspace_id, scope.project_id, write=human, service=service
        )

    async def _get[C: RuntimeDTO](
        self, tx: RuntimeTransaction, scope: AuthorityScope, model: type[C], identity: str
    ) -> C:
        record = await tx.get(model, identity)
        require(record is not None, Code.RESOURCE_NOT_FOUND)
        assert record is not None
        require(
            (record.workspace_id, record.project_id) == (scope.workspace_id, scope.project_id),
            Code.RESOURCE_NOT_FOUND,
        )
        return record

    async def _contract[C: RoboticsContract](
        self, tx: RuntimeTransaction, scope: AuthorityScope, identity: str, model: type[C]
    ) -> C:
        row = await tx.registry.get(identity)
        require(row is not None, Code.RESOURCE_NOT_FOUND)
        assert row is not None
        parsed = original(row.original_json, model)
        expected = self._record(row.original_json, parsed)
        require(
            all(
                getattr(row, k) == getattr(expected, k)
                for k in RegistryRecord.__dataclass_fields__
                if k != "revision"
            ),
            Code.INVALID_CONTRACT,
        )
        require(
            (parsed.workspace_id, parsed.project_id) == (scope.workspace_id, scope.project_id),
            Code.RESOURCE_NOT_FOUND,
        )
        return parsed

    @staticmethod
    def _record(raw: str, parsed: RoboticsContract) -> RegistryRecord:
        return RegistryRecord(
            id=parsed.contract_id,
            workspace_id=parsed.workspace_id,
            project_id=str(parsed.project_id),
            contract_type=parsed.contract_type,
            logical_name=parsed.contract_id,
            version=parsed.schema_version,
            schema_version=parsed.schema_version,
            content_hash=parsed.content_hash,
            original_json=raw,
            created_by=parsed.created_by.principal_id,
            created_at=parsed.created_at,
        )

    async def _retain(
        self, tx: RuntimeTransaction, scope: AuthorityScope, raw: str, parsed: RoboticsContract
    ) -> None:
        require(
            (parsed.workspace_id, parsed.project_id) == (scope.workspace_id, scope.project_id),
            Code.RESOURCE_NOT_FOUND,
        )
        await tx.registry.insert(self._record(raw, parsed))

    async def _owner(
        self, tx: RuntimeTransaction, scope: AuthorityScope, ep: RuntimeEpisode
    ) -> RuntimeBinding:
        binding = await self._get(tx, scope, RuntimeBinding, ep.binding_id)
        seal = await self._contract(tx, scope, binding.contract_id, SimulationRunBinding)
        require(
            (
                seal.run_id,
                seal.episode_id,
                seal.experiment_contract_hash,
                seal.orchestrator_principal.principal_id,
            )
            == (ep.run_id, ep.id, ep.setup.experiment_contract_hash, binding.orchestrator_id),
            Code.INVALID_CONTRACT,
        )
        require(binding.initiator_id != binding.orchestrator_id, Code.CAPABILITY_DENIED)
        for identity in sorted({binding.initiator_id, binding.orchestrator_id}):
            await tx.registry.authorize(
                identity,
                scope.workspace_id,
                scope.project_id,
                write=identity == binding.initiator_id,
                service=identity == binding.orchestrator_id,
            )
        run, task = await tx.run_task(binding.run_id)
        require(
            run.principal_id == binding.initiator_id
            and run.project_id == scope.project_id
            and task.envelope.project_id == scope.project_id
            and task.envelope.task_id == run.task_id
            and task.envelope.task_type is TaskType.EXPERIMENT
            and run.provider is Provider.DETERMINISTIC,
            Code.INVALID_CONTRACT,
        )
        require(
            run.state not in {RunState.CANCELLED, RunState.FAILED, RunState.SUCCEEDED},
            Code.EPISODE_STATE_CONFLICT,
        )
        return binding

    async def _check(
        self,
        tx: RuntimeTransaction,
        scope: AuthorityScope,
        operation: str,
        ep: RuntimeEpisode,
        lease: RuntimeLease | None = None,
        resource: RuntimeResource | None = None,
        *,
        request_json: str | None = None,
        response_json: str | None = None,
        live: LiveState | None = None,
    ) -> AuthorityCheck:
        await self._actor(tx, scope)
        binding = await self._owner(tx, scope, ep)
        require(
            ep.setup.evaluator_principal_id
            not in {ep.setup.adapter_principal_id, binding.orchestrator_id},
            Code.CAPABILITY_DENIED,
        )
        for identity in sorted({ep.setup.adapter_principal_id, ep.setup.evaluator_principal_id}):
            await tx.registry.authorize(
                identity, scope.workspace_id, scope.project_id, service=True
            )
        if resource is not None:
            await tx.registry.authorize(
                resource.host_principal_id, scope.workspace_id, scope.project_id, service=True
            )
        check = AuthorityCheck(
            operation=operation,
            actor_id=scope.actor_id,
            now=await tx.now(),
            episode=ep,
            lease=lease,
            resource=resource,
            request_json=request_json,
            response_json=response_json,
            live=live,
        )
        for collaborator in (self.policy, self.host, self.conformance):
            require(collaborator is not None, Code.SIMULATION_UNAVAILABLE)
            assert collaborator is not None
            await collaborator.check(tx, AuthorityCheck.model_validate_json(canonical_json(check)))
        if lease is not None:
            now = await tx.now()
            require(
                lease.status == "ACTIVE" and now < min(lease.expires_at, lease.heartbeat_deadline),
                Code.LEASE_INVALID,
            )
        return check

    def _revision(self, scope: AuthorityScope, record: RuntimeDTO) -> None:
        require(scope.expected_revision is not None, Code.REVISION_REQUIRED)
        require(scope.expected_revision == record.revision, Code.REVISION_CONFLICT)

    def _ledger_scope(self, scope: AuthorityScope, op: str, resource: str) -> IdempotencyScope:
        return IdempotencyScope(
            scope.workspace_id,
            scope.project_id,
            scope.actor_id,
            op,
            resource,
            scope.idempotency_key,
        )

    async def _retry[C: RuntimeDTO](
        self,
        tx: RuntimeTransaction,
        scope: AuthorityScope,
        op: str,
        resource: str,
        body: Any,
        model: type[C],
    ) -> C | None:
        ledger = await tx.registry.ledger(self._ledger_scope(scope, op, resource))
        if ledger:
            require(
                ledger.request_hash == content_hash(body, exclude=()), Code.IDEMPOTENCY_CONFLICT
            )
            return model.model_validate_json(ledger.response_json)
        return None

    async def _remember(
        self,
        tx: RuntimeTransaction,
        scope: AuthorityScope,
        op: str,
        resource: str,
        body: Any,
        response: RuntimeDTO,
    ) -> None:
        await tx.registry.remember(
            LedgerRecord(
                self._ledger_scope(scope, op, resource),
                content_hash(body, exclude=()),
                canonical_json(response).decode(),
            )
        )

    async def _event(
        self,
        tx: RuntimeTransaction,
        scope: AuthorityScope,
        ep: RuntimeEpisode,
        event_type: str,
        payload: dict[str, Any],
    ) -> RuntimeEpisode:
        now = await tx.now()
        actor = await self._actor(tx, scope)
        event = SimulationDomainEvent.model_validate(
            dict(
                contract_id=new_id("simulation_domain_event"),
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                created_at=now,
                created_by=PrincipalRef(principal_id=actor.principal_id, status=actor.status),
                event_type=event_type,
                occurred_at=now,
                run_id=ep.run_id,
                episode_id=ep.id,
                correlation_id=ep.id,
                producer=PrincipalRef(principal_id=actor.principal_id, status=actor.status),
                sequence=ep.event_sequence + 1,
                payload=payload,
            )
        )
        ep = copy_update(ep, event_sequence=event.sequence)
        await tx.put(ep)
        await tx.registry.append_event(
            StoredEvent(
                event.contract_id,
                ep.binding_id,
                scope.workspace_id,
                scope.project_id,
                event.sequence,
                event.content_hash,
                canonical_json(event).decode(),
            )
        )
        return ep

    async def create_bound_run(
        self,
        scope: AuthorityScope,
        *,
        task: Task,
        run: Run,
        binding_original: str,
        setup: EpisodeSetup,
    ) -> RuntimeEpisode:
        """Internal M4 seam: all three actual records and episode state commit together."""
        binding = original(binding_original, SimulationRunBinding)
        setup = EpisodeSetup.model_validate_json(canonical_json(setup))
        descriptor = original(setup.descriptor_original, EmbodimentDescriptor)
        require(
            (descriptor.workspace_id, descriptor.project_id)
            == (scope.workspace_id, scope.project_id),
            Code.RESOURCE_NOT_FOUND,
        )
        require(
            run.principal_id is not None and run.principal_id != scope.actor_id,
            Code.CAPABILITY_DENIED,
        )
        require(
            binding.created_by.principal_id == scope.actor_id
            and binding.orchestrator_principal.principal_id == scope.actor_id,
            Code.CAPABILITY_DENIED,
        )
        require(
            run.run_id == binding.run_id
            and run.task_id == task.envelope.task_id
            and run.project_id == task.envelope.project_id == scope.project_id
            and binding.experiment_contract_hash == setup.experiment_contract_hash
            and task.envelope.task_type is TaskType.EXPERIMENT
            and task.envelope.risk_level is RiskLevel.HIGH
            and run.provider is Provider.DETERMINISTIC
            and run.state is RunState.PENDING,
            Code.INVALID_CONTRACT,
        )
        assert run.principal_id is not None
        body = dict(task=task, run=run, binding=binding_original, setup=setup)
        async with self._transaction(scope) as tx:
            await self._actor(tx, scope, service=True)
            await tx.registry.authorize(
                run.principal_id, scope.workspace_id, scope.project_id, write=True
            )
            retry = await self._retry(
                tx, scope, "bind_run", binding.episode_id, body, RuntimeEpisode
            )
            if retry:
                return retry
            await self._retain(tx, scope, binding_original, binding)
            await tx.create_task_run(task, run)
            row = RuntimeBinding(
                id=binding.contract_id,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                run_id=run.run_id,
                episode_id=binding.episode_id,
                contract_id=binding.contract_id,
                initiator_id=run.principal_id,
                orchestrator_id=scope.actor_id,
            )
            await tx.put(row, insert=True)
            ep = RuntimeEpisode(
                id=binding.episode_id,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                run_id=run.run_id,
                binding_id=row.id,
                setup=setup,
            )
            await tx.put(ep, insert=True)
            await self._check(tx, scope, "BIND_RUN", ep)
            ep = await self._event(
                tx, scope, ep, "simulation_run.bound", {"binding_hash": binding.content_hash}
            )
            await self._remember(tx, scope, "bind_run", ep.id, body, ep)
            return ep

    async def bootstrap_resource(
        self, scope: AuthorityScope, *, resource: RuntimeResource
    ) -> RuntimeResource:
        """Trusted deployment-only inventory, never first-request resource claiming."""
        resource = RuntimeResource.model_validate_json(canonical_json(resource))
        require(
            (resource.workspace_id, resource.project_id) == (scope.workspace_id, scope.project_id),
            Code.RESOURCE_NOT_FOUND,
        )
        require(
            resource.generation == 0
            and not resource.quarantined
            and resource.current_lease_id is None,
            Code.INVALID_CONTRACT,
        )
        async with self._transaction(scope) as tx:
            await self._actor(tx, scope, service=True)
            await tx.registry.authorize(
                resource.host_principal_id, scope.workspace_id, scope.project_id, service=True
            )
            await tx.lock_resource(resource.id)
            current = await tx.get(RuntimeResource, resource.id)
            if current:
                require(current == resource, Code.CONTRACT_CONFLICT)
                return current
            # The trusted caller is the deployment composition root. Inventory
            # alone never allocates a lease or supplies policy/host approval.
            await tx.put(resource, insert=True)
            return resource

    async def acquire_lease(
        self,
        scope: AuthorityScope,
        *,
        episode_id: str,
        resource_id: str,
        lifetime_seconds: int,
        heartbeat_timeout_seconds: int,
    ) -> RuntimeLease:
        require(
            type(lifetime_seconds) is int
            and 0 < lifetime_seconds <= 3600
            and type(heartbeat_timeout_seconds) is int
            and 0 < heartbeat_timeout_seconds <= lifetime_seconds,
            Code.INVALID_REQUEST,
        )
        body = dict(
            episode_id=episode_id,
            resource_id=resource_id,
            lifetime=lifetime_seconds,
            heartbeat=heartbeat_timeout_seconds,
        )
        async with self._transaction(scope) as tx:
            await self._actor(tx, scope, service=True)
            ep = await self._get(tx, scope, RuntimeEpisode, episode_id)
            await tx.lock_resource(resource_id)
            resource = await self._get(tx, scope, RuntimeResource, resource_id)
            await self._check(tx, scope, "ACQUIRE_LEASE", ep, resource=resource)
            binding = await self._get(tx, scope, RuntimeBinding, ep.binding_id)
            require(scope.actor_id == binding.orchestrator_id, Code.CAPABILITY_DENIED)
            retry = await self._retry(tx, scope, "acquire_lease", ep.id, body, RuntimeLease)
            if retry:
                return retry
            self._revision(scope, ep)
            require(ep.status == "BOUND")
            require(not resource.quarantined and resource.current_lease_id is None, Code.LEASE_BUSY)
            require(resource.generation < 2**63 - 1, Code.RESOURCE_CAP_EXHAUSTED)
            now = await tx.now()
            seal = SimulationLease(
                contract_id=new_id("simulation_lease"),
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                created_at=now,
                created_by=PrincipalRef(principal_id=scope.actor_id, status=PrincipalStatus.ACTIVE),
                episode_id=ep.id,
                run_id=ep.run_id,
                experiment_contract_hash=ep.setup.experiment_contract_hash,
                environment_snapshot_hash=ep.setup.environment_snapshot_hash,
                adapter_manifest_hash=ep.setup.adapter_manifest_hash,
                generation=resource.generation + 1,
                resource_id=resource.id,
                endpoint_handle="simh_" + token_hex(24),
                owner_principal=PrincipalRef(
                    principal_id=scope.actor_id, status=PrincipalStatus.ACTIVE
                ),
                acquired_at=now,
                expires_at=now + timedelta(seconds=lifetime_seconds),
                heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            )
            await self._retain(tx, scope, canonical_json(seal).decode(), seal)
            lease = RuntimeLease(
                id=seal.contract_id,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                resource_id=resource.id,
                run_id=ep.run_id,
                episode_id=ep.id,
                generation=seal.generation,
                contract_id=seal.contract_id,
                owner_principal_id=scope.actor_id,
                endpoint_handle=seal.endpoint_handle,
                expires_at=seal.expires_at,
                last_heartbeat_at=now,
                heartbeat_deadline=now + timedelta(seconds=heartbeat_timeout_seconds),
                heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            )
            await tx.put(lease, insert=True)
            await tx.put(
                copy_update(resource, generation=lease.generation, current_lease_id=lease.id)
            )
            ep = copy_update(ep, lease_id=lease.id, status="LEASED")
            await self._event(
                tx,
                scope,
                ep,
                "simulation_lease.acquired",
                {"lease_id": lease.id, "generation": lease.generation},
            )
            await self._remember(tx, scope, "acquire_lease", ep.id, body, lease)
            return lease

    async def _lease(
        self,
        tx: RuntimeTransaction,
        scope: AuthorityScope,
        ep: RuntimeEpisode,
        *,
        live: bool = True,
    ) -> tuple[RuntimeLease, RuntimeResource]:
        require(ep.lease_id is not None, Code.LEASE_INVALID)
        lease = await self._get(tx, scope, RuntimeLease, str(ep.lease_id))
        await tx.lock_resource(lease.resource_id)
        resource = await self._get(tx, scope, RuntimeResource, lease.resource_id)
        seal = await self._contract(tx, scope, lease.contract_id, SimulationLease)
        require(
            (
                seal.resource_id,
                seal.generation,
                seal.run_id,
                seal.episode_id,
                seal.endpoint_handle,
                seal.expires_at,
                seal.heartbeat_timeout_seconds,
                seal.owner_principal.principal_id,
                seal.experiment_contract_hash,
                seal.environment_snapshot_hash,
                seal.adapter_manifest_hash,
            )
            == (
                lease.resource_id,
                lease.generation,
                ep.run_id,
                ep.id,
                lease.endpoint_handle,
                lease.expires_at,
                lease.heartbeat_timeout_seconds,
                lease.owner_principal_id,
                ep.setup.experiment_contract_hash,
                ep.setup.environment_snapshot_hash,
                ep.setup.adapter_manifest_hash,
            ),
            Code.INVALID_CONTRACT,
        )
        if live:
            now = await tx.now()
            require(
                lease.status == "ACTIVE"
                and not resource.quarantined
                and resource.current_lease_id == lease.id
                and resource.generation == lease.generation
                and now < min(lease.expires_at, lease.heartbeat_deadline),
                Code.LEASE_INVALID,
            )
            await tx.registry.authorize(
                resource.host_principal_id, scope.workspace_id, scope.project_id, service=True
            )
        return lease, resource

    async def heartbeat(
        self, scope: AuthorityScope, *, episode_id: str, generation: int
    ) -> RuntimeLease:
        require(type(generation) is int and generation > 0, Code.INVALID_REQUEST)
        body = {"generation": generation}
        async with self._transaction(scope) as tx:
            ep = await self._get(tx, scope, RuntimeEpisode, episode_id)
            lease, resource = await self._lease(tx, scope, ep, live=False)
            await self._check(tx, scope, "HEARTBEAT", ep, lease, resource)
            require(
                scope.actor_id in {lease.owner_principal_id, resource.host_principal_id},
                Code.CAPABILITY_DENIED,
            )
            retry = await self._retry(tx, scope, "heartbeat", lease.id, body, RuntimeLease)
            if retry:
                return retry
            self._revision(scope, lease)
            await self._lease(tx, scope, ep)
            require(generation == lease.generation, Code.LEASE_INVALID)
            now = await tx.now()
            lease = copy_update(
                lease,
                last_heartbeat_at=now,
                heartbeat_deadline=min(
                    lease.expires_at, now + timedelta(seconds=lease.heartbeat_timeout_seconds)
                ),
            )
            await tx.put(lease)
            await self._event(
                tx,
                scope,
                ep,
                "simulation_lease.heartbeat",
                {"lease_id": lease.id, "generation": generation},
            )
            await self._remember(tx, scope, "heartbeat", lease.id, body, lease)
            return lease

    async def end_lease(
        self,
        scope: AuthorityScope,
        *,
        episode_id: str,
        generation: int,
        disposition: Literal["REVOKED", "EXPIRED", "RELEASED"],
    ) -> RuntimeLease:
        require(disposition in {"REVOKED", "EXPIRED", "RELEASED"}, Code.INVALID_REQUEST)
        require(type(generation) is int and generation > 0, Code.INVALID_REQUEST)
        body = dict(generation=generation, disposition=disposition)
        async with self._transaction(scope) as tx:
            await self._actor(tx, scope)
            ep = await self._get(tx, scope, RuntimeEpisode, episode_id)
            lease, resource = await self._lease(tx, scope, ep, live=False)
            # Cleanup/revocation must remain possible after initiating owner disable.
            require(
                scope.actor_id in {lease.owner_principal_id, resource.host_principal_id},
                Code.CAPABILITY_DENIED,
            )
            await self._actor(tx, scope, service=True)
            retry = await self._retry(tx, scope, "end_lease", lease.id, body, RuntimeLease)
            if retry:
                return retry
            self._revision(scope, lease)
            require(
                generation == lease.generation and resource.current_lease_id == lease.id,
                Code.LEASE_INVALID,
            )
            if disposition == "EXPIRED":
                require(
                    await tx.now() >= min(lease.expires_at, lease.heartbeat_deadline),
                    Code.LEASE_INVALID,
                )
            lease = copy_update(lease, status=disposition)
            await tx.put(lease)
            await tx.put(copy_update(resource, quarantined=True))
            if ep.in_flight:
                pending = await self._get(tx, scope, DispatchReservation, ep.in_flight)
                await tx.put(copy_update(pending, status="UNCERTAIN"))
            # M4 alone enters VERIFYING after committed termination/capture
            # evidence. Releasing a RUNNING lease is never proof of completion.
            status = (
                "VERIFYING"
                if disposition == "RELEASED" and ep.status == "VERIFYING" and ep.in_flight is None
                else "ABORTED"
            )
            ep = copy_update(ep, status=status)
            await self._event(
                tx, scope, ep, "simulation_lease." + disposition.lower(), {"lease_id": lease.id}
            )
            await self._remember(tx, scope, "end_lease", lease.id, body, lease)
            return lease

    async def confirm_cleanup(
        self, scope: AuthorityScope, *, episode_id: str, generation: int
    ) -> RuntimeResource:
        require(type(generation) is int and generation > 0, Code.INVALID_REQUEST)
        body = {"generation": generation}
        async with self._transaction(scope) as tx:
            await self._actor(tx, scope, service=True)
            ep = await self._get(tx, scope, RuntimeEpisode, episode_id)
            lease, resource = await self._lease(tx, scope, ep, live=False)
            require(scope.actor_id == resource.host_principal_id, Code.CAPABILITY_DENIED)
            retry = await self._retry(tx, scope, "cleanup", lease.id, body, RuntimeResource)
            if retry:
                return retry
            self._revision(scope, resource)
            require(
                resource.current_lease_id == lease.id
                and resource.generation == generation
                and resource.quarantined
                and lease.status != "ACTIVE",
                Code.LEASE_INVALID,
            )
            require(self.cleanup is not None, Code.ISOLATION_UNAVAILABLE)
            assert self.cleanup is not None
            await self.cleanup.verify(tx, resource, lease)
            resource = copy_update(resource, current_lease_id=None, quarantined=False)
            await tx.put(resource)
            await self._event(tx, scope, ep, "simulation_lease.cleaned", {"lease_id": lease.id})
            await self._remember(tx, scope, "cleanup", lease.id, body, resource)
            return resource

    def _pins(
        self, ep: RuntimeEpisode, lease: RuntimeLease, preflight: SimulationPreflightReceipt
    ) -> EpisodePins:
        setup = ep.setup
        return EpisodePins(
            episode_id=ep.id,
            experiment_contract_hash=setup.experiment_contract_hash,
            preflight_receipt_hash=preflight.content_hash,
            environment_snapshot_hash=setup.environment_snapshot_hash,
            adapter_manifest_hash=setup.adapter_manifest_hash,
            safety_envelope_hash=setup.safety_envelope_hash,
            verification_spec_hash=setup.verification_spec_hash,
            seed=setup.seed,
            randomization_sample_hash=setup.randomization_sample_hash,
            lease=LeaseBinding(lease_id=lease.id, generation=lease.generation),
        )

    async def record_preflight(
        self, scope: AuthorityScope, *, episode_id: str, original_json: str, live: LiveState
    ) -> RuntimeEpisode:
        preflight = original(original_json, SimulationPreflightReceipt)
        live = LiveState.model_validate_json(canonical_json(live))
        body = dict(original_json=original_json, live=live)
        async with self._transaction(scope) as tx:
            await self._actor(tx, scope, service=True)
            ep = await self._get(tx, scope, RuntimeEpisode, episode_id)
            lease, resource = await self._lease(tx, scope, ep)
            await self._check(tx, scope, "PREFLIGHT", ep, lease, resource)
            require(
                scope.actor_id == lease.owner_principal_id == preflight.created_by.principal_id,
                Code.CAPABILITY_DENIED,
            )
            retry = await self._retry(tx, scope, "preflight", ep.id, body, RuntimeEpisode)
            if retry:
                return retry
            self._revision(scope, ep)
            require(ep.status == "LEASED")
            pins = self._pins(ep, lease, preflight)
            require(
                all(
                    getattr(preflight, field) == value
                    for field, value in pins.__dict__.items()
                    if field != "preflight_receipt_hash"
                ),
                Code.PREFLIGHT_FAILED,
            )
            require(
                preflight.result == "PASS"
                and preflight.created_at <= await tx.now() < preflight.valid_until
                and preflight.valid_until <= lease.expires_at,
                Code.PREFLIGHT_FAILED,
            )
            require(
                live.budget.actions == 0 and live.sequence == 0 and live.state.sim_time_ns == 0,
                Code.EPISODE_STATE_CONFLICT,
            )
            await self._retain(tx, scope, original_json, preflight)
            ep = copy_update(ep, status="PREFLIGHT", preflight_id=preflight.contract_id, live=live)
            await tx.put(ep)
            # Collaborators can now inspect the exact retained preflight in this UoW.
            await self._check(tx, scope, "PREFLIGHT_COMMIT", ep, lease, resource)
            ep = await self._event(
                tx,
                scope,
                ep,
                "simulation_preflight.completed",
                {"preflight_hash": preflight.content_hash},
            )
            await self._remember(tx, scope, "preflight", ep.id, body, ep)
            return ep

    async def _preflight(
        self, tx: RuntimeTransaction, scope: AuthorityScope, ep: RuntimeEpisode, lease: RuntimeLease
    ) -> SimulationPreflightReceipt:
        require(ep.preflight_id is not None, Code.PREFLIGHT_FAILED)
        preflight = await self._contract(
            tx, scope, str(ep.preflight_id), SimulationPreflightReceipt
        )
        pins = self._pins(ep, lease, preflight)
        require(
            all(
                getattr(preflight, field) == value
                for field, value in pins.__dict__.items()
                if field != "preflight_receipt_hash"
            ),
            Code.PREFLIGHT_FAILED,
        )
        require(
            preflight.result == "PASS"
            and preflight.created_at <= await tx.now() < preflight.valid_until,
            Code.PREFLIGHT_FAILED,
        )
        return preflight

    async def approve_episode(
        self, scope: AuthorityScope, *, episode_id: str, original_json: str
    ) -> RuntimeApproval:
        """Called only for the actual authenticated human's exact reviewed artifact."""
        approval = original(original_json, SimulationEpisodeApproval)
        body = {"original_json": original_json}
        async with self._transaction(scope) as tx:
            actor = await self._actor(tx, scope, human=True)
            ep = await self._get(tx, scope, RuntimeEpisode, episode_id)
            lease, resource = await self._lease(tx, scope, ep)
            await self._check(tx, scope, "APPROVE", ep, lease, resource)
            retry = await self._retry(tx, scope, "approve", ep.id, body, RuntimeApproval)
            if retry:
                return retry
            self._revision(scope, ep)
            require(ep.status == "PREFLIGHT" and ep.approval_id is None)
            preflight = await self._preflight(tx, scope, ep, lease)
            require(
                approval.created_by.principal_id
                == approval.approved_by.principal_id
                == actor.principal_id
                and approval.approved_by.status is PrincipalStatus.ACTIVE
                and approval.pins == self._pins(ep, lease, preflight)
                and approval.policy_ref == ep.setup.policy_ref
                and approval.created_at
                <= await tx.now()
                < approval.expires_at
                <= min(lease.expires_at, preflight.valid_until),
                Code.APPROVAL_INVALID,
            )
            await self._retain(tx, scope, original_json, approval)
            row = RuntimeApproval(
                id=approval.contract_id,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                episode_id=ep.id,
                contract_id=approval.contract_id,
            )
            await tx.put(row, insert=True)
            ep = copy_update(ep, approval_id=row.id)
            await self._event(
                tx,
                scope,
                ep,
                "simulation_approval.created",
                {"approval_hash": approval.content_hash},
            )
            await self._remember(tx, scope, "approve", ep.id, body, row)
            return row

    async def revoke_approval(
        self, scope: AuthorityScope, *, episode_id: str, approval_id: str
    ) -> RuntimeApproval:
        body = {"approval_id": approval_id}
        async with self._transaction(scope) as tx:
            await self._actor(tx, scope, human=True)
            ep = await self._get(tx, scope, RuntimeEpisode, episode_id)
            row = await self._get(tx, scope, RuntimeApproval, approval_id)
            require(row.episode_id == ep.id and ep.approval_id == row.id, Code.APPROVAL_INVALID)
            seal = await self._contract(tx, scope, row.contract_id, SimulationEpisodeApproval)
            # An approver may always retract their own authority, even if another
            # episode participant has since been disabled. No new actuation grant.
            require(scope.actor_id == seal.approved_by.principal_id, Code.CAPABILITY_DENIED)
            retry = await self._retry(tx, scope, "revoke_approval", row.id, body, RuntimeApproval)
            if retry:
                return retry
            self._revision(scope, row)
            row = copy_update(row, revoked_at=await tx.now())
            await tx.put(row)
            await self._event(tx, scope, ep, "simulation_approval.revoked", {"approval_id": row.id})
            await self._remember(tx, scope, "revoke_approval", row.id, body, row)
            return row

    async def _approval(
        self,
        tx: RuntimeTransaction,
        scope: AuthorityScope,
        ep: RuntimeEpisode,
        lease: RuntimeLease,
        *,
        consumed: bool,
    ) -> tuple[RuntimeApproval, SimulationEpisodeApproval]:
        require(ep.approval_id is not None, Code.APPROVAL_REQUIRED)
        row = await self._get(tx, scope, RuntimeApproval, str(ep.approval_id))
        approval = await self._contract(tx, scope, row.contract_id, SimulationEpisodeApproval)
        await tx.registry.authorize(
            approval.approved_by.principal_id, scope.workspace_id, scope.project_id, write=True
        )
        preflight = await self._preflight(tx, scope, ep, lease)
        require(
            row.episode_id == ep.id
            and row.revoked_at is None
            and approval.pins == self._pins(ep, lease, preflight)
            and approval.policy_ref == ep.setup.policy_ref
            and approval.created_at <= await tx.now() < approval.expires_at,
            Code.APPROVAL_INVALID,
        )
        require(
            (row.consumed_episode_id == ep.id) if consumed else row.consumed_episode_id is None,
            Code.APPROVAL_INVALID,
        )
        return row, approval

    async def consume_approval(
        self, scope: AuthorityScope, *, episode_id: str, approval_id: str
    ) -> RuntimeEpisode:
        body = {"approval_id": approval_id}
        async with self._transaction(scope) as tx:
            ep = await self._get(tx, scope, RuntimeEpisode, episode_id)
            lease, resource = await self._lease(tx, scope, ep)
            await self._check(tx, scope, "CONSUME_APPROVAL", ep, lease, resource)
            require(scope.actor_id == lease.owner_principal_id, Code.CAPABILITY_DENIED)
            retry = await self._retry(tx, scope, "consume_approval", ep.id, body, RuntimeEpisode)
            if retry:
                return retry
            self._revision(scope, ep)
            require(ep.status == "PREFLIGHT" and ep.approval_id == approval_id)
            row, approval = await self._approval(tx, scope, ep, lease, consumed=False)
            await tx.put(copy_update(row, consumed_episode_id=ep.id, consumed_at=await tx.now()))
            ep = copy_update(ep, status="APPROVED")
            ep = await self._event(
                tx,
                scope,
                ep,
                "simulation_approval.consumed",
                {"approval_hash": approval.content_hash},
            )
            await self._remember(tx, scope, "consume_approval", ep.id, body, ep)
            return ep

    def execution_pins(
        self, ep: RuntimeEpisode, approval: SimulationEpisodeApproval
    ) -> ExecutionPins:
        require(ep.live is not None)
        assert ep.live is not None
        return ExecutionPins(
            workspace_id=ep.workspace_id,
            project_id=ep.project_id,
            episode=approval.pins,
            dependencies=ep.setup.dependencies,
            descriptor_hash=original(
                ep.setup.descriptor_original, EmbodimentDescriptor
            ).content_hash,
            adapter_principal_id=ep.setup.adapter_principal_id,
            evaluator_principal_id=ep.setup.evaluator_principal_id,
            policy_ref=ep.setup.policy_ref,
            evaluator=ep.setup.evaluator,
            approval_hash=approval.content_hash,
            state=ep.live.state,
            budget=ep.live.budget,
            next_action_sequence=ep.live.sequence,
        )

    def safety_context(
        self,
        ep: RuntimeEpisode,
        lease: RuntimeLease,
        approval: SimulationEpisodeApproval,
        *,
        receipt_id: str,
        now: datetime,
    ) -> SafetyContext:
        require(ep.live is not None)
        assert ep.live is not None
        return SafetyContext(
            **ep.live.model_dump(mode="python"),
            workspace_id=ep.workspace_id,
            project_id=ep.project_id,
            receipt_id=receipt_id,
            now=now,
            episode=approval.pins,
            closure=ep.setup.dependencies,
            approval_hash=approval.content_hash,
            approval_active=True,
            policy_ref=ep.setup.policy_ref,
            lease_active=True,
            lease_expires_at=lease.expires_at,
        )

    async def _keys(
        self, tx: RuntimeTransaction, check: AuthorityCheck
    ) -> Mapping[str, TrustedSafetyKey]:
        require(self.safety_keys is not None, Code.SAFETY_DENIED)
        assert self.safety_keys is not None
        keys = await self.safety_keys.keys(tx, check)
        require(bool(keys), Code.SAFETY_DENIED)
        return keys

    async def record_safety(
        self, scope: AuthorityScope, *, episode_id: str, issuance: SafetyEvaluation
    ) -> RuntimeIssuance:
        # Reconstruct frozen canonical bytes; never retain a mutable caller instance.
        require(
            len(issuance.request_bytes) <= 4 * 1024 * 1024
            and (issuance.preview_bytes is None or len(issuance.preview_bytes) <= 32 * 1024 * 1024),
            Code.PAYLOAD_TOO_LARGE,
        )
        issuance = SafetyEvaluation(
            issuance.receipt_envelope, issuance.request_bytes, issuance.preview_bytes
        )
        receipt = issuance.receipt
        body = dict(
            receipt=issuance.receipt_envelope.original_json,
            request=issuance.request_bytes.decode(),
            preview=None if issuance.preview_bytes is None else issuance.preview_bytes.decode(),
        )
        async with self._transaction(scope) as tx:
            await self._actor(tx, scope, service=True)
            ep = await self._get(tx, scope, RuntimeEpisode, episode_id)
            lease, resource = await self._lease(tx, scope, ep)
            check = await self._check(tx, scope, "ISSUE_SAFETY", ep, lease, resource)
            require(
                scope.actor_id == ep.setup.evaluator_principal_id == receipt.created_by.principal_id
                and scope.actor_id != ep.setup.adapter_principal_id,
                Code.CAPABILITY_DENIED,
            )
            await tx.registry.authorize(
                ep.setup.adapter_principal_id, scope.workspace_id, scope.project_id, service=True
            )
            retry = await self._retry(
                tx, scope, "safety", receipt.contract_id, body, RuntimeIssuance
            )
            if retry:
                return retry
            self._revision(scope, ep)
            require(ep.status == "RUNNING" and ep.in_flight is None)
            _, approval = await self._approval(tx, scope, ep, lease, consumed=True)
            verify_safety_signature(receipt, trusted_keys=await self._keys(tx, check))
            context = self.safety_context(
                ep, lease, approval, receipt_id=receipt.contract_id, now=await tx.now()
            )
            require(
                canonical_json(issuance.request.descriptor)
                == canonical_json(original(ep.setup.descriptor_original, EmbodimentDescriptor)),
                Code.SAFETY_DENIED,
            )
            if receipt.decision.decision == "ALLOW":
                issuance.validate_current_context(context)
                validate_execution(
                    issuance.request.intent,
                    issuance.request.prepared,
                    receipt,
                    pins=self.execution_pins(ep, approval),
                    descriptor=original(ep.setup.descriptor_original, EmbodimentDescriptor),
                    trusted_keys=await self._keys(tx, check),
                )
            else:
                require(
                    canonical_json(issuance.request.context.model_dump(exclude={"now"}))
                    == canonical_json(context.model_dump(exclude={"now"})),
                    Code.SAFETY_DENIED,
                )
            await self._retain(tx, scope, issuance.receipt_envelope.original_json, receipt)
            row = RuntimeIssuance(
                id=receipt.contract_id,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                episode_id=ep.id,
                contract_id=receipt.contract_id,
                receipt_hash=receipt.content_hash,
                request_json=issuance.request_bytes.decode(),
                preview_json=None
                if issuance.preview_bytes is None
                else issuance.preview_bytes.decode(),
            )
            await tx.put(row, insert=True)
            await self._event(
                tx,
                scope,
                ep,
                "simulation_safety.issued",
                {"receipt_hash": receipt.content_hash, "decision": receipt.decision.decision},
            )
            await self._remember(tx, scope, "safety", receipt.contract_id, body, row)
            return row

    async def reserve_dispatch(
        self,
        scope: AuthorityScope,
        *,
        episode_id: str,
        request: ProtocolRequest,
        pins: ExecutionPins,
    ) -> AuthorityAdmission:
        """Authority server must also bind this request to its supervised in-flight IPC.

        Identical API retries return an existing reservation, NEVER a new permit
        to send. RESERVED on recovery is uncertain, not permission to replay.
        """
        checked = parse_message(canonical_json(request))
        require(isinstance(checked, ProtocolRequest), Code.INVALID_REQUEST)
        assert isinstance(checked, ProtocolRequest)
        request = checked
        require(request.sequence <= 2**63 - 1, Code.RESOURCE_CAP_EXHAUSTED)
        require(
            isinstance(request.payload, ExecuteRequest | ResetRequest | TerminateRequest),
            Code.INVALID_REQUEST,
        )
        body = {"request_json": canonical_json(request).decode()}
        async with self._transaction(scope) as tx:
            ep = await self._get(tx, scope, RuntimeEpisode, episode_id)
            lease, resource = await self._lease(tx, scope, ep)
            check = await self._check(
                tx, scope, "RESERVE_" + request.payload.op, ep, lease, resource
            )
            require(scope.actor_id == lease.owner_principal_id, Code.CAPABILITY_DENIED)
            retry = await self._retry(tx, scope, "dispatch", ep.id, body, DispatchReservation)
            if retry:
                return AuthorityAdmission(fresh=False, reservation=retry)
            self._revision(scope, ep)
            require(ep.in_flight is None and request.sequence > ep.last_request_sequence)
            _, approval = await self._approval(tx, scope, ep, lease, consumed=True)
            preflight = await self._preflight(tx, scope, ep, lease)
            # The dispatch permit must carry every actual authority deadline,
            # even when configured collaborators contribute no shorter cap.
            # Check these locked originals again at the final commit boundary.
            tx.require_valid_interval(
                lease.last_heartbeat_at,
                min(lease.expires_at, lease.heartbeat_deadline),
                Code.LEASE_INVALID,
            )
            tx.require_valid_interval(
                preflight.created_at, preflight.valid_until, Code.PREFLIGHT_FAILED
            )
            tx.require_valid_interval(
                approval.created_at, approval.expires_at, Code.APPROVAL_INVALID
            )
            admission_episode = ep
            issuance: SafetyEvaluation | None = None
            require(
                canonical_json(pins) == canonical_json(self.execution_pins(ep, approval)),
                Code.EPISODE_STATE_CONFLICT,
            )
            require(request.scope == pins.command_scope(), Code.LEASE_INVALID)
            prepared_hash = receipt_hash = None
            live = ep.live
            assert live is not None
            if isinstance(request.payload, ResetRequest):
                require(
                    ep.status == "APPROVED"
                    and not ep.reset_completed
                    and request.payload.seed == ep.setup.seed
                    and request.payload.randomization_sample_hash
                    == ep.setup.randomization_sample_hash
                )
            elif isinstance(request.payload, TerminateRequest):
                # Normal termination is durable and single-use, but never spends
                # an action budget or establishes independent acceptance.
                require(ep.status == "RUNNING" and ep.reset_completed)
            else:
                assert isinstance(request.payload, ExecuteRequest)
                require(ep.status == "RUNNING" and ep.reset_completed)
                receipt = request.payload.safety.for_execution(SafetyDecisionReceipt)
                row = await self._get(tx, scope, RuntimeIssuance, receipt.contract_id)
                stored = await self._contract(tx, scope, row.contract_id, SafetyDecisionReceipt)
                require(
                    row.episode_id == ep.id
                    and row.receipt_hash == receipt.content_hash == stored.content_hash,
                    Code.SAFETY_DENIED,
                )
                issuance = SafetyEvaluation(
                    CanonicalWriterEnvelope(canonical_json(stored).decode()),
                    row.request_json.encode(),
                    None if row.preview_json is None else row.preview_json.encode(),
                )
                require(
                    canonical_json(issuance.request.descriptor)
                    == canonical_json(original(ep.setup.descriptor_original, EmbodimentDescriptor)),
                    Code.SAFETY_DENIED,
                )
                issuance.validate_current_context(
                    self.safety_context(
                        ep, lease, approval, receipt_id=receipt.contract_id, now=await tx.now()
                    )
                )
                require(
                    canonical_json(issuance.request.intent)
                    == canonical_json(request.payload.intent.for_execution(ActionIntent))
                    and canonical_json(issuance.request.prepared)
                    == canonical_json(request.payload.prepared.for_execution(PreparedCommand)),
                    Code.SAFETY_DENIED,
                )
                await tx.registry.authorize(
                    ep.setup.evaluator_principal_id,
                    scope.workspace_id,
                    scope.project_id,
                    service=True,
                )
                await tx.registry.authorize(
                    ep.setup.adapter_principal_id,
                    scope.workspace_id,
                    scope.project_id,
                    service=True,
                )
                validate_execution(
                    issuance.request.intent,
                    issuance.request.prepared,
                    receipt,
                    pins=pins,
                    descriptor=original(ep.setup.descriptor_original, EmbodimentDescriptor),
                    trusted_keys=await self._keys(tx, check),
                )
                # Safety has a signed wall issuance time and a simulator-time
                # expiry. Its wall validity is bounded by its retained lease and
                # approval; never reinterpret simulator nanoseconds as UTC.
                safety_request = issuance.request
                tx.require_valid_interval(
                    receipt.decision.issued_at,
                    min(
                        safety_request.context.lease_expires_at,
                        safety_request.approval.expires_at,
                    ),
                    Code.SAFETY_DENIED,
                )
                prepared_hash, receipt_hash = (
                    receipt.decision.prepared_command_hash,
                    receipt.content_hash,
                )
                live = LiveState.model_validate(
                    {**live.model_dump(mode="python"), "budget": receipt.decision.budget_after}
                )
            deadline = tx.authority_valid_until
            assert deadline is not None
            reservation = DispatchReservation(
                id=request.request_digest,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                episode_id=ep.id,
                request_sequence=request.sequence,
                request_json=body["request_json"],
                prepared_hash=prepared_hash,
                receipt_hash=receipt_hash,
                reserved_at=await tx.now(),
                authority_valid_until=deadline,
            )
            await tx.put(reservation, insert=True)
            ep = copy_update(
                ep, in_flight=reservation.id, last_request_sequence=request.sequence, live=live
            )
            await self._event(
                tx,
                scope,
                ep,
                "simulation_action.admitted",
                {"reservation_id": reservation.id, "operation": request.payload.op},
            )
            await self._remember(tx, scope, "dispatch", ep.id, body, reservation)
            # Collaborator lookups and even the final ledger write can wait past
            # an authority deadline. Recheck after every awaited lookup/write,
            # using the locked originals and the unreserved budget. Refusal here
            # rolls back the reservation, budget, event and idempotency ledger.
            # No collaborator or further write may follow this clock read.
            now = await tx.now()
            require(
                lease.status == "ACTIVE" and now < min(lease.expires_at, lease.heartbeat_deadline),
                Code.LEASE_INVALID,
            )
            require(
                preflight.created_at <= now < preflight.valid_until,
                Code.PREFLIGHT_FAILED,
            )
            require(
                approval.created_at <= now < approval.expires_at,
                Code.APPROVAL_INVALID,
            )
            if issuance is not None:
                issuance.validate_current_context(
                    self.safety_context(
                        admission_episode,
                        lease,
                        approval,
                        receipt_id=issuance.receipt.contract_id,
                        now=now,
                    )
                )
            return AuthorityAdmission(fresh=True, reservation=reservation)

    async def finish_termination(
        self,
        scope: AuthorityScope,
        *,
        episode_id: str,
        reservation_id: str,
        response: ProtocolResponse,
    ) -> DispatchReservation:
        """Record normal termination and enter verification, never acceptance.

        The supervised host supplies the exact pending acknowledgement. Cleanup
        via authorize_read(TERMINATE), an expired lease or an uncertain dispatch
        cannot create this transition. No simulator call occurs under this lock.
        """
        parsed = parse_message(canonical_json(response))
        require(isinstance(parsed, ProtocolResponse), Code.INVALID_REQUEST)
        assert isinstance(parsed, ProtocolResponse)
        response = parsed
        response_json = canonical_json(response).decode()
        async with self._transaction(scope) as tx:
            await self._actor(tx, scope, service=True)
            ep = await self._get(tx, scope, RuntimeEpisode, episode_id)
            binding = await self._get(tx, scope, RuntimeBinding, ep.binding_id)
            require(scope.actor_id == binding.orchestrator_id, Code.CAPABILITY_DENIED)
            row = await self._get(tx, scope, DispatchReservation, reservation_id)
            require(row.episode_id == ep.id)
            request = ProtocolRequest.model_validate_json(row.request_json)
            require(isinstance(request.payload, TerminateRequest), Code.INVALID_REQUEST)
            validate_response_binding(request, response)
            require(
                not isinstance(response.outcome, ErrorOutcome)
                and isinstance(response.outcome.payload, TerminateResult),
                Code.ACKNOWLEDGEMENT_UNCERTAIN,
            )
            if row.status == "COMPLETED":
                require(row.response_json == response_json, Code.IDEMPOTENCY_CONFLICT)
                return row  # Historical receipt, even after subsequent cleanup.
            self._revision(scope, ep)
            require(
                ep.status == "RUNNING"
                and ep.reset_completed
                and ep.in_flight == row.id
                and row.status == "RESERVED"
                and row.request_sequence == ep.last_request_sequence,
                Code.EPISODE_STATE_CONFLICT,
            )
            lease, resource = await self._lease(tx, scope, ep)
            await self._check(
                tx,
                scope,
                "FINISH_TERMINATION",
                ep,
                lease,
                resource,
                request_json=row.request_json,
                response_json=response_json,
            )
            _, approval = await self._approval(tx, scope, ep, lease, consumed=True)
            preflight = await self._preflight(tx, scope, ep, lease)
            require(
                request.scope == self.execution_pins(ep, approval).command_scope(),
                Code.EPISODE_STATE_CONFLICT,
            )
            row = copy_update(
                row, status="COMPLETED", response_json=response_json, completed_at=await tx.now()
            )
            await tx.put(row)
            await self._event(
                tx,
                scope,
                copy_update(ep, status="VERIFYING", in_flight=None),
                "simulation_episode.terminated",
                {
                    "reservation_id": row.id,
                    "request_digest": request.request_digest,
                    "response_digest": response.response_digest,
                    "request_json": row.request_json,
                    "response_json": response_json,
                    "execution_state": "VERIFYING",
                },
            )
            # Waiting for any trusted check or final event write cannot turn
            # expired authority into a new VERIFYING transition.
            now = await tx.now()
            require(now < min(lease.expires_at, lease.heartbeat_deadline), Code.LEASE_INVALID)
            require(preflight.created_at <= now < preflight.valid_until, Code.PREFLIGHT_FAILED)
            require(approval.created_at <= now < approval.expires_at, Code.APPROVAL_INVALID)
            return row

    async def finish_dispatch(
        self,
        scope: AuthorityScope,
        *,
        episode_id: str,
        reservation_id: str,
        response: ProtocolResponse,
        live: LiveState,
    ) -> DispatchReservation:
        response = ProtocolResponse.model_validate_json(canonical_json(response))
        live = LiveState.model_validate_json(canonical_json(live))
        async with self._transaction(scope) as tx:
            ep = await self._get(tx, scope, RuntimeEpisode, episode_id)
            lease, resource = await self._lease(tx, scope, ep)
            await self._check(
                tx,
                scope,
                "FINISH_DISPATCH",
                ep,
                lease,
                resource,
                response_json=canonical_json(response).decode(),
                live=live,
            )
            require(scope.actor_id == lease.owner_principal_id, Code.CAPABILITY_DENIED)
            row = await self._get(tx, scope, DispatchReservation, reservation_id)
            require(row.episode_id == ep.id)
            raw = canonical_json(response).decode()
            if row.status == "COMPLETED":
                require(row.response_json == raw, Code.IDEMPOTENCY_CONFLICT)
                return row
            self._revision(scope, row)
            require(row.status == "RESERVED" and ep.in_flight == row.id)
            request = ProtocolRequest.model_validate_json(row.request_json)
            validate_response_binding(request, response)
            require(not isinstance(response.outcome, ErrorOutcome), Code.ACKNOWLEDGEMENT_UNCERTAIN)
            assert not isinstance(response.outcome, ErrorOutcome)
            result = response.outcome.payload
            require(isinstance(result, ExecuteResult | ObservationResult), Code.INVALID_CONTRACT)
            assert isinstance(result, ExecuteResult | ObservationResult)
            require(
                live.state == result.observation.state_binding() and ep.live is not None,
                Code.OBSERVATION_INVALID,
            )
            assert ep.live is not None
            require(
                result.observation.episode_id == ep.id
                and result.observation.lease
                == LeaseBinding(lease_id=lease.id, generation=lease.generation),
                Code.OBSERVATION_INVALID,
            )
            require(
                live.budget == ep.live.budget
                and live.phase == ep.live.phase
                and live.physics_step_ns == ep.live.physics_step_ns
                and live.max_preview_intervals == ep.live.max_preview_intervals
                and live.episode_start_sim_time_ns == ep.live.episode_start_sim_time_ns,
                Code.INVALID_CONTRACT,
            )
            if isinstance(request.payload, ResetRequest):
                require(
                    live.state.sim_time_ns == 0
                    and live.sequence == ep.live.sequence
                    and live.last_action_sim_time_ns == 0
                )
            else:
                assert isinstance(request.payload, ExecuteRequest)
                receipt = request.payload.safety.for_execution(SafetyDecisionReceipt)
                require(isinstance(result, ExecuteResult), Code.INVALID_CONTRACT)
                assert isinstance(result, ExecuteResult)
                require(
                    result.prepared_command_hash == receipt.decision.prepared_command_hash
                    and result.safety_decision_hash == receipt.content_hash,
                    Code.INVALID_CONTRACT,
                )
                require(
                    live.state.sim_time_ns > ep.live.state.sim_time_ns
                    and live.state.observation_sequence > ep.live.state.observation_sequence
                    and live.state.sim_time_ns <= receipt.decision.expires_at_sim_time_ns
                    and live.sequence == ep.live.sequence + 1
                    and live.last_action_sim_time_ns == live.state.sim_time_ns
                )
                require(
                    Fraction(live.state.sim_time_ns - live.episode_start_sim_time_ns, 1_000_000_000)
                    <= Fraction(live.budget.elapsed_sim_seconds),
                    Code.RESOURCE_CAP_EXHAUSTED,
                )
            row = copy_update(
                row, status="COMPLETED", response_json=raw, completed_at=await tx.now()
            )
            await tx.put(row)
            ep = copy_update(ep, status="RUNNING", live=live, in_flight=None, reset_completed=True)
            await self._event(
                tx, scope, ep, "simulation_dispatch.completed", {"reservation_id": row.id}
            )
            return row

    async def mark_uncertain(
        self, scope: AuthorityScope, *, episode_id: str, reservation_id: str
    ) -> DispatchReservation:
        async with self._transaction(scope) as tx:
            await self._actor(tx, scope, service=True)
            ep = await self._get(tx, scope, RuntimeEpisode, episode_id)
            lease, resource = await self._lease(tx, scope, ep, live=False)
            require(
                scope.actor_id in {lease.owner_principal_id, resource.host_principal_id},
                Code.CAPABILITY_DENIED,
            )
            row = await self._get(tx, scope, DispatchReservation, reservation_id)
            require(row.episode_id == ep.id)
            if row.status == "UNCERTAIN":
                return row
            self._revision(scope, row)
            require(row.status == "RESERVED" and ep.in_flight == row.id)
            row = copy_update(row, status="UNCERTAIN", completed_at=await tx.now())
            await tx.put(row)
            await tx.put(copy_update(lease, status="REVOKED"))
            await tx.put(copy_update(resource, quarantined=True))
            ep = copy_update(ep, status="ABORTED")
            await self._event(
                tx, scope, ep, "simulation_dispatch.uncertain", {"reservation_id": row.id}
            )
            return row

    async def get_episode(self, scope: AuthorityScope, episode_id: str) -> RuntimeEpisode:
        async with self._transaction(scope) as tx:
            await self._actor(tx, scope)
            return await self._get(tx, scope, RuntimeEpisode, episode_id)

    async def get_lease(self, scope: AuthorityScope, lease_id: str) -> RuntimeLease:
        async with self._transaction(scope) as tx:
            await self._actor(tx, scope)
            return await self._get(tx, scope, RuntimeLease, lease_id)

    async def authorize_read(
        self,
        scope: AuthorityScope,
        *,
        episode_id: str,
        request: ProtocolRequest,
        pins: ExecutionPins,
    ) -> AuthorityAdmission:
        """Current authorization for no-advance operations; never spends safety budget.

        RESTORE is deliberately refused until a fresh replay authority is stored.
        HELLO is handshake-only. Preflight inspection precedes an admitted SDK
        session and must use the host's separately authenticated read boundary.
        """
        request = ProtocolRequest.model_validate_json(canonical_json(request))
        require(
            request.payload.op
            in {"DESCRIBE", "OBSERVE", "PREPARE", "SNAPSHOT", "HEARTBEAT", "TERMINATE"},
            Code.INVALID_REQUEST,
        )
        async with self._transaction(scope) as tx:
            await self._actor(tx, scope, service=True)
            ep = await self._get(tx, scope, RuntimeEpisode, episode_id)
            lease, resource = await self._lease(
                tx, scope, ep, live=not isinstance(request.payload, TerminateRequest)
            )
            require(scope.actor_id == lease.owner_principal_id, Code.CAPABILITY_DENIED)
            if isinstance(request.payload, TerminateRequest):
                # Cleanup is still possible after revocation/expiry/owner disable.
                require(
                    request.scope is not None
                    and request.scope.lease
                    == LeaseBinding(lease_id=lease.id, generation=lease.generation),
                    Code.LEASE_INVALID,
                )
                assert request.scope is not None
                require(
                    request.scope.workspace_id == scope.workspace_id
                    and request.scope.project_id == scope.project_id
                    and request.scope.episode_id == ep.id,
                    Code.LEASE_INVALID,
                )
                return AuthorityAdmission(fresh=True)
            await self._check(
                tx,
                scope,
                request.payload.op,
                ep,
                lease,
                resource,
                request_json=canonical_json(request).decode(),
            )
            _, approval = await self._approval(tx, scope, ep, lease, consumed=True)
            require(
                canonical_json(pins) == canonical_json(self.execution_pins(ep, approval))
                and request.scope == pins.command_scope(),
                Code.EPISODE_STATE_CONFLICT,
            )
            require(ep.status in {"APPROVED", "RUNNING"} and ep.in_flight is None)
            if isinstance(request.payload, PrepareRequest):
                intent = request.payload.intent.for_execution(ActionIntent)
                require(
                    ep.status == "RUNNING"
                    and intent.state == pins.state
                    and intent.episode_id == ep.id
                    and intent.sequence == pins.next_action_sequence
                    and (intent.workspace_id, intent.project_id)
                    == (scope.workspace_id, scope.project_id),
                    Code.INVALID_CONTRACT,
                )
            return AuthorityAdmission(fresh=True)

    async def fence_uncertain(
        self,
        scope: AuthorityScope,
        *,
        episode_id: str,
        lease_binding: LeaseBinding,
        request_digest: Digest,
    ) -> RuntimeEpisode:
        """Internal supervisor emergency fence, including a lost reservation ID.

        This can only remove authority. It deliberately reads the latest revision
        under lock: no public write/ETag bypass and no retry of the original call.
        An old lease may never fence a newer generation or a different resource.
        """
        request_digest = TypeAdapter(Digest).validate_python(request_digest)
        lease_binding = LeaseBinding.model_validate(lease_binding)
        async with self._transaction(scope) as tx:
            await self._actor(tx, scope, service=True)
            ep = await self._get(tx, scope, RuntimeEpisode, episode_id)
            lease, resource = await self._lease(tx, scope, ep, live=False)
            require(
                scope.actor_id in {lease.owner_principal_id, resource.host_principal_id},
                Code.CAPABILITY_DENIED,
            )
            require(
                lease_binding == LeaseBinding(lease_id=lease.id, generation=lease.generation),
                Code.LEASE_INVALID,
            )
            if ep.status == "ABORTED":
                return ep
            require(
                resource.current_lease_id == lease.id and resource.generation == lease.generation,
                Code.LEASE_INVALID,
            )
            if ep.in_flight:
                row = await self._get(tx, scope, DispatchReservation, ep.in_flight)
                # A supervisor's lost result may concern a nonmutation callback;
                # conservatively quarantine any current dispatch, never refund it.
                require(row.status == "RESERVED", Code.EPISODE_STATE_CONFLICT)
                await tx.put(copy_update(row, status="UNCERTAIN", completed_at=await tx.now()))
            await tx.put(copy_update(lease, status="REVOKED"))
            await tx.put(copy_update(resource, quarantined=True))
            ep = copy_update(ep, status="ABORTED")
            return await self._event(
                tx,
                scope,
                ep,
                "simulation_dispatch.uncertain",
                {"request_digest": request_digest, "reservation_id": ep.in_flight},
            )

    async def list_episodes(
        self, scope: AuthorityScope, *, after: str = "", limit: int = 25
    ) -> list[RuntimeEpisode]:
        """Bounded restart/reconciliation inventory; listing grants no invocation."""
        async with self._transaction(scope) as tx:
            await self._actor(tx, scope)
            return await tx.list_rows(
                RuntimeEpisode,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                after=after,
                limit=limit,
            )

    async def current_safety_context(
        self, scope: AuthorityScope, *, episode_id: str, receipt_id: str
    ) -> SafetyContext:
        async with self._transaction(scope) as tx:
            ep = await self._get(tx, scope, RuntimeEpisode, episode_id)
            lease, resource = await self._lease(tx, scope, ep)
            await self._check(tx, scope, "SAFETY_CONTEXT", ep, lease, resource)
            require(
                scope.actor_id in {lease.owner_principal_id, ep.setup.evaluator_principal_id},
                Code.CAPABILITY_DENIED,
            )
            require(ep.status == "RUNNING" and ep.in_flight is None)
            _, approval = await self._approval(tx, scope, ep, lease, consumed=True)
            return self.safety_context(
                ep, lease, approval, receipt_id=receipt_id, now=await tx.now()
            )
