from __future__ import annotations

import asyncio
import hashlib
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from accretion.contracts import (
    AgentEvent,
    ApprovalStatus,
    ArtifactRef,
    CapabilityExecutionStatus,
    ContextBundle,
    EventType,
    Provider,
    RunState,
    Task,
    VerificationResult,
    VerificationStatus,
)
from accretion.experience.embedding import (
    canonical_digest,
    deterministic_embedding,
    manifest_digest,
    repository_manifests,
    task_family,
)
from accretion.experience.models import (
    CompatibilityAssessment,
    Experience,
    ExperienceDetail,
    ExperienceEmbedding,
    ExperienceMatch,
    ExperiencePolarity,
    ExperienceQuery,
    ExperienceSelection,
    ExperienceSourceKind,
    ExperienceTrust,
    MatchDisposition,
    ModerationAction,
    TrajectorySegment,
    TrajectorySegmentKind,
)
from accretion.ids import new_id
from accretion.orchestration.models import CandidateStatus
from accretion.planning import has_irreversible_capabilities
from accretion.redaction import redact_text
from accretion.services.run_manager import RunManager

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_CANDIDATES = {
    CandidateStatus.COMPLETED,
    CandidateStatus.FAILED,
    CandidateStatus.PRUNED,
    CandidateStatus.SELECTED,
}


class ExperienceDisabledError(PermissionError):
    pass


class ExperienceConflictError(RuntimeError):
    pass


class ExperienceService:
    """P7 authority boundary for explicit local experience retrieval."""

    def __init__(
        self,
        manager: RunManager,
        *,
        globally_enabled: bool,
        operator_identity: str,
    ) -> None:
        self.manager = manager
        self.store = manager.store
        self.globally_enabled = globally_enabled
        self.operator_identity = operator_identity

    async def materialize(
        self, run_id: str, *, candidate_id: str | None = None
    ) -> ExperienceDetail:
        run = await self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        await self._require_enabled(run.project_id)
        task = await self.store.get_task(run.task_id)
        project = await self.store.get_project(run.project_id)
        if task is None or project is None:
            raise ExperienceConflictError("experience source has incomplete project state")
        planning = await self.manager.get_task_planning(task.envelope.task_id)

        source_kind = ExperienceSourceKind.RUN
        candidate = None
        score = None
        if candidate_id is not None:
            candidate = await self.store.get_search_candidate(candidate_id)
            if candidate is None:
                raise KeyError(candidate_id)
            if candidate.run_id != run_id:
                raise ExperienceConflictError("candidate does not belong to the source run")
            source_kind = ExperienceSourceKind.CANDIDATE
            scores = await self.store.list_candidate_scores(candidate.search_id)
            score = next((item for item in scores if item.candidate_id == candidate_id), None)

        trust, polarity, outcome, failure_taxonomy = await self._classify_source(
            run_id, task, candidate_id=candidate_id
        )
        verifier_ids = sorted(set(self.manager._verifier_ids(task)))
        verifications = await self.store.list_verifications(run_id)
        if candidate is not None:
            allowed_refs = set(candidate.verifier_result_refs)
            verifications = [
                item for item in verifications if item.verification_id in allowed_refs
            ]
        artifacts = await self.store.list_artifacts(run_id)
        if candidate is not None:
            artifact_refs = set(candidate.artifact_refs)
            artifacts = [item for item in artifacts if item.artifact_id in artifact_refs]
        events = [] if candidate is not None else await self.store.list_events(run_id)
        segments_data = self._safe_segments(
            events=events,
            verifications=verifications,
            artifacts=artifacts,
            failure_taxonomy=failure_taxonomy,
            candidate_status=candidate.status if candidate is not None else None,
            candidate_score_status=score.verifier_status if score is not None else None,
        )
        content_digest = canonical_digest(segments_data)
        manifests = repository_manifests(project.repository_path)
        source_commit = await self._source_commit(
            project.repository_path,
            lease_id=(
                candidate.workspace_lease_id
                if candidate is not None
                else run.workspace_lease_id
            ),
        )
        repository_identity = await self.repository_identity(
            project.repository_path, project.project_id
        )
        policy_digest = self._policy_digest(task)
        verifier_digest = self._verifier_digest(task, verifier_ids)
        prompt_digest = canonical_digest(
            {
                "version": planning.prompt_contract.version,
                "role": planning.prompt_contract.role,
                "tool_rules": planning.prompt_contract.tool_rules,
                "output_schema": planning.prompt_contract.output_schema,
            }
        )
        context_digest = self._context_shape_digest(planning.context_bundle)
        tool_profile_digest = self._tool_profile_digest(task)

        existing = next(
            (
                item
                for item in await self.store.list_experiences(
                    project_id=project.project_id, include_retracted=True
                )
                if item.source_run_id == run_id
                and item.source_candidate_id == candidate_id
            ),
            None,
        )
        current_identity = {
            "source_commit": source_commit,
            "manifest_digest": manifest_digest(project.repository_path, manifests),
            "policy_digest": policy_digest,
            "verifier_digest": verifier_digest,
            "prompt_digest": prompt_digest,
            "context_digest": context_digest,
            "tool_profile_digest": tool_profile_digest,
            "content_digest": content_digest,
            "trust": trust,
            "polarity": polarity,
            "outcome": outcome,
            "failure_taxonomy": failure_taxonomy,
        }
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in current_identity.items()):
                raise ExperienceConflictError(
                    "terminal source evidence changed after experience materialization"
                )
            return await self.detail(existing.experience_id)

        experience = Experience(
            experience_id=new_id("experience"),
            project_id=project.project_id,
            repository_identity=repository_identity,
            task_id=task.envelope.task_id,
            task_type=task.envelope.task_type,
            task_family=task_family(task, manifests),
            source_kind=source_kind,
            source_run_id=run_id,
            source_candidate_id=candidate_id,
            source_commit=source_commit,
            architecture_version="2.0",
            manifest_digest=current_identity["manifest_digest"],
            manifest_paths=manifests,
            policy_digest=policy_digest,
            verifier_digest=verifier_digest,
            prompt_digest=prompt_digest,
            context_digest=context_digest,
            tool_profile_digest=tool_profile_digest,
            requested_skills=sorted(set(task.envelope.requested_skills)),
            allowed_capabilities=sorted(set(task.envelope.allowed_capabilities)),
            denied_capabilities=sorted(set(task.envelope.denied_capabilities)),
            verifier_ids=verifier_ids,
            provider=candidate.provider if candidate is not None else run.provider,
            runtime_model=candidate.runtime_model if candidate is not None else run.provider.value,
            runtime_version=(
                candidate.runtime_version if candidate is not None else "run-runtime-v1"
            ),
            trust=trust,
            polarity=polarity,
            outcome=outcome,
            failure_taxonomy=failure_taxonomy,
            content_digest=content_digest,
        )
        segments: list[TrajectorySegment] = []
        for index, segment_data in enumerate(segments_data, start=1):
            kind, content = segment_data
            segments.append(
                TrajectorySegment(
                    segment_id=new_id("trajectory_segment"),
                    experience_id=experience.experience_id,
                    ordinal=index,
                    kind=kind,
                    content=content,
                    content_digest=canonical_digest(content),
                )
            )
        embedded = deterministic_embedding(
            task,
            planning.current_profile,
            manifests=manifests,
            verifier_ids=verifier_ids,
            segment_kinds=[item.kind for item in segments],
        )
        embedding = ExperienceEmbedding(
            embedding_id=new_id("experience_embedding"),
            experience_id=experience.experience_id,
            input_digest=embedded.input_digest,
            vector=embedded.vector,
        )
        try:
            await self.store.save_experience(experience, segments, embedding)
        except ValueError as exc:
            raise ExperienceConflictError(str(exc)) from exc
        return ExperienceDetail(
            experience=experience,
            segments=segments,
            embedding_version=embedding.version,
            embedding_input_digest=embedding.input_digest,
        )

    async def list_experiences(
        self,
        *,
        project_id: str | None = None,
        repository_identity: str | None = None,
        include_retracted: bool = False,
    ) -> list[Experience]:
        return await self.store.list_experiences(
            project_id=project_id,
            repository_identity=repository_identity,
            include_retracted=include_retracted,
        )

    async def detail(self, experience_id: str) -> ExperienceDetail:
        experience = await self.store.get_experience(experience_id)
        if experience is None:
            raise KeyError(experience_id)
        embedding = await self.store.get_experience_embedding(experience_id)
        if embedding is None:
            raise ExperienceConflictError("experience embedding is missing")
        return ExperienceDetail(
            experience=experience,
            segments=await self.store.list_trajectory_segments(experience_id),
            embedding_version=embedding.version,
            embedding_input_digest=embedding.input_digest,
        )

    async def retract(
        self, experience_id: str, *, reason: str, expected_revision: int
    ) -> Experience:
        experience = await self.store.get_experience(experience_id)
        if experience is None:
            raise KeyError(experience_id)
        await self._require_enabled(experience.project_id)
        action = ModerationAction(
            action_id=new_id("moderation_action"),
            experience_id=experience_id,
            reason=reason,
            expected_revision=expected_revision,
            resulting_revision=expected_revision + 1,
            actor=self.operator_identity,
        )
        try:
            return await self.store.retract_experience(action)
        except ValueError as exc:
            raise ExperienceConflictError(str(exc)) from exc

    async def query(
        self,
        task_id: str,
        *,
        include_failures: bool = True,
        top_k: int = 5,
        max_age_days: int | None = None,
    ) -> list[ExperienceMatch]:
        task = await self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        await self._require_enabled(task.envelope.project_id)
        if await self.store.list_workflow_proposals(task_id=task_id):
            raise ExperienceConflictError(
                "experience retrieval is frozen after workflow proposal"
            )
        project = await self.store.get_project(task.envelope.project_id)
        if project is None:
            raise KeyError(task.envelope.project_id)
        planning = await self.manager.get_task_planning(task_id)
        manifests = repository_manifests(project.repository_path)
        verifier_ids = sorted(set(self.manager._verifier_ids(task)))
        embedded = deterministic_embedding(
            task,
            planning.current_profile,
            manifests=manifests,
            verifier_ids=verifier_ids,
        )
        repository_identity = await self.repository_identity(
            project.repository_path, project.project_id
        )
        query = ExperienceQuery(
            query_id=new_id("experience_query"),
            project_id=project.project_id,
            task_id=task_id,
            task_profile_id=planning.current_profile.profile_id,
            repository_identity=repository_identity,
            source_commit=await self._git_output(project.repository_path, "rev-parse", "HEAD"),
            architecture_version="2.0",
            manifest_digest=manifest_digest(project.repository_path, manifests),
            manifest_paths=manifests,
            policy_digest=self._policy_digest(task),
            verifier_digest=self._verifier_digest(task, verifier_ids),
            prompt_digest=canonical_digest(
                {
                    "version": planning.prompt_contract.version,
                    "role": planning.prompt_contract.role,
                    "tool_rules": planning.prompt_contract.tool_rules,
                    "output_schema": planning.prompt_contract.output_schema,
                }
            ),
            context_digest=self._context_shape_digest(planning.context_bundle),
            tool_profile_digest=self._tool_profile_digest(task),
            requested_skills=sorted(set(task.envelope.requested_skills)),
            allowed_capabilities=sorted(set(task.envelope.allowed_capabilities)),
            denied_capabilities=sorted(set(task.envelope.denied_capabilities)),
            verifier_ids=verifier_ids,
            include_failures=include_failures,
            top_k=top_k,
            max_age_days=max_age_days,
            embedding_input_digest=embedded.input_digest,
        )
        await self.store.save_experience_query(query, embedded.vector)
        nearest = await self.store.nearest_experience_embeddings(
            repository_identity,
            embedded.vector,
            limit=max(10, min(100, top_k * 4)),
        )
        matches: list[ExperienceMatch] = []
        for rank, (experience_id, distance) in enumerate(nearest[:top_k], start=1):
            experience = await self.store.get_experience(experience_id)
            if experience is None:
                continue
            assessment = await self.assess(
                query,
                experience,
                semantic_score=min(max(1 - distance, 0.0), 1.0),
                repository=project.repository_path,
            )
            matches.append(
                ExperienceMatch(
                    match_id=new_id("experience_match"),
                    query_id=query.query_id,
                    experience_id=experience_id,
                    rank=rank,
                    trust=experience.trust,
                    polarity=experience.polarity,
                    assessment=assessment,
                )
            )
        await self.store.save_experience_matches(matches)
        return matches

    async def select(
        self,
        task_id: str,
        *,
        query_id: str,
        match_ids: list[str],
        expected_context_bundle_id: str,
    ) -> ExperienceSelection:
        task = await self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        await self._require_enabled(task.envelope.project_id)
        stored_query = await self.store.get_experience_query(query_id)
        if stored_query is None or stored_query[0].task_id != task_id:
            raise ExperienceConflictError("experience query does not belong to this task")
        if not 1 <= len(match_ids) <= 3 or len(set(match_ids)) != len(match_ids):
            raise ValueError("select between one and three unique experience matches")
        matches = {
            item.match_id: item
            for item in await self.store.list_experience_matches(query_id)
        }
        if any(match_id not in matches for match_id in match_ids):
            raise ExperienceConflictError("selection includes a match from another query")
        selected = [matches[match_id] for match_id in match_ids]
        if any(item.assessment.disposition is not MatchDisposition.ACCEPTED for item in selected):
            raise ExperienceConflictError("only accepted experience matches may be selected")
        planning = await self.manager.get_task_planning(task_id)
        current = planning.context_bundle
        if current.context_bundle_id != expected_context_bundle_id:
            raise ExperienceConflictError("context bundle revision conflict")
        if current.version == "context-bundle-v2":
            raise ExperienceConflictError("experience selection is already frozen for this task")
        experiences = []
        negative_refs = list(current.previous_failure_refs)
        for match in selected:
            experience = await self.store.get_experience(match.experience_id)
            if experience is None or experience.retracted:
                raise ExperienceConflictError("selected experience is unavailable")
            experiences.append(experience)
            if experience.polarity is ExperiencePolarity.NEGATIVE:
                negative_refs.append(experience.experience_id)
        resulting_id = new_id("context")
        context = current.model_copy(
            update={
                "schema_version": "2.0",
                "context_bundle_id": resulting_id,
                "version": "context-bundle-v2",
                "supersedes_context_bundle_id": current.context_bundle_id,
                "experience_query_id": query_id,
                "experience_match_refs": match_ids,
                "experience_refs": [item.experience_id for item in experiences],
                "previous_failure_refs": list(dict.fromkeys(negative_refs)),
                "provenance": [
                    *current.provenance,
                    f"experience-query:{query_id}",
                    *(f"experience-match:{item}" for item in match_ids),
                ],
                "created_at": datetime.now(UTC),
            }
        )
        selection = ExperienceSelection(
            selection_id=new_id("experience_selection"),
            task_id=task_id,
            query_id=query_id,
            match_ids=match_ids,
            expected_context_bundle_id=current.context_bundle_id,
            resulting_context_bundle_id=resulting_id,
            selected_by=self.operator_identity,
        )
        try:
            return await self.store.revise_context_with_experience(selection, context)
        except ValueError as exc:
            raise ExperienceConflictError(str(exc)) from exc

    async def selections(self, task_id: str) -> list[ExperienceSelection]:
        if await self.store.get_task(task_id) is None:
            raise KeyError(task_id)
        return await self.store.list_experience_selections(task_id)

    async def selected_matches(self, task_id: str) -> list[ExperienceMatch]:
        task = await self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        await self._require_enabled(task.envelope.project_id)
        planning = await self.manager.get_task_planning(task_id)
        query_id = planning.context_bundle.experience_query_id
        if query_id is None:
            return []
        selected = set(planning.context_bundle.experience_match_refs)
        return [
            item
            for item in await self.store.list_experience_matches(query_id)
            if item.match_id in selected
        ]

    async def revalidate_match(
        self,
        task_id: str,
        match_id: str,
        *,
        runtime_provider: Provider | None = None,
    ) -> tuple[ExperienceMatch, Experience, CompatibilityAssessment]:
        """Rebuild compatibility from current repository and authority state."""

        task = await self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        await self._require_enabled(task.envelope.project_id)
        project = await self.store.get_project(task.envelope.project_id)
        if project is None:
            raise KeyError(task.envelope.project_id)
        planning = await self.manager.get_task_planning(task_id)
        query_id = planning.context_bundle.experience_query_id
        if query_id is None:
            raise ExperienceConflictError("task has no selected experience query")
        stored_query = await self.store.get_experience_query(query_id)
        if stored_query is None:
            raise ExperienceConflictError("selected experience query is unavailable")
        match = next(
            (
                item
                for item in await self.store.list_experience_matches(query_id)
                if item.match_id == match_id
            ),
            None,
        )
        if match is None:
            raise ExperienceConflictError("experience match is outside the selected query")
        experience = await self.store.get_experience(match.experience_id)
        if experience is None:
            raise ExperienceConflictError("experience source is unavailable")
        manifests = repository_manifests(project.repository_path)
        verifier_ids = sorted(set(self.manager._verifier_ids(task)))
        current_query = stored_query[0].model_copy(
            update={
                "repository_identity": await self.repository_identity(
                    project.repository_path, project.project_id
                ),
                "source_commit": await self._git_output(
                    project.repository_path, "rev-parse", "HEAD"
                ),
                "architecture_version": "2.0",
                "manifest_digest": manifest_digest(project.repository_path, manifests),
                "manifest_paths": manifests,
                "policy_digest": self._policy_digest(task),
                "verifier_digest": self._verifier_digest(task, verifier_ids),
                "context_digest": self._context_shape_digest(planning.context_bundle),
                "tool_profile_digest": self._tool_profile_digest(task),
                "requested_skills": sorted(set(task.envelope.requested_skills)),
                "allowed_capabilities": sorted(
                    set(task.envelope.allowed_capabilities)
                ),
                "denied_capabilities": sorted(set(task.envelope.denied_capabilities)),
                "verifier_ids": verifier_ids,
                "runtime_provider": runtime_provider,
            }
        )
        assessment = await self.assess(
            current_query,
            experience,
            semantic_score=match.assessment.semantic_score,
            repository=project.repository_path,
        )
        return match, experience, assessment

    async def assess(
        self,
        query: ExperienceQuery,
        experience: Experience,
        *,
        semantic_score: float,
        repository: Path,
    ) -> CompatibilityAssessment:
        reasons: list[str] = []
        hard_reasons: list[str] = []
        if experience.retracted:
            hard_reasons.append("EXPERIENCE_RETRACTED")
        if experience.repository_identity != query.repository_identity:
            hard_reasons.append("REPOSITORY_MISMATCH")
        if experience.protected_side_effects:
            hard_reasons.append("PROTECTED_SIDE_EFFECT_STATE")
        if not all(
            _DIGEST.fullmatch(value)
            for value in (
                experience.manifest_digest,
                experience.policy_digest,
                experience.verifier_digest,
                experience.prompt_digest,
                experience.context_digest,
                experience.tool_profile_digest,
            )
        ):
            hard_reasons.append("INVALID_DIGEST")
        if self._major(experience.architecture_version) != self._major(
            query.architecture_version
        ):
            hard_reasons.append("ARCHITECTURE_MAJOR_INCOMPATIBLE")
        if experience.policy_digest != query.policy_digest:
            hard_reasons.append("POLICY_INCOMPATIBLE")
        if experience.verifier_digest != query.verifier_digest:
            hard_reasons.append("VERIFIER_INCOMPATIBLE")
        if set(experience.requested_skills) - set(query.requested_skills):
            hard_reasons.append("SKILL_NOT_REQUESTED")
        if set(experience.allowed_capabilities) - set(query.allowed_capabilities):
            hard_reasons.append("CAPABILITY_NOT_ALLOWED")
        if set(experience.allowed_capabilities) & set(query.denied_capabilities):
            hard_reasons.append("CAPABILITY_DENIED")
        available_skills = {item.skill_id for item in await self.store.list_skills()}
        available_plugins = {
            item.plugin_id
            for item in await self.store.list_plugins(allowlisted_only=True)
        }
        if any(
            item not in available_skills and item not in available_plugins
            for item in experience.requested_skills
        ):
            hard_reasons.append("SKILL_OR_PLUGIN_UNAVAILABLE")
        available_capabilities = {
            item.capability_id for item in await self.store.list_capabilities()
        }
        if any(
            item not in available_capabilities
            for item in experience.allowed_capabilities
        ):
            hard_reasons.append("CAPABILITY_UNAVAILABLE")
        available_verifiers = set(self.manager.verifiers.list_ids())
        if any(item not in available_verifiers for item in experience.verifier_ids):
            hard_reasons.append("VERIFIER_UNAVAILABLE")
        if experience.polarity is ExperiencePolarity.NEGATIVE and not query.include_failures:
            hard_reasons.append("FAILURES_EXCLUDED")

        commit_score = 0.0
        if await self._git_success(
            repository, "cat-file", "-e", f"{experience.source_commit}^{{commit}}"
        ):
            if experience.source_commit == query.source_commit:
                commit_score = 1.0
            elif await self._git_success(
                repository,
                "merge-base",
                "--is-ancestor",
                experience.source_commit,
                query.source_commit,
            ):
                commit_score = 0.9
            else:
                hard_reasons.append("SOURCE_COMMIT_NOT_ANCESTOR")
        else:
            hard_reasons.append("SOURCE_COMMIT_MISSING")

        age_days = max(0.0, (datetime.now(UTC) - experience.created_at).total_seconds() / 86_400)
        if query.max_age_days is not None and age_days > query.max_age_days:
            hard_reasons.append("MAX_AGE_EXCEEDED")
        freshness = (
            1.0
            if age_days <= 30
            else 0.95
            if age_days <= 90
            else 0.9
            if age_days <= 180
            else 0.8
        )
        if experience.manifest_digest == query.manifest_digest:
            manifest_score = 1.0
        elif set(experience.manifest_paths) == set(query.manifest_paths):
            manifest_score = 0.8
            reasons.append("MANIFEST_CONTENT_DRIFT")
        else:
            manifest_score = 0.6
            reasons.append("MANIFEST_SET_DRIFT")
        environment = 0.6 * commit_score + 0.4 * manifest_score
        architecture = (
            1.0
            if experience.architecture_version == query.architecture_version
            else 0.85
        )
        policy_verifier = (
            1.0
            if experience.policy_digest == query.policy_digest
            and experience.verifier_digest == query.verifier_digest
            else 0.0
        )
        runtime = 0.8
        if query.runtime_provider is not None:
            runtime = 0.75 if experience.provider == query.runtime_provider else 0.5
            if (
                experience.provider == query.runtime_provider
                and experience.runtime_model == query.runtime_model
            ):
                runtime = 0.9
                if experience.runtime_version == query.runtime_version:
                    runtime = 1.0
        prompt_context_tool = sum(
            (
                1.0 if experience.prompt_digest == query.prompt_digest else 0.5,
                1.0 if experience.context_digest == query.context_digest else 0.5,
                1.0 if experience.tool_profile_digest == query.tool_profile_digest else 0.5,
            )
        ) / 3
        version = (
            0.35 * architecture
            + 0.30 * policy_verifier
            + 0.20 * runtime
            + 0.15 * prompt_context_tool
        )
        final = math.prod(
            (max(semantic_score, 0.0), max(environment, 0.0), max(version, 0.0), freshness)
        ) ** 0.25
        transfer_risk = 1 - min(environment, version, freshness)
        replay = (
            not hard_reasons
            and experience.trust is ExperienceTrust.HIGH
            and experience.polarity is ExperiencePolarity.POSITIVE
            and semantic_score >= 0.60
            and environment >= 0.90
            and version >= 0.85
            and freshness >= 0.80
            and final >= 0.75
        )
        negative = (
            not hard_reasons
            and experience.trust is ExperienceTrust.MEDIUM
            and experience.polarity is ExperiencePolarity.NEGATIVE
            and bool(experience.failure_taxonomy)
            and semantic_score >= 0.55
            and environment >= 0.75
            and final >= 0.65
        )
        disposition = (
            MatchDisposition.REJECTED
            if hard_reasons
            else MatchDisposition.ACCEPTED
            if replay or negative
            else MatchDisposition.DOWNRANKED
        )
        return CompatibilityAssessment(
            semantic_score=round(semantic_score, 6),
            environment_score=round(environment, 6),
            version_score=round(version, 6),
            freshness_score=round(freshness, 6),
            final_score=round(final, 6),
            transfer_risk=round(transfer_risk, 6),
            disposition=disposition,
            replay_eligible=replay,
            negative_guidance_eligible=negative,
            reasons=[*hard_reasons, *reasons],
        )

    async def repository_identity(self, repository: Path, project_id: str) -> str:
        remote = await self._git_output(
            repository, "config", "--get", "remote.origin.url", required=False
        )
        normalized = self._normalize_remote(remote) if remote else f"project:{project_id}"
        return hashlib.sha256(normalized.encode()).hexdigest()

    async def _classify_source(
        self, run_id: str, task: Task, *, candidate_id: str | None
    ) -> tuple[ExperienceTrust, ExperiencePolarity, str, list[str]]:
        run = await self.store.get_run(run_id)
        assert run is not None
        if has_irreversible_capabilities(task.envelope.allowed_capabilities):
            raise ExperienceConflictError(
                "irreversible or protected authority cannot become replay experience"
            )
        capability_results = await self.store.list_capability_results(run_id)
        ambiguous = any(
            item.status
            in {
                CapabilityExecutionStatus.REQUIRES_APPROVAL,
                CapabilityExecutionStatus.EXECUTING,
                CapabilityExecutionStatus.UNKNOWN,
            }
            or item.side_effect_operation_id is not None
            for item in capability_results
        )
        if ambiguous:
            raise ExperienceConflictError("source has protected or ambiguous side-effect state")

        if candidate_id is not None:
            candidate = await self.store.get_search_candidate(candidate_id)
            assert candidate is not None
            if candidate.status is CandidateStatus.CANCELLED:
                raise ExperienceConflictError("cancelled candidates cannot become experience")
            if candidate.status not in _TERMINAL_CANDIDATES or candidate.completed_at is None:
                raise ExperienceConflictError("candidate is not terminal and complete")
            scores = await self.store.list_candidate_scores(candidate.search_id)
            score = next((item for item in scores if item.candidate_id == candidate_id), None)
            if score is None:
                raise ExperienceConflictError("candidate has no complete verifier score")
            if (
                candidate.status is CandidateStatus.SELECTED
                and score.eligible
                and score.verifier_status == VerificationStatus.PASS.value
            ):
                return (
                    ExperienceTrust.HIGH,
                    ExperiencePolarity.POSITIVE,
                    "VERIFIED_SUCCESS",
                    [],
                )
            taxonomy = [
                "OUT_RANKED" if candidate.status is CandidateStatus.PRUNED else "CANDIDATE_FAILED",
                f"VERIFIER_{score.verifier_status}",
            ]
            return (
                ExperienceTrust.MEDIUM,
                ExperiencePolarity.NEGATIVE,
                candidate.status.value,
                taxonomy,
            )

        if run.state is RunState.CANCELLED:
            raise ExperienceConflictError("cancelled runs cannot become experience")
        if run.state not in {RunState.SUCCEEDED, RunState.FAILED, RunState.REQUIRES_HUMAN}:
            raise ExperienceConflictError("run is not terminal")
        verifications = await self.store.list_verifications(run_id)
        policy = (
            await self.store.get_acceptance_policy(run.acceptance_policy_id)
            if run.acceptance_policy_id is not None
            else None
        )
        if run.state is RunState.SUCCEEDED:
            if policy is None:
                raise ExperienceConflictError("successful source has no acceptance policy")
            by_verifier = {item.verifier_id: item for item in verifications}
            if any(
                verifier not in by_verifier
                or by_verifier[verifier].status is not VerificationStatus.PASS
                for verifier in policy.required_verifiers
            ):
                raise ExperienceConflictError("required verifier evidence is incomplete")
            if task.envelope.required_outputs and not await self.store.list_artifacts(run_id):
                raise ExperienceConflictError("required artifact evidence is incomplete")
            if await self.store.list_approvals(run_id, ApprovalStatus.PENDING):
                raise ExperienceConflictError("source has unresolved approval state")
            return (
                ExperienceTrust.HIGH,
                ExperiencePolarity.POSITIVE,
                "VERIFIED_SUCCESS",
                [],
            )
        taxonomy = sorted(
            {
                run.error.code if run.error is not None else run.state.value,
                *(
                    finding.code
                    for verification in verifications
                    for finding in verification.findings
                ),
            }
        )
        return ExperienceTrust.MEDIUM, ExperiencePolarity.NEGATIVE, run.state.value, taxonomy

    def _safe_segments(
        self,
        *,
        events: list[AgentEvent],
        verifications: list[VerificationResult],
        artifacts: list[ArtifactRef],
        failure_taxonomy: list[str],
        candidate_status: CandidateStatus | None,
        candidate_score_status: str | None,
    ) -> list[tuple[TrajectorySegmentKind, dict[str, object]]]:
        workflow = [
            redact_text(str(item.node_id))
            for item in events
            if item.normalized_type is EventType.NODE_ENTERED and item.node_id
        ]
        if candidate_status is not None:
            workflow = ["isolated-candidate", f"terminal:{candidate_status.value.lower()}"]
        tool_sequence = [
            redact_text(str(item.payload["capability_id"]))
            for item in events
            if item.normalized_type is EventType.TOOL_REQUESTED
            and item.payload.get("capability_id")
        ]
        verifier_findings = [
            {
                "verifier": redact_text(item.verifier_id),
                "status": item.status.value,
                "finding_codes": sorted(redact_text(finding.code) for finding in item.findings),
            }
            for item in verifications
        ]
        if candidate_score_status is not None and not verifier_findings:
            verifier_findings = [
                {
                    "verifier": "candidate-score",
                    "status": candidate_score_status,
                    "finding_codes": [],
                }
            ]
        repair_count = sum(
            1
            for item in events
            if item.normalized_type is EventType.LOOP_ITERATION_COMPLETED
        )
        artifact_shapes = [
            {
                "kind": redact_text(str(item.kind)),
                "suffix": Path(item.path).suffix.lower(),
                "digest_present": item.sha256 is not None,
            }
            for item in artifacts
        ]
        segments: list[tuple[TrajectorySegmentKind, dict[str, object]]] = [
            (TrajectorySegmentKind.WORKFLOW_PATH, {"nodes": workflow}),
        ]
        if tool_sequence:
            segments.append((TrajectorySegmentKind.TOOL_SEQUENCE, {"capabilities": tool_sequence}))
        if verifier_findings:
            segments.append(
                (TrajectorySegmentKind.VERIFIER_FINDINGS, {"results": verifier_findings})
            )
        if repair_count:
            segments.append(
                (TrajectorySegmentKind.REPAIR_PATTERN, {"completed_iterations": repair_count})
            )
        if failure_taxonomy:
            segments.append(
                (
                    TrajectorySegmentKind.FAILURE_PATTERN,
                    {"taxonomy": [redact_text(item) for item in failure_taxonomy]},
                )
            )
        segments.append(
            (TrajectorySegmentKind.ARTIFACT_SHAPE, {"artifacts": artifact_shapes})
        )
        return segments

    async def _require_enabled(self, project_id: str) -> None:
        if not self.globally_enabled:
            raise ExperienceDisabledError(
                "experience retrieval is globally disabled; set "
                "ACCRETION_ENABLE_EXPERIENCE_RETRIEVAL=true"
            )
        features = await self.store.get_project_features(project_id)
        if not (
            features.dynamic_workflows
            and features.candidate_search
            and features.experience_retrieval
        ):
            raise ExperienceDisabledError(
                f"experience retrieval is disabled for project {project_id}"
            )

    async def _source_commit(self, repository: Path, lease_id: str | None) -> str:
        if lease_id is not None:
            lease = await self.store.get_lease(lease_id)
            if lease is not None:
                return lease.base_revision
        return await self._git_output(repository, "rev-parse", "HEAD")

    @staticmethod
    def _policy_digest(task: Task) -> str:
        return canonical_digest(
            {
                "risk": task.envelope.risk_level.value,
                "allowed": sorted(set(task.envelope.allowed_capabilities)),
                "denied": sorted(set(task.envelope.denied_capabilities)),
            }
        )

    @staticmethod
    def _verifier_digest(task: Task, verifier_ids: list[str]) -> str:
        return canonical_digest(
            {
                "policy": task.envelope.verifier_policy_ref,
                "verifiers": sorted(set(verifier_ids)),
            }
        )

    @staticmethod
    def _context_shape_digest(context: ContextBundle) -> str:
        return canonical_digest(
            {
                "version": context.version,
                "phase": context.phase,
                "workspace_keys": sorted(context.workspace_map),
                "permissions": sorted(context.permissions),
            }
        )

    @staticmethod
    def _tool_profile_digest(task: Task) -> str:
        return canonical_digest(
            {
                "skills": sorted(set(task.envelope.requested_skills)),
                "allowed": sorted(set(task.envelope.allowed_capabilities)),
                "denied": sorted(set(task.envelope.denied_capabilities)),
            }
        )

    @staticmethod
    def _major(value: str) -> str:
        return value.split(".", 1)[0]

    @staticmethod
    def _normalize_remote(value: str) -> str:
        remote = value.strip()
        if "://" not in remote and re.match(r"^[^/@]+@[^:]+:.+$", remote):
            user_host, path = remote.split(":", 1)
            remote = f"ssh://{user_host}/{path}"
        parsed = urlsplit(remote)
        if parsed.hostname:
            path = parsed.path.rstrip("/")
            if path.endswith(".git"):
                path = path[:-4]
            return f"{parsed.hostname.lower()}{path.lower()}"
        path = remote.rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        return path

    async def _git_output(
        self, repository: Path, *arguments: str, required: bool = True
    ) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(repository),
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            if not required:
                return ""
            message = redact_text(stderr.decode(errors="replace"))[:500]
            raise ExperienceConflictError(
                f"repository inspection failed: {message}"
            )
        return stdout.decode(errors="replace").strip()

    async def _git_success(self, repository: Path, *arguments: str) -> bool:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(repository),
            *arguments,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.communicate()
        return process.returncode == 0
