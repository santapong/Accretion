#!/usr/bin/env python3
"""Bounded M0 development feasibility, not adapter conformance or task acceptance.

Run on an explicitly installed optional environment with the pinned Menagerie
checkout. No networking, viewer, agent runtime, robot transport, or learned
controller is used. Both models execute sequentially in this process.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

PIN = "e4049d0a3bfd58d2a3081614e6777d4007e3f86a"
MODEL_DIRS = ("universal_robots_ur5e", "robotiq_2f85", "franka_emika_panda")
PACKAGES = {"mujoco": "3.10.0", "numpy": "2.3.3", "Pillow": "11.3.0"}
SOFTWARE_ENV = {
    "MUJOCO_GL": "egl",
    "PYOPENGL_PLATFORM": "egl",
    "LIBGL_ALWAYS_SOFTWARE": "1",
    "MESA_LOADER_DRIVER_OVERRIDE": "llvmpipe",
    "EGL_PLATFORM": "surfaceless",
    "LP_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
}
STEPS = 1000
DT = 0.002
SEED = 707


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, timeout=30).strip()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def run(root: Path, output: Path) -> dict[str, object]:
    # Set before importing OpenGL/MuJoCo. No fallback to an accelerated renderer.
    os.environ.update(SOFTWARE_ENV)
    # MuJoCo may write MUJOCO_LOG.TXT on errors; keep it with this run's evidence.
    os.chdir(output)
    resource.setrlimit(resource.RLIMIT_CPU, (240, 240))
    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024 * 1024, 128 * 1024 * 1024))
    signal.alarm(300)
    import mujoco
    import numpy as np
    from OpenGL import GL
    from PIL import Image

    for name, expected in PACKAGES.items():
        actual = importlib.metadata.version(name)
        if actual != expected:
            raise RuntimeError(f"{name}: expected {expected}, got {actual}")
    if git(root, "rev-parse", "HEAD") != PIN:
        raise RuntimeError("model checkout does not match the explicit source pin")
    if git(root, "status", "--porcelain", "--", *MODEL_DIRS):
        raise RuntimeError("model checkout contains changes or missing pinned files")

    source_files = {
        str(p.relative_to(root)): digest(p)
        for name in MODEL_DIRS
        for p in sorted((root / name).rglob("*"))
        if p.is_file()
    }
    if not source_files:
        raise RuntimeError("model sources are missing")
    write_json(output / "model-files.json", source_files)
    model_trees = {name: git(root, "rev-parse", f"HEAD:{name}") for name in MODEL_DIRS}
    license_hashes = {name: digest(root / name / "LICENSE") for name in MODEL_DIRS}
    installed = {dist.metadata["Name"]: dist.version for dist in importlib.metadata.distributions()}
    context = mujoco.GLContext(320, 240)
    context.make_current()
    renderer_name = GL.glGetString(GL.GL_RENDERER).decode()
    gl_version = GL.glGetString(GL.GL_VERSION).decode()
    context.free()
    if "llvmpipe" not in renderer_name.lower():
        raise RuntimeError(f"software renderer required; received {renderer_name}")

    results = []
    for kind in ("ur5e_2f85", "panda"):
        assembly = output / kind
        assembly.mkdir()
        if kind == "ur5e_2f85":
            spec = mujoco.MjSpec.from_file(str(root / MODEL_DIRS[0] / "scene.xml"))
            spec.option.cone = mujoco.mjtCone.mjCONE_ELLIPTIC
            spec.option.impratio = 10.0
            child = mujoco.MjSpec.from_file(str(root / MODEL_DIRS[1] / "2f85.xml"))
            spec.attach(child, prefix="2f85/", site=spec.site("attachment_site"))
            arm_names = [
                "shoulder_pan_joint",
                "shoulder_lift_joint",
                "elbow_joint",
                "wrist_1_joint",
                "wrist_2_joint",
                "wrist_3_joint",
            ]
            actuator_names = [
                "shoulder_pan",
                "shoulder_lift",
                "elbow",
                "wrist_1",
                "wrist_2",
                "wrist_3",
            ]
            home = [-1.5708, -1.5708, 1.5708, -1.5708, -1.5708, 0.0]
            gripper_name = "2f85/fingers_actuator"
            grip = 0.0
        else:
            spec = mujoco.MjSpec.from_file(str(root / MODEL_DIRS[2] / "scene.xml"))
            arm_names = [f"joint{i}" for i in range(1, 8)]
            actuator_names = [f"actuator{i}" for i in range(1, 8)]
            home = [0.0, 0.0, 0.0, -1.57079, 0.0, 1.57079, -0.7853]
            gripper_name = "actuator8"
            grip = 255.0
        # Home vectors above come from each pinned upstream model's home key.
        # Drop keyframes before adding a free body with additional qpos entries.
        for key in list(spec.keys):
            spec.delete(key)
        spec.option.timestep = DT
        spec.option.cone = mujoco.mjtCone.mjCONE_ELLIPTIC
        spec.option.impratio = 10.0
        spec.modelname = f"accretion_m0_{kind}"
        cube = spec.worldbody.add_body(name="development_cube", pos=[0.35, -0.45, 0.2])
        cube.add_freejoint(name="development_cube_free")
        cube.add_geom(
            name="development_cube_geom",
            type=mujoco.mjtGeom.mjGEOM_BOX,
            size=[0.025, 0.025, 0.025],
            mass=0.05,
            rgba=[0.9, 0.3, 0.12, 1.0],
            contype=1,
            conaffinity=1,
            friction=[0.8, 0.005, 0.0001],
        )
        camera_pos = np.array([1.4, -1.5, 1.05])
        forward = np.array([0.1, -0.15, 0.3]) - camera_pos
        forward /= np.linalg.norm(forward)
        right = np.cross(forward, [0.0, 0.0, 1.0])
        right /= np.linalg.norm(right)
        up = np.cross(right, forward)
        spec.worldbody.add_camera(
            name="development_camera",
            pos=camera_pos,
            xyaxes=np.concatenate([right, up]),
            fovy=48.0,
        )
        spec.visual.global_.offwidth = 320
        spec.visual.global_.offheight = 240
        model = spec.compile()
        compiled = assembly / "compiled.xml"
        compiled.write_text(spec.to_xml())
        joint_ids = [model.joint(name).id for name in arm_names]
        qpos_ids = [int(model.jnt_qposadr[j]) for j in joint_ids]
        actuator_ids = [model.actuator(name).id for name in actuator_names]
        gripper_id = model.actuator(gripper_name).id
        cube_id = model.geom("development_cube_geom").id
        floor_id = model.geom("floor").id
        cube_qpos = int(model.jnt_qposadr[model.joint("development_cube_free").id])
        joints = []
        for j in range(model.njnt):
            jt = int(model.jnt_type[j])
            if jt == int(mujoco.mjtJoint.mjJNT_FREE):
                continue
            unit = "rad" if jt == int(mujoco.mjtJoint.mjJNT_HINGE) else "m"
            joints.append(
                {
                    "name": model.joint(j).name,
                    "type": "hinge" if unit == "rad" else "slide",
                    "position_unit": unit,
                    "velocity_unit": f"{unit}/s",
                    "range": model.jnt_range[j].tolist(),
                    "limited": bool(model.jnt_limited[j]),
                }
            )
        if any(int(model.jnt_type[j]) != int(mujoco.mjtJoint.mjJNT_HINGE) for j in joint_ids):
            raise RuntimeError("unexpected arm joint type")

        traces = []
        contact_steps = 0
        max_contact_force = 0.0
        max_tracking_error = 0.0
        renderer = mujoco.Renderer(model, height=240, width=320)
        observed = {}
        for repetition in range(2):
            data = mujoco.MjData(model)
            mujoco.mj_resetData(model, data)
            data.qpos[qpos_ids] = home
            if kind == "panda":
                for name in ("finger_joint1", "finger_joint2"):
                    data.qpos[model.jnt_qposadr[model.joint(name).id]] = 0.04
            data.ctrl[actuator_ids] = home
            data.ctrl[gripper_id] = grip
            data.qpos[cube_qpos] = np.random.default_rng(SEED).uniform(0.34, 0.36)
            mujoco.mj_forward(model, data)
            trace = []
            for step in range(STEPS):
                target = np.array(home)
                target[0] += 0.015 * np.sin(np.pi * step / STEPS)
                data.ctrl[actuator_ids] = target
                mujoco.mj_step(model, data)
                if not np.isfinite(data.qpos).all() or not np.isfinite(data.qvel).all():
                    raise RuntimeError("non-finite state")
                if abs(data.time - (step + 1) * DT) > 1e-10:
                    raise RuntimeError("simulation clock changed unexpectedly")
                max_tracking_error = max(
                    max_tracking_error, float(np.max(np.abs(data.qpos[qpos_ids] - target)))
                )
                for j in joint_ids:
                    pos = float(data.qpos[model.jnt_qposadr[j]])
                    lo, hi = model.jnt_range[j]
                    if bool(model.jnt_limited[j]) and not lo - 1e-4 <= pos <= hi + 1e-4:
                        raise RuntimeError("arm exceeded a position limit")
                contact = False
                for c in range(data.ncon):
                    pair = {int(data.contact[c].geom1), int(data.contact[c].geom2)}
                    if pair == {cube_id, floor_id}:
                        contact = True
                        force = np.zeros(6)
                        mujoco.mj_contactForce(model, data, c, force)
                        max_contact_force = max(max_contact_force, float(force[0]))
                if repetition == 0 and contact:
                    contact_steps += 1
                if step % 10 == 0:
                    trace.append(
                        np.concatenate(
                            [[data.time], data.qpos.copy(), data.qvel.copy(), [int(contact)]]
                        )
                    )
            traces.append(np.asarray(trace))
            if repetition == 0:
                renderer.update_scene(data, camera="development_camera")
                rgb = renderer.render().copy()
                renderer.enable_depth_rendering()
                renderer.update_scene(data, camera="development_camera")
                depth = renderer.render().copy()
                renderer.disable_depth_rendering()
                if rgb.shape != (240, 320, 3) or rgb.dtype != np.uint8 or float(rgb.std()) < 5:
                    raise RuntimeError("RGB observation failed shape/dtype/content checks")
                if (
                    depth.shape != (240, 320)
                    or not np.isfinite(depth).all()
                    or float(depth.min()) <= 0
                ):
                    raise RuntimeError("depth observation failed shape/finite/metric checks")
                Image.fromarray(rgb).save(assembly / "rgb.png")
                np.save(assembly / "depth-metres.npy", depth, allow_pickle=False)
                observed = {
                    "rgb_shape": list(rgb.shape),
                    "rgb_dtype": str(rgb.dtype),
                    "rgb_std": float(rgb.std()),
                    "depth_shape": list(depth.shape),
                    "depth_dtype": str(depth.dtype),
                    "depth_unit": "m",
                    "depth_min": float(depth.min()),
                    "depth_max": float(depth.max()),
                    "clock": "SIMULATION",
                    "sample_time_seconds": float(data.time),
                    "cube_final_height_m": float(data.qpos[cube_qpos + 2]),
                }
        renderer.close()
        reset_delta = float(np.max(np.abs(traces[0] - traces[1])))
        movement = float(np.ptp(traces[0][:, 1 + qpos_ids[0]]))
        if reset_delta > 1e-10 or contact_steps == 0 or max_contact_force <= 0:
            raise RuntimeError("reset-repeatability or rigid-object contact witness failed")
        if max_tracking_error > 0.3 or movement < 1e-5:
            raise RuntimeError("bounded controller step witness failed")
        if not 0.015 < float(observed["cube_final_height_m"]) < 0.035:
            raise RuntimeError("cube failed to settle on floor")
        np.save(assembly / "trace-first.npy", traces[0], allow_pickle=False)
        np.save(assembly / "trace-reset.npy", traces[1], allow_pickle=False)
        results.append(
            {
                "assembly": kind,
                "status": "PASS_DEVELOPMENT_FEASIBILITY",
                "arm_dof": len(joint_ids),
                "nq": model.nq,
                "nv": model.nv,
                "nu": model.nu,
                "ngeom": model.ngeom,
                "joint_metadata": joints,
                "observation": observed,
                "steps_per_reset": STEPS,
                "resets": 2,
                "timestep_seconds": DT,
                "seed": SEED,
                "same_process_reset_trace_max_abs_delta": reset_delta,
                "cube_floor_contact_steps_first_reset": contact_steps,
                "max_cube_floor_normal_force_newtons": max_contact_force,
                "max_arm_tracking_error_rad": max_tracking_error,
                "first_joint_observed_motion_rad": movement,
                "controller": (
                    "pinned upstream position servos; 0.015 rad sine on first arm joint; "
                    "other targets held"
                ),
                "physics": {"engine": "MuJoCo", "cone": "ELLIPTIC", "impratio": 10.0},
                "compiled_xml_sha256": digest(compiled),
            }
        )
        print(json.dumps({"assembly": kind, "status": results[-1]["status"]}), flush=True)
    signal.alarm(0)
    artifacts = {
        str(p.relative_to(output)): {"sha256": digest(p), "bytes": p.stat().st_size}
        for p in sorted(output.rglob("*"))
        if p.is_file()
    }
    return {
        "status": "PASS_DEVELOPMENT_FEASIBILITY",
        "scope": "M0 local development feasibility only",
        "not_proven": [
            "grasp/lift/release",
            "adapter conformance",
            "Accretion gateway integration",
            "safety admission",
            "independent verification",
            "cross-process replay",
            "benchmark claim",
            "physical execution",
        ],
        "model_commit": PIN,
        "model_tree_git_oids": model_trees,
        "model_file_manifest_sha256": digest(output / "model-files.json"),
        "model_license_sha256": license_hashes,
        "artifacts": artifacts,
        "requirements_lock_sha256": digest(
            Path(__file__).with_name("requirements-feasibility.txt")
        ),
        "installed_packages": installed,
        "python": sys.version,
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "renderer": renderer_name,
        "gl_version": gl_version,
        "software_environment": SOFTWARE_ENV,
        "script_sha256": digest(Path(__file__)),
        "assemblies": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output must be a new directory to preserve prior evidence")
    args.output.mkdir(parents=True)
    started = time.monotonic()
    try:
        result = run(args.models.resolve(), args.output.resolve())
        code = 0
    except Exception as error:
        result = {"status": "FAIL", "error": f"{type(error).__name__}: {error}"}
        code = 1
    result["wall_seconds"] = time.monotonic() - started
    usage = resource.getrusage(resource.RUSAGE_SELF)
    result["cpu_seconds"] = usage.ru_utime + usage.ru_stime
    result["exit_code"] = code
    write_json(args.output / "result.json", result)
    print(
        json.dumps({k: result[k] for k in ("status", "wall_seconds", "cpu_seconds", "exit_code")}),
        flush=True,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
