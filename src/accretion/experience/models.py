from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from accretion.contracts import Provider, StrictModel, TaskType

EXPERIENCE_SCHEMA_VERSION = "2.0"
EXPERIENCE_EMBEDDING_VERSION = "deterministic-hybrid-384-v1"
EXPERIENCE_VECTOR_DIMENSIONS = 384


class ExperienceSourceKind(StrEnum):
    RUN = "RUN"
    CANDIDATE = "CANDIDATE"


class ExperienceTrust(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ExperiencePolarity(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"


class TrajectorySegmentKind(StrEnum):
    WORKFLOW_PATH = "WORKFLOW_PATH"
    TOOL_SEQUENCE = "TOOL_SEQUENCE"
    VERIFIER_FINDINGS = "VERIFIER_FINDINGS"
    REPAIR_PATTERN = "REPAIR_PATTERN"
    FAILURE_PATTERN = "FAILURE_PATTERN"
    ARTIFACT_SHAPE = "ARTIFACT_SHAPE"


class MatchDisposition(StrEnum):
    ACCEPTED = "ACCEPTED"
    DOWNRANKED = "DOWNRANKED"
    REJECTED = "REJECTED"


class ModerationActionType(StrEnum):
    RETRACT = "RETRACT"


class SeedValidationStatus(StrEnum):
    PENDING = "PENDING"
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"
    USED = "USED"


class Experience(StrictModel):
    """Immutable, redacted evidence derived from one terminal local trajectory."""

    schema_version: Literal["2.0"] = "2.0"
    experience_id: str
    project_id: str
    repository_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str
    task_type: TaskType
    task_family: str = Field(min_length=1, max_length=160)
    source_kind: ExperienceSourceKind
    source_run_id: str
    source_candidate_id: str | None = None
    source_commit: str = Field(min_length=7, max_length=128)
    architecture_version: str = Field(min_length=1, max_length=64)
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_paths: list[str] = Field(default_factory=list, max_length=256)
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: Provider
    runtime_model: str = Field(min_length=1, max_length=160)
    runtime_version: str = Field(min_length=1, max_length=160)
    trust: ExperienceTrust
    polarity: ExperiencePolarity
    outcome: str = Field(min_length=1, max_length=160)
    failure_taxonomy: list[str] = Field(default_factory=list, max_length=32)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(default=1, ge=1)
    retracted: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_source_and_trust(self) -> Experience:
        if (self.source_kind is ExperienceSourceKind.CANDIDATE) != (
            self.source_candidate_id is not None
        ):
            raise ValueError("candidate experiences require exactly one source candidate")
        if self.polarity is ExperiencePolarity.POSITIVE and self.trust is not ExperienceTrust.HIGH:
            raise ValueError("positive replay evidence must have HIGH trust")
        if self.polarity is ExperiencePolarity.NEGATIVE and self.trust is ExperienceTrust.HIGH:
            raise ValueError("negative guidance cannot have HIGH trust")
        return self


class TrajectorySegment(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    segment_id: str
    experience_id: str
    ordinal: int = Field(ge=1, le=64)
    kind: TrajectorySegmentKind
    content: dict[str, Any]
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExperienceEmbedding(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    embedding_id: str
    experience_id: str
    version: Literal["deterministic-hybrid-384-v1"] = "deterministic-hybrid-384-v1"
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    vector: list[float] = Field(min_length=EXPERIENCE_VECTOR_DIMENSIONS, max_length=384)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExperienceQuery(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    query_id: str
    project_id: str
    task_id: str
    task_profile_id: str
    repository_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_commit: str = Field(min_length=7, max_length=128)
    architecture_version: str = Field(min_length=1, max_length=64)
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_paths: list[str] = Field(default_factory=list, max_length=256)
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_provider: Provider | None = None
    runtime_model: str | None = None
    runtime_version: str | None = None
    include_failures: bool = True
    top_k: int = Field(default=5, ge=1, le=10)
    max_age_days: int | None = Field(default=None, ge=1, le=3650)
    embedding_version: Literal["deterministic-hybrid-384-v1"] = (
        "deterministic-hybrid-384-v1"
    )
    embedding_input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CompatibilityAssessment(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    semantic_score: float = Field(ge=0, le=1)
    environment_score: float = Field(ge=0, le=1)
    version_score: float = Field(ge=0, le=1)
    freshness_score: float = Field(ge=0, le=1)
    final_score: float = Field(ge=0, le=1)
    transfer_risk: float = Field(ge=0, le=1)
    disposition: MatchDisposition
    replay_eligible: bool = False
    negative_guidance_eligible: bool = False
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_eligibility(self) -> CompatibilityAssessment:
        if self.replay_eligible and self.negative_guidance_eligible:
            raise ValueError("a match cannot be both a replay seed and negative guidance")
        if self.disposition is MatchDisposition.REJECTED and (
            self.replay_eligible or self.negative_guidance_eligible
        ):
            raise ValueError("rejected matches are never eligible")
        return self


class ExperienceMatch(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    match_id: str
    query_id: str
    experience_id: str
    rank: int = Field(ge=1, le=100)
    trust: ExperienceTrust
    polarity: ExperiencePolarity
    assessment: CompatibilityAssessment
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExperienceSelection(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    selection_id: str
    task_id: str
    query_id: str
    match_ids: list[str] = Field(min_length=1, max_length=3)
    expected_context_bundle_id: str
    resulting_context_bundle_id: str
    selected_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def unique_matches(self) -> ExperienceSelection:
        if len(set(self.match_ids)) != len(self.match_ids):
            raise ValueError("experience selection cannot contain duplicate matches")
        if self.expected_context_bundle_id == self.resulting_context_bundle_id:
            raise ValueError("experience selection must create a new context revision")
        return self


class ModerationAction(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    action_id: str
    experience_id: str
    action: ModerationActionType = ModerationActionType.RETRACT
    reason: str = Field(min_length=1, max_length=2_000)
    expected_revision: int = Field(ge=1)
    resulting_revision: int = Field(ge=2)
    actor: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def increments_revision(self) -> ModerationAction:
        if self.resulting_revision != self.expected_revision + 1:
            raise ValueError("moderation must increment the experience revision by one")
        return self


class TrajectorySeed(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    seed_id: str
    search_id: str
    candidate_id: str
    match_id: str
    experience_id: str
    segment_ids: list[str] = Field(min_length=1, max_length=64)
    procedural_guidance: list[str] = Field(default_factory=list, max_length=64)
    assumptions: list[str] = Field(default_factory=list, max_length=32)
    required_revalidations: list[str] = Field(default_factory=list, max_length=32)
    validation_status: SeedValidationStatus = SeedValidationStatus.PENDING
    rejection_reasons: list[str] = Field(default_factory=list)
    revalidated_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
