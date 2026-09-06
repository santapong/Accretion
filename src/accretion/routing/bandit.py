"""SDD §9.5's guarded exploration, as a :class:`~accretion.routing.stages.BehaviorPolicy`.

M5 froze the seam and M6 gave it a shadow stage to earn its way through; this module is the
policy that finally takes an action the deterministic selector would not have taken. Every
line of it is a gate, and the gates are the point: the *ability* to explore is one paragraph
of arithmetic, and the nine conditions that have to hold before that paragraph runs are the
milestone.

**Why a conservative bandit and not an ε-greedy one (R5,
https://www.alphaxiv.org/abs/2412.06165).** ε-greedy spends a fixed fraction of every
workspace's traffic on actions it already believes are worse, and it spends that fraction
whether or not the workspace can afford it. A conservative contextual bandit instead
guarantees, at *every* round, that the cumulative cost of what it explored stays within a
factor of ``(1 + α)`` of what the deterministic router would have spent on the same rounds.
That is a statement an approver can read, and :class:`~accretion.routing.ledger.CostLedger`
is where it is checked. α is not this module's to choose: it comes from
:class:`~accretion.contracts.routing.ExplorationPolicy` on the objective, because the person
who approved the goal is the person entitled to say how much of its budget may be spent
learning.

**Why inverse-gap weighting.** The exploration distribution is
``p(a) = 1 / (K + γ_t · (Û(â) − Û(a)))`` for every non-greedy action, with the remainder on
the greedy action ``â``. Its two properties are exactly the two an off-policy estimator
needs. Every action keeps a *strictly positive* probability, so no action is ever logged with
a propensity of zero and no later estimator has to divide by one; and the probability decays
with the *utility gap*, so a candidate that is merely a little worse is sampled far more often
than one that is much worse, which is what makes the regret bound hold without a schedule
anybody has to tune. ``γ_t = γ₀·√t`` widens the exploration early and narrows it as evidence
accumulates, with ``t`` counted per ``(workspace, node class)`` — the same key the ledger uses,
because a graph's tolerance for wrong answers does not pool across node classes.

**Why a conformal clip on top (R6).** Inverse-gap weighting bounds *regret*, which is an
average over rounds, and an average is not a safety property. The clip is:
``p(a) / p_safe ≤ β`` for every non-greedy ``a``, where ``p_safe`` is the greedy action's
probability under the ε-smoothed baseline (``ε`` = :data:`SMOOTHING_EPSILON`) and ``β`` is one
minus the split-conformal quantile of the safety losses the workspace has actually logged.
A workspace whose logged decisions never fell short of its objective's success floor gets
``β = 1`` and the unclipped distribution; one that routinely fell short gets a β that pushes
mass back onto the deterministic choice; and one with too few *projects* for a conformal
quantile to exist at all gets ``β = 0`` — :func:`~accretion.routing.calibration.
conformal_quantile` returns 1.0 in that case by design, and this module reads that as "no
exploration" rather than as "no constraint". Vacuous, and visibly so.

**Why a refusal is still a measurement.** When any gate refuses, the decision is the
deterministic baseline with a propensity of exactly 1.0, which is the truth: the policy that
acted had one admissible action and took it. The receipt then carries the reason under
:data:`REFUSED_LABEL` (and the tripped breaker ids under :data:`BREAKERS_TRIPPED_LABEL`), so
"exploration was off" is a fact with a cause attached rather than the absence of a fact.

**Why an EXPLORE receipt can name the greedy action.**
:class:`~accretion.contracts.routing.DecisionType` distinguishes ``EXPLORE`` from ``EXPLOIT``
so that off-policy evaluation knows which decisions were *drawn from the behaviour policy* —
not which ones happened to differ from the baseline. A draw that lands on ``â`` was still a
draw, its propensity is still ``p(â) < 1``, and recording it as ``EXPLOIT`` with a propensity
of 1.0 would put a fabricated number into the one field every estimator divides by.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from accretion.contracts.routing import (
    ConfigurationCandidate,
    ConstructionStage,
    DecisionType,
    ExperienceRecord,
    ExplorationPolicy,
    NodeContract,
    ObjectiveContract,
    RejectedCandidate,
    RiskClass,
    RoutingContext,
    RoutingDecisionReceipt,
    StructuredExplanation,
    UtilityWeights,
    VerificationState,
)
from accretion.persistence.store import StateStore
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.breaker_inputs import (
    BreakerSampler,
    BreakerSamplerConfig,
    BreakerSampling,
)
from accretion.routing.breakers import exploration_allowed
from accretion.routing.calibration import CalibrationDataError, conformal_quantile
from accretion.routing.catalog import WORKSPACE_ROUTER_VERSION
from accretion.routing.ledger import CostLedger, ExplorationCaps
from accretion.routing.selector import DETERMINISTIC_PROPENSITY, SelectionResult
from accretion.routing.shadow import (
    DEFAULT_DELTA_NI,
    DEFAULT_MIN_PAIRED_RUNS,
    ShadowReportConfig,
    shadow_gate,
    shadow_report,
)
from accretion.routing.snapshot import RoutingSnapshot
from accretion.routing.stages import BehaviorDecision, ScoredSlate
from accretion.routing.train import LearnedPredictorLoader

WORKTREE_ISOLATION = "WORKTREE"
"""The one ``workspace_isolation`` §9.5 permits an exploration to run under.

Not a configurable set. A configuration that shares the run's own workspace can write to it,
and an exploration is by definition an action nobody has evidence for; the whole reason
exploring is admissible at all is that a worktree makes the mistake discardable.
"""

SMOOTHING_EPSILON = 0.02
"""The ``ε`` of the ε-smoothed baseline the conformal clip is stated against (R6).

Small and fixed. It is not an exploration rate — nothing is ever drawn from the smoothed
baseline — it is the reference distribution that gives ``p_safe`` a value strictly below 1,
so that the clip ``p(a) ≤ β · p_safe`` is a bound on a ratio of two real probabilities rather
than a bound against a degenerate point mass.
"""

DEFAULT_GAMMA_ZERO = 4.0
"""``γ₀`` in ``γ_t = γ₀·√t``. Larger is more conservative: it shrinks every exploration
probability toward zero, because ``γ`` multiplies the utility gap in the denominator."""

DEFAULT_CONFORMAL_ALPHA = 0.1
"""The miscoverage the safety quantile is taken at: a 90% upper bound on the safety loss."""

DEFAULT_MAX_ECE = 0.1
"""The calibration ceiling §15.3's second breaker is evaluated against.

Exploration is chosen against lower confidence bounds, and a bound produced by a router whose
expected calibration error has drifted past this is still a number and no longer a bound.
"""

DEFAULT_RECENT_WINDOW = 50
"""How many of a node class's most recent experience records the breakers are sampled over."""

ALPHA_LABEL = "exploration.alpha"
"""The ``α`` the conservative inequality was evaluated under, from the objective."""

CAPS_LABEL = "exploration.caps"
"""The two absolute caps, as ``count=<n>,cost=<c>``. Both bind whatever ``α`` says."""

COST_UCB_LABEL = "exploration.cost_ucb"
"""What the explored candidate was *charged*: its upper confidence bound, not its mean."""

BASELINE_COST_LCB_LABEL = "exploration.baseline_cost_lcb"
"""What the deterministic choice was *credited*: its lower confidence bound."""

BASELINE_PROPENSITY_LABEL = "exploration.baseline_propensity"
"""``p(â)``, the probability the behaviour policy left on the deterministic choice."""

NODE_CLASS_LABEL = "exploration.node_class"
"""The ledger key this exploration was charged to (ADR4-M7-003 rebuilds from it)."""

BETA_LABEL = "exploration.beta"
"""The conformal safety clip in force when the distribution was drawn."""

REFUSED_LABEL = "exploration.refused"
"""Why exploration did not happen, when it did not. Present on exactly the refusals."""

BREAKERS_TRIPPED_LABEL = "breakers.tripped"
"""The §15.3 breaker ids that refused, comma-separated in :data:`BREAKERS` order."""

EXPLORED_SUMMARY = (
    "Explored a safe alternative to the deterministic choice, within the objective's "
    "exploration budget and under the §15.3 breakers."
)

NOT_EXPLORED_REASON = "NOT_DRAWN_BY_EXPLORATION"
"""The reason code the deterministic choice carries on a receipt that explored past it."""


@dataclass(frozen=True, slots=True)
class BanditConfig:
    """Everything the bandit is not allowed to decide for itself, in one sealed object.

    None of these is read from the objective, and that is the division: the objective owns
    *how much* may be spent (``α`` and the two caps) because a human approved it, while these
    are properties of the algorithm and of the evidence bar, fixed for a deployment. Holding
    them together rather than as eight defaulted parameters is what lets a test state the bar
    it is applying and a reader see that two decisions were made under one.
    """

    gamma_zero: float = DEFAULT_GAMMA_ZERO
    epsilon: float = SMOOTHING_EPSILON
    conformal_alpha: float = DEFAULT_CONFORMAL_ALPHA
    max_ece: float = DEFAULT_MAX_ECE
    recent_window: int = DEFAULT_RECENT_WINDOW
    min_paired_runs: int = DEFAULT_MIN_PAIRED_RUNS
    delta_ni: float = DEFAULT_DELTA_NI
    shadow: ShadowReportConfig = field(default_factory=lambda: ShadowReportConfig(seed=0))

    def __post_init__(self) -> None:
        if self.gamma_zero <= 0:
            raise ValueError(
                f"gamma_zero is {self.gamma_zero}; a non-positive learning rate would make "
                "the exploration distribution uniform over the utility gap, which is the one "
                "thing inverse-gap weighting exists not to be"
            )
        if not 0.0 < self.epsilon < 1.0:
            raise ValueError(
                f"epsilon is {self.epsilon}; the smoothed baseline must leave the safe action "
                "a probability strictly between 0 and 1 for the clip to bound a ratio"
            )
        if self.recent_window <= 0:
            raise ValueError(
                f"recent_window is {self.recent_window}; the breakers are sampled over recent "
                "behaviour and an empty window is not recent behaviour"
            )


def utility_of(candidate: ConfigurationCandidate, weights: UtilityWeights) -> float:
    """§9.3's scalar utility of one candidate, recomputed rather than read off it.

    ``ConfigurationCandidate.utility_score`` is ``None`` for a Pareto-dominated candidate and
    for one the success gate refused, because the deterministic selector only scores what it
    is willing to rank. The safe action set is a *wider* set than the ranked one — it is every
    hard-eligible candidate that clears the success floor — so reading the stored score would
    leave some members of ``A_safe`` with no utility at all, and an inverse-gap distribution
    cannot be taken over a set whose gaps are undefined.

    The formula is the selector's, to twelve places for the same reason: two candidates whose
    utilities differ in the sixteenth bit are the same action to a human reading the receipt,
    and floating-point noise in the gap would make the drawn distribution depend on the order
    the candidates happened to be built in.
    """

    return round(
        weights.quality * candidate.predicted.quality.mean
        - weights.cost * candidate.predicted.cost.mean
        - weights.latency * candidate.predicted.latency.mean,
        12,
    )


def exploration_policy_for(objective: ObjectiveContract) -> ExplorationPolicy | None:
    """The budget this objective authorises, from the field or from its labels.

    ``ObjectiveContract.exploration_policy`` is the authoritative form and is read first. The
    label fallback exists because the field was added by the v0.4 freeze delta (ADR-062) and
    an objective sealed before it cannot gain one: a sealed contract re-validated with a new
    field would no longer hash to the digest its node contract pins. ``labels`` is inside the
    seal too, so a workspace that wants to authorise exploration for such an objective seals a
    *new revision* carrying the three label keys, and the authority is still the objective's.

    All three keys are required together. A partial declaration is refused with ``None``
    rather than defaulted, because every one of the three is a bound and a missing bound is
    not a generous bound — it is an unstated one.
    """

    if objective.exploration_policy is not None:
        return objective.exploration_policy
    labels = objective.labels
    keys = (ALPHA_LABEL, "exploration.max_explore_count", "exploration.max_cost")
    if not all(key in labels for key in keys):
        return None
    try:
        return ExplorationPolicy(
            alpha=float(labels[ALPHA_LABEL]),
            max_explore_count=int(labels["exploration.max_explore_count"]),
            max_cost=float(labels["exploration.max_cost"]),
        )
    except (TypeError, ValueError):
        return None


def node_class_of(node: NodeContract) -> str:
    """The ledger and breaker key: the node's kind, as a plain string.

    The kind and not the contract signature. §9.5's budget is per *class* of work — a graph
    that spends its tolerance for wrong answers on cheap formatting nodes and has none left
    for the expensive planning node has made exactly the wrong trade — and a signature would
    give every distinct capability set its own untouched budget, which is a quota per node
    rather than a budget per class.
    """

    return node.node_kind.value


def _reseal(candidate: ConfigurationCandidate, **updates: object) -> ConfigurationCandidate:
    """Apply behaviour-stage fields and re-seal the immutable candidate document."""

    payload = candidate.model_dump(mode="python")
    payload.update(updates)
    payload["content_hash"] = ""
    return ConfigurationCandidate.model_validate(payload)


@dataclass(frozen=True, slots=True)
class _Refusal:
    """Why exploration did not happen, and which breakers said so if any did."""

    reason: str
    breakers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Admission:
    """What the gates proved present, handed forward so nothing is looked up twice."""

    policy: ExplorationPolicy
    greedy: ConfigurationCandidate
    safe: tuple[ConfigurationCandidate, ...]
    version_id: str
    beta: float


def _normalised(cost: float) -> float:
    """A predicted cost bound as the ledger's unit: a fraction of the node's cap in ``[0, 1]``.

    Clamped rather than refused. A predictor is free to emit a bound outside the unit
    interval — a cold-start prior does not know the node's cap — and the ledger's own
    ``_check_cost`` would turn that into an exception inside a routing decision. Clamping *up*
    to 1.0 charges the exploration the whole budget, which is the conservative direction; the
    honest alternative, refusing, would take the deterministic baseline down with it.
    """

    return min(1.0, max(0.0, cost))


class LedgerRegistry:
    """The live cost ledgers of one process, keyed by ``(workspace_id, node_class)``.

    **There is no ledger table (ADR4-M7-003).** Everything the conservative inequality needs is
    already on the receipt that spent the budget — the charged upper bound, the credited
    baseline lower bound and the node class — so a ledger is *rebuilt* by replaying a
    workspace's ``EXPLORE`` receipts rather than read from a second durable copy of a derived
    quantity. A table would have to be kept in step with the receipts, which are the audit
    record either way, and the two disagreeing is a class of bug with no external symptom.

    :meth:`ledger` folds in every receipt it has not seen before on *every* call, so a
    decision taken a moment ago by this same process is charged against the next one without
    the registry having to be told. What the cache holds and a replay cannot rebuild is the
    *settlements* :class:`~accretion.routing.settlement.ExplorationSettlement` applied, and
    losing those across a restart is deliberate: an unsettled exploration keeps its upper
    bound, so a fresh process charges every past exploration at its UCB and can only ever
    hold a *tighter* budget than the one that has been measured. Conservative in the
    direction a budget is allowed to be wrong in.
    """

    def __init__(self, store: StateStore) -> None:
        self.store = store
        self._ledgers: dict[tuple[str, str], CostLedger] = {}
        self._recorded: dict[tuple[str, str], set[str]] = {}

    async def ledger(self, *, workspace_id: str, node_class: str) -> CostLedger:
        """This key's ledger, with every ``EXPLORE`` receipt not yet in it folded in.

        Receipts are replayed in ``contract_id`` order so that two processes rebuilding the
        same ledger hold the same floats, and a receipt whose labels are missing or
        unparseable is skipped rather than guessed at: a charge nobody can read is not a
        charge this ledger may invent a number for.
        """

        key = (workspace_id, node_class)
        ledger = self._ledgers.get(key)
        if ledger is None:
            ledger = CostLedger(workspace_id=workspace_id, node_class=node_class)
            self._ledgers[key] = ledger
            self._recorded[key] = set()
        seen = self._recorded[key]
        receipts = await self.store.list_routing_receipts(workspace_id=workspace_id)
        for receipt in sorted(receipts, key=lambda item: item.contract_id):
            if receipt.contract_id in seen:
                continue
            charge = _charge_of(receipt, node_class=node_class)
            if charge is None:
                continue
            ledger.record(receipt.contract_id, *charge)
            seen.add(receipt.contract_id)
        return ledger


class GuardedBandit:
    """§9.5's exploration policy: nine gates, then inverse-gap weighting under a clip.

    ``store`` is read and never written. Every durable consequence of an exploration is on
    the receipt the routing service commits — the propensity, the charged cost, the credited
    baseline cost and the ledger key — which is what lets the cost ledger be reconstructed
    from receipts instead of from a table of its own (ADR4-M7-003).

    ``artifacts`` is here because the default :class:`~accretion.routing.breaker_inputs.
    BreakerSampler` needs a :class:`~accretion.routing.train.LearnedPredictorLoader` to read
    the ACTIVE version's sealed calibration report, and that loader is the only door a
    calibration artifact comes through (AC4-M4-016). A caller that has already built one
    passes its own sampler instead.

    ``rng`` is injected so that a test can state the draw it is asserting about. It is *not*
    seeded from the decision: a routing request that repeats replays the committed receipt
    rather than re-drawing, so a per-decision seed would buy determinism nothing consumes and
    would make two workspaces at the same round draw the same action.
    """

    def __init__(
        self,
        store: StateStore,
        artifacts: ArtifactStore,
        config: BanditConfig | None = None,
        *,
        sampler: BreakerSampling | None = None,
        ledgers: LedgerRegistry | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.store = store
        self.artifacts = artifacts
        self.config = config or BanditConfig()
        self.sampler: BreakerSampling = sampler or BreakerSampler(
            store, LearnedPredictorLoader(store, artifacts)
        )
        self.ledgers = ledgers or LedgerRegistry(store)
        self.rng = rng or random.Random()

    async def select(
        self,
        *,
        context: RoutingContext,
        slate: ScoredSlate,
        baseline: SelectionResult,
        node: NodeContract,
        objective: ObjectiveContract,
        snapshot: RoutingSnapshot,
    ) -> BehaviorDecision:
        """Draw the action actually taken, or refuse and say which gate refused."""

        del slate  # the safe action set is read off the baseline's own scored slate.
        admission = await self._admit(
            context=context,
            baseline=baseline,
            node=node,
            objective=objective,
            snapshot=snapshot,
        )
        if isinstance(admission, _Refusal):
            labels = {REFUSED_LABEL: admission.reason}
            if admission.breakers:
                labels[BREAKERS_TRIPPED_LABEL] = ",".join(admission.breakers)
            return BehaviorDecision(
                selection=baseline, propensity=DETERMINISTIC_PROPENSITY, labels=labels
            )

        node_class = node_class_of(node)
        ledger = await self.ledgers.ledger(
            workspace_id=node.workspace_id, node_class=node_class
        )
        probabilities = self._distribution(
            admission.safe,
            greedy=admission.greedy,
            weights=objective.utility_weights,
            rounds=ledger.explore_count + 1,
            beta=admission.beta,
        )
        chosen = self._draw(admission.safe, probabilities)
        propensity = probabilities[chosen.contract_id]
        policy = admission.policy
        cost_ucb = _normalised(chosen.predicted.cost.upper_bound)
        baseline_cost_lcb = _normalised(admission.greedy.predicted.cost.lower_bound)
        verdict = ledger.can_explore(
            candidate_cost_ucb=cost_ucb,
            baseline_cost_lcb=baseline_cost_lcb,
            alpha=policy.alpha,
            caps=ExplorationCaps(
                max_explore_count=policy.max_explore_count, max_cost=policy.max_cost
            ),
        )
        if not verdict.allowed:
            return BehaviorDecision(
                selection=baseline,
                propensity=DETERMINISTIC_PROPENSITY,
                labels={REFUSED_LABEL: verdict.reason},
            )
        return BehaviorDecision(
            selection=self._explored(
                baseline, chosen=chosen, propensity=propensity, greedy=admission.greedy
            ),
            propensity=propensity,
            labels={
                ALPHA_LABEL: repr(policy.alpha),
                CAPS_LABEL: f"count={policy.max_explore_count},cost={policy.max_cost}",
                COST_UCB_LABEL: repr(cost_ucb),
                BASELINE_COST_LCB_LABEL: repr(baseline_cost_lcb),
                BASELINE_PROPENSITY_LABEL: repr(probabilities[admission.greedy.contract_id]),
                BETA_LABEL: repr(admission.beta),
                NODE_CLASS_LABEL: node_class,
            },
        )

    # -- the gates ---------------------------------------------------------------------

    async def _admit(
        self,
        *,
        context: RoutingContext,
        baseline: SelectionResult,
        node: NodeContract,
        objective: ObjectiveContract,
        snapshot: RoutingSnapshot,
    ) -> _Admission | _Refusal:
        """§9.5's preconditions in §9.5's order, evaluated until one refuses.

        In order, and not all at once: unlike the six breakers — which are alternatives an
        operator has to fix together — these are a *ladder*, and the ones below only make
        sense once the ones above hold. Asking whether an ACTIVE version cleared its shadow
        gate for a node that may never be explored at all would be asking a question about a
        decision nobody is entitled to make.

        Returning the four things the gates *proved present* rather than a boolean is what
        keeps the caller free of re-derivation: a second lookup of "which candidate did the
        baseline choose" could disagree with the one the gate checked.
        """

        if node.allowed_risk_class is not RiskClass.LOW_DIGITAL:
            return _Refusal(
                f"the node's allowed_risk_class is {node.allowed_risk_class.value}; §9.5 "
                f"permits exploration only on {RiskClass.LOW_DIGITAL.value} nodes"
            )
        greedy = baseline.selected
        if greedy is None:
            return _Refusal(
                "the deterministic selector chose nothing, so there is no safe action to "
                "explore instead of"
            )
        isolation = greedy.configuration.environment.workspace_isolation
        if isolation != WORKTREE_ISOLATION:
            return _Refusal(
                f"the deterministic choice runs under workspace_isolation {isolation!r}; an "
                f"exploration is only discardable under {WORKTREE_ISOLATION!r}"
            )
        if context.task_features.irreversible_actions:
            return _Refusal(
                "the task declares irreversible actions; an exploration that cannot be "
                "undone is not an experiment"
            )
        if (
            greedy.configuration.verifier.verification_spec_hash
            != node.verification_spec_ref.content_hash
        ):
            return _Refusal(
                "the deterministic choice is not bound to this node's verification spec, so "
                "an exploration's outcome would not be evidence about it"
            )
        version_id = context.workspace_router_version
        if version_id == WORKSPACE_ROUTER_VERSION:
            return _Refusal(
                "no learned router is active for this workspace, so nothing has passed a "
                "shadow gate and there is no policy to explore around"
            )
        passed = await self._passed_shadow(
            workspace_id=node.workspace_id,
            version_id=version_id,
            weights=objective.utility_weights,
        )
        if not passed:
            return _Refusal(
                f"the active router version {version_id} has not cleared its shadow gate at "
                f"{self.config.min_paired_runs} paired runs and delta_ni "
                f"{self.config.delta_ni}"
            )
        policy = exploration_policy_for(objective)
        if policy is None:
            return _Refusal(
                f"objective {objective.contract_id} authorises no exploration budget; an "
                "absent policy and a zero policy say the same operational thing"
            )
        safe = self._safe_actions(baseline, node=node, objective=objective)
        if len(safe) < 2:
            return _Refusal(
                f"the safe action set holds {len(safe)} action(s), so every distribution "
                "over it is the deterministic choice with propensity 1.0"
            )
        beta = await self._beta(workspace_id=node.workspace_id, objective=objective)
        if beta <= 0.0:
            return _Refusal(
                "the conformal safety clip is 0.0: the workspace's logged decisions do not "
                "bound its safety loss below the objective's floor, or there are too few "
                "projects for a conformal quantile to exist at all"
            )
        inputs = await self.sampler.sample(
            workspace_id=node.workspace_id,
            node_class=node_class_of(node),
            config=BreakerSamplerConfig(
                router_version_id=version_id,
                serving_versions={
                    health.provider.value: health.runtime_version
                    for health in snapshot.runtime_health
                },
                policy_snapshot_resolved=bool(snapshot.policy_snapshot_id),
                false_acceptance_ceiling=objective.false_acceptance_ceiling,
                coverage_floor=objective.verified_success_floor,
                max_ece=self.config.max_ece,
                delta_ni=self.config.delta_ni,
                recent_window=self.config.recent_window,
                project_id=context.project_id,
            ),
        )
        allowed, tripped = exploration_allowed(inputs)
        if not allowed:
            return _Refusal(
                "the §15.3 circuit breakers refused: " + ", ".join(tripped),
                tuple(tripped),
            )
        return _Admission(
            policy=policy, greedy=greedy, safe=safe, version_id=version_id, beta=beta
        )

    def _safe_actions(
        self,
        baseline: SelectionResult,
        *,
        node: NodeContract,
        objective: ObjectiveContract,
    ) -> tuple[ConfigurationCandidate, ...]:
        """``A_safe``: hard-eligible, over the success floor, isolated and verifier-bound.

        Deliberately *not* the selector's ranked set. The ranked set excludes the audited
        fallback and every Pareto-dominated candidate, and both of those are safe actions —
        a dominated candidate is one the selector would not *prefer*, not one it considers
        unsafe. Excluding them would shrink ``K`` and, with it, inflate every remaining
        exploration probability, which is the wrong direction for a safety gate to err in.

        Ordered by ``configuration_hash`` so that the distribution and the draw are taken over
        the same sequence on both store backends.
        """

        return tuple(
            sorted(
                (
                    candidate
                    for candidate in baseline.candidates
                    if candidate.hard_eligible
                    and candidate.lower_confidence_success >= objective.verified_success_floor
                    and candidate.configuration.environment.workspace_isolation
                    == WORKTREE_ISOLATION
                    and candidate.configuration.verifier.verification_spec_hash
                    == node.verification_spec_ref.content_hash
                ),
                key=lambda item: (item.configuration.configuration_hash, item.contract_id),
            )
        )

    async def _passed_shadow(
        self, *, workspace_id: str, version_id: str, weights: UtilityWeights
    ) -> bool:
        """Whether ``version_id``'s own shadow decisions clear :func:`shadow_gate`.

        The report is recomputed here rather than read from a stored one, because a stored
        report is a rendering taken at some past instant and this is a question about the
        evidence *now*: rollout rows land after the report that summarised the decisions they
        belong to. Decisions naming other shadow versions are excluded, since a report
        describes one policy and averaging two would be a verdict about neither.
        """

        decisions = [
            decision
            for decision in await self.store.list_shadow_decisions(workspace_id=workspace_id)
            if decision.shadow_router_version_id == version_id
        ]
        if not decisions:
            return False
        results = await self.store.list_shadow_rollout_results(workspace_id=workspace_id)
        report = shadow_report(
            results,
            decisions,
            weights=weights,
            config=self.config.shadow,
        )
        return shadow_gate(
            report,
            min_paired_runs=self.config.min_paired_runs,
            delta_ni=self.config.delta_ni,
        )

    # -- the distribution --------------------------------------------------------------

    def _distribution(
        self,
        safe: Sequence[ConfigurationCandidate],
        *,
        greedy: ConfigurationCandidate,
        weights: UtilityWeights,
        rounds: int,
        beta: float,
    ) -> dict[str, float]:
        """``p(a) = 1/(K + γ_t·gap)`` for every non-greedy action, clipped, remainder on ``â``.

        ``rounds`` is ``t`` — the exploration this draw would be, counted per
        ``(workspace, node class)`` — so ``γ_t = γ₀·√t`` grows with the evidence the ledger has
        accumulated and the distribution concentrates on the greedy action over time.

        The gap is clamped at zero. A candidate the *selector* ranked below ``â`` can still
        score above it here, because ``â`` is chosen under the Pareto filter and the fallback
        rule as well as by utility; a negative gap would then make the denominator smaller
        than ``K`` and hand that action more probability than the greedy one, which is not
        inverse-gap weighting and would not be bounded by anything.
        """

        size = len(safe)
        gamma = self.config.gamma_zero * math.sqrt(rounds)
        greedy_utility = utility_of(greedy, weights)
        probabilities: dict[str, float] = {}
        for candidate in safe:
            if candidate.contract_id == greedy.contract_id:
                continue
            gap = max(0.0, greedy_utility - utility_of(candidate, weights))
            probabilities[candidate.contract_id] = 1.0 / (size + gamma * gap)
        # R6: nothing may be drawn more often than ``β`` times the ε-smoothed baseline's own
        # probability of playing it safe. The clip is applied per action and the mass it
        # removes goes back to ``â``, so the distribution stays normalised and the direction
        # of every adjustment is toward the deterministic choice.
        safe_probability = 1.0 - self.config.epsilon + self.config.epsilon / size
        ceiling = beta * safe_probability
        for contract_id, probability in probabilities.items():
            probabilities[contract_id] = min(probability, ceiling)
        probabilities[greedy.contract_id] = 1.0 - sum(probabilities.values())
        return probabilities

    def _draw(
        self, safe: Sequence[ConfigurationCandidate], probabilities: Mapping[str, float]
    ) -> ConfigurationCandidate:
        """Sample one action from ``probabilities`` over ``safe``, in ``safe``'s order.

        Inverse transform over the sorted sequence rather than ``random.choices``: the draw
        has to be a function of the ordered candidates and one uniform, so that a test that
        fixes the uniform fixes the action, and so that the same distribution drawn on two
        backends visits the actions in one order.
        """

        threshold = self.rng.random()
        cumulative = 0.0
        for candidate in safe:
            cumulative += probabilities[candidate.contract_id]
            if threshold < cumulative:
                return candidate
        return safe[-1]

    async def _beta(self, *, workspace_id: str, objective: ObjectiveContract) -> float:
        """The conformal safety clip: one minus a 90% upper bound on the safety loss.

        The loss of one logged decision is how far its verified outcome fell *short of the
        objective's own success floor* — zero for a node whose verification passed, and the
        whole floor for one that did not. Exchangeability is over projects, not rows, which is
        :func:`~accretion.routing.calibration.conformal_quantile`'s whole point: a hundred
        records from one project buy exactly as much confidence as five do.

        Returns 0.0 — no exploration is admissible — whenever the bound cannot be computed:
        no records at all, or too few projects for the quantile's index to exist, in which
        case ``conformal_quantile`` returns the maximum residual 1.0 and this returns zero.
        That is the fail-closed direction, and it is why a workspace's *first* explorations
        wait for evidence from more than one project.
        """

        records = await self.store.list_experience_records(workspace_id=workspace_id)
        losses: list[float] = []
        groups: list[str] = []
        for record in sorted(records, key=lambda item: (item.created_at, item.contract_id)):
            losses.append(self._safety_loss(record, floor=objective.verified_success_floor))
            groups.append(record.project_id or "")
        if not losses:
            return 0.0
        try:
            quantile = conformal_quantile(losses, self.config.conformal_alpha, groups)
        except CalibrationDataError:
            return 0.0
        return max(0.0, min(1.0, 1.0 - quantile))

    @staticmethod
    def _safety_loss(record: ExperienceRecord, *, floor: float) -> float:
        """One logged decision's shortfall against the objective's verified-success floor.

        A record whose local verification passed cost the objective nothing and scores 0.0;
        one that failed, errored or was never judged scores the whole ``floor``, because the
        floor is exactly the success rate the objective declared it needed.
        """

        if record.local_verification_status is VerificationState.PASS:
            return 0.0
        return floor

    # -- the explained selection -------------------------------------------------------

    def _explored(
        self,
        baseline: SelectionResult,
        *,
        chosen: ConfigurationCandidate,
        propensity: float,
        greedy: ConfigurationCandidate,
    ) -> SelectionResult:
        """The baseline's slate, re-stamped so the receipt says what actually happened.

        Three things move and nothing else does. The drawn candidate carries
        ``SELECT_BEHAVIOR`` and the displaced deterministic choice falls back to
        ``RANK_BY_UTILITY``, because a receipt that showed two candidates at the behaviour
        stage would not say which one ran. The explanation drops the drawn candidate from its
        rejections — it was not rejected — and names the deterministic choice there instead,
        with the reason code an auditor greps for. And ``decision_type`` becomes ``EXPLORE``
        even when the draw landed on the deterministic choice, which is the whole point of the
        distinction: the field records that the action was *drawn*, not that it differed.
        """

        candidates = tuple(
            _reseal(candidate, construction_stage=ConstructionStage.SELECT_BEHAVIOR)
            if candidate.contract_id == chosen.contract_id
            else _reseal(candidate, construction_stage=ConstructionStage.RANK_BY_UTILITY)
            if candidate.contract_id == greedy.contract_id
            else candidate
            for candidate in baseline.candidates
        )
        selected = next(
            candidate for candidate in candidates if candidate.contract_id == chosen.contract_id
        )
        rejections = [
            rejection
            for rejection in baseline.explanation.rejected_candidates
            if rejection.candidate_id != chosen.contract_id
        ]
        if greedy.contract_id != chosen.contract_id:
            rejections.append(
                RejectedCandidate(
                    candidate_id=greedy.contract_id,
                    stage=ConstructionStage.SELECT_BEHAVIOR,
                    reason_code=NOT_EXPLORED_REASON,
                    detail=(
                        "The deterministic selector preferred this candidate; the guarded "
                        "bandit drew another from the safe action set."
                    ),
                )
            )
        rejections.sort(key=lambda item: item.candidate_id)
        payload = baseline.explanation.model_dump(mode="python")
        payload["summary"] = EXPLORED_SUMMARY
        payload["rejected_candidates"] = [
            rejection.model_dump(mode="python") for rejection in rejections
        ]
        payload["content_hash"] = ""
        return replace(
            baseline,
            candidates=candidates,
            selected=selected,
            decision_type=DecisionType.EXPLORE,
            selection_propensity=propensity,
            explanation=StructuredExplanation.model_validate(payload),
        )


def _charge_of(
    receipt: RoutingDecisionReceipt, *, node_class: str
) -> tuple[float, float] | None:
    """``(cost_ucb, baseline_cost_lcb)`` for an EXPLORE receipt of ``node_class``, or ``None``."""

    if receipt.decision_type is not DecisionType.EXPLORE:
        return None
    if receipt.labels.get(NODE_CLASS_LABEL) != node_class:
        return None
    try:
        charged = float(receipt.labels[COST_UCB_LABEL])
        credited = float(receipt.labels[BASELINE_COST_LCB_LABEL])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0.0 <= charged <= 1.0 and 0.0 <= credited <= 1.0):
        return None
    return charged, credited


__all__ = [
    "ALPHA_LABEL",
    "BASELINE_COST_LCB_LABEL",
    "BASELINE_PROPENSITY_LABEL",
    "BETA_LABEL",
    "BREAKERS_TRIPPED_LABEL",
    "CAPS_LABEL",
    "COST_UCB_LABEL",
    "DEFAULT_CONFORMAL_ALPHA",
    "DEFAULT_GAMMA_ZERO",
    "DEFAULT_MAX_ECE",
    "DEFAULT_RECENT_WINDOW",
    "EXPLORED_SUMMARY",
    "NODE_CLASS_LABEL",
    "NOT_EXPLORED_REASON",
    "REFUSED_LABEL",
    "SMOOTHING_EPSILON",
    "WORKTREE_ISOLATION",
    "BanditConfig",
    "GuardedBandit",
    "LedgerRegistry",
    "exploration_policy_for",
    "node_class_of",
    "utility_of",
]
