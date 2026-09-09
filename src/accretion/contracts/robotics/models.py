"""Canonical simulation records. Construction validates a claim, not its authority.

The registry, gateway and verifier services must resolve references and enforce
actor permissions. No model here connects to a simulator or changes state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import Field, model_validator

from accretion.contracts import EvidenceClass, PrincipalRef, StrictModel
from accretion.contracts.canonical import CanonicalContract, canonical_json, content_hash
from accretion.contracts.refs import CapabilityRef, PolicyRef, VerifierRef

# Importing routing also resolves CanonicalContract's inherited ObjectiveContractRef.
from accretion.contracts.routing import (
    NodeContractRef,
    ObjectiveContractRef,
    RiskClass,
    VerificationState,
)

from .values import (
    ActionConstraints,
    ActionTarget,
    AllowedContact,
    ContactPolicy,
    ContentAddressedArtifactRef,
    ControllerMode,
    DependencyClosure,
    DetachedSignature,
    Digest,
    EndEffectorDescriptor,
    EpisodeId,
    EpisodePins,
    GripperEnvelope,
    Identifier,
    JointLimit,
    Kinematics,
    LeaseBinding,
    MotionBudgetState,
    Nonnegative,
    ObservationField,
    Positive,
    PreparedCommandPayload,
    ProcessBudget,
    ReplayClass,
    ResourceBudget,
    RoboticsContractRef,
    Scale,
    SemanticAction,
    Semver,
    SensorDescriptor,
    SequenceNumber,
    SimulationArtifactRef,
    SimulatorProfile,
    StateBinding,
    TimeAlignment,
    VerifierRequirement,
)

AnnotatedSeed = Annotated[int, Field(ge=0, strict=True)]
AnnotatedReason = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=128)]

SIMULATION_CAPABILITIES = frozenset(
    {
        "robotics.sim.inspect",
        "robotics.sim.observe",
        "robotics.sim.propose_action",
        "robotics.sim.reset",
        "robotics.sim.snapshot",
        "robotics.sim.terminate",
    }
)


class RoboticsContract(CanonicalContract):
    """One shared simulation header; the wire schema also refuses unknown majors."""

    schema_version: str = Field(default="1.0.0", pattern=r"^1\.\d+\.\d+$")


class EmbodimentDescriptor(RoboticsContract):
    CONTRACT_TYPE: ClassVar[str] = "accretion.embodiment-descriptor"
    ID_KIND: ClassVar[str] = "embodiment_descriptor"
    contract_type: Literal["accretion.embodiment-descriptor"] = "accretion.embodiment-descriptor"
    embodiment_id: Identifier
    descriptor_version: Semver
    kind: Literal["MANIPULATOR"] = "MANIPULATOR"
    evidence_class: Literal[EvidenceClass.SIMULATION] = EvidenceClass.SIMULATION
    kinematics: Kinematics
    control_interfaces: list[ControllerMode] = Field(min_length=1)
    sensors: list[SensorDescriptor] = Field(default_factory=list)
    end_effectors: list[EndEffectorDescriptor] = Field(min_length=1, max_length=1)
    workspace_ref: ContentAddressedArtifactRef
    robot_model_ref: ContentAddressedArtifactRef

    @model_validator(mode="after")
    def _unique_interfaces(self) -> Self:
        if len(set(self.control_interfaces)) != len(self.control_interfaces):
            raise ValueError("control interfaces must be unique")
        if len({s.sensor_id for s in self.sensors}) != len(self.sensors):
            raise ValueError("sensor IDs must be unique")
        return self


class ObservationSpec(RoboticsContract):
    CONTRACT_TYPE: ClassVar[str] = "accretion.observation-spec"
    ID_KIND: ClassVar[str] = "observation_spec"
    contract_type: Literal["accretion.observation-spec"] = "accretion.observation-spec"
    required: list[ObservationField] = Field(min_length=1)
    optional: list[ObservationField] = Field(default_factory=list)
    time_alignment: TimeAlignment
    missing_data_policy: Literal["FAIL_EPISODE"] = "FAIL_EPISODE"

    @model_validator(mode="after")
    def _unique_fields(self) -> Self:
        fields = [item.field for item in [*self.required, *self.optional]]
        if len(fields) != len(set(fields)):
            raise ValueError("observation fields must be unique across required and optional")
        return self


class ActionIntent(RoboticsContract):
    CONTRACT_TYPE: ClassVar[str] = "accretion.action-intent"
    ID_KIND: ClassVar[str] = "action_intent"
    contract_type: Literal["accretion.action-intent"] = "accretion.action-intent"
    episode_id: EpisodeId
    sequence: SequenceNumber
    semantic_action: SemanticAction
    target: ActionTarget
    constraints: ActionConstraints
    state: StateBinding
    expires_at_sim_time_ns: SequenceNumber
    idempotency_key: Digest

    @model_validator(mode="after")
    def _intent_binding(self) -> Self:
        if self.semantic_action.value != self.target.kind:
            raise ValueError("semantic action and typed target disagree")
        if self.expires_at_sim_time_ns <= self.state.sim_time_ns:
            raise ValueError("intent must expire after its observed simulation state")
        return self


class SafetyEnvelope(RoboticsContract):
    CONTRACT_TYPE: ClassVar[str] = "accretion.safety-envelope"
    ID_KIND: ClassVar[str] = "safety_envelope"
    contract_type: Literal["accretion.safety-envelope"] = "accretion.safety-envelope"
    embodiment_descriptor_hash: Digest
    controller_modes: list[ControllerMode] = Field(min_length=1)
    joint_limits: list[JointLimit] = Field(min_length=1)
    gripper: GripperEnvelope
    workspace_ref: ContentAddressedArtifactRef
    speed_scale_max: Scale
    acceleration_scale_max: Scale
    joint_limit_margin_rad: Nonnegative
    collision_policy: ContactPolicy = ContactPolicy.STOP_BEFORE_CONTACT
    allowed_contacts: list[AllowedContact] = Field(default_factory=list)
    forbidden_volume_refs: list[ContentAddressedArtifactRef] = Field(default_factory=list)
    tool_payload_ref: ContentAddressedArtifactRef
    max_episode_sim_seconds: Positive
    max_actions: int = Field(gt=0, strict=True)
    max_cumulative_joint_motion_rad: Positive
    no_action_timeout_seconds: Positive
    policy_ref: PolicyRef

    @model_validator(mode="after")
    def _envelope_limits(self) -> Self:
        names = [limit.joint_name for limit in self.joint_limits]
        if len(names) != len(set(names)):
            raise ValueError("safety joint limits must name each joint once")
        if len(set(self.controller_modes)) != len(self.controller_modes):
            raise ValueError("controller modes must be unique")
        if any(
            2 * self.joint_limit_margin_rad >= limit.upper_rad - limit.lower_rad
            for limit in self.joint_limits
        ):
            raise ValueError("joint margin removes the entire permitted range")
        if self.collision_policy is ContactPolicy.STOP_BEFORE_CONTACT and self.allowed_contacts:
            raise ValueError("STOP_BEFORE_CONTACT cannot silently allow contact pairs")
        if (
            self.collision_policy is ContactPolicy.DECLARED_CONTACT_ONLY
            and not self.allowed_contacts
        ):
            raise ValueError("declared contact policy requires explicit pairs and phases")
        contact_phases: set[tuple[str, str, str]] = set()
        for contact in self.allowed_contacts:
            first, second = sorted((contact.first_body, contact.second_body))
            for phase in contact.phases:
                key = (first, second, phase)
                if key in contact_phases:
                    raise ValueError("unordered contact pairs cannot overlap in a phase")
                contact_phases.add(key)
        return self


class RobotAdapterManifest(RoboticsContract):
    """Immutable declaration. Current conformance is a registry join, never a hash cycle."""

    CONTRACT_TYPE: ClassVar[str] = "accretion.robot-adapter-manifest"
    ID_KIND: ClassVar[str] = "robot_adapter_manifest"
    contract_type: Literal["accretion.robot-adapter-manifest"] = "accretion.robot-adapter-manifest"
    adapter_id: Identifier
    adapter_version: Semver
    embodiment_descriptor_hashes: list[Digest] = Field(min_length=1)
    simulator: SimulatorProfile
    capabilities: list[CapabilityRef] = Field(min_length=1)
    observation_spec_hash: Digest
    action_intent_schema_hash: Digest
    prepared_command_schema_hash: Digest
    artifact_digest: Digest
    artifact_signature_ref: ContentAddressedArtifactRef
    controller_modes: list[ControllerMode] = Field(min_length=1)
    process_budget: ProcessBudget
    endpoint_profile_id: Identifier

    @model_validator(mode="after")
    def _simulation_declaration(self) -> Self:
        names = [capability.capability_id for capability in self.capabilities]
        if len(names) != len(set(names)) or not set(names) <= SIMULATION_CAPABILITIES:
            raise ValueError("manifest requests must be unique, known simulation capabilities")
        if len(self.embodiment_descriptor_hashes) != len(set(self.embodiment_descriptor_hashes)):
            raise ValueError("embodiment descriptor hashes must be unique")
        return self


class SimulationExperimentContract(RoboticsContract):
    CONTRACT_TYPE: ClassVar[str] = "accretion.simulation-experiment"
    ID_KIND: ClassVar[str] = "simulation_experiment"
    contract_type: Literal["accretion.simulation-experiment"] = "accretion.simulation-experiment"
    objective_contract_ref: ObjectiveContractRef
    node_contract_ref: NodeContractRef
    embodiment_ref: RoboticsContractRef
    adapter_ref: RoboticsContractRef
    world_artifact_digest: Digest
    controller_artifact_digest: Digest
    safety_envelope_hash: Digest
    verification_spec_hash: Digest
    risk_class: Literal[RiskClass.SIMULATION] = RiskClass.SIMULATION
    seed_set: list[AnnotatedSeed] = Field(min_length=1)
    randomization_spec_ref: ContentAddressedArtifactRef
    resource_budget: ResourceBudget
    replay_class: ReplayClass = ReplayClass.TOLERANT
    tolerance_profile_ref: ContentAddressedArtifactRef
    protocol_ref: ContentAddressedArtifactRef

    @model_validator(mode="after")
    def _experiment_scope(self) -> Self:
        if len(self.seed_set) != len(set(self.seed_set)):
            raise ValueError("seed set must be unique")
        if len(self.seed_set) > self.resource_budget.max_episodes:
            raise ValueError("seed matrix exceeds the experiment episode budget")
        if (
            self.objective_contract_ref.workspace_id != self.workspace_id
            or self.objective_contract_ref.project_id != self.project_id
        ):
            raise ValueError("objective reference must share experiment scope")
        return self


class SimulationEnvironmentSnapshot(RoboticsContract):
    CONTRACT_TYPE: ClassVar[str] = "accretion.simulation-environment-snapshot"
    ID_KIND: ClassVar[str] = "simulation_environment_snapshot"
    contract_type: Literal["accretion.simulation-environment-snapshot"] = (
        "accretion.simulation-environment-snapshot"
    )
    simulator_image_digest: Digest
    world_digest: Digest
    robot_model_digest: Digest
    controller_digest: Digest
    adapter_digest: Digest
    physics_engine: Identifier
    physics_parameters_ref: ContentAddressedArtifactRef
    rendering_parameters_ref: ContentAddressedArtifactRef
    simulator: SimulatorProfile
    host_architecture: Identifier
    gpu_driver: str | None = Field(default=None, max_length=255)
    randomization_sample_ref: ContentAddressedArtifactRef
    environment_profile_hash: Digest
    host_compatibility_profile_hash: Digest


class EpisodeRecord(RoboticsContract):
    """Sealed final capture. Mutable execution/review state is a separate service DTO."""

    CONTRACT_TYPE: ClassVar[str] = "accretion.episode-record"
    ID_KIND: ClassVar[str] = "episode_record"
    contract_type: Literal["accretion.episode-record"] = "accretion.episode-record"
    episode_id: EpisodeId
    run_id: Identifier
    experiment_contract_hash: Digest
    environment_snapshot_hash: Digest
    seed: AnnotatedSeed
    started_at: datetime
    finished_at: datetime
    termination_reason: Literal[
        "TASK_COMPLETE",
        "ACTION_CAP",
        "TIME_CAP",
        "MOTION_CAP",
        "SAFETY_DENIAL",
        "NO_ACTION_TIMEOUT",
        "OBSERVATION_INVALID",
        "CLOCK_REGRESSION",
        "ADAPTER_CRASH",
        "HEARTBEAT_LOST",
        "ACKNOWLEDGEMENT_UNCERTAIN",
        "ARTIFACT_FAILURE",
        "CANCELLED",
    ]
    trajectory_ref: SimulationArtifactRef
    sensor_manifest_ref: SimulationArtifactRef
    action_receipts_ref: SimulationArtifactRef
    safety_events_ref: SimulationArtifactRef
    metrics_ref: SimulationArtifactRef
    provenance_manifest_ref: SimulationArtifactRef
    producer_runtime_ref: Identifier
    producer_principal: PrincipalRef
    evidence_class: Literal[EvidenceClass.SIMULATION] = EvidenceClass.SIMULATION
    replay_class: ReplayClass

    @model_validator(mode="after")
    def _time_order(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("episode finish precedes start")
        return self


class EmbodiedVerificationSpec(RoboticsContract):
    CONTRACT_TYPE: ClassVar[str] = "accretion.embodied-verification-spec"
    ID_KIND: ClassVar[str] = "embodied_verification_spec"
    contract_type: Literal["accretion.embodied-verification-spec"] = (
        "accretion.embodied-verification-spec"
    )
    task_verifiers: list[VerifierRequirement] = Field(min_length=1)
    safety_verifiers: list[VerifierRequirement] = Field(min_length=1)
    reproducibility_verifier: VerifierRequirement
    evidence_completeness_verifier: VerifierRequirement
    optional_model_verifiers: list[VerifierRef] = Field(default_factory=list)
    inconclusive_policy: Literal["HUMAN_REVIEW"] = "HUMAN_REVIEW"
    separate_process_required: Literal[True] = True
    separate_identity_required: Literal[True] = True


class AdapterConformanceReport(RoboticsContract):
    CONTRACT_TYPE: ClassVar[str] = "accretion.adapter-conformance-report"
    ID_KIND: ClassVar[str] = "adapter_conformance_report"
    contract_type: Literal["accretion.adapter-conformance-report"] = (
        "accretion.adapter-conformance-report"
    )
    adapter_manifest_hash: Digest
    dependencies: DependencyClosure
    suite_version: Semver
    suite_artifact_digest: Digest
    tests_total: int = Field(gt=0, strict=True)
    tests_passed: int = Field(ge=0, strict=True)
    result: Literal[VerificationState.PASS, VerificationState.FAIL, VerificationState.INCONCLUSIVE]
    evidence_bundle_ref: SimulationArtifactRef
    verifier: VerifierRef
    verifier_principal: PrincipalRef
    adapter_producer_principal: PrincipalRef
    expires_on_environment_change: Literal[True] = True

    @model_validator(mode="after")
    def _conformance_claim(self) -> Self:
        if self.tests_passed > self.tests_total:
            raise ValueError("passed count exceeds total")
        if self.result is VerificationState.PASS and self.tests_passed != self.tests_total:
            raise ValueError("PASS conformance requires every test to pass")
        if self.verifier_principal.principal_id == self.adapter_producer_principal.principal_id:
            raise ValueError("adapter producer cannot issue its own conformance verdict")
        if self.created_by.principal_id != self.verifier_principal.principal_id:
            raise ValueError("conformance report must name its issuing verifier")
        return self


class SimulationLease(RoboticsContract):
    CONTRACT_TYPE: ClassVar[str] = "accretion.simulation-lease"
    ID_KIND: ClassVar[str] = "simulation_lease"
    contract_type: Literal["accretion.simulation-lease"] = "accretion.simulation-lease"
    episode_id: EpisodeId
    run_id: Identifier
    experiment_contract_hash: Digest
    environment_snapshot_hash: Digest
    adapter_manifest_hash: Digest
    generation: int = Field(gt=0, strict=True)
    resource_id: Identifier
    endpoint_handle: str = Field(pattern=r"^simh_[0-9a-f]{32,64}$")
    owner_principal: PrincipalRef
    acquired_at: datetime
    expires_at: datetime
    heartbeat_timeout_seconds: Positive
    revoked: bool = False

    @model_validator(mode="after")
    def _lease_time(self) -> Self:
        if self.expires_at <= self.acquired_at:
            raise ValueError("lease expiry must follow acquisition")
        return self


class SimulationRunBinding(RoboticsContract):
    """Internal ownership of a real run, not a provider-based dispatch convention."""

    CONTRACT_TYPE: ClassVar[str] = "accretion.simulation-run-binding"
    ID_KIND: ClassVar[str] = "simulation_run_binding"
    contract_type: Literal["accretion.simulation-run-binding"] = "accretion.simulation-run-binding"
    run_id: Identifier
    episode_id: EpisodeId
    experiment_contract_hash: Digest
    owner: Literal["EMBODIED_ORCHESTRATOR"] = "EMBODIED_ORCHESTRATOR"
    orchestrator_principal: PrincipalRef

    @model_validator(mode="after")
    def _binding_actor(self) -> Self:
        if self.orchestrator_principal.principal_id != self.created_by.principal_id:
            raise ValueError("simulation run binding must name its creating orchestrator")
        return self


class PreflightCheck(StrictModel):
    name: Literal[
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
    ]
    passed: bool
    evidence_ref: ContentAddressedArtifactRef
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=128)


class SimulationPreflightReceipt(RoboticsContract):
    CONTRACT_TYPE: ClassVar[str] = "accretion.simulation-preflight"
    ID_KIND: ClassVar[str] = "simulation_preflight"
    contract_type: Literal["accretion.simulation-preflight"] = "accretion.simulation-preflight"
    episode_id: EpisodeId
    experiment_contract_hash: Digest
    environment_snapshot_hash: Digest
    adapter_manifest_hash: Digest
    safety_envelope_hash: Digest
    verification_spec_hash: Digest
    conformance_report_hash: Digest
    lease: LeaseBinding
    seed: AnnotatedSeed
    randomization_sample_hash: Digest
    checks: list[PreflightCheck] = Field(min_length=10, max_length=10)
    result: Literal["PASS", "REJECTED"]
    valid_until: datetime

    @model_validator(mode="after")
    def _complete_preflight(self) -> Self:
        if len({check.name for check in self.checks}) != 10:
            raise ValueError("preflight requires each of the ten named checks exactly once")
        if (self.result == "PASS") != all(check.passed for check in self.checks):
            raise ValueError("preflight result disagrees with checks")
        if self.valid_until <= self.created_at:
            raise ValueError("preflight must have a positive validity interval")
        return self


class SimulationEpisodeApproval(RoboticsContract):
    CONTRACT_TYPE: ClassVar[str] = "accretion.simulation-episode-approval"
    ID_KIND: ClassVar[str] = "simulation_episode_approval"
    contract_type: Literal["accretion.simulation-episode-approval"] = (
        "accretion.simulation-episode-approval"
    )
    pins: EpisodePins
    approved_by: PrincipalRef
    approval_actor_type: Literal["HUMAN"] = "HUMAN"
    policy_ref: PolicyRef
    risk_class: Literal[RiskClass.SIMULATION] = RiskClass.SIMULATION
    decision: Literal["APPROVE"] = "APPROVE"
    expires_at: datetime
    max_consumptions: Literal[1] = 1

    @model_validator(mode="after")
    def _approval_provenance(self) -> Self:
        if self.created_by.principal_id != self.approved_by.principal_id:
            raise ValueError("approval creator and approving human must match")
        if self.expires_at <= self.created_at:
            raise ValueError("approval expiry must follow creation")
        return self


class SimulationApprovalMatrix(RoboticsContract):
    """Finite references to actual exact approvals; never a grant for future trials."""

    CONTRACT_TYPE: ClassVar[str] = "accretion.simulation-approval-matrix"
    ID_KIND: ClassVar[str] = "simulation_approval_matrix"
    contract_type: Literal["accretion.simulation-approval-matrix"] = (
        "accretion.simulation-approval-matrix"
    )
    experiment_contract_hash: Digest
    approval_refs: list[RoboticsContractRef] = Field(min_length=1, max_length=10_000)
    expires_at: datetime

    @model_validator(mode="after")
    def _finite_approvals(self) -> Self:
        ids = [ref.contract_id for ref in self.approval_refs]
        if len(set(ids)) != len(ids) or any(not value.startswith("sea_") for value in ids):
            raise ValueError("matrix must name distinct exact episode approval records")
        if self.expires_at <= self.created_at:
            raise ValueError("approval matrix expiry must follow creation")
        return self


class PreparedCommand(RoboticsContract):
    CONTRACT_TYPE: ClassVar[str] = "accretion.prepared-command"
    ID_KIND: ClassVar[str] = "prepared_command"
    contract_type: Literal["accretion.prepared-command"] = "accretion.prepared-command"
    episode_id: EpisodeId
    action_intent_hash: Digest
    sequence: SequenceNumber
    state: StateBinding
    lease: LeaseBinding
    controller_mode: ControllerMode
    command_ref: ContentAddressedArtifactRef
    command_payload: PreparedCommandPayload
    command_schema_hash: Digest
    controller_digest: Digest
    adapter_artifact_digest: Digest
    expires_at_sim_time_ns: SequenceNumber

    @model_validator(mode="after")
    def _command_expiry(self) -> Self:
        if self.expires_at_sim_time_ns <= self.state.sim_time_ns:
            raise ValueError("prepared command must expire after its observed state")
        serialized = canonical_json(self.command_payload)
        if self.command_ref.digest != content_hash(self.command_payload, exclude=()):
            raise ValueError("command artifact must bind the exact typed prepared payload")
        if self.command_ref.size_bytes != len(serialized):
            raise ValueError("command artifact size must match canonical prepared payload")
        return self


class SafetyDecisionPayload(StrictModel):
    """The entire authority-relevant unsigned message, before detached signing."""

    receipt_id: Identifier
    workspace_id: Identifier
    project_id: Identifier
    episode_id: EpisodeId
    action_intent_hash: Digest
    prepared_command_hash: Digest
    safety_envelope_hash: Digest
    episode_approval_hash: Digest
    policy_ref: PolicyRef
    state: StateBinding
    lease: LeaseBinding
    evaluator: VerifierRef
    evaluator_principal_id: Identifier
    decision: Literal["ALLOW", "DENY"]
    reason_codes: list[AnnotatedReason] = Field(min_length=1)
    budget_before: MotionBudgetState
    budget_after: MotionBudgetState
    issued_at: datetime
    expires_at_sim_time_ns: SequenceNumber

    @model_validator(mode="after")
    def _decision_bounds(self) -> Self:
        # canonical_json also rejects naive timestamps and non-finite values.
        canonical_json(self)
        if self.expires_at_sim_time_ns <= self.state.sim_time_ns:
            raise ValueError("safety decision must expire after its observed state")
        before, after = self.budget_before, self.budget_after
        if (
            after.actions < before.actions
            or after.cumulative_joint_motion_rad < before.cumulative_joint_motion_rad
            or after.elapsed_sim_seconds < before.elapsed_sim_seconds
        ):
            raise ValueError("safety admission cannot refund consumed budget")
        if self.decision == "DENY" and before != after:
            raise ValueError("denied command cannot reserve motion or action budget")
        if self.decision == "ALLOW" and after.actions != before.actions + 1:
            raise ValueError("an admitted action must reserve exactly one action")
        return self


class SafetyDecisionReceipt(RoboticsContract):
    CONTRACT_TYPE: ClassVar[str] = "accretion.safety-decision"
    ID_KIND: ClassVar[str] = "safety_decision"
    contract_type: Literal["accretion.safety-decision"] = "accretion.safety-decision"
    decision: SafetyDecisionPayload
    signature: DetachedSignature

    @model_validator(mode="after")
    def _signed_body_binding(self) -> Self:
        if (
            self.decision.receipt_id != self.contract_id
            or self.decision.workspace_id != self.workspace_id
            or self.decision.project_id != self.project_id
            or self.decision.issued_at != self.created_at
            or self.decision.evaluator_principal_id != self.created_by.principal_id
            or self.signature.signer_principal_id != self.created_by.principal_id
        ):
            raise ValueError("signed safety body must bind the receipt identity, scope and actor")
        if self.signature.unsigned_payload_digest != content_hash(self.decision, exclude=()):
            raise ValueError("safety signature does not name this unsigned payload")
        return self


class SimulationActionReceipt(RoboticsContract):
    CONTRACT_TYPE: ClassVar[str] = "accretion.simulation-action-receipt"
    ID_KIND: ClassVar[str] = "simulation_action_receipt"
    contract_type: Literal["accretion.simulation-action-receipt"] = (
        "accretion.simulation-action-receipt"
    )
    episode_id: EpisodeId
    sequence: SequenceNumber
    idempotency_key: Digest
    action_intent_hash: Digest
    prepared_command_hash: Digest
    safety_decision_hash: Digest
    lease: LeaseBinding
    status: Literal["ADMITTED", "DENIED", "ACKNOWLEDGED", "UNKNOWN", "FAILED"]
    adapter_ack_ref: SimulationArtifactRef | None = None
    resulting_state: StateBinding | None = None
    reason_code: AnnotatedReason

    @model_validator(mode="after")
    def _ack_evidence(self) -> Self:
        if self.status == "ACKNOWLEDGED" and (
            self.adapter_ack_ref is None or self.resulting_state is None
        ):
            raise ValueError("acknowledged command requires adapter and state evidence")
        if self.status in {"DENIED", "UNKNOWN"} and self.resulting_state is not None:
            raise ValueError("denied or uncertain action cannot claim a resulting state")
        return self


class EmbodiedVerificationResult(RoboticsContract):
    CONTRACT_TYPE: ClassVar[str] = "accretion.embodied-verification-result"
    ID_KIND: ClassVar[str] = "embodied_verification_result"
    contract_type: Literal["accretion.embodied-verification-result"] = (
        "accretion.embodied-verification-result"
    )
    episode_id: EpisodeId
    episode_record_hash: Digest
    verification_spec_hash: Digest
    verifier: VerifierRef
    verifier_principal: PrincipalRef
    producer_principal: PrincipalRef
    verifier_process_id: Identifier
    producer_process_id: Identifier
    role: Literal["TASK", "SAFETY", "REPLAY", "COMPLETENESS", "MODEL_REVIEW"]
    status: Literal[
        VerificationState.PASS,
        VerificationState.FAIL,
        VerificationState.INCONCLUSIVE,
        VerificationState.ERROR,
    ]
    deterministic: bool
    evidence_refs: list[SimulationArtifactRef] = Field(min_length=1)
    findings_ref: SimulationArtifactRef
    replay_class: ReplayClass | None = None

    @model_validator(mode="after")
    def _independent_identity(self) -> Self:
        if (
            self.producer_principal.principal_id == self.verifier_principal.principal_id
            or self.producer_process_id == self.verifier_process_id
        ):
            raise ValueError("embodied verification needs a separate process and identity")
        if self.created_by.principal_id != self.verifier_principal.principal_id:
            raise ValueError("verification result must name its issuing verifier")
        if self.role != "MODEL_REVIEW" and not self.deterministic:
            raise ValueError("required embodied verifier roles are deterministic")
        if self.role == "REPLAY" and self.replay_class is None:
            raise ValueError("replay verdict must name the evaluated replay class")
        return self


class SimulationDomainEvent(RoboticsContract):
    """Canonical domain event; a trace projection is not a second source of state."""

    CONTRACT_TYPE: ClassVar[str] = "accretion.simulation-domain-event"
    ID_KIND: ClassVar[str] = "simulation_domain_event"
    DERIVED_HASH_FIELDS: ClassVar[tuple[str, ...]] = ("payload_hash",)
    contract_type: Literal["accretion.simulation-domain-event"] = (
        "accretion.simulation-domain-event"
    )
    event_type: Literal[
        "simulation_contract.registered",
        "simulation_run.bound",
        "simulation_authority.policy_changed",
        "simulation_authority.conformance_changed",
        "simulation_lease.acquired",
        "simulation_lease.heartbeat",
        "simulation_lease.expired",
        "simulation_lease.revoked",
        "simulation_lease.released",
        "simulation_lease.cleaned",
        "simulation_approval.created",
        "simulation_approval.revoked",
        "simulation_approval.consumed",
        "simulation_safety.issued",
        "simulation_dispatch.completed",
        "simulation_dispatch.uncertain",
        "embodiment.registered",
        "robot_adapter.registered",
        "robot_adapter.conformance_completed",
        "simulation_experiment.created",
        "simulation_preflight.completed",
        "simulation_episode.started",
        "simulation_action.proposed",
        "simulation_action.denied",
        "simulation_action.admitted",
        "simulation_episode.terminated",
        "simulation_verification.completed",
        "simulation_episode.quarantined",
    ]
    occurred_at: datetime
    run_id: Identifier | None = None
    node_id: Identifier | None = None
    episode_id: EpisodeId | None = None
    correlation_id: Identifier
    causation_id: Identifier | None = None
    producer: PrincipalRef
    sequence: SequenceNumber
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_hash: str = ""

    def seal_derived_hashes(self) -> None:
        if len(canonical_json(self.payload)) > 65_536:
            raise ValueError("domain events carry bounded metadata, not sensor payloads")
        expected = content_hash(self.payload, exclude=())
        if self.payload_hash and self.payload_hash != expected:
            raise ValueError("event payload digest does not match")
        self.payload_hash = expected

    @model_validator(mode="after")
    def _event_actor(self) -> Self:
        if self.producer.principal_id != self.created_by.principal_id:
            raise ValueError("domain event producer must match its creator")
        if self.node_id is not None and self.run_id is None:
            raise ValueError("node-scoped event requires its run")
        return self


class EpisodeQuarantineRecord(RoboticsContract):
    CONTRACT_TYPE: ClassVar[str] = "accretion.episode-quarantine"
    ID_KIND: ClassVar[str] = "episode_quarantine"
    contract_type: Literal["accretion.episode-quarantine"] = "accretion.episode-quarantine"
    episode_id: EpisodeId
    episode_record_hash: Digest
    contradiction_evidence_ref: SimulationArtifactRef
    affected_experience_ids: list[Identifier] = Field(default_factory=list)
    affected_snapshot_ids: list[Identifier] = Field(default_factory=list)
    reason_code: AnnotatedReason
    resolves_quarantine_ref: RoboticsContractRef | None = None
    resolution_evidence_ref: SimulationArtifactRef | None = None

    @model_validator(mode="after")
    def _resolution_is_evidenced(self) -> Self:
        if (self.resolves_quarantine_ref is None) != (self.resolution_evidence_ref is None):
            raise ValueError("quarantine resolution needs both its original record and evidence")
        return self


CORE_CONTRACT_INVENTORY: tuple[type[CanonicalContract], ...] = (
    EmbodimentDescriptor,
    ObservationSpec,
    ActionIntent,
    SafetyEnvelope,
    RobotAdapterManifest,
    SimulationExperimentContract,
    SimulationEnvironmentSnapshot,
    EpisodeRecord,
    EmbodiedVerificationSpec,
    AdapterConformanceReport,
)

CONTRACT_INVENTORY: tuple[type[CanonicalContract], ...] = (
    *CORE_CONTRACT_INVENTORY,
    SimulationLease,
    SimulationRunBinding,
    SimulationPreflightReceipt,
    SimulationEpisodeApproval,
    SimulationApprovalMatrix,
    PreparedCommand,
    SafetyDecisionReceipt,
    SimulationActionReceipt,
    EmbodiedVerificationResult,
    SimulationDomainEvent,
    EpisodeQuarantineRecord,
)
