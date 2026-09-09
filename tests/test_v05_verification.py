"""Pure synthetic construction witnesses, never simulator or benchmark evidence."""

from __future__ import annotations

import json
import math
import struct
from dataclasses import replace

import pytest
from pydantic import ValidationError
from v05_sdk_fixtures import replace_contract
from v05_verification_fixtures import Bundle, bundle

from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics import CanonicalWriterEnvelope
from accretion.contracts.robotics.values import ObservationField, ReplayClass
from accretion.robotics.errors import RoboticsError
from accretion.robotics.observations import TENSOR_MEDIA_TYPE
from accretion.robotics.verification.checks import (
    _actions,
    _coverage,
    recovery_findings,
    safety_findings,
    task_findings,
    verify_episode,
)
from accretion.robotics.verification.reader import (
    EvidenceProblem,
    EvidenceReader,
    ObservedChannel,
    load_episode,
)
from accretion.robotics.verification.replay import compare_loaded, compare_replay
from accretion.robotics.verification.types import (
    ChunkManifestV1,
    EvidenceReadLimits,
    FieldToleranceV1,
    PickPlaceTaskV1,
    PoseChannels,
    PoseGoal,
    ReplayToleranceProfileV1,
    TerminationPayloadV1,
)


@pytest.fixture
def capture():
    return bundle()


@pytest.fixture
def loaded(capture):
    return load_episode(capture.original, capture.binding, capture.artifacts)


def reasons(result):
    return {finding.role: (finding.status, finding.reasons) for finding in result.findings}


def verify(capture):
    return verify_episode(
        capture.original, capture.binding, capture.artifacts, trusted_safety_keys=capture.keys
    )


def put(capture: Bundle, value):
    return capture.artifacts.put(canonical_json(value), media_type="application/json")


def channel(frame, name, values, *, unit=None, modality=None):
    previous = frame.channels.get(name)
    field = (
        previous.field
        if previous
        else ObservationField(
            field=name,
            dtype="float64",
            modality=modality or "POSE",
            frame_id="base",
            sensor_id=name,
            shape=[len(values)],
            unit=unit or "m",
        )
    )
    if unit is not None:
        field = field.model_copy(update={"unit": unit})
    channels = dict(frame.channels)
    channels[name] = ObservedChannel(
        field, struct.pack("<" + "d" * len(values), *values), frame.batch.sim_time_ns
    )
    return replace(frame, channels=channels)


def test_full_original_capture_is_construction_only(capture):
    result = verify(capture)
    assert result.evidence_level == "CONSTRUCTION_FINDINGS"
    assert {role: status for role, (status, _) in reasons(result).items()} == {
        "COMPLETENESS": "SATISFIED",
        "SAFETY": "SATISFIED",
        "TASK": "SATISFIED",
        "REPLAY": "INCONCLUSIVE",
    }
    assert {m.name for m in result.derived_metrics} == {
        "task_final_position_error_m",
        "task_final_orientation_error_rad",
        "episode_duration_s",
    }
    assert "untrusted_success" not in {m.name for m in result.derived_metrics}
    assert not hasattr(result, "status")


@pytest.mark.parametrize(
    "change", ["truncated", "digest", "missing", "bool", "unknown", "duplicate"]
)
def test_manifest_tampering_fails_closed(capture, change):
    ref = capture.record.provenance_manifest_ref
    raw = capture.artifacts.blobs[ref.digest]
    if change == "truncated":
        capture.artifacts.blobs[ref.digest] = raw[:-1]
    elif change == "digest":
        capture.artifacts.blobs[ref.digest] = (
            raw.replace(b"APPROACH", b"APPR0ACH") if b"APPROACH" in raw else b"x" * len(raw)
        )
    else:
        value = json.loads(raw)
        if change == "missing":
            value.pop("physics_step_ns")
        elif change == "bool":
            value["physics_step_ns"] = True
        elif change == "unknown":
            value["producer_success"] = True
        elif change == "duplicate":
            raw = raw[:-1] + b',"physics_step_ns":1}'
        if change != "duplicate":
            raw = canonical_json(value)
        updated = capture.artifacts.put(raw, media_type="application/json")
        capture.record = replace_contract(capture.record, provenance_manifest_ref=updated)
    result = reasons(verify(capture))
    assert result["COMPLETENESS"][0] == "VIOLATED"
    assert result["TASK"][0] == "INCONCLUSIVE"


def test_original_writer_seal_and_major_are_not_projected(capture):
    raw = capture.original.payload()
    raw["termination_reason"] = "CANCELLED"
    with pytest.raises(ValueError, match="original verified"):
        CanonicalWriterEnvelope(json.dumps(raw))
    raw["schema_version"] = "2.0.0"
    raw["content_hash"] = content_hash(raw)
    with pytest.raises(ValueError, match="major"):
        CanonicalWriterEnvelope(json.dumps(raw))


def test_future_minor_original_cannot_be_execution_evidence(capture):
    raw = capture.original.payload()
    raw.update(schema_version="1.1.0", future_value=1)
    raw["content_hash"] = content_hash(raw)
    original = CanonicalWriterEnvelope(json.dumps(raw))
    with pytest.raises(ValueError, match="fully understood"):
        verify_episode(
            original, capture.binding, capture.artifacts, trusted_safety_keys=capture.keys
        )


def test_untrusted_configuration_and_ancillary_inventory(capture):
    altered = capture.binding.configuration.model_copy(update={"maximum_observation_gap_ns": 1})
    capture.binding = replace(capture.binding, configuration_bytes=canonical_json(altered))
    assert reasons(verify(capture))["COMPLETENESS"][1] == ("UNTRUSTED_CONFIGURATION",)
    capture = bundle()
    capture.binding = replace(capture.binding, expected_ancillary_artifacts=())
    assert reasons(verify(capture))["COMPLETENESS"][1] == ("ANCILLARY_INVENTORY_NOT_TRUSTED",)


def test_bounded_reader_hash_length_metadata_and_resource_caps(capture):
    raw = b"abcdefgh"
    ref = capture.artifacts.put(raw, media_type=TENSOR_MEDIA_TYPE)
    reader = EvidenceReader(capture.artifacts, EvidenceReadLimits(8, 1, 10))
    assert reader.read(ref, max_bytes=8) == raw
    assert reader.read(ref, max_bytes=8) == raw
    with pytest.raises(EvidenceProblem, match="METADATA"):
        reader.read(ref.model_copy(update={"media_type": "application/json"}), max_bytes=8)
    second = capture.artifacts.put(b"i", media_type=TENSOR_MEDIA_TYPE)
    with pytest.raises(RoboticsError):
        reader.read(second, max_bytes=8)
    capture.artifacts.blobs[ref.digest] = raw[:-1]
    with pytest.raises(RoboticsError):
        EvidenceReader(capture.artifacts, EvidenceReadLimits(8, 1, 10)).read(ref, max_bytes=8)


def test_scope_and_snapshot_detach_before_reader_callback(capture):
    source = capture.artifacts
    original_scope = capture.binding.pins.episode_id

    class MutatingSource:
        def iter_bytes(self, ref, *, max_bytes, chunk_size):
            capture.binding.pins.episode_id = "sep_" + "0" * 26
            yield from source.iter_bytes(ref, max_bytes=max_bytes, chunk_size=chunk_size)

    result = verify_episode(
        capture.original, capture.binding, MutatingSource(), trusted_safety_keys=capture.keys
    )
    assert reasons(result)["COMPLETENESS"][0] == "SATISFIED"
    assert capture.binding.pins.episode_id != original_scope


@pytest.mark.parametrize(
    "change,reason",
    [
        ("missing_ack", "ACK_BYTES_REQUIRED"),
        ("wrong_ack_ref", "ACK_ARTIFACT_PIN"),
        ("wrong_state", "ACTION_START_STATE"),
        ("bad_signature", "SIGNATURE"),
        ("missing_admission", "ACTION_LEDGER_TRANSITIONS"),
    ],
)
def test_action_original_ack_and_signature_binding(loaded, capture, change, reason):
    action = loaded.actions[0]
    if change == "missing_ack":
        action = replace(action, response=None)
    elif change == "wrong_ack_ref":
        action = replace(
            action, row=action.row.model_copy(update={"response_ref": action.row.request_ref})
        )
    elif change == "wrong_state":
        action = replace(
            action,
            intent=replace_contract(action.intent, state=loaded.frames[1].batch.state_binding()),
        )
    elif change == "bad_signature":
        with pytest.raises(ValueError, match="signature|key|signer"):
            _actions(loaded, {})
        return
    else:
        action = replace(action, receipts=action.receipts[1:])
    with pytest.raises(EvidenceProblem, match=reason):
        _actions(replace(loaded, actions=(action,)), capture.keys)


def test_unknown_ack_cannot_resume_terminated_episode(loaded, capture):
    action = loaded.actions[0]
    unknown = replace_contract(
        action.receipts[-1], status="UNKNOWN", adapter_ack_ref=None, resulting_state=None
    )
    first = replace(action, response=None, receipts=(action.receipts[0], unknown))
    episode = replace(
        loaded,
        record=replace_contract(loaded.record, termination_reason="ACKNOWLEDGEMENT_UNCERTAIN"),
        actions=(first, action),
    )
    with pytest.raises(EvidenceProblem, match="ACTION_AFTER_TERMINAL_UNCERTAINTY"):
        _actions(episode, capture.keys)


@pytest.mark.parametrize(
    "change,reason",
    [
        ("gap", "ACTUAL_SAFETY_COVERAGE"),
        ("state", "ACTUAL_SAFETY_STATE"),
        ("phase", "ACTUAL_SAFETY_PHASE"),
        ("time", "ACTUAL_SAFETY_TIME"),
        ("disposition", "PRODUCER_EXECUTION_DISPOSITION"),
    ],
)
def test_coverage_requires_both_adjacent_observations(loaded, change, reason):
    if change == "gap":
        loaded = replace(loaded, safety=loaded.safety[:1])
    elif change == "disposition":
        loaded = replace(
            loaded, termination=loaded.termination.model_copy(update={"final_state": "ABORTED"})
        )
    else:
        row = loaded.safety[0]
        if change == "state":
            row = row.model_copy(update={"before": loaded.frames[1].batch.state_binding()})
        elif change == "phase":
            row = row.model_copy(update={"phase": "GRASP"})
        else:
            row = row.model_copy(
                update={"interval": row.interval.model_copy(update={"start_ns": 1})}
            )
        loaded = replace(loaded, safety=(row, *loaded.safety[1:]))
    with pytest.raises(EvidenceProblem, match=reason):
        _coverage(loaded)


@pytest.mark.parametrize(
    "change,reason",
    [
        ("joint", "ACTUAL_BOUNDS_EXCLUDE_RAW_STATE"),
        ("gripper", "ACTUAL_GRIPPER_EXCLUDES_RAW_STATE"),
        ("motion", "ACTUAL_MOTION_EXCLUDES_RAW_STATE"),
        ("interior", "ACTUAL_POSITION_LIMIT"),
        ("contact", "ACTUAL_CONTACT"),
    ],
)
def test_actual_safety_uses_raw_values_and_interior_bounds(loaded, change, reason):
    row = loaded.safety[0]
    interval = row.interval.model_dump(mode="python")
    if change == "joint":
        interval["joints"][0]["maximum_rad"] = 0.01
    elif change == "gripper":
        interval["gripper"].update(minimum_opening_m=0.02, maximum_opening_m=0.03)
    elif change == "motion":
        interval["joints"][0]["motion_upper_rad"] = 0.001
    elif change == "interior":
        interval["joints"][0]["maximum_rad"] = 2.0
    else:
        interval["contacts"] = [
            dict(
                first_body="arm",
                second_body="payload",
                phase="APPROACH",
                force_upper_n=1.0,
                penetration_upper_m=0.001,
            )
        ]
    row = type(row).model_validate({**row.model_dump(mode="python"), "interval": interval})
    with pytest.raises(EvidenceProblem, match=reason):
        safety_findings(replace(loaded, safety=(row, *loaded.safety[1:])))


@pytest.mark.parametrize("position,works", [(0.01, True), (math.nextafter(0.01, math.inf), False)])
def test_reach_position_boundary_uses_observations(loaded, position, works):
    frames = tuple(channel(frame, "tool_position", [position, 0.0, 0.0]) for frame in loaded.frames)
    loaded = replace(loaded, frames=frames)
    if works:
        assert task_findings(loaded)[0].value == position
    else:
        with pytest.raises(EvidenceProblem, match="REACH_HOLD"):
            task_findings(loaded)


def test_reach_requires_orientation_and_sustained_hold(loaded):
    frame = channel(loaded.frames[-1], "tool_orientation", [0.0, 0.0, 1.0, 0.0])
    with pytest.raises(EvidenceProblem, match="REACH_HOLD"):
        task_findings(replace(loaded, frames=(*loaded.frames[:-1], frame)))
    frame = channel(loaded.frames[1], "tool_position", [0.02, 0.0, 0.0])
    with pytest.raises(EvidenceProblem, match="REACH_HOLD"):
        task_findings(replace(loaded, frames=(loaded.frames[0], frame, loaded.frames[-1])))


def pick_capture(loaded):
    goal = PoseGoal(
        frame_id="base",
        position_m=(0.2, 0.0, 0.0),
        orientation_xyzw=(0.0, 0.0, 0.0, 1.0),
        position_tolerance_m=0.01,
        orientation_tolerance_rad=0.01,
        hold_ns=1_000_000_000,
    )
    task = PickPlaceTaskV1(
        tool=PoseChannels(position="tool_position", orientation="tool_orientation"),
        object=PoseChannels(position="object_position", orientation="object_orientation"),
        gripper_opening="opening",
        finger_contact_forces=("left", "right"),
        support_contact_force="support",
        goal=goal,
        closed_opening_max_m=0.01,
        released_opening_min_m=0.03,
        grasp_contact_min_n=1.0,
        release_contact_max_n=0.1,
        support_contact_min_n=1.0,
        minimum_lift_m=0.1,
        minimum_transport_m=0.1,
        maximum_grasp_relative_drift_m=0.01,
        maximum_stability_displacement_m=0.002,
    )
    frames = []
    positions = [
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.2),
        (0.2, 0.0, 0.2),
        (0.2, 0.0, 0.0),
        (0.2, 0.0, 0.0),
    ]
    for i, position in enumerate(positions):
        frame = replace(
            loaded.frames[0],
            batch=loaded.frames[0].batch.model_copy(
                update={"sequence": i, "sim_time_ns": i * 1_000_000_000}
            ),
        )
        for name, values, unit, modality in [
            ("tool_position", position, "m", "POSE"),
            ("tool_orientation", (0.0, 0.0, 0.0, 1.0), "1", "POSE"),
            ("object_position", position, "m", "POSE"),
            ("object_orientation", (0.0, 0.0, 0.0, 1.0), "1", "POSE"),
            ("opening", [0.0 if 1 <= i <= 3 else 0.04], "m", "GRIPPER_OPENING"),
            ("left", [2.0 if 1 <= i <= 3 else 0.0], "N", "CONTACT"),
            ("right", [2.0 if 1 <= i <= 3 else 0.0], "N", "CONTACT"),
            ("support", [0.0 if 2 <= i <= 3 else 2.0], "N", "CONTACT"),
        ]:
            frame = channel(frame, name, values, unit=unit, modality=modality)
        frames.append(frame)
    return replace(
        loaded,
        frames=tuple(frames),
        configuration=loaded.configuration.model_copy(update={"task": task}),
    )


def test_observed_pick_lift_transport_release_stability(loaded):
    assert task_findings(pick_capture(loaded))[0].value == 0.0


@pytest.mark.parametrize(
    "change", ["one_finger", "no_lift", "no_transport", "stuck_grip", "unsupported", "unstable"]
)
def test_pick_place_negative_observations(loaded, change):
    e = pick_capture(loaded)
    frames = list(e.frames)
    if change == "one_finger":
        frames = [channel(frame, "right", [0.0]) for frame in frames]
    elif change == "no_lift":
        for i in (2, 3):
            position = list(frames[i].channels["object_position"].numbers())
            position[2] = 0.0
            frames[i] = channel(
                channel(frames[i], "object_position", position), "tool_position", position
            )
    elif change == "no_transport":
        frames[3] = channel(
            channel(frames[3], "object_position", [0.0, 0.0, 0.2]), "tool_position", [0.0, 0.0, 0.2]
        )
    elif change == "stuck_grip":
        for i in (4, 5):
            frames[i] = channel(frames[i], "opening", [0.0])
    elif change == "unsupported":
        frames[4] = channel(frames[4], "support", [0.0])
    else:
        frames[5] = channel(frames[5], "object_position", [0.205, 0.0, 0.0])
    with pytest.raises(EvidenceProblem):
        task_findings(replace(e, frames=tuple(frames)))


def test_fresh_recovery_absence_is_not_success(capture, loaded):
    config = loaded.configuration.model_copy(update={"requires_fresh_episode_recovery": True})
    with pytest.raises(EvidenceProblem, match="FRESH_RECOVERY_LINK_REQUIRED"):
        recovery_findings(replace(loaded, configuration=config), capture.binding)


def test_no_producer_accepted_or_pass_disposition():
    for value in ("ACCEPTED", "PASS"):
        with pytest.raises(ValidationError):
            TerminationPayloadV1(
                termination_reason="TASK_COMPLETE",
                final_state=value,
                final_observation=dict(
                    observation_sequence=0, sim_time_ns=0, observation_digest="a" * 64
                ),
                action_count=0,
                observation_count=1,
            )


def replay_copy(loaded, mode=ReplayClass.TOLERANT):
    source = replace(loaded, record=replace_contract(loaded.record, replay_class=mode))
    replay = replace(
        source,
        provenance=source.provenance.model_copy(
            update={
                "pins": source.provenance.pins.model_copy(
                    update={
                        "lease": source.provenance.pins.lease.model_copy(update={"generation": 2})
                    }
                )
            }
        ),
        record=replace_contract(source.record, episode_id="sep_" + "0" * 26, run_id="replay-run"),
    )
    return source, replay


@pytest.mark.parametrize("mode", [ReplayClass.EXACT, ReplayClass.TOLERANT])
def test_supplied_replay_comparison_positive_only(loaded, mode):
    source, replay = replay_copy(loaded, mode)
    compare_loaded(source, replay)


def test_same_capture_is_not_replay(capture):
    result = compare_replay(
        capture.original,
        capture.binding,
        capture.artifacts,
        capture.original,
        capture.binding,
        capture.artifacts,
        trusted_safety_keys=capture.keys,
    )
    assert result.status == "VIOLATED" and result.reasons == ("REPLAY_REUSED_EPISODE",)


@pytest.mark.parametrize(
    "change,reason",
    [
        ("exact", "REPLAY_EXACT_BYTES"),
        ("tolerant", "REPLAY_FIELD_TOLERANCE"),
        ("inventory", "REPLAY_CHANNEL_INVENTORY"),
        ("profile", "REPLAY_TOLERANCE_COVERAGE"),
        ("statistical", "REGISTERED_STATISTICAL_PROTOCOL_REQUIRED"),
    ],
)
def test_replay_never_silently_drops_evidence(loaded, change, reason):
    mode = (
        ReplayClass.EXACT
        if change == "exact"
        else ReplayClass.STATISTICAL
        if change == "statistical"
        else ReplayClass.TOLERANT
    )
    source, replay = replay_copy(loaded, mode)
    if change in {"exact", "tolerant"}:
        frames = list(replay.frames)
        frames[0] = channel(frames[0], "q", [0.02])
        replay = replace(replay, frames=tuple(frames))
    elif change == "inventory":
        frame = replay.frames[0]
        fields = dict(frame.channels)
        fields.pop("q")
        replay = replace(replay, frames=(replace(frame, channels=fields), *replay.frames[1:]))
    elif change == "profile":
        profile = source.replay_tolerances.model_copy(
            update={"fields": source.replay_tolerances.fields[1:]}
        )
        source = replace(source, replay_tolerances=profile)
        replay = replace(replay, replay_tolerances=profile)
    with pytest.raises(EvidenceProblem, match=reason):
        compare_loaded(source, replay)


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1.0])
def test_explicit_thresholds_reject_invalid_numbers(value):
    with pytest.raises(ValidationError):
        FieldToleranceV1(field="x", unit="m", mode="COMPONENT", absolute=value, relative=0.0)


def test_empty_duplicate_and_gapped_manifests_and_tolerances(loaded):
    ref = loaded.record.metrics_ref
    with pytest.raises(ValidationError):
        ChunkManifestV1(scope=loaded.provenance.scope, role="METRICS", total_records=1, chunks=())
    with pytest.raises(ValidationError):
        ChunkManifestV1(
            scope=loaded.provenance.scope,
            role="METRICS",
            total_records=1,
            chunks=({"artifact": ref, "first_index": 1, "record_count": 1},),
        )
    profile = loaded.replay_tolerances.model_dump(mode="python")
    profile["fields"].append(profile["fields"][0]) if isinstance(profile["fields"], list) else None
    profile["fields"] = (*loaded.replay_tolerances.fields, loaded.replay_tolerances.fields[0])
    with pytest.raises(ValidationError):
        ReplayToleranceProfileV1.model_validate(profile)


def test_public_replay_comparison_consumes_two_fresh_complete_captures(capture):
    replay = bundle(replay=True)
    result = compare_replay(
        capture.original,
        capture.binding,
        capture.artifacts,
        replay.original,
        replay.binding,
        replay.artifacts,
        trusted_safety_keys=capture.keys,
    )
    assert result.status == "SATISFIED"
    assert result.reasons == ("SUPPLIED_CAPTURE_COMPARISON_ONLY",)


def test_replay_tolerance_exact_boundary_and_quaternion_equivalence(loaded):
    source, replay = replay_copy(loaded)
    frame = channel(replay.frames[0], "q", [0.01])
    frame = channel(frame, "tool_orientation", [0.0, 0.0, 0.0, -1.0])
    compare_loaded(source, replace(replay, frames=(frame, *replay.frames[1:])))
    frame = channel(frame, "q", [math.nextafter(0.01, math.inf)])
    with pytest.raises(EvidenceProblem, match="REPLAY_FIELD_TOLERANCE"):
        compare_loaded(source, replace(replay, frames=(frame, *replay.frames[1:])))


def test_replay_missing_metric_tolerance_does_not_vacuously_pass(loaded):
    source, replay = replay_copy(loaded)
    profile = source.replay_tolerances.model_copy(
        update={"metric_tolerances": source.replay_tolerances.metric_tolerances[:-1]}
    )
    with pytest.raises(EvidenceProblem, match="REPLAY_METRIC_TOLERANCE_COVERAGE"):
        compare_loaded(
            replace(source, replay_tolerances=profile), replace(replay, replay_tolerances=profile)
        )


def fresh_recovery_bundle():
    from datetime import timedelta

    from accretion.robotics.verification.types import (
        ActionChunkV1,
        EvidenceChunkRef,
        RecoveryLinkV1,
    )

    source = bundle()
    s = load_episode(source.original, source.binding, source.artifacts)
    action = s.actions[0]
    unknown = replace_contract(
        action.receipts[-1], status="UNKNOWN", adapter_ack_ref=None, resulting_state=None
    )
    row = action.row.model_copy(
        update={
            "response_ref": None,
            "receipt_refs": (action.row.receipt_refs[0], put(source, unknown)),
        }
    )
    action_chunk = put(
        source, ActionChunkV1(scope=s.provenance.scope, first_index=0, records=(row,))
    )
    actions_ref = put(
        source,
        ChunkManifestV1(
            scope=s.provenance.scope,
            role="ACTIONS",
            total_records=1,
            chunks=(EvidenceChunkRef(artifact=action_chunk, first_index=0, record_count=1),),
        ),
    )
    terminal = s.termination.model_copy(
        update={"final_state": "ABORTED", "termination_reason": "ACKNOWLEDGEMENT_UNCERTAIN"}
    )
    event = replace_contract(
        s.termination_event, payload=terminal.model_dump(mode="json"), payload_hash=""
    )
    event_ref = put(source, event)
    provenance = s.provenance.model_copy(
        update={
            "termination_event_ref": event_ref,
            "artifacts": s.provenance.artifacts.model_copy(update={"actions": actions_ref}),
        }
    )
    source.record = replace_contract(
        s.record,
        termination_reason="ACKNOWLEDGEMENT_UNCERTAIN",
        provenance_manifest_ref=put(source, provenance),
        action_receipts_ref=actions_ref,
    )
    assert reasons(verify(source))["COMPLETENESS"][0] == "SATISFIED"
    fresh = bundle(replay=True, wall_offset=timedelta(minutes=5))
    current = load_episode(fresh.original, fresh.binding, fresh.artifacts)
    fresh.artifacts.blobs.update(source.artifacts.blobs)
    link = RecoveryLinkV1(
        source_episode_record_ref=put(fresh, source.record),
        source_termination_event_ref=event_ref,
        source_final_state="ABORTED",
        source_final_observation=terminal.final_observation,
        recovery_scope=current.provenance.scope,
        reset_batch_ref=put(fresh, current.frames[0].batch),
        preflight_ref=current.provenance.contracts.preflight,
        approval_ref=current.provenance.contracts.approval,
    )
    link_ref = put(fresh, link)
    config = current.configuration.model_copy(update={"requires_fresh_episode_recovery": True})
    provenance = current.provenance.model_copy(
        update={"recovery_link_ref": link_ref, "verifier_configuration_ref": put(fresh, config)}
    )
    fresh.binding = replace(
        fresh.binding,
        configuration_bytes=canonical_json(config),
        expected_recovery_link_digest=link_ref.digest,
    )
    fresh.record = replace_contract(fresh.record, provenance_manifest_ref=put(fresh, provenance))
    return fresh


def test_fresh_recovery_requires_original_abort_and_new_reset_approval():
    fresh = fresh_recovery_bundle()
    assert reasons(verify(fresh))["RECOVERY"][0] == "SATISFIED"
    assert all(
        status == "SATISFIED"
        for role, (status, _) in reasons(verify(fresh)).items()
        if role != "REPLAY"
    )


@pytest.mark.parametrize(
    "change,reason",
    [
        ("same_episode", "RECOVERY_MUST_USE_NEW_EPISODE_RUN"),
        ("not_aborted", "RECOVERY_SOURCE_NOT_ABORTED"),
        ("same_lease", "RECOVERY_RESET_PIN"),
        ("wrong_original_event", "RECOVERY_SOURCE_PROVENANCE_PIN"),
        ("old_approval", "RECOVERY_NEW_APPROVAL_PREFLIGHT"),
    ],
)
def test_recovery_rejects_resuming_or_substituting_original(change, reason):
    fresh = fresh_recovery_bundle()
    e = load_episode(fresh.original, fresh.binding, fresh.artifacts)
    recovery = e.recovery
    assert recovery is not None
    if change == "same_episode":
        recovery = replace(
            recovery, source=replace_contract(recovery.source, episode_id=e.record.episode_id)
        )
    elif change == "not_aborted":
        recovery = replace(
            recovery,
            termination=recovery.termination.model_copy(update={"final_state": "VERIFYING"}),
        )
    elif change == "same_lease":
        recovery = replace(
            recovery,
            source_provenance=recovery.source_provenance.model_copy(
                update={
                    "pins": recovery.source_provenance.pins.model_copy(
                        update={"lease": e.provenance.pins.lease}
                    )
                }
            ),
        )
    elif change == "old_approval":
        recovery = replace(
            recovery,
            approval=replace_contract(recovery.approval, created_at=recovery.source.started_at),
        )
    else:
        recovery = replace(
            recovery,
            source_provenance=recovery.source_provenance.model_copy(
                update={"termination_event_ref": e.provenance.termination_event_ref}
            ),
        )
    with pytest.raises(EvidenceProblem, match=reason):
        recovery_findings(replace(e, recovery=recovery), fresh.binding)


def test_exact_replay_includes_normalized_actual_safety_trace(loaded):
    source, replay = replay_copy(loaded, ReplayClass.EXACT)
    row = replay.safety[0]
    interval = row.interval.model_copy(
        update={"gripper": row.interval.gripper.model_copy(update={"force_upper_n": 1.1})}
    )
    row = row.model_copy(update={"interval": interval})
    with pytest.raises(EvidenceProblem, match="REPLAY_EXACT_SAFETY_TRACE"):
        compare_loaded(source, replace(replay, safety=(row, *replay.safety[1:])))
