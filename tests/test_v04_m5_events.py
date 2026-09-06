"""The four §12 routing lifecycle events M2 declared and left unemitted.

``EventType`` has carried ``ROUTING_REQUESTED``, ``ROUTING_CANDIDATES_BUILT``,
``ROUTING_FALLBACK_SELECTED`` and ``ROUTING_HUMAN_REVIEW_REQUIRED`` since M2, and until this
milestone nothing produced any of them. An enum member no code emits is worse than a missing
one: an operator watching for fallbacks sees none and concludes there were none.

Three claims, and each of them is a different way the log could lie.

**Order and completeness.** The whole sequence is asserted as one list equality, not as a set
of membership checks, because the log's value is that it is a *narrative*: a
``ROUTING_DECISION_CREATED`` before the candidates were built would describe a decision made
without a slate.

**Nothing on replay.** §8.2 makes a repeated identical request a lookup. An event log that
grew on replay would report one fallback per retry, and a fallback *rate* computed from it
would rise with the number of times a caller happened to retry.

**No secrets.** An objective is user text. The one below contains a bearer token, and no
event payload may contain it. This is the assertion that keeps §17's log — the widest-read
surface in the system — from becoming the leak.

There is no ``conftest.py``. The real freeze/snapshot/catalog stack is imported from
``tests/test_v04_m2_service.py`` and not edited, and every assertion reads the events back
out of the store rather than out of the objects handed to it.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from test_v04_m2_service import _routable_execution

from accretion.contracts import AgentEvent, EventType
from accretion.contracts.routing import DecisionType
from accretion.governance import CapabilityPolicyEngine
from accretion.routing.candidates import CandidateBuilder
from accretion.routing.compatibility import CompatibilityEngine
from accretion.routing.gates import PolicyGate
from accretion.routing.protocols import RoutingMode
from accretion.routing.service import DefaultNodeRoutingService

BEARER = "Bearer sk-live-4f2c9ab1d0e7ff31"
LEAKY_OBJECTIVE = (
    f"Call the billing API with the header Authorization: {BEARER} and record the result."
)

BASELINE_SEQUENCE = (
    EventType.ROUTING_REQUESTED,
    EventType.ROUTING_CANDIDATES_BUILT,
    EventType.ROUTING_DECISION_CREATED,
    EventType.ROUTING_FALLBACK_SELECTED,
)


def _service(execution) -> DefaultNodeRoutingService:
    """The fixture's service, rebuilt so no earlier test's collaborators leak into this one."""

    return DefaultNodeRoutingService(
        store=execution.service.store,
        snapshots=execution.service.snapshots,
        catalog_factory=execution.service.catalog_factory,
        runtimes=execution.service.runtimes,
    )


async def _routing_events(execution) -> list[AgentEvent]:
    """Every routing event on the run, in stored order."""

    events = await execution.store.list_events(execution.run.run_id)
    return [event for event in events if event.native_type.startswith("accretion/routing/")]


async def _configuration_hashes(execution) -> list[str]:
    """The configurations the fixture's catalog can build, without persisting anything."""

    catalog = await execution.service.catalog_factory(
        execution.frozen, execution.snapshot, execution.run, execution.task
    )
    snapshot = replace(
        execution.snapshot, fallback_bundle_digest=catalog.fallback_bundle.digest
    )
    who = execution.frozen.node_contract.created_by
    built = CandidateBuilder(
        gate=PolicyGate(CapabilityPolicyEngine(set()), snapshot.policy, created_by=who),
        evaluator=CompatibilityEngine(created_by=who),
        catalog=catalog,
        created_by=who,
    ).build(
        routing_request_id="rrq_m5_event_probe",
        node_contract=execution.frozen.node_contract,
        task=execution.task,
        principal=who,
        entitled_workspace_id=execution.frozen.node_contract.workspace_id,
        snapshot=snapshot,
        workspace_id=execution.frozen.node_contract.workspace_id,
        project_id=execution.run.project_id,
        clock=lambda: datetime(2026, 5, 1, tzinfo=UTC),
    )
    return [item.configuration.configuration_hash for item in built.candidates]


async def test_a_baseline_route_appends_the_four_lifecycle_events_in_order(
    tmp_path: Path,
) -> None:
    """§12's sequence for a decision that fell back: requested, built, created, fallback.

    The normalized types are asserted as an ordered list *and* the outcome event is tied to
    the receipt's own decision type, so an implementation that emitted a fallback event for
    an exploit decision would fail even though the list would still have four entries.
    """

    execution = await _routable_execution(tmp_path)

    receipt = await _service(execution).route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )

    events = await _routing_events(execution)
    assert [event.normalized_type for event in events] == list(BASELINE_SEQUENCE)
    assert receipt.decision_type is DecisionType.FALLBACK
    # The two request-scoped events are caused by the request, the two receipt-scoped ones
    # by the receipt. A log that caused all four by the receipt would claim the request was
    # a consequence of the decision it produced.
    assert [event.causation_id for event in events] == [
        receipt.routing_request_id,
        receipt.routing_request_id,
        receipt.contract_id,
        receipt.contract_id,
    ]
    assert {event.correlation_id for event in events} == {execution.run.run_id}


async def test_a_replayed_route_appends_no_further_lifecycle_events(
    tmp_path: Path,
) -> None:
    """§8.2's idempotency reaches the event log, not only the receipt table.

    Without this a fallback rate would be a function of how often a caller retried, and the
    §16.1 metric built on it would move without any routing decision changing.
    """

    execution = await _routable_execution(tmp_path)
    service = _service(execution)

    first = await service.route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )
    after_first = [event.event_id for event in await _routing_events(execution)]
    second = await service.route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )
    after_second = [event.event_id for event in await _routing_events(execution)]

    assert second == first
    assert after_second == after_first
    assert len(after_first) == len(BASELINE_SEQUENCE)


async def test_a_route_with_no_buildable_candidate_announces_human_review_not_fallback(
    tmp_path: Path,
) -> None:
    """The fourth event is chosen by the decision, so §9.7's exclusion changes it.

    Excluding every buildable configuration is the shape §9.7 produces after a repeated
    failure: nothing is left to fall back to, and the honest §12 statement is that a human
    is needed rather than that a fallback was taken.
    """

    execution = await _routable_execution(tmp_path)
    hashes = await _configuration_hashes(execution)
    assert hashes

    receipt = await _service(execution).route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
        excluded_configuration_hashes=hashes,
    )

    events = await _routing_events(execution)
    assert receipt.decision_type is DecisionType.HUMAN_REVIEW_REQUIRED
    assert [event.normalized_type for event in events] == [
        EventType.ROUTING_REQUESTED,
        EventType.ROUTING_CANDIDATES_BUILT,
        EventType.ROUTING_DECISION_CREATED,
        EventType.ROUTING_HUMAN_REVIEW_REQUIRED,
    ]
    built = next(
        event
        for event in events
        if event.normalized_type is EventType.ROUTING_CANDIDATES_BUILT
    )
    assert built.payload["candidate_count"] == 0
    assert built.payload["rejected_count"] >= len(hashes)


async def test_no_routing_event_payload_repeats_the_objective_or_a_credential_in_it(
    tmp_path: Path,
) -> None:
    """§17's log is read by more eyes than the contract store, so it carries ids and counts.

    The objective below contains a bearer token and reaches the node contract, which the
    routing events are *about*. What is asserted is that no payload, serialised, contains
    either the token or the sentence around it — so a payload that started echoing the
    objective "for context" fails here rather than in an incident review.
    """

    execution = await _routable_execution(tmp_path, objective=LEAKY_OBJECTIVE)

    await _service(execution).route(
        frozen=execution.frozen,
        snapshot=execution.snapshot,
        mode=RoutingMode.BASELINE_ONLY,
        run=execution.run,
    )

    events = await _routing_events(execution)
    assert len(events) == len(BASELINE_SEQUENCE)
    for event in events:
        serialised = json.dumps(event.payload, default=str)
        assert BEARER not in serialised
        assert "sk-live" not in serialised
        assert LEAKY_OBJECTIVE not in serialised
    # The token really did reach the routed contract, so the assertion above is about
    # discipline in the payloads and not about the fixture never having had a secret.
    assert BEARER in execution.frozen.node_contract.objective

    requested = events[0]
    assert set(requested.payload) == {
        "routing_request_id",
        "node_contract_hash",
        "mode",
        "workspace_router_version",
        "action",
    }
    assert requested.payload["mode"] == RoutingMode.BASELINE_ONLY.value
