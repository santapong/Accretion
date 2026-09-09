"""Fixed image worker entrypoint; staged initialization grants no actuation.

Deployment supplies planned identity in a private read-only bootstrap, without
future preflight or approval records. The ready frame reports actual initial
observation; the supervisor records it before supplying original exact approvals.
Every subsequent SDK operation still crosses the live authority channel. This
module does not manufacture preflight, approval, conformance or acceptance.
"""

from __future__ import annotations

import argparse
import os
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Literal, Protocol

from pydantic import Field, model_validator

from accretion.contracts import EvidenceClass, PrincipalStatus
from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.refs import PolicyRef, VerifierRef
from accretion.contracts.robotics import (
    EmbodimentDescriptor,
    ObservationSpec,
    SimulationEpisodeApproval,
    SimulationPreflightReceipt,
    TrustedSafetyKey,
)
from accretion.contracts.robotics.values import Digest, EpisodeId, EpisodePins, Identifier
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.observations import ArtifactReader, ObservationBatch, ObservationValidator
from accretion.robotics.protocol import (
    MAX_FRAME_BYTES,
    AdapterDescription,
    ProtocolRequest,
    TerminateRequest,
    WireModel,
    WriterRecord,
    bounded_json,
    parse_message,
)
from accretion.robotics.sdk import (
    AdapterSession,
    AdmissionGuard,
    ExecutionPins,
    InitializationPins,
    RobotAdapter,
)

from .artifact_channel import UnixArtifacts
from .authority_channel import UnixAdmissionGuard

BOOTSTRAP_PATH = Path("/run/accretion/bootstrap.json")
MODELS_PATH = Path("/opt/accretion/models")


class PublicSafetyKey(WireModel):
    key_id: Identifier
    principal_id: Identifier
    public_key_hex: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)


class PlannedEpisodePins(InitializationPins):
    """Immutable plan inputs known before a worker's first observation exists."""

    experiment_contract_hash: Digest
    environment_snapshot_hash: Digest
    adapter_manifest_hash: Digest
    safety_envelope_hash: Digest
    verification_spec_hash: Digest

    @classmethod
    def from_full(cls, episode: EpisodePins) -> PlannedEpisodePins:
        return cls.model_validate({name: getattr(episode, name) for name in cls.model_fields})

    def initialization(self) -> InitializationPins:
        return InitializationPins.from_episode(self)


class WorkerBootstrap(WireModel):
    format: Literal["accretion.simulation-worker-bootstrap.v1"] = (
        "accretion.simulation-worker-bootstrap.v1"
    )
    worker_kind: Literal["UR5E"]
    episode: PlannedEpisodePins
    description: AdapterDescription
    orchestrator_principal_id: Identifier
    adapter_principal_id: Identifier
    evaluator_principal_id: Identifier
    policy_ref: PolicyRef
    evaluator: VerifierRef
    conformance_report_hash: Digest
    public_keys: tuple[PublicSafetyKey, ...] = Field(min_length=1, max_length=32)
    replay_source_episode_id: EpisodeId | None = None
    # These are upper bounds; live revocation/heartbeat remains host authority.
    authority_expires_at: datetime
    wall_seconds: int = Field(ge=1, le=86400, strict=True)
    cpu_seconds: int = Field(ge=1, le=86400, strict=True)

    @model_validator(mode="after")
    def _bindings(self) -> WorkerBootstrap:
        canonical_json(self)
        descriptor = self.description.descriptor.for_execution(EmbodimentDescriptor)
        observations = self.description.observation_spec.for_execution(ObservationSpec)
        if (
            descriptor.project_id is None
            or descriptor.workspace_id != observations.workspace_id
            or descriptor.project_id != observations.project_id
            or observations.content_hash != self.description.dependencies.observation_spec_hash
            or descriptor.created_by.principal_id != self.adapter_principal_id
            or self.replay_source_episode_id == self.episode.episode_id
            or len({key.key_id for key in self.public_keys}) != len(self.public_keys)
            or any(key.principal_id != self.evaluator_principal_id for key in self.public_keys)
            or len(
                {
                    self.orchestrator_principal_id,
                    self.adapter_principal_id,
                    self.evaluator_principal_id,
                }
            )
            != 3
        ):
            raise ValueError("bootstrap identity or dependency mismatch")
        return self

    def keys(self) -> dict[str, TrustedSafetyKey]:
        return {
            item.key_id: TrustedSafetyKey(
                principal_id=item.principal_id, public_key=bytes.fromhex(item.public_key_hex)
            )
            for item in self.public_keys
        }

    def validate_initial(self, initial: ObservationBatch) -> None:
        descriptor = self.description.descriptor.for_execution(EmbodimentDescriptor)
        if (
            initial.episode_id != self.episode.episode_id
            or initial.lease != self.episode.lease
            or initial.sequence != 0
            or initial.sim_time_ns != 0
            or initial.frame_transform_digest
            != descriptor.kinematics.transform_provenance_ref.digest
        ):
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)

    def pins(
        self,
        initial: ObservationBatch,
        preflight: WriterRecord,
        approval: WriterRecord,
    ) -> ExecutionPins:
        """Derive proposed full pins; validate_activation and live guard remain required."""
        self.validate_initial(initial)
        descriptor = self.description.descriptor.for_execution(EmbodimentDescriptor)
        preflight_record = preflight.for_execution(SimulationPreflightReceipt)
        approval_record = approval.for_execution(SimulationEpisodeApproval)
        if any(
            getattr(preflight_record, name) != getattr(self.episode, name)
            for name in PlannedEpisodePins.model_fields
        ):
            raise RoboticsError(Code.PREFLIGHT_FAILED)
        episode = EpisodePins.model_validate(
            {
                **self.episode.model_dump(mode="python"),
                "preflight_receipt_hash": preflight_record.content_hash,
            }
        )
        if approval_record.pins != episode or approval_record.policy_ref != self.policy_ref:
            raise RoboticsError(Code.APPROVAL_INVALID)
        return ExecutionPins(
            workspace_id=descriptor.workspace_id,
            project_id=descriptor.project_id,
            episode=episode,
            dependencies=self.description.dependencies,
            descriptor_hash=descriptor.content_hash,
            adapter_principal_id=self.adapter_principal_id,
            evaluator_principal_id=self.evaluator_principal_id,
            policy_ref=self.policy_ref,
            evaluator=self.evaluator,
            approval_hash=approval_record.content_hash,
            state=initial.state_binding(),
            budget=dict(actions=0, cumulative_joint_motion_rad=0.0, elapsed_sim_seconds=0.0),
            next_action_sequence=0,
            replay_source_episode_id=self.replay_source_episode_id,
        )


class WorkerReady(WireModel):
    format: Literal["accretion.simulation-worker-ready.v1"] = "accretion.simulation-worker-ready.v1"
    bootstrap_hash: Digest
    description: AdapterDescription
    initial_observation: ObservationBatch
    observed_at: datetime

    @model_validator(mode="after")
    def _time(self) -> WorkerReady:
        canonical_json(self)
        return self


class WorkerActivation(WireModel):
    format: Literal["accretion.simulation-worker-activation.v1"] = (
        "accretion.simulation-worker-activation.v1"
    )
    ready_hash: Digest
    pins: ExecutionPins
    preflight: WriterRecord
    approval: WriterRecord

    @model_validator(mode="after")
    def _originals(self) -> WorkerActivation:
        self.preflight.for_execution(SimulationPreflightReceipt)
        self.approval.for_execution(SimulationEpisodeApproval)
        return self


def validate_activation(
    bootstrap: WorkerBootstrap,
    ready: WorkerReady,
    activation: WorkerActivation,
    *,
    now: datetime | None = None,
) -> ExecutionPins:
    """Verify exact staged construction. Original records never grant live authority.

    The supervisor must authenticate persisted HUMAN approval, current service
    membership, lease, policy and conformance again through the live guard. This
    helper checks the original records, actual ready metadata and immutable pins.
    The worker/supervisor separately validate the complete initial tensor bytes.
    """
    bootstrap = WorkerBootstrap.model_validate(bounded_json(canonical_json(bootstrap)))
    ready = WorkerReady.model_validate(bounded_json(canonical_json(ready)))
    activation = WorkerActivation.model_validate(bounded_json(canonical_json(activation)))
    current = datetime.now(UTC) if now is None else now
    canonical_json(current)
    if not ready.observed_at <= current < bootstrap.authority_expires_at:
        raise RoboticsError(Code.LEASE_INVALID)
    if (
        ready.bootstrap_hash != content_hash(bootstrap, exclude=())
        or ready.description != bootstrap.description
        or activation.ready_hash != content_hash(ready, exclude=())
    ):
        raise RoboticsError(Code.CONFORMANCE_STALE)
    preflight = activation.preflight.for_execution(SimulationPreflightReceipt)
    approval = activation.approval.for_execution(SimulationEpisodeApproval)
    pins = bootstrap.pins(ready.initial_observation, activation.preflight, activation.approval)
    if activation.pins != pins:
        raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
    if (
        (preflight.workspace_id, preflight.project_id) != (pins.workspace_id, pins.project_id)
        or preflight.created_by.principal_id != bootstrap.orchestrator_principal_id
        or preflight.created_by.status is not PrincipalStatus.ACTIVE
        or preflight.result != "PASS"
        or preflight.conformance_report_hash != bootstrap.conformance_report_hash
        or not ready.observed_at <= preflight.created_at <= current < preflight.valid_until
        or preflight.valid_until > bootstrap.authority_expires_at
    ):
        raise RoboticsError(Code.PREFLIGHT_FAILED)
    observations = next(check for check in preflight.checks if check.name == "OBSERVATIONS")
    evidence = observations.evidence_ref
    if (
        evidence.digest != content_hash(ready, exclude=())
        or evidence.size_bytes != len(canonical_json(ready))
        or evidence.media_type != "application/json"
        or evidence.evidence_class is not EvidenceClass.SIMULATION
    ):
        raise RoboticsError(Code.PREFLIGHT_FAILED)
    if (
        (approval.workspace_id, approval.project_id) != (pins.workspace_id, pins.project_id)
        or approval.approved_by.status is not PrincipalStatus.ACTIVE
        or approval.created_by.status is not PrincipalStatus.ACTIVE
        or approval.approved_by.principal_id
        in {
            bootstrap.orchestrator_principal_id,
            bootstrap.adapter_principal_id,
            bootstrap.evaluator_principal_id,
        }
        or not preflight.created_at <= approval.created_at <= current < approval.expires_at
        or approval.expires_at > min(bootstrap.authority_expires_at, preflight.valid_until)
    ):
        raise RoboticsError(Code.APPROVAL_INVALID)
    return pins


def activation_deadline(bootstrap: WorkerBootstrap, activation: WorkerActivation) -> datetime:
    """Only use after validate_activation; watchdog retains the lease upper cap."""
    return min(
        bootstrap.authority_expires_at,
        activation.preflight.for_execution(SimulationPreflightReceipt).valid_until,
        activation.approval.for_execution(SimulationEpisodeApproval).expires_at,
    )


class InitializableAdapter(RobotAdapter, Protocol):
    def bind_episode(self, episode: EpisodePins) -> None:
        """Bind once after validated activation; no operational permission is granted."""
        ...


def read_frame(source: BinaryIO) -> bytes:
    raw = source.readline(MAX_FRAME_BYTES + 2)
    if not raw or len(raw) > MAX_FRAME_BYTES + 1 or not raw.endswith(b"\n"):
        raise RoboticsError(Code.INVALID_REQUEST)
    return raw[:-1]


def write_frame(destination: BinaryIO, value: WireModel) -> None:
    raw = canonical_json(value)
    if len(raw) > MAX_FRAME_BYTES:
        raise RoboticsError(Code.PAYLOAD_TOO_LARGE)
    destination.write(raw + b"\n")
    destination.flush()


def load_bootstrap(path: Path) -> WorkerBootstrap:
    # No caller-selected model, module, network endpoint or storage mount exists.
    if path != BOOTSTRAP_PATH or path.resolve() != path:
        raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o400
            or info.st_size > MAX_FRAME_BYTES
        ):
            raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
        with os.fdopen(os.dup(descriptor), "rb") as source:
            raw = source.read(MAX_FRAME_BYTES + 1)
        return WorkerBootstrap.model_validate(bounded_json(raw))
    finally:
        os.close(descriptor)


class DeadlineGuard:
    def __init__(self, guard: AdmissionGuard, deadline: datetime):
        self.guard, self.deadline = guard, deadline

    def authorize(self, request: ProtocolRequest, *, pins: ExecutionPins) -> None:
        # Cleanup can still terminate after expiry. No other operation may use
        # a permit delayed across the immutable upper authority deadline.
        cleanup = isinstance(request.payload, TerminateRequest)
        if not cleanup and datetime.now(UTC) >= self.deadline:
            raise RoboticsError(Code.LEASE_INVALID)
        self.guard.authorize(request, pins=pins)
        if not cleanup and datetime.now(UTC) >= self.deadline:
            raise RoboticsError(Code.ACKNOWLEDGEMENT_UNCERTAIN)


def serve(
    bootstrap: WorkerBootstrap,
    *,
    factory: Callable[[WorkerBootstrap], InitializableAdapter],
    artifacts: ArtifactReader,
    authority: AdmissionGuard,
    source: BinaryIO,
    destination: BinaryIO,
) -> None:
    # Reparse nested models before construction: these values are mutable even
    # when the outer Pydantic object is frozen. Factory sees a separate copy.
    frozen = canonical_json(bootstrap)
    bootstrap = WorkerBootstrap.model_validate(bounded_json(frozen))
    if not callable(getattr(authority, "authorize", None)):
        raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
    if datetime.now(UTC) >= bootstrap.authority_expires_at:
        raise RoboticsError(Code.LEASE_INVALID)
    adapter = factory(WorkerBootstrap.model_validate_json(frozen))
    try:
        if not callable(getattr(adapter, "bind_episode", None)):
            raise RoboticsError(Code.CONFORMANCE_STALE)
        description = AdapterDescription.model_validate_json(canonical_json(adapter.describe()))
        if description != bootstrap.description:
            raise RoboticsError(Code.CONFORMANCE_STALE)
        initial = (
            ObservationValidator()
            .validate(
                description.observation_spec.for_execution(ObservationSpec),
                description.descriptor.for_execution(EmbodimentDescriptor),
                adapter.observe(),
                artifacts,
                episode_id=bootstrap.episode.episode_id,
                lease=bootstrap.episode.lease,
                previous=None,
            )
            .batch
        )
        bootstrap.validate_initial(initial)
        ready = WorkerReady(
            bootstrap_hash=content_hash(bootstrap, exclude=()),
            description=description,
            initial_observation=initial,
            observed_at=datetime.now(UTC),
        )
        write_frame(destination, ready)
        activation = WorkerActivation.model_validate(bounded_json(read_frame(source)))
        pins = validate_activation(bootstrap, ready, activation)
        deadline = activation_deadline(bootstrap, activation)

        # Waiting for the authentic human approval cannot silently change the
        # initialized state. Re-read before binding and after the binding hook.
        def current_initial() -> ObservationBatch:
            return (
                ObservationValidator()
                .validate(
                    description.observation_spec.for_execution(ObservationSpec),
                    description.descriptor.for_execution(EmbodimentDescriptor),
                    adapter.observe(),
                    artifacts,
                    episode_id=bootstrap.episode.episode_id,
                    lease=bootstrap.episode.lease,
                    previous=initial.state_binding(),
                )
                .batch
            )

        if current_initial() != initial:
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        if datetime.now(UTC) >= deadline:
            raise RoboticsError(Code.LEASE_INVALID)
        adapter.bind_episode(EpisodePins.model_validate_json(canonical_json(pins.episode)))
        if current_initial() != initial:
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        if datetime.now(UTC) >= deadline:
            raise RoboticsError(Code.LEASE_INVALID)
        session = AdapterSession(
            adapter,
            pins=pins,
            artifact_reader=artifacts,
            admission_guard=DeadlineGuard(authority, deadline),
            trusted_keys=bootstrap.keys(),
        )
        for _ in range(10_000):
            request = parse_message(read_frame(source))
            if not isinstance(request, ProtocolRequest):
                raise RoboticsError(Code.INVALID_REQUEST)
            response = session.dispatch(request)
            write_frame(destination, response)
            if isinstance(request.payload, TerminateRequest):
                return
        raise RoboticsError(Code.RESOURCE_CAP_EXHAUSTED)
    finally:
        adapter.terminate()


def run_worker(bootstrap: WorkerBootstrap, source: BinaryIO, destination: BinaryIO) -> None:
    if bootstrap.worker_kind != "UR5E":
        raise RoboticsError(Code.INVALID_CONTRACT)
    artifacts = UnixArtifacts(BOOTSTRAP_PATH.parent / "artifacts.sock")
    authority = UnixAdmissionGuard(BOOTSTRAP_PATH.parent / "authority.sock")

    def factory(values: WorkerBootstrap) -> InitializableAdapter:
        # The only admitted code path is fixed by the image. Import is lazy so
        # contract/API installations do not need NumPy, MuJoCo or a renderer.
        from accretion.robotics.adapters import ur5e

        descriptor = values.description.descriptor.for_execution(EmbodimentDescriptor)
        observations = values.description.observation_spec.for_execution(ObservationSpec)
        if descriptor.project_id is None:
            raise RoboticsError(Code.INVALID_CONTRACT)
        profile = ur5e.build_profile(
            MODELS_PATH,
            artifacts,
            workspace_id=descriptor.workspace_id,
            project_id=descriptor.project_id,
            principal=descriptor.created_by,
            created_at=descriptor.created_at,
            descriptor_id=descriptor.contract_id,
            observation_spec_id=observations.contract_id,
            simulator_image_digest=values.description.dependencies.simulator_image_digest,
            host_compatibility_profile_hash=(
                values.description.dependencies.host_compatibility_profile_hash
            ),
        )
        if profile.description != values.description:
            raise RoboticsError(Code.CONFORMANCE_STALE)
        return ur5e.UR5eAdapter(
            profile,
            episode=values.episode.initialization(),
            artifacts=artifacts,
            trusted_keys=values.keys(),
            replay_source_episode_id=values.replay_source_episode_id,
            execution_guard=authority.check_current,
        )

    serve(
        bootstrap,
        factory=factory,
        artifacts=artifacts,
        authority=authority,
        source=source,
        destination=destination,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", required=True, type=Path)
    args = parser.parse_args()
    bootstrap = load_bootstrap(args.bootstrap)
    # Mandatory PID-1 watchdog: direct unbounded execution is not a CLI option.
    from .watchdog import supervise

    raise SystemExit(supervise(bootstrap))


if __name__ == "__main__":
    main()
