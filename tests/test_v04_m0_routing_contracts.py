"""The v0.4 contract family: construction, validators, enums, and hash sensitivity.

Four things are under test here, and each is a rule the freeze would be worthless without.

**The inventory is complete.** ``CONTRACT_INVENTORY`` is what every other proof in this
milestone parametrizes over, so a contract missing from it would have no fixtures, no
committed schema and no hash coverage while still looking finished. The first test compares
the tuple against every ``CanonicalContract`` subclass the module defines.

**No enum was reinvented.** Registry §21 makes "the same concept under a different name" a
stop-and-reconcile event. The whole existing vocabulary — fifty-seven ``StrEnum`` classes in
``accretion.contracts`` and seven more in ``accretion.experience.models`` — is compared
against the fifteen this module adds, by value set, so a synonym is a red test rather than a
review comment somebody might miss. The same rule has a second half that value sets cannot
see: a *name* the locked registry has already promised to a later release. That side is read
straight out of the registry's §8-§13 tables, which is how ``PromotionDecision`` — v0.10's
capability-release contract — was caught being spent on a v0.4 router enum.

**The validators refuse what they are supposed to refuse.** The interesting ones are not the
range checks: they are ``Literal[True]`` on the independence flags (OQ-418),
``is_compatible()`` refusing ``UNKNOWN`` (SDD §7.7), the receipt refusing secret-shaped
values (SDD §12, §14.2), ``risk_level_for`` raising on ``PROHIBITED`` (ADR-054 d), and the
non-recoverable failure owners refusing to be retryable (registry §5.4).

**Every field is inside the hash.** The last section walks every declared field of every
contract, mutates exactly one, and requires the digest to move. A field that could change
without changing the digest would be a field a tampered document could edit for free.

Payloads come from the committed ``complete`` fixtures rather than being rebuilt here. That
is deliberate: it keeps one description of what a valid contract looks like, and it means a
test in this file cannot pass against a payload the fixtures no longer describe.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import accretion.contracts as contracts_root
import accretion.contracts.routing as routing
import accretion.experience.models as experience_models
from accretion.contracts import RiskLevel, VerificationStatus
from accretion.contracts.canonical import (
    CONTRACT_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_MAJOR,
    CanonicalContract,
    content_hash,
)
from accretion.contracts.routing import (
    _CONFIGURATION_SIGNATURE_FIELDS,
    CONTRACT_INVENTORY,
    NON_RECOVERABLE_FAILURE_OWNERS,
    TERMINAL_VERIFICATION_STATES,
    CompatibilityDecision,
    CompatibilityStatus,
    ConfigurationCandidate,
    ExecutionConfiguration,
    ExperienceRecord,
    FailureEvent,
    FailureOwner,
    IndependenceRequirements,
    IndependentVerificationResult,
    NodeContract,
    ObjectiveContract,
    ObjectiveContractRef,
    RiskClass,
    RouterModelVersion,
    RouterPromotionReport,
    RouterTrainingSnapshot,
    RoutingContext,
    RoutingDecisionReceipt,
    ShadowDecision,
    VerificationSpec,
    VerificationState,
    risk_level_for,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"
IDS = [model.__name__ for model in CONTRACT_INVENTORY]


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def complete(model: type[CanonicalContract]) -> dict[str, Any]:
    """The committed ``complete`` fixture for ``model``, as a fresh mutable dict."""

    path = FIXTURE_ROOT / snake_case(model.__name__) / "complete.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def rebuilt(model: type[CanonicalContract], **changes: Any) -> dict[str, Any]:
    """The complete fixture with ``changes`` applied and every digest dropped for re-sealing.

    Any edit invalidates the digests the fixture carries, so they are removed and recomputed
    on construction. ``DERIVED_HASH_FIELDS`` means the helper never has to know which
    contracts carry a second digest.
    """

    document = {**complete(model), **changes}
    for field in ("content_hash", *model.DERIVED_HASH_FIELDS):
        document.pop(field, None)
    return document


# --------------------------------------------------------------------------------------
# The inventory
# --------------------------------------------------------------------------------------


def test_the_inventory_holds_exactly_the_nineteen_contracts_the_freeze_names() -> None:
    assert len(CONTRACT_INVENTORY) == 19
    assert len(set(CONTRACT_INVENTORY)) == 19
    assert IDS == [
        "ObjectiveContract",
        "ObjectiveContractRef",
        "NodeContract",
        "VerificationSpec",
        "TaskFeatures",
        "ProjectFeatures",
        "RoutingContext",
        "ExecutionConfiguration",
        "ConfigurationCandidate",
        "CompatibilityDecision",
        "StructuredExplanation",
        "RoutingDecisionReceipt",
        "IndependentVerificationResult",
        "ExperienceRecord",
        "FailureEvent",
        "RouterModelVersion",
        "RouterTrainingSnapshot",
        "RouterPromotionReport",
        "ShadowDecision",
    ]


def test_every_canonical_contract_defined_in_the_module_is_in_the_inventory() -> None:
    """The gap this closes: a contract written, exported, and never proven.

    Without this, adding a class and forgetting the tuple would produce no fixtures, no
    committed schema and no hash coverage — and every existing test would still be green.
    """

    defined = {
        value
        for value in vars(routing).values()
        if isinstance(value, type)
        and issubclass(value, CanonicalContract)
        and value is not CanonicalContract
    }
    assert defined == set(CONTRACT_INVENTORY)


def test_every_contract_declares_a_distinct_canonical_type_string() -> None:
    types = [model.CONTRACT_TYPE for model in CONTRACT_INVENTORY]
    assert all(kind.startswith("accretion.") for kind in types)
    assert len(set(types)) == len(types)


def test_the_base_header_is_not_itself_a_record() -> None:
    """``CanonicalContract`` carries the header and refuses to be one."""

    with pytest.raises(ValidationError, match="declares no CONTRACT_TYPE"):
        CanonicalContract(
            contract_id="anything",
            created_by=complete(ObjectiveContract)["created_by"],
            workspace_id="wks_x",
            project_id="prj_x",
        )


# --------------------------------------------------------------------------------------
# Enums (registry §5, §21)
# --------------------------------------------------------------------------------------


def module_enums(module: Any) -> dict[str, frozenset[str]]:
    """Every ``StrEnum`` a module *defines*, as its set of values."""

    return {
        name: frozenset(member.value for member in value)
        for name, value in vars(module).items()
        if isinstance(value, type)
        and issubclass(value, StrEnum)
        and value is not StrEnum
        and value.__module__ == module.__name__
    }


def test_the_v04_enums_are_the_fifteen_the_freeze_names() -> None:
    assert sorted(module_enums(routing)) == [
        "CompatibilityStatus",
        "ConstructionStage",
        "ContradictionStatus",
        "Criticality",
        "DecisionType",
        "FailureOwner",
        "FailureType",
        "MetricOperator",
        "RiskClass",
        "RouterPromotionDecision",
        "RouterScope",
        "RouterStatus",
        "SubjectType",
        "VerificationState",
        "Visibility",
    ]


def test_no_v04_enum_duplicates_an_existing_one_under_a_new_name() -> None:
    """Registry §21's stop-and-reconcile rule, mechanised.

    Two enums with identical value sets are the same concept spelled twice, whatever they
    are called, and duplicate sources of truth are what the rule forbids. Comparing value
    sets rather than names is the only way to catch the case that actually happens: someone
    reintroducing ``PASS|FAIL|INCONCLUSIVE`` under a fresh name because the existing one was
    in a module they had not read.
    """

    existing = {
        **module_enums(contracts_root),
        **module_enums(experience_models),
    }
    duplicates = {
        f"{new_name} == {old_name}"
        for new_name, values in module_enums(routing).items()
        for old_name, old_values in existing.items()
        if values == old_values
    }
    assert duplicates == set()


REGISTRY = (
    Path(__file__).parent.parent
    / "docs"
    / "sdd"
    / "future"
    / "v0.4-v1.0"
    / "01_GOVERNANCE"
    / "Accretion_Cross_Release_Contract_Registry_v0.4_to_v1.0.md"
)


def names_reserved_by_later_releases() -> dict[str, str]:
    """Every contract name the registry's §8-§13 tables assign to v0.5 and later.

    Read out of the locked document rather than copied into this file, so that a registry
    update moves the guard with it instead of leaving a stale allow-list behind. Only the
    first column of a table row counts: prose elsewhere in those sections legitimately
    mentions v0.4-owned names (§10 contrasts ``EmbodimentCompatibilityDecision`` with our
    ``CompatibilityDecision``), and a mention is not an ownership claim.
    """

    text = REGISTRY.read_text(encoding="utf-8")
    reserved: dict[str, str] = {}
    section: str | None = None
    for line in text.splitlines():
        heading = re.match(r"^## (\d+)\.", line)
        if heading is not None:
            number = int(heading.group(1))
            section = line[3:].strip() if 8 <= number <= 13 else None
            continue
        if section is None or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0] in {"Contract", ""} or set(cells[0]) <= {"-", ":"}:
            continue
        for name in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", cells[0]):
            reserved.setdefault(name, section)
    return reserved


def test_the_registry_reservation_table_is_actually_being_read() -> None:
    """Guard the guard: a parser that matched nothing would pass the next test vacuously."""

    reserved = names_reserved_by_later_releases()
    assert reserved["EmbodimentDescriptor"].startswith("8.")
    assert reserved["PhysicalTrialApproval"].startswith("9.")
    assert reserved["PromotionDecision"].startswith("13.")
    assert len(reserved) > 40


def test_no_v04_type_name_is_reserved_by_a_later_release() -> None:
    """Registry §21's other half: the same *name* for a different artifact.

    ``test_no_v04_enum_duplicates_an_existing_one_under_a_new_name`` compares value sets
    against code that already exists, so it is blind to a name that no code has taken yet
    but the locked registry has already promised to a future release. ``PromotionDecision``
    was exactly that: registry §13 gives it to a v0.10 *contract* about capability-candidate
    release, while this module wanted it for a v0.4 *enum* about router promotion. Two
    artifacts under one name in one package cannot satisfy the §19 gate "every contract has
    one owner and schema version", and after the freeze the fix would be a §3.2 Major
    change. Hence :class:`~accretion.contracts.routing.RouterPromotionDecision`.

    Scope is the types this module defines: names it merely imports are owned by v0.1-v0.3
    and are not this milestone's to reconcile.
    """

    reserved = names_reserved_by_later_releases()
    defined = {
        name
        for name, value in vars(routing).items()
        if isinstance(value, type) and value.__module__ == routing.__name__
    }
    collisions = {name: reserved[name] for name in sorted(defined & set(reserved))}
    assert collisions == {}


def test_verification_state_stands_beside_verification_status_rather_than_replacing_it() -> None:
    """ADR-054 (a). The v0.1 enum keeps its three values and its name."""

    assert {member.value for member in VerificationStatus} == {"PASS", "FAIL", "INCONCLUSIVE"}
    assert {member.value for member in VerificationState} == {
        "PENDING",
        "PASS",
        "FAIL",
        "INCONCLUSIVE",
        "ERROR",
        "QUARANTINED",
    }
    # Registry §5.1: ERROR is not INCONCLUSIVE and neither is PASS.
    assert VerificationState.ERROR is not VerificationState.INCONCLUSIVE
    assert TERMINAL_VERIFICATION_STATES == frozenset(
        {VerificationState.PASS, VerificationState.FAIL, VerificationState.INCONCLUSIVE}
    )


@pytest.mark.parametrize(
    ("risk_class", "expected"),
    [
        (RiskClass.LOW_DIGITAL, RiskLevel.LOW),
        (RiskClass.MEDIUM_DIGITAL, RiskLevel.MEDIUM),
        (RiskClass.HIGH_DIGITAL, RiskLevel.HIGH),
        (RiskClass.SIMULATION, RiskLevel.HIGH),
        (RiskClass.PHYSICAL_HIGH, RiskLevel.CRITICAL),
    ],
)
def test_risk_level_for_maps_each_actionable_class_to_its_approval_level(
    risk_class: RiskClass, expected: RiskLevel
) -> None:
    """ADR-054 (d)'s mapping, transcribed from the decision rather than from the code."""

    assert risk_level_for(risk_class) is expected


def test_risk_level_for_refuses_to_map_prohibited() -> None:
    """A prohibited action is refused, not escalated.

    Returning ``CRITICAL`` would turn "never" into "ask a senior human", which is the
    weakening registry §3.2 forbids outright.
    """

    with pytest.raises(ValueError, match="PROHIBITED has no RiskLevel"):
        risk_level_for(RiskClass.PROHIBITED)


def test_risk_level_for_is_total_over_every_risk_class() -> None:
    """Totality asserted over the enum, so a sixth actionable class cannot be forgotten."""

    for risk_class in RiskClass:
        if risk_class is RiskClass.PROHIBITED:
            continue
        assert isinstance(risk_level_for(risk_class), RiskLevel)


def test_the_non_recoverable_owners_are_the_three_registry_5_4_names() -> None:
    assert NON_RECOVERABLE_FAILURE_OWNERS == frozenset(
        {FailureOwner.SAFETY, FailureOwner.AUTHORITY, FailureOwner.UNKNOWN}
    )


# --------------------------------------------------------------------------------------
# The canonical header (registry §3; ADR-057)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_every_contract_defaults_to_the_frozen_schema_version(
    model: type[CanonicalContract],
) -> None:
    document = rebuilt(model)
    document.pop("schema_version")
    assert model.model_validate(document).schema_version == CONTRACT_SCHEMA_VERSION


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
@pytest.mark.parametrize("version", ["0.9.0", "2.0.0", "17.3.1"])
def test_an_unknown_major_is_rejected(model: type[CanonicalContract], version: str) -> None:
    with pytest.raises(ValidationError, match="declares major"):
        model.model_validate(rebuilt(model, schema_version=version))


def test_the_supported_major_is_one() -> None:
    assert SUPPORTED_SCHEMA_MAJOR == 1
    assert CONTRACT_SCHEMA_VERSION.startswith("1.")


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_a_contract_cannot_be_relabelled_with_another_contracts_type(
    model: type[CanonicalContract],
) -> None:
    other = next(item for item in CONTRACT_INVENTORY if item is not model)
    with pytest.raises(ValidationError):
        model.model_validate(rebuilt(model, contract_type=other.CONTRACT_TYPE))


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_an_unknown_field_is_refused(model: type[CanonicalContract]) -> None:
    """ADR-057: ``extra="forbid"`` stands until M8 introduces a second writer."""

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model.model_validate(rebuilt(model, unexpected_field="whatever"))


@pytest.mark.parametrize(
    "model",
    [item for item in CONTRACT_INVENTORY if item.ID_KIND is not None],
    ids=[item.__name__ for item in CONTRACT_INVENTORY if item.ID_KIND is not None],
)
def test_a_contract_with_an_id_kind_refuses_a_foreign_prefix(
    model: type[CanonicalContract],
) -> None:
    with pytest.raises(ValidationError, match="identity prefix required by ADR-055"):
        model.model_validate(rebuilt(model, contract_id="zzz_01ARZ3NDEKTSV4RRFFQ69G5FAV"))


@pytest.mark.parametrize(
    "model",
    [item for item in CONTRACT_INVENTORY if item.PROJECT_SCOPED],
    ids=[item.__name__ for item in CONTRACT_INVENTORY if item.PROJECT_SCOPED],
)
def test_a_project_scoped_contract_requires_its_project(
    model: type[CanonicalContract],
) -> None:
    document = rebuilt(model)
    document["project_id"] = None
    with pytest.raises(ValidationError, match="project-scoped"):
        model.model_validate(document)


def test_the_three_workspace_scoped_contracts_are_the_router_learning_records() -> None:
    """SDD §7.12 makes ``project_id`` nullable for a workspace router; two neighbours follow."""

    workspace_scoped = sorted(
        model.__name__ for model in CONTRACT_INVENTORY if not model.PROJECT_SCOPED
    )
    assert workspace_scoped == [
        "RouterModelVersion",
        "RouterPromotionReport",
        "RouterTrainingSnapshot",
    ]


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_a_contract_seals_its_own_digest_when_none_is_supplied(
    model: type[CanonicalContract],
) -> None:
    parsed = model.model_validate(rebuilt(model))
    assert re.fullmatch(r"[0-9a-f]{64}", parsed.content_hash)
    assert parsed.content_hash == content_hash(parsed)


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_a_contract_verifies_a_supplied_digest_instead_of_overwriting_it(
    model: type[CanonicalContract],
) -> None:
    with pytest.raises(ValidationError, match="does not match the digest of this payload"):
        model.model_validate({**complete(model), "content_hash": "0" * 64})


def test_the_objective_reference_is_the_type_of_the_shared_header_field() -> None:
    """Registry §3's optional ``objective_contract_ref``, resolved to the module's own type."""

    reference = ObjectiveContractRef.model_validate(complete(ObjectiveContractRef))
    node = NodeContract.model_validate(rebuilt(NodeContract))
    assert isinstance(node.objective_contract_ref, ObjectiveContractRef)
    assert isinstance(reference.objective_contract_ref, ObjectiveContractRef)


# --------------------------------------------------------------------------------------
# Contract-specific rules
# --------------------------------------------------------------------------------------


def test_an_objective_cannot_declare_the_same_scope_item_in_and_out() -> None:
    with pytest.raises(ValidationError, match="both in and out of scope"):
        ObjectiveContract.model_validate(
            rebuilt(ObjectiveContract, scope_in=["routing"], scope_out=["routing"])
        )


def test_a_node_contract_must_name_the_objective_revision_it_was_authorised_against() -> None:
    document = rebuilt(NodeContract)
    document["objective_contract_ref"] = None
    with pytest.raises(ValidationError, match="must name the objective revision"):
        NodeContract.model_validate(document)


def test_a_node_contract_refuses_a_prohibited_risk_class() -> None:
    with pytest.raises(ValidationError, match="may never run"):
        NodeContract.model_validate(rebuilt(NodeContract, allowed_risk_class="PROHIBITED"))


def test_a_node_contracts_immutable_hash_is_sealed_and_verified() -> None:
    node = NodeContract.model_validate(rebuilt(NodeContract))
    assert node.immutable_hash == content_hash(
        node, exclude=("content_hash", "immutable_hash")
    )
    with pytest.raises(ValidationError, match="does not match the digest of this node contract"):
        NodeContract.model_validate({**rebuilt(NodeContract), "immutable_hash": "0" * 64})


def test_a_node_contracts_content_hash_commits_to_its_immutable_hash() -> None:
    """The sealing order, asserted rather than assumed.

    ``immutable_hash`` is sealed first and the header digest is taken over a document that
    already contains it, so a tampered immutable hash cannot be made consistent by also
    recomputing the header digest — the two disagree in a way that is detectable from either
    end.
    """

    node = NodeContract.model_validate(rebuilt(NodeContract))
    payload = node.model_dump(mode="python")
    assert payload["immutable_hash"] == node.immutable_hash
    without = {key: value for key, value in payload.items() if key != "immutable_hash"}
    assert content_hash(without) != node.content_hash


def test_graph_revision_is_the_integer_pair_v02_actually_stores() -> None:
    """Registry §7.2 asks for a ``graph_revision_id``; v0.2 has a monotonic int and a graph id.

    The test pins the choice ADR-055's "adapt the layout, keep the semantics" allowance
    permits, so that a later refactor toward a synthetic id has to argue with a red test.
    """

    node = NodeContract.model_validate(rebuilt(NodeContract))
    assert isinstance(node.graph_revision, int)
    assert node.graph_revision >= 1
    assert node.run_graph_id.startswith("rgr_")
    with pytest.raises(ValidationError):
        NodeContract.model_validate(rebuilt(NodeContract, graph_revision=0))


def test_the_independence_flags_cannot_be_turned_off(  # OQ-418
) -> None:
    """OQ-418: a separate context is mandatory, so the flag is ``Literal[True]``.

    A ``bool`` would have let a spec be *persisted* with the guarantee disabled, and every
    downstream check would have read it as a legitimate configuration.
    """

    for flag in ("producer_cannot_self_accept", "separate_context_required"):
        with pytest.raises(ValidationError, match="Input should be True"):
            IndependenceRequirements(**{flag: False})  # type: ignore[arg-type]
    assert IndependenceRequirements().distinct_runtime_preferred is True
    relaxed = IndependenceRequirements(distinct_runtime_preferred=False)
    assert relaxed.distinct_runtime_preferred is False


def test_a_verification_spec_cannot_pre_accept_an_error_or_a_pending_state() -> None:
    for outcome in ("ERROR", "PENDING", "QUARANTINED"):
        with pytest.raises(ValidationError, match="accepted_outcomes may not contain"):
            VerificationSpec.model_validate(
                rebuilt(VerificationSpec, accepted_outcomes=["PASS", outcome])
            )


def test_a_verification_spec_without_a_required_claim_is_a_report_not_a_verification() -> None:
    document = rebuilt(VerificationSpec)
    document["claims"] = [{**document["claims"][0], "criticality": "SUPPORTING"}]
    with pytest.raises(ValidationError, match="can never block acceptance"):
        VerificationSpec.model_validate(document)


def test_a_routing_context_refuses_features_computed_in_another_workspace() -> None:
    document = rebuilt(RoutingContext)
    document["task_features"] = {
        **document["task_features"],
        "workspace_id": "wks_someone-elses-workspace",
    }
    document["task_features"].pop("content_hash", None)
    with pytest.raises(ValidationError, match="cannot cross a tenancy boundary"):
        RoutingContext.model_validate(document)


def test_two_configurations_with_the_same_tuple_share_a_configuration_hash() -> None:
    """§9.2 canonicalises behaviourally equivalent candidates by configuration signature.

    Same six semantic fields, different ids, different clocks, different labels — and the
    same signature, or experience gathered under one identity would never be found under the
    other. The header digests differ, which is the other half of the claim: two documents,
    one execution surface.
    """

    first = ExecutionConfiguration.model_validate(rebuilt(ExecutionConfiguration))
    second = ExecutionConfiguration.model_validate(
        rebuilt(
            ExecutionConfiguration,
            contract_id="cfg_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            created_at="2027-11-05T22:00:00Z",
            labels={"unrelated": "metadata"},
        )
    )
    assert first.configuration_hash == second.configuration_hash
    assert first.content_hash != second.content_hash


def test_changing_the_execution_tuple_changes_the_configuration_hash() -> None:
    baseline = ExecutionConfiguration.model_validate(rebuilt(ExecutionConfiguration))
    document = rebuilt(ExecutionConfiguration)
    document["skills"] = []
    changed = ExecutionConfiguration.model_validate(document)
    assert changed.configuration_hash != baseline.configuration_hash


def test_the_configuration_signature_covers_every_non_header_field() -> None:
    """A field outside the signature would let two different surfaces share one hash."""

    header_fields = set(CanonicalContract.model_fields)
    body = set(ExecutionConfiguration.model_fields) - header_fields - {"configuration_hash"}
    assert body == set(_CONFIGURATION_SIGNATURE_FIELDS)


def test_a_candidate_that_is_not_hard_eligible_cannot_be_the_audited_fallback() -> None:
    with pytest.raises(ValidationError, match="cannot be the audited fallback"):
        ConfigurationCandidate.model_validate(
            rebuilt(ConfigurationCandidate, hard_eligible=False, fallback_eligible=True)
        )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (CompatibilityStatus.COMPATIBLE, True),
        (CompatibilityStatus.INCOMPATIBLE, False),
        (CompatibilityStatus.UNKNOWN, False),
    ],
)
def test_only_compatible_counts_as_compatible(
    status: CompatibilityStatus, expected: bool
) -> None:
    """SDD §7.7: ``UNKNOWN`` MUST NOT be treated as compatible for a required constraint."""

    decision = CompatibilityDecision.model_validate(
        rebuilt(CompatibilityDecision, status=status.value)
    )
    assert decision.is_compatible() is expected


def test_unknown_is_the_case_that_makes_is_compatible_worth_having() -> None:
    """Stated separately because it is the assertion a careless refactor would delete.

    ``status is not INCOMPATIBLE`` reads as an equivalent spelling and is not one: it admits
    ``UNKNOWN``, which is how an unchecked assumption reaches dispatch wearing a receipt.
    """

    unknown = CompatibilityDecision.model_validate(
        rebuilt(CompatibilityDecision, status="UNKNOWN")
    )
    assert unknown.status is CompatibilityStatus.UNKNOWN
    assert unknown.is_compatible() is False


@pytest.mark.parametrize(
    "poison",
    [
        {"labels": {"authorization": "Bearer abcdef0123456789"}},
        {"labels": {"api_key": "harmless-looking"}},
        {"labels": {"note": "call with Bearer sk-live-abcdefghijklmnopqrst"}},
        {"labels": {"note": "eyJhbGciOi.eyJzdWIiOiJh.c2lnbmF0dXJl"}},
    ],
)
def test_a_receipt_refuses_a_secret_shaped_value(poison: dict[str, Any]) -> None:
    """SDD §12 and §14.2. Both halves: a credential-shaped *key* and a credential-shaped *value*.

    Refused rather than redacted, because a receipt is hashed and replayed: rewriting one
    would produce a document that no longer matches its own digest and an audit trail that
    had been edited.
    """

    with pytest.raises(ValidationError, match="secret-shaped value"):
        RoutingDecisionReceipt.model_validate(rebuilt(RoutingDecisionReceipt, **poison))


def test_a_receipt_accepts_an_ordinary_label() -> None:
    """The negative control: the refusal above must not be refusing everything."""

    receipt = RoutingDecisionReceipt.model_validate(
        rebuilt(RoutingDecisionReceipt, labels={"team": "routing-platform"})
    )
    assert receipt.labels == {"team": "routing-platform"}


def test_a_receipt_that_selected_nothing_must_say_a_human_is_needed() -> None:
    document = rebuilt(RoutingDecisionReceipt)
    document["selected_configuration_id"] = None
    document["selected_configuration_hash"] = None
    with pytest.raises(ValidationError, match="only HUMAN_REVIEW_REQUIRED may select nothing"):
        RoutingDecisionReceipt.model_validate(document)


def test_a_receipt_cannot_carry_half_a_selection() -> None:
    document = rebuilt(RoutingDecisionReceipt)
    document["selected_configuration_hash"] = None
    with pytest.raises(ValidationError, match="present or absent together"):
        RoutingDecisionReceipt.model_validate(document)


def test_an_exploring_receipt_must_record_its_propensity() -> None:
    """§9.5: without the behaviour propensity the decision is unusable for off-policy evaluation."""

    document = rebuilt(RoutingDecisionReceipt, decision_type="EXPLORE")
    document["selection_propensity"] = None
    with pytest.raises(ValidationError, match="behaviour propensity"):
        RoutingDecisionReceipt.model_validate(document)


def test_a_verification_pass_cannot_contain_a_claim_that_did_not_pass() -> None:
    with pytest.raises(ValidationError, match="status is PASS while claims"):
        IndependentVerificationResult.model_validate(
            rebuilt(IndependentVerificationResult, status="PASS")
        )


def test_an_independent_verification_may_link_the_v01_result_it_came_from() -> None:
    """ADR-054 (a): the two verification records coexist and are joined, not merged."""

    result = IndependentVerificationResult.model_validate(
        rebuilt(IndependentVerificationResult)
    )
    assert result.source_verification_id is not None
    document = rebuilt(IndependentVerificationResult)
    document["source_verification_id"] = None
    assert (
        IndependentVerificationResult.model_validate(document).source_verification_id is None
    )


def test_the_experience_record_declares_none_of_the_p7_experience_fields() -> None:
    """ADR-054 (b). The projection is keyed by ``experience_id`` and copies nothing.

    A field name shared with ``Experience`` would be a second copy of a value that already
    has an owner, and the two would diverge the first time an experience was retracted. The
    header fields are excluded from the comparison: they belong to the registry §3 header,
    not to the P7 record, and ``created_at`` appearing in both is a coincidence of naming.
    """

    header_fields = set(CanonicalContract.model_fields)
    projection_fields = set(ExperienceRecord.model_fields) - header_fields
    p7_fields = set(experience_models.Experience.model_fields)
    # Non-vacuity: the disjointness below would also hold if the projection declared nothing.
    assert len(projection_fields) == 13
    assert len(p7_fields) > 30
    assert projection_fields & p7_fields == set()


def test_the_experience_record_is_keyed_by_the_p7_experience_id() -> None:
    record = ExperienceRecord.model_validate(rebuilt(ExperienceRecord))
    assert ExperienceRecord.ID_KIND == "experience"
    assert record.contract_id.startswith("exp_")


def test_an_experience_cannot_be_shared_more_widely_than_it_was_permitted() -> None:
    with pytest.raises(ValidationError, match="cannot be shared more widely"):
        ExperienceRecord.model_validate(rebuilt(ExperienceRecord, visibility="PROJECT"))


def test_only_a_verified_uncontradicted_experience_is_eligible_for_learning() -> None:
    with pytest.raises(ValidationError, match="only a verified outcome is"):
        ExperienceRecord.model_validate(
            rebuilt(ExperienceRecord, local_verification_status="FAIL")
        )
    with pytest.raises(ValidationError, match="contradiction is OPEN"):
        ExperienceRecord.model_validate(
            rebuilt(ExperienceRecord, contradiction_status="OPEN")
        )


@pytest.mark.parametrize("owner", sorted(NON_RECOVERABLE_FAILURE_OWNERS))
def test_a_failure_that_stops_automatic_recovery_cannot_be_retryable(owner: FailureOwner) -> None:
    """Registry §5.4. ``SAFETY``, ``AUTHORITY`` and unresolved ``UNKNOWN`` stop recovery."""

    with pytest.raises(ValidationError, match="stops automatic recovery"):
        FailureEvent.model_validate(rebuilt(FailureEvent, assigned_owner=owner.value))


def test_a_failure_event_refuses_a_configuration_hash_that_is_not_a_digest() -> None:
    """§9.7 compares these for equality; two spellings of one digest would let a repeat through."""

    with pytest.raises(ValidationError, match="not a lowercase sha256 digest"):
        FailureEvent.model_validate(
            rebuilt(FailureEvent, attempted_configuration_hashes=["A" * 64])
        )


def test_a_workspace_router_belongs_to_no_project_and_an_adapter_must_name_one() -> None:
    with pytest.raises(ValidationError, match="belongs to no single project"):
        RouterModelVersion.model_validate(rebuilt(RouterModelVersion, scope="TEAM_WORKSPACE"))
    document = rebuilt(RouterModelVersion)
    document["project_id"] = None
    with pytest.raises(ValidationError, match="must name the project it adapts to"):
        RouterModelVersion.model_validate(document)


def test_a_training_snapshot_refuses_an_empty_window_and_a_repeated_experience() -> None:
    with pytest.raises(ValidationError, match="is not after window_start"):
        RouterTrainingSnapshot.model_validate(
            rebuilt(RouterTrainingSnapshot, window_end="2025-06-01T00:00:00Z")
        )
    duplicate = complete(RouterTrainingSnapshot)["included_experience_ids"][0]
    with pytest.raises(ValidationError, match="repeats an id"):
        RouterTrainingSnapshot.model_validate(
            rebuilt(RouterTrainingSnapshot, included_experience_ids=[duplicate, duplicate])
        )


def test_a_project_on_both_sides_of_a_training_split_is_a_leak() -> None:
    document = rebuilt(RouterTrainingSnapshot)
    split = dict(document["split"])
    split["holdout_project_ids"] = list(split["training_project_ids"])
    document["split"] = split
    with pytest.raises(ValidationError, match="leaks"):
        RouterTrainingSnapshot.model_validate(document)


def test_a_critical_regression_blocks_promotion() -> None:
    """§10.3. The block is the reason the report exists."""

    with pytest.raises(ValidationError, match="critical correctness or safety regression"):
        RouterPromotionReport.model_validate(
            rebuilt(RouterPromotionReport, decision="PROMOTE")
        )


def test_a_failed_critical_cohort_blocks_promotion_even_with_an_empty_regression_list() -> None:
    """Otherwise the block could be avoided by leaving a list empty."""

    document = rebuilt(RouterPromotionReport, decision="PROMOTE")
    document["critical_regressions"] = []
    document["false_acceptance_non_regression"] = {
        **document["false_acceptance_non_regression"],
        "passed": True,
    }
    with pytest.raises(ValidationError, match="critical cohorts"):
        RouterPromotionReport.model_validate(
            {
                **document,
                "cohort_results": [
                    {
                        **document["cohort_results"][0],
                        "critical": True,
                        "comparison": {
                            **document["cohort_results"][0]["comparison"],
                            "passed": False,
                        },
                    }
                ],
            }
        )


def test_a_promotion_names_the_human_who_authorised_it() -> None:
    """OQ-411: promotion approval belongs to a workspace admin or research owner."""

    document = rebuilt(RouterPromotionReport, decision="PROMOTE")
    document["critical_regressions"] = []
    document["false_acceptance_non_regression"] = {
        **document["false_acceptance_non_regression"],
        "passed": True,
    }
    document["approved_by"] = None
    with pytest.raises(ValidationError, match="without an approver"):
        RouterPromotionReport.model_validate(document)


def test_an_undisclosed_tradeoff_is_a_regression() -> None:
    document = rebuilt(RouterPromotionReport)
    document["noncritical_tradeoffs"] = [
        {**document["noncritical_tradeoffs"][0], "disclosed_bound": None}
    ]
    with pytest.raises(ValidationError, match="carry no disclosed bound"):
        RouterPromotionReport.model_validate(document)


def test_shadow_agreement_must_match_the_hashes_it_summarises() -> None:
    with pytest.raises(ValidationError, match="say otherwise"):
        ShadowDecision.model_validate(rebuilt(ShadowDecision, agreement=False))
    document = rebuilt(ShadowDecision, agreement=False)
    document["shadow_configuration_hash"] = "b" * 64
    assert ShadowDecision.model_validate(document).agreement is False


def test_two_decisions_that_selected_nothing_have_not_agreed() -> None:
    """The null case, which a naive equality check would call agreement."""

    document = rebuilt(ShadowDecision, agreement=True)
    document["executed_configuration_hash"] = None
    document["shadow_configuration_hash"] = None
    with pytest.raises(ValidationError, match="say otherwise"):
        ShadowDecision.model_validate(document)


# --------------------------------------------------------------------------------------
# Hash sensitivity: every field of every contract
# --------------------------------------------------------------------------------------


def mutate(value: Any) -> Any:
    """Return a value that is definitely different, and definitely still canonicalisable.

    The mutation happens on the *dumped payload* rather than on the model, so it never has
    to satisfy a field constraint: the question is whether the digest covers the field, not
    whether the contract would accept the new value. Every branch produces a value with a
    different canonical form — a string gains a character, a number moves, a list gains an
    element, a mapping gains a key — so a digest that did not move can only mean the field
    was outside the hash input.
    """

    if value is None:
        return "mutation-sentinel"
    if isinstance(value, bool):
        return not value
    if isinstance(value, StrEnum):
        return f"{value.value}-mutated"
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 1.0
    if isinstance(value, Decimal):
        return value + Decimal(1)
    if isinstance(value, datetime):
        return value + timedelta(seconds=1)
    if isinstance(value, str):
        return f"{value}-mutated"
    if isinstance(value, list):
        return [*value, "mutation-sentinel"]
    if isinstance(value, dict):
        return {**value, "mutation_sentinel_key": True}
    raise AssertionError(f"no mutation defined for {type(value).__name__}")


HASH_SENSITIVITY_CASES = [
    (model, field)
    for model in CONTRACT_INVENTORY
    for field in sorted(model.model_fields)
    if field != "content_hash"
]


@pytest.mark.parametrize(
    ("model", "field"),
    HASH_SENSITIVITY_CASES,
    ids=[f"{model.__name__}.{field}" for model, field in HASH_SENSITIVITY_CASES],
)
def test_changing_any_field_changes_the_digest(
    model: type[CanonicalContract], field: str
) -> None:
    """Every declared field of every contract, one at a time.

    ``content_hash(model)`` and ``content_hash(model.model_dump())`` are the same digest by
    construction — the hasher dumps a model in python mode before normalising — so mutating
    the dumped payload is a faithful stand-in for mutating the contract, and it works for
    fields whose constraints would reject an arbitrary new value.
    """

    parsed = model.model_validate(complete(model))
    payload = parsed.model_dump(mode="python")
    assert content_hash(payload) == parsed.content_hash

    mutated = {**payload, field: mutate(payload[field])}
    assert content_hash(mutated) != parsed.content_hash, (
        f"{model.__name__}.{field} is outside the content hash, so a tampered document "
        "could change it for free"
    )


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_the_content_hash_field_is_excluded_from_its_own_input(
    model: type[CanonicalContract],
) -> None:
    """The one field that must *not* move the digest, or no document could ever carry one."""

    parsed = model.model_validate(complete(model))
    payload = parsed.model_dump(mode="python")
    for claimed in ("", "not-a-hash", "f" * 64):
        assert content_hash({**payload, "content_hash": claimed}) == parsed.content_hash


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_key_order_does_not_change_a_contracts_digest(
    model: type[CanonicalContract],
) -> None:
    """Object key order is presentation; the digest must not see it."""

    parsed = model.model_validate(complete(model))
    payload = parsed.model_dump(mode="python")
    reversed_payload = dict(reversed(list(payload.items())))
    assert list(reversed_payload) != list(payload)
    assert content_hash(reversed_payload) == parsed.content_hash


def test_the_reference_and_the_datetime_conventions_survive_a_json_round_trip() -> None:
    """A timestamp written by pydantic and re-read as text must hash the same either way.

    This is the property that lets an auditor with the committed JSON and no Python reach the
    same digest as the writer, and it is the one that would break silently if a datetime ever
    started serialising with an offset instead of ``Z``.
    """

    parsed = ObjectiveContractRef.model_validate(complete(ObjectiveContractRef))
    as_json = json.loads(json.dumps(parsed.model_dump(mode="json")))
    assert content_hash(as_json) == parsed.content_hash
    assert parsed.approved_at.tzinfo is not None
    assert parsed.approved_at.astimezone(UTC) == parsed.approved_at


def test_the_header_base_is_complete_only_once_the_routing_module_is_imported() -> None:
    """Pins a coupling rather than hiding it.

    ``CanonicalContract.objective_contract_ref`` is a forward reference resolved by the
    ``model_rebuild`` call at the bottom of ``accretion.contracts.routing``. A contract family
    that imported only ``canonical.py`` would fail its first validation with a class-build
    error, so the fact is asserted here where the next family's author will find it.
    """

    import subprocess
    import sys

    probe = (
        "import accretion.contracts.canonical as c; "
        "print(c.CanonicalContract.__pydantic_complete__); "
        "import accretion.contracts.routing; "
        "print(c.CanonicalContract.__pydantic_complete__)"
    )
    output = subprocess.run(
        [sys.executable, "-c", probe], check=True, capture_output=True, text=True
    ).stdout.split()
    assert output == ["False", "True"], output


def test_a_document_that_lost_its_seal_is_resealed_which_is_why_stores_must_require_it() -> None:
    """The behaviour PR3's store guards against, stated as a fact rather than assumed away.

    Construction seals an unsealed body; it cannot know whether the body was ever sealed
    before. A tampered copy with its ``content_hash`` removed therefore validates and comes
    back with a fresh digest. The read path is responsible for refusing a persisted payload
    without a digest; this case pins the behaviour that makes that responsibility real.
    """

    import copy
    import json
    from pathlib import Path

    from accretion.contracts.routing import ObjectiveContract

    fixtures = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"
    fixture = json.loads((fixtures / "objective_contract" / "complete.json").read_text("utf-8"))
    sealed = ObjectiveContract.model_validate(fixture)
    tampered = copy.deepcopy(fixture)
    tampered["goal"] = tampered["goal"] + " TAMPERED"
    tampered.pop("content_hash")
    resealed = ObjectiveContract.model_validate(tampered)
    assert resealed.content_hash != sealed.content_hash
    with_stale_seal = copy.deepcopy(tampered)
    with_stale_seal["content_hash"] = sealed.content_hash
    with pytest.raises(ValidationError):
        ObjectiveContract.model_validate(with_stale_seal)
