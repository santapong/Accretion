"""The six SDD §15.3 circuit breakers, proven on both sides of every boundary.

Two claims are under test, and the milestone is worth nothing without either.

**Each predicate trips where it says it trips, and not one step earlier.** A ceiling that
tripped at the ceiling, or a floor that tolerated a value below it, would be a breaker whose
threshold is not the number the objective declared. Every numeric case is therefore checked
at the boundary itself and at ``math.nextafter`` of it — the smallest float that is genuinely
over the line — so a comparison flipped between ``>`` and ``>=`` fails a case rather than
surviving on the slack of a hand-picked test value.

**The composite names every breaker that tripped.** The dangerous failure here is not a
breaker that never fires; it is a composite that fires, names the first cause and stops
looking. An operator who fixes the one condition they were told about would turn automatic
exploration back on into whichever conditions the composite never got round to evaluating.
The second test therefore trips each breaker alone *and* alongside a companion, at every
position in :data:`~accretion.routing.breakers.BREAKERS`, and requires both names back in
declaration order.

**And each verdict says which numbers tripped it.** The same half-told-reason failure lives
inside a single breaker: one that regressed two cohorts and named ``regressed[0]``, or that
returned a constant sentence, is indistinguishable from a correct one under an assertion that
the detail is merely non-empty. Every case below therefore carries the fragments its detail
must contain — the offending value, the offending id, or both ids where two offend at once —
and every breaker that can name more than one offender has a case that makes it name two.

Both tests find their predicate by asking each member of ``BREAKERS`` for the id it reports,
so a predicate dropped from the tuple takes its own case down with it instead of quietly
being skipped. No clock, no store, no network: every input here is a frozen dataclass.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from accretion.routing.breakers import (
    BREAKERS,
    Breaker,
    BreakerInput,
    exploration_allowed,
)

# The six of §15.3 in the order the section lists them, which is also the order tripped ids
# must come back in. Spelled out here rather than read off `BREAKERS` so that a reordered or
# renamed predicate is a failing test and not a silently updated expectation.
EXPECTED_IDS: tuple[str, ...] = (
    "false_acceptance_alert",
    "calibration_exceeded",
    "critical_cohort_regression",
    "unvalidated_version_drift",
    "verification_coverage_drop",
    "policy_or_audit_unavailable",
)


def healthy_inputs(**overrides: Any) -> BreakerInput:
    """Evidence under which all six breakers are quiet, with named fields overridden.

    The dyadic thresholds (0.5, 0.75, 0.25) are deliberate: they are exact in binary, so
    "exactly at the boundary" is exactly at the boundary and not a float's width away from
    it, and ``math.nextafter`` moves by one representable step from a value the arithmetic
    can hit precisely.
    """
    fields: dict[str, Any] = {
        "false_acceptance_rate_recent": 0.25,
        "false_acceptance_ceiling": 0.5,
        "ece_recent": 0.25,
        "max_ece": 0.5,
        "cohort_lcbs": {"correctness": 0.75, "policy": 0.875},
        "cohort_baselines": {"correctness": 0.75, "policy": 0.875},
        "delta_ni": 0.25,
        "serving_versions": {"planner": "2.1.0", "verifier": "4.0.0"},
        "version_boundaries": {"planner": ("2.0.0", "2.4.0"), "verifier": ("4.0.0", "4.0.0")},
        "verification_coverage_recent": 0.75,
        "coverage_floor": 0.5,
        "policy_snapshot_resolved": True,
        "audit_probe_ok": True,
    }
    fields.update(overrides)
    return BreakerInput(**fields)


def breaker_by_id(breaker_id: str) -> Breaker:
    """Find the predicate that reports ``breaker_id``, or fail naming what is missing."""
    for breaker in BREAKERS:
        if breaker(healthy_inputs()).breaker_id == breaker_id:
            return breaker
    raise AssertionError(f"no member of BREAKERS reports the id {breaker_id!r}")


# One override per breaker that puts it unambiguously over its line. Every breaker's fields
# are disjoint from every other's, so any two of these merge into an input that trips both.
TRIPPING_OVERRIDES: dict[str, dict[str, Any]] = {
    "false_acceptance_alert": {"false_acceptance_rate_recent": 0.75},
    "calibration_exceeded": {"ece_recent": 0.75},
    "critical_cohort_regression": {"cohort_lcbs": {"correctness": 0.25, "policy": 0.875}},
    "unvalidated_version_drift": {"serving_versions": {"planner": "3.0.0", "verifier": "4.0.0"}},
    "verification_coverage_drop": {"verification_coverage_recent": 0.25},
    "policy_or_audit_unavailable": {"policy_snapshot_resolved": False},
}

# Per breaker: (label, overrides, expected `tripped`, fragments the detail must contain).
# Each list walks the healthy side of the boundary, the boundary itself, and the smallest
# step past it; the fragments pin what the verdict *said*, so a detail that named a constant,
# or named only the first of two offenders, fails the case that has two.
BoundaryCase = tuple[str, dict[str, Any], bool, tuple[str, ...]]

# One representable step either side of the boundary value the healthy input sits against.
# Bound to names because their `str()` is what the verdict prints, and the expected fragment
# has to be that exact repr rather than a rounded transcription of it.
JUST_OVER_HALF: float = math.nextafter(0.5, 1.0)
JUST_UNDER_HALF: float = math.nextafter(0.5, 0.0)

BOUNDARY_CASES: tuple[tuple[str, tuple[BoundaryCase, ...]], ...] = (
    (
        "false_acceptance_alert",
        (
            (
                "well under the ceiling",
                {"false_acceptance_rate_recent": 0.25},
                False,
                ("0.25", "0.5"),
            ),
            ("exactly at the ceiling", {"false_acceptance_rate_recent": 0.5}, False, ("0.5",)),
            (
                "one representable step over the ceiling",
                {"false_acceptance_rate_recent": JUST_OVER_HALF},
                True,
                (str(JUST_OVER_HALF), "0.5"),
            ),
            (
                "far over the ceiling",
                {"false_acceptance_rate_recent": 0.9},
                True,
                ("0.9", "0.5"),
            ),
        ),
    ),
    (
        "calibration_exceeded",
        (
            ("well under the maximum ECE", {"ece_recent": 0.25}, False, ("0.25", "0.5")),
            ("exactly at the maximum ECE", {"ece_recent": 0.5}, False, ("0.5",)),
            (
                "one representable step over the maximum ECE",
                {"ece_recent": JUST_OVER_HALF},
                True,
                (str(JUST_OVER_HALF), "0.5"),
            ),
            ("far over the maximum ECE", {"ece_recent": 0.9}, True, ("0.9", "0.5")),
        ),
    ),
    (
        "critical_cohort_regression",
        (
            # baseline 0.75 with a 0.25 margin puts the line at exactly 0.5.
            (
                "every cohort above its margin",
                {"cohort_lcbs": {"correctness": 0.625}},
                False,
                ("all 1 critical cohorts", "0.25"),
            ),
            (
                "a cohort exactly on its margin",
                {"cohort_lcbs": {"correctness": 0.5}},
                False,
                ("all 1 critical cohorts", "0.25"),
            ),
            (
                "a cohort one representable step below its margin",
                {"cohort_lcbs": {"correctness": JUST_UNDER_HALF}},
                True,
                ("correctness", str(JUST_UNDER_HALF), "0.75", "0.25"),
            ),
            (
                "a cohort far below its margin",
                {"cohort_lcbs": {"correctness": 0.1}},
                True,
                ("correctness", "0.1", "0.75"),
            ),
            (
                "a cohort with no recorded baseline to compare against",
                {"cohort_lcbs": {"correctness": 0.75, "secrets": 0.99}},
                True,
                ("secrets", "0.99", "no recorded baseline"),
            ),
            # Two at once: a verdict that reported `regressed[0]` would name correctness,
            # leave policy unmentioned, and pass every other case in this list.
            (
                "two cohorts regressed at once",
                {"cohort_lcbs": {"correctness": 0.1, "policy": 0.1}},
                True,
                ("correctness", "0.75", "policy", "0.875"),
            ),
        ),
    ),
    (
        "unvalidated_version_drift",
        (
            (
                "inside the validated window",
                {"serving_versions": {"planner": "2.1.0"}},
                False,
                ("all 1 serving versions",),
            ),
            (
                "exactly on both endpoints of the window",
                {
                    "serving_versions": {"planner": "2.0.0", "verifier": "4.0.0"},
                    "version_boundaries": {
                        "planner": ("2.0.0", "2.4.0"),
                        "verifier": ("4.0.0", "4.0.0"),
                    },
                },
                False,
                ("all 2 serving versions",),
            ),
            (
                "below the window",
                {"serving_versions": {"planner": "1.9.9"}},
                True,
                ("planner", "1.9.9", "[2.0.0, 2.4.0]"),
            ),
            (
                "above the window",
                {"serving_versions": {"planner": "2.4.1"}},
                True,
                ("planner", "2.4.1", "[2.0.0, 2.4.0]"),
            ),
            # Lexicographically "2.10.0" < "2.4.0"; numerically it is past the high endpoint.
            (
                "above the window by a two-digit segment",
                {"serving_versions": {"planner": "2.10.0"}},
                True,
                ("planner", "2.10.0"),
            ),
            # A release candidate is not the release it precedes: only "2.0.0" was validated,
            # and an ordering that seated "2.0.0-rc1" above its own numeric prefix would let
            # an unvalidated rc serve inside a window recorded for the GA release alone.
            (
                "a prerelease of the window's low endpoint",
                {"serving_versions": {"planner": "2.0.0-rc1"}},
                True,
                ("planner", "2.0.0-rc1", "[2.0.0, 2.4.0]"),
            ),
            (
                "the prerelease that is itself the window's low endpoint",
                {
                    "serving_versions": {"planner": "2.0.0-rc1"},
                    "version_boundaries": {"planner": ("2.0.0-rc1", "2.4.0")},
                },
                False,
                ("all 1 serving versions",),
            ),
            # A window recorded in a different shape was not recorded for this version.
            (
                "a window recorded with fewer segments than the serving version",
                {
                    "serving_versions": {"planner": "2.0.0"},
                    "version_boundaries": {"planner": ("2.0", "2.0")},
                },
                True,
                ("planner", "2.0.0", "[2.0, 2.0]"),
            ),
            (
                "serving a component with no validated window at all",
                {"serving_versions": {"planner": "2.1.0", "ranker": "1.0.0"}},
                True,
                ("ranker", "1.0.0", "no validated window"),
            ),
            # Two at once, for the same reason the cohort breaker has one.
            (
                "two components drifted at once",
                {"serving_versions": {"planner": "3.0.0", "verifier": "9.9.9"}},
                True,
                ("planner", "3.0.0", "verifier", "9.9.9"),
            ),
        ),
    ),
    (
        "verification_coverage_drop",
        (
            (
                "well above the coverage floor",
                {"verification_coverage_recent": 0.75},
                False,
                ("0.75", "0.5"),
            ),
            (
                "exactly on the coverage floor",
                {"verification_coverage_recent": 0.5},
                False,
                ("0.5",),
            ),
            (
                "one representable step below the coverage floor",
                {"verification_coverage_recent": JUST_UNDER_HALF},
                True,
                (str(JUST_UNDER_HALF), "0.5"),
            ),
            (
                "far below the coverage floor",
                {"verification_coverage_recent": 0.1},
                True,
                ("0.1", "0.5"),
            ),
        ),
    ),
    (
        "policy_or_audit_unavailable",
        (
            (
                "policy resolved and audit probe green",
                {"policy_snapshot_resolved": True, "audit_probe_ok": True},
                False,
                ("policy", "audit"),
            ),
            (
                "the policy snapshot did not resolve",
                {"policy_snapshot_resolved": False},
                True,
                ("the policy snapshot did not resolve",),
            ),
            (
                "the audit probe failed",
                {"audit_probe_ok": False},
                True,
                ("the audit service probe failed",),
            ),
            # Both owners named, because restoring one does not restore the other.
            (
                "neither policy nor audit is available",
                {"policy_snapshot_resolved": False, "audit_probe_ok": False},
                True,
                ("policy", "audit"),
            ),
        ),
    ),
)


def test_breakers_declares_the_six_of_sdd_15_3_in_section_order() -> None:
    reported = tuple(breaker(healthy_inputs()).breaker_id for breaker in BREAKERS)
    assert reported == EXPECTED_IDS
    assert len(BREAKERS) == len(EXPECTED_IDS)
    assert tuple(case[0] for case in BOUNDARY_CASES) == EXPECTED_IDS
    assert tuple(TRIPPING_OVERRIDES) == EXPECTED_IDS


@pytest.mark.parametrize(
    ("breaker_id", "cases"), BOUNDARY_CASES, ids=[case[0] for case in BOUNDARY_CASES]
)
def test_each_breaker_trips_on_its_boundary_and_not_below(
    breaker_id: str, cases: tuple[BoundaryCase, ...]
) -> None:
    breaker = breaker_by_id(breaker_id)
    assert any(expected for _, _, expected, _ in cases), f"{breaker_id} has no tripping case"
    assert any(not expected for _, _, expected, _ in cases), f"{breaker_id} has no quiet case"
    assert all(
        fragments for _, _, _, fragments in cases
    ), f"{breaker_id} has a case that checks no part of the detail"
    for label, overrides, expected, fragments in cases:
        verdict = breaker(healthy_inputs(**overrides))
        assert verdict.tripped is expected, f"{breaker_id} with {label}: {verdict.detail}"
        assert verdict.breaker_id == breaker_id
        # Explained on both outcomes, and explained about *this* case. The quiet verdict is
        # what an audit reads to find out what the numbers were when exploration was allowed
        # to run; the tripped one is what an operator acts on, and one that named a constant
        # sentence, or only the first of two offenders, would send them back into the
        # condition it declined to mention.
        for fragment in fragments:
            assert fragment in verdict.detail, (
                f"{breaker_id} with {label}: {fragment!r} is missing from {verdict.detail!r}"
            )


# AC4-M7-020 ("circuit breakers disable exploration on safety/calibration alerts") stays
# `not_yet_due` until M7's last PR, and this test is only half of what flips it: it proves the
# predicate returns the refusal and the names, not that anything obeys it. Before the row is
# flipped, a second test must also carry this marker and assert *authority* — drive the M7
# exploration decision path with a tripping BreakerInput and assert the decision falls back to
# the deterministic baseline, that the ledger recorded no new exploration, and that the receipt
# names the tripped breaker id. The marker is variadic, so both claimants coexist; this one
# alone must not be what proves the criterion.
@pytest.mark.acceptance("AC4-M7-020")
@pytest.mark.parametrize("breaker_id", EXPECTED_IDS)
def test_any_tripped_breaker_disables_exploration_and_names_itself(breaker_id: str) -> None:
    allowed, tripped = exploration_allowed(healthy_inputs())
    assert allowed is True
    assert tripped == []

    allowed, tripped = exploration_allowed(healthy_inputs(**TRIPPING_OVERRIDES[breaker_id]))
    assert allowed is False
    assert tripped == [breaker_id]

    # The companion is the next breaker round the ring, so every position in BREAKERS is once
    # the earlier and once the later of a pair. A composite that stopped at its first hit
    # would drop one of the two names in one of these six runs.
    companion = EXPECTED_IDS[(EXPECTED_IDS.index(breaker_id) + 1) % len(EXPECTED_IDS)]
    both = {**TRIPPING_OVERRIDES[breaker_id], **TRIPPING_OVERRIDES[companion]}
    allowed, tripped = exploration_allowed(healthy_inputs(**both))
    assert allowed is False
    assert set(tripped) == {breaker_id, companion}
    assert tripped == [name for name in EXPECTED_IDS if name in {breaker_id, companion}]


def test_exploration_allowed_reports_all_six_when_all_six_trip() -> None:
    everything: dict[str, Any] = {}
    for overrides in TRIPPING_OVERRIDES.values():
        everything.update(overrides)
    allowed, tripped = exploration_allowed(healthy_inputs(**everything))
    assert allowed is False
    assert tripped == list(EXPECTED_IDS)
