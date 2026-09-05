"""``derived_id``: the deterministic half of the identity scheme (plan M1.1).

Every identifier in this repository has been minted by :func:`~accretion.ids.new_id`, which
is right for a record created once and wrong for one that must be *re-derivable*. A
compatibility decision evaluated twice from the same registry snapshot is the same decision,
and AC4-M2-011's "replay is a lookup" only works if the id a replay computes is the id the
first evaluation stored. :func:`~accretion.ids.derived_id` is that function, and these tests
pin the four properties the callers depend on: equal inputs give equal ids, different inputs
give different ids, the id carries its kind's prefix, and the result survives the
``CanonicalContract`` header check that owns the prefix.

The last one is the reason the function reuses ``new_id``'s base32 shape rather than, say, a
hex digest: ``has_prefix`` compares a total length, and a longer or shorter body would be
rejected by every contract that declares an ``ID_KIND`` — at construction time, inside
whichever service happened to build the record first.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from accretion.contracts import PrincipalRef, PrincipalStatus
from accretion.contracts.routing import (
    CompatibilityDecision,
    CompatibilityStatus,
    SubjectType,
)
from accretion.ids import _PREFIXES, derived_id, has_prefix, new_id

PRINCIPAL = PrincipalRef(
    principal_id="usr_4CF33CQ2YNVSFEK71H8ETSCYE0",
    display_name="M1 test",
    status=PrincipalStatus.ACTIVE,
)


def test_the_same_parts_derive_the_same_id() -> None:
    first = derived_id("compatibility_decision", "compat.runtime.ready", "compat-rules/1", "abc")
    second = derived_id("compatibility_decision", "compat.runtime.ready", "compat-rules/1", "abc")
    assert first == second


def test_different_parts_derive_different_ids() -> None:
    base = derived_id("compatibility_decision", "compat.runtime.ready", "abc")
    assert base != derived_id("compatibility_decision", "compat.runtime.ready", "abd")
    assert base != derived_id("compatibility_decision", "compat.model.allowed", "abc")


def test_a_part_boundary_is_inside_the_digest() -> None:
    """``("ab", "c")`` and ``("a", "bc")`` are different inputs and must derive different ids.

    A hash over the concatenated parts would collapse them, which is how two decisions about
    two different subjects come to share one identity. The canonical-JSON encoding of the
    parts *as a list* keeps the boundary inside the hash input.
    """

    assert derived_id("compatibility_decision", "ab", "c") != derived_id(
        "compatibility_decision", "a", "bc"
    )


def test_no_parts_at_all_still_derives_a_well_shaped_id() -> None:
    """The degenerate input is a valid one; it must not raise or produce a short body."""

    minted = derived_id("compatibility_decision")
    assert has_prefix(minted, "compatibility_decision")


@pytest.mark.parametrize("kind", sorted(_PREFIXES))
def test_a_derived_id_carries_its_kind_prefix_and_the_repository_id_shape(kind: str) -> None:
    """Same prefix table and same 30-character shape as ``new_id``, for every kind.

    Parametrized over the whole table rather than one kind, because ``derived_id`` is offered
    to every caller ``new_id`` is and a kind whose prefix width differed would fail only in
    whichever milestone first used it.
    """

    minted = derived_id(kind, "part-one", "part-two")
    assert has_prefix(minted, kind)
    assert len(minted) == len(new_id(kind))


def test_a_derived_id_validates_against_the_contract_that_owns_the_prefix() -> None:
    """The header check in ``CanonicalContract`` is the real consumer, so it is the real test."""

    decision = CompatibilityDecision(
        contract_id=derived_id("compatibility_decision", "compat.runtime.ready", "rt-fake"),
        created_by=PRINCIPAL,
        workspace_id="wks_8G33T24F686H6EJPBHRSFYCC3C",
        project_id="prj_8W5DH3HW6DPAFFPBHQ47R21DK9",
        subject_type=SubjectType.RUNTIME,
        subject_ref="runtime_fake",
        status=CompatibilityStatus.COMPATIBLE,
        rule_id="compat.runtime.ready",
        rule_version="compat-rules/1",
        reason_code="COMPATIBLE",
        evaluated_at=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
    )
    assert decision.contract_id.startswith("cmp_")


def test_an_id_derived_under_the_wrong_kind_is_refused_by_the_contract() -> None:
    """Negative control for the test above: the prefix check is doing work, not decoration."""

    with pytest.raises(ValidationError):
        CompatibilityDecision(
            contract_id=derived_id("routing_receipt", "compat.runtime.ready"),
            created_by=PRINCIPAL,
            workspace_id="wks_8G33T24F686H6EJPBHRSFYCC3C",
            project_id="prj_8W5DH3HW6DPAFFPBHQ47R21DK9",
            subject_type=SubjectType.RUNTIME,
            subject_ref="runtime_fake",
            status=CompatibilityStatus.COMPATIBLE,
            rule_id="compat.runtime.ready",
            rule_version="compat-rules/1",
            reason_code="COMPATIBLE",
            evaluated_at=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
        )


def test_an_unknown_kind_raises_the_same_key_error_new_id_raises() -> None:
    with pytest.raises(KeyError):
        derived_id("not_a_registered_kind", "x")
