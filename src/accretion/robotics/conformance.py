"""SDK construction checks. These cannot activate an adapter in the registry.

Real black-box host and simulator witnesses remain mandatory. In particular,
this runner is not the independent issuer of AdapterConformanceReport.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from accretion.contracts.robotics import EmbodimentDescriptor, ObservationSpec
from accretion.contracts.robotics.values import DependencyClosure, LeaseBinding

from .errors import RoboticsError
from .errors import RoboticsErrorCode as Code
from .observations import ArtifactReader, ObservationValidator
from .protocol import AdapterDescription
from .sdk import RobotAdapter

PENDING_HOST_CASES = (
    "independent_verifier_identity_and_exact_dependency_closure",
    "process_tree_cleanup_and_deadline_enforcement",
    "cpu_memory_pids_output_filesystem_network_and_device_isolation",
    "durable_exclusive_leases_fencing_revocation_and_restart",
    "atomic_current_approval_budget_and_exact_command_consumption",
    "trusted_full_step_dynamics_contact_and_swept_geometry_preview",
    "lost_acknowledgement_after_actual_physics_without_resend",
    "fresh_process_replay_with_complete_verified_artifacts",
    "ur5e_robotiq_and_independent_panda_same_black_box_suite",
    "physical_grasp_lift_transport_release_and_post_release_stability_in_simulation",
)


@dataclass(frozen=True, slots=True)
class ConstructionCase:
    name: str
    passed: bool
    error_code: Code | None = None


@dataclass(frozen=True, slots=True)
class SDKConstructionReport:
    cases: tuple[ConstructionCase, ...]
    scope: Literal["SDK_CONSTRUCTION_ONLY"] = "SDK_CONSTRUCTION_ONLY"

    @property
    def activation_eligible(self) -> Literal[False]:
        return False

    @property
    def pending_host_cases(self) -> tuple[str, ...]:
        return PENDING_HOST_CASES

    @property
    def construction_checks_passed(self) -> bool:
        return bool(self.cases) and all(case.passed for case in self.cases)


class ConformanceRunner:
    def run(
        self,
        adapter: RobotAdapter,
        expected_closure: DependencyClosure,
        *,
        artifact_reader: ArtifactReader,
        episode_id: str,
        lease: LeaseBinding,
    ) -> SDKConstructionReport:
        cases: list[ConstructionCase] = []
        name = "exact_description_and_dependency_closure"
        try:
            description = AdapterDescription.model_validate(
                adapter.describe().model_dump(mode="python")
            )
            descriptor = description.descriptor.for_execution(EmbodimentDescriptor)
            spec = description.observation_spec.for_execution(ObservationSpec)
            if description.dependencies != expected_closure or (
                expected_closure.observation_spec_hash != spec.content_hash
            ):
                raise RoboticsError(Code.CONFORMANCE_STALE)
            cases.append(ConstructionCase(name, True))
            name = "required_observations_and_referenced_bytes"
            validator = ObservationValidator()
            first = validator.validate(
                spec,
                descriptor,
                adapter.observe(),
                artifact_reader,
                episode_id=episode_id,
                lease=lease,
            )
            cases.append(ConstructionCase(name, True))
            name = "observation_does_not_advance_simulation"
            second = validator.validate(
                spec,
                descriptor,
                adapter.observe(),
                artifact_reader,
                episode_id=episode_id,
                lease=lease,
                previous=first.state,
            )
            if second.state != first.state:
                raise RoboticsError(Code.EPISODE_STATE_CONFLICT)
            cases.append(ConstructionCase(name, True))
        except RoboticsError as exc:
            cases.append(ConstructionCase(name, False, exc.code))
        except (ValueError, OSError, RuntimeError):
            cases.append(ConstructionCase(name, False, Code.ADAPTER_CRASH))
        return SDKConstructionReport(tuple(cases))
