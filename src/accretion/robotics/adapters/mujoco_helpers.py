"""Small simulator-independent numerical and artifact helpers.

The desired polynomial is the same piecewise quintic Hermite curve certified by
the safety evaluator. These evaluations are numerical commands, not conservative
bounds on a physical rollout.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import math
from typing import Any, Protocol

from accretion.contracts import EvidenceClass
from accretion.contracts.robotics.values import (
    ContentAddressedArtifactRef,
    PreparedTrajectory,
    SimulationArtifactRef,
)
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.observations import ArtifactReader, verified_chunks

PACKAGES = {"mujoco": "3.10.0", "numpy": "2.3.3", "Pillow": "11.3.0"}
INTERPOLATION = "PIECEWISE_QUINTIC_HERMITE_V1"
STEP_NS = 2_000_000
MAX_COMMAND_BYTES = 1_048_576


class AdapterArtifacts(ArtifactReader, Protocol):
    """The enclosing host authorizes scope; ArtifactStore implements this API."""

    def put(
        self,
        data: bytes,
        *,
        media_type: str,
        retention_class: Any,
        evidence_class: EvidenceClass,
    ) -> ContentAddressedArtifactRef: ...


def publish(
    artifacts: AdapterArtifacts, data: bytes, *, media_type: str = "application/json"
) -> SimulationArtifactRef:
    ref = artifacts.put(
        data, media_type=media_type, retention_class="RUN", evidence_class=EvidenceClass.SIMULATION
    )
    return SimulationArtifactRef.model_validate(ref.model_dump(mode="python"))


def read_verified(
    artifacts: ArtifactReader, ref: ContentAddressedArtifactRef, *, limit: int = MAX_COMMAND_BYTES
) -> bytes:
    return b"".join(verified_chunks(artifacts, ref, max_bytes=limit))


def runtime() -> tuple[Any, Any]:
    """Fail before importing an absent or different optional runtime."""
    try:
        if any(importlib.metadata.version(name) != pin for name, pin in PACKAGES.items()):
            raise RoboticsError(Code.SIMULATION_UNAVAILABLE)
        return importlib.import_module("mujoco"), importlib.import_module("numpy")
    except (ImportError, importlib.metadata.PackageNotFoundError) as error:
        raise RoboticsError(Code.SIMULATION_UNAVAILABLE) from error


def _bezier(coefficients: list[float], u: float) -> float:
    values = coefficients[:]
    while len(values) > 1:
        values = [(1 - u) * a + u * b for a, b in zip(values, values[1:], strict=False)]
    return values[0]


def quintic(
    trajectory: PreparedTrajectory, time_ns: int
) -> tuple[list[float], list[float], list[float]]:
    """Evaluate q/v/a from all six Hermite endpoint constraints, in SI units."""
    if type(time_ns) is not int or not 0 <= time_ns <= trajectory.points[-1].time_from_start_ns:
        raise RoboticsError(Code.INVALID_REQUEST)
    # Preserve exact endpoint values (including nonzero velocity/acceleration).
    for point in trajectory.points:
        if point.time_from_start_ns == time_ns:
            return point.positions_rad[:], point.velocities_rad_s[:], point.accelerations_rad_s2[:]
    start, end = next(
        (a, b)
        for a, b in zip(trajectory.points, trajectory.points[1:], strict=False)
        if a.time_from_start_ns < time_ns < b.time_from_start_ns
    )
    h = (end.time_from_start_ns - start.time_from_start_ns) / 1e9
    u = (time_ns - start.time_from_start_ns) / (end.time_from_start_ns - start.time_from_start_ns)
    q, v, a = [], [], []
    for index in range(len(trajectory.joint_names)):
        q0, q1 = start.positions_rad[index], end.positions_rad[index]
        v0, v1 = start.velocities_rad_s[index], end.velocities_rad_s[index]
        a0, a1 = start.accelerations_rad_s2[index], end.accelerations_rad_s2[index]
        coefficients = [
            q0,
            q0 + v0 * h / 5,
            q0 + 2 * v0 * h / 5 + a0 * h * h / 20,
            q1 - 2 * v1 * h / 5 + a1 * h * h / 20,
            q1 - v1 * h / 5,
            q1,
        ]
        velocity = [5 * (y - x) / h for x, y in zip(coefficients, coefficients[1:], strict=False)]
        acceleration = [4 * (y - x) / h for x, y in zip(velocity, velocity[1:], strict=False)]
        q.append(_bezier(coefficients, u))
        v.append(_bezier(velocity, u))
        a.append(_bezier(acceleration, u))
    if not all(math.isfinite(value) for value in [*q, *v, *a]):
        raise RoboticsError(Code.INVALID_REQUEST)
    return q, v, a
