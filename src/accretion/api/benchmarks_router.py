"""HTTP adapter for the v0.4 router benchmark (protocol §8, SDD §11.4).

Two routes over :class:`~accretion.router_benchmark.RouterBenchmarkRunner`, and the shape is
the one the v0.1 ACR-ARCH suite settled and every v0.2 benchmark route since has kept: a
``GET`` that hands back the last summary this process produced, and a ``POST .../run`` that
recomputes it. Nothing here executes a provider, spends a token or writes a row.

**Why the refusal is at the route and not only in the runner.**
:meth:`~accretion.router_benchmark.RouterBenchmarkRunner.run` already refuses any execution
source but ``REPLAY``, but it refuses with a :class:`~accretion.router_benchmark.LiveRunRefused`,
which is a ``RuntimeError`` and would reach a client as a 500 — an internal fault, for a
request that was merely not allowed. So the route makes the same check the v0.1 route makes,
one frame earlier, and raises a :class:`~accretion.routing.errors.RoutingError` carrying
:data:`LIVE_RUN_REFUSED` and a 422. The runner's own refusal is caught as well rather than
trusted to be unreachable: two guards for one rule is cheap, and the alternative is a 500 the
first time a future caller reaches the runner by another path.

**Why the summary is a mirror and not the dataclass.**
:class:`~accretion.router_benchmark.RouterBenchmarkResult` is a frozen dataclass carrying
every per-task row — several hundred of them — because that is what a local analysis wants.
An HTTP client wants the policy table. :class:`RouterBenchmarkSummary` is that table as a
``StrictModel``, so the response is validated on the way out, appears in the OpenAPI document
and generates a TypeScript type, and so that adding a field to the analysis object is not
automatically an API change.

**The cache.** The corpus is immutable on disk and a run is a pure function of it, so the
runner is built once per process and the last summary is held on ``app.state``. Holding it on
the application and not in a module global is what keeps two applications in one test session
independent; there is no lifespan line, because there is no service to construct — a benchmark
that needed one would be a benchmark with state.
"""

from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from accretion.contracts import BenchmarkExecutionSource, StrictModel
from accretion.router_benchmark import (
    BenchmarkSplit,
    GateReport,
    LiveRunRefused,
    PolicyResult,
    RouterBenchmarkResult,
    RouterBenchmarkRunner,
)
from accretion.routing.baselines import BASELINE_ORDER
from accretion.routing.errors import RoutingError
from accretion.routing.stats import Estimands

ROUTER_BENCHMARK_PATH = "/api/v2/benchmarks/router"
ROUTER_BENCHMARK_RUN_PATH = ROUTER_BENCHMARK_PATH + "/run"

LIVE_RUN_REFUSED = "ROUTER_BENCHMARK_LIVE_RUN_REFUSED"
"""The stable code a non-``REPLAY`` request is refused with, at 422.

422 and not 400: the body parsed and every field in it was well formed, and what the request
asked for is a thing this deployment will not do. A client distinguishing "you sent nonsense"
from "you may not ask that over HTTP" is the difference between fixing a payload and finding
the local release gate.
"""

_SUMMARY_ATTRIBUTE = "router_benchmark_summary"
"""``app.state`` key for the last summary. Per application, so tests do not share one."""


# --------------------------------------------------------------------------------------
# The response mirror.
# --------------------------------------------------------------------------------------


class RouterBestFixedSummary(StrictModel):
    """The selection-valid baseline: what won the argmax, and what it then scored.

    Both rates are carried for the reason
    :class:`~accretion.routing.stats.BestFixed` gives — ``selection_rate`` is inflated by
    having won the selection and ``evaluation_rate`` is the honest one — so that a dashboard
    can show the winner's curse rather than describe it.
    """

    schema_version: Literal["1.0"] = "1.0"
    config_id: str
    selection_successes: int
    selection_trials: int
    selection_rate: float
    evaluation_successes: int
    evaluation_trials: int
    evaluation_rate: float
    evaluation_interval: tuple[float, float]


class RouterEstimandsSummary(StrictModel):
    """Protocol §12's three gains, their intervals and the recovered fraction when defined.

    ``recovered_fraction`` stays ``None`` whenever the opportunity gap's lower limit is not
    strictly positive, exactly as :func:`~accretion.routing.stats.estimands` decides it. The
    field is nullable in the schema for that reason and not as a convenience: a share of an
    opportunity nobody has shown to exist is not a small number, it is not a number.
    """

    schema_version: Literal["1.0"] = "1.0"
    g_out: float
    g_z: float
    g_learn: float
    intervals: dict[str, tuple[float, float]]
    recovered_fraction: float | None = None
    best_fixed: RouterBestFixedSummary
    adjusted_alpha: float


class RouterGateSummary(StrictModel):
    """Protocol §8.2's two safety rates and the registered thresholds they are read against.

    ``both_met`` is carried rather than left to the client to compute, because it is a
    property of the gates and not of a reader: the two rates are deliberately not combined
    into a score, and a client that ANDed them itself would eventually AND them wrongly.
    """

    schema_version: Literal["1.0"] = "1.0"
    selections: int
    verified_successes: int
    verified_success_rate: float
    verified_success_floor: float
    verified_success_met: bool
    false_acceptances: int
    false_acceptance_rate: float
    false_acceptance_ceiling: float
    false_acceptance_met: bool
    both_met: bool


class RouterSafetySummary(StrictModel):
    """The four safety counters the regret report keeps beside its utility column."""

    schema_version: Literal["1.0"] = "1.0"
    invalid_selections: int
    unverified_selections: int
    false_acceptances: int
    deferred_to_human: int


class RouterPolicySummary(StrictModel):
    """One comparator's line, or the reason it has none.

    Unavailable §8.1 methods keep their row with ``available`` false and every measurement
    ``None``: §8.2 requires all baselines to remain in the final report, and an API that
    dropped the unbuilt ones would let a client believe the field was smaller than it is.
    """

    schema_version: Literal["1.0"] = "1.0"
    policy_id: str
    available: bool
    reason_code: str | None = None
    selections: int
    mean_utility: float | None = None
    mean_regret: float | None = None
    total_regret: float | None = None
    regret_by_project: dict[str, float] = Field(default_factory=dict)
    regret_interval: tuple[float, float] | None = None
    safety: RouterSafetySummary | None = None
    gates: RouterGateSummary | None = None
    estimands: RouterEstimandsSummary | None = None


class RouterBenchmarkSummary(StrictModel):
    """One benchmark run as an API document: the corpus it read and every comparator.

    The digests are part of the response and not metadata: a number quoted from this endpoint
    is only evidence if the reader can say which bytes produced it, and ``run_id`` is derived
    from those two digests alone.
    """

    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    suite_version: str
    configuration_version: str
    execution_source: Literal["REPLAY"] = "REPLAY"
    split: BenchmarkSplit
    corpus_sha256: str
    trace_sha256: str
    selection_task_ids: list[str]
    evaluation_task_ids: list[str]
    reported_task_ids: list[str]
    policies: list[RouterPolicySummary]


class RouterBenchmarkRunCreate(BaseModel):
    """The request body: which half to report, and an execution source that must be REPLAY.

    A plain ``BaseModel`` with ``extra="forbid"`` and not a ``StrictModel``, following
    :class:`~accretion.api.router_admin` — a request body is not a persisted contract, so it
    carries no ``schema_version``, but a misspelt field is still a 422 rather than a silently
    ignored one.

    ``execution_source`` is typed as the full enum rather than as ``Literal[REPLAY]`` on
    purpose. Narrowing it here would make a live request a schema error, and the refusal this
    route owes is a *policy* refusal with a code a client can act on, not a validation
    complaint about a value the enum genuinely has.
    """

    model_config = ConfigDict(extra="forbid")

    execution_source: BenchmarkExecutionSource = BenchmarkExecutionSource.REPLAY
    split: BenchmarkSplit = BenchmarkSplit.EVALUATION


# --------------------------------------------------------------------------------------
# Projection.
# --------------------------------------------------------------------------------------


def _gates(report: GateReport) -> RouterGateSummary:
    return RouterGateSummary(
        selections=report.selections,
        verified_successes=report.verified_successes,
        verified_success_rate=report.verified_success_rate,
        verified_success_floor=report.verified_success_floor,
        verified_success_met=report.verified_success_met,
        false_acceptances=report.false_acceptances,
        false_acceptance_rate=report.false_acceptance_rate,
        false_acceptance_ceiling=report.false_acceptance_ceiling,
        false_acceptance_met=report.false_acceptance_met,
        both_met=report.both_met,
    )


def _estimands(values: Estimands) -> RouterEstimandsSummary:
    best = values.best_fixed
    return RouterEstimandsSummary(
        g_out=values.g_out,
        g_z=values.g_z,
        g_learn=values.g_learn,
        intervals={key: (bounds[0], bounds[1]) for key, bounds in values.intervals.items()},
        recovered_fraction=values.recovered_fraction,
        best_fixed=RouterBestFixedSummary(
            config_id=best.config_id,
            selection_successes=best.selection_successes,
            selection_trials=best.selection_trials,
            selection_rate=best.selection_rate,
            evaluation_successes=best.evaluation_successes,
            evaluation_trials=best.evaluation_trials,
            evaluation_rate=best.evaluation_rate,
            evaluation_interval=best.evaluation_interval,
        ),
        adjusted_alpha=values.adjusted_alpha,
    )


def _policy(result: PolicyResult) -> RouterPolicySummary:
    regret = result.regret
    return RouterPolicySummary(
        policy_id=result.policy_id,
        available=result.available,
        reason_code=result.reason_code,
        selections=len(result.rows),
        mean_utility=result.mean_utility,
        mean_regret=None if regret is None else regret.mean_regret,
        total_regret=None if regret is None else regret.total_regret,
        regret_by_project={} if regret is None else dict(regret.regret_by_project),
        regret_interval=result.regret_interval,
        safety=(
            None
            if regret is None
            else RouterSafetySummary(
                invalid_selections=regret.safety.invalid_selections,
                unverified_selections=regret.safety.unverified_selections,
                false_acceptances=regret.safety.false_acceptances,
                deferred_to_human=regret.safety.deferred_to_human,
            )
        ),
        gates=None if result.gates is None else _gates(result.gates),
        estimands=None if result.estimands is None else _estimands(result.estimands),
    )


def summarise(result: RouterBenchmarkResult) -> RouterBenchmarkSummary:
    """Project one benchmark result onto the API document, in the protocol's report order."""

    return RouterBenchmarkSummary(
        run_id=result.run_id,
        suite_version=result.suite_version,
        configuration_version=result.configuration_version,
        split=result.split,
        corpus_sha256=result.corpus_sha256,
        trace_sha256=result.trace_sha256,
        selection_task_ids=list(result.selection_task_ids),
        evaluation_task_ids=list(result.evaluation_task_ids),
        reported_task_ids=list(result.reported_task_ids),
        policies=[_policy(policy) for policy in result.policies],
    )


# --------------------------------------------------------------------------------------
# The routes.
# --------------------------------------------------------------------------------------

router = APIRouter(tags=["benchmarks"])

_runner: RouterBenchmarkRunner | None = None


def runner() -> RouterBenchmarkRunner:
    """The process-wide runner, built on first use.

    A cache of immutable data and not state: the corpus is validated and digested at load and
    nothing can write to it afterwards, so a second load would read the same bytes and pay for
    the 432-trace validation again. The same argument
    :class:`~accretion.router_benchmark.RouterBenchmarkRunner` makes about its own derived
    views, one level up.
    """

    global _runner
    if _runner is None:
        _runner = RouterBenchmarkRunner()
    return _runner


def _replay(
    split: BenchmarkSplit,
    execution_source: BenchmarkExecutionSource = BenchmarkExecutionSource.REPLAY,
) -> RouterBenchmarkSummary:
    """Replay every §8.1 comparator over one half, turning a live refusal into a 422.

    The caller's ``execution_source`` is forwarded rather than replaced, so the runner's own
    refusal is a real second guard behind the route's: a future caller that skips the route
    check still cannot make the runner spend provider quota, and still gets the 422 rather
    than a 500.
    """

    try:
        result = runner().run(
            list(BASELINE_ORDER), split=split, execution_source=execution_source
        )
    except LiveRunRefused as exc:
        raise RoutingError(LIVE_RUN_REFUSED, str(exc), status_code=422) from exc
    return summarise(result)


def _cached(request: Request) -> RouterBenchmarkSummary | None:
    summary = getattr(request.app.state, _SUMMARY_ATTRIBUTE, None)
    return None if summary is None else cast(RouterBenchmarkSummary, summary)


@router.get(ROUTER_BENCHMARK_PATH, response_model=RouterBenchmarkSummary)
async def get_router_benchmark(request: Request) -> RouterBenchmarkSummary:
    """The last summary this process produced, or a fresh evaluation-half run if there is none.

    Reading is unauthenticated beyond the session the middleware already established, and
    unscoped to a workspace, for the reason every benchmark route in this API is: the corpus
    is a committed development fixture, the run touches no workspace's data, and a number
    computed from files in the repository is not somebody's tenant record.

    The fallback is a run and not a 404. A dashboard asking a fresh process what the router
    benchmark says should get the answer, and "nobody has pressed run yet" is a fact about
    this process rather than about the benchmark.
    """

    summary = _cached(request)
    if summary is None:
        summary = _replay(BenchmarkSplit.EVALUATION)
        setattr(request.app.state, _SUMMARY_ATTRIBUTE, summary)
    return summary


@router.post(ROUTER_BENCHMARK_RUN_PATH, response_model=RouterBenchmarkSummary)
async def run_router_benchmark(
    payload: RouterBenchmarkRunCreate, request: Request
) -> RouterBenchmarkSummary:
    """Replay the corpus and make the result what ``GET`` will return next.

    ``execution_source`` must be ``REPLAY``. The check is the one
    ``POST /api/v1/benchmarks/acr-arch/run`` makes and it is made here, before the runner, so
    that the refusal carries :data:`LIVE_RUN_REFUSED` at 422 instead of surfacing the runner's
    ``RuntimeError`` as a 500. A live router benchmark spends provider quota against real
    repositories and is released by an explicit local gate, never by a caller sending an enum.
    """

    if payload.execution_source is not BenchmarkExecutionSource.REPLAY:
        raise RoutingError(
            LIVE_RUN_REFUSED,
            (
                f"the router benchmark replays a frozen corpus; execution_source "
                f"{payload.execution_source.value} requires the explicit local CLI release gate"
            ),
            status_code=422,
        )
    summary = _replay(payload.split, payload.execution_source)
    setattr(request.app.state, _SUMMARY_ATTRIBUTE, summary)
    return summary


__all__ = [
    "LIVE_RUN_REFUSED",
    "ROUTER_BENCHMARK_PATH",
    "ROUTER_BENCHMARK_RUN_PATH",
    "RouterBenchmarkRunCreate",
    "RouterBenchmarkSummary",
    "RouterBestFixedSummary",
    "RouterEstimandsSummary",
    "RouterGateSummary",
    "RouterPolicySummary",
    "RouterSafetySummary",
    "router",
    "runner",
    "summarise",
]
