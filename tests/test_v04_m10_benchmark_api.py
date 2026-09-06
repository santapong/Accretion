"""SDD §11.4 HTTP contract for the v0.4 router benchmark route.

Three things this route owes, and one it must never do. It owes a summary that a client can
parse without knowing the analysis dataclasses, a refusal with a stable code when asked for a
live run, and a report that still contains the comparators no milestone has built. What it
must never do is spend provider quota because a caller sent an enum, and the test that pins
that is the first one below: a non-``REPLAY`` request is a 422 with
:data:`~accretion.api.benchmarks_router.LIVE_RUN_REFUSED`, and specifically not a 500, because
a 500 would mean the refusal escaped as a fault rather than being made as a decision.

The application under test is assembled here rather than imported from ``main``: these routes
touch no store, and building the whole production application would make a benchmark test
require a database it has no use for. One test does read the real application, and only to
check that the router is included and its response model published.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from accretion.api.benchmarks_router import (
    LIVE_RUN_REFUSED,
    ROUTER_BENCHMARK_PATH,
    ROUTER_BENCHMARK_RUN_PATH,
    RouterBenchmarkSummary,
    router,
    runner,
    summarise,
)
from accretion.contracts import Principal, PrincipalStatus
from accretion.ids import new_id
from accretion.router_benchmark import BenchmarkSplit, RouterBenchmarkRunner
from accretion.routing.baselines import BASELINE_ORDER
from accretion.routing.errors import RoutingError


def build_app() -> FastAPI:
    """The route under the two collaborators ``main`` gives it: a principal and the handler.

    The ``RoutingError`` handler mirrors ``main.routing_error_handler`` exactly — status and
    code taken off the error — so a status asserted here is the status a client sees.
    """

    application = FastAPI()
    application.include_router(router)

    @application.middleware("http")
    async def attach_principal(request: Request, call_next: Any) -> Any:
        request.state.principal = Principal(
            principal_id=new_id("principal"),
            issuer="test",
            subject="operator",
            display_name="Test Operator",
            status=PrincipalStatus.ACTIVE,
        )
        return await call_next(request)

    @application.exception_handler(RoutingError)
    async def routing_failure(request: Request, exc: RoutingError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
        )

    return application


def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=build_app()), base_url="http://test")


def test_the_runner_refuses_a_live_source_even_past_the_route_guard() -> None:
    """Two guards for one rule: the route's check and the runner's own.

    Called directly, past the route, with a LIVE source, the replay helper must still come
    back as the 422 the route would have sent — which is only true because it forwards the
    caller's ``execution_source`` to the runner instead of hard-wiring REPLAY.
    """

    from accretion.api import benchmarks_router as module
    from accretion.router_benchmark import BenchmarkExecutionSource

    with pytest.raises(RoutingError) as refusal:
        module._replay(BenchmarkSplit.EVALUATION, BenchmarkExecutionSource.LIVE)
    assert refusal.value.status_code == 422


async def test_a_live_run_is_refused_with_422_and_a_stable_code() -> None:
    async with client() as caller:
        response = await caller.post(
            ROUTER_BENCHMARK_RUN_PATH, json={"execution_source": "LIVE"}
        )

    # 422 and not 500: the runner's own refusal is a RuntimeError, and letting it surface
    # would tell a client its request broke the server rather than that it was declined.
    assert response.status_code == 422
    assert response.json()["code"] == LIVE_RUN_REFUSED
    assert "explicit local CLI release gate" in response.json()["message"]


async def test_a_replay_run_survives_the_wire_without_losing_a_field() -> None:
    """The serialisation half only: the document JSON-encodes and re-validates unchanged.

    ``expected`` is deliberately ``summarise``'s own output, so this proves exactly one
    thing — nothing is dropped, coerced or reordered between the projection and the client —
    and it proves nothing about whether the projection read the analysis result correctly. A
    ``_policy`` that blanked a field would agree with itself here and pass. The measurements
    are pinned against an independent reading of ``PolicyResult`` in the test below.
    """

    expected = summarise(
        runner().run(list(BASELINE_ORDER), split=BenchmarkSplit.EVALUATION)
    )

    async with client() as caller:
        response = await caller.post(
            ROUTER_BENCHMARK_RUN_PATH, json={"execution_source": "REPLAY"}
        )

    assert response.status_code == 200
    assert RouterBenchmarkSummary.model_validate(response.json()) == expected
    body = response.json()
    assert body["execution_source"] == "REPLAY"
    assert body["split"] == "EVALUATION"
    assert body["run_id"] == expected.run_id
    assert body["corpus_sha256"] == expected.corpus_sha256
    assert body["trace_sha256"] == expected.trace_sha256


async def test_the_policy_row_carries_the_regret_the_runner_actually_measured() -> None:
    """Ground truth read off the analysis object, never through ``summarise``.

    The runner is rebuilt here rather than borrowed from the route's process-wide cache, so
    the only thing shared between the expected values and the response body is the corpus on
    disk. Every number below therefore fails if ``_policy``'s field mapping is wrong —
    dropped, ``None``-ed, swapped between ``mean_regret`` and ``total_regret``, or taken from
    the wrong policy — which the round-trip equality above cannot detect.

    ``M0`` is the subject because it is an *available* comparator: the unavailable rows carry
    ``None`` everywhere, so a projection that reported nothing at all would look correct
    against them.
    """

    measured = RouterBenchmarkRunner().run(
        list(BASELINE_ORDER), split=BenchmarkSplit.EVALUATION
    ).policy("M0")
    regret = measured.regret
    assert regret is not None
    assert measured.mean_utility is not None
    # Guards against the degenerate agreement: an empty per-project table would match an
    # empty projection, and a zero mean would match a hard-coded zero.
    assert len(regret.regret_by_project) > 1
    assert regret.total_regret != 0.0

    async with client() as caller:
        response = await caller.post(
            ROUTER_BENCHMARK_RUN_PATH, json={"execution_source": "REPLAY"}
        )

    reported = next(
        policy for policy in response.json()["policies"] if policy["policy_id"] == "M0"
    )
    assert reported["mean_regret"] == pytest.approx(regret.mean_regret)
    assert reported["total_regret"] == pytest.approx(regret.total_regret)
    assert reported["mean_utility"] == pytest.approx(measured.mean_utility)
    assert reported["regret_by_project"] == pytest.approx(dict(regret.regret_by_project))
    assert reported["selections"] == len(measured.rows)
    assert reported["safety"]["false_acceptances"] == regret.safety.false_acceptances
    assert reported["safety"]["invalid_selections"] == regret.safety.invalid_selections


async def test_a_first_read_of_a_fresh_application_replays_the_evaluation_half() -> None:
    async with client() as caller:
        response = await caller.get(ROUTER_BENCHMARK_PATH)

    assert response.status_code == 200
    assert response.json()["split"] == "EVALUATION"
    assert len(response.json()["reported_task_ids"]) == 18


async def test_a_read_returns_what_the_last_run_stored_and_not_a_fresh_default() -> None:
    application = build_app()
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as caller:
        ran = await caller.post(
            ROUTER_BENCHMARK_RUN_PATH,
            json={"execution_source": "REPLAY", "split": "SELECTION"},
        )
        read = await caller.get(ROUTER_BENCHMARK_PATH)

    assert ran.status_code == 200
    assert read.status_code == 200
    # The stored summary wins over the evaluation-half default, so a reader sees the half the
    # operator actually asked for rather than the half the route would have chosen.
    assert read.json()["split"] == "SELECTION"
    assert read.json() == ran.json()


async def test_the_stored_summary_belongs_to_one_application() -> None:
    async with client() as first:
        await first.post(
            ROUTER_BENCHMARK_RUN_PATH,
            json={"execution_source": "REPLAY", "split": "SELECTION"},
        )
    async with client() as second:
        response = await second.get(ROUTER_BENCHMARK_PATH)

    assert response.json()["split"] == "EVALUATION"


async def test_every_protocol_baseline_stays_in_the_reported_table() -> None:
    """All eleven §8.1 methods, in §8.1's order, each with the projection a reader quotes.

    The route replays the registered table and takes no policy list from the caller, so since
    M10c — which wired M7, M8 and M9 — there is no way to reach this endpoint and get an
    unavailable row. That is the right shape for the route and it moves the unavailable-row
    assertion to the two places that can still make one: the runner's own test, with an id
    §8.1 does not name, and the pilot's. What is left here is the stronger half of §8.2's
    rule, and it is the half a dashboard depends on: nothing is missing, nothing is
    reordered, and no method is quietly reported without its gates.
    """

    async with client() as caller:
        response = await caller.get(ROUTER_BENCHMARK_PATH)

    policies = response.json()["policies"]
    assert [policy["policy_id"] for policy in policies] == list(BASELINE_ORDER)
    assert all(policy["available"] for policy in policies)
    for policy in policies:
        assert policy["reason_code"] is None
        assert policy["gates"] is not None
        assert policy["estimands"] is not None
        assert policy["selections"] == 18


async def test_the_summary_carries_the_gates_and_the_estimands_a_reader_would_quote() -> None:
    """The two nested projections, pinned the same independent way the policy row is.

    ``GET`` on a fresh application replays the evaluation half, which is the same half the
    runner is asked for here, so the gate counters and §12 gains in the body must equal the
    ones :func:`~accretion.routing.stats.estimands` produced — not merely be internally
    consistent with each other.
    """

    measured = RouterBenchmarkRunner().run(
        list(BASELINE_ORDER), split=BenchmarkSplit.EVALUATION
    ).policy("M0")
    assert measured.gates is not None
    assert measured.estimands is not None

    async with client() as caller:
        response = await caller.get(ROUTER_BENCHMARK_PATH)

    baseline = next(
        policy for policy in response.json()["policies"] if policy["policy_id"] == "M0"
    )
    gates = baseline["gates"]
    assert gates["selections"] == 18
    assert gates["both_met"] == (gates["verified_success_met"] and gates["false_acceptance_met"])
    assert gates["verified_successes"] == measured.gates.verified_successes
    assert gates["verified_success_rate"] == pytest.approx(
        measured.gates.verified_success_rate
    )
    assert gates["verified_success_floor"] == pytest.approx(
        measured.gates.verified_success_floor
    )
    assert gates["false_acceptances"] == measured.gates.false_acceptances
    assert gates["false_acceptance_ceiling"] == pytest.approx(
        measured.gates.false_acceptance_ceiling
    )
    estimands = baseline["estimands"]
    assert estimands["g_out"] == pytest.approx(measured.estimands.g_out)
    assert estimands["g_z"] == pytest.approx(measured.estimands.g_z)
    assert estimands["g_learn"] == pytest.approx(measured.estimands.g_learn)
    assert estimands["adjusted_alpha"] == pytest.approx(measured.estimands.adjusted_alpha)
    assert estimands["best_fixed"]["config_id"] == measured.estimands.best_fixed.config_id
    assert sorted(estimands["intervals"]) == [
        "best_fixed",
        "g_learn",
        "g_out",
        "g_z",
        "learned",
        "oracle",
        "signal",
    ]
    # The negative control again, this time through the wire format: no opportunity has been
    # shown, so no share of it may be reported.
    assert estimands["recovered_fraction"] is None
    assert estimands["best_fixed"]["evaluation_trials"] == 18
    assert baseline["regret_interval"] is not None


async def test_an_unknown_body_field_is_rejected_rather_than_ignored() -> None:
    async with client() as caller:
        response = await caller.post(
            ROUTER_BENCHMARK_RUN_PATH,
            json={"execution_source": "REPLAY", "weights": {"cost": 0.0}},
        )

    assert response.status_code == 422


def test_the_application_publishes_both_routes_and_the_response_model() -> None:
    from accretion.api.main import app

    spec = app.openapi()

    assert ROUTER_BENCHMARK_PATH in spec["paths"]
    assert ROUTER_BENCHMARK_RUN_PATH in spec["paths"]
    assert "get" in spec["paths"][ROUTER_BENCHMARK_PATH]
    assert "post" in spec["paths"][ROUTER_BENCHMARK_RUN_PATH]
    assert "RouterBenchmarkSummary" in spec["components"]["schemas"]
