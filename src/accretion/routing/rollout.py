"""Shadow decisions on the live path, and the branched rollouts that score them (ADR-060).

M6.1 built the arithmetic: what a shadow version would have chosen, how a pair of arms is
joined, and what the report says. This module is the half that runs — the two stage
collaborators :mod:`accretion.routing.stages` declared and nobody implemented, wired to the
routing service by :mod:`accretion.routing.bootstrap`.

**Why a branched rollout and not a replay (R7, ADR-060).** The obvious way to score a shadow
recommendation is to replay the executed trajectory against the shadow configuration and read
off what changes. It does not work: a trajectory is a record of what one configuration did, and
another configuration would have taken different actions from the second turn onwards, so a
replay measures how well the shadow configuration imitates the executed one rather than how
well it does the node's work. So the run is *forked* instead. The shadow configuration executes
in a fresh sandbox, the executed configuration executes in a sibling sandbox under the same seed
policy, and the difference between the two is the measurement. The per-node difficulty both arms
share cancels, which is the whole reason the pair exists.

**Why the CONTROL arm is re-run rather than read off the live node.** The live node ran in the
run's own workspace, at the run's own point in the graph, with whatever state earlier nodes left
behind. A fork starts from the run's base revision. Scoring the shadow fork against the live
node would therefore compare two executions that differ in their starting state as well as in
their configuration, and the difference would be attributed entirely to the router. The CONTROL
arm is the executed configuration run *in a fork of its own*, so the only thing that differs
between the two arms is the configuration.

**Why nothing here can fail a run.** :class:`ShadowRoutingHook` runs after a receipt is durable
and :class:`BranchedRolloutExecutor` runs after a node has finished; by then the run has already
been told what happened. Both therefore swallow every exception and log it. A shadow stage that
could fail a run would be a live experiment with production authority, which is precisely what
§11.1 stages the shadow regime to avoid.

**Why the shadow receipt is born superseded.** The shadow decision is persisted as a real
:class:`~accretion.contracts.routing.RoutingDecisionReceipt` so that its slate, its explanation
and its predicted outcomes stay auditable and so that the executor can dereference the
configuration it recommended. But ``DefaultNodeRoutingService`` finds the decision in force for
a node by taking the *head* of the receipts sharing its ``node_contract_hash`` — the receipts no
other receipt supersedes — and a second head would make ``latest_receipt`` a coin flip and
``route`` refuse the node for having competing heads. The shadow receipt therefore names *itself*
in ``supersedes_contract_id``: it is excluded from the head set by construction, so the executed
decision stays the only head, and ``_assert_amendable`` refuses to amend or dispatch it for the
same reason. A shadow decision that cannot be the head cannot be claimed, which is the
mechanical form of "a shadow is a record and never a dispatch".
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from accretion.contracts import (
    AcceptancePolicy,
    AgentEvent,
    ArtifactRef,
    EventType,
    IterationDirective,
    IterationDirectiveKind,
    Provider,
    Run,
    RunNode,
    RuntimeExecutionRequest,
    SessionConfig,
    Task,
    VerificationResult,
    VerificationStatus,
    WorkspaceLease,
)
from accretion.contracts.routing import (
    ExecutionConfiguration,
    IndependentVerificationResult,
    ObjectiveContract,
    ObservedOutcome,
    RiskClass,
    RouterModelVersion,
    RouterStatus,
    RoutingContext,
    RoutingDecisionReceipt,
    ServingWindow,
    ShadowDecision,
    ShadowRolloutKind,
    ShadowRolloutResult,
    UncertaintySummary,
)
from accretion.ids import derived_id, new_id
from accretion.persistence.store import StateStore
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.coldstart import ColdStartScorer
from accretion.routing.identity import execution_instance_id, routing_request_id
from accretion.routing.protocols import FrozenNode, RoutingMode
from accretion.routing.selector import (
    DETERMINISTIC_PROPENSITY,
    DeterministicSelector,
    SelectionResult,
)
from accretion.routing.shadow import SHADOW_ADAPTER_VERSION, ShadowBudget, record_shadow_decision
from accretion.routing.snapshot import RoutingSnapshot
from accretion.routing.stages import ActiveVersions, ScoredSlate
from accretion.routing.train import LearnedPredictorLoader
from accretion.runtimes.common import make_event
from accretion.services.run_manager import RunManager

_LOGGER = logging.getLogger(__name__)

SHADOW_OF_LABEL = "shadow_of"
"""On the shadow context and receipt: the *executed* routing request this shadows.

The executed request id and not the executed receipt id, because §8.2 makes the request the
identity of the question and the receipt the identity of one answer to it; an amendment mints a
new receipt for the same request, and a link through the receipt would go stale the moment an
operator overrode the decision this shadow was recorded beside.
"""

SHADOW_VERSION_LABEL = "shadow_router_version_id"
"""On the shadow context: which registered SHADOW version produced the recommendation."""

SHADOW_ROUTING_STATUS = "SHADOW"
"""``routing_status`` for a receipt that was recorded and may never be dispatched.

The executed path writes ``READY`` here and reads it back in ``_assert_amendable``. A third
value rather than a reuse of ``CANCELLED``: a cancelled decision was live and was withdrawn,
and a reader counting withdrawn decisions must not find every shadow among them.
"""

SHADOW_FORK_PREFIX = "shadow-"
"""The ``search_id`` prefix every rollout fork is acquired under.

``WorktreeManager.acquire_candidate`` roots a fork at ``<root>/search/<search_id>/<candidate_id>``,
so this prefix is what keeps a rollout's sandbox out of both the run's lease directory and the
candidate-search namespace, and it is what a test asserts a fork path against.
"""

FORK_SAMPLE_BUCKETS = 1_000
"""The resolution of the fork sampling decision. Thousandths, not a float comparison."""

_ROLLOUT_RECORDED = "router.shadow.rollout-recorded"
_ROLLOUT_SKIPPED = "router.shadow.rollout-skipped"

BUDGET_EXHAUSTED = "budget_exhausted"
"""The skip reason recorded when a policy has spent its day's shadow budget."""

NOT_FORK_ELIGIBLE = "not_fork_eligible"
"""The skip reason recorded for a node a fork may not be taken of at all."""

NOT_SAMPLED = "not_sampled"
"""The skip reason recorded when the receipt fell outside ``fork_fraction``."""


def _utc_now() -> datetime:
    """The clock both collaborators default to. Named so a test can substitute a frozen one."""

    return datetime.now(UTC)


class ShadowRoutingHook:
    """§9.4's post-route seam, recording what a registered SHADOW version would have chosen.

    Constructed with the same three collaborators
    :class:`~accretion.routing.shadow.ShadowEvaluator` takes, because the two halves of the
    shadow stage read the same rows and a second spelling of "the artefact root" would let a
    registration and an evaluation disagree about where a predictor lives.

    ``mode`` is the deployment's routing mode, captured at assembly. The hook has no other way
    to know it: :meth:`~accretion.routing.stages.PostRouteHook.after_receipt` is handed a
    committed receipt and a receipt does not carry the mode it was decided under (the mode is
    inside the *request* id's digest, which is one-way by construction). Capturing it is
    honest because §11.1 scopes the mode to the deployment's earned position rather than to a
    call, and :meth:`~accretion.routing.service.DefaultNodeRoutingService.default_mode` is
    read-only for exactly that reason.
    """

    def __init__(
        self,
        store: StateStore,
        artifacts: ArtifactStore,
        *,
        mode: RoutingMode = RoutingMode.SHADOW,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.store = store
        self.artifacts = artifacts
        self.mode = mode
        self.clock = clock
        self.scorer = ColdStartScorer(LearnedPredictorLoader(store, artifacts), artifacts, clock)

    async def after_receipt(
        self,
        *,
        run: Run,
        frozen: FrozenNode,
        snapshot: RoutingSnapshot,
        context: RoutingContext,
        receipt: RoutingDecisionReceipt,
        slate: ScoredSlate,
    ) -> None:
        """Record one shadow decision beside one executed decision. Never dispatches."""

        if self.mode is RoutingMode.BASELINE_ONLY:
            # A deployment that has not earned a learned mode has no shadow stage to run, and
            # §11.1 makes that a property of the deployment rather than of a request body.
            return
        try:
            await self._record(
                run=run,
                frozen=frozen,
                snapshot=snapshot,
                context=context,
                receipt=receipt,
                slate=slate,
            )
        except Exception:
            # The receipt is durable and the caller already holds it. The stage protocol says
            # this method must not raise, and the run must not learn that a shadow failed.
            _LOGGER.exception(
                "shadow recording failed for receipt %s; the executed decision stands",
                receipt.contract_id,
            )

    async def _record(
        self,
        *,
        run: Run,
        frozen: FrozenNode,
        snapshot: RoutingSnapshot,
        context: RoutingContext,
        receipt: RoutingDecisionReceipt,
        slate: ScoredSlate,
    ) -> None:
        version = await self.shadow_version(context.workspace_id)
        if version is None:
            return
        objective = await self.store.get_objective_contract(
            frozen.objective_ref.objective_contract_id
        )
        if objective is None:
            return

        request_id = routing_request_id(
            frozen.node_contract.immutable_hash,
            snapshot,
            version.contract_id,
            context.project_adapter_version,
            RoutingMode.SHADOW,
        )
        shadow_context = await self.store.put_routing_request(
            RoutingContext.model_validate(
                {
                    **context.model_dump(mode="python"),
                    "contract_id": request_id,
                    "content_hash": "",
                    "workspace_router_version": version.contract_id,
                    "labels": {
                        **context.labels,
                        SHADOW_OF_LABEL: receipt.routing_request_id,
                        SHADOW_VERSION_LABEL: version.contract_id,
                    },
                }
            )
        )
        shadow_slate, selection = await self._decide(
            context=shadow_context,
            slate=slate,
            frozen=frozen,
            objective=objective,
            version=version,
        )
        shadow_receipt = await self.store.put_routing_receipt(
            self._receipt(
                run=run,
                frozen=frozen,
                snapshot=snapshot,
                context=shadow_context,
                executed=receipt,
                slate=shadow_slate,
                selection=selection,
                version=version,
                request_id=request_id,
            )
        )
        await record_shadow_decision(
            self.store,
            executed_receipt=receipt,
            shadow_receipt=shadow_receipt,
            version_id=version.contract_id,
            notes=(
                f"shadow version {version.contract_id} re-scored the executed slate of "
                f"{len(slate.candidates)} candidates under mode SHADOW and was not dispatched; "
                f"executed request {receipt.routing_request_id}, shadow request {request_id}"
            ),
        )

    async def shadow_version(self, workspace_id: str) -> RouterModelVersion | None:
        """The workspace's registered SHADOW stage, or ``None`` when it has none.

        The most recently created one wins when a workspace has registered several, ordered by
        ``(created_at, contract_id)`` so that two readers agree. A workspace with two shadow
        stages is evaluating the newer of them; scoring both would double the cost of every
        routed node without anyone having asked for it, and picking by store iteration order
        would make the report depend on which backend answered.
        """

        versions = [
            version
            for version in await self.store.list_router_model_versions(workspace_id=workspace_id)
            if version.status is RouterStatus.SHADOW
        ]
        if not versions:
            return None
        return max(versions, key=lambda version: (version.created_at, version.contract_id))

    async def _decide(
        self,
        *,
        context: RoutingContext,
        slate: ScoredSlate,
        frozen: FrozenNode,
        objective: ObjectiveContract,
        version: RouterModelVersion,
    ) -> tuple[ScoredSlate, SelectionResult]:
        """Re-score the executed slate under the SHADOW predictor and select deterministically.

        The *executed* slate's candidates are re-scored rather than rebuilt. Rebuilding would
        re-run candidate construction, which reads the policy gate and the compatibility engine
        and would let a shadow decision be made over a different admissible set from the one the
        executed decision faced — and then the two would not be comparable at all.
        :func:`~accretion.routing.selector._rebuild_candidate` keeps each candidate's
        ``contract_id`` and replaces only the prediction fields, so the two decisions name the
        same persisted candidate rows.

        Evidence is empty and stated as empty rather than re-retrieved: the executed decision's
        retrieval already happened, the hook is not handed its records, and re-running retrieval
        here would score the shadow against a history the executed decision did not see.
        """

        shadow_slate = await self.scorer.score(
            context=context,
            candidates=slate.candidates,
            node=frozen.node_contract,
            objective=objective,
            versions=ActiveVersions(
                router_version_id=version.contract_id,
                adapter_version_id=None,
                router_label=version.contract_id,
                adapter_label=None,
            ),
            evidence_by_hash={
                candidate.configuration.configuration_hash: ()
                for candidate in slate.candidates
            },
        )
        selection = DeterministicSelector().select(
            shadow_slate.candidates,
            (),
            verified_success_floor=objective.verified_success_floor,
            created_at=context.requested_at,
            created_by=frozen.node_contract.created_by,
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            utility_weights=objective.utility_weights,
        )
        return shadow_slate, selection

    @staticmethod
    def _receipt(
        *,
        run: Run,
        frozen: FrozenNode,
        snapshot: RoutingSnapshot,
        context: RoutingContext,
        executed: RoutingDecisionReceipt,
        slate: ScoredSlate,
        selection: SelectionResult,
        version: RouterModelVersion,
        request_id: str,
    ) -> RoutingDecisionReceipt:
        """The shadow decision as a sealed receipt under its own request id.

        ``created_at`` is the executed receipt's, not the clock's: both decisions were made
        against one routing context at one instant, and a wall-clock reading here would make a
        replayed hook write a byte-different document under the same derived id, which the
        append-only store refuses as drift.
        """

        selected = selection.selected
        receipt_id = derived_id("routing_receipt", request_id)
        return RoutingDecisionReceipt.model_validate(
            {
                "contract_id": receipt_id,
                # Self-superseding on purpose; see this module's docstring.
                "supersedes_contract_id": receipt_id,
                "created_at": executed.created_at,
                "created_by": frozen.node_contract.created_by,
                "workspace_id": context.workspace_id,
                "project_id": context.project_id,
                "objective_contract_ref": frozen.objective_ref,
                "routing_request_id": request_id,
                "node_contract_hash": frozen.node_contract.immutable_hash,
                "selected_configuration_id": (
                    selected.configuration.contract_id if selected else None
                ),
                "selected_configuration_hash": (
                    selected.configuration.configuration_hash if selected else None
                ),
                "decision_type": selection.decision_type,
                "selection_propensity": DETERMINISTIC_PROPENSITY,
                "predicted_outcomes": selected.predicted if selected else None,
                "uncertainty": UncertaintySummary(
                    epistemic_uncertainty=selected.uncertainty_score if selected else 1,
                    lower_confidence_success=(
                        selected.lower_confidence_success if selected else 0
                    ),
                    calibration_version=slate.calibration_version,
                ),
                "candidate_summary_refs": [item.contract_id for item in selection.candidates],
                "rejected_candidate_reasons": [],
                "experience_refs": list(slate.evidence_ids),
                "workspace_router_version": version.contract_id,
                "project_adapter_version": None,
                "objective_contract_version": frozen.objective_ref.revision,
                "capability_registry_snapshot_id": snapshot.capability_registry_snapshot_id,
                "policy_snapshot_id": snapshot.policy_snapshot_id,
                "fallback_configuration_id": next(
                    (
                        item.configuration.contract_id
                        for item in selection.candidates
                        if item.fallback_eligible
                    ),
                    None,
                ),
                "explanation": selection.explanation,
                "labels": {
                    **dict(slate.labels),
                    "run_id": run.run_id,
                    "decision_version": "1",
                    "routing_status": SHADOW_ROUTING_STATUS,
                    SHADOW_OF_LABEL: executed.routing_request_id,
                    SHADOW_VERSION_LABEL: version.contract_id,
                },
            }
        )


class BranchedRolloutExecutor:
    """§9.4's post-node seam: fork the run and score the shadow recommendation for real.

    One instance serves a whole deployment. Everything about one rollout — the run, the node,
    the receipt, the configuration and the lease — arrives as an argument, so two executions
    with the same arguments write the same rows regardless of which executor object made them.

    ``fork_fraction`` is the share of routed nodes a fork is taken of. It is sampled from a
    digest of the receipt id rather than from a random number generator, so the same run
    forks the same nodes on a replay; a random sample would make a re-run of a seeded run
    diverge from it for a reason that has nothing to do with the router.
    """

    def __init__(
        self,
        manager: RunManager,
        *,
        fork_fraction: float = 1.0,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not 0.0 <= fork_fraction <= 1.0:
            raise ValueError(
                f"fork_fraction {fork_fraction} is a share of routed nodes and lies in [0, 1]; "
                "a value outside it would either disable the stage silently or promise more "
                "forks than there are nodes"
            )
        self.manager = manager
        self.store = manager.store
        self.fork_fraction = fork_fraction
        self.clock = clock

    async def after_node(
        self,
        *,
        run: Run,
        node: RunNode,
        frozen: FrozenNode,
        receipt: RoutingDecisionReceipt,
        configuration: ExecutionConfiguration,
        outcome: IndependentVerificationResult | None,
        lease: WorkspaceLease | None,
    ) -> None:
        """Branch one completed routed node into a paired rollout. Never fails the run."""

        try:
            await self._rollout(
                run=run,
                node=node,
                frozen=frozen,
                receipt=receipt,
                configuration=configuration,
                lease=lease,
            )
        except Exception:
            # ADR-060 buys evidence with forks, and a fork is a whole second execution: it can
            # time out, run out of disk or lose a worktree. The node it is about has already
            # finished and been reported, so none of that is the run's problem.
            _LOGGER.exception(
                "branched rollout failed for receipt %s; the executed node stands",
                receipt.contract_id,
            )

    def samples(self, receipt_id: str) -> bool:
        """Whether ``receipt_id`` falls inside ``fork_fraction``, deterministically."""

        bucket = int(hashlib.sha256(receipt_id.encode("utf-8")).hexdigest(), 16)
        return bucket % FORK_SAMPLE_BUCKETS < round(self.fork_fraction * FORK_SAMPLE_BUCKETS)

    async def _rollout(
        self,
        *,
        run: Run,
        node: RunNode,
        frozen: FrozenNode,
        receipt: RoutingDecisionReceipt,
        configuration: ExecutionConfiguration,
        lease: WorkspaceLease | None,
    ) -> None:
        decision = await self._decision_for(receipt)
        if decision is None:
            return
        if lease is None or not self._fork_eligible(frozen, configuration):
            await self._skip(run, decision, NOT_FORK_ELIGIBLE)
            return
        if not self.samples(receipt.contract_id):
            await self._skip(run, decision, NOT_SAMPLED)
            return
        version = await self.store.get_router_model_version(decision.shadow_router_version_id)
        if version is None:
            return
        if not await self._within_budget(version):
            await self._skip(run, decision, BUDGET_EXHAUSTED)
            return
        shadow_configuration = await self._shadow_configuration(decision)
        if shadow_configuration is None:
            # HUMAN_REVIEW_REQUIRED, or a slate whose selected candidate has since been
            # superseded. There is a shadow *decision* but no shadow *configuration*, so
            # there is nothing to run and a lone CONTROL arm would measure the node.
            await self._skip(run, decision, NOT_FORK_ELIGIBLE)
            return

        task = await self.store.get_task(run.task_id)
        if task is None:
            return
        policy = await self.manager._require_policy(run.acceptance_policy_id)
        trial_index = await self._next_trial(decision)
        seed = _trial_seed(decision.contract_id, trial_index)
        for kind, arm in (
            (ShadowRolloutKind.SHADOW, shadow_configuration),
            (ShadowRolloutKind.CONTROL, configuration),
        ):
            result = await self._run_arm(
                run=run,
                node=node,
                task=task,
                frozen=frozen,
                policy=policy,
                lease=lease,
                decision=decision,
                kind=kind,
                configuration=arm,
                trial_index=trial_index,
                seed=seed,
            )
            await self._announce(run, decision, result)

    # ------------------------------------------------------------------ gates

    @staticmethod
    def _fork_eligible(frozen: FrozenNode, configuration: ExecutionConfiguration) -> bool:
        """Both halves of ADR-060's safety precondition, and neither is sufficient alone.

        ``LOW_DIGITAL`` is the only risk class a second, unasked-for execution may be taken at:
        every higher class either touches something outside the workspace or is gated on a
        human approval that was given for *one* execution, and a fork would spend it twice.
        ``WORKTREE`` isolation is the other half: a fork is only a sandbox if the configuration
        was going to run in a workspace that can be branched, and forking a configuration bound
        to a shared environment would run the second arm in the first one's world.
        """

        return (
            frozen.node_contract.allowed_risk_class is RiskClass.LOW_DIGITAL
            and configuration.environment.workspace_isolation == "WORKTREE"
        )

    async def _decision_for(self, receipt: RoutingDecisionReceipt) -> ShadowDecision | None:
        """The shadow decision recorded beside ``receipt``, or ``None`` if none was."""

        decisions = [
            decision
            for decision in await self.store.list_shadow_decisions(
                workspace_id=receipt.workspace_id, project_id=receipt.project_id
            )
            if decision.executed_receipt_id == receipt.contract_id
        ]
        if not decisions:
            return None
        return max(decisions, key=lambda decision: (decision.created_at, decision.contract_id))

    async def _within_budget(self, version: RouterModelVersion) -> bool:
        """Whether this policy has anything left of today's budget (ADR-060).

        Both limits, because :class:`~accretion.routing.shadow.ShadowBudget` states both and a
        policy whose forks are individually cheap can still saturate the executor. The window is
        the UTC day of the clock, matched against ``completed_at`` rather than the header's
        ``created_at`` so that a fork is charged to the day it ran on.

        A version carrying no budget labels is refused rather than allowed: an unbudgeted policy
        is one nobody agreed to spend anything on.
        """

        budget = ShadowBudget.from_labels(version.labels)
        if budget is None:
            return False
        today = self.clock().date()
        mine = {
            decision.contract_id
            for decision in await self.store.list_shadow_decisions(
                workspace_id=version.workspace_id, project_id=version.project_id
            )
            if decision.shadow_router_version_id == version.contract_id
        }
        rows = [
            row
            for row in await self.store.list_shadow_rollout_results(
                workspace_id=version.workspace_id, project_id=version.project_id
            )
            if row.shadow_decision_id in mine and row.completed_at.date() == today
        ]
        spent = sum(row.budget_consumed for row in rows)
        return spent < budget.daily_cost_cap and len(rows) < budget.max_trials_per_day

    async def _shadow_configuration(
        self, decision: ShadowDecision
    ) -> ExecutionConfiguration | None:
        """The configuration the shadow receipt selected, dereferenced from its own slate."""

        if decision.shadow_configuration_hash is None:
            return None
        receipt = await self.store.get_routing_receipt(decision.shadow_receipt_id)
        if receipt is None or receipt.selected_configuration_id is None:
            return None
        for ref in receipt.candidate_summary_refs:
            candidate = await self.store.get_configuration_candidate(ref)
            if (
                candidate is not None
                and candidate.configuration.contract_id == receipt.selected_configuration_id
                and candidate.configuration.configuration_hash
                == receipt.selected_configuration_hash
            ):
                return ExecutionConfiguration.model_validate(
                    candidate.configuration.model_dump()
                )
        return None

    async def _next_trial(self, decision: ShadowDecision) -> int:
        """The next trial index for ``decision``, counted from its own SHADOW arms.

        Counted from the stored rows and not from an in-memory counter: an executor restarted
        mid-stage would otherwise reuse trial 0 and collide with a pair that already exists,
        and the append-only store would refuse the write with an error about drift rather than
        about the count.
        """

        rows = await self.store.list_shadow_rollout_results(
            workspace_id=decision.workspace_id, project_id=decision.project_id
        )
        return sum(
            1
            for row in rows
            if row.shadow_decision_id == decision.contract_id
            and row.kind is ShadowRolloutKind.SHADOW
        )

    # ------------------------------------------------------------------- arms

    async def _run_arm(
        self,
        *,
        run: Run,
        node: RunNode,
        task: Task,
        frozen: FrozenNode,
        policy: AcceptancePolicy,
        lease: WorkspaceLease,
        decision: ShadowDecision,
        kind: ShadowRolloutKind,
        configuration: ExecutionConfiguration,
        trial_index: int,
        seed: int,
    ) -> ShadowRolloutResult:
        """Execute one arm in its own fresh sandbox and seal one rollout row from it."""

        project = await self.store.get_project(run.project_id)
        if project is None:
            raise KeyError(run.project_id)
        fork_execution = execution_instance_id(
            run.run_id, f"{node.key}#{kind.value.lower()}", trial_index + 1
        )
        fork = await self.manager.worktrees.acquire_candidate(
            project_id=run.project_id,
            run_id=run.run_id,
            search_id=f"{SHADOW_FORK_PREFIX}{decision.contract_id}",
            candidate_id=f"{kind.value.lower()}-{trial_index}",
            repository=project.repository_path,
            base_revision=lease.base_revision,
            parent_patch="",
        )
        runtime = self.manager.runtimes[configuration.runtime.provider]
        session = await runtime.create_session(
            SessionConfig(
                run_id=run.run_id,
                workspace=fork.path,
                model=configuration.model.model_id,
                allowed_tools=[tool.capability.capability_id for tool in configuration.tools],
                denied_tools=task.envelope.denied_capabilities,
            )
        )
        budget = frozen.node_contract.resource_cap
        started = self.clock()
        request = RuntimeExecutionRequest(
            runtime_call_id=new_id("runtime_call"),
            run_id=run.run_id,
            task=task.envelope.model_copy(
                update={
                    "objective": frozen.node_contract.objective,
                    "budgets": task.envelope.budgets.model_copy(
                        update={
                            "wall_time_seconds": max(1, budget.maximum_latency_ms // 1_000),
                            "max_turns": max(1, budget.maximum_attempts),
                            "max_tool_calls": max(1, budget.maximum_tool_calls),
                        }
                    ),
                }
            ),
            directive=IterationDirective(
                kind=IterationDirectiveKind.INITIAL,
                objective=frozen.node_contract.objective,
            ),
            deadline=started + timedelta(milliseconds=budget.maximum_latency_ms),
            max_turns=max(1, budget.maximum_attempts),
            max_tool_calls=max(1, budget.maximum_tool_calls),
        )
        events: list[AgentEvent] = []
        tool_calls: set[str] = set()
        ref = await runtime.submit(session, request)
        try:
            async with asyncio.timeout(max(0.001, budget.maximum_latency_ms / 1_000)):
                async for event in runtime.events(ref):
                    events.append(event)
                    if event.normalized_type in {
                        EventType.TOOL_REQUESTED,
                        EventType.TOOL_STARTED,
                    }:
                        tool_calls.add(self.manager._tool_call_key(event))
        except TimeoutError:
            await runtime.interrupt(ref)
        completed = self.clock()

        artifact = await self._capture(fork, decision, kind, trial_index)
        # `persist=False` because these are not the run's verdicts: a rollout is graded to
        # produce a number, and writing its results into the run's `verifications` table would
        # put a fork's verdict where a reader looks for the executed node's.
        results = await self.manager._verify_candidate(
            run=run,
            task=task,
            lease=fork,
            session_id=session.session_id,
            policy=policy,
            artifact_ref=artifact.artifact_id if artifact else None,
            diff_sha256=artifact.sha256 if artifact else None,
            persist=False,
            emit_result=False,
            trajectory_events=events,
        )
        health = await runtime.health()
        cost = _cost_proxy(budget.maximum_attempts, budget.maximum_tool_calls, len(tool_calls))
        row = ShadowRolloutResult.model_validate(
            {
                "contract_id": derived_id(
                    "shadow_rollout_result",
                    decision.contract_id,
                    kind.value,
                    str(trial_index),
                ),
                "created_at": completed,
                "created_by": frozen.node_contract.created_by,
                "workspace_id": decision.workspace_id,
                "project_id": decision.project_id,
                "shadow_decision_id": decision.contract_id,
                "kind": kind,
                "fork_execution_id": fork_execution,
                "configuration_hash": configuration.configuration_hash,
                "serving": ServingWindow(
                    provider=health.provider,
                    runtime_version=health.runtime_version,
                    model_id=configuration.model.model_id,
                    serving_labels={"seed": str(seed), "workspace_isolation": "WORKTREE"},
                ),
                # No `verification_result_id`, and therefore `verified=False` below: the fork
                # was graded with `persist=False`, so no §7.9 independent result exists for it,
                # and the contract refuses a rollout that calls itself verified without naming
                # the record that verified it. The passing fraction is carried by `quality`.
                "verification_result_id": None,
                "observed": ObservedOutcome(
                    quality=_quality(results),
                    cost=cost,
                    latency_ms=max(0.0, (completed - started).total_seconds() * 1_000),
                    verified=False,
                ),
                "budget_consumed": cost,
                "trial_index": trial_index,
                "seed": seed,
                "completed_at": completed,
            }
        )
        return await self.store.put_shadow_rollout_result(row)

    async def _capture(
        self,
        fork: WorkspaceLease,
        decision: ShadowDecision,
        kind: ShadowRolloutKind,
        trial_index: int,
    ) -> ArtifactRef | None:
        """The fork's diff, saved so the graders have something content-addressed to read."""

        artifact = await self.manager.worktrees.capture_diff(
            fork,
            name=f"{decision.contract_id}-{kind.value.lower()}-{trial_index:03}.patch",
            kind="SHADOW_ROLLOUT_GIT_DIFF",
        )
        if artifact is not None:
            await self.store.save_artifact(artifact)
        return artifact

    # ----------------------------------------------------------------- events

    async def _announce(
        self, run: Run, decision: ShadowDecision, row: ShadowRolloutResult
    ) -> None:
        await self._emit(
            run,
            _ROLLOUT_RECORDED,
            {
                "shadow_decision_id": decision.contract_id,
                "shadow_rollout_result_id": row.contract_id,
                "kind": row.kind.value,
                "trial_index": row.trial_index,
                "budget_consumed": row.budget_consumed,
            },
        )

    async def _skip(self, run: Run, decision: ShadowDecision, reason: str) -> None:
        await self._emit(
            run,
            _ROLLOUT_SKIPPED,
            {
                "shadow_decision_id": decision.contract_id,
                "shadow_router_version_id": decision.shadow_router_version_id,
                "reason": reason,
            },
        )

    async def _emit(self, run: Run, native_type: str, payload: dict[str, object]) -> None:
        """One §12 event about a rollout, under the native type that distinguishes it.

        ``normalized_type`` is ``ROUTER_CANDIDATE_TRAINED`` for the reason
        :meth:`~accretion.routing.shadow.ShadowEvaluator._announce` gives: §12's twelve v0.4
        event types were declared in M1 and none of them names a rollout, and widening a frozen
        enum for one string would regenerate the TypeScript schema for a label.
        """

        await self.store.append_event(
            make_event(
                run_id=run.run_id,
                session_id=run.session_id or "ses_pending",
                provider=Provider.DETERMINISTIC,
                native_type=native_type,
                normalized_type=EventType.ROUTER_CANDIDATE_TRAINED,
                payload=dict(payload),
                adapter_version=SHADOW_ADAPTER_VERSION,
            )
        )


def _trial_seed(decision_id: str, trial_index: int) -> int:
    """The seed both arms of one trial share, derived so that "the same seed" is checkable.

    ``ShadowRolloutResult.seed`` exists because "the two arms ran under the same seed policy"
    is otherwise a claim in a paragraph. Deriving it from the decision and the trial rather than
    drawing it means a reader can recompute it, and means a re-run of the same trial is the same
    trial rather than a new one wearing its name.
    """

    digest = hashlib.sha256(f"{decision_id}:{trial_index}".encode()).hexdigest()
    return int(digest[:8], 16)


def _cost_proxy(max_attempts: int, max_tool_calls: int, tool_calls: int) -> float:
    """Budget-relative spend in ``[0, 1]``, the same proxy candidate search already uses.

    One turn out of the node's attempt ceiling plus the observed tool calls out of its tool
    ceiling, averaged. Absolute money is not measurable here — no runtime in this repository
    reports a price — and a fabricated currency figure inside a record that gates a promotion
    would be worse than an honest budget fraction. ``ShadowBudget.daily_cost_cap`` is compared
    against a sum of these, in these units.
    """

    return round(
        min(1.0, (1 / max(1, max_attempts) + tool_calls / max(1, max_tool_calls)) / 2), 6
    )


def _quality(results: Sequence[VerificationResult]) -> float:
    """The fork's normalised quality: the graders' mean score, or the pass fraction.

    ``VerificationResult.score`` is optional and most deterministic verifiers leave it unset, so
    a mean over scores alone would be a mean over an arbitrary subset. When no result carries a
    score the fraction of results that passed is used instead, which is in ``[0, 1]`` by
    construction and is the same quantity the candidate-search scorer falls back to.
    """

    scored = [result.score for result in results if result.score is not None]
    if scored:
        return round(min(1.0, max(0.0, sum(scored) / len(scored))), 6)
    if not results:
        return 0.0
    passed = sum(1 for result in results if result.status is VerificationStatus.PASS)
    return round(passed / len(results), 6)


__all__ = [
    "BUDGET_EXHAUSTED",
    "FORK_SAMPLE_BUCKETS",
    "NOT_FORK_ELIGIBLE",
    "NOT_SAMPLED",
    "SHADOW_FORK_PREFIX",
    "SHADOW_OF_LABEL",
    "SHADOW_ROUTING_STATUS",
    "SHADOW_VERSION_LABEL",
    "BranchedRolloutExecutor",
    "ShadowRoutingHook",
]
