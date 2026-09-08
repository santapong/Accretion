"""Receipt-first routing over injectable §9.4 stages, and append-only operator decisions.

M2 built this service as one method with two seam comments in it. M5 replaces the comments
with the collaborators in :mod:`accretion.routing.stages`: who is routing
(:class:`~accretion.routing.stages.ActiveVersionResolver`), what history there is
(:class:`~accretion.routing.stages.EvidenceRetriever`), what the candidates are worth
(:class:`~accretion.routing.stages.CandidateScorer`), which of them is actually taken
(:class:`~accretion.routing.stages.BehaviorPolicy`) and who is told afterwards
(:class:`~accretion.routing.stages.PostRouteHook`). The defaults reproduce M2 exactly, so
this milestone changes no stored receipt for a workspace that has promoted nothing.

**Why the mode gate moved out of the API and into here.** M2 rejected any mode but
``BASELINE_ONLY`` at the HTTP boundary, which was right while there was nothing else to run.
It is wrong now: whether ``AUTO`` is available is a property of how the service was
*assembled* — a scorer was injected or it was not — and a route handler cannot see that. The
gate is therefore one question asked in one place, and the answer is the same whether the
caller arrived over HTTP or through the run manager.

**Why the four §12 events are emitted here and not by the caller.** ``ROUTING_REQUESTED``,
``ROUTING_CANDIDATES_BUILT``, ``ROUTING_FALLBACK_SELECTED`` and
``ROUTING_HUMAN_REVIEW_REQUIRED`` are all statements about steps that happen *inside* the
routing transaction, and three of them are about outcomes the caller cannot observe without
re-deriving them from the receipt. Emitting them here also puts them inside the same
transaction as the receipt, so a run's event log cannot claim a decision the store does not
hold. Their payloads are ids and counts only: an objective can carry anything a user typed,
including a credential, and §17's event log is read by more eyes than the contract store is.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

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
    RuntimeStatus,
    Task,
    WorkflowNodeSpec,
    WorkflowTemplate,
    WorkspaceRole,
)
from accretion.contracts.canonical import content_hash
from accretion.contracts.routing import (
    ConfigurationCandidate,
    DecisionType,
    ExecutionConfiguration,
    ExperienceRecord,
    NodeContract,
    NodeContractRef,
    ObjectiveContract,
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
from accretion.routing.flags import FULL, RouterFeatureFlags
from accretion.routing.freeze import NodeContractFreezer
from accretion.routing.gates import PolicyGate
from accretion.routing.graph_features import graph_features
from accretion.routing.identity import principal_ref_for_run, routing_request_id, workspace_for_run
from accretion.routing.protocols import FrozenNode, RoutingMode
from accretion.routing.selector import (
    COLD_START_PRIOR_METHOD,
    DETERMINISTIC_PROPENSITY,
    DeterministicSelector,
    SelectionResult,
)
from accretion.routing.snapshot import RegistrySnapshotBuilder, RoutingSnapshot
from accretion.routing.stages import (
    ADAPTER_UNAVAILABLE,
    DEGRADED_LABEL,
    EVIDENCE_UNAVAILABLE,
    VOCABULARY_MISMATCH,
    WORKSPACE_MODEL_UNAVAILABLE,
    ActiveVersionResolver,
    ActiveVersions,
    BehaviorPolicy,
    CandidateScorer,
    DeterministicBehavior,
    EvidenceRetriever,
    NoEvidence,
    PostNodeHook,
    PostRouteHook,
    ScoredSlate,
    StatusActiveVersionResolver,
    node_signature,
    worst_degradation,
)

CatalogFactory = Callable[[FrozenNode, RoutingSnapshot, Run, Task], Awaitable[ConfigurationCatalog]]

_LOGGER = logging.getLogger(__name__)

_DECISION_EVENTS: Mapping[DecisionType, tuple[str, EventType]] = {
    DecisionType.FALLBACK: ("fallback", EventType.ROUTING_FALLBACK_SELECTED),
    DecisionType.HUMAN_REVIEW_REQUIRED: (
        "human-review",
        EventType.ROUTING_HUMAN_REVIEW_REQUIRED,
    ),
}
"""The two §12 outcome events, keyed by the decision that causes them.

A mapping and not two ``if``s, because these are the two decision types that mean "no
learned or ranked choice was made" and a third one added later must be a *decision* to leave
it unannounced rather than an omission nobody notices. ``EXPLOIT``, ``EXPLORE`` and the two
human decisions are already covered by ``ROUTING_DECISION_CREATED`` and
``ROUTING_OVERRIDE_RECORDED``.
"""


DETERMINISTIC_VERSIONS = ActiveVersions(
    router_version_id=None,
    adapter_version_id=None,
    router_label=WORKSPACE_ROUTER_VERSION,
    adapter_label=None,
)
"""The audited deterministic router, named, as the answer to "who decided this".

A constant rather than four literals at three call sites, because these labels go into
:func:`~accretion.routing.identity.routing_request_id` and a fifth spelling of the same
thing would silently mint a second family of request ids for the same decisions.
"""


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
        active_versions: ActiveVersionResolver | None = None,
        evidence: EvidenceRetriever | None = None,
        scorer: CandidateScorer | None = None,
        behavior: BehaviorPolicy | None = None,
        post_route: Sequence[PostRouteHook] = (),
        post_node: Sequence[PostNodeHook] = (),
        default_mode: RoutingMode = RoutingMode.BASELINE_ONLY,
        flags: RouterFeatureFlags = FULL,
    ) -> None:
        self.store = store
        self.snapshots = snapshots
        self.catalog_factory = catalog_factory
        self.runtimes = runtimes
        self.granted_permissions = granted_permissions or set()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.active_versions = active_versions or StatusActiveVersionResolver(store)
        self.evidence = evidence or NoEvidence()
        # The one collaborator with no inert default. `None` here is not "score with the
        # prior", it is "this deployment has no learned scorer", and that is the fact the
        # mode gate reads: a service that silently substituted a stand-in would accept AUTO
        # and route deterministically under a receipt claiming otherwise.
        self.scorer = scorer
        # Protocol §14, read at three collaborator sites and nowhere else. `FULL` is every
        # component present, so a deployment that names no flags constructs the collaborators
        # it constructed before this existed, retrieves the same evidence, scores the same
        # slate and writes receipts with no additional label — which is what the M5, M6 and
        # M7 suites assert by passing none of this.
        #
        # A7 is enforced here rather than at the call site because "no guarded exploration"
        # is a statement about which behaviour policy exists, not about whether a policy that
        # exists is consulted: an injected bandit that were merely skipped would still be
        # holding a store handle and a ledger, and a later refactor that consulted it would
        # be a one-line silent regression.
        self.flags = flags
        self.behavior = (
            (behavior or DeterministicBehavior())
            if flags.guarded_exploration
            else DeterministicBehavior()
        )
        self.post_route = tuple(post_route)
        self.post_node = tuple(post_node)
        self._default_mode = default_mode

    @property
    def default_mode(self) -> RoutingMode:
        """The mode a caller that expresses no preference is routed under (§11.1).

        Read-only, because §11.1 makes the mode a workspace's earned position in a
        progression and a caller that could reassign it at run time could put a workspace
        into ``AUTO`` without the shadow evaluation that is the only way to reach it.
        """

        return self._default_mode

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
        *,
        versions: ActiveVersions,
        observed_task_count: int,
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
            observed_task_count=observed_task_count,
        )
        # `observed_task_count` is a count of retrieved records and every other project
        # aggregate stays absent. That asymmetry is deliberate: a count of observations is a
        # fact the retriever established, while a mean over them would be an aggregate this
        # layer computed without the eligibility rules §10.1 puts around one. Absent is
        # never success evidence, and zero would be a claim.
        structural_features = graph_features(
            graph, node.node_id, int(node.labels.get("attempt", "1"))
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
                graph_features=structural_features,
                available_runtime_snapshot_id=snapshot.available_runtime_snapshot_id,
                capability_registry_snapshot_id=snapshot.capability_registry_snapshot_id,
                connection_availability_snapshot_id=snapshot.connection_availability_snapshot_id,
                policy_snapshot_id=snapshot.policy_snapshot_id,
                workspace_router_version=versions.router_label,
                project_adapter_version=versions.adapter_label,
                requested_at=at,
                labels={"run_id": run.run_id, "fallback_digest": snapshot.fallback_bundle_digest},
            )
        )

    def _assert_mode_available(self, mode: RoutingMode) -> None:
        """§11.1: a learned mode is available exactly when a learned scorer was injected.

        Asked as one question in one place so that the HTTP route, the run manager and a
        direct caller cannot disagree about which modes this process supports. The refusal
        names the mode rather than the missing collaborator: a client has no business
        knowing how the service was assembled, only that the regime it asked for is not one
        this deployment can honour.
        """

        if mode is not RoutingMode.BASELINE_ONLY and self.scorer is None:
            raise _error(
                "ROUTING_MODE_UNAVAILABLE",
                f"routing mode {mode.value} requires a learned scorer that is not configured",
                422,
            )

    async def _versions(
        self, mode: RoutingMode, *, workspace_id: str, project_id: str
    ) -> ActiveVersions:
        """Who §11.1 permits to decide under ``mode``, which is what the request id pins.

        Outside ``AUTO`` nothing learned is consulted, so the answer is the audited
        deterministic router and the resolver is not asked at all. That is not an
        optimisation. The request id derives from these labels, so consulting the resolver
        here would make *promoting a model* change every future request id — and therefore
        stop every stored receipt from replaying — for a workspace that had not turned
        learned routing on.
        """

        if mode is not RoutingMode.AUTO:
            return DETERMINISTIC_VERSIONS
        return await self.active_versions.resolve(workspace_id=workspace_id, project_id=project_id)

    @staticmethod
    def _attributed(versions: ActiveVersions, degraded: str | None) -> ActiveVersions:
        """Who actually decided, which under §15.1 is not always who was in force.

        A receipt's ``workspace_router_version`` is an attribution: it is the answer to
        "which model do I hold responsible for this decision", and §10.2's evaluation reads
        it as one. A prior that could not be loaded, or one whose vocabulary did not match,
        decided nothing — so naming it would attribute a deterministic fallback's outcomes
        to a model that never ran, which is the one way an offline evaluation can be wrong
        without being detectably wrong.

        The routing *context* keeps the in-force versions unchanged, because §8.3 makes it
        the snapshot of the inputs and what was in force is an input. The receipt records
        the outcome. The two differing is the record of a degradation, not a contradiction.
        """

        if degraded in (WORKSPACE_MODEL_UNAVAILABLE, VOCABULARY_MISMATCH):
            return DETERMINISTIC_VERSIONS
        if degraded == ADAPTER_UNAVAILABLE:
            return replace(versions, adapter_version_id=None, adapter_label=None)
        return versions

    async def route(
        self,
        *,
        frozen: FrozenNode,
        snapshot: RoutingSnapshot,
        mode: RoutingMode,
        run: Run,
        excluded_configuration_hashes: Sequence[str] = (),
        _catalog: ConfigurationCatalog | None = None,
    ) -> RoutingDecisionReceipt:
        self._assert_mode_available(mode)
        task = await self.store.get_task(run.task_id)
        if task is None:
            raise _error("ROUTING_INPUT_MISSING", "Task not found", 404)
        persisted_node = await self.store.get_node_contract(frozen.node_contract.contract_id)
        persisted_spec = await self.store.get_verification_spec(
            frozen.verification_spec.contract_id
        )
        graph = await self.store.get_run_graph(run.run_id)
        if (
            persisted_node is None
            or persisted_spec is None
            or graph is None
            or persisted_node.content_hash != frozen.node_contract.content_hash
            or persisted_spec.content_hash != frozen.verification_spec.content_hash
            or persisted_node.verification_spec_ref.content_hash != persisted_spec.content_hash
            or persisted_node.run_graph_id != graph.run_graph_id
            or persisted_node.project_id != run.project_id
        ):
            raise _error(
                "ROUTING_INPUT_INVALID", "Matching persisted frozen inputs are required", 422
            )
        await self._authorize(frozen.node_contract.workspace_id, principal_ref_for_run(run))
        catalog = _catalog or await self.catalog_factory(frozen, snapshot, run, task)
        snapshot = replace(snapshot, fallback_bundle_digest=catalog.fallback_bundle.digest)
        versions = await self._versions(
            mode,
            workspace_id=frozen.node_contract.workspace_id,
            project_id=run.project_id,
        )
        request_id = routing_request_id(
            frozen.node_contract.immutable_hash,
            snapshot,
            versions.router_label,
            versions.adapter_label,
            mode,
        )
        committed: tuple[RoutingDecisionReceipt, RoutingContext, ScoredSlate] | None = None
        try:
            transaction = (
                self.store.routing_transaction(
                    run.run_id,
                    budget_key=(
                        frozen.node_contract.workspace_id,
                        frozen.node_contract.node_kind.value,
                    ),
                )
                if mode is RoutingMode.AUTO
                else self.store.routing_transaction(run.run_id)
            )
            async with transaction as store:
                await self._authorize(
                    frozen.node_contract.workspace_id,
                    principal_ref_for_run(run),
                    mutate=True,
                    store=store,
                )
                existing = await store.get_routing_receipt_for_request(request_id)
                if existing is not None:
                    return existing
                history = [
                    r
                    for r in await store.list_routing_receipts_for_run_graph(
                        workspace_id=frozen.node_contract.workspace_id,
                        run_graph_id=frozen.node_contract.run_graph_id,
                    )
                    if r.node_contract_hash == frozen.node_contract.immutable_hash
                ]
                superseded = {r.supersedes_contract_id for r in history}
                heads = [r for r in history if r.contract_id not in superseded]
                if len(heads) > 1:
                    raise _error("RECEIPT_VERSION_CONFLICT", "Routing history has competing heads")
                predecessor = heads[0] if heads else None
                if predecessor:
                    await self._assert_amendable(store, predecessor, run)
                await self._event(
                    store,
                    run,
                    "requested",
                    EventType.ROUTING_REQUESTED,
                    causation_id=request_id,
                    payload={
                        "routing_request_id": request_id,
                        "node_contract_hash": frozen.node_contract.immutable_hash,
                        "mode": mode.value,
                        "workspace_router_version": versions.router_label,
                    },
                )
                records, evidence_degraded = await self._retrieve(frozen, run)
                context = await self._context(
                    store,
                    frozen,
                    snapshot,
                    run,
                    request_id,
                    versions=versions,
                    observed_task_count=len(records),
                )
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
                    flags=self.flags,
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
                    excluded_configuration_hashes=excluded_configuration_hashes,
                )
                await self._event(
                    store,
                    run,
                    "candidates-built",
                    EventType.ROUTING_CANDIDATES_BUILT,
                    causation_id=request_id,
                    payload={
                        "routing_request_id": request_id,
                        "candidate_count": len(built.candidates),
                        "rejected_count": len(built.rejected),
                    },
                )
                objective = await store.get_objective_contract(
                    frozen.objective_ref.objective_contract_id
                )
                if objective is None:
                    raise _error("ROUTING_INPUT_MISSING", "Frozen objective not found", 422)
                slate = await self._score(
                    mode,
                    context=context,
                    candidates=built.candidates,
                    node=frozen.node_contract,
                    objective=objective,
                    versions=versions,
                    records=records,
                )
                baseline = DeterministicSelector().select(
                    slate.candidates,
                    built.rejected,
                    verified_success_floor=objective.verified_success_floor,
                    utility_weights=objective.utility_weights,
                    created_at=context.requested_at,
                    created_by=who,
                    workspace_id=context.workspace_id,
                    project_id=context.project_id,
                )
                selection, propensity, behavior_labels = await self._behave(
                    mode,
                    store=store,
                    context=context,
                    slate=slate,
                    baseline=baseline,
                    node=frozen.node_contract,
                    objective=objective,
                    snapshot=snapshot,
                )
                for decision in built.compatibility_decisions:
                    await store.put_compatibility_decision(decision)
                for candidate in selection.candidates:
                    await store.put_configuration_candidate(candidate)
                selected = selection.selected
                labels = self._receipt_labels(
                    run=run,
                    predecessor=predecessor,
                    slate=slate,
                    behavior_labels=behavior_labels,
                    evidence_degraded=evidence_degraded,
                )
                attributed = self._attributed(versions, labels.get(DEGRADED_LABEL))
                receipt = RoutingDecisionReceipt(  # type: ignore[call-arg]
                    contract_id=derived_id("routing_receipt", request_id),
                    supersedes_contract_id=predecessor.contract_id if predecessor else None,
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
                    selection_propensity=propensity,
                    predicted_outcomes=selected.predicted if selected else None,
                    uncertainty=UncertaintySummary(
                        epistemic_uncertainty=selected.uncertainty_score if selected else 1,
                        lower_confidence_success=selected.lower_confidence_success
                        if selected
                        else 0,
                        calibration_version=slate.calibration_version,
                    ),
                    candidate_summary_refs=[c.contract_id for c in selection.candidates],
                    rejected_candidate_reasons=list(built.rejected),
                    experience_refs=list(slate.evidence_ids),
                    workspace_router_version=attributed.router_label,
                    project_adapter_version=attributed.adapter_label,
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
                    labels=labels,
                )
                receipt = await store.put_routing_receipt(receipt)
                await self._receipt_event(
                    store, run, receipt, "created", EventType.ROUTING_DECISION_CREATED
                )
                outcome_event = _DECISION_EVENTS.get(selection.decision_type)
                if outcome_event is not None:
                    await self._receipt_event(store, run, receipt, *outcome_event)
                committed = (receipt, context, slate)
        except ValueError as exc:
            raise _error("RECEIPT_VERSION_CONFLICT", "Routing records conflict") from exc
        if committed is None:
            # Only reachable when the transaction body returned the replayed receipt, which
            # it does by returning directly; keeping the fallthrough typed rather than
            # asserting means a future edit that stops assigning `committed` degrades to
            # "no hooks ran" instead of to an AttributeError inside a committed decision.
            raise _error("ROUTING_RECORD_INVALID", "Routing decision was not committed")
        receipt, context, slate = committed
        for hook in self.post_route:
            try:
                await hook.after_receipt(
                    run=run,
                    frozen=frozen,
                    snapshot=snapshot,
                    context=context,
                    receipt=receipt,
                    slate=slate,
                )
            except Exception:
                # The receipt is committed and has been promised to the caller. A shadow
                # recorder that failed must not turn a durable decision into an error the
                # caller would retry — the retry would replay to this very receipt.
                _LOGGER.exception(
                    "post-route hook %s failed for receipt %s",
                    type(hook).__name__,
                    receipt.contract_id,
                )
        return receipt

    async def _retrieve(
        self, frozen: FrozenNode, run: Run
    ) -> tuple[list[ExperienceRecord], str | None]:
        """SDD §9.4 stage 3, and §15.1's answer when it fails: nothing, said out loud.

        Returns the records and the degradation to record, so that "there was no history"
        and "there may have been history and we could not read it" reach the receipt as
        different statements. Fabricating the second as the first is exactly what §15.1
        forbids, and it is the failure a caller cannot detect afterwards.
        """

        if not self.flags.experience_retrieval:
            # §14 A3, reported on the §15.1 ladder as `EVIDENCE_UNAVAILABLE` and not as an
            # empty history. The consequence is the one that entry names — the decision was
            # made without history — and it is the consequence, not the cause, that a reader
            # of the receipt has to be able to trust. Returning `None` here would let the
            # receipt claim the store was consulted and had nothing, which is the false
            # statement §15.1 exists to forbid.
            return [], EVIDENCE_UNAVAILABLE
        try:
            records = await self.evidence.retrieve(
                workspace_id=frozen.node_contract.workspace_id,
                project_id=run.project_id,
                signature=node_signature(
                    frozen.node_contract,
                    objective_digest=frozen.objective_ref.objective_contract_hash,
                ),
                principal=principal_ref_for_run(run),
                as_of=self.clock(),
            )
        except Exception:
            _LOGGER.exception(
                "experience retrieval failed for node %s; routing without history",
                frozen.node_contract.contract_id,
            )
            return [], EVIDENCE_UNAVAILABLE
        return list(records), None

    async def _score(
        self,
        mode: RoutingMode,
        *,
        context: RoutingContext,
        candidates: Sequence[ConfigurationCandidate],
        node: NodeContract,
        objective: ObjectiveContract,
        versions: ActiveVersions,
        records: Sequence[ExperienceRecord],
    ) -> ScoredSlate:
        """§9.3 outcome estimation, consulted in ``AUTO`` only.

        ``SHADOW`` deliberately does *not* score here. A shadow decision is recorded by a
        :class:`~accretion.routing.stages.PostRouteHook` alongside the baseline that actually
        executed (M6), so scoring in the main path would make the shadow router's choice the
        one the receipt attributes the run to — which is the one thing §11.1 says a shadow
        must never be.
        """

        if mode is not RoutingMode.AUTO or self.scorer is None:
            return ScoredSlate(
                candidates=tuple(candidates),
                calibration_version=COLD_START_PRIOR_METHOD,
                evidence_ids=(),
                labels=self.flags.labels(),
            )
        scored = await self.scorer.score(
            context=context,
            candidates=candidates,
            node=node,
            objective=objective,
            versions=versions,
            # Every candidate is scored against the whole retrieved body rather than
            # against the records that name its own hash: §7.10 makes the *signature* the
            # retrieval key, so the evidence that matters to a candidate includes runs of
            # sibling configurations under the same signature, and a per-hash split would
            # hide all of it.
            evidence_by_hash={
                candidate.configuration.configuration_hash: records for candidate in candidates
            },
        )
        # §14 A4 and A9 reach the receipt through the slate's labels, which is the channel
        # §15.1 already uses to say what a decision was made without. The scorer's own labels
        # win a collision: they describe what happened during this scoring, while these
        # describe the configuration it happened under, and a run that degraded for a reason
        # of its own should say so rather than be overwritten by a flag.
        ablation = self.flags.labels()
        if not ablation:
            return scored
        return replace(scored, labels={**ablation, **scored.labels})

    async def _behave(
        self,
        mode: RoutingMode,
        *,
        store: StateStore,
        context: RoutingContext,
        slate: ScoredSlate,
        baseline: SelectionResult,
        node: NodeContract,
        objective: ObjectiveContract,
        snapshot: RoutingSnapshot,
    ) -> tuple[SelectionResult, float, Mapping[str, str]]:
        """§9.1 stage 11. Outside ``AUTO`` the deterministic choice is the taken action.

        The propensity returned outside ``AUTO`` is 1.0 and is a measurement rather than a
        default: the policy that acted had one admissible action and took it.
        """

        if mode is not RoutingMode.AUTO:
            return baseline, DETERMINISTIC_PROPENSITY, {}
        decision = await self.behavior.select(
            store=store,
            context=context,
            slate=slate,
            baseline=baseline,
            node=node,
            objective=objective,
            snapshot=snapshot,
        )
        return decision.selection, decision.propensity, decision.labels

    @staticmethod
    def _receipt_labels(
        *,
        run: Run,
        predecessor: RoutingDecisionReceipt | None,
        slate: ScoredSlate,
        behavior_labels: Mapping[str, str],
        evidence_degraded: str | None,
    ) -> dict[str, str]:
        """M2's three labels, plus whatever the stages had to say, with one ``degraded`` key.

        The stage labels are merged *under* the three the service owns, so no collaborator
        can rewrite ``run_id``, ``decision_version`` or ``routing_status`` — those three are
        how the amendment chain and the dispatch gate find this receipt, and a scorer that
        could set them could detach a decision from its own history.
        """

        degraded = worst_degradation(
            slate.labels.get(DEGRADED_LABEL),
            behavior_labels.get(DEGRADED_LABEL),
            evidence_degraded,
        )
        labels = {**dict(slate.labels), **dict(behavior_labels)}
        labels.pop(DEGRADED_LABEL, None)
        if degraded is not None:
            labels[DEGRADED_LABEL] = degraded
        labels.update(
            run_id=run.run_id,
            decision_version=str(int(predecessor.labels.get("decision_version", "1")) + 1)
            if predecessor
            else "1",
            routing_status="READY",
        )
        return labels

    async def _receipt_event(
        self,
        store: StateStore,
        run: Run,
        receipt: RoutingDecisionReceipt,
        action: str,
        event_type: EventType,
    ) -> None:
        """One §12 event *about a receipt*, keyed and caused by that receipt's id.

        ``node_contract_hash`` is in the payload because :meth:`_assert_amendable` reads it
        back off the ``dispatch`` event to decide whether a decision has been claimed. That
        is a load-bearing payload key, not a convenience.
        """

        await self._event(
            store,
            run,
            action,
            event_type,
            causation_id=receipt.contract_id,
            payload={
                "receipt_id": receipt.contract_id,
                "node_contract_hash": receipt.node_contract_hash,
            },
        )

    async def _event(
        self,
        store: StateStore,
        run: Run,
        action: str,
        event_type: EventType,
        *,
        causation_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Append one routing event, derived from ``causation_id`` so a replay cannot double it.

        ``payload`` carries ids, digests, counts and enum values only. An objective is user
        text and can contain a credential; §17's event log is the widest-read surface in the
        system, and a router that echoed its inputs into it would make the log the leak.
        """

        await store.append_event(
            AgentEvent(
                event_id=derived_id("event", causation_id, action),
                run_id=run.run_id,
                session_id=run.session_id or run.run_id,
                provider=run.provider,
                native_type="accretion/routing/" + action,
                normalized_type=event_type,
                correlation_id=run.run_id,
                causation_id=causation_id,
                adapter_version="routing/1",
                timestamp=self.clock(),
                payload={**dict(payload), "action": action},
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
            e.native_type == "accretion/routing/dispatch"
            and e.payload.get("node_contract_hash") == receipt.node_contract_hash
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
        if (
            health is None
            or health.runtime_id != configuration.runtime.runtime_id
            or health.provider != configuration.runtime.provider
            or health.runtime_version != configuration.runtime.adapter_version
            or content_hash(
                {
                    "runtime_id": health.runtime_id,
                    "provider": health.provider,
                    "runtime_version": health.runtime_version,
                    "capabilities": sorted(health.capabilities),
                },
                exclude=(),
            )
            != configuration.runtime.capability_profile_digest
        ):
            raise _error("RUNTIME_VERSION_DRIFT", "Runtime changed after routing")
        if health.status is not RuntimeStatus.READY:
            raise _error("RUNTIME_UNAVAILABLE", "Selected runtime is not ready for dispatch")
        async with self.store.routing_transaction(run.run_id) as store:
            await self._authorize(
                receipt.workspace_id, principal_ref_for_run(run), mutate=True, store=store
            )
            await self._assert_amendable(store, receipt, run)
            await self._receipt_event(
                store, run, receipt, "dispatch", EventType.ROUTING_DECISION_CREATED
            )
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
                await self._receipt_event(
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
        self._assert_mode_available(mode)
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

        versions = await self._versions(mode, workspace_id=node.workspace_id, project_id=project_id)
        expected = derive_request(
            node.immutable_hash,
            snapshot,
            versions.router_label,
            versions.adapter_label,
            mode,
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
