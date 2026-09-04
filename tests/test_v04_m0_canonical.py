"""Canonical JSON and content hashing (ADR-056; registry §3.1, §20.2).

The golden vectors in ``tests/fixtures/contracts/v0.4/hash_vectors.json`` were generated
once and committed. These tests **read** that file and compare; they never recompute the
expectation, because a test that derives its own expected value from the code under test
proves only that the code agrees with itself.

JSON cannot spell a `Decimal` or a `datetime`, so the fixture encodes those two as typed
literals — an object with exactly the keys ``$type`` and ``value`` — which
:func:`_materialize` turns back into Python objects on load. A third literal,
``{"$type": "model", "class": ..., "fields": ...}``, constructs a real contract from the
package root, because the pydantic path is where canonicalization is easiest to get wrong:
it is the only path that can hand its input to someone else's serializer first. Nothing
else in the file is transformed, and an unknown ``$type`` is a hard failure rather than a
silent pass-through.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel

import accretion.contracts as contracts
from accretion.contracts import ArtifactRef, PrincipalRef, PrincipalStatus
from accretion.contracts.canonical import (
    CanonicalizationError,
    canonical_json,
    content_hash,
)

VECTOR_FILE = Path(__file__).parent / "fixtures" / "contracts" / "v0.4" / "hash_vectors.json"

# The vectors the freeze promises to cover. Named here so that deleting one from the
# fixture is a red test rather than a quietly smaller suite.
REQUIRED_VECTOR_NAMES = frozenset(
    {
        "empty_object",
        "nested_scrambled_keys",
        "unicode_text",
        "integer_one",
        "float_one",
        "decimal_as_string",
        "datetime_offset_normalises_to_z",
        "bogus_content_hash_excluded",
        "model_datetime_offset_normalises_to_z",
    }
)


def _materialize(value: Any) -> Any:
    """Turn the fixture's typed literals back into the Python values they encode."""

    if isinstance(value, dict):
        if "$type" in value:
            kind = value["$type"]
            if kind == "decimal" and set(value) == {"$type", "value"}:
                return Decimal(value["value"])
            if kind == "datetime" and set(value) == {"$type", "value"}:
                return datetime.fromisoformat(value["value"])
            if kind == "model" and set(value) == {"$type", "class", "fields"}:
                model = getattr(contracts, value["class"])
                assert isinstance(model, type) and issubclass(model, BaseModel), (
                    f"{value['class']!r} is not a contract in accretion.contracts"
                )
                return model(**_materialize(value["fields"]))
            raise AssertionError(f"unknown typed literal {kind!r} in the vector file")
        return {key: _materialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_materialize(item) for item in value]
    return value


def load_vectors() -> list[dict[str, Any]]:
    """Read the committed vectors. Collection fails loudly if the file is gone."""

    return list(json.loads(VECTOR_FILE.read_text(encoding="utf-8")))


VECTORS = load_vectors()


def test_the_committed_vector_file_covers_every_promised_case() -> None:
    names = {vector["name"] for vector in VECTORS}
    assert REQUIRED_VECTOR_NAMES <= names
    assert len(names) == len(VECTORS), "vector names must be unique"


@pytest.mark.parametrize("vector", VECTORS, ids=[vector["name"] for vector in VECTORS])
def test_every_committed_vector_still_hashes_to_its_recorded_digest(vector: dict[str, Any]) -> None:
    value = _materialize(vector["input"])
    assert content_hash(value) == vector["sha256"], vector["description"]


# The two exclusion vectors carry a top-level ``content_hash``, so for them the digest is
# deliberately NOT sha256 over the whole canonical form. They are covered above instead;
# filtering here beats skipping inside the test, which would read as a hole in the suite.
VECTORS_WITHOUT_A_CONTENT_HASH = [
    vector
    for vector in VECTORS
    if not (isinstance(vector["input"], dict) and "content_hash" in vector["input"])
]


@pytest.mark.parametrize(
    "vector",
    VECTORS_WITHOUT_A_CONTENT_HASH,
    ids=[vector["name"] for vector in VECTORS_WITHOUT_A_CONTENT_HASH],
)
def test_a_vector_without_a_content_hash_field_hashes_its_whole_canonical_form(
    vector: dict[str, Any],
) -> None:
    """With nothing to exclude, ``content_hash`` is exactly sha256 over the canonical bytes."""

    value = _materialize(vector["input"])
    assert hashlib.sha256(canonical_json(value)).hexdigest() == vector["sha256"]


def test_hashing_the_same_value_twice_gives_the_same_digest() -> None:
    value = {"b": [1, 2, {"z": None}], "a": "x"}
    assert content_hash(value) == content_hash(value)
    assert canonical_json(value) == canonical_json(value)


def test_two_separately_built_equal_values_give_the_same_digest() -> None:
    first = {"a": 1, "nested": {"x": [True, None]}}
    second = {"nested": {"x": [True, None]}, "a": 1}
    assert first == second
    assert content_hash(first) == content_hash(second)


def test_key_order_does_not_change_the_digest_at_any_depth() -> None:
    scrambled = next(item for item in VECTORS if item["name"] == "nested_scrambled_keys")
    value = _materialize(scrambled["input"])

    def sort_deep(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: sort_deep(node[key]) for key in sorted(node)}
        if isinstance(node, list):
            return [sort_deep(item) for item in node]
        return node

    reordered = sort_deep(value)
    assert list(reordered) != list(value), "the fixture must actually be out of order"
    assert content_hash(reordered) == scrambled["sha256"]


def test_array_order_does_change_the_digest() -> None:
    """Object key order is presentation; array order is data."""

    assert content_hash({"items": [1, 2]}) != content_hash({"items": [2, 1]})


def test_garbage_in_the_top_level_content_hash_does_not_move_the_digest() -> None:
    body = {"contract_type": "objective-contract", "schema_version": "1.0.0", "value": 41}
    baseline = content_hash(body)

    for claimed in ("", "not-a-hash-at-all", "0" * 64, "f" * 64):
        assert content_hash({**body, "content_hash": claimed}) == baseline


def test_excluding_a_key_that_is_absent_is_not_an_error() -> None:
    """A payload that has not been hashed yet is the normal case at write time."""

    assert content_hash({"a": 1}) == content_hash({"a": 1}, exclude=("content_hash",))


def test_a_nested_content_hash_is_part_of_what_the_outer_contract_commits_to() -> None:
    outer = {"content_hash": "ignored", "ref": {"content_hash": "a" * 64}}
    changed = {"content_hash": "ignored", "ref": {"content_hash": "b" * 64}}
    assert content_hash(outer) != content_hash(changed)


def test_a_caller_may_exclude_other_top_level_keys() -> None:
    body = {"a": 1, "volatile": "changes every call"}
    assert content_hash(body, exclude=("volatile",)) == content_hash({"a": 1})


def test_changing_any_field_changes_the_digest() -> None:
    body = {"a": 1, "b": "x", "c": [1, 2]}
    baseline = content_hash(body)
    assert content_hash({**body, "a": 2}) != baseline
    assert content_hash({**body, "b": "y"}) != baseline
    assert content_hash({**body, "c": [1, 2, 3]}) != baseline
    assert content_hash({**body, "d": None}) != baseline


def test_the_canonical_form_carries_no_whitespace_and_sorts_its_keys() -> None:
    assert canonical_json({"b": 1, "a": {"d": 2, "c": 3}}) == b'{"a":{"c":3,"d":2},"b":1}'


def test_the_canonical_form_is_utf8_bytes_and_does_not_escape_non_ascii() -> None:
    payload = canonical_json({"thai": "สวัสดี", "emoji": "🚀"})
    assert isinstance(payload, bytes)
    assert payload == '{"emoji":"🚀","thai":"สวัสดี"}'.encode()
    assert b"\\u" not in payload


def test_an_integer_and_the_same_number_as_a_float_are_different_inputs() -> None:
    assert canonical_json({"count": 1}) == b'{"count":1}'
    assert canonical_json({"count": 1.0}) == b'{"count":1.0}'
    assert content_hash({"count": 1}) != content_hash({"count": 1.0})


def test_a_large_integer_keeps_every_digit() -> None:
    assert canonical_json({"n": 12345678901234567890}) == b'{"n":12345678901234567890}'


def test_floats_serialize_in_their_shortest_round_trip_form() -> None:
    assert canonical_json({"r": 0.1}) == b'{"r":0.1}'
    assert canonical_json({"r": 1e21}) == b'{"r":1e+21}'
    assert json.loads(canonical_json({"r": 0.3333333333333333}))["r"] == 0.3333333333333333


def test_booleans_stay_json_booleans_rather_than_becoming_numbers() -> None:
    assert canonical_json({"ok": True, "no": False}) == b'{"no":false,"ok":true}'
    assert content_hash({"ok": True}) != content_hash({"ok": 1})


def test_a_decimal_serializes_as_its_exact_string() -> None:
    assert canonical_json({"amount": Decimal("1.50")}) == b'{"amount":"1.50"}'
    # Trailing zeros are significant digits: the digest preserves what the writer wrote.
    assert content_hash({"amount": Decimal("1.50")}) != content_hash({"amount": Decimal("1.5")})
    assert content_hash({"amount": Decimal("1.5")}) != content_hash({"amount": 1.5})


def test_a_datetime_normalises_to_rfc3339_utc_with_a_literal_z() -> None:
    offset = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    assert canonical_json({"at": offset}) == b'{"at":"2026-09-05T10:00:00Z"}'
    assert content_hash({"at": offset}) == content_hash(
        {"at": datetime(2026, 9, 5, 10, 0, 0, tzinfo=UTC)}
    )


def test_a_datetime_keeps_its_microseconds() -> None:
    at = datetime(2026, 9, 5, 10, 0, 0, 123456, tzinfo=UTC)
    assert canonical_json({"at": at}) == b'{"at":"2026-09-05T10:00:00.123456Z"}'


def test_none_is_kept_rather_than_dropped() -> None:
    assert canonical_json({"a": None}) == b'{"a":null}'
    assert content_hash({"a": None}) != content_hash({})


def test_nested_structures_are_canonicalized_all_the_way_down() -> None:
    value = {
        "outer": [
            {"b": Decimal("2.0"), "a": datetime(2026, 1, 1, tzinfo=UTC)},
            [None, True, 3],
        ]
    }
    assert canonical_json(value) == (
        b'{"outer":[{"a":"2026-01-01T00:00:00Z","b":"2.0"},[null,true,3]]}'
    )


def test_a_tuple_serializes_as_an_array_like_a_list() -> None:
    assert canonical_json({"a": (1, 2)}) == canonical_json({"a": [1, 2]})


def test_a_pydantic_model_hashes_as_its_json_dump() -> None:
    ref = PrincipalRef(principal_id="usr_x", display_name="Ada", status=PrincipalStatus.ACTIVE)
    assert content_hash(ref) == content_hash(ref.model_dump(mode="json"))
    assert canonical_json(ref) == b'{"display_name":"Ada","principal_id":"usr_x","status":"ACTIVE"}'


class _Timestamped(BaseModel):
    """A stand-in for the timestamp field every persisted contract already carries."""

    at: datetime


def test_a_model_with_an_offset_datetime_normalises_to_utc_like_a_plain_dict() -> None:
    """The datetime rule is a property of the value, not of how it reached the hasher.

    A model is the *headline* input type here — ADR-057's header hashes ``self`` — so if
    pydantic's own serializer got to stringify the timestamp first, the offset would survive
    and a receipt written in +02:00 would not verify against the same receipt normalized to
    UTC by the server.
    """

    at = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    assert canonical_json(_Timestamped(at=at)) == b'{"at":"2026-09-05T10:00:00Z"}'
    assert canonical_json(_Timestamped(at=at)) == canonical_json({"at": at})
    assert content_hash(_Timestamped(at=at)) == content_hash(
        _Timestamped(at=datetime(2026, 9, 5, 10, 0, 0, tzinfo=UTC))
    )


def test_a_naive_datetime_inside_a_model_is_refused_just_like_a_bare_one() -> None:
    """No contract field is annotated ``AwareDatetime``, so the hasher is the last guard."""

    naive = datetime(2026, 9, 5, 10, 0, 0)
    with pytest.raises(CanonicalizationError):
        canonical_json(_Timestamped(at=naive))
    with pytest.raises(CanonicalizationError):
        canonical_json({"at": naive})


def test_a_model_whose_timestamp_is_utc_still_hashes_as_its_persisted_json() -> None:
    """Every timestamp this repository writes is ``datetime.now(UTC)``, and for those the
    model path and the ``definition`` column's JSON agree byte for byte."""

    model = _Timestamped(at=datetime(2026, 9, 5, 10, 0, 0, tzinfo=UTC))
    assert canonical_json(model) == canonical_json(model.model_dump(mode="json"))


def test_the_two_vectors_holding_the_same_value_record_the_same_committed_digest() -> None:
    """Read from the file: the model form and the dict form were generated as one digest."""

    recorded = {vector["name"]: vector["sha256"] for vector in VECTORS}
    assert (
        recorded["model_datetime_offset_normalises_to_z"] == recorded["model_equivalent_plain_dict"]
    )


def test_a_path_serializes_as_its_string_from_a_model_and_from_a_dict() -> None:
    """``ArtifactRef.path`` is a `Path`, so refusing paths would make the ref unhashable."""

    ref = ArtifactRef(artifact_id="art_x", run_id="run_x", kind="LOG", path=Path("/w/out.txt"))
    assert canonical_json(ref) == (
        b'{"artifact_id":"art_x","kind":"LOG","path":"/w/out.txt","run_id":"run_x","sha256":null}'
    )
    assert canonical_json({"path": Path("/w/out.txt")}) == b'{"path":"/w/out.txt"}'
    # Matching `str()` rather than inventing a spelling is what keeps the model and the
    # JSON the store already writes into `definition` columns on the same digest.
    assert content_hash(ref) == content_hash(ref.model_dump(mode="json"))


def test_a_uuid_serializes_as_its_canonical_lowercase_string() -> None:
    value = UUID("0191D0A2-0000-7000-8000-0000000000FF")
    assert canonical_json({"u": value}) == b'{"u":"0191d0a2-0000-7000-8000-0000000000ff"}'
    assert content_hash({"u": value}) == content_hash({"u": str(value).lower()})


def test_an_enum_hashes_as_its_declared_value() -> None:
    assert canonical_json({"status": PrincipalStatus.ACTIVE}) == b'{"status":"ACTIVE"}'


def test_nan_is_refused_because_it_cannot_be_hashed_deterministically() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json({"r": math.nan})
    with pytest.raises(CanonicalizationError):
        content_hash({"r": float("nan")})


def test_the_infinities_are_refused() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json({"r": math.inf})
    with pytest.raises(CanonicalizationError):
        canonical_json({"r": -math.inf})


def test_a_non_finite_decimal_is_refused_too() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json({"r": Decimal("NaN")})
    with pytest.raises(CanonicalizationError):
        canonical_json({"r": Decimal("Infinity")})


def test_a_naive_datetime_is_refused_because_it_names_no_instant() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json({"at": datetime(2026, 9, 5, 10, 0, 0)})


def test_a_non_string_object_key_is_refused_rather_than_silently_coerced() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json({1: "one"})


def test_a_type_with_no_canonical_form_is_refused() -> None:
    """Only types with a *single* obvious spelling are admitted; the rest fail loudly.

    ``bytes`` has two equally reasonable spellings (base64 or hex) and picking one is a
    contract decision, not a serialization one. A ``set`` has no defined order, so any
    ordering this module chose would be its own invention. Refusing beats guessing: a
    silent guess would put a digest in a receipt that another implementation cannot
    reproduce.
    """

    with pytest.raises(CanonicalizationError):
        canonical_json({"raw": b"bytes"})
    with pytest.raises(CanonicalizationError):
        canonical_json({"unordered": {"a", "b"}})
    with pytest.raises(CanonicalizationError):
        canonical_json({"elapsed": timedelta(seconds=3)})
    with pytest.raises(CanonicalizationError):
        canonical_json({"arbitrary": object()})


def test_the_canonicalization_error_is_a_value_error() -> None:
    """Callers that only care that the payload was bad can catch the broader class."""

    assert issubclass(CanonicalizationError, ValueError)


def test_object_keys_sort_by_code_point_not_by_utf16_code_unit() -> None:
    """The documented RFC 8785 deviation, pinned by bytes rather than by a docstring.

    ``U+FF7D`` (a high-BMP character) sorts before ``U+1F600`` (an astral character) by code
    point, which is what this module does; a JCS sorter compares UTF-16 code units and would
    put the surrogate pair (``0xD83D``) first. The committed ``astral_key_sort_order`` vector
    pins the digest; this case pins the byte order so the failure names the rule.
    """

    encoded = canonical_json({"😀": 2, "ｽ": 1})
    assert encoded.startswith("{\"ｽ\":1,\"😀\":2}".encode()), encoded
    assert any(vector["name"] == "astral_key_sort_order" for vector in VECTORS)
