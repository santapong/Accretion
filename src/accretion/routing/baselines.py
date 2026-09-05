"""The research protocol's eleven comparators, as policies the router benchmark can run.

Protocol §8.1 names M0 through M9 and an ORACLE, and §8.2 requires that *all* of them stay
in the final report. A table in a document is not a comparator, though: a baseline that is
described but never executed drifts into whatever the author assumed it would do, and the
usual direction of that drift is downwards, because a weak baseline is what makes a result.
So every row of §8.1 is a callable object here, registered under its protocol id, and the
three that no milestone has wired yet are *registered as unavailable* rather than omitted —
a benchmark that silently reports eight of eleven methods has not reported eight methods, it
has reported an unlabelled subset.

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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from accretion.contracts import (
    BenchmarkExecutionSource,
    ExecutionMode,
    Provider,
    RuntimeHealth,
)
from accretion.orchestration.router import PerformanceAwareRuntimeRouter
from accretion.routing.stats import BestFixed, select_best_fixed

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


_FACTORIES: Mapping[str, Callable[[], BaselinePolicy]] = MappingProxyType(
    {
        "M0": StrongestFixedPolicy,
        "M1": CheapestValidPolicy,
        "M2": DeterministicV01Policy,
        "M3": PerformanceAwarePolicy,
        "M4": PerRunPolicy,
        "M5": ModelOnlyPolicy,
        "M6": PlannerLLMPolicy,
        "M7": lambda: NotAvailablePolicy(
            policy_id="M7",
            milestone="v0.4 M4",
            description="v0.4 offline ranker only",
        ),
        "M8": lambda: NotAvailablePolicy(
            policy_id="M8",
            milestone="v0.4 M5",
            description="v0.4 workspace prior plus project adapter",
        ),
        "M9": lambda: NotAvailablePolicy(
            policy_id="M9",
            milestone="v0.4 M7",
            description="full v0.4 guarded router",
        ),
        "ORACLE": OraclePolicy,
    }
)
"""How each protocol §8.1 method is built. Factories rather than instances, because M4 holds
per-run state and two benchmark runs sharing one held choice would be a silent leak."""


BASELINE_REGISTRY: Mapping[str, BaselinePolicy] = MappingProxyType(
    {policy_id: factory() for policy_id, factory in _FACTORIES.items()}
)
"""Protocol §8.1's table, one instance per row, keyed M0..M9 and ORACLE.

Built from :data:`_FACTORIES` so the listing and the runner cannot disagree about which
methods exist. Read it to enumerate the comparators; call :func:`baseline_for` to get one to
run, because these instances are shared."""


BASELINE_ORDER: tuple[str, ...] = tuple(_FACTORIES)
"""Report order: the protocol's own, M0 through M9 and then the oracle."""


def baseline_for(policy_id: str) -> BaselinePolicy:
    """A fresh instance of the registered method, never the shared one.

    The runner calls this rather than indexing :data:`BASELINE_REGISTRY` so that M4's held
    per-run choice belongs to exactly one benchmark run.
    """

    factory = _FACTORIES.get(policy_id)
    if factory is None:
        raise UnknownBaseline(
            f"{policy_id!r} is not a protocol §8.1 method; the registered ids are "
            f"{list(BASELINE_ORDER)!r}"
        )
    return factory()
