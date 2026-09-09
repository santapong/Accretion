"""Deterministic construction findings from captured evidence, never production PASS.

The future independent host authenticates process/service identity and capture
provenance, then attests results. These functions neither launch replay nor
accept/quarantine an episode. Missing trust/coverage remains INCONCLUSIVE.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from fractions import Fraction

from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics import (
    ActionIntent,
    CanonicalWriterEnvelope,
    EpisodeRecord,
    PreparedCommand,
    SafetyDecisionReceipt,
    TrustedSafetyKey,
    verify_safety_signature,
)
from accretion.contracts.robotics.models import RoboticsContract
from accretion.contracts.robotics.values import ContactPolicy, EpisodePins
from accretion.robotics.errors import RoboticsError
from accretion.robotics.observations import ArtifactReader
from accretion.robotics.protocol import (
    ErrorOutcome,
    ExecuteRequest,
    ExecuteResult,
    ObservationResult,
    ResetRequest,
    SuccessOutcome,
)
from accretion.robotics.safety import AxisAlignedBox

from .reader import EvidenceProblem, LoadedEpisode, ObservedFrame, load_episode, require
from .types import (
    ConstructionFindings,
    FindingStatus,
    PickPlaceTaskV1,
    PoseChannels,
    PoseGoal,
    RecordedMetric,
    RoleFinding,
    TrustedVerificationBinding,
)

ABORT_REASONS = frozenset(
    {
        "ACKNOWLEDGEMENT_UNCERTAIN",
        "ADAPTER_CRASH",
        "HEARTBEAT_LOST",
        "SAFETY_DENIAL",
        "OBSERVATION_INVALID",
        "CLOCK_REGRESSION",
        "ARTIFACT_FAILURE",
        "ACTION_CAP",
        "TIME_CAP",
        "MOTION_CAP",
        "NO_ACTION_TIMEOUT",
        "CANCELLED",
    }
)


def _same_ref(first: object, second: object) -> bool:
    return canonical_json(first) == canonical_json(second)


def _scope(document: RoboticsContract, episode: LoadedEpisode) -> None:
    scope = episode.provenance.scope
    require(
        (document.workspace_id, document.project_id) == (scope.workspace_id, scope.project_id),
        "WRITER_SCOPE_MISMATCH",
    )


def _closure(episode: LoadedEpisode) -> None:
    e = episode
    p, closure = e.provenance.pins, e.provenance.dependencies
    require(
        e.record.experiment_contract_hash
        == e.experiment.content_hash
        == p.experiment_contract_hash,
        "EXPERIMENT_PIN",
    )
    require(
        e.record.environment_snapshot_hash
        == e.environment.content_hash
        == p.environment_snapshot_hash,
        "ENVIRONMENT_PIN",
    )
    require(e.record.seed == p.seed and p.seed in e.experiment.seed_set, "SEED_PIN")
    require(e.record.episode_id == p.episode_id, "EPISODE_PIN")
    require(e.record.replay_class == e.experiment.replay_class, "REPLAY_CLASS_SUBSTITUTION")
    require(
        e.provenance.producer_principal_id == e.record.producer_principal.principal_id,
        "PRODUCER_PIN",
    )
    require(
        e.descriptor.content_hash
        == e.provenance.descriptor_hash
        == e.experiment.embodiment_ref.content_hash
        == e.safety_envelope.embodiment_descriptor_hash,
        "DESCRIPTOR_PIN",
    )
    require(e.descriptor.contract_id == e.experiment.embodiment_ref.contract_id, "DESCRIPTOR_ID")
    require(
        e.adapter.content_hash == p.adapter_manifest_hash == e.experiment.adapter_ref.content_hash,
        "ADAPTER_PIN",
    )
    require(e.adapter.contract_id == e.experiment.adapter_ref.contract_id, "ADAPTER_ID")
    require(
        e.descriptor.content_hash in e.adapter.embodiment_descriptor_hashes, "ADAPTER_EMBODIMENT"
    )
    require(
        e.safety_envelope.content_hash
        == p.safety_envelope_hash
        == e.experiment.safety_envelope_hash,
        "ENVELOPE_PIN",
    )
    require(
        e.verification_spec.content_hash
        == p.verification_spec_hash
        == e.experiment.verification_spec_hash,
        "VERIFICATION_SPEC_PIN",
    )
    require(e.preflight.content_hash == p.preflight_receipt_hash, "PREFLIGHT_PIN")
    require(
        EpisodePins.model_validate(
            {
                key: (
                    e.preflight.content_hash
                    if key == "preflight_receipt_hash"
                    else getattr(e.preflight, key)
                )
                for key in EpisodePins.model_fields
            }
        )
        == p,
        "PREFLIGHT_CLOSURE",
    )
    require(
        e.preflight.result == "PASS"
        and e.preflight.created_at <= e.record.started_at < e.preflight.valid_until,
        "PREFLIGHT_NOT_VALID_AT_START",
    )
    require(
        e.approval.pins == p
        and e.approval.created_at <= e.record.started_at < e.approval.expires_at,
        "APPROVAL_NOT_VALID_AT_START",
    )
    require(e.approval.policy_ref == e.safety_envelope.policy_ref, "APPROVAL_POLICY_PIN")
    expected = {
        "adapter_artifact_digest": e.adapter.artifact_digest,
        "simulator_image_digest": e.environment.simulator_image_digest,
        "world_digest": e.environment.world_digest,
        "robot_model_digest": e.environment.robot_model_digest,
        "controller_digest": e.environment.controller_digest,
        "observation_spec_hash": e.observation_spec.content_hash,
        "action_intent_schema_hash": e.adapter.action_intent_schema_hash,
        "prepared_command_schema_hash": e.adapter.prepared_command_schema_hash,
        "tolerance_profile_hash": e.configuration.replay_tolerance_ref.digest,
        "environment_profile_hash": e.environment.environment_profile_hash,
        "physics_parameters_hash": e.environment.physics_parameters_ref.digest,
        "rendering_parameters_hash": e.environment.rendering_parameters_ref.digest,
        "host_compatibility_profile_hash": e.environment.host_compatibility_profile_hash,
    }
    require(
        all(getattr(closure, key) == value for key, value in expected.items()), "DEPENDENCY_CLOSURE"
    )
    require(e.environment.adapter_digest == e.adapter.artifact_digest, "ENVIRONMENT_ADAPTER_PIN")
    require(
        e.environment.robot_model_digest == e.descriptor.robot_model_ref.digest, "ROBOT_MODEL_PIN"
    )
    require(
        e.environment.world_digest == e.experiment.world_artifact_digest
        and e.environment.controller_digest == e.experiment.controller_artifact_digest,
        "EXPERIMENT_DEPENDENCIES",
    )
    require(
        e.environment.randomization_sample_ref.digest == p.randomization_sample_hash,
        "RANDOMIZATION_PIN",
    )
    require(
        e.observation_spec.content_hash
        == e.provenance.observation_spec_hash
        == e.adapter.observation_spec_hash,
        "OBSERVATION_SPEC_PIN",
    )
    require(
        _same_ref(e.experiment.tolerance_profile_ref, e.configuration.replay_tolerance_ref),
        "TOLERANCE_PROFILE_PIN",
    )


def _coverage(e: LoadedEpisode) -> None:
    require(
        e.frames[0].batch.state_binding() == e.provenance.initial_state
        and e.frames[-1].batch.state_binding() == e.provenance.final_state,
        "ENDPOINT_STATE_PIN",
    )
    require(e.frames[0].batch.sequence == 0 and e.frames[0].batch.sim_time_ns == 0, "RESET_ORIGIN")
    for index, frame in enumerate(e.frames):
        require(frame.batch.sequence == index, "OBSERVATION_SEQUENCE_GAP")
        require(
            e.record.started_at <= frame.index.wall_time <= e.record.finished_at, "WALL_TIME_RANGE"
        )
        if index:
            previous = e.frames[index - 1]
            dt = frame.batch.sim_time_ns - previous.batch.sim_time_ns
            require(
                0
                < dt
                <= min(e.provenance.physics_step_ns, e.configuration.maximum_observation_gap_ns),
                "OBSERVATION_TIME_GAP",
            )
            require(frame.index.wall_time >= previous.index.wall_time, "WALL_CLOCK_REGRESSION")
    request, response = e.reset_request, e.reset_response
    require(isinstance(request.payload, ResetRequest), "RESET_REQUEST_REQUIRED")
    assert isinstance(request.payload, ResetRequest)
    require(
        request.payload.seed == e.record.seed
        and request.payload.randomization_sample_hash
        == e.provenance.pins.randomization_sample_hash,
        "RESET_RANDOMIZATION_PIN",
    )
    require(
        request.scope is not None
        and request.scope.episode_id == e.record.episode_id
        and request.scope.workspace_id == e.record.workspace_id
        and request.scope.project_id == e.record.project_id
        and request.scope.lease == e.provenance.pins.lease
        and request.scope.dependency_closure_hash
        == content_hash(e.provenance.dependencies, exclude=()),
        "RESET_SCOPE_PIN",
    )
    require(
        isinstance(response.outcome, SuccessOutcome)
        and isinstance(response.outcome.payload, ObservationResult)
        and response.outcome.payload.op == "RESET",
        "RESET_ACK_REQUIRED",
    )
    assert isinstance(response.outcome, SuccessOutcome)
    assert isinstance(response.outcome.payload, ObservationResult)
    require(response.outcome.payload.observation == e.frames[0].batch, "RESET_ACK_STATE")
    event, terminal = e.termination_event, e.termination
    _scope(event, e)
    require(
        event.event_type == "simulation_episode.terminated"
        and event.episode_id == e.record.episode_id
        and event.run_id == e.record.run_id
        and event.occurred_at == e.record.finished_at,
        "COMMITTED_TERMINATION_BINDING",
    )
    require(
        event.producer.principal_id == e.record.created_by.principal_id, "TERMINATION_WRITER_PIN"
    )
    require(
        terminal.termination_reason == e.record.termination_reason
        and terminal.final_observation == e.provenance.final_state
        and terminal.observation_count == len(e.frames)
        and terminal.action_count == len(e.actions),
        "TERMINATION_CAPTURE_COUNTS",
    )
    require(
        (terminal.final_state == "VERIFYING") == (e.record.termination_reason == "TASK_COMPLETE"),
        "PRODUCER_EXECUTION_DISPOSITION",
    )
    require(len(e.safety) == len(e.frames) - 1, "ACTUAL_SAFETY_COVERAGE_MISSING")
    for index, row in enumerate(e.safety):
        before, after = e.frames[index], e.frames[index + 1]
        require(
            row.index == index
            and row.before == before.batch.state_binding()
            and row.after == after.batch.state_binding(),
            "ACTUAL_SAFETY_STATE_BINDING",
        )
        require(
            row.interval.start_ns == before.batch.sim_time_ns
            and row.interval.end_ns == after.batch.sim_time_ns,
            "ACTUAL_SAFETY_TIME_BINDING",
        )
        require(row.phase == before.index.phase, "ACTUAL_SAFETY_PHASE")


def _actions(e: LoadedEpisode, keys: Mapping[str, TrustedSafetyKey]) -> None:
    known_states = {
        frame.batch.state_binding().observation_digest: frame.batch.state_binding()
        for frame in e.frames
    }
    keys_seen: set[str] = set()
    reserved = 0
    last_end = 0
    blocked = False
    for index, action in enumerate(e.actions):
        row, intent, prepared, decision = (
            action.row,
            action.intent,
            action.prepared,
            action.issuance.receipt.decision,
        )
        require(not blocked, "ACTION_AFTER_TERMINAL_UNCERTAINTY")
        require(
            row.sequence == intent.sequence == prepared.sequence == index, "ACTION_SEQUENCE_GAP"
        )
        for item in (intent, prepared, action.issuance.receipt, *action.receipts):
            _scope(item, e)
            require(
                e.record.started_at <= item.created_at <= e.record.finished_at, "ACTION_WALL_TIME"
            )
        verify_safety_signature(action.issuance.receipt, trusted_keys=keys)
        require(
            intent.episode_id == prepared.episode_id == e.record.episode_id, "ACTION_EPISODE_PIN"
        )
        require(intent.idempotency_key not in keys_seen, "DUPLICATE_ACTION_KEY")
        keys_seen.add(intent.idempotency_key)
        require(
            known_states.get(intent.state.observation_digest) == intent.state
            and prepared.state == intent.state
            and intent.state.sim_time_ns >= last_end,
            "ACTION_START_STATE",
        )
        require(
            prepared.action_intent_hash == intent.content_hash
            and prepared.lease == e.provenance.pins.lease,
            "PREPARED_BINDING",
        )
        issuance = action.issuance.request
        require(
            issuance.intent == intent
            and issuance.prepared == prepared
            and issuance.envelope == e.safety_envelope
            and issuance.descriptor == e.descriptor
            and issuance.approval == e.approval,
            "ISSUANCE_CAPTURE_BINDING",
        )
        require(
            issuance.context.episode == e.provenance.pins
            and issuance.context.closure == e.provenance.dependencies
            and issuance.context.physics_step_ns == e.provenance.physics_step_ns,
            "ISSUANCE_CONTEXT_BINDING",
        )
        require(decision.budget_before.actions == reserved, "ACTION_RESERVATION_COUNT")
        prior_motion = sum(
            (
                Fraction(j.motion_upper_rad)
                for r in e.safety
                if r.after.sim_time_ns <= intent.state.sim_time_ns
                for j in r.interval.joints
            ),
            Fraction(0),
        )
        require(
            Fraction(decision.budget_before.cumulative_joint_motion_rad) >= prior_motion
            and Fraction(decision.budget_before.elapsed_sim_seconds)
            >= Fraction(intent.state.sim_time_ns, 1_000_000_000),
            "BUDGET_BEFORE_OMITS_ACTUAL_HISTORY",
        )

        if decision.decision == "ALLOW":
            action.issuance.validate_current_context(issuance.context)
            reserved += 1
        final = action.receipts[-1]
        statuses = [receipt.status for receipt in action.receipts]
        require(
            statuses
            in (
                ["DENIED"],
                ["ADMITTED", "ACKNOWLEDGED"],
                ["ADMITTED", "UNKNOWN"],
                ["ADMITTED", "FAILED"],
            ),
            "ACTION_LEDGER_TRANSITIONS",
        )
        require(
            (decision.decision == "DENY") == (final.status == "DENIED"), "SAFETY_DECISION_STATUS"
        )
        for receipt in action.receipts:
            require(
                receipt.sequence == index
                and receipt.episode_id == e.record.episode_id
                and receipt.idempotency_key == intent.idempotency_key
                and receipt.action_intent_hash == intent.content_hash
                and receipt.prepared_command_hash == prepared.content_hash
                and receipt.safety_decision_hash == action.issuance.receipt.content_hash
                and receipt.lease == prepared.lease,
                "ACTION_RECEIPT_BINDING",
            )
        if final.status == "DENIED":
            require(action.request is None and action.response is None, "EXECUTION_AFTER_DENIAL")
            require(all(row.action_sequence != index for row in e.safety), "PHYSICS_AFTER_DENIAL")
            require(e.record.termination_reason == "SAFETY_DENIAL", "DENIAL_MUST_TERMINATE")
            blocked = True
            continue
        require(
            action.request is not None and isinstance(action.request.payload, ExecuteRequest),
            "EXECUTE_REQUEST_REQUIRED",
        )
        assert action.request is not None and isinstance(action.request.payload, ExecuteRequest)
        payload = action.request.payload
        require(
            payload.intent.for_execution(ActionIntent) == intent
            and payload.prepared.for_execution(PreparedCommand) == prepared
            and payload.safety.for_execution(SafetyDecisionReceipt) == action.issuance.receipt,
            "EXECUTE_REQUEST_SUBSTITUTION",
        )
        scope = action.request.scope
        require(
            scope is not None
            and scope.workspace_id == e.record.workspace_id
            and scope.project_id == e.record.project_id
            and scope.episode_id == e.record.episode_id
            and scope.lease == prepared.lease
            and scope.expected_state == intent.state
            and scope.dependency_closure_hash
            == content_hash(e.provenance.dependencies, exclude=()),
            "EXECUTE_SCOPE",
        )
        if final.status == "ACKNOWLEDGED":
            response = action.response
            require(
                response is not None
                and isinstance(response.outcome, SuccessOutcome)
                and isinstance(response.outcome.payload, ExecuteResult),
                "ACK_BYTES_REQUIRED",
            )
            assert response is not None and isinstance(response.outcome, SuccessOutcome)
            assert isinstance(response.outcome.payload, ExecuteResult)
            actual = response.outcome.payload
            require(
                actual.prepared_command_hash == prepared.content_hash
                and actual.safety_decision_hash == action.issuance.receipt.content_hash
                and actual.observation.state_binding() == final.resulting_state,
                "ACK_SEMANTIC_BINDING",
            )
            require(final.adapter_ack_ref == row.response_ref, "ACK_ARTIFACT_PIN")
            assert final.resulting_state is not None
            require(
                known_states.get(final.resulting_state.observation_digest) == final.resulting_state
                and final.resulting_state.sim_time_ns > intent.state.sim_time_ns,
                "ACK_OBSERVED_STATE",
            )
            last_end = final.resulting_state.sim_time_ns
            preview = action.issuance.preview
            require(preview is not None, "EXECUTION_PREVIEW_MISSING")
            assert preview is not None
            require(
                last_end == prepared.state.sim_time_ns + preview.duration_ns,
                "COMMAND_RELATIVE_TIME_BINDING",
            )
            covered = [r for r in e.safety if r.action_sequence == index]
            require(
                bool(covered)
                and covered[0].before == intent.state
                and covered[-1].after == final.resulting_state,
                "ACTION_PHYSICS_COVERAGE",
            )
            require(all(r.phase == issuance.context.phase for r in covered), "ACTION_PHASE_DRIFT")
            actual_motion = sum(
                (Fraction(j.motion_upper_rad) for r in covered for j in r.interval.joints),
                Fraction(0),
            )
            require(
                actual_motion
                <= Fraction(decision.budget_after.cumulative_joint_motion_rad)
                - Fraction(decision.budget_before.cumulative_joint_motion_rad),
                "ACTUAL_MOTION_EXCEEDS_RESERVATION",
            )
            require(
                Fraction(preview.duration_ns, 1_000_000_000)
                <= Fraction(decision.budget_after.elapsed_sim_seconds)
                - Fraction(decision.budget_before.elapsed_sim_seconds),
                "ACTUAL_TIME_EXCEEDS_RESERVATION",
            )

            require(
                all(a.after == b.before for a, b in zip(covered, covered[1:], strict=False)),
                "ACTION_PHYSICS_GAP",
            )
        elif final.status == "UNKNOWN":
            require(
                action.response is None
                and e.record.termination_reason == "ACKNOWLEDGEMENT_UNCERTAIN",
                "UNKNOWN_ACK_MUST_ABORT",
            )
            blocked = True
        else:
            require(
                action.response is None or isinstance(action.response.outcome, ErrorOutcome),
                "FAILED_ACTION_HAS_SUCCESS_ACK",
            )
            require(e.record.termination_reason in ABORT_REASONS, "FAILED_ACTION_MUST_ABORT")
            blocked = True
    for safety_row in e.safety:
        require(
            safety_row.action_sequence is None or safety_row.action_sequence < len(e.actions),
            "UNKNOWN_PHYSICS_ACTION",
        )
    if e.record.termination_reason == "TASK_COMPLETE":
        require(
            bool(e.actions) and any(a.receipts[-1].status == "ACKNOWLEDGED" for a in e.actions),
            "TASK_WITHOUT_ACKNOWLEDGED_ACTION",
        )


def completeness(e: LoadedEpisode, keys: Mapping[str, TrustedSafetyKey]) -> None:
    _closure(e)
    _coverage(e)
    _actions(e, keys)
    config, spec = e.configuration, e.verification_spec
    for requirements, selected in (
        (spec.task_verifiers, config.task_verifier),
        (spec.safety_verifiers, config.safety_verifier),
        ([spec.evidence_completeness_verifier], config.completeness_verifier),
        ([spec.reproducibility_verifier], config.replay_verifier),
    ):
        require(
            len(requirements) == 1
            and requirements[0].verifier == selected
            and requirements[0].required
            and requirements[0].independent
            and requirements[0].deterministic,
            "UNSUPPORTED_REQUIRED_VERIFIER",
        )


def _inside(inner: AxisAlignedBox, outer: AxisAlignedBox) -> bool:
    return all(
        lo <= a <= b <= hi
        for a, b, lo, hi in zip(
            inner.minimum_m, inner.maximum_m, outer.minimum_m, outer.maximum_m, strict=True
        )
    )


def _intersects(first: AxisAlignedBox, second: AxisAlignedBox) -> bool:
    return all(
        a <= d and c <= b
        for a, b, c, d in zip(
            first.minimum_m, first.maximum_m, second.minimum_m, second.maximum_m, strict=True
        )
    )


def safety_findings(e: LoadedEpisode) -> None:
    envelope, geometry, names = e.safety_envelope, e.geometry, e.descriptor.kinematics.joint_names
    for action in e.actions:
        if action.issuance.preview is not None:
            require(
                geometry == action.issuance.preview.geometry,
                "ACTUAL_GEOMETRY_DIFFERS_FROM_SIGNED_PREVIEW",
            )
    require(
        _same_ref(geometry.descriptor_workspace.artifact, e.descriptor.workspace_ref)
        and _same_ref(geometry.envelope_workspace.artifact, envelope.workspace_ref)
        and _same_ref(geometry.joint_limits_ref, e.descriptor.kinematics.joint_limits_ref)
        and _same_ref(geometry.tool_payload_ref, envelope.tool_payload_ref),
        "ACTUAL_GEOMETRY_PIN",
    )
    require(
        [volume.artifact for volume in geometry.forbidden_volumes]
        == envelope.forbidden_volume_refs,
        "FORBIDDEN_GEOMETRY_PIN",
    )
    require(
        _inside(geometry.envelope_workspace.box, geometry.descriptor_workspace.box),
        "WORKSPACE_MODEL_LIMIT",
    )
    require(
        [limit.joint_name for limit in envelope.joint_limits]
        == names
        == [limit.joint_name for limit in geometry.joint_limits],
        "MODEL_JOINT_ORDER",
    )
    for limit, model in zip(envelope.joint_limits, geometry.joint_limits, strict=True):
        require(
            model.lower_rad <= limit.lower_rad < limit.upper_rad <= model.upper_rad
            and limit.velocity_rad_s <= model.velocity_rad_s
            and limit.acceleration_rad_s2 <= model.acceleration_rad_s2
            and limit.effort_nm <= model.effort_nm,
            "ENVELOPE_EXCEEDS_MODEL",
        )
    channels = e.configuration.safety_channels
    contact_definitions = {
        field.field: field
        for field in (*e.observation_spec.required, *e.observation_spec.optional)
        if field.modality == "CONTACT"
    }
    contact_names = {item.field for item in channels.contacts}
    require(contact_names == contact_definitions.keys(), "ACTUAL_CONTACT_MAPPING_INVENTORY")
    joint_fields = (
        (channels.joint_position, "rad"),
        (channels.joint_velocity, "rad/s"),
        (channels.joint_acceleration, "rad/s2"),
        (channels.joint_effort, "N.m"),
    )
    raw_states = []
    raw_contacts = []
    for frame in e.frames:
        values = tuple(
            _values(frame, field, [len(names)], unit, e.descriptor.kinematics.base_frame)
            for field, unit in joint_fields
        )
        opening = _values(frame, channels.gripper_opening, [1], "m", None)[0]
        raw_states.append((values, opening))
        require(
            {
                name
                for name, channel in frame.channels.items()
                if channel.field.modality == "CONTACT"
            }
            == contact_names,
            "ACTUAL_CONTACT_CHANNEL_INVENTORY",
        )
        contact_values = {}
        for contact_channel in channels.contacts:
            field = contact_definitions[contact_channel.field]
            require(
                field.shape == [1]
                and (
                    (field.dtype in {"float32", "float64"} and field.unit == "N")
                    or (field.dtype == "bool" and field.unit == "1")
                ),
                "ACTUAL_CONTACT_CHANNEL_SCHEMA",
            )
            value = _values(
                frame, field.field, [1], field.unit, e.descriptor.kinematics.base_frame
            )[0]
            require(value >= 0, "ACTUAL_CONTACT_NEGATIVE_FORCE")
            contact_values[field.field] = value
        raw_contacts.append(contact_values)
    require(
        e.descriptor.end_effectors[0].minimum_opening_m <= envelope.gripper.minimum_opening_m
        and envelope.gripper.maximum_opening_m <= e.descriptor.end_effectors[0].maximum_opening_m
        and envelope.gripper.maximum_force_n <= e.descriptor.end_effectors[0].maximum_force_n,
        "GRIPPER_ENVELOPE_EXCEEDS_MODEL",
    )
    motion = Fraction(0)
    for row in e.safety:
        interval = row.interval
        duration = Fraction(interval.end_ns - interval.start_ns, 1_000_000_000)
        require([bound.joint_name for bound in interval.joints] == names, "ACTUAL_JOINT_INVENTORY")
        scale_v = scale_a = 1.0
        if row.action_sequence is not None:
            action = e.actions[row.action_sequence]
            scale_v, scale_a = (
                action.intent.constraints.speed_scale,
                action.intent.constraints.acceleration_scale,
            )
        before, opening_before = raw_states[row.index]
        after, opening_after = raw_states[row.index + 1]
        for joint_index, (bound, limit) in enumerate(
            zip(interval.joints, envelope.joint_limits, strict=True)
        ):
            for endpoint in (before, after):
                require(
                    bound.minimum_rad <= endpoint[0][joint_index] <= bound.maximum_rad
                    and abs(endpoint[1][joint_index]) <= bound.speed_upper_rad_s
                    and abs(endpoint[2][joint_index]) <= bound.acceleration_upper_rad_s2
                    and abs(endpoint[3][joint_index]) <= bound.effort_upper_nm,
                    "ACTUAL_BOUNDS_EXCLUDE_RAW_STATE",
                )
            displacement = Fraction(after[0][joint_index]) - Fraction(before[0][joint_index])
            first_velocity, last_velocity = (
                Fraction(endpoint[1][joint_index]) for endpoint in (before, after)
            )
            require(
                abs(displacement) <= Fraction(bound.motion_upper_rad),
                "ACTUAL_MOTION_EXCLUDES_RAW_STATE",
            )
            # Necessary integral bounds for the closed interval. Checking only
            # sampled endpoint speeds misses an impossible position jump with
            # a purported zero continuous speed/acceleration bound.
            require(
                abs(displacement) <= Fraction(bound.speed_upper_rad_s) * duration,
                "ACTUAL_SPEED_EXCLUDES_DISPLACEMENT",
            )
            acceleration = Fraction(bound.acceleration_upper_rad_s2)
            require(
                abs(last_velocity - first_velocity) <= acceleration * duration
                and all(
                    abs(displacement - velocity * duration)
                    <= acceleration * duration * duration / 2
                    for velocity in (first_velocity, last_velocity)
                ),
                "ACTUAL_ACCELERATION_EXCLUDES_ENDPOINTS",
            )
            require(
                Fraction(bound.minimum_rad)
                >= Fraction(limit.lower_rad) + Fraction(envelope.joint_limit_margin_rad)
                and Fraction(bound.maximum_rad)
                <= Fraction(limit.upper_rad) - Fraction(envelope.joint_limit_margin_rad),
                "ACTUAL_POSITION_LIMIT",
            )
            require(
                Fraction(bound.speed_upper_rad_s)
                <= Fraction(limit.velocity_rad_s) * Fraction(scale_v),
                "ACTUAL_VELOCITY_LIMIT",
            )
            require(
                Fraction(bound.acceleration_upper_rad_s2)
                <= Fraction(limit.acceleration_rad_s2) * Fraction(scale_a),
                "ACTUAL_ACCELERATION_LIMIT",
            )
            require(bound.effort_upper_nm <= limit.effort_nm, "ACTUAL_EFFORT_LIMIT")
            motion += Fraction(bound.motion_upper_rad)
        grip = interval.gripper
        require(
            all(
                grip.minimum_opening_m <= value <= grip.maximum_opening_m
                for value in (opening_before, opening_after)
            ),
            "ACTUAL_GRIPPER_EXCLUDES_RAW_STATE",
        )
        require(
            abs(Fraction(opening_after) - Fraction(opening_before))
            <= Fraction(grip.speed_upper_m_s) * duration,
            "ACTUAL_GRIPPER_SPEED_EXCLUDES_DISPLACEMENT",
        )
        require(
            envelope.gripper.minimum_opening_m
            <= grip.minimum_opening_m
            <= grip.maximum_opening_m
            <= envelope.gripper.maximum_opening_m
            and grip.speed_upper_m_s <= envelope.gripper.maximum_velocity_m_s
            and grip.acceleration_upper_m_s2 <= envelope.gripper.maximum_acceleration_m_s2
            and grip.force_upper_n <= envelope.gripper.maximum_force_n,
            "ACTUAL_GRIPPER_LIMIT",
        )
        require(
            {body.body_name for body in interval.bodies} == set(geometry.body_names),
            "ACTUAL_BODY_INVENTORY",
        )
        for body in interval.bodies:
            require(_inside(body.box, geometry.envelope_workspace.box), "ACTUAL_WORKSPACE_LIMIT")
            require(
                not any(_intersects(body.box, volume.box) for volume in geometry.forbidden_volumes),
                "ACTUAL_FORBIDDEN_VOLUME",
            )
        contact_bounds = {
            frozenset((contact.first_body, contact.second_body)): contact
            for contact in interval.contacts
        }
        for channel in channels.contacts:
            contact_bound = contact_bounds.get(frozenset((channel.first_body, channel.second_body)))
            for contact_endpoint in (raw_contacts[row.index], raw_contacts[row.index + 1]):
                value = contact_endpoint[channel.field]
                if value > 0:
                    require(contact_bound is not None, "ACTUAL_CONTACT_OMITS_RAW_STATE")
                    assert contact_bound is not None
                    require(contact_bound.phase == row.phase, "ACTUAL_CONTACT_RAW_PHASE")
                    if contact_definitions[channel.field].dtype != "bool":
                        require(
                            value <= contact_bound.force_upper_n,
                            "ACTUAL_CONTACT_FORCE_EXCLUDES_RAW",
                        )
        for contact in interval.contacts:
            require(
                envelope.collision_policy is ContactPolicy.DECLARED_CONTACT_ONLY,
                "ACTUAL_CONTACT_FORBIDDEN",
            )
            allowed = next(
                (
                    item
                    for item in envelope.allowed_contacts
                    if (
                        {item.first_body, item.second_body}
                        == {contact.first_body, contact.second_body}
                        and contact.phase in item.phases
                        and contact.phase == row.phase
                    )
                ),
                None,
            )
            require(allowed is not None, "ACTUAL_CONTACT_UNDECLARED")
            assert allowed is not None
            require(
                contact.force_upper_n <= allowed.maximum_force_n
                and contact.penetration_upper_m <= allowed.maximum_penetration_m,
                "ACTUAL_CONTACT_LIMIT",
            )
    require(motion <= Fraction(envelope.max_cumulative_joint_motion_rad), "ACTUAL_MOTION_CAP")
    require(
        Fraction(e.frames[-1].batch.sim_time_ns, 1_000_000_000)
        <= Fraction(envelope.max_episode_sim_seconds),
        "ACTUAL_TIME_CAP",
    )
    require(
        sum(a.issuance.receipt.decision.decision == "ALLOW" for a in e.actions)
        <= envelope.max_actions,
        "ACTUAL_ACTION_CAP",
    )


def _values(
    frame: ObservedFrame, name: str, shape: list[int], unit: str, frame_id: str | None
) -> tuple[float, ...]:
    channel = frame.channels.get(name)
    require(channel is not None, "TASK_CHANNEL_MISSING")
    assert channel is not None
    require(
        channel.field.shape == shape
        and channel.field.unit == unit
        and (frame_id is None or channel.field.frame_id == frame_id),
        "TASK_CHANNEL_UNIT_FRAME",
    )
    require(channel.sim_time_ns == frame.batch.sim_time_ns, "TASK_CHANNEL_TIME_SKEW")
    return channel.numbers()


def _pose(
    frame: ObservedFrame, channels: PoseChannels, goal: PoseGoal
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    return (
        _values(frame, channels.position, [3], "m", goal.frame_id),
        _values(frame, channels.orientation, [4], "1", goal.frame_id),
    )


def quaternion_angle(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    norm = math.sqrt(sum(x * x for x in first) * sum(x * x for x in second))
    require(norm > 0 and math.isfinite(norm), "QUATERNION_INVALID")
    return 2 * math.acos(
        min(1.0, abs(sum(a * b for a, b in zip(first, second, strict=True)) / norm))
    )


def _at_goal(frame: ObservedFrame, channels: PoseChannels, goal: PoseGoal) -> bool:
    position, orientation = _pose(frame, channels, goal)
    return (
        math.dist(position, goal.position_m) <= goal.position_tolerance_m
        and quaternion_angle(orientation, goal.orientation_xyzw) <= goal.orientation_tolerance_rad
    )


def _hold_start(e: LoadedEpisode, hold_ns: int, after_index: int = 0) -> int:
    deadline = e.frames[-1].batch.sim_time_ns - hold_ns
    candidates = [i for i, frame in enumerate(e.frames) if frame.batch.sim_time_ns <= deadline]
    require(bool(candidates) and candidates[-1] >= after_index, "INSUFFICIENT_STABILITY_CAPTURE")
    return candidates[-1]


def task_findings(e: LoadedEpisode) -> tuple[RecordedMetric, ...]:
    require(e.record.termination_reason == "TASK_COMPLETE", "EPISODE_DID_NOT_COMPLETE_TASK")
    task = e.configuration.task
    channels = task.object if isinstance(task, PickPlaceTaskV1) else task.tool
    if isinstance(task, PickPlaceTaskV1):
        _pick_place(e, task)
    else:
        start = _hold_start(e, task.goal.hold_ns)
        require(
            all(_at_goal(frame, channels, task.goal) for frame in e.frames[start:]),
            "REACH_HOLD_NOT_MET",
        )
    position, orientation = _pose(e.frames[-1], channels, task.goal)
    return (
        RecordedMetric(
            name="task_final_position_error_m",
            value=math.dist(position, task.goal.position_m),
            unit="m",
        ),
        RecordedMetric(
            name="task_final_orientation_error_rad",
            value=quaternion_angle(orientation, task.goal.orientation_xyzw),
            unit="rad",
        ),
        RecordedMetric(
            name="episode_duration_s",
            value=e.frames[-1].batch.sim_time_ns / 1_000_000_000,
            unit="s",
        ),
    )


def _pick_place(e: LoadedEpisode, task: PickPlaceTaskV1) -> None:
    positions = [_pose(frame, task.object, task.goal)[0] for frame in e.frames]
    tools = [_pose(frame, task.tool, task.goal)[0] for frame in e.frames]
    openings = [
        _values(frame, task.gripper_opening, [1], "m", task.goal.frame_id)[0] for frame in e.frames
    ]
    forces = [
        [
            _values(frame, name, [1], "N", task.goal.frame_id)[0]
            for name in task.finger_contact_forces
        ]
        for frame in e.frames
    ]
    support = [
        _values(frame, task.support_contact_force, [1], "N", task.goal.frame_id)[0]
        for frame in e.frames
    ]
    require(
        all(value >= 0 for row in forces for value in row) and all(x >= 0 for x in support),
        "NEGATIVE_CONTACT_FORCE",
    )
    require(
        openings[0] >= task.released_opening_min_m
        and all(force <= task.release_contact_max_n for force in forces[0])
        and support[0] >= task.support_contact_min_n,
        "PICK_RESET_NOT_OPEN_SUPPORTED",
    )
    stage = 0
    grasp_index = lift_index = release_index = -1
    relative: tuple[float, ...] = ()
    for index in range(len(e.frames)):
        gripping = openings[index] <= task.closed_opening_max_m and all(
            force >= task.grasp_contact_min_n for force in forces[index]
        )
        if stage == 0 and gripping:
            grasp_index = index
            relative = tuple(a - b for a, b in zip(positions[index], tools[index], strict=True))
            stage = 1
        elif stage in (1, 2, 3):
            rel = tuple(a - b for a, b in zip(positions[index], tools[index], strict=True))
            stable_grasp = (
                gripping and math.dist(rel, relative) <= task.maximum_grasp_relative_drift_m
            )
            lifted = positions[index][2] - positions[0][2] >= task.minimum_lift_m
            if (
                stage == 3
                and openings[index] >= task.released_opening_min_m
                and all(force <= task.release_contact_max_n for force in forces[index])
                and support[index] >= task.support_contact_min_n
                and _at_goal(e.frames[index], task.object, task.goal)
            ):
                release_index, stage = index, 4
            else:
                require(stable_grasp, "GRASP_LOST_BEFORE_RELEASE")
                if (
                    stage == 1
                    and index > grasp_index
                    and lifted
                    and support[index] <= task.release_contact_max_n
                ):
                    lift_index, stage = index, 2
                elif (
                    stage == 2
                    and index > lift_index
                    and lifted
                    and math.dist(positions[index][:2], positions[grasp_index][:2])
                    >= task.minimum_transport_m
                ):
                    stage = 3
    require(stage == 4, "GRASP_LIFT_TRANSPORT_RELEASE_NOT_OBSERVED")
    start = _hold_start(e, task.goal.hold_ns, release_index)
    for index in range(release_index, len(e.frames)):
        require(
            openings[index] >= task.released_opening_min_m
            and all(force <= task.release_contact_max_n for force in forces[index])
            and support[index] >= task.support_contact_min_n,
            "PLACEMENT_NOT_RELEASED_SUPPORTED",
        )
        require(_at_goal(e.frames[index], task.object, task.goal), "PLACEMENT_OUTSIDE_TARGET")
    require(
        all(
            math.dist(positions[i], positions[start]) <= task.maximum_stability_displacement_m
            for i in range(start, len(e.frames))
        ),
        "POST_RELEASE_STABILITY_NOT_MET",
    )


def recovery_findings(e: LoadedEpisode, binding: TrustedVerificationBinding) -> None:
    config = e.configuration
    if config.requires_fresh_episode_recovery:
        recovery = e.recovery
        require(
            recovery is not None and binding.expected_recovery_link_digest is not None,
            "FRESH_RECOVERY_LINK_REQUIRED",
        )
        assert recovery is not None
        link, source, terminal, event = (
            recovery.link,
            recovery.source,
            recovery.termination,
            recovery.event,
        )
        require(
            source.episode_id != e.record.episode_id and source.run_id != e.record.run_id,
            "RECOVERY_MUST_USE_NEW_EPISODE_RUN",
        )
        require(
            source.termination_reason in ABORT_REASONS
            and terminal.final_state in {"ABORTED", "REJECTED"}
            and terminal.termination_reason == source.termination_reason
            and link.source_final_state == terminal.final_state
            and link.source_final_observation == terminal.final_observation,
            "RECOVERY_SOURCE_NOT_ABORTED",
        )
        require(
            event.event_type == "simulation_episode.terminated"
            and (event.workspace_id, event.project_id, event.episode_id, event.run_id)
            == (source.workspace_id, source.project_id, source.episode_id, source.run_id)
            and event.occurred_at == source.finished_at
            and event.producer.principal_id == source.created_by.principal_id,
            "RECOVERY_SOURCE_TERMINATION_PIN",
        )
        require(
            (source.workspace_id, source.project_id) == (e.record.workspace_id, e.record.project_id)
            and link.recovery_scope == e.provenance.scope,
            "RECOVERY_SCOPE",
        )
        require(
            e.record.started_at >= source.finished_at
            and recovery.reset == e.frames[0].batch
            and recovery.reset.episode_id == e.record.episode_id
            and recovery.reset.lease != recovery.source_provenance.pins.lease,
            "RECOVERY_RESET_PIN",
        )
        require(
            recovery.preflight == e.preflight
            and recovery.approval == e.approval
            and recovery.preflight.created_at >= source.finished_at
            and recovery.approval.created_at >= source.finished_at
            and recovery.preflight.content_hash
            != recovery.source_provenance.pins.preflight_receipt_hash
            and e.provenance.contracts.approval != recovery.source_provenance.contracts.approval,
            "RECOVERY_NEW_APPROVAL_PREFLIGHT",
        )
        prior = recovery.source_provenance
        require(
            prior.scope.episode_id == source.episode_id
            and prior.scope.run_id == source.run_id
            and prior.scope.workspace_id == source.workspace_id
            and prior.scope.project_id == source.project_id
            and prior.final_state == terminal.final_observation
            and prior.termination_event_ref == link.source_termination_event_ref,
            "RECOVERY_SOURCE_PROVENANCE_PIN",
        )
    disturbance = config.recovery
    if disturbance is not None:
        require(e.record.termination_reason == "TASK_COMPLETE", "DISTURBANCE_DID_NOT_RECOVER")
        errors = [
            math.dist(
                _values(frame, disturbance.perceived_position, [3], "m", config.task.goal.frame_id),
                _values(
                    frame, disturbance.ground_truth_position, [3], "m", config.task.goal.frame_id
                ),
            )
            for frame in e.frames
        ]
        during = [
            i
            for i, frame in enumerate(e.frames)
            if disturbance.declared_start_ns
            <= frame.batch.sim_time_ns
            < disturbance.declared_end_ns
        ]
        after = [
            i
            for i, frame in enumerate(e.frames)
            if frame.batch.sim_time_ns >= disturbance.declared_end_ns
        ]
        require(
            bool(during) and all(errors[i] >= disturbance.minimum_fault_error_m for i in during),
            "DECLARED_DISTURBANCE_NOT_OBSERVED",
        )
        require(
            bool(after) and all(errors[i] <= disturbance.maximum_recovered_error_m for i in after),
            "PERCEPTION_NOT_RECOVERED",
        )
        before_actions = [
            a for a in e.actions if a.intent.state.sim_time_ns < disturbance.declared_start_ns
        ]
        after_actions = [
            a
            for a in e.actions
            if a.intent.state.sim_time_ns >= disturbance.declared_end_ns
            and a.receipts[-1].status == "ACKNOWLEDGED"
        ]
        require(
            bool(before_actions)
            and bool(after_actions)
            and after_actions[0].prepared.command_ref.digest
            != before_actions[-1].prepared.command_ref.digest,
            "RECOVERY_CHANGED_ADMITTED_COMMAND_REQUIRED",
        )


def _finding(role: str, status: FindingStatus, reason: str) -> RoleFinding:
    return RoleFinding.model_validate({"role": role, "status": status, "reasons": [reason]})


def verify_episode(
    original: CanonicalWriterEnvelope,
    binding: TrustedVerificationBinding,
    source: ArtifactReader,
    *,
    trusted_safety_keys: Mapping[str, TrustedSafetyKey],
) -> ConstructionFindings:
    binding = binding.snapshot()
    trusted_safety_keys = dict(trusted_safety_keys)
    record = original.for_execution(EpisodeRecord)
    findings: list[RoleFinding] = []
    metrics: tuple[RecordedMetric, ...] = ()
    try:
        e = load_episode(original, binding, source)
        completeness(e, trusted_safety_keys)
    except (EvidenceProblem, RoboticsError, ValueError, TypeError) as error:
        reason = (
            error.reason
            if isinstance(error, EvidenceProblem)
            else (
                error.code.value
                if isinstance(error, RoboticsError)
                else "INVALID_OR_UNTRUSTED_EVIDENCE"
            )
        )
        missing = reason in {
            "ARTIFACT_UNAVAILABLE",
            "RESOURCE_CAP_EXHAUSTED",
            "ACTUAL_SAFETY_COVERAGE_MISSING",
            "UNSUPPORTED_REQUIRED_VERIFIER",
            "FRESH_RECOVERY_LINK_REQUIRED",
        }
        findings.extend(
            [
                _finding("COMPLETENESS", "INCONCLUSIVE" if missing else "VIOLATED", reason),
                _finding("TASK", "INCONCLUSIVE", "COMPLETE_EVIDENCE_REQUIRED"),
                _finding("SAFETY", "INCONCLUSIVE", "COMPLETE_EVIDENCE_REQUIRED"),
            ]
        )
    else:
        findings.append(_finding("COMPLETENESS", "SATISFIED", "CAPTURE_CONSTRUCTION_CONSISTENT"))
        checks: list[tuple[str, Callable[[], None]]] = [("SAFETY", lambda: safety_findings(e))]
        if e.configuration.recovery is not None or e.configuration.requires_fresh_episode_recovery:
            checks.append(("RECOVERY", lambda: recovery_findings(e, binding)))
        for role, check in checks:
            try:
                check()
                findings.append(_finding(role, "SATISFIED", "CONSTRUCTION_CHECKS_SATISFIED"))
            except EvidenceProblem as error:
                findings.append(
                    _finding(
                        role,
                        "INCONCLUSIVE"
                        if error.reason in {"FRESH_RECOVERY_LINK_REQUIRED", "TASK_CHANNEL_MISSING"}
                        else "VIOLATED",
                        error.reason,
                    )
                )
        try:
            metrics = task_findings(e)
            findings.append(_finding("TASK", "SATISFIED", "OBSERVED_TASK_CONDITIONS_SATISFIED"))
        except EvidenceProblem as error:
            findings.append(
                _finding(
                    "TASK",
                    "INCONCLUSIVE" if error.reason == "TASK_CHANNEL_MISSING" else "VIOLATED",
                    error.reason,
                )
            )
    findings.append(_finding("REPLAY", "INCONCLUSIVE", "SEPARATE_REPLAY_CAPTURE_REQUIRED"))
    return ConstructionFindings(
        episode_record_hash=record.content_hash,
        configuration_digest=binding.configuration_digest,
        findings=tuple(findings),
        derived_metrics=metrics,
    )
