"""The freeze delta of 5 Sep 2026: two new contracts and one additive field (ADR-060..062).

Three facts M0 froze do not compose with M6-M8, and this module proves the fixes rather
than the shapes. ``tests/test_v04_m0_fixtures.py`` already parses, seals, tampers with and
round-trips every one of the twenty-one contracts, and ``tests/test_v04_m0_store.py``
already writes, re-writes, revises and lists every one of the seventeen tables — both are
parametrized over constants the delta grew, so both cover the new records for free. What
is left, and what is here, is everything those two files *cannot* say:

* the rules the two new validators hold, one test per clause, because a validator with
  three clauses and one test is a validator with two clauses nobody is checking;
* that ``ObjectiveContract.exploration_policy`` is a registry §3.2 **Minor** change of
  *shape* — a body written before it existed still parses — and, separately, that it moved
  every ``ObjectiveContract`` digest, so that a body presented with its *pre-delta* digest
  is refused as tampered; ADR-056 keeps nulls, and a "purely additive" field that silently
  changes a hash is exactly the kind of thing a freeze record has to say out loud;
* that the ledger behaves like a ledger in the store: two arms of one pair both land, and
  the head of a sequence is a query.

Contracts are built from the **committed golden fixtures**, not assembled field by field,
for the reason the M0 store tests give: a test that builds its own document proves the code
agrees with this file's idea of the shape, not with the frozen one.

Nothing here carries an acceptance marker: the freeze delta claims no criterion, exactly as
M0 claims none (ADR-052).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from accretion.contracts import Project
from accretion.contracts.canonical import CanonicalContract, content_hash
from accretion.contracts.routing import (
    CONTRACT_INVENTORY,
    ExplorationPolicy,
    ObjectiveContract,
    ObservedOutcome,
    RouterActivation,
    RouterActivationKind,
    RouterScope,
    ShadowRolloutKind,
    ShadowRolloutResult,
)
from accretion.ids import _PREFIXES, has_prefix, new_id
from accretion.persistence.models import V04_FREEZE_DELTA_TABLES
from accretion.persistence.store import MemoryStore

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def build[C: CanonicalContract](
    model: type[C], fixture: str = "minimal", /, **overrides: Any
) -> C:
    """One golden fixture, re-sealed after whatever this test changed.

    The digest is dropped rather than recomputed here: the model seals itself, so a test
    that computed the digest by hand would be testing its own arithmetic.
    """

    path = FIXTURE_ROOT / snake_case(model.__name__) / f"{fixture}.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    document.pop("_expect", None)
    document.update(overrides)
    document.pop("content_hash", None)
    if "contract_id" not in overrides and model.ID_KIND is not None:
        document["contract_id"] = new_id(model.ID_KIND)
    return model.model_validate(document)


def raw(model: type[CanonicalContract], kind: str) -> dict[str, Any]:
    path = FIXTURE_ROOT / snake_case(model.__name__) / f"{kind}.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


FIXTURE_WORKSPACE_ID: str = raw(ObjectiveContract, "minimal")["workspace_id"]
FIXTURE_PROJECT_ID: str = raw(ObjectiveContract, "minimal")["project_id"]


async def setup_store() -> MemoryStore:
    """A fresh store holding the project every project-scoped record here references."""

    store = MemoryStore()
    await store.create_project(
        Project(
            project_id=FIXTURE_PROJECT_ID,
            name="v0.4 freeze delta",
            repository_path=Path("/tmp/accretion-v04-freeze-delta"),
        )
    )
    return store


# --------------------------------------------------------------------------------------
# RouterActivation — the ledger's four rules (ADR-061)
# --------------------------------------------------------------------------------------


def test_a_rollback_activation_that_records_no_cause_is_refused() -> None:
    """§10.3's reversibility is worth nothing if the ledger cannot say why.

    The row an incident review reads first is the one that says a version was withdrawn.
    A withdrawal with no stated cause is a fact with no explanation attached to it, and by
    the time anyone asks, the person who did it is not in the room.
    """

    with pytest.raises(ValidationError, match=r"a ROLLBACK activation leaves \['cause'\]"):
        build(RouterActivation, "complete", cause=None)


def test_a_rollback_activation_that_names_no_restore_target_is_refused() -> None:
    """A reversal that does not say what it reversed *to* is not reversible evidence."""

    with pytest.raises(
        ValidationError, match=r"leaves \['rollback_target_version_id'\]"
    ):
        build(RouterActivation, "complete", rollback_target_version_id=None)


def test_a_promotion_activation_needs_neither_a_cause_nor_a_restore_target() -> None:
    """The other half of the rule, so that it constrains ``ROLLBACK`` and nothing else.

    Without this, deleting the ``kind is ROLLBACK`` guard and requiring ``cause`` on every
    entry would leave the two tests above green while making ordinary promotion impossible.
    """

    activation = build(RouterActivation, "minimal")

    assert activation.kind is RouterActivationKind.PROMOTE
    assert activation.cause is None
    assert activation.rollback_target_version_id is None


def test_the_first_entry_in_a_ledger_cannot_name_the_version_it_displaced() -> None:
    """Sequence 1 displaces nothing; a predecessor there describes a history that never was."""

    with pytest.raises(ValidationError, match="displaces nothing"):
        build(
            RouterActivation,
            "minimal",
            previous_version_id="rmv_0GD4WXKB7NQZJ3MPCVH25TR89A",
        )


def test_a_later_entry_may_and_should_name_the_version_it_displaced() -> None:
    """The pairing test for the rule above: contiguity is a property of sequence ≥ 2."""

    activation = build(
        RouterActivation,
        "minimal",
        sequence=2,
        previous_version_id="rmv_0GD4WXKB7NQZJ3MPCVH25TR89A",
    )

    assert activation.sequence == 2
    assert activation.previous_version_id == "rmv_0GD4WXKB7NQZJ3MPCVH25TR89A"


def test_an_activation_that_displaces_the_very_version_it_activates_is_refused() -> None:
    """A row that changes nothing would still take a sequence number and still read as a
    release, which is how a ledger acquires entries that mean nothing."""

    with pytest.raises(ValidationError, match="an activation that changes nothing"):
        build(
            RouterActivation,
            "minimal",
            sequence=2,
            previous_version_id=raw(RouterActivation, "minimal")["router_version_id"],
        )


def test_a_workspace_prior_activation_that_names_a_project_is_refused() -> None:
    """SDD §7.12's nullability made exact, as ``RouterModelVersion`` already makes it."""

    with pytest.raises(ValidationError, match="belongs to no single project"):
        build(RouterActivation, "minimal", project_id=FIXTURE_PROJECT_ID)


def test_a_project_adapter_activation_without_a_project_is_refused() -> None:
    with pytest.raises(ValidationError, match="must name the project whose adapter"):
        build(
            RouterActivation,
            "complete",
            scope=RouterScope.PROJECT_ADAPTER.value,
            project_id=None,
        )


# --------------------------------------------------------------------------------------
# ShadowRolloutResult — the observed-outcome rules (ADR-060)
# --------------------------------------------------------------------------------------


def test_a_rollout_claiming_verification_must_name_the_verification_result() -> None:
    """§8.4 makes verification independent of the executor.

    A fork that scored itself as verified and pointed at nothing is a self-report, and a
    self-report cannot be the evidence a promotion gate reads.
    """

    with pytest.raises(ValidationError, match="no verification_result_id is named"):
        build(ShadowRolloutResult, "complete", verification_result_id=None)


def test_a_rollout_that_was_not_verified_may_leave_the_verification_unnamed() -> None:
    """The other half: the rule binds a verified outcome and constrains nothing else."""

    rollout = build(ShadowRolloutResult, "minimal")

    assert rollout.observed.verified is False
    assert rollout.verification_result_id is None
    assert rollout.kind is ShadowRolloutKind.CONTROL


def test_a_false_acceptance_is_refused_on_an_outcome_the_verifier_never_accepted() -> None:
    """A false acceptance is an acceptance that turned out wrong, so there must be one."""

    with pytest.raises(ValidationError, match="did not accept"):
        ObservedOutcome(
            quality=0.4, cost=0.1, latency_ms=100.0, verified=False, false_accept=True
        )


def test_an_unknown_false_acceptance_is_a_null_and_not_a_guessed_false() -> None:
    """It is discovered, not measured, so ``None`` has to be expressible at write time."""

    outcome = ObservedOutcome(quality=0.4, cost=0.1, latency_ms=100.0, verified=True)

    assert outcome.false_accept is None


def test_the_two_arms_of_one_pair_carry_the_same_seed_and_different_kinds() -> None:
    """The seed is a field and not a paragraph, which is what makes the pair comparable.

    ``U(SHADOW) - U(CONTROL)`` is only a measurement of the configuration if everything
    else about the two forks was held equal; the seed is the part of "everything else" a
    later reader can actually check.
    """

    shadow = build(ShadowRolloutResult, "complete")
    control = build(
        ShadowRolloutResult,
        "complete",
        kind=ShadowRolloutKind.CONTROL.value,
        verification_result_id=None,
        observed={"quality": 0.8, "cost": 0.4, "latency_ms": 17000.0, "verified": False},
    )

    assert shadow.kind is ShadowRolloutKind.SHADOW
    assert control.kind is ShadowRolloutKind.CONTROL
    assert shadow.seed == control.seed
    assert shadow.shadow_decision_id == control.shadow_decision_id
    assert shadow.content_hash != control.content_hash


# --------------------------------------------------------------------------------------
# ObjectiveContract.exploration_policy — the Minor bump (OQ-410, ADR-062)
# --------------------------------------------------------------------------------------


def test_an_unsealed_objective_written_before_the_field_existed_still_parses() -> None:
    """Registry §3.2: an additive optional field is Minor *of shape*, and shapes hold.

    ``build`` drops the digest, so what this proves is the half of Minor that is true: a
    body whose field list predates ``exploration_policy`` is accepted field for field and
    seals as an objective with no policy. A reader that refused the *shape* would have made
    every future field a breaking change. The sealed half — the same body presented with the
    digest it was sealed under before the delta — is the test below, and it fails closed.
    """

    objective = build(ObjectiveContract, "minimal")

    assert objective.exploration_policy is None
    assert objective.schema_version == "1.0.0"


# ``objective_contract/minimal.json``'s digest at develop ``edd4bb1``, over the identical
# body this fixture still carries — the field added no key to it, only a null to the
# canonical form. Literal rather than recomputed: a digest a test derives from today's
# model is not a record of what yesterday sealed.
PRE_DELTA_MINIMAL_CONTENT_HASH = (
    "ed2c3c296b67e1d57a0841d034c876a4dbae42f53b2a993445d99c725364265c"
)


def test_an_objective_sealed_before_the_field_existed_is_refused_as_tampered() -> None:
    """The other half of Minor, pinned as behaviour instead of wished away in prose.

    ADR-056 keeps nulls, so the digest recomputed at any read boundary is over a body that
    now carries ``exploration_policy: null``, and a document presented with the digest it
    was sealed with yesterday cannot match it. The refusal is the seal working — an
    unexplained hash mismatch is exactly what tampering looks like — but it means the delta
    is *not* transparent to stored rows, and ``docs/releases/v0.4/m0-freeze.md`` says so.
    This is the same ``model_validate`` call ``get_objective_contract`` makes on either
    backend, so a pre-delta row fails on read and takes its whole ``list_`` page with it.

    Nothing in the field is in this position: migration 0017 is on ``develop`` and in no
    release, and no code outside this suite writes an ``objective_contracts`` row, so a
    developer database already at 0017 is recreated rather than migrated. Registry §20.5's
    read-boundary upcaster (M8) owns the general case, and this test is what tells M8 the
    case is real.
    """

    sealed_before_the_delta = raw(ObjectiveContract, "minimal")
    assert "exploration_policy" not in sealed_before_the_delta, (
        "the minimal fixture is still the pre-delta body verbatim; if it ever declares a "
        "policy, this test must carry its own literal copy of the pre-delta document"
    )
    sealed_before_the_delta["content_hash"] = PRE_DELTA_MINIMAL_CONTENT_HASH

    with pytest.raises(ValidationError, match="does not match the digest of this payload"):
        ObjectiveContract.model_validate(sealed_before_the_delta)


def test_an_absent_exploration_policy_is_the_no_exploration_posture() -> None:
    """``None`` and ``alpha=0`` say the same operational thing; only one claims a decision.

    Stated as a test because the difference is the whole reason the field is optional
    rather than defaulted to a zero policy: a project that never considered exploration and
    a project that considered it and said no are different facts about the approver.
    """

    unset = build(ObjectiveContract, "minimal")
    declared = build(
        ObjectiveContract,
        "minimal",
        exploration_policy={"alpha": 0.0, "max_explore_count": 0, "max_cost": 0.0},
    )

    assert unset.exploration_policy is None
    assert declared.exploration_policy == ExplorationPolicy(
        alpha=0.0, max_explore_count=0, max_cost=0.0
    )
    assert unset.content_hash != declared.content_hash


def test_the_additive_field_moved_the_digest_because_the_canonical_form_keeps_nulls() -> None:
    """The half of "purely additive" that is not true, pinned rather than left implicit.

    ADR-056 keeps ``None`` in the canonical form, so ``exploration_policy: null`` is inside
    the body of every objective sealed from now on and a byte-identical objective seals to
    a different digest than it did before the field existed. ``docs/releases/v0.4/m0-freeze.md``
    records the re-sealed fixtures and the new schema digest; this is what makes that
    paragraph a checked claim instead of a recollection.
    """

    document = raw(ObjectiveContract, "complete")
    body = {key: value for key, value in document.items() if key != "content_hash"}
    as_a_document_written_before_the_field_existed = {
        key: value for key, value in body.items() if key != "exploration_policy"
    }

    # The committed digest is over the body as the field-bearing model dumps it — which is
    # why the fixture had to be re-sealed and the pre-delta digest is now wrong.
    assert content_hash(body) == document["content_hash"]
    assert content_hash(as_a_document_written_before_the_field_existed) != (
        document["content_hash"]
    )

    # And the null itself is the difference on a document that declares no policy: an
    # objective that omits the key hashes as one that spells it `null`, not as one that has
    # no such field, because ADR-056 keeps nulls so `{"a": null}` and `{}` cannot collide.
    unset = build(ObjectiveContract, "minimal")
    dumped = unset.model_dump(mode="json")

    assert dumped["exploration_policy"] is None
    assert content_hash(dumped) == unset.content_hash
    assert content_hash(
        {key: value for key, value in dumped.items() if key != "exploration_policy"}
    ) != unset.content_hash


def test_an_exploration_alpha_above_one_is_refused() -> None:
    """``alpha`` is a fraction of the baseline's cost; above 1 it is not a guard rail."""

    with pytest.raises(ValidationError, match="less than or equal to 1"):
        ExplorationPolicy(alpha=1.5, max_explore_count=10, max_cost=5.0)


def test_an_exploration_budget_carries_absolute_caps_beside_the_fraction() -> None:
    """A proportional bound alone scales with traffic; the approver's intent does not."""

    policy = build(ObjectiveContract, "complete").exploration_policy

    assert policy is not None
    assert policy.alpha == 0.05
    assert policy.max_explore_count == 200
    assert policy.max_cost == 25.0


# --------------------------------------------------------------------------------------
# Identity and inventory
# --------------------------------------------------------------------------------------


def test_the_two_new_records_carry_their_own_three_character_prefixes() -> None:
    """ADR-055's registry is three characters wide and every kind in it is distinct."""

    assert _PREFIXES["shadow_rollout_result"] == "shr"
    assert _PREFIXES["router_activation"] == "rac"
    assert len(set(_PREFIXES.values())) == len(_PREFIXES)
    assert has_prefix(new_id("shadow_rollout_result"), "shadow_rollout_result")
    assert has_prefix(new_id("router_activation"), "router_activation")


def test_an_activation_id_minted_from_the_wrong_kind_is_refused() -> None:
    """The header's ADR-055 check, on a record that did not exist when it was written."""

    with pytest.raises(ValidationError, match="identity prefix required by ADR-055"):
        build(RouterActivation, "minimal", contract_id=new_id("router_model_version"))


def test_the_two_new_contracts_are_the_last_two_in_the_inventory() -> None:
    """The tuple is also the migration's creation order, and 0018 creates these two."""

    assert CONTRACT_INVENTORY[-2:] == (ShadowRolloutResult, RouterActivation)
    assert V04_FREEZE_DELTA_TABLES == ("shadow_rollout_results", "router_activations")


# --------------------------------------------------------------------------------------
# The store (MemoryStore; the PostgreSQL twin is test_v04_freeze_delta_postgres_store.py)
# --------------------------------------------------------------------------------------


async def test_both_arms_of_one_pair_are_stored_and_list_in_completion_order() -> None:
    """Two rows, not one: a pair whose second arm failed still holds a real measurement."""

    store = await setup_store()
    base = raw(ShadowRolloutResult, "minimal")
    created = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    control = build(
        ShadowRolloutResult,
        "minimal",
        created_at=(created + timedelta(minutes=1)).isoformat(),
    )
    shadow = build(
        ShadowRolloutResult,
        "minimal",
        created_at=created.isoformat(),
        kind=ShadowRolloutKind.SHADOW.value,
        fork_execution_id="rtc_0KZ4M8CVXT62BWNDHJ9QRAG5P7",
    )

    await store.put_shadow_rollout_result(control)
    await store.put_shadow_rollout_result(shadow)

    listed = await store.list_shadow_rollout_results(workspace_id=base["workspace_id"])

    assert [item.contract_id for item in listed] == [shadow.contract_id, control.contract_id]
    assert [item.kind for item in listed] == [
        ShadowRolloutKind.SHADOW,
        ShadowRolloutKind.CONTROL,
    ]
    assert {item.shadow_decision_id for item in listed} == {base["shadow_decision_id"]}


async def test_the_head_of_an_activation_ledger_is_its_highest_sequence() -> None:
    """"Active" is a position in a sequence, which is what makes a second one insertable.

    The rule M0 could not express: under the partial unique index a second ``ACTIVE``
    router could never be written at all, because nothing in this family updates a row.
    Three appended entries and a head that moves is the whole of ADR-061.
    """

    store = await setup_store()
    versions = [
        "rmv_2KPQMJ4VCETFFK2Z973NZSM3NV",
        "rmv_0GD4WXKB7NQZJ3MPCVH25TR89A",
        "rmv_6QJ0ZC2NKXW9BTMDHV35PRAG74",
    ]
    for index, version in enumerate(versions, start=1):
        await store.put_router_activation(
            build(
                RouterActivation,
                "minimal",
                sequence=index,
                router_version_id=version,
                previous_version_id=None if index == 1 else versions[index - 2],
                created_at=datetime(2026, 3, index, 9, 0, tzinfo=UTC).isoformat(),
            )
        )

    ledger = await store.list_router_activations(workspace_id=FIXTURE_WORKSPACE_ID)
    head = max(ledger, key=lambda entry: entry.sequence)

    assert [entry.sequence for entry in ledger] == [1, 2, 3]
    assert head.router_version_id == versions[-1]
    assert head.previous_version_id == versions[-2]


async def test_a_rollback_appends_rather_than_editing_the_entry_it_reverses() -> None:
    """Registry §17: historical records are never rewritten in place.

    After the withdrawal both entries are still readable, the promotion is still a
    ``PROMOTE``, and the head is the rollback — which is what "reversible" has to mean in a
    store with no ``update_`` on any table.
    """

    store = await setup_store()
    promotion = build(RouterActivation, "minimal", sequence=1)
    rollback = build(
        RouterActivation,
        "minimal",
        sequence=2,
        kind=RouterActivationKind.ROLLBACK.value,
        previous_version_id=promotion.router_version_id,
        rollback_target_version_id="rmv_0GD4WXKB7NQZJ3MPCVH25TR89A",
        router_version_id="rmv_0GD4WXKB7NQZJ3MPCVH25TR89A",
        cause="Critical cohort regression on secrets handling.",
        created_at=datetime(2026, 3, 2, 9, 0, tzinfo=UTC).isoformat(),
    )
    await store.put_router_activation(promotion)
    await store.put_router_activation(rollback)

    ledger = await store.list_router_activations(workspace_id=FIXTURE_WORKSPACE_ID)
    stored_promotion = await store.get_router_activation(promotion.contract_id)

    assert [entry.kind for entry in ledger] == [
        RouterActivationKind.PROMOTE,
        RouterActivationKind.ROLLBACK,
    ]
    assert stored_promotion == promotion
    assert ledger[-1].cause == "Critical cohort regression on secrets handling."


async def test_a_freeze_delta_listing_never_returns_another_workspaces_rows() -> None:
    """The tenancy filter, on the two tables that did not exist when it was frozen."""

    store = await setup_store()
    mine = build(RouterActivation, "minimal")
    theirs = build(
        RouterActivation, "minimal", workspace_id="wks_1ZZZZZZZZZZZZZZZZZZZZZZZZZ"
    )
    await store.put_router_activation(mine)
    await store.put_router_activation(theirs)

    listed = await store.list_router_activations(workspace_id=FIXTURE_WORKSPACE_ID)

    assert [entry.contract_id for entry in listed] == [mine.contract_id]
