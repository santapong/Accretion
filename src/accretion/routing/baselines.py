"""The research protocol's eleven comparators, as policies the router benchmark can run.

Protocol §8.1 names M0 through M9 and an ORACLE, and §8.2 requires that *all* of them stay
in the final report. A table in a document is not a comparator, though: a baseline that is
described but never executed drifts into whatever the author assumed it would do, and the
usual direction of that drift is downwards, because a weak baseline is what makes a result.
So every row of §8.1 is a callable object here, registered under its protocol id. Until v0.4
M10c the three that no milestone had wired were *registered as unavailable* rather than
omitted — a benchmark that silently reports eight of eleven methods has not reported eight
methods, it has reported an unlabelled subset — and :class:`NotAvailablePolicy` stays for the
next release that names a comparator before it builds one.

**The learned three.** M7, M8 and M9 are the v0.4 stack itself, and M10c makes them run: an
offline ranker fitted on the corpus's selection half, that ranker with a project adapter
fitted on each project's own log, and that adapter's choice drawn under §9.5's guarded
exploration against a real cost ledger. They are the only comparators that learn, so they are
the only ones that need to be *told* what they may learn from: :class:`ReplayEvidence` is that
entitlement, it is bounded to the training half, and a policy handed none of it refuses rather
than reaching for a corpus of its own. They are also the comparators protocol §14's ablations
are ablations *of* — every flag in :mod:`accretion.routing.flags` removes something one of
these three would otherwise do.

**One shape for all of them.** :class:`BaselinePolicy` is a protocol with an id and one
method: given a :class:`BenchmarkContext` and the candidate configurations, return the
:class:`Selection` this method would have made. Nothing in the shape lets a policy reach a
store, a clock or the network, so a policy is a pure function of the corpus row it is shown
and the benchmark is replayable by construction.

**What a policy is allowed to see.** The context carries the task's identity, its node class,
the deterministic v0.1 strategy decision, the planner's recorded choice, the declared success
head and the performance-router inputs — everything a *production* selector could have known
at decision time. It does **not** carry the observed outcomes, except in one place:
``observed_utility`` is populated only when the runner is running :class:`OraclePolicy` under
``REPLAY``. That is a structural guard and not a convention. A policy that wanted to peek at
the answer would have to be handed it, and only the oracle ever is.

**The oracle is post-hoc and says so twice.** Protocol §8.3 makes it a benchmark-only upper
bound that MUST NOT be presented as an available production policy, so
:class:`OraclePolicy` refuses any context whose ``execution_source`` is not ``REPLAY`` — the
same refusal the v0.1 ACR-ARCH route makes at the API boundary — and refuses again if the
observed utilities it needs were withheld. It also ranges over the corpus's registered
``oracle_candidate_subset`` and not over every candidate, because §8.3's oracle is the best
*executed* configuration under matched conditions, and an oracle over configurations nobody
ran is a bound on nothing.

**M0 is the one with a trap in it.** The strongest fixed configuration is *chosen*, and an
argmax scored on the data that chose it is biased upward by the winner's curse — which would
shrink every gap the benchmark reports, in the direction that flatters the router. So
:class:`StrongestFixedPolicy` delegates to :func:`accretion.routing.stats.select_best_fixed`
and hands it the selection split ids alone. Picking on the union, or on the evaluation rows,
is the mutation the tests exist to kill.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from accretion.contracts import (
    BenchmarkExecutionSource,
    ExecutionMode,
    Provider,
    RuntimeHealth,
)
from accretion.contracts.routing import ExplorationPolicy, UtilityWeights
from accretion.orchestration.router import PerformanceAwareRuntimeRouter
from accretion.routing.adapter import AdapterArtifact, ProjectAdapter
from accretion.routing.bandit import BanditConfig
from accretion.routing.calibration import (
    CalibrationDataError,
    PlattCalibrator,
    conformal_quantile,
    success_residuals,
)
from accretion.routing.flags import FULL, RouterFeatureFlags
from accretion.routing.gbdt import sigmoid
from accretion.routing.ledger import CostLedger, ExplorationCaps
from accretion.routing.ranker import (
    ORDERED_HEADS,
    LearnedOutcomePredictor,
    OutcomeHead,
    train_ranker,
)
from accretion.routing.regret import Outcome, utility
from accretion.routing.stats import BestFixed, select_best_fixed

BENCHMARK_LEDGER_WORKSPACE = "wks-router-benchmark"
"""The workspace every replay cost ledger is keyed under.

One workspace and not one per policy: the ledger's key is ``(workspace, node class)`` and a
replay is one workspace's history, so keying by policy would give each comparator its own
budget and make the caps mean something different for each of them."""

_NO_FLOATS: Mapping[str, float] = MappingProxyType({})
_NO_MODES: Mapping[ExecutionMode, str] = MappingProxyType({})
_NO_OUTCOMES: Mapping[str, Mapping[str, int]] = MappingProxyType({})


class BaselineError(RuntimeError):
    """Base class for every refusal a baseline policy makes.

    Registered last among the module's exceptions for the same reason the API registers the
    base handler last: a caller that wants to distinguish "not built yet" from "used
    illegally" must be able to, and a caller that does not want to may catch this.
    """

    reason_code = "BASELINE_ERROR"


class PolicyNotAvailable(BaselineError):
    """A protocol baseline whose milestone has not wired it yet.

    Carries :data:`NOT_AVAILABLE` so the runner can report the method as present-and-unrun
    rather than dropping it from the table.
    """

    reason_code = "NOT_AVAILABLE"


class OracleOutsideReplay(BaselineError):
    """The post-hoc oracle was asked to select outside a replay (protocol §8.3)."""

    reason_code = "ORACLE_OUTSIDE_REPLAY"


class UnknownBaseline(BaselineError):
    """A policy id that protocol §8.1 does not name."""

    reason_code = "UNKNOWN_BASELINE"


NOT_AVAILABLE = "NOT_AVAILABLE"
"""The reason code an unwired protocol baseline reports, in the benchmark's result model."""


@dataclass(frozen=True, slots=True)
class Selection:
    """What a policy chose, and how likely it was to choose it.

    ``propensity`` is the behaviour probability §9.5 needs for off-policy evaluation. A
    deterministic policy records ``1.0``; the oracle records ``None``, because it is not a
    behaviour policy and a propensity of one would licence importance-weighting a bound that
    no runtime could have produced.
    """

    candidate_id: str
    propensity: float | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkCandidate:
    """One registered configuration, as the corpus declares it.

    ``eligible_node_classes`` is admissibility and not preference: a policy may still select
    a candidate that does not serve the task's node class, and the regret computation is
    what charges it the registered invalid-action penalty. Filtering the ineligible ones out
    here would hide exactly the failure the safety counters exist to count.
    """

    candidate_id: str
    provider: Provider
    runtime_id: str
    runtime_version: str
    model_id: str
    tool_profile: str
    declared_cost: float
    declared_latency_ms: int
    predicted_success: float
    eligible_node_classes: frozenset[str] = frozenset()

    def serves(self, node_class: str) -> bool:
        """Whether this configuration is admissible for ``node_class``."""

        return node_class in self.eligible_node_classes


@dataclass(frozen=True, slots=True)
class BenchmarkContext:
    """Everything a policy is allowed to know about one task at decision time.

    The first block is the task itself. The second is the evidence the pre-learning methods
    read: the deterministic v0.1 strategy decision (M2), the recorded planner choice (M6),
    the declared success head (M5) and the performance router's inputs (M3, M4). The third
    is what M0 needs to run a *selection-valid* argmax: the two split id lists and the binary
    outcome grid the argmax runs over.

    ``observed_utility`` is the fourth block and is ``None`` for every policy but the oracle.
    """

    task_id: str
    project_id: str
    run_id: str
    node_class: str
    execution_source: BenchmarkExecutionSource
    strategy_decision: ExecutionMode
    planner_choice: str
    deterministic_v01_table: Mapping[ExecutionMode, str] = _NO_MODES
    predicted_success: Mapping[str, float] = _NO_FLOATS
    performance_scores: Mapping[str, float] = _NO_FLOATS
    runtime_health: tuple[RuntimeHealth, ...] = ()
    historical_quality: Mapping[str, float] = _NO_FLOATS
    selection_task_ids: tuple[str, ...] = ()
    evaluation_task_ids: tuple[str, ...] = ()
    fixed_baseline_outcomes: Mapping[str, Mapping[str, int]] = _NO_OUTCOMES
    oracle_candidate_subset: tuple[str, ...] = ()
    observed_utility: Mapping[str, float] | None = None

    def runtime_quality(self) -> dict[tuple[Provider, str], float]:
        """``historical_quality`` in the shape :meth:`PerformanceAwareRuntimeRouter.decide` wants.

        The corpus stores the key as ``"<PROVIDER>|<runtime_version>"`` because JSON has no
        tuple keys; unpacking it here keeps that encoding in one place instead of at every
        call site.
        """

        quality: dict[tuple[Provider, str], float] = {}
        for key in sorted(self.historical_quality):
            provider_name, _, runtime_version = key.partition("|")
            quality[(Provider(provider_name), runtime_version)] = self.historical_quality[key]
        return quality


@runtime_checkable
class BaselinePolicy(Protocol):
    """One row of protocol §8.1: an id, and the choice that method would have made.

    ``runtime_checkable`` so a caller can assert a registered object really is a policy;
    the check is structural and covers the id and the method, which is all the runner uses.
    """

    policy_id: str

    def select(
        self, context: BenchmarkContext, candidates: Sequence[BenchmarkCandidate]
    ) -> Selection:
        """The configuration this method selects for ``context``."""


def _require_candidates(
    policy_id: str, candidates: Sequence[BenchmarkCandidate]
) -> Sequence[BenchmarkCandidate]:
    """Refuse an empty candidate set rather than return a selection of nothing."""

    if not candidates:
        raise BaselineError(f"policy {policy_id} was given no candidate to select from")
    return candidates


def _eligible(
    context: BenchmarkContext, candidates: Sequence[BenchmarkCandidate]
) -> list[BenchmarkCandidate]:
    """The candidates admissible for this task's node class, in candidate-id order."""

    return sorted(
        (candidate for candidate in candidates if candidate.serves(context.node_class)),
        key=lambda candidate: candidate.candidate_id,
    )


class StrongestFixedPolicy:
    """M0. The single configuration with the best rate on the **selection** split.

    One configuration for every task, every project and every node class — that is what
    makes it the fixed baseline the protocol compares against, and it is also why it will
    sometimes select a configuration that does not serve the task's node class. That is not
    a defect to be smoothed over: a fixed configuration really does take the invalid-action
    penalty on the nodes it cannot serve, and a baseline that were quietly allowed to swap
    in a substitute would be an adaptive router wearing the baseline's name.

    The argmax is delegated to :func:`~accretion.routing.stats.select_best_fixed`, which
    refuses overlapping splits, so a corpus that leaked an evaluation task into the selection
    list fails here rather than producing a plausible number.
    """

    policy_id: str = "M0"

    def best_fixed(self, context: BenchmarkContext) -> BestFixed:
        """The selection-split winner, scored honestly on the evaluation split."""

        if not context.fixed_baseline_outcomes:
            raise BaselineError(
                "M0 needs the binary outcome grid to run its argmax; the context carries none"
            )
        return select_best_fixed(
            context.fixed_baseline_outcomes,
            context.selection_task_ids,
            context.evaluation_task_ids,
        )

    def select(
        self, context: BenchmarkContext, candidates: Sequence[BenchmarkCandidate]
    ) -> Selection:
        _require_candidates(self.policy_id, candidates)
        return Selection(candidate_id=self.best_fixed(context).config_id, propensity=1.0)


class CheapestValidPolicy:
    """M1. The admissible configuration with the lowest declared cost.

    "Valid" is the whole of the method: the cheapest configuration overall is usually one
    that cannot serve the node at all, and a benchmark whose cheap baseline is also an
    invalid one would report the penalty as if it were the price of being cheap. Ties break
    on candidate id so the choice does not depend on corpus order.
    """

    policy_id: str = "M1"

    def select(
        self, context: BenchmarkContext, candidates: Sequence[BenchmarkCandidate]
    ) -> Selection:
        _require_candidates(self.policy_id, candidates)
        eligible = _eligible(context, candidates) or sorted(
            candidates, key=lambda candidate: candidate.candidate_id
        )
        cheapest = min(
            eligible, key=lambda candidate: (candidate.declared_cost, candidate.candidate_id)
        )
        return Selection(candidate_id=cheapest.candidate_id, propensity=1.0)


class DeterministicV01Policy:
    """M2. The v0.1 selector's decision, through the corpus's declared configuration table.

    :func:`accretion.planning.select_strategy` chooses an *execution mode*, not a
    configuration, so the corpus declares the mapping from mode to configuration and this
    policy is the lookup. Keeping the table in the corpus rather than in code is what lets
    the benchmark be rerun against a different v0.1 deployment without editing the
    comparator, and it makes the mapping a reviewable line in a data file.
    """

    policy_id: str = "M2"

    def select(
        self, context: BenchmarkContext, candidates: Sequence[BenchmarkCandidate]
    ) -> Selection:
        _require_candidates(self.policy_id, candidates)
        chosen = context.deterministic_v01_table.get(context.strategy_decision)
        if chosen is None:
            raise BaselineError(
                f"the corpus declares no v0.1 configuration for strategy decision "
                f"{context.strategy_decision.value}"
            )
        return Selection(candidate_id=chosen, propensity=1.0)


class PerformanceAwarePolicy:
    """M3. The v0.2 performance-aware runtime router, lifted to whole configurations.

    :class:`~accretion.orchestration.router.PerformanceAwareRuntimeRouter` scores
    *providers* from runtime health, historical quality and observed pressure. Where the
    corpus supplies those inputs this policy runs the real router and then picks the
    best-scoring configuration bound to the provider it chose, so the comparator is the
    shipped v0.2 behaviour and not a re-description of it. Where the inputs are absent — a
    task recorded before health telemetry existed — it falls back to the corpus's declared
    per-candidate scores, which is the same ranking the router would have produced from
    them.
    """

    policy_id: str = "M3"

    def __init__(self, router: PerformanceAwareRuntimeRouter | None = None) -> None:
        self.router = router if router is not None else PerformanceAwareRuntimeRouter()

    def _by_declared_score(
        self, context: BenchmarkContext, candidates: Sequence[BenchmarkCandidate]
    ) -> BenchmarkCandidate:
        """Argmax of the corpus's declared performance score, ties broken by candidate id."""

        return max(
            sorted(candidates, key=lambda candidate: candidate.candidate_id),
            key=lambda candidate: context.performance_scores.get(candidate.candidate_id, 0.0),
        )

    def select(
        self, context: BenchmarkContext, candidates: Sequence[BenchmarkCandidate]
    ) -> Selection:
        _require_candidates(self.policy_id, candidates)
        if context.runtime_health:
            decision = self.router.decide(
                run_id=context.run_id,
                node_id=context.task_id,
                health=list(context.runtime_health),
                historical_quality=context.runtime_quality(),
            )
            if decision.selected_runtime is not None:
                bound = [
                    candidate
                    for candidate in candidates
                    if candidate.provider is decision.selected_runtime
                ]
                if bound:
                    return Selection(
                        candidate_id=self._by_declared_score(context, bound).candidate_id,
                        propensity=1.0,
                    )
        return Selection(
            candidate_id=self._by_declared_score(context, candidates).candidate_id,
            propensity=1.0,
        )


class PerRunPolicy:
    """M4. M3's choice, made once at the head of a run and held for every node in it.

    The comparator that isolates *node-level* adaptivity: if per-run selection captures the
    whole gain then the router's granularity buys nothing, which is a result worth being able
    to report. State is per-instance and keyed by run id, so the runner takes a fresh
    instance for every benchmark run and two runs can never share a held choice; ``reset``
    exists so a caller that reuses one instance has a way to say so out loud.
    """

    policy_id: str = "M4"

    def __init__(self, inner: PerformanceAwarePolicy | None = None) -> None:
        self.inner = inner if inner is not None else PerformanceAwarePolicy()
        self._held: dict[str, Selection] = {}

    def reset(self) -> None:
        """Forget every held per-run choice."""

        self._held.clear()

    def select(
        self, context: BenchmarkContext, candidates: Sequence[BenchmarkCandidate]
    ) -> Selection:
        _require_candidates(self.policy_id, candidates)
        held = self._held.get(context.run_id)
        if held is None:
            held = self.inner.select(context, candidates)
            self._held[context.run_id] = held
        return held


class ModelOnlyPolicy:
    """M5. A node-level router with a success head and nothing else.

    No cost term, no latency term and no admissibility filter: the point of the comparator
    is to show what a predicted-success ranker alone is worth, and giving it the eligibility
    rule would be giving it half of the guarded router. Until M4 wires the real ranker the
    head is the corpus's declared per-task prediction, falling back to the candidate's own
    declared prior where a task does not override it.
    """

    policy_id: str = "M5"

    def _head(self, context: BenchmarkContext, candidate: BenchmarkCandidate) -> float:
        return context.predicted_success.get(candidate.candidate_id, candidate.predicted_success)

    def select(
        self, context: BenchmarkContext, candidates: Sequence[BenchmarkCandidate]
    ) -> Selection:
        _require_candidates(self.policy_id, candidates)
        best = max(
            sorted(candidates, key=lambda candidate: candidate.candidate_id),
            key=lambda candidate: self._head(context, candidate),
        )
        return Selection(candidate_id=best.candidate_id, propensity=1.0)


class PlannerLLMPolicy:
    """M6. Whatever the planning model chose, as the replay trace recorded it.

    There is no model call here and there must not be: the choice is a *recorded* fact about
    a past run, and re-asking a model would make the comparator drift with the provider and
    stop being replayable. A trace missing its planner choice is refused rather than
    defaulted, because a default would silently turn the planner baseline into a fixed one.
    """

    policy_id: str = "M6"

    def select(
        self, context: BenchmarkContext, candidates: Sequence[BenchmarkCandidate]
    ) -> Selection:
        _require_candidates(self.policy_id, candidates)
        if not context.planner_choice:
            raise BaselineError(
                f"task {context.task_id} records no planner choice, so M6 has nothing to replay"
            )
        return Selection(candidate_id=context.planner_choice, propensity=1.0)


class OraclePolicy:
    """ORACLE. The best observed configuration, post hoc, over the registered subset.

    Protocol §8.3 in three refusals. It refuses any ``execution_source`` other than
    ``REPLAY``, because an oracle computed while a run is still deciding is a leak of the
    future into the present. It refuses a context that withheld the observed utilities,
    which is every context the runner builds for every other policy. And it ranges over the
    corpus's registered ``oracle_candidate_subset`` only — the configurations that were
    actually executed under matched conditions — because a bound over configurations nobody
    ran bounds nothing.
    """

    policy_id: str = "ORACLE"

    def select(
        self, context: BenchmarkContext, candidates: Sequence[BenchmarkCandidate]
    ) -> Selection:
        _require_candidates(self.policy_id, candidates)
        if context.execution_source is not BenchmarkExecutionSource.REPLAY:
            raise OracleOutsideReplay(
                f"the oracle is a post-hoc benchmark bound and may not select under "
                f"{context.execution_source.value}; protocol §8.3 forbids presenting it as "
                "an available production policy"
            )
        observed = context.observed_utility
        if not observed:
            raise OracleOutsideReplay(
                f"task {context.task_id} carries no observed utilities, so there is no "
                "post-hoc best to take; the oracle never guesses"
            )
        subset = set(context.oracle_candidate_subset) or {
            candidate.candidate_id for candidate in candidates
        }
        ranked = sorted(candidate_id for candidate_id in observed if candidate_id in subset)
        if not ranked:
            raise OracleOutsideReplay(
                f"task {context.task_id} has no observed outcome for any configuration in "
                "the registered oracle subset"
            )
        best = max(ranked, key=lambda candidate_id: observed[candidate_id])
        return Selection(candidate_id=best, propensity=None)


@dataclass(slots=True)
class NotAvailablePolicy:
    """A protocol §8.1 method whose milestone has not wired it yet.

    Registered, named, and loudly unrun. ``milestone`` says which release owes it, so the
    benchmark report can state *why* a row is empty rather than leaving a reader to assume
    the method was tried and lost.
    """

    policy_id: str
    milestone: str
    description: str
    reason_code: str = field(default=NOT_AVAILABLE)

    def select(
        self, context: BenchmarkContext, candidates: Sequence[BenchmarkCandidate]
    ) -> Selection:
        raise PolicyNotAvailable(
            f"{self.policy_id} ({self.description}) is not wired until {self.milestone}; "
            f"it is reported as {self.reason_code} rather than omitted (protocol §8.2)"
        )


# --------------------------------------------------------------------------------------
# The learned comparators: M7, M8 and M9, composed over the replay corpus.
# --------------------------------------------------------------------------------------


BENCHMARK_FEATURE_SCHEMA_VERSION = "router-benchmark-1.0.0"
"""The vocabulary the benchmark's own feature rows are written under.

Deliberately not :data:`~accretion.contracts.routing.FEATURE_SCHEMA_VERSION`. The production
vocabulary is about node contracts, catalog entries and experience records, and a corpus row
is none of those; declaring the production version over these columns would let a predictor
trained here be assembled against a production artefact whose columns mean something else.
"""

BENCHMARK_RANKER_TREES = 24
BENCHMARK_RANKER_BAGS = 3
"""The replay ranker's size. Smaller than :func:`~accretion.routing.ranker.train_ranker`'s
default, and stated rather than inherited: the training half of this corpus is seventy-two
rows, which supports neither sixty trees nor five bags, and a benchmark that spent three
seconds per ablation fitting a model to noise would be slow *and* overfitted."""

CALIBRATION_PROJECTS = 3
"""How many of the training half's projects are held back to calibrate rather than to fit.

Projects and not rows, for :func:`~accretion.routing.calibration.conformal_quantile`'s
reason: the exchangeable unit is a project, so a calibration split drawn across rows of the
same project would quote a confidence its sample size does not support."""

BENCHMARK_CONFORMAL_ALPHA = 0.25
"""The miscoverage the replay's conformal bounds are quoted at, and why it is not 0.05.

:func:`~accretion.routing.calibration.conformal_quantile` needs more than ``1/alpha - 1``
exchangeable groups for its index to exist, and returns the vacuous residual 1.0 when it does
not. The training half of this corpus is six projects, three of which calibrate, so a 95%
bound over projects is not a bound this corpus can support: every lower bound would collapse
to zero, every candidate would score the invalid-action penalty, and M7 through M9 would tie
on every task and be decided by candidate id. Quoting a 75% bound and saying so is the honest
form of that constraint; quoting 95% and grouping by row instead would be the dishonest one.
"""

ADAPTER_L2 = 1.0
"""The project adapter's ridge. Positive by construction — an unpenalised residual on a
separable project has no finite optimum, which :meth:`ProjectAdapter.fit` refuses outright."""

ADAPTER_HALF_LIFE = 2.0
"""``k`` in :func:`~accretion.routing.adapter.influence`, for a corpus of three-node projects.

Production's :data:`~accretion.routing.adapter.DEFAULT_INFLUENCE_HALF_LIFE` is twenty, which
is right for a workspace where a project accumulates hundreds of resolved outcomes and wrong
here for an arithmetical reason rather than a debatable one: this corpus gives every project
three nodes, so an adapter never sees more than two outcomes before it is applied, and at
``k = 20`` its influence would be at most ``2/22`` — under a tenth of a correction that is
itself small. A4 would then measure the half-life rather than local adaptation, and would
report "no effect" for a component that had been switched off by a constant. The half-life
lives on the artifact precisely so that it is a reviewed number and not a serving-time
argument; this is that review, for this corpus.
"""

REPLAY_BANDIT_CONFIG = BanditConfig(conformal_alpha=BENCHMARK_CONFORMAL_ALPHA)
"""§9.5's algorithm constants for the replay: the shipped ones, at the corpus's alpha.

``gamma_zero`` and ``epsilon`` are :class:`~accretion.routing.bandit.BanditConfig`'s own —
they are properties of inverse-gap weighting and not of this corpus — and only the conformal
level moves, for :data:`BENCHMARK_CONFORMAL_ALPHA`'s reason. Six training projects cannot
support a 90% safety bound either, and a bandit whose clip was silently vacuous would report
"no exploration was admissible" as a finding about the router rather than about the corpus."""

REPLAY_EXPLORATION_POLICY = ExplorationPolicy(alpha=0.5, max_explore_count=6, max_cost=3.0)
"""The exploration budget M9 replays under, declared once and in the open.

In production this is :class:`~accretion.contracts.routing.ObjectiveContract`'s field,
because §9.5 makes the budget the approver's to set and not the router's. A replay has no
objective contract and no approver, so the benchmark declares one — and declares it here,
beside the policy it governs, rather than reading it from the corpus, because it is a
property of *the comparator M9* and not of the tasks M9 is compared on. Changing it changes
what M9 is, which is exactly the sort of change a reviewer should see in a diff.
"""

EXPLORATION_EXCLUDED_NODE_CLASSES = frozenset({"VERIFICATION"})
"""The node classes §9.5's risk gate refuses in replay.

The corpus records no ``allowed_risk_class``, no workspace isolation and no verification-spec
binding, so three of §9.5's five preconditions cannot be evaluated here and this module does
not pretend to evaluate them (``docs/releases/v0.4/m10c-notes.md`` says so in the
report). The one risk statement the corpus *does* support is the node class, and a
verification node is the node whose action produces the verdict every gate in this benchmark
is read from: exploring it would be exploring the measurement instrument.
"""


class ReplayEvidenceRequired(BaselineError):
    """A learned comparator was asked to select without the corpus it learns from.

    Not :class:`PolicyNotAvailable`: M7, M8 and M9 are wired, and reporting them as unbuilt
    because a caller forgot an argument would put a false claim about the milestone into the
    report. The runner always supplies the evidence; this is what a hand-built policy sees.
    """

    reason_code = "REPLAY_EVIDENCE_REQUIRED"


@dataclass(frozen=True, slots=True)
class ReplayTask:
    """One corpus task, as the learned policies read it — identity and decision-time inputs."""

    task_id: str
    project_id: str
    run_id: str
    node_class: str
    strategy_decision: ExecutionMode
    planner_choice: str
    predicted_success: Mapping[str, float] = _NO_FLOATS
    performance_scores: Mapping[str, float] = _NO_FLOATS


@dataclass(frozen=True, slots=True)
class ReplayEvidence:
    """The corpus a learned comparator is allowed to learn from, and nothing else.

    The structural guard on :class:`BenchmarkContext` — no outcomes except for the oracle —
    is about what a policy may see *at decision time*. Training is a different moment with a
    different entitlement: a production router is fitted on resolved history, so a comparator
    that stands for one must be fitted on something. This object is that something, and it is
    bounded in the two ways that matter. ``train_task_ids`` is the selection half, so no
    label from a scored task ever reaches a fit. And the cells are the corpus's own pooled
    outcomes, so the fit is reproducible from the committed files by anyone.

    ``first_trial`` is carried beside ``pooled`` for A10 alone: expected-value stopping is a
    claim about *how many trials were spent*, and a corpus that only kept the pooled cell
    could not express the difference between stopping after one and paying for two.
    """

    tasks: tuple[ReplayTask, ...]
    candidates: tuple[BenchmarkCandidate, ...]
    pooled: Mapping[tuple[str, str], Outcome]
    first_trial: Mapping[tuple[str, str], Outcome]
    train_task_ids: frozenset[str]
    weights: UtilityWeights
    verified_success_floor: float
    invalid_action_penalty: float
    latency_budget_ms: int
    seed: int
    corpus_digest: str

    def task(self, task_id: str) -> ReplayTask:
        """One task by id, or a refusal naming it."""

        for item in self.tasks:
            if item.task_id == task_id:
                return item
        raise BaselineError(f"task {task_id!r} is not in the replay evidence")

    def node_classes(self) -> tuple[str, ...]:
        """Every node class the corpus contains, sorted: the one-hot's fixed vocabulary."""

        return tuple(sorted({task.node_class for task in self.tasks}))

    def strategies(self) -> tuple[ExecutionMode, ...]:
        """Every recorded v0.1 strategy decision, in enum order."""

        return tuple(
            mode for mode in sorted(ExecutionMode) if any(
                task.strategy_decision is mode for task in self.tasks
            )
        )


@dataclass(frozen=True, slots=True)
class _Vocabulary:
    """The two categorical columns' fixed levels, pinned before any row is built."""

    node_classes: tuple[str, ...]
    strategies: tuple[ExecutionMode, ...]

    @property
    def width(self) -> int:
        return 6 + len(self.node_classes) + len(self.strategies) + 2


@dataclass(frozen=True, slots=True)
class _Labels:
    """The node-level and final-run verdicts one ablation reads off the corpus.

    Two labels and not one, because A5 and A6 are separate questions: local credit and the
    global outcome disagree exactly where a node succeeded inside a run that did not, which
    is the case the two ablations exist to price.
    """

    node: Mapping[tuple[str, str], int]
    run: Mapping[tuple[str, str], int]


@dataclass(frozen=True, slots=True)
class _TrainedRouter:
    """One fitted replay router: the predictor, its vocabulary and its retrieved history."""

    predictor: LearnedOutcomePredictor
    vocabulary: _Vocabulary
    evidence_rates: Mapping[tuple[str, str], tuple[float, int]]
    labels: _Labels


@dataclass(frozen=True, slots=True)
class _Scored:
    """One candidate after §9.3, with everything the three learned policies rank or gate on.

    Both success estimates are carried, and both readings of the run head. §7.6 ranks on two
    heads and A9 changes which end of each interval is read, so a record that kept only the
    number that happened to be used would make the ranking unauditable: "this was the
    conservative estimate" is a claim about the *other* number as much as about this one.
    """

    candidate: BenchmarkCandidate
    hard_eligible: bool
    mean_success: float
    lower_confidence_success: float
    adapted_success: float
    run_success: float
    predicted_utility: float
    score: float
    cost_ucb: float
    cost_lcb: float
    prior_logit: float


_TRAINED: dict[tuple[str, tuple[bool, ...]], _TrainedRouter] = {}
"""Fitted routers, cached per process by corpus digest and by the flags that change a fit.

A cache and not state: every entry is a pure function of a frozen corpus and a flag tuple,
so two callers that agree on both are entitled to the same object. It exists because a
benchmark that refitted five bagged heads for every one of eleven ablation runs would spend
its whole budget in :func:`~accretion.routing.ranker.train_ranker`."""


def _training_key(flags: RouterFeatureFlags) -> tuple[bool, ...]:
    """The flags a *fit* depends on. The others change serving and not the artefact.

    Four of §14's ablations change what the router was trained on — which verdict is the
    label (A5, A6), whether a false acceptance counts as a success (A8), and how many trials
    the stop rule paid for (A10) — and one changes the columns it was trained over (A3). The
    remaining five change what is done with the predictions, so they share a cache entry.
    """

    return (
        flags.experience_retrieval,
        flags.node_feedback,
        flags.final_run_feedback,
        flags.independent_verification,
        flags.evi_recovery_stop,
    )


def _clamp01(value: float) -> float:
    """A probability or a normalised cost, forced back into the unit interval.

    The magnitude heads are fitted under squared loss and are free to predict outside
    ``[0, 1]``; :meth:`CostLedger.can_explore` refuses a cost that is not normalised, and
    rightly, because the conservative inequality is stated in normalised units.
    """

    return min(1.0, max(0.0, value))


def _logit(probability: float) -> float:
    """The inverse of :func:`~accretion.routing.gbdt.sigmoid`, clamped away from the poles.

    Clamped and not guarded by an ``if``: a calibrated probability of exactly one is a real
    output of a conformal bound, and an infinite prior logit would make every adapted logit
    infinite and every downstream comparison meaningless.
    """

    bounded = min(1.0 - 1e-9, max(1e-9, probability))
    return math.log(bounded / (1.0 - bounded))


def _cell(evidence: ReplayEvidence, flags: RouterFeatureFlags, key: tuple[str, str]) -> Outcome:
    """The outcome the EVI stop rule would have paid for on one cell.

    With :attr:`~accretion.routing.flags.RouterFeatureFlags.evi_recovery_stop` on, a first
    trial that verified is where the router stops: there is no information left to buy about
    a node that already passed, so the second trial is waste and its result is not observed.
    With the rule off every trial is paid for and pooled, which is the corpus's own
    conservative reading — verified only if verified every time.
    """

    if not flags.evi_recovery_stop:
        return evidence.pooled[key]
    first = evidence.first_trial[key]
    return first if first.verified else evidence.pooled[key]


def _label_table(evidence: ReplayEvidence, flags: RouterFeatureFlags) -> _Labels:
    """The node-level and run-level verdicts, under one ablation's evidence governance.

    A8 is the interesting one. A false acceptance *is* a verification pass — that is what
    makes it false — so with independent verification in place it does not count as a
    success, and with it removed the router learns from a label that says the run passed
    because the run said so. The run label is the node's own verdict conjoined with what the
    rest of the run actually did under its recorded planner choice: a node that succeeded
    inside a run that failed elsewhere gets local credit and no global credit, which is
    precisely the disagreement A5 and A6 are about.
    """

    def verdict(outcome: Outcome) -> int:
        if flags.independent_verification:
            return int(outcome.verified and not outcome.false_accept)
        return int(outcome.verified)

    node: dict[tuple[str, str], int] = {}
    for task in evidence.tasks:
        for candidate in evidence.candidates:
            key = (task.task_id, candidate.candidate_id)
            node[key] = verdict(_cell(evidence, flags, key))

    siblings: dict[str, list[ReplayTask]] = {}
    for task in evidence.tasks:
        siblings.setdefault(task.run_id, []).append(task)
    run: dict[tuple[str, str], int] = {}
    for task in evidence.tasks:
        rest = [
            node[(other.task_id, other.planner_choice)]
            for other in sorted(siblings[task.run_id], key=lambda item: item.task_id)
            if other.task_id != task.task_id
        ]
        elsewhere = int(all(rest)) if rest else 1
        for candidate in evidence.candidates:
            key = (task.task_id, candidate.candidate_id)
            run[key] = node[key] & elsewhere
    return _Labels(node=MappingProxyType(node), run=MappingProxyType(run))


def _head_label(flags: RouterFeatureFlags, labels: _Labels, key: tuple[str, str]) -> int:
    """The success label the ranker's node head is fitted on, under A5.

    With node-level feedback removed there is no local verdict to learn from, so the node
    head is trained on the run's outcome — which is the whole of A5's question: can the
    global signal alone carry local credit?
    """

    return labels.node[key] if flags.node_feedback else labels.run[key]


def _run_head_label(flags: RouterFeatureFlags, labels: _Labels, key: tuple[str, str]) -> int:
    """The label the run head is fitted on, under A6 — the mirror of :func:`_head_label`."""

    return labels.run[key] if flags.final_run_feedback else labels.node[key]


def _evidence_rates(
    evidence: ReplayEvidence, labels: _Labels, task_ids: Sequence[str]
) -> dict[tuple[str, str], tuple[float, int]]:
    """Verified history per ``(node class, configuration)`` over the fitting rows.

    This is the retrieval A3 removes: what a router would have found in the store when it
    asked "how has this configuration done on nodes like this one". Keyed by node class
    rather than by task, because a signature-level key on thirty-six tasks would retrieve
    one row per key and be a lookup of the answer.
    """

    totals: dict[tuple[str, str], list[int]] = {}
    lookup = {task.task_id: task for task in evidence.tasks}
    for task_id in task_ids:
        task = lookup[task_id]
        for candidate in evidence.candidates:
            key = (task.node_class, candidate.candidate_id)
            totals.setdefault(key, []).append(labels.node[(task_id, candidate.candidate_id)])
    return {
        key: (round(sum(values) / len(values), 6), len(values))
        for key, values in sorted(totals.items())
    }


def _feature_row(
    task: ReplayTask,
    candidate: BenchmarkCandidate,
    *,
    vocabulary: _Vocabulary,
    retrieved: tuple[float, int] | None,
    latency_budget_ms: int,
) -> list[float | None]:
    """One ``(task, configuration)`` row in :data:`BENCHMARK_FEATURE_SCHEMA_VERSION`.

    ``retrieved`` is ``None`` under A3 and the two history columns are then written as
    ``None`` rather than as zero. The distinction is the same one §15.1 makes about a failed
    retrieval: zero says "this configuration has never verified", ``None`` says "no history
    was consulted", and a gradient-boosted tree treats the two completely differently.
    """

    row: list[float | None] = [
        candidate.declared_cost,
        _clamp01(candidate.declared_latency_ms / latency_budget_ms),
        candidate.predicted_success,
        task.predicted_success.get(candidate.candidate_id, candidate.predicted_success),
        task.performance_scores.get(candidate.candidate_id, 0.0),
        float(candidate.serves(task.node_class)),
    ]
    row.extend(float(task.node_class == level) for level in vocabulary.node_classes)
    row.extend(float(task.strategy_decision is level) for level in vocabulary.strategies)
    if retrieved is None:
        row.extend((None, None))
    else:
        rate, count = retrieved
        row.extend((rate, float(count)))
    return row


def _train(evidence: ReplayEvidence, flags: RouterFeatureFlags) -> _TrainedRouter:
    """Fit one replay router on the corpus's training half, and cache it for the process.

    The training half is the *selection* half: the projects the protocol already allows a
    method to be chosen on. Nothing from an evaluation project reaches a fit, a calibration
    or a retrieved rate, which is what lets M7, M8 and M9 be quoted on the evaluation half at
    all — a ranker fitted on the rows it is scored on would report the best number in the
    table and mean nothing by it.

    Inside the training half the projects are split again: the last two calibrate, the rest
    fit. That second split is what makes the conformal bound a bound rather than a
    description of the residuals it was fitted on, and it is taken over projects for
    :func:`~accretion.routing.calibration.conformal_quantile`'s reason.

    The retrieved history columns are computed leave-one-task-out for the fitting rows. A
    target encoding that included the row's own label would be a column containing the
    answer, the trees would split on it first, and the model would score beautifully on the
    training half and know nothing.
    """

    key = (evidence.corpus_digest, _training_key(flags))
    cached = _TRAINED.get(key)
    if cached is not None:
        return cached

    labels = _label_table(evidence, flags)
    vocabulary = _Vocabulary(
        node_classes=evidence.node_classes(), strategies=evidence.strategies()
    )
    train_tasks = sorted(
        (task for task in evidence.tasks if task.task_id in evidence.train_task_ids),
        key=lambda item: item.task_id,
    )
    if not train_tasks:
        raise BaselineError(
            "the replay evidence carries no training tasks; a learned comparator cannot be "
            "fitted on an empty selection half"
        )
    projects = sorted({task.project_id for task in train_tasks})
    if len(projects) <= CALIBRATION_PROJECTS:
        raise BaselineError(
            f"the selection half has {len(projects)} projects; at least "
            f"{CALIBRATION_PROJECTS + 1} are needed to fit on some and calibrate on others"
        )
    calibration_projects = set(projects[-CALIBRATION_PROJECTS:])
    fitting = [task for task in train_tasks if task.project_id not in calibration_projects]
    calibrating = [task for task in train_tasks if task.project_id in calibration_projects]

    retrieved_by_key = (
        _evidence_rates(evidence, labels, [task.task_id for task in fitting])
        if flags.experience_retrieval
        else {}
    )

    def retrieval(task: ReplayTask, candidate: BenchmarkCandidate) -> tuple[float, int] | None:
        if not flags.experience_retrieval:
            return None
        return retrieved_by_key.get((task.node_class, candidate.candidate_id), (0.0, 0))

    def loo(task: ReplayTask, candidate: BenchmarkCandidate) -> tuple[float, int] | None:
        """The retrieved rate a fitting row is entitled to: its own label removed."""

        found = retrieval(task, candidate)
        if found is None:
            return None
        rate, count = found
        own = labels.node[(task.task_id, candidate.candidate_id)]
        if count <= 1:
            return (0.0, 0)
        return (round((rate * count - own) / (count - 1), 6), count - 1)

    rows: list[list[float | None]] = []
    targets: dict[OutcomeHead, list[float]] = {head: [] for head in ORDERED_HEADS}
    for task in fitting:
        for candidate in evidence.candidates:
            cell_key = (task.task_id, candidate.candidate_id)
            outcome = _cell(evidence, flags, cell_key)
            rows.append(
                _feature_row(
                    task,
                    candidate,
                    vocabulary=vocabulary,
                    retrieved=loo(task, candidate),
                    latency_budget_ms=evidence.latency_budget_ms,
                )
            )
            targets[OutcomeHead.NODE_VERIFIED_SUCCESS].append(
                float(_head_label(flags, labels, cell_key))
            )
            targets[OutcomeHead.RUN_VERIFIED_SUCCESS].append(
                float(_run_head_label(flags, labels, cell_key))
            )
            targets[OutcomeHead.QUALITY].append(outcome.quality)
            targets[OutcomeHead.COST].append(outcome.cost)
            targets[OutcomeHead.LATENCY].append(outcome.latency)

    artifact = train_ranker(
        rows,
        targets,
        version_id=f"rmv-router-benchmark-{'-'.join(flags.removed) or 'full'}",
        feature_schema_version=BENCHMARK_FEATURE_SCHEMA_VERSION,
        seed=evidence.seed,
        n_trees=BENCHMARK_RANKER_TREES,
        bags=BENCHMARK_RANKER_BAGS,
    )

    # The calibrator is fitted on the *node* head's margins, where
    # `RouterTrainingService._calibrate` fits on the run head's. The difference is not a
    # divergence but the same rule applied to a different store: that service's joined rows
    # carry a final run status and nothing finer, while this corpus records both verdicts,
    # and one calibrator serves both success heads. Fitting it on the head every gate and
    # every score in this module reads is what keeps those numbers probabilities. Fitting it
    # on the run head here would fit it on a label that is true for roughly one row in
    # seventy — three nodes must all verify for a run to — and every calibrated probability,
    # every conformal lower bound and therefore every ranked score would collapse together.
    ensemble = artifact.ensemble(OutcomeHead.NODE_VERIFIED_SUCCESS)
    margins: list[float] = []
    calibration_labels: list[float] = []
    groups: list[str] = []
    for task in calibrating:
        for candidate in evidence.candidates:
            row = _feature_row(
                task,
                candidate,
                vocabulary=vocabulary,
                retrieved=retrieval(task, candidate),
                latency_budget_ms=evidence.latency_budget_ms,
            )
            values = ensemble.bag_values(row, None)
            margins.append(math.fsum(values) / len(values))
            calibration_labels.append(
                float(_head_label(flags, labels, (task.task_id, candidate.candidate_id)))
            )
            groups.append(task.project_id)

    calibrator = PlattCalibrator.fit(margins, calibration_labels)
    probabilities = [calibrator.apply(margin) for margin in margins]
    quantile = conformal_quantile(
        success_residuals(probabilities, calibration_labels), BENCHMARK_CONFORMAL_ALPHA, groups
    )
    trained = _TrainedRouter(
        predictor=LearnedOutcomePredictor(
            artifact=artifact,
            calibrator=calibrator,
            conformal_quantile=quantile,
            feature_schema_version=BENCHMARK_FEATURE_SCHEMA_VERSION,
            alpha=BENCHMARK_CONFORMAL_ALPHA,
        ),
        vocabulary=vocabulary,
        evidence_rates=MappingProxyType(
            _evidence_rates(evidence, labels, [task.task_id for task in train_tasks])
            if flags.experience_retrieval
            else {}
        ),
        labels=labels,
    )
    _TRAINED[key] = trained
    return trained


class LearnedRankerPolicy:
    """M7. The v0.4 offline ranker, and nothing that came after it.

    One fitted :class:`~accretion.routing.ranker.LearnedOutcomePredictor` over the training
    half, applied node by node. No project adapter, no exploration and no online state: M7 is
    the comparator that says how much of v0.4's gain is available from ranking alone, and
    lending it any part of M8 or M9 would make that number unanswerable.

    **What "best" means here.** The predictor returns five estimates, and the ranking is the
    *expected* utility of the two that matter together: a node that fails verification
    produces no value, so the configuration's utility is weighted by its predicted chance of
    verifying and the complement is charged the corpus's registered invalid-action penalty.
    Ranking on utility alone would prefer a cheap configuration that never passes; ranking on
    predicted success alone would prefer an expensive one that always does. Both are
    comparators the protocol already has (M1 and M5), and M7 is not either of them.

    **Where the flags enter.** The candidate set is the eligible one under A2 and the whole
    corpus set without it; the ranking is taken inside one provider family under A1 and
    across all of them without it; the success estimate is the conformal lower bound under A9
    and the calibrated mean without it. Every one of them defaults on.
    """

    policy_id: str = "M7"

    def __init__(
        self,
        *,
        flags: RouterFeatureFlags = FULL,
        evidence: ReplayEvidence | None = None,
    ) -> None:
        self.flags = flags
        self.evidence = evidence

    def _require_evidence(self) -> ReplayEvidence:
        if self.evidence is None:
            raise ReplayEvidenceRequired(
                f"{self.policy_id} is a learned comparator and needs the replay corpus it "
                "is fitted on; construct it through RouterBenchmarkRunner, or pass "
                "`evidence=` to `baseline_for`"
            )
        return self.evidence

    def _adapter(self, context: BenchmarkContext) -> tuple[AdapterArtifact | None, int]:
        """M7 has no local correction. M8 overrides this and M9 inherits M8's."""

        del context
        return None, 0

    def scored(
        self, context: BenchmarkContext, candidates: Sequence[BenchmarkCandidate]
    ) -> tuple[_Scored, ...]:
        """Every candidate's §9.3 estimates for this node, in candidate-id order."""

        evidence = self._require_evidence()
        trained = _train(evidence, self.flags)
        task = ReplayTask(
            task_id=context.task_id,
            project_id=context.project_id,
            run_id=context.run_id,
            node_class=context.node_class,
            strategy_decision=context.strategy_decision,
            planner_choice=context.planner_choice,
            predicted_success=context.predicted_success,
            performance_scores=context.performance_scores,
        )
        artifact, project_count = self._adapter(context)
        weights = evidence.weights
        penalty = evidence.invalid_action_penalty
        results: list[_Scored] = []
        for candidate in sorted(candidates, key=lambda item: item.candidate_id):
            row = _feature_row(
                task,
                candidate,
                vocabulary=trained.vocabulary,
                retrieved=(
                    trained.evidence_rates.get((task.node_class, candidate.candidate_id), (0.0, 0))
                    if self.flags.experience_retrieval
                    else None
                ),
                latency_budget_ms=evidence.latency_budget_ms,
            )
            predicted, _ = trained.predictor.predict(row)
            mean_success = _clamp01(predicted.node_verified_success.mean)
            lower = _clamp01(predicted.node_verified_success.lower_bound)
            run_success = _clamp01(
                predicted.run_verified_success.lower_bound
                if self.flags.uncertainty_gate
                else predicted.run_verified_success.mean
            )
            prior_logit = _logit(mean_success)
            if artifact is None:
                adapted, adapted_lower = mean_success, lower
            else:
                shift = ProjectAdapter.apply(artifact, prior_logit, project_count) - prior_logit
                adapted = _clamp01(sigmoid(prior_logit + shift))
                adapted_lower = _clamp01(sigmoid(_logit(lower) + shift))
            conservative = self.flags.uncertainty_gate
            # The predicted outcome is scored by the same function the report scores the
            # observed one with. A second expression for utility here would be a second
            # definition of it, and the two would drift on the first re-weighting.
            predicted_utility = utility(
                Outcome(
                    quality=_clamp01(
                        predicted.quality.lower_bound if conservative else predicted.quality.mean
                    ),
                    cost=_clamp01(
                        predicted.cost.upper_bound if conservative else predicted.cost.mean
                    ),
                    latency=_clamp01(
                        predicted.latency.upper_bound if conservative else predicted.latency.mean
                    ),
                    latency_ms=0,
                    verified=False,
                    false_accept=False,
                    invalid=False,
                ),
                weights,
            )
            success = adapted_lower if conservative else adapted
            # §7.6 ranks on both success heads and this score reads both. A node that
            # verified inside a run that did not is worth less than one inside a run that
            # did — that is what a *final-run* head is for — so the value of a verified node
            # is its utility less the penalty a failed run costs anyway, and the value of an
            # unverified one is the penalty. With A6 clear the run head is fitted on the
            # node's own verdict, so the term is still computed and carries no independent
            # information, which is precisely what "no global correction" means.
            value = success * (predicted_utility - (1.0 - run_success) * penalty)
            results.append(
                _Scored(
                    candidate=candidate,
                    hard_eligible=candidate.serves(context.node_class),
                    mean_success=adapted,
                    lower_confidence_success=adapted_lower,
                    adapted_success=success,
                    run_success=run_success,
                    predicted_utility=predicted_utility,
                    score=round(value - (1.0 - success) * penalty, 9),
                    cost_ucb=_clamp01(predicted.cost.upper_bound),
                    cost_lcb=_clamp01(predicted.cost.lower_bound),
                    prior_logit=prior_logit,
                )
            )
        return tuple(results)

    def ranked(self, context: BenchmarkContext, scored: Sequence[_Scored]) -> list[_Scored]:
        """The ranked slate: A2's pruning, then A9's gate, then A1's factorization.

        In that order and not another. Pruning is admissibility and comes first because an
        inadmissible configuration is not a worse choice but a refused one; the success gate
        then removes what the router is not confident enough to run; and the hierarchical
        step chooses *within* what survived, because a factorized search that ranged over
        pruned or ungated configurations would be factorizing a set the router had already
        refused.
        """

        floor = self._require_evidence().verified_success_floor
        pool = [item for item in scored if item.hard_eligible]
        if not self.flags.compatibility_pruning or not pool:
            pool = list(scored)
        if self.flags.uncertainty_gate:
            gated = [item for item in pool if item.lower_confidence_success >= floor]
            if gated:
                pool = gated
        if self.flags.hierarchical_construction:
            families: dict[str, list[_Scored]] = {}
            for item in pool:
                families.setdefault(item.candidate.provider.value, []).append(item)
            best_family = max(
                sorted(families),
                key=lambda name: sum(item.score for item in families[name]) / len(families[name]),
            )
            pool = families[best_family]
        return sorted(pool, key=lambda item: (-item.score, item.candidate.candidate_id))

    def select(
        self, context: BenchmarkContext, candidates: Sequence[BenchmarkCandidate]
    ) -> Selection:
        _require_candidates(self.policy_id, candidates)
        ranked = self.ranked(context, self.scored(context, candidates))
        return Selection(candidate_id=ranked[0].candidate.candidate_id, propensity=1.0)


class AdaptedRankerPolicy(LearnedRankerPolicy):
    """M8. M7's ranker with the project adapter fitted on the project's own history.

    **Whose history.** The split is by project, so an evaluation project has *no* rows in the
    training half by construction: there is no offline local data for it and there was never
    meant to be. What a live router would have in its place is its own log — the outcomes of
    the actions it actually took in that project, in the order it took them — and that is
    what this policy fits on. It never reads the outcome of a configuration it did not
    select, and it never reads a task it has not already decided, so the correction at task
    *n* is a function of tasks 1..n-1 alone. An adapter fitted on the whole project would
    score better and would be reading its own answers.

    **Why it is refitted every time.** :meth:`ProjectAdapter.fit` is two parameters by damped
    Newton on a handful of points; refitting is cheaper than reasoning about when a cached
    artifact went stale, and a stale adapter is the failure that would be invisible.

    With A4 clear the whole of the above is skipped and M8 is M7 — which is what makes the
    A4 row a measurement of local adaptation rather than of two implementations.
    """

    policy_id: str = "M8"

    def __init__(
        self,
        *,
        flags: RouterFeatureFlags = FULL,
        evidence: ReplayEvidence | None = None,
    ) -> None:
        super().__init__(flags=flags, evidence=evidence)
        self._history: dict[str, list[tuple[float, int]]] = {}

    def reset(self) -> None:
        """Forget every project's accumulated log."""

        self._history.clear()

    def _adapter(self, context: BenchmarkContext) -> tuple[AdapterArtifact | None, int]:
        if not self.flags.project_adapter:
            return None, 0
        observed = self._history.get(context.project_id, [])
        if not observed:
            return None, 0
        evidence = self._require_evidence()
        trained = _train(evidence, self.flags)
        artifact = ProjectAdapter.fit(
            [logit for logit, _ in observed],
            [label for _, label in observed],
            l2=ADAPTER_L2,
            seed=evidence.seed,
            k=ADAPTER_HALF_LIFE,
            prior_version_id=trained.predictor.version_id,
        )
        return artifact, len(observed)

    def _record(self, context: BenchmarkContext, chosen: _Scored) -> None:
        """Add the outcome of the action just taken to this project's log, and nothing else."""

        evidence = self._require_evidence()
        trained = _train(evidence, self.flags)
        key = (context.task_id, chosen.candidate.candidate_id)
        if key not in trained.labels.node:
            return
        label = _head_label(self.flags, trained.labels, key)
        self._history.setdefault(context.project_id, []).append((chosen.prior_logit, label))

    def select(
        self, context: BenchmarkContext, candidates: Sequence[BenchmarkCandidate]
    ) -> Selection:
        _require_candidates(self.policy_id, candidates)
        ranked = self.ranked(context, self.scored(context, candidates))
        chosen = ranked[0]
        self._record(context, chosen)
        return Selection(candidate_id=chosen.candidate.candidate_id, propensity=1.0)


class GuardedRouterPolicy(AdaptedRankerPolicy):
    """M9. The full v0.4 router: M8's ranked choice, drawn under §9.5's guarded exploration.

    The exploration is the real one in the two ways a replay can be: the distribution is
    §9.5's inverse-gap weighting under the ε-smoothed conformal clip, and the budget is
    enforced by :class:`~accretion.routing.ledger.CostLedger` — the same object the routing
    service charges, with the same conservative inequality and the same absolute caps, keyed
    the same way by node class. Explorations are *settled* at the corpus's observed cost, so
    the inequality that governs the next draw is evaluated against what the last one really
    spent rather than against its upper bound forever.

    **What a replay cannot check, and does not claim to.** §9.5 admits an exploration only on
    a low-risk node, under worktree isolation, bound to the node's verification spec, with an
    ACTIVE version past its shadow gate and six §15.3 breakers untripped. A corpus of tasks
    and traces records none of those five things. The gates that *are* evaluable here — the
    node class (:data:`EXPLORATION_EXCLUDED_NODE_CLASSES`), hard eligibility, a safe set with
    something to choose between, the conformal clip and the ledger — are evaluated, and the
    rest are named in the milestone note rather than silently reported as passed.

    **The propensity is the measurement.** Every row M9 produces carries the probability the
    behaviour policy assigned to the action it took: below one whenever a draw happened, and
    exactly one when a gate refused, which is the arithmetic §9.5's off-policy estimators
    need. With A7 clear no draw ever happens and every propensity is one, which is how the
    A7 row is read.
    """

    policy_id: str = "M9"

    def __init__(
        self,
        *,
        flags: RouterFeatureFlags = FULL,
        evidence: ReplayEvidence | None = None,
        config: BanditConfig | None = None,
        rng: random.Random | None = None,
    ) -> None:
        super().__init__(flags=flags, evidence=evidence)
        self.config = config or REPLAY_BANDIT_CONFIG
        # Seeded from the corpus, not from the clock: a benchmark whose exploration draw
        # changed between two runs of the same corpus would report a different number every
        # time and none of them would be reproducible.
        self.rng = rng if rng is not None else random.Random(
            evidence.seed if evidence is not None else 0
        )
        self._ledgers: dict[str, CostLedger] = {}
        self._explorations = 0

    @property
    def explorations(self) -> int:
        """How many draws the ledger admitted. Read by the report, never by the policy."""

        return self._explorations

    def _ledger(self, node_class: str) -> CostLedger:
        ledger = self._ledgers.get(node_class)
        if ledger is None:
            ledger = CostLedger(workspace_id=BENCHMARK_LEDGER_WORKSPACE, node_class=node_class)
            self._ledgers[node_class] = ledger
        return ledger

    def _beta(self) -> float:
        """§9.5's conformal safety clip, over the training half's projects.

        The loss of one logged decision is how far it fell short of the registered verified-
        success floor: nothing if it verified, the whole floor if it did not. Grouped by
        project because that is the exchangeable unit, and returned as ``0`` — no exploration
        admissible — whenever the quantile cannot be taken, which is the fail-closed
        direction and the same one :meth:`GuardedBandit._beta` takes.
        """

        evidence = self._require_evidence()
        trained = _train(evidence, self.flags)
        losses: list[float] = []
        groups: list[str] = []
        for task in sorted(evidence.tasks, key=lambda item: item.task_id):
            if task.task_id not in evidence.train_task_ids:
                continue
            verified = trained.labels.node[(task.task_id, task.planner_choice)]
            losses.append(0.0 if verified else evidence.verified_success_floor)
            groups.append(task.project_id)
        if not losses:
            return 0.0
        try:
            quantile = conformal_quantile(losses, self.config.conformal_alpha, groups)
        except CalibrationDataError:
            return 0.0
        return max(0.0, min(1.0, 1.0 - quantile))

    def _distribution(
        self, safe: Sequence[_Scored], *, greedy: _Scored, rounds: int, beta: float
    ) -> dict[str, float]:
        """``p(a) = 1/(K + γ_t·gap)`` for every non-greedy safe action, clipped, rest on ``â``.

        §9.5's own weighting, in §9.5's own shape: ``γ_t = γ₀·√t`` grows with the ledger's
        round count so the distribution concentrates as evidence accumulates, the gap is
        clamped at zero so a candidate the ranker put below the greedy choice can never be
        drawn more often than it, and the per-action clip ``β·p_safe`` sends every unit of
        removed mass back to the deterministic choice.
        """

        size = len(safe)
        gamma = self.config.gamma_zero * math.sqrt(rounds)
        probabilities: dict[str, float] = {}
        for item in safe:
            if item.candidate.candidate_id == greedy.candidate.candidate_id:
                continue
            gap = max(0.0, greedy.score - item.score)
            probabilities[item.candidate.candidate_id] = 1.0 / (size + gamma * gap)
        safe_probability = 1.0 - self.config.epsilon + self.config.epsilon / size
        ceiling = beta * safe_probability
        for candidate_id, probability in probabilities.items():
            probabilities[candidate_id] = min(probability, ceiling)
        probabilities[greedy.candidate.candidate_id] = 1.0 - sum(probabilities.values())
        return probabilities

    def _draw(self, safe: Sequence[_Scored], probabilities: Mapping[str, float]) -> _Scored:
        """Inverse transform over ``safe``'s fixed order, so one uniform fixes one action."""

        threshold = self.rng.random()
        cumulative = 0.0
        for item in safe:
            cumulative += probabilities[item.candidate.candidate_id]
            if threshold < cumulative:
                return item
        return safe[-1]

    def select(
        self, context: BenchmarkContext, candidates: Sequence[BenchmarkCandidate]
    ) -> Selection:
        _require_candidates(self.policy_id, candidates)
        evidence = self._require_evidence()
        scored = self.scored(context, candidates)
        ranked = self.ranked(context, scored)
        greedy = ranked[0]
        if not self.flags.guarded_exploration:
            self._record(context, greedy)
            return Selection(candidate_id=greedy.candidate.candidate_id, propensity=1.0)

        safe = sorted(
            (item for item in scored if item.hard_eligible),
            key=lambda item: item.candidate.candidate_id,
        )
        admissible = (
            context.node_class not in EXPLORATION_EXCLUDED_NODE_CLASSES
            and len(safe) > 1
            and any(item.candidate.candidate_id == greedy.candidate.candidate_id for item in safe)
        )
        if not admissible:
            self._record(context, greedy)
            return Selection(candidate_id=greedy.candidate.candidate_id, propensity=1.0)

        ledger = self._ledger(context.node_class)
        probabilities = self._distribution(
            safe, greedy=greedy, rounds=ledger.explore_count + 1, beta=self._beta()
        )
        chosen = self._draw(safe, probabilities)
        verdict = ledger.can_explore(
            candidate_cost_ucb=chosen.cost_ucb,
            baseline_cost_lcb=greedy.cost_lcb,
            alpha=REPLAY_EXPLORATION_POLICY.alpha,
            caps=ExplorationCaps(
                max_explore_count=REPLAY_EXPLORATION_POLICY.max_explore_count,
                max_cost=REPLAY_EXPLORATION_POLICY.max_cost,
            ),
        )
        if not verdict.allowed:
            self._record(context, greedy)
            return Selection(candidate_id=greedy.candidate.candidate_id, propensity=1.0)

        receipt_id = f"{context.task_id}/{chosen.candidate.candidate_id}"
        ledger.record(
            receipt_id, cost_ucb=chosen.cost_ucb, baseline_cost_lcb=greedy.cost_lcb
        )
        observed = evidence.pooled.get((context.task_id, chosen.candidate.candidate_id))
        if observed is not None:
            ledger.settle(receipt_id, _clamp01(observed.cost))
        self._explorations += 1
        self._record(context, chosen)
        return Selection(
            candidate_id=chosen.candidate.candidate_id,
            propensity=round(probabilities[chosen.candidate.candidate_id], 9),
        )


@runtime_checkable
class PolicyFactory(Protocol):
    """How one protocol §8.1 method is built, for the two arguments a learned one needs.

    Every factory takes both keywords and the stateless comparators ignore both, rather than
    the table holding two shapes of callable. A caller reading the table can then see that
    the *only* difference between M1 and M9 is what the constructor does with what it is
    given, which is the claim §8.1 is making about them.
    """

    def __call__(
        self, *, flags: RouterFeatureFlags, evidence: ReplayEvidence | None
    ) -> BaselinePolicy:
        """Build one fresh instance of the method."""


_FACTORIES: Mapping[str, PolicyFactory] = MappingProxyType(
    {
        "M0": lambda *, flags, evidence: StrongestFixedPolicy(),
        "M1": lambda *, flags, evidence: CheapestValidPolicy(),
        "M2": lambda *, flags, evidence: DeterministicV01Policy(),
        "M3": lambda *, flags, evidence: PerformanceAwarePolicy(),
        "M4": lambda *, flags, evidence: PerRunPolicy(),
        "M5": lambda *, flags, evidence: ModelOnlyPolicy(),
        "M6": lambda *, flags, evidence: PlannerLLMPolicy(),
        "M7": lambda *, flags, evidence: LearnedRankerPolicy(flags=flags, evidence=evidence),
        "M8": lambda *, flags, evidence: AdaptedRankerPolicy(flags=flags, evidence=evidence),
        "M9": lambda *, flags, evidence: GuardedRouterPolicy(flags=flags, evidence=evidence),
        "ORACLE": lambda *, flags, evidence: OraclePolicy(),
    }
)
"""How each protocol §8.1 method is built. Factories rather than instances, because M4 holds
a per-run choice, M8 holds a project log and M9 holds a cost ledger, and two benchmark runs
sharing any of the three would be a silent leak.

M7, M8 and M9 stopped being :class:`NotAvailablePolicy` in v0.4 M10c. The class stays,
because §8.2's rule — a method no milestone has wired is reported and not dropped — is a
property of the report and not of which methods happen to be built today."""


BASELINE_REGISTRY: Mapping[str, BaselinePolicy] = MappingProxyType(
    {
        policy_id: factory(flags=FULL, evidence=None)
        for policy_id, factory in _FACTORIES.items()
    }
)
"""Protocol §8.1's table, one instance per row, keyed M0..M9 and ORACLE.

Built from :data:`_FACTORIES` so the listing and the runner cannot disagree about which
methods exist. Read it to enumerate the comparators; call :func:`baseline_for` to get one to
run, because these instances are shared."""


BASELINE_ORDER: tuple[str, ...] = tuple(_FACTORIES)
"""Report order: the protocol's own, M0 through M9 and then the oracle."""


def baseline_for(
    policy_id: str,
    *,
    flags: RouterFeatureFlags = FULL,
    evidence: ReplayEvidence | None = None,
) -> BaselinePolicy:
    """A fresh instance of the registered method, never the shared one.

    The runner calls this rather than indexing :data:`BASELINE_REGISTRY` so that M4's held
    per-run choice, M8's project log and M9's cost ledger each belong to exactly one
    benchmark run.

    ``flags`` and ``evidence`` are ignored by the seven comparators that predate v0.4's
    learned stack and are the whole of what M7, M8 and M9 are built from. Both default to the
    unablated, corpus-less case, which is why a caller that wants a learned policy to *run*
    has to say which corpus it learns from out loud.
    """

    factory = _FACTORIES.get(policy_id)
    if factory is None:
        raise UnknownBaseline(
            f"{policy_id!r} is not a protocol §8.1 method; the registered ids are "
            f"{list(BASELINE_ORDER)!r}"
        )
    return factory(flags=flags, evidence=evidence)
