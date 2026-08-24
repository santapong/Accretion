"""The acceptance gate must be able to go red.

A harness that cannot fail is decoration, so every failure mode it claims to detect
is exercised here: a failing claim, an uncovered MUST, a skipped-only claim, an
expired waiver, and stale manual evidence.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from accretion import acceptance as harness


def criterion(**overrides: object) -> object:
    defaults = {
        "id": "V01-P0-001",
        "release": "v0.1",
        "stage": "P0",
        "priority": "MUST",
        "text": "example",
        "source": "docs/sdd/example.md:1",
    }
    defaults.update(overrides)
    return harness.Criterion(**defaults)  # type: ignore[arg-type]


def test_a_criterion_with_no_claiming_test_is_uncovered() -> None:
    assert harness.classify(criterion()) == "UNCOVERED"


def test_a_failing_claim_is_reported_failing_not_proven() -> None:
    entry = criterion(tests=["t::a", "t::b"], outcomes=["passed", "failed"])
    assert harness.classify(entry) == "FAILING"


def test_a_claim_that_only_ever_skips_proves_nothing() -> None:
    """Live-provider tests skip in CI; a skipped test must not count as evidence."""

    entry = criterion(tests=["t::live"], outcomes=["skipped"])
    assert harness.classify(entry) == "SKIPPED_ONLY"


def test_a_passing_claim_is_proven() -> None:
    entry = criterion(tests=["t::a"], outcomes=["passed"])
    assert harness.classify(entry) == "PROVEN"


def test_an_expired_waiver_fails_the_gate() -> None:
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    entry = criterion(
        verification="waived", reason="deferred", issue="#52", expires=yesterday
    )
    assert harness.classify(entry) == "WAIVER_EXPIRED"


def test_a_live_waiver_is_accepted() -> None:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    entry = criterion(
        verification="waived", reason="deferred", issue="#52", expires=tomorrow
    )
    assert harness.classify(entry) == "WAIVED"


def test_manual_evidence_goes_stale() -> None:
    old = (date.today() - timedelta(days=400)).isoformat()
    entry = criterion(verification="manual", evidence="docs/x.md", last_verified=old)
    assert harness.classify(entry) == "MANUAL_STALE"

    recent = (date.today() - timedelta(days=10)).isoformat()
    fresh = criterion(verification="manual", evidence="docs/x.md", last_verified=recent)
    assert harness.classify(fresh) == "MANUAL"


def test_not_yet_due_is_out_of_scope_rather_than_failing() -> None:
    entry = criterion(verification="not_yet_due", reason="M4")
    assert harness.classify(entry) == "NOT_YET_DUE"
    assert entry.in_scope is False


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("V01-P0-001", "P0"),
        ("V02-P7-008", "P7"),
        ("V01-BENCH-001", "v0.1-bench"),
        ("V02-UI-003", "v0.2-ui"),
        ("AC3-CON-06", "M2"),
        ("AC3-UI-01", "M6"),
    ],
)
def test_every_id_shape_maps_to_a_stage(identifier: str, expected: str) -> None:
    assert harness.stage_of(identifier) == expected


def test_the_sdds_still_parse_and_the_policy_is_well_formed() -> None:
    """Guards against an SDD edit that silently drops criteria from the gate."""

    criteria = harness.load_criteria()
    assert len(criteria) == 110, "expected 110 criteria across the three SDDs"
    assert sum(1 for c in criteria.values() if c.priority == "MUST") == 108
    assert harness.apply_policy(criteria) == []
