"""Pure staged identity witnesses; no physics, approval or preflight result."""

import pytest
from test_v05_ur5e_fail_closed import bare_adapter
from v05_sdk_fixtures import Harness

from accretion.contracts.canonical import canonical_json
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.sdk import InitializationPins


def staged():
    harness = Harness()
    adapter = bare_adapter(harness)
    adapter._episode = None
    adapter._initialization = canonical_json(InitializationPins.from_episode(harness.episode))
    return harness, adapter


def test_initial_capture_has_no_preflight_or_approval_placeholder() -> None:
    harness, adapter = staged()
    assert adapter.observe() == harness.initial
    assert set(adapter.initialization.model_dump()) == {
        "episode_id",
        "lease",
        "seed",
        "randomization_sample_hash",
    }
    with pytest.raises(RoboticsError) as exc:
        _ = adapter.episode
    assert exc.value.code is Code.APPROVAL_REQUIRED
    with pytest.raises(RoboticsError):
        adapter.reset(
            seed=harness.episode.seed,
            randomization_sample_hash=harness.episode.randomization_sample_hash,
        )
    assert not adapter._reset_used


def test_full_episode_can_be_bound_once_without_resetting_state() -> None:
    harness, adapter = staged()
    before = adapter.observe()
    adapter.bind_episode(harness.episode)
    assert adapter.episode == harness.episode and adapter.observe() == before
    with pytest.raises(RoboticsError):
        adapter.bind_episode(harness.episode)
    assert (
        adapter.reset(
            seed=harness.episode.seed,
            randomization_sample_hash=harness.episode.randomization_sample_hash,
        )
        == before
    )


@pytest.mark.parametrize("change", ["seed", "lease", "hash", "time", "sequence", "closed"])
def test_changed_or_nonpristine_initialization_refuses_binding(change: str) -> None:
    harness, adapter = staged()
    episode = harness.episode.model_copy(deep=True)
    if change == "seed":
        episode.seed += 1
    elif change == "lease":
        episode.lease.generation += 1
    elif change == "hash":
        episode.randomization_sample_hash = "0" * 64
    elif change == "time":
        adapter._data.time = 0.001
    elif change == "sequence":
        adapter._sequence = 1
    else:
        adapter._closed = True
    with pytest.raises(RoboticsError):
        adapter.bind_episode(episode)
    assert adapter._episode is None
