"""Current policy eligibility and independently attested conformance authority.

All reads and writes use the caller's project transaction. Installation is an
internal human-maintainer operation; it cannot issue an episode approval, make a
report PASS, or install a trust root. Deployment must supply the independent
attestor. Nothing in this module executes a capability or contacts a host.
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Protocol

from pydantic import AwareDatetime, ConfigDict, model_validator
from sqlalchemy.exc import IntegrityError

from accretion.contracts import (
    AuthorizationOutcome,
    CapabilityPolicy,
    CapabilityRequest,
    PrincipalRef,
    PrincipalType,
    Provider,
    RiskLevel,
    RunState,
    StrictModel,
    TaskType,
)
from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics import (
    AdapterConformanceReport,
    CanonicalWriterEnvelope,
    EmbodimentDescriptor,
    RobotAdapterManifest,
    SimulationDomainEvent,
)
from accretion.contracts.robotics.models import SIMULATION_CAPABILITIES, RoboticsContract
from accretion.governance import CapabilityPolicyEngine
from accretion.ids import new_id
from accretion.robotics.authority import (
    AuthorityCheck,
    AuthorityScope,
    SimulationAuthority,
    original,
    require,
)
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.protocol import ProtocolResponse, SuccessOutcome, parse_message
from accretion.robotics.registry import _scoped
from accretion.robotics.runtime_store import (
    ConformanceAdmissionSpec,
    InventoryRecord,
    PolicyGrantSpec,
    RuntimeBinding,
    RuntimeConformanceAdmission,
    RuntimePolicyGrant,
    RuntimeStore,
    RuntimeTransaction,
)
from accretion.robotics.store import IdempotencyScope, LedgerRecord, StoredEvent


class AttestationValidity(StrictModel):
    """Validity authenticated by the configured verifier, never by report labels."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    valid_from: AwareDatetime
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def _ordered(self) -> AttestationValidity:
        if self.valid_from >= self.valid_until:
            raise ValueError("invalid attestation interval")
        return self


class CurrentConformanceAttestor(Protocol):
    async def verify(
        self,
        tx: RuntimeTransaction,
        report: CanonicalWriterEnvelope,
        *,
        attestation_original_json: str,
        now: datetime,
    ) -> AttestationValidity:
        """Authenticate exact report/closure/suite/verifier and current trust.

        Mandatory deployment-owned implementation with independent admitted
        runner/key configuration. Check retained attestation bytes, authenticated
        validity, runner isolation and current revocation; report claims or a key
        supplied in those bytes are insufficient. Only bounded local verification
        and reads in ``tx`` are permitted: no network, host/artifact IO, separate
        transactions, signing or inferred trust. Raise on missing/corrupt trust.
        """
        ...


def policy_grant_id(workspace: str, project: str, operator: str, orchestrator: str) -> str:
    return content_hash(
        ["simulation-policy", workspace, project, operator, orchestrator], exclude=()
    )


def conformance_admission_id(workspace: str, project: str, adapter: str, closure: str) -> str:
    return content_hash(
        ["simulation-conformance", workspace, project, adapter, closure], exclude=()
    )


async def _contract[C: RoboticsContract](
    tx: RuntimeTransaction, workspace: str, project: str, identity: str, model: type[C]
) -> C:
    # Reuse registry original/index validation and exact-writer version checks.
    row = _scoped(await tx.registry.get(identity), workspace, project)
    return original(row.original_json, model)


async def _manifest(tx: RuntimeTransaction, check: AuthorityCheck) -> RobotAdapterManifest:
    ep = check.episode
    row = _scoped(
        await tx.registry.lookup(
            ep.workspace_id,
            ep.project_id,
            RobotAdapterManifest.CONTRACT_TYPE,
            digest=ep.setup.adapter_manifest_hash,
        ),
        ep.workspace_id,
        ep.project_id,
    )
    manifest = original(row.original_json, RobotAdapterManifest)
    require(manifest.content_hash == ep.setup.adapter_manifest_hash, Code.CONFORMANCE_STALE)
    return manifest


def _scope(record: InventoryRecord, workspace: str, project: str) -> None:
    require(
        (record.workspace_id, record.project_id) == (workspace, project), Code.RESOURCE_NOT_FOUND
    )


def _current(tx: RuntimeTransaction, row: InventoryRecord, now: datetime, code: Code) -> None:
    require(row.disposition == "ACTIVE" and row.valid_from <= now < row.valid_until, code)
    tx.require_valid_interval(row.valid_from, row.valid_until, code)


async def _inventory_provenance(
    tx: RuntimeTransaction, row: InventoryRecord, *, current_editor: bool = True
) -> None:
    anchor = await tx.registry.get(row.event_stream_id)
    require(anchor is not None, Code.INVALID_CONTRACT)
    assert anchor is not None
    first = original(anchor.original_json, SimulationDomainEvent)
    expected = SimulationAuthority._record(anchor.original_json, first)
    first_record = first.payload.get("record")
    require(isinstance(first_record, dict), Code.INVALID_CONTRACT)
    assert isinstance(first_record, dict)
    require(
        all(
            getattr(anchor, key) == getattr(expected, key)
            for key in expected.__dataclass_fields__
            if key != "revision"
        )
        and anchor.revision == row.revision
        and first.contract_id == first.correlation_id == row.event_stream_id
        and (first.workspace_id, first.project_id) == (row.workspace_id, row.project_id)
        and first.sequence == 1
        and first.payload.get("operation") == "INSTALL"
        and first_record.get("id") == row.id,
        Code.INVALID_CONTRACT,
    )
    events = await tx.registry.events(row.event_stream_id, after=row.revision - 1, limit=1)
    require(bool(events), Code.INVALID_CONTRACT)
    stored = events[0]
    event = original(stored.original_json, SimulationDomainEvent)
    kind = "policy" if isinstance(row, RuntimePolicyGrant) else "conformance"
    require(
        stored.id == event.contract_id
        and stored.content_hash == event.content_hash
        and (stored.workspace_id, stored.project_id) == (row.workspace_id, row.project_id)
        and (event.workspace_id, event.project_id) == (row.workspace_id, row.project_id)
        and stored.aggregate_id == event.correlation_id == row.event_stream_id
        and stored.sequence == event.sequence == row.revision
        and event.event_type == "simulation_authority." + kind + "_changed"
        and event.created_by.principal_id == row.updated_by
        and event.created_at == event.occurred_at == row.updated_at
        and event.payload.get("record") == row.model_dump(mode="json")
        and event.payload.get("operation")
        == ("INSTALL" if row.disposition == "ACTIVE" else row.disposition),
        Code.INVALID_CONTRACT,
    )
    if current_editor:
        await tx.registry.authorize(row.updated_by, row.workspace_id, row.project_id, write=True)


async def _policy_material(
    tx: RuntimeTransaction, spec: PolicyGrantSpec | RuntimePolicyGrant
) -> CapabilityPolicy:
    policy = await tx.capability_policy(spec.policy_ref.policy_id, spec.policy_ref.version)
    require(policy is not None, Code.CAPABILITY_DENIED)
    assert policy is not None
    require(
        content_hash(policy, exclude=()) == spec.policy_ref.content_digest, Code.CAPABILITY_DENIED
    )
    for pin in spec.capabilities:
        require(pin.capability_id in SIMULATION_CAPABILITIES, Code.CAPABILITY_DENIED)
        cap = await tx.capability(pin.capability_id, pin.capability_version)
        require(cap is not None, Code.CAPABILITY_DENIED)
        assert cap is not None
        require(
            cap.enabled and content_hash(cap, exclude=()) == pin.content_digest,
            Code.CAPABILITY_DENIED,
        )
    return policy


async def _principals(
    tx: RuntimeTransaction, workspace: str, project: str, operator: str, orchestrator: str
) -> None:
    person = await tx.registry.authorize(operator, workspace, project)
    require(person.type is PrincipalType.HUMAN and operator != orchestrator, Code.CAPABILITY_DENIED)
    await tx.registry.authorize(orchestrator, workspace, project, service=True)


async def _report_provenance(
    tx: RuntimeTransaction,
    workspace: str,
    project: str,
    adapter_id: str,
    report_id: str,
    report_hash: str,
    closure_hash: str,
) -> tuple[RobotAdapterManifest, AdapterConformanceReport, CanonicalWriterEnvelope]:
    manifest = await _contract(tx, workspace, project, adapter_id, RobotAdapterManifest)
    row = _scoped(await tx.registry.get(report_id), workspace, project)
    envelope = CanonicalWriterEnvelope(row.original_json)
    report = original(row.original_json, AdapterConformanceReport)
    require(
        report.content_hash == report_hash
        and report.adapter_manifest_hash == manifest.content_hash
        and content_hash(report.dependencies, exclude=()) == closure_hash
        and report.result == "PASS"
        and report.tests_passed == report.tests_total
        and report.adapter_producer_principal.principal_id == manifest.created_by.principal_id,
        Code.CONFORMANCE_STALE,
    )
    await tx.registry.authorize(report.adapter_producer_principal.principal_id, workspace, project)
    await tx.registry.authorize(
        report.verifier_principal.principal_id, workspace, project, service=True
    )
    # Current exact-closure result only: a newer FAIL cannot fall back to old PASS.
    links = await tx.registry.conformance(adapter_id, closure_hash, limit=1)
    require(bool(links), Code.CONFORMANCE_STALE)
    link = links[0]
    adapter_row = _scoped(await tx.registry.get(adapter_id), workspace, project)
    require(
        link.adapter_id == adapter_id
        and link.report_id == report_id
        and link.closure_hash == closure_hash
        and 1 < link.revision <= adapter_row.revision,
        Code.CONFORMANCE_STALE,
    )
    events = await tx.registry.events(adapter_id, after=link.revision - 1, limit=1)
    require(bool(events), Code.CONFORMANCE_STALE)
    stored = events[0]
    event = original(stored.original_json, SimulationDomainEvent)
    require(
        stored.id == event.contract_id
        and stored.content_hash == event.content_hash
        and (stored.workspace_id, stored.project_id) == (workspace, project)
        and (event.workspace_id, event.project_id) == (workspace, project)
        and stored.aggregate_id == event.correlation_id == adapter_id
        and stored.sequence == event.sequence == link.revision
        and event.event_type == "robot_adapter.conformance_completed"
        and event.created_by.principal_id == report.created_by.principal_id
        and event.payload.get("report_id") == report_id
        and event.payload.get("report_hash") == report_hash
        and event.payload.get("result") == "PASS",
        Code.CONFORMANCE_STALE,
    )
    deps = report.dependencies
    require(
        deps.adapter_artifact_digest == manifest.artifact_digest
        and deps.observation_spec_hash == manifest.observation_spec_hash
        and deps.action_intent_schema_hash == manifest.action_intent_schema_hash
        and deps.prepared_command_schema_hash == manifest.prepared_command_schema_hash,
        Code.CONFORMANCE_STALE,
    )
    # The registry admitted the descriptor dependency. Re-resolve the exact sealed
    # current originals instead of treating a stored PASS as ongoing provenance.
    model_digests = set()
    for digest in manifest.embodiment_descriptor_hashes:
        descriptor_row = _scoped(
            await tx.registry.lookup(
                workspace, project, EmbodimentDescriptor.CONTRACT_TYPE, digest=digest
            ),
            workspace,
            project,
        )
        descriptor = original(descriptor_row.original_json, EmbodimentDescriptor)
        model_digests.add(descriptor.robot_model_ref.digest)
    require(deps.robot_model_digest in model_digests, Code.CONFORMANCE_STALE)
    return manifest, report, envelope


async def _attest(
    tx: RuntimeTransaction,
    attestor: CurrentConformanceAttestor | None,
    report: CanonicalWriterEnvelope,
    proof: str,
    now: datetime,
) -> AttestationValidity:
    require(attestor is not None, Code.VERIFIER_UNAVAILABLE)
    assert attestor is not None
    try:
        validity = AttestationValidity.model_validate_json(
            canonical_json(
                await attestor.verify(
                    tx,
                    report,
                    attestation_original_json=proof,
                    now=now,
                )
            )
        )
    except (ValueError, TypeError, OSError) as exc:
        raise RoboticsError(Code.CONFORMANCE_STALE) from exc
    require(validity.valid_from <= now < validity.valid_until, Code.CONFORMANCE_STALE)
    tx.require_valid_interval(validity.valid_from, validity.valid_until, Code.CONFORMANCE_STALE)
    return validity


class AuthorityInventory:
    """Internal attributable installation/revocation; no live grants are seeded."""

    def __init__(self, store: RuntimeStore, *, attestor: CurrentConformanceAttestor | None = None):
        self.store, self.attestor = store, attestor

    async def install_policy(
        self, scope: AuthorityScope, *, spec: PolicyGrantSpec
    ) -> RuntimePolicyGrant:
        spec = PolicyGrantSpec.model_validate_json(canonical_json(spec))
        identity = policy_grant_id(
            scope.workspace_id, scope.project_id, spec.operator_id, spec.orchestrator_id
        )
        return await self._change(scope, identity, RuntimePolicyGrant, "INSTALL", spec)

    async def install_conformance(
        self, scope: AuthorityScope, *, spec: ConformanceAdmissionSpec
    ) -> RuntimeConformanceAdmission:
        spec = ConformanceAdmissionSpec.model_validate_json(canonical_json(spec))
        identity = conformance_admission_id(
            scope.workspace_id, scope.project_id, spec.adapter_contract_id, spec.closure_hash
        )
        return await self._change(scope, identity, RuntimeConformanceAdmission, "INSTALL", spec)

    async def revoke_policy(self, scope: AuthorityScope, *, grant_id: str) -> RuntimePolicyGrant:
        return await self._change(scope, grant_id, RuntimePolicyGrant, "REVOKED", None)

    async def revoke_conformance(
        self, scope: AuthorityScope, *, admission_id: str, quarantine: bool = False
    ) -> RuntimeConformanceAdmission:
        return await self._change(
            scope,
            admission_id,
            RuntimeConformanceAdmission,
            "QUARANTINED" if quarantine else "REVOKED",
            None,
        )

    async def _change[C: InventoryRecord](
        self,
        scope: AuthorityScope,
        identity: str,
        model: type[C],
        operation: str,
        spec: PolicyGrantSpec | ConformanceAdmissionSpec | None,
    ) -> C:
        scope = AuthorityScope.model_validate_json(canonical_json(scope))
        kind = "policy" if model is RuntimePolicyGrant else "conformance"
        ledger_scope = IdempotencyScope(
            scope.workspace_id,
            scope.project_id,
            scope.actor_id,
            "authority_inventory." + kind,
            identity,
            scope.idempotency_key,
        )
        body = {"operation": operation, "spec": spec, "expected_revision": scope.expected_revision}
        digest = content_hash(body, exclude=())
        try:
            async with self.store.transaction(scope.project_id) as tx:
                actor = await tx.registry.authorize(
                    scope.actor_id, scope.workspace_id, scope.project_id, write=True
                )
                current = await tx.get(model, identity)
                if current:
                    _scope(current, scope.workspace_id, scope.project_id)
                previous = await tx.registry.ledger(ledger_scope)
                if previous:
                    require(previous.request_hash == digest, Code.IDEMPOTENCY_CONFLICT)
                    response = model.model_validate_json(previous.response_json)
                    _scope(response, scope.workspace_id, scope.project_id)
                    require(
                        current is not None
                        and response.id == identity
                        and response.revision <= current.revision,
                        Code.INVALID_CONTRACT,
                    )
                    # Historical installation receipt only. Providers ALWAYS read
                    # current inventory; replay cannot reactivate a revoked grant.
                    return response
                if current is None:
                    require(operation == "INSTALL", Code.RESOURCE_NOT_FOUND)
                    require(scope.expected_revision is None, Code.REVISION_CONFLICT)
                else:
                    require(scope.expected_revision is not None, Code.REVISION_REQUIRED)
                    require(scope.expected_revision == current.revision, Code.REVISION_CONFLICT)
                now = await tx.now()
                if operation == "INSTALL":
                    assert spec is not None
                    require(
                        now < spec.valid_until
                        and (spec.valid_until - now).total_seconds() <= 86_400,
                        Code.INVALID_REQUEST,
                    )
                    if isinstance(spec, PolicyGrantSpec):
                        await _principals(
                            tx,
                            scope.workspace_id,
                            scope.project_id,
                            spec.operator_id,
                            spec.orchestrator_id,
                        )
                        await _policy_material(tx, spec)
                        fields = spec.model_dump(mode="python")
                    else:
                        manifest, report, envelope = await _report_provenance(
                            tx,
                            scope.workspace_id,
                            scope.project_id,
                            spec.adapter_contract_id,
                            spec.report_id,
                            spec.report_hash,
                            spec.closure_hash,
                        )
                        validity = await _attest(
                            tx, self.attestor, envelope, spec.attestation_original_json, now
                        )
                        require(spec.valid_until <= validity.valid_until, Code.CONFORMANCE_STALE)
                        fields = spec.model_dump(mode="python") | {
                            "adapter_manifest_hash": manifest.content_hash,
                            "verifier": report.verifier,
                            "verifier_principal_id": report.verifier_principal.principal_id,
                            "suite_version": report.suite_version,
                            "suite_artifact_digest": report.suite_artifact_digest,
                            "attestation_digest": sha256(
                                spec.attestation_original_json.encode()
                            ).hexdigest(),
                        }
                    fields.update(valid_from=now, disposition="ACTIVE")
                    tx.require_valid_interval(
                        now,
                        spec.valid_until,
                        Code.CAPABILITY_DENIED if kind == "policy" else Code.CONFORMANCE_STALE,
                    )
                else:
                    assert current is not None
                    fields = current.model_dump(mode="python") | {"disposition": operation}
                fields.update(
                    id=identity,
                    event_stream_id=current.event_stream_id
                    if current
                    else new_id("simulation_domain_event"),
                    workspace_id=scope.workspace_id,
                    project_id=scope.project_id,
                    revision=current.revision + 1 if current else 1,
                    created_at=current.created_at if current else now,
                    created_by=current.created_by if current else actor.principal_id,
                    updated_at=now,
                    updated_by=actor.principal_id,
                )
                response = model.model_validate(fields)
                event = SimulationDomainEvent.model_validate(
                    dict(
                        contract_id=new_id("simulation_domain_event")
                        if current
                        else response.event_stream_id,
                        workspace_id=scope.workspace_id,
                        project_id=scope.project_id,
                        created_at=now,
                        created_by=PrincipalRef(
                            principal_id=actor.principal_id, status=actor.status
                        ),
                        event_type="simulation_authority." + kind + "_changed",
                        occurred_at=now,
                        correlation_id=response.event_stream_id,
                        producer=PrincipalRef(principal_id=actor.principal_id, status=actor.status),
                        sequence=response.revision,
                        payload={
                            "operation": operation,
                            "record": response.model_dump(mode="json"),
                        },
                    )
                )
                if current is None:
                    # Preserve the event table's canonical aggregate FK. The
                    # original first installation event anchors this stream;
                    # its writer bytes never change as the stream revision grows.
                    await tx.registry.insert(
                        SimulationAuthority._record(canonical_json(event).decode(), event)
                    )
                else:
                    await _inventory_provenance(tx, current, current_editor=False)
                    await tx.registry.set_revision(current.event_stream_id, response.revision)
                await tx.put(response, insert=current is None)
                await tx.registry.append_event(
                    StoredEvent(
                        event.contract_id,
                        response.event_stream_id,
                        scope.workspace_id,
                        scope.project_id,
                        event.sequence,
                        event.content_hash,
                        canonical_json(event).decode(),
                    )
                )
                await tx.registry.remember(
                    LedgerRecord(ledger_scope, digest, canonical_json(response).decode())
                )
                return response
        except IntegrityError as exc:
            raise RoboticsError(Code.CONTRACT_CONFLICT) from exc


_ALL_CAPABILITIES = frozenset(
    {
        "BIND_RUN",
        "ACQUIRE_LEASE",
        "HEARTBEAT",
        "PREFLIGHT",
        "PREFLIGHT_COMMIT",
        "APPROVE",
        "CONSUME_APPROVAL",
    }
)
_OPERATIONS = {
    "DESCRIBE": "robotics.sim.inspect",
    "OBSERVE": "robotics.sim.observe",
    "PREPARE": "robotics.sim.propose_action",
    "ISSUE_SAFETY": "robotics.sim.propose_action",
    "SAFETY_CONTEXT": "robotics.sim.propose_action",
    "RESERVE_EXECUTE": "robotics.sim.propose_action",
    "RESERVE_RESET": "robotics.sim.reset",
    "SNAPSHOT": "robotics.sim.snapshot",
    "RESERVE_TERMINATE": "robotics.sim.terminate",
    "FINISH_TERMINATION": "robotics.sim.terminate",
}


class InventoryPolicyAuthority:
    """Policy eligibility only; the SimulationAuthority approval gate is mandatory."""

    async def check(self, tx: RuntimeTransaction, check: AuthorityCheck) -> None:
        await tx.require_scope(check.episode.workspace_id, check.episode.project_id)
        ep = check.episode
        await tx.registry.authorize(check.actor_id, ep.workspace_id, ep.project_id)
        run, task = await tx.run_task(ep.run_id)
        binding = await tx.get(RuntimeBinding, ep.binding_id)
        require(binding is not None, Code.CAPABILITY_DENIED)
        assert binding is not None
        require(
            (binding.workspace_id, binding.project_id, binding.episode_id, binding.run_id)
            == (ep.workspace_id, ep.project_id, ep.id, ep.run_id)
            and run.principal_id == binding.initiator_id
            and run.project_id == task.envelope.project_id == ep.project_id
            and run.task_id == task.envelope.task_id
            and run.provider is Provider.DETERMINISTIC
            and run.state not in {RunState.CANCELLED, RunState.FAILED, RunState.SUCCEEDED}
            and task.envelope.task_type is TaskType.EXPERIMENT
            and task.envelope.risk_level is RiskLevel.HIGH,
            Code.CAPABILITY_DENIED,
        )
        await _principals(
            tx, ep.workspace_id, ep.project_id, binding.initiator_id, binding.orchestrator_id
        )
        identity = policy_grant_id(
            ep.workspace_id, ep.project_id, binding.initiator_id, binding.orchestrator_id
        )
        row = await tx.get(RuntimePolicyGrant, identity)
        require(row is not None, Code.CAPABILITY_DENIED)
        assert row is not None
        _scope(row, ep.workspace_id, ep.project_id)
        await _inventory_provenance(tx, row)
        require(
            row.operator_id == binding.initiator_id
            and row.orchestrator_id == binding.orchestrator_id
            and row.policy_ref == ep.setup.policy_ref,
            Code.CAPABILITY_DENIED,
        )
        _current(tx, row, await tx.now(), Code.CAPABILITY_DENIED)
        policy = await _policy_material(tx, row)
        manifest = await _manifest(tx, check)
        declared = {c.capability_id: c.capability_version for c in manifest.capabilities}
        if check.operation in _ALL_CAPABILITIES:
            required = set(declared)
        elif check.operation == "FINISH_DISPATCH":
            require(check.response_json is not None, Code.CAPABILITY_DENIED)
            response = parse_message(str(check.response_json).encode())
            require(
                isinstance(response, ProtocolResponse)
                and isinstance(response.outcome, SuccessOutcome),
                Code.CAPABILITY_DENIED,
            )
            assert isinstance(response, ProtocolResponse) and isinstance(
                response.outcome, SuccessOutcome
            )
            op = response.outcome.payload.op
            require(op in {"RESET", "EXECUTE"}, Code.CAPABILITY_DENIED)
            required = {_OPERATIONS["RESERVE_" + op]}
        else:
            require(check.operation in _OPERATIONS, Code.CAPABILITY_DENIED)
            required = {_OPERATIONS[check.operation]}
        require(bool(required) and required <= declared.keys(), Code.CAPABILITY_DENIED)
        pins = {c.capability_id: c for c in row.capabilities}
        require(required <= pins.keys(), Code.CAPABILITY_DENIED)
        engine = CapabilityPolicyEngine(granted_permissions=set(row.operator_permissions))
        for name in sorted(required):
            pin = pins[name]
            require(pin.capability_version == declared[name], Code.CAPABILITY_DENIED)
            cap = await tx.capability(name, pin.capability_version)
            assert cap is not None  # _policy_material checked exact versions under the same locks.
            # This deterministic metadata request cannot be sent to a capability
            # executor. Actual operations already require AuthorityScope's durable
            # idempotency key, and dispatch retains the exact protocol request.
            identity = content_hash([ep.id, ep.revision, check.operation], exclude=())
            request = CapabilityRequest(
                request_id=identity,
                run_id=run.run_id,
                node_id=ep.id,
                capability_id=name,
                capability_version=pin.capability_version,
                declared_reason="Simulation authority policy eligibility",
                idempotency_key=identity,
            )
            decision = engine.authorize(
                task=task, capability=cap, request=request, policy=policy, approval=None
            )
            require(
                decision.outcome
                in {AuthorizationOutcome.ALLOW, AuthorizationOutcome.REQUIRE_APPROVAL},
                Code.CAPABILITY_DENIED,
            )
            # REQUIRE_APPROVAL is deliberately only eligibility. Never manufacture
            # a legacy ApprovalRecord or project this result as execution ALLOW.
            # SimulationAuthority still authenticates the exact episode approval,
            # lease, signed safety issuance and one-time dispatch atomically.


class InventoryConformanceAuthority:
    def __init__(self, *, attestor: CurrentConformanceAttestor | None = None):
        self.attestor = attestor

    async def check(self, tx: RuntimeTransaction, check: AuthorityCheck) -> None:
        await tx.require_scope(check.episode.workspace_id, check.episode.project_id)
        ep = check.episode
        await tx.registry.authorize(check.actor_id, ep.workspace_id, ep.project_id)
        manifest = await _manifest(tx, check)
        closure_hash = content_hash(ep.setup.dependencies, exclude=())
        identity = conformance_admission_id(
            ep.workspace_id, ep.project_id, manifest.contract_id, closure_hash
        )
        row = await tx.get(RuntimeConformanceAdmission, identity)
        require(row is not None, Code.CONFORMANCE_STALE)
        assert row is not None
        _scope(row, ep.workspace_id, ep.project_id)
        await _inventory_provenance(tx, row)
        now = await tx.now()
        _current(tx, row, now, Code.CONFORMANCE_STALE)
        require(
            row.adapter_contract_id == manifest.contract_id
            and row.adapter_manifest_hash == manifest.content_hash
            and row.closure_hash == closure_hash,
            Code.CONFORMANCE_STALE,
        )
        _, report, envelope = await _report_provenance(
            tx,
            ep.workspace_id,
            ep.project_id,
            manifest.contract_id,
            row.report_id,
            row.report_hash,
            closure_hash,
        )
        descriptor = original(ep.setup.descriptor_original, EmbodimentDescriptor)
        require(
            (descriptor.workspace_id, descriptor.project_id) == (ep.workspace_id, ep.project_id)
            and descriptor.content_hash in manifest.embodiment_descriptor_hashes
            and descriptor.robot_model_ref.digest == ep.setup.dependencies.robot_model_digest
            and report.dependencies == ep.setup.dependencies
            and report.verifier == row.verifier
            and report.verifier_principal.principal_id == row.verifier_principal_id
            and report.suite_version == row.suite_version
            and report.suite_artifact_digest == row.suite_artifact_digest,
            Code.CONFORMANCE_STALE,
        )
        if check.resource:
            require(not check.resource.quarantined, Code.CONFORMANCE_STALE)
        validity = await _attest(tx, self.attestor, envelope, row.attestation_original_json, now)
        require(row.valid_until <= validity.valid_until, Code.CONFORMANCE_STALE)


async def checked_inventory_deadline(
    tx: RuntimeTransaction,
    check: AuthorityCheck,
    *,
    policy: InventoryPolicyAuthority,
    conformance: InventoryConformanceAuthority,
) -> datetime:
    """Internal bootstrap/read permit cap after current checks, in the same tx.

    Callers must ALSO check owning host, lease, preflight and approval as required.
    A return value is not a reusable permit. Transaction commit must succeed and
    the host must enforce the minimum of this and all other authority deadlines.
    """
    await policy.check(tx, check)
    await conformance.check(tx, check)
    deadline = tx.authority_valid_until
    require(deadline is not None, Code.CAPABILITY_DENIED)
    assert deadline is not None
    return deadline
