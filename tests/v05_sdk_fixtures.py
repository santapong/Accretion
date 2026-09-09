"""Synthetic SDK-only fixtures: no operational approval or conformance evidence."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from accretion.contracts.robotics import (
    ActionIntent,
    EmbodimentDescriptor,
    ObservationSpec,
    PreparedCommand,
    SafetyDecisionPayload,
    SafetyDecisionReceipt,
    TrustedSafetyKey,
    sign_safety_payload,
)
from accretion.contracts.robotics.models import RoboticsContract
from accretion.contracts.robotics.values import DependencyClosure, EpisodePins
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.observations import TENSOR_MEDIA_TYPE, ObservationBatch, ObservationSample
from accretion.robotics.protocol import (
    AdapterDescription,
    DescriptorRecord,
    ExecuteRequest,
    IntentRecord,
    ObservationSpecRecord,
    PreparedRecord,
    PrepareRequest,
    PrepareResult,
    ProtocolRequest,
    RequestPayload,
    SafetyRecord,
    SuccessOutcome,
)
from accretion.robotics.sdk import AdapterSession, ExecutionPins
from accretion.robotics.testing.fault_adapter import Fault, MemoryArtifacts, ScriptedFaultAdapter

FIXTURES = Path(__file__).parent / "fixtures/contracts/v0.5"


def fixture(name: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads((FIXTURES / name / "complete.json").read_text())
    return value


def editable(name: str) -> dict[str, Any]:
    value = fixture(name)
    value.pop("content_hash")
    return value


def replace_contract[C: RoboticsContract](value: C, **changes: Any) -> C:
    fields = value.model_dump(mode="python")
    fields.pop("content_hash")
    fields.update(changes)
    return type(value).model_validate(fields)


class SyntheticGuard:
    """Deliberately not a durable production authority. Only used by these tests."""

    def __init__(self) -> None:
        self.allowed = True
        self.calls: list[str] = []

    def authorize(self, request: ProtocolRequest, *, pins: ExecutionPins) -> None:
        if not self.allowed:
            raise RoboticsError(Code.APPROVAL_INVALID)
        self.calls.append(request.payload.op)


class Harness:
    def __init__(
        self,
        fault: Fault = Fault.NONE,
        *,
        episode_id: str | None = None,
        replay_source: str | None = None,
        artifacts: MemoryArtifacts | None = None,
    ) -> None:
        self.artifacts = artifacts or MemoryArtifacts()
        desc = editable("EmbodimentDescriptor")
        desc["sensors"].append(
            dict(sensor_id="joint_state", modality="JOINT_STATE", frame_id="base")
        )
        self.descriptor = EmbodimentDescriptor.model_validate(desc)
        self.spec = ObservationSpec.model_validate(fixture("ObservationSpec"))
        dependencies = fixture("AdapterConformanceReport")["dependencies"]
        dependencies["observation_spec_hash"] = self.spec.content_hash
        prepared_fixture = fixture("PreparedCommand")
        dependencies.update(
            controller_digest=prepared_fixture["controller_digest"],
            adapter_artifact_digest=prepared_fixture["adapter_artifact_digest"],
            prepared_command_schema_hash=prepared_fixture["command_schema_hash"],
        )
        self.dependencies = DependencyClosure.model_validate(dependencies)
        pins = fixture("SimulationEpisodeApproval")["pins"]
        if episode_id is not None:
            pins["episode_id"] = episode_id
        self.episode = EpisodePins.model_validate(pins)
        tensor = self.artifacts.put(struct.pack("<6d", *([0.0] * 6)), media_type=TENSOR_MEDIA_TYPE)
        self.initial = ObservationBatch(
            episode_id=self.episode.episode_id,
            lease=self.episode.lease,
            sequence=0,
            sim_time_ns=0,
            frame_transform_digest=self.descriptor.kinematics.transform_provenance_ref.digest,
            samples=(
                ObservationSample(
                    field=self.spec.required[0], sequence=0, sim_time_ns=0, artifact=tensor
                ),
            ),
        )
        safety = fixture("SafetyDecisionReceipt")
        prepared = fixture("PreparedCommand")
        self.pins = ExecutionPins(
            workspace_id=self.descriptor.workspace_id,
            project_id=self.descriptor.project_id,
            episode=self.episode,
            dependencies=self.dependencies,
            descriptor_hash=self.descriptor.content_hash,
            adapter_principal_id=prepared["created_by"]["principal_id"],
            evaluator_principal_id=safety["created_by"]["principal_id"],
            policy_ref=safety["decision"]["policy_ref"],
            evaluator=safety["decision"]["evaluator"],
            approval_hash=safety["decision"]["episode_approval_hash"],
            state=self.initial.state_binding(),
            budget=safety["decision"]["budget_before"],
            next_action_sequence=0,
            replay_source_episode_id=replay_source,
        )
        self.description = AdapterDescription(
            descriptor=DescriptorRecord.from_contract(self.descriptor),
            observation_spec=ObservationSpecRecord.from_contract(self.spec),
            dependencies=self.dependencies,
        )
        self.adapter = ScriptedFaultAdapter(
            self.description, self.initial, self.make_prepared, self.artifacts, fault=fault
        )
        self.key = Ed25519PrivateKey.generate()
        self.trusted_keys = {
            "test-only": TrustedSafetyKey(
                principal_id=self.pins.evaluator_principal_id,
                public_key=self.key.public_key().public_bytes_raw(),
            )
        }
        self.guard = SyntheticGuard()
        self.session = AdapterSession(
            self.adapter,
            pins=self.pins,
            artifact_reader=self.artifacts,
            admission_guard=self.guard,
            trusted_keys=self.trusted_keys,
        )
        self.sequence = 0

    def make_intent(self) -> ActionIntent:
        fields = editable("ActionIntent")
        pins = self.session.pins
        fields.update(
            state=pins.state, sequence=pins.next_action_sequence, episode_id=pins.episode.episode_id
        )
        fields.update(
            semantic_action="SET_GRIPPER",
            target=dict(kind="SET_GRIPPER", opening_m=0.04, force_n=10.0),
        )
        return ActionIntent.model_validate(fields)

    def make_prepared(self, intent: ActionIntent) -> PreparedCommand:
        fields = editable("PreparedCommand")
        fields.update(
            action_intent_hash=intent.content_hash,
            state=intent.state,
            episode_id=intent.episode_id,
            sequence=intent.sequence,
            lease=self.episode.lease,
        )
        return PreparedCommand.model_validate(fields)

    def sign(self, prepared: PreparedCommand, **changes: Any) -> SafetyDecisionReceipt:
        fields = editable("SafetyDecisionReceipt")
        payload = fields["decision"]
        pins = self.session.pins
        payload.update(
            decision="ALLOW",
            action_intent_hash=prepared.action_intent_hash,
            prepared_command_hash=prepared.content_hash,
            state=prepared.state,
            episode_id=prepared.episode_id,
            lease=prepared.lease,
            budget_before=pins.budget,
            budget_after=dict(
                actions=pins.budget.actions + 1,
                cumulative_joint_motion_rad=pins.budget.cumulative_joint_motion_rad,
                elapsed_sim_seconds=pins.budget.elapsed_sim_seconds + 0.001,
            ),
        )
        payload.update(changes)
        decision = SafetyDecisionPayload.model_validate(payload)
        fields.update(
            decision=decision,
            signature=sign_safety_payload(decision, key_id="test-only", private_key=self.key),
        )
        return SafetyDecisionReceipt.model_validate(fields)

    def request(self, payload: RequestPayload) -> ProtocolRequest:
        sequence = self.sequence
        self.sequence += 1
        return ProtocolRequest.create(
            request_id=f"request-{sequence}",
            sequence=sequence,
            scope=self.session.pins.command_scope(),
            payload=payload,
        )

    def prepared(self) -> tuple[ActionIntent, PreparedCommand]:
        intent = self.make_intent()
        response = self.session.dispatch(
            self.request(PrepareRequest(intent=IntentRecord.from_contract(intent)))
        )
        assert isinstance(response.outcome, SuccessOutcome), response.outcome
        assert isinstance(response.outcome.payload, PrepareResult)
        return intent, response.outcome.payload.prepared.for_execution(PreparedCommand)

    def execute_payload(self) -> ExecuteRequest:
        intent, prepared = self.prepared()
        return ExecuteRequest(
            intent=IntentRecord.from_contract(intent),
            prepared=PreparedRecord.from_contract(prepared),
            safety=SafetyRecord.from_contract(self.sign(prepared)),
        )
