"""Bounded ten-check preflight evidence assembly; no operational authority.

Deployment supplies one trusted implementation for each named SDD precondition.
Implementations resolve/check actual scoped registry, host, quota and verifier
state; they must raise PreflightRefusal when proof is absent. These callbacks are
trusted application code, never deserialized client-selected checker names.
This module verifies the complete returned evidence and its exact planned scope.
It does not infer attestation from a blob, manufacture a canonical PASS receipt,
consume approval, allocate a lease or move an episode into RUNNING.

The later episode service must authenticate these implementations and commit a
receipt only after current authority/freshness checks. A pure assessment is not
a substitute for that transaction or for an actual independent conformance run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import AwareDatetime, Field, model_validator

from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.robotics.values import (
    DependencyClosure,
    Digest,
    Identifier,
    SimulationArtifactRef,
)
from accretion.robotics.errors import RoboticsError
from accretion.robotics.observations import ArtifactReader, verified_chunks
from accretion.robotics.protocol import MAX_FRAME_BYTES, WireModel, bounded_json

from .worker import PlannedEpisodePins


class PreflightName(StrEnum):
    CONTRACT_HASHES = "CONTRACT_HASHES"
    CONFORMANCE = "CONFORMANCE"
    ENVIRONMENT = "ENVIRONMENT"
    SEED_RANDOMIZATION = "SEED_RANDOMIZATION"
    OBSERVATIONS = "OBSERVATIONS"
    SAFETY_ENVELOPE = "SAFETY_ENVELOPE"
    INDEPENDENT_VERIFIERS = "INDEPENDENT_VERIFIERS"
    QUOTA_LEASE = "QUOTA_LEASE"
    SIMULATION_ENDPOINT = "SIMULATION_ENDPOINT"
    WORKSPACE_ARTIFACT_STORE = "WORKSPACE_ARTIFACT_STORE"


class PreflightPlan(WireModel):
    """Immutable planned scope, not an authenticated current-state projection."""

    format: Literal["accretion.preflight-plan.v1"] = "accretion.preflight-plan.v1"
    workspace_id: Identifier
    project_id: Identifier
    run_id: Identifier
    episode: PlannedEpisodePins
    dependencies: DependencyClosure
    now: AwareDatetime
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def _interval(self) -> PreflightPlan:
        canonical_json(self)
        if not 0 < (self.valid_until - self.now).total_seconds() <= 3600:
            raise ValueError("preflight assessment interval must be positive and at most one hour")
        return self

    @property
    def digest(self) -> str:
        return content_hash(self, exclude=())


class PreflightProof(WireModel):
    """Evidence returned only after a configured checker completed its checks.

    There is deliberately no passed=True field or freeform executable location.
    Referenced source bytes must exist and be completely verified. Authenticity
    comes from the configured checker, never these source references alone.
    """

    format: Literal["accretion.preflight-check-proof.v1"] = "accretion.preflight-check-proof.v1"
    name: PreflightName
    plan_digest: Digest
    observed_at: AwareDatetime
    valid_until: AwareDatetime
    source_refs: tuple[SimulationArtifactRef, ...] = Field(min_length=1, max_length=32)
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=128)


class PreflightRefusal(Exception):
    def __init__(self, reason_code: str):
        # Reuse the strict proof vocabulary rather than rendering exception text
        # that could contain source data, filesystem paths or credentials.
        if (
            not reason_code
            or len(reason_code) > 128
            or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in reason_code)
        ):
            raise ValueError("invalid preflight reason code")
        self.reason_code = reason_code
        super().__init__(reason_code)


class PreflightChecker(Protocol):
    def evaluate(self, plan: PreflightPlan) -> PreflightProof:
        """Check this named real precondition; refuse absent/invalid/untrusted proof.

        CONTRACT_HASHES resolves every original contract/reference with exact
        writer versions, seals and scope. CONFORMANCE authenticates the current
        exact report/closure. ENVIRONMENT checks actual image/model/controller
        and simulator constraints. SEED_RANDOMIZATION checks frozen seed/sample.
        OBSERVATIONS validates actual referenced fields, SI units and alignment.
        SAFETY_ENVELOPE resolves compatible physical limits/workspace/geometry.
        INDEPENDENT_VERIFIERS checks preselected installed implementations and
        independent identities. QUOTA_LEASE rechecks current owned fence/budgets.
        SIMULATION_ENDPOINT verifies the actual nonphysical inspected host.
        WORKSPACE_ARTIFACT_STORE checks scoped write and exact readback witnesses.

        No generic capability/configuration label can stand in for those checks.
        Each implementation must retain its supporting exact source references.
        """
        ...


@dataclass(frozen=True, slots=True)
class PreflightFinding:
    name: PreflightName
    satisfied: bool
    reason_code: str
    proof_bytes: bytes | None = None


@dataclass(frozen=True, slots=True)
class PreflightAssessment:
    plan_bytes: bytes
    findings: tuple[PreflightFinding, ...]
    valid_until: datetime
    scope: Literal["PREFLIGHT_FOUNDATION_ONLY"] = "PREFLIGHT_FOUNDATION_ONLY"

    @property
    def all_checks_satisfied(self) -> bool:
        return len(self.findings) == len(PreflightName) and all(
            finding.satisfied for finding in self.findings
        )

    @property
    def activation_eligible(self) -> Literal[False]:
        return False


class PreflightBuilder:
    """Assemble all ten named proofs with bounded, independently verified bytes."""

    def __init__(
        self,
        checkers: Mapping[PreflightName, PreflightChecker],
        *,
        max_source_bytes: int = 64 * 1024 * 1024,
        max_source_artifacts: int = 320,
    ):
        if any(type(name) is not PreflightName for name in checkers):
            raise ValueError("preflight checker names must be exact SDD preconditions")
        if type(max_source_bytes) is not int or not 0 < max_source_bytes <= 128 * 1024 * 1024:
            raise ValueError("preflight sources require a bounded byte budget")
        if type(max_source_artifacts) is not int or not 0 < max_source_artifacts <= 320:
            raise ValueError("preflight sources require a bounded artifact budget")
        self._checkers = tuple((name, checkers.get(name)) for name in PreflightName)
        self.max_source_bytes, self.max_source_artifacts = max_source_bytes, max_source_artifacts

    def assess(self, plan: PreflightPlan, *, source: ArtifactReader) -> PreflightAssessment:
        plan_bytes = canonical_json(plan)
        pinned = PreflightPlan.model_validate(bounded_json(plan_bytes))
        findings: list[PreflightFinding] = []
        verified: dict[str, bytes] = {}
        charged: dict[str, bytes] = {}
        charged_bytes = 0
        valid_until = pinned.valid_until
        for name, checker in self._checkers:
            raw: bytes | None = None
            try:
                if checker is None:
                    raise PreflightRefusal("REQUIRED_CHECK_UNAVAILABLE")
                # A callback never receives the mutable plan used by later checks.
                proof = checker.evaluate(PreflightPlan.model_validate_json(plan_bytes))
                if not isinstance(proof, PreflightProof):
                    raise PreflightRefusal("INVALID_CHECK_RESULT")
                raw = canonical_json(proof)
                proof = PreflightProof.model_validate(bounded_json(raw, max_bytes=MAX_FRAME_BYTES))
                if proof.name is not name or proof.plan_digest != pinned.digest:
                    raise PreflightRefusal("PREFLIGHT_PROOF_BINDING_MISMATCH")
                if not proof.observed_at <= pinned.now < proof.valid_until <= pinned.valid_until:
                    raise PreflightRefusal("PREFLIGHT_PROOF_STALE")
                for ref in proof.source_refs:
                    reference_bytes = canonical_json(ref)
                    if ref.digest in charged:
                        if charged[ref.digest] != reference_bytes:
                            raise PreflightRefusal("PREFLIGHT_ARTIFACT_METADATA_CONFLICT")
                        if ref.digest not in verified:
                            raise PreflightRefusal("PREFLIGHT_ARTIFACT_PREVIOUSLY_FAILED")
                        continue
                    if (
                        len(charged) >= self.max_source_artifacts
                        or charged_bytes + ref.size_bytes > self.max_source_bytes
                    ):
                        raise PreflightRefusal("PREFLIGHT_EVIDENCE_BUDGET_EXHAUSTED")
                    # Charge before the reader can allocate; failures do not refund.
                    charged_bytes += ref.size_bytes
                    charged[ref.digest] = reference_bytes
                    for _ in verified_chunks(source, ref, max_bytes=ref.size_bytes):
                        pass
                    verified[ref.digest] = reference_bytes
                valid_until = min(valid_until, proof.valid_until)
                findings.append(PreflightFinding(name, True, proof.reason_code, raw))
            except PreflightRefusal as exc:
                findings.append(PreflightFinding(name, False, exc.reason_code, raw))
            except RoboticsError as exc:
                findings.append(PreflightFinding(name, False, exc.code.value, raw))
            except Exception:
                # A failed checker is retained as refusal; no default PASS and no
                # sensitive exception detail enters the exported assessment.
                findings.append(PreflightFinding(name, False, "PREFLIGHT_CHECK_FAILED", raw))
        return PreflightAssessment(plan_bytes, tuple(findings), valid_until)
