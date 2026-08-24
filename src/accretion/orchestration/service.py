from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from accretion.contracts import (
    RISK_RANK,
    EventType,
    GraphNodeStatus,
    Provider,
    RiskLevel,
    Run,
    RunState,
    RuntimeStatus,
)
from accretion.ids import new_id
from accretion.orchestration.fragments import FragmentWorkflowPlanner
from accretion.orchestration.materialize import materialize_workflow_template
from accretion.orchestration.models import (
    CapabilitySnapshot,
    GraphRevisionDiff,
    GraphValidationResult,
    GraphValidationStatus,
    PlannerRuntime,
    PolicySnapshot,
    ProjectFeatureSettings,
    ReplanOutcome,
    ReplanReason,
    ReplanRequest,
    ReplanStatus,
    RunGraphRevision,
    RuntimeDecision,
    WorkflowActivationOutcome,
    WorkflowProposal,
    WorkflowValidationOutcome,
)
from accretion.orchestration.router import PerformanceAwareRuntimeRouter
from accretion.orchestration.validator import GraphValidator
from accretion.services.run_manager import RunManager


class DynamicWorkflowDisabledError(PermissionError):
    pass


class DynamicWorkflowConflictError(RuntimeError):
    pass


class DynamicWorkflowService:
    """P5 authority boundary for proposal, validation, activation, and replan."""

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
        self.planner = FragmentWorkflowPlanner()
        self.validator = GraphValidator()
        self.router = PerformanceAwareRuntimeRouter()

    async def get_project_features(self, project_id: str) -> ProjectFeatureSettings:
        return await self.store.get_project_features(project_id)

    async def update_project_features(
        self,
        project_id: str,
        *,
        dynamic_workflows: bool | None,
        candidate_search: bool | None = None,
        experience_retrieval: bool | None = None,
        expected_revision: int,
    ) -> ProjectFeatureSettings:
        current = await self.store.get_project_features(project_id)
        next_dynamic = (
            current.dynamic_workflows
            if dynamic_workflows is None
            else dynamic_workflows
        )
        next_search = (
            current.candidate_search if candidate_search is None else candidate_search
        )
        next_experience = (
            current.experience_retrieval
            if experience_retrieval is None
            else experience_retrieval
        )
        if not next_dynamic:
            next_search = False
        if not next_search:
            next_experience = False
        requested = current.model_copy(
            update={
                "dynamic_workflows": next_dynamic,
                "candidate_search": next_search,
                "experience_retrieval": next_experience,
            }
        )
        return await self.store.update_project_features(
            requested, expected_revision=expected_revision
        )

    async def propose(
        self,
        task_id: str,
        *,
        execution_provider: Provider,
        planner_runtime: PlannerRuntime = PlannerRuntime.DETERMINISTIC,
    ) -> WorkflowProposal:
        task = await self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        await self._require_enabled(task.envelope.project_id)
        if planner_runtime in {PlannerRuntime.CLAUDE, PlannerRuntime.CODEX}:
            raise ValueError(
                "P5 ships the reviewed fragment planner; live model topology glue is not enabled"
            )
        planning = await self.manager.get_task_planning(task_id)
        effective_runtime = (
            PlannerRuntime.DETERMINISTIC
            if planner_runtime is PlannerRuntime.AUTO
            else planner_runtime
        )
        draft = self.planner.propose(
            task,
            planning.current_profile,
            planner_runtime=effective_runtime,
        )
        if planning.context_bundle.experience_match_refs:
            draft = draft.model_copy(
                update={
                    "provenance_refs": [
                        *draft.provenance_refs,
                        *(
                            f"experience-match:{item}"
                            for item in planning.context_bundle.experience_match_refs
                        ),
                    ]
                }
            )
        run = await self.manager.prepare_dynamic_run(
            task_id,
            execution_provider,
            required_verifiers=draft.expected_verifiers,
            has_approval_gates=bool(draft.expected_approval_gates),
        )
        proposal = draft.model_copy(update={"run_id": run.run_id})
        await self.store.save_workflow_proposal(proposal)
        decision = await self._record_runtime_decision(run, execution_provider)
        await self.manager.emit_dynamic_event(
            run.run_id,
            native_type="accretion/workflow-proposal-created",
            event_type=EventType.WORKFLOW_PROPOSAL_CREATED,
            payload={
                "proposal_id": proposal.proposal_id,
                "planner_version": proposal.planner_version,
                "planner_runtime": proposal.planner_runtime.value,
                "fragment_refs": proposal.fragment_refs,
            },
        )
        await self.manager.emit_dynamic_event(
            run.run_id,
            native_type="accretion/runtime-decision",
            event_type=EventType.RUNTIME_DECISION,
            payload={
                "decision_id": decision.decision_id,
                "selected_runtime": (
                    decision.selected_runtime.value if decision.selected_runtime else None
                ),
                "policy_version": decision.policy_version,
                "fallback_order": [item.value for item in decision.fallback_order],
            },
        )
        return proposal

    async def validate(self, run_id: str, proposal_id: str) -> WorkflowValidationOutcome:
        proposal = await self._require_proposal(run_id, proposal_id)
        return await self._validate_with_bounded_repair(proposal)

    async def activate(
        self, run_id: str, proposal_id: str
    ) -> WorkflowActivationOutcome:
        proposal = await self._require_proposal(run_id, proposal_id)
        if proposal.based_on_graph_revision is not None:
            raise DynamicWorkflowConflictError("replan proposals activate through /replan")
        validation = await self._accepted_validation(proposal_id)
        assert validation.normalized_graph_hash is not None
        template = materialize_workflow_template(
            proposal, normalized_graph_hash=validation.normalized_graph_hash
        )
        run, graph, stored_template = await self.manager.install_dynamic_graph(
            run_id, template
        )
        revision = RunGraphRevision(
            revision_id=new_id("graph_revision"),
            run_graph_id=graph.run_graph_id,
            run_id=run_id,
            revision=1,
            proposal_id=proposal.proposal_id,
            reason=ReplanReason.INITIAL,
            nodes=proposal.nodes,
            edges=proposal.edges,
            normalized_graph_hash=validation.normalized_graph_hash,
            activated_at=datetime.now(UTC),
        )
        await self.store.save_graph_revision(revision)
        await self.manager.emit_dynamic_event(
            run.run_id,
            native_type="accretion/graph-revision-activated",
            event_type=EventType.GRAPH_REVISION_ACTIVATED,
            payload={
                "revision_id": revision.revision_id,
                "revision": revision.revision,
                "proposal_id": proposal.proposal_id,
                "normalized_graph_hash": revision.normalized_graph_hash,
                "template_id": stored_template.template_id,
                "template_checksum": stored_template.checksum,
            },
        )
        await self.manager.launch_dynamic_run(run_id)
        return WorkflowActivationOutcome(
            run_id=run_id,
            proposal_id=proposal.proposal_id,
            revision=revision,
            workflow_template_id=stored_template.template_id,
        )

    async def replan(
        self,
        run_id: str,
        *,
        reason: ReplanReason,
        evidence_refs: list[str],
    ) -> ReplanOutcome:
        run = await self.manager._require_run(run_id)
        await self._require_enabled(run.project_id)
        revisions = await self.store.list_graph_revisions(run_id)
        if not revisions:
            raise DynamicWorkflowConflictError("run has no active dynamic graph revision")
        await self._settle_for_replan(run)
        run = await self.manager._require_run(run_id)
        if run.state is not RunState.PAUSED:
            raise DynamicWorkflowConflictError(
                f"run must be PAUSED for replan, got {run.state.value}"
            )
        latest = revisions[-1]
        request = ReplanRequest(
            replan_request_id=new_id("replan_request"),
            run_id=run_id,
            based_on_graph_revision=latest.revision,
            reason=reason,
            evidence_refs=evidence_refs,
            requested_by=self.operator_identity,
        )
        await self.store.save_replan_request(request)
        await self.manager.emit_dynamic_event(
            run_id,
            native_type="accretion/replan-requested",
            event_type=EventType.REPLAN_REQUESTED,
            payload={
                "replan_request_id": request.replan_request_id,
                "based_on_graph_revision": latest.revision,
                "reason": reason.value,
                "evidence_refs": evidence_refs,
            },
        )
        task = await self.manager._require_task(run.task_id)
        planning = await self.manager.get_task_planning(run.task_id)
        proposal = self.planner.propose(
            task,
            planning.current_profile,
            run_id=run_id,
            based_on_graph_revision=latest.revision,
        )
        await self.store.save_workflow_proposal(proposal)
        request = request.model_copy(
            update={
                "status": ReplanStatus.VALIDATING,
                "resulting_proposal_id": proposal.proposal_id,
                "updated_at": datetime.now(UTC),
            }
        )
        await self.store.save_replan_request(request)
        await self.manager.emit_dynamic_event(
            run_id,
            native_type="accretion/replan-started",
            event_type=EventType.REPLAN_STARTED,
            payload={
                "replan_request_id": request.replan_request_id,
                "proposal_id": proposal.proposal_id,
            },
        )
        outcome = await self._validate_with_bounded_repair(proposal)
        proposal = outcome.proposal
        validation = outcome.validation
        if validation.status is not GraphValidationStatus.ACCEPT:
            request = request.model_copy(
                update={
                    "status": ReplanStatus.REQUIRES_HUMAN,
                    "resulting_proposal_id": proposal.proposal_id,
                    "updated_at": datetime.now(UTC),
                }
            )
            await self.store.save_replan_request(request)
            return ReplanOutcome(
                request=request, proposal=proposal, validation=validation
            )
        await self._assert_protected_nodes_preserved(run_id, latest, proposal)
        assert validation.normalized_graph_hash is not None
        template = materialize_workflow_template(
            proposal, normalized_graph_hash=validation.normalized_graph_hash
        )
        _, graph, _ = await self.manager.install_dynamic_replan(run_id, template)
        protected_refs = await self._protected_state_refs(run_id)
        revision = RunGraphRevision(
            revision_id=new_id("graph_revision"),
            run_graph_id=graph.run_graph_id,
            run_id=run_id,
            revision=latest.revision + 1,
            parent_revision=latest.revision,
            proposal_id=proposal.proposal_id,
            reason=reason,
            nodes=proposal.nodes,
            edges=proposal.edges,
            normalized_graph_hash=validation.normalized_graph_hash,
            protected_state_refs=protected_refs,
            activated_at=datetime.now(UTC),
        )
        await self.store.save_graph_revision(revision)
        request = request.model_copy(
            update={
                "status": ReplanStatus.ACTIVATED,
                "resulting_proposal_id": proposal.proposal_id,
                "resulting_revision": revision.revision,
                "updated_at": datetime.now(UTC),
            }
        )
        await self.store.save_replan_request(request)
        await self.manager.emit_dynamic_event(
            run_id,
            native_type="accretion/replan-completed",
            event_type=EventType.REPLAN_COMPLETED,
            payload={
                "replan_request_id": request.replan_request_id,
                "proposal_id": proposal.proposal_id,
                "revision": revision.revision,
                "protected_state_refs": protected_refs,
            },
        )
        await self.manager.resume(run_id)
        return ReplanOutcome(
            request=request,
            proposal=proposal,
            validation=validation,
            revision=revision,
        )

    async def graph_diff(
        self, run_id: str, from_revision: int, to_revision: int
    ) -> GraphRevisionDiff:
        before = await self.store.get_graph_revision(run_id, from_revision)
        after = await self.store.get_graph_revision(run_id, to_revision)
        if before is None:
            raise KeyError(f"{run_id}/revision/{from_revision}")
        if after is None:
            raise KeyError(f"{run_id}/revision/{to_revision}")
        before_nodes = {item.local_id: item for item in before.nodes}
        after_nodes = {item.local_id: item for item in after.nodes}
        before_edges = {item.local_id: item for item in before.edges}
        after_edges = {item.local_id: item for item in after.edges}
        return GraphRevisionDiff(
            run_id=run_id,
            from_revision=from_revision,
            to_revision=to_revision,
            added_nodes=sorted(set(after_nodes) - set(before_nodes)),
            removed_nodes=sorted(set(before_nodes) - set(after_nodes)),
            changed_nodes=sorted(
                key
                for key in set(before_nodes) & set(after_nodes)
                if before_nodes[key] != after_nodes[key]
            ),
            added_edges=sorted(set(after_edges) - set(before_edges)),
            removed_edges=sorted(set(before_edges) - set(after_edges)),
            changed_edges=sorted(
                key
                for key in set(before_edges) & set(after_edges)
                if before_edges[key] != after_edges[key]
            ),
            protected_state_refs=after.protected_state_refs,
        )

    async def _validate_with_bounded_repair(
        self, proposal: WorkflowProposal
    ) -> WorkflowValidationOutcome:
        assert proposal.run_id is not None
        run_id = proposal.run_id
        task = await self.manager._require_task(proposal.task_id)
        await self.manager.emit_dynamic_event(
            run_id,
            native_type="accretion/graph-validation-started",
            event_type=EventType.GRAPH_VALIDATION_STARTED,
            payload={"proposal_id": proposal.proposal_id},
        )
        capabilities, policy = await self._snapshots(task.envelope.task_id, run_id)
        validation = self.validator.validate(
            proposal, capabilities, policy, task.envelope.budgets
        )
        await self.store.save_graph_validation(validation)
        await self._emit_validation(run_id, validation)
        if validation.status is GraphValidationStatus.REPAIRABLE:
            proposal = self.planner.repair(proposal)
            await self.store.save_workflow_proposal(proposal)
            await self.manager.emit_dynamic_event(
                run_id,
                native_type="accretion/workflow-proposal-repaired",
                event_type=EventType.WORKFLOW_PROPOSAL_REPAIRED,
                payload={
                    "proposal_id": proposal.proposal_id,
                    "supersedes_proposal_id": proposal.provenance_refs[-1],
                    "repair_attempt": proposal.repair_attempt,
                },
            )
            validation = self.validator.validate(
                proposal, capabilities, policy, task.envelope.budgets
            )
            await self.store.save_graph_validation(validation)
            await self._emit_validation(run_id, validation)
        fallback_run_id: str | None = None
        if (
            validation.status is GraphValidationStatus.REJECT
            and proposal.based_on_graph_revision is None
        ):
            dynamic_run = await self.manager._require_run(run_id)
            await self.manager.fallback_dynamic_run(
                run_id, reason="dynamic proposal exhausted its repair budget"
            )
            fallback = await self.manager.start_run(proposal.task_id, dynamic_run.provider)
            fallback_run_id = fallback.run_id
        return WorkflowValidationOutcome(
            proposal=proposal,
            validation=validation,
            fallback_run_id=fallback_run_id,
        )

    async def _snapshots(
        self, task_id: str, run_id: str
    ) -> tuple[CapabilitySnapshot, PolicySnapshot]:
        task = await self.manager._require_task(task_id)
        run = await self.manager._require_run(run_id)
        capabilities = await self.store.list_capabilities()
        skills = await self.store.list_skills()
        health = await asyncio.gather(
            *(runtime.health() for runtime in self.manager.runtimes.values())
        )
        available = {
            item.provider
            for item in health
            if item.status in {RuntimeStatus.READY, RuntimeStatus.BUSY}
            and (
                item.provider not in {Provider.CODEX, Provider.CLAUDE}
                or self.manager.live_providers_enabled
            )
        }
        available.add(Provider.DETERMINISTIC)
        return (
            CapabilitySnapshot(
                capabilities={item.capability_id: item.risk for item in capabilities},
                protected_capabilities={
                    item.capability_id
                    for item in capabilities
                    if item.side_effects
                    or RISK_RANK[item.risk] >= RISK_RANK[RiskLevel.HIGH]
                },
                skills={item.skill_id for item in skills},
                verifiers=set(self.manager.verifiers.list_ids()),
                available_runtimes=available,
            ),
            PolicySnapshot(
                allowed_capabilities=set(task.envelope.allowed_capabilities),
                denied_capabilities=set(task.envelope.denied_capabilities),
                required_verifiers=set(self.manager._verifier_ids(task)),
                maximum_risk=task.envelope.risk_level,
                execution_runtime=run.provider,
            ),
        )

    async def _record_runtime_decision(
        self, run: Run, requested_provider: Provider
    ) -> RuntimeDecision:
        health = [await self.manager.runtimes[requested_provider].health()]
        decision = self.router.decide(
            run_id=run.run_id,
            node_id=f"{run.run_id}:workflow",
            health=health,
            historical_quality={},
            specialization_fit={requested_provider: 1.0},
        )
        return await self.store.save_runtime_decision(decision)

    async def _accepted_validation(self, proposal_id: str) -> GraphValidationResult:
        validations = await self.store.list_graph_validations(proposal_id)
        if not validations or validations[-1].status is not GraphValidationStatus.ACCEPT:
            raise DynamicWorkflowConflictError(
                f"proposal {proposal_id} has no accepted validation"
            )
        return validations[-1]

    async def _require_proposal(
        self, run_id: str, proposal_id: str
    ) -> WorkflowProposal:
        proposal = await self.store.get_workflow_proposal(proposal_id)
        if proposal is None:
            raise KeyError(proposal_id)
        if proposal.run_id != run_id:
            raise DynamicWorkflowConflictError(
                f"proposal {proposal_id} does not belong to run {run_id}"
            )
        run = await self.manager._require_run(run_id)
        await self._require_enabled(run.project_id)
        return proposal

    async def _require_enabled(self, project_id: str) -> None:
        if not self.globally_enabled:
            raise DynamicWorkflowDisabledError(
                "dynamic workflows are globally disabled; set "
                "ACCRETION_ENABLE_DYNAMIC_WORKFLOWS=true"
            )
        features = await self.store.get_project_features(project_id)
        if not features.dynamic_workflows:
            raise DynamicWorkflowDisabledError(
                f"dynamic workflows are disabled for project {project_id}"
            )

    async def _emit_validation(
        self, run_id: str, validation: GraphValidationResult
    ) -> None:
        await self.manager.emit_dynamic_event(
            run_id,
            native_type="accretion/graph-validation-result",
            event_type=EventType.GRAPH_VALIDATION_RESULT,
            payload={
                "validation_id": validation.validation_id,
                "proposal_id": validation.proposal_id,
                "status": validation.status.value,
                "validator_version": validation.validator_version,
                "normalized_graph_hash": validation.normalized_graph_hash,
                "error_codes": [item.code for item in validation.errors],
            },
        )

    async def _settle_for_replan(self, run: Run) -> None:
        if run.state in {RunState.RUNNING, RunState.STARTING}:
            await self.manager.pause(run.run_id)
            background = self.manager.background.get(run.run_id)
            if background is not None and not background.done():
                try:
                    await asyncio.wait_for(asyncio.shield(background), timeout=10)
                except TimeoutError as exc:
                    raise DynamicWorkflowConflictError(
                        "active node did not settle within the replan safety window"
                    ) from exc

    async def _assert_protected_nodes_preserved(
        self,
        run_id: str,
        previous: RunGraphRevision,
        proposal: WorkflowProposal,
    ) -> None:
        graph = await self.store.get_run_graph(run_id)
        if graph is None:
            raise DynamicWorkflowConflictError("active run graph disappeared")
        checkpoint = await self.store.get_latest_checkpoint(run_id)
        statuses = (
            checkpoint.node_statuses
            if checkpoint is not None and checkpoint.run_graph_id == graph.run_graph_id
            else {node.key: node.status for node in graph.nodes}
        )
        protected_keys = {
            node.key
            for node in graph.nodes
            if statuses.get(node.key)
            in {
                GraphNodeStatus.SUCCEEDED,
                GraphNodeStatus.FAILED,
                GraphNodeStatus.CANCELLED,
            }
            and not node.key.endswith("-act")
            and not node.key.endswith("-observe")
        }
        old_nodes = {node.local_id: node for node in previous.nodes}
        new_nodes = {node.local_id: node for node in proposal.nodes}
        missing = protected_keys - set(new_nodes)
        changed = {
            key
            for key in protected_keys & set(old_nodes) & set(new_nodes)
            if old_nodes[key] != new_nodes[key]
        }
        if missing or changed:
            raise DynamicWorkflowConflictError(
                "replan cannot remove or rewrite completed nodes: "
                f"missing={sorted(missing)}, changed={sorted(changed)}"
            )

    async def _protected_state_refs(self, run_id: str) -> list[str]:
        graph = await self.store.get_run_graph(run_id)
        checkpoint = await self.store.get_latest_checkpoint(run_id)
        statuses = (
            checkpoint.node_statuses
            if checkpoint is not None
            and graph is not None
            and checkpoint.run_graph_id == graph.run_graph_id
            else ({node.key: node.status for node in graph.nodes} if graph else {})
        )
        refs = [
            node.node_id
            for node in graph.nodes
            if statuses.get(node.key)
            in {
                GraphNodeStatus.SUCCEEDED,
                GraphNodeStatus.FAILED,
                GraphNodeStatus.CANCELLED,
            }
        ] if graph is not None else []
        capability_results = await self.store.list_capability_results(run_id)
        refs.extend(
            result.side_effect_operation_id
            for result in capability_results
            if result.side_effect_operation_id is not None
        )
        return sorted(set(refs))
