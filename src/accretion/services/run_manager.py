from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from accretion.checkpoints import (
    ReconcileClassification,
    classify_run,
    evaluate_checkpoint,
)
from accretion.concurrency import ConcurrencyLimiter
from accretion.contracts import (
    LIVE_PROVIDERS,
    RISK_RANK,
    TERMINAL_RUN_STATES,
    AcceptancePolicy,
    AgentEvent,
    AgentRuntime,
    ApprovalDecisionValue,
    ApprovalRecord,
    ApprovalStatus,
    Checkpoint,
    CheckpointKind,
    CheckpointLoopCursor,
    EdgeGuard,
    ErrorSummary,
    EventType,
    ExecutionMode,
    ExecutionTrace,
    Finding,
    FindingSeverity,
    GraphEdgeKind,
    GraphNodeKind,
    GraphNodeStatus,
    GraphProjection,
    IterationDirective,
    IterationDirectiveKind,
    LoopBudgetRemaining,
    LoopExecution,
    LoopExecutionStatus,
    LoopIteration,
    LoopIterationStatus,
    LoopSpec,
    LoopState,
    LoopStopReason,
    Project,
    Provider,
    RiskLevel,
    Run,
    RunEdge,
    RunGraph,
    RunNode,
    RunRef,
    RunState,
    RuntimeExecutionRequest,
    SessionConfig,
    SessionRef,
    StrategyOverrideResult,
    Task,
    TaskEnvelope,
    TaskPlanning,
    TaskType,
    TemplateStatus,
    VerificationContext,
    VerificationResult,
    VerificationStatus,
    VerificationTarget,
    VerificationTargetKind,
    WorkflowNodeSpec,
    WorkflowTemplate,
    WorkspaceLease,
)
from accretion.ids import new_id
from accretion.looping import (
    build_loop_execution,
    build_loop_spec,
    terminal_outcome,
)
from accretion.orchestration.models import SearchRecord, SearchStatus
from accretion.persistence.side_effects import SideEffectLedger
from accretion.persistence.store import StateStore
from accretion.planning import (
    build_initial_planning,
    evaluate_override,
    has_irreversible_capabilities,
)
from accretion.projections import build_graph_projection, build_loop_projection
from accretion.routing.protocols import FeedbackPipeline, NodeRoutingService
from accretion.runtimes.common import make_event
from accretion.templates import (
    compute_template_checksum,
    instantiate_run_graph,
    seed_templates,
)
from accretion.tracing import build_execution_trace
from accretion.verifiers.git_diff import GitDiffVerifier
from accretion.verifiers.output_contract import OutputContractVerifier
from accretion.verifiers.policy import evaluate_acceptance as evaluate_acceptance_policy
from accretion.verifiers.registry import VerifierRegistry, VerifierUnavailableError
from accretion.verifiers.research import RESEARCH_VERIFIER_IDS
from accretion.verifiers.results import finding, verification_result
from accretion.verifiers.trajectory import TrajectoryPolicyVerifier
from accretion.workspace import WorktreeManager


@dataclass(slots=True)
class RuntimeCallOutcome:
    session: SessionRef
    ref: RunRef
    completed: bool
    cancelled: bool
    tool_calls: int
    error: ErrorSummary | None = None
    stop_reason: LoopStopReason | None = None


@dataclass(slots=True, frozen=True)
class ActiveRuntimeRef:
    """One in-flight provider call, together with the session that owns it.

    ``RunRef`` names a call by run and session id but not by provider, so a bare ref
    cannot say which runtime is holding it. That was harmless while every session on a
    run ran on ``run.provider``; it stops being harmless the moment a node executes on
    a runtime other than the run's, because interrupt, resume and terminate would then
    be delivered to a runtime that has never heard of the call. Pairing the ref with
    its session keeps the owning runtime derivable from the entry itself, which is
    what :meth:`RunManager._runtime_for` reads.
    """

    session: SessionRef
    ref: RunRef


class CapabilityNodeInvoker(Protocol):
    """Executes one capability reference hung on a workflow node.

    A Protocol rather than a concrete gateway for the same reason
    :class:`SearchNodeExecutor` is one: ``CapabilityGateway`` is constructed in the MCP
    gateway process, not alongside ``RunManager``, so binding the scheduler to that
    class would either drag the whole governance stack into every run or force a fake
    that is not the real path. ``accretion.governance.GatewayCapabilityInvoker`` is the
    production implementation and does resolve-then-execute through the real gateway.

    Returning ``None`` means "not invoked" --- unresolvable or unauthorized --- and is
    deliberately not an error: a node that also captures a diff must not lose that
    outcome because a capability could not be reached.
    """

    async def __call__(
        self,
        *,
        run_id: str,
        node_id: str,
        capability_id: str,
        arguments: dict[str, object],
        executing_provider: Provider | None = None,
    ) -> object | None: ...


class SearchNodeExecutor(Protocol):
    async def __call__(
        self,
        record: SearchRecord,
        run: Run,
        task: Task,
        lease: WorkspaceLease,
        node: RunNode,
        policy: AcceptancePolicy,
    ) -> SearchRecord: ...


class NodeOutcome(StrEnum):
    """What a graph node execution produced toward its outgoing edges."""

    SUCCESS = "SUCCESS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"


_GUARD_MATCHES: dict[EdgeGuard, frozenset[NodeOutcome]] = {
    EdgeGuard.ON_SUCCESS: frozenset({NodeOutcome.SUCCESS}),
    EdgeGuard.ON_FAIL: frozenset({NodeOutcome.FAIL}),
    EdgeGuard.ON_INCONCLUSIVE: frozenset({NodeOutcome.INCONCLUSIVE}),
    EdgeGuard.ON_APPROVED: frozenset({NodeOutcome.APPROVED}),
    EdgeGuard.ON_DENIED: frozenset({NodeOutcome.DENIED}),
    EdgeGuard.ON_REPLAN_AVAILABLE: frozenset({NodeOutcome.FAIL}),
    EdgeGuard.ON_REPLAN_EXHAUSTED: frozenset({NodeOutcome.FAIL}),
}

_TERMINAL_GUARD_STATES: dict[EdgeGuard, RunState] = {
    EdgeGuard.ON_SUCCESS: RunState.SUCCEEDED,
    EdgeGuard.ON_APPROVED: RunState.SUCCEEDED,
    EdgeGuard.ON_FAIL: RunState.FAILED,
    EdgeGuard.ON_INCONCLUSIVE: RunState.REQUIRES_HUMAN,
    EdgeGuard.ON_DENIED: RunState.REQUIRES_HUMAN,
    EdgeGuard.ON_REPLAN_EXHAUSTED: RunState.REQUIRES_HUMAN,
}


@dataclass(slots=True)
class _GraphCursor:
    """In-memory scheduler state, always recoverable from durable evidence."""

    statuses: dict[str, GraphNodeStatus]
    entered_via: dict[str, int]
    current_key: str
    arrival_guard: EdgeGuard | None = None
    arrival_edge_kind: GraphEdgeKind | None = None
    entry_edge_key: str | None = None
    entered_via_retry: bool = False
    last_artifact_id: str | None = None
    last_artifact_sha256: str | None = None
    last_results: list[VerificationResult] = field(default_factory=list)
    last_error: ErrorSummary | None = None
    stop_reason: LoopStopReason | None = None
    gate_wait_seconds: float = 0.0


class RunManager:
    def __init__(
        self,
        *,
        store: StateStore,
        worktrees: WorktreeManager,
        runtimes: dict[Provider, AgentRuntime],
        limiter: ConcurrencyLimiter,
        live_providers_enabled: bool,
        side_effect_ledger: SideEffectLedger | None = None,
        operator_identity: str = "local-operator",
        verifier_registry: VerifierRegistry | None = None,
        default_verifier_ids: tuple[str, ...] = (),
        auto_resume_on_reconcile: bool = False,
    ) -> None:
        self.store = store
        self.worktrees = worktrees
        self.runtimes = runtimes
        self.limiter = limiter
        self.live_providers_enabled = live_providers_enabled
        self.side_effect_ledger = side_effect_ledger
        self.operator_identity = operator_identity
        self.verifiers = verifier_registry or VerifierRegistry(
            [GitDiffVerifier(), OutputContractVerifier(), TrajectoryPolicyVerifier()]
        )
        self.default_verifier_ids = default_verifier_ids
        self.auto_resume_on_reconcile = auto_resume_on_reconcile
        self.background: dict[str, asyncio.Task[None]] = {}
        self.active_refs: dict[str, ActiveRuntimeRef] = {}
        self.event_conditions: dict[str, asyncio.Condition] = {}
        self.pause_requested: set[str] = set()
        self.terminal_locks: dict[str, asyncio.Lock] = {}
        self.approval_conditions: dict[str, asyncio.Condition] = {}
        self.search_executor: SearchNodeExecutor | None = None
        # Set after construction, exactly like ``search_executor`` above: the capability
        # gateway lives in its own process, so the scheduler is handed an invoker rather
        # than building one. ``None`` means capability-bearing nodes execute precisely as
        # they did before v0.3 M5.
        self.capability_invoker: CapabilityNodeInvoker | None = None
        # The two v0.4 routing seams, on the same ``search_executor`` precedent and for the
        # same reason: both implementations own a store, a snapshot builder and — later — a
        # ranker and the P7 experience service, so constructing them here would drag the
        # whole routing stack into every run. They are frozen as protocols in M1.2
        # (``accretion.routing.protocols``) and implemented by M2 and M3 respectively.
        # ``None`` is the default and means a node is planned, dispatched and recorded
        # precisely as it was before v0.4: nothing here calls either attribute, and a
        # deployment that never sets them cannot tell this release from the last.
        self.routing_service: NodeRoutingService | None = None
        self.feedback_pipeline: FeedbackPipeline | None = None

    async def create_project(self, name: str, repository_path: Path) -> Project:
        repository_path = repository_path.resolve(strict=True)
        project = Project(
            project_id=new_id("project"),
            name=name,
            repository_path=repository_path,
        )
        return await self.store.create_project(project)

    async def create_task(
        self,
        *,
        project_id: str,
        objective: str,
        task_patch: dict[str, object],
    ) -> Task:
        project = await self.store.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        envelope = TaskEnvelope(
            task_id=new_id("task"),
            project_id=project_id,
            objective=objective,
            **task_patch,
        )
        task = Task(envelope=envelope)
        prompt, context, profile, decision = build_initial_planning(task, project)
        task = task.model_copy(
            update={
                "envelope": envelope.model_copy(
                    update={
                        "prompt_contract_ref": prompt.prompt_contract_id,
                        "context_policy_ref": context.context_bundle_id,
                    }
                )
            }
        )
        return await self.store.create_task_with_planning(task, prompt, context, profile, decision)

    async def get_task_planning(self, task_id: str) -> TaskPlanning:
        task = await self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        existing = await self.store.get_task_planning(task_id)
        if existing is not None:
            return existing
        project = await self.store.get_project(task.envelope.project_id)
        if project is None:
            raise KeyError(task.envelope.project_id)
        prompt, context, profile, decision = build_initial_planning(task, project)
        return await self.store.save_task_planning(task_id, prompt, context, profile, decision)

    async def override_strategy(
        self,
        *,
        task_id: str,
        requested_mode: ExecutionMode,
        requested_template_id: str,
        reason: str,
    ) -> StrategyOverrideResult:
        planning = await self.get_task_planning(task_id)
        override, decision = evaluate_override(
            profile=planning.current_profile,
            current=planning.current_decision,
            requested_mode=requested_mode,
            requested_template_id=requested_template_id,
            reason=reason,
            operator_identity=self.operator_identity,
        )
        await self.store.append_strategy_override(override, decision)
        return StrategyOverrideResult(
            override=override,
            current_decision=decision or planning.current_decision,
        )

    async def start_run(
        self, task_id: str, provider: Provider, principal_id: str | None = None
    ) -> Run:
        task = await self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        planning = await self.get_task_planning(task_id)
        decision = planning.current_decision
        if not await self.store.list_workflow_templates():
            # Templates are code-defined; the store row is their idempotent,
            # checksum-pinned projection (normally written at API startup).
            await seed_templates(self.store)
        template = await self.store.get_workflow_template(decision.selected_template_id)
        if template is None:
            any_version = [
                candidate
                for candidate in await self.store.list_workflow_templates()
                if candidate.template_id == decision.selected_template_id
            ]
            if any_version:
                raise WorkflowTemplateError(
                    "TEMPLATE_NOT_VALIDATED",
                    f"workflow template {decision.selected_template_id} is "
                    f"{any_version[0].status.value}; only VALIDATED templates may execute",
                )
            raise WorkflowTemplateError(
                "TEMPLATE_UNKNOWN",
                f"workflow template {decision.selected_template_id} is not registered",
            )
        if template.status is not TemplateStatus.VALIDATED:
            raise WorkflowTemplateError(
                "TEMPLATE_NOT_VALIDATED",
                f"workflow template {template.template_id} is {template.status.value}; "
                "only VALIDATED templates may execute",
            )
        if template.mode is not decision.selected_mode:
            raise WorkflowTemplateError(
                "TEMPLATE_MODE_MISMATCH",
                f"template {template.template_id} belongs to {template.mode.value}, "
                f"not {decision.selected_mode.value}",
            )
        if compute_template_checksum(template) != template.checksum:
            raise WorkflowTemplateError(
                "TEMPLATE_CHECKSUM_MISMATCH",
                f"template {template.template_id} content drifted from its checksum",
            )
        self._require_runtime(provider)
        verifier_ids = list(
            dict.fromkeys([*self._verifier_ids(task), *template.required_verifiers])
        )
        self.verifiers.resolve(verifier_ids)
        # A template-declared outcome GATE is the independent human reviewer;
        # gate-free templates keep the P2 independent-verifier requirement.
        has_gates = bool(template.required_approval_gates)
        if (
            decision.requires_independent_verifier
            and not has_gates
            and "independent-review" not in verifier_ids
        ):
            raise VerifierUnavailableError(
                "the selected strategy requires an independent verifier"
            )
        policy = AcceptancePolicy(
            policy_id=new_id("acceptance_policy"),
            required_verifiers=verifier_ids,
            require_independent_reviewer=(
                decision.requires_independent_verifier and not has_gates
            ),
            independent_reviewer_ref=(
                "independent-review"
                if decision.requires_independent_verifier and not has_gates
                else None
            ),
            require_human_if_risk_gte=None if has_gates else RiskLevel.HIGH,
            outcome_check="all declared deterministic verifiers must pass",
        )
        await self.store.save_acceptance_policy(policy)
        run = Run(
            run_id=new_id("run"),
            task_id=task_id,
            project_id=task.envelope.project_id,
            provider=provider,
            state=RunState.PENDING,
            principal_id=principal_id,
            strategy_decision_id=decision.decision_id,
            execution_mode=decision.selected_mode,
            workflow_template_id=decision.selected_template_id,
            acceptance_policy_id=policy.policy_id,
        )
        await self.store.create_run(run)
        graph = instantiate_run_graph(
            template,
            run_id=run.run_id,
            task_id=task_id,
            budgets=task.envelope.budgets,
        )
        await self.store.create_run_graph(graph)
        if decision.selected_mode is ExecutionMode.LOOP:
            execution = build_loop_execution(
                run_id=run.run_id,
                spec=build_loop_spec(task.envelope, verifier_ids),
                policy=policy,
            )
            await self.store.create_loop_execution(execution)
        await self._append(
            self._control_event(
                run,
                "accretion/run-created",
                EventType.RUN_CREATED,
                payload={
                    "strategy_decision_id": decision.decision_id,
                    "execution_mode": decision.selected_mode.value,
                    "workflow_template_id": decision.selected_template_id,
                    "acceptance_policy_id": policy.policy_id,
                    "run_graph_id": graph.run_graph_id,
                    "template_record_id": template.template_record_id,
                    "template_checksum": template.checksum,
                },
            )
        )
        self.background[run.run_id] = asyncio.create_task(self._execute_new(run.run_id))
        return (await self.store.get_run(run.run_id)) or run

    async def prepare_dynamic_run(
        self,
        task_id: str,
        provider: Provider,
        *,
        required_verifiers: list[str],
        has_approval_gates: bool,
    ) -> Run:
        """Create a durable P5 run without making an unvalidated graph executable."""

        task = await self._require_task(task_id)
        planning = await self.get_task_planning(task_id)
        self._require_runtime(provider)
        verifier_ids = list(dict.fromkeys(required_verifiers))
        self.verifiers.resolve(verifier_ids)
        policy = AcceptancePolicy(
            policy_id=new_id("acceptance_policy"),
            required_verifiers=verifier_ids,
            require_human_if_risk_gte=None if has_approval_gates else RiskLevel.HIGH,
            outcome_check="all dynamic-workflow verifiers must pass",
        )
        await self.store.save_acceptance_policy(policy)
        run = Run(
            run_id=new_id("run"),
            task_id=task_id,
            project_id=task.envelope.project_id,
            provider=provider,
            state=RunState.PENDING,
            strategy_decision_id=planning.current_decision.decision_id,
            execution_mode=ExecutionMode.GRAPH,
            acceptance_policy_id=policy.policy_id,
        )
        await self.store.create_run(run)
        await self._append(
            self._control_event(
                run,
                "accretion/dynamic-run-created",
                EventType.RUN_CREATED,
                payload={
                    "strategy_decision_id": planning.current_decision.decision_id,
                    "execution_mode": ExecutionMode.GRAPH.value,
                    "acceptance_policy_id": policy.policy_id,
                    "orchestration_version": "dynamic-v2",
                    "awaiting_graph_validation": True,
                },
            )
        )
        return (await self.store.get_run(run.run_id)) or run

    async def install_dynamic_graph(
        self, run_id: str, template: WorkflowTemplate
    ) -> tuple[Run, RunGraph, WorkflowTemplate]:
        """Install a validated P5 graph, but do not start it before revision evidence exists."""

        run = await self._require_run(run_id)
        if run.state is not RunState.PENDING:
            raise ValueError(f"dynamic graph activation requires PENDING, got {run.state.value}")
        if await self.store.get_run_graph(run_id) is not None:
            raise ValueError(f"run {run_id} already has an active graph")
        task = await self._require_task(run.task_id)
        stored_template = await self.store.upsert_workflow_template(template)
        run = await self.store.update_run(
            run_id,
            RunState.PENDING,
            execution_mode=ExecutionMode.GRAPH,
            workflow_template_id=stored_template.template_id,
        )
        graph = instantiate_run_graph(
            stored_template,
            run_id=run_id,
            task_id=run.task_id,
            budgets=task.envelope.budgets,
        )
        graph = await self.store.create_run_graph(graph)
        return run, graph, stored_template

    async def launch_dynamic_run(self, run_id: str) -> Run:
        """Start a previously installed graph after its immutable revision is durable."""

        run = await self._require_run(run_id)
        if run.state is not RunState.PENDING:
            raise ValueError(f"dynamic launch requires PENDING, got {run.state.value}")
        if await self.store.get_run_graph(run_id) is None:
            raise ValueError(f"run {run_id} has no validated graph")
        active = self.background.get(run_id)
        if active is None or active.done():
            self.background[run_id] = asyncio.create_task(self._execute_new(run_id))
        return (await self.store.get_run(run_id)) or run

    async def install_dynamic_replan(
        self, run_id: str, template: WorkflowTemplate
    ) -> tuple[Run, RunGraph, WorkflowTemplate]:
        """Replace only the active projection; immutable semantic revisions stay separate."""

        run = await self._require_run(run_id)
        if run.state is not RunState.PAUSED:
            raise ValueError(f"dynamic replan requires PAUSED, got {run.state.value}")
        current = await self.store.get_run_graph(run_id)
        if current is None:
            raise ValueError(f"run {run_id} has no active graph")
        prior_checkpoint = await self.store.get_latest_checkpoint(run_id)
        prior_statuses = (
            prior_checkpoint.node_statuses
            if prior_checkpoint is not None
            and prior_checkpoint.run_graph_id == current.run_graph_id
            else {node.key: node.status for node in current.nodes}
        )
        if any(status is GraphNodeStatus.RUNNING for status in prior_statuses.values()):
            raise ValueError("a running node must settle before graph revision activation")
        task = await self._require_task(run.task_id)
        stored_template = await self.store.upsert_workflow_template(template)
        replacement = instantiate_run_graph(
            stored_template,
            run_id=run_id,
            task_id=run.task_id,
            budgets=task.envelope.budgets,
        )
        prior_nodes = {node.key: node for node in current.nodes}
        prior_edges = {edge.key: edge for edge in current.edges}
        replacement = replacement.model_copy(
            update={
                "run_graph_id": current.run_graph_id,
                "nodes": [
                    node.model_copy(
                        update={
                            "status": prior_statuses[node.key],
                            "iteration": prior_nodes[node.key].iteration,
                            "loop_execution_id": prior_nodes[node.key].loop_execution_id,
                            "approval_id": prior_nodes[node.key].approval_id,
                            "verifier_state": prior_nodes[node.key].verifier_state,
                            "terminal_outcome": prior_nodes[node.key].terminal_outcome,
                        }
                    )
                    if node.key in prior_nodes
                    and prior_statuses.get(node.key)
                    in {
                        GraphNodeStatus.SUCCEEDED,
                        GraphNodeStatus.FAILED,
                        GraphNodeStatus.CANCELLED,
                    }
                    else node
                    for node in replacement.nodes
                ],
                "edges": [
                    edge.model_copy(
                        update={"traversal_count": prior.traversal_count}
                    )
                    if (prior := prior_edges.get(edge.key))
                    else edge
                    for edge in replacement.edges
                ],
            }
        )
        graph = await self.store.replace_run_graph(
            replacement, expected_revision=current.graph_revision
        )
        run = await self.store.update_run(
            run_id,
            RunState.PAUSED,
            execution_mode=ExecutionMode.GRAPH,
            workflow_template_id=stored_template.template_id,
        )
        root = next(
            node
            for node in graph.nodes
            if node.node_id not in {edge.target for edge in graph.edges}
        )
        checkpoint = Checkpoint(
            checkpoint_id=new_id("checkpoint"),
            run_id=run_id,
            kind=CheckpointKind.NODE_BOUNDARY,
            sequence=0,
            run_state=RunState.PAUSED,
            run_revision=run.revision,
            active_node_ids=[root.node_id],
            node_statuses={node.key: node.status for node in graph.nodes},
            run_graph_id=graph.run_graph_id,
            graph_revision=graph.graph_revision,
            workspace_lease_id=run.workspace_lease_id,
        )
        await self.store.append_checkpoint(
            checkpoint,
            events=[
                self._control_event(
                    run,
                    "accretion/replan-checkpoint-saved",
                    EventType.CHECKPOINT_SAVED,
                    payload={
                        "checkpoint_id": checkpoint.checkpoint_id,
                        "kind": checkpoint.kind.value,
                        "active_node_ids": checkpoint.active_node_ids,
                        "run_graph_id": graph.run_graph_id,
                        "replan": True,
                    },
                )
            ],
        )
        await self._notify(run_id)
        return run, graph, stored_template

    async def emit_dynamic_event(
        self,
        run_id: str,
        *,
        native_type: str,
        event_type: EventType,
        payload: dict[str, object],
        causation_id: str | None = None,
    ) -> AgentEvent:
        run = await self._require_run(run_id)
        event = self._control_event(run, native_type, event_type, payload=payload)
        if causation_id is not None:
            event = event.model_copy(update={"causation_id": causation_id})
        return await self._append(event)

    async def fallback_dynamic_run(self, run_id: str, *, reason: str) -> Run:
        run = await self._require_run(run_id)
        if await self.store.get_run_graph(run_id) is not None:
            raise ValueError("an installed dynamic graph cannot fall back in place")
        return await self._commit_run_terminal(
            run,
            state=RunState.CANCELLED,
            event_type=EventType.RUN_CANCELLED,
            native_type="accretion/dynamic-static-fallback",
            payload={"reason": reason, "fallback": "validated-v1-static-template"},
        )

    def _require_runtime(self, provider: Provider) -> None:
        if provider not in self.runtimes:
            raise ValueError(f"runtime {provider.value} is not configured")
        if provider in LIVE_PROVIDERS and not self.live_providers_enabled:
            raise PermissionError(
                "live providers are disabled; set ACCRETION_ENABLE_LIVE_PROVIDERS=true"
            )

    def _runtime_for(self, session: SessionRef) -> AgentRuntime:
        """The runtime that is actually executing ``session``.

        ``run.provider`` is the provider the operator *requested* for the run. The
        provider that executes a given call is a property of the session that call was
        submitted on, and the two coincide only for as long as every session on a run
        is created on ``run.provider``. Every site that has a session in hand reads it
        from here instead, so per-node runtime selection becomes a change to session
        creation and to nothing else.
        """

        runtime = self.runtimes.get(session.provider)
        if runtime is None:
            raise ValueError(f"runtime {session.provider.value} is not configured")
        return runtime

    def _verifier_ids(self, task: Task) -> list[str]:
        selected = list(self.default_verifier_ids)
        if task.envelope.required_outputs:
            selected.append("output-contract")
        if task.envelope.task_type in {TaskType.IMPLEMENT, TaskType.EXPERIMENT}:
            selected.append("git-diff")
        outcome_verifiers = {
            verifier_id
            for verifier_id in selected
            if verifier_id not in {"git-diff", "trajectory-policy"}
        }
        if not outcome_verifiers:
            # An absent semantic/output verifier must become INCONCLUSIVE, not self-acceptance.
            selected.append("output-contract")
        selected.append("trajectory-policy")
        return list(dict.fromkeys(selected))

    async def _execute_new(self, run_id: str) -> None:
        run = await self._require_run(run_id)
        task = await self._require_task(run.task_id)
        # A session-creation site, and the only surviving one that reads the run: there
        # is no session yet, so the requested provider is the right and only answer.
        # Everything downstream of ``create_session`` reads the session instead.
        runtime = self.runtimes[run.provider]
        lease: WorkspaceLease | None = None
        session: SessionRef | None = None
        try:
            async with self.limiter.slot(run.provider, run.project_id):
                await self.store.update_run(run_id, RunState.STARTING)
                project = await self.store.get_project(run.project_id)
                if project is None:
                    raise RuntimeError("project disappeared before run start")
                lease = await self.worktrees.acquire(
                    project_id=run.project_id,
                    run_id=run_id,
                    repository=project.repository_path,
                )
                await self.store.save_lease(lease)
                session = await runtime.create_session(
                    SessionConfig(
                        run_id=run_id,
                        workspace=lease.path,
                        allowed_tools=task.envelope.allowed_capabilities,
                        denied_tools=task.envelope.denied_capabilities,
                    )
                )
                await self.store.save_session(session)
                if run_id in self.pause_requested:
                    self.pause_requested.discard(run_id)
                    run = await self.store.update_run(
                        run_id,
                        RunState.PAUSED,
                        session_id=session.session_id,
                        workspace_lease_id=lease.lease_id,
                    )
                    execution = await self.store.get_loop_execution_for_run(run_id)
                    if execution is not None and execution.status is not LoopExecutionStatus.PAUSED:
                        await self.store.update_loop_execution(
                            execution.loop_execution_id,
                            execution.state,
                            status=LoopExecutionStatus.PAUSED,
                            stop_reason=LoopStopReason.INTERRUPTED,
                            expected_revision=execution.revision,
                        )
                    await self._append_pause_if_missing(run)
                    return
                run = await self.store.update_run(
                    run_id,
                    RunState.RUNNING,
                    session_id=session.session_id,
                    workspace_lease_id=lease.lease_id,
                )
                await self._append(
                    self._control_event(run, "accretion/run-started", EventType.RUN_STARTED)
                )
                if run.execution_mode is ExecutionMode.LOOP:
                    await self._execute_loop(run, task, lease, session)
                else:
                    graph = await self.store.get_run_graph(run.run_id)
                    if graph is not None:
                        await self._execute_graph(run, task, lease, session, graph)
                    else:
                        await self._execute_direct(run, task, lease, session)
        except asyncio.CancelledError:
            await self._cancel_execution(run_id)
            raise
        except Exception as exc:
            await self._fail_execution(run_id, exc, session.session_id if session else None)
        finally:
            self.active_refs.pop(run_id, None)
            self.background.pop(run_id, None)

    async def _execute_direct(
        self,
        run: Run,
        task: Task,
        lease: WorkspaceLease,
        session: SessionRef,
    ) -> None:
        deadline = datetime.now(UTC).timestamp() + task.envelope.budgets.wall_time_seconds
        outcome = await self._runtime_call(
            run,
            session,
            task.envelope,
            runtime_call_id=new_id("runtime_call"),
            deadline=deadline,
            node_key="act",
            directive=IterationDirective(
                kind=IterationDirectiveKind.INITIAL,
                objective=task.envelope.objective,
            ),
        )
        if outcome.stop_reason is not None:
            artifact = await self.worktrees.capture_diff(
                lease, name="final.patch", kind="FINAL_GIT_DIFF"
            )
            if artifact:
                await self.store.save_artifact(artifact)
            await self._commit_run_terminal(
                run,
                state=RunState.REQUIRES_HUMAN,
                event_type=EventType.RUN_FAILED,
                native_type="accretion/direct-budget-stop",
                session_id=outcome.session.session_id,
                payload={"stop_reason": outcome.stop_reason.value},
            )
            return
        if outcome.cancelled:
            if run.run_id in self.pause_requested:
                self.pause_requested.discard(run.run_id)
                paused = await self.store.update_run(run.run_id, RunState.PAUSED)
                await self._append_pause_if_missing(paused)
                return
            await self._cancel_execution(run.run_id)
            return
        if not outcome.completed:
            raise RuntimeError(outcome.error.message if outcome.error else "provider call failed")
        artifact = await self.worktrees.capture_diff(
            lease, name="final.patch", kind="FINAL_GIT_DIFF"
        )
        if artifact:
            await self.store.save_artifact(artifact)
        policy = await self._require_policy(run.acceptance_policy_id)
        results = await self._verify_candidate(
            run=run,
            task=task,
            lease=lease,
            session_id=outcome.session.session_id,
            policy=policy,
            artifact_ref=artifact.artifact_id if artifact else None,
            diff_sha256=artifact.sha256 if artifact else None,
        )
        acceptance = evaluate_acceptance_policy(
            policy, results, risk=task.envelope.risk_level
        ).status
        if run.run_id in self.pause_requested:
            self.pause_requested.discard(run.run_id)
            paused = await self.store.update_run(run.run_id, RunState.PAUSED)
            await self._append_pause_if_missing(paused)
            return
        if acceptance is VerificationStatus.PASS:
            state = RunState.SUCCEEDED
            terminal = EventType.RUN_COMPLETED
        elif acceptance is VerificationStatus.FAIL:
            state = RunState.FAILED
            terminal = EventType.RUN_FAILED
        else:
            state = RunState.REQUIRES_HUMAN
            terminal = EventType.RUN_FAILED
        await self._commit_run_terminal(
            run,
            state=state,
            event_type=terminal,
            native_type="accretion/acceptance-result",
            session_id=outcome.session.session_id,
            payload={"acceptance": acceptance.value},
        )
        await self.worktrees.release(lease, successful=state is RunState.SUCCEEDED)

    @staticmethod
    def _key_of(node_id: str) -> str:
        return node_id.rsplit(":", 1)[-1]

    async def _load_graph_cursor(
        self, run: Run, graph: RunGraph, entry_key: str
    ) -> _GraphCursor:
        """Rebuild scheduler state from the last valid checkpoint and events."""

        statuses = {node.key: GraphNodeStatus.PENDING for node in graph.nodes}
        entered_via: dict[str, int] = {}
        gate_wait = 0.0
        required_at: dict[str, datetime] = {}
        for event in await self.store.list_events(run.run_id):
            if event.normalized_type is EventType.NODE_ENTERED:
                via = event.payload.get("entered_via")
                if via:
                    entered_via[str(via)] = entered_via.get(str(via), 0) + 1
            elif event.normalized_type is EventType.APPROVAL_REQUIRED and event.payload.get(
                "approval_id"
            ):
                required_at[str(event.payload["approval_id"])] = event.timestamp
            elif event.normalized_type is EventType.APPROVAL_RESOLVED and event.payload.get(
                "approval_id"
            ):
                started = required_at.pop(str(event.payload["approval_id"]), None)
                if started is not None:
                    gate_wait += max(0.0, (event.timestamp - started).total_seconds())
        # A still-pending gate's entire span, including downtime, is wait: the
        # wall clock stays paused while a durable PENDING approval exists.
        now = datetime.now(UTC)
        for started in required_at.values():
            gate_wait += max(0.0, (now - started).total_seconds())
        cursor = _GraphCursor(
            statuses=statuses,
            entered_via=entered_via,
            current_key=entry_key,
            gate_wait_seconds=gate_wait,
        )
        checkpoint = await self.store.get_latest_checkpoint(run.run_id)
        if checkpoint is not None and checkpoint.run_graph_id == graph.run_graph_id:
            for key, status in checkpoint.node_statuses.items():
                if key in statuses:
                    statuses[key] = status
            if checkpoint.active_node_ids:
                active_key = self._key_of(checkpoint.active_node_ids[0])
                if active_key in statuses:
                    cursor.current_key = active_key
            # Restore the durable routing decision so guard-dependent behavior
            # (a TERMINAL commit, a REPAIR retry) survives a restart.
            if checkpoint.arrival_edge_key:
                edge = next(
                    (
                        item
                        for item in graph.edges
                        if item.key == checkpoint.arrival_edge_key
                    ),
                    None,
                )
                if edge is not None:
                    cursor.entry_edge_key = edge.key
                    cursor.arrival_guard = edge.guard
                    cursor.arrival_edge_kind = edge.kind
                    cursor.entered_via_retry = edge.kind is GraphEdgeKind.RETRY
                    if cursor.entered_via_retry:
                        cursor.last_results = self._latest_verifications(
                            await self.store.list_verifications(run.run_id)
                        )
        return cursor

    async def _remaining_run_budgets(self, run_id: str, task: Task) -> tuple[int, int]:
        spent = await self.store.get_budget_spent(run_id)
        budgets = task.envelope.budgets
        return (
            budgets.max_turns - spent["turns"],
            budgets.max_tool_calls - spent["tool_calls"],
        )

    @staticmethod
    def _latest_verifications(
        results: list[VerificationResult],
    ) -> list[VerificationResult]:
        latest: dict[str, VerificationResult] = {}
        for result in results:
            current = latest.get(result.verifier_id)
            if current is None or result.executed_at >= current.executed_at:
                latest[result.verifier_id] = result
        return list(latest.values())

    async def _graph_checkpoint(
        self,
        run: Run,
        graph: RunGraph,
        cursor: _GraphCursor,
        lease: WorkspaceLease,
        *,
        active_key: str,
        session_id: str,
    ) -> None:
        current = await self._require_run(run.run_id)
        executions = await self.store.list_loop_executions_for_run(run.run_id)
        checkpoint = Checkpoint(
            checkpoint_id=new_id("checkpoint"),
            run_id=run.run_id,
            kind=CheckpointKind.NODE_BOUNDARY,
            sequence=0,
            run_state=current.state,
            run_revision=current.revision,
            active_node_ids=[f"{run.run_id}:{active_key}"],
            arrival_edge_key=cursor.entry_edge_key,
            node_statuses=dict(cursor.statuses),
            loop_cursors=[
                CheckpointLoopCursor(
                    loop_execution_id=execution.loop_execution_id,
                    iteration=execution.state.iteration,
                    revision=execution.revision,
                    status=execution.status,
                )
                for execution in executions
            ],
            run_graph_id=graph.run_graph_id,
            graph_revision=graph.graph_revision,
            workspace_lease_id=lease.lease_id,
            workspace_revision=lease.base_revision,
        )
        event = self._control_event(
            run,
            "accretion/checkpoint-saved",
            EventType.CHECKPOINT_SAVED,
            session_id=session_id,
            node_key=active_key,
            payload={
                "checkpoint_id": checkpoint.checkpoint_id,
                "kind": checkpoint.kind.value,
                "active_node_ids": checkpoint.active_node_ids,
                "run_graph_id": graph.run_graph_id,
            },
        )
        await self.store.append_checkpoint(checkpoint, events=[event])
        await self._notify(run.run_id)

    async def _execute_graph(
        self,
        run: Run,
        task: Task,
        lease: WorkspaceLease,
        session: SessionRef,
        graph: RunGraph,
    ) -> None:
        template = await self.store.get_workflow_template(
            graph.template_id, graph.template_version
        )
        if template is None:
            raise RuntimeError(f"run graph template {graph.template_id} is not registered")
        nodes = {node.key: node for node in graph.nodes}
        template_nodes = {spec.key: spec for spec in template.nodes}
        out_edges: dict[str, list[RunEdge]] = {}
        inbound_keys: set[str] = set()
        for edge in graph.edges:
            out_edges.setdefault(self._key_of(edge.source), []).append(edge)
            inbound_keys.add(self._key_of(edge.target))
        region_owner: dict[str, str] = {}
        for spec in template.nodes:
            if spec.loop is not None:
                for member in spec.loop.region_keys:
                    if member != spec.key:
                        region_owner[member] = spec.key
        gates = {gate.node_key: gate for gate in template.required_approval_gates}
        entry_key = next(node.key for node in graph.nodes if node.key not in inbound_keys)
        cursor = await self._load_graph_cursor(run, graph, entry_key)
        policy = await self._require_policy(run.acceptance_policy_id)

        while True:
            deadline = (
                run.created_at.timestamp()
                + task.envelope.budgets.wall_time_seconds
                + cursor.gate_wait_seconds
            )
            if run.run_id in self.pause_requested:
                self.pause_requested.discard(run.run_id)
                await self._pause_graph(run)
                return
            node = nodes[cursor.current_key]
            if node.kind is GraphNodeKind.TERMINAL:
                await self._commit_graph_terminal(run, lease, session, cursor, node)
                return
            outcome, session = await self._run_graph_node(
                run,
                task,
                lease,
                session,
                node=node,
                template_node=template_nodes[node.key],
                template=template,
                gate=gates.get(node.key),
                policy=policy,
                deadline=deadline,
                cursor=cursor,
            )
            if outcome is NodeOutcome.PAUSED:
                await self._pause_graph(run)
                return
            if outcome is NodeOutcome.CANCELLED:
                await self._cancel_execution(run.run_id)
                return
            if outcome is NodeOutcome.BUDGET_EXHAUSTED:
                await self._graph_budget_stop(run, lease, session, cursor)
                return
            selected, selection_error = self._select_edge(
                node, outcome, out_edges.get(node.key, []), cursor, template
            )
            if selected is None:
                await self._commit_run_terminal(
                    run,
                    state=RunState.REQUIRES_HUMAN,
                    event_type=EventType.RUN_FAILED,
                    native_type="accretion/graph-no-eligible-edge",
                    session_id=session.session_id,
                    payload={"node": node.key, "outcome": outcome.value},
                    error=selection_error
                    or ErrorSummary(
                        code="GRAPH_NO_ELIGIBLE_EDGE",
                        message=(
                            f"node {node.key} produced {outcome.value} with no eligible edge"
                        ),
                    ),
                )
                await self.worktrees.release(lease, successful=False)
                return
            target_key = self._key_of(selected.target)
            routed_key = region_owner.get(target_key, target_key)
            cursor.entry_edge_key = selected.key
            cursor.arrival_guard = selected.guard
            cursor.arrival_edge_kind = selected.kind
            cursor.entered_via_retry = selected.kind is GraphEdgeKind.RETRY
            cursor.entered_via[selected.key] = cursor.entered_via.get(selected.key, 0) + 1
            await self._graph_checkpoint(
                run,
                graph,
                cursor,
                lease,
                active_key=routed_key,
                session_id=session.session_id,
            )
            cursor.current_key = routed_key

    async def _run_graph_node(
        self,
        run: Run,
        task: Task,
        lease: WorkspaceLease,
        session: SessionRef,
        *,
        node: RunNode,
        template_node: object,
        template: WorkflowTemplate,
        gate: object,
        policy: AcceptancePolicy,
        deadline: float,
        cursor: _GraphCursor,
    ) -> tuple[NodeOutcome, SessionRef]:
        from accretion.contracts import GateSpec

        spec = template_node if isinstance(template_node, WorkflowNodeSpec) else None
        entered_via = cursor.entry_edge_key
        cursor.entry_edge_key = None
        if node.kind is GraphNodeKind.TASK:
            if cursor.statuses.get(node.key) is GraphNodeStatus.SUCCEEDED:
                return NodeOutcome.SUCCESS, session
            await self._node_transition(
                run, session.session_id, node.key, entered=True, entered_via=entered_via
            )
            await self._node_transition(run, session.session_id, node.key, entered=False)
            cursor.statuses[node.key] = GraphNodeStatus.SUCCEEDED
            return NodeOutcome.SUCCESS, session
        if node.kind is GraphNodeKind.AGENT:
            return await self._graph_agent(
                run,
                task,
                lease,
                session,
                node=node,
                instruction=spec.instruction if spec else None,
                deadline=deadline,
                cursor=cursor,
                entered_via=entered_via,
            )
        if node.kind is GraphNodeKind.TOOL:
            await self._node_transition(
                run, session.session_id, node.key, entered=True, entered_via=entered_via
            )
            # The section 27 exit criterion. Capability references travel from the
            # proposal through materialization to here, and are spent through the
            # governed gateway, which normalizes and stores the evidence itself. A node
            # with no references does not reach this call at all, so the diff capture
            # below --- the entirety of pre-M5 TOOL behaviour --- is untouched.
            if spec is not None and spec.capability_refs:
                await self._invoke_node_capabilities(
                    run, node, spec, executing_provider=session.provider
                )
            captures = cursor.entered_via.get(f"capture:{node.key}", 0) + 1
            cursor.entered_via[f"capture:{node.key}"] = captures
            artifact = await self.worktrees.capture_diff(
                lease,
                name=f"{node.key}-{captures:03}.patch",
                kind="GRAPH_NODE_GIT_DIFF",
            )
            if artifact:
                await self.store.save_artifact(artifact)
                cursor.last_artifact_id = artifact.artifact_id
                cursor.last_artifact_sha256 = artifact.sha256
            await self._node_transition(run, session.session_id, node.key, entered=False)
            cursor.statuses[node.key] = GraphNodeStatus.SUCCEEDED
            return NodeOutcome.SUCCESS, session
        if node.kind is GraphNodeKind.VERIFIER:
            return await self._graph_verifier(
                run,
                task,
                lease,
                session,
                node=node,
                policy=policy,
                cursor=cursor,
                entered_via=entered_via,
            )
        if node.kind is GraphNodeKind.GATE:
            assert isinstance(gate, GateSpec)
            return await self._graph_gate(
                run,
                task,
                session,
                node=node,
                gate=gate,
                cursor=cursor,
                entered_via=entered_via,
            )
        if node.kind is GraphNodeKind.LOOP:
            assert spec is not None and spec.loop is not None
            return await self._graph_loop(
                run,
                task,
                lease,
                session,
                node=node,
                loop_policy=spec.loop,
                template=template,
                policy=policy,
                deadline=deadline,
                cursor=cursor,
                entered_via=entered_via,
            )
        raise RuntimeError(f"unsupported graph node kind {node.kind.value}")

    async def _invoke_node_capabilities(
        self,
        run: Run,
        node: RunNode,
        spec: WorkflowNodeSpec,
        *,
        executing_provider: Provider | None = None,
    ) -> None:
        """Spend each of the node's capability references, in declared order.

        Order is the template's, not a set's, so two runs of the same template invoke
        the same capabilities in the same sequence and the evidence they produce sorts
        identically.

        The query is the node's own text. Per-capability argument binding is genuinely
        richer than one string --- it is what ``input_transform_ref`` exists for --- but
        binding arbitrary node inputs is M6 work; what M5 needs is that the canonical
        capability id, and only the capability id, crosses this seam.

        One reference failing does not stop the next, and none of them changes the
        node's outcome: this loop cannot make a TOOL node fail.

        ``executing_provider`` is the provider of the session the node is running on,
        and is what the gateway's authorization terminals and audit events name. It
        defaults to the run's requested provider so that the two callers that hold no
        session --- the tests that spend a node's references directly --- attribute
        exactly what they attributed before.
        """

        if self.capability_invoker is None:
            return
        query = (spec.instruction or spec.label).strip()
        if not query:
            return
        provider = executing_provider if executing_provider is not None else run.provider
        for capability_id in spec.capability_refs:
            try:
                await self.capability_invoker(
                    run_id=run.run_id,
                    node_id=node.key,
                    capability_id=capability_id,
                    arguments={"query": query},
                    executing_provider=provider,
                )
            except Exception:  # noqa: BLE001 - a reference must not abort the run
                continue

    async def _graph_agent(
        self,
        run: Run,
        task: Task,
        lease: WorkspaceLease,
        session: SessionRef,
        *,
        node: RunNode,
        instruction: str | None,
        deadline: float,
        cursor: _GraphCursor,
        entered_via: str | None,
    ) -> tuple[NodeOutcome, SessionRef]:
        revisions = await self.store.list_graph_revisions(run.run_id)
        graph_revision = revisions[-1].revision if revisions else 1
        searches = [
            item
            for item in await self.store.list_searches(run.run_id)
            if item.plan.parent_node_id == node.key
            and item.plan.graph_revision == graph_revision
        ]
        if searches:
            if len(searches) != 1 or self.search_executor is None:
                cursor.last_error = ErrorSummary(
                    code="SEARCH_EXECUTOR_UNAVAILABLE",
                    message="the pending search plan has no unique executor",
                )
                return NodeOutcome.FAIL, session
            await self._node_transition(
                run,
                session.session_id,
                node.key,
                entered=True,
                entered_via=entered_via,
            )
            record = searches[0]
            if record.status is SearchStatus.PLANNED:
                record = await self.search_executor(
                    record,
                    run,
                    task,
                    lease,
                    node,
                    await self._require_policy(run.acceptance_policy_id),
                )
            successful = record.status is SearchStatus.SUCCEEDED
            waiting = record.status in {
                SearchStatus.REQUIRES_HUMAN,
                SearchStatus.RUNNING,
                SearchStatus.SELECTING,
            }
            await self._node_transition(
                run,
                session.session_id,
                node.key,
                entered=False,
                status=(
                    "SUCCEEDED"
                    if successful
                    else "WAITING"
                    if waiting
                    else "CANCELLED"
                    if record.status is SearchStatus.CANCELLED
                    else "FAILED"
                ),
            )
            if successful:
                cursor.statuses[node.key] = GraphNodeStatus.SUCCEEDED
                selected = (
                    await self.store.get_search_candidate(record.selected_candidate_id)
                    if record.selected_candidate_id
                    else None
                )
                if selected and selected.artifact_refs:
                    cursor.last_artifact_id = selected.artifact_refs[-1]
                    cursor.last_artifact_sha256 = selected.patch_sha256
                return NodeOutcome.SUCCESS, session
            if record.status is SearchStatus.CANCELLED:
                cursor.statuses[node.key] = GraphNodeStatus.CANCELLED
                return NodeOutcome.CANCELLED, session
            if waiting:
                cursor.statuses[node.key] = GraphNodeStatus.WAITING
                return NodeOutcome.INCONCLUSIVE, session
            cursor.statuses[node.key] = GraphNodeStatus.FAILED
            return NodeOutcome.FAIL, session
        objective = task.envelope.objective
        if instruction:
            objective = f"{objective}\n\n{instruction}"
        if cursor.entered_via_retry and cursor.last_results:
            directive = IterationDirective(
                kind=IterationDirectiveKind.REPAIR,
                objective=objective,
                findings=[
                    item for result in cursor.last_results for item in result.findings
                ],
                evidence_refs=[
                    ref for result in cursor.last_results for ref in result.evidence_refs
                ],
            )
        else:
            directive = IterationDirective(
                kind=IterationDirectiveKind.INITIAL, objective=objective
            )
        # Task turn/tool ceilings are cumulative across the whole graph; every
        # call draws from the same durable run-level account.
        turns_left, tool_calls_left = await self._remaining_run_budgets(run.run_id, task)
        if turns_left <= 0 or tool_calls_left <= 0:
            cursor.stop_reason = (
                LoopStopReason.MAX_TURNS if turns_left <= 0 else LoopStopReason.MAX_TOOL_CALLS
            )
            return NodeOutcome.BUDGET_EXHAUSTED, session
        await self._node_transition(
            run, session.session_id, node.key, entered=True, entered_via=entered_via
        )
        outcome = await self._runtime_call(
            run,
            session,
            task.envelope.model_copy(
                update={
                    "objective": objective,
                    "budgets": task.envelope.budgets.model_copy(
                        update={
                            "max_turns": turns_left,
                            "max_tool_calls": tool_calls_left,
                        }
                    ),
                }
            ),
            runtime_call_id=new_id("runtime_call"),
            deadline=deadline,
            node_key=node.key,
            directive=directive,
        )
        session = outcome.session
        await self.store.add_budget_spent(
            run.run_id, turns=1, tool_calls=outcome.tool_calls
        )
        await self._node_transition(
            run,
            session.session_id,
            node.key,
            entered=False,
            status="SUCCEEDED" if outcome.completed else "FAILED",
        )
        if outcome.stop_reason is not None:
            cursor.stop_reason = outcome.stop_reason
            cursor.statuses[node.key] = GraphNodeStatus.FAILED
            return NodeOutcome.BUDGET_EXHAUSTED, session
        if outcome.cancelled:
            if run.run_id in self.pause_requested:
                self.pause_requested.discard(run.run_id)
                return NodeOutcome.PAUSED, session
            return NodeOutcome.CANCELLED, session
        if outcome.completed:
            cursor.statuses[node.key] = GraphNodeStatus.SUCCEEDED
            return NodeOutcome.SUCCESS, session
        cursor.last_error = outcome.error
        cursor.statuses[node.key] = GraphNodeStatus.FAILED
        return NodeOutcome.FAIL, session

    async def _graph_verifier(
        self,
        run: Run,
        task: Task,
        lease: WorkspaceLease,
        session: SessionRef,
        *,
        node: RunNode,
        policy: AcceptancePolicy,
        cursor: _GraphCursor,
        entered_via: str | None,
    ) -> tuple[NodeOutcome, SessionRef]:
        await self._node_transition(
            run, session.session_id, node.key, entered=True, entered_via=entered_via
        )
        if cursor.last_artifact_id is None:
            artifact = await self.worktrees.capture_diff(
                lease, name="final.patch", kind="FINAL_GIT_DIFF"
            )
            if artifact:
                await self.store.save_artifact(artifact)
                cursor.last_artifact_id = artifact.artifact_id
                cursor.last_artifact_sha256 = artifact.sha256
        results = await self._verify_candidate(
            run=run,
            task=task,
            lease=lease,
            session_id=session.session_id,
            policy=policy,
            artifact_ref=cursor.last_artifact_id,
            diff_sha256=cursor.last_artifact_sha256,
        )
        cursor.last_results = results
        acceptance = evaluate_acceptance_policy(
            policy, results, risk=task.envelope.risk_level
        ).status
        status = {
            VerificationStatus.PASS: "SUCCEEDED",
            VerificationStatus.FAIL: "FAILED",
            VerificationStatus.INCONCLUSIVE: "WAITING",
        }[acceptance]
        await self._node_transition(
            run, session.session_id, node.key, entered=False, status=status
        )
        cursor.statuses[node.key] = GraphNodeStatus(status)
        return {
            VerificationStatus.PASS: NodeOutcome.SUCCESS,
            VerificationStatus.FAIL: NodeOutcome.FAIL,
            VerificationStatus.INCONCLUSIVE: NodeOutcome.INCONCLUSIVE,
        }[acceptance], session

    async def _graph_gate(
        self,
        run: Run,
        task: Task,
        session: SessionRef,
        *,
        node: RunNode,
        gate: object,
        cursor: _GraphCursor,
        entered_via: str | None,
    ) -> tuple[NodeOutcome, SessionRef]:
        from accretion.contracts import GateSpec

        assert isinstance(gate, GateSpec)
        record = await self.store.save_approval(
            ApprovalRecord(
                approval_id=new_id("approval"),
                run_id=run.run_id,
                node_id=node.node_id,
                native_request_id=f"gate:{node.key}",
                method="accretion/gate",
                summary=gate.summary,
                payload={"gate_id": gate.gate_id},
            )
        )
        await self._node_transition(
            run, session.session_id, node.key, entered=True, entered_via=entered_via
        )
        # A task that is protected for any reason - risk at or above the gate
        # threshold, or irreversible capabilities - always requires the human
        # decision; auto-approval exists only for genuinely unprotected runs
        # that were manually routed onto a gated template.
        auto_approve = RISK_RANK[task.envelope.risk_level] < RISK_RANK[
            gate.required_for_risk_gte
        ] and not has_irreversible_capabilities(task.envelope.allowed_capabilities)
        if record.status is ApprovalStatus.PENDING and auto_approve:
            # Documented SDD deviation: template gates below their risk
            # threshold auto-approve with durable evidence, so a manually
            # overridden low-risk graph run does not dead-end unattended.
            record = await self.store.decide_approval(
                record.approval_id, ApprovalDecisionValue.APPROVE
            )
            await self._append(
                self._control_event(
                    run,
                    "accretion/gate-auto-approved",
                    EventType.APPROVAL_RESOLVED,
                    session_id=session.session_id,
                    node_key=node.key,
                    payload={
                        "approval_id": record.approval_id,
                        "gate_id": gate.gate_id,
                        "decision": ApprovalDecisionValue.APPROVE.value,
                        "auto_approved": True,
                    },
                )
            )
        elif record.status is ApprovalStatus.PENDING:
            await self._append(
                self._control_event(
                    run,
                    "accretion/gate-approval-required",
                    EventType.APPROVAL_REQUIRED,
                    session_id=session.session_id,
                    node_key=node.key,
                    payload={
                        "approval_id": record.approval_id,
                        "gate_id": gate.gate_id,
                        "summary": gate.summary,
                    },
                )
            )
            cursor.statuses[node.key] = GraphNodeStatus.WAITING
            wait_started = time.monotonic()
            condition = self.approval_conditions.setdefault(
                record.approval_id, asyncio.Condition()
            )
            while True:
                current = await self.store.get_approval(record.approval_id)
                if current is not None and current.status is not ApprovalStatus.PENDING:
                    record = current
                    break
                if run.run_id in self.pause_requested:
                    self.pause_requested.discard(run.run_id)
                    cursor.gate_wait_seconds += time.monotonic() - wait_started
                    return NodeOutcome.PAUSED, session
                async with condition:
                    try:
                        await asyncio.wait_for(condition.wait(), timeout=0.2)
                    except TimeoutError:
                        continue
            # The wall clock pauses while a durable PENDING approval waits.
            cursor.gate_wait_seconds += time.monotonic() - wait_started
            await self._append(
                self._control_event(
                    run,
                    "accretion/gate-approval-resolved",
                    EventType.APPROVAL_RESOLVED,
                    session_id=session.session_id,
                    node_key=node.key,
                    payload={
                        "approval_id": record.approval_id,
                        "gate_id": gate.gate_id,
                        "decision": record.decision.value if record.decision else None,
                    },
                )
            )
        else:
            # Decided while the backend was down; make the evidence durable
            # exactly once.
            resolved_already = any(
                event.normalized_type is EventType.APPROVAL_RESOLVED
                and event.payload.get("approval_id") == record.approval_id
                for event in await self.store.list_events(run.run_id)
            )
            if not resolved_already:
                await self._append(
                    self._control_event(
                        run,
                        "accretion/gate-approval-resolved",
                        EventType.APPROVAL_RESOLVED,
                        session_id=session.session_id,
                        node_key=node.key,
                        payload={
                            "approval_id": record.approval_id,
                            "gate_id": gate.gate_id,
                            "decision": record.decision.value if record.decision else None,
                        },
                    )
                )
        approved = record.status is ApprovalStatus.APPROVED
        await self._node_transition(
            run,
            session.session_id,
            node.key,
            entered=False,
            status="SUCCEEDED" if approved else "FAILED",
        )
        cursor.statuses[node.key] = (
            GraphNodeStatus.SUCCEEDED if approved else GraphNodeStatus.FAILED
        )
        return (NodeOutcome.APPROVED if approved else NodeOutcome.DENIED), session

    async def _graph_loop(
        self,
        run: Run,
        task: Task,
        lease: WorkspaceLease,
        session: SessionRef,
        *,
        node: RunNode,
        loop_policy: object,
        template: WorkflowTemplate,
        policy: AcceptancePolicy,
        deadline: float,
        cursor: _GraphCursor,
        entered_via: str | None,
    ) -> tuple[NodeOutcome, SessionRef]:
        from accretion.contracts import NodeLoopPolicy

        assert isinstance(loop_policy, NodeLoopPolicy)
        terminal_statuses = {
            LoopExecutionStatus.SUCCEEDED,
            LoopExecutionStatus.FAILED,
            LoopExecutionStatus.CANCELLED,
            LoopExecutionStatus.REQUIRES_HUMAN,
        }
        execution = await self.store.get_loop_execution_for_node(run.run_id, node.key)
        if execution is None or execution.status in terminal_statuses:
            attempt = execution.attempt + 1 if execution else 1
            budgets = task.envelope.budgets
            # A new attempt draws from the run's remaining cumulative budget,
            # never a fresh grant of the original ceilings.
            turns_left, tool_calls_left = await self._remaining_run_budgets(
                run.run_id, task
            )
            if turns_left <= 0 or tool_calls_left <= 0:
                cursor.stop_reason = (
                    LoopStopReason.MAX_TURNS
                    if turns_left <= 0
                    else LoopStopReason.MAX_TOOL_CALLS
                )
                return NodeOutcome.BUDGET_EXHAUSTED, session
            remaining_wall = max(1, int(deadline - datetime.now(UTC).timestamp()))
            spec = LoopSpec(
                loop_id=new_id("loop"),
                max_iterations=node.max_iterations or budgets.max_loop_iterations,
                max_wall_time_seconds=max(
                    1, int(remaining_wall * loop_policy.budget_fraction)
                ),
                max_tool_calls=max(
                    1,
                    min(
                        int(budgets.max_tool_calls * loop_policy.budget_fraction),
                        tool_calls_left,
                    ),
                ),
                max_turns=max(
                    1,
                    min(int(budgets.max_turns * loop_policy.budget_fraction), turns_left),
                ),
            )
            execution = await self.store.create_loop_execution(
                LoopExecution(
                    loop_execution_id=new_id("loop_execution"),
                    run_id=run.run_id,
                    node_key=node.key,
                    attempt=attempt,
                    spec=spec,
                    state=LoopState(
                        budget_remaining=LoopBudgetRemaining(
                            wall_time_seconds=spec.max_wall_time_seconds,
                            tool_calls=spec.max_tool_calls,
                            turns=spec.max_turns,
                            iterations=spec.max_iterations,
                        )
                    ),
                    acceptance_policy_ref=policy.policy_id,
                )
            )
        await self._node_transition(
            run, session.session_id, node.key, entered=True, entered_via=entered_via
        )
        outcome, session = await self._run_bounded_region(
            run,
            task,
            lease,
            session,
            node=node,
            loop_policy=loop_policy,
            template=template,
            execution=execution,
            cursor=cursor,
        )
        status = {
            NodeOutcome.SUCCESS: "SUCCEEDED",
            NodeOutcome.FAIL: "FAILED",
            NodeOutcome.BUDGET_EXHAUSTED: "FAILED",
            NodeOutcome.PAUSED: "WAITING",
            NodeOutcome.CANCELLED: "CANCELLED",
        }.get(outcome, "FAILED")
        await self._node_transition(
            run, session.session_id, node.key, entered=False, status=status
        )
        cursor.statuses[node.key] = GraphNodeStatus(status)
        return outcome, session

    async def _run_bounded_region(
        self,
        run: Run,
        task: Task,
        lease: WorkspaceLease,
        session: SessionRef,
        *,
        node: RunNode,
        loop_policy: object,
        template: WorkflowTemplate,
        execution: LoopExecution,
        cursor: _GraphCursor,
    ) -> tuple[NodeOutcome, SessionRef]:
        """Drive a bounded, unverified loop region (verify_in_region=False).

        Region completion at the iteration ceiling is normal completion, not
        an escalation: acceptance is judged by the downstream VERIFIER node."""

        from accretion.contracts import NodeLoopPolicy

        assert isinstance(loop_policy, NodeLoopPolicy)
        act_key = loop_policy.act_key
        observe_key = loop_policy.observe_key
        act_spec = next(
            (spec for spec in template.nodes if spec.key == act_key), None
        )
        deadline = (
            datetime.now(UTC).timestamp()
            + execution.state.budget_remaining.wall_time_seconds
        )
        if execution.status in {LoopExecutionStatus.PENDING, LoopExecutionStatus.PAUSED}:
            execution = await self.store.update_loop_execution(
                execution.loop_execution_id,
                execution.state,
                status=LoopExecutionStatus.RUNNING,
                expected_revision=execution.revision,
            )
        previous_iterations = await self.store.list_loop_iterations(
            execution.loop_execution_id
        )
        while True:
            if run.run_id in self.pause_requested:
                self.pause_requested.discard(run.run_id)
                await self.store.update_loop_execution(
                    execution.loop_execution_id,
                    execution.state,
                    status=LoopExecutionStatus.PAUSED,
                    stop_reason=LoopStopReason.INTERRUPTED,
                    expected_revision=execution.revision,
                )
                return NodeOutcome.PAUSED, session
            if execution.state.budget_remaining.iterations <= 0:
                # Resume parity with the in-loop ceiling: completion is only
                # SUCCESS when the final persisted attempt actually completed.
                last = previous_iterations[-1] if previous_iterations else None
                completed = last is not None and last.status is LoopIterationStatus.COMPLETED
                execution = await self.store.update_loop_execution(
                    execution.loop_execution_id,
                    execution.state,
                    status=(
                        LoopExecutionStatus.SUCCEEDED
                        if completed
                        else LoopExecutionStatus.FAILED
                    ),
                    stop_reason=(
                        LoopStopReason.MAX_ITERATIONS
                        if completed
                        else LoopStopReason.PROVIDER_FAILURE
                    ),
                    expected_revision=execution.revision,
                )
                return (
                    NodeOutcome.SUCCESS if completed else NodeOutcome.FAIL
                ), session
            region_remaining = execution.state.budget_remaining
            if region_remaining.tool_calls <= 0 or region_remaining.turns <= 0:
                cursor.stop_reason = (
                    LoopStopReason.MAX_TOOL_CALLS
                    if region_remaining.tool_calls <= 0
                    else LoopStopReason.MAX_TURNS
                )
                await self.store.update_loop_execution(
                    execution.loop_execution_id,
                    execution.state,
                    status=LoopExecutionStatus.REQUIRES_HUMAN,
                    stop_reason=cursor.stop_reason,
                    expected_revision=execution.revision,
                )
                return NodeOutcome.BUDGET_EXHAUSTED, session
            if datetime.now(UTC).timestamp() >= deadline:
                cursor.stop_reason = LoopStopReason.WALL_TIME_EXCEEDED
                await self.store.update_loop_execution(
                    execution.loop_execution_id,
                    execution.state,
                    status=LoopExecutionStatus.REQUIRES_HUMAN,
                    stop_reason=LoopStopReason.WALL_TIME_EXCEEDED,
                    expected_revision=execution.revision,
                )
                return NodeOutcome.BUDGET_EXHAUSTED, session
            number = execution.state.iteration + 1
            iteration_id = new_id("iteration")
            await self._append(
                self._control_event(
                    run,
                    "accretion/loop-iteration-started",
                    EventType.LOOP_ITERATION_STARTED,
                    session_id=session.session_id,
                    node_key=node.key,
                    payload={"iteration_id": iteration_id, "number": number},
                )
            )
            await self._node_transition(run, session.session_id, act_key, entered=True)
            objective = task.envelope.objective
            if act_spec is not None and act_spec.instruction:
                objective = f"{objective}\n\n{act_spec.instruction}"
            outcome = await self._runtime_call(
                run,
                session,
                self._iteration_envelope(
                    task.envelope.model_copy(update={"objective": objective}),
                    IterationDirective(
                        kind=IterationDirectiveKind.INITIAL, objective=objective
                    ),
                    deadline,
                    execution.state.budget_remaining,
                ),
                runtime_call_id=new_id("runtime_call"),
                deadline=deadline,
                node_key=act_key,
                iteration_number=number,
                directive=IterationDirective(
                    kind=IterationDirectiveKind.INITIAL, objective=objective
                ),
            )
            session = outcome.session
            await self._node_transition(
                run,
                session.session_id,
                act_key,
                entered=False,
                status="SUCCEEDED" if outcome.completed else "FAILED",
            )
            if outcome.cancelled and outcome.stop_reason is None:
                if run.run_id in self.pause_requested:
                    self.pause_requested.discard(run.run_id)
                    # Close the started attempt durably: the interrupted
                    # iteration and its consumed budget commit atomically with
                    # the PAUSED transition, so resume starts at N+1 with a
                    # diminished budget instead of replaying the attempt free.
                    interrupted = RuntimeCallOutcome(
                        session=outcome.session,
                        ref=outcome.ref,
                        completed=False,
                        cancelled=True,
                        tool_calls=outcome.tool_calls,
                        error=outcome.error,
                        stop_reason=LoopStopReason.INTERRUPTED,
                    )
                    next_state, iteration = self._next_iteration_state(
                        execution=execution,
                        run=run,
                        iteration_id=iteration_id,
                        number=number,
                        outcome=interrupted,
                        artifact_ref=None,
                        diff_sha256=None,
                        results=[],
                        deadline=deadline,
                        previous=previous_iterations[-1] if previous_iterations else None,
                    )
                    iteration = iteration.model_copy(
                        update={"status": LoopIterationStatus.INTERRUPTED}
                    )
                    await self.store.append_loop_iteration(
                        execution.loop_execution_id,
                        iteration,
                        next_state,
                        status=LoopExecutionStatus.PAUSED,
                        stop_reason=LoopStopReason.INTERRUPTED,
                        expected_revision=execution.revision,
                        events=[
                            self._control_event(
                                run,
                                "accretion/loop-iteration-interrupted",
                                EventType.LOOP_ITERATION_COMPLETED,
                                session_id=session.session_id,
                                node_key=node.key,
                                payload={
                                    "iteration_id": iteration_id,
                                    "number": number,
                                    "status": LoopIterationStatus.INTERRUPTED.value,
                                },
                            )
                        ],
                    )
                    await self.store.add_budget_spent(
                        run.run_id, turns=1, tool_calls=outcome.tool_calls
                    )
                    await self._notify(run.run_id)
                    return NodeOutcome.PAUSED, session
                return NodeOutcome.CANCELLED, session
            artifact = None
            if observe_key is not None:
                await self._node_transition(
                    run, session.session_id, observe_key, entered=True
                )
                artifact = await self.worktrees.capture_diff(
                    lease,
                    name=f"{node.key}-{execution.attempt:02}-{number:03}.patch",
                    kind="LOOP_ITERATION_GIT_DIFF",
                )
                if artifact:
                    await self.store.save_artifact(artifact)
                    cursor.last_artifact_id = artifact.artifact_id
                    cursor.last_artifact_sha256 = artifact.sha256
                await self._node_transition(
                    run, session.session_id, observe_key, entered=False
                )
            next_state, iteration = self._next_iteration_state(
                execution=execution,
                run=run,
                iteration_id=iteration_id,
                number=number,
                outcome=outcome,
                artifact_ref=artifact.artifact_id if artifact else None,
                diff_sha256=artifact.sha256 if artifact else None,
                results=[],
                deadline=deadline,
                previous=previous_iterations[-1] if previous_iterations else None,
            )
            status: LoopExecutionStatus | None = None
            stop_reason: LoopStopReason | None = None
            terminal_outcome_value: NodeOutcome | None = None
            if outcome.stop_reason is not None:
                cursor.stop_reason = outcome.stop_reason
                status = LoopExecutionStatus.REQUIRES_HUMAN
                stop_reason = outcome.stop_reason
                terminal_outcome_value = NodeOutcome.BUDGET_EXHAUSTED
            elif (
                not outcome.completed
                and next_state.provider_failure_count
                >= execution.spec.provider_failure_threshold
            ):
                cursor.last_error = outcome.error
                status = LoopExecutionStatus.FAILED
                stop_reason = LoopStopReason.PROVIDER_FAILURE
                terminal_outcome_value = NodeOutcome.FAIL
            elif next_state.budget_remaining.iterations <= 0:
                status = LoopExecutionStatus.SUCCEEDED
                stop_reason = LoopStopReason.MAX_ITERATIONS
                terminal_outcome_value = (
                    NodeOutcome.SUCCESS if outcome.completed else NodeOutcome.FAIL
                )
                if not outcome.completed:
                    status = LoopExecutionStatus.FAILED
                    stop_reason = LoopStopReason.PROVIDER_FAILURE
            checkpoint = Checkpoint(
                checkpoint_id=new_id("checkpoint"),
                run_id=run.run_id,
                kind=CheckpointKind.NODE_BOUNDARY,
                sequence=0,
                run_state=RunState.RUNNING,
                run_revision=run.revision,
                active_node_ids=[node.node_id],
                node_statuses=dict(cursor.statuses),
                loop_cursors=[
                    CheckpointLoopCursor(
                        loop_execution_id=execution.loop_execution_id,
                        iteration=next_state.iteration,
                        revision=execution.revision + 1,
                        status=status or LoopExecutionStatus.RUNNING,
                    )
                ],
                workspace_lease_id=lease.lease_id,
                workspace_revision=lease.base_revision,
                workspace_diff_sha256=artifact.sha256 if artifact else None,
            )
            execution = await self.store.append_loop_iteration(
                execution.loop_execution_id,
                iteration,
                next_state,
                status=status,
                stop_reason=stop_reason,
                expected_revision=execution.revision,
                events=[
                    self._control_event(
                        run,
                        "accretion/loop-iteration-completed",
                        EventType.LOOP_ITERATION_COMPLETED,
                        session_id=session.session_id,
                        node_key=node.key,
                        payload={
                            "iteration_id": iteration_id,
                            "number": number,
                            "status": iteration.status.value,
                        },
                    )
                ],
                checkpoint=checkpoint,
            )
            await self.store.add_budget_spent(
                run.run_id, turns=1, tool_calls=outcome.tool_calls
            )
            await self._notify(run.run_id)
            previous_iterations.append(iteration)
            if terminal_outcome_value is not None:
                return terminal_outcome_value, session

    def _select_edge(
        self,
        node: RunNode,
        outcome: NodeOutcome,
        edges: list[RunEdge],
        cursor: _GraphCursor,
        template: WorkflowTemplate,
    ) -> tuple[RunEdge | None, ErrorSummary | None]:
        budget = template.global_budget_policy
        candidates = [edge for edge in edges if edge.kind is not GraphEdgeKind.LOOP_BACK]
        for edge in candidates:
            if edge.kind is not GraphEdgeKind.RETRY or edge.guard is None:
                continue
            if outcome not in _GUARD_MATCHES.get(edge.guard, frozenset()):
                continue
            limit = (
                budget.max_replans
                if edge.guard is EdgeGuard.ON_REPLAN_AVAILABLE
                else budget.max_node_retries
            )
            if cursor.entered_via.get(edge.key, 0) < limit:
                return edge, None
        guarded = [
            edge
            for edge in candidates
            if edge.kind in {GraphEdgeKind.CONDITION, GraphEdgeKind.APPROVAL}
            and edge.guard is not None
            and outcome in _GUARD_MATCHES.get(edge.guard, frozenset())
        ]
        if len(guarded) == 1:
            return guarded[0], None
        if len(guarded) > 1:
            keys = sorted(edge.key for edge in guarded)
            return None, ErrorSummary(
                code="GRAPH_AMBIGUOUS_EDGES",
                message=(
                    f"node {node.key} produced {outcome.value} matching "
                    f"multiple edges {keys}"
                ),
            )
        if outcome is NodeOutcome.SUCCESS:
            plain = [
                edge
                for edge in candidates
                if edge.kind is GraphEdgeKind.NORMAL and edge.guard is None
            ]
            if len(plain) == 1:
                return plain[0], None
        return None, ErrorSummary(
            code="GRAPH_NO_ELIGIBLE_EDGE",
            message=f"node {node.key} produced {outcome.value} with no eligible edge",
        )

    async def _commit_graph_terminal(
        self,
        run: Run,
        lease: WorkspaceLease,
        session: SessionRef,
        cursor: _GraphCursor,
        node: RunNode,
    ) -> None:
        if cursor.arrival_guard is not None:
            state = _TERMINAL_GUARD_STATES.get(
                cursor.arrival_guard, RunState.REQUIRES_HUMAN
            )
        elif cursor.arrival_edge_kind is GraphEdgeKind.NORMAL:
            # The only guard-free success routing is an explicit NORMAL edge.
            state = RunState.SUCCEEDED
        else:
            # Unknown routing into a terminal never claims success.
            state = RunState.REQUIRES_HUMAN
        event_type = {
            RunState.SUCCEEDED: EventType.RUN_COMPLETED,
            RunState.CANCELLED: EventType.RUN_CANCELLED,
        }.get(state, EventType.RUN_FAILED)
        payload: dict[str, object] = {"node": node.key}
        if cursor.arrival_guard is not None:
            payload["guard"] = cursor.arrival_guard.value
        error = cursor.last_error if state is RunState.FAILED else None
        if error is not None:
            payload["error"] = error.model_dump(mode="json")
        await self._commit_run_terminal(
            run,
            state=state,
            event_type=event_type,
            native_type="accretion/graph-terminal",
            session_id=session.session_id,
            payload=payload,
            node_key=node.key,
            error=error,
        )
        await self.worktrees.release(lease, successful=state is RunState.SUCCEEDED)

    async def _graph_budget_stop(
        self,
        run: Run,
        lease: WorkspaceLease,
        session: SessionRef,
        cursor: _GraphCursor,
    ) -> None:
        if cursor.last_artifact_id is None:
            artifact = await self.worktrees.capture_diff(
                lease, name="final.patch", kind="FINAL_GIT_DIFF"
            )
            if artifact:
                await self.store.save_artifact(artifact)
        await self._commit_run_terminal(
            run,
            state=RunState.REQUIRES_HUMAN,
            event_type=EventType.RUN_FAILED,
            native_type="accretion/graph-budget-stop",
            session_id=session.session_id,
            payload={
                "stop_reason": cursor.stop_reason.value
                if cursor.stop_reason
                else LoopStopReason.WALL_TIME_EXCEEDED.value
            },
        )
        await self.worktrees.release(lease, successful=False)

    async def _pause_graph(self, run: Run) -> None:
        paused = await self.store.update_run(run.run_id, RunState.PAUSED)
        await self._append_pause_if_missing(paused)

    async def resolve_approval(
        self, approval_id: str, decision: ApprovalDecisionValue
    ) -> ApprovalRecord:
        record = await self.store.decide_approval(approval_id, decision)
        condition = self.approval_conditions.setdefault(approval_id, asyncio.Condition())
        async with condition:
            condition.notify_all()
        return record

    async def get_trace(self, run_id: str) -> ExecutionTrace:
        run = await self._require_run(run_id)
        graph = await self.store.get_run_graph(run_id)
        return build_execution_trace(
            run=run,
            events=await self.store.list_events(run_id),
            run_graph_id=graph.run_graph_id if graph else None,
        )

    async def _execute_loop(
        self,
        run: Run,
        task: Task,
        lease: WorkspaceLease,
        session: SessionRef,
    ) -> None:
        execution = await self._require_loop(run.run_id)
        policy = await self._require_policy(execution.acceptance_policy_ref)
        execution = await self.store.update_loop_execution(
            execution.loop_execution_id,
            execution.state,
            status=LoopExecutionStatus.RUNNING,
            expected_revision=execution.revision,
        )
        if execution.state.iteration == 0:
            await self._node_transition(run, session.session_id, "initialize", entered=True)
            await self._node_transition(run, session.session_id, "initialize", entered=False)
        deadline = execution.created_at.timestamp() + execution.spec.max_wall_time_seconds
        previous_iterations = await self.store.list_loop_iterations(execution.loop_execution_id)
        previous_results = (
            await self.store.list_verifications(
                run.run_id, previous_iterations[-1].iteration_id
            )
            if previous_iterations
            else []
        )
        directive = self._repair_directive(task, previous_iterations, previous_results)
        while True:
            if run.run_id in self.pause_requested:
                self.pause_requested.discard(run.run_id)
                await self._pause_loop(run, execution, session.session_id)
                return
            number = execution.state.iteration + 1
            if datetime.now(UTC).timestamp() >= deadline:
                await self._finish_loop(
                    run,
                    execution,
                    LoopExecutionStatus.REQUIRES_HUMAN,
                    LoopStopReason.WALL_TIME_EXCEEDED,
                    RunState.REQUIRES_HUMAN,
                    session.session_id,
                    lease,
                )
                return
            iteration_id = new_id("iteration")
            await self._append(
                self._control_event(
                    run,
                    "accretion/loop-iteration-started",
                    EventType.LOOP_ITERATION_STARTED,
                    session_id=session.session_id,
                    node_key="evaluate",
                    payload={"iteration_id": iteration_id, "number": number},
                )
            )
            await self._node_transition(run, session.session_id, "act", entered=True)
            envelope = self._iteration_envelope(
                task.envelope,
                directive,
                deadline,
                execution.state.budget_remaining,
            )
            outcome = await self._runtime_call(
                run,
                session,
                envelope,
                runtime_call_id=new_id("runtime_call"),
                deadline=deadline,
                node_key="act",
                iteration_number=number,
                directive=directive,
            )
            session = outcome.session
            await self._node_transition(
                run,
                session.session_id,
                "act",
                entered=False,
                status="SUCCEEDED" if outcome.completed else "FAILED",
            )
            if outcome.stop_reason is None and run.run_id in self.pause_requested:
                self.pause_requested.discard(run.run_id)
                await self._pause_interrupted_iteration(
                    run=run,
                    execution=execution,
                    session_id=session.session_id,
                    iteration_id=iteration_id,
                    number=number,
                    outcome=outcome,
                    deadline=deadline,
                    previous=previous_iterations[-1] if previous_iterations else None,
                )
                return
            if outcome.cancelled and outcome.stop_reason is None:
                await self._cancel_execution(run.run_id)
                return

            await self._node_transition(run, session.session_id, "observe", entered=True)
            artifact = await self.worktrees.capture_diff(
                lease,
                name=f"iteration-{number:03}.patch",
                kind="LOOP_ITERATION_GIT_DIFF",
            )
            if artifact:
                await self.store.save_artifact(artifact)
            await self._node_transition(run, session.session_id, "observe", entered=False)

            results: list[VerificationResult] = []
            if outcome.completed:
                await self._node_transition(run, session.session_id, "verify", entered=True)
                results = await self._verify_candidate(
                    run=run,
                    task=task,
                    lease=lease,
                    session_id=session.session_id,
                    policy=policy,
                    artifact_ref=artifact.artifact_id if artifact else None,
                    diff_sha256=artifact.sha256 if artifact else None,
                    iteration_id=iteration_id,
                    persist=False,
                    emit_result=False,
                )
            acceptance = (
                evaluate_acceptance_policy(
                    policy, results, risk=task.envelope.risk_level
                ).status
                if outcome.completed
                else VerificationStatus.FAIL
            )
            next_state, iteration = self._next_iteration_state(
                execution=execution,
                run=run,
                iteration_id=iteration_id,
                number=number,
                outcome=outcome,
                artifact_ref=artifact.artifact_id if artifact else None,
                diff_sha256=artifact.sha256 if artifact else None,
                results=results,
                deadline=deadline,
                previous=previous_iterations[-1] if previous_iterations else None,
            )
            projected = execution.model_copy(update={"state": next_state})
            terminal = terminal_outcome(projected, acceptance)
            transition_events = [
                self._control_event(
                    run,
                    "accretion/verification-result",
                    EventType.VERIFICATION_RESULT,
                    session_id=session.session_id,
                    node_key="verify",
                    payload={
                        "iteration_id": iteration_id,
                        "acceptance": acceptance.value,
                        "verification_ids": iteration.verification_refs,
                    },
                ),
                self._control_event(
                    run,
                    "accretion/loop-iteration-completed",
                    EventType.LOOP_ITERATION_COMPLETED,
                    session_id=session.session_id,
                    node_key="evaluate",
                    payload={
                        "iteration_id": iteration_id,
                        "number": number,
                        "acceptance": acceptance.value,
                    },
                ),
            ]
            status = terminal[0] if terminal else LoopExecutionStatus.RUNNING
            stop_reason = terminal[1] if terminal else None
            iteration_checkpoint = Checkpoint(
                checkpoint_id=new_id("checkpoint"),
                run_id=run.run_id,
                kind=CheckpointKind.NODE_BOUNDARY,
                sequence=0,
                run_state=RunState.RUNNING,
                run_revision=run.revision,
                active_node_ids=[self._node_id(run.run_id, "evaluate")],
                loop_cursors=[
                    CheckpointLoopCursor(
                        loop_execution_id=execution.loop_execution_id,
                        iteration=next_state.iteration,
                        revision=execution.revision + 1,
                        status=status,
                    )
                ],
                budget_remaining=next_state.budget_remaining,
                workspace_lease_id=lease.lease_id,
                workspace_revision=lease.base_revision,
                workspace_diff_sha256=artifact.sha256 if artifact else None,
            )
            execution = await self.store.append_loop_iteration(
                execution.loop_execution_id,
                iteration,
                next_state,
                status=status,
                stop_reason=stop_reason,
                expected_revision=execution.revision,
                verifications=results,
                events=transition_events,
                checkpoint=iteration_checkpoint,
            )
            await self._notify(run.run_id)
            previous_iterations.append(iteration)
            if outcome.completed:
                await self._node_transition(
                    run,
                    session.session_id,
                    "verify",
                    entered=False,
                    status={
                        VerificationStatus.PASS: "SUCCEEDED",
                        VerificationStatus.FAIL: "FAILED",
                        VerificationStatus.INCONCLUSIVE: "WAITING",
                    }[acceptance],
                )
            if run.run_id in self.pause_requested and terminal is None:
                self.pause_requested.discard(run.run_id)
                execution = await self.store.update_loop_execution(
                    execution.loop_execution_id,
                    execution.state,
                    status=LoopExecutionStatus.PAUSED,
                    stop_reason=LoopStopReason.INTERRUPTED,
                    expected_revision=execution.revision,
                )
                paused = await self.store.update_run(run.run_id, RunState.PAUSED)
                await self._append_pause_if_missing(paused)
                return
            if terminal:
                await self._finish_loop(
                    run,
                    execution,
                    terminal[0],
                    terminal[1],
                    terminal[2],
                    session.session_id,
                    lease,
                    loop_already_updated=True,
                )
                return
            directive = self._repair_directive(task, previous_iterations, results)

    async def _runtime_call(
        self,
        run: Run,
        session: SessionRef,
        envelope: TaskEnvelope,
        *,
        runtime_call_id: str,
        deadline: float,
        node_key: str,
        iteration_number: int = 1,
        directive: IterationDirective | None = None,
    ) -> RuntimeCallOutcome:
        runtime = self._runtime_for(session)
        remaining = max(1, int(deadline - datetime.now(UTC).timestamp()))
        request_envelope = envelope.model_copy(
            update={
                "budgets": envelope.budgets.model_copy(
                    update={"wall_time_seconds": remaining}
                )
            }
        )
        request = RuntimeExecutionRequest(
            runtime_call_id=runtime_call_id,
            run_id=run.run_id,
            task=request_envelope,
            iteration_number=iteration_number,
            directive=directive
            or IterationDirective(
                kind=IterationDirectiveKind.INITIAL,
                objective=envelope.objective,
            ),
            deadline=datetime.fromtimestamp(deadline, UTC),
            max_turns=request_envelope.budgets.max_turns,
            max_tool_calls=request_envelope.budgets.max_tool_calls,
        )
        ref = await runtime.submit(session, request)
        if ref.runtime_call_id is None:
            ref = ref.model_copy(update={"runtime_call_id": runtime_call_id})
        if ref.native_run_id and ref.native_run_id != session.native_session_id:
            session = session.model_copy(update={"native_session_id": ref.native_run_id})
            await self.store.save_session(session)
        self.active_refs[run.run_id] = ActiveRuntimeRef(session=session, ref=ref)
        completed = False
        cancelled = False
        tool_ids: set[str] = set()
        error: ErrorSummary | None = None
        stop_reason: LoopStopReason | None = None
        interrupted_for_budget = False

        async def consume_events() -> None:
            nonlocal cancelled, completed, error, interrupted_for_budget, stop_reason
            async for event in runtime.events(ref):
                payload = dict(event.payload)
                payload.setdefault("runtime_call_id", ref.runtime_call_id or runtime_call_id)
                stored = await self._append(
                    event.model_copy(
                        update={
                            "node_id": self._node_id(run.run_id, node_key),
                            "payload": payload,
                        }
                    )
                )
                if stored.normalized_type in {
                    EventType.TOOL_REQUESTED,
                    EventType.TOOL_STARTED,
                }:
                    tool_ids.add(self._tool_call_key(stored))
                    if len(tool_ids) > request.max_tool_calls and not interrupted_for_budget:
                        interrupted_for_budget = True
                        stop_reason = LoopStopReason.MAX_TOOL_CALLS
                        error = ErrorSummary(
                            code="MAX_TOOL_CALLS",
                            message="runtime call reached the remaining tool-call ceiling",
                        )
                        await runtime.interrupt(ref)
                elif stored.normalized_type is EventType.RUNTIME_CALL_COMPLETED:
                    completed = True
                elif stored.normalized_type is EventType.RUNTIME_CALL_FAILED:
                    error = ErrorSummary(
                        code="RUNTIME_CALL_FAILED",
                        message=str(stored.payload.get("error", "provider call failed"))[:2000],
                    )
                elif stored.normalized_type is EventType.RUNTIME_CALL_CANCELLED:
                    cancelled = True

        try:
            timeout_seconds = max(0.001, deadline - datetime.now(UTC).timestamp())
            async with asyncio.timeout(timeout_seconds):
                await consume_events()
        except TimeoutError:
            stop_reason = LoopStopReason.WALL_TIME_EXCEEDED
            error = ErrorSummary(
                code="WALL_TIME_EXCEEDED",
                message="runtime call reached the persisted wall-time deadline",
            )
            await runtime.interrupt(ref)
            try:
                async with asyncio.timeout(5):
                    await consume_events()
            except TimeoutError:
                cancelled = True
        if not completed and not cancelled and error is None:
            error = ErrorSummary(
                code="RUNTIME_CALL_EOF",
                message="runtime event stream closed without a terminal call event",
            )
        if stop_reason is not None:
            completed = False
        return RuntimeCallOutcome(
            session=session,
            ref=ref,
            completed=completed,
            cancelled=cancelled,
            tool_calls=min(len(tool_ids), request.max_tool_calls),
            error=error,
            stop_reason=stop_reason,
        )

    @staticmethod
    def _tool_call_key(event: AgentEvent) -> str:
        for key in ("tool_call_id", "native_request_id", "call_id", "id"):
            value = event.payload.get(key)
            if value:
                return str(value)
        extension = event.payload.get("provider_extension")
        if isinstance(extension, dict):
            for key in ("tool_call_id", "native_request_id", "call_id", "id"):
                value = extension.get(key)
                if value:
                    return str(value)
            item = extension.get("item")
            if isinstance(item, dict):
                for key in ("tool_call_id", "call_id", "id"):
                    value = item.get(key)
                    if value:
                        return str(value)
        return event.event_id

    async def _verify_candidate(
        self,
        *,
        run: Run,
        task: Task,
        lease: WorkspaceLease,
        session_id: str,
        policy: AcceptancePolicy,
        artifact_ref: str | None,
        diff_sha256: str | None,
        iteration_id: str | None = None,
        persist: bool = True,
        emit_result: bool = True,
        trajectory_events: list[AgentEvent] | None = None,
    ) -> list[VerificationResult]:
        events = (
            await self.store.list_events(run.run_id)
            if trajectory_events is None
            else trajectory_events
        )
        context = VerificationContext(
            task_id=task.envelope.task_id,
            project_id=task.envelope.project_id,
            workspace=lease.path,
            allowed_capabilities=task.envelope.allowed_capabilities,
            denied_capabilities=task.envelope.denied_capabilities,
            observed_capabilities=self._observed_capabilities(events),
            unresolved_approval_ids=self._unresolved_approvals(events),
            trajectory_events=[event.model_dump(mode="json") for event in events],
        )
        results: list[VerificationResult] = []
        for verifier_id in policy.required_verifiers:
            verifier = self.verifiers.get(verifier_id)
            started_at = time.monotonic()
            target = self._verification_target(
                verifier_id=verifier_id,
                run=run,
                task=task,
                iteration_id=iteration_id,
                artifact_ref=artifact_ref,
                diff_sha256=diff_sha256,
            )
            await self._append(
                self._control_event(
                    run,
                    "accretion/verification-started",
                    EventType.VERIFICATION_STARTED,
                    session_id=session_id,
                    node_key="verify",
                    payload={
                        "verifier_id": verifier_id,
                        "iteration_id": iteration_id,
                    },
                )
            )
            try:
                result = await verifier.verify(target, context)
            except Exception as exc:
                result = verification_result(
                    verifier_id=verifier_id,
                    verifier_version=getattr(verifier, "verifier_version", "unknown"),
                    target=target,
                    status=VerificationStatus.INCONCLUSIVE,
                    started_at=started_at,
                    findings=[
                        finding(
                            "VERIFIER_ERROR",
                            FindingSeverity.ERROR,
                            f"Verifier failed safely: {type(exc).__name__}",
                        )
                    ],
                )
            results.append(result)
            if persist:
                await self.store.save_verification(result)
            if emit_result:
                await self._append(
                    self._control_event(
                        run,
                        "accretion/verification-result",
                        EventType.VERIFICATION_RESULT,
                        session_id=session_id,
                        node_key="verify",
                        payload={
                            "verification_id": result.verification_id,
                            "verifier_id": result.verifier_id,
                            "status": result.status.value,
                        },
                    )
                )
        return results

    def _verification_target(
        self,
        *,
        verifier_id: str,
        run: Run,
        task: Task,
        iteration_id: str | None,
        artifact_ref: str | None,
        diff_sha256: str | None,
    ) -> VerificationTarget:
        if verifier_id == "output-contract":
            kind = VerificationTargetKind.OUTPUT_CONTRACT
        elif verifier_id == "git-diff":
            kind = VerificationTargetKind.GIT_DIFF
        elif verifier_id == "trajectory-policy":
            kind = VerificationTargetKind.TRAJECTORY_POLICY
        elif verifier_id in RESEARCH_VERIFIER_IDS:
            # Without this branch a research verifier id falls through to the
            # COMMAND_SUITE default below, and every research verifier then rejects
            # its own target as a kind mismatch: an INCONCLUSIVE that reads as a
            # configuration fault and is really a missing elif. ``evidence_refs`` is
            # left empty on purpose here — a run-scoped acceptance policy judges
            # every record the run gathered.
            kind = VerificationTargetKind.EXTERNAL_EVIDENCE
        else:
            kind = VerificationTargetKind.COMMAND_SUITE
        return VerificationTarget(
            target_ref=artifact_ref or iteration_id or run.run_id,
            kind=kind,
            run_id=run.run_id,
            iteration_id=iteration_id,
            artifact_refs=[artifact_ref] if artifact_ref else [],
            required_outputs=task.envelope.required_outputs,
            require_git_changes=task.envelope.task_type
            in {TaskType.IMPLEMENT, TaskType.EXPERIMENT},
            expected_diff_sha256=diff_sha256 if kind is VerificationTargetKind.GIT_DIFF else None,
            command_suite_refs=[verifier_id]
            if kind is VerificationTargetKind.COMMAND_SUITE
            else [],
        )

    def _next_iteration_state(
        self,
        *,
        execution: LoopExecution,
        run: Run,
        iteration_id: str,
        number: int,
        outcome: RuntimeCallOutcome,
        artifact_ref: str | None,
        diff_sha256: str | None,
        results: list[VerificationResult],
        deadline: float,
        previous: LoopIteration | None,
    ) -> tuple[LoopState, LoopIteration]:
        output_fingerprint = diff_sha256 or "no-workspace-diff"
        finding_fingerprints = sorted(
            item.fingerprint or item.code for result in results for item in result.findings
        )
        finding_signature = hashlib.sha256(
            json.dumps(finding_fingerprints, separators=(",", ":")).encode()
        ).hexdigest()
        candidate_completed = outcome.completed
        previous_completed = (
            previous is not None and previous.status is LoopIterationStatus.COMPLETED
        )
        same_output = (
            candidate_completed
            and previous_completed
            and previous is not None
            and previous.output_fingerprint == output_fingerprint
        )
        same_failure = (
            candidate_completed
            and previous_completed
            and previous is not None
            and previous.finding_signature == finding_signature
        )
        remaining = execution.state.budget_remaining
        next_state = LoopState(
            iteration=number,
            latest_observation_ref=artifact_ref or iteration_id,
            accumulated_evidence_refs=[
                *execution.state.accumulated_evidence_refs,
                *([artifact_ref] if artifact_ref else []),
                *(result.verification_id for result in results),
            ],
            repeated_failure_signature=finding_signature,
            consecutive_no_progress=(
                execution.state.consecutive_no_progress + 1
                if same_output
                else 0
                if candidate_completed
                else execution.state.consecutive_no_progress
            ),
            repeated_failure_count=(
                execution.state.repeated_failure_count + 1
                if same_failure
                else 1
                if candidate_completed
                else execution.state.repeated_failure_count
            ),
            provider_failure_count=(
                0
                if candidate_completed
                else execution.state.provider_failure_count + 1
                if outcome.stop_reason is None
                else execution.state.provider_failure_count
            ),
            budget_remaining=LoopBudgetRemaining(
                wall_time_seconds=max(0, int(deadline - datetime.now(UTC).timestamp())),
                tool_calls=max(0, remaining.tool_calls - outcome.tool_calls),
                turns=max(0, remaining.turns - 1),
                iterations=max(0, remaining.iterations - 1),
            ),
        )
        iteration = LoopIteration(
            iteration_id=iteration_id,
            loop_execution_id=execution.loop_execution_id,
            run_id=run.run_id,
            number=number,
            status=(
                LoopIterationStatus.COMPLETED
                if outcome.completed
                else LoopIterationStatus.FAILED
            ),
            runtime_call_ref=outcome.ref.runtime_call_id,
            observation_ref=artifact_ref or iteration_id,
            diff_artifact_ref=artifact_ref,
            artifact_refs=[artifact_ref] if artifact_ref else [],
            verification_refs=[result.verification_id for result in results],
            evidence_refs=[ref for result in results for ref in result.evidence_refs],
            diff_sha256=diff_sha256,
            output_fingerprint=output_fingerprint,
            finding_signature=finding_signature,
            tool_calls=outcome.tool_calls,
            turns=1,
            error=outcome.error,
        )
        return next_state, iteration

    def _repair_directive(
        self,
        task: Task,
        previous: list[LoopIteration],
        results: list[VerificationResult],
    ) -> IterationDirective:
        findings: list[Finding] = [item for result in results for item in result.findings]
        return IterationDirective(
            kind=IterationDirectiveKind.REPAIR if previous else IterationDirectiveKind.INITIAL,
            objective=task.envelope.objective,
            findings=findings,
            evidence_refs=[ref for result in results for ref in result.evidence_refs],
            previous_iteration_id=previous[-1].iteration_id if previous else None,
        )

    @staticmethod
    def _iteration_envelope(
        envelope: TaskEnvelope,
        directive: IterationDirective,
        deadline: float,
        remaining_budget: LoopBudgetRemaining,
    ) -> TaskEnvelope:
        remaining = max(1, int(deadline - datetime.now(UTC).timestamp()))
        budget_update = {
            "wall_time_seconds": remaining,
            "max_turns": max(1, remaining_budget.turns),
            "max_tool_calls": max(1, remaining_budget.tool_calls),
            "max_loop_iterations": max(1, remaining_budget.iterations),
        }
        if directive.kind is IterationDirectiveKind.INITIAL:
            return envelope.model_copy(
                update={
                    "budgets": envelope.budgets.model_copy(
                        update=budget_update
                    )
                }
            )
        feedback = [
            {"code": item.code, "severity": item.severity.value, "message": item.message}
            for item in directive.findings
        ]
        objective = (
            f"{envelope.objective}\n\nRepair the current workspace using this verifier feedback. "
            f"Do not discard valid prior work. Structured findings: "
            f"{json.dumps(feedback, ensure_ascii=False)}"
        )
        return envelope.model_copy(
            update={
                "objective": objective,
                "budgets": envelope.budgets.model_copy(
                    update=budget_update
                ),
            }
        )

    async def _finish_loop(
        self,
        run: Run,
        execution: LoopExecution,
        loop_status: LoopExecutionStatus,
        stop_reason: LoopStopReason,
        run_state: RunState,
        session_id: str,
        lease: WorkspaceLease,
        *,
        loop_already_updated: bool = False,
    ) -> None:
        if not loop_already_updated:
            execution = await self.store.update_loop_execution(
                execution.loop_execution_id,
                execution.state,
                status=loop_status,
                stop_reason=stop_reason,
                expected_revision=execution.revision,
            )
        terminal = {
            RunState.SUCCEEDED: EventType.RUN_COMPLETED,
            RunState.CANCELLED: EventType.RUN_CANCELLED,
        }.get(run_state, EventType.RUN_FAILED)
        await self._commit_run_terminal(
            run,
            state=run_state,
            event_type=terminal,
            native_type="accretion/loop-terminal",
            session_id=session_id,
            node_key="complete",
            payload={
                "loop_execution_id": execution.loop_execution_id,
                "stop_reason": stop_reason.value,
            },
            final_node_status=(
                "SUCCEEDED"
                if run_state is RunState.SUCCEEDED
                else "CANCELLED"
                if run_state is RunState.CANCELLED
                else "WAITING"
                if run_state is RunState.REQUIRES_HUMAN
                else "FAILED"
            ),
        )
        await self.worktrees.release(lease, successful=run_state is RunState.SUCCEEDED)

    async def _pause_loop(
        self, run: Run, execution: LoopExecution, session_id: str
    ) -> None:
        closed = await self._close_dangling_iteration(
            run=run,
            execution=execution,
            iteration_status=LoopIterationStatus.INTERRUPTED,
            loop_status=LoopExecutionStatus.PAUSED,
            stop_reason=LoopStopReason.INTERRUPTED,
            session_id=session_id,
        )
        if closed is None:
            await self.store.update_loop_execution(
                execution.loop_execution_id,
                execution.state,
                status=LoopExecutionStatus.PAUSED,
                stop_reason=LoopStopReason.INTERRUPTED,
                expected_revision=execution.revision,
            )
        await self.store.update_run(run.run_id, RunState.PAUSED)
        await self._append(
            self._control_event(
                run,
                "accretion/run-paused",
                EventType.RUN_PAUSED,
                session_id=session_id,
            )
        )

    async def _close_dangling_iteration(
        self,
        *,
        run: Run,
        execution: LoopExecution,
        iteration_status: LoopIterationStatus,
        loop_status: LoopExecutionStatus,
        stop_reason: LoopStopReason,
        session_id: str | None = None,
    ) -> LoopExecution | None:
        """Close a started-but-uncommitted attempt as one durable transition."""

        expected_number = execution.state.iteration + 1
        events = await self.store.list_events(run.run_id)
        started = next(
            (
                event
                for event in reversed(events)
                if event.normalized_type is EventType.LOOP_ITERATION_STARTED
                and event.payload.get("number") == expected_number
            ),
            None,
        )
        if started is None:
            return None
        iteration_id = str(started.payload.get("iteration_id") or new_id("iteration"))
        persisted = await self.store.list_loop_iterations(execution.loop_execution_id)
        if any(item.iteration_id == iteration_id for item in persisted):
            return await self.store.get_loop_execution(execution.loop_execution_id)

        attempt_events = [event for event in events if event.sequence >= started.sequence]
        tool_ids = {
            self._tool_call_key(event)
            for event in attempt_events
            if event.normalized_type in {EventType.TOOL_REQUESTED, EventType.TOOL_STARTED}
        }
        runtime_call_ref = next(
            (
                str(event.payload["runtime_call_id"])
                for event in attempt_events
                if event.payload.get("runtime_call_id")
            ),
            None,
        )
        artifact = next(
            (
                item
                for item in reversed(await self.store.list_artifacts(run.run_id))
                if item.kind == "LOOP_ITERATION_GIT_DIFF"
                # P2 whole-run loops name captures "iteration-NNN.patch";
                # node-scoped regions name them "{key}-AA-NNN.patch".
                and item.path.name.endswith(f"-{expected_number:03}.patch")
            ),
            None,
        )
        used_tools = min(len(tool_ids), execution.state.budget_remaining.tool_calls)
        deadline = execution.created_at.timestamp() + execution.spec.max_wall_time_seconds
        remaining = execution.state.budget_remaining
        next_state = LoopState(
            iteration=expected_number,
            latest_observation_ref=artifact.artifact_id if artifact else iteration_id,
            accumulated_evidence_refs=[
                *execution.state.accumulated_evidence_refs,
                *([artifact.artifact_id] if artifact else []),
            ],
            progress_score=execution.state.progress_score,
            repeated_failure_signature=execution.state.repeated_failure_signature,
            consecutive_no_progress=execution.state.consecutive_no_progress,
            repeated_failure_count=execution.state.repeated_failure_count,
            provider_failure_count=execution.state.provider_failure_count,
            budget_remaining=LoopBudgetRemaining(
                wall_time_seconds=max(0, int(deadline - datetime.now(UTC).timestamp())),
                tool_calls=max(0, remaining.tool_calls - used_tools),
                turns=max(0, remaining.turns - 1),
                iterations=max(0, remaining.iterations - 1),
            ),
        )
        error_code = (
            "OPERATOR_CANCELLED"
            if iteration_status is LoopIterationStatus.CANCELLED
            else "ITERATION_INTERRUPTED"
        )
        iteration = LoopIteration(
            iteration_id=iteration_id,
            loop_execution_id=execution.loop_execution_id,
            run_id=run.run_id,
            number=expected_number,
            status=iteration_status,
            runtime_call_ref=runtime_call_ref,
            observation_ref=artifact.artifact_id if artifact else iteration_id,
            diff_artifact_ref=artifact.artifact_id if artifact else None,
            artifact_refs=[artifact.artifact_id] if artifact else [],
            diff_sha256=artifact.sha256 if artifact else None,
            output_fingerprint=artifact.sha256 if artifact else "no-workspace-diff",
            tool_calls=used_tools,
            turns=1,
            started_at=started.timestamp,
            error=ErrorSummary(code=error_code, message=stop_reason.value),
        )
        try:
            updated = await self.store.append_loop_iteration(
                execution.loop_execution_id,
                iteration,
                next_state,
                status=loop_status,
                stop_reason=stop_reason,
                expected_revision=execution.revision,
                events=[
                    self._control_event(
                        run,
                        f"accretion/loop-iteration-{iteration_status.value.lower()}",
                        EventType.LOOP_ITERATION_COMPLETED,
                        session_id=session_id or started.session_id,
                        node_key="evaluate",
                        payload={
                            "iteration_id": iteration_id,
                            "number": expected_number,
                            "status": iteration_status.value,
                        },
                    )
                ],
            )
        except ValueError:
            current = await self.store.get_loop_execution(execution.loop_execution_id)
            if current is not None and current.state.iteration >= expected_number:
                return current
            raise
        await self._notify(run.run_id)
        return updated

    async def _pause_interrupted_iteration(
        self,
        *,
        run: Run,
        execution: LoopExecution,
        session_id: str,
        iteration_id: str,
        number: int,
        outcome: RuntimeCallOutcome,
        deadline: float,
        previous: LoopIteration | None,
    ) -> None:
        interrupted_outcome = RuntimeCallOutcome(
            session=outcome.session,
            ref=outcome.ref,
            completed=False,
            cancelled=True,
            tool_calls=outcome.tool_calls,
            error=outcome.error,
            stop_reason=LoopStopReason.INTERRUPTED,
        )
        next_state, iteration = self._next_iteration_state(
            execution=execution,
            run=run,
            iteration_id=iteration_id,
            number=number,
            outcome=interrupted_outcome,
            artifact_ref=None,
            diff_sha256=None,
            results=[],
            deadline=deadline,
            previous=previous,
        )
        iteration = iteration.model_copy(
            update={"status": LoopIterationStatus.INTERRUPTED}
        )
        await self.store.append_loop_iteration(
            execution.loop_execution_id,
            iteration,
            next_state,
            status=LoopExecutionStatus.PAUSED,
            stop_reason=LoopStopReason.INTERRUPTED,
            expected_revision=execution.revision,
            events=[
                self._control_event(
                    run,
                    "accretion/loop-iteration-interrupted",
                    EventType.LOOP_ITERATION_COMPLETED,
                    session_id=session_id,
                    node_key="evaluate",
                    payload={
                        "iteration_id": iteration_id,
                        "number": number,
                        "status": LoopIterationStatus.INTERRUPTED.value,
                    },
                )
            ],
        )
        await self._notify(run.run_id)
        await self.store.update_run(run.run_id, RunState.PAUSED)
        await self._append(
            self._control_event(
                run,
                "accretion/run-paused",
                EventType.RUN_PAUSED,
                session_id=session_id,
                payload={"reason": LoopStopReason.INTERRUPTED.value},
            )
        )

    async def _cancel_execution(self, run_id: str) -> None:
        run = await self.store.get_run(run_id)
        if run is None or run.state in TERMINAL_RUN_STATES:
            return
        execution = await self.store.get_loop_execution_for_run(run_id)
        terminal_loop_states = {
            LoopExecutionStatus.SUCCEEDED: (
                RunState.SUCCEEDED,
                EventType.RUN_COMPLETED,
            ),
            LoopExecutionStatus.FAILED: (RunState.FAILED, EventType.RUN_FAILED),
            LoopExecutionStatus.CANCELLED: (
                RunState.CANCELLED,
                EventType.RUN_CANCELLED,
            ),
            LoopExecutionStatus.REQUIRES_HUMAN: (
                RunState.REQUIRES_HUMAN,
                EventType.RUN_FAILED,
            ),
        }
        # A terminal loop status implies the run outcome only for whole-run
        # LOOP mode; a graph node's bounded region can legitimately finish
        # SUCCEEDED mid-graph before verification, and must never resolve the
        # run (V01 invariant: completion never accepts its own output).
        if (
            run.execution_mode is ExecutionMode.LOOP
            and execution is not None
            and execution.status in terminal_loop_states
        ):
            state, event_type = terminal_loop_states[execution.status]
            await self._commit_run_terminal(
                run,
                state=state,
                event_type=event_type,
                native_type="accretion/recovered-loop-terminal",
                payload={
                    "stop_reason": execution.stop_reason.value
                    if execution.stop_reason
                    else "UNKNOWN"
                },
                node_key="complete",
            )
            return
        if execution is not None and execution.status not in terminal_loop_states:
            closed = await self._close_dangling_iteration(
                run=run,
                execution=execution,
                iteration_status=LoopIterationStatus.CANCELLED,
                loop_status=LoopExecutionStatus.CANCELLED,
                stop_reason=LoopStopReason.OPERATOR_CANCELLED,
            )
            if closed is None:
                await self.store.update_loop_execution(
                    execution.loop_execution_id,
                    execution.state,
                    status=LoopExecutionStatus.CANCELLED,
                    stop_reason=LoopStopReason.OPERATOR_CANCELLED,
                    expected_revision=execution.revision,
                )
        await self._commit_run_terminal(
            run,
            state=RunState.CANCELLED,
            event_type=EventType.RUN_CANCELLED,
            native_type="accretion/run-cancelled",
        )

    async def _fail_execution(
        self, run_id: str, exc: Exception, session_id: str | None = None
    ) -> None:
        run = await self.store.get_run(run_id)
        if run is None or run.state in TERMINAL_RUN_STATES:
            return
        error = ErrorSummary(code="RUN_EXECUTION_FAILED", message=str(exc)[:2000])
        execution = await self.store.get_loop_execution_for_run(run_id)
        # See _cancel_execution: a terminal region execution never resolves a
        # graph-mode run.
        if (
            run.execution_mode is ExecutionMode.LOOP
            and execution is not None
        ) and execution.status in {
            LoopExecutionStatus.SUCCEEDED,
            LoopExecutionStatus.FAILED,
            LoopExecutionStatus.CANCELLED,
            LoopExecutionStatus.REQUIRES_HUMAN,
        }:
            state, event_type = {
                LoopExecutionStatus.SUCCEEDED: (
                    RunState.SUCCEEDED,
                    EventType.RUN_COMPLETED,
                ),
                LoopExecutionStatus.FAILED: (RunState.FAILED, EventType.RUN_FAILED),
                LoopExecutionStatus.CANCELLED: (
                    RunState.CANCELLED,
                    EventType.RUN_CANCELLED,
                ),
                LoopExecutionStatus.REQUIRES_HUMAN: (
                    RunState.REQUIRES_HUMAN,
                    EventType.RUN_FAILED,
                ),
            }[execution.status]
            await self._commit_run_terminal(
                run,
                state=state,
                event_type=event_type,
                native_type="accretion/recovered-loop-terminal",
                payload={
                    "stop_reason": execution.stop_reason.value
                    if execution.stop_reason
                    else "UNKNOWN"
                },
                node_key="complete",
            )
            return
        if execution and execution.status not in {
            LoopExecutionStatus.SUCCEEDED,
            LoopExecutionStatus.FAILED,
            LoopExecutionStatus.CANCELLED,
            LoopExecutionStatus.REQUIRES_HUMAN,
        }:
            await self.store.update_loop_execution(
                execution.loop_execution_id,
                execution.state,
                status=LoopExecutionStatus.FAILED,
                stop_reason=LoopStopReason.PROVIDER_FAILURE,
                expected_revision=execution.revision,
            )
        await self._commit_run_terminal(
            run,
            state=RunState.FAILED,
            event_type=EventType.RUN_FAILED,
            native_type="accretion/run-error",
            session_id=session_id or run.session_id or "ses_pending",
            payload={"error": error.model_dump(mode="json")},
            error=error,
        )

    async def _node_transition(
        self,
        run: Run,
        session_id: str,
        key: str,
        *,
        entered: bool,
        status: str = "SUCCEEDED",
        entered_via: str | None = None,
    ) -> None:
        payload: dict[str, object] = {"status": "RUNNING" if entered else status}
        if entered and entered_via:
            payload["entered_via"] = entered_via
        await self._append(
            self._control_event(
                run,
                f"accretion/node-{'entered' if entered else 'exited'}",
                EventType.NODE_ENTERED if entered else EventType.NODE_EXITED,
                session_id=session_id,
                node_key=key,
                payload=payload,
            )
        )

    @staticmethod
    def _observed_capabilities(events: list[AgentEvent]) -> list[str]:
        return sorted(
            {
                str(event.payload.get("capability_id"))
                for event in events
                if event.payload.get("capability_id")
            }
        )

    @staticmethod
    def _unresolved_approvals(events: list[AgentEvent]) -> list[str]:
        required = {
            str(event.payload.get("approval_id"))
            for event in events
            if event.normalized_type is EventType.APPROVAL_REQUIRED
            and event.payload.get("approval_id")
        }
        resolved = {
            str(event.payload.get("approval_id"))
            for event in events
            if event.normalized_type is EventType.APPROVAL_RESOLVED
            and event.payload.get("approval_id")
        }
        return sorted(required - resolved)

    async def get_loop(self, run_id: str) -> LoopExecution:
        await self._require_run(run_id)
        return await self._require_loop(run_id)

    async def get_graph(self, run_id: str) -> GraphProjection:
        run = await self._require_run(run_id)
        task = await self._require_task(run.task_id)
        if run.execution_mode is ExecutionMode.LOOP:
            execution = await self._require_loop(run_id)
            return build_loop_projection(
                run=run,
                task=task,
                execution=execution,
                events=await self.store.list_events(run_id),
                verifications=await self.store.list_verifications(run_id),
            )
        graph = await self.store.get_run_graph(run_id)
        if graph is None:
            raise ProjectionUnavailableError(run)
        executions = {
            execution.loop_execution_id: execution
            for execution in await self.store.list_loop_executions_for_run(run_id)
        }
        return build_graph_projection(
            run=run,
            task=task,
            run_graph=graph,
            events=await self.store.list_events(run_id),
            loop_executions=executions,
            verifications=await self.store.list_verifications(run_id),
            artifacts=await self.store.list_artifacts(run_id),
        )

    async def wait_for_events(
        self, run_id: str, after: int, timeout_seconds: float = 15.0
    ) -> None:
        condition = self.event_conditions.setdefault(run_id, asyncio.Condition())
        run = await self.store.get_run(run_id)
        if run is None or run.last_sequence > after or run.state in TERMINAL_RUN_STATES:
            return
        async with condition:
            try:
                await asyncio.wait_for(condition.wait(), timeout_seconds)
            except TimeoutError:
                return

    async def pause(self, run_id: str) -> Run:
        run = await self._require_run(run_id)
        if run.state in TERMINAL_RUN_STATES or run.state is RunState.PAUSED:
            return run
        self.pause_requested.add(run_id)
        active = self.active_refs.get(run_id)
        if active:
            await self._runtime_for(active.session).interrupt(active.ref)
        elif run.execution_mode is ExecutionMode.LOOP:
            execution = await self._require_loop(run_id)
            await self.store.update_loop_execution(
                execution.loop_execution_id,
                execution.state,
                status=LoopExecutionStatus.PAUSED,
                stop_reason=LoopStopReason.INTERRUPTED,
                expected_revision=execution.revision,
            )
            return await self.store.update_run(run_id, RunState.PAUSED)
        return await self.store.update_run(run_id, RunState.PAUSED)

    async def resume(self, run_id: str) -> Run:
        run = await self._require_run(run_id)
        if run.state in TERMINAL_RUN_STATES:
            return run
        if run.state is not RunState.PAUSED:
            active = self.active_refs.get(run_id)
            if not active:
                return run
            await self._runtime_for(active.session).resume(active.ref)
            return await self.store.update_run(run_id, RunState.RUNNING)
        if run_id in self.background and not self.background[run_id].done():
            return run
        self.pause_requested.discard(run_id)
        graph = await self.store.get_run_graph(run_id)
        run = await self.store.update_run(run_id, RunState.RUNNING)
        if run.execution_mode is ExecutionMode.LOOP:
            resume_operation = self._resume_loop(run_id)
        elif graph is not None:
            resume_operation = self._resume_graph(run_id)
        else:
            resume_operation = self._resume_direct(run_id)
        self.background[run_id] = asyncio.create_task(resume_operation)
        return run

    async def _resume_graph(self, run_id: str) -> None:
        run = await self._require_run(run_id)
        task = await self._require_task(run.task_id)
        if not run.workspace_lease_id:
            await self._escalate_recovery_failure(
                run,
                message="paused graph run has no workspace lease",
                native_type="accretion/resume-requires-human",
            )
            return
        lease = await self.store.get_lease(run.workspace_lease_id)
        prior_session = await self.store.get_session_for_run(run_id)
        if (
            lease is None
            or prior_session is None
            or await self.worktrees.inspect(lease) != "CONSISTENT"
        ):
            await self._escalate_recovery_failure(
                run,
                message="paused graph run cannot recover its workspace or session",
                native_type="accretion/resume-requires-human",
            )
            return
        graph = await self.store.get_run_graph(run_id)
        if graph is None:
            await self._escalate_recovery_failure(
                run,
                message="paused graph run has no persisted run graph",
                native_type="accretion/resume-requires-human",
            )
            return
        try:
            # A resume continues the prior session rather than opening a new one: the
            # native session id handed to ``create_session`` below is meaningful only to
            # the runtime that minted it, so both the slot and the call follow the
            # session's provider, not the run's. They are the same provider today.
            async with self.limiter.slot(prior_session.provider, run.project_id):
                session = await self._runtime_for(prior_session).create_session(
                    SessionConfig(
                        run_id=run_id,
                        workspace=lease.path,
                        allowed_tools=task.envelope.allowed_capabilities,
                        denied_tools=task.envelope.denied_capabilities,
                        resume_native_session_id=prior_session.native_session_id,
                    )
                )
                await self.store.save_session(session)
                run = await self.store.update_run(
                    run_id,
                    RunState.RUNNING,
                    session_id=session.session_id,
                )
                await self._append(
                    self._control_event(
                        run,
                        "accretion/run-resumed",
                        EventType.RUN_RESUMED,
                        session_id=session.session_id,
                    )
                )
                await self._execute_graph(run, task, lease, session, graph)
        except Exception as exc:
            await self._fail_execution(run_id, exc)
        finally:
            self.active_refs.pop(run_id, None)
            self.background.pop(run_id, None)

    async def _resume_direct(self, run_id: str) -> None:
        run = await self._require_run(run_id)
        task = await self._require_task(run.task_id)
        if not run.workspace_lease_id:
            await self._fail_execution(run_id, RuntimeError("paused run has no workspace lease"))
            return
        lease = await self.store.get_lease(run.workspace_lease_id)
        prior_session = await self.store.get_session_for_run(run_id)
        if (
            lease is None
            or prior_session is None
            or await self.worktrees.inspect(lease) != "CONSISTENT"
        ):
            await self._fail_execution(
                run_id, RuntimeError("paused run cannot recover its workspace or session")
            )
            return
        try:
            # A resume continues the prior session rather than opening a new one: the
            # native session id handed to ``create_session`` below is meaningful only to
            # the runtime that minted it, so both the slot and the call follow the
            # session's provider, not the run's. They are the same provider today.
            async with self.limiter.slot(prior_session.provider, run.project_id):
                session = await self._runtime_for(prior_session).create_session(
                    SessionConfig(
                        run_id=run_id,
                        workspace=lease.path,
                        allowed_tools=task.envelope.allowed_capabilities,
                        denied_tools=task.envelope.denied_capabilities,
                        resume_native_session_id=prior_session.native_session_id,
                    )
                )
                await self.store.save_session(session)
                run = await self.store.update_run(
                    run_id,
                    RunState.RUNNING,
                    session_id=session.session_id,
                )
                await self._append(
                    self._control_event(
                        run,
                        "accretion/run-resumed",
                        EventType.RUN_RESUMED,
                        session_id=session.session_id,
                    )
                )
                await self._execute_direct(run, task, lease, session)
        except Exception as exc:
            await self._fail_execution(run_id, exc)
        finally:
            self.active_refs.pop(run_id, None)
            self.background.pop(run_id, None)

    async def _resume_loop(self, run_id: str) -> None:
        run = await self._require_run(run_id)
        task = await self._require_task(run.task_id)
        if not run.workspace_lease_id:
            await self._escalate_recovery_failure(
                run,
                message="paused loop has no workspace lease",
                native_type="accretion/resume-requires-human",
            )
            return
        lease = await self.store.get_lease(run.workspace_lease_id)
        prior_session = await self.store.get_session_for_run(run_id)
        if (
            lease is None
            or prior_session is None
            or await self.worktrees.inspect(lease) != "CONSISTENT"
        ):
            await self._escalate_recovery_failure(
                run,
                message="paused loop cannot recover its workspace or session",
                native_type="accretion/resume-requires-human",
            )
            return
        try:
            # A resume continues the prior session rather than opening a new one: the
            # native session id handed to ``create_session`` below is meaningful only to
            # the runtime that minted it, so both the slot and the call follow the
            # session's provider, not the run's. They are the same provider today.
            async with self.limiter.slot(prior_session.provider, run.project_id):
                session = await self._runtime_for(prior_session).create_session(
                    SessionConfig(
                        run_id=run_id,
                        workspace=lease.path,
                        allowed_tools=task.envelope.allowed_capabilities,
                        denied_tools=task.envelope.denied_capabilities,
                        resume_native_session_id=prior_session.native_session_id,
                    )
                )
                await self.store.save_session(session)
                run = await self.store.update_run(
                    run_id,
                    RunState.RUNNING,
                    session_id=session.session_id,
                )
                await self._append(
                    self._control_event(
                        run,
                        "accretion/run-resumed",
                        EventType.RUN_RESUMED,
                        session_id=session.session_id,
                    )
                )
                await self._execute_loop(run, task, lease, session)
        except Exception as exc:
            await self._fail_execution(run_id, exc)
        finally:
            self.active_refs.pop(run_id, None)
            self.background.pop(run_id, None)

    async def cancel(self, run_id: str) -> Run:
        run = await self._require_run(run_id)
        if run.state in TERMINAL_RUN_STATES:
            return run
        active = self.active_refs.get(run_id)
        if active:
            await self._runtime_for(active.session).terminate(active.ref)
        task = self.background.get(run_id)
        if task and not task.done():
            task.cancel()
        await self._cancel_execution(run_id)
        return await self._require_run(run_id)

    async def reconcile(self) -> None:
        uncertain_run_ids: set[str] = set()
        if self.side_effect_ledger is not None:
            uncertain_run_ids = {
                operation.run_id
                for operation in await self.side_effect_ledger.reconcile_uncertain()
            }
        for run in await self.store.list_runs(limit=10_000):
            execution = await self.store.get_loop_execution_for_run(run.run_id)
            # Only a whole-run LOOP execution's terminal status resolves the
            # run; a graph node's bounded region ending SUCCEEDED mid-graph is
            # not a verified run outcome and falls through to classification.
            if (
                run.execution_mode is ExecutionMode.LOOP
                and execution is not None
            ) and execution.status in {
                LoopExecutionStatus.SUCCEEDED,
                LoopExecutionStatus.FAILED,
                LoopExecutionStatus.CANCELLED,
                LoopExecutionStatus.REQUIRES_HUMAN,
            }:
                run_state = {
                    LoopExecutionStatus.SUCCEEDED: RunState.SUCCEEDED,
                    LoopExecutionStatus.FAILED: RunState.FAILED,
                    LoopExecutionStatus.CANCELLED: RunState.CANCELLED,
                    LoopExecutionStatus.REQUIRES_HUMAN: RunState.REQUIRES_HUMAN,
                }[execution.status]
                if run.state is not run_state:
                    run = await self.store.update_run(run.run_id, run_state)
                await self._append_terminal_if_missing(
                    run,
                    stop_reason=execution.stop_reason,
                    native_type="accretion/reconciled-loop-terminal",
                )
                continue
            existing_terminal = next(
                (
                    event
                    for event in reversed(await self.store.list_events(run.run_id))
                    if event.normalized_type
                    in {
                        EventType.RUN_COMPLETED,
                        EventType.RUN_FAILED,
                        EventType.RUN_CANCELLED,
                    }
                ),
                None,
            )
            if existing_terminal is not None:
                recovered_state = {
                    EventType.RUN_COMPLETED: RunState.SUCCEEDED,
                    EventType.RUN_CANCELLED: RunState.CANCELLED,
                }.get(
                    existing_terminal.normalized_type,
                    RunState.REQUIRES_HUMAN
                    if run.state is RunState.REQUIRES_HUMAN
                    else RunState.FAILED,
                )
                if run.state is not recovered_state:
                    await self.store.update_run(run.run_id, recovered_state)
                continue
            if run.state in TERMINAL_RUN_STATES:
                await self._append_terminal_if_missing(
                    run,
                    native_type="accretion/reconciled-run-terminal",
                )
                continue
            if run.state is RunState.PAUSED and (
                execution is None or execution.status is LoopExecutionStatus.PAUSED
            ):
                await self._append_pause_if_missing(run)
                continue
            await self.store.update_run(run.run_id, RunState.RECONCILING)
            if not run.workspace_lease_id:
                await self._escalate_recovery_failure(
                    run,
                    message="reconciling run has no workspace lease",
                    native_type="accretion/reconciliation-requires-human",
                )
                continue
            lease = await self.store.get_lease(run.workspace_lease_id)
            if lease is None:
                await self._escalate_recovery_failure(
                    run,
                    message="reconciling run cannot recover its workspace",
                    native_type="accretion/reconciliation-requires-human",
                )
                continue
            workspace_status = await self.worktrees.inspect(lease)
            loop_executions = {
                item.loop_execution_id: item
                for item in await self.store.list_loop_executions_for_run(run.run_id)
            }
            checkpoint = await self.store.get_latest_checkpoint(run.run_id)
            evaluation = (
                evaluate_checkpoint(
                    checkpoint,
                    run_last_sequence=run.last_sequence,
                    loop_executions=loop_executions,
                    workspace_status=workspace_status,
                )
                if checkpoint is not None
                else None
            )
            classification = classify_run(
                workspace_status=workspace_status,
                has_uncertain_side_effects=run.run_id in uncertain_run_ids,
                has_candidate_work=bool(await self.store.list_artifacts(run.run_id)),
                checkpoint_evaluation=evaluation,
            )
            await self._append(
                self._control_event(
                    run,
                    "accretion/reconciliation-classified",
                    EventType.RUN_PROGRESS,
                    payload={
                        "classification": classification.classification.value,
                        "reason": classification.reason,
                        "checkpoint_id": checkpoint.checkpoint_id if checkpoint else None,
                        "checkpoint_reason": (
                            classification.checkpoint_reason.value
                            if classification.checkpoint_reason
                            else None
                        ),
                    },
                )
            )
            if classification.classification is ReconcileClassification.REQUIRES_HUMAN:
                message = classification.reason
                if classification.checkpoint_reason is not None:
                    message = (
                        f"CHECKPOINT_INVALID:{classification.checkpoint_reason.value} — "
                        f"{classification.reason}"
                    )
                await self._escalate_recovery_failure(
                    run,
                    message=message,
                    native_type="accretion/reconciliation-requires-human",
                )
                continue
            if classification.classification is ReconcileClassification.RECREATE:
                project = await self.store.get_project(run.project_id)
                if project is None:
                    await self._escalate_recovery_failure(
                        run,
                        message="recreate-classified run has no project record",
                        native_type="accretion/reconciliation-requires-human",
                    )
                    continue
                try:
                    fresh = await self.worktrees.reacquire(
                        lease=lease, repository=project.repository_path
                    )
                except Exception:
                    await self._escalate_recovery_failure(
                        run,
                        message="workspace recreation failed",
                        native_type="accretion/reconciliation-requires-human",
                    )
                    continue
                await self.store.save_lease(fresh)
                run = await self.store.update_run(
                    run.run_id, RunState.RECONCILING, workspace_lease_id=fresh.lease_id
                )
            if execution is not None and execution.status not in {
                LoopExecutionStatus.SUCCEEDED,
                LoopExecutionStatus.FAILED,
                LoopExecutionStatus.CANCELLED,
                LoopExecutionStatus.REQUIRES_HUMAN,
            }:
                closed = await self._close_dangling_iteration(
                    run=run,
                    execution=execution,
                    iteration_status=LoopIterationStatus.INTERRUPTED,
                    loop_status=LoopExecutionStatus.PAUSED,
                    stop_reason=LoopStopReason.INTERRUPTED,
                )
                if closed is None:
                    await self.store.update_loop_execution(
                        execution.loop_execution_id,
                        execution.state,
                        status=LoopExecutionStatus.PAUSED,
                        stop_reason=LoopStopReason.INTERRUPTED,
                        expected_revision=execution.revision,
                    )
            run = await self.store.update_run(run.run_id, RunState.PAUSED)
            await self._append_pause_if_missing(run)
            if (
                self.auto_resume_on_reconcile
                and classification.classification is ReconcileClassification.RESUMABLE
                and checkpoint is not None
                and evaluation is not None
                and evaluation.valid
                and await self._resume_runtime_available(run)
            ):
                # resume() registers background[run_id] itself; a distinct key
                # keeps its not-already-running check truthful.
                self.background[f"auto-resume:{run.run_id}"] = asyncio.create_task(
                    self._auto_resume(run.run_id)
                )

    async def _auto_resume(self, run_id: str) -> None:
        await self.resume(run_id)

    async def _resume_runtime_available(self, run: Run) -> bool:
        """Can the runtime an auto-resume would actually reach be reached?

        A resume re-enters the prior session's runtime, because only that runtime knows
        the native session id it is asked to continue. Probing ``run.provider`` instead
        would clear a run for auto-resume against a runtime that is not the one about
        to be called. A run with no session cannot be resumed at all --- the resume
        paths escalate --- so the run's requested provider is the honest fallback.
        """

        session = await self.store.get_session_for_run(run.run_id)
        return self._runtime_available(session.provider if session is not None else run.provider)

    def _runtime_available(self, provider: Provider) -> bool:
        if provider not in self.runtimes:
            return False
        if provider in LIVE_PROVIDERS and not self.live_providers_enabled:
            return False
        return True

    async def _escalate_recovery_failure(
        self,
        run: Run,
        *,
        message: str,
        native_type: str,
    ) -> None:
        execution = await self.store.get_loop_execution_for_run(run.run_id)
        terminal_loop_states = {
            LoopExecutionStatus.SUCCEEDED: (RunState.SUCCEEDED, EventType.RUN_COMPLETED),
            LoopExecutionStatus.FAILED: (RunState.FAILED, EventType.RUN_FAILED),
            LoopExecutionStatus.CANCELLED: (RunState.CANCELLED, EventType.RUN_CANCELLED),
            LoopExecutionStatus.REQUIRES_HUMAN: (
                RunState.REQUIRES_HUMAN,
                EventType.RUN_FAILED,
            ),
        }
        # As in _cancel_execution: only a whole-run LOOP execution's terminal
        # status may resolve the run.
        if (
            run.execution_mode is ExecutionMode.LOOP
            and execution is not None
            and execution.status in terminal_loop_states
        ):
            state, event_type = terminal_loop_states[execution.status]
            await self._commit_run_terminal(
                run,
                state=state,
                event_type=event_type,
                native_type="accretion/recovered-loop-terminal",
                payload={
                    "stop_reason": execution.stop_reason.value
                    if execution.stop_reason
                    else "UNKNOWN"
                },
                node_key="complete",
            )
            return
        if execution is not None and execution.status not in terminal_loop_states:
            await self.store.update_loop_execution(
                execution.loop_execution_id,
                execution.state,
                status=LoopExecutionStatus.REQUIRES_HUMAN,
                stop_reason=LoopStopReason.INTERRUPTED,
                expected_revision=execution.revision,
            )
        error = ErrorSummary(code="RUN_RECOVERY_REQUIRES_HUMAN", message=message)
        await self._commit_run_terminal(
            run,
            state=RunState.REQUIRES_HUMAN,
            event_type=EventType.RUN_FAILED,
            native_type=native_type,
            payload={"stop_reason": LoopStopReason.INTERRUPTED.value, "reason": message},
            node_key="complete" if execution is not None else None,
            error=error,
        )

    async def _append_terminal_if_missing(
        self,
        run: Run,
        *,
        stop_reason: LoopStopReason | None = None,
        native_type: str,
    ) -> None:
        event_type = {
            RunState.SUCCEEDED: EventType.RUN_COMPLETED,
            RunState.CANCELLED: EventType.RUN_CANCELLED,
        }.get(run.state, EventType.RUN_FAILED)
        payload: dict[str, object] = {"reconciled": True}
        if stop_reason is not None:
            payload["stop_reason"] = stop_reason.value
        await self._commit_run_terminal(
            run,
            state=run.state,
            event_type=event_type,
            native_type=native_type,
            payload=payload,
            node_key="complete" if run.execution_mode is ExecutionMode.LOOP else None,
        )

    async def _commit_run_terminal(
        self,
        run: Run,
        *,
        state: RunState,
        event_type: EventType,
        native_type: str,
        session_id: str | None = None,
        payload: dict[str, object] | None = None,
        node_key: str | None = None,
        final_node_status: str | None = None,
        error: ErrorSummary | None = None,
    ) -> Run:
        # The terminal RunState travels in the event payload so replay can
        # reconstruct REQUIRES_HUMAN from immutable events alone (a bare
        # RUN_FAILED cannot distinguish failure from escalation).
        payload = dict(payload or {})
        payload.setdefault("terminal_state", state.value)
        lock = self.terminal_locks.setdefault(run.run_id, asyncio.Lock())
        async with lock:
            current = await self._require_run(run.run_id)
            terminal_events = [
                event
                for event in await self.store.list_events(run.run_id)
                if event.normalized_type
                in {
                    EventType.RUN_COMPLETED,
                    EventType.RUN_FAILED,
                    EventType.RUN_CANCELLED,
                }
            ]
            if terminal_events:
                if current.state not in TERMINAL_RUN_STATES:
                    existing_type = terminal_events[0].normalized_type
                    recovered_state = {
                        EventType.RUN_COMPLETED: RunState.SUCCEEDED,
                        EventType.RUN_CANCELLED: RunState.CANCELLED,
                    }.get(
                        existing_type,
                        state if state is RunState.REQUIRES_HUMAN else RunState.FAILED,
                    )
                    current = await self.store.update_run(
                        run.run_id, recovered_state, error=error
                    )
                    await self._notify(run.run_id)
                return current

            current = await self.store.update_run(run.run_id, state, error=error)
            await self._notify(run.run_id)
            if final_node_status is not None and node_key is not None:
                await self._node_transition(
                    run,
                    session_id or run.session_id or "ses_pending",
                    node_key,
                    entered=True,
                )
            await self._append(
                self._control_event(
                    run,
                    native_type,
                    event_type,
                    session_id=session_id,
                    payload=payload,
                    node_key=node_key,
                )
            )
            if final_node_status is not None and node_key is not None:
                await self._node_transition(
                    run,
                    session_id or run.session_id or "ses_pending",
                    node_key,
                    entered=False,
                    status=final_node_status,
                )
            return current

    async def _append_pause_if_missing(self, run: Run) -> None:
        if any(
            event.normalized_type is EventType.RUN_PAUSED
            for event in await self.store.list_events(run.run_id)
        ):
            return
        await self._append(
            self._control_event(
                run,
                "accretion/reconciled-run-paused",
                EventType.RUN_PAUSED,
                payload={"reason": LoopStopReason.INTERRUPTED.value, "reconciled": True},
            )
        )

    async def _append(self, event: AgentEvent) -> AgentEvent:
        stored = await self.store.append_event(event)
        await self._notify(event.run_id)
        return stored

    async def _notify(self, run_id: str) -> None:
        condition = self.event_conditions.setdefault(run_id, asyncio.Condition())
        async with condition:
            condition.notify_all()

    def _control_event(
        self,
        run: Run,
        native_type: str,
        normalized_type: EventType,
        *,
        session_id: str | None = None,
        payload: dict[str, object] | None = None,
        node_key: str | None = None,
    ) -> AgentEvent:
        event = make_event(
            run_id=run.run_id,
            session_id=session_id or run.session_id or "ses_pending",
            provider=Provider.DETERMINISTIC,
            native_type=native_type,
            normalized_type=normalized_type,
            payload=payload,
            adapter_version="control-plane-p2-v1",
        )
        return event.model_copy(
            update={"node_id": self._node_id(run.run_id, node_key) if node_key else None}
        )

    @staticmethod
    def _node_id(run_id: str, key: str) -> str:
        return f"{run_id}:{key}"

    async def _require_run(self, run_id: str) -> Run:
        run = await self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    async def _require_task(self, task_id: str) -> Task:
        task = await self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    async def _require_loop(self, run_id: str) -> LoopExecution:
        execution = await self.store.get_loop_execution_for_run(run_id)
        if execution is None:
            raise KeyError(run_id)
        return execution

    async def _require_policy(self, policy_id: str | None) -> AcceptancePolicy:
        if policy_id is None:
            raise KeyError("acceptance-policy")
        policy = await self.store.get_acceptance_policy(policy_id)
        if policy is None:
            raise KeyError(policy_id)
        return policy


class WorkflowTemplateError(RuntimeError):
    """A run cannot start because its template fails a fail-closed guard."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ProjectionUnavailableError(RuntimeError):
    """The run predates P3 persistence and has no graph to project."""

    def __init__(self, run: Run) -> None:
        mode = run.execution_mode.value if run.execution_mode is not None else "UNKNOWN"
        super().__init__(
            f"Run {run.run_id} ({mode}) has no persisted run graph to project"
        )
