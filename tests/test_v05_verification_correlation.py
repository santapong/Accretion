"""Pure raw-evidence contradiction regressions; no authentic runtime claims."""

from __future__ import annotations

import json
import math
from dataclasses import replace

import pytest
from pydantic import ValidationError
from test_v05_verification import channel
from v05_sdk_fixtures import replace_contract
from v05_verification_fixtures import bundle

from accretion.contracts.canonical import canonical_json
from accretion.robotics.verification.checks import safety_findings, verify_episode
from accretion.robotics.verification.reader import EvidenceProblem, load_episode
from accretion.robotics.verification.replay import compare_replay
from accretion.robotics.verification.types import ContactChannelV1, PerceptionRecoveryV1


def document(capture, ref):
    digest = ref.digest if hasattr(ref, "digest") else ref["digest"]
    return json.loads(capture.artifacts.blobs[digest])


def put(capture, value):
    return capture.artifacts.put(canonical_json(value), media_type="application/json")


def findings(capture):
    return {
        row.role: (row.status, row.reasons)
        for row in verify_episode(
            capture.original, capture.binding, capture.artifacts, trusted_safety_keys=capture.keys
        ).findings
    }


def mutate_actual(capture, transform, geometry=None, *, replace_trusted_geometry=False):
    """Reseal producer-owned artifacts without changing signed safety issuance."""
    provenance = document(capture, capture.record.provenance_manifest_ref)
    manifest = document(capture, capture.record.safety_events_ref)
    for entry in manifest["chunks"]:
        chunk = document(capture, entry["artifact"])
        for row in chunk["records"]:
            transform(row["interval"])
        entry["artifact"] = put(capture, chunk).model_dump(mode="json")
    ref = put(capture, manifest)
    provenance["artifacts"]["safety"] = ref.model_dump(mode="json")
    if geometry is not None:
        value = document(capture, provenance["geometry_ref"])
        geometry(value)
        geometry_ref = put(capture, value)
        provenance["geometry_ref"] = geometry_ref.model_dump(mode="json")
        if replace_trusted_geometry:
            capture.binding = replace(capture.binding, expected_geometry_ref=geometry_ref)
    capture.record = replace_contract(
        capture.record, safety_events_ref=ref, provenance_manifest_ref=put(capture, provenance)
    )


def configure(capture, **updates):
    config = capture.binding.configuration.model_copy(update=updates)
    capture.binding = replace(capture.binding, configuration_bytes=canonical_json(config))
    provenance = document(capture, capture.record.provenance_manifest_ref)
    provenance["verifier_configuration_ref"] = put(capture, config).model_dump(mode="json")
    capture.record = replace_contract(
        capture.record, provenance_manifest_ref=put(capture, provenance)
    )


@pytest.mark.parametrize("change", ["omit_bodies", "widen_workspace"])
@pytest.mark.parametrize("replace_trusted_geometry", [False, True])
def test_rewritten_geometry_cannot_hide_bodies_or_workspace(change, replace_trusted_geometry):
    capture = bundle()
    original = load_episode(capture.original, capture.binding, capture.artifacts)

    def geometry(value):
        if change == "omit_bodies":
            value["body_names"] = ["arm"]
        else:
            for name in ("descriptor_workspace", "envelope_workspace"):
                value[name]["box"] = {"minimum_m": [-100.0] * 3, "maximum_m": [100.0] * 3}

    def interval(value):
        if change == "omit_bodies":
            value["bodies"] = value["bodies"][:1]
        else:
            value["bodies"][0]["box"] = {"minimum_m": [2.0] * 3, "maximum_m": [3.0] * 3}

    mutate_actual(capture, interval, geometry, replace_trusted_geometry=replace_trusted_geometry)
    if replace_trusted_geometry:
        # Even a mistakenly repinned geometry cannot contradict retained signed inputs.
        loaded = load_episode(capture.original, capture.binding, capture.artifacts)
        assert loaded.actions[0].issuance.receipt == original.actions[0].issuance.receipt
        assert findings(capture)["SAFETY"] == (
            "VIOLATED",
            ("ACTUAL_GEOMETRY_DIFFERS_FROM_SIGNED_PREVIEW",),
        )
    else:
        assert findings(capture)["COMPLETENESS"] == ("VIOLATED", ("UNTRUSTED_GEOMETRY",))
        assert findings(capture)["SAFETY"][0] == "INCONCLUSIVE"


def test_geometry_trust_uses_full_reference_metadata():
    capture = bundle()
    capture.binding = replace(
        capture.binding,
        expected_geometry_ref=capture.binding.expected_geometry_ref.model_copy(
            update={"size_bytes": capture.binding.expected_geometry_ref.size_bytes + 1}
        ),
    )
    assert findings(capture)["COMPLETENESS"] == ("VIOLATED", ("UNTRUSTED_GEOMETRY",))


@pytest.mark.parametrize(
    "field,reason",
    [
        ("speed_upper_rad_s", "ACTUAL_SPEED_EXCLUDES_DISPLACEMENT"),
        ("acceleration_upper_rad_s2", "ACTUAL_ACCELERATION_EXCLUDES_ENDPOINTS"),
    ],
)
def test_continuous_bound_cannot_claim_zero_motion_derivative_with_raw_q_change(field, reason):
    capture = bundle()
    mutate_actual(capture, lambda row: row["joints"][0].update({field: 0.0}))
    result = findings(capture)
    assert result["COMPLETENESS"][0] == "SATISFIED"
    assert result["SAFETY"] == ("VIOLATED", (reason,))


@pytest.mark.parametrize("bound,passes", [(0.0625, True), (math.nextafter(0.0625, 0.0), False)])
def test_displacement_speed_si_boundary_is_exact(bound, passes):
    capture = bundle()
    mutate_actual(capture, lambda row: row["joints"][0].update(speed_upper_rad_s=bound))
    result = findings(capture)["SAFETY"]
    assert result[0] == ("SATISFIED" if passes else "VIOLATED")
    if not passes:
        assert result[1] == ("ACTUAL_SPEED_EXCLUDES_DISPLACEMENT",)


def test_gripper_opening_cannot_jump_with_zero_speed():
    capture = bundle()
    e = load_episode(capture.original, capture.binding, capture.artifacts)
    frames = (e.frames[0], channel(e.frames[1], "opening", [0.045]), e.frames[2])
    rows = tuple(
        row.model_copy(
            update={
                "interval": row.interval.model_copy(
                    update={
                        "gripper": row.interval.gripper.model_copy(
                            update={"maximum_opening_m": 0.05}
                        )
                    }
                )
            }
        )
        for row in e.safety
    )
    with pytest.raises(EvidenceProblem, match="ACTUAL_GRIPPER_SPEED_EXCLUDES_DISPLACEMENT"):
        safety_findings(replace(e, frames=frames, safety=rows))


@pytest.mark.parametrize("dtype,values", [("float64", (1000.0,) * 3), ("bool", (True,) * 3)])
def test_raw_contact_cannot_disappear_from_interval_inventory(dtype, values):
    capture = bundle(contact_dtype=dtype, contact_values=values)
    result = findings(capture)
    assert result["COMPLETENESS"][0] == "SATISFIED"
    assert result["SAFETY"] == ("VIOLATED", ("ACTUAL_CONTACT_OMITS_RAW_STATE",))


def contact_interval(row, *, force=2.0, phase="APPROACH", reverse=False):
    row["contacts"] = [
        dict(
            first_body="payload" if reverse else "arm",
            second_body="arm" if reverse else "payload",
            phase=phase,
            force_upper_n=force,
            penetration_upper_m=0.0,
        )
    ]


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("dtype,values", [("float64", (2.0,) * 3), ("bool", (True,) * 3)])
def test_raw_contact_and_declared_pair_bound_agree(dtype, values, reverse):
    capture = bundle(contact_dtype=dtype, contact_values=values, declared_contact=True)
    mutate_actual(capture, lambda row: contact_interval(row, reverse=reverse))
    assert findings(capture)["SAFETY"][0] == "SATISFIED"


@pytest.mark.parametrize(
    "force,phase,reason",
    [
        (1.0, "APPROACH", "ACTUAL_CONTACT_FORCE_EXCLUDES_RAW"),
        (2.0, "GRASP", "ACTUAL_CONTACT_RAW_PHASE"),
    ],
)
def test_contact_force_and_phase_cannot_contradict_raw_pair(force, phase, reason):
    capture = bundle(contact_dtype="float64", contact_values=(2.0,) * 3, declared_contact=True)
    mutate_actual(capture, lambda row: contact_interval(row, force=force, phase=phase))
    assert findings(capture)["SAFETY"] == ("VIOLATED", (reason,))


def test_contact_mapping_is_required_for_every_contact_channel():
    capture = bundle(contact_dtype="float64", contact_values=(1000.0,) * 3)
    configure(
        capture,
        safety_channels=capture.binding.configuration.safety_channels.model_copy(
            update={"contacts": ()}
        ),
    )
    assert findings(capture)["SAFETY"] == ("VIOLATED", ("ACTUAL_CONTACT_MAPPING_INVENTORY",))


def test_contact_mapping_rejects_duplicate_or_reversed_pairs():
    channels = bundle(contact_dtype="float64").binding.configuration.safety_channels
    reverse = ContactChannelV1(field="another", first_body="payload", second_body="arm")
    with pytest.raises(ValidationError, match="unordered pair"):
        type(channels).model_validate(
            {**channels.model_dump(mode="python"), "contacts": (*channels.contacts, reverse)}
        )
    with pytest.raises(ValidationError, match="fields must be unique"):
        type(channels).model_validate(
            {
                **channels.model_dump(mode="python"),
                "contacts": (*channels.contacts, channels.contacts[0]),
            }
        )


def test_negative_raw_contact_force_is_not_zero_contact():
    capture = bundle(contact_dtype="float64", contact_values=(-1.0,) * 3)
    assert findings(capture)["SAFETY"] == ("VIOLATED", ("ACTUAL_CONTACT_NEGATIVE_FORCE",))


@pytest.mark.parametrize("requirement", ["fresh", "perception"])
def test_replay_cannot_satisfy_comparison_while_required_recovery_fails(requirement):
    source, replay = bundle(), bundle(replay=True)
    for capture in (source, replay):
        if requirement == "fresh":
            configure(capture, requires_fresh_episode_recovery=True)
        else:
            configure(
                capture,
                recovery=PerceptionRecoveryV1(
                    perceived_position="tool_position",
                    ground_truth_position="tool_position",
                    declared_start_ns=0,
                    declared_end_ns=1_000_000_000,
                    minimum_fault_error_m=0.1,
                    maximum_recovered_error_m=0.01,
                ),
            )
    individual = findings(source)["RECOVERY"]
    result = compare_replay(
        source.original,
        source.binding,
        source.artifacts,
        replay.original,
        replay.binding,
        replay.artifacts,
        trusted_safety_keys=source.keys,
    )
    assert result.status == individual[0]
    assert result.reasons == individual[1]
    assert result.status != "SATISFIED"


@pytest.mark.parametrize("values", [(2.0, 0.0, 0.0), (0.0, 0.0, 2.0)])
def test_contact_presence_checks_both_closed_interval_endpoints(values):
    capture = bundle(contact_dtype="float64", contact_values=values, declared_contact=True)
    assert findings(capture)["SAFETY"] == ("VIOLATED", ("ACTUAL_CONTACT_OMITS_RAW_STATE",))


@pytest.mark.parametrize(
    "change,reason",
    [
        ("vector", "ACTUAL_CONTACT_CHANNEL_SCHEMA"),
        ("stale", "TASK_CHANNEL_TIME_SKEW"),
        ("frame", "TASK_CHANNEL_UNIT_FRAME"),
        ("missing", "ACTUAL_CONTACT_CHANNEL_INVENTORY"),
        ("pair", "ACTUAL_CONTACT_OMITS_RAW_STATE"),
    ],
)
def test_contact_mapping_requires_scalar_current_pair_evidence(change, reason):
    capture = bundle(contact_dtype="float64", contact_values=(2.0,) * 3, declared_contact=True)
    mutate_actual(capture, contact_interval)
    e = load_episode(capture.original, capture.binding, capture.artifacts)
    if change == "vector":
        fields = [
            field.model_copy(update={"shape": [2]}) if field.field == "pair_contact" else field
            for field in e.observation_spec.required
        ]
        e = replace(e, observation_spec=replace_contract(e.observation_spec, required=fields))
    elif change == "pair":
        mapping = ContactChannelV1(field="pair_contact", first_body="arm", second_body="finger")
        e = replace(
            e,
            configuration=e.configuration.model_copy(
                update={
                    "safety_channels": e.configuration.safety_channels.model_copy(
                        update={"contacts": (mapping,)}
                    )
                }
            ),
        )
    else:
        first = e.frames[0]
        channels = dict(first.channels)
        value = channels.pop("pair_contact")
        if change == "stale":
            channels["pair_contact"] = replace(value, sim_time_ns=1)
        elif change == "frame":
            channels["pair_contact"] = replace(
                value, field=value.field.model_copy(update={"frame_id": "another_frame"})
            )
        e = replace(e, frames=(replace(first, channels=channels), *e.frames[1:]))
    with pytest.raises(EvidenceProblem, match=reason):
        safety_findings(e)
