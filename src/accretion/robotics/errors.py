"""Stable, safe-to-display failures shared by simulation services and the API.

Internal exceptions may be chained for diagnostics, but their text never becomes
an API/event error message. The public vocabulary contains no host path, endpoint
or caller-supplied diagnostic string.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RoboticsErrorCode(StrEnum):
    SIMULATION_UNAVAILABLE = "SIMULATION_UNAVAILABLE"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    CAPABILITY_DENIED = "CAPABILITY_DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    IDEMPOTENCY_REQUIRED = "IDEMPOTENCY_REQUIRED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_CONTRACT = "INVALID_CONTRACT"
    CONTRACT_CONFLICT = "CONTRACT_CONFLICT"
    UNKNOWN_CONTRACT_VERSION = "UNKNOWN_CONTRACT_VERSION"
    CONFORMANCE_STALE = "CONFORMANCE_STALE"
    LEASE_BUSY = "LEASE_BUSY"
    LEASE_INVALID = "LEASE_INVALID"
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"
    SAFETY_DENIED = "SAFETY_DENIED"
    OBSERVATION_INVALID = "OBSERVATION_INVALID"
    CLOCK_REGRESSION = "CLOCK_REGRESSION"
    OBSERVATION_SKEW = "OBSERVATION_SKEW"
    ADAPTER_CRASH = "ADAPTER_CRASH"
    HEARTBEAT_LOST = "HEARTBEAT_LOST"
    ACKNOWLEDGEMENT_UNCERTAIN = "ACKNOWLEDGEMENT_UNCERTAIN"
    ARTIFACT_UNAVAILABLE = "ARTIFACT_UNAVAILABLE"
    ARTIFACT_INVALID = "ARTIFACT_INVALID"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    RESOURCE_CAP_EXHAUSTED = "RESOURCE_CAP_EXHAUSTED"
    EPISODE_STATE_CONFLICT = "EPISODE_STATE_CONFLICT"
    VERIFICATION_INCONCLUSIVE = "VERIFICATION_INCONCLUSIVE"
    VERIFIER_UNAVAILABLE = "VERIFIER_UNAVAILABLE"
    EVIDENCE_QUARANTINED = "EVIDENCE_QUARANTINED"
    PHYSICAL_ENDPOINT_DENIED = "PHYSICAL_ENDPOINT_DENIED"
    ISOLATION_UNAVAILABLE = "ISOLATION_UNAVAILABLE"
    REPLAY_FAILED = "REPLAY_FAILED"


@dataclass(frozen=True)
class _Disposition:
    status_code: int
    message: str
    recovery_action: str


_REFRESH = "Refresh the resource and review its current state before retrying."
_NEW_EPISODE = "Preserve this episode; reset and obtain approval for a new episode."
_REVIEW = "Review the recorded evidence and resolve the cause before retrying."

_DISPOSITIONS: dict[RoboticsErrorCode, _Disposition] = {
    RoboticsErrorCode.SIMULATION_UNAVAILABLE: _Disposition(
        409, "Simulation services are unavailable.", "Configure an approved simulation worker."
    ),
    RoboticsErrorCode.RESOURCE_NOT_FOUND: _Disposition(
        404, "The simulation resource was not found.", "Check the selected project and resource."
    ),
    RoboticsErrorCode.CAPABILITY_DENIED: _Disposition(
        403, "This operation is not permitted.", "Request the required project permission."
    ),
    RoboticsErrorCode.APPROVAL_REQUIRED: _Disposition(
        403, "This episode needs approval.", "Review and approve the exact prepared episode."
    ),
    RoboticsErrorCode.APPROVAL_INVALID: _Disposition(
        403,
        "The episode approval is no longer valid.",
        "Repeat preflight and review a new approval.",
    ),
    RoboticsErrorCode.REVISION_REQUIRED: _Disposition(
        428, "A resource revision is required.", _REFRESH
    ),
    RoboticsErrorCode.REVISION_CONFLICT: _Disposition(
        412, "The resource changed after it was read.", _REFRESH
    ),
    RoboticsErrorCode.IDEMPOTENCY_REQUIRED: _Disposition(
        428, "An idempotency key is required.", "Submit the operation with a stable request key."
    ),
    RoboticsErrorCode.IDEMPOTENCY_CONFLICT: _Disposition(
        409, "This request key already identifies a different operation.", _REFRESH
    ),
    RoboticsErrorCode.INVALID_REQUEST: _Disposition(
        422,
        "The request does not match the simulation API contract.",
        "Correct the request fields.",
    ),
    RoboticsErrorCode.INVALID_CONTRACT: _Disposition(
        422,
        "The simulation contract is invalid or incomplete.",
        "Review its fields and references.",
    ),
    RoboticsErrorCode.CONTRACT_CONFLICT: _Disposition(
        409, "An immutable contract already uses this identity.", "Register a new contract version."
    ),
    RoboticsErrorCode.UNKNOWN_CONTRACT_VERSION: _Disposition(
        422, "This contract version is not supported.", "Use a supported contract version."
    ),
    RoboticsErrorCode.CONFORMANCE_STALE: _Disposition(
        409,
        "Adapter conformance does not cover the current environment.",
        "Run conformance for the exact adapter and environment.",
    ),
    RoboticsErrorCode.LEASE_BUSY: _Disposition(
        409, "The simulator is already leased.", "Wait for the current lease to finish."
    ),
    RoboticsErrorCode.LEASE_INVALID: _Disposition(
        409, "The simulator lease is no longer valid.", _NEW_EPISODE
    ),
    RoboticsErrorCode.PREFLIGHT_FAILED: _Disposition(
        409, "The episode did not pass preflight.", "Resolve the recorded preflight failures."
    ),
    RoboticsErrorCode.SAFETY_DENIED: _Disposition(
        403, "The proposed action was denied by the safety evaluator.", _REVIEW
    ),
    RoboticsErrorCode.OBSERVATION_INVALID: _Disposition(
        409, "A required observation is invalid or missing.", _NEW_EPISODE
    ),
    RoboticsErrorCode.CLOCK_REGRESSION: _Disposition(
        409, "The simulator clock moved backwards.", _NEW_EPISODE
    ),
    RoboticsErrorCode.OBSERVATION_SKEW: _Disposition(
        409, "Observation timing exceeds the admitted tolerance.", _NEW_EPISODE
    ),
    RoboticsErrorCode.ADAPTER_CRASH: _Disposition(
        409, "The robot adapter stopped unexpectedly.", _NEW_EPISODE
    ),
    RoboticsErrorCode.HEARTBEAT_LOST: _Disposition(
        409, "The simulation worker stopped responding.", _NEW_EPISODE
    ),
    RoboticsErrorCode.ACKNOWLEDGEMENT_UNCERTAIN: _Disposition(
        409, "The action outcome is uncertain; the command will not be resent.", _NEW_EPISODE
    ),
    RoboticsErrorCode.ARTIFACT_UNAVAILABLE: _Disposition(
        409, "Required episode evidence is unavailable.", _REVIEW
    ),
    RoboticsErrorCode.ARTIFACT_INVALID: _Disposition(
        409, "Episode evidence failed integrity validation.", _REVIEW
    ),
    RoboticsErrorCode.PAYLOAD_TOO_LARGE: _Disposition(
        413, "The simulation payload exceeds its allowed size.", "Use a bounded artifact payload."
    ),
    RoboticsErrorCode.RESOURCE_CAP_EXHAUSTED: _Disposition(
        409, "The episode exhausted an admitted resource limit.", _NEW_EPISODE
    ),
    RoboticsErrorCode.EPISODE_STATE_CONFLICT: _Disposition(
        409, "This operation is not valid in the episode's current state.", _REFRESH
    ),
    RoboticsErrorCode.VERIFICATION_INCONCLUSIVE: _Disposition(
        409, "Independent verification needs human review.", _REVIEW
    ),
    RoboticsErrorCode.VERIFIER_UNAVAILABLE: _Disposition(
        409, "A required independent verifier is unavailable.", _REVIEW
    ),
    RoboticsErrorCode.EVIDENCE_QUARANTINED: _Disposition(
        409, "This evidence is quarantined and cannot be reused.", _REVIEW
    ),
    RoboticsErrorCode.PHYSICAL_ENDPOINT_DENIED: _Disposition(
        403,
        "The selected endpoint is outside the simulation boundary.",
        "Select an approved simulation worker.",
    ),
    RoboticsErrorCode.ISOLATION_UNAVAILABLE: _Disposition(
        409, "Required worker isolation is unavailable.", "Restore the approved isolation profile."
    ),
    RoboticsErrorCode.REPLAY_FAILED: _Disposition(
        409, "Replay did not meet the declared verification requirements.", _REVIEW
    ),
}


class RoboticsError(Exception):
    """A typed domain refusal; constructor accepts no arbitrary diagnostic text."""

    def __init__(self, code: RoboticsErrorCode) -> None:
        self.code = RoboticsErrorCode(code)
        disposition = _DISPOSITIONS[self.code]
        self.status_code = disposition.status_code
        self.message = disposition.message
        self.recovery_action = disposition.recovery_action
        super().__init__(self.message)

    def public_detail(self) -> dict[str, str]:
        return {
            "code": self.code.value,
            "message": self.message,
            "recovery_action": self.recovery_action,
        }
