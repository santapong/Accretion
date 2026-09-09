"""Injected solver boundaries prove expired host permits cannot continue commands."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from test_v05_ur5e_fail_closed import Artifacts, bare_adapter
from v05_sdk_fixtures import Harness, replace_contract

from accretion.contracts.canonical import canonical_json
from accretion.robotics.adapters.mujoco_helpers import publish, read_verified
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code


@pytest.mark.parametrize("deny_at", [1, 2])
def test_current_permit_denial_poisoned_at_effect_or_evidence_boundary(deny_at):
    harness = Harness(artifacts=Artifacts())
    _, prepared = harness.prepared()
    prepared = replace_contract(
        prepared,
        command_ref=publish(harness.artifacts, canonical_json(prepared.command_payload)),
    )
    adapter = bare_adapter(harness)
    adapter._prepared = canonical_json(prepared)
    receipt = harness.sign(
        prepared,
        budget_after=dict(actions=1, cumulative_joint_motion_rad=0.0, elapsed_sim_seconds=1.0),
    )
    guards, effects = [], []

    def check():
        guards.append("current_permit_check")
        if len(guards) == deny_at:
            raise RoboticsError(Code.LEASE_INVALID)

    def step(model, data):
        effects.append("injected_solver_step")
        data.time = 0.002

    adapter._execution_guard = check
    adapter._mj = SimpleNamespace(mj_step=step, mj_forward=lambda model, data: None)
    with pytest.raises(RoboticsError) as error:
        adapter.execute(prepared, receipt)
    assert error.value.code is Code.LEASE_INVALID
    assert len(guards) == deny_at and len(effects) == (0 if deny_at == 1 else 1)
    assert adapter._closed and adapter._prepared is None
    trace = json.loads(read_verified(harness.artifacts, adapter.last_trace()))
    assert trace["aborted"] is True
    assert trace["last_sim_time_ns"] == (0 if deny_at == 1 else 2_000_000)
    with pytest.raises(RoboticsError):
        adapter.execute(prepared, receipt)
    assert len(effects) == (0 if deny_at == 1 else 1)
