from __future__ import annotations

from datetime import UTC, datetime

from accretion.contracts import GraphNodeKind, RunState
from accretion.ids import new_id
from accretion.orchestration.models import (
    ProjectFeatureSettings,
    SearchBudgetEnvelope,
    SearchMode,
    SearchPlan,
    SearchRecord,
    SearchStatus,
    SearchStopReason,
)
from accretion.services.run_manager import RunManager


class CandidateSearchDisabledError(PermissionError):
    pass


class CandidateSearchConflictError(RuntimeError):
    pass


class SearchService:
    """P6 authority boundary. PR 1 persists validated, inert search plans only."""

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
    ) -> SearchRecord:
        run = await self.manager._require_run(run_id)
        await self._require_enabled(run.project_id)
        if mode is SearchMode.REPLAY_BRANCH:
            raise CandidateSearchConflictError("REPLAY_BRANCH_REQUIRES_P7")
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
        if mode is SearchMode.CROSS_PROVIDER and branch_count not in {2, 4}:
            raise ValueError("cross-provider search requires two or four branches")
        task = await self.manager._require_task(run.task_id)
        budgets = task.envelope.budgets
        if max_parallel > budgets.max_parallel_runs:
            raise ValueError("search parallelism exceeds the task ceiling")
        if total_budget.wall_time_seconds > budgets.wall_time_seconds:
            raise ValueError("search wall time exceeds the task ceiling")
        if total_budget.max_turns > budgets.max_turns:
            raise ValueError("search turns exceed the task ceiling")
        if total_budget.max_tool_calls > budgets.max_tool_calls:
            raise ValueError("search tool calls exceed the task ceiling")
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
            verifier_policy_ref=(
                run.acceptance_policy_id
                or "search-verifier:" + ",".join(sorted(verifier_refs))
            ),
            requested_by=self.operator_identity,
        )
        return await self.store.create_search(SearchRecord(plan=plan))

    async def get(self, search_id: str) -> SearchRecord:
        record = await self.store.get_search(search_id)
        if record is None:
            raise KeyError(search_id)
        return record

    async def cancel(self, search_id: str) -> SearchRecord:
        record = await self.get(search_id)
        if record.status not in {SearchStatus.PLANNED, SearchStatus.RUNNING}:
            return record
        updated = record.model_copy(
            update={
                "status": SearchStatus.CANCELLED,
                "stop_reason": SearchStopReason.OPERATOR_CANCELLED,
                "completed_at": datetime.now(UTC),
            }
        )
        return await self.store.update_search(updated, expected_revision=record.revision)

    async def project_features(self, project_id: str) -> ProjectFeatureSettings:
        return await self.store.get_project_features(project_id)

    async def _require_enabled(self, project_id: str) -> None:
        if not self.globally_enabled:
            raise CandidateSearchDisabledError(
                "candidate search is globally disabled; set "
                "ACCRETION_ENABLE_CANDIDATE_SEARCH=true"
            )
        features = await self.store.get_project_features(project_id)
        if not features.dynamic_workflows or not features.candidate_search:
            raise CandidateSearchDisabledError(
                f"candidate search is disabled for project {project_id}"
            )
