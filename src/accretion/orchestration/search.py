from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from accretion.contracts import (
    AcceptancePolicy,
    AgentEvent,
    AgentRuntime,
    EventType,
    GraphNodeKind,
    IterationDirective,
    IterationDirectiveKind,
    Provider,
    Run,
    RunNode,
    RunRef,
    RunState,
    RuntimeExecutionRequest,
    RuntimeStatus,
    SessionConfig,
    Task,
    VerificationResult,
    VerificationStatus,
    WorkspaceLease,
)
from accretion.experience.embedding import canonical_digest
from accretion.experience.models import (
    CompatibilityAssessment,
    ExperiencePolarity,
    MatchDisposition,
    SeedValidationStatus,
    TrajectorySeed,
    TrajectorySegment,
    TrajectorySegmentKind,
)
from accretion.experience.service import ExperienceConflictError, ExperienceService
from accretion.ids import new_id
from accretion.orchestration.models import (
    CandidateScore,
    CandidateSourceKind,
    CandidateStatus,
    CandidateTrajectory,
    ProjectFeatureSettings,
    SearchBudgetEnvelope,
    SearchBudgetSpent,
    SearchMode,
    SearchPlan,
    SearchPromotionRecord,
    SearchRecord,
    SearchStatus,
    SearchStopReason,
)
from accretion.orchestration.router import PerformanceAwareRuntimeRouter
from accretion.redaction import redact_text
from accretion.runtimes.common import submission_call_id
from accretion.services.run_manager import RunManager
from accretion.verifiers.policy import evaluate_acceptance


class CandidateSearchDisabledError(PermissionError):
    pass


class CandidateSearchConflictError(RuntimeError):
    pass


class SearchService:
    """P6 authority boundary for validated plans and bounded speculative execution."""

    def __init__(
        self,
        manager: RunManager,
        *,
        globally_enabled: bool,
        operator_identity: str,
        experience_service: ExperienceService | None = None,
    ) -> None:
        self.manager = manager
        self.store = manager.store
        self.globally_enabled = globally_enabled
        self.operator_identity = operator_identity
        self.experience_service = experience_service
        self.router = PerformanceAwareRuntimeRouter()
        self.active_refs: dict[str, list[tuple[AgentRuntime, RunRef]]] = {}
        self.search_locks: dict[str, asyncio.Lock] = {}
        manager.search_executor = self.execute_at_node

    async def create_plan(
        self,
        run_id: str,
        *,
        parent_node_id: str,
        mode: SearchMode,
        branch_count: int,
        max_parallel: int,
        per_branch_budget: SearchBudgetEnvelope,
        total_budget: SearchBudgetEnvelope,
        candidate_directives: list[str],
        replay_seed_match_ids: list[str] | None = None,
        negative_guidance_match_ids: list[str] | None = None,
    ) -> SearchRecord:
        run = await self.manager._require_run(run_id)
        await self._require_enabled(run.project_id)
        if mode is SearchMode.REPLAY_BRANCH and self.experience_service is None:
            raise CandidateSearchConflictError("REPLAY_BRANCH_REQUIRES_P7")
        replay_seed_match_ids = replay_seed_match_ids or []
        negative_guidance_match_ids = negative_guidance_match_ids or []
        if run.state not in {RunState.PENDING, RunState.PAUSED}:
            raise CandidateSearchConflictError(
                "search plans attach only while a run is PENDING or safely PAUSED"
            )
        revisions = await self.store.list_graph_revisions(run_id)
        if not revisions:
            proposals = await self.store.list_workflow_proposals(run_id=run_id)
            if not proposals:
                raise CandidateSearchConflictError(
                    "search requires an accepted P5 proposal or active graph revision"
                )
            proposal = proposals[-1]
            validations = await self.store.list_graph_validations(proposal.proposal_id)
            if not validations or validations[-1].status.value != "ACCEPT":
                raise CandidateSearchConflictError(
                    "search requires an accepted workflow validation"
                )
            graph_revision = 1
            nodes = proposal.nodes
        else:
            graph_revision = revisions[-1].revision
            nodes = revisions[-1].nodes
        parent = next((item for item in nodes if item.local_id == parent_node_id), None)
        if parent is None:
            raise CandidateSearchConflictError(f"unknown parent node {parent_node_id}")
        if parent.kind is not GraphNodeKind.AGENT:
            raise CandidateSearchConflictError("P6 search plans attach only to AGENT nodes")
        if parent.capability_refs:
            raise CandidateSearchConflictError(
                "speculative candidates cannot attach to capability-bearing nodes"
            )
        if mode is SearchMode.GENERATOR_REVIEWER and branch_count != 2:
            raise ValueError("generator-reviewer search requires exactly two branches")
        if mode is SearchMode.GENERATOR_REVIEWER and (
            per_branch_budget.max_turns < 2 or per_branch_budget.max_tool_calls < 2
        ):
            raise ValueError(
                "generator-reviewer search reserves at least two turns and tool calls per branch"
            )
        if mode is SearchMode.CROSS_PROVIDER and branch_count not in {2, 4}:
            raise ValueError("cross-provider search requires two or four branches")
        task = await self.manager._require_task(run.task_id)
        if mode is SearchMode.REPLAY_BRANCH:
            await self._validate_replay_plan(
                task,
                run,
                replay_seed_match_ids,
                negative_guidance_match_ids,
            )
        budgets = task.envelope.budgets
        if max_parallel > budgets.max_parallel_runs:
            raise ValueError("search parallelism exceeds the task ceiling")
        if total_budget.wall_time_seconds > budgets.wall_time_seconds:
            raise ValueError("search wall time exceeds the task ceiling")
        if total_budget.max_turns > budgets.max_turns:
            raise ValueError("search turns exceed the task ceiling")
        if total_budget.max_tool_calls > budgets.max_tool_calls:
            raise ValueError("search tool calls exceed the task ceiling")
        spent = await self.store.get_budget_spent(run_id)
        if total_budget.max_turns > budgets.max_turns - spent["turns"]:
            raise ValueError("search turns exceed the remaining run budget")
        if total_budget.max_tool_calls > budgets.max_tool_calls - spent["tool_calls"]:
            raise ValueError("search tool calls exceed the remaining run budget")
        verifier_refs = self.manager._verifier_ids(task)
        plan = SearchPlan(
            search_id=new_id("search"),
            run_id=run_id,
            parent_node_id=parent_node_id,
            graph_revision=graph_revision,
            mode=mode,
            branch_count=branch_count,
            max_parallel=max_parallel,
            per_branch_budget=per_branch_budget,
            total_budget=total_budget,
            candidate_directives=candidate_directives,
            replay_seed_match_ids=replay_seed_match_ids,
            negative_guidance_match_ids=negative_guidance_match_ids,
            verifier_policy_ref=(
                run.acceptance_policy_id or "search-verifier:" + ",".join(sorted(verifier_refs))
            ),
            requested_by=self.operator_identity,
        )
        record = await self.store.create_search(SearchRecord(plan=plan))
        if mode is SearchMode.REPLAY_BRANCH:
            planning = await self.manager.get_task_planning(task.envelope.task_id)
            query_event = await self._emit(
                run.run_id,
                EventType.EXPERIENCE_QUERY,
                "accretion/experience-query-attached",
                {
                    "search_id": plan.search_id,
                    "query_id": planning.context_bundle.experience_query_id,
                },
            )
            await self._emit(
                run.run_id,
                EventType.EXPERIENCE_RETRIEVED,
                "accretion/experience-retrieved",
                {
                    "search_id": plan.search_id,
                    "replay_seed_match_ids": replay_seed_match_ids,
                    "negative_guidance_match_ids": negative_guidance_match_ids,
                },
                causation_id=query_event.event_id,
            )
        return record

    async def get(self, search_id: str) -> SearchRecord:
        record = await self.store.get_search(search_id)
        if record is None:
            raise KeyError(search_id)
        return record

    async def _validate_replay_plan(
        self,
        task: Task,
        run: Run,
        replay_seed_match_ids: list[str],
        negative_guidance_match_ids: list[str],
    ) -> None:
        if self.experience_service is None:
            raise CandidateSearchConflictError("REPLAY_BRANCH_REQUIRES_P7")
        if not 1 <= len(replay_seed_match_ids) <= 3:
            raise ValueError("replay search requires one to three positive seeds")
        if len(set(replay_seed_match_ids)) != len(replay_seed_match_ids):
            raise ValueError("replay seed matches must be unique")
        if len(set(negative_guidance_match_ids)) != len(negative_guidance_match_ids):
            raise ValueError("negative guidance matches must be unique")
        if set(replay_seed_match_ids) & set(negative_guidance_match_ids):
            raise ValueError("a match cannot be both replay seed and negative guidance")
        planning = await self.manager.get_task_planning(task.envelope.task_id)
        selected = set(planning.context_bundle.experience_match_refs)
        attached = set(replay_seed_match_ids) | set(negative_guidance_match_ids)
        if planning.context_bundle.version != "context-bundle-v2" or not attached <= selected:
            raise CandidateSearchConflictError(
                "replay uses only experience matches explicitly selected into task context"
            )
        for match_id in replay_seed_match_ids:
            _, experience, assessment = await self.experience_service.revalidate_match(
                task.envelope.task_id,
                match_id,
                runtime_provider=run.provider,
            )
            if (
                experience.polarity is not ExperiencePolarity.POSITIVE
                or not assessment.replay_eligible
                or assessment.disposition is not MatchDisposition.ACCEPTED
            ):
                raise CandidateSearchConflictError(
                    f"positive replay seed {match_id} failed compatibility revalidation"
                )
        for match_id in negative_guidance_match_ids:
            _, experience, assessment = await self.experience_service.revalidate_match(
                task.envelope.task_id,
                match_id,
                runtime_provider=run.provider,
            )
            if (
                experience.polarity is not ExperiencePolarity.NEGATIVE
                or not assessment.negative_guidance_eligible
                or assessment.disposition is not MatchDisposition.ACCEPTED
            ):
                raise CandidateSearchConflictError(
                    f"negative guidance {match_id} failed compatibility revalidation"
                )

    async def cancel(self, search_id: str) -> SearchRecord:
        async with self._search_lock(search_id):
            record = await self.get(search_id)
            if record.status not in {
                SearchStatus.PLANNED,
                SearchStatus.RUNNING,
                SearchStatus.SELECTING,
            }:
                return record
            updated = record.model_copy(
                update={
                    "status": SearchStatus.CANCELLED,
                    "stop_reason": SearchStopReason.OPERATOR_CANCELLED,
                    "completed_at": datetime.now(UTC),
                }
            )
            cancelled = await self.store.update_search(updated, expected_revision=record.revision)
        for runtime, ref in self.active_refs.get(search_id, []):
            await runtime.interrupt(ref)
        return cancelled

    async def reconcile(self) -> None:
        """Fail closed on searches left active by an unclean shutdown.

        Candidate calls are not replayed because a provider may already have charged
        the full branch allowance. A durable promotion intent is the sole operation
        that can be resumed, and only while the parent workspace still matches the
        digest captured immediately before promotion.
        """

        for run in await self.store.list_runs(limit=500):
            for record in await self.store.list_searches(run.run_id):
                if record.status not in {SearchStatus.RUNNING, SearchStatus.SELECTING}:
                    continue
                candidates = await self.store.list_search_candidates(record.plan.search_id)
                reconciled: list[CandidateTrajectory] = []
                for candidate in candidates:
                    if candidate.status is CandidateStatus.RUNNING:
                        candidate = candidate.model_copy(
                            update={
                                "status": CandidateStatus.INTERRUPTED,
                                "terminal_reason": (
                                    "candidate execution was interrupted by process restart"
                                ),
                                "budget_spent": self._spent(
                                    record.plan.per_branch_budget.wall_time_seconds * 1_000,
                                    record.plan.per_branch_budget.max_tool_calls,
                                    record.plan.per_branch_budget.max_turns,
                                ),
                                "completed_at": datetime.now(UTC),
                            }
                        )
                        await self.store.save_search_candidate(candidate)
                    reconciled.append(candidate)
                await self._persist_spend(record.plan.search_id, reconciled)
                promotion = await self.store.get_search_promotion(record.plan.search_id)
                if promotion is not None and promotion.status == "INTENT":
                    if await self._resume_promotion(run, record, promotion, reconciled):
                        continue
                await self._stop(
                    record.plan.search_id,
                    SearchStatus.REQUIRES_HUMAN,
                    SearchStopReason.VERIFIER_UNCERTAIN,
                )

    async def _resume_promotion(
        self,
        run: Run,
        record: SearchRecord,
        promotion: SearchPromotionRecord,
        candidates: list[CandidateTrajectory],
    ) -> bool:
        lease = (
            await self.store.get_lease(run.workspace_lease_id) if run.workspace_lease_id else None
        )
        winner = next(
            (item for item in candidates if item.candidate_id == promotion.candidate_id),
            None,
        )
        if winner is not None and winner.source_kind is CandidateSourceKind.REPLAY:
            task = await self.manager._require_task(run.task_id)
            valid, reasons = await self._revalidate_replay_candidate(
                record, task, winner
            )
            if not valid:
                await self.store.save_search_promotion(
                    promotion.model_copy(
                        update={"status": "CONFLICT", "completed_at": datetime.now(UTC)}
                    )
                )
                await self._reject_replay_candidate(
                    winner, phase="RECOVERY", reasons=reasons
                )
                return False
        artifacts = await self.store.list_artifacts(run.run_id)
        artifact = (
            next(
                (item for item in artifacts if item.artifact_id in winner.artifact_refs),
                None,
            )
            if winner is not None
            else None
        )
        if lease is None or artifact is None or winner is None or not Path(artifact.path).exists():
            await self.store.save_search_promotion(
                promotion.model_copy(
                    update={"status": "CONFLICT", "completed_at": datetime.now(UTC)}
                )
            )
            return False
        patch = Path(artifact.path).read_text(encoding="utf-8")
        current_digest = hashlib.sha256(
            (await self.manager.worktrees.diff(lease)).encode()
        ).hexdigest()
        valid = (
            current_digest == promotion.parent_before_sha256
            and hashlib.sha256(patch.encode()).hexdigest() == promotion.patch_sha256
        )
        if not valid:
            await self.store.save_search_promotion(
                promotion.model_copy(
                    update={"status": "CONFLICT", "completed_at": datetime.now(UTC)}
                )
            )
            return False
        await self.manager.worktrees.apply_diff(lease, patch)
        parent_after = hashlib.sha256(
            (await self.manager.worktrees.diff(lease)).encode()
        ).hexdigest()
        await self.store.save_search_promotion(
            promotion.model_copy(
                update={
                    "status": "COMPLETED",
                    "parent_after_sha256": parent_after,
                    "completed_at": datetime.now(UTC),
                }
            )
        )
        for candidate in candidates:
            if candidate.candidate_id == winner.candidate_id:
                await self.store.save_search_candidate(
                    candidate.model_copy(
                        update={
                            "status": CandidateStatus.SELECTED,
                            "terminal_reason": "promotion completed during reconciliation",
                        }
                    )
                )
            elif candidate.status is CandidateStatus.COMPLETED:
                await self.store.save_search_candidate(
                    candidate.model_copy(
                        update={
                            "status": CandidateStatus.PRUNED,
                            "terminal_reason": "not selected during promotion reconciliation",
                        }
                    )
                )
        await self._stop(
            record.plan.search_id,
            SearchStatus.SUCCEEDED,
            SearchStopReason.ACCEPTED,
            selected_candidate_id=winner.candidate_id,
        )
        return True

    async def execute_at_node(
        self,
        record: SearchRecord,
        run: Run,
        task: Task,
        lease: WorkspaceLease,
        node: RunNode,
        policy: AcceptancePolicy,
    ) -> SearchRecord:
        current = await self.get(record.plan.search_id)
        if current.status is not SearchStatus.PLANNED:
            return current
        started_at = datetime.now(UTC)
        current = await self.store.update_search(
            current.model_copy(update={"status": SearchStatus.RUNNING, "started_at": started_at}),
            expected_revision=current.revision,
        )
        await self._emit(
            run.run_id,
            EventType.SEARCH_STARTED,
            "accretion/search-started",
            {
                "search_id": current.plan.search_id,
                "parent_node_id": node.key,
                "mode": current.plan.mode.value,
                "branch_count": current.plan.branch_count,
                "max_parallel": current.plan.max_parallel,
            },
        )
        parent_patch = await self.manager.worktrees.diff(lease)
        snapshot_sha = hashlib.sha256(parent_patch.encode()).hexdigest()
        current = await self._update_record(
            current.plan.search_id, source_snapshot_sha256=snapshot_sha
        )
        assignments = await self._runtime_assignments(current, run)
        if not assignments:
            return await self._stop(
                current.plan.search_id,
                SearchStatus.STOPPED,
                SearchStopReason.PROVIDER_UNAVAILABLE,
            )
        candidates = await self._create_candidates(current, run, assignments)
        run_spent = await self.store.get_budget_spent(run.run_id)
        remaining_turns = min(
            current.plan.total_budget.max_turns,
            max(0, task.envelope.budgets.max_turns - run_spent["turns"]),
        )
        remaining_tools = min(
            current.plan.total_budget.max_tool_calls,
            max(0, task.envelope.budgets.max_tool_calls - run_spent["tool_calls"]),
        )
        if remaining_turns <= 0 or remaining_tools <= 0:
            return await self._stop(
                current.plan.search_id,
                SearchStatus.STOPPED,
                SearchStopReason.BUDGET_EXHAUSTED,
            )
        effective_parallel = min(
            current.plan.max_parallel,
            self.manager.limiter.global_limit,
            self.manager.limiter.provider_limit,
            self.manager.limiter.project_limit,
        )
        search_deadline = started_at.timestamp() + current.plan.total_budget.wall_time_seconds
        completed: list[CandidateTrajectory] = []
        for offset in range(0, len(candidates), effective_parallel):
            latest = await self.get(current.plan.search_id)
            if latest.status is SearchStatus.CANCELLED:
                return latest
            if datetime.now(UTC).timestamp() >= search_deadline:
                return await self._stop(
                    current.plan.search_id,
                    SearchStatus.STOPPED,
                    SearchStopReason.BUDGET_EXHAUSTED,
                )
            wave = candidates[offset : offset + effective_parallel]
            borrowed_candidate_id = next(
                (item.candidate_id for item in wave if item.provider is run.provider),
                wave[0].candidate_id,
            )
            jobs: list[asyncio.Task[CandidateTrajectory]] = []
            for candidate in wave:
                turns = min(current.plan.per_branch_budget.max_turns, remaining_turns)
                tools = min(current.plan.per_branch_budget.max_tool_calls, remaining_tools)
                remaining_turns -= turns
                remaining_tools -= tools
                if turns <= 0 or tools <= 0:
                    pruned = candidate.model_copy(
                        update={
                            "status": CandidateStatus.PRUNED,
                            "terminal_reason": "shared search budget was exhausted",
                            "completed_at": datetime.now(UTC),
                        }
                    )
                    await self.store.save_search_candidate(pruned)
                    completed.append(pruned)
                    continue
                jobs.append(
                    asyncio.create_task(
                        self._execute_candidate(
                            current,
                            run,
                            task,
                            lease,
                            candidate,
                            policy,
                            parent_patch,
                            search_deadline,
                            turns,
                            tools,
                            node.key,
                            candidate.candidate_id == borrowed_candidate_id,
                        )
                    )
                )
            if jobs:
                completed.extend(await asyncio.gather(*jobs))
            await self._persist_spend(current.plan.search_id, completed)
            scores = await self.store.list_candidate_scores(current.plan.search_id)
            if (
                current.plan.mode is not SearchMode.REPLAY_BRANCH
                and current.plan.stop_policy.stop_on_acceptance
                and any(item.eligible for item in scores)
            ):
                for candidate in candidates[offset + len(wave) :]:
                    pruned = candidate.model_copy(
                        update={
                            "status": CandidateStatus.PRUNED,
                            "terminal_reason": "search stopped after an acceptable wave",
                            "completed_at": datetime.now(UTC),
                        }
                    )
                    await self.store.save_search_candidate(pruned)
                    await self._emit_candidate_pruned(pruned)
                break
        latest = await self.get(current.plan.search_id)
        if latest.status is SearchStatus.CANCELLED:
            return latest
        latest = await self._update_record(current.plan.search_id, status=SearchStatus.SELECTING)
        return await self._select_and_promote(latest, run, task, lease, policy)

    async def _runtime_assignments(
        self, record: SearchRecord, run: Run
    ) -> list[tuple[Provider, Provider | None, str, str, str]]:
        health_items = []
        for provider, runtime in self.manager.runtimes.items():
            if provider in {Provider.HUMAN, Provider.DETERMINISTIC}:
                continue
            if (
                provider in {Provider.CLAUDE, Provider.CODEX}
                and not self.manager.live_providers_enabled
            ):
                continue
            health = await runtime.health()
            health_items.append(health.model_copy(update={"provider": provider}))
        by_provider = {item.provider: item for item in health_items}
        available = {
            item.provider
            for item in health_items
            if item.status in {RuntimeStatus.READY, RuntimeStatus.BUSY}
        }
        if record.plan.mode in {SearchMode.CROSS_PROVIDER, SearchMode.GENERATOR_REVIEWER}:
            if Provider.CLAUDE not in available or Provider.CODEX not in available:
                return []
            if record.plan.mode is SearchMode.GENERATOR_REVIEWER:
                providers = [Provider.CLAUDE, Provider.CODEX]
                reviewers: list[Provider | None] = [Provider.CODEX, Provider.CLAUDE]
            else:
                providers = [
                    (Provider.CLAUDE, Provider.CODEX)[index % 2]
                    for index in range(record.plan.branch_count)
                ]
                reviewers = [None] * len(providers)
        else:
            historical_quality, expected_latency = await self._historical_observations(
                run.project_id
            )
            decision = self.router.decide(
                run_id=run.run_id,
                node_id=record.plan.parent_node_id,
                health=health_items,
                historical_quality=historical_quality,
                expected_latency=expected_latency,
                specialization_fit={run.provider: 1.0},
            )
            await self.store.save_runtime_decision(decision)
            if decision.selected_runtime is None:
                return []
            providers = [decision.selected_runtime] * record.plan.branch_count
            reviewers = [None] * len(providers)
        return [
            (
                provider,
                reviewers[index],
                by_provider[provider].runtime_id,
                "default",
                by_provider[provider].runtime_version,
            )
            for index, provider in enumerate(providers)
        ]

    async def _historical_observations(
        self, project_id: str
    ) -> tuple[dict[tuple[Provider, str], float], dict[Provider, float]]:
        quality_samples: dict[tuple[Provider, str], list[float]] = {}
        latency_samples: dict[Provider, list[float]] = {}
        for run in await self.store.list_runs(limit=500):
            if run.project_id != project_id:
                continue
            for search in await self.store.list_searches(run.run_id):
                candidates = await self.store.list_search_candidates(search.plan.search_id)
                scores = {
                    item.candidate_id: item
                    for item in await self.store.list_candidate_scores(search.plan.search_id)
                }
                for candidate in candidates:
                    score = scores.get(candidate.candidate_id)
                    if score is not None and score.quality_score is not None:
                        quality_samples.setdefault(
                            (candidate.provider, candidate.runtime_version), []
                        ).append(score.quality_score)
                    if candidate.latency_ms > 0:
                        latency_samples.setdefault(candidate.provider, []).append(
                            min(
                                1.0,
                                candidate.latency_ms
                                / (search.plan.per_branch_budget.wall_time_seconds * 1_000),
                            )
                        )
        return (
            {key: sum(values) / len(values) for key, values in quality_samples.items()},
            {provider: sum(values) / len(values) for provider, values in latency_samples.items()},
        )

    async def _create_candidates(
        self,
        record: SearchRecord,
        run: Run,
        assignments: list[tuple[Provider, Provider | None, str, str, str]],
    ) -> list[CandidateTrajectory]:
        candidates: list[CandidateTrajectory] = []
        task = await self.manager._require_task(run.task_id)
        for index, (provider, reviewer, runtime_id, model, version) in enumerate(assignments):
            ordinal = index + 1
            replay_match_id = (
                record.plan.replay_seed_match_ids[index - 1]
                if record.plan.mode is SearchMode.REPLAY_BRANCH and index > 0
                else None
            )
            seed_id = new_id("trajectory_seed") if replay_match_id is not None else None
            source_experience_id = None
            segment_refs: list[str] = []
            assessment: CompatibilityAssessment | None = None
            if replay_match_id is not None:
                assert self.experience_service is not None
                planning = await self.manager.get_task_planning(task.envelope.task_id)
                query_id = planning.context_bundle.experience_query_id
                matched = next(
                    (
                        item
                        for item in await self.store.list_experience_matches(query_id or "")
                        if item.match_id == replay_match_id
                    ),
                    None,
                )
                if matched is None:
                    raise CandidateSearchConflictError(
                        "selected replay match disappeared before candidate creation"
                    )
                source_experience_id = matched.experience_id
                try:
                    _, _, assessment = await self.experience_service.revalidate_match(
                        task.envelope.task_id,
                        replay_match_id,
                        runtime_provider=provider,
                    )
                except (ExperienceConflictError, KeyError, ValueError):
                    assessment = None
                segment_refs = [
                    item.segment_id
                    for item in await self.store.list_trajectory_segments(
                        source_experience_id
                    )
                ]
            candidate = CandidateTrajectory(
                candidate_id=new_id("search_candidate"),
                search_id=record.plan.search_id,
                run_id=run.run_id,
                ordinal=ordinal,
                provider=provider,
                reviewer_provider=reviewer,
                runtime_id=runtime_id,
                runtime_model=model,
                runtime_version=version,
                source_kind=(
                    CandidateSourceKind.REPLAY
                    if replay_match_id is not None
                    else CandidateSourceKind.FRESH
                ),
                replay_seed_id=seed_id,
                source_experience_id=source_experience_id,
                source_match_id=replay_match_id,
                trajectory_segment_refs=segment_refs,
                seed_revalidation_status=(
                    SeedValidationStatus.ELIGIBLE.value
                    if replay_match_id is not None
                    and assessment is not None
                    and assessment.replay_eligible
                    else None
                ),
                seed_revalidation_reasons=(assessment.reasons if assessment else []),
            )
            await self.store.save_search_candidate(candidate)
            if replay_match_id is not None:
                assert seed_id is not None and source_experience_id is not None
                try:
                    seed = await self._build_seed(
                        record,
                        candidate,
                        replay_match_id,
                        source_experience_id,
                    )
                except CandidateSearchConflictError:
                    seed = None
                if seed is not None:
                    await self.store.save_trajectory_seed(seed)
            candidates.append(candidate)
        return candidates

    async def _build_seed(
        self,
        record: SearchRecord,
        candidate: CandidateTrajectory,
        match_id: str,
        experience_id: str,
    ) -> TrajectorySeed:
        assert candidate.replay_seed_id is not None
        segments = await self.store.list_trajectory_segments(experience_id)
        if not segments or any(
            canonical_digest(segment.content) != segment.content_digest
            for segment in segments
        ):
            raise CandidateSearchConflictError("replay source segment digest is invalid")
        guidance = self._procedural_guidance(segments)
        if not guidance:
            raise CandidateSearchConflictError("replay source has no procedural guidance")
        return TrajectorySeed(
            seed_id=candidate.replay_seed_id,
            search_id=record.plan.search_id,
            candidate_id=candidate.candidate_id,
            match_id=match_id,
            experience_id=experience_id,
            segment_ids=[item.segment_id for item in segments],
            procedural_guidance=guidance,
            assumptions=[
                "The source repository identity matches the target repository.",
                "The source commit remains an ancestor of the target commit.",
                "Current policy, verifier, and capability authority remains controlling.",
            ],
            required_revalidations=[
                "Revalidate repository, commit, manifests, and architecture.",
                "Revalidate policy, verifiers, skills, plugins, and capabilities.",
                "Revalidate the seed before launch, selection, and promotion.",
            ],
            validation_status=SeedValidationStatus.ELIGIBLE,
            revalidated_at=datetime.now(UTC),
        )

    @staticmethod
    def _procedural_guidance(segments: list[TrajectorySegment]) -> list[str]:
        """Translate redacted evidence into instructions, never provider-native state."""

        guidance: list[str] = []
        for segment in sorted(segments, key=lambda item: item.ordinal):
            content = segment.content
            if segment.kind is TrajectorySegmentKind.WORKFLOW_PATH:
                nodes = content.get("nodes", [])
                safe_nodes = [
                    redact_text(str(item))[:160]
                    for item in nodes
                    if isinstance(item, (str, int))
                ]
                guidance.append(
                    "Follow the verified workflow stages in order: "
                    + (" -> ".join(safe_nodes) if safe_nodes else "revalidate, execute, verify")
                    + "."
                )
            elif segment.kind is TrajectorySegmentKind.TOOL_SEQUENCE:
                capabilities = content.get("capabilities", [])
                safe_capabilities = [
                    redact_text(str(item))[:160]
                    for item in capabilities
                    if isinstance(item, (str, int))
                ]
                if safe_capabilities:
                    guidance.append(
                        "Preserve this verified capability ordering only where current "
                        "authority permits it: "
                        + " -> ".join(safe_capabilities)
                        + "."
                    )
            elif segment.kind is TrajectorySegmentKind.VERIFIER_FINDINGS:
                results = content.get("results", [])
                summaries: list[str] = []
                for item in results if isinstance(results, list) else []:
                    if not isinstance(item, dict):
                        continue
                    verifier = redact_text(str(item.get("verifier", "verifier")))[:160]
                    finding_codes = item.get("finding_codes", [])
                    codes = [
                        redact_text(str(code))[:160]
                        for code in finding_codes
                        if isinstance(code, (str, int))
                    ]
                    suffix = f" ({', '.join(codes)})" if codes else ""
                    summaries.append(f"{verifier}{suffix}")
                if summaries:
                    guidance.append(
                        "Run the current verifier contracts and resolve these finding classes: "
                        + "; ".join(summaries)
                        + "."
                    )
            elif segment.kind is TrajectorySegmentKind.REPAIR_PATTERN:
                iterations = content.get("completed_iterations")
                if isinstance(iterations, int) and iterations > 0:
                    guidance.append(
                        f"Allow for {iterations} verified repair iteration(s), while treating "
                        "current verifier output as authoritative."
                    )
            elif segment.kind is TrajectorySegmentKind.ARTIFACT_SHAPE:
                artifacts = content.get("artifacts", [])
                shapes: list[str] = []
                for item in artifacts if isinstance(artifacts, list) else []:
                    if not isinstance(item, dict):
                        continue
                    kind = redact_text(str(item.get("kind", "artifact")))[:160]
                    suffix = redact_text(str(item.get("suffix", "")))[:32]
                    shapes.append(f"{kind}{suffix}")
                guidance.append(
                    "Preserve the verified artifact shape under current output contracts"
                    + (": " + ", ".join(shapes) if shapes else "")
                    + "."
                )
        return guidance

    async def _revalidate_replay_candidate(
        self,
        record: SearchRecord,
        task: Task,
        candidate: CandidateTrajectory,
    ) -> tuple[bool, list[str]]:
        if candidate.source_kind is not CandidateSourceKind.REPLAY:
            return True, []
        if self.experience_service is None:
            return False, ["EXPERIENCE_SERVICE_UNAVAILABLE"]
        if candidate.source_match_id not in record.plan.replay_seed_match_ids:
            return False, ["MATCH_OUTSIDE_REPLAY_PLAN"]
        try:
            match, experience, assessment = await self.experience_service.revalidate_match(
                task.envelope.task_id,
                candidate.source_match_id or "",
                runtime_provider=candidate.provider,
            )
            seeds = await self.store.list_trajectory_seeds(record.plan.search_id)
            seed = next(
                (item for item in seeds if item.seed_id == candidate.replay_seed_id),
                None,
            )
            if seed is None:
                return False, ["TRAJECTORY_SEED_MISSING"]
            reasons = list(assessment.reasons)
            if match.experience_id != candidate.source_experience_id:
                reasons.append("EXPERIENCE_REFERENCE_CHANGED")
            if seed.candidate_id != candidate.candidate_id:
                reasons.append("SEED_CANDIDATE_MISMATCH")
            if seed.match_id != candidate.source_match_id:
                reasons.append("SEED_MATCH_MISMATCH")
            if seed.experience_id != candidate.source_experience_id:
                reasons.append("SEED_EXPERIENCE_MISMATCH")
            if seed.segment_ids != candidate.trajectory_segment_refs:
                reasons.append("SEED_SEGMENT_SET_CHANGED")
            if seed.validation_status is not SeedValidationStatus.ELIGIBLE:
                reasons.append("SEED_NOT_ELIGIBLE")
            if not seed.procedural_guidance:
                reasons.append("SEED_GUIDANCE_EMPTY")
            segments = await self.store.list_trajectory_segments(experience.experience_id)
            if [item.segment_id for item in segments] != seed.segment_ids:
                reasons.append("SOURCE_SEGMENT_SET_CHANGED")
            if any(
                canonical_digest(item.content) != item.content_digest for item in segments
            ):
                reasons.append("SOURCE_SEGMENT_DIGEST_INVALID")
            eligible = (
                experience.polarity is ExperiencePolarity.POSITIVE
                and assessment.disposition is MatchDisposition.ACCEPTED
                and assessment.replay_eligible
            )
            if not eligible:
                reasons.append("REPLAY_COMPATIBILITY_REJECTED")
            hard_failures = {
                "EXPERIENCE_REFERENCE_CHANGED",
                "SEED_CANDIDATE_MISMATCH",
                "SEED_MATCH_MISMATCH",
                "SEED_EXPERIENCE_MISMATCH",
                "SEED_SEGMENT_SET_CHANGED",
                "SEED_NOT_ELIGIBLE",
                "SEED_GUIDANCE_EMPTY",
                "SOURCE_SEGMENT_SET_CHANGED",
                "SOURCE_SEGMENT_DIGEST_INVALID",
                "REPLAY_COMPATIBILITY_REJECTED",
            }
            return not any(item in hard_failures for item in reasons), sorted(set(reasons))
        except (ExperienceConflictError, KeyError, ValueError) as exc:
            return False, [f"SEED_REVALIDATION_ERROR:{type(exc).__name__}"]

    async def _reject_replay_candidate(
        self,
        candidate: CandidateTrajectory,
        *,
        phase: str,
        reasons: list[str],
    ) -> CandidateTrajectory:
        rejected = candidate.model_copy(
            update={
                "status": CandidateStatus.PRUNED,
                "seed_revalidation_status": SeedValidationStatus.REJECTED.value,
                "seed_revalidation_reasons": sorted(set(reasons)),
                "terminal_reason": (
                    f"replay seed rejected during {phase.lower()}: "
                    + ", ".join(sorted(set(reasons)))
                )[:2_000],
                "completed_at": datetime.now(UTC),
            }
        )
        await self.store.save_search_candidate(rejected)
        causation_id = await self._replay_causation_id(
            candidate.run_id, candidate.search_id, candidate.candidate_id
        )
        await self._emit(
            candidate.run_id,
            EventType.TRAJECTORY_REPLAY_REJECTED,
            "accretion/trajectory-replay-rejected",
            {
                "search_id": candidate.search_id,
                "candidate_id": candidate.candidate_id,
                "seed_id": candidate.replay_seed_id,
                "match_id": candidate.source_match_id,
                "experience_id": candidate.source_experience_id,
                "phase": phase,
                "reasons": sorted(set(reasons)),
            },
            causation_id=causation_id,
        )
        await self._emit_candidate_pruned(rejected)
        return rejected

    async def _execute_candidate(
        self,
        record: SearchRecord,
        run: Run,
        task: Task,
        parent_lease: WorkspaceLease,
        candidate: CandidateTrajectory,
        policy: AcceptancePolicy,
        parent_patch: str,
        search_deadline: float,
        max_turns: int,
        max_tools: int,
        node_key: str,
        borrow_parent_slot: bool,
    ) -> CandidateTrajectory:
        started_at = datetime.now(UTC)
        runtime = self.manager.runtimes[candidate.provider]
        project = await self.store.get_project(run.project_id)
        if project is None:
            raise KeyError(run.project_id)
        if candidate.source_kind is CandidateSourceKind.REPLAY:
            valid, reasons = await self._revalidate_replay_candidate(
                record, task, candidate
            )
            if not valid:
                return await self._reject_replay_candidate(
                    candidate, phase="LAUNCH", reasons=reasons
                )
            candidate = candidate.model_copy(
                update={
                    "seed_revalidation_status": SeedValidationStatus.ELIGIBLE.value,
                    "seed_revalidation_reasons": reasons,
                }
            )
            await self.store.save_search_candidate(candidate)
            causation_id = await self._replay_causation_id(
                run.run_id, record.plan.search_id, candidate.candidate_id
            )
            await self._emit(
                run.run_id,
                EventType.TRAJECTORY_REPLAY_STARTED,
                "accretion/trajectory-replay-started",
                {
                    "search_id": record.plan.search_id,
                    "candidate_id": candidate.candidate_id,
                    "seed_id": candidate.replay_seed_id,
                    "match_id": candidate.source_match_id,
                    "experience_id": candidate.source_experience_id,
                    "phase": "LAUNCH",
                },
                causation_id=causation_id,
            )
        lease: WorkspaceLease | None = None
        session_id: str | None = None
        candidate_events: list[AgentEvent] = []
        try:
            async with self._candidate_slot(
                candidate.provider, run.project_id, borrowed=borrow_parent_slot
            ):
                lease = await self.manager.worktrees.acquire_candidate(
                    project_id=run.project_id,
                    run_id=run.run_id,
                    search_id=record.plan.search_id,
                    candidate_id=candidate.candidate_id,
                    repository=project.repository_path,
                    base_revision=parent_lease.base_revision,
                    parent_patch=parent_patch,
                )
                session = await runtime.create_session(
                    SessionConfig(
                        run_id=run.run_id,
                        workspace=lease.path,
                        allowed_tools=[],
                        denied_tools=sorted(
                            set(task.envelope.allowed_capabilities)
                            | set(task.envelope.denied_capabilities)
                        ),
                    )
                )
                session_id = session.session_id
                candidate = candidate.model_copy(
                    update={
                        "status": CandidateStatus.RUNNING,
                        "session_id": session.session_id,
                        "workspace_lease_id": lease.lease_id,
                        "workspace_path": str(lease.path),
                        "started_at": started_at,
                    }
                )
                await self.store.save_search_candidate(candidate)
                await self._emit(
                    run.run_id,
                    EventType.SEARCH_CANDIDATE_STARTED,
                    "accretion/search-candidate-started",
                    self._candidate_payload(candidate),
                )
                directive = await self._candidate_directive(record, candidate, task)
                deadline = min(
                    search_deadline,
                    started_at.timestamp() + record.plan.per_branch_budget.wall_time_seconds,
                )
                envelope = task.envelope.model_copy(
                    update={
                        "objective": directive,
                        "allowed_capabilities": [],
                        "denied_capabilities": sorted(
                            set(task.envelope.allowed_capabilities)
                            | set(task.envelope.denied_capabilities)
                        ),
                        "budgets": task.envelope.budgets.model_copy(
                            update={
                                "wall_time_seconds": max(
                                    1, int(deadline - datetime.now(UTC).timestamp())
                                ),
                                "max_turns": max_turns,
                                "max_tool_calls": max_tools,
                                "max_parallel_runs": 1,
                            }
                        ),
                    }
                )
                request = RuntimeExecutionRequest(
                    runtime_call_id=new_id("runtime_call"),
                    run_id=run.run_id,
                    task=envelope,
                    directive=IterationDirective(
                        kind=IterationDirectiveKind.INITIAL,
                        objective=directive,
                    ),
                    deadline=datetime.fromtimestamp(deadline, UTC),
                    max_turns=(
                        max_turns - 1 if candidate.reviewer_provider is not None else max_turns
                    ),
                    max_tool_calls=(
                        max_tools - 1 if candidate.reviewer_provider is not None else max_tools
                    ),
                )
                ref = await runtime.submit(session, request)
                self.active_refs.setdefault(record.plan.search_id, []).append((runtime, ref))
                completed = False
                cancelled = False
                tool_calls: set[str] = set()
                try:
                    async with asyncio.timeout(
                        max(0.001, deadline - datetime.now(UTC).timestamp())
                    ):
                        async for event in runtime.events(ref):
                            stored = await self.manager._append(
                                event.model_copy(
                                    update={
                                        "node_id": self.manager._node_id(run.run_id, node_key),
                                        "payload": {
                                            **event.payload,
                                            "search_id": record.plan.search_id,
                                            "candidate_id": candidate.candidate_id,
                                            "speculative": True,
                                            "runtime_call_id": submission_call_id(request),
                                        },
                                    }
                                )
                            )
                            candidate_events.append(stored)
                            if stored.normalized_type in {
                                EventType.TOOL_REQUESTED,
                                EventType.TOOL_STARTED,
                            }:
                                tool_calls.add(self.manager._tool_call_key(stored))
                                if len(tool_calls) > max_tools:
                                    await runtime.interrupt(ref)
                            elif stored.normalized_type is EventType.RUNTIME_CALL_COMPLETED:
                                completed = True
                            elif stored.normalized_type is EventType.RUNTIME_CALL_CANCELLED:
                                cancelled = True
                except TimeoutError:
                    await runtime.interrupt(ref)
                    cancelled = True
                if candidate.reviewer_provider is not None and completed and not cancelled:
                    completed = await self._run_reviewer(
                        candidate,
                        record,
                        run,
                        task,
                        lease,
                        deadline,
                        candidate_events,
                    )
                artifact = await self.manager.worktrees.capture_diff(
                    lease,
                    name=f"{record.plan.search_id}-{candidate.candidate_id}.patch",
                    kind="SEARCH_CANDIDATE_GIT_DIFF",
                )
                if artifact is not None:
                    await self.store.save_artifact(artifact)
                results = await self.manager._verify_candidate(
                    run=run,
                    task=task,
                    lease=lease,
                    session_id=session.session_id,
                    policy=policy,
                    artifact_ref=artifact.artifact_id if artifact else None,
                    diff_sha256=artifact.sha256 if artifact else None,
                    trajectory_events=candidate_events,
                )
                evaluation = evaluate_acceptance(policy, results, risk=task.envelope.risk_level)
                latency_ms = max(0, int((datetime.now(UTC) - started_at).total_seconds() * 1000))
                spent = self._spent(
                    latency_ms,
                    min(max_tools, len(tool_calls) + (1 if candidate.reviewer_provider else 0)),
                    2 if candidate.reviewer_provider is not None else 1,
                )
                score = self._score(
                    record, candidate, results, evaluation.accepted, spent, latency_ms
                )
                await self.store.save_candidate_score(score)
                candidate = candidate.model_copy(
                    update={
                        "status": (
                            CandidateStatus.CANCELLED
                            if cancelled
                            else CandidateStatus.COMPLETED
                            if completed
                            else CandidateStatus.FAILED
                        ),
                        "terminal_reason": "; ".join(evaluation.reasons),
                        "trajectory_ref": (
                            f"events:{candidate_events[0].sequence}-{candidate_events[-1].sequence}"
                            if candidate_events
                            else None
                        ),
                        "artifact_refs": [artifact.artifact_id] if artifact else [],
                        "verifier_result_refs": [item.verification_id for item in results],
                        "budget_spent": spent,
                        "latency_ms": latency_ms,
                        "patch_sha256": artifact.sha256 if artifact else None,
                        "completed_at": datetime.now(UTC),
                    }
                )
                await self.store.save_search_candidate(candidate)
                await self._emit(
                    run.run_id,
                    EventType.SEARCH_CANDIDATE_COMPLETED,
                    "accretion/search-candidate-completed",
                    {
                        **self._candidate_payload(candidate),
                        "eligible": score.eligible,
                        "total_score": score.total_score,
                    },
                )
                return candidate
        except Exception as exc:
            failed = candidate.model_copy(
                update={
                    "status": CandidateStatus.FAILED,
                    "session_id": session_id,
                    "workspace_lease_id": lease.lease_id if lease else None,
                    "workspace_path": str(lease.path) if lease else None,
                    "terminal_reason": f"{type(exc).__name__}: {exc}"[:2_000],
                    "completed_at": datetime.now(UTC),
                }
            )
            await self.store.save_search_candidate(failed)
            await self._emit(
                run.run_id,
                EventType.SEARCH_CANDIDATE_COMPLETED,
                "accretion/search-candidate-failed",
                self._candidate_payload(failed),
            )
            return failed

    async def _run_reviewer(
        self,
        candidate: CandidateTrajectory,
        record: SearchRecord,
        run: Run,
        task: Task,
        lease: WorkspaceLease,
        deadline: float,
        events: list[AgentEvent],
    ) -> bool:
        assert candidate.reviewer_provider is not None
        runtime = self.manager.runtimes[candidate.reviewer_provider]
        session = await runtime.create_session(
            SessionConfig(
                run_id=run.run_id,
                workspace=lease.path,
                allowed_tools=[],
                denied_tools=sorted(
                    set(task.envelope.allowed_capabilities) | set(task.envelope.denied_capabilities)
                ),
            )
        )
        objective = "Review the candidate without executing protected side effects."
        request = RuntimeExecutionRequest(
            runtime_call_id=new_id("runtime_call"),
            run_id=run.run_id,
            task=task.envelope.model_copy(
                update={
                    "objective": objective,
                    "allowed_capabilities": [],
                    "denied_capabilities": sorted(
                        set(task.envelope.allowed_capabilities)
                        | set(task.envelope.denied_capabilities)
                    ),
                }
            ),
            directive=IterationDirective(kind=IterationDirectiveKind.INITIAL, objective=objective),
            deadline=datetime.fromtimestamp(deadline, UTC),
            max_turns=1,
            max_tool_calls=1,
        )
        ref = await runtime.submit(session, request)
        self.active_refs.setdefault(record.plan.search_id, []).append((runtime, ref))
        completed = False
        try:
            async with asyncio.timeout(max(0.001, deadline - datetime.now(UTC).timestamp())):
                async for event in runtime.events(ref):
                    stored = await self.manager._append(
                        event.model_copy(
                            update={
                                "payload": {
                                    **event.payload,
                                    "search_id": record.plan.search_id,
                                    "candidate_id": candidate.candidate_id,
                                    "reviewer": True,
                                    "speculative": True,
                                }
                            }
                        )
                    )
                    events.append(stored)
                    if stored.normalized_type is EventType.RUNTIME_CALL_COMPLETED:
                        completed = True
        except TimeoutError:
            await runtime.interrupt(ref)
            return False
        return completed

    def _score(
        self,
        record: SearchRecord,
        candidate: CandidateTrajectory,
        results: list[VerificationResult],
        eligible: bool,
        spent: SearchBudgetSpent,
        latency_ms: int,
    ) -> CandidateScore:
        values = [item.score for item in results if item.score is not None]
        quality = sum(values) / len(values) if values else 1.0 if eligible else 0.0
        branch = record.plan.per_branch_budget
        cost = min(
            1.0,
            (spent.turns / branch.max_turns + spent.tool_calls / branch.max_tool_calls) / 2,
        )
        latency = min(1.0, latency_ms / (branch.wall_time_seconds * 1_000))
        total = round(
            quality - 0.25 * cost - 0.15 * latency,
            record.plan.stop_policy.score_precision,
        )
        return CandidateScore(
            score_id=new_id("candidate_score"),
            search_id=record.plan.search_id,
            candidate_id=candidate.candidate_id,
            verifier_policy_ref=record.plan.verifier_policy_ref,
            verifier_status=(
                VerificationStatus.PASS.value
                if eligible
                else VerificationStatus.INCONCLUSIVE.value
                if any(item.status is VerificationStatus.INCONCLUSIVE for item in results)
                else VerificationStatus.FAIL.value
            ),
            eligible=eligible,
            quality_score=round(quality, 6),
            cost_proxy=round(cost, 6),
            latency_proxy=round(latency, 6),
            risk_score=0,
            total_score=total if eligible else None,
            explanation=(
                "independent verifiers accepted the speculative candidate"
                if eligible
                else "candidate is ineligible because verification did not pass"
            ),
        )

    async def _select_and_promote(
        self,
        record: SearchRecord,
        run: Run,
        task: Task,
        parent_lease: WorkspaceLease,
        policy: AcceptancePolicy,
    ) -> SearchRecord:
        candidates = await self.store.list_search_candidates(record.plan.search_id)
        for candidate in candidates:
            if (
                candidate.source_kind is CandidateSourceKind.REPLAY
                and candidate.status is CandidateStatus.COMPLETED
            ):
                valid, reasons = await self._revalidate_replay_candidate(
                    record, task, candidate
                )
                if not valid:
                    await self._reject_replay_candidate(
                        candidate, phase="SELECTION", reasons=reasons
                    )
        candidates = await self.store.list_search_candidates(record.plan.search_id)
        scores = await self.store.list_candidate_scores(record.plan.search_id)
        by_candidate = {item.candidate_id: item for item in candidates}
        eligible = [
            item
            for item in scores
            if item.eligible
            and item.total_score is not None
            and by_candidate[item.candidate_id].status is CandidateStatus.COMPLETED
        ]
        if not eligible:
            uncertain = any(
                item.verifier_status == VerificationStatus.INCONCLUSIVE.value for item in scores
            )
            return await self._stop(
                record.plan.search_id,
                SearchStatus.REQUIRES_HUMAN if uncertain else SearchStatus.STOPPED,
                (
                    SearchStopReason.VERIFIER_UNCERTAIN
                    if uncertain
                    else SearchStopReason.CANDIDATE_FAILURE
                ),
            )
        hashes = {
            by_candidate[item.candidate_id].patch_sha256
            for item in eligible
            if by_candidate[item.candidate_id].patch_sha256 is not None
        }
        if len(eligible) > 1 and len(hashes) <= 1:
            return await self._stop(
                record.plan.search_id,
                SearchStatus.STOPPED,
                SearchStopReason.LOW_DIVERSITY,
            )
        ordered = sorted(
            eligible,
            key=lambda item: (-(item.total_score or 0), item.candidate_id),
        )
        if len(ordered) > 1:
            gain = (ordered[0].total_score or 0) - (ordered[1].total_score or 0)
            if gain == 0:
                return await self._stop(
                    record.plan.search_id,
                    SearchStatus.REQUIRES_HUMAN,
                    SearchStopReason.VERIFIER_UNCERTAIN,
                )
            if gain < record.plan.stop_policy.minimum_score_gain:
                return await self._stop(
                    record.plan.search_id,
                    SearchStatus.STOPPED,
                    SearchStopReason.LOW_EXPECTED_GAIN,
                )
        winner = by_candidate[ordered[0].candidate_id]
        artifacts = await self.store.list_artifacts(run.run_id)
        artifact = next(
            (item for item in artifacts if item.artifact_id in winner.artifact_refs),
            None,
        )
        if artifact is None or not Path(artifact.path).exists() or winner.patch_sha256 is None:
            return await self._stop(
                record.plan.search_id,
                SearchStatus.REQUIRES_HUMAN,
                SearchStopReason.VERIFIER_UNCERTAIN,
            )
        return await self._promote_winner(
            record,
            run,
            task,
            parent_lease,
            policy,
            candidates,
            winner,
            ordered[0],
            Path(artifact.path),
        )

    async def _promote_winner(
        self,
        record: SearchRecord,
        run: Run,
        task: Task,
        parent_lease: WorkspaceLease,
        policy: AcceptancePolicy,
        candidates: list[CandidateTrajectory],
        winner: CandidateTrajectory,
        winner_score: CandidateScore,
        artifact_path: Path,
    ) -> SearchRecord:
        async with self._search_lock(record.plan.search_id):
            assert winner.patch_sha256 is not None
            current = await self.get(record.plan.search_id)
            if current.status is SearchStatus.CANCELLED:
                return current
            if winner.source_kind is CandidateSourceKind.REPLAY:
                valid, reasons = await self._revalidate_replay_candidate(
                    current, task, winner
                )
                if not valid:
                    await self._reject_replay_candidate(
                        winner, phase="PROMOTION", reasons=reasons
                    )
                    return await self._stop(
                        record.plan.search_id,
                        SearchStatus.REQUIRES_HUMAN,
                        SearchStopReason.VERIFIER_UNCERTAIN,
                    )
            verifications = await self.store.list_verifications(run.run_id)
            result_by_id = {item.verification_id: item for item in verifications}
            winner_results = [
                result_by_id[item] for item in winner.verifier_result_refs if item in result_by_id
            ]
            policy_evaluation = evaluate_acceptance(
                policy, winner_results, risk=task.envelope.risk_level
            )
            if (
                len(winner_results) != len(winner.verifier_result_refs)
                or not policy_evaluation.accepted
            ):
                return await self._stop(
                    record.plan.search_id,
                    SearchStatus.REQUIRES_HUMAN,
                    SearchStopReason.VERIFIER_UNCERTAIN,
                )
            parent_before = hashlib.sha256(
                (await self.manager.worktrees.diff(parent_lease)).encode()
            ).hexdigest()
            if current.source_snapshot_sha256 != parent_before:
                return await self._stop(
                    record.plan.search_id,
                    SearchStatus.REQUIRES_HUMAN,
                    SearchStopReason.VERIFIER_UNCERTAIN,
                )
            promotion = SearchPromotionRecord(
                promotion_id=new_id("search_promotion"),
                search_id=record.plan.search_id,
                candidate_id=winner.candidate_id,
                run_id=run.run_id,
                patch_sha256=winner.patch_sha256,
                parent_before_sha256=parent_before,
            )
            await self.store.save_search_promotion(promotion)
            await self._emit(
                run.run_id,
                EventType.SEARCH_PROMOTION_STARTED,
                "accretion/search-promotion-started",
                {
                    "search_id": record.plan.search_id,
                    "candidate_id": winner.candidate_id,
                    "patch_sha256": winner.patch_sha256,
                    "parent_before_sha256": parent_before,
                },
            )
            try:
                await self.manager.worktrees.apply_diff(
                    parent_lease, artifact_path.read_text(encoding="utf-8")
                )
            except Exception:
                await self.store.save_search_promotion(
                    promotion.model_copy(
                        update={
                            "status": "CONFLICT",
                            "completed_at": datetime.now(UTC),
                        }
                    )
                )
                return await self._stop(
                    record.plan.search_id,
                    SearchStatus.REQUIRES_HUMAN,
                    SearchStopReason.VERIFIER_UNCERTAIN,
                )
            parent_after = hashlib.sha256(
                (await self.manager.worktrees.diff(parent_lease)).encode()
            ).hexdigest()
            promotion = promotion.model_copy(
                update={
                    "status": "COMPLETED",
                    "parent_after_sha256": parent_after,
                    "completed_at": datetime.now(UTC),
                }
            )
            await self.store.save_search_promotion(promotion)
            for candidate in candidates:
                if candidate.status is not CandidateStatus.COMPLETED:
                    continue
                selected = candidate.candidate_id == winner.candidate_id
                updated = candidate.model_copy(
                    update={
                        "status": (
                            CandidateStatus.SELECTED if selected else CandidateStatus.PRUNED
                        ),
                        "terminal_reason": (
                            "selected by independent candidate scorer"
                            if selected
                            else "not selected by independent candidate scorer"
                        ),
                    }
                )
                await self.store.save_search_candidate(updated)
                if not selected:
                    await self._emit_candidate_pruned(updated)
            await self._emit(
                run.run_id,
                EventType.SEARCH_SELECTION,
                "accretion/search-selection",
                {
                    "search_id": record.plan.search_id,
                    "selected_candidate_id": winner.candidate_id,
                    "score_id": winner_score.score_id,
                },
            )
            await self._emit(
                run.run_id,
                EventType.SEARCH_PROMOTION_COMPLETED,
                "accretion/search-promotion-completed",
                {
                    "search_id": record.plan.search_id,
                    "candidate_id": winner.candidate_id,
                    "parent_after_sha256": parent_after,
                },
            )
            return await self._stop(
                record.plan.search_id,
                SearchStatus.SUCCEEDED,
                SearchStopReason.ACCEPTED,
                selected_candidate_id=winner.candidate_id,
            )

    async def _persist_spend(self, search_id: str, candidates: list[CandidateTrajectory]) -> None:
        current = await self.get(search_id)
        spent = SearchBudgetSpent(
            wall_time_seconds=max(
                (item.budget_spent.wall_time_seconds for item in candidates), default=0
            ),
            turns=sum(item.budget_spent.turns for item in candidates),
            tool_calls=sum(item.budget_spent.tool_calls for item in candidates),
        )
        await self._update_record(search_id, budget_spent=spent)
        added_turns = max(0, spent.turns - current.budget_spent.turns)
        added_tools = max(0, spent.tool_calls - current.budget_spent.tool_calls)
        await self.store.add_budget_spent(
            current.plan.run_id,
            turns=added_turns,
            tool_calls=added_tools,
        )

    async def _update_record(self, search_id: str, **updates: object) -> SearchRecord:
        current = await self.get(search_id)
        if current.status is SearchStatus.CANCELLED:
            return current
        return await self.store.update_search(
            current.model_copy(update=updates), expected_revision=current.revision
        )

    async def _stop(
        self,
        search_id: str,
        status: SearchStatus,
        reason: SearchStopReason,
        *,
        selected_candidate_id: str | None = None,
    ) -> SearchRecord:
        current = await self.get(search_id)
        if current.status is SearchStatus.CANCELLED:
            return current
        updated = await self.store.update_search(
            current.model_copy(
                update={
                    "status": status,
                    "stop_reason": reason,
                    "selected_candidate_id": selected_candidate_id,
                    "completed_at": datetime.now(UTC),
                }
            ),
            expected_revision=current.revision,
        )
        await self._emit(
            updated.plan.run_id,
            EventType.SEARCH_STOPPED,
            "accretion/search-stopped",
            {
                "search_id": search_id,
                "status": status.value,
                "stop_reason": reason.value,
                "selected_candidate_id": selected_candidate_id,
            },
        )
        self.active_refs.pop(search_id, None)
        return updated

    async def _emit_candidate_pruned(self, candidate: CandidateTrajectory) -> None:
        await self._emit(
            candidate.run_id,
            EventType.SEARCH_CANDIDATE_PRUNED,
            "accretion/search-candidate-pruned",
            self._candidate_payload(candidate),
        )

    async def _replay_causation_id(
        self,
        run_id: str,
        search_id: str,
        candidate_id: str,
    ) -> str | None:
        events = await self.store.list_events(run_id)
        for event in reversed(events):
            if (
                event.normalized_type is EventType.TRAJECTORY_REPLAY_STARTED
                and event.payload.get("search_id") == search_id
                and event.payload.get("candidate_id") == candidate_id
            ):
                return event.event_id
        for event in reversed(events):
            if (
                event.normalized_type is EventType.EXPERIENCE_RETRIEVED
                and event.payload.get("search_id") == search_id
            ):
                return event.event_id
        return None

    def _search_lock(self, search_id: str) -> asyncio.Lock:
        return self.search_locks.setdefault(search_id, asyncio.Lock())

    @asynccontextmanager
    async def _candidate_slot(
        self, provider: Provider, project_id: str, *, borrowed: bool
    ) -> AsyncIterator[None]:
        # The parent graph run already owns one global/project/provider slot while
        # it waits for search. One candidate borrows that idle slot so a configured
        # limit of one cannot deadlock; every additional candidate acquires a
        # regular limiter slot.
        if borrowed:
            yield
            return
        async with self.manager.limiter.slot(provider, project_id):
            yield

    async def _emit(
        self,
        run_id: str,
        event_type: EventType,
        native_type: str,
        payload: dict[str, object],
        *,
        causation_id: str | None = None,
    ) -> AgentEvent:
        return await self.manager.emit_dynamic_event(
            run_id,
            native_type=native_type,
            event_type=event_type,
            payload=payload,
            causation_id=causation_id,
        )

    @staticmethod
    def _candidate_payload(candidate: CandidateTrajectory) -> dict[str, object]:
        return {
            "search_id": candidate.search_id,
            "candidate_id": candidate.candidate_id,
            "ordinal": candidate.ordinal,
            "provider": candidate.provider.value,
            "runtime_id": candidate.runtime_id,
            "runtime_model": candidate.runtime_model,
            "runtime_version": candidate.runtime_version,
            "source_kind": candidate.source_kind.value,
            "replay_seed_id": candidate.replay_seed_id,
            "source_experience_id": candidate.source_experience_id,
            "source_match_id": candidate.source_match_id,
            "trajectory_segment_refs": candidate.trajectory_segment_refs,
            "seed_revalidation_status": candidate.seed_revalidation_status,
            "seed_revalidation_reasons": candidate.seed_revalidation_reasons,
            "status": candidate.status.value,
            "terminal_reason": candidate.terminal_reason,
        }

    async def _candidate_directive(
        self,
        record: SearchRecord,
        candidate: CandidateTrajectory,
        task: Task,
    ) -> str:
        if record.plan.mode is SearchMode.HYPOTHESIS_BRANCH:
            suffix = record.plan.candidate_directives[candidate.ordinal - 1]
        else:
            suffix = f"Independent candidate {candidate.ordinal} of {record.plan.branch_count}."
        directive = (
            f"{task.envelope.objective}\n\n{suffix}\n"
            "Work only in the isolated local workspace. Do not execute protected external "
            "side effects or request credentials."
        )
        if candidate.source_kind is CandidateSourceKind.FRESH:
            return directive
        seeds = await self.store.list_trajectory_seeds(record.plan.search_id)
        seed = next(
            (item for item in seeds if item.seed_id == candidate.replay_seed_id),
            None,
        )
        if seed is None:
            raise CandidateSearchConflictError("replay candidate has no frozen seed")
        negative_guidance = await self._negative_guidance(
            record, candidate.provider, task
        )
        replay_lines = [
            "Verified procedural seed (guidance only; current repository, policy, and "
            "verifiers remain authoritative):",
            *(f"- {item}" for item in seed.procedural_guidance),
        ]
        if negative_guidance:
            replay_lines.extend(
                [
                    "Previously observed failure classes to avoid (guidance only):",
                    *(f"- {item}" for item in negative_guidance),
                ]
            )
        return directive + "\n\n" + "\n".join(replay_lines)

    async def _negative_guidance(
        self,
        record: SearchRecord,
        provider: Provider,
        task: Task,
    ) -> list[str]:
        if self.experience_service is None:
            return []
        guidance: list[str] = []
        for match_id in record.plan.negative_guidance_match_ids:
            try:
                _, experience, assessment = await self.experience_service.revalidate_match(
                    task.envelope.task_id,
                    match_id,
                    runtime_provider=provider,
                )
            except (ExperienceConflictError, KeyError, ValueError):
                continue
            if (
                experience.polarity is not ExperiencePolarity.NEGATIVE
                or assessment.disposition is not MatchDisposition.ACCEPTED
                or not assessment.negative_guidance_eligible
            ):
                continue
            codes = [redact_text(item)[:160] for item in experience.failure_taxonomy]
            if codes:
                guidance.append("Avoid failure taxonomy: " + ", ".join(codes) + ".")
        return guidance

    @staticmethod
    def _spent(latency_ms: int, tool_calls: int, turns: int) -> SearchBudgetSpent:
        return SearchBudgetSpent(
            wall_time_seconds=max(0, latency_ms // 1_000),
            turns=turns,
            tool_calls=tool_calls,
        )

    async def project_features(self, project_id: str) -> ProjectFeatureSettings:
        return await self.store.get_project_features(project_id)

    async def _require_enabled(self, project_id: str) -> None:
        if not self.globally_enabled:
            raise CandidateSearchDisabledError(
                "candidate search is globally disabled; set ACCRETION_ENABLE_CANDIDATE_SEARCH=true"
            )
        features = await self.store.get_project_features(project_id)
        if not features.dynamic_workflows or not features.candidate_search:
            raise CandidateSearchDisabledError(
                f"candidate search is disabled for project {project_id}"
            )
