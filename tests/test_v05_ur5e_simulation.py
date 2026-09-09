"""Opt-in real development dynamics; never conformance or safety-admission proof.

Run only via the bounded development runner with the explicit pinned asset root.
The test signer bypasses production admission deliberately and cannot be reused
as a host-preview producer. No study, grasp success or benchmark is claimed.
"""

from __future__ import annotations

import json
import os
import struct
from datetime import UTC, datetime
from pathlib import Path

import pytest

if os.environ.get("ACCRETION_RUN_SIMULATION_TESTS") != "1":
    pytest.skip("explicit optional simulation development window required", allow_module_level=True)

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from v05_sdk_fixtures import editable, replace_contract

from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics import (
    ActionIntent,
    EmbodimentDescriptor,
    PreparedCommand,
    SafetyDecisionPayload,
    SafetyDecisionReceipt,
    TrustedSafetyKey,
    sign_safety_payload,
)
from accretion.contracts.robotics.values import EpisodePins, PreparedTrajectory, TrajectoryPoint
from accretion.robotics.adapters.mujoco_helpers import publish, read_verified
from accretion.robotics.adapters.ur5e import (
    JOINT_NAMES,
    UR5eAdapter,
    build_profile,
    randomization_sample,
)
from accretion.robotics.artifacts import ArtifactStore
from accretion.robotics.errors import RoboticsError


class Rig:
    def __init__(self, path: Path) -> None:
        self.artifacts = ArtifactStore(path, max_store_bytes=128 * 1024 * 1024)
        desc = EmbodimentDescriptor.model_validate(editable("EmbodimentDescriptor"))
        self.profile = build_profile(
            Path(os.environ["ACCRETION_MENAGERIE_ROOT"]),
            self.artifacts,
            workspace_id=desc.workspace_id,
            project_id=desc.project_id,
            principal=desc.created_by,
            created_at=datetime(2026, 9, 9, tzinfo=UTC),
            descriptor_id=desc.contract_id,
            observation_spec_id=editable("ObservationSpec")["contract_id"],
        )
        pins = editable("SimulationEpisodeApproval")["pins"]
        pins.update(
            seed=707, randomization_sample_hash=content_hash(randomization_sample(707), exclude=())
        )
        self.episode = EpisodePins.model_validate(pins)
        self.key = Ed25519PrivateKey.generate()
        self.principal = editable("SafetyDecisionReceipt")["created_by"]["principal_id"]
        self.keys = {
            "development-only": TrustedSafetyKey(
                principal_id=self.principal, public_key=self.key.public_key().public_bytes_raw()
            )
        }
        self.adapter = UR5eAdapter(
            self.profile, episode=self.episode, artifacts=self.artifacts, trusted_keys=self.keys
        )
        self.action = 0

    def field(self, name: str, adapter: UR5eAdapter | None = None) -> list[float]:
        adapter = adapter or self.adapter
        sample = next(s for s in adapter.observe().samples if s.field.field == name)
        raw = read_verified(self.artifacts, sample.artifact)
        return list(struct.unpack("<" + "d" * (len(raw) // 8), raw))

    def intent(self, *, move: float = 0.015, adapter: UR5eAdapter | None = None) -> ActionIntent:
        adapter = adapter or self.adapter
        start = self.field("joint_position", adapter)
        end = start[:]
        end[0] += move
        path = PreparedTrajectory(
            joint_names=list(JOINT_NAMES),
            points=[
                TrajectoryPoint(
                    time_from_start_ns=0,
                    positions_rad=start,
                    velocities_rad_s=self.field("joint_velocity", adapter),
                    accelerations_rad_s2=self.field("joint_acceleration", adapter),
                    effort_upper_bounds_nm=[150.0, 150.0, 150.0, 28.0, 28.0, 28.0],
                ),
                TrajectoryPoint(
                    time_from_start_ns=1_000_000_000,
                    positions_rad=end,
                    velocities_rad_s=[0.0] * 6,
                    accelerations_rad_s2=[0.0] * 6,
                    effort_upper_bounds_nm=[150.0, 150.0, 150.0, 28.0, 28.0, 28.0],
                ),
            ],
        )
        values = editable("ActionIntent")
        values.update(
            episode_id=adapter.episode.episode_id,
            sequence=self.action,
            state=adapter.observe().state_binding(),
            semantic_action="JOINT_TRAJECTORY",
            expires_at_sim_time_ns=adapter.observe().sim_time_ns + 10_000_000_000,
            target=dict(
                kind="JOINT_TRAJECTORY",
                frame_id="base",
                joint_names=list(JOINT_NAMES),
                trajectory_ref=publish(self.artifacts, canonical_json(path)),
            ),
        )
        return ActionIntent.model_validate(values)

    def receipt(
        self, command: PreparedCommand, *, decision: str = "ALLOW"
    ) -> SafetyDecisionReceipt:
        values = editable("SafetyDecisionReceipt")
        body = values["decision"]
        before = dict(actions=self.action, cumulative_joint_motion_rad=0.0, elapsed_sim_seconds=0.0)
        after = dict(
            actions=self.action + 1, cumulative_joint_motion_rad=100.0, elapsed_sim_seconds=10.0
        )
        body.update(
            episode_id=command.episode_id,
            state=command.state,
            lease=command.lease,
            prepared_command_hash=command.content_hash,
            action_intent_hash=command.action_intent_hash,
            safety_envelope_hash=self.episode.safety_envelope_hash,
            decision=decision,
            budget_before=before,
            budget_after=after if decision == "ALLOW" else before,
            expires_at_sim_time_ns=command.expires_at_sim_time_ns,
        )
        payload = SafetyDecisionPayload.model_validate(body)
        values.update(
            decision=payload,
            signature=sign_safety_payload(payload, key_id="development-only", private_key=self.key),
        )
        return SafetyDecisionReceipt.model_validate(values)


@pytest.fixture
def rig(tmp_path: Path):
    value = Rig(tmp_path / "artifacts")
    yield value
    value.adapter.terminate()
    value.artifacts.close()


def test_real_prepare_observe_snapshot_denial_do_not_advance(rig: Rig) -> None:
    before = rig.adapter.snapshot()
    assert (
        rig.adapter.reset(
            seed=707, randomization_sample_hash=rig.episode.randomization_sample_hash
        ).sim_time_ns
        == 0
    )
    intent = rig.intent()
    command = rig.adapter.prepare(intent)
    for _ in range(3):
        assert rig.adapter.observe().state_binding() == before.state
    assert rig.adapter.snapshot() == before
    with pytest.raises(RoboticsError):
        rig.adapter.execute(command, rig.receipt(command, decision="DENY"))
    assert rig.adapter.snapshot() == before
    assert rig.adapter.last_trace() is None
    assert 0.08 < rig.field("gripper_opening")[0] < 0.09


def test_real_tracking_contacts_and_fresh_scope_replay(rig: Rig) -> None:
    initial = rig.field("joint_position")
    command = rig.adapter.prepare(rig.intent())
    result = rig.adapter.execute(command, rig.receipt(command))
    rig.action += 1
    assert result.sim_time_ns == 1_000_000_000
    assert abs(rig.field("joint_position")[0] - initial[0]) > 0.005
    trace_ref = rig.adapter.last_trace()
    assert trace_ref is not None
    trace = json.loads(read_verified(rig.artifacts, trace_ref, limit=16 * 1024 * 1024))
    assert len(trace["rows"]) == 500
    assert max(row["tracking_error_rad"] for row in trace["rows"]) < 0.15
    assert any(
        any(
            "development_cube" in c["first_body"] or "development_cube" in c["second_body"]
            for c in row["contacts"]
        )
        for row in trace["rows"]
    )
    assert 0.02 < rig.field("cube_position")[2] < 0.03
    evidence = Path(os.environ["ACCRETION_DEVELOPMENT_EVIDENCE"])
    (evidence / "description.json").write_bytes(rig.profile.description_bytes)
    (evidence / "tracking-trace.json").write_bytes(canonical_json(trace))
    (evidence / "observation.json").write_bytes(canonical_json(result))
    rgb = next(sample for sample in result.samples if sample.field.field == "rgb")
    from PIL import Image

    Image.frombytes("RGB", (320, 240), read_verified(rig.artifacts, rgb.artifact)).save(
        evidence / "rgb.png"
    )
    snapshot = rig.adapter.snapshot()
    values = rig.episode.model_dump(mode="python")
    values.update(
        episode_id="sep_01ARZ3NDEKTSV4RRFFQ69G5FAA",
        lease=dict(lease_id="sle_01ARZ3NDEKTSV4RRFFQ69G5FAA", generation=1),
    )
    replay = UR5eAdapter(
        rig.profile,
        episode=EpisodePins.model_validate(values),
        artifacts=rig.artifacts,
        trusted_keys=rig.keys,
        replay_source_episode_id=rig.episode.episode_id,
    )
    try:
        rebound = replay.restore(snapshot)
        assert rebound.episode_id != result.episode_id
        assert rebound.samples == result.samples
        first = rig.adapter.prepare(rig.intent(move=-0.01))
        rig.action = 0
        second = replay.prepare(rig.intent(move=-0.01, adapter=replay))
        rig.adapter.execute(first, rig.receipt(first))
        replay.execute(second, rig.receipt(second))
        for name in ("joint_position", "joint_velocity", "cube_position", "gripper_opening"):
            assert rig.field(name, replay) == rig.field(name)
        with pytest.raises(RoboticsError):
            replay.restore(snapshot)
    finally:
        replay.terminate()


def test_prepare_rejects_wrong_joint_order_and_mutated_seal(rig: Rig) -> None:
    intent = rig.intent()
    bad = replace_contract(
        intent,
        target=dict(
            kind="JOINT_TRAJECTORY",
            frame_id="base",
            trajectory_ref=intent.target.trajectory_ref,
            joint_names=list(reversed(JOINT_NAMES)),
        ),
    )
    with pytest.raises(RoboticsError):
        rig.adapter.prepare(bad)
    intent.sequence = 9
    with pytest.raises(ValueError):
        rig.adapter.prepare(intent)
    assert rig.adapter.observe().sim_time_ns == 0


def test_real_si_gripper_and_bounded_end_effector_reach(rig: Rig) -> None:
    opening = rig.field("gripper_opening")[0]
    intent = rig.intent()
    grip = replace_contract(
        intent,
        semantic_action="SET_GRIPPER",
        target=dict(kind="SET_GRIPPER", opening_m=0.06, force_n=20.0),
        constraints=dict(speed_scale=1.0, acceleration_scale=1.0, planning_time_ms=500),
    )
    before = rig.adapter.snapshot()
    command = rig.adapter.prepare(grip)
    assert command.command_payload.opening_m == 0.06
    assert rig.adapter.snapshot() == before
    rig.adapter.execute(command, rig.receipt(command))
    rig.action += 1
    after = rig.field("gripper_opening")[0]
    assert 0.055 < after < 0.073
    assert opening - after > 0.01
    grip_trace = rig.adapter.last_trace()
    assert grip_trace is not None
    evidence = Path(os.environ["ACCRETION_DEVELOPMENT_EVIDENCE"])
    (evidence / "gripper-trace.json").write_bytes(
        read_verified(rig.artifacts, grip_trace, limit=16 * 1024 * 1024)
    )
    grip_snapshot = rig.adapter.snapshot()
    values = rig.episode.model_dump(mode="python")
    values.update(
        episode_id="sep_01ARZ3NDEKTSV4RRFFQ69G5FAC",
        lease=dict(lease_id="sle_01ARZ3NDEKTSV4RRFFQ69G5FAC", generation=1),
    )
    replay = UR5eAdapter(
        rig.profile,
        episode=EpisodePins.model_validate(values),
        artifacts=rig.artifacts,
        trusted_keys=rig.keys,
        replay_source_episode_id=rig.episode.episode_id,
    )
    try:
        assert replay.restore(grip_snapshot).samples == rig.adapter.observe().samples
    finally:
        replay.terminate()
    start = rig.field("tool_position")
    target = start[:]
    target[0] += 0.01
    pose = replace_contract(
        rig.intent(),
        semantic_action="MOVE_END_EFFECTOR",
        target=dict(
            kind="MOVE_END_EFFECTOR",
            frame_id="base",
            position_m=target,
            orientation_xyzw=rig.field("tool_orientation"),
        ),
    )
    before = rig.adapter.snapshot()
    prepared = rig.adapter.prepare(pose)
    assert rig.adapter.snapshot() == before
    rig.adapter.execute(prepared, rig.receipt(prepared))
    reached = rig.field("tool_position")
    assert sum((a - b) ** 2 for a, b in zip(target, reached, strict=True)) ** 0.5 < 0.002
    (evidence / "reach.json").write_bytes(
        canonical_json(
            dict(
                start_m=start,
                target_m=target,
                actual_m=reached,
                gripper_before_m=opening,
                gripper_after_m=after,
            )
        )
    )


def test_real_refused_duplicate_execute_and_bad_signature_do_not_step(rig: Rig) -> None:
    command = rig.adapter.prepare(rig.intent())
    receipt = rig.receipt(command)
    bad_key = Ed25519PrivateKey.generate()
    bad = replace_contract(
        receipt,
        signature=sign_safety_payload(
            receipt.decision, key_id="development-only", private_key=bad_key
        ),
    )
    before = rig.adapter.snapshot()
    with pytest.raises(ValueError):
        rig.adapter.execute(command, bad)
    assert rig.adapter.snapshot() == before
    rig.adapter.execute(command, receipt)
    after = rig.adapter.snapshot()
    with pytest.raises(RoboticsError):
        rig.adapter.execute(command, receipt)
    assert rig.adapter.snapshot() == after


def test_corrupted_snapshot_state_cannot_invent_observation(rig: Rig) -> None:
    from accretion.robotics.protocol import SnapshotReference

    snapshot = rig.adapter.snapshot()
    payload = json.loads(read_verified(rig.artifacts, snapshot.artifact))
    payload["integration_state"][1] += 0.1
    reference = publish(
        rig.artifacts, canonical_json(payload), media_type=snapshot.artifact.media_type
    )
    changed = SnapshotReference(
        source_episode_id=snapshot.source_episode_id,
        state=snapshot.state,
        dependency_closure_hash=snapshot.dependency_closure_hash,
        artifact=reference,
    )
    values = rig.episode.model_dump(mode="python")
    values.update(
        episode_id="sep_01ARZ3NDEKTSV4RRFFQ69G5FAB",
        lease=dict(lease_id="sle_01ARZ3NDEKTSV4RRFFQ69G5FAB", generation=1),
    )
    replay = UR5eAdapter(
        rig.profile,
        episode=EpisodePins.model_validate(values),
        artifacts=rig.artifacts,
        trusted_keys=rig.keys,
        replay_source_episode_id=rig.episode.episode_id,
    )
    try:
        with pytest.raises(RoboticsError):
            replay.restore(changed)
        with pytest.raises(RoboticsError):
            replay.observe()
        assert rig.adapter.snapshot() == snapshot
    finally:
        replay.terminate()


def test_evidence_write_failure_after_real_steps_poisons_worker(rig: Rig, monkeypatch) -> None:
    from accretion.robotics.errors import RoboticsErrorCode

    original_put = rig.artifacts.put

    def fail_trace(data, **kwargs):
        if b"DEVELOPMENT_SAMPLED_DYNAMICS_NOT_CONTINUOUS_PROOF" in data:
            raise RoboticsError(RoboticsErrorCode.ARTIFACT_UNAVAILABLE)
        return original_put(data, **kwargs)

    monkeypatch.setattr(rig.artifacts, "put", fail_trace)
    command = rig.adapter.prepare(rig.intent())
    receipt = rig.receipt(command)
    with pytest.raises(RoboticsError):
        rig.adapter.execute(command, receipt)
    elapsed = float(rig.adapter._data.time)
    assert elapsed == pytest.approx(1.0)
    with pytest.raises(RoboticsError):
        rig.adapter.observe()
    with pytest.raises(RoboticsError):
        rig.adapter.execute(command, receipt)
    assert float(rig.adapter._data.time) == elapsed
