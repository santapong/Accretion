"""Synthetic pure-module witnesses; no real-host execution or AC5 claim is made."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from accretion.contracts import PrincipalRef
from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.refs import VerifierRef
from accretion.contracts.robotics import (
    ActionIntent,
    EmbodimentDescriptor,
    PreparedCommand,
    SafetyDecisionReceipt,
    SafetyEnvelope,
    SimulationEpisodeApproval,
    TrustedSafetyKey,
    verify_safety_signature,
)
from accretion.contracts.robotics.models import RoboticsContract
from accretion.contracts.robotics.values import DependencyClosure
from accretion.robotics.errors import RoboticsError
from accretion.robotics.safety import (
    HostPreview,
    PreviewRequest,
    SafetyContext,
    SafetyEvaluation,
    SafetySigner,
    evaluate_safety,
)

FIXTURES = Path(__file__).parent / "fixtures/contracts/v0.5"
DIGEST = "a" * 64


def fixture(model: type[RoboticsContract]) -> dict[str, Any]:
    return json.loads((FIXTURES / model.__name__ / "complete.json").read_text())


def change[T: RoboticsContract](value: T, **updates: Any) -> T:
    payload = value.model_dump(mode="python")
    payload.pop("content_hash")
    payload.update(updates)
    return type(value).model_validate(payload)


def box(minimum: float = -1.0, maximum: float = 1.0) -> dict[str, Any]:
    return {"minimum_m": [minimum] * 3, "maximum_m": [maximum] * 3}


class FixtureHost:
    """Deliberate fake trust injection. It does not implement kinematics or physics."""

    def __init__(self, mutate: Callable[[dict[str, Any]], None] | None = None):
        self.mutate = mutate
        self.calls = 0

    def preview(self, request: PreviewRequest) -> HostPreview:
        self.calls += 1
        data: dict[str, Any] = {
            "request_digest": request.digest,
            "coverage": "CONTINUOUS_CONSERVATIVE_PHYSICS_BOUNDS",
            "duration_ns": 2_000_000_000,
            "intent_satisfied": True,
            "geometry": {
                "descriptor_workspace": {
                    "artifact": request.descriptor.workspace_ref,
                    "box": box(),
                },
                "envelope_workspace": {"artifact": request.envelope.workspace_ref, "box": box()},
                "forbidden_volumes": [
                    {"artifact": ref, "box": box(0.8, 0.9)}
                    for ref in request.envelope.forbidden_volume_refs
                ],
                "joint_limits_ref": request.descriptor.kinematics.joint_limits_ref,
                "joint_limits": [
                    item.model_dump(mode="python") for item in request.envelope.joint_limits
                ],
                "tool_payload_ref": request.envelope.tool_payload_ref,
                "body_names": ["arm", "finger", "payload"],
            },
            "intervals": [
                {
                    "start_ns": start,
                    "end_ns": start + 1_000_000_000,
                    "joints": [
                        {
                            "joint_name": "joint_0",
                            "minimum_rad": 0.0,
                            "maximum_rad": 0.125,
                            "speed_upper_rad_s": 0.25,
                            "acceleration_upper_rad_s2": 0.5,
                            "effort_upper_nm": 1.0,
                            "motion_upper_rad": 0.125,
                        }
                    ],
                    "gripper": {
                        "minimum_opening_m": 0.04,
                        "maximum_opening_m": 0.04,
                        "speed_upper_m_s": 0.0,
                        "acceleration_upper_m_s2": 0.0,
                        "force_upper_n": 1.0,
                    },
                    "bodies": [
                        {"body_name": name, "box": box(-0.1, 0.1)}
                        for name in ["arm", "finger", "payload"]
                    ],
                    "contacts": [],
                }
                for start in [0, 1_000_000_000]
            ],
        }
        if self.mutate:
            self.mutate(data)
        return HostPreview.model_validate(data)


@dataclass
class Case:
    intent: ActionIntent
    prepared: PreparedCommand
    envelope: SafetyEnvelope
    descriptor: EmbodimentDescriptor
    approval: SimulationEpisodeApproval
    context: SafetyContext
    signer: SafetySigner
    host: FixtureHost | None

    def evaluate(self) -> SafetyDecisionReceipt:
        return self.issuance().receipt

    def issuance(self) -> SafetyEvaluation:
        return evaluate_safety(
            self.intent,
            self.prepared,
            self.envelope,
            self.descriptor,
            self.approval,
            context=self.context,
            host=self.host,
            signer=self.signer,
        )

    def rebind(self) -> None:
        """Re-seal intentionally changed fixtures without pretending old pins match."""
        self.envelope = change(
            self.envelope, embodiment_descriptor_hash=self.descriptor.content_hash
        )
        self.prepared = change(self.prepared, action_intent_hash=self.intent.content_hash)
        pins = self.approval.pins.model_dump(mode="python")
        pins["safety_envelope_hash"] = self.envelope.content_hash
        self.approval = change(self.approval, pins=pins)
        self.context.episode = self.approval.pins.model_copy(deep=True)
        self.context.approval_hash = self.approval.content_hash


@pytest.fixture
def case() -> Case:
    descriptor_data = fixture(EmbodimentDescriptor)
    descriptor_data.pop("content_hash")
    descriptor_data["kinematics"]["joint_names"] = ["joint_0"]
    descriptor = EmbodimentDescriptor.model_validate(descriptor_data)
    envelope_data = fixture(SafetyEnvelope)
    envelope_data.pop("content_hash")
    envelope_data.update(
        embodiment_descriptor_hash=descriptor.content_hash,
        joint_limits=[
            {
                "joint_name": "joint_0",
                "lower_rad": -1.0,
                "upper_rad": 1.0,
                "velocity_rad_s": 2.0,
                "acceleration_rad_s2": 5.0,
                "effort_nm": 30.0,
            }
        ],
        joint_limit_margin_rad=0.0,
        speed_scale_max=1.0,
        acceleration_scale_max=1.0,
    )
    envelope = SafetyEnvelope.model_validate(envelope_data)
    intent_data = fixture(ActionIntent)
    intent_data.pop("content_hash")
    intent_data.update(
        semantic_action="JOINT_TRAJECTORY",
        target={
            "kind": "JOINT_TRAJECTORY",
            "frame_id": "base",
            "joint_names": ["joint_0"],
            "trajectory_ref": descriptor.workspace_ref,
        },
        expires_at_sim_time_ns=10_000_000_000,
        constraints={"speed_scale": 1.0, "acceleration_scale": 1.0, "planning_time_ms": 100},
    )
    intent = ActionIntent.model_validate(intent_data)
    prepared_data = fixture(PreparedCommand)
    prepared_data.pop("content_hash")
    command = {
        "schema_version": "1.0.0",
        "command_type": "JOINT_TRAJECTORY",
        "joint_names": ["joint_0"],
        "points": [
            {
                "time_from_start_ns": time,
                "positions_rad": [position],
                "velocities_rad_s": [0.0],
                "accelerations_rad_s2": [0.0],
                "effort_upper_bounds_nm": [1.0],
            }
            for time, position in [(0, 0.0), (2_000_000_000, 0.125)]
        ],
    }
    digest = content_hash(command, exclude=())
    prepared_data.update(
        action_intent_hash=intent.content_hash,
        expires_at_sim_time_ns=10_000_000_000,
        command_payload=command,
        command_ref={
            **prepared_data["command_ref"],
            "digest": digest,
            "uri": f"artifact://sha256/{digest}",
            "size_bytes": len(canonical_json(command)),
        },
    )
    prepared = PreparedCommand.model_validate(prepared_data)
    approval_data = fixture(SimulationEpisodeApproval)
    approval_data.pop("content_hash")
    approval_data["pins"]["safety_envelope_hash"] = envelope.content_hash
    approval = SimulationEpisodeApproval.model_validate(approval_data)
    closure = {name: DIGEST for name in DependencyClosure.model_fields}
    closure.update(
        robot_model_digest=descriptor.robot_model_ref.digest,
        prepared_command_schema_hash=prepared.command_schema_hash,
        adapter_artifact_digest=prepared.adapter_artifact_digest,
        controller_digest=prepared.controller_digest,
    )
    receipt_data = fixture(SafetyDecisionReceipt)
    context = SafetyContext(
        workspace_id=intent.workspace_id,
        project_id=intent.project_id,
        receipt_id=receipt_data["contract_id"],
        now=datetime(2026, 9, 9, 0, 1, tzinfo=UTC),
        state=intent.state,
        sequence=intent.sequence,
        episode=approval.pins,
        closure=DependencyClosure.model_validate(closure),
        approval_hash=approval.content_hash,
        approval_active=True,
        policy_ref=approval.policy_ref,
        lease_active=True,
        lease_expires_at=datetime(2026, 9, 9, 0, 5, tzinfo=UTC),
        last_action_sim_time_ns=0,
        episode_start_sim_time_ns=0,
        budget={"actions": 0, "cumulative_joint_motion_rad": 0.0, "elapsed_sim_seconds": 0.0},
        phase="APPROACH",
        physics_step_ns=1_000_000_000,
        joint_positions_rad=[0.0],
        joint_velocities_rad_s=[0.0],
        joint_accelerations_rad_s2=[0.0],
        gripper_opening_m=0.04,
    )
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    principal = PrincipalRef.model_validate(receipt_data["created_by"])
    signer = SafetySigner(
        key_id="fixture-safety-key",
        principal=principal,
        evaluator=VerifierRef.model_validate(receipt_data["decision"]["evaluator"]),
        private_key=key,
        trusted_key=TrustedSafetyKey(
            principal_id=principal.principal_id, public_key=key.public_key().public_bytes_raw()
        ),
    )
    return Case(intent, prepared, envelope, descriptor, approval, context, signer, FixtureHost())


def command_change(case: Case, mutate: Callable[[dict[str, Any]], None]) -> None:
    payload = case.prepared.command_payload.model_dump(mode="python")
    mutate(payload)
    digest = content_hash(payload, exclude=())
    ref = case.prepared.command_ref.model_dump(mode="python")
    ref.update(
        digest=digest,
        uri=f"artifact://sha256/{digest}",
        size_bytes=len(canonical_json(payload)),
    )
    case.prepared = change(case.prepared, command_payload=payload, command_ref=ref)


def deny(case: Case, reason: str) -> SafetyDecisionReceipt:
    before = case.context.model_dump(mode="json")
    receipt = case.evaluate()
    assert receipt.decision.decision == "DENY"
    assert reason in receipt.decision.reason_codes
    assert receipt.decision.budget_after == receipt.decision.budget_before
    assert before == case.context.model_dump(mode="json")
    verify_safety_signature(receipt, trusted_keys={case.signer.key_id: case.signer.trusted_key})
    return receipt


def test_signed_allow_is_deterministic_and_does_not_mutate_or_reserve(case: Case) -> None:
    before = case.context.model_dump(mode="json")
    first, second = case.evaluate(), case.evaluate()
    assert first == second
    assert first.decision.decision == "ALLOW"
    assert first.decision.budget_after.actions == 1
    assert first.decision.budget_after.cumulative_joint_motion_rad == 0.25
    assert first.decision.budget_after.elapsed_sim_seconds == 2.0
    assert case.context.model_dump(mode="json") == before
    verify_safety_signature(first, trusted_keys={case.signer.key_id: case.signer.trusted_key})


def test_issuance_is_immutable_and_receipt_alone_does_not_cover_phase_drift(case: Case) -> None:
    issuance = case.issuance()
    assert issuance.preview is not None
    assert issuance.preview.request_digest == issuance.request_digest
    assert issuance.preview_digest == content_hash(issuance.preview, exclude=())
    projected = issuance.request
    projected.context.phase = "GRASP"
    assert issuance.request.context.phase == "APPROACH"
    with pytest.raises(FrozenInstanceError):
        issuance.request_bytes = b"{}"  # type: ignore[misc]
    issuance.validate_current_context(case.context)
    later = case.context.model_copy(deep=True)
    later.now += timedelta(seconds=1)
    issuance.validate_current_context(later)
    # Existing state, receipt pins, and signature remain valid, but phase drift
    # changes the contact policy interpretation and must prevent consumption.
    later.phase = "GRASP"
    assert later.state == issuance.receipt.decision.state
    verify_safety_signature(
        issuance.receipt, trusted_keys={case.signer.key_id: case.signer.trusted_key}
    )
    with pytest.raises(RoboticsError):
        issuance.validate_current_context(later)


@pytest.mark.parametrize("mutation", ["physics_step", "budget", "approval", "expired", "clock"])
def test_issuance_consumption_requires_full_current_context(case: Case, mutation: str) -> None:
    issuance = case.issuance()
    current = case.context.model_copy(deep=True)
    if mutation == "physics_step":
        current.physics_step_ns //= 2
    elif mutation == "budget":
        current.budget.actions += 1
    elif mutation == "approval":
        current.approval_active = False
    elif mutation == "expired":
        current.now = case.approval.expires_at
    else:
        current.now -= timedelta(seconds=1)
    with pytest.raises(RoboticsError):
        issuance.validate_current_context(current)


def test_missing_preview_cannot_turn_path_checks_into_allow(case: Case) -> None:
    case.host = None
    deny(case, "PREVIEW_REQUIRED")


@pytest.mark.parametrize(
    "field, value, reason",
    [
        ("approval_active", False, "APPROVAL_INVALID"),
        ("lease_active", False, "LEASE_INVALID"),
        ("sequence", 1, "ACTION_BINDING_MISMATCH"),
        ("approval_hash", DIGEST, "APPROVAL_INVALID"),
        ("project_id", "other-project", "SCOPE_MISMATCH"),
        ("episode_start_sim_time_ns", 1, "CLOCK_REGRESSION"),
        ("last_action_sim_time_ns", 1, "CLOCK_REGRESSION"),
    ],
)
def test_authoritative_snapshot_mismatch_denies_before_host(
    case: Case, field: str, value: Any, reason: str
) -> None:
    setattr(case.context, field, value)
    deny(case, reason)
    assert case.host is not None and case.host.calls == 0


@pytest.mark.parametrize(
    "mutation, reason",
    [
        ("state", "STATE_BINDING_MISMATCH"),
        ("fence", "LEASE_INVALID"),
        ("closure", "CLOSURE_MISMATCH"),
        ("policy", "POLICY_BINDING_MISMATCH"),
        ("approval_expiry", "APPROVAL_INVALID"),
        ("lease_expiry", "LEASE_INVALID"),
        ("before_approval", "APPROVAL_INVALID"),
        ("action_expiry", "ACTION_EXPIRED"),
    ],
)
def test_stale_and_substituted_bindings(case: Case, mutation: str, reason: str) -> None:
    if mutation == "state":
        case.context.state = case.context.state.model_copy(update={"observation_digest": DIGEST})
    elif mutation == "fence":
        lease = case.prepared.lease.model_copy(update={"generation": 2})
        case.prepared = change(case.prepared, lease=lease)
    elif mutation == "closure":
        case.context.closure.controller_digest = DIGEST
    elif mutation == "policy":
        case.context.policy_ref = case.context.policy_ref.model_copy(update={"version": "2.0.0"})
    elif mutation == "approval_expiry":
        case.context.now = case.approval.expires_at
    elif mutation == "lease_expiry":
        case.context.lease_expires_at = case.context.now
    elif mutation == "before_approval":
        case.context.now = case.approval.created_at - timedelta(seconds=1)
    else:
        case.context.state = case.context.state.model_copy(update={"sim_time_ns": 10_000_000_000})
    deny(case, reason)


@pytest.mark.parametrize("bad", [True, False, "1.0", float("nan"), float("inf")])
def test_mutated_current_numbers_refuse_instead_of_coercing(case: Case, bad: Any) -> None:
    case.context.joint_positions_rad[0] = bad
    with pytest.raises(RoboticsError):
        case.evaluate()


def test_mutated_sealed_command_and_unknown_minor_are_refused(case: Case) -> None:
    original = case.prepared.model_copy(deep=True)
    case.prepared.command_payload.points[1].positions_rad[0] = 0.2  # type: ignore[union-attr]
    with pytest.raises(RoboticsError):
        case.evaluate()
    case.prepared = original
    case.intent = change(case.intent, schema_version="1.1.0")
    with pytest.raises(RoboticsError):
        case.evaluate()


def test_subnormal_interior_overshoot_cannot_round_into_an_allow(case: Case) -> None:
    tiny = math.nextafter(0.0, 1.0)
    assert 1.0 + tiny * 2 / 5 == 1.0  # A naive float Bernstein check loses this violation.

    def move(p: dict[str, Any]) -> None:
        for point in p["points"]:
            point["positions_rad"] = [1.0]
        p["points"][0]["velocities_rad_s"] = [tiny]

    command_change(case, move)
    case.context.joint_positions_rad = [1.0]
    case.context.joint_velocities_rad_s = [tiny]
    deny(case, "TRAJECTORY_POSITION_LIMIT")


def test_preview_must_cover_initial_observed_arm_and_gripper(case: Case) -> None:
    case.host = FixtureHost(lambda p: p["intervals"][0]["joints"][0].update(minimum_rad=0.01))
    deny(case, "PREVIEW_INITIAL_STATE_MISMATCH")
    case.host = FixtureHost(
        lambda p: p["intervals"][0]["gripper"].update(
            minimum_opening_m=0.02, maximum_opening_m=0.03
        )
    )
    deny(case, "PREVIEW_INITIAL_GRIPPER_MISMATCH")


@pytest.mark.parametrize("criterion", ["position", "velocity", "acceleration", "effort"])
def test_continuous_desired_trajectory_interior_and_effort(case: Case, criterion: str) -> None:
    if criterion == "position":

        def overshoot(payload: dict[str, Any]) -> None:
            payload["points"][0]["velocities_rad_s"] = [8.0]
            payload["points"][1]["velocities_rad_s"] = [-8.0]
            payload["points"][1]["positions_rad"] = [0.0]

        command_change(case, overshoot)
        case.context.joint_velocities_rad_s = [8.0]
    elif criterion == "effort":
        command_change(case, lambda p: p["points"][1].update(effort_upper_bounds_nm=[31.0]))
    else:
        command_change(case, lambda p: p["points"][1].update(positions_rad=[0.5]))
        limits = [limit.model_dump(mode="python") for limit in case.envelope.joint_limits]
        limits[0]["velocity_rad_s" if criterion == "velocity" else "acceleration_rad_s2"] = (
            1.0 if criterion == "velocity" else 2.0
        )
        case.envelope = change(case.envelope, joint_limits=limits)
        case.rebind()
    deny(case, f"TRAJECTORY_{criterion.upper()}_LIMIT")


def test_desired_trajectory_start_and_joint_inventory_must_match(case: Case) -> None:
    command_change(case, lambda p: p["points"][0].update(positions_rad=[0.1]))
    deny(case, "TRAJECTORY_START_MISMATCH")
    command_change(case, lambda p: p.update(joint_names=["foreign_joint"]))
    deny(case, "JOINT_ORDER_MISMATCH")


@pytest.mark.parametrize(
    "field, value, reason",
    [
        ("minimum_rad", -1.01, "ACTUAL_POSITION_LIMIT"),
        ("speed_upper_rad_s", 2.01, "ACTUAL_VELOCITY_LIMIT"),
        ("acceleration_upper_rad_s2", 5.01, "ACTUAL_ACCELERATION_LIMIT"),
        ("effort_upper_nm", 30.01, "ACTUAL_EFFORT_LIMIT"),
        ("motion_upper_rad", 81.0, "MOTION_BUDGET_EXHAUSTED"),
    ],
)
def test_actual_physics_bounds_are_checked_separately(
    case: Case, field: str, value: float, reason: str
) -> None:
    case.host = FixtureHost(lambda p: p["intervals"][1]["joints"][0].update({field: value}))
    deny(case, reason)


@pytest.mark.parametrize(
    "mutation, reason",
    [
        ("digest", "PREVIEW_BINDING_MISMATCH"),
        ("gap", "PHYSICS_COVERAGE_INCOMPLETE"),
        ("missing_step", "PHYSICS_COVERAGE_INCOMPLETE"),
        ("long_step", "PHYSICS_COVERAGE_INCOMPLETE"),
        ("body", "BODY_COVERAGE_INCOMPLETE"),
        ("joint", "PHYSICS_JOINT_INCOMPLETE"),
        ("geometry", "GEOMETRY_BINDING_MISMATCH"),
        ("intent", "INTENT_NOT_SATISFIED"),
        ("duration", "PREVIEW_DURATION_MISMATCH"),
        ("sampled", "PREVIEW_UNAVAILABLE"),
        ("nan", "PREVIEW_UNAVAILABLE"),
        ("bool", "PREVIEW_UNAVAILABLE"),
    ],
)
def test_preview_never_accepts_missing_or_substituted_proof(
    case: Case, mutation: str, reason: str
) -> None:
    def mutate(p: dict[str, Any]) -> None:
        if mutation == "digest":
            p["request_digest"] = DIGEST
        elif mutation == "gap":
            p["intervals"][1]["start_ns"] += 1
        elif mutation == "missing_step":
            p["intervals"].pop()
        elif mutation == "long_step":
            p["intervals"][0]["end_ns"] += 1
        elif mutation == "body":
            p["intervals"][1]["bodies"].pop()
        elif mutation == "joint":
            p["intervals"][1]["joints"][0]["joint_name"] = "foreign_joint"
        elif mutation == "geometry":
            p["geometry"]["tool_payload_ref"] = case.descriptor.robot_model_ref
        elif mutation == "intent":
            p["intent_satisfied"] = False
        elif mutation == "duration":
            p["duration_ns"] += 1
        elif mutation == "sampled":
            p["coverage"] = "SAMPLED_WAYPOINTS"
        else:
            p["intervals"][0]["joints"][0]["speed_upper_rad_s"] = (
                float("nan") if mutation == "nan" else True
            )

    case.host = FixtureHost(mutate)
    deny(case, reason)


def test_workspace_closed_boundary_and_forbidden_touch(case: Case) -> None:
    case.host = FixtureHost(lambda p: p["intervals"][1]["bodies"][0].update(box=box(-1.0, 1.0)))
    assert case.evaluate().decision.decision == "ALLOW"
    case.host = FixtureHost(lambda p: p["intervals"][1]["bodies"][0].update(box=box(-1.0, 1.001)))
    deny(case, "WORKSPACE_VIOLATION")
    case.envelope = change(case.envelope, forbidden_volume_refs=[case.descriptor.robot_model_ref])
    case.rebind()
    case.host = FixtureHost(lambda p: p["intervals"][1]["bodies"][0].update(box=box(0.7, 0.8)))
    deny(case, "FORBIDDEN_VOLUME_INTERSECTION")


def test_envelope_must_be_contained_by_resolved_model_limits(case: Case) -> None:
    case.host = FixtureHost(lambda p: p["geometry"]["joint_limits"][0].update(effort_nm=20.0))
    deny(case, "ENVELOPE_EXCEEDS_MODEL")
    case.host = FixtureHost(
        lambda p: p["geometry"]["descriptor_workspace"].update(box=box(-0.5, 0.5))
    )
    deny(case, "WORKSPACE_OUTSIDE_MODEL")


def contact() -> dict[str, Any]:
    return {
        "first_body": "finger",
        "second_body": "object",
        "phase": "GRASP",
        "force_upper_n": 2.0,
        "penetration_upper_m": 0.001,
    }


def allow_contact(case: Case) -> None:
    case.envelope = change(
        case.envelope,
        collision_policy="DECLARED_CONTACT_ONLY",
        allowed_contacts=[
            {
                "first_body": "finger",
                "second_body": "object",
                "phases": ["GRASP"],
                "maximum_force_n": 2.0,
                "maximum_penetration_m": 0.001,
            }
        ],
    )
    case.context.phase = "GRASP"
    case.rebind()


def test_contact_requires_exact_pair_phase_force_and_penetration(case: Case) -> None:
    case.host = FixtureHost(lambda p: p["intervals"][1].update(contacts=[contact()]))
    deny(case, "CONTACT_FORBIDDEN")
    allow_contact(case)
    assert case.evaluate().decision.decision == "ALLOW"
    for field, value, reason in [
        ("phase", "TRANSPORT", "CONTACT_UNDECLARED"),
        ("second_body", "other", "CONTACT_UNDECLARED"),
        ("force_upper_n", 2.001, "CONTACT_LIMIT"),
        ("penetration_upper_m", 0.00101, "CONTACT_LIMIT"),
    ]:
        case.host = FixtureHost(
            lambda p, field=field, value=value: p["intervals"][1].update(
                contacts=[{**contact(), field: value}]
            )
        )
        deny(case, reason)
    case.context.phase = "APPROACH"
    case.host = FixtureHost(lambda p: p["intervals"][1].update(contacts=[contact()]))
    deny(case, "CONTACT_UNDECLARED")


@pytest.mark.parametrize("kind", ["actions", "motion", "time", "idle", "elapsed"])
def test_cumulative_and_idle_caps_leave_budget_unchanged(case: Case, kind: str) -> None:
    reasons = {
        "actions": "ACTION_BUDGET_EXHAUSTED",
        "motion": "MOTION_BUDGET_EXHAUSTED",
        "time": "TIME_BUDGET_EXHAUSTED",
        "idle": "NO_ACTION_TIMEOUT",
        "elapsed": "ELAPSED_BUDGET_STALE",
    }
    if kind == "actions":
        case.context.budget.actions = case.envelope.max_actions
    elif kind == "motion":
        case.context.budget.cumulative_joint_motion_rad = 79.875
    elif kind == "time":
        case.context.budget.elapsed_sim_seconds = 119.0
    elif kind == "idle":
        case.context.state = case.context.state.model_copy(update={"sim_time_ns": 5_000_000_000})
    else:
        case.context.state = case.context.state.model_copy(update={"sim_time_ns": 1_000_000_000})
    deny(case, reasons[kind])


def test_gripper_units_and_force_are_independent_from_arm_limits(case: Case) -> None:
    case.intent = change(
        case.intent,
        semantic_action="SET_GRIPPER",
        target={"kind": "SET_GRIPPER", "opening_m": 0.04, "force_n": 10.0},
    )

    def gripper(p: dict[str, Any]) -> None:
        p.clear()
        p.update(
            schema_version="1.0.0",
            command_type="GRIPPER_OPENING",
            opening_m=0.04,
            velocity_m_s=0.01,
            acceleration_m_s2=0.02,
            force_limit_n=10.0,
        )

    command_change(case, gripper)
    case.rebind()
    assert case.evaluate().decision.decision == "ALLOW"
    command_change(case, lambda p: p.update(opening_m=0.5))
    deny(case, "GRIPPER_COMMAND_LIMIT")
    command_change(case, lambda p: p.update(opening_m=0.04))
    case.host = FixtureHost(lambda p: p["intervals"][1]["gripper"].update(force_upper_n=11.0))
    deny(case, "GRIPPER_COMMANDED_FORCE_LIMIT")


def test_signature_cannot_be_forged_by_resealing_or_wrong_trust_identity(case: Case) -> None:
    receipt = case.evaluate()
    data = receipt.model_dump(mode="python")
    data["decision"]["budget_after"]["actions"] += 1
    data["content_hash"] = content_hash(data)
    with pytest.raises(ValidationError):
        SafetyDecisionReceipt.model_validate(data)
    data = receipt.model_dump(mode="python")
    data["decision"]["budget_after"]["elapsed_sim_seconds"] += 1.0
    data["signature"]["unsigned_payload_digest"] = content_hash(data["decision"], exclude=())
    data["content_hash"] = content_hash(data)
    forged = SafetyDecisionReceipt.model_validate(data)
    with pytest.raises(ValueError):
        verify_safety_signature(forged, trusted_keys={case.signer.key_id: case.signer.trusted_key})
    other_key = Ed25519PrivateKey.from_private_bytes(bytes(reversed(range(32))))
    with pytest.raises(ValueError):
        verify_safety_signature(
            receipt,
            trusted_keys={
                case.signer.key_id: TrustedSafetyKey(
                    principal_id=case.signer.principal.principal_id,
                    public_key=other_key.public_key().public_bytes_raw(),
                )
            },
        )
    with pytest.raises(ValueError):
        verify_safety_signature(
            receipt,
            trusted_keys={
                case.signer.key_id: TrustedSafetyKey(
                    principal_id="other-principal", public_key=case.signer.trusted_key.public_key
                )
            },
        )
    case.signer = SafetySigner(
        key_id=case.signer.key_id,
        principal=case.signer.principal,
        evaluator=case.signer.evaluator,
        private_key=other_key,
        trusted_key=case.signer.trusted_key,
    )
    with pytest.raises(RoboticsError):
        case.evaluate()
