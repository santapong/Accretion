"""Contract-boundary witnesses, not AC5 runtime or simulation acceptance claims."""

from __future__ import annotations

import base64
import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError

from accretion.contracts.canonical import CanonicalContract, content_hash
from accretion.contracts.robotics import (
    CONTRACT_INVENTORY,
    CORE_CONTRACT_INVENTORY,
    ActionIntent,
    AdapterConformanceReport,
    CanonicalWriterEnvelope,
    EmbodiedVerificationResult,
    ObservationSpec,
    PreparedCommand,
    RobotAdapterManifest,
    SafetyDecisionReceipt,
    SafetyEnvelope,
    SimulationApprovalMatrix,
    SimulationDomainEvent,
    SimulationEpisodeApproval,
    SimulationExperimentContract,
    SimulationLease,
    SimulationPreflightReceipt,
    TrustedSafetyKey,
    verify_safety_signature,
)
from accretion.contracts.robotics.values import (
    ContentAddressedArtifactRef,
    Finite,
    Nonnegative,
    Positive,
    PreparedTrajectory,
    Scale,
)
from accretion.contracts.routing import RiskClass, risk_level_for
from accretion.ids import _PREFIXES, has_prefix

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/contracts/v0.5"
sys.path.insert(0, str(ROOT / "scripts"))
from export_contract_schemas import check_all, render, schema_path  # noqa: E402


def fixture(name: str, variant: str = "complete") -> dict[str, Any]:
    value: dict[str, Any] = json.loads((FIXTURES / name / f"{variant}.json").read_text())
    return value


def editable(model: type[CanonicalContract]) -> dict[str, Any]:
    result = fixture(model.__name__)
    result.pop("content_hash")
    return result


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=lambda item: item.__name__)
@pytest.mark.parametrize("variant", ["minimal", "complete"])
def test_golden_writer_round_trip_keeps_original_seals(
    model: type[CanonicalContract], variant: str
) -> None:
    payload = fixture(model.__name__, variant)
    value = model.model_validate(payload)
    assert value.content_hash == payload["content_hash"]
    assert model.ID_KIND is not None and has_prefix(value.contract_id, model.ID_KIND)
    envelope = CanonicalWriterEnvelope.from_contract(value)
    assert envelope.for_execution(model) == value
    assert envelope.writer_content_hash == value.content_hash


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=lambda item: item.__name__)
@pytest.mark.parametrize("variant", ["invalid", "unknown_version"])
def test_invalid_scope_and_unknown_major_are_rejected(
    model: type[CanonicalContract], variant: str
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(fixture(model.__name__, variant))


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=lambda item: item.__name__)
def test_schema_exports_and_complete_examples_are_valid(model: type[CanonicalContract]) -> None:
    assert schema_path(model).read_text() == render(model)
    schema = json.loads(render(model))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(fixture(model.__name__))
    assert list(
        Draft202012Validator(schema).iter_errors(fixture(model.__name__, "unknown_version"))
    )


def test_inventory_is_ten_core_plus_registered_unique_supporting_records() -> None:
    assert {model.__name__ for model in CORE_CONTRACT_INVENTORY} == {
        "EmbodimentDescriptor",
        "ObservationSpec",
        "ActionIntent",
        "SafetyEnvelope",
        "RobotAdapterManifest",
        "SimulationExperimentContract",
        "SimulationEnvironmentSnapshot",
        "EpisodeRecord",
        "EmbodiedVerificationSpec",
        "AdapterConformanceReport",
    }
    assert len({model.CONTRACT_TYPE for model in CONTRACT_INVENTORY}) == len(CONTRACT_INVENTORY)
    assert len(_PREFIXES.values()) == len(set(_PREFIXES.values()))
    assert check_all() is None
    assert risk_level_for(RiskClass.SIMULATION).value == "HIGH"


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=lambda item: item.__name__)
def test_unknown_minor_is_forwarded_but_cannot_become_execution(
    model: type[CanonicalContract],
) -> None:
    payload = fixture(model.__name__)
    payload.update(schema_version="1.1.0", future_optional={"retain": ["exact", 3]})
    payload["content_hash"] = content_hash(payload)
    original = json.dumps(payload)
    envelope = CanonicalWriterEnvelope(original)
    projected = envelope.read_projection(model)
    assert projected.content_hash != envelope.writer_content_hash
    assert projected.labels["upcast_dropped_keys"] == "future_optional"
    assert envelope.forward_json() == original
    assert envelope.payload()["future_optional"] == {"retain": ["exact", 3]}
    with pytest.raises(ValueError, match="fully understood"):
        envelope.for_execution(model)
    with pytest.raises(ValueError, match="lossy reader projection"):
        CanonicalWriterEnvelope.from_contract(projected)


def test_unknown_same_version_or_nested_authority_field_does_not_become_a_projection() -> None:
    for field in ("outer", "nested"):
        payload = fixture("ActionIntent")
        if field == "outer":
            payload["disable_safety"] = True
        else:
            payload["schema_version"] = "1.1.0"
            payload["constraints"]["disable_safety"] = True
        payload["content_hash"] = content_hash(payload)
        with pytest.raises(ValidationError):
            CanonicalWriterEnvelope(json.dumps(payload)).read_projection(ActionIntent)


@pytest.mark.parametrize("mutation", ["missing_seal", "tamper", "unknown_major", "duplicate"])
def test_writer_boundary_rejects_missing_or_forged_seals(mutation: str) -> None:
    payload = fixture("ObservationSpec")
    if mutation == "missing_seal":
        payload.pop("content_hash")
    elif mutation == "tamper":
        payload["time_alignment"]["maximum_skew_ms"] = 1000
    elif mutation == "unknown_major":
        payload["schema_version"] = "2.0.0"
        payload["content_hash"] = content_hash(payload)
    serialized = json.dumps(payload)
    if mutation == "duplicate":
        serialized = serialized[:-1] + ', "project_id": "other"}'
    with pytest.raises(ValueError):
        CanonicalWriterEnvelope(serialized)


@pytest.mark.parametrize("numeric", [Finite, Nonnegative, Positive, Scale])
@pytest.mark.parametrize("bad", [True, False, "0.5", float("nan"), float("inf")])
def test_safety_numbers_do_not_coerce_boolean_strings_or_nonfinite_values(
    numeric: Any, bad: Any
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(numeric).validate_python(bad)
    assert TypeAdapter(numeric).validate_python(1) == 1.0


@pytest.mark.parametrize(
    "mutation", ["torque", "mismatch", "expired", "negative_sequence", "frame_extra"]
)
def test_action_intent_fails_closed_for_unrepresentable_or_expired_commands(mutation: str) -> None:
    payload = editable(ActionIntent)
    if mutation == "torque":
        payload["semantic_action"] = "RAW_TORQUE"
    elif mutation == "mismatch":
        payload["semantic_action"] = "SET_GRIPPER"
    elif mutation == "expired":
        payload["expires_at_sim_time_ns"] = payload["state"]["sim_time_ns"]
    elif mutation == "negative_sequence":
        payload["sequence"] = -1
    else:
        payload["target"]["physical_endpoint"] = "robot-device"
    with pytest.raises(ValidationError):
        ActionIntent.model_validate(payload)


def test_rgb_shape_unit_and_sensor_mapping_are_explicit() -> None:
    payload = editable(ObservationSpec)
    payload["required"] = [
        {
            "field": "rgb",
            "sensor_id": "wrist",
            "modality": "RGB",
            "dtype": "uint8",
            "shape": [480, 640, 3],
            "unit": "pixel",
            "frame_id": "wrist_camera",
        }
    ]
    ObservationSpec.model_validate(payload)
    for field, invalid in {"dtype": "float64", "unit": "N", "shape": [1]}.items():
        changed = copy.deepcopy(payload)
        changed["required"][0][field] = invalid
        with pytest.raises(ValidationError, match="modality"):
            ObservationSpec.model_validate(changed)


def test_contact_limits_require_explicit_force_depth_pairs_and_nonoverlapping_phases() -> None:
    payload = editable(SafetyEnvelope)
    contact = {
        "first_body": "finger",
        "second_body": "object",
        "phases": ["GRASP"],
        "maximum_force_n": 10,
        "maximum_penetration_m": 0.001,
    }
    payload["allowed_contacts"] = [contact]
    with pytest.raises(ValidationError, match="STOP_BEFORE_CONTACT"):
        SafetyEnvelope.model_validate(payload)
    payload["collision_policy"] = "DECLARED_CONTACT_ONLY"
    SafetyEnvelope.model_validate(payload)
    payload["allowed_contacts"].append({**contact, "first_body": "object", "second_body": "finger"})
    with pytest.raises(ValidationError, match="overlap"):
        SafetyEnvelope.model_validate(payload)


def test_panda_gripper_limits_stay_in_metres_and_do_not_reuse_angular_limits() -> None:
    payload = editable(SafetyEnvelope)
    payload["gripper"]["maximum_velocity_m_s"] = "0.02"
    with pytest.raises(ValidationError):
        SafetyEnvelope.model_validate(payload)
    payload["gripper"]["maximum_velocity_m_s"] = 0.02
    payload["gripper"]["maximum_opening_m"] = 0
    with pytest.raises(ValidationError):
        SafetyEnvelope.model_validate(payload)


@pytest.mark.parametrize(
    "capability", ["robotics.physical.move", "robotics.sim.raw_torque", "shell.execute"]
)
def test_manifest_cannot_declare_physical_or_undeclared_escape_capabilities(
    capability: str,
) -> None:
    payload = editable(RobotAdapterManifest)
    payload["capabilities"][0]["capability_id"] = capability
    with pytest.raises(ValidationError, match="simulation capabilities"):
        RobotAdapterManifest.model_validate(payload)


def test_manifest_and_conformance_do_not_create_a_content_hash_cycle() -> None:
    assert "conformance_report_hash" not in RobotAdapterManifest.model_fields
    payload = editable(AdapterConformanceReport)
    payload["result"] = "PASS"
    with pytest.raises(ValidationError, match="every test"):
        AdapterConformanceReport.model_validate(payload)
    payload["tests_passed"] = payload["tests_total"]
    payload["verifier_principal"] = payload["adapter_producer_principal"]
    with pytest.raises(ValidationError, match="own conformance"):
        AdapterConformanceReport.model_validate(payload)


def test_approval_cannot_be_an_unbounded_matrix_or_unattributed_human_record() -> None:
    matrix = editable(SimulationApprovalMatrix)
    matrix["approval_refs"][0]["contract_id"] = "*"
    with pytest.raises(ValidationError):
        SimulationApprovalMatrix.model_validate(matrix)
    approval = editable(SimulationEpisodeApproval)
    approval["approved_by"]["principal_id"] = "different-principal"
    with pytest.raises(ValidationError, match="approving human"):
        SimulationEpisodeApproval.model_validate(approval)
    approval = editable(SimulationEpisodeApproval)
    approval["max_consumptions"] = 2
    with pytest.raises(ValidationError):
        SimulationEpisodeApproval.model_validate(approval)


def test_lease_has_opaque_endpoint_and_preflight_requires_all_ten_checks() -> None:
    lease = editable(SimulationLease)
    lease["endpoint_handle"] = "https://physical-controller.example"
    with pytest.raises(ValidationError):
        SimulationLease.model_validate(lease)
    preflight = editable(SimulationPreflightReceipt)
    preflight["result"] = "PASS"
    with pytest.raises(ValidationError, match="disagrees"):
        SimulationPreflightReceipt.model_validate(preflight)
    preflight["result"] = "REJECTED"
    preflight["checks"][-1] = preflight["checks"][0]
    with pytest.raises(ValidationError, match="exactly once"):
        SimulationPreflightReceipt.model_validate(preflight)


def test_seed_budget_and_high_simulation_risk_are_not_weakened() -> None:
    payload = editable(SimulationExperimentContract)
    payload["resource_budget"]["max_episodes"] = 1
    with pytest.raises(ValidationError, match="episode budget"):
        SimulationExperimentContract.model_validate(payload)
    payload = editable(SimulationExperimentContract)
    payload["risk_class"] = "LOW_DIGITAL"
    with pytest.raises(ValidationError):
        SimulationExperimentContract.model_validate(payload)


def test_prepared_payload_is_bound_to_its_artifact_not_an_opaque_unchecked_blob() -> None:
    payload = editable(PreparedCommand)
    payload["command_payload"]["opening_m"] = 0.07
    with pytest.raises(ValidationError, match="exact typed"):
        PreparedCommand.model_validate(payload)
    points = [
        {
            "time_from_start_ns": time,
            "positions_rad": [0, 0],
            "velocities_rad_s": [0, 0],
            "accelerations_rad_s2": [0, 0],
            "effort_upper_bounds_nm": [1, 1],
        }
        for time in (0, 100)
    ]
    good = {"joint_names": ["shoulder", "elbow"], "points": points}
    PreparedTrajectory.model_validate(good)
    points[1]["time_from_start_ns"] = 0
    with pytest.raises(ValidationError, match="increases strictly"):
        PreparedTrajectory.model_validate(good)


def test_independent_verification_requires_both_process_and_principal_separation() -> None:
    for field in ("process", "principal"):
        payload = editable(EmbodiedVerificationResult)
        if field == "process":
            payload["verifier_process_id"] = payload["producer_process_id"]
        else:
            payload["verifier_principal"] = payload["producer_principal"]
        with pytest.raises(ValidationError, match="separate process and identity"):
            EmbodiedVerificationResult.model_validate(payload)


def test_detached_safety_signature_authenticates_exact_body_and_trusted_actor() -> None:
    receipt = SafetyDecisionReceipt.model_validate(fixture("SafetyDecisionReceipt"))
    public = Ed25519PrivateKey.from_private_bytes(bytes(range(32))).public_key().public_bytes_raw()
    trust = {receipt.signature.key_id: TrustedSafetyKey(receipt.created_by.principal_id, public)}
    verify_safety_signature(receipt, trusted_keys=trust)
    with pytest.raises(ValueError, match="not trusted"):
        verify_safety_signature(receipt, trusted_keys={})
    with pytest.raises(ValueError, match="not trusted"):
        verify_safety_signature(
            receipt, trusted_keys={receipt.signature.key_id: TrustedSafetyKey("other", public)}
        )
    forged = editable(SafetyDecisionReceipt)
    forged["decision"]["reason_codes"] = ["FORGED_AFTER_SIGNING"]
    forged["signature"]["unsigned_payload_digest"] = content_hash(forged["decision"], exclude=())
    resealed = SafetyDecisionReceipt.model_validate(forged)
    with pytest.raises(ValueError, match="signature verification failed"):
        verify_safety_signature(resealed, trusted_keys=trust)
    forged = editable(SafetyDecisionReceipt)
    forged["signature"]["signature_base64"] = base64.b64encode(bytes(64)).decode()
    with pytest.raises(ValueError, match="signature verification failed"):
        verify_safety_signature(SafetyDecisionReceipt.model_validate(forged), trusted_keys=trust)


def test_domain_events_allow_registration_before_a_run_but_pin_payload_digest() -> None:
    payload = fixture("SimulationDomainEvent")
    event = SimulationDomainEvent.model_validate(payload)
    assert event.run_id is None and event.payload_hash == content_hash(event.payload, exclude=())
    payload.pop("content_hash")
    payload["payload"]["descriptor_hash"] = "changed"
    with pytest.raises(ValidationError, match="payload digest"):
        SimulationDomainEvent.model_validate(payload)


def test_simulation_artifacts_never_relabel_as_physical_or_resolve_external_urls() -> None:
    artifact = fixture("EpisodeRecord")["trajectory_ref"]
    artifact["evidence_class"] = "PHYSICAL"
    with pytest.raises(ValidationError, match="PHYSICAL"):
        ContentAddressedArtifactRef.model_validate(artifact)
    artifact["evidence_class"] = "SIMULATION"
    artifact["uri"] = "https://external.example/sensor-data"
    with pytest.raises(ValidationError):
        ContentAddressedArtifactRef.model_validate(artifact)
