"""Pure numerical/import witnesses; no optional simulator installation needed."""

import subprocess
import sys

import pytest

from accretion.contracts.robotics.values import PreparedTrajectory, TrajectoryPoint
from accretion.robotics.adapters.mujoco_helpers import quintic
from accretion.robotics.adapters.ur5e import randomization_sample
from accretion.robotics.errors import RoboticsError


def test_import_does_not_load_simulator_or_array_runtime() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import accretion.robotics.adapters.ur5e; "
                "assert not {'mujoco','numpy','PIL','OpenGL'}.intersection(sys.modules)"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("time_ns", [0, 100_000_000, 500_000_000, 900_000_000, 1_000_000_000])
def test_quintic_reconstructs_independent_fifth_order_polynomial(time_ns: int) -> None:
    # Analytic polynomial exercises nonzero q/v/a at both endpoints, not the
    # implementation's coefficient formula or only the common zero-velocity case.
    def q(t: float) -> float:
        return 1 + 2 * t + 3 * t * t + 4 * t**3 + 5 * t**4 + 6 * t**5

    def v(t: float) -> float:
        return 2 + 6 * t + 12 * t * t + 20 * t**3 + 30 * t**4

    def a(t: float) -> float:
        return 6 + 24 * t + 60 * t * t + 120 * t**3

    path = PreparedTrajectory(
        joint_names=["joint"],
        points=[
            TrajectoryPoint(
                time_from_start_ns=int(t * 1e9),
                positions_rad=[q(t)],
                velocities_rad_s=[v(t)],
                accelerations_rad_s2=[a(t)],
                effort_upper_bounds_nm=[1.0],
            )
            for t in (0.0, 1.0)
        ],
    )
    actual = quintic(path, time_ns)
    t = time_ns / 1e9
    assert [value[0] for value in actual] == pytest.approx([q(t), v(t), a(t)], abs=1e-11)


@pytest.mark.parametrize("seed", [-1, True, 2**63, 1.5])
def test_randomization_rejects_ambiguous_seed(seed: int) -> None:
    with pytest.raises(RoboticsError):
        randomization_sample(seed)


def test_randomization_is_repeatable_and_bounded() -> None:
    assert randomization_sample(707) == randomization_sample(707)
    assert 0.34 <= randomization_sample(707)["cube_x_m"] <= 0.36


def test_missing_pinned_model_is_refused_before_optional_runtime(tmp_path, monkeypatch) -> None:
    from datetime import UTC, datetime

    from v05_sdk_fixtures import editable

    from accretion.contracts import PrincipalRef
    from accretion.robotics.adapters import ur5e

    def forbidden_runtime():
        raise AssertionError("source provenance must be checked before loading a runtime")

    monkeypatch.setattr(ur5e, "runtime", forbidden_runtime)
    fields = editable("EmbodimentDescriptor")
    with pytest.raises(RoboticsError):
        ur5e.build_profile(
            tmp_path,
            None,
            workspace_id=fields["workspace_id"],
            project_id=fields["project_id"],
            principal=PrincipalRef.model_validate(fields["created_by"]),
            created_at=datetime(2026, 9, 9, tzinfo=UTC),
            descriptor_id=fields["contract_id"],
            observation_spec_id=editable("ObservationSpec")["contract_id"],
        )
