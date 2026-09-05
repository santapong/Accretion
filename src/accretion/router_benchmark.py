"""The router benchmark: a frozen corpus, eleven comparators, and one honest number.

The v0.1 ACR-ARCH suite settled the shape of a benchmark in this repository — a corpus on
disk, a run id derived from its digests, a replay-only execution source and metrics that can
be recomputed by anyone with the files. This module is that shape applied to the question
v0.4 exists to answer: does choosing a configuration per node beat choosing one good
configuration and keeping it?

**What a run is.** :meth:`RouterBenchmarkRunner.run` takes a list of protocol §8.1 policy
ids, replays each of them over the corpus, and returns per-task rows, a regret report, the
two safety gates and — for a run on the evaluation half — the three R2 estimands from
:mod:`accretion.routing.stats`. Nothing executes. ``execution_source`` other than ``REPLAY``
is refused with the same argument the v0.1 route makes at the API boundary: a live run costs
provider money and changes the world, and it is released by an explicit local gate rather
than by a default argument.

**Two halves, and why the baseline is chosen on one of them.** The corpus declares which
projects may pick the fixed baseline and which projects every quoted number is measured on.
The two lists are disjoint, and :meth:`RouterBenchmarkCorpus.load` proves something stronger
than disjointness before it returns: no *lineage* straddles them. Two forks of one repository
are not two independent projects, and a baseline chosen on the upstream and scored on the
fork is a baseline scored on data it has already seen. The lineage map comes from
:mod:`accretion.routing.split`, which is the module that knows how to find a lineage nobody
declared.

**Gates are not utility.** Protocol §8.2 selects the primary baseline by "the registered
primary/safety criteria", plural, and this module keeps them apart in the type system rather
than in prose: :class:`GateReport` holds the verified-success rate against its floor and the
false-acceptance rate against its ceiling, :class:`RegretReport` holds utility, and neither
can be computed from the other. A router that traded a false acceptance for a better mean
utility would look better in exactly one of the two columns, which is the point of having
two.

**The run id is the corpus.** ``run_id`` is derived from the digests of all four corpus
files, so editing a single trace value produces a different run id and a report that cannot
be mistaken for the earlier one. That is a weaker guarantee than a signature and a much
stronger one than a version string somebody remembers to bump.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from accretion.contracts import (
    BenchmarkExecutionSource,
    ExecutionMode,
    PrincipalRef,
    PrincipalStatus,
    Provider,
    RuntimeHealth,
    StrictModel,
)
from accretion.contracts.refs import (
    CapabilityRef,
    EnvironmentRef,
    RuntimeRef,
    ToolRef,
    VerifierRef,
)
from accretion.contracts.routing import (
    ConfigurationCandidate,
    ConstructionStage,
    DecisionType,
    DistributionEstimate,
    EnvironmentBinding,
    ExecutionConfiguration,
    ModelBinding,
    PredictedOutcomes,
    RoutingDecisionReceipt,
    StructuredExplanation,
    ToolBinding,
    UncertaintySummary,
    UtilityWeights,
    VerifierBinding,
)
from accretion.ids import derived_id
from accretion.routing.baselines import (
    BenchmarkCandidate,
    BenchmarkContext,
    PolicyNotAvailable,
    Selection,
    baseline_for,
)
from accretion.routing.regret import (
    ORACLE_SUBSET_LABEL,
    ORACLE_SUBSET_REGISTERED,
    Outcome,
    RegretReport,
    TaskSelection,
    outcome_key,
    regret_over_selections,
    utility,
)
from accretion.routing.split import SplitViolation, lineage_roots, load_project_registry
from accretion.routing.stats import Estimands, estimands, hierarchical_bootstrap

CORPUS_ROOT = Path(__file__).resolve().parents[2] / "evals" / "router"
"""Where the four ``*.v1.json`` corpus documents live, beside the project registry."""

FROZEN_AT = datetime(2026, 9, 5, tzinfo=UTC)
"""The timestamp every emitted contract carries.

A frozen literal and not ``datetime.now``: a replay that stamped the present would produce a
different ``content_hash`` on every run, and two runs of a replay benchmark that disagree
about their own digests cannot be compared."""

BENCHMARK_WORKSPACE_ID = "wks-router-benchmark"
BENCHMARK_PRINCIPAL = PrincipalRef(
    principal_id="usr-router-benchmark",
    display_name="v0.4 router benchmark",
    status=PrincipalStatus.ACTIVE,
)

_BOOTSTRAP_REPLICATES = 2_000
"""Bootstrap replicates for the clustered interval. Enough that the 2.5% percentile is not
itself noise; small enough that a full benchmark run stays under a second."""


class BenchmarkSplit(StrEnum):
    """Which half of the corpus a run reports rows for.

    ``EVALUATION`` is the honest headline and the only side a superiority claim may quote.
    ``SELECTION`` exists because the half that picks the baseline is also the half a
    developer is *allowed* to look at while iterating, and giving that permission a name is
    better than having people quietly evaluate on the locked side to see how it is going.
    The estimands are computed the same way whichever side is reported: the baseline is
    always chosen on the selection ids and always scored on the evaluation ids.
    """

    SELECTION = "SELECTION"
    EVALUATION = "EVALUATION"


class LiveRunRefused(RuntimeError):
    """A benchmark run asked for something other than ``REPLAY``."""


class CorpusError(ValueError):
    """The corpus on disk is not a corpus this runner will run."""


# --------------------------------------------------------------------------------------
# The corpus documents.
# --------------------------------------------------------------------------------------


class SelectionSplit(StrictModel):
    """The two project lists, and the rule that they are two.

    Validated here rather than at first use so that a leaking corpus fails while it is still
    obviously a corpus problem — the same argument :func:`accretion.routing.split.\
load_project_registry` makes about the registry.
    """

    schema_version: Literal["1.0"] = "1.0"
    selection_project_ids: list[str] = Field(min_length=1, max_length=256)
    evaluation_project_ids: list[str] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def _halves_are_disjoint(self) -> Self:
        shared = sorted(set(self.selection_project_ids) & set(self.evaluation_project_ids))
        if shared:
            raise ValueError(
                f"projects {shared!r} are declared on both sides of the split; a baseline "
                "chosen on data it is then scored on is chosen invalidly"
            )
        return self


class RouterBenchmarkConfig(StrictModel):
    """``config.v1.json``: the registered constants a run is not allowed to choose.

    Every number a result depends on and no policy may vary is here — the utility weights,
    the invalid-action penalty, the latency budget, the two gate thresholds, the split and
    the registered oracle subset. Pre-registration is the whole point: a benchmark whose
    penalty could be tuned after the rows were seen is a benchmark with one free parameter
    per surprising result.
    """

    schema_version: Literal["1.0"] = "1.0"
    suite_version: str = Field(min_length=1, max_length=64)
    configuration_version: str = Field(min_length=1, max_length=64)
    seed: int
    invalid_action_penalty: float = Field(ge=0)
    latency_budget_ms: int = Field(gt=0)
    weights: UtilityWeights
    verified_success_floor: float = Field(ge=0, le=1)
    false_acceptance_ceiling: float = Field(ge=0, le=1)
    selection_split: SelectionSplit
    oracle_candidate_subset: list[str] = Field(min_length=1, max_length=64)
    deterministic_v01_table: dict[ExecutionMode, str] = Field(min_length=1)


class CorpusCandidate(StrictModel):
    """``candidates.v1.json``: one registered configuration and its declared profile."""

    schema_version: Literal["1.0"] = "1.0"
    candidate_id: str = Field(min_length=1, max_length=64)
    provider: Provider
    runtime_id: str = Field(min_length=1, max_length=64)
    runtime_version: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=64)
    tool_profile: str = Field(min_length=1, max_length=64)
    declared_cost: float = Field(ge=0, le=1)
    declared_latency_ms: int = Field(gt=0)
    predicted_success: float = Field(ge=0, le=1)
    eligible_node_classes: list[str] = Field(min_length=1, max_length=16)


class CorpusTask(StrictModel):
    """``tasks.v1.json``: one node, its class, and everything a pre-learning method reads."""

    schema_version: Literal["1.0"] = "1.0"
    task_id: str = Field(min_length=1, max_length=64)
    project_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    node_class: str = Field(min_length=1, max_length=64)
    strategy_decision: ExecutionMode
    planner_choice: str = Field(min_length=1, max_length=64)
    predicted_success: dict[str, float] = Field(default_factory=dict)
    performance_scores: dict[str, float] = Field(default_factory=dict)
    historical_quality: dict[str, float] = Field(default_factory=dict)
    runtime_health: list[RuntimeHealth] = Field(default_factory=list)


class ReplayTrace(StrictModel):
    """``replay-traces.v1.json``: one task × configuration × trial cell.

    ``invalid`` marks a cell whose configuration does not serve the task's node class. Those
    cells are recorded rather than omitted: an invalid selection has to cost something
    observable, and a missing row would make it cost nothing.
    """

    schema_version: Literal["1.0"] = "1.0"
    task_id: str = Field(min_length=1, max_length=64)
    candidate_id: str = Field(min_length=1, max_length=64)
    trial: int = Field(ge=0)
    quality: float = Field(ge=0, le=1)
    cost: float = Field(ge=0, le=1)
    latency_ms: int = Field(ge=0)
    verified: bool
    false_accept: bool
    invalid: bool

    @model_validator(mode="after")
    def _a_refused_cell_verified_nothing(self) -> Self:
        if self.invalid and (self.verified or self.false_accept):
            raise ValueError(
                f"trace {self.task_id}/{self.candidate_id}#{self.trial} is marked invalid "
                "but also claims a verification result; a configuration the node refused "
                "produced no verdict to record"
            )
        if self.false_accept and not self.verified:
            raise ValueError(
                f"trace {self.task_id}/{self.candidate_id}#{self.trial} claims a false "
                "acceptance without a verification pass; a false acceptance *is* a pass that "
                "should not have been given"
            )
        return self


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CorpusError(f"{path} is missing; the router benchmark corpus is incomplete")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CorpusError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def configuration_id_for(candidate_id: str) -> str:
    """The ``cfg_`` contract id one corpus candidate always maps to.

    Derived and not minted, so a receipt written today names the same configuration a receipt
    written next year does, and a cold store populated from a pull request's fixtures lines
    up with a corpus loaded from disk. :func:`accretion.ids.derived_id` keeps the id shape
    every ``ID_KIND`` check expects.
    """

    return derived_id("execution_configuration", "router-benchmark", candidate_id)


@dataclass(frozen=True, slots=True)
class RouterBenchmarkCorpus:
    """The four corpus documents, validated together, with the digests that name them.

    Loading is where the corpus is *proved* rather than merely parsed. Five things are
    checked, and each of them is a way a benchmark could report a number nobody should
    believe: a project on both sides of the split, a project with no side at all, a lineage
    straddling the split, a trace grid with a hole in it, and an ``invalid`` flag that
    disagrees with the eligibility the candidate declared.
    """

    config: RouterBenchmarkConfig
    tasks: tuple[CorpusTask, ...]
    candidates: tuple[CorpusCandidate, ...]
    traces: tuple[ReplayTrace, ...]
    corpus_sha256: str
    trace_sha256: str
    root: Path

    @property
    def run_id(self) -> str:
        """The benchmark run's identity: a function of the corpus digests and nothing else."""

        return derived_id("benchmark_run", self.corpus_sha256, self.trace_sha256)

    @classmethod
    def load(cls, root: Path = CORPUS_ROOT) -> RouterBenchmarkCorpus:
        """Read, validate and digest the corpus at ``root``."""

        config = RouterBenchmarkConfig.model_validate(_read_json(root / "config.v1.json"))
        tasks = tuple(
            CorpusTask.model_validate(item)
            for item in _read_json(root / "tasks.v1.json").get("tasks", [])
        )
        candidates = tuple(
            CorpusCandidate.model_validate(item)
            for item in _read_json(root / "candidates.v1.json").get("candidates", [])
        )
        traces = tuple(
            ReplayTrace.model_validate(item)
            for item in _read_json(root / "replay-traces.v1.json").get("traces", [])
        )
        corpus = cls(
            config=config,
            tasks=tasks,
            candidates=candidates,
            traces=traces,
            corpus_sha256=hashlib.sha256(
                "".join(
                    _sha256(root / name)
                    for name in ("config.v1.json", "tasks.v1.json", "candidates.v1.json")
                ).encode()
            ).hexdigest(),
            trace_sha256=_sha256(root / "replay-traces.v1.json"),
            root=root,
        )
        corpus._validate()
        return corpus

    def _validate(self) -> None:
        if len(self.tasks) < 24:
            raise CorpusError(
                f"the router benchmark needs at least 24 tasks to have any power; found "
                f"{len(self.tasks)}"
            )
        if not 6 <= len(self.candidates) <= 8:
            raise CorpusError(
                f"the corpus registers {len(self.candidates)} configurations; the suite is "
                "specified for six to eight"
            )
        known = {candidate.candidate_id for candidate in self.candidates}
        unknown = sorted(set(self.config.oracle_candidate_subset) - known)
        if unknown:
            raise CorpusError(
                f"the registered oracle subset names unknown configurations {unknown!r}"
            )
        unknown_table = sorted(set(self.config.deterministic_v01_table.values()) - known)
        if unknown_table:
            raise CorpusError(
                f"the v0.1 configuration table names unknown configurations {unknown_table!r}"
            )
        self._validate_split()
        self._validate_grid(known)

    def _validate_split(self) -> None:
        split = self.config.selection_split
        sides = {
            **{project_id: "selection" for project_id in split.selection_project_ids},
            **{project_id: "evaluation" for project_id in split.evaluation_project_ids},
        }
        homeless = sorted({task.project_id for task in self.tasks} - set(sides))
        if homeless:
            raise CorpusError(
                f"projects {homeless!r} carry tasks but are on neither side of the split; a "
                "task that belongs to no split is a task nobody decided how to use"
            )
        roots = lineage_roots(load_project_registry().projects)
        by_root: dict[str, set[str]] = {}
        for project_id, side in sides.items():
            root = roots.get(project_id)
            if root is None:
                raise SplitViolation(
                    f"project {project_id!r} is split but is not in the development registry, "
                    "so its lineage is unknown and the split cannot be shown to be clean"
                )
            by_root.setdefault(root, set()).add(side)
        straddling = sorted(root for root, side_names in by_root.items() if len(side_names) > 1)
        if straddling:
            raise SplitViolation(
                f"lineages {straddling!r} appear on both sides of the split; a fork and its "
                "upstream are not two independent projects, and a baseline chosen on one and "
                "scored on the other is scored on data it has seen"
            )

    def _validate_grid(self, known: set[str]) -> None:
        by_class = {
            candidate.candidate_id: set(candidate.eligible_node_classes)
            for candidate in self.candidates
        }
        node_class = {task.task_id: task.node_class for task in self.tasks}
        cells: dict[tuple[str, str], int] = {}
        for trace in self.traces:
            if trace.task_id not in node_class:
                raise CorpusError(f"trace names unknown task {trace.task_id!r}")
            if trace.candidate_id not in known:
                raise CorpusError(f"trace names unknown configuration {trace.candidate_id!r}")
            serves = node_class[trace.task_id] in by_class[trace.candidate_id]
            if trace.invalid == serves:
                raise CorpusError(
                    f"trace {trace.task_id}/{trace.candidate_id} marks invalid="
                    f"{trace.invalid} while the configuration's declared eligibility says "
                    "otherwise; the corpus contradicts itself about what was admissible"
                )
            cells[(trace.task_id, trace.candidate_id)] = (
                cells.get((trace.task_id, trace.candidate_id), 0) + 1
            )
        for task in self.tasks:
            for candidate in self.candidates:
                trials = cells.get((task.task_id, candidate.candidate_id), 0)
                if trials < 2:
                    raise CorpusError(
                        f"cell {task.task_id}/{candidate.candidate_id} has {trials} trials; "
                        "the grid must be complete with at least two trials per cell, or the "
                        "oracle and the baseline are scored on different task sets"
                    )

    # -- derived views ------------------------------------------------------------------

    def task_ids_for(self, split: BenchmarkSplit) -> tuple[str, ...]:
        """The task ids on one side of the split, sorted."""

        side = (
            self.config.selection_split.selection_project_ids
            if split is BenchmarkSplit.SELECTION
            else self.config.selection_split.evaluation_project_ids
        )
        projects = set(side)
        return tuple(
            sorted(task.task_id for task in self.tasks if task.project_id in projects)
        )

    def pooled_cells(self) -> dict[tuple[str, str], Outcome]:
        """One outcome per task × configuration cell, keyed by the pair itself.

        Trials are pooled here: means for the continuous quantities, **all** for ``verified``
        and **any** for ``false_accept`` and ``invalid``. The asymmetry is deliberate and is
        the conservative reading of each — a configuration has verified a node only if it
        verified it every time it was asked, and one false acceptance in two trials is a
        false acceptance.

        Keyed by the tuple rather than by a string so that the two string keyings below are
        both projections of one table. Deriving one of them from the other by taking a key
        apart would make the encoding reversible, which it is not obliged to be.
        """

        trials: dict[tuple[str, str], list[ReplayTrace]] = {}
        for trace in self.traces:
            trials.setdefault((trace.task_id, trace.candidate_id), []).append(trace)
        pooled: dict[tuple[str, str], Outcome] = {}
        for pair in sorted(trials):
            cell = sorted(trials[pair], key=lambda trace: trace.trial)
            count = len(cell)
            latency_ms = sum(trace.latency_ms for trace in cell) / count
            pooled[pair] = Outcome(
                quality=round(sum(trace.quality for trace in cell) / count, 6),
                cost=round(sum(trace.cost for trace in cell) / count, 6),
                latency=round(min(1.0, latency_ms / self.config.latency_budget_ms), 6),
                latency_ms=round(latency_ms),
                verified=all(trace.verified for trace in cell),
                false_accept=any(trace.false_accept for trace in cell),
                invalid=any(trace.invalid for trace in cell),
            )
        return pooled

    def outcomes(self) -> dict[str, Outcome]:
        """Pooled cells keyed by ``outcome_key(task_id, candidate_id)`` — the corpus's names."""

        return {
            outcome_key(task_id, candidate_id): value
            for (task_id, candidate_id), value in self.pooled_cells().items()
        }

    def stored_outcomes(self) -> dict[str, Outcome]:
        """The same cells, keyed by the ``cfg_`` ids that appear in stored receipts.

        The audit path reads receipts, which name configurations by contract id, while the
        corpus names them by candidate id. Both keyings are projections of
        :meth:`pooled_cells`, so a reviewer recomputing regret from a store and a corpus is
        comparing the same numbers under two names rather than two numbers.
        """

        return {
            outcome_key(task_id, configuration_id_for(candidate_id)): value
            for (task_id, candidate_id), value in self.pooled_cells().items()
        }

    def binary_outcomes(self) -> dict[str, dict[str, int]]:
        """``{candidate_id: {task_id: 0 | 1}}`` — the grid :mod:`~accretion.routing.stats` scores.

        Verified success is the binary, not utility. The estimands are rates and a rate needs
        a yes-or-no; using a thresholded utility would smuggle the objective's weights into
        the success criterion, where protocol §12 does not put them.
        """

        outcomes = self.outcomes()
        return {
            candidate.candidate_id: {
                task.task_id: int(
                    outcomes[outcome_key(task.task_id, candidate.candidate_id)].verified
                )
                for task in self.tasks
            }
            for candidate in self.candidates
        }


# --------------------------------------------------------------------------------------
# The result model.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GateReport:
    """Protocol §8.2's safety criteria, computed from verdicts and never from utility.

    Two rates and the two registered thresholds they are read against. There is no combined
    score and there deliberately is not one: a method that raised verified success by
    accepting more wrongly would move both numbers, and a single figure would let the two
    movements cancel.
    """

    selections: int
    verified_successes: int
    verified_success_rate: float
    verified_success_floor: float
    verified_success_met: bool
    false_acceptances: int
    false_acceptance_rate: float
    false_acceptance_ceiling: float
    false_acceptance_met: bool

    @property
    def both_met(self) -> bool:
        """Whether the method cleared the floor *and* stayed under the ceiling."""

        return self.verified_success_met and self.false_acceptance_met


@dataclass(frozen=True, slots=True)
class BenchmarkRow:
    """One policy's decision on one task, and what the corpus says happened next."""

    task_id: str
    project_id: str
    node_class: str
    policy_id: str
    selected_candidate_id: str
    propensity: float | None
    verified: bool
    false_accept: bool
    invalid: bool
    utility: float


@dataclass(frozen=True, slots=True)
class PolicyResult:
    """Everything one comparator produced, or the reason it produced nothing.

    ``available`` is ``False`` for protocol §8.1 methods no milestone has wired yet, and the
    row stays in the table with ``reason_code`` set to ``NOT_AVAILABLE``. §8.2 requires all
    baselines to remain in the final report, and a report that dropped the unbuilt ones would
    be quietly comparing against a smaller field than it claimed.
    """

    policy_id: str
    available: bool
    reason_code: str | None
    rows: tuple[BenchmarkRow, ...]
    regret: RegretReport | None
    gates: GateReport | None
    mean_utility: float | None
    estimands: Estimands | None
    regret_interval: tuple[float, float] | None


@dataclass(frozen=True, slots=True)
class RouterBenchmarkResult:
    """One benchmark run: the corpus it read, the split it reported, and every comparator."""

    run_id: str
    suite_version: str
    configuration_version: str
    execution_source: BenchmarkExecutionSource
    split: BenchmarkSplit
    corpus_sha256: str
    trace_sha256: str
    selection_task_ids: tuple[str, ...]
    evaluation_task_ids: tuple[str, ...]
    reported_task_ids: tuple[str, ...]
    policies: tuple[PolicyResult, ...]

    def policy(self, policy_id: str) -> PolicyResult:
        """One comparator's result, by protocol id."""

        for result in self.policies:
            if result.policy_id == policy_id:
                return result
        raise KeyError(policy_id)


@dataclass(frozen=True, slots=True)
class StoredDecisions:
    """The contracts one policy's run would have written, ready to persist.

    Emitted rather than persisted: this module has no store handle, and a benchmark that
    wrote to a database as a side effect of computing a number would be a benchmark nobody
    could run twice.
    """

    policy_id: str
    receipts: tuple[RoutingDecisionReceipt, ...]
    candidates: tuple[ConfigurationCandidate, ...]


# --------------------------------------------------------------------------------------
# The runner.
# --------------------------------------------------------------------------------------


class RouterBenchmarkRunner:
    """Replays the protocol's comparators over a frozen corpus.

    Construct it with a corpus and, optionally, a different weight vector: the gates are a
    function of the recorded verdicts and the utility column is a function of the weights, so
    re-running with different weights is how a reader checks that the two are really
    independent rather than being told they are.
    """

    def __init__(
        self,
        corpus: RouterBenchmarkCorpus | None = None,
        *,
        weights: UtilityWeights | None = None,
    ) -> None:
        self.corpus = corpus if corpus is not None else RouterBenchmarkCorpus.load()
        self.weights = weights if weights is not None else self.corpus.config.weights
        # Derived views, computed once. Every one of them is a pure function of the corpus,
        # and recomputing the 36x6 outcome grid inside the per-task loop turned a run into a
        # cubic one. They are caches of immutable data, not state: nothing here can be stale
        # because nothing can write to the corpus after it is loaded.
        self._outcomes = self.corpus.outcomes()
        self._binary = MappingProxyType(self.corpus.binary_outcomes())
        self._selection_ids = self.corpus.task_ids_for(BenchmarkSplit.SELECTION)
        self._evaluation_ids = self.corpus.task_ids_for(BenchmarkSplit.EVALUATION)
        self._candidates = self._benchmark_candidates()
        self._v01_table = MappingProxyType(dict(self.corpus.config.deterministic_v01_table))
        self._oracle_subset = tuple(sorted(self.corpus.config.oracle_candidate_subset))

    # -- context construction -----------------------------------------------------------

    def _benchmark_candidates(self) -> tuple[BenchmarkCandidate, ...]:
        return tuple(
            BenchmarkCandidate(
                candidate_id=candidate.candidate_id,
                provider=candidate.provider,
                runtime_id=candidate.runtime_id,
                runtime_version=candidate.runtime_version,
                model_id=candidate.model_id,
                tool_profile=candidate.tool_profile,
                declared_cost=candidate.declared_cost,
                declared_latency_ms=candidate.declared_latency_ms,
                predicted_success=candidate.predicted_success,
                eligible_node_classes=frozenset(candidate.eligible_node_classes),
            )
            for candidate in sorted(self.corpus.candidates, key=lambda item: item.candidate_id)
        )

    def _context(
        self,
        task: CorpusTask,
        *,
        execution_source: BenchmarkExecutionSource,
        observed_utility: Mapping[str, float] | None,
    ) -> BenchmarkContext:
        """One task's context. ``observed_utility`` is passed for the oracle and nobody else."""

        return BenchmarkContext(
            task_id=task.task_id,
            project_id=task.project_id,
            run_id=task.run_id,
            node_class=task.node_class,
            execution_source=execution_source,
            strategy_decision=task.strategy_decision,
            planner_choice=task.planner_choice,
            deterministic_v01_table=self._v01_table,
            predicted_success=MappingProxyType(dict(task.predicted_success)),
            performance_scores=MappingProxyType(dict(task.performance_scores)),
            runtime_health=tuple(task.runtime_health),
            historical_quality=MappingProxyType(dict(task.historical_quality)),
            selection_task_ids=self._selection_ids,
            evaluation_task_ids=self._evaluation_ids,
            fixed_baseline_outcomes=self._binary,
            oracle_candidate_subset=self._oracle_subset,
            observed_utility=observed_utility,
        )

    def _observed_utility(self, task_id: str, outcomes: Mapping[str, Outcome]) -> dict[str, float]:
        """Per-configuration utility on one task: the oracle's whole information advantage."""

        values: dict[str, float] = {}
        for candidate in self.corpus.candidates:
            outcome = outcomes.get(outcome_key(task_id, candidate.candidate_id))
            if outcome is None or outcome.invalid:
                continue
            values[candidate.candidate_id] = utility(outcome, self.weights)
        return values

    # -- the run ------------------------------------------------------------------------

    def run(
        self,
        policy_ids: Sequence[str],
        *,
        split: BenchmarkSplit = BenchmarkSplit.EVALUATION,
        execution_source: BenchmarkExecutionSource = BenchmarkExecutionSource.REPLAY,
    ) -> RouterBenchmarkResult:
        """Replay ``policy_ids`` over the corpus and report each of them.

        ``execution_source`` must be ``REPLAY``. The refusal is the same one the v0.1
        ACR-ARCH route makes: a live run spends provider quota and mutates repositories, so it
        is released by an explicit local gate and never by a caller passing an enum.

        Every requested policy appears in the result, including the ones no milestone has
        wired: those come back with ``available=False`` and ``reason_code="NOT_AVAILABLE"``
        rather than being dropped, because protocol §8.2 keeps all baselines in the report.
        """

        if execution_source is not BenchmarkExecutionSource.REPLAY:
            raise LiveRunRefused(
                f"the router benchmark replays a frozen corpus; execution_source "
                f"{execution_source.value} requires the explicit local CLI release gate"
            )
        outcomes = self._outcomes
        reported = set(self.corpus.task_ids_for(split))
        tasks = tuple(task for task in self.corpus.tasks if task.task_id in reported)
        candidates = self._candidates
        eligible_by_task = {
            task.task_id: tuple(
                candidate.candidate_id
                for candidate in candidates
                if candidate.serves(task.node_class)
            )
            for task in tasks
        }
        registered = set(self.corpus.config.oracle_candidate_subset)

        results: list[PolicyResult] = []
        for policy_id in policy_ids:
            results.append(
                self._run_policy(
                    policy_id,
                    tasks=tasks,
                    candidates=candidates,
                    outcomes=outcomes,
                    eligible_by_task=eligible_by_task,
                    registered=registered,
                    execution_source=execution_source,
                )
            )
        return RouterBenchmarkResult(
            run_id=self.corpus.run_id,
            suite_version=self.corpus.config.suite_version,
            configuration_version=self.corpus.config.configuration_version,
            execution_source=execution_source,
            split=split,
            corpus_sha256=self.corpus.corpus_sha256,
            trace_sha256=self.corpus.trace_sha256,
            selection_task_ids=self._selection_ids,
            evaluation_task_ids=self._evaluation_ids,
            reported_task_ids=tuple(sorted(reported)),
            policies=tuple(results),
        )

    def selections_for(
        self, policy_id: str, *, split: BenchmarkSplit = BenchmarkSplit.EVALUATION
    ) -> dict[str, Selection]:
        """What one policy chose for every task on ``split``, keyed by task id.

        Exposed because the estimands need a ``{task_id: config_id}`` map for the
        signal-restricted chooser, and because a caller comparing two policies wants the
        choices rather than the summary.
        """

        outcomes = self._outcomes
        reported = set(self.corpus.task_ids_for(split))
        policy = baseline_for(policy_id)
        candidates = self._candidates
        chosen: dict[str, Selection] = {}
        for task in self.corpus.tasks:
            if task.task_id not in reported:
                continue
            observed = (
                self._observed_utility(task.task_id, outcomes) if policy_id == "ORACLE" else None
            )
            context = self._context(
                task,
                execution_source=BenchmarkExecutionSource.REPLAY,
                observed_utility=observed,
            )
            chosen[task.task_id] = policy.select(context, candidates)
        return chosen

    def _run_policy(
        self,
        policy_id: str,
        *,
        tasks: Sequence[CorpusTask],
        candidates: Sequence[BenchmarkCandidate],
        outcomes: Mapping[str, Outcome],
        eligible_by_task: Mapping[str, tuple[str, ...]],
        registered: set[str],
        execution_source: BenchmarkExecutionSource,
    ) -> PolicyResult:
        policy = baseline_for(policy_id)
        rows: list[BenchmarkRow] = []
        selections: list[TaskSelection] = []
        choice: dict[str, str] = {}
        for task in tasks:
            observed = (
                self._observed_utility(task.task_id, outcomes) if policy_id == "ORACLE" else None
            )
            context = self._context(
                task, execution_source=execution_source, observed_utility=observed
            )
            try:
                selection = policy.select(context, candidates)
            except PolicyNotAvailable as error:
                return PolicyResult(
                    policy_id=policy_id,
                    available=False,
                    reason_code=error.reason_code,
                    rows=(),
                    regret=None,
                    gates=None,
                    mean_utility=None,
                    estimands=None,
                    regret_interval=None,
                )
            eligible = eligible_by_task[task.task_id]
            observed_outcome = outcomes.get(outcome_key(task.task_id, selection.candidate_id))
            invalid = selection.candidate_id not in eligible or (
                observed_outcome is not None and observed_outcome.invalid
            )
            selections.append(
                TaskSelection(
                    task_id=task.task_id,
                    project_id=task.project_id,
                    selected_id=selection.candidate_id,
                    eligible_ids=eligible,
                    oracle_subset_ids=tuple(
                        candidate_id for candidate_id in eligible if candidate_id in registered
                    ),
                    task_outcomes={
                        candidate_id: outcomes[outcome_key(task.task_id, candidate_id)]
                        for candidate_id in eligible
                        if outcome_key(task.task_id, candidate_id) in outcomes
                    },
                    selected_outcome=observed_outcome,
                )
            )
            choice[task.task_id] = selection.candidate_id
            rows.append(
                BenchmarkRow(
                    task_id=task.task_id,
                    project_id=task.project_id,
                    node_class=task.node_class,
                    policy_id=policy_id,
                    selected_candidate_id=selection.candidate_id,
                    propensity=selection.propensity,
                    verified=observed_outcome is not None and observed_outcome.verified,
                    false_accept=observed_outcome is not None and observed_outcome.false_accept,
                    invalid=invalid,
                    utility=(
                        round(-self.corpus.config.invalid_action_penalty, 9)
                        if invalid or observed_outcome is None
                        else utility(observed_outcome, self.weights)
                    ),
                )
            )

        report = regret_over_selections(
            selections, self.weights, self.corpus.config.invalid_action_penalty
        )
        return PolicyResult(
            policy_id=policy_id,
            available=True,
            reason_code=None,
            rows=tuple(rows),
            regret=report,
            gates=self._gates(rows),
            mean_utility=(
                round(sum(row.selected_utility for row in report.rows) / len(report.rows), 9)
                if report.rows
                else None
            ),
            estimands=self._estimands(choice),
            regret_interval=self._regret_interval(report),
        )

    def _gates(self, rows: Sequence[BenchmarkRow]) -> GateReport:
        """The two safety rates, read off the recorded verdicts alone.

        Not one weight and not one utility appears in this method, which is the whole reason
        it is a method: a gate that shared an expression with the objective would move when
        the objective was re-weighted, and a safety criterion that a project can re-weight is
        not a safety criterion.
        """

        selections = len(rows)
        verified = sum(1 for row in rows if row.verified)
        false_accepts = sum(1 for row in rows if row.false_accept)
        verified_rate = round(verified / selections, 9) if selections else 0.0
        false_rate = round(false_accepts / selections, 9) if selections else 0.0
        return GateReport(
            selections=selections,
            verified_successes=verified,
            verified_success_rate=verified_rate,
            verified_success_floor=self.corpus.config.verified_success_floor,
            verified_success_met=verified_rate >= self.corpus.config.verified_success_floor,
            false_acceptances=false_accepts,
            false_acceptance_rate=false_rate,
            false_acceptance_ceiling=self.corpus.config.false_acceptance_ceiling,
            false_acceptance_met=false_rate <= self.corpus.config.false_acceptance_ceiling,
        )

    def _estimands(self, choice: Mapping[str, str]) -> Estimands | None:
        """G_out, G_Z and G_learn against the selection-valid best fixed configuration.

        The signal-restricted chooser is M5, the declared success head: protocol §12's Z is
        "what a chooser that sees only the routing signal could do", and until M4 wires the
        real ranker the corpus's per-task predicted success *is* the signal. Returns ``None``
        when the policy made no choice for some evaluation task, which happens whenever a run
        reports the selection half — the estimands are defined on the evaluation half and
        computing them from a partial choice map would quietly change their denominator.
        """

        evaluation = self._evaluation_ids
        if not set(evaluation) <= set(choice):
            return None
        signal = {
            task_id: selection.candidate_id
            for task_id, selection in self.selections_for(
                "M5", split=BenchmarkSplit.EVALUATION
            ).items()
        }
        return estimands(
            dict(self._binary),
            dict(choice),
            signal,
            self._selection_ids,
            evaluation,
            k_configs=len(self.corpus.candidates),
        )

    def _regret_interval(self, report: RegretReport) -> tuple[float, float] | None:
        """A project-clustered percentile interval for this policy's mean regret.

        Projects and not tasks are the resampled unit, because three nodes inside one project
        share an objective, a repository and a policy set, and an interval that treated them
        as three independent draws would be narrower than the data supports by roughly the
        square root of the cluster size. The seed is the corpus's own, so the interval is a
        function of the corpus and the policy and of nothing else.
        """

        groups = report.pairs_by_project()
        if not groups:
            return None
        return hierarchical_bootstrap(
            groups,
            lambda values: sum(values) / len(values),
            _BOOTSTRAP_REPLICATES,
            self.corpus.config.seed,
        )

    # -- emitting the contracts a run would have persisted -------------------------------

    def stored_decisions(self, result: PolicyResult) -> StoredDecisions:
        """The receipts and candidates one policy's run would have written to a store.

        This is what makes the regret number auditable rather than merely reported: persist
        these, throw the runner away, and
        :func:`accretion.routing.regret.regret_from_receipts` reproduces the report from the
        rows alone. Every id is derived from the corpus, and every timestamp is
        :data:`FROZEN_AT`, so the emitted contracts hash the same on every machine.
        """

        by_task = {task.task_id: task for task in self.corpus.tasks}
        profile = {candidate.candidate_id: candidate for candidate in self.corpus.candidates}
        registered = set(self.corpus.config.oracle_candidate_subset)
        receipts: list[RoutingDecisionReceipt] = []
        candidates: list[ConfigurationCandidate] = []
        for row in result.rows:
            task = by_task[row.task_id]
            for candidate in sorted(self.corpus.candidates, key=lambda item: item.candidate_id):
                eligible = task.node_class in candidate.eligible_node_classes
                candidates.append(
                    self._candidate_contract(
                        task,
                        candidate,
                        hard_eligible=eligible,
                        in_subset=candidate.candidate_id in registered,
                    )
                )
            receipts.append(
                self._receipt_contract(task, row, profile[row.selected_candidate_id])
            )
        return StoredDecisions(
            policy_id=result.policy_id,
            receipts=tuple(receipts),
            candidates=tuple(candidates),
        )

    def _configuration(self, candidate: CorpusCandidate) -> ExecutionConfiguration:
        """One corpus candidate as a complete SDD §7.5 tuple, digested deterministically."""

        digest = hashlib.sha256(candidate.candidate_id.encode()).hexdigest()
        return ExecutionConfiguration(  # type: ignore[call-arg]
            # The pydantic mypy plugin does not see the registry §3 header fields
            # `CanonicalContract` supplies through a forward reference resolved by
            # `model_rebuild`. `routing/compatibility.py` carries the same ignore for
            # the same reason; the fields are real and pydantic validates them.
            contract_id=configuration_id_for(candidate.candidate_id),
            created_at=FROZEN_AT,
            created_by=BENCHMARK_PRINCIPAL,
            workspace_id=BENCHMARK_WORKSPACE_ID,
            project_id="prj-router-benchmark",
            environment=EnvironmentBinding(
                environment=EnvironmentRef(
                    environment_id="router-benchmark-sandbox",
                    image_digest=digest,
                    policy_profile="restricted-egress",
                ),
                workspace_isolation="worktree",
            ),
            runtime=RuntimeRef(
                runtime_id=candidate.runtime_id,
                adapter_version=candidate.runtime_version,
                provider=candidate.provider,
                model=candidate.model_id,
                capability_profile_digest=digest,
            ),
            model=ModelBinding(model_id=candidate.model_id, provider=candidate.provider),
            tools=[
                ToolBinding(
                    capability=CapabilityRef(capability_id="fs.read", capability_version="1.0.0"),
                    tool=ToolRef(
                        tool_id=f"toolset-{candidate.tool_profile}", implementation_digest=digest
                    ),
                    binding_id=f"bnd-{candidate.candidate_id}",
                    binding_version="1.0.0",
                )
            ],
            verifier=VerifierBinding(
                verifier=VerifierRef(
                    verifier_contract_id="router-benchmark-verifier",
                    implementation_digest=digest,
                ),
                version="1.0.0",
                verification_spec_hash=digest,
            ),
        )

    def _candidate_contract(
        self,
        task: CorpusTask,
        candidate: CorpusCandidate,
        *,
        hard_eligible: bool,
        in_subset: bool,
    ) -> ConfigurationCandidate:
        head = task.predicted_success.get(candidate.candidate_id, candidate.predicted_success)
        labels = (
            {ORACLE_SUBSET_LABEL: ORACLE_SUBSET_REGISTERED}
            if in_subset and hard_eligible
            else {}
        )
        return ConfigurationCandidate(  # type: ignore[call-arg]
            # The pydantic mypy plugin does not see the registry §3 header fields
            # `CanonicalContract` supplies through a forward reference resolved by
            # `model_rebuild`. `routing/compatibility.py` carries the same ignore for
            # the same reason; the fields are real and pydantic validates them.
            contract_id=derived_id(
                "configuration_candidate", "router-benchmark", task.task_id, candidate.candidate_id
            ),
            created_at=FROZEN_AT,
            created_by=BENCHMARK_PRINCIPAL,
            workspace_id=BENCHMARK_WORKSPACE_ID,
            project_id=task.project_id,
            labels=labels,
            routing_request_id=task.task_id,
            configuration=self._configuration(candidate),
            construction_stage=ConstructionStage.RANK_BY_UTILITY,
            hard_eligible=hard_eligible,
            predicted=_predicted(head, candidate),
            uncertainty_score=round(1.0 - head, 6),
            lower_confidence_success=round(max(0.0, head - 0.1), 6),
        )

    def _receipt_contract(
        self, task: CorpusTask, row: BenchmarkRow, candidate: CorpusCandidate
    ) -> RoutingDecisionReceipt:
        head = task.predicted_success.get(candidate.candidate_id, candidate.predicted_success)
        return RoutingDecisionReceipt(  # type: ignore[call-arg]
            # The pydantic mypy plugin does not see the registry §3 header fields
            # `CanonicalContract` supplies through a forward reference resolved by
            # `model_rebuild`. `routing/compatibility.py` carries the same ignore for
            # the same reason; the fields are real and pydantic validates them.
            contract_id=derived_id(
                "routing_receipt", "router-benchmark", row.policy_id, task.task_id
            ),
            created_at=FROZEN_AT,
            created_by=BENCHMARK_PRINCIPAL,
            workspace_id=BENCHMARK_WORKSPACE_ID,
            project_id=task.project_id,
            routing_request_id=task.task_id,
            node_contract_hash=hashlib.sha256(task.task_id.encode()).hexdigest(),
            selected_configuration_id=configuration_id_for(row.selected_candidate_id),
            selected_configuration_hash=hashlib.sha256(
                row.selected_candidate_id.encode()
            ).hexdigest(),
            decision_type=DecisionType.EXPLOIT,
            selection_propensity=row.propensity,
            uncertainty=UncertaintySummary(
                epistemic_uncertainty=round(1.0 - head, 6),
                lower_confidence_success=round(max(0.0, head - 0.1), 6),
                calibration_version="router-benchmark-v1",
            ),
            workspace_router_version=f"router-benchmark-{row.policy_id}",
            objective_contract_version=1,
            capability_registry_snapshot_id="mcp-router-benchmark-v1",
            policy_snapshot_id="pol-router-benchmark-v1",
            explanation=StructuredExplanation(  # type: ignore[call-arg]
                contract_id=f"explanation-{row.policy_id}-{task.task_id}",
                created_at=FROZEN_AT,
                created_by=BENCHMARK_PRINCIPAL,
                workspace_id=BENCHMARK_WORKSPACE_ID,
                project_id=task.project_id,
                summary=(
                    f"{row.policy_id} selected {row.selected_candidate_id} for a "
                    f"{task.node_class} node under replay."
                ),
            ),
        )


def _predicted(head: float, candidate: CorpusCandidate) -> PredictedOutcomes:
    """A candidate's five declared estimates, as intervals around the corpus's point values."""

    def band(mean: float, width: float, *, upper: float | None = None) -> DistributionEstimate:
        return DistributionEstimate(
            mean=round(mean, 6),
            lower_bound=round(max(0.0, mean - width), 6),
            upper_bound=round(mean + width if upper is None else upper, 6),
            confidence=0.9,
            method="router-benchmark-declared",
        )

    return PredictedOutcomes(
        quality=band(head, 0.08),
        cost=band(candidate.declared_cost, 0.05),
        latency=band(float(candidate.declared_latency_ms), 2_000.0),
        node_verified_success=band(head, 0.08, upper=min(1.0, head + 0.08)),
        run_verified_success=band(
            max(0.0, head - 0.1), 0.1, upper=min(1.0, max(0.0, head - 0.1) + 0.1)
        ),
    )
