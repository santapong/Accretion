from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from accretion.contracts import (
    TERMINAL_RUN_STATES,
    AcceptancePolicy,
    AgentEvent,
    ApprovalDecisionValue,
    ApprovalRecord,
    ApprovalStatus,
    ArchitectureMetric,
    ArtifactRef,
    AuthSession,
    AuthTransaction,
    BenchmarkRun,
    BenchmarkTask,
    Capability,
    CapabilityBinding,
    CapabilityExecutionResult,
    CapabilityPolicy,
    Checkpoint,
    Connection,
    ConnectionStatus,
    ConnectorDefinition,
    ContextBundle,
    ErrorSummary,
    ExecutionMode,
    LoopExecution,
    LoopExecutionStatus,
    LoopIteration,
    LoopState,
    LoopStopReason,
    MetaPlugin,
    MetaSkill,
    Principal,
    Project,
    PromptContract,
    Provider,
    Run,
    RunEdge,
    RunGraph,
    RunNode,
    RunState,
    SessionRef,
    StrategyDecision,
    StrategyOverride,
    Task,
    TaskEnvelope,
    TaskPlanning,
    TaskProfile,
    TemplateStatus,
    VerificationResult,
    WorkflowTemplate,
    WorkspaceEntity,
    WorkspaceLease,
    WorkspaceMembership,
)
from accretion.experience.models import (
    Experience,
    ExperienceEmbedding,
    ExperienceMatch,
    ExperienceQuery,
    ExperienceSelection,
    ModerationAction,
    TrajectorySeed,
    TrajectorySegment,
)
from accretion.ids import new_id
from accretion.orchestration.models import (
    CandidateScore,
    CandidateTrajectory,
    GraphValidationResult,
    ProjectFeatureSettings,
    ReplanRequest,
    RunGraphRevision,
    RuntimeDecision,
    SearchPromotionRecord,
    SearchRecord,
    WorkflowProposal,
)
from accretion.persistence.models import (
    AcceptancePolicyRow,
    AgentEventRow,
    ApprovalRow,
    ArchitectureMetricRow,
    AuthSessionRow,
    AuthTransactionRow,
    BenchmarkRunRow,
    BenchmarkTaskRow,
    CandidateScoreRow,
    CapabilityBindingRow,
    CapabilityPolicyRow,
    CapabilityRequestRow,
    CapabilityRow,
    CheckpointRow,
    ConnectionRow,
    ConnectorDefinitionRow,
    ContextBundleRow,
    ExperienceEmbeddingRow,
    ExperienceMatchRow,
    ExperienceModerationActionRow,
    ExperienceQueryRow,
    ExperienceRow,
    ExperienceSelectionRow,
    GraphValidationResultRow,
    LoopExecutionRow,
    LoopIterationRow,
    PluginRow,
    PrincipalRow,
    ProjectFeatureSettingsRow,
    ProjectRow,
    PromptContractRow,
    ReplanRequestRow,
    RunGraphEdgeRow,
    RunGraphNodeRow,
    RunGraphRevisionRow,
    RunGraphRow,
    RunRow,
    RuntimeDecisionRow,
    RuntimeSessionRow,
    SearchCandidateRow,
    SearchPlanRow,
    SearchPromotionRow,
    SkillRow,
    StrategyDecisionRow,
    StrategyOverrideRow,
    TaskProfileRow,
    TaskRow,
    TrajectoryReplaySeedRow,
    TrajectorySegmentRow,
    VerificationRow,
    WorkflowProposalRow,
    WorkflowTemplateRow,
    WorkspaceLeaseRow,
    WorkspaceMembershipRow,
    WorkspaceRow,
)

_TERMINAL_LOOP_EXECUTION_STATUSES = {
    LoopExecutionStatus.SUCCEEDED,
    LoopExecutionStatus.FAILED,
    LoopExecutionStatus.CANCELLED,
    LoopExecutionStatus.REQUIRES_HUMAN,
}

_CHECKPOINT_IDENTITY_EXCLUDED = {"checkpoint_id", "created_at"}

_APPROVAL_DECISION_STATUS = {
    ApprovalDecisionValue.APPROVE: ApprovalStatus.APPROVED,
    ApprovalDecisionValue.APPROVE_SESSION: ApprovalStatus.APPROVED,
    ApprovalDecisionValue.DENY: ApprovalStatus.DENIED,
    ApprovalDecisionValue.CANCEL: ApprovalStatus.CANCELLED,
}


def _ordered_context_history(contexts: Sequence[ContextBundle]) -> list[ContextBundle]:
    """Order immutable context revisions by lineage, not timestamp coincidence."""

    by_parent: dict[str | None, list[ContextBundle]] = {}
    for context in contexts:
        by_parent.setdefault(context.supersedes_context_bundle_id, []).append(context)
    for children in by_parent.values():
        children.sort(key=lambda item: (item.created_at, item.context_bundle_id))
    ordered: list[ContextBundle] = []
    visited: set[str] = set()

    def visit(context: ContextBundle) -> None:
        if context.context_bundle_id in visited:
            return
        visited.add(context.context_bundle_id)
        ordered.append(context)
        for child in by_parent.get(context.context_bundle_id, []):
            visit(child)

    for root in by_parent.get(None, []):
        visit(root)
    for context in sorted(contexts, key=lambda item: (item.created_at, item.context_bundle_id)):
        visit(context)
    return ordered


class StateStore(Protocol):
    async def create_project(self, project: Project) -> Project: ...
    async def get_project(self, project_id: str) -> Project | None: ...
    async def list_projects(self) -> list[Project]: ...
    async def create_task(self, task: Task) -> Task: ...
    async def create_task_with_planning(
        self,
        task: Task,
        prompt: PromptContract,
        context: ContextBundle,
        profile: TaskProfile,
        decision: StrategyDecision,
    ) -> Task: ...
    async def get_task(self, task_id: str) -> Task | None: ...
    async def save_task_planning(
        self,
        task_id: str,
        prompt: PromptContract,
        context: ContextBundle,
        profile: TaskProfile,
        decision: StrategyDecision,
    ) -> TaskPlanning: ...
    async def get_task_planning(self, task_id: str) -> TaskPlanning | None: ...
    async def revise_context_with_experience(
        self, selection: ExperienceSelection, context: ContextBundle
    ) -> ExperienceSelection: ...
    async def append_strategy_override(
        self, override: StrategyOverride, decision: StrategyDecision | None
    ) -> None: ...
    async def create_run(self, run: Run) -> Run: ...
    async def get_run(self, run_id: str) -> Run | None: ...
    async def list_runs(self, limit: int = 100) -> list[Run]: ...
    async def update_run(
        self,
        run_id: str,
        state: RunState,
        *,
        session_id: str | None = None,
        workspace_lease_id: str | None = None,
        strategy_decision_id: str | None = None,
        execution_mode: ExecutionMode | None = None,
        workflow_template_id: str | None = None,
        acceptance_policy_id: str | None = None,
        loop_execution_id: str | None = None,
        error: ErrorSummary | None = None,
    ) -> Run: ...
    async def save_acceptance_policy(self, policy: AcceptancePolicy) -> None: ...
    async def get_acceptance_policy(self, policy_id: str) -> AcceptancePolicy | None: ...
    async def create_loop_execution(self, execution: LoopExecution) -> LoopExecution: ...
    async def get_loop_execution(self, loop_execution_id: str) -> LoopExecution | None: ...
    async def get_loop_execution_for_run(self, run_id: str) -> LoopExecution | None: ...
    async def update_loop_execution(
        self,
        loop_execution_id: str,
        state: LoopState,
        *,
        status: LoopExecutionStatus | None = None,
        stop_reason: LoopStopReason | None = None,
        expected_revision: int | None = None,
    ) -> LoopExecution: ...
    async def append_loop_iteration(
        self,
        loop_execution_id: str,
        iteration: LoopIteration,
        next_state: LoopState,
        *,
        status: LoopExecutionStatus | None = None,
        stop_reason: LoopStopReason | None = None,
        expected_revision: int | None = None,
        verifications: Sequence[VerificationResult] = (),
        events: Sequence[AgentEvent] = (),
        checkpoint: Checkpoint | None = None,
    ) -> LoopExecution: ...
    async def list_loop_iterations(self, loop_execution_id: str) -> list[LoopIteration]: ...
    async def save_verification(self, result: VerificationResult) -> None: ...
    async def get_verification(self, verification_id: str) -> VerificationResult | None: ...
    async def list_verifications(
        self, run_id: str, iteration_id: str | None = None
    ) -> list[VerificationResult]: ...
    async def save_lease(self, lease: WorkspaceLease) -> None: ...
    async def get_lease(self, lease_id: str) -> WorkspaceLease | None: ...
    async def save_session(self, session: SessionRef) -> None: ...
    async def get_session_for_run(self, run_id: str) -> SessionRef | None: ...
    async def list_sessions(self, provider: Provider | None = None) -> list[SessionRef]: ...
    async def save_artifact(self, artifact: ArtifactRef) -> None: ...
    async def list_artifacts(self, run_id: str) -> list[ArtifactRef]: ...
    async def append_event(self, event: AgentEvent) -> AgentEvent: ...
    async def list_events(self, run_id: str, after: int = 0) -> list[AgentEvent]: ...
    async def upsert_workflow_template(self, template: WorkflowTemplate) -> WorkflowTemplate: ...
    async def get_workflow_template(
        self, template_id: str, version: str | None = None
    ) -> WorkflowTemplate | None: ...
    async def list_workflow_templates(
        self, status: TemplateStatus | None = None
    ) -> list[WorkflowTemplate]: ...
    async def create_run_graph(self, graph: RunGraph) -> RunGraph: ...
    async def get_run_graph(self, run_id: str) -> RunGraph | None: ...
    async def update_run_graph(
        self,
        run_graph_id: str,
        *,
        nodes: Sequence[RunNode] = (),
        edges: Sequence[RunEdge] = (),
        expected_revision: int,
    ) -> RunGraph: ...
    async def replace_run_graph(self, graph: RunGraph, *, expected_revision: int) -> RunGraph: ...
    async def append_checkpoint(
        self, checkpoint: Checkpoint, events: Sequence[AgentEvent] = ()
    ) -> Checkpoint: ...
    async def get_latest_checkpoint(self, run_id: str) -> Checkpoint | None: ...
    async def list_checkpoints(self, run_id: str) -> list[Checkpoint]: ...
    async def save_approval(self, approval: ApprovalRecord) -> ApprovalRecord: ...
    async def get_approval(self, approval_id: str) -> ApprovalRecord | None: ...
    async def list_approvals(
        self, run_id: str | None = None, status: ApprovalStatus | None = None
    ) -> list[ApprovalRecord]: ...
    async def decide_approval(
        self, approval_id: str, decision: ApprovalDecisionValue
    ) -> ApprovalRecord: ...
    async def get_loop_execution_for_node(
        self, run_id: str, node_key: str, attempt: int | None = None
    ) -> LoopExecution | None: ...
    async def list_loop_executions_for_run(self, run_id: str) -> list[LoopExecution]: ...
    async def add_budget_spent(
        self, run_id: str, *, turns: int = 0, tool_calls: int = 0
    ) -> dict[str, int]: ...
    async def get_budget_spent(self, run_id: str) -> dict[str, int]: ...
    async def upsert_capability(self, capability: Capability) -> Capability: ...
    async def get_capability(
        self, capability_id: str, version: str | None = None
    ) -> Capability | None: ...
    async def list_capabilities(self, enabled_only: bool = True) -> list[Capability]: ...
    async def upsert_skill(self, skill: MetaSkill) -> MetaSkill: ...
    async def list_skills(self) -> list[MetaSkill]: ...
    async def upsert_plugin(self, plugin: MetaPlugin) -> MetaPlugin: ...
    async def list_plugins(self, allowlisted_only: bool = True) -> list[MetaPlugin]: ...
    async def upsert_principal(self, principal: Principal) -> Principal: ...
    async def get_principal(self, principal_id: str) -> Principal | None: ...
    async def get_principal_by_identity(
        self, issuer: str, subject: str
    ) -> Principal | None: ...
    async def list_principals(self) -> list[Principal]: ...
    async def upsert_workspace(self, workspace: WorkspaceEntity) -> WorkspaceEntity: ...
    async def list_workspaces_for_principal(
        self, principal_id: str
    ) -> list[WorkspaceEntity]: ...
    async def upsert_workspace_membership(
        self, membership: WorkspaceMembership
    ) -> WorkspaceMembership: ...
    async def list_workspace_memberships(
        self,
        workspace_id: str | None = None,
        principal_id: str | None = None,
    ) -> list[WorkspaceMembership]: ...
    async def create_auth_session(self, session: AuthSession) -> AuthSession: ...
    async def get_auth_session(self, auth_session_id: str) -> AuthSession | None: ...
    async def revoke_auth_session(self, auth_session_id: str) -> None: ...
    async def create_auth_transaction(
        self, transaction: AuthTransaction
    ) -> AuthTransaction: ...
    async def consume_auth_transaction(self, state: str) -> AuthTransaction | None: ...
    async def upsert_connector_definition(
        self, connector: ConnectorDefinition
    ) -> ConnectorDefinition: ...
    async def get_connector_definition(self, connector_id: str) -> ConnectorDefinition | None: ...
    async def list_connector_definitions(self) -> list[ConnectorDefinition]: ...
    async def upsert_connection(self, connection: Connection) -> Connection: ...
    async def get_connection(self, connection_id: str) -> Connection | None: ...
    async def list_connections(
        self,
        connector_id: str | None = None,
        status: ConnectionStatus | None = None,
    ) -> list[Connection]: ...
    async def upsert_capability_binding(self, binding: CapabilityBinding) -> CapabilityBinding: ...
    async def list_capability_bindings(
        self,
        capability_id: str | None = None,
        connector_id: str | None = None,
        enabled_only: bool = True,
    ) -> list[CapabilityBinding]: ...
    async def upsert_capability_policy(self, policy: CapabilityPolicy) -> CapabilityPolicy: ...
    async def get_capability_policy(
        self, policy_id: str, version: str | None = None
    ) -> CapabilityPolicy | None: ...
    async def save_capability_result(
        self, result: CapabilityExecutionResult
    ) -> CapabilityExecutionResult: ...
    async def list_capability_results(self, run_id: str) -> list[CapabilityExecutionResult]: ...
    async def upsert_benchmark_task(self, task: BenchmarkTask) -> BenchmarkTask: ...
    async def get_benchmark_task(self, task_id: str) -> BenchmarkTask | None: ...
    async def list_benchmark_tasks(self) -> list[BenchmarkTask]: ...
    async def save_benchmark_run(
        self, run: BenchmarkRun, metrics: Sequence[ArchitectureMetric]
    ) -> BenchmarkRun: ...
    async def list_benchmark_runs(self, limit: int = 20) -> list[BenchmarkRun]: ...
    async def list_architecture_metrics(
        self, benchmark_run_id: str | None = None
    ) -> list[ArchitectureMetric]: ...
    async def get_project_features(self, project_id: str) -> ProjectFeatureSettings: ...
    async def update_project_features(
        self, settings: ProjectFeatureSettings, *, expected_revision: int
    ) -> ProjectFeatureSettings: ...
    async def save_workflow_proposal(self, proposal: WorkflowProposal) -> WorkflowProposal: ...
    async def get_workflow_proposal(self, proposal_id: str) -> WorkflowProposal | None: ...
    async def list_workflow_proposals(
        self, *, task_id: str | None = None, run_id: str | None = None
    ) -> list[WorkflowProposal]: ...
    async def save_graph_validation(
        self, result: GraphValidationResult
    ) -> GraphValidationResult: ...
    async def list_graph_validations(self, proposal_id: str) -> list[GraphValidationResult]: ...
    async def save_graph_revision(self, revision: RunGraphRevision) -> RunGraphRevision: ...
    async def list_graph_revisions(self, run_id: str) -> list[RunGraphRevision]: ...
    async def get_graph_revision(self, run_id: str, revision: int) -> RunGraphRevision | None: ...
    async def save_replan_request(self, request: ReplanRequest) -> ReplanRequest: ...
    async def list_replan_requests(self, run_id: str) -> list[ReplanRequest]: ...
    async def save_runtime_decision(self, decision: RuntimeDecision) -> RuntimeDecision: ...
    async def list_runtime_decisions(self, run_id: str) -> list[RuntimeDecision]: ...
    async def create_search(self, record: SearchRecord) -> SearchRecord: ...
    async def get_search(self, search_id: str) -> SearchRecord | None: ...
    async def list_searches(self, run_id: str) -> list[SearchRecord]: ...
    async def update_search(
        self, record: SearchRecord, *, expected_revision: int
    ) -> SearchRecord: ...
    async def save_search_candidate(
        self, candidate: CandidateTrajectory
    ) -> CandidateTrajectory: ...
    async def get_search_candidate(
        self, candidate_id: str
    ) -> CandidateTrajectory | None: ...
    async def list_search_candidates(self, search_id: str) -> list[CandidateTrajectory]: ...
    async def save_candidate_score(self, score: CandidateScore) -> CandidateScore: ...
    async def list_candidate_scores(self, search_id: str) -> list[CandidateScore]: ...
    async def save_search_promotion(
        self, promotion: SearchPromotionRecord
    ) -> SearchPromotionRecord: ...
    async def get_search_promotion(
        self, search_id: str
    ) -> SearchPromotionRecord | None: ...
    async def save_experience(
        self,
        experience: Experience,
        segments: Sequence[TrajectorySegment],
        embedding: ExperienceEmbedding,
    ) -> Experience: ...
    async def get_experience(self, experience_id: str) -> Experience | None: ...
    async def list_experiences(
        self,
        *,
        project_id: str | None = None,
        repository_identity: str | None = None,
        include_retracted: bool = False,
    ) -> list[Experience]: ...
    async def list_trajectory_segments(self, experience_id: str) -> list[TrajectorySegment]: ...
    async def get_experience_embedding(
        self, experience_id: str
    ) -> ExperienceEmbedding | None: ...
    async def nearest_experience_embeddings(
        self, repository_identity: str, vector: Sequence[float], *, limit: int
    ) -> list[tuple[str, float]]: ...
    async def save_experience_query(
        self, query: ExperienceQuery, vector: Sequence[float]
    ) -> ExperienceQuery: ...
    async def get_experience_query(
        self, query_id: str
    ) -> tuple[ExperienceQuery, list[float]] | None: ...
    async def save_experience_matches(
        self, matches: Sequence[ExperienceMatch]
    ) -> list[ExperienceMatch]: ...
    async def list_experience_matches(self, query_id: str) -> list[ExperienceMatch]: ...
    async def save_experience_selection(
        self, selection: ExperienceSelection
    ) -> ExperienceSelection: ...
    async def list_experience_selections(self, task_id: str) -> list[ExperienceSelection]: ...
    async def retract_experience(self, action: ModerationAction) -> Experience: ...
    async def list_moderation_actions(self, experience_id: str) -> list[ModerationAction]: ...
    async def save_trajectory_seed(self, seed: TrajectorySeed) -> TrajectorySeed: ...
    async def list_trajectory_seeds(self, search_id: str) -> list[TrajectorySeed]: ...


class MemoryStore:
    """Deterministic store for unit tests and protocol development, never production."""

    def __init__(self) -> None:
        self.projects: dict[str, Project] = {}
        self.tasks: dict[str, Task] = {}
        self.runs: dict[str, Run] = {}
        self.leases: dict[str, WorkspaceLease] = {}
        self.sessions: dict[str, SessionRef] = {}
        self.artifacts: dict[str, list[ArtifactRef]] = {}
        self.run_events: dict[str, list[AgentEvent]] = {}
        self.prompts: dict[str, PromptContract] = {}
        self.contexts: dict[str, ContextBundle] = {}
        self.profiles: dict[str, list[TaskProfile]] = {}
        self.decisions: dict[str, list[StrategyDecision]] = {}
        self.overrides: dict[str, list[StrategyOverride]] = {}
        self.acceptance_policies: dict[str, AcceptancePolicy] = {}
        self.loop_executions: dict[str, LoopExecution] = {}
        self.loop_execution_by_run: dict[str, str] = {}
        self.loop_execution_by_node: dict[tuple[str, str, int], str] = {}
        self.loop_iterations: dict[str, list[LoopIteration]] = {}
        self.verifications: dict[str, VerificationResult] = {}
        self.verification_ids_by_run: dict[str, list[str]] = {}
        self.workflow_templates: dict[tuple[str, str], WorkflowTemplate] = {}
        self.run_graphs: dict[str, RunGraph] = {}
        self.checkpoints: dict[str, list[Checkpoint]] = {}
        self.approvals: dict[str, ApprovalRecord] = {}
        self.approval_by_request: dict[tuple[str, str], str] = {}
        self.budget_spent: dict[str, dict[str, int]] = {}
        self.capabilities: dict[tuple[str, str], Capability] = {}
        self.skills: dict[tuple[str, str], MetaSkill] = {}
        self.plugins: dict[tuple[str, str], MetaPlugin] = {}
        self.capability_policies: dict[tuple[str, str], CapabilityPolicy] = {}
        self.principals: dict[str, Principal] = {}
        self.workspaces: dict[str, WorkspaceEntity] = {}
        self.workspace_memberships: dict[tuple[str, str], WorkspaceMembership] = {}
        self.auth_sessions: dict[str, AuthSession] = {}
        self.auth_transactions: dict[str, AuthTransaction] = {}
        self.connector_definitions: dict[str, ConnectorDefinition] = {}
        self.connections: dict[str, Connection] = {}
        self.capability_bindings: dict[str, CapabilityBinding] = {}
        self.capability_results: dict[str, CapabilityExecutionResult] = {}
        self.benchmark_tasks: dict[tuple[str, str], BenchmarkTask] = {}
        self.benchmark_runs: dict[str, BenchmarkRun] = {}
        self.architecture_metrics: dict[str, list[ArchitectureMetric]] = {}
        self.project_features: dict[str, ProjectFeatureSettings] = {}
        self.workflow_proposals: dict[str, WorkflowProposal] = {}
        self.graph_validations: dict[str, list[GraphValidationResult]] = {}
        self.graph_revisions: dict[str, list[RunGraphRevision]] = {}
        self.replan_requests: dict[str, ReplanRequest] = {}
        self.runtime_decisions: dict[str, RuntimeDecision] = {}
        self.searches: dict[str, SearchRecord] = {}
        self.search_candidates: dict[str, CandidateTrajectory] = {}
        self.candidate_scores: dict[str, CandidateScore] = {}
        self.search_promotions: dict[str, SearchPromotionRecord] = {}
        self.experiences: dict[str, Experience] = {}
        self.experience_source_keys: dict[tuple[str, str | None], str] = {}
        self.trajectory_segments: dict[str, list[TrajectorySegment]] = {}
        self.experience_embeddings: dict[str, ExperienceEmbedding] = {}
        self.experience_queries: dict[str, tuple[ExperienceQuery, list[float]]] = {}
        self.experience_matches: dict[str, list[ExperienceMatch]] = {}
        self.experience_selections: dict[str, list[ExperienceSelection]] = {}
        self.moderation_actions: dict[str, list[ModerationAction]] = {}
        self.trajectory_seeds: dict[str, list[TrajectorySeed]] = {}
        self._lock = asyncio.Lock()

    async def create_project(self, project: Project) -> Project:
        self.projects[project.project_id] = project
        return project

    async def get_project(self, project_id: str) -> Project | None:
        return self.projects.get(project_id)

    async def list_projects(self) -> list[Project]:
        return sorted(self.projects.values(), key=lambda project: project.created_at)

    async def create_task(self, task: Task) -> Task:
        self.tasks[task.envelope.task_id] = task
        return task

    async def create_task_with_planning(
        self,
        task: Task,
        prompt: PromptContract,
        context: ContextBundle,
        profile: TaskProfile,
        decision: StrategyDecision,
    ) -> Task:
        async with self._lock:
            planned = task.model_copy(
                update={
                    "prompt_contract_id": prompt.prompt_contract_id,
                    "context_bundle_id": context.context_bundle_id,
                    "current_profile_id": profile.profile_id,
                    "current_strategy_decision_id": decision.decision_id,
                }
            )
            self.tasks[task.envelope.task_id] = planned
            self.prompts[prompt.prompt_contract_id] = prompt
            self.contexts[context.context_bundle_id] = context
            self.profiles[task.envelope.task_id] = [profile]
            self.decisions[task.envelope.task_id] = [decision]
            self.overrides[task.envelope.task_id] = []
            return planned

    async def get_task(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    async def save_task_planning(
        self,
        task_id: str,
        prompt: PromptContract,
        context: ContextBundle,
        profile: TaskProfile,
        decision: StrategyDecision,
    ) -> TaskPlanning:
        async with self._lock:
            task = self.tasks[task_id]
            if task.current_strategy_decision_id is None:
                self.tasks[task_id] = task.model_copy(
                    update={
                        "prompt_contract_id": prompt.prompt_contract_id,
                        "context_bundle_id": context.context_bundle_id,
                        "current_profile_id": profile.profile_id,
                        "current_strategy_decision_id": decision.decision_id,
                    }
                )
                self.prompts[prompt.prompt_contract_id] = prompt
                self.contexts[context.context_bundle_id] = context
                self.profiles[task_id] = [profile]
                self.decisions[task_id] = [decision]
                self.overrides[task_id] = []
        planning = await self.get_task_planning(task_id)
        if planning is None:
            raise RuntimeError("planning records were not saved")
        return planning

    async def get_task_planning(self, task_id: str) -> TaskPlanning | None:
        task = self.tasks.get(task_id)
        if (
            task is None
            or task.prompt_contract_id is None
            or task.context_bundle_id is None
            or task.current_profile_id is None
            or task.current_strategy_decision_id is None
        ):
            return None
        profiles = self.profiles.get(task_id, [])
        decisions = self.decisions.get(task_id, [])
        current_profile = next(
            profile for profile in profiles if profile.profile_id == task.current_profile_id
        )
        current_decision = next(
            decision
            for decision in decisions
            if decision.decision_id == task.current_strategy_decision_id
        )
        return TaskPlanning(
            task_id=task_id,
            prompt_contract=self.prompts[task.prompt_contract_id],
            context_bundle=self.contexts[task.context_bundle_id],
            current_profile=current_profile,
            current_decision=current_decision,
            context_history=_ordered_context_history(
                [
                    context
                    for context in self.contexts.values()
                    if context.task_ref == task_id
                ]
            ),
            profile_history=profiles,
            decision_history=decisions,
            override_history=self.overrides.get(task_id, []),
        )

    async def revise_context_with_experience(
        self, selection: ExperienceSelection, context: ContextBundle
    ) -> ExperienceSelection:
        async with self._lock:
            task = self.tasks.get(selection.task_id)
            if task is None:
                raise KeyError(selection.task_id)
            if await self.list_workflow_proposals(task_id=selection.task_id):
                raise ValueError("experience selection is frozen after workflow proposal")
            if task.context_bundle_id != selection.expected_context_bundle_id:
                raise ValueError("context bundle revision conflict")
            if context.context_bundle_id != selection.resulting_context_bundle_id:
                raise ValueError("selection resulting context does not match context revision")
            if context.task_ref != selection.task_id:
                raise ValueError("context revision belongs to another task")
            self.contexts[context.context_bundle_id] = context
            self.tasks[selection.task_id] = task.model_copy(
                update={"context_bundle_id": context.context_bundle_id}
            )
            self.experience_selections.setdefault(selection.task_id, []).append(selection)
        return selection

    async def append_strategy_override(
        self, override: StrategyOverride, decision: StrategyDecision | None
    ) -> None:
        async with self._lock:
            self.overrides.setdefault(override.task_id, []).append(override)
            if decision is not None:
                self.decisions.setdefault(override.task_id, []).append(decision)
                task = self.tasks[override.task_id]
                self.tasks[override.task_id] = task.model_copy(
                    update={"current_strategy_decision_id": decision.decision_id}
                )

    async def create_run(self, run: Run) -> Run:
        self.runs[run.run_id] = run
        return run

    async def get_run(self, run_id: str) -> Run | None:
        return self.runs.get(run_id)

    async def list_runs(self, limit: int = 100) -> list[Run]:
        return sorted(self.runs.values(), key=lambda run: run.created_at, reverse=True)[:limit]

    async def update_run(
        self,
        run_id: str,
        state: RunState,
        *,
        session_id: str | None = None,
        workspace_lease_id: str | None = None,
        strategy_decision_id: str | None = None,
        execution_mode: ExecutionMode | None = None,
        workflow_template_id: str | None = None,
        acceptance_policy_id: str | None = None,
        loop_execution_id: str | None = None,
        error: ErrorSummary | None = None,
    ) -> Run:
        current = self.runs[run_id]
        updated = current.model_copy(
            update={
                "state": state,
                "session_id": session_id if session_id is not None else current.session_id,
                "workspace_lease_id": (
                    workspace_lease_id
                    if workspace_lease_id is not None
                    else current.workspace_lease_id
                ),
                "strategy_decision_id": (
                    strategy_decision_id
                    if strategy_decision_id is not None
                    else current.strategy_decision_id
                ),
                "execution_mode": (
                    execution_mode if execution_mode is not None else current.execution_mode
                ),
                "workflow_template_id": (
                    workflow_template_id
                    if workflow_template_id is not None
                    else current.workflow_template_id
                ),
                "acceptance_policy_id": (
                    acceptance_policy_id
                    if acceptance_policy_id is not None
                    else current.acceptance_policy_id
                ),
                "loop_execution_id": (
                    loop_execution_id
                    if loop_execution_id is not None
                    else current.loop_execution_id
                ),
                "error": error,
                "revision": current.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self.runs[run_id] = updated
        return updated

    async def save_acceptance_policy(self, policy: AcceptancePolicy) -> None:
        async with self._lock:
            current = self.acceptance_policies.get(policy.policy_id)
            if current is not None and current != policy:
                raise ValueError(f"acceptance policy {policy.policy_id} is immutable")
            self.acceptance_policies[policy.policy_id] = policy

    async def get_acceptance_policy(self, policy_id: str) -> AcceptancePolicy | None:
        return self.acceptance_policies.get(policy_id)

    async def create_loop_execution(self, execution: LoopExecution) -> LoopExecution:
        async with self._lock:
            if execution.acceptance_policy_ref not in self.acceptance_policies:
                raise KeyError(execution.acceptance_policy_ref)
            if execution.run_id not in self.runs:
                raise KeyError(execution.run_id)
            if execution.loop_execution_id in self.loop_executions:
                raise ValueError(f"loop execution {execution.loop_execution_id} already exists")
            node_slot = (execution.run_id, execution.node_key, execution.attempt)
            if node_slot in self.loop_execution_by_node:
                raise ValueError(
                    f"run {execution.run_id} already has loop attempt "
                    f"{execution.attempt} for node {execution.node_key}"
                )
            policy = self.acceptance_policies[execution.acceptance_policy_ref]
            stored = execution.model_copy(update={"acceptance_policy": policy})
            self.loop_executions[stored.loop_execution_id] = stored
            self.loop_execution_by_run[stored.run_id] = stored.loop_execution_id
            self.loop_execution_by_node[node_slot] = stored.loop_execution_id
            self.loop_iterations[stored.loop_execution_id] = []
            run = self.runs[stored.run_id]
            self.runs[stored.run_id] = run.model_copy(
                update={
                    "acceptance_policy_id": stored.acceptance_policy_ref,
                    "loop_execution_id": stored.loop_execution_id,
                    "revision": run.revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            return stored

    async def get_loop_execution(self, loop_execution_id: str) -> LoopExecution | None:
        return self.loop_executions.get(loop_execution_id)

    async def get_loop_execution_for_run(self, run_id: str) -> LoopExecution | None:
        loop_execution_id = self.loop_execution_by_run.get(run_id)
        return self.loop_executions.get(loop_execution_id) if loop_execution_id else None

    async def get_loop_execution_for_node(
        self, run_id: str, node_key: str, attempt: int | None = None
    ) -> LoopExecution | None:
        if attempt is not None:
            loop_execution_id = self.loop_execution_by_node.get((run_id, node_key, attempt))
            return self.loop_executions.get(loop_execution_id) if loop_execution_id else None
        candidates = [
            execution
            for execution in self.loop_executions.values()
            if execution.run_id == run_id and execution.node_key == node_key
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda execution: execution.attempt)

    async def list_loop_executions_for_run(self, run_id: str) -> list[LoopExecution]:
        return sorted(
            (
                execution
                for execution in self.loop_executions.values()
                if execution.run_id == run_id
            ),
            key=lambda execution: (execution.created_at, execution.loop_execution_id),
        )

    async def update_loop_execution(
        self,
        loop_execution_id: str,
        state: LoopState,
        *,
        status: LoopExecutionStatus | None = None,
        stop_reason: LoopStopReason | None = None,
        expected_revision: int | None = None,
    ) -> LoopExecution:
        async with self._lock:
            return self._update_memory_loop_execution(
                loop_execution_id,
                state,
                status=status,
                stop_reason=stop_reason,
                expected_revision=expected_revision,
            )

    async def append_loop_iteration(
        self,
        loop_execution_id: str,
        iteration: LoopIteration,
        next_state: LoopState,
        *,
        status: LoopExecutionStatus | None = None,
        stop_reason: LoopStopReason | None = None,
        expected_revision: int | None = None,
        verifications: Sequence[VerificationResult] = (),
        events: Sequence[AgentEvent] = (),
        checkpoint: Checkpoint | None = None,
    ) -> LoopExecution:
        async with self._lock:
            current = self.loop_executions[loop_execution_id]
            self._validate_iteration_transition(current, iteration, next_state)
            if expected_revision is not None and current.revision != expected_revision:
                raise ValueError("loop execution revision conflict")
            self._validate_iteration_evidence(iteration, verifications, events)
            if any(
                stored.iteration_id == iteration.iteration_id
                for iterations in self.loop_iterations.values()
                for stored in iterations
            ):
                raise ValueError("iteration identifier already exists")
            if any(result.verification_id in self.verifications for result in verifications):
                raise ValueError("verification identifier already exists")
            existing_event_ids = {
                stored.event_id for stored in self.run_events.get(iteration.run_id, [])
            }
            new_event_ids = {event.event_id for event in events}
            if len(new_event_ids) != len(events) or existing_event_ids.intersection(new_event_ids):
                raise ValueError("event identifier already exists")
            self.loop_iterations[loop_execution_id].append(iteration)
            for result in verifications:
                self.verifications[result.verification_id] = result
                self.verification_ids_by_run.setdefault(result.run_id, []).append(
                    result.verification_id
                )
            run_events = self.run_events.setdefault(iteration.run_id, [])
            run = self.runs[iteration.run_id]
            for event in events:
                stored_event = event.model_copy(update={"sequence": len(run_events) + 1})
                run_events.append(stored_event)
            if events:
                self.runs[iteration.run_id] = run.model_copy(
                    update={"last_sequence": run_events[-1].sequence}
                )
            if checkpoint is not None:
                self._append_memory_checkpoint(
                    checkpoint.model_copy(
                        update={"sequence": self.runs[iteration.run_id].last_sequence}
                    )
                )
            return self._update_memory_loop_execution(
                loop_execution_id,
                next_state,
                status=status,
                stop_reason=stop_reason,
                expected_revision=current.revision,
            )

    async def list_loop_iterations(self, loop_execution_id: str) -> list[LoopIteration]:
        return list(self.loop_iterations.get(loop_execution_id, []))

    async def save_verification(self, result: VerificationResult) -> None:
        async with self._lock:
            current = self.verifications.get(result.verification_id)
            if current is not None:
                if current != result:
                    raise ValueError(f"verification {result.verification_id} is immutable")
                return
            if result.run_id not in self.runs:
                raise KeyError(result.run_id)
            if result.iteration_id is not None and not any(
                item.iteration_id == result.iteration_id
                for iterations in self.loop_iterations.values()
                for item in iterations
            ):
                raise ValueError("iteration verification must be saved with append_loop_iteration")
            self.verifications[result.verification_id] = result
            self.verification_ids_by_run.setdefault(result.run_id, []).append(
                result.verification_id
            )

    async def get_verification(self, verification_id: str) -> VerificationResult | None:
        return self.verifications.get(verification_id)

    async def list_verifications(
        self, run_id: str, iteration_id: str | None = None
    ) -> list[VerificationResult]:
        results = [
            self.verifications[verification_id]
            for verification_id in self.verification_ids_by_run.get(run_id, [])
        ]
        if iteration_id is not None:
            results = [result for result in results if result.iteration_id == iteration_id]
        return sorted(results, key=lambda result: (result.executed_at, result.verification_id))

    def _update_memory_loop_execution(
        self,
        loop_execution_id: str,
        state: LoopState,
        *,
        status: LoopExecutionStatus | None,
        stop_reason: LoopStopReason | None,
        expected_revision: int | None,
    ) -> LoopExecution:
        current = self.loop_executions[loop_execution_id]
        if current.status in _TERMINAL_LOOP_EXECUTION_STATUSES:
            raise ValueError("terminal loop execution is immutable")
        if expected_revision is not None and current.revision != expected_revision:
            raise ValueError("loop execution revision conflict")
        next_status = status or current.status
        now = datetime.now(UTC)
        completed_at = (
            now if next_status in _TERMINAL_LOOP_EXECUTION_STATUSES else current.completed_at
        )
        updated = current.model_copy(
            update={
                "state": state,
                "status": next_status,
                "stop_reason": stop_reason,
                "revision": current.revision + 1,
                "updated_at": now,
                "completed_at": completed_at,
            }
        )
        self.loop_executions[loop_execution_id] = updated
        return updated

    @staticmethod
    def _validate_iteration_transition(
        execution: LoopExecution, iteration: LoopIteration, next_state: LoopState
    ) -> None:
        if execution.status in _TERMINAL_LOOP_EXECUTION_STATUSES:
            raise ValueError("terminal loop execution cannot accept iterations")
        if iteration.loop_execution_id != execution.loop_execution_id:
            raise ValueError("iteration belongs to a different loop execution")
        if iteration.run_id != execution.run_id:
            raise ValueError("iteration belongs to a different run")
        expected_number = execution.state.iteration + 1
        if iteration.number != expected_number or next_state.iteration != expected_number:
            raise ValueError(f"expected loop iteration {expected_number}")

    @staticmethod
    def _validate_iteration_evidence(
        iteration: LoopIteration,
        verifications: Sequence[VerificationResult],
        events: Sequence[AgentEvent],
    ) -> None:
        verification_ids = {result.verification_id for result in verifications}
        if len(verification_ids) != len(verifications):
            raise ValueError("verification identifiers must be unique")
        if set(iteration.verification_refs) != verification_ids:
            raise ValueError("iteration verification refs must match persisted verifications")
        if any(
            result.run_id != iteration.run_id or result.iteration_id != iteration.iteration_id
            for result in verifications
        ):
            raise ValueError("verification belongs to a different run or iteration")
        if any(event.run_id != iteration.run_id for event in events):
            raise ValueError("event belongs to a different run")

    async def save_lease(self, lease: WorkspaceLease) -> None:
        self.leases[lease.lease_id] = lease

    async def get_lease(self, lease_id: str) -> WorkspaceLease | None:
        return self.leases.get(lease_id)

    async def save_session(self, session: SessionRef) -> None:
        self.sessions[session.run_id] = session

    async def get_session_for_run(self, run_id: str) -> SessionRef | None:
        return self.sessions.get(run_id)

    async def list_sessions(self, provider: Provider | None = None) -> list[SessionRef]:
        sessions = [
            item for item in self.sessions.values() if provider is None or item.provider is provider
        ]
        return sorted(sessions, key=lambda item: (item.provider.value, item.run_id))

    async def save_artifact(self, artifact: ArtifactRef) -> None:
        self.artifacts.setdefault(artifact.run_id, []).append(artifact)

    async def list_artifacts(self, run_id: str) -> list[ArtifactRef]:
        return self.artifacts.get(run_id, [])

    async def append_event(self, event: AgentEvent) -> AgentEvent:
        async with self._lock:
            events = self.run_events.setdefault(event.run_id, [])
            stored = event.model_copy(update={"sequence": len(events) + 1})
            events.append(stored)
            run = self.runs.get(event.run_id)
            if run is not None:
                self.runs[event.run_id] = run.model_copy(update={"last_sequence": stored.sequence})
            return stored

    async def list_events(self, run_id: str, after: int = 0) -> list[AgentEvent]:
        return [event for event in self.run_events.get(run_id, []) if event.sequence > after]

    async def upsert_workflow_template(self, template: WorkflowTemplate) -> WorkflowTemplate:
        async with self._lock:
            key = (template.template_id, template.version)
            existing = self.workflow_templates.get(key)
            if existing is not None:
                if existing.checksum != template.checksum:
                    raise ValueError(
                        f"workflow template {template.template_id} {template.version} "
                        "content drifted from the stored checksum"
                    )
                return existing
            self.workflow_templates[key] = template
            return template

    async def get_workflow_template(
        self, template_id: str, version: str | None = None
    ) -> WorkflowTemplate | None:
        if version is not None:
            return self.workflow_templates.get((template_id, version))
        validated = [
            template
            for template in self.workflow_templates.values()
            if template.template_id == template_id and template.status is TemplateStatus.VALIDATED
        ]
        if len(validated) > 1:
            raise ValueError(
                f"template {template_id} has multiple VALIDATED versions; pass one explicitly"
            )
        return validated[0] if validated else None

    async def list_workflow_templates(
        self, status: TemplateStatus | None = None
    ) -> list[WorkflowTemplate]:
        templates = [
            template
            for template in self.workflow_templates.values()
            if status is None or template.status is status
        ]
        return sorted(templates, key=lambda template: (template.template_id, template.version))

    async def create_run_graph(self, graph: RunGraph) -> RunGraph:
        async with self._lock:
            if graph.run_id not in self.runs:
                raise KeyError(graph.run_id)
            if graph.run_id in self.run_graphs:
                raise ValueError(f"run {graph.run_id} already has a run graph")
            if not any(
                template.template_record_id == graph.template_record_id
                for template in self.workflow_templates.values()
            ):
                raise KeyError(graph.template_record_id)
            self.run_graphs[graph.run_id] = graph
            return graph

    async def get_run_graph(self, run_id: str) -> RunGraph | None:
        return self.run_graphs.get(run_id)

    async def update_run_graph(
        self,
        run_graph_id: str,
        *,
        nodes: Sequence[RunNode] = (),
        edges: Sequence[RunEdge] = (),
        expected_revision: int,
    ) -> RunGraph:
        async with self._lock:
            return self._update_memory_run_graph(
                run_graph_id, nodes=nodes, edges=edges, expected_revision=expected_revision
            )

    async def replace_run_graph(
        self, graph: RunGraph, *, expected_revision: int
    ) -> RunGraph:
        async with self._lock:
            current = self.run_graphs.get(graph.run_id)
            if current is None or current.run_graph_id != graph.run_graph_id:
                raise KeyError(graph.run_graph_id)
            if current.graph_revision != expected_revision:
                raise ValueError("run graph revision conflict")
            node_ids = [node.node_id for node in graph.nodes]
            edge_ids = [edge.edge_id for edge in graph.edges]
            if len(node_ids) != len(set(node_ids)) or len(edge_ids) != len(set(edge_ids)):
                raise ValueError("replacement graph identifiers must be unique")
            updated = graph.model_copy(update={"graph_revision": expected_revision + 1})
            self.run_graphs[graph.run_id] = updated
            return updated

    def _update_memory_run_graph(
        self,
        run_graph_id: str,
        *,
        nodes: Sequence[RunNode],
        edges: Sequence[RunEdge],
        expected_revision: int,
    ) -> RunGraph:
        graph = next(
            (item for item in self.run_graphs.values() if item.run_graph_id == run_graph_id),
            None,
        )
        if graph is None:
            raise KeyError(run_graph_id)
        if graph.graph_revision != expected_revision:
            raise ValueError("run graph revision conflict")
        node_by_key = {node.key: node for node in graph.nodes}
        for node in nodes:
            current = node_by_key.get(node.key)
            if current is None:
                raise ValueError(f"run graph has no node {node.key}")
            if node.node_id != current.node_id:
                raise ValueError("run graph node ids are immutable")
            node_by_key[node.key] = node
        edge_by_key = {edge.key: edge for edge in graph.edges}
        for edge in edges:
            current_edge = edge_by_key.get(edge.key)
            if current_edge is None:
                raise ValueError(f"run graph has no edge {edge.key}")
            if edge.edge_id != current_edge.edge_id:
                raise ValueError("run graph edge ids are immutable")
            edge_by_key[edge.key] = edge
        updated = graph.model_copy(
            update={
                "nodes": [node_by_key[node.key] for node in graph.nodes],
                "edges": [edge_by_key[edge.key] for edge in graph.edges],
                "graph_revision": graph.graph_revision + 1,
            }
        )
        self.run_graphs[graph.run_id] = updated
        return updated

    def _append_memory_checkpoint(self, checkpoint: Checkpoint) -> Checkpoint:
        run = self.runs.get(checkpoint.run_id)
        if run is None:
            raise KeyError(checkpoint.run_id)
        if run.state in TERMINAL_RUN_STATES:
            raise ValueError("terminal run cannot accept new checkpoints")
        stored_list = self.checkpoints.setdefault(checkpoint.run_id, [])
        existing = next(
            (item for item in stored_list if item.sequence == checkpoint.sequence), None
        )
        if existing is not None:
            identity = checkpoint.model_dump(mode="json", exclude=_CHECKPOINT_IDENTITY_EXCLUDED)
            if existing.model_dump(mode="json", exclude=_CHECKPOINT_IDENTITY_EXCLUDED) != identity:
                raise ValueError(
                    f"checkpoint at sequence {checkpoint.sequence} is immutable evidence"
                )
            return existing
        stored_list.append(checkpoint)
        return checkpoint

    async def append_checkpoint(
        self, checkpoint: Checkpoint, events: Sequence[AgentEvent] = ()
    ) -> Checkpoint:
        async with self._lock:
            run = self.runs.get(checkpoint.run_id)
            if run is None:
                raise KeyError(checkpoint.run_id)
            run_events = self.run_events.setdefault(checkpoint.run_id, [])
            for event in events:
                stored_event = event.model_copy(update={"sequence": len(run_events) + 1})
                run_events.append(stored_event)
            if events:
                run = run.model_copy(update={"last_sequence": run_events[-1].sequence})
                self.runs[checkpoint.run_id] = run
            return self._append_memory_checkpoint(
                checkpoint.model_copy(update={"sequence": run.last_sequence})
            )

    async def get_latest_checkpoint(self, run_id: str) -> Checkpoint | None:
        stored = self.checkpoints.get(run_id, [])
        if not stored:
            return None
        return max(stored, key=lambda checkpoint: checkpoint.sequence)

    async def list_checkpoints(self, run_id: str) -> list[Checkpoint]:
        return sorted(self.checkpoints.get(run_id, []), key=lambda checkpoint: checkpoint.sequence)

    async def save_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        async with self._lock:
            if approval.run_id not in self.runs:
                raise KeyError(approval.run_id)
            request_key = (approval.run_id, approval.native_request_id)
            existing_id = self.approval_by_request.get(request_key)
            if existing_id is not None:
                return self.approvals[existing_id]
            self.approvals[approval.approval_id] = approval
            self.approval_by_request[request_key] = approval.approval_id
            return approval

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        return self.approvals.get(approval_id)

    async def list_approvals(
        self, run_id: str | None = None, status: ApprovalStatus | None = None
    ) -> list[ApprovalRecord]:
        records = [
            approval
            for approval in self.approvals.values()
            if (run_id is None or approval.run_id == run_id)
            and (status is None or approval.status is status)
        ]
        return sorted(records, key=lambda approval: (approval.created_at, approval.approval_id))

    async def decide_approval(
        self, approval_id: str, decision: ApprovalDecisionValue
    ) -> ApprovalRecord:
        async with self._lock:
            current = self.approvals.get(approval_id)
            if current is None:
                raise KeyError(approval_id)
            if current.status is not ApprovalStatus.PENDING:
                raise ValueError(f"approval {approval_id} was already decided")
            decided = current.model_copy(
                update={
                    "status": _APPROVAL_DECISION_STATUS[decision],
                    "decision": decision,
                    "decided_at": datetime.now(UTC),
                }
            )
            self.approvals[approval_id] = decided
            return decided

    async def add_budget_spent(
        self, run_id: str, *, turns: int = 0, tool_calls: int = 0
    ) -> dict[str, int]:
        async with self._lock:
            if run_id not in self.runs:
                raise KeyError(run_id)
            spent = self.budget_spent.setdefault(run_id, {"turns": 0, "tool_calls": 0})
            spent["turns"] += turns
            spent["tool_calls"] += tool_calls
            return dict(spent)

    async def get_budget_spent(self, run_id: str) -> dict[str, int]:
        return dict(self.budget_spent.get(run_id, {"turns": 0, "tool_calls": 0}))

    async def upsert_capability(self, capability: Capability) -> Capability:
        key = (capability.capability_id, capability.version)
        current = self.capabilities.get(key)
        if current is not None and current != capability:
            raise ValueError(
                f"capability {capability.capability_id}@{capability.version} is immutable"
            )
        self.capabilities[key] = capability
        return capability

    async def get_capability(
        self, capability_id: str, version: str | None = None
    ) -> Capability | None:
        if version is not None:
            return self.capabilities.get((capability_id, version))
        candidates = [
            item for (item_id, _), item in self.capabilities.items() if item_id == capability_id
        ]
        return max(candidates, key=lambda item: item.created_at) if candidates else None

    async def list_capabilities(self, enabled_only: bool = True) -> list[Capability]:
        return sorted(
            (item for item in self.capabilities.values() if item.enabled or not enabled_only),
            key=lambda item: (item.capability_id, item.version),
        )

    async def upsert_skill(self, skill: MetaSkill) -> MetaSkill:
        key = (skill.skill_id, skill.version)
        current = self.skills.get(key)
        if current is not None and current != skill:
            raise ValueError(f"skill {skill.skill_id}@{skill.version} is immutable")
        self.skills[key] = skill
        return skill

    async def list_skills(self) -> list[MetaSkill]:
        return sorted(self.skills.values(), key=lambda item: (item.skill_id, item.version))

    async def upsert_plugin(self, plugin: MetaPlugin) -> MetaPlugin:
        key = (plugin.plugin_id, plugin.version)
        current = self.plugins.get(key)
        if current is not None and current != plugin:
            raise ValueError(f"plugin {plugin.plugin_id}@{plugin.version} is immutable")
        self.plugins[key] = plugin
        return plugin

    async def list_plugins(self, allowlisted_only: bool = True) -> list[MetaPlugin]:
        return sorted(
            (item for item in self.plugins.values() if item.allowlisted or not allowlisted_only),
            key=lambda item: (item.plugin_id, item.version),
        )

    async def upsert_principal(self, principal: Principal) -> Principal:
        existing = await self.get_principal_by_identity(principal.issuer, principal.subject)
        if existing is not None:
            principal = principal.model_copy(
                update={
                    "principal_id": existing.principal_id,
                    "created_at": existing.created_at,
                }
            )
        self.principals[principal.principal_id] = principal
        return principal

    async def get_principal(self, principal_id: str) -> Principal | None:
        return self.principals.get(principal_id)

    async def get_principal_by_identity(self, issuer: str, subject: str) -> Principal | None:
        for item in self.principals.values():
            if item.issuer == issuer and item.subject == subject:
                return item
        return None

    async def list_principals(self) -> list[Principal]:
        return sorted(self.principals.values(), key=lambda item: item.principal_id)

    async def upsert_workspace(self, workspace: WorkspaceEntity) -> WorkspaceEntity:
        self.workspaces[workspace.workspace_id] = workspace
        return workspace

    async def list_workspaces_for_principal(self, principal_id: str) -> list[WorkspaceEntity]:
        member_of = {
            item.workspace_id
            for item in self.workspace_memberships.values()
            if item.principal_id == principal_id
        }
        return sorted(
            (item for item in self.workspaces.values() if item.workspace_id in member_of),
            key=lambda item: item.workspace_id,
        )

    async def upsert_workspace_membership(
        self, membership: WorkspaceMembership
    ) -> WorkspaceMembership:
        key = (membership.workspace_id, membership.principal_id)
        existing = self.workspace_memberships.get(key)
        if existing is not None:
            membership = membership.model_copy(
                update={
                    "membership_id": existing.membership_id,
                    "created_at": existing.created_at,
                    "revision": existing.revision
                    + (1 if existing.role != membership.role else 0),
                }
            )
        self.workspace_memberships[key] = membership
        return membership

    async def list_workspace_memberships(
        self,
        workspace_id: str | None = None,
        principal_id: str | None = None,
    ) -> list[WorkspaceMembership]:
        return sorted(
            (
                item
                for item in self.workspace_memberships.values()
                if (workspace_id is None or item.workspace_id == workspace_id)
                and (principal_id is None or item.principal_id == principal_id)
            ),
            key=lambda item: item.membership_id,
        )

    async def create_auth_session(self, session: AuthSession) -> AuthSession:
        self.auth_sessions[session.auth_session_id] = session
        return session

    async def get_auth_session(self, auth_session_id: str) -> AuthSession | None:
        session = self.auth_sessions.get(auth_session_id)
        if session is None or session.revoked or session.expires_at <= datetime.now(UTC):
            return None
        return session

    async def revoke_auth_session(self, auth_session_id: str) -> None:
        session = self.auth_sessions.get(auth_session_id)
        if session is not None:
            self.auth_sessions[auth_session_id] = session.model_copy(
                update={"revoked": True}
            )

    async def create_auth_transaction(self, transaction: AuthTransaction) -> AuthTransaction:
        self.auth_transactions[transaction.state] = transaction
        return transaction

    async def consume_auth_transaction(self, state: str) -> AuthTransaction | None:
        transaction = self.auth_transactions.pop(state, None)
        if transaction is None or transaction.expires_at <= datetime.now(UTC):
            return None
        return transaction

    async def upsert_connector_definition(
        self, connector: ConnectorDefinition
    ) -> ConnectorDefinition:
        self.connector_definitions[connector.connector_id] = connector
        return connector

    async def get_connector_definition(self, connector_id: str) -> ConnectorDefinition | None:
        return self.connector_definitions.get(connector_id)

    async def list_connector_definitions(self) -> list[ConnectorDefinition]:
        return sorted(self.connector_definitions.values(), key=lambda item: item.connector_id)

    async def upsert_connection(self, connection: Connection) -> Connection:
        self.connections[connection.connection_id] = connection
        return connection

    async def get_connection(self, connection_id: str) -> Connection | None:
        return self.connections.get(connection_id)

    async def list_connections(
        self,
        connector_id: str | None = None,
        status: ConnectionStatus | None = None,
    ) -> list[Connection]:
        return sorted(
            (
                item
                for item in self.connections.values()
                if (connector_id is None or item.connector_id == connector_id)
                and (status is None or item.status == status)
            ),
            key=lambda item: item.connection_id,
        )

    async def upsert_capability_binding(self, binding: CapabilityBinding) -> CapabilityBinding:
        self.capability_bindings[binding.binding_id] = binding
        return binding

    async def list_capability_bindings(
        self,
        capability_id: str | None = None,
        connector_id: str | None = None,
        enabled_only: bool = True,
    ) -> list[CapabilityBinding]:
        return sorted(
            (
                item
                for item in self.capability_bindings.values()
                if (capability_id is None or item.capability_id == capability_id)
                and (connector_id is None or item.connector_id == connector_id)
                and (item.enabled or not enabled_only)
            ),
            key=lambda item: item.binding_id,
        )

    async def upsert_capability_policy(self, policy: CapabilityPolicy) -> CapabilityPolicy:
        key = (policy.policy_id, policy.version)
        current = self.capability_policies.get(key)
        if current is not None and current != policy:
            raise ValueError(f"policy {policy.policy_id}@{policy.version} is immutable")
        self.capability_policies[key] = policy
        return policy

    async def get_capability_policy(
        self, policy_id: str, version: str | None = None
    ) -> CapabilityPolicy | None:
        if version is not None:
            return self.capability_policies.get((policy_id, version))
        candidates = [
            item for (item_id, _), item in self.capability_policies.items() if item_id == policy_id
        ]
        return max(candidates, key=lambda item: item.created_at) if candidates else None

    async def save_capability_result(
        self, result: CapabilityExecutionResult
    ) -> CapabilityExecutionResult:
        self.capability_results[result.request.request_id] = result
        return result

    async def list_capability_results(self, run_id: str) -> list[CapabilityExecutionResult]:
        return sorted(
            (item for item in self.capability_results.values() if item.request.run_id == run_id),
            key=lambda item: item.request.created_at,
        )

    async def upsert_benchmark_task(self, task: BenchmarkTask) -> BenchmarkTask:
        key = (task.benchmark_task_id, task.version)
        current = self.benchmark_tasks.get(key)
        if current is not None and current != task:
            raise ValueError(f"benchmark task {task.benchmark_task_id}@{task.version} is immutable")
        self.benchmark_tasks[key] = task
        return task

    async def get_benchmark_task(self, task_id: str) -> BenchmarkTask | None:
        candidates = [
            item for (item_id, _), item in self.benchmark_tasks.items() if item_id == task_id
        ]
        return max(candidates, key=lambda item: item.version) if candidates else None

    async def list_benchmark_tasks(self) -> list[BenchmarkTask]:
        return sorted(
            self.benchmark_tasks.values(),
            key=lambda item: (item.benchmark_task_id, item.version),
        )

    async def save_benchmark_run(
        self, run: BenchmarkRun, metrics: Sequence[ArchitectureMetric]
    ) -> BenchmarkRun:
        if len(metrics) != run.scenario_count or any(
            item.benchmark_run_id != run.benchmark_run_id for item in metrics
        ):
            raise ValueError("benchmark run metrics do not match the run manifest")
        if len({item.metric_id for item in metrics}) != len(metrics):
            raise ValueError("benchmark metric identifiers must be unique")
        current = self.benchmark_runs.get(run.benchmark_run_id)
        if current is not None and (
            current != run or self.architecture_metrics.get(run.benchmark_run_id) != list(metrics)
        ):
            raise ValueError(f"benchmark run {run.benchmark_run_id} is immutable")
        self.benchmark_runs[run.benchmark_run_id] = run
        self.architecture_metrics[run.benchmark_run_id] = list(metrics)
        return run

    async def list_benchmark_runs(self, limit: int = 20) -> list[BenchmarkRun]:
        return sorted(self.benchmark_runs.values(), key=lambda item: item.started_at, reverse=True)[
            :limit
        ]

    async def list_architecture_metrics(
        self, benchmark_run_id: str | None = None
    ) -> list[ArchitectureMetric]:
        if benchmark_run_id is not None:
            return list(self.architecture_metrics.get(benchmark_run_id, []))
        return [
            metric
            for run_id in sorted(self.architecture_metrics)
            for metric in self.architecture_metrics[run_id]
        ]

    async def get_project_features(self, project_id: str) -> ProjectFeatureSettings:
        if project_id not in self.projects:
            raise KeyError(project_id)
        return self.project_features.get(project_id, ProjectFeatureSettings(project_id=project_id))

    async def update_project_features(
        self, settings: ProjectFeatureSettings, *, expected_revision: int
    ) -> ProjectFeatureSettings:
        current = await self.get_project_features(settings.project_id)
        if current.revision != expected_revision:
            raise ValueError("project feature revision conflict")
        updated = settings.model_copy(
            update={"revision": expected_revision + 1, "updated_at": datetime.now(UTC)}
        )
        self.project_features[settings.project_id] = updated
        return updated

    async def save_workflow_proposal(self, proposal: WorkflowProposal) -> WorkflowProposal:
        current = self.workflow_proposals.get(proposal.proposal_id)
        if current is not None and current != proposal:
            raise ValueError(f"workflow proposal {proposal.proposal_id} is immutable")
        self.workflow_proposals[proposal.proposal_id] = proposal
        return proposal

    async def get_workflow_proposal(self, proposal_id: str) -> WorkflowProposal | None:
        return self.workflow_proposals.get(proposal_id)

    async def list_workflow_proposals(
        self, *, task_id: str | None = None, run_id: str | None = None
    ) -> list[WorkflowProposal]:
        return sorted(
            (
                item
                for item in self.workflow_proposals.values()
                if (task_id is None or item.task_id == task_id)
                and (run_id is None or item.run_id == run_id)
            ),
            key=lambda item: item.created_at,
        )

    async def save_graph_validation(self, result: GraphValidationResult) -> GraphValidationResult:
        items = self.graph_validations.setdefault(result.proposal_id, [])
        current = next((item for item in items if item.validation_id == result.validation_id), None)
        if current is not None and current != result:
            raise ValueError(f"graph validation {result.validation_id} is immutable")
        if current is None:
            items.append(result)
        return result

    async def list_graph_validations(self, proposal_id: str) -> list[GraphValidationResult]:
        return sorted(self.graph_validations.get(proposal_id, []), key=lambda item: item.created_at)

    async def save_graph_revision(self, revision: RunGraphRevision) -> RunGraphRevision:
        items = self.graph_revisions.setdefault(revision.run_id, [])
        current = next((item for item in items if item.revision == revision.revision), None)
        if current is not None and current != revision:
            raise ValueError(
                f"run graph revision {revision.run_id}/{revision.revision} is immutable"
            )
        if current is None:
            if items and revision.revision != items[-1].revision + 1:
                raise ValueError("run graph revisions must be contiguous")
            items.append(revision)
        return revision

    async def list_graph_revisions(self, run_id: str) -> list[RunGraphRevision]:
        return sorted(self.graph_revisions.get(run_id, []), key=lambda item: item.revision)

    async def get_graph_revision(self, run_id: str, revision: int) -> RunGraphRevision | None:
        return next(
            (item for item in self.graph_revisions.get(run_id, []) if item.revision == revision),
            None,
        )

    async def save_replan_request(self, request: ReplanRequest) -> ReplanRequest:
        current = self.replan_requests.get(request.replan_request_id)
        if current is not None and current.run_id != request.run_id:
            raise ValueError("replan request identity is immutable")
        self.replan_requests[request.replan_request_id] = request
        return request

    async def list_replan_requests(self, run_id: str) -> list[ReplanRequest]:
        return sorted(
            (item for item in self.replan_requests.values() if item.run_id == run_id),
            key=lambda item: item.created_at,
        )

    async def save_runtime_decision(self, decision: RuntimeDecision) -> RuntimeDecision:
        current = self.runtime_decisions.get(decision.decision_id)
        if current is not None and current != decision:
            raise ValueError(f"runtime decision {decision.decision_id} is immutable")
        self.runtime_decisions[decision.decision_id] = decision
        return decision

    async def list_runtime_decisions(self, run_id: str) -> list[RuntimeDecision]:
        return sorted(
            (item for item in self.runtime_decisions.values() if item.run_id == run_id),
            key=lambda item: item.created_at,
        )

    async def create_search(self, record: SearchRecord) -> SearchRecord:
        if record.plan.run_id not in self.runs:
            raise KeyError(record.plan.run_id)
        if record.plan.search_id in self.searches:
            raise ValueError(f"search {record.plan.search_id} already exists")
        if any(
            item.plan.run_id == record.plan.run_id
            and item.plan.graph_revision == record.plan.graph_revision
            and item.plan.parent_node_id == record.plan.parent_node_id
            for item in self.searches.values()
        ):
            raise ValueError("a search already exists for this graph node revision")
        self.searches[record.plan.search_id] = record
        return record

    async def get_search(self, search_id: str) -> SearchRecord | None:
        return self.searches.get(search_id)

    async def list_searches(self, run_id: str) -> list[SearchRecord]:
        return sorted(
            (item for item in self.searches.values() if item.plan.run_id == run_id),
            key=lambda item: item.plan.created_at,
        )

    async def update_search(
        self, record: SearchRecord, *, expected_revision: int
    ) -> SearchRecord:
        current = self.searches.get(record.plan.search_id)
        if current is None:
            raise KeyError(record.plan.search_id)
        if current.plan != record.plan:
            raise ValueError("search plan is immutable")
        if current.revision != expected_revision:
            raise ValueError("search revision conflict")
        updated = record.model_copy(
            update={"revision": expected_revision + 1, "updated_at": datetime.now(UTC)}
        )
        self.searches[record.plan.search_id] = updated
        return updated

    async def save_search_candidate(
        self, candidate: CandidateTrajectory
    ) -> CandidateTrajectory:
        if candidate.search_id not in self.searches:
            raise KeyError(candidate.search_id)
        current = self.search_candidates.get(candidate.candidate_id)
        if current is not None and (
            current.search_id != candidate.search_id or current.ordinal != candidate.ordinal
        ):
            raise ValueError("candidate identity is immutable")
        self.search_candidates[candidate.candidate_id] = candidate
        return candidate

    async def get_search_candidate(
        self, candidate_id: str
    ) -> CandidateTrajectory | None:
        return self.search_candidates.get(candidate_id)

    async def list_search_candidates(self, search_id: str) -> list[CandidateTrajectory]:
        return sorted(
            (item for item in self.search_candidates.values() if item.search_id == search_id),
            key=lambda item: item.ordinal,
        )

    async def save_candidate_score(self, score: CandidateScore) -> CandidateScore:
        if score.search_id not in self.searches:
            raise KeyError(score.search_id)
        current = self.candidate_scores.get(score.candidate_id)
        if current is not None and current != score:
            raise ValueError(f"candidate score for {score.candidate_id} is immutable")
        self.candidate_scores[score.candidate_id] = score
        return score

    async def list_candidate_scores(self, search_id: str) -> list[CandidateScore]:
        return sorted(
            (item for item in self.candidate_scores.values() if item.search_id == search_id),
            key=lambda item: item.candidate_id,
        )

    async def save_search_promotion(
        self, promotion: SearchPromotionRecord
    ) -> SearchPromotionRecord:
        current = self.search_promotions.get(promotion.search_id)
        if current is not None and (
            current.promotion_id != promotion.promotion_id
            or current.candidate_id != promotion.candidate_id
        ):
            raise ValueError("search promotion identity is immutable")
        self.search_promotions[promotion.search_id] = promotion
        return promotion

    async def get_search_promotion(
        self, search_id: str
    ) -> SearchPromotionRecord | None:
        return self.search_promotions.get(search_id)

    async def save_experience(
        self,
        experience: Experience,
        segments: Sequence[TrajectorySegment],
        embedding: ExperienceEmbedding,
    ) -> Experience:
        source_key = (experience.source_run_id, experience.source_candidate_id)
        async with self._lock:
            existing_id = self.experience_source_keys.get(source_key)
            if existing_id is not None:
                existing = self.experiences[existing_id]
                if (
                    existing != experience
                    or self.trajectory_segments[existing_id] != list(segments)
                    or self.experience_embeddings[existing_id] != embedding
                ):
                    raise ValueError("conflicting experience evidence for terminal source")
                return existing
            if any(segment.experience_id != experience.experience_id for segment in segments):
                raise ValueError("trajectory segment belongs to another experience")
            if embedding.experience_id != experience.experience_id:
                raise ValueError("embedding belongs to another experience")
            ordinals = [segment.ordinal for segment in segments]
            if not segments or len(set(ordinals)) != len(ordinals):
                raise ValueError("experience requires uniquely ordered trajectory segments")
            self.experiences[experience.experience_id] = experience
            self.experience_source_keys[source_key] = experience.experience_id
            self.trajectory_segments[experience.experience_id] = sorted(
                segments, key=lambda item: item.ordinal
            )
            self.experience_embeddings[experience.experience_id] = embedding
            return experience

    async def get_experience(self, experience_id: str) -> Experience | None:
        return self.experiences.get(experience_id)

    async def list_experiences(
        self,
        *,
        project_id: str | None = None,
        repository_identity: str | None = None,
        include_retracted: bool = False,
    ) -> list[Experience]:
        items = self.experiences.values()
        return sorted(
            (
                item
                for item in items
                if (project_id is None or item.project_id == project_id)
                and (
                    repository_identity is None
                    or item.repository_identity == repository_identity
                )
                and (include_retracted or not item.retracted)
            ),
            key=lambda item: (item.created_at, item.experience_id),
        )

    async def list_trajectory_segments(self, experience_id: str) -> list[TrajectorySegment]:
        return list(self.trajectory_segments.get(experience_id, []))

    async def get_experience_embedding(
        self, experience_id: str
    ) -> ExperienceEmbedding | None:
        return self.experience_embeddings.get(experience_id)

    async def nearest_experience_embeddings(
        self, repository_identity: str, vector: Sequence[float], *, limit: int
    ) -> list[tuple[str, float]]:
        values = [float(value) for value in vector]
        if len(values) != 384:
            raise ValueError("experience query vector must contain 384 dimensions")
        query_norm = sum(value * value for value in values) ** 0.5
        ranked: list[tuple[str, float]] = []
        for experience_id, embedding in self.experience_embeddings.items():
            experience = self.experiences[experience_id]
            if experience.repository_identity != repository_identity:
                continue
            source_norm = sum(value * value for value in embedding.vector) ** 0.5
            denominator = query_norm * source_norm
            similarity = (
                sum(left * right for left, right in zip(values, embedding.vector, strict=True))
                / denominator
                if denominator
                else 0.0
            )
            ranked.append((experience_id, 1 - similarity))
        return sorted(ranked, key=lambda item: (item[1], item[0]))[:limit]

    async def save_experience_query(
        self, query: ExperienceQuery, vector: Sequence[float]
    ) -> ExperienceQuery:
        values = [float(value) for value in vector]
        if len(values) != 384:
            raise ValueError("experience query vector must contain 384 dimensions")
        current = self.experience_queries.get(query.query_id)
        if current is not None and current != (query, values):
            raise ValueError("experience query is immutable")
        self.experience_queries[query.query_id] = (query, values)
        return query

    async def get_experience_query(
        self, query_id: str
    ) -> tuple[ExperienceQuery, list[float]] | None:
        current = self.experience_queries.get(query_id)
        if current is None:
            return None
        return current[0], list(current[1])

    async def save_experience_matches(
        self, matches: Sequence[ExperienceMatch]
    ) -> list[ExperienceMatch]:
        if not matches:
            return []
        query_id = matches[0].query_id
        if any(match.query_id != query_id for match in matches):
            raise ValueError("experience matches must belong to one query")
        if query_id not in self.experience_queries:
            raise KeyError(query_id)
        current = self.experience_matches.get(query_id)
        if current is not None and current != list(matches):
            raise ValueError("experience matches are immutable")
        self.experience_matches[query_id] = list(matches)
        return list(matches)

    async def list_experience_matches(self, query_id: str) -> list[ExperienceMatch]:
        return sorted(self.experience_matches.get(query_id, []), key=lambda item: item.rank)

    async def save_experience_selection(
        self, selection: ExperienceSelection
    ) -> ExperienceSelection:
        items = self.experience_selections.setdefault(selection.task_id, [])
        current = next(
            (item for item in items if item.selection_id == selection.selection_id), None
        )
        if current is not None:
            if current != selection:
                raise ValueError("experience selection is immutable")
            return current
        items.append(selection)
        return selection

    async def list_experience_selections(self, task_id: str) -> list[ExperienceSelection]:
        return sorted(
            self.experience_selections.get(task_id, []), key=lambda item: item.created_at
        )

    async def retract_experience(self, action: ModerationAction) -> Experience:
        async with self._lock:
            current = self.experiences.get(action.experience_id)
            if current is None:
                raise KeyError(action.experience_id)
            existing = next(
                (
                    item
                    for item in self.moderation_actions.get(action.experience_id, [])
                    if item.action_id == action.action_id
                ),
                None,
            )
            if existing is not None:
                if existing != action:
                    raise ValueError("moderation action is immutable")
                return current
            if current.revision != action.expected_revision:
                raise ValueError("experience revision conflict")
            updated = current.model_copy(
                update={"revision": action.resulting_revision, "retracted": True}
            )
            self.experiences[action.experience_id] = updated
            self.moderation_actions.setdefault(action.experience_id, []).append(action)
            return updated

    async def list_moderation_actions(self, experience_id: str) -> list[ModerationAction]:
        return sorted(
            self.moderation_actions.get(experience_id, []), key=lambda item: item.created_at
        )

    async def save_trajectory_seed(self, seed: TrajectorySeed) -> TrajectorySeed:
        items = self.trajectory_seeds.setdefault(seed.search_id, [])
        current = next((item for item in items if item.seed_id == seed.seed_id), None)
        if current is not None:
            if current != seed:
                raise ValueError("trajectory seed is immutable")
            return current
        if any(item.candidate_id == seed.candidate_id for item in items):
            raise ValueError("candidate already has a trajectory seed")
        items.append(seed)
        return seed

    async def list_trajectory_seeds(self, search_id: str) -> list[TrajectorySeed]:
        return sorted(self.trajectory_seeds.get(search_id, []), key=lambda item: item.created_at)


class PostgresStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def create_project(self, project: Project) -> Project:
        async with self.sessions.begin() as session:
            session.add(
                ProjectRow(
                    id=project.project_id,
                    name=project.name,
                    repository_path=str(project.repository_path),
                    created_at=project.created_at,
                )
            )
        return project

    async def get_project(self, project_id: str) -> Project | None:
        async with self.sessions() as session:
            row = await session.get(ProjectRow, project_id)
        if row is None:
            return None
        return Project(
            project_id=row.id,
            name=row.name,
            repository_path=row.repository_path,
            created_at=row.created_at,
        )

    async def list_projects(self) -> list[Project]:
        async with self.sessions() as session:
            rows = (await session.scalars(select(ProjectRow).order_by(ProjectRow.created_at))).all()
        return [
            Project(
                project_id=row.id,
                name=row.name,
                repository_path=row.repository_path,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def create_task(self, task: Task) -> Task:
        async with self.sessions.begin() as session:
            session.add(
                TaskRow(
                    id=task.envelope.task_id,
                    project_id=task.envelope.project_id,
                    envelope=task.envelope.model_dump(mode="json"),
                    prompt_contract_id=task.prompt_contract_id,
                    context_bundle_id=task.context_bundle_id,
                    current_profile_id=task.current_profile_id,
                    current_strategy_decision_id=task.current_strategy_decision_id,
                    created_at=task.created_at,
                )
            )
        return task

    async def create_task_with_planning(
        self,
        task: Task,
        prompt: PromptContract,
        context: ContextBundle,
        profile: TaskProfile,
        decision: StrategyDecision,
    ) -> Task:
        planned = task.model_copy(
            update={
                "prompt_contract_id": prompt.prompt_contract_id,
                "context_bundle_id": context.context_bundle_id,
                "current_profile_id": profile.profile_id,
                "current_strategy_decision_id": decision.decision_id,
            }
        )
        async with self.sessions.begin() as session:
            session.add(self._task_to_row(planned))
            await session.flush()
            await self._add_planning_rows(session, prompt, context, profile, decision)
        return planned

    async def get_task(self, task_id: str) -> Task | None:
        async with self.sessions() as session:
            row = await session.get(TaskRow, task_id)
        if row is None:
            return None
        return self._row_to_task(row)

    async def save_task_planning(
        self,
        task_id: str,
        prompt: PromptContract,
        context: ContextBundle,
        profile: TaskProfile,
        decision: StrategyDecision,
    ) -> TaskPlanning:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(TaskRow).where(TaskRow.id == task_id).with_for_update()
            )
            if row is None:
                raise KeyError(task_id)
            if row.current_strategy_decision_id is None:
                await self._add_planning_rows(session, prompt, context, profile, decision)
                row.prompt_contract_id = prompt.prompt_contract_id
                row.context_bundle_id = context.context_bundle_id
                row.current_profile_id = profile.profile_id
                row.current_strategy_decision_id = decision.decision_id
        planning = await self.get_task_planning(task_id)
        if planning is None:
            raise RuntimeError("planning records were not saved")
        return planning

    async def get_task_planning(self, task_id: str) -> TaskPlanning | None:
        async with self.sessions() as session:
            task = await session.get(TaskRow, task_id)
            if task is None:
                raise KeyError(task_id)
            if (
                task.prompt_contract_id is None
                or task.context_bundle_id is None
                or task.current_profile_id is None
                or task.current_strategy_decision_id is None
            ):
                return None
            prompt = await session.get(PromptContractRow, task.prompt_contract_id)
            context = await session.get(ContextBundleRow, task.context_bundle_id)
            context_rows = (
                await session.scalars(
                    select(ContextBundleRow)
                    .where(ContextBundleRow.task_id == task_id)
                    .order_by(ContextBundleRow.created_at, ContextBundleRow.id)
                )
            ).all()
            current_profile = await session.get(TaskProfileRow, task.current_profile_id)
            current_decision = await session.get(
                StrategyDecisionRow, task.current_strategy_decision_id
            )
            profile_rows = (
                await session.scalars(
                    select(TaskProfileRow)
                    .where(TaskProfileRow.task_id == task_id)
                    .order_by(TaskProfileRow.created_at, TaskProfileRow.id)
                )
            ).all()
            decision_rows = (
                await session.scalars(
                    select(StrategyDecisionRow)
                    .where(StrategyDecisionRow.task_id == task_id)
                    .order_by(StrategyDecisionRow.created_at, StrategyDecisionRow.id)
                )
            ).all()
            override_rows = (
                await session.scalars(
                    select(StrategyOverrideRow)
                    .where(StrategyOverrideRow.task_id == task_id)
                    .order_by(StrategyOverrideRow.created_at, StrategyOverrideRow.id)
                )
            ).all()
        if prompt is None or context is None or current_profile is None or current_decision is None:
            raise RuntimeError(f"task {task_id} has incomplete planning references")
        return TaskPlanning(
            task_id=task_id,
            prompt_contract=PromptContract.model_validate(prompt.contract),
            context_bundle=ContextBundle.model_validate(context.bundle),
            current_profile=TaskProfile.model_validate(current_profile.profile),
            current_decision=StrategyDecision.model_validate(current_decision.decision),
            context_history=_ordered_context_history(
                [ContextBundle.model_validate(row.bundle) for row in context_rows]
            ),
            profile_history=[TaskProfile.model_validate(row.profile) for row in profile_rows],
            decision_history=[
                StrategyDecision.model_validate(row.decision) for row in decision_rows
            ],
            override_history=[
                StrategyOverride.model_validate(row.override) for row in override_rows
            ],
        )

    async def revise_context_with_experience(
        self, selection: ExperienceSelection, context: ContextBundle
    ) -> ExperienceSelection:
        async with self.sessions.begin() as session:
            task = await session.scalar(
                select(TaskRow)
                .where(TaskRow.id == selection.task_id)
                .with_for_update()
            )
            if task is None:
                raise KeyError(selection.task_id)
            proposal = await session.scalar(
                select(WorkflowProposalRow.id)
                .where(WorkflowProposalRow.task_id == selection.task_id)
                .limit(1)
            )
            if proposal is not None:
                raise ValueError("experience selection is frozen after workflow proposal")
            if task.context_bundle_id != selection.expected_context_bundle_id:
                raise ValueError("context bundle revision conflict")
            if context.context_bundle_id != selection.resulting_context_bundle_id:
                raise ValueError("selection resulting context does not match context revision")
            if context.task_ref != selection.task_id:
                raise ValueError("context revision belongs to another task")
            session.add(
                ContextBundleRow(
                    id=context.context_bundle_id,
                    task_id=context.task_ref,
                    version=context.version,
                    bundle=context.model_dump(mode="json"),
                    created_at=context.created_at,
                )
            )
            await session.flush()
            session.add(
                ExperienceSelectionRow(
                    id=selection.selection_id,
                    task_id=selection.task_id,
                    query_id=selection.query_id,
                    expected_context_bundle_id=selection.expected_context_bundle_id,
                    resulting_context_bundle_id=selection.resulting_context_bundle_id,
                    record=selection.model_dump(mode="json"),
                    created_at=selection.created_at,
                )
            )
            task.context_bundle_id = context.context_bundle_id
        return selection

    async def append_strategy_override(
        self, override: StrategyOverride, decision: StrategyDecision | None
    ) -> None:
        async with self.sessions.begin() as session:
            task = await session.scalar(
                select(TaskRow).where(TaskRow.id == override.task_id).with_for_update()
            )
            if task is None:
                raise KeyError(override.task_id)
            if task.current_strategy_decision_id != override.original_decision_id:
                raise ValueError("strategy decision changed before the override could be recorded")
            if decision is not None:
                session.add(self._decision_to_row(decision))
                await session.flush()
                task.current_strategy_decision_id = decision.decision_id
            session.add(
                StrategyOverrideRow(
                    id=override.override_id,
                    task_id=override.task_id,
                    original_decision_id=override.original_decision_id,
                    resulting_decision_id=override.resulting_decision_id,
                    operator_identity=override.operator_identity,
                    accepted=override.accepted,
                    override=override.model_dump(mode="json"),
                    created_at=override.created_at,
                )
            )

    async def create_run(self, run: Run) -> Run:
        async with self.sessions.begin() as session:
            session.add(self._run_to_row(run))
        return run

    async def get_run(self, run_id: str) -> Run | None:
        async with self.sessions() as session:
            row = await session.get(RunRow, run_id)
        return self._row_to_run(row) if row else None

    async def list_runs(self, limit: int = 100) -> list[Run]:
        async with self.sessions() as session:
            rows: Sequence[RunRow] = (
                await session.scalars(
                    select(RunRow).order_by(RunRow.created_at.desc()).limit(limit)
                )
            ).all()
        return [self._row_to_run(row) for row in rows]

    async def update_run(
        self,
        run_id: str,
        state: RunState,
        *,
        session_id: str | None = None,
        workspace_lease_id: str | None = None,
        strategy_decision_id: str | None = None,
        execution_mode: ExecutionMode | None = None,
        workflow_template_id: str | None = None,
        acceptance_policy_id: str | None = None,
        loop_execution_id: str | None = None,
        error: ErrorSummary | None = None,
    ) -> Run:
        async with self.sessions.begin() as session:
            row = await session.scalar(select(RunRow).where(RunRow.id == run_id).with_for_update())
            if row is None:
                raise KeyError(run_id)
            row.state = state.value
            row.session_id = session_id if session_id is not None else row.session_id
            row.workspace_lease_id = (
                workspace_lease_id if workspace_lease_id is not None else row.workspace_lease_id
            )
            row.strategy_decision_id = (
                strategy_decision_id
                if strategy_decision_id is not None
                else row.strategy_decision_id
            )
            row.execution_mode = (
                execution_mode.value if execution_mode is not None else row.execution_mode
            )
            row.workflow_template_id = (
                workflow_template_id
                if workflow_template_id is not None
                else row.workflow_template_id
            )
            row.acceptance_policy_id = (
                acceptance_policy_id
                if acceptance_policy_id is not None
                else row.acceptance_policy_id
            )
            row.loop_execution_id = (
                loop_execution_id if loop_execution_id is not None else row.loop_execution_id
            )
            row.error = error.model_dump(mode="json") if error else None
            row.revision += 1
            row.updated_at = datetime.now(UTC)
            await session.flush()
            result = self._row_to_run(row)
        return result

    async def save_acceptance_policy(self, policy: AcceptancePolicy) -> None:
        async with self.sessions.begin() as session:
            current = await session.get(AcceptancePolicyRow, policy.policy_id)
            if current is not None:
                if AcceptancePolicy.model_validate(current.policy) != policy:
                    raise ValueError(f"acceptance policy {policy.policy_id} is immutable")
                return
            session.add(
                AcceptancePolicyRow(
                    id=policy.policy_id,
                    version=policy.version,
                    policy=policy.model_dump(mode="json"),
                    created_at=policy.created_at,
                )
            )

    async def get_acceptance_policy(self, policy_id: str) -> AcceptancePolicy | None:
        async with self.sessions() as session:
            row = await session.get(AcceptancePolicyRow, policy_id)
        return AcceptancePolicy.model_validate(row.policy) if row else None

    async def create_loop_execution(self, execution: LoopExecution) -> LoopExecution:
        async with self.sessions.begin() as session:
            run = await session.scalar(
                select(RunRow).where(RunRow.id == execution.run_id).with_for_update()
            )
            if run is None:
                raise KeyError(execution.run_id)
            existing = await session.scalar(
                select(LoopExecutionRow).where(
                    LoopExecutionRow.run_id == execution.run_id,
                    LoopExecutionRow.node_key == execution.node_key,
                    LoopExecutionRow.attempt == execution.attempt,
                )
            )
            if existing is not None:
                raise ValueError(
                    f"run {execution.run_id} already has loop attempt "
                    f"{execution.attempt} for node {execution.node_key}"
                )
            policy = await session.get(AcceptancePolicyRow, execution.acceptance_policy_ref)
            if policy is None:
                raise KeyError(execution.acceptance_policy_ref)
            stored_policy = AcceptancePolicy.model_validate(policy.policy)
            session.add(self._loop_execution_to_row(execution))
            run.acceptance_policy_id = execution.acceptance_policy_ref
            run.loop_execution_id = execution.loop_execution_id
            run.revision += 1
            run.updated_at = datetime.now(UTC)
        return execution.model_copy(update={"acceptance_policy": stored_policy})

    async def get_loop_execution(self, loop_execution_id: str) -> LoopExecution | None:
        async with self.sessions() as session:
            row = await session.get(LoopExecutionRow, loop_execution_id)
            if row is None:
                return None
            policy = await session.get(AcceptancePolicyRow, row.acceptance_policy_id)
        return self._row_to_loop_execution(row, policy)

    async def get_loop_execution_for_run(self, run_id: str) -> LoopExecution | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(LoopExecutionRow)
                .where(LoopExecutionRow.run_id == run_id)
                .order_by(LoopExecutionRow.created_at.desc(), LoopExecutionRow.id.desc())
                .limit(1)
            )
            if row is None:
                return None
            policy = await session.get(AcceptancePolicyRow, row.acceptance_policy_id)
        return self._row_to_loop_execution(row, policy)

    async def get_loop_execution_for_node(
        self, run_id: str, node_key: str, attempt: int | None = None
    ) -> LoopExecution | None:
        query = select(LoopExecutionRow).where(
            LoopExecutionRow.run_id == run_id, LoopExecutionRow.node_key == node_key
        )
        if attempt is not None:
            query = query.where(LoopExecutionRow.attempt == attempt)
        else:
            query = query.order_by(LoopExecutionRow.attempt.desc()).limit(1)
        async with self.sessions() as session:
            row = await session.scalar(query)
            if row is None:
                return None
            policy = await session.get(AcceptancePolicyRow, row.acceptance_policy_id)
        return self._row_to_loop_execution(row, policy)

    async def list_loop_executions_for_run(self, run_id: str) -> list[LoopExecution]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(LoopExecutionRow)
                    .where(LoopExecutionRow.run_id == run_id)
                    .order_by(LoopExecutionRow.created_at, LoopExecutionRow.id)
                )
            ).all()
            executions = []
            for row in rows:
                policy = await session.get(AcceptancePolicyRow, row.acceptance_policy_id)
                executions.append(self._row_to_loop_execution(row, policy))
        return executions

    async def update_loop_execution(
        self,
        loop_execution_id: str,
        state: LoopState,
        *,
        status: LoopExecutionStatus | None = None,
        stop_reason: LoopStopReason | None = None,
        expected_revision: int | None = None,
    ) -> LoopExecution:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(LoopExecutionRow)
                .where(LoopExecutionRow.id == loop_execution_id)
                .with_for_update()
            )
            if row is None:
                raise KeyError(loop_execution_id)
            self._update_loop_row(
                row,
                state,
                status=status,
                stop_reason=stop_reason,
                expected_revision=expected_revision,
            )
            await session.flush()
            policy = await session.get(AcceptancePolicyRow, row.acceptance_policy_id)
            updated = self._row_to_loop_execution(row, policy)
        return updated

    async def append_loop_iteration(
        self,
        loop_execution_id: str,
        iteration: LoopIteration,
        next_state: LoopState,
        *,
        status: LoopExecutionStatus | None = None,
        stop_reason: LoopStopReason | None = None,
        expected_revision: int | None = None,
        verifications: Sequence[VerificationResult] = (),
        events: Sequence[AgentEvent] = (),
        checkpoint: Checkpoint | None = None,
    ) -> LoopExecution:
        async with self.sessions.begin() as session:
            run = await session.scalar(
                select(RunRow).where(RunRow.id == iteration.run_id).with_for_update()
            )
            if run is None:
                raise KeyError(iteration.run_id)
            row = await session.scalar(
                select(LoopExecutionRow)
                .where(LoopExecutionRow.id == loop_execution_id)
                .with_for_update()
            )
            if row is None:
                raise KeyError(loop_execution_id)
            current = self._row_to_loop_execution(row)
            MemoryStore._validate_iteration_transition(current, iteration, next_state)
            if expected_revision is not None and row.revision != expected_revision:
                raise ValueError("loop execution revision conflict")
            MemoryStore._validate_iteration_evidence(iteration, verifications, events)
            session.add(
                LoopIterationRow(
                    id=iteration.iteration_id,
                    loop_execution_id=iteration.loop_execution_id,
                    run_id=iteration.run_id,
                    number=iteration.number,
                    iteration=iteration.model_dump(mode="json"),
                    created_at=iteration.completed_at,
                )
            )
            for result in verifications:
                session.add(
                    VerificationRow(
                        id=result.verification_id,
                        run_id=result.run_id,
                        loop_execution_id=loop_execution_id,
                        iteration_id=result.iteration_id,
                        verifier_id=result.verifier_id,
                        verifier_version=result.verifier_version,
                        target_ref=result.target_ref,
                        status=result.status.value,
                        result=result.model_dump(mode="json"),
                        executed_at=result.executed_at,
                    )
                )
            for event in events:
                run.last_sequence += 1
                stored_event = event.model_copy(update={"sequence": run.last_sequence})
                session.add(self._event_to_row(stored_event))
            if checkpoint is not None:
                await self._insert_checkpoint_row(
                    session,
                    checkpoint.model_copy(update={"sequence": run.last_sequence}),
                )
            self._update_loop_row(
                row,
                next_state,
                status=status,
                stop_reason=stop_reason,
                expected_revision=row.revision,
            )
            await session.flush()
            policy = await session.get(AcceptancePolicyRow, row.acceptance_policy_id)
            updated = self._row_to_loop_execution(row, policy)
        return updated

    async def list_loop_iterations(self, loop_execution_id: str) -> list[LoopIteration]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(LoopIterationRow)
                    .where(LoopIterationRow.loop_execution_id == loop_execution_id)
                    .order_by(LoopIterationRow.number)
                )
            ).all()
        return [LoopIteration.model_validate(row.iteration) for row in rows]

    async def save_verification(self, result: VerificationResult) -> None:
        async with self.sessions.begin() as session:
            current = await session.get(VerificationRow, result.verification_id)
            if current is not None:
                if VerificationResult.model_validate(current.result) != result:
                    raise ValueError(f"verification {result.verification_id} is immutable")
                return
            loop_execution_id: str | None = None
            if result.iteration_id is not None:
                iteration = await session.get(LoopIterationRow, result.iteration_id)
                if iteration is None:
                    raise ValueError(
                        "iteration verification must be saved with append_loop_iteration"
                    )
                loop_execution_id = iteration.loop_execution_id
            session.add(
                VerificationRow(
                    id=result.verification_id,
                    run_id=result.run_id,
                    loop_execution_id=loop_execution_id,
                    iteration_id=result.iteration_id,
                    verifier_id=result.verifier_id,
                    verifier_version=result.verifier_version,
                    target_ref=result.target_ref,
                    status=result.status.value,
                    result=result.model_dump(mode="json"),
                    executed_at=result.executed_at,
                )
            )

    async def get_verification(self, verification_id: str) -> VerificationResult | None:
        async with self.sessions() as session:
            row = await session.get(VerificationRow, verification_id)
        return VerificationResult.model_validate(row.result) if row else None

    async def list_verifications(
        self, run_id: str, iteration_id: str | None = None
    ) -> list[VerificationResult]:
        query = select(VerificationRow).where(VerificationRow.run_id == run_id)
        if iteration_id is not None:
            query = query.where(VerificationRow.iteration_id == iteration_id)
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    query.order_by(VerificationRow.executed_at, VerificationRow.id)
                )
            ).all()
        return [VerificationResult.model_validate(row.result) for row in rows]

    async def save_lease(self, lease: WorkspaceLease) -> None:
        async with self.sessions.begin() as session:
            session.add(
                WorkspaceLeaseRow(
                    id=lease.lease_id,
                    project_id=lease.project_id,
                    run_id=lease.run_id,
                    base_revision=lease.base_revision,
                    path=str(lease.path),
                    branch_name=lease.branch_name,
                    cleanup_policy=lease.cleanup_policy,
                    acquired_at=lease.acquired_at,
                    expires_at=lease.expires_at,
                )
            )

    async def get_lease(self, lease_id: str) -> WorkspaceLease | None:
        async with self.sessions() as session:
            row = await session.get(WorkspaceLeaseRow, lease_id)
        if row is None:
            return None
        return WorkspaceLease(
            lease_id=row.id,
            project_id=row.project_id,
            run_id=row.run_id,
            base_revision=row.base_revision,
            path=row.path,
            branch_name=row.branch_name,
            cleanup_policy=row.cleanup_policy,
            acquired_at=row.acquired_at,
            expires_at=row.expires_at,
        )

    async def save_session(self, runtime_session: SessionRef) -> None:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(RuntimeSessionRow)
                .where(RuntimeSessionRow.run_id == runtime_session.run_id)
                .with_for_update()
            )
            if row is None:
                session.add(
                    RuntimeSessionRow(
                        id=runtime_session.session_id,
                        run_id=runtime_session.run_id,
                        provider=runtime_session.provider.value,
                        native_session_id=runtime_session.native_session_id,
                        workspace_path=str(runtime_session.workspace),
                        created_at=datetime.now(UTC),
                    )
                )
            else:
                row.id = runtime_session.session_id
                row.provider = runtime_session.provider.value
                row.native_session_id = runtime_session.native_session_id
                row.workspace_path = str(runtime_session.workspace)

    async def get_session_for_run(self, run_id: str) -> SessionRef | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(RuntimeSessionRow).where(RuntimeSessionRow.run_id == run_id)
            )
        if row is None:
            return None
        return SessionRef(
            session_id=row.id,
            run_id=row.run_id,
            provider=Provider(row.provider),
            native_session_id=row.native_session_id,
            workspace=row.workspace_path,
        )

    async def list_sessions(self, provider: Provider | None = None) -> list[SessionRef]:
        query = select(RuntimeSessionRow).order_by(
            RuntimeSessionRow.provider,
            RuntimeSessionRow.created_at.desc(),
        )
        if provider is not None:
            query = query.where(RuntimeSessionRow.provider == provider.value)
        async with self.sessions() as session:
            rows = (await session.scalars(query)).all()
        return [
            SessionRef(
                session_id=row.id,
                run_id=row.run_id,
                provider=Provider(row.provider),
                native_session_id=row.native_session_id,
                workspace=row.workspace_path,
            )
            for row in rows
        ]

    async def save_artifact(self, artifact: ArtifactRef) -> None:
        from accretion.persistence.models import ArtifactRow

        async with self.sessions.begin() as session:
            session.add(
                ArtifactRow(
                    id=artifact.artifact_id,
                    run_id=artifact.run_id,
                    kind=artifact.kind,
                    path=str(artifact.path),
                    sha256=artifact.sha256,
                    created_at=datetime.now(UTC),
                )
            )

    async def list_artifacts(self, run_id: str) -> list[ArtifactRef]:
        from accretion.persistence.models import ArtifactRow

        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(ArtifactRow)
                    .where(ArtifactRow.run_id == run_id)
                    .order_by(ArtifactRow.created_at)
                )
            ).all()
        return [
            ArtifactRef(
                artifact_id=row.id,
                run_id=row.run_id,
                kind=row.kind,
                path=row.path,
                sha256=row.sha256,
            )
            for row in rows
        ]

    async def append_event(self, event: AgentEvent) -> AgentEvent:
        async with self.sessions.begin() as session:
            run = await session.scalar(
                select(RunRow).where(RunRow.id == event.run_id).with_for_update()
            )
            if run is None:
                raise KeyError(event.run_id)
            run.last_sequence += 1
            sequence = run.last_sequence
            stored = event.model_copy(update={"sequence": sequence})
            session.add(
                AgentEventRow(
                    id=stored.event_id,
                    run_id=stored.run_id,
                    session_id=stored.session_id,
                    provider=stored.provider.value,
                    native_type=stored.native_type,
                    normalized_type=stored.normalized_type.value,
                    sequence=sequence,
                    occurred_at=stored.timestamp,
                    correlation_id=stored.correlation_id,
                    causation_id=stored.causation_id,
                    node_id=stored.node_id,
                    payload=stored.payload,
                    adapter_version=stored.adapter_version,
                )
            )
        return stored

    async def list_events(self, run_id: str, after: int = 0) -> list[AgentEvent]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(AgentEventRow)
                    .where(AgentEventRow.run_id == run_id, AgentEventRow.sequence > after)
                    .order_by(AgentEventRow.sequence)
                )
            ).all()
        return [self._row_to_event(row) for row in rows]

    async def upsert_workflow_template(self, template: WorkflowTemplate) -> WorkflowTemplate:
        async with self.sessions.begin() as session:
            existing = await session.scalar(
                select(WorkflowTemplateRow)
                .where(
                    WorkflowTemplateRow.template_id == template.template_id,
                    WorkflowTemplateRow.version == template.version,
                )
                .with_for_update()
            )
            if existing is not None:
                if existing.checksum != template.checksum:
                    raise ValueError(
                        f"workflow template {template.template_id} {template.version} "
                        "content drifted from the stored checksum"
                    )
                return WorkflowTemplate.model_validate(existing.definition)
            session.add(
                WorkflowTemplateRow(
                    id=template.template_record_id,
                    template_id=template.template_id,
                    version=template.version,
                    mode=template.mode.value,
                    status=template.status.value,
                    checksum=template.checksum,
                    definition=template.model_dump(mode="json"),
                    created_at=template.created_at,
                )
            )
        return template

    async def get_workflow_template(
        self, template_id: str, version: str | None = None
    ) -> WorkflowTemplate | None:
        query = select(WorkflowTemplateRow).where(WorkflowTemplateRow.template_id == template_id)
        if version is not None:
            query = query.where(WorkflowTemplateRow.version == version)
        else:
            query = query.where(WorkflowTemplateRow.status == TemplateStatus.VALIDATED.value)
        async with self.sessions() as session:
            rows = (await session.scalars(query)).all()
        if version is None and len(rows) > 1:
            raise ValueError(
                f"template {template_id} has multiple VALIDATED versions; pass one explicitly"
            )
        if not rows:
            return None
        return WorkflowTemplate.model_validate(rows[0].definition)

    async def list_workflow_templates(
        self, status: TemplateStatus | None = None
    ) -> list[WorkflowTemplate]:
        query = select(WorkflowTemplateRow).order_by(
            WorkflowTemplateRow.template_id, WorkflowTemplateRow.version
        )
        if status is not None:
            query = query.where(WorkflowTemplateRow.status == status.value)
        async with self.sessions() as session:
            rows = (await session.scalars(query)).all()
        return [WorkflowTemplate.model_validate(row.definition) for row in rows]

    async def create_run_graph(self, graph: RunGraph) -> RunGraph:
        async with self.sessions.begin() as session:
            run = await session.scalar(
                select(RunRow).where(RunRow.id == graph.run_id).with_for_update()
            )
            if run is None:
                raise KeyError(graph.run_id)
            existing = await session.scalar(
                select(RunGraphRow).where(RunGraphRow.run_id == graph.run_id)
            )
            if existing is not None:
                raise ValueError(f"run {graph.run_id} already has a run graph")
            template = await session.get(WorkflowTemplateRow, graph.template_record_id)
            if template is None:
                raise KeyError(graph.template_record_id)
            session.add(
                RunGraphRow(
                    id=graph.run_graph_id,
                    run_id=graph.run_id,
                    task_id=graph.task_id,
                    template_record_id=graph.template_record_id,
                    template_id=graph.template_id,
                    template_version=graph.template_version,
                    template_checksum=graph.template_checksum,
                    graph_revision=graph.graph_revision,
                    instantiated_at=graph.instantiated_at,
                )
            )
            await session.flush()
            for position, node in enumerate(graph.nodes):
                session.add(
                    RunGraphNodeRow(
                        id=node.node_id,
                        run_graph_id=graph.run_graph_id,
                        run_id=graph.run_id,
                        key=node.key,
                        kind=node.kind.value,
                        status=node.status.value,
                        position=position,
                        node=node.model_dump(mode="json"),
                    )
                )
            for position, edge in enumerate(graph.edges):
                session.add(
                    RunGraphEdgeRow(
                        id=edge.edge_id,
                        run_graph_id=graph.run_graph_id,
                        key=edge.key,
                        source=edge.source,
                        target=edge.target,
                        kind=edge.kind.value,
                        traversal_count=edge.traversal_count,
                        position=position,
                        edge=edge.model_dump(mode="json"),
                    )
                )
        return graph

    async def get_run_graph(self, run_id: str) -> RunGraph | None:
        async with self.sessions() as session:
            row = await session.scalar(select(RunGraphRow).where(RunGraphRow.run_id == run_id))
            if row is None:
                return None
            graph = await self._assemble_run_graph(session, row)
        return graph

    async def update_run_graph(
        self,
        run_graph_id: str,
        *,
        nodes: Sequence[RunNode] = (),
        edges: Sequence[RunEdge] = (),
        expected_revision: int,
    ) -> RunGraph:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(RunGraphRow).where(RunGraphRow.id == run_graph_id).with_for_update()
            )
            if row is None:
                raise KeyError(run_graph_id)
            if row.graph_revision != expected_revision:
                raise ValueError("run graph revision conflict")
            for node in nodes:
                node_row = await session.scalar(
                    select(RunGraphNodeRow)
                    .where(
                        RunGraphNodeRow.run_graph_id == run_graph_id,
                        RunGraphNodeRow.key == node.key,
                    )
                    .with_for_update()
                )
                if node_row is None:
                    raise ValueError(f"run graph has no node {node.key}")
                if node.node_id != node_row.id:
                    raise ValueError("run graph node ids are immutable")
                node_row.status = node.status.value
                node_row.node = node.model_dump(mode="json")
            for edge in edges:
                edge_row = await session.scalar(
                    select(RunGraphEdgeRow)
                    .where(
                        RunGraphEdgeRow.run_graph_id == run_graph_id,
                        RunGraphEdgeRow.key == edge.key,
                    )
                    .with_for_update()
                )
                if edge_row is None:
                    raise ValueError(f"run graph has no edge {edge.key}")
                if edge.edge_id != edge_row.id:
                    raise ValueError("run graph edge ids are immutable")
                edge_row.traversal_count = edge.traversal_count
                edge_row.edge = edge.model_dump(mode="json")
            row.graph_revision += 1
            await session.flush()
            graph = await self._assemble_run_graph(session, row)
        return graph

    async def replace_run_graph(
        self, graph: RunGraph, *, expected_revision: int
    ) -> RunGraph:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(RunGraphRow)
                .where(RunGraphRow.id == graph.run_graph_id)
                .with_for_update()
            )
            if row is None or row.run_id != graph.run_id:
                raise KeyError(graph.run_graph_id)
            if row.graph_revision != expected_revision:
                raise ValueError("run graph revision conflict")
            template = await session.get(WorkflowTemplateRow, graph.template_record_id)
            if template is None:
                raise KeyError(graph.template_record_id)
            await session.execute(
                delete(RunGraphEdgeRow).where(
                    RunGraphEdgeRow.run_graph_id == graph.run_graph_id
                )
            )
            await session.execute(
                delete(RunGraphNodeRow).where(
                    RunGraphNodeRow.run_graph_id == graph.run_graph_id
                )
            )
            row.template_record_id = graph.template_record_id
            row.template_id = graph.template_id
            row.template_version = graph.template_version
            row.template_checksum = graph.template_checksum
            row.graph_revision = expected_revision + 1
            row.instantiated_at = graph.instantiated_at
            for position, node in enumerate(graph.nodes):
                session.add(
                    RunGraphNodeRow(
                        id=node.node_id,
                        run_graph_id=graph.run_graph_id,
                        run_id=graph.run_id,
                        key=node.key,
                        kind=node.kind.value,
                        status=node.status.value,
                        position=position,
                        node=node.model_dump(mode="json"),
                    )
                )
            for position, edge in enumerate(graph.edges):
                session.add(
                    RunGraphEdgeRow(
                        id=edge.edge_id,
                        run_graph_id=graph.run_graph_id,
                        key=edge.key,
                        source=edge.source,
                        target=edge.target,
                        kind=edge.kind.value,
                        traversal_count=edge.traversal_count,
                        position=position,
                        edge=edge.model_dump(mode="json"),
                    )
                )
            await session.flush()
            return await self._assemble_run_graph(session, row)

    async def append_checkpoint(
        self, checkpoint: Checkpoint, events: Sequence[AgentEvent] = ()
    ) -> Checkpoint:
        async with self.sessions.begin() as session:
            run = await session.scalar(
                select(RunRow).where(RunRow.id == checkpoint.run_id).with_for_update()
            )
            if run is None:
                raise KeyError(checkpoint.run_id)
            for event in events:
                run.last_sequence += 1
                stored_event = event.model_copy(update={"sequence": run.last_sequence})
                session.add(self._event_to_row(stored_event))
            stored = await self._insert_checkpoint_row(
                session, checkpoint.model_copy(update={"sequence": run.last_sequence})
            )
        return stored

    async def _insert_checkpoint_row(
        self, session: AsyncSession, checkpoint: Checkpoint
    ) -> Checkpoint:
        run = await session.get(RunRow, checkpoint.run_id)
        if run is not None and RunState(run.state) in TERMINAL_RUN_STATES:
            raise ValueError("terminal run cannot accept new checkpoints")
        existing = await session.scalar(
            select(CheckpointRow).where(
                CheckpointRow.run_id == checkpoint.run_id,
                CheckpointRow.sequence == checkpoint.sequence,
            )
        )
        if existing is not None:
            stored = Checkpoint.model_validate(existing.state)
            identity = checkpoint.model_dump(mode="json", exclude=_CHECKPOINT_IDENTITY_EXCLUDED)
            if stored.model_dump(mode="json", exclude=_CHECKPOINT_IDENTITY_EXCLUDED) != identity:
                raise ValueError(
                    f"checkpoint at sequence {checkpoint.sequence} is immutable evidence"
                )
            return stored
        session.add(
            CheckpointRow(
                id=checkpoint.checkpoint_id,
                run_id=checkpoint.run_id,
                sequence=checkpoint.sequence,
                state=checkpoint.model_dump(mode="json"),
                created_at=checkpoint.created_at,
            )
        )
        return checkpoint

    async def get_latest_checkpoint(self, run_id: str) -> Checkpoint | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(CheckpointRow)
                .where(CheckpointRow.run_id == run_id)
                .order_by(CheckpointRow.sequence.desc())
                .limit(1)
            )
        return Checkpoint.model_validate(row.state) if row else None

    async def list_checkpoints(self, run_id: str) -> list[Checkpoint]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(CheckpointRow)
                    .where(CheckpointRow.run_id == run_id)
                    .order_by(CheckpointRow.sequence)
                )
            ).all()
        return [Checkpoint.model_validate(row.state) for row in rows]

    async def save_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        async with self.sessions.begin() as session:
            run = await session.get(RunRow, approval.run_id)
            if run is None:
                raise KeyError(approval.run_id)
            existing = await session.scalar(
                select(ApprovalRow).where(
                    ApprovalRow.run_id == approval.run_id,
                    ApprovalRow.native_request_id == approval.native_request_id,
                )
            )
            if existing is not None:
                return self._row_to_approval(existing)
            session.add(self._approval_to_row(approval))
        return approval

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        async with self.sessions() as session:
            row = await session.get(ApprovalRow, approval_id)
        return self._row_to_approval(row) if row else None

    async def list_approvals(
        self, run_id: str | None = None, status: ApprovalStatus | None = None
    ) -> list[ApprovalRecord]:
        query = select(ApprovalRow).order_by(ApprovalRow.created_at, ApprovalRow.id)
        if run_id is not None:
            query = query.where(ApprovalRow.run_id == run_id)
        if status is not None:
            query = query.where(ApprovalRow.status == status.value)
        async with self.sessions() as session:
            rows = (await session.scalars(query)).all()
        return [self._row_to_approval(row) for row in rows]

    async def decide_approval(
        self, approval_id: str, decision: ApprovalDecisionValue
    ) -> ApprovalRecord:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(ApprovalRow).where(ApprovalRow.id == approval_id).with_for_update()
            )
            if row is None:
                raise KeyError(approval_id)
            if ApprovalStatus(row.status) is not ApprovalStatus.PENDING:
                raise ValueError(f"approval {approval_id} was already decided")
            row.status = _APPROVAL_DECISION_STATUS[decision].value
            row.decision = decision.value
            row.decided_at = datetime.now(UTC)
            await session.flush()
            decided = self._row_to_approval(row)
        return decided

    async def add_budget_spent(
        self, run_id: str, *, turns: int = 0, tool_calls: int = 0
    ) -> dict[str, int]:
        async with self.sessions.begin() as session:
            row = await session.scalar(select(RunRow).where(RunRow.id == run_id).with_for_update())
            if row is None:
                raise KeyError(run_id)
            spent = dict(row.budget_spent or {"turns": 0, "tool_calls": 0})
            spent["turns"] = int(spent.get("turns", 0)) + turns
            spent["tool_calls"] = int(spent.get("tool_calls", 0)) + tool_calls
            row.budget_spent = spent
            await session.flush()
        return spent

    async def get_budget_spent(self, run_id: str) -> dict[str, int]:
        async with self.sessions() as session:
            row = await session.get(RunRow, run_id)
        if row is None:
            raise KeyError(run_id)
        spent = row.budget_spent or {}
        return {
            "turns": int(spent.get("turns", 0)),
            "tool_calls": int(spent.get("tool_calls", 0)),
        }

    async def upsert_capability(self, capability: Capability) -> Capability:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(CapabilityRow).where(
                    CapabilityRow.capability_id == capability.capability_id,
                    CapabilityRow.version == capability.version,
                )
            )
            definition = capability.model_dump(mode="json")
            if row is not None:
                if row.definition != definition:
                    raise ValueError(
                        f"capability {capability.capability_id}@{capability.version} is immutable"
                    )
                return Capability.model_validate(row.definition)
            session.add(
                CapabilityRow(
                    id=new_id("capability"),
                    capability_id=capability.capability_id,
                    version=capability.version,
                    definition=definition,
                    enabled=capability.enabled,
                    created_at=capability.created_at,
                )
            )
        return capability

    async def get_capability(
        self, capability_id: str, version: str | None = None
    ) -> Capability | None:
        query = select(CapabilityRow).where(CapabilityRow.capability_id == capability_id)
        if version is not None:
            query = query.where(CapabilityRow.version == version)
        else:
            query = query.order_by(CapabilityRow.created_at.desc()).limit(1)
        async with self.sessions() as session:
            row = await session.scalar(query)
        return Capability.model_validate(row.definition) if row else None

    async def list_capabilities(self, enabled_only: bool = True) -> list[Capability]:
        query = select(CapabilityRow).order_by(CapabilityRow.capability_id, CapabilityRow.version)
        if enabled_only:
            query = query.where(CapabilityRow.enabled.is_(True))
        async with self.sessions() as session:
            rows = (await session.scalars(query)).all()
        return [Capability.model_validate(row.definition) for row in rows]

    async def upsert_skill(self, skill: MetaSkill) -> MetaSkill:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(SkillRow).where(
                    SkillRow.skill_id == skill.skill_id,
                    SkillRow.version == skill.version,
                )
            )
            definition = skill.model_dump(mode="json")
            if row is not None:
                if row.definition != definition:
                    raise ValueError(f"skill {skill.skill_id}@{skill.version} is immutable")
                return MetaSkill.model_validate(row.definition)
            session.add(
                SkillRow(
                    id=new_id("skill"),
                    skill_id=skill.skill_id,
                    version=skill.version,
                    definition=definition,
                    created_at=skill.created_at,
                )
            )
        return skill

    async def list_skills(self) -> list[MetaSkill]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(SkillRow).order_by(SkillRow.skill_id, SkillRow.version)
                )
            ).all()
        return [MetaSkill.model_validate(row.definition) for row in rows]

    async def upsert_plugin(self, plugin: MetaPlugin) -> MetaPlugin:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(PluginRow).where(
                    PluginRow.plugin_id == plugin.plugin_id,
                    PluginRow.version == plugin.version,
                )
            )
            definition = plugin.model_dump(mode="json")
            if row is not None:
                if row.definition != definition:
                    raise ValueError(f"plugin {plugin.plugin_id}@{plugin.version} is immutable")
                return MetaPlugin.model_validate(row.definition)
            session.add(
                PluginRow(
                    id=new_id("plugin"),
                    plugin_id=plugin.plugin_id,
                    version=plugin.version,
                    checksum=plugin.checksum,
                    definition=definition,
                    allowlisted=plugin.allowlisted,
                    created_at=plugin.created_at,
                )
            )
        return plugin

    async def list_plugins(self, allowlisted_only: bool = True) -> list[MetaPlugin]:
        query = select(PluginRow).order_by(PluginRow.plugin_id, PluginRow.version)
        if allowlisted_only:
            query = query.where(PluginRow.allowlisted.is_(True))
        async with self.sessions() as session:
            rows = (await session.scalars(query)).all()
        return [MetaPlugin.model_validate(row.definition) for row in rows]

    async def upsert_principal(self, principal: Principal) -> Principal:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(PrincipalRow).where(
                    PrincipalRow.issuer == principal.issuer,
                    PrincipalRow.subject == principal.subject,
                )
            )
            if row is not None:
                existing = Principal.model_validate(row.definition)
                principal = principal.model_copy(
                    update={
                        "principal_id": existing.principal_id,
                        "created_at": existing.created_at,
                    }
                )
                row.status = principal.status.value
                row.definition = principal.model_dump(mode="json")
            else:
                session.add(
                    PrincipalRow(
                        id=new_id("principal"),
                        principal_id=principal.principal_id,
                        issuer=principal.issuer,
                        subject=principal.subject,
                        status=principal.status.value,
                        definition=principal.model_dump(mode="json"),
                        created_at=principal.created_at,
                    )
                )
        return principal

    async def get_principal(self, principal_id: str) -> Principal | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(PrincipalRow).where(PrincipalRow.principal_id == principal_id)
            )
        return Principal.model_validate(row.definition) if row else None

    async def get_principal_by_identity(self, issuer: str, subject: str) -> Principal | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(PrincipalRow).where(
                    PrincipalRow.issuer == issuer, PrincipalRow.subject == subject
                )
            )
        return Principal.model_validate(row.definition) if row else None

    async def list_principals(self) -> list[Principal]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(select(PrincipalRow).order_by(PrincipalRow.principal_id))
            ).all()
        return [Principal.model_validate(row.definition) for row in rows]

    async def upsert_workspace(self, workspace: WorkspaceEntity) -> WorkspaceEntity:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(WorkspaceRow).where(WorkspaceRow.workspace_id == workspace.workspace_id)
            )
            if row is not None:
                row.definition = workspace.model_dump(mode="json")
            else:
                session.add(
                    WorkspaceRow(
                        id=new_id("workspace_entity"),
                        workspace_id=workspace.workspace_id,
                        definition=workspace.model_dump(mode="json"),
                        created_at=workspace.created_at,
                    )
                )
        return workspace

    async def list_workspaces_for_principal(self, principal_id: str) -> list[WorkspaceEntity]:
        async with self.sessions() as session:
            member_rows = (
                await session.scalars(
                    select(WorkspaceMembershipRow).where(
                        WorkspaceMembershipRow.principal_id == principal_id
                    )
                )
            ).all()
            workspace_ids = {row.workspace_id for row in member_rows}
            if not workspace_ids:
                return []
            rows = (
                await session.scalars(
                    select(WorkspaceRow)
                    .where(WorkspaceRow.workspace_id.in_(workspace_ids))
                    .order_by(WorkspaceRow.workspace_id)
                )
            ).all()
        return [WorkspaceEntity.model_validate(row.definition) for row in rows]

    async def upsert_workspace_membership(
        self, membership: WorkspaceMembership
    ) -> WorkspaceMembership:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(WorkspaceMembershipRow).where(
                    WorkspaceMembershipRow.workspace_id == membership.workspace_id,
                    WorkspaceMembershipRow.principal_id == membership.principal_id,
                )
            )
            if row is not None:
                existing = WorkspaceMembership.model_validate(row.definition)
                membership = membership.model_copy(
                    update={
                        "membership_id": existing.membership_id,
                        "created_at": existing.created_at,
                        "revision": existing.revision
                        + (1 if existing.role != membership.role else 0),
                    }
                )
                row.role = membership.role.value
                row.definition = membership.model_dump(mode="json")
            else:
                session.add(
                    WorkspaceMembershipRow(
                        id=new_id("workspace_membership"),
                        membership_id=membership.membership_id,
                        workspace_id=membership.workspace_id,
                        principal_id=membership.principal_id,
                        role=membership.role.value,
                        definition=membership.model_dump(mode="json"),
                        created_at=membership.created_at,
                    )
                )
        return membership

    async def list_workspace_memberships(
        self,
        workspace_id: str | None = None,
        principal_id: str | None = None,
    ) -> list[WorkspaceMembership]:
        query = select(WorkspaceMembershipRow).order_by(WorkspaceMembershipRow.membership_id)
        if workspace_id is not None:
            query = query.where(WorkspaceMembershipRow.workspace_id == workspace_id)
        if principal_id is not None:
            query = query.where(WorkspaceMembershipRow.principal_id == principal_id)
        async with self.sessions() as session:
            rows = (await session.scalars(query)).all()
        return [WorkspaceMembership.model_validate(row.definition) for row in rows]

    async def create_auth_session(self, auth_session: AuthSession) -> AuthSession:
        async with self.sessions.begin() as session:
            session.add(
                AuthSessionRow(
                    id=new_id("auth_session"),
                    auth_session_id=auth_session.auth_session_id,
                    principal_id=auth_session.principal_id,
                    expires_at=auth_session.expires_at,
                    revoked=auth_session.revoked,
                    definition=auth_session.model_dump(mode="json"),
                    created_at=auth_session.issued_at,
                )
            )
        return auth_session

    async def get_auth_session(self, auth_session_id: str) -> AuthSession | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(AuthSessionRow).where(
                    AuthSessionRow.auth_session_id == auth_session_id,
                    AuthSessionRow.revoked.is_(False),
                    AuthSessionRow.expires_at > datetime.now(UTC),
                )
            )
        return AuthSession.model_validate(row.definition) if row else None

    async def revoke_auth_session(self, auth_session_id: str) -> None:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(AuthSessionRow).where(
                    AuthSessionRow.auth_session_id == auth_session_id
                )
            )
            if row is not None:
                revoked = AuthSession.model_validate(row.definition).model_copy(
                    update={"revoked": True}
                )
                row.revoked = True
                row.definition = revoked.model_dump(mode="json")

    async def create_auth_transaction(self, transaction: AuthTransaction) -> AuthTransaction:
        async with self.sessions.begin() as session:
            session.add(
                AuthTransactionRow(
                    id=new_id("auth_transaction"),
                    state=transaction.state,
                    expires_at=transaction.expires_at,
                    definition=transaction.model_dump(mode="json"),
                    created_at=transaction.created_at,
                )
            )
        return transaction

    async def consume_auth_transaction(self, state: str) -> AuthTransaction | None:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(AuthTransactionRow)
                .where(AuthTransactionRow.state == state)
                .with_for_update()
            )
            if row is None:
                return None
            transaction = AuthTransaction.model_validate(row.definition)
            await session.delete(row)
            if transaction.expires_at <= datetime.now(UTC):
                return None
        return transaction

    async def upsert_connector_definition(
        self, connector: ConnectorDefinition
    ) -> ConnectorDefinition:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(ConnectorDefinitionRow).where(
                    ConnectorDefinitionRow.connector_id == connector.connector_id
                )
            )
            definition = connector.model_dump(mode="json")
            if row is not None:
                row.auth_type = connector.auth_type.value
                row.definition = definition
            else:
                session.add(
                    ConnectorDefinitionRow(
                        id=new_id("conndef"),
                        connector_id=connector.connector_id,
                        auth_type=connector.auth_type.value,
                        definition=definition,
                        created_at=connector.created_at,
                    )
                )
        return connector

    async def get_connector_definition(self, connector_id: str) -> ConnectorDefinition | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(ConnectorDefinitionRow).where(
                    ConnectorDefinitionRow.connector_id == connector_id
                )
            )
        return ConnectorDefinition.model_validate(row.definition) if row else None

    async def list_connector_definitions(self) -> list[ConnectorDefinition]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(ConnectorDefinitionRow).order_by(ConnectorDefinitionRow.connector_id)
                )
            ).all()
        return [ConnectorDefinition.model_validate(row.definition) for row in rows]

    async def upsert_connection(self, connection: Connection) -> Connection:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(ConnectionRow).where(ConnectionRow.connection_id == connection.connection_id)
            )
            definition = connection.model_dump(mode="json")
            if row is not None:
                # Ownership can change on re-consent; the indexed columns must follow the
                # model or any query filtering on them reads a stale owner.
                row.workspace_id = connection.workspace_id
                row.principal_id = connection.principal_id
                row.status = connection.status.value
                row.scope = connection.scope.value
                row.definition = definition
            else:
                session.add(
                    ConnectionRow(
                        id=new_id("conn"),
                        connection_id=connection.connection_id,
                        connector_id=connection.connector_id,
                        workspace_id=connection.workspace_id,
                        principal_id=connection.principal_id,
                        scope=connection.scope.value,
                        status=connection.status.value,
                        definition=definition,
                        created_at=connection.created_at,
                    )
                )
        return connection

    async def get_connection(self, connection_id: str) -> Connection | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(ConnectionRow).where(ConnectionRow.connection_id == connection_id)
            )
        return Connection.model_validate(row.definition) if row else None

    async def list_connections(
        self,
        connector_id: str | None = None,
        status: ConnectionStatus | None = None,
    ) -> list[Connection]:
        query = select(ConnectionRow).order_by(ConnectionRow.connection_id)
        if connector_id is not None:
            query = query.where(ConnectionRow.connector_id == connector_id)
        if status is not None:
            query = query.where(ConnectionRow.status == status.value)
        async with self.sessions() as session:
            rows = (await session.scalars(query)).all()
        return [Connection.model_validate(row.definition) for row in rows]

    async def upsert_capability_binding(self, binding: CapabilityBinding) -> CapabilityBinding:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(CapabilityBindingRow).where(
                    CapabilityBindingRow.binding_id == binding.binding_id
                )
            )
            definition = binding.model_dump(mode="json")
            if row is not None:
                row.enabled = binding.enabled
                row.definition = definition
            else:
                session.add(
                    CapabilityBindingRow(
                        id=new_id("capbind"),
                        binding_id=binding.binding_id,
                        capability_id=binding.capability_id,
                        connector_id=binding.connector_id,
                        enabled=binding.enabled,
                        definition=definition,
                        created_at=binding.created_at,
                    )
                )
        return binding

    async def list_capability_bindings(
        self,
        capability_id: str | None = None,
        connector_id: str | None = None,
        enabled_only: bool = True,
    ) -> list[CapabilityBinding]:
        query = select(CapabilityBindingRow).order_by(CapabilityBindingRow.binding_id)
        if capability_id is not None:
            query = query.where(CapabilityBindingRow.capability_id == capability_id)
        if connector_id is not None:
            query = query.where(CapabilityBindingRow.connector_id == connector_id)
        if enabled_only:
            query = query.where(CapabilityBindingRow.enabled.is_(True))
        async with self.sessions() as session:
            rows = (await session.scalars(query)).all()
        return [CapabilityBinding.model_validate(row.definition) for row in rows]

    async def upsert_capability_policy(self, policy: CapabilityPolicy) -> CapabilityPolicy:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(CapabilityPolicyRow).where(
                    CapabilityPolicyRow.policy_id == policy.policy_id,
                    CapabilityPolicyRow.version == policy.version,
                )
            )
            definition = policy.model_dump(mode="json")
            if row is not None:
                if row.definition != definition:
                    raise ValueError(f"policy {policy.policy_id}@{policy.version} is immutable")
                return CapabilityPolicy.model_validate(row.definition)
            session.add(
                CapabilityPolicyRow(
                    id=new_id("policy"),
                    policy_id=policy.policy_id,
                    version=policy.version,
                    definition=definition,
                    created_at=policy.created_at,
                )
            )
        return policy

    async def get_capability_policy(
        self, policy_id: str, version: str | None = None
    ) -> CapabilityPolicy | None:
        query = select(CapabilityPolicyRow).where(CapabilityPolicyRow.policy_id == policy_id)
        if version is not None:
            query = query.where(CapabilityPolicyRow.version == version)
        else:
            query = query.order_by(CapabilityPolicyRow.created_at.desc()).limit(1)
        async with self.sessions() as session:
            row = await session.scalar(query)
        return CapabilityPolicy.model_validate(row.definition) if row else None

    async def save_capability_result(
        self, result: CapabilityExecutionResult
    ) -> CapabilityExecutionResult:
        async with self.sessions.begin() as session:
            if await session.get(RunRow, result.request.run_id) is None:
                raise KeyError(result.request.run_id)
            row = await session.get(CapabilityRequestRow, result.request.request_id)
            if row is None:
                row = CapabilityRequestRow(
                    id=result.request.request_id,
                    run_id=result.request.run_id,
                    capability_id=result.request.capability_id,
                    capability_version=result.request.capability_version,
                    status=result.status.value,
                    request=result.request.model_dump(mode="json"),
                    authorization=result.authorization.model_dump(mode="json"),
                    output=result.output,
                    error=result.error.model_dump(mode="json") if result.error else None,
                    side_effect_operation_id=result.side_effect_operation_id,
                    created_at=result.request.created_at,
                    completed_at=result.completed_at,
                )
                session.add(row)
            else:
                row.status = result.status.value
                row.authorization = result.authorization.model_dump(mode="json")
                row.output = result.output
                row.error = result.error.model_dump(mode="json") if result.error else None
                row.side_effect_operation_id = result.side_effect_operation_id
                row.completed_at = result.completed_at
        return result

    async def list_capability_results(self, run_id: str) -> list[CapabilityExecutionResult]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(CapabilityRequestRow)
                    .where(CapabilityRequestRow.run_id == run_id)
                    .order_by(CapabilityRequestRow.created_at, CapabilityRequestRow.id)
                )
            ).all()
        return [
            CapabilityExecutionResult(
                request=row.request,
                authorization=row.authorization,
                status=row.status,
                output=row.output,
                error=row.error,
                side_effect_operation_id=row.side_effect_operation_id,
                completed_at=row.completed_at,
            )
            for row in rows
        ]

    async def upsert_benchmark_task(self, task: BenchmarkTask) -> BenchmarkTask:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(BenchmarkTaskRow).where(
                    BenchmarkTaskRow.benchmark_task_id == task.benchmark_task_id,
                    BenchmarkTaskRow.version == task.version,
                )
            )
            definition = task.model_dump(mode="json")
            if row is not None:
                if row.definition != definition:
                    raise ValueError(
                        f"benchmark task {task.benchmark_task_id}@{task.version} is immutable"
                    )
                return BenchmarkTask.model_validate(row.definition)
            session.add(
                BenchmarkTaskRow(
                    id=new_id("task"),
                    benchmark_task_id=task.benchmark_task_id,
                    version=task.version,
                    category=task.category.value,
                    task_type=task.task_type.value,
                    environment_ref=task.environment_ref,
                    environment_version=task.environment_version,
                    definition=definition,
                    created_at=datetime(2026, 8, 22, tzinfo=UTC),
                )
            )
        return task

    async def get_benchmark_task(self, task_id: str) -> BenchmarkTask | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(BenchmarkTaskRow)
                .where(BenchmarkTaskRow.benchmark_task_id == task_id)
                .order_by(BenchmarkTaskRow.version.desc())
                .limit(1)
            )
        return BenchmarkTask.model_validate(row.definition) if row else None

    async def list_benchmark_tasks(self) -> list[BenchmarkTask]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(BenchmarkTaskRow).order_by(
                        BenchmarkTaskRow.benchmark_task_id,
                        BenchmarkTaskRow.version,
                    )
                )
            ).all()
        return [BenchmarkTask.model_validate(row.definition) for row in rows]

    async def save_benchmark_run(
        self, run: BenchmarkRun, metrics: Sequence[ArchitectureMetric]
    ) -> BenchmarkRun:
        if len(metrics) != run.scenario_count or any(
            item.benchmark_run_id != run.benchmark_run_id for item in metrics
        ):
            raise ValueError("benchmark run metrics do not match the run manifest")
        if len({item.metric_id for item in metrics}) != len(metrics):
            raise ValueError("benchmark metric identifiers must be unique")
        async with self.sessions.begin() as session:
            current = await session.get(BenchmarkRunRow, run.benchmark_run_id)
            if current is not None:
                if (
                    current.corpus_sha256 != run.corpus_sha256
                    or current.trace_sha256 != run.trace_sha256
                    or current.scenario_count != run.scenario_count
                ):
                    raise ValueError(f"benchmark run {run.benchmark_run_id} is immutable")
                metric_count = await session.scalar(
                    select(func.count())
                    .select_from(ArchitectureMetricRow)
                    .where(ArchitectureMetricRow.benchmark_run_id == run.benchmark_run_id)
                )
                if metric_count != run.scenario_count:
                    raise ValueError(f"benchmark run {run.benchmark_run_id} has incomplete metrics")
                return self._row_to_benchmark_run(current)
            session.add(
                BenchmarkRunRow(
                    id=run.benchmark_run_id,
                    suite_version=run.suite_version,
                    configuration_version=run.configuration_version,
                    execution_source=run.execution_source.value,
                    status=run.status.value,
                    corpus_sha256=run.corpus_sha256,
                    trace_sha256=run.trace_sha256,
                    scenario_count=run.scenario_count,
                    started_at=run.started_at,
                    completed_at=run.completed_at,
                )
            )
            await session.flush()
            session.add_all(
                ArchitectureMetricRow(
                    id=metric.metric_id,
                    benchmark_run_id=metric.benchmark_run_id,
                    benchmark_task_id=metric.benchmark_task_id,
                    task_version=metric.task_version,
                    mode=metric.mode.value,
                    provider=metric.provider.value,
                    verifier_id=metric.verifier_id,
                    selector_version=metric.selector_version,
                    metric=metric.model_dump(mode="json"),
                )
                for metric in metrics
            )
        return run

    async def list_benchmark_runs(self, limit: int = 20) -> list[BenchmarkRun]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(BenchmarkRunRow).order_by(BenchmarkRunRow.started_at.desc()).limit(limit)
                )
            ).all()
        return [self._row_to_benchmark_run(row) for row in rows]

    async def list_architecture_metrics(
        self, benchmark_run_id: str | None = None
    ) -> list[ArchitectureMetric]:
        query = select(ArchitectureMetricRow).order_by(
            ArchitectureMetricRow.benchmark_task_id,
            ArchitectureMetricRow.mode,
        )
        if benchmark_run_id is not None:
            query = query.where(ArchitectureMetricRow.benchmark_run_id == benchmark_run_id)
        async with self.sessions() as session:
            rows = (await session.scalars(query)).all()
        return [ArchitectureMetric.model_validate(row.metric) for row in rows]

    async def get_project_features(self, project_id: str) -> ProjectFeatureSettings:
        async with self.sessions() as session:
            if await session.get(ProjectRow, project_id) is None:
                raise KeyError(project_id)
            row = await session.get(ProjectFeatureSettingsRow, project_id)
        if row is None:
            return ProjectFeatureSettings(project_id=project_id)
        return ProjectFeatureSettings.model_validate(row.settings)

    async def update_project_features(
        self, settings: ProjectFeatureSettings, *, expected_revision: int
    ) -> ProjectFeatureSettings:
        async with self.sessions.begin() as session:
            if await session.get(ProjectRow, settings.project_id) is None:
                raise KeyError(settings.project_id)
            row = await session.get(
                ProjectFeatureSettingsRow, settings.project_id, with_for_update=True
            )
            current_revision = row.revision if row is not None else 1
            if current_revision != expected_revision:
                raise ValueError("project feature revision conflict")
            updated = settings.model_copy(
                update={"revision": expected_revision + 1, "updated_at": datetime.now(UTC)}
            )
            if row is None:
                session.add(
                    ProjectFeatureSettingsRow(
                        project_id=settings.project_id,
                        settings=updated.model_dump(mode="json"),
                        revision=updated.revision,
                        updated_at=updated.updated_at,
                    )
                )
            else:
                row.settings = updated.model_dump(mode="json")
                row.revision = updated.revision
                row.updated_at = updated.updated_at
        return updated

    async def save_workflow_proposal(self, proposal: WorkflowProposal) -> WorkflowProposal:
        async with self.sessions.begin() as session:
            row = await session.get(WorkflowProposalRow, proposal.proposal_id)
            if row is not None:
                current = WorkflowProposal.model_validate(row.proposal)
                if current != proposal:
                    raise ValueError(f"workflow proposal {proposal.proposal_id} is immutable")
                return current
            session.add(
                WorkflowProposalRow(
                    id=proposal.proposal_id,
                    task_id=proposal.task_id,
                    run_id=proposal.run_id,
                    based_on_graph_revision=proposal.based_on_graph_revision,
                    planner_version=proposal.planner_version,
                    proposal=proposal.model_dump(mode="json"),
                    created_at=proposal.created_at,
                )
            )
        return proposal

    async def get_workflow_proposal(self, proposal_id: str) -> WorkflowProposal | None:
        async with self.sessions() as session:
            row = await session.get(WorkflowProposalRow, proposal_id)
        return WorkflowProposal.model_validate(row.proposal) if row is not None else None

    async def list_workflow_proposals(
        self, *, task_id: str | None = None, run_id: str | None = None
    ) -> list[WorkflowProposal]:
        query = select(WorkflowProposalRow).order_by(WorkflowProposalRow.created_at)
        if task_id is not None:
            query = query.where(WorkflowProposalRow.task_id == task_id)
        if run_id is not None:
            query = query.where(WorkflowProposalRow.run_id == run_id)
        async with self.sessions() as session:
            rows = (await session.scalars(query)).all()
        return [WorkflowProposal.model_validate(row.proposal) for row in rows]

    async def save_graph_validation(self, result: GraphValidationResult) -> GraphValidationResult:
        async with self.sessions.begin() as session:
            row = await session.get(GraphValidationResultRow, result.validation_id)
            if row is not None:
                current = GraphValidationResult.model_validate(row.result)
                if current != result:
                    raise ValueError(f"graph validation {result.validation_id} is immutable")
                return current
            session.add(
                GraphValidationResultRow(
                    id=result.validation_id,
                    proposal_id=result.proposal_id,
                    status=result.status.value,
                    validator_version=result.validator_version,
                    result=result.model_dump(mode="json"),
                    created_at=result.created_at,
                )
            )
        return result

    async def list_graph_validations(self, proposal_id: str) -> list[GraphValidationResult]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(GraphValidationResultRow)
                    .where(GraphValidationResultRow.proposal_id == proposal_id)
                    .order_by(GraphValidationResultRow.created_at)
                )
            ).all()
        return [GraphValidationResult.model_validate(row.result) for row in rows]

    async def save_graph_revision(self, revision: RunGraphRevision) -> RunGraphRevision:
        async with self.sessions.begin() as session:
            existing = await session.scalar(
                select(RunGraphRevisionRow).where(
                    RunGraphRevisionRow.run_graph_id == revision.run_graph_id,
                    RunGraphRevisionRow.revision == revision.revision,
                )
            )
            if existing is not None:
                current = RunGraphRevision.model_validate(existing.definition)
                if current != revision:
                    raise ValueError(
                        f"run graph revision {revision.run_id}/{revision.revision} is immutable"
                    )
                return current
            latest = await session.scalar(
                select(func.max(RunGraphRevisionRow.revision)).where(
                    RunGraphRevisionRow.run_graph_id == revision.run_graph_id
                )
            )
            expected = 1 if latest is None else latest + 1
            if revision.revision != expected:
                raise ValueError("run graph revisions must be contiguous")
            session.add(
                RunGraphRevisionRow(
                    id=revision.revision_id,
                    run_graph_id=revision.run_graph_id,
                    run_id=revision.run_id,
                    revision=revision.revision,
                    parent_revision=revision.parent_revision,
                    proposal_id=revision.proposal_id,
                    graph_hash=revision.normalized_graph_hash,
                    definition=revision.model_dump(mode="json"),
                    activated_at=revision.activated_at,
                    created_at=revision.created_at,
                )
            )
        return revision

    async def list_graph_revisions(self, run_id: str) -> list[RunGraphRevision]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(RunGraphRevisionRow)
                    .where(RunGraphRevisionRow.run_id == run_id)
                    .order_by(RunGraphRevisionRow.revision)
                )
            ).all()
        return [RunGraphRevision.model_validate(row.definition) for row in rows]

    async def get_graph_revision(self, run_id: str, revision: int) -> RunGraphRevision | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(RunGraphRevisionRow).where(
                    RunGraphRevisionRow.run_id == run_id,
                    RunGraphRevisionRow.revision == revision,
                )
            )
        return RunGraphRevision.model_validate(row.definition) if row is not None else None

    async def save_replan_request(self, request: ReplanRequest) -> ReplanRequest:
        async with self.sessions.begin() as session:
            row = await session.get(ReplanRequestRow, request.replan_request_id)
            if row is None:
                session.add(
                    ReplanRequestRow(
                        id=request.replan_request_id,
                        run_id=request.run_id,
                        based_on_graph_revision=request.based_on_graph_revision,
                        status=request.status.value,
                        request=request.model_dump(mode="json"),
                        created_at=request.created_at,
                        updated_at=request.updated_at,
                    )
                )
            else:
                current = ReplanRequest.model_validate(row.request)
                if current.run_id != request.run_id:
                    raise ValueError("replan request identity is immutable")
                row.status = request.status.value
                row.request = request.model_dump(mode="json")
                row.updated_at = request.updated_at
        return request

    async def list_replan_requests(self, run_id: str) -> list[ReplanRequest]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(ReplanRequestRow)
                    .where(ReplanRequestRow.run_id == run_id)
                    .order_by(ReplanRequestRow.created_at)
                )
            ).all()
        return [ReplanRequest.model_validate(row.request) for row in rows]

    async def save_runtime_decision(self, decision: RuntimeDecision) -> RuntimeDecision:
        async with self.sessions.begin() as session:
            row = await session.get(RuntimeDecisionRow, decision.decision_id)
            if row is not None:
                current = RuntimeDecision.model_validate(row.decision)
                if current != decision:
                    raise ValueError(f"runtime decision {decision.decision_id} is immutable")
                return current
            session.add(
                RuntimeDecisionRow(
                    id=decision.decision_id,
                    run_id=decision.run_id,
                    node_id=decision.node_id,
                    selected_runtime=(
                        decision.selected_runtime.value
                        if decision.selected_runtime is not None
                        else None
                    ),
                    policy_version=decision.policy_version,
                    decision=decision.model_dump(mode="json"),
                    created_at=decision.created_at,
                )
            )
        return decision

    async def list_runtime_decisions(self, run_id: str) -> list[RuntimeDecision]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(RuntimeDecisionRow)
                    .where(RuntimeDecisionRow.run_id == run_id)
                    .order_by(RuntimeDecisionRow.created_at)
                )
            ).all()
        return [RuntimeDecision.model_validate(row.decision) for row in rows]

    async def create_search(self, record: SearchRecord) -> SearchRecord:
        async with self.sessions.begin() as session:
            if await session.get(RunRow, record.plan.run_id) is None:
                raise KeyError(record.plan.run_id)
            session.add(
                SearchPlanRow(
                    id=record.plan.search_id,
                    run_id=record.plan.run_id,
                    parent_node_id=record.plan.parent_node_id,
                    graph_revision=record.plan.graph_revision,
                    mode=record.plan.mode.value,
                    status=record.status.value,
                    revision=record.revision,
                    record=record.model_dump(mode="json"),
                    created_at=record.plan.created_at,
                    updated_at=record.updated_at,
                )
            )
        return record

    async def get_search(self, search_id: str) -> SearchRecord | None:
        async with self.sessions() as session:
            row = await session.get(SearchPlanRow, search_id)
        return SearchRecord.model_validate(row.record) if row is not None else None

    async def list_searches(self, run_id: str) -> list[SearchRecord]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(SearchPlanRow)
                    .where(SearchPlanRow.run_id == run_id)
                    .order_by(SearchPlanRow.created_at, SearchPlanRow.id)
                )
            ).all()
        return [SearchRecord.model_validate(row.record) for row in rows]

    async def update_search(
        self, record: SearchRecord, *, expected_revision: int
    ) -> SearchRecord:
        async with self.sessions.begin() as session:
            row = await session.get(
                SearchPlanRow, record.plan.search_id, with_for_update=True
            )
            if row is None:
                raise KeyError(record.plan.search_id)
            current = SearchRecord.model_validate(row.record)
            if current.plan != record.plan:
                raise ValueError("search plan is immutable")
            if row.revision != expected_revision:
                raise ValueError("search revision conflict")
            updated = record.model_copy(
                update={
                    "revision": expected_revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            row.status = updated.status.value
            row.revision = updated.revision
            row.record = updated.model_dump(mode="json")
            row.updated_at = updated.updated_at
        return updated

    async def save_search_candidate(
        self, candidate: CandidateTrajectory
    ) -> CandidateTrajectory:
        async with self.sessions.begin() as session:
            if await session.get(SearchPlanRow, candidate.search_id) is None:
                raise KeyError(candidate.search_id)
            row = await session.get(SearchCandidateRow, candidate.candidate_id)
            if row is None:
                session.add(
                    SearchCandidateRow(
                        id=candidate.candidate_id,
                        search_id=candidate.search_id,
                        run_id=candidate.run_id,
                        ordinal=candidate.ordinal,
                        provider=candidate.provider.value,
                        status=candidate.status.value,
                        trajectory=candidate.model_dump(mode="json"),
                        created_at=candidate.created_at,
                        completed_at=candidate.completed_at,
                    )
                )
            else:
                current = CandidateTrajectory.model_validate(row.trajectory)
                if current.search_id != candidate.search_id or current.ordinal != candidate.ordinal:
                    raise ValueError("candidate identity is immutable")
                row.provider = candidate.provider.value
                row.status = candidate.status.value
                row.trajectory = candidate.model_dump(mode="json")
                row.completed_at = candidate.completed_at
        return candidate

    async def get_search_candidate(
        self, candidate_id: str
    ) -> CandidateTrajectory | None:
        async with self.sessions() as session:
            row = await session.get(SearchCandidateRow, candidate_id)
        return CandidateTrajectory.model_validate(row.trajectory) if row is not None else None

    async def list_search_candidates(self, search_id: str) -> list[CandidateTrajectory]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(SearchCandidateRow)
                    .where(SearchCandidateRow.search_id == search_id)
                    .order_by(SearchCandidateRow.ordinal)
                )
            ).all()
        return [CandidateTrajectory.model_validate(row.trajectory) for row in rows]

    async def save_candidate_score(self, score: CandidateScore) -> CandidateScore:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(CandidateScoreRow).where(
                    CandidateScoreRow.candidate_id == score.candidate_id
                )
            )
            if row is not None:
                current = CandidateScore.model_validate(row.score)
                if current != score:
                    raise ValueError(f"candidate score for {score.candidate_id} is immutable")
                return current
            session.add(
                CandidateScoreRow(
                    id=score.score_id,
                    search_id=score.search_id,
                    candidate_id=score.candidate_id,
                    eligible=score.eligible,
                    total_score=score.total_score,
                    score=score.model_dump(mode="json"),
                    created_at=score.created_at,
                )
            )
        return score

    async def list_candidate_scores(self, search_id: str) -> list[CandidateScore]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(CandidateScoreRow)
                    .where(CandidateScoreRow.search_id == search_id)
                    .order_by(CandidateScoreRow.created_at, CandidateScoreRow.id)
                )
            ).all()
        return [CandidateScore.model_validate(row.score) for row in rows]

    async def save_search_promotion(
        self, promotion: SearchPromotionRecord
    ) -> SearchPromotionRecord:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(SearchPromotionRow).where(
                    SearchPromotionRow.search_id == promotion.search_id
                )
            )
            if row is None:
                session.add(
                    SearchPromotionRow(
                        id=promotion.promotion_id,
                        search_id=promotion.search_id,
                        candidate_id=promotion.candidate_id,
                        run_id=promotion.run_id,
                        status=promotion.status,
                        record=promotion.model_dump(mode="json"),
                        created_at=promotion.created_at,
                        completed_at=promotion.completed_at,
                    )
                )
            else:
                current = SearchPromotionRecord.model_validate(row.record)
                if (
                    current.promotion_id != promotion.promotion_id
                    or current.candidate_id != promotion.candidate_id
                ):
                    raise ValueError("search promotion identity is immutable")
                row.status = promotion.status
                row.record = promotion.model_dump(mode="json")
                row.completed_at = promotion.completed_at
        return promotion

    async def get_search_promotion(
        self, search_id: str
    ) -> SearchPromotionRecord | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(SearchPromotionRow).where(SearchPromotionRow.search_id == search_id)
            )
        return SearchPromotionRecord.model_validate(row.record) if row is not None else None

    async def save_experience(
        self,
        experience: Experience,
        segments: Sequence[TrajectorySegment],
        embedding: ExperienceEmbedding,
    ) -> Experience:
        if any(segment.experience_id != experience.experience_id for segment in segments):
            raise ValueError("trajectory segment belongs to another experience")
        if embedding.experience_id != experience.experience_id:
            raise ValueError("embedding belongs to another experience")
        ordinals = [segment.ordinal for segment in segments]
        if not segments or len(set(ordinals)) != len(ordinals):
            raise ValueError("experience requires uniquely ordered trajectory segments")
        source_key = self._experience_source_key(experience)
        async with self.sessions.begin() as session:
            existing = await session.scalar(
                select(ExperienceRow)
                .where(ExperienceRow.source_key == source_key)
                .with_for_update()
            )
            if existing is not None:
                current = Experience.model_validate(existing.record)
                segment_rows = (
                    await session.scalars(
                        select(TrajectorySegmentRow)
                        .where(TrajectorySegmentRow.experience_id == existing.id)
                        .order_by(TrajectorySegmentRow.ordinal)
                    )
                ).all()
                embedding_row = await session.scalar(
                    select(ExperienceEmbeddingRow).where(
                        ExperienceEmbeddingRow.experience_id == existing.id
                    )
                )
                current_segments = [
                    TrajectorySegment.model_validate(row.record) for row in segment_rows
                ]
                if (
                    current != experience
                    or current_segments != sorted(segments, key=lambda item: item.ordinal)
                    or embedding_row is None
                    or embedding_row.id != embedding.embedding_id
                    or embedding_row.version != embedding.version
                    or embedding_row.input_digest != embedding.input_digest
                ):
                    raise ValueError("conflicting experience evidence for terminal source")
                return current
            session.add(
                ExperienceRow(
                    id=experience.experience_id,
                    project_id=experience.project_id,
                    task_id=experience.task_id,
                    source_run_id=experience.source_run_id,
                    source_candidate_id=experience.source_candidate_id,
                    source_key=source_key,
                    repository_identity=experience.repository_identity,
                    trust=experience.trust.value,
                    polarity=experience.polarity.value,
                    retracted=experience.retracted,
                    revision=experience.revision,
                    record=experience.model_dump(mode="json"),
                    created_at=experience.created_at,
                )
            )
            await session.flush()
            session.add_all(
                TrajectorySegmentRow(
                    id=segment.segment_id,
                    experience_id=segment.experience_id,
                    ordinal=segment.ordinal,
                    kind=segment.kind.value,
                    record=segment.model_dump(mode="json"),
                    created_at=segment.created_at,
                )
                for segment in segments
            )
            session.add(
                ExperienceEmbeddingRow(
                    id=embedding.embedding_id,
                    experience_id=embedding.experience_id,
                    version=embedding.version,
                    input_digest=embedding.input_digest,
                    embedding=embedding.vector,
                    created_at=embedding.created_at,
                )
            )
        return experience

    async def get_experience(self, experience_id: str) -> Experience | None:
        async with self.sessions() as session:
            row = await session.get(ExperienceRow, experience_id)
        return Experience.model_validate(row.record) if row is not None else None

    async def list_experiences(
        self,
        *,
        project_id: str | None = None,
        repository_identity: str | None = None,
        include_retracted: bool = False,
    ) -> list[Experience]:
        query = select(ExperienceRow).order_by(ExperienceRow.created_at, ExperienceRow.id)
        if project_id is not None:
            query = query.where(ExperienceRow.project_id == project_id)
        if repository_identity is not None:
            query = query.where(ExperienceRow.repository_identity == repository_identity)
        if not include_retracted:
            query = query.where(ExperienceRow.retracted.is_(False))
        async with self.sessions() as session:
            rows = (await session.scalars(query)).all()
        return [Experience.model_validate(row.record) for row in rows]

    async def list_trajectory_segments(self, experience_id: str) -> list[TrajectorySegment]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(TrajectorySegmentRow)
                    .where(TrajectorySegmentRow.experience_id == experience_id)
                    .order_by(TrajectorySegmentRow.ordinal)
                )
            ).all()
        return [TrajectorySegment.model_validate(row.record) for row in rows]

    async def get_experience_embedding(
        self, experience_id: str
    ) -> ExperienceEmbedding | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(ExperienceEmbeddingRow).where(
                    ExperienceEmbeddingRow.experience_id == experience_id
                )
            )
        if row is None:
            return None
        return ExperienceEmbedding(
            embedding_id=row.id,
            experience_id=row.experience_id,
            version=row.version,
            input_digest=row.input_digest,
            vector=[float(value) for value in row.embedding],
            created_at=row.created_at,
        )

    async def nearest_experience_embeddings(
        self, repository_identity: str, vector: Sequence[float], *, limit: int
    ) -> list[tuple[str, float]]:
        values = [float(value) for value in vector]
        if len(values) != 384:
            raise ValueError("experience query vector must contain 384 dimensions")
        distance = ExperienceEmbeddingRow.embedding.cosine_distance(values).label(
            "cosine_distance"
        )
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(ExperienceEmbeddingRow.experience_id, distance)
                    .join(
                        ExperienceRow,
                        ExperienceRow.id == ExperienceEmbeddingRow.experience_id,
                    )
                    .where(ExperienceRow.repository_identity == repository_identity)
                    .order_by(distance, ExperienceEmbeddingRow.experience_id)
                    .limit(limit)
                )
            ).all()
        return [(str(experience_id), float(value)) for experience_id, value in rows]

    async def save_experience_query(
        self, query: ExperienceQuery, vector: Sequence[float]
    ) -> ExperienceQuery:
        values = [float(value) for value in vector]
        if len(values) != 384:
            raise ValueError("experience query vector must contain 384 dimensions")
        async with self.sessions.begin() as session:
            row = await session.get(ExperienceQueryRow, query.query_id)
            if row is not None:
                current = ExperienceQuery.model_validate(row.record)
                if current != query:
                    raise ValueError("experience query is immutable")
                return current
            session.add(
                ExperienceQueryRow(
                    id=query.query_id,
                    project_id=query.project_id,
                    task_id=query.task_id,
                    repository_identity=query.repository_identity,
                    record=query.model_dump(mode="json"),
                    embedding=values,
                    created_at=query.created_at,
                )
            )
        return query

    async def get_experience_query(
        self, query_id: str
    ) -> tuple[ExperienceQuery, list[float]] | None:
        async with self.sessions() as session:
            row = await session.get(ExperienceQueryRow, query_id)
        if row is None:
            return None
        return (
            ExperienceQuery.model_validate(row.record),
            [float(value) for value in row.embedding],
        )

    async def save_experience_matches(
        self, matches: Sequence[ExperienceMatch]
    ) -> list[ExperienceMatch]:
        if not matches:
            return []
        query_id = matches[0].query_id
        if any(match.query_id != query_id for match in matches):
            raise ValueError("experience matches must belong to one query")
        async with self.sessions.begin() as session:
            if await session.get(ExperienceQueryRow, query_id) is None:
                raise KeyError(query_id)
            existing_rows = (
                await session.scalars(
                    select(ExperienceMatchRow)
                    .where(ExperienceMatchRow.query_id == query_id)
                    .order_by(ExperienceMatchRow.rank)
                )
            ).all()
            if existing_rows:
                existing = [ExperienceMatch.model_validate(row.record) for row in existing_rows]
                if existing != list(matches):
                    raise ValueError("experience matches are immutable")
                return existing
            session.add_all(
                ExperienceMatchRow(
                    id=match.match_id,
                    query_id=match.query_id,
                    experience_id=match.experience_id,
                    rank=match.rank,
                    disposition=match.assessment.disposition.value,
                    final_score=match.assessment.final_score,
                    record=match.model_dump(mode="json"),
                    created_at=match.created_at,
                )
                for match in matches
            )
        return list(matches)

    async def list_experience_matches(self, query_id: str) -> list[ExperienceMatch]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(ExperienceMatchRow)
                    .where(ExperienceMatchRow.query_id == query_id)
                    .order_by(ExperienceMatchRow.rank)
                )
            ).all()
        return [ExperienceMatch.model_validate(row.record) for row in rows]

    async def save_experience_selection(
        self, selection: ExperienceSelection
    ) -> ExperienceSelection:
        async with self.sessions.begin() as session:
            row = await session.get(ExperienceSelectionRow, selection.selection_id)
            if row is not None:
                current = ExperienceSelection.model_validate(row.record)
                if current != selection:
                    raise ValueError("experience selection is immutable")
                return current
            session.add(
                ExperienceSelectionRow(
                    id=selection.selection_id,
                    task_id=selection.task_id,
                    query_id=selection.query_id,
                    expected_context_bundle_id=selection.expected_context_bundle_id,
                    resulting_context_bundle_id=selection.resulting_context_bundle_id,
                    record=selection.model_dump(mode="json"),
                    created_at=selection.created_at,
                )
            )
        return selection

    async def list_experience_selections(self, task_id: str) -> list[ExperienceSelection]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(ExperienceSelectionRow)
                    .where(ExperienceSelectionRow.task_id == task_id)
                    .order_by(ExperienceSelectionRow.created_at)
                )
            ).all()
        return [ExperienceSelection.model_validate(row.record) for row in rows]

    async def retract_experience(self, action: ModerationAction) -> Experience:
        async with self.sessions.begin() as session:
            existing_action = await session.get(ExperienceModerationActionRow, action.action_id)
            if existing_action is not None:
                current_action = ModerationAction.model_validate(existing_action.record)
                if current_action != action:
                    raise ValueError("moderation action is immutable")
                row = await session.get(ExperienceRow, action.experience_id)
                if row is None:
                    raise KeyError(action.experience_id)
                return Experience.model_validate(row.record)
            row = await session.get(
                ExperienceRow, action.experience_id, with_for_update=True
            )
            if row is None:
                raise KeyError(action.experience_id)
            if row.revision != action.expected_revision:
                raise ValueError("experience revision conflict")
            current = Experience.model_validate(row.record)
            updated = current.model_copy(
                update={"revision": action.resulting_revision, "retracted": True}
            )
            row.revision = updated.revision
            row.retracted = True
            row.record = updated.model_dump(mode="json")
            session.add(
                ExperienceModerationActionRow(
                    id=action.action_id,
                    experience_id=action.experience_id,
                    action=action.action.value,
                    expected_revision=action.expected_revision,
                    resulting_revision=action.resulting_revision,
                    record=action.model_dump(mode="json"),
                    created_at=action.created_at,
                )
            )
        return updated

    async def list_moderation_actions(self, experience_id: str) -> list[ModerationAction]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(ExperienceModerationActionRow)
                    .where(ExperienceModerationActionRow.experience_id == experience_id)
                    .order_by(ExperienceModerationActionRow.created_at)
                )
            ).all()
        return [ModerationAction.model_validate(row.record) for row in rows]

    async def save_trajectory_seed(self, seed: TrajectorySeed) -> TrajectorySeed:
        async with self.sessions.begin() as session:
            row = await session.get(TrajectoryReplaySeedRow, seed.seed_id)
            if row is not None:
                current = TrajectorySeed.model_validate(row.record)
                if current != seed:
                    raise ValueError("trajectory seed is immutable")
                return current
            existing = await session.scalar(
                select(TrajectoryReplaySeedRow).where(
                    TrajectoryReplaySeedRow.candidate_id == seed.candidate_id
                )
            )
            if existing is not None:
                raise ValueError("candidate already has a trajectory seed")
            session.add(
                TrajectoryReplaySeedRow(
                    id=seed.seed_id,
                    search_id=seed.search_id,
                    candidate_id=seed.candidate_id,
                    match_id=seed.match_id,
                    experience_id=seed.experience_id,
                    validation_status=seed.validation_status.value,
                    record=seed.model_dump(mode="json"),
                    created_at=seed.created_at,
                )
            )
        return seed

    async def list_trajectory_seeds(self, search_id: str) -> list[TrajectorySeed]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(TrajectoryReplaySeedRow)
                    .where(TrajectoryReplaySeedRow.search_id == search_id)
                    .order_by(TrajectoryReplaySeedRow.created_at)
                )
            ).all()
        return [TrajectorySeed.model_validate(row.record) for row in rows]

    @staticmethod
    def _experience_source_key(experience: Experience) -> str:
        candidate = experience.source_candidate_id or "run"
        return f"{experience.source_run_id}:{candidate}"

    @staticmethod
    def _row_to_benchmark_run(row: BenchmarkRunRow) -> BenchmarkRun:
        return BenchmarkRun(
            benchmark_run_id=row.id,
            suite_version=row.suite_version,
            configuration_version=row.configuration_version,
            execution_source=row.execution_source,
            status=row.status,
            corpus_sha256=row.corpus_sha256,
            trace_sha256=row.trace_sha256,
            scenario_count=row.scenario_count,
            started_at=row.started_at,
            completed_at=row.completed_at,
        )

    @staticmethod
    async def _assemble_run_graph(session: AsyncSession, row: RunGraphRow) -> RunGraph:
        node_rows = (
            await session.scalars(
                select(RunGraphNodeRow)
                .where(RunGraphNodeRow.run_graph_id == row.id)
                .order_by(RunGraphNodeRow.position)
            )
        ).all()
        edge_rows = (
            await session.scalars(
                select(RunGraphEdgeRow)
                .where(RunGraphEdgeRow.run_graph_id == row.id)
                .order_by(RunGraphEdgeRow.position)
            )
        ).all()
        return RunGraph(
            run_graph_id=row.id,
            run_id=row.run_id,
            task_id=row.task_id,
            template_record_id=row.template_record_id,
            template_id=row.template_id,
            template_version=row.template_version,
            template_checksum=row.template_checksum,
            nodes=[RunNode.model_validate(item.node) for item in node_rows],
            edges=[RunEdge.model_validate(item.edge) for item in edge_rows],
            graph_revision=row.graph_revision,
            instantiated_at=row.instantiated_at,
        )

    @staticmethod
    def _approval_to_row(approval: ApprovalRecord) -> ApprovalRow:
        # The contract field "payload" maps onto the historical column
        # "request_payload"; callers redact payloads before persistence.
        return ApprovalRow(
            id=approval.approval_id,
            run_id=approval.run_id,
            node_id=approval.node_id,
            native_request_id=approval.native_request_id,
            method=approval.method,
            summary=approval.summary,
            status=approval.status.value,
            request_payload=approval.payload,
            decision=approval.decision.value if approval.decision else None,
            created_at=approval.created_at,
            decided_at=approval.decided_at,
        )

    @staticmethod
    def _row_to_approval(row: ApprovalRow) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=row.id,
            run_id=row.run_id,
            node_id=row.node_id,
            native_request_id=row.native_request_id,
            method=row.method,
            summary=row.summary,
            payload=row.request_payload,
            status=ApprovalStatus(row.status),
            decision=ApprovalDecisionValue(row.decision) if row.decision else None,
            created_at=row.created_at,
            decided_at=row.decided_at,
        )

    @staticmethod
    def _task_to_row(task: Task) -> TaskRow:
        return TaskRow(
            id=task.envelope.task_id,
            project_id=task.envelope.project_id,
            envelope=task.envelope.model_dump(mode="json"),
            prompt_contract_id=task.prompt_contract_id,
            context_bundle_id=task.context_bundle_id,
            current_profile_id=task.current_profile_id,
            current_strategy_decision_id=task.current_strategy_decision_id,
            created_at=task.created_at,
        )

    @staticmethod
    def _row_to_task(row: TaskRow) -> Task:
        return Task(
            envelope=TaskEnvelope.model_validate(row.envelope),
            prompt_contract_id=row.prompt_contract_id,
            context_bundle_id=row.context_bundle_id,
            current_profile_id=row.current_profile_id,
            current_strategy_decision_id=row.current_strategy_decision_id,
            created_at=row.created_at,
        )

    @classmethod
    async def _add_planning_rows(
        cls,
        session: AsyncSession,
        prompt: PromptContract,
        context: ContextBundle,
        profile: TaskProfile,
        decision: StrategyDecision,
    ) -> None:
        session.add_all(
            [
                PromptContractRow(
                    id=prompt.prompt_contract_id,
                    task_id=prompt.task_id,
                    version=prompt.version,
                    contract=prompt.model_dump(mode="json"),
                    created_at=prompt.created_at,
                ),
                ContextBundleRow(
                    id=context.context_bundle_id,
                    task_id=context.task_ref,
                    version=context.version,
                    bundle=context.model_dump(mode="json"),
                    created_at=context.created_at,
                ),
                TaskProfileRow(
                    id=profile.profile_id,
                    task_id=profile.task_id,
                    profiler_version=profile.profiler_version,
                    profile=profile.model_dump(mode="json"),
                    created_at=profile.created_at,
                ),
            ]
        )
        await session.flush()
        session.add(cls._decision_to_row(decision))

    @staticmethod
    def _decision_to_row(decision: StrategyDecision) -> StrategyDecisionRow:
        return StrategyDecisionRow(
            id=decision.decision_id,
            task_id=decision.task_id,
            task_profile_id=decision.task_profile_ref,
            policy_version=decision.policy_version,
            decision=decision.model_dump(mode="json"),
            created_at=decision.created_at,
        )

    @staticmethod
    def _run_to_row(run: Run) -> RunRow:
        return RunRow(
            id=run.run_id,
            task_id=run.task_id,
            project_id=run.project_id,
            provider=run.provider.value,
            state=run.state.value,
            last_sequence=run.last_sequence,
            revision=run.revision,
            session_id=run.session_id,
            workspace_lease_id=run.workspace_lease_id,
            strategy_decision_id=run.strategy_decision_id,
            execution_mode=run.execution_mode.value if run.execution_mode else None,
            workflow_template_id=run.workflow_template_id,
            acceptance_policy_id=run.acceptance_policy_id,
            loop_execution_id=run.loop_execution_id,
            error=run.error.model_dump(mode="json") if run.error else None,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    @staticmethod
    def _row_to_run(row: RunRow) -> Run:
        return Run(
            run_id=row.id,
            task_id=row.task_id,
            project_id=row.project_id,
            provider=Provider(row.provider),
            state=RunState(row.state),
            last_sequence=row.last_sequence,
            revision=row.revision,
            session_id=row.session_id,
            workspace_lease_id=row.workspace_lease_id,
            strategy_decision_id=row.strategy_decision_id,
            execution_mode=ExecutionMode(row.execution_mode) if row.execution_mode else None,
            workflow_template_id=row.workflow_template_id,
            acceptance_policy_id=row.acceptance_policy_id,
            loop_execution_id=row.loop_execution_id,
            error=ErrorSummary.model_validate(row.error) if row.error else None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _loop_execution_to_row(execution: LoopExecution) -> LoopExecutionRow:
        return LoopExecutionRow(
            id=execution.loop_execution_id,
            run_id=execution.run_id,
            node_key=execution.node_key,
            attempt=execution.attempt,
            acceptance_policy_id=execution.acceptance_policy_ref,
            spec=execution.spec.model_dump(mode="json"),
            state=execution.state.model_dump(mode="json"),
            status=execution.status.value,
            stop_reason=execution.stop_reason.value if execution.stop_reason else None,
            revision=execution.revision,
            created_at=execution.created_at,
            updated_at=execution.updated_at,
            completed_at=execution.completed_at,
        )

    @staticmethod
    def _row_to_loop_execution(
        row: LoopExecutionRow, policy: AcceptancePolicyRow | None = None
    ) -> LoopExecution:
        acceptance_policy = AcceptancePolicy.model_validate(policy.policy) if policy else None
        return LoopExecution(
            loop_execution_id=row.id,
            run_id=row.run_id,
            node_key=row.node_key,
            attempt=row.attempt,
            spec=row.spec,
            state=row.state,
            acceptance_policy_ref=row.acceptance_policy_id,
            acceptance_policy=acceptance_policy,
            status=row.status,
            stop_reason=row.stop_reason,
            revision=row.revision,
            created_at=row.created_at,
            updated_at=row.updated_at,
            completed_at=row.completed_at,
        )

    @staticmethod
    def _update_loop_row(
        row: LoopExecutionRow,
        state: LoopState,
        *,
        status: LoopExecutionStatus | None,
        stop_reason: LoopStopReason | None,
        expected_revision: int | None,
    ) -> None:
        if LoopExecutionStatus(row.status) in _TERMINAL_LOOP_EXECUTION_STATUSES:
            raise ValueError("terminal loop execution is immutable")
        if expected_revision is not None and row.revision != expected_revision:
            raise ValueError("loop execution revision conflict")
        if status is not None:
            row.status = status.value
        row.state = state.model_dump(mode="json")
        row.stop_reason = stop_reason.value if stop_reason else None
        row.revision += 1
        row.updated_at = datetime.now(UTC)
        if LoopExecutionStatus(row.status) in _TERMINAL_LOOP_EXECUTION_STATUSES:
            row.completed_at = row.updated_at

    @staticmethod
    def _row_to_event(row: AgentEventRow) -> AgentEvent:
        return AgentEvent(
            event_id=row.id,
            run_id=row.run_id,
            session_id=row.session_id,
            provider=Provider(row.provider),
            native_type=row.native_type,
            normalized_type=row.normalized_type,
            sequence=row.sequence,
            timestamp=row.occurred_at,
            correlation_id=row.correlation_id,
            causation_id=row.causation_id,
            node_id=row.node_id,
            payload=row.payload,
            adapter_version=row.adapter_version,
        )

    @staticmethod
    def _event_to_row(event: AgentEvent) -> AgentEventRow:
        return AgentEventRow(
            id=event.event_id,
            run_id=event.run_id,
            session_id=event.session_id,
            provider=event.provider.value,
            native_type=event.native_type,
            normalized_type=event.normalized_type.value,
            sequence=event.sequence,
            occurred_at=event.timestamp,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            node_id=event.node_id,
            payload=event.payload,
            adapter_version=event.adapter_version,
        )
