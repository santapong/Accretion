"""Synthetic captured bytes, signatures and explicit test-only thresholds.

This fixture is deliberately fabricated; it makes no host/physics/AC5 claim.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from test_v05_safety import case as safety_case
from v05_sdk_fixtures import editable, fixture, replace_contract

from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics import (
    CanonicalWriterEnvelope,
    EmbodiedVerificationSpec,
    EpisodeRecord,
    ObservationSpec,
    RobotAdapterManifest,
    SimulationActionReceipt,
    SimulationDomainEvent,
    SimulationEnvironmentSnapshot,
    SimulationExperimentContract,
    SimulationPreflightReceipt,
)
from accretion.contracts.robotics.values import DependencyClosure, EpisodePins, ObservationField
from accretion.robotics.observations import TENSOR_MEDIA_TYPE, ObservationBatch, ObservationSample
from accretion.robotics.protocol import (
    CommandScope,
    ExecuteRequest,
    ExecuteResult,
    IntentRecord,
    ObservationResult,
    PreparedRecord,
    ProtocolRequest,
    ProtocolResponse,
    ResetRequest,
    SafetyRecord,
    SuccessOutcome,
)
from accretion.robotics.testing.fault_adapter import MemoryArtifacts
from accretion.robotics.verification.types import (
    ActionChunkV1,
    ActionEvidenceRow,
    ActualSafetyRow,
    ChunkManifestV1,
    ContactChannelV1,
    ContractSourcesV1,
    EpisodeArtifactRoles,
    EpisodeProvenanceV1,
    EvidenceChunkRef,
    EvidenceReadLimits,
    EvidenceScope,
    FieldToleranceV1,
    MetricChunkV1,
    PoseChannels,
    PoseGoal,
    ReachTaskV1,
    RecordedMetric,
    ReplayToleranceProfileV1,
    SafetyChannelsV1,
    SafetyChunkV1,
    SafetyIssuanceV1,
    SensorChunkV1,
    TerminationPayloadV1,
    TraceIndexRow,
    TrajectoryChunkV1,
    TrustedVerificationBinding,
    VerifierConfigurationV1,
)


@dataclass
class Bundle:
    record: EpisodeRecord
    binding: TrustedVerificationBinding
    artifacts: MemoryArtifacts
    keys: dict[str, Any]

    @property
    def original(self):
        return CanonicalWriterEnvelope.from_contract(self.record)


def bundle(
    *,
    replay: bool = False,
    wall_offset: timedelta = timedelta(0),
    contact_dtype: str | None = None,
    contact_values: tuple[float | bool, ...] = (0.0, 0.0, 0.0),
    declared_contact: bool = False,
) -> Bundle:
    c = safety_case.__wrapped__()
    c.context.now += wall_offset
    c.context.lease_expires_at += wall_offset
    c.approval = replace_contract(
        c.approval,
        created_at=c.approval.created_at + wall_offset,
        expires_at=c.approval.expires_at + wall_offset,
    )
    episode_id = "sep_" + "0" * 26 if replay else c.intent.episode_id
    lease = {"lease_id": "sle_" + "0" * 26, "generation": 1} if replay else c.prepared.lease
    run_id = "synthetic-replay-run" if replay else fixture("EpisodeRecord")["run_id"]
    store = MemoryArtifacts()

    def put(value):
        return store.put(canonical_json(value), media_type="application/json")

    fields = [
        ("q", "JOINT_STATE", [1], "rad"),
        ("v", "JOINT_STATE", [1], "rad/s"),
        ("a", "JOINT_STATE", [1], "rad/s2"),
        ("effort", "JOINT_STATE", [1], "N.m"),
        ("opening", "GRIPPER_OPENING", [1], "m"),
        ("tool_position", "POSE", [3], "m"),
        ("tool_orientation", "POSE", [4], "1"),
    ]
    observations = [
        ObservationField(
            field=name,
            modality=modality,
            shape=shape,
            unit=unit,
            dtype="float64",
            frame_id="base",
            sensor_id=name,
        )
        for name, modality, shape, unit in fields
    ]
    if contact_dtype is not None:
        observations.append(
            ObservationField(
                field="pair_contact",
                modality="CONTACT",
                shape=[1],
                unit="1" if contact_dtype == "bool" else "N",
                dtype=contact_dtype,
                frame_id="base",
                sensor_id="pair_contact",
            )
        )
    c.descriptor = replace_contract(
        c.descriptor,
        sensors=[
            dict(sensor_id=f.field, modality=f.modality, frame_id=f.frame_id) for f in observations
        ],
    )
    c.envelope = replace_contract(c.envelope, embodiment_descriptor_hash=c.descriptor.content_hash)
    if declared_contact:
        c.envelope = replace_contract(
            c.envelope,
            collision_policy="DECLARED_CONTACT_ONLY",
            allowed_contacts=[
                dict(
                    first_body="arm",
                    second_body="payload",
                    phases=["APPROACH"],
                    maximum_force_n=5.0,
                    maximum_penetration_m=0.01,
                )
            ],
        )
    spec = ObservationSpec.model_validate({**editable("ObservationSpec"), "required": observations})
    verification = EmbodiedVerificationSpec.model_validate(fixture("EmbodiedVerificationSpec"))
    tolerance = ReplayToleranceProfileV1(
        time_tolerance_ns=0,
        fields=tuple(
            FieldToleranceV1(
                field=f.field,
                unit="rad" if f.shape == [4] else f.unit,
                mode="QUATERNION_ANGLE" if f.shape == [4] else "COMPONENT",
                absolute=0.01,
                relative=0.0,
            )
            for f in observations
        ),
        metric_tolerances=tuple(
            FieldToleranceV1(field=name, unit=unit, mode="COMPONENT", absolute=0.01, relative=0.0)
            for name, unit in [
                ("task_final_position_error_m", "m"),
                ("task_final_orientation_error_rad", "rad"),
                ("episode_duration_s", "s"),
            ]
        ),
    )
    tolerance_ref = put(tolerance)
    verifier = verification.task_verifiers[0].verifier
    config = VerifierConfigurationV1(
        task_verifier=verifier,
        safety_verifier=verifier,
        completeness_verifier=verifier,
        replay_verifier=verifier,
        task=ReachTaskV1(
            tool=PoseChannels(position="tool_position", orientation="tool_orientation"),
            goal=PoseGoal(
                frame_id="base",
                position_m=(0.0, 0.0, 0.0),
                orientation_xyzw=(0.0, 0.0, 0.0, 1.0),
                position_tolerance_m=0.01,
                orientation_tolerance_rad=0.01,
                hold_ns=1_000_000_000,
            ),
        ),
        recovery=None,
        requires_fresh_episode_recovery=False,
        replay_tolerance_ref=tolerance_ref,
        safety_channels=SafetyChannelsV1(
            joint_position="q",
            joint_velocity="v",
            joint_acceleration="a",
            joint_effort="effort",
            gripper_opening="opening",
            contacts=(
                ContactChannelV1(field="pair_contact", first_body="arm", second_body="payload"),
            )
            if contact_dtype is not None
            else (),
        ),
        maximum_observation_gap_ns=1_000_000_000,
    )
    adapter = RobotAdapterManifest.model_validate(
        {
            **editable("RobotAdapterManifest"),
            "embodiment_descriptor_hashes": [c.descriptor.content_hash],
            "observation_spec_hash": spec.content_hash,
            "artifact_digest": c.prepared.adapter_artifact_digest,
            "prepared_command_schema_hash": c.prepared.command_schema_hash,
        }
    )
    env = SimulationEnvironmentSnapshot.model_validate(
        {
            **editable("SimulationEnvironmentSnapshot"),
            "robot_model_digest": c.descriptor.robot_model_ref.digest,
            "controller_digest": c.prepared.controller_digest,
            "adapter_digest": adapter.artifact_digest,
        }
    )
    exp = SimulationExperimentContract.model_validate(
        {
            **editable("SimulationExperimentContract"),
            "embodiment_ref": dict(
                contract_id=c.descriptor.contract_id, content_hash=c.descriptor.content_hash
            ),
            "adapter_ref": dict(contract_id=adapter.contract_id, content_hash=adapter.content_hash),
            "world_artifact_digest": env.world_digest,
            "controller_artifact_digest": env.controller_digest,
            "safety_envelope_hash": c.envelope.content_hash,
            "verification_spec_hash": verification.content_hash,
            "tolerance_profile_ref": tolerance_ref,
        }
    )
    preflight = SimulationPreflightReceipt.model_validate(
        {
            **editable("SimulationPreflightReceipt"),
            "episode_id": episode_id,
            "lease": lease,
            "valid_until": c.context.now + timedelta(minutes=1),
            "created_at": c.approval.created_at,
            "result": "PASS",
            "checks": [
                {**check, "passed": True}
                for check in fixture("SimulationPreflightReceipt")["checks"]
            ],
            "experiment_contract_hash": exp.content_hash,
            "environment_snapshot_hash": env.content_hash,
            "adapter_manifest_hash": adapter.content_hash,
            "safety_envelope_hash": c.envelope.content_hash,
            "verification_spec_hash": verification.content_hash,
            "randomization_sample_hash": env.randomization_sample_ref.digest,
        }
    )
    pins = EpisodePins.model_validate(
        {
            name: preflight.content_hash
            if name == "preflight_receipt_hash"
            else getattr(preflight, name)
            for name in EpisodePins.model_fields
        }
    )
    c.approval = replace_contract(c.approval, pins=pins)
    closure = DependencyClosure.model_validate(
        dict(
            adapter_artifact_digest=adapter.artifact_digest,
            simulator_image_digest=env.simulator_image_digest,
            world_digest=env.world_digest,
            robot_model_digest=env.robot_model_digest,
            controller_digest=env.controller_digest,
            observation_spec_hash=spec.content_hash,
            action_intent_schema_hash=adapter.action_intent_schema_hash,
            prepared_command_schema_hash=adapter.prepared_command_schema_hash,
            tolerance_profile_hash=tolerance_ref.digest,
            environment_profile_hash=env.environment_profile_hash,
            physics_parameters_hash=env.physics_parameters_ref.digest,
            rendering_parameters_hash=env.rendering_parameters_ref.digest,
            host_compatibility_profile_hash=env.host_compatibility_profile_hash,
        )
    )
    batches = []
    for index in range(3):
        values = [[0.125 * index / 2], [0.0], [0.0], [0.0], [0.04], [0.0] * 3, [0.0, 0.0, 0.0, 1.0]]
        if contact_dtype is not None:
            values.append([contact_values[index]])
        samples = [
            ObservationSample(
                field=f,
                sequence=index,
                sim_time_ns=index * 1_000_000_000,
                artifact=store.put(
                    struct.pack("<" + ("B" if f.dtype == "bool" else "d") * len(value), *value),
                    media_type=TENSOR_MEDIA_TYPE,
                ),
            )
            for f, value in zip(observations, values, strict=True)
        ]
        batches.append(
            ObservationBatch(
                episode_id=pins.episode_id,
                lease=pins.lease,
                sequence=index,
                sim_time_ns=index * 1_000_000_000,
                frame_transform_digest=c.descriptor.kinematics.transform_provenance_ref.digest,
                samples=tuple(samples),
            )
        )
    c.intent = replace_contract(
        c.intent, state=batches[0].state_binding(), created_at=c.context.now, episode_id=episode_id
    )
    c.prepared = replace_contract(
        c.prepared,
        action_intent_hash=c.intent.content_hash,
        episode_id=episode_id,
        lease=lease,
        state=c.intent.state,
        created_at=c.context.now,
    )
    c.context.episode = pins
    c.context.closure = closure
    c.context.approval_hash = c.approval.content_hash
    c.context.state = c.intent.state
    evaluation = c.issuance()
    assert evaluation.receipt.decision.decision == "ALLOW", evaluation.receipt.decision.reason_codes
    scope = EvidenceScope(
        workspace_id=c.intent.workspace_id,
        project_id=c.intent.project_id,
        episode_id=pins.episode_id,
        run_id=run_id,
    )
    command_scope = CommandScope(
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        episode_id=scope.episode_id,
        lease=pins.lease,
        expected_state=c.intent.state,
        dependency_closure_hash=content_hash(closure, exclude=()),
    )
    reset_request = ProtocolRequest.create(
        request_id="reset",
        sequence=0,
        scope=command_scope,
        payload=ResetRequest(
            seed=pins.seed, randomization_sample_hash=pins.randomization_sample_hash
        ),
    )
    reset_response = ProtocolResponse.create(
        reset_request, SuccessOutcome(payload=ObservationResult(op="RESET", observation=batches[0]))
    )
    execute_request = ProtocolRequest.create(
        request_id="execute",
        sequence=1,
        scope=command_scope,
        payload=ExecuteRequest(
            intent=IntentRecord.from_contract(c.intent),
            prepared=PreparedRecord.from_contract(c.prepared),
            safety=SafetyRecord.from_contract(evaluation.receipt),
        ),
    )
    execute_response = ProtocolResponse.create(
        execute_request,
        SuccessOutcome(
            payload=ExecuteResult(
                observation=batches[-1],
                prepared_command_hash=c.prepared.content_hash,
                safety_decision_hash=evaluation.receipt.content_hash,
            )
        ),
    )
    response_ref = put(execute_response)
    receipts = []
    for status in ("ADMITTED", "ACKNOWLEDGED"):
        receipts.append(
            SimulationActionReceipt.model_validate(
                {
                    **editable("SimulationActionReceipt"),
                    "episode_id": episode_id,
                    "lease": lease,
                    "created_at": c.context.now,
                    "action_intent_hash": c.intent.content_hash,
                    "prepared_command_hash": c.prepared.content_hash,
                    "safety_decision_hash": evaluation.receipt.content_hash,
                    "status": status,
                    "sequence": 0,
                    "idempotency_key": c.intent.idempotency_key,
                    "adapter_ack_ref": response_ref if status == "ACKNOWLEDGED" else None,
                    "resulting_state": batches[-1].state_binding()
                    if status == "ACKNOWLEDGED"
                    else None,
                }
            )
        )
    action = ActionEvidenceRow(
        sequence=0,
        intent_ref=put(c.intent),
        prepared_ref=put(c.prepared),
        safety_issuance_ref=put(SafetyIssuanceV1.from_evaluation(evaluation)),
        receipt_refs=tuple(put(r) for r in receipts),
        request_ref=put(execute_request),
        response_ref=response_ref,
    )
    trace = tuple(
        TraceIndexRow(
            index=i,
            state=batch.state_binding(),
            wall_time=c.context.now + timedelta(seconds=i),
            phase="APPROACH",
        )
        for i, batch in enumerate(batches)
    )
    preview = evaluation.preview
    assert preview is not None
    actual = tuple(
        ActualSafetyRow(
            index=i,
            action_sequence=0,
            before=batches[i].state_binding(),
            after=batches[i + 1].state_binding(),
            phase="APPROACH",
            interval=interval,
        )
        for i, interval in enumerate(preview.intervals)
    )
    roles = {}
    for name, role, model, rows in [
        ("trajectory", "TRAJECTORY", TrajectoryChunkV1, trace),
        ("sensors", "SENSORS", SensorChunkV1, tuple(batches)),
        ("actions", "ACTIONS", ActionChunkV1, (action,)),
        ("safety", "SAFETY", SafetyChunkV1, actual),
        (
            "metrics",
            "METRICS",
            MetricChunkV1,
            (RecordedMetric(name="untrusted_success", value=1.0, unit="1"),),
        ),
    ]:
        chunk = model(scope=scope, first_index=0, records=rows)
        roles[name] = put(
            ChunkManifestV1(
                scope=scope,
                role=role,
                total_records=len(rows),
                chunks=(
                    EvidenceChunkRef(artifact=put(chunk), first_index=0, record_count=len(rows)),
                ),
            )
        )
    terminal = TerminationPayloadV1(
        termination_reason="TASK_COMPLETE",
        final_state="VERIFYING",
        final_observation=batches[-1].state_binding(),
        action_count=1,
        observation_count=3,
    )
    event = SimulationDomainEvent.model_validate(
        {
            **editable("SimulationDomainEvent"),
            "payload_hash": "",
            "event_type": "simulation_episode.terminated",
            "payload": terminal.model_dump(mode="json"),
            "occurred_at": trace[-1].wall_time,
            "run_id": scope.run_id,
            "episode_id": scope.episode_id,
        }
    )
    provenance = EpisodeProvenanceV1(
        scope=scope,
        pins=pins,
        dependencies=closure,
        descriptor_hash=c.descriptor.content_hash,
        observation_spec_hash=spec.content_hash,
        verifier_configuration_ref=put(config),
        artifacts=EpisodeArtifactRoles(**roles),
        contracts=ContractSourcesV1(
            experiment=put(exp),
            environment=put(env),
            descriptor=put(c.descriptor),
            observations=put(spec),
            safety_envelope=put(c.envelope),
            verification_spec=put(verification),
            preflight=put(preflight),
            approval=put(c.approval),
            adapter_manifest=put(adapter),
        ),
        geometry_ref=put(preview.geometry),
        reset_request_ref=put(reset_request),
        reset_response_ref=put(reset_response),
        termination_event_ref=put(event),
        policy_and_routing_refs=(put({"synthetic_policy_record": True}),),
        producer_principal_id=fixture("EpisodeRecord")["producer_principal"]["principal_id"],
        producer_process_id="synthetic-process",
        initial_state=batches[0].state_binding(),
        final_state=batches[-1].state_binding(),
        physics_step_ns=c.context.physics_step_ns,
    )
    record = EpisodeRecord.model_validate(
        {
            **editable("EpisodeRecord"),
            "episode_id": episode_id,
            "run_id": run_id,
            "started_at": trace[0].wall_time,
            "finished_at": trace[-1].wall_time,
            "termination_reason": "TASK_COMPLETE",
            "experiment_contract_hash": exp.content_hash,
            "environment_snapshot_hash": env.content_hash,
            "seed": pins.seed,
            "trajectory_ref": roles["trajectory"],
            "sensor_manifest_ref": roles["sensors"],
            "action_receipts_ref": roles["actions"],
            "safety_events_ref": roles["safety"],
            "metrics_ref": roles["metrics"],
            "provenance_manifest_ref": put(provenance),
            "replay_class": exp.replay_class,
        }
    )
    binding = TrustedVerificationBinding(
        scope,
        pins,
        closure,
        canonical_json(config),
        EvidenceReadLimits(10_000_000, 1000, 1000),
        provenance.geometry_ref,
        provenance.policy_and_routing_refs,
    )
    return Bundle(record, binding, store, {c.signer.key_id: c.signer.trusted_key})
