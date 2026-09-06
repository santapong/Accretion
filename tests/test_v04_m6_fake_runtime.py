"""``FakeRuntime.outcomes_by_model``: two configurations of one node behaving differently.

ADR-060 scores a shadow choice by branching the run — the shadow configuration in one fork,
the executed configuration in a sibling fork — so a test that wants a shadow arm to *lose*
must be able to make one runtime behave two ways. ``scripted_outcomes`` cannot express that:
it is a single queue and it hands out outcomes in submission order, so which arm gets the
failure depends on which fork happened to be submitted first.

The hook this file pins is the session's model. ``RuntimeExecutionRequest`` carries no
configuration hash, but ``RunManager`` sets ``SessionConfig.model`` to the selected
configuration's model id, so the model is the one thing that reaches the runtime and differs
between the two arms.

The first test is the null: a ``FakeRuntime`` built without ``outcomes_by_model`` emits the
same three events, in the same order, that it emitted before this field existed. The
byte-for-byte version of that claim across a whole graph run lives in
``tests/test_v04_seam_executing_provider.py``'s golden trace, which this PR also leaves green;
this one is the local, readable form of it.
"""

from __future__ import annotations

from pathlib import Path

from accretion.contracts import EventType, SessionConfig, TaskEnvelope
from accretion.ids import new_id
from accretion.runtimes.fake import FakeCallOutcome, FakeRuntime

BASELINE_SEQUENCE = [
    EventType.RUNTIME_CALL_STARTED,
    EventType.RUN_PROGRESS,
    EventType.RUNTIME_CALL_COMPLETED,
]
"""What a default ``FakeRuntime`` emitted for one call before ``outcomes_by_model`` existed."""


async def drive(
    runtime: FakeRuntime, workspace: Path, *, model: str | None
) -> list[EventType]:
    """Submit one call on a fresh session and return the ordered normalized event types."""

    run_id = new_id("run")
    session = await runtime.create_session(
        SessionConfig(run_id=run_id, workspace=workspace, model=model)
    )
    run = await runtime.submit(
        session,
        TaskEnvelope(
            task_id=new_id("task"),
            project_id=new_id("project"),
            objective="exercise a shadow fork",
        ),
    )
    return [event.normalized_type async for event in runtime.events(run)]


async def test_a_runtime_with_no_model_script_emits_exactly_what_it_emitted_before(
    tmp_path: Path,
) -> None:
    """The golden null. An empty ``outcomes_by_model`` must change nothing at all.

    Both spellings are checked — the field omitted and the field passed empty — because the
    constructor normalises them to the same dict and a future change that special-cased only
    one of them would leave half the callers on a different path.
    """

    assert await drive(FakeRuntime(), tmp_path, model=None) == BASELINE_SEQUENCE
    assert await drive(FakeRuntime(), tmp_path, model="fake-small") == BASELINE_SEQUENCE
    assert (
        await drive(FakeRuntime(outcomes_by_model={}), tmp_path, model="fake-small")
        == BASELINE_SEQUENCE
    )


async def test_two_sessions_on_different_models_draw_from_different_scripts(
    tmp_path: Path,
) -> None:
    """The whole point: the shadow arm can fail while the control arm succeeds.

    Submission order is deliberately shadow-then-control here and control-then-shadow in the
    second half, and the verdicts follow the model rather than the order. A queue keyed on
    anything but the session's model would pass one half and fail the other.
    """

    def runtime() -> FakeRuntime:
        return FakeRuntime(
            outcomes_by_model={
                "fake-small": [FakeCallOutcome(terminal=EventType.RUNTIME_CALL_FAILED)],
                "fake-large": [FakeCallOutcome(terminal=EventType.RUNTIME_CALL_COMPLETED)],
            }
        )

    first = runtime()
    assert (await drive(first, tmp_path, model="fake-small"))[-1] is (
        EventType.RUNTIME_CALL_FAILED
    )
    assert (await drive(first, tmp_path, model="fake-large"))[-1] is (
        EventType.RUNTIME_CALL_COMPLETED
    )

    second = runtime()
    assert (await drive(second, tmp_path, model="fake-large"))[-1] is (
        EventType.RUNTIME_CALL_COMPLETED
    )
    assert (await drive(second, tmp_path, model="fake-small"))[-1] is (
        EventType.RUNTIME_CALL_FAILED
    )


async def test_a_model_queue_is_consumed_in_order_and_then_falls_through(
    tmp_path: Path,
) -> None:
    """A per-model script is a queue, and running it out returns the runtime to its default.

    Falling through rather than repeating the last entry is what lets a test script the first
    two trials of an arm and leave the rest at the baseline, which is how a flaky shadow
    configuration is expressed without writing one entry per trial.
    """

    runtime = FakeRuntime(
        outcomes_by_model={
            "fake-small": [
                FakeCallOutcome(terminal=EventType.RUNTIME_CALL_FAILED),
                FakeCallOutcome(terminal=EventType.RUNTIME_CALL_CANCELLED),
            ]
        }
    )
    terminals = [
        (await drive(runtime, tmp_path, model="fake-small"))[-1] for _ in range(3)
    ]
    assert terminals == [
        EventType.RUNTIME_CALL_FAILED,
        EventType.RUNTIME_CALL_CANCELLED,
        EventType.RUNTIME_CALL_COMPLETED,
    ]
    assert not runtime.outcomes_by_model["fake-small"]


async def test_the_flat_script_still_wins_for_a_model_with_no_queue_of_its_own(
    tmp_path: Path,
) -> None:
    """``scripted_outcomes`` keeps its meaning for every model the map does not name.

    The precedence is model queue, then flat script, then fallback. Every existing test in the
    repository relies on the second rung, so a model queue that shadowed the flat script for
    *all* models would break them silently rather than loudly.
    """

    runtime = FakeRuntime(
        scripted_outcomes=[FakeCallOutcome(terminal=EventType.RUNTIME_CALL_CANCELLED)],
        outcomes_by_model={
            "fake-small": [FakeCallOutcome(terminal=EventType.RUNTIME_CALL_FAILED)]
        },
    )
    assert (await drive(runtime, tmp_path, model="fake-large"))[-1] is (
        EventType.RUNTIME_CALL_CANCELLED
    )
    assert (await drive(runtime, tmp_path, model="fake-small"))[-1] is (
        EventType.RUNTIME_CALL_FAILED
    )


async def test_a_session_that_names_no_model_never_reaches_the_model_scripts(
    tmp_path: Path,
) -> None:
    """``SessionConfig.model`` is optional, and ``None`` is not a key into the map."""

    runtime = FakeRuntime(
        outcomes_by_model={"fake-small": [FakeCallOutcome(EventType.RUNTIME_CALL_FAILED)]}
    )
    assert await drive(runtime, tmp_path, model=None) == BASELINE_SEQUENCE
    assert len(runtime.outcomes_by_model["fake-small"]) == 1
    assert runtime.session_models == {}


async def test_the_runtime_remembers_which_model_each_session_was_created_on(
    tmp_path: Path,
) -> None:
    """The map that makes the pairing possible, asserted directly rather than through a call."""

    runtime = FakeRuntime()
    first = await runtime.create_session(
        SessionConfig(run_id=new_id("run"), workspace=tmp_path, model="fake-small")
    )
    second = await runtime.create_session(
        SessionConfig(run_id=new_id("run"), workspace=tmp_path, model="fake-large")
    )
    assert runtime.session_models == {
        first.session_id: "fake-small",
        second.session_id: "fake-large",
    }
