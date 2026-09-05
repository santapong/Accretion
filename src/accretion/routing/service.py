"""Receipt-first baseline routing and append-only operator decisions (v0.4 M2)."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime

from accretion.contracts import (
    AcceptancePolicy,
    AgentEvent,
    AgentRuntime,
    EventType,
    PrincipalRef,
    PrincipalStatus,
    Provider,
    Run,
    RunNode,
    Task,
    WorkflowNodeSpec,
    WorkflowTemplate,
    WorkspaceRole,
)
from accretion.contracts.routing import (
    ConfigurationCandidate,
    DecisionType,
    ExecutionConfiguration,
    GraphFeatures,
    NodeContractRef,
    ProjectFeatures,
    RoutingContext,
    RoutingDecisionReceipt,
    StructuredExplanation,
    TaskFeatures,
    UncertaintySummary,
)
from accretion.governance import CapabilityPolicyEngine
from accretion.ids import derived_id
from accretion.persistence.store import StateStore
from accretion.routing.candidates import CandidateBuilder
from accretion.routing.catalog import WORKSPACE_ROUTER_VERSION, ConfigurationCatalog
from accretion.routing.compatibility import CompatibilityEngine
from accretion.routing.errors import RoutingError
from accretion.routing.freeze import NodeContractFreezer
from accretion.routing.gates import PolicyGate
from accretion.routing.identity import principal_ref_for_run, routing_request_id, workspace_for_run
from accretion.routing.protocols import FrozenNode, RoutingMode
from accretion.routing.selector import DeterministicSelector
from accretion.routing.snapshot import RegistrySnapshotBuilder, RoutingSnapshot

CatalogFactory = Callable[[FrozenNode, RoutingSnapshot, Run, Task], Awaitable[ConfigurationCatalog]]


def _error(code: str, message: str, status: int = 409) -> RoutingError:
    return RoutingError(code, message, status)


class DefaultNodeRoutingService:
    def __init__(
        self,
        *,
        store: StateStore,
        snapshots: RegistrySnapshotBuilder,
        catalog_factory: CatalogFactory,
        runtimes: Mapping[Provider, AgentRuntime],
        granted_permissions: set[str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.snapshots = snapshots
        self.catalog_factory = catalog_factory
        self.runtimes = runtimes
        self.granted_permissions = granted_permissions or set()
        self.clock = clock or (lambda: datetime.now(UTC))

    async def freeze(
        self,
        *,
        run: Run,
        task: Task,
        node: RunNode,
        spec: WorkflowNodeSpec,
        template: WorkflowTemplate,
        policy: AcceptancePolicy,
        graph_revision: int,
        attempt: int,
    ) -> FrozenNode:
        return await NodeContractFreezer(
            store=self.store,
            created_by=principal_ref_for_run(run),
            workspace_id=await workspace_for_run(self.store, run),
        ).freeze(
            run=run,
            task=task,
            node=node,
            spec=spec,
            template=template,
            policy=policy,
            graph_revision=graph_revision,
            attempt=attempt,
        )

    async def snapshot(
        self, *, workspace_id: str, project_id: str | None, task: Task
    ) -> RoutingSnapshot:
        return await self.snapshots.build(
            workspace_id=workspace_id, project_id=project_id, task=task, clock=self.clock
        )

    async def replay(self, routing_request_id: str) -> RoutingDecisionReceipt | None:
        return await self.store.get_routing_receipt_for_request(routing_request_id)

    async def _authorize(
        self,
        workspace_id: str,
        principal: PrincipalRef,
        *,
        mutate: bool = False,
        store: StateStore | None = None,
    ) -> None:
        store = store or self.store
        stored = await store.get_principal(principal.principal_id)
        memberships = await store.list_workspace_memberships(
            workspace_id=workspace_id, principal_id=principal.principal_id
        )
        if (
            principal.status != PrincipalStatus.ACTIVE
            or stored is None
            or stored.status != PrincipalStatus.ACTIVE
            or not memberships
            or (mutate and memberships[0].role == WorkspaceRole.VIEWER)
        ):
            raise _error("RECEIPT_NOT_FOUND", "Routing resource not found", 404)

    async def _context(
        self,
        store: StateStore,
        frozen: FrozenNode,
        snapshot: RoutingSnapshot,
        run: Run,
        request_id: str,
    ) -> RoutingContext:
        prior = await store.get_routing_request(request_id)
        if prior is not None:
            return prior
        planning = await store.get_task_planning(run.task_id)
        graph = await store.get_run_graph(run.run_id)
        if planning is None or graph is None:
            raise _error("ROUTING_INPUT_MISSING", "Persisted planning and graph are required", 422)
        at = self.clock()
        node = frozen.node_contract
        header = dict(
            created_at=at,
            created_by=node.created_by,
            workspace_id=node.workspace_id,
            project_id=node.project_id,
            objective_contract_ref=frozen.objective_ref,
        )
        profile = planning.current_profile
        feature_names = (
            "complexity",
            "structure_certainty",
            "feedback_dependency",
            "dependency_complexity",
            "parallelism_potential",
            "uncertainty",
            "verifier_strength",
            "risk",
            "irreversible_actions",
            "expected_horizon",
            "profile_confidence",
        )
        task_features = TaskFeatures(  # type: ignore[call-arg]
            contract_id=request_id + "-task",
            **header,
            source_profile_id=profile.profile_id,
            **{name: getattr(profile, name) for name in feature_names},
        )
        project_features = ProjectFeatures(  # type: ignore[call-arg]
            contract_id=request_id + "-project",
            **header,
            feature_window_days=30,
            observed_task_count=0,
        )
        # Project history is deliberately absent until M3; absent is never success evidence.
        nodes = {n.node_id: n for n in graph.nodes}
        parents = [
            nodes[e.source].kind
            for e in graph.edges
            if e.target == node.node_id and e.source in nodes
        ]
        children = [
            nodes[e.target].kind
            for e in graph.edges
            if e.source == node.node_id and e.target in nodes
        ]
        graph_features = GraphFeatures(
            parent_node_types=parents,
            child_node_types=children,
            depth=0,
            critical_path=False,
            retry_number=max(0, int(node.labels.get("attempt", "1")) - 1),
        )
        return await store.put_routing_request(
            RoutingContext(  # type: ignore[call-arg]
                contract_id=request_id,
                **header,
                node_contract_ref=NodeContractRef(
                    node_contract_id=node.contract_id, immutable_hash=node.immutable_hash
                ),
                task_features=task_features,
                project_features=project_features,
                graph_features=graph_features,
                available_runtime_snapshot_id=snapshot.available_runtime_snapshot_id,
                capability_registry_snapshot_id=snapshot.capability_registry_snapshot_id,
                connection_availability_snapshot_id=snapshot.connection_availability_snapshot_id,
                policy_snapshot_id=snapshot.policy_snapshot_id,
                workspace_router_version=WORKSPACE_ROUTER_VERSION,
                project_adapter_version=None,
                requested_at=at,
                labels={"run_id": run.run_id, "fallback_digest": snapshot.fallback_bundle_digest},
            )
        )

    async def route(
        self,
        *,
        frozen: FrozenNode,
        snapshot: RoutingSnapshot,
        mode: RoutingMode,
        run: Run,
        _catalog: ConfigurationCatalog | None = None,
    ) -> RoutingDecisionReceipt:
        if mode != RoutingMode.BASELINE_ONLY:
            raise _error("ROUTING_MODE_UNAVAILABLE", "Only BASELINE_ONLY is enabled", 422)
        task = await self.store.get_task(run.task_id)
        if task is None:
            raise _error("ROUTING_INPUT_MISSING", "Task not found", 404)
        await self._authorize(frozen.node_contract.workspace_id, principal_ref_for_run(run))
        catalog = _catalog or await self.catalog_factory(frozen, snapshot, run, task)
        snapshot = replace(snapshot, fallback_bundle_digest=catalog.fallback_bundle.digest)
        request_id = routing_request_id(
            frozen.node_contract.immutable_hash, snapshot, WORKSPACE_ROUTER_VERSION, None, mode
        )
        try:
            async with self.store.routing_transaction(run.run_id) as store:
                await self._authorize(
                    frozen.node_contract.workspace_id,
                    principal_ref_for_run(run),
                    mutate=True,
                    store=store,
                )
                existing = await store.get_routing_receipt_for_request(request_id)
                if existing is not None:
                    return existing
                context = await self._context(store, frozen, snapshot, run, request_id)
                who = frozen.node_contract.created_by
                builder = CandidateBuilder(
                    gate=PolicyGate(
                        CapabilityPolicyEngine(self.granted_permissions),
                        snapshot.policy,
                        created_by=who,
                    ),
                    evaluator=CompatibilityEngine(created_by=who),
                    catalog=catalog,
                    created_by=who,
                )
                built = builder.build(
                    routing_request_id=request_id,
                    node_contract=frozen.node_contract,
                    task=task,
                    principal=who,
                    entitled_workspace_id=frozen.node_contract.workspace_id,
                    snapshot=snapshot,
                    workspace_id=frozen.node_contract.workspace_id,
                    project_id=run.project_id,
                    clock=lambda: context.requested_at,
                )
                objective = await store.get_objective_contract(
                    frozen.objective_ref.objective_contract_id
                )
                if objective is None:
                    raise _error("ROUTING_INPUT_MISSING", "Frozen objective not found", 422)
                # stage-9-gate / stage-11-behavior: later milestones replace the baseline.
                selection = DeterministicSelector().select(
                    built.candidates,
                    built.rejected,
                    verified_success_floor=objective.verified_success_floor,
                    utility_weights=objective.utility_weights,
                    created_at=context.requested_at,
                    created_by=who,
                    workspace_id=context.workspace_id,
                    project_id=context.project_id,
                )
                for decision in built.compatibility_decisions:
                    await store.put_compatibility_decision(decision)
                for candidate in selection.candidates:
                    await store.put_configuration_candidate(candidate)
                selected = selection.selected
                receipt = RoutingDecisionReceipt(  # type: ignore[call-arg]
                    contract_id=derived_id("routing_receipt", request_id),
                    created_at=context.requested_at,
                    created_by=who,
                    workspace_id=context.workspace_id,
                    project_id=context.project_id,
                    objective_contract_ref=frozen.objective_ref,
                    routing_request_id=request_id,
                    node_contract_hash=frozen.node_contract.immutable_hash,
                    selected_configuration_id=selected.configuration.contract_id
                    if selected
                    else None,
                    selected_configuration_hash=selected.configuration.configuration_hash
                    if selected
                    else None,
                    decision_type=selection.decision_type,
                    selection_propensity=1.0,
                    predicted_outcomes=selected.predicted if selected else None,
                    uncertainty=UncertaintySummary(
                        epistemic_uncertainty=selected.uncertainty_score if selected else 1,
                        lower_confidence_success=selected.lower_confidence_success
                        if selected
                        else 0,
                        calibration_version="cold-start-prior/1",
                    ),
                    candidate_summary_refs=[c.contract_id for c in selection.candidates],
                    rejected_candidate_reasons=list(built.rejected),
                    workspace_router_version=WORKSPACE_ROUTER_VERSION,
                    project_adapter_version=None,
                    objective_contract_version=frozen.objective_ref.revision,
                    capability_registry_snapshot_id=snapshot.capability_registry_snapshot_id,
                    policy_snapshot_id=snapshot.policy_snapshot_id,
                    fallback_configuration_id=next(
                        (
                            c.configuration.contract_id
                            for c in selection.candidates
                            if c.fallback_eligible
                        ),
                        None,
                    ),
                    explanation=selection.explanation,
                    labels={
                        "run_id": run.run_id,
                        "decision_version": "1",
                        "routing_status": "READY",
                    },
                )
                receipt = await store.put_routing_receipt(receipt)
                await self._event(
                    store, run, receipt, "created", EventType.ROUTING_DECISION_CREATED
                )
                # post-route-shadow / active-version: intentionally inert until M6/M8.
                return receipt
        except ValueError as exc:
            raise _error("RECEIPT_VERSION_CONFLICT", "Routing records conflict") from exc

    async def _event(
        self,
        store: StateStore,
        run: Run,
        receipt: RoutingDecisionReceipt,
        action: str,
        event_type: EventType,
    ) -> None:
        await store.append_event(
            AgentEvent(
                event_id=derived_id("event", receipt.contract_id, action),
                run_id=run.run_id,
                session_id=run.session_id or run.run_id,
                provider=run.provider,
                native_type="accretion/routing/" + action,
                normalized_type=event_type,
                correlation_id=run.run_id,
                causation_id=receipt.contract_id,
                adapter_version="routing/1",
                timestamp=self.clock(),
                payload={
                    "receipt_id": receipt.contract_id,
                    "node_contract_hash": receipt.node_contract_hash,
                    "action": action,
                },
            )
        )

    async def get_receipt(
        self, *, receipt_id: str, principal: PrincipalRef
    ) -> RoutingDecisionReceipt:
        receipt = await self.store.get_routing_receipt(receipt_id)
        if receipt is None:
            raise _error("RECEIPT_NOT_FOUND", "Routing resource not found", 404)
        await self._authorize(receipt.workspace_id, principal)
        return receipt

    async def candidates_for(
        self, *, receipt_id: str, principal: PrincipalRef
    ) -> list[ConfigurationCandidate]:
        receipt = await self.get_receipt(receipt_id=receipt_id, principal=principal)
        return await self._candidates(self.store, receipt)

    async def _candidates(
        self, store: StateStore, receipt: RoutingDecisionReceipt
    ) -> list[ConfigurationCandidate]:
        result = []
        for candidate_id in receipt.candidate_summary_refs:
            candidate = await store.get_configuration_candidate(candidate_id)
            if (
                candidate is None
                or candidate.workspace_id != receipt.workspace_id
                or candidate.project_id != receipt.project_id
            ):
                raise _error("ROUTING_RECORD_INVALID", "Candidate record is unavailable")
            result.append(candidate)
        return result

    async def configuration_for(self, receipt: RoutingDecisionReceipt) -> ExecutionConfiguration:
        persisted = await self.store.get_routing_receipt(receipt.contract_id)
        if persisted is None or persisted.content_hash != receipt.content_hash:
            raise _error("DISPATCH_WITHOUT_RECEIPT", "A persisted matching receipt is required")
        for candidate in await self._candidates(self.store, persisted):
            if (
                candidate.hard_eligible
                and candidate.configuration.contract_id == persisted.selected_configuration_id
                and candidate.configuration.configuration_hash
                == persisted.selected_configuration_hash
            ):
                return ExecutionConfiguration.model_validate(candidate.configuration.model_dump())
        raise _error(
            "CANDIDATE_NOT_ELIGIBLE", "Receipt has no eligible selected configuration", 422
        )

    async def latest_receipt(
        self, *, frozen: FrozenNode, run: Run
    ) -> RoutingDecisionReceipt | None:
        receipts = await self.store.list_routing_receipts_for_run_graph(
            workspace_id=frozen.node_contract.workspace_id,
            run_graph_id=frozen.node_contract.run_graph_id,
        )
        matches = [
            r for r in receipts if r.node_contract_hash == frozen.node_contract.immutable_hash
        ]
        superseded = {r.supersedes_contract_id for r in matches}
        heads = [r for r in matches if r.contract_id not in superseded]
        return max(heads, key=lambda r: (r.created_at, r.contract_id)) if heads else None

    async def _run_for(self, receipt: RoutingDecisionReceipt) -> Run:
        context = await self.store.get_routing_request(receipt.routing_request_id)
        node = (
            await self.store.get_node_contract(context.node_contract_ref.node_contract_id)
            if context
            else None
        )
        run = await self.store.get_run(node.labels.get("run_id", "")) if node else None
        graph = await self.store.get_run_graph(run.run_id) if run else None
        if (
            run is None
            or node is None
            or graph is None
            or graph.run_graph_id != node.run_graph_id
            or node.immutable_hash != receipt.node_contract_hash
            or run.project_id != receipt.project_id
        ):
            raise _error("RECEIPT_NOT_FOUND", "Routing resource not found", 404)
        return run

    async def _assert_amendable(
        self, store: StateStore, receipt: RoutingDecisionReceipt, run: Run
    ) -> None:
        receipts = await store.list_routing_receipts(
            workspace_id=receipt.workspace_id, project_id=receipt.project_id
        )
        if any(r.supersedes_contract_id == receipt.contract_id for r in receipts):
            raise _error("RECEIPT_VERSION_CONFLICT", "A newer routing decision exists")
        if receipt.labels.get("routing_status") == "CANCELLED":
            raise _error("RECEIPT_CANCELLED", "Routing decision was cancelled")
        events = await store.list_events(run.run_id)
        if any(
            e.native_type == "accretion/routing/dispatch" and e.causation_id == receipt.contract_id
            for e in events
        ):
            raise _error(
                "RECEIPT_ALREADY_DISPATCHED",
                "Routing decision has already been claimed for dispatch",
            )

    async def claim_dispatch(
        self, *, receipt: RoutingDecisionReceipt, run: Run
    ) -> ExecutionConfiguration:
        configuration = await self.configuration_for(receipt)
        owner = await self._run_for(receipt)
        if owner.run_id != run.run_id:
            raise _error("DISPATCH_WITHOUT_RECEIPT", "Receipt belongs to another execution")
        await self._authorize(receipt.workspace_id, principal_ref_for_run(run))
        runtime = self.runtimes.get(configuration.runtime.provider)
        health = await runtime.health() if runtime else None
        if health is None or health.runtime_version != configuration.runtime.adapter_version:
            raise _error("RUNTIME_VERSION_DRIFT", "Runtime changed after routing")
        async with self.store.routing_transaction(run.run_id) as store:
            await self._authorize(
                receipt.workspace_id, principal_ref_for_run(run), mutate=True, store=store
            )
            await self._assert_amendable(store, receipt, run)
            await self._event(store, run, receipt, "dispatch", EventType.ROUTING_DECISION_CREATED)
        return configuration

    async def override(
        self,
        *,
        receipt_id: str,
        candidate_id: str,
        reason_code: str,
        reason: str,
        expected_receipt_version: int,
        principal: PrincipalRef,
    ) -> RoutingDecisionReceipt:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", reason_code) or not reason.strip():
            raise _error(
                "OVERRIDE_REASON_INVALID", "A structured reason and explanation are required", 422
            )
        return await self._amend(
            receipt_id=receipt_id,
            principal=principal,
            candidate_id=candidate_id,
            reason_code=reason_code,
            reason=reason,
            expected_version=expected_receipt_version,
        )

    async def cancel(self, *, receipt_id: str, principal: PrincipalRef) -> RoutingDecisionReceipt:
        return await self._amend(
            receipt_id=receipt_id,
            principal=principal,
            candidate_id=None,
            reason_code="CANCELLED",
            reason="Operator cancelled before dispatch",
            expected_version=None,
        )

    async def _amend(
        self,
        *,
        receipt_id: str,
        principal: PrincipalRef,
        candidate_id: str | None,
        reason_code: str,
        reason: str,
        expected_version: int | None,
    ) -> RoutingDecisionReceipt:
        original = await self.get_receipt(receipt_id=receipt_id, principal=principal)
        run = await self._run_for(original)
        version = int(original.labels.get("decision_version", "1"))
        if expected_version is not None and expected_version != version:
            raise _error("RECEIPT_VERSION_CONFLICT", "Routing decision version changed")
        request_id = derived_id(
            "routing_request",
            original.routing_request_id,
            candidate_id or "cancel",
            principal.principal_id,
            reason_code,
            reason,
        )
        try:
            async with self.store.routing_transaction(run.run_id) as store:
                await self._authorize(original.workspace_id, principal, mutate=True, store=store)
                existing = await store.get_routing_receipt_for_request(request_id)
                if existing is not None:
                    return existing
                await self._assert_amendable(store, original, run)
                candidates = await self._candidates(store, original)
                selected = next(
                    (c for c in candidates if c.contract_id == candidate_id and c.hard_eligible),
                    None,
                )
                if candidate_id is not None and selected is None:
                    raise _error(
                        "CANDIDATE_NOT_ELIGIBLE", "Candidate is not in the eligible set", 422
                    )
                context = await store.get_routing_request(original.routing_request_id)
                if context is None:
                    raise _error("ROUTING_RECORD_INVALID", "Routing context is unavailable")
                at = self.clock()
                payload = context.model_dump(mode="python")
                payload.update(
                    contract_id=request_id,
                    content_hash="",
                    supersedes_contract_id=context.contract_id,
                    created_at=at,
                    requested_at=at,
                    created_by=principal,
                )
                await store.put_routing_request(RoutingContext.model_validate(payload))
                header = dict(
                    created_at=at,
                    created_by=principal,
                    workspace_id=original.workspace_id,
                    project_id=original.project_id,
                    objective_contract_ref=original.objective_contract_ref,
                )
                receipt_id_new = derived_id("routing_receipt", request_id)
                payload = original.model_dump(mode="python")
                payload.update(
                    **header,
                    contract_id=receipt_id_new,
                    content_hash="",
                    routing_request_id=request_id,
                    supersedes_contract_id=original.contract_id,
                    selected_configuration_id=selected.configuration.contract_id
                    if selected
                    else None,
                    selected_configuration_hash=selected.configuration.configuration_hash
                    if selected
                    else None,
                    decision_type=DecisionType.HUMAN_OVERRIDE
                    if selected
                    else DecisionType.HUMAN_REVIEW_REQUIRED,
                    predicted_outcomes=selected.predicted if selected else None,
                    explanation=StructuredExplanation(  # type: ignore[call-arg]
                        contract_id=receipt_id_new + "-why",
                        **header,
                        summary="Operator selected an eligible configuration"
                        if selected
                        else "Operator cancelled routing",
                        factors=[],
                        rejected_candidates=[],
                    ),
                    labels={
                        "run_id": run.run_id,
                        "decision_version": str(version + 1),
                        "routing_status": "READY" if selected else "CANCELLED",
                    },
                )
                amended = RoutingDecisionReceipt.model_validate(payload)
                if selected:
                    await store.put_routing_override(
                        override_id=derived_id("routing_override", request_id),
                        workspace_id=original.workspace_id,
                        project_id=original.project_id,
                        receipt_id=original.contract_id,
                        principal_id=principal.principal_id,
                        candidate_id=selected.contract_id,
                        reason_code=reason_code,
                        reason=reason,
                        superseding_receipt_id=amended.contract_id,
                        created_at=at,
                    )
                await store.put_routing_receipt(amended)
                await self._event(
                    store,
                    run,
                    amended,
                    "override" if selected else "cancel",
                    EventType.ROUTING_OVERRIDE_RECORDED,
                )
                return amended
        except ValueError as exc:
            raise _error("RECEIPT_VERSION_CONFLICT", "Routing records conflict") from exc

    async def route_execution(
        self,
        *,
        project_id: str,
        execution_instance_id: str,
        routing_request_id: str,
        node_contract_id: str,
        expected_node_contract_hash: str,
        mode: RoutingMode,
        expected_registry_snapshot_id: str,
        principal: PrincipalRef,
    ) -> RoutingDecisionReceipt:
        node = await self.store.get_node_contract(node_contract_id)
        if (
            node is None
            or node.project_id != project_id
            or node.execution_instance_id != execution_instance_id
        ):
            raise _error("RECEIPT_NOT_FOUND", "Routing resource not found", 404)
        await self._authorize(node.workspace_id, principal)
        if node.immutable_hash != expected_node_contract_hash:
            raise _error("RECEIPT_VERSION_CONFLICT", "Node contract hash changed")
        if mode != RoutingMode.BASELINE_ONLY:
            raise _error("ROUTING_MODE_UNAVAILABLE", "Only BASELINE_ONLY is enabled", 422)
        existing = await self.replay(routing_request_id)
        if existing is not None:
            if (
                existing.node_contract_hash != node.immutable_hash
                or existing.capability_registry_snapshot_id != expected_registry_snapshot_id
            ):
                raise _error(
                    "RECEIPT_VERSION_CONFLICT", "Request does not match its stored receipt"
                )
            return existing
        run = await self.store.get_run(node.labels.get("run_id", ""))
        task = await self.store.get_task(run.task_id) if run else None
        spec = await self.store.get_verification_spec(
            node.verification_spec_ref.verification_spec_id
        )
        if run is None or task is None or spec is None or node.objective_contract_ref is None:
            raise _error("ROUTING_INPUT_MISSING", "Frozen execution inputs are unavailable", 422)
        frozen = FrozenNode(node, spec, node.objective_contract_ref, node.execution_instance_id)
        snapshot = await self.snapshot(
            workspace_id=node.workspace_id, project_id=project_id, task=task
        )
        catalog = await self.catalog_factory(frozen, snapshot, run, task)
        snapshot = replace(snapshot, fallback_bundle_digest=catalog.fallback_bundle.digest)
        from accretion.routing.identity import routing_request_id as derive_request

        expected = derive_request(
            node.immutable_hash, snapshot, WORKSPACE_ROUTER_VERSION, None, mode
        )
        if (
            expected != routing_request_id
            or snapshot.capability_registry_snapshot_id != expected_registry_snapshot_id
        ):
            raise _error(
                "RECEIPT_VERSION_CONFLICT", "Registry snapshot or routing identity changed"
            )
        return await self.route(
            frozen=frozen, snapshot=snapshot, mode=mode, run=run, _catalog=catalog
        )
