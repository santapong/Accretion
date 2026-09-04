"""The golden fixtures for the v0.4 contract family (registry §19).

Registry §19 requires golden fixtures covering the minimal, complete, invalid and
unknown-version cases for every contract, and this module is parametrized over
``CONTRACT_INVENTORY`` crossed with those four kinds. That crossing is deliberate: a
contract added to the family without fixtures is a *collection* error here — the file is
missing, the parametrized case is red — rather than a quiet gap that nobody notices until a
milestone later.

These tests **read** the committed files and never regenerate them. A test that rebuilt its
own expectation from the models would prove only that the models agree with themselves;
reading a committed document proves that the document a reader will actually be handed still
parses, still hashes to what it claims, and still fails where it is supposed to.

The two hash properties are worth stating separately, because they are not the same claim:

* the ``content_hash`` recorded inside ``complete.json`` equals the digest of the *parsed
  model*, which is what a writer computes; and
* it also equals the digest of the *raw JSON as committed*, which is what an auditor with
  the file and no Python computes.

The second only holds because the canonical form of a decimal-as-string and an RFC 3339
``Z`` timestamp is identical whether it arrives as JSON text or as a parsed Python object —
the property ADR-056 exists to guarantee, checked here on nineteen real documents rather
than on a synthetic vector.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from accretion.contracts.canonical import CanonicalContract, content_hash
from accretion.contracts.routing import CONTRACT_INVENTORY

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"
FIXTURE_KINDS: tuple[str, ...] = ("minimal", "complete", "invalid", "unknown_version")
UNKNOWN_MAJOR_VERSION = "2.0.0"

IDS = [model.__name__ for model in CONTRACT_INVENTORY]

# `created_at` and `content_hash` are pinned in every fixture even though the model supplies
# both. Without a pinned clock the digest would change on every run, and a golden fixture
# whose digest moves is not golden. They are therefore exempt from the minimality check
# below, which asserts that every *other* field in `minimal.json` is one the model genuinely
# cannot be constructed without.
PINNED_HEADER_FIELDS = frozenset({"created_at", "content_hash"})


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def fixture_path(model: type[CanonicalContract], kind: str) -> Path:
    return FIXTURE_ROOT / snake_case(model.__name__) / f"{kind}.json"


def load(model: type[CanonicalContract], kind: str) -> dict[str, Any]:
    path = fixture_path(model, kind)
    assert path.exists(), (
        f"{path} is missing; run scripts/export_contract_fixtures.py or add the contract's "
        "fixtures by hand"
    )
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_every_contract_has_every_fixture_kind(
    model: type[CanonicalContract], kind: str
) -> None:
    """Seventy-six files. A forgotten contract or a forgotten kind is one red case."""

    assert fixture_path(model, kind).exists()


def test_the_fixture_tree_holds_exactly_one_directory_per_contract() -> None:
    """An orphan directory left by a rename would keep testing a contract that no longer exists."""

    directories = sorted(path.name for path in FIXTURE_ROOT.iterdir() if path.is_dir())
    assert directories == sorted(snake_case(name) for name in IDS)


# --------------------------------------------------------------------------------------
# minimal
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_the_minimal_fixture_parses_and_seals_to_the_digest_it_records(
    model: type[CanonicalContract],
) -> None:
    document = load(model, "minimal")
    parsed = model.model_validate(document)
    assert parsed.content_hash == document["content_hash"]
    assert re.fullmatch(r"[0-9a-f]{64}", parsed.content_hash)


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_the_minimal_fixture_carries_nothing_the_model_could_do_without(
    model: type[CanonicalContract],
) -> None:
    """Minimality asserted by construction rather than against a hand-written field list.

    For each field in the file, remove it and require the parse to fail. That is the actual
    definition of "required" — including the cases a naive ``is_required()`` check would miss,
    such as ``NodeContract.objective_contract_ref``, which is optional in the type and made
    mandatory by a validator, and ``project_id``, which is optional in the type and required
    by the header's project-scoping rule.
    """

    document = load(model, "minimal")
    for field in sorted(set(document) - PINNED_HEADER_FIELDS):
        reduced = {key: value for key, value in document.items() if key != field}
        with pytest.raises(ValidationError):
            model.model_validate(reduced)


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_the_minimal_fixture_names_only_fields_the_model_declares(
    model: type[CanonicalContract],
) -> None:
    assert set(load(model, "minimal")) <= set(model.model_fields)


# --------------------------------------------------------------------------------------
# complete
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_the_complete_fixture_populates_every_field_with_a_non_null_value(
    model: type[CanonicalContract],
) -> None:
    """"Complete" means complete: every declared field, and none of them null.

    A fixture that left an optional field out would exercise the same code path as the
    minimal one, and the whole point of the second file is to walk the branches the first
    one never reaches.
    """

    document = load(model, "complete")
    assert set(document) == set(model.model_fields)
    nulls = sorted(key for key, value in document.items() if value is None)
    assert nulls == [], f"{model.__name__} complete fixture leaves {nulls} null"


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_the_complete_fixture_parses(model: type[CanonicalContract]) -> None:
    parsed = model.model_validate(load(model, "complete"))
    assert parsed.contract_type == model.CONTRACT_TYPE


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_the_parsed_complete_fixture_hashes_to_the_digest_it_records(
    model: type[CanonicalContract],
) -> None:
    """A hand edit anywhere in the document moves the digest and fails here."""

    document = load(model, "complete")
    assert model.model_validate(document).content_hash == document["content_hash"]


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_the_raw_committed_json_hashes_to_the_same_digest_as_the_parsed_model(
    model: type[CanonicalContract],
) -> None:
    """ADR-056's cross-representation guarantee, on the real documents.

    An auditor holding the file and no Python must reach the same digest as the writer that
    produced it, or the hash is only a checksum of one implementation's internals.
    """

    document = load(model, "complete")
    assert content_hash(document) == document["content_hash"]


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_the_complete_fixture_round_trips_through_the_model_unchanged(
    model: type[CanonicalContract],
) -> None:
    """Parse, dump, compare. A field lost in serialization is a field lost in the store."""

    document = load(model, "complete")
    assert model.model_validate(document).model_dump(mode="json") == document


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_editing_one_value_in_the_complete_fixture_breaks_its_hash(
    model: type[CanonicalContract],
) -> None:
    """The property the whole fixture scheme rests on, stated once per contract.

    ``contract_id`` is edited because every contract has one, it is a plain string, and the
    edit keeps the document otherwise valid — so the failure that follows is unambiguously
    the digest and not a constraint.
    """

    document = load(model, "complete")
    tampered = {**document, "contract_id": document["contract_id"][:-1] + "Z"}
    # "does not match the digest" and not the full header message: on `NodeContract` and
    # `ExecutionConfiguration` the *derived* digest is sealed first and catches the edit
    # before the header digest ever runs. Either refusal is the property under test.
    with pytest.raises(ValidationError, match="does not match the digest"):
        model.model_validate(tampered)


# --------------------------------------------------------------------------------------
# invalid
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_the_invalid_fixture_is_rejected_with_the_error_it_predicts(
    model: type[CanonicalContract],
) -> None:
    """``_expect`` is recorded in the file, so the fixture states its own violation.

    Asserting on the message and not merely on "something raised" is what stops an invalid
    fixture from silently starting to fail for a different reason — a renamed field, a typo
    in a nested block — and still reading as a passing test.
    """

    document = load(model, "invalid")
    expected = document.pop("_expect")
    assert isinstance(expected, str) and expected
    with pytest.raises(ValidationError) as error:
        model.model_validate(document)
    assert expected in str(error.value), (
        f"{model.__name__}: invalid fixture raised a different error than it predicts\n"
        f"expected substring: {expected!r}\nactual: {error.value}"
    )


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_the_invalid_fixture_differs_from_the_complete_one_in_exactly_one_field(
    model: type[CanonicalContract],
) -> None:
    """One named violation, not a shotgun.

    ``content_hash`` is dropped from every invalid fixture — an invalid document is not a
    sealed one — so it is excluded from the comparison. Everything else must match, or the
    fixture would be testing several rules at once and proving none of them.
    """

    complete = load(model, "complete")
    invalid = load(model, "invalid")
    invalid.pop("_expect")
    assert "content_hash" not in invalid
    differing = sorted(
        key
        for key in set(complete) | set(invalid)
        if key != "content_hash" and complete.get(key) != invalid.get(key)
    )
    assert len(differing) == 1, f"{model.__name__} invalid fixture changes {differing}"


# --------------------------------------------------------------------------------------
# unknown_version
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_the_unknown_version_fixture_is_the_complete_one_with_a_future_major(
    model: type[CanonicalContract],
) -> None:
    complete = load(model, "complete")
    unknown = load(model, "unknown_version")
    assert unknown["schema_version"] == UNKNOWN_MAJOR_VERSION
    assert {key: value for key, value in unknown.items() if key != "schema_version"} == {
        key: value for key, value in complete.items() if key != "schema_version"
    }


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_an_unknown_major_is_rejected_for_being_an_unknown_major(
    model: type[CanonicalContract],
) -> None:
    """Registry §3.2's fail-closed rule, and it must fail for the *version*.

    The unknown-version document also carries a now-stale ``content_hash``, so a reader that
    checked the digest first would still reject it — and would reject it for the wrong
    reason, sending whoever reads the error looking for a corrupted file instead of a
    document from a version they do not understand. The major check is a field validator
    precisely so that it fires first, and this assertion is what holds that ordering in place.
    """

    with pytest.raises(ValidationError, match="declares major 2"):
        model.model_validate(load(model, "unknown_version"))


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_a_known_major_with_a_higher_minor_is_still_accepted(
    model: type[CanonicalContract],
) -> None:
    """The other half of registry §3.2: a minor bump is additive and must not fail closed.

    A reader that rejected ``1.1.0`` would make every future additive field a breaking
    change, which is exactly the outcome the major/minor split exists to prevent. The digest
    moves with the version, so the fixture is re-sealed rather than reused.
    """

    document = {**load(model, "complete"), "schema_version": "1.7.3"}
    # Every digest the document carries moves with the version, so all of them are dropped
    # and re-sealed: `DERIVED_HASH_FIELDS` is declared on each contract for exactly this,
    # so the test never has to name `immutable_hash` or `configuration_hash` itself.
    for field in ("content_hash", *model.DERIVED_HASH_FIELDS):
        document.pop(field, None)
    parsed = model.model_validate(document)
    assert parsed.schema_version == "1.7.3"
