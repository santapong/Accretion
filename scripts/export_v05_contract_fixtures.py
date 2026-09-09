"""Generate synthetic v0.5 schema examples; none is an operational approval or result.

The deterministic signing key below is PUBLIC TEST MATERIAL. Never install it
as a trusted evaluator key. Digests label synthetic blobs; no simulator or
artifact store is contacted by this script.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from accretion.contracts import PrincipalRef
from accretion.contracts.canonical import CanonicalContract, canonical_json, content_hash
from accretion.contracts.robotics import (
    CONTRACT_INVENTORY,
    SafetyDecisionPayload,
    sign_safety_payload,
)
from accretion.contracts.robotics.values import PreparedGripperCommand
from accretion.contracts.routing import ObjectiveContractRef
from accretion.ids import derived_id

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests/fixtures/contracts/v0.5"
CREATED = "2026-09-09T00:00:00Z"
LATER = "2026-09-09T00:05:00Z"
PUBLIC_TEST_KEY = bytes(range(32))
PROJECT = derived_id("project", "v05-schema-dev-fixture")
WORKSPACE = derived_id("workspace_entity", "v05-schema-dev-fixture")
EPISODE = derived_id("simulation_episode", "v05-schema-dev-fixture")
HUMAN = {
    "principal_id": derived_id("principal", "v05-schema-dev-human"),
    "display_name": "SYNTHETIC schema fixture; not an operational approval",
    "status": "ACTIVE",
}
VERIFIER = {**HUMAN, "principal_id": derived_id("principal", "v05-schema-dev-verifier")}
PRODUCER = {**HUMAN, "principal_id": derived_id("principal", "v05-schema-dev-producer")}


def digest(label: str) -> str:
    return hashlib.sha256(f"v05-SYNTHETIC-contract-fixture:{label}".encode()).hexdigest()


def blob(label: str, *, simulation: bool = False) -> dict[str, Any]:
    value = digest(label)
    return {
        "uri": f"artifact://sha256/{value}",
        "digest": value,
        "media_type": "application/json",
        "size_bytes": 32,
        "retention_class": "RESEARCH_ARCHIVE",
        "evidence_class": "SIMULATION" if simulation else "DIGITAL",
    }


def header(model: type[CanonicalContract]) -> dict[str, Any]:
    assert model.ID_KIND is not None
    return {
        "contract_id": derived_id(model.ID_KIND, "v05-schema-dev-fixture"),
        "created_at": CREATED,
        "created_by": HUMAN,
        "workspace_id": WORKSPACE,
        "project_id": PROJECT,
        "labels": {"fixture": "SYNTHETIC_NOT_EXECUTION_OR_AUTHORIZATION"},
    }


def examples() -> dict[str, CanonicalContract]:
    command = PreparedGripperCommand(
        opening_m=0.04,
        velocity_m_s=0.01,
        acceleration_m_s2=0.02,
        force_limit_n=10,
    )
    command_digest = content_hash(command, exclude=())
    command_ref = {
        **blob("command"),
        "uri": f"artifact://sha256/{command_digest}",
        "digest": command_digest,
        "size_bytes": len(canonical_json(command)),
    }
    policy = {
        "policy_id": "dev-fixture-policy",
        "version": "1.0.0",
        "content_digest": digest("policy"),
    }
    verifier_ref = {
        "verifier_contract_id": "dev-fixture-verifier",
        "implementation_digest": digest("verifier"),
    }
    state = {
        "observation_digest": digest("observation"),
        "observation_sequence": 0,
        "sim_time_ns": 0,
        "frame_transform_digest": digest("transforms"),
    }
    lease = {"lease_id": derived_id("simulation_lease", "v05-schema-dev-fixture"), "generation": 1}
    profile = {
        "family": "MUJOCO",
        "version": "3.3.7",
        "middleware": "NONE",
        "transport": "LOCAL_PROCESS",
        "profile_id": "dev-cpu-contract-fixture",
    }
    ref = {
        "contract_id": derived_id("embodiment_descriptor", "dev-fixture-reference"),
        "content_hash": digest("reference"),
    }
    objective = ObjectiveContractRef(
        contract_id="dev-fixture-objective-ref",
        created_at=CREATED,
        created_by=PrincipalRef.model_validate(HUMAN),
        workspace_id=WORKSPACE,
        project_id=PROJECT,
        objective_contract_id=derived_id("objective_contract", "dev"),
        revision=1,
        objective_contract_hash=digest("objective"),
        verified_success_floor=0.9,
        utility_profile_id="dev-fixture-utility",
        risk_policy=policy,
        approved_by=PrincipalRef.model_validate(HUMAN),
        approved_at=CREATED,
    )
    pins = {
        "episode_id": EPISODE,
        "experiment_contract_hash": digest("experiment"),
        "preflight_receipt_hash": digest("preflight"),
        "environment_snapshot_hash": digest("environment"),
        "adapter_manifest_hash": digest("adapter"),
        "safety_envelope_hash": digest("envelope"),
        "verification_spec_hash": digest("verification"),
        "seed": 101,
        "randomization_sample_hash": digest("randomization"),
        "lease": lease,
    }
    fields: dict[str, dict[str, Any]] = {
        "EmbodimentDescriptor": {
            "embodiment_id": "robot.sim.dev-arm.v1",
            "descriptor_version": "1.0.0",
            "kinematics": {
                "joint_names": [f"joint_{i}" for i in range(6)],
                "base_frame": "base",
                "tool_frame": "tool",
                "joint_limits_ref": blob("limits"),
                "transform_provenance_ref": blob("transforms"),
            },
            "control_interfaces": ["JOINT_TRAJECTORY"],
            "sensors": [{"sensor_id": "rgb", "modality": "RGB", "frame_id": "camera"}],
            "end_effectors": [
                {
                    "joint_names": ["finger_joint"],
                    "joint_types": ["PRISMATIC"],
                    "minimum_opening_m": 0,
                    "maximum_opening_m": 0.085,
                    "maximum_force_n": 20,
                }
            ],
            "workspace_ref": blob("workspace"),
            "robot_model_ref": blob("robot"),
        },
        "ObservationSpec": {
            "required": [
                {
                    "field": "joint_position",
                    "sensor_id": "joint_state",
                    "modality": "JOINT_STATE",
                    "dtype": "float64",
                    "shape": [6],
                    "unit": "rad",
                    "frame_id": "base",
                }
            ],
            "time_alignment": {"maximum_skew_ms": 20},
        },
        "ActionIntent": {
            "episode_id": EPISODE,
            "sequence": 0,
            "semantic_action": "MOVE_END_EFFECTOR",
            "target": {
                "kind": "MOVE_END_EFFECTOR",
                "frame_id": "base",
                "position_m": [0.4, 0, 0.3],
                "orientation_xyzw": [0, 0, 0, 1],
            },
            "constraints": {
                "speed_scale": 0.2,
                "acceleration_scale": 0.15,
                "planning_time_ms": 500,
            },
            "state": state,
            "expires_at_sim_time_ns": 1_000_000_000,
            "idempotency_key": digest("request"),
        },
        "SafetyEnvelope": {
            "gripper": {
                "minimum_opening_m": 0,
                "maximum_opening_m": 0.085,
                "maximum_velocity_m_s": 0.02,
                "maximum_acceleration_m_s2": 0.04,
                "maximum_force_n": 20,
            },
            "embodiment_descriptor_hash": digest("embodiment"),
            "controller_modes": ["JOINT_TRAJECTORY"],
            "joint_limits": [
                {
                    "joint_name": f"joint_{i}",
                    "lower_rad": -3.0,
                    "upper_rad": 3.0,
                    "velocity_rad_s": 1.0,
                    "acceleration_rad_s2": 2.0,
                    "effort_nm": 30.0,
                }
                for i in range(6)
            ],
            "workspace_ref": blob("workspace"),
            "speed_scale_max": 0.25,
            "acceleration_scale_max": 0.2,
            "joint_limit_margin_rad": 0.05,
            "tool_payload_ref": blob("payload"),
            "max_episode_sim_seconds": 120,
            "max_actions": 300,
            "max_cumulative_joint_motion_rad": 80,
            "no_action_timeout_seconds": 5,
            "policy_ref": policy,
        },
        "RobotAdapterManifest": {
            "adapter_id": "dev.sim.arm.adapter",
            "adapter_version": "0.5.0",
            "embodiment_descriptor_hashes": [digest("embodiment")],
            "simulator": profile,
            "capabilities": [
                {"capability_id": "robotics.sim.observe", "capability_version": "1.0.0"}
            ],
            "observation_spec_hash": digest("observation-schema"),
            "action_intent_schema_hash": digest("action-schema"),
            "prepared_command_schema_hash": digest("command-schema"),
            "artifact_digest": digest("adapter"),
            "artifact_signature_ref": blob("adapter-signature"),
            "controller_modes": ["JOINT_TRAJECTORY"],
            "endpoint_profile_id": "dev-local-cpu",
            "process_budget": {
                "max_cpu_seconds": 120,
                "max_wall_seconds": 180,
                "max_memory_bytes": 1_000_000_000,
                "max_output_bytes": 1_000_000,
            },
        },
        "SimulationExperimentContract": {
            "objective_contract_ref": objective,
            "node_contract_ref": {
                "node_contract_id": derived_id("node_contract", "dev"),
                "immutable_hash": digest("node"),
            },
            "embodiment_ref": ref,
            "adapter_ref": ref,
            "world_artifact_digest": digest("world"),
            "controller_artifact_digest": digest("controller"),
            "safety_envelope_hash": digest("envelope"),
            "verification_spec_hash": digest("verification"),
            "seed_set": [101, 102],
            "randomization_spec_ref": blob("randomization-spec"),
            "resource_budget": {
                "max_episodes": 4,
                "max_wall_seconds": 3600,
                "max_artifact_bytes": 1_000_000,
            },
            "tolerance_profile_ref": blob("tolerances"),
            "protocol_ref": blob("protocol"),
        },
        "SimulationEnvironmentSnapshot": {
            "environment_profile_hash": digest("environment-profile"),
            "host_compatibility_profile_hash": digest("host-profile"),
            "simulator_image_digest": digest("image"),
            "world_digest": digest("world"),
            "robot_model_digest": digest("robot"),
            "controller_digest": digest("controller"),
            "adapter_digest": digest("adapter"),
            "physics_engine": "mujoco",
            "physics_parameters_ref": blob("physics"),
            "rendering_parameters_ref": blob("rendering"),
            "simulator": profile,
            "host_architecture": "x86_64",
            "randomization_sample_ref": blob("randomization"),
        },
        "EpisodeRecord": {
            "episode_id": EPISODE,
            "run_id": derived_id("run", "dev"),
            "experiment_contract_hash": digest("experiment"),
            "environment_snapshot_hash": digest("environment"),
            "seed": 101,
            "started_at": CREATED,
            "finished_at": LATER,
            "termination_reason": "CANCELLED",
            **{
                key: blob(key, simulation=True)
                for key in (
                    "trajectory_ref",
                    "sensor_manifest_ref",
                    "action_receipts_ref",
                    "safety_events_ref",
                    "metrics_ref",
                    "provenance_manifest_ref",
                )
            },
            "producer_runtime_ref": "dev-fixture-runtime",
            "producer_principal": PRODUCER,
            "replay_class": "TOLERANT",
        },
        "EmbodiedVerificationSpec": {
            "task_verifiers": [{"verifier": verifier_ref}],
            "safety_verifiers": [{"verifier": verifier_ref}],
            "reproducibility_verifier": {"verifier": verifier_ref},
            "evidence_completeness_verifier": {"verifier": verifier_ref},
        },
        "AdapterConformanceReport": {
            "created_by": VERIFIER,
            "adapter_manifest_hash": digest("adapter"),
            "dependencies": {
                key: digest(key)
                for key in (
                    "adapter_artifact_digest",
                    "simulator_image_digest",
                    "world_digest",
                    "robot_model_digest",
                    "controller_digest",
                    "observation_spec_hash",
                    "action_intent_schema_hash",
                    "prepared_command_schema_hash",
                    "tolerance_profile_hash",
                    "environment_profile_hash",
                    "physics_parameters_hash",
                    "rendering_parameters_hash",
                    "host_compatibility_profile_hash",
                )
            },
            "suite_version": "0.5.0",
            "suite_artifact_digest": digest("suite"),
            "tests_total": 10,
            "tests_passed": 0,
            "result": "INCONCLUSIVE",
            "evidence_bundle_ref": blob("conformance", simulation=True),
            "verifier": verifier_ref,
            "verifier_principal": VERIFIER,
            "adapter_producer_principal": PRODUCER,
        },
        "SimulationLease": {
            "episode_id": EPISODE,
            "run_id": derived_id("run", "dev"),
            "experiment_contract_hash": digest("experiment"),
            "environment_snapshot_hash": digest("environment"),
            "adapter_manifest_hash": digest("adapter"),
            "generation": 1,
            "resource_id": "dev-fixture-resource",
            "endpoint_handle": "simh_" + "a" * 32,
            "owner_principal": PRODUCER,
            "acquired_at": CREATED,
            "expires_at": LATER,
            "heartbeat_timeout_seconds": 5,
        },
        "SimulationRunBinding": {
            "created_by": PRODUCER,
            "run_id": derived_id("run", "dev"),
            "episode_id": EPISODE,
            "experiment_contract_hash": digest("experiment"),
            "orchestrator_principal": PRODUCER,
        },
        "SimulationPreflightReceipt": {
            **{key: value for key, value in pins.items() if key != "preflight_receipt_hash"},
            "conformance_report_hash": digest("conformance"),
            "checks": [
                {
                    "name": name,
                    "passed": False,
                    "evidence_ref": blob(name),
                    "reason_code": "SYNTHETIC_NOT_EXECUTED",
                }
                for name in (
                    "CONTRACT_HASHES",
                    "CONFORMANCE",
                    "ENVIRONMENT",
                    "SEED_RANDOMIZATION",
                    "OBSERVATIONS",
                    "SAFETY_ENVELOPE",
                    "INDEPENDENT_VERIFIERS",
                    "QUOTA_LEASE",
                    "SIMULATION_ENDPOINT",
                    "WORKSPACE_ARTIFACT_STORE",
                )
            ],
            "result": "REJECTED",
            "valid_until": LATER,
        },
        "SimulationEpisodeApproval": {
            "pins": pins,
            "approved_by": HUMAN,
            "policy_ref": policy,
            "expires_at": LATER,
        },
        "SimulationApprovalMatrix": {
            "experiment_contract_hash": digest("experiment"),
            "expires_at": LATER,
            "approval_refs": [
                {
                    "contract_id": derived_id("simulation_episode_approval", "dev"),
                    "content_hash": digest("approval"),
                }
            ],
        },
        "PreparedCommand": {
            "episode_id": EPISODE,
            "action_intent_hash": digest("intent"),
            "sequence": 0,
            "state": state,
            "lease": lease,
            "controller_mode": "JOINT_TRAJECTORY",
            "command_ref": command_ref,
            "command_payload": command,
            "command_schema_hash": digest("command-schema"),
            "controller_digest": digest("controller"),
            "adapter_artifact_digest": digest("adapter"),
            "expires_at_sim_time_ns": 1_000_000_000,
        },
        "SimulationActionReceipt": {
            "episode_id": EPISODE,
            "sequence": 0,
            "idempotency_key": digest("request"),
            "action_intent_hash": digest("intent"),
            "prepared_command_hash": digest("command"),
            "safety_decision_hash": digest("safety"),
            "lease": lease,
            "status": "DENIED",
            "reason_code": "SYNTHETIC_NOT_EXECUTED",
        },
        "EmbodiedVerificationResult": {
            "created_by": VERIFIER,
            "episode_id": EPISODE,
            "episode_record_hash": digest("episode"),
            "verification_spec_hash": digest("verification"),
            "verifier": verifier_ref,
            "verifier_principal": VERIFIER,
            "producer_principal": PRODUCER,
            "verifier_process_id": "dev-verifier-process",
            "producer_process_id": "dev-producer-process",
            "role": "TASK",
            "status": "INCONCLUSIVE",
            "deterministic": True,
            "evidence_refs": [blob("evidence", simulation=True)],
            "findings_ref": blob("findings", simulation=True),
        },
        "SimulationDomainEvent": {
            "event_type": "embodiment.registered",
            "occurred_at": CREATED,
            "correlation_id": "dev-fixture-correlation",
            "producer": HUMAN,
            "sequence": 0,
            "payload": {"fixture_only": True, "descriptor_hash": digest("embodiment")},
        },
        "EpisodeQuarantineRecord": {
            "episode_id": EPISODE,
            "episode_record_hash": digest("episode"),
            "contradiction_evidence_ref": blob("contradiction", simulation=True),
            "reason_code": "SYNTHETIC_NOT_EXECUTED",
        },
    }
    result: dict[str, CanonicalContract] = {}
    for model in CONTRACT_INVENTORY:
        common = header(model)
        if model.__name__ == "SafetyDecisionReceipt":
            common["created_by"] = VERIFIER
            budget = {"actions": 0, "cumulative_joint_motion_rad": 0, "elapsed_sim_seconds": 0}
            payload = SafetyDecisionPayload.model_validate(
                {
                    "receipt_id": common["contract_id"],
                    "workspace_id": WORKSPACE,
                    "project_id": PROJECT,
                    "episode_id": EPISODE,
                    "action_intent_hash": digest("intent"),
                    "prepared_command_hash": digest("command"),
                    "safety_envelope_hash": digest("envelope"),
                    "episode_approval_hash": digest("approval"),
                    "policy_ref": policy,
                    "state": state,
                    "lease": lease,
                    "evaluator": verifier_ref,
                    "evaluator_principal_id": VERIFIER["principal_id"],
                    "decision": "DENY",
                    "reason_codes": ["SYNTHETIC_NOT_EXECUTED"],
                    "budget_before": budget,
                    "budget_after": budget,
                    "issued_at": CREATED,
                    "expires_at_sim_time_ns": 1_000_000_000,
                }
            )
            fields[model.__name__] = {
                "decision": payload,
                "signature": sign_safety_payload(
                    payload,
                    key_id="PUBLIC_DEV_TEST_KEY_NEVER_TRUST",
                    private_key=Ed25519PrivateKey.from_private_bytes(PUBLIC_TEST_KEY),
                ),
            }
        result[model.__name__] = model.model_validate({**common, **fields[model.__name__]})
    return result


def main() -> None:
    for name, record in examples().items():
        target = FIXTURE_ROOT / name
        target.mkdir(parents=True, exist_ok=True)
        complete = record.model_dump(mode="json")
        required = {key for key, field in type(record).model_fields.items() if field.is_required()}
        required.update({"created_at", "project_id", "labels"})
        minimal = {key: value for key, value in complete.items() if key in required}
        minimal_record = type(record).model_validate(minimal)
        minimal["content_hash"] = minimal_record.content_hash
        invalid = {**complete, "project_id": None}
        invalid.pop("content_hash")
        unknown = {**complete, "schema_version": "2.0.0"}
        for variant, payload in {
            "complete": complete,
            "minimal": minimal,
            "invalid": invalid,
            "unknown_version": unknown,
        }.items():
            (target / f"{variant}.json").write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n"
            )
    print(f"wrote {len(CONTRACT_INVENTORY) * 4} synthetic v0.5 fixture files")


if __name__ == "__main__":
    main()
