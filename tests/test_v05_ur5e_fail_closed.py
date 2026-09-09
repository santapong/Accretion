"""Pure injected-state failure witnesses; no simulator or dynamics is executed."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from v05_sdk_fixtures import Harness, replace_contract

from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics.values import EpisodePins
from accretion.robotics.adapters.mujoco_helpers import publish, read_verified
from accretion.robotics.adapters.ur5e import SNAPSHOT_MEDIA_TYPE, UR5eAdapter
from accretion.robotics.errors import RoboticsError
from accretion.robotics.protocol import SnapshotReference
from accretion.robotics.testing.fault_adapter import MemoryArtifacts


class Artifacts(MemoryArtifacts):
    def put(self, data, *, media_type, retention_class=None, evidence_class=None):
        return super().put(data, media_type=media_type)


class ArrayStub:
    float64 = "float64"

    @staticmethod
    def clip(value, minimum, maximum):
        return min(maximum, max(minimum, value))

    @staticmethod
    def asarray(value, *, dtype):
        return value[:]


def bare_adapter(harness):
    """Construct only the Python state machine, bypassing optional runtime init."""
    adapter = object.__new__(UR5eAdapter)
    adapter._episode = canonical_json(harness.episode)
    adapter._description = canonical_json(harness.description)
    adapter._data = SimpleNamespace(time=0.0, ctrl={0: 0.0})
    adapter._model = SimpleNamespace(actuator_forcerange={0: [-5.0, 5.0]})
    adapter._grip = 0
    adapter._grip_control = 0.0
    adapter._grip_force = 20.0
    adapter._arm_target = [0.0] * 6
    adapter._sequence = adapter._action_sequence = 0
    adapter._closed = adapter._restored = adapter._reset_used = False
    adapter._prepared = None
    adapter._trace = adapter._renderer = None
    adapter._replay_source = None
    adapter._np = ArrayStub()
    adapter._artifacts = harness.artifacts
    adapter._keys = harness.trusted_keys
    adapter._batch = canonical_json(harness.initial)
    adapter._opening = lambda: 0.04
    adapter._set_arm_target = lambda positions, velocities: None
    return adapter


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt, asyncio.CancelledError])
def test_interrupted_partial_action_poisoned_before_trace_is_published(failure):
    artifacts = Artifacts()
    harness = Harness(artifacts=artifacts)
    _, prepared = harness.prepared()
    prepared = replace_contract(
        prepared, command_ref=publish(artifacts, canonical_json(prepared.command_payload))
    )
    receipt = harness.sign(
        prepared,
        budget_after=dict(actions=1, cumulative_joint_motion_rad=0.0, elapsed_sim_seconds=1.0),
    )
    adapter = bare_adapter(harness)
    adapter._prepared = canonical_json(prepared)

    def interrupted_step(model, data):
        # Inject an observable mutation, not a physical simulation step.
        data.time = 0.002
        raise failure("injected interruption after state mutation")

    adapter._mj = SimpleNamespace(mj_step=interrupted_step)
    with pytest.raises(failure):
        adapter.execute(prepared, receipt)
    assert adapter._data.time == 0.002
    assert adapter._closed and adapter._prepared is None
    trace = adapter.last_trace()
    assert trace is not None
    raw = read_verified(artifacts, trace)
    assert b'"aborted":true' in raw
    assert b'"last_sim_time_ns":2000000' in raw
    with pytest.raises(RoboticsError):
        adapter.observe()
    with pytest.raises(RoboticsError):
        adapter.execute(prepared, receipt)


def restoration_case(failure, *, fail_at):
    artifacts = Artifacts()
    harness = Harness(artifacts=artifacts)
    adapter = bare_adapter(harness)
    source = harness.initial
    fresh = harness.episode.model_dump(mode="python")
    fresh.update(episode_id="sep_" + "0" * 26, lease=dict(lease_id="sle_" + "0" * 26, generation=1))
    episode = EpisodePins.model_validate(fresh)
    adapter._episode = canonical_json(episode)
    adapter._replay_source = source.episode_id
    initial = source.model_copy(update={"episode_id": episode.episode_id, "lease": episode.lease})
    adapter._batch = canonical_json(initial)
    calls = []

    def set_state(model, data, state, kind):
        calls.append("set_state")
        if fail_at == "set_state":
            raise failure("injected state loading interruption")

    def forward(model, data):
        calls.append("forward")
        if fail_at == "forward":
            raise failure("injected forward interruption")

    def capture():
        calls.append("capture")
        if fail_at == "capture":
            raise failure("injected observation capture interruption")
        return initial

    adapter._mj = SimpleNamespace(
        mjtState=SimpleNamespace(mjSTATE_INTEGRATION=1),
        mj_stateSize=lambda model, kind: 2,
        MjData=lambda model: SimpleNamespace(time=0.0),
        mj_setState=set_state,
        mj_forward=forward,
    )
    adapter._capture = capture
    closure = content_hash(harness.dependencies, exclude=())
    payload = dict(
        version="1.0.0",
        source_episode_id=source.episode_id,
        seed=episode.seed,
        randomization_sample_hash=episode.randomization_sample_hash,
        closure_hash=closure,
        state_kind=1,
        integration_state=[0.0, 0.0],
        batch=source,
        action_sequence=9,
        arm_target=[0.0] * 6,
        gripper_control=0.0,
        gripper_force_n=10.0,
        gripper_force_range=[-0.3, 0.3],
    )
    snapshot = SnapshotReference(
        source_episode_id=source.episode_id,
        state=source.state_binding(),
        dependency_closure_hash=closure,
        artifact=publish(artifacts, canonical_json(payload), media_type=SNAPSHOT_MEDIA_TYPE),
    )
    return adapter, snapshot, calls, initial


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt, asyncio.CancelledError])
@pytest.mark.parametrize("fail_at", ["set_state", "forward", "capture"])
def test_restore_failure_after_model_mutation_poisoned(failure, fail_at):
    adapter, snapshot, calls, _ = restoration_case(failure, fail_at=fail_at)
    with pytest.raises(failure):
        adapter.restore(snapshot)
    assert adapter._model.actuator_forcerange[0] == [-0.3, 0.3]
    assert fail_at in calls
    assert adapter._closed
    with pytest.raises(RoboticsError):
        adapter.observe()
    with pytest.raises(RoboticsError):
        adapter.restore(snapshot)


def test_successful_injected_restore_retains_fresh_action_namespace():
    adapter, snapshot, calls, initial = restoration_case(RuntimeError, fail_at=None)
    result = adapter.restore(snapshot)
    assert result == initial and not adapter._closed
    assert adapter._restored and adapter._action_sequence == 0
    assert calls == ["set_state", "forward", "set_state", "capture"]
