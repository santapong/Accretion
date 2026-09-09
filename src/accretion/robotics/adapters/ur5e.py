"""Pinned UR5e + Robotiq 2F85 MuJoCo adapter, for an isolated local host.

No simulator is imported at module import time. No network, viewer or physical
endpoint exists here. The host must enforce durable authority before invoking
this object. Dense sampled telemetry is development evidence, not continuous
conservative physics proof or activation-ready conformance.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import platform
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from accretion.contracts import PrincipalRef, StrictModel
from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics import (
    ActionIntent,
    CanonicalWriterEnvelope,
    EmbodimentDescriptor,
    ObservationSpec,
    PreparedCommand,
    SafetyDecisionReceipt,
    TrustedSafetyKey,
    verify_safety_signature,
)
from accretion.contracts.robotics.models import RoboticsContract
from accretion.contracts.robotics.values import (
    DependencyClosure,
    Digest,
    EpisodeId,
    EpisodePins,
    Finite,
    ObservationField,
    PoseTarget,
    PreparedGripperCommand,
    PreparedTrajectory,
    SequenceNumber,
    SimulationArtifactRef,
    TrajectoryPoint,
    TrajectoryTarget,
)
from accretion.ids import new_id
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.observations import (
    TENSOR_MEDIA_TYPE,
    ObservationBatch,
    ObservationSample,
    ObservationValidator,
)
from accretion.robotics.protocol import (
    AdapterDescription,
    DescriptorRecord,
    ObservationSpecRecord,
    SnapshotReference,
    bounded_json,
)
from accretion.robotics.sdk import InitializationPins

from .mujoco_helpers import (
    INTERPOLATION,
    PACKAGES,
    STEP_NS,
    AdapterArtifacts,
    publish,
    quintic,
    read_verified,
    runtime,
)

MODEL_COMMIT = "e4049d0a3bfd58d2a3081614e6777d4007e3f86a"
JOINT_NAMES = (
    "shoulder_pan_joint",
    "shoulder_lift_joint",
    "elbow_joint",
    "wrist_1_joint",
    "wrist_2_joint",
    "wrist_3_joint",
)
ACTUATOR_NAMES = ("shoulder_pan", "shoulder_lift", "elbow", "wrist_1", "wrist_2", "wrist_3")
HOME = (-1.5708, -1.5708, 1.5708, -1.5708, -1.5708, 0.0)
WIDTH, HEIGHT = 320, 240
MAX_STEPS = 5_000
MAX_OPENING_M = 0.0854
MAX_FORCE_N = 20.0
SNAPSHOT_MEDIA_TYPE = "application/x.accretion.mujoco-state+json"
CONTROLLER = {
    "interpolation": INTERPOLATION,
    "command_sampling": "STEP_END_ZERO_ORDER_HOLD",
    "physics_step_ns": STEP_NS,
    "arm": "pinned position servos plus gravity-bias and desired-velocity feedforward",
    "maximum_tracking_error_rad": 0.15,
    "gripper": "pad-face aperture PI feedback; original tendon position actuator",
    "gripper_velocity_m_s": 0.02,
    "gripper_acceleration_m_s2": 0.1,
    "gripper_control_gain_per_m_s": 8_000.0,
    "gripper_nominal_torque_arm_m": 0.03,
    "force_semantics": "requested aperture force; sampled actual pad-force cutoff, not proof",
    "maximum_action_steps": MAX_STEPS,
}


def _checked[C: RoboticsContract](value: C) -> C:
    return CanonicalWriterEnvelope.from_contract(value).for_execution(type(value))


def randomization_sample(seed: int) -> dict[str, float | int]:
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise RoboticsError(Code.INVALID_REQUEST)
    return {"seed": seed, "cube_x_m": random.Random(seed).uniform(0.34, 0.36)}


class _SnapshotData(StrictModel):
    version: Literal["1.0.0"]
    source_episode_id: EpisodeId
    seed: SequenceNumber
    randomization_sample_hash: Digest
    closure_hash: Digest
    state_kind: int = Field(strict=True, ge=1)
    integration_state: list[Finite] = Field(min_length=1, max_length=10_000)
    batch: ObservationBatch
    action_sequence: SequenceNumber
    arm_target: list[Finite] = Field(min_length=6, max_length=6)
    gripper_control: float = Field(strict=True, ge=0, le=255, allow_inf_nan=False)
    gripper_force_n: float = Field(strict=True, gt=0, le=MAX_FORCE_N, allow_inf_nan=False)
    gripper_force_range: list[Finite] = Field(min_length=2, max_length=2)


@dataclass(frozen=True)
class UR5eProfile:
    """Prepared host-local model and exact immutable descriptor/spec/closure bytes.

    Build once before registry admission. A fresh process reconstructs it using
    the same explicit record IDs/time; it must match the admitted description.
    The private model is copied by every adapter, so gripper controls cannot leak.
    """

    description_bytes: bytes
    _model: Any

    @property
    def description(self) -> AdapterDescription:
        return AdapterDescription.model_validate_json(self.description_bytes)


def build_profile(
    models_root: Path,
    artifacts: AdapterArtifacts,
    *,
    workspace_id: str,
    project_id: str,
    principal: PrincipalRef,
    created_at: datetime,
    descriptor_id: str,
    observation_spec_id: str,
    simulator_image_digest: str | None = None,
    host_compatibility_profile_hash: str | None = None,
) -> UR5eProfile:
    """Verify every pinned selected source file before compiling the local overlay.

    Mesh bytes are loaded from the verified read-only checkout. The host must
    mount that checkout read-only to prevent modification between check and load.
    No downloader or inferred/latest package/model pin is accepted.
    """
    manifest_bytes = Path(__file__).with_name("ur5e-model-files.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    for relative, expected in manifest.items():
        source = models_root / relative
        if (
            source.is_symlink()
            or not source.is_file()
            or hashlib.sha256(source.read_bytes()).hexdigest() != expected
        ):
            raise RoboticsError(Code.ARTIFACT_INVALID)
    mujoco, np = runtime()
    spec = mujoco.MjSpec.from_file(str(models_root / "universal_robots_ur5e/scene.xml"))
    spec.option.cone = mujoco.mjtCone.mjCONE_ELLIPTIC
    spec.option.impratio = 10.0
    spec.option.timestep = STEP_NS / 1e9
    child = mujoco.MjSpec.from_file(str(models_root / "robotiq_2f85/2f85.xml"))
    spec.attach(child, prefix="2f85/", site=spec.site("attachment_site"))
    for key in list(spec.keys):
        spec.delete(key)
    cube = spec.worldbody.add_body(name="development_cube", pos=[0.35, -0.45, 0.2])
    cube.add_freejoint(name="development_cube_free")
    cube.add_geom(
        name="development_cube_geom",
        type=mujoco.mjtGeom.mjGEOM_BOX,
        size=[0.025] * 3,
        mass=0.05,
        rgba=[0.9, 0.3, 0.12, 1.0],
        contype=1,
        conaffinity=1,
        friction=[0.8, 0.005, 0.0001],
    )
    position = np.array([1.4, -1.5, 1.05])
    forward = np.array([0.1, -0.15, 0.3]) - position
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0, 0, 1])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    spec.worldbody.add_camera(
        name="development_camera", pos=position, xyaxes=np.concatenate([right, up]), fovy=48.0
    )
    spec.visual.global_.offwidth = WIDTH
    spec.visual.global_.offheight = HEIGHT
    model = spec.compile()
    xml = spec.to_xml().encode()
    model_ref = publish(
        artifacts,
        canonical_json(
            {
                "source_commit": MODEL_COMMIT,
                "files": manifest,
                "overlay_xml": publish(artifacts, xml, media_type="application/xml").model_dump(
                    mode="json"
                ),
                "licenses": {
                    "universal_robots_ur5e": "BSD-3-Clause",
                    "robotiq_2f85": "BSD-2-Clause",
                },
            }
        ),
    )
    limits = []
    for joint_name, actuator_name in zip(JOINT_NAMES, ACTUATOR_NAMES, strict=True):
        joint, actuator = model.joint(joint_name), model.actuator(actuator_name)
        if int(joint.type[0]) != int(mujoco.mjtJoint.mjJNT_HINGE):
            raise RoboticsError(Code.PREFLIGHT_FAILED)
        limits.append(
            dict(
                joint_name=joint_name,
                lower_rad=float(joint.range[0]),
                upper_rad=float(joint.range[1]),
                velocity_rad_s=1.0,
                acceleration_rad_s2=4.0,
                effort_nm=float(max(abs(actuator.forcerange))),
            )
        )
    limits_ref = publish(artifacts, canonical_json(limits))
    transform = publish(
        artifacts,
        canonical_json(
            {
                "base_frame": "base",
                "tool_site": "2f85/pinch",
                "model_digest": model_ref.digest,
                "camera": "development_camera",
                "camera_xyaxes": [*right.tolist(), *up.tolist()],
            }
        ),
    )
    workspace = publish(
        artifacts,
        canonical_json(
            {
                "minimum_m": [-1.2, -1.2, -0.01],
                "maximum_m": [1.2, 1.2, 1.5],
                "frame": "base",
                "purpose": "development scene extent; admission separately required",
            }
        ),
    )
    fields = [
        ("joint_position", "joint_state", "JOINT_STATE", "float64", [6], "rad", "base"),
        ("joint_velocity", "joint_state", "JOINT_STATE", "float64", [6], "rad/s", "base"),
        ("joint_acceleration", "joint_state", "JOINT_STATE", "float64", [6], "rad/s2", "base"),
        ("joint_effort", "joint_state", "JOINT_STATE", "float64", [6], "N.m", "base"),
        ("gripper_opening", "gripper", "GRIPPER_OPENING", "float64", [1], "m", "2f85/pinch"),
        ("tool_position", "tool_pose", "POSE", "float64", [3], "m", "base"),
        ("tool_orientation", "tool_pose", "POSE", "float64", [4], "1", "base"),
        ("cube_position", "cube_pose", "POSE", "float64", [3], "m", "base"),
        ("contact_force", "contacts", "CONTACT", "float64", [1], "N", "base"),
        ("rgb", "rgb", "RGB", "uint8", [HEIGHT, WIDTH, 3], "pixel", "development_camera"),
        ("depth", "depth", "DEPTH", "float32", [HEIGHT, WIDTH], "m", "development_camera"),
    ]
    observations = [
        ObservationField(
            field=f, sensor_id=s, modality=m, dtype=d, shape=shape, unit=u, frame_id=frame
        )
        for f, s, m, d, shape, u, frame in fields
    ]
    sensors = {
        f.sensor_id: dict(sensor_id=f.sensor_id, modality=f.modality, frame_id=f.frame_id)
        for f in observations
    }
    header = dict(
        workspace_id=workspace_id,
        project_id=project_id,
        created_by=principal,
        created_at=created_at,
        labels={"profile": "UR5E_ROBOTIQ_DEVELOPMENT_V1"},
    )
    descriptor = EmbodimentDescriptor(
        **header,
        contract_id=descriptor_id,
        embodiment_id="robot.sim.ur5e-robotiq2f85.v1",
        descriptor_version="1.0.0",
        control_interfaces=["JOINT_TRAJECTORY"],
        kinematics=dict(
            joint_names=list(JOINT_NAMES),
            base_frame="base",
            tool_frame="2f85/pinch",
            joint_limits_ref=limits_ref,
            transform_provenance_ref=transform,
        ),
        sensors=list(sensors.values()),
        end_effectors=[
            dict(
                joint_names=[
                    model.joint(i).name
                    for i in range(model.njnt)
                    if model.joint(i).name.startswith("2f85/")
                ],
                joint_types=["REVOLUTE"] * 8,
                minimum_opening_m=0.0,
                maximum_opening_m=MAX_OPENING_M,
                maximum_force_n=MAX_FORCE_N,
            )
        ],
        workspace_ref=workspace,
        robot_model_ref=model_ref,
    )
    observation_spec = ObservationSpec(
        **header,
        contract_id=observation_spec_id,
        required=observations,
        time_alignment=dict(clock="SIMULATION", maximum_skew_ms=0.0),
    )
    source_ref = publish(
        artifacts,
        canonical_json(
            {
                name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in ("ur5e.py", "mujoco_helpers.py", "ur5e-model-files.json")
            }
        ),
    )
    pins = {
        "adapter_artifact_digest": source_ref.digest,
        "simulator_image_digest": publish(
            artifacts,
            canonical_json(
                {
                    "kind": "LOCAL_OPTIONAL_ENVIRONMENT_NOT_OCI",
                    "packages": PACKAGES,
                    "python": platform.python_version(),
                    "machine": platform.machine(),
                }
            ),
        ).digest,
        "world_digest": hashlib.sha256(xml).hexdigest(),
        "robot_model_digest": model_ref.digest,
        "controller_digest": publish(
            artifacts,
            canonical_json({"parameters": CONTROLLER, "implementation_digest": source_ref.digest}),
        ).digest,
        "observation_spec_hash": observation_spec.content_hash,
        "action_intent_schema_hash": publish(
            artifacts, canonical_json(ActionIntent.model_json_schema())
        ).digest,
        "prepared_command_schema_hash": publish(
            artifacts, canonical_json(PreparedCommand.model_json_schema())
        ).digest,
        "tolerance_profile_hash": publish(
            artifacts,
            canonical_json(
                {
                    "tracking_rad": 0.15,
                    "reset": "same environment exact integration state",
                    "conformance": "PENDING",
                }
            ),
        ).digest,
        "environment_profile_hash": publish(
            artifacts,
            canonical_json(
                {"seed": "explicit", "randomization": "seeded cube x in [0.34,0.36]; reset only"}
            ),
        ).digest,
        "physics_parameters_hash": publish(
            artifacts,
            canonical_json(
                {
                    "step_ns": STEP_NS,
                    "cone": "ELLIPTIC",
                    "impratio": 10.0,
                    "gravity_m_s2": model.opt.gravity.tolist(),
                }
            ),
        ).digest,
        "rendering_parameters_hash": publish(
            artifacts,
            canonical_json(
                {
                    "width": WIDTH,
                    "height": HEIGHT,
                    "camera": "development_camera",
                    "software_renderer_required": True,
                }
            ),
        ).digest,
        "host_compatibility_profile_hash": publish(
            artifacts,
            canonical_json(
                {
                    "platform": platform.platform(),
                    "python": platform.python_version(),
                    "packages": PACKAGES,
                    "isolation": "host must supply; no activation claim",
                }
            ),
        ).digest,
    }
    if simulator_image_digest is not None:
        pins["simulator_image_digest"] = simulator_image_digest
    if host_compatibility_profile_hash is not None:
        pins["host_compatibility_profile_hash"] = host_compatibility_profile_hash
    description = AdapterDescription(
        descriptor=DescriptorRecord.from_contract(descriptor),
        observation_spec=ObservationSpecRecord.from_contract(observation_spec),
        dependencies=DependencyClosure.model_validate(pins),
    )
    return UR5eProfile(canonical_json(description), model)


class UR5eAdapter:
    """One episode, one worker, serialized calls through AdapterSession.

    Local signature checks are defense in depth. They do not replace the host's
    atomic issuance/lease/policy/budget check. Any failure after a physics step
    poisons this instance: terminate it and inspect evidence, never retry.
    """

    _execution_guard: Callable[[], None] | None = None

    def __init__(
        self,
        profile: UR5eProfile,
        *,
        episode: EpisodePins | InitializationPins,
        artifacts: AdapterArtifacts,
        trusted_keys: Mapping[str, TrustedSafetyKey],
        replay_source_episode_id: str | None = None,
        execution_guard: Callable[[], None] | None = None,
    ) -> None:
        import copy

        self._mj, self._np = runtime()
        self._description = profile.description_bytes
        self._model = copy.copy(profile._model)
        self._data = self._mj.MjData(self._model)
        self._initialization = canonical_json(InitializationPins.from_episode(episode))
        self._episode = (
            canonical_json(EpisodePins.model_validate(episode.model_dump(mode="python")))
            if isinstance(episode, EpisodePins)
            else None
        )
        self._artifacts = artifacts
        self._keys = dict(trusted_keys)
        self._replay_source = replay_source_episode_id
        self._execution_guard = execution_guard
        self._sequence = 0
        self._action_sequence = 0
        self._closed = False
        self._reset_used = False
        self._restored = False
        self._prepared: bytes | None = None
        self._trace: SimulationArtifactRef | None = None
        self._renderer: Any = None
        self._q = [int(self._model.joint(name).qposadr[0]) for name in JOINT_NAMES]
        self._v = [int(self._model.joint(name).dofadr[0]) for name in JOINT_NAMES]
        self._actuators = [self._model.actuator(name).id for name in ACTUATOR_NAMES]
        self._grip = self._model.actuator("2f85/fingers_actuator").id
        self._tool = self._model.site("2f85/pinch").id
        self._cube_q = int(self._model.joint("development_cube_free").qposadr[0])
        self._arm_target = list(HOME)
        self._grip_control = 0.0
        self._grip_force = MAX_FORCE_N
        try:
            self._initialize()
        except Exception:
            self.terminate()
            raise

    @property
    def episode(self) -> EpisodePins:
        if self._episode is None:
            raise RoboticsError(Code.APPROVAL_REQUIRED)
        return EpisodePins.model_validate_json(self._episode)

    @property
    def initialization(self) -> InitializationPins:
        if self._episode is not None:
            return InitializationPins.from_episode(self.episode)
        return InitializationPins.model_validate_json(self._initialization)

    def bind_episode(self, episode: EpisodePins) -> None:
        self._live()
        episode = EpisodePins.model_validate_json(canonical_json(episode))
        if (
            self._episode is not None
            or InitializationPins.from_episode(episode) != self.initialization
            or self._sequence != 0
            or self._action_sequence != 0
            or self._reset_used
            or self._restored
            or self._data.time != 0
            or self.observe().sim_time_ns != 0
        ):
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        # Exact full pins arrive only after host-recorded preflight and approval.
        # The SDK live guard still owns every operational admission.
        self._episode = canonical_json(episode)

    def describe(self) -> AdapterDescription:
        return AdapterDescription.model_validate_json(self._description)

    def _live(self) -> None:
        if self._closed:
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)

    def _initialize(self) -> None:
        sample = randomization_sample(self.initialization.seed)
        if content_hash(sample, exclude=()) != self.initialization.randomization_sample_hash:
            raise RoboticsError(Code.PREFLIGHT_FAILED)
        self._mj.mj_resetData(self._model, self._data)
        self._data.qpos[self._q] = HOME
        self._data.ctrl[self._actuators] = HOME
        self._data.qpos[self._cube_q] = sample["cube_x_m"]
        self._data.ctrl[self._grip] = 0.0
        self._mj.mj_forward(self._model, self._data)
        self._set_arm_target(list(HOME), [0.0] * 6)
        self._mj.mj_forward(self._model, self._data)
        self._batch = canonical_json(self._capture())

    def observe(self) -> ObservationBatch:
        self._live()
        # Cached exact observations: no time, sequence, renderer or solver mutation.
        return ObservationBatch.model_validate_json(self._batch)

    def reset(self, *, seed: int, randomization_sample_hash: str) -> ObservationBatch:
        self._live()
        if (
            type(seed) is not int
            or self._reset_used
            or self._sequence
            or self._restored
            or (
                seed != self.episode.seed
                or randomization_sample_hash != self.episode.randomization_sample_hash
            )
        ):
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        self._reset_used = True
        self._prepared = None
        # Construction already materialized this exact seeded initial state.
        return self.observe()

    def _set_arm_target(self, positions: list[float], velocities: list[float]) -> None:
        self._arm_target = positions[:]
        for index, actuator in enumerate(self._actuators):
            gain = self._model.actuator_gainprm[actuator, 0]
            damping = -self._model.actuator_biasprm[actuator, 2]
            self._data.ctrl[actuator] = (
                positions[index]
                + (self._data.qfrc_bias[self._v[index]] + damping * velocities[index]) / gain
            )

    def _opening(self) -> float:
        # Signed separation of the actual inner faces, not a 0..255 conversion.
        left = self._model.geom("2f85/left_pad1").id
        right = self._model.geom("2f85/right_pad1").id
        direction = self._data.geom_xpos[right] - self._data.geom_xpos[left]
        distance = float(self._np.linalg.norm(direction))
        if distance <= 1e-12:
            return 0.0
        direction /= distance
        support = sum(
            float(
                self._np.dot(
                    self._np.abs(self._data.geom_xmat[index].reshape(3, 3).T @ direction),
                    self._model.geom_size[index],
                )
            )
            for index in (left, right)
        )
        return max(0.0, distance - support)

    def _contacts(self) -> list[dict[str, Any]]:
        rows = []
        if self._data.ncon > 64:
            raise RoboticsError(Code.RESOURCE_CAP_EXHAUSTED)
        for index in range(self._data.ncon):
            contact = self._data.contact[index]
            force = self._np.zeros(6)
            self._mj.mj_contactForce(self._model, self._data, index, force)
            rows.append(
                dict(
                    first_geom=self._model.geom(contact.geom1).name or str(contact.geom1),
                    second_geom=self._model.geom(contact.geom2).name or str(contact.geom2),
                    first_body=self._model.body(int(self._model.geom_bodyid[contact.geom1])).name,
                    second_body=self._model.body(int(self._model.geom_bodyid[contact.geom2])).name,
                    force_n=float(self._np.linalg.norm(force[:3])),
                    penetration_m=max(0.0, -float(contact.dist)),
                )
            )
        return rows

    def _capture(self) -> ObservationBatch:
        np, mj, data = self._np, self._mj, self._data
        if self._renderer is None:
            required_environment = {
                "MUJOCO_GL": "egl",
                "PYOPENGL_PLATFORM": "egl",
                "LIBGL_ALWAYS_SOFTWARE": "1",
                "MESA_LOADER_DRIVER_OVERRIDE": "llvmpipe",
                "EGL_PLATFORM": "surfaceless",
            }
            if any(os.environ.get(key) != value for key, value in required_environment.items()):
                raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
            self._renderer = mj.Renderer(self._model, height=HEIGHT, width=WIDTH)
            # The bounded development profile explicitly disallows accelerated GL.
            GL = importlib.import_module("OpenGL.GL")

            if b"llvmpipe" not in GL.glGetString(GL.GL_RENDERER).lower():
                raise RoboticsError(Code.ISOLATION_UNAVAILABLE)
        self._renderer.disable_depth_rendering()
        self._renderer.update_scene(data, camera="development_camera")
        rgb = self._renderer.render().copy()
        self._renderer.enable_depth_rendering()
        self._renderer.update_scene(data, camera="development_camera")
        depth = self._renderer.render().copy()
        self._renderer.disable_depth_rendering()
        quaternion = np.zeros(4)
        mj.mju_mat2Quat(quaternion, data.site_xmat[self._tool])
        values = {
            "joint_position": data.qpos[self._q],
            "joint_velocity": data.qvel[self._v],
            "joint_acceleration": data.qacc[self._v],
            "joint_effort": data.qfrc_actuator[self._v],
            "gripper_opening": [self._opening()],
            "tool_position": data.site_xpos[self._tool],
            "tool_orientation": quaternion[[1, 2, 3, 0]],
            "cube_position": data.qpos[self._cube_q : self._cube_q + 3],
            "contact_force": [max((row["force_n"] for row in self._contacts()), default=0.0)],
            "rgb": rgb,
            "depth": depth,
        }
        spec = self.describe().observation_spec.for_execution(ObservationSpec)
        descriptor = self.describe().descriptor.for_execution(EmbodimentDescriptor)
        sim_time_ns = round(float(data.time) * 1e9)
        samples = []
        for field in spec.required:
            array = np.asarray(values[field.field], dtype=np.dtype(field.dtype).newbyteorder("<"))
            if list(array.shape) != field.shape:
                raise RoboticsError(Code.OBSERVATION_INVALID)
            artifact = publish(
                self._artifacts, array.tobytes(order="C"), media_type=TENSOR_MEDIA_TYPE
            )
            samples.append(
                ObservationSample(
                    field=field, sequence=self._sequence, sim_time_ns=sim_time_ns, artifact=artifact
                )
            )
        batch = ObservationBatch(
            episode_id=self.initialization.episode_id,
            lease=self.initialization.lease,
            sequence=self._sequence,
            sim_time_ns=sim_time_ns,
            frame_transform_digest=descriptor.kinematics.transform_provenance_ref.digest,
            samples=tuple(samples),
        )
        ObservationValidator().validate(
            spec,
            descriptor,
            batch,
            self._artifacts,
            episode_id=self.initialization.episode_id,
            lease=self.initialization.lease,
        )
        return batch

    def _trajectory(self, target: PoseTarget, planning_time_ms: int) -> PreparedTrajectory:
        """Bounded damped least-squares IK on a separate non-stepped data clone."""
        if target.frame_id != "base":
            raise RoboticsError(Code.INVALID_REQUEST)
        mj, np = self._mj, self._np
        clone = mj.MjData(self._model)
        mj.mj_copyData(clone, self._model, self._data)
        target_matrix = np.zeros(9)
        quat = np.array([target.orientation_xyzw[3], *target.orientation_xyzw[:3]])
        mj.mju_quat2Mat(target_matrix, quat)
        desired = target_matrix.reshape(3, 3)
        deadline = time.monotonic() + min(planning_time_ms / 1000, 1.0)
        converged = False
        for _ in range(150):
            if time.monotonic() > deadline:
                break
            mj.mj_forward(self._model, clone)
            position_error = np.asarray(target.position_m) - clone.site_xpos[self._tool]
            current = clone.site_xmat[self._tool].reshape(3, 3)
            rotation_error = sum(np.cross(current[:, i], desired[:, i]) for i in range(3)) / 2
            # The matrix trace also rejects the 180-degree cross-product zero.
            angle = math.acos(float(np.clip((np.trace(desired @ current.T) - 1) / 2, -1, 1)))
            if np.linalg.norm(position_error) < 0.0005 and angle < 0.005:
                converged = True
                break
            jacp, jacr = np.zeros((3, self._model.nv)), np.zeros((3, self._model.nv))
            mj.mj_jacSite(self._model, clone, jacp, jacr, self._tool)
            jac = np.vstack([jacp[:, self._v], jacr[:, self._v]])
            error = np.concatenate([position_error, rotation_error])
            change = jac.T @ np.linalg.solve(jac @ jac.T + 1e-4 * np.eye(6), error)
            change *= min(1.0, 0.05 / max(float(np.linalg.norm(change)), 1e-12))
            for index, joint_name in enumerate(JOINT_NAMES):
                lo, hi = self._model.joint(joint_name).range
                clone.qpos[self._q[index]] = np.clip(
                    clone.qpos[self._q[index]] + change[index], lo + 0.01, hi - 0.01
                )
        if not converged:
            raise RoboticsError(Code.PREFLIGHT_FAILED)
        start = self._data.qpos[self._q].tolist()
        end = clone.qpos[self._q].tolist()
        duration = max(1.0, 4.0 * max(abs(a - b) for a, b in zip(start, end, strict=True)))
        duration_ns = math.ceil(duration * 1e9 / STEP_NS) * STEP_NS
        effort = [float(max(abs(self._model.actuator(name).forcerange))) for name in ACTUATOR_NAMES]
        return PreparedTrajectory(
            joint_names=list(JOINT_NAMES),
            points=[
                TrajectoryPoint(
                    time_from_start_ns=0,
                    positions_rad=start,
                    velocities_rad_s=self._data.qvel[self._v].tolist(),
                    accelerations_rad_s2=self._data.qacc[self._v].tolist(),
                    effort_upper_bounds_nm=effort,
                ),
                TrajectoryPoint(
                    time_from_start_ns=duration_ns,
                    positions_rad=end,
                    velocities_rad_s=[0.0] * 6,
                    accelerations_rad_s2=[0.0] * 6,
                    effort_upper_bounds_nm=effort,
                ),
            ],
        )

    def prepare(self, intent: ActionIntent) -> PreparedCommand:
        self._live()
        intent = _checked(intent)
        descriptor = self.describe().descriptor.for_execution(EmbodimentDescriptor)
        if (
            intent.workspace_id,
            intent.project_id,
            intent.episode_id,
            intent.sequence,
            intent.state,
        ) != (
            descriptor.workspace_id,
            descriptor.project_id,
            self.episode.episode_id,
            self._action_sequence,
            self.observe().state_binding(),
        ):
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        payload: PreparedTrajectory | PreparedGripperCommand
        if isinstance(intent.target, TrajectoryTarget):
            if intent.target.joint_names != list(JOINT_NAMES) or intent.target.frame_id != "base":
                raise RoboticsError(Code.INVALID_REQUEST)
            raw = read_verified(self._artifacts, intent.target.trajectory_ref)
            payload = PreparedTrajectory.model_validate_json(raw)
            if canonical_json(payload) != raw:
                raise RoboticsError(Code.ARTIFACT_INVALID)
        elif isinstance(intent.target, PoseTarget):
            payload = self._trajectory(intent.target, intent.constraints.planning_time_ms)
        else:
            if (
                not 0 <= intent.target.opening_m <= MAX_OPENING_M
                or intent.target.force_n > MAX_FORCE_N
            ):
                raise RoboticsError(Code.INVALID_REQUEST)
            payload = PreparedGripperCommand(
                opening_m=intent.target.opening_m,
                velocity_m_s=0.02 * intent.constraints.speed_scale,
                acceleration_m_s2=0.1 * intent.constraints.acceleration_scale,
                force_limit_n=intent.target.force_n,
            )
        if isinstance(payload, PreparedTrajectory):
            first = payload.points[0]
            if payload.joint_names != list(JOINT_NAMES) or (
                first.positions_rad,
                first.velocities_rad_s,
                first.accelerations_rad_s2,
            ) != (
                self._data.qpos[self._q].tolist(),
                self._data.qvel[self._v].tolist(),
                self._data.qacc[self._v].tolist(),
            ):
                raise RoboticsError(Code.INVALID_REQUEST)
            if (
                any(point.time_from_start_ns % STEP_NS for point in payload.points)
                or payload.points[-1].time_from_start_ns > MAX_STEPS * STEP_NS
            ):
                raise RoboticsError(Code.RESOURCE_CAP_EXHAUSTED)
        deps = self.describe().dependencies
        prepared = PreparedCommand(
            contract_id=new_id("prepared_command"),
            created_at=datetime.now(UTC),
            created_by=descriptor.created_by,
            workspace_id=intent.workspace_id,
            project_id=intent.project_id,
            episode_id=intent.episode_id,
            action_intent_hash=intent.content_hash,
            sequence=intent.sequence,
            state=intent.state,
            lease=self.episode.lease,
            controller_mode="JOINT_TRAJECTORY",
            command_ref=publish(self._artifacts, canonical_json(payload)),
            command_payload=payload,
            command_schema_hash=deps.prepared_command_schema_hash,
            controller_digest=deps.controller_digest,
            adapter_artifact_digest=deps.adapter_artifact_digest,
            expires_at_sim_time_ns=intent.expires_at_sim_time_ns,
        )
        self._prepared = canonical_json(prepared)
        return prepared

    def execute(self, prepared: PreparedCommand, safety: SafetyDecisionReceipt) -> ObservationBatch:
        self._live()
        prepared, safety = _checked(prepared), _checked(safety)
        decision = safety.decision
        verify_safety_signature(safety, trusted_keys=self._keys)
        if (
            self._prepared != canonical_json(prepared)
            or decision.decision != "ALLOW"
            or (
                decision.workspace_id,
                decision.project_id,
                decision.prepared_command_hash,
                decision.action_intent_hash,
                decision.episode_id,
                decision.state,
                decision.lease,
                decision.safety_envelope_hash,
            )
            != (
                prepared.workspace_id,
                prepared.project_id,
                prepared.content_hash,
                prepared.action_intent_hash,
                self.episode.episode_id,
                self.observe().state_binding(),
                self.episode.lease,
                self.episode.safety_envelope_hash,
            )
        ):
            raise RoboticsError(Code.SAFETY_DENIED)
        if read_verified(self._artifacts, prepared.command_ref) != canonical_json(
            prepared.command_payload
        ):
            raise RoboticsError(Code.ARTIFACT_INVALID)
        command = prepared.command_payload
        opening_start = self._opening()
        if isinstance(command, PreparedTrajectory):
            duration_ns = command.points[-1].time_from_start_ns
        else:
            distance = abs(command.opening_m - opening_start)
            # Rest-to-rest quintic aperture reference: maxima 1.875 and 5.774.
            duration = max(
                0.5,
                1.875 * distance / command.velocity_m_s,
                math.sqrt(5.774 * distance / command.acceleration_m_s2),
            )
            duration_ns = math.ceil(duration * 1e9 / STEP_NS) * STEP_NS
        end_ns = self.observe().sim_time_ns + duration_ns
        if (
            duration_ns > MAX_STEPS * STEP_NS
            or end_ns > min(prepared.expires_at_sim_time_ns, decision.expires_at_sim_time_ns)
            or duration_ns / 1e9
            > (
                decision.budget_after.elapsed_sim_seconds
                - decision.budget_before.elapsed_sim_seconds
                + 1e-12
            )
        ):
            raise RoboticsError(Code.SAFETY_DENIED)
        self._prepared = None  # consume before entering the solver; no uncertain retry
        rows = []
        trace_bytes = 0
        try:
            for elapsed in range(STEP_NS, duration_ns + 1, STEP_NS):
                if self._execution_guard is not None:
                    self._execution_guard()
                if isinstance(command, PreparedTrajectory):
                    desired, velocity, _ = quintic(command, elapsed)
                    self._set_arm_target(desired, velocity)
                else:
                    u = elapsed / duration_ns
                    desired_opening = opening_start + (command.opening_m - opening_start) * (
                        10 * u**3 - 15 * u**4 + 6 * u**5
                    )
                    self._grip_control = float(
                        self._np.clip(
                            self._grip_control
                            + 8_000 * (self._opening() - desired_opening) * STEP_NS / 1e9,
                            0,
                            255,
                        )
                    )
                    self._grip_force = command.force_limit_n
                    self._data.ctrl[self._grip] = self._grip_control
                    torque = min(5.0, self._grip_force * 0.03)
                    self._model.actuator_forcerange[self._grip] = [-torque, torque]
                    desired = self._arm_target[:]
                    self._set_arm_target(desired, [0.0] * 6)
                self._mj.mj_step(self._model, self._data)
                if self._execution_guard is not None:
                    self._execution_guard()
                self._mj.mj_forward(self._model, self._data)
                contacts = self._contacts()
                tracking = float(self._np.max(self._np.abs(self._data.qpos[self._q] - desired)))
                row = dict(
                    sim_time_ns=round(float(self._data.time) * 1e9),
                    desired_q_rad=desired,
                    q_rad=self._data.qpos[self._q].tolist(),
                    v_rad_s=self._data.qvel[self._v].tolist(),
                    a_rad_s2=self._data.qacc[self._v].tolist(),
                    effort_nm=self._data.qfrc_actuator[self._v].tolist(),
                    opening_m=self._opening(),
                    contacts=contacts,
                    tracking_error_rad=tracking,
                    cube_position_m=self._data.qpos[self._cube_q : self._cube_q + 3].tolist(),
                    tool_position_m=self._data.site_xpos[self._tool].tolist(),
                )
                trace_bytes += len(canonical_json(row))
                if trace_bytes > 8 * 1024 * 1024:
                    raise RoboticsError(Code.RESOURCE_CAP_EXHAUSTED)
                rows.append(row)
                if (
                    not self._np.isfinite(self._data.qpos).all()
                    or not self._np.isfinite(self._data.qvel).all()
                    or tracking > 0.15
                ):
                    raise RoboticsError(Code.SAFETY_DENIED)
                if (
                    sum(
                        row["force_n"]
                        for row in contacts
                        if "pad" in row["first_geom"] or "pad" in row["second_geom"]
                    )
                    > self._grip_force
                ):
                    raise RoboticsError(Code.SAFETY_DENIED)
            self._sequence += 1
            self._action_sequence += 1
            if self._execution_guard is not None:
                self._execution_guard()
            self._batch = canonical_json(self._capture())
            if self._execution_guard is not None:
                self._execution_guard()
        except BaseException:
            # Cancellation/interrupts can arrive after solver mutation too. Burn
            # this worker before publishing the aborted trace or propagating.
            self._closed = True
            raise
        finally:
            try:
                self._trace = publish(
                    self._artifacts,
                    canonical_json(
                        {
                            "evidence": "DEVELOPMENT_SAMPLED_DYNAMICS_NOT_CONTINUOUS_PROOF",
                            "episode_id": self.episode.episode_id,
                            "prepared_command_hash": prepared.content_hash,
                            "safety_receipt_hash": safety.content_hash,
                            "rows": rows,
                            "aborted": self._closed,
                            "last_sim_time_ns": round(float(self._data.time) * 1e9),
                        }
                    ),
                )
            except BaseException:
                # Physics may have completed, but its evidence acknowledgement
                # did not. This worker must never accept another command.
                self._closed = True
                raise
        return self.observe()

    def last_trace(self) -> SimulationArtifactRef | None:
        """Immutable artifact with each physics-step sample, including an aborted run."""
        return None if self._trace is None else self._trace.model_copy(deep=True)

    def snapshot(self) -> SnapshotReference:
        self._live()
        kind = self._mj.mjtState.mjSTATE_INTEGRATION
        state = self._np.zeros(self._mj.mj_stateSize(self._model, kind))
        self._mj.mj_getState(self._model, self._data, state, kind)
        closure = content_hash(self.describe().dependencies, exclude=())
        payload = dict(
            version="1.0.0",
            source_episode_id=self.episode.episode_id,
            seed=self.episode.seed,
            randomization_sample_hash=self.episode.randomization_sample_hash,
            closure_hash=closure,
            state_kind=int(kind),
            integration_state=state.tolist(),
            batch=json.loads(self._batch),
            action_sequence=self._action_sequence,
            arm_target=self._arm_target,
            gripper_control=self._grip_control,
            gripper_force_n=self._grip_force,
            gripper_force_range=self._model.actuator_forcerange[self._grip].tolist(),
        )
        return SnapshotReference(
            source_episode_id=self.episode.episode_id,
            state=self.observe().state_binding(),
            dependency_closure_hash=closure,
            artifact=publish(
                self._artifacts, canonical_json(payload), media_type=SNAPSHOT_MEDIA_TYPE
            ),
        )

    def restore(self, snapshot: SnapshotReference) -> ObservationBatch:
        self._live()
        if (
            self._restored
            or self._sequence
            or self._reset_used
            or self._prepared is not None
            or (
                self._replay_source != snapshot.source_episode_id
                or self.episode.episode_id == snapshot.source_episode_id
            )
        ):
            raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
        if snapshot.dependency_closure_hash != content_hash(
            self.describe().dependencies, exclude=()
        ):
            raise RoboticsError(Code.REPLAY_FAILED)
        raw = read_verified(self._artifacts, snapshot.artifact)
        try:
            payload = _SnapshotData.model_validate(bounded_json(raw))
        except ValueError as error:
            raise RoboticsError(Code.REPLAY_FAILED) from error
        if canonical_json(payload) != raw or (
            payload.source_episode_id,
            payload.closure_hash,
            payload.seed,
            payload.randomization_sample_hash,
        ) != (
            snapshot.source_episode_id,
            snapshot.dependency_closure_hash,
            self.episode.seed,
            self.episode.randomization_sample_hash,
        ):
            raise RoboticsError(Code.REPLAY_FAILED)
        original = payload.batch
        if (
            original.state_binding() != snapshot.state
            or original.episode_id != snapshot.source_episode_id
            or original.lease == self.episode.lease
        ):
            raise RoboticsError(Code.REPLAY_FAILED)
        kind = self._mj.mjtState.mjSTATE_INTEGRATION
        if (
            payload.state_kind != int(kind)
            or len(payload.integration_state) != self._mj.mj_stateSize(self._model, kind)
            or not -5.0
            <= payload.gripper_force_range[0]
            < 0
            < payload.gripper_force_range[1]
            <= 5.0
        ):
            raise RoboticsError(Code.REPLAY_FAILED)
        ObservationValidator().validate(
            self.describe().observation_spec.for_execution(ObservationSpec),
            self.describe().descriptor.for_execution(EmbodimentDescriptor),
            original,
            self._artifacts,
            episode_id=original.episode_id,
            lease=original.lease,
        )
        # Never load a snapshot directly over a live original episode. The entire
        # replacement is staged in a fresh data object and checked against all
        # original tensor bytes, including RGB/depth and derived dynamics.
        state = self._np.asarray(payload.integration_state, dtype=self._np.float64)
        candidate = self._mj.MjData(self._model)
        # Controller settings mutate the private model, even before the new
        # data becomes live. Include all staging and capture in the same poison
        # boundary; a failed or interrupted restore cannot be retried in place.
        try:
            self._model.actuator_forcerange[self._grip] = payload.gripper_force_range
            self._mj.mj_setState(self._model, candidate, state, kind)
            self._mj.mj_forward(self._model, candidate)
            self._mj.mj_setState(self._model, candidate, state, kind)
            self._data = candidate
            self._sequence = original.sequence
            self._arm_target = payload.arm_target[:]
            self._grip_control = payload.gripper_control
            self._grip_force = payload.gripper_force_n
            # Fresh replay commands have their own sequence namespace. The source
            # action_sequence is provenance only, never inherited authority.
            self._action_sequence = 0
            self._restored = True
            rebound = self._capture()
            if rebound.samples != original.samples or rebound.sim_time_ns != original.sim_time_ns:
                raise RoboticsError(Code.REPLAY_FAILED)
            self._batch = canonical_json(rebound)
        except BaseException:
            self._closed = True
            raise
        return self.observe()

    def terminate(self) -> None:
        self._closed = True
        self._prepared = None
        if self._renderer is not None:
            self._renderer.close()
            self._renderer = None
