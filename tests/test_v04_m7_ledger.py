"""The conservative exploration cost ledger: the inequality, the bounds, and the bookkeeping.

Four claims, each one a way the budget could be quietly overspent if it were wrong.

**The inequality binds at its boundary.** Every cost in these tests is dyadic — 0.25, 0.5,
0.75 — so ``(1 + α) x baseline`` lands on a float the arithmetic can represent exactly, and
"exactly at the bound" really is exactly at it. The refusing case is ``math.nextafter`` of
the allowed one: one representable step, nothing else changed. A ``<=`` weakened to ``<``
fails the allowed case; a ``<`` widened to ``<=`` fails the refused one.

**An unsettled exploration keeps its upper bound.** The same call is refused before settling
and allowed after, with nothing else touched, so a ``settle`` that recorded the observation
somewhere without replacing the charge changes neither and fails both halves.

**Absolute caps do not care about α.** The cap cases are run at α = 8, where the relative
inequality has more headroom than the ledger will ever use, and the identical refusal is
re-run at α = 0 to show the reason does not move.

**The ledger is order-independent.** Two ledgers fed the same explorations in two seeded
shuffles must produce byte-identical snapshots, which is only true if every sum and every
listing is taken in sorted order rather than insertion order.

No store, no clock, no randomness beyond a seeded shuffle.
"""

from __future__ import annotations

import json
import math
import random

import pytest

from accretion.routing.ledger import CostLedger, ExplorationCaps

GENEROUS = ExplorationCaps(max_explore_count=8, max_cost=4.0)


def new_ledger() -> CostLedger:
    return CostLedger(workspace_id="ws_m7", node_class="LOW_DIGITAL")


def test_the_ledger_refuses_when_the_conservative_inequality_would_break() -> None:
    ledger = new_ledger()

    # Fresh ledger, α = 0.5, baseline LCB 0.5: the round being weighed joins the right-hand
    # side, so the budget is 1.5 x 0.5 = 0.75 exactly.
    at_the_bound = ledger.can_explore(
        candidate_cost_ucb=0.75, baseline_cost_lcb=0.5, alpha=0.5, caps=GENEROUS
    )
    assert at_the_bound.allowed is True
    assert at_the_bound.reason

    over_the_bound = ledger.can_explore(
        candidate_cost_ucb=math.nextafter(0.75, 1.0),
        baseline_cost_lcb=0.5,
        alpha=0.5,
        caps=GENEROUS,
    )
    assert over_the_bound.allowed is False
    assert "conservative" in over_the_bound.reason

    # can_explore is a read. Weighing a round must not spend it.
    assert ledger.explore_count == 0
    assert ledger.explored_cost_sum == 0.0

    # With one exploration booked, both sides of the inequality carry it: the budget is now
    # 1.25 x (0.5 + 0.5) = 1.25 and the charge is 0.5 + the candidate.
    ledger.record("rcp_a", 0.5, 0.5)
    assert ledger.explored_cost_sum == 0.5
    assert ledger.baseline_cost_lcb_sum == 0.5

    second_at_the_bound = ledger.can_explore(
        candidate_cost_ucb=0.75, baseline_cost_lcb=0.5, alpha=0.25, caps=GENEROUS
    )
    assert second_at_the_bound.allowed is True

    # One step over the bound, expressed as a step of the *sum*: adding one ULP of 0.75 to a
    # charge of magnitude 1.25 would round straight back onto the bound, because the ULP at
    # 1.25 is twice the ULP at 0.75.
    second_over_the_bound = ledger.can_explore(
        candidate_cost_ucb=math.nextafter(1.25, 2.0) - 0.5,
        baseline_cost_lcb=0.5,
        alpha=0.25,
        caps=GENEROUS,
    )
    assert second_over_the_bound.allowed is False
    assert "conservative" in second_over_the_bound.reason

    # A tighter α refuses what a looser one allowed, on the same recorded history.
    assert (
        ledger.can_explore(
            candidate_cost_ucb=0.75, baseline_cost_lcb=0.5, alpha=0.0, caps=GENEROUS
        ).allowed
        is False
    )

    # The inequality is stated in normalised costs; a cost outside [0, 1] would make it a
    # comparison between two different units.
    with pytest.raises(ValueError, match="candidate_cost_ucb"):
        ledger.can_explore(
            candidate_cost_ucb=1.5, baseline_cost_lcb=0.5, alpha=0.25, caps=GENEROUS
        )
    with pytest.raises(ValueError, match="baseline_cost_lcb"):
        ledger.can_explore(
            candidate_cost_ucb=0.5, baseline_cost_lcb=-0.25, alpha=0.25, caps=GENEROUS
        )


def test_unsettled_explorations_are_charged_at_their_upper_bound_until_settled() -> None:
    ledger = new_ledger()
    ledger.record("rcp_1", 0.75, 0.5)
    assert ledger.explored_cost_sum == 0.75

    # α = 0, so the budget is exactly the baseline sum: 0.5 booked + 0.5 for this round = 1.0.
    # Charged at the upper bound, 0.75 + 0.625 = 1.375 is over it.
    refused = ledger.can_explore(
        candidate_cost_ucb=0.625, baseline_cost_lcb=0.5, alpha=0.0, caps=GENEROUS
    )
    assert refused.allowed is False
    assert "conservative" in refused.reason

    settled = ledger.settle("rcp_1", 0.25)
    assert settled.observed_cost == 0.25
    assert settled.cost_ucb == 0.75
    assert settled.charged_cost == 0.25

    # The observation replaces the bound: 0.25 + 0.625 = 0.875 is inside the same budget.
    assert ledger.explored_cost_sum == 0.25
    allowed = ledger.can_explore(
        candidate_cost_ucb=0.625, baseline_cost_lcb=0.5, alpha=0.0, caps=GENEROUS
    )
    assert allowed.allowed is True

    # Settling moves the charge and nothing else: the exploration still happened, still
    # counts, and still credits the baseline it displaced.
    assert ledger.explore_count == 1
    assert ledger.baseline_cost_lcb_sum == 0.5

    # A second, unsettled exploration goes on charging its bound while the first is settled.
    ledger.record("rcp_2", 0.5, 0.5)
    assert ledger.explored_cost_sum == 0.75
    entries = ledger.snapshot()["entries"]
    assert entries == [
        {
            "receipt_id": "rcp_1",
            "cost_ucb": 0.75,
            "baseline_cost_lcb": 0.5,
            "observed_cost": 0.25,
            "settled": True,
        },
        {
            "receipt_id": "rcp_2",
            "cost_ucb": 0.5,
            "baseline_cost_lcb": 0.5,
            "observed_cost": None,
            "settled": False,
        },
    ]

    with pytest.raises(ValueError, match="append-only"):
        ledger.record("rcp_2", 0.25, 0.5)
    with pytest.raises(ValueError, match="already settled"):
        ledger.settle("rcp_1", 0.125)
    with pytest.raises(KeyError, match="never recorded"):
        ledger.settle("rcp_absent", 0.125)
    with pytest.raises(ValueError, match="observed_cost"):
        ledger.settle("rcp_2", 2.0)


def test_absolute_caps_bind_independently_of_alpha() -> None:
    ledger = new_ledger()
    ledger.record("rcp_1", 0.25, 0.5)
    ledger.record("rcp_2", 0.25, 0.5)

    # α = 8 leaves the conservative inequality with 9 x 1.5 = 13.5 against a charge of 0.75:
    # anything refused below is refused by an absolute cap and by nothing else.
    assert (
        ledger.can_explore(
            candidate_cost_ucb=0.25, baseline_cost_lcb=0.5, alpha=8.0, caps=GENEROUS
        ).allowed
        is True
    )

    count_cap = ExplorationCaps(max_explore_count=2, max_cost=4.0)
    generous_alpha = ledger.can_explore(
        candidate_cost_ucb=0.25, baseline_cost_lcb=0.5, alpha=8.0, caps=count_cap
    )
    tight_alpha = ledger.can_explore(
        candidate_cost_ucb=0.25, baseline_cost_lcb=0.5, alpha=0.0, caps=count_cap
    )
    assert generous_alpha.allowed is False
    assert "max_explore_count" in generous_alpha.reason
    assert tight_alpha == generous_alpha

    # Inclusive on the healthy side: a cap of three permits the third exploration.
    room_for_one_more = ExplorationCaps(max_explore_count=3, max_cost=4.0)
    assert (
        ledger.can_explore(
            candidate_cost_ucb=0.25, baseline_cost_lcb=0.5, alpha=8.0, caps=room_for_one_more
        ).allowed
        is True
    )

    # The cumulative cost cap, at its boundary and one representable step under it. Charged
    # 0.5 plus a 0.25 candidate is 0.75 exactly.
    at_cost_cap = ExplorationCaps(max_explore_count=99, max_cost=0.75)
    under_cost_cap = ExplorationCaps(max_explore_count=99, max_cost=math.nextafter(0.75, 0.0))
    assert (
        ledger.can_explore(
            candidate_cost_ucb=0.25, baseline_cost_lcb=0.5, alpha=8.0, caps=at_cost_cap
        ).allowed
        is True
    )
    over_cost = ledger.can_explore(
        candidate_cost_ucb=0.25, baseline_cost_lcb=0.5, alpha=8.0, caps=under_cost_cap
    )
    assert over_cost.allowed is False
    assert "max_cost" in over_cost.reason
    assert over_cost == ledger.can_explore(
        candidate_cost_ucb=0.25, baseline_cost_lcb=0.5, alpha=0.0, caps=under_cost_cap
    )

    with pytest.raises(ValueError, match="alpha"):
        ledger.can_explore(
            candidate_cost_ucb=0.25, baseline_cost_lcb=0.5, alpha=-0.5, caps=GENEROUS
        )
    with pytest.raises(ValueError, match="max_explore_count"):
        ExplorationCaps(max_explore_count=-1, max_cost=1.0)
    with pytest.raises(ValueError, match="max_cost"):
        ExplorationCaps(max_explore_count=1, max_cost=-1.0)


def test_the_ledger_is_deterministic_and_serialisable() -> None:
    bookings = [
        ("rcp_01", 0.5, 0.25),
        ("rcp_02", 0.25, 0.5),
        ("rcp_03", 0.125, 0.75),
        ("rcp_04", 0.75, 0.125),
        ("rcp_05", 0.625, 0.375),
    ]

    def build(order: list[tuple[str, float, float]]) -> CostLedger:
        ledger = new_ledger()
        for receipt_id, cost_ucb, baseline in order:
            ledger.record(receipt_id, cost_ucb, baseline)
        ledger.settle("rcp_03", 0.0625)
        return ledger

    first_order = list(bookings)
    random.Random(20260905).shuffle(first_order)
    second_order = list(bookings)
    random.Random(4711).shuffle(second_order)
    assert first_order != second_order, "the two seeded shuffles must differ to prove anything"

    first = build(first_order).snapshot()
    second = build(second_order).snapshot()
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    # Entries come out in receipt-id order, not in the order they were booked.
    assert [entry["receipt_id"] for entry in first["entries"]] == [
        "rcp_01",
        "rcp_02",
        "rcp_03",
        "rcp_04",
        "rcp_05",
    ]
    assert first["workspace_id"] == "ws_m7"
    assert first["node_class"] == "LOW_DIGITAL"
    assert first["explore_count"] == 5
    assert first["baseline_cost_lcb_sum"] == 2.0
    # 0.5 + 0.25 + 0.75 + 0.625 = 2.125 booked at their bounds, plus rcp_03 settled at 0.0625.
    assert first["explored_cost_sum"] == 2.1875

    # The snapshot round-trips through JSON unchanged: a later PR persists this dict as it is.
    assert json.loads(json.dumps(first)) == first

    # Every call builds a fresh structure, so a caller may keep or mutate what it was handed.
    ledger = build(first_order)
    taken = ledger.snapshot()
    taken["explore_count"] = 999
    assert isinstance(taken["entries"], list)
    taken["entries"].clear()
    assert ledger.snapshot() == first
