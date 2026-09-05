"""Read-boundary upcasting of stored v0.4 payloads (ADR-057; registry §20.5).

The claim under test is narrow and has three halves. A document written by a *newer minor
of major 1* — one that carries optional fields this binary has never heard of — is projected
onto the shape this binary does understand, and says on the record what it lost. A document
written by an unknown *major* is still refused, unchanged from M0. And the stored document
itself is never touched by either outcome: reading is not writing, and a store that rewrote
a row while answering a ``get_`` would be forging the writer's seal under the writer's id.

**Where the fixtures live, and why not with the others.**
``tests/test_v04_m0_fixtures.py`` asserts that ``tests/fixtures/contracts/v0.4/`` holds
exactly one directory per contract and exactly four files in each, so a fifth kind dropped
in there would turn that test red for a reason that has nothing to do with what it is
guarding. The newer-minor documents therefore live under their own root,
``tests/fixtures/contracts/v0.4-upcast/``.

**Why the fixtures are committed and also regenerated here.** They are committed because a
future-version document is precisely the thing this repository cannot produce from its own
models — ``extra="forbid"`` means no code here can build one — so reading a committed file
is the only way to test against a document a *peer* wrote rather than against this test's
opinion of one. They are regenerated because a hand-maintained future document rots: bump a
field in ``complete.json`` and the committed newer-minor copy silently becomes a document of
a contract that no longer exists, with a digest that still verifies.
``test_every_newer_minor_fixture_is_the_document_the_generator_produces`` is what stops
that, by re-deriving each file from its ``complete.json`` and demanding equality.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from accretion.contracts import Project
from accretion.contracts.canonical import (
    CONTRACT_SCHEMA_VERSION,
    CanonicalContract,
    content_hash,
)
from accretion.contracts.routing import CONTRACT_INVENTORY, ObjectiveContract
from accretion.contracts.upcast import (
    UPCAST_DROPPED_KEYS_LABEL,
    UPCASTERS,
    upcast,
)
from accretion.persistence.store import MemoryStore

M0_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4"
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "contracts" / "v0.4-upcast"

NEWER_MINOR_VERSION = "1.1.0"
"""The version the generated fixtures declare: a newer minor of the supported major."""

UNKNOWN_MAJOR_VERSION = "2.0.0"
"""The version the fail-closed test declares. Same spelling as the M0 fixtures use."""

UNKNOWN_KEY = "reserved_future_field"
"""The one key no model in ``CONTRACT_INVENTORY`` declares, so it is the one that drops."""

UNKNOWN_VALUE: dict[str, Any] = {
    "introduced_in": NEWER_MINOR_VERSION,
    "note": "an optional field added by a newer minor of major 1 (registry §3.2)",
}
"""An object rather than a scalar, so the projection is shown dropping a whole subtree."""

OFFSET_ZONE = timezone(timedelta(hours=2))
"""The zone the offset-timestamp peer writes in. Any non-UTC offset would do."""

OFFSET_SPELLING = "2026-03-01T11:00:00+02:00"
"""``ObjectiveContract.created_at`` once written in that zone.

The same instant as the fixtures' ``2026-03-01T09:00:00Z``, spelled the way
``model_dump(mode="json")`` spells a datetime that is not already UTC — which is the one
place the stored bytes and the sealed field set can disagree.
"""

OFFSET_TIMESTAMP_FIELDS = (
    ("created_at",),
    ("objective_contract_ref", "approved_at"),
)
"""Where the offset spelling is planted: one header field, one inside a nested contract.

The nested one is the half a top-level-only restoration would miss.
``ObjectiveContractRef`` is a contract in its own right, so ``approved_at`` sits inside the
outer contract's digest exactly as much as ``created_at`` does, and the writer's model
hashed both of them as instants rather than as strings.
"""

IDS = [model.__name__ for model in CONTRACT_INVENTORY]

TABLE = "objective_contracts"
"""The one table the store-level tests use; ``ObjectiveContract`` is its model."""


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def m0_document(model: type[CanonicalContract], kind: str) -> dict[str, Any]:
    path = M0_FIXTURE_ROOT / snake_case(model.__name__) / f"{kind}.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def fixture_path(model: type[CanonicalContract]) -> Path:
    return FIXTURE_ROOT / snake_case(model.__name__) / "newer_minor.json"


def load_newer_minor(model: type[CanonicalContract]) -> dict[str, Any]:
    path = fixture_path(model)
    assert path.exists(), (
        f"{path} is missing; regenerate it from the model's complete.json with "
        "newer_minor_document()"
    )
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def body_scoped_hash_fields(
    model: type[CanonicalContract], document: dict[str, Any]
) -> tuple[str, ...]:
    """Which ``DERIVED_HASH_FIELDS`` are digests of the *whole* body.

    Detected from the committed document rather than listed by name, because the two kinds
    of derived hash in the family behave differently when a key is added and a hand-written
    list would have to be right about which is which. ``NodeContract.immutable_hash`` is the
    digest of everything except the digests, so a newer minor's extra key moves it and a
    fixture that left it alone would be a document no writer could have produced.
    ``ExecutionConfiguration.configuration_hash`` covers six named fields only, so the extra
    key does *not* move it and recomputing it would be inventing a value. The test is the
    equality below: a field whose committed value is the body digest is the first kind.
    """

    excluded = ("content_hash", *model.DERIVED_HASH_FIELDS)
    body_digest = content_hash(document, exclude=excluded)
    return tuple(
        field for field in model.DERIVED_HASH_FIELDS if document.get(field) == body_digest
    )


def newer_minor_document(model: type[CanonicalContract]) -> dict[str, Any]:
    """The ``complete.json`` document as a peer one minor ahead would have written it.

    One added optional key, the minor bumped, and every digest recomputed in the order the
    model itself seals them — derived hashes first, then the header ``content_hash`` over a
    body that now includes them. Recomputing matters: a fixture carrying the 1.0.0 digest
    would be refused by the hash check for the right reason and would then prove nothing
    about upcasting at all.
    """

    document = m0_document(model, "complete")
    moving = body_scoped_hash_fields(model, document)

    document[UNKNOWN_KEY] = deepcopy(UNKNOWN_VALUE)
    document["schema_version"] = NEWER_MINOR_VERSION
    excluded = ("content_hash", *model.DERIVED_HASH_FIELDS)
    for field in moving:
        document[field] = content_hash(document, exclude=excluded)
    document["content_hash"] = content_hash(document)
    return document


def read_at(document: dict[str, Any], path: tuple[str, ...]) -> Any:
    target: Any = document
    for key in path:
        target = target[key]
    return target


def plant(document: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    target: Any = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def in_another_zone(spelling: str) -> str:
    """The same instant, written with a ``+02:00`` offset instead of a ``Z``."""

    return datetime.fromisoformat(spelling).astimezone(OFFSET_ZONE).isoformat()


def newer_minor_with_offset_timestamps() -> dict[str, Any]:
    """The newer-minor ``ObjectiveContract`` as a peer in a non-UTC zone would have stored it.

    Only the *spelling* of the two timestamps changes; the instants, and therefore the
    writer's seal, do not. That is the whole claim, and it is asserted here rather than
    assumed: ADR-056 seals a contract's field set, ``canonical`` normalizes a datetime to
    UTC before hashing it, so ``content_hash`` over the body with those fields as real
    `datetime` objects — which is exactly what ``content_hash(model)`` hashes at write time —
    is the digest the all-UTC fixture already carries. What differs is the row: pydantic's
    JSON mode leaves the ``+02:00`` offset alone, so the stored strings are not the strings
    the writer hashed.

    Nothing here calls into ``accretion.contracts.upcast``. A generator that reused the
    reader's own timestamp restoration would agree with it by construction and would go on
    passing if that restoration were deleted.
    """

    document = newer_minor_document(ObjectiveContract)
    assert not ObjectiveContract.DERIVED_HASH_FIELDS, (
        "ObjectiveContract grew a derived hash; this generator would have to reseal it too"
    )

    body: dict[str, Any] = deepcopy(document)
    for path in OFFSET_TIMESTAMP_FIELDS:
        spelling = read_at(document, path)
        plant(body, path, datetime.fromisoformat(spelling))
        plant(document, path, in_another_zone(spelling))

    assert content_hash(body) == document["content_hash"], (
        "re-spelling an instant moved the writer's seal, so this is no longer an emulation "
        "of a writer and the test below would prove nothing"
    )
    return document


def serialize(document: dict[str, Any]) -> str:
    """The committed spelling: sorted keys, two-space indent, one trailing newline."""

    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def canonical_bytes(document: dict[str, Any]) -> str:
    """A stable rendering of a stored payload, for byte-identity comparisons."""

    return json.dumps(document, sort_keys=True, ensure_ascii=False)


# --------------------------------------------------------------------------------------
# the fixture tree
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_every_contract_has_a_newer_minor_fixture(model: type[CanonicalContract]) -> None:
    """A contract added to the family without one is a red case, not a silent gap."""

    assert fixture_path(model).exists()


def test_the_newer_minor_fixtures_live_outside_the_m0_fixture_root() -> None:
    """M0's tree test enumerates its own root and would fail on a fifth kind dropped in it."""

    assert FIXTURE_ROOT != M0_FIXTURE_ROOT
    assert not FIXTURE_ROOT.is_relative_to(M0_FIXTURE_ROOT)
    assert sorted(path.name for path in FIXTURE_ROOT.iterdir() if path.is_dir()) == sorted(
        snake_case(name) for name in IDS
    )


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_every_newer_minor_fixture_is_the_document_the_generator_produces(
    model: type[CanonicalContract],
) -> None:
    """The committed file and its ``complete.json`` cannot drift apart unnoticed."""

    assert load_newer_minor(model) == newer_minor_document(model)


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_every_newer_minor_fixture_is_sealed_with_the_digest_it_records(
    model: type[CanonicalContract],
) -> None:
    """Otherwise the loading tests below would be passing for the wrong reason.

    A fixture whose digest did not match its body would be refused by the hash check, and
    the refusal would look exactly like the tamper test's refusal — so the upcast tests
    would be green while proving nothing had ever been upcast.
    """

    document = load_newer_minor(model)
    assert document["content_hash"] == content_hash(document)
    assert document["schema_version"] == NEWER_MINOR_VERSION
    assert UNKNOWN_KEY not in model.model_fields


# --------------------------------------------------------------------------------------
# the projection
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_a_newer_minor_payload_with_an_unknown_key_loads_and_records_the_drop(
    model: type[CanonicalContract],
) -> None:
    """The projection is the complete document plus one label, and nothing else.

    Asserted as a whole-document equality rather than as a handful of spot checks, because
    the failure this guards against is a projection that quietly loses a field it *did*
    understand — which no spot check would catch unless it happened to name that field.
    """

    document = load_newer_minor(model)
    record = upcast(document, model)

    assert record.labels[UPCAST_DROPPED_KEYS_LABEL] == UNKNOWN_KEY
    assert record.schema_version == CONTRACT_SCHEMA_VERSION

    expected = m0_document(model, "complete")
    expected["labels"] = {**expected["labels"], UPCAST_DROPPED_KEYS_LABEL: UNKNOWN_KEY}
    produced = record.model_dump(mode="json")
    for field in ("content_hash", *model.DERIVED_HASH_FIELDS):
        expected.pop(field, None)
        produced.pop(field, None)
    assert produced == expected

    # The reader's seal, over the reader's projection — not the writer's digest carried
    # across onto a body it never committed to.
    assert record.content_hash == content_hash(record)
    assert record.content_hash != document["content_hash"]

    # Reading is not writing: the caller's dict is the stored row in ``MemoryStore``.
    assert document == load_newer_minor(model)


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_an_unknown_major_still_fails_closed(model: type[CanonicalContract]) -> None:
    """A major-2 document, sealed as its own writer would have sealed it, is refused.

    The seal is the point of the test. A major-2 document carrying a stale major-1 digest
    would be refused by the hash check and would say nothing about majors at all; this one
    is internally consistent, so the *only* thing standing between it and a projection that
    would rewrite its ``schema_version`` to ``1.0.0`` is the major comparison.
    """

    document = load_newer_minor(model)
    document["schema_version"] = UNKNOWN_MAJOR_VERSION
    document["content_hash"] = content_hash(document)

    with pytest.raises(ValidationError, match="unknown major"):
        upcast(document, model)


def test_an_older_minor_payload_loads_with_defaults() -> None:
    """The other direction needs no projection: absent optional fields take their defaults.

    "Older" is expressed by content and not by a version string, because ``1.0.0`` is the
    first minor of the family and there is no earlier one to write. What a document from
    before an optional field existed looks like to this reader is exactly this: a body that
    omits the field, sealed by its writer, loading with the default in place and the seal
    still verifying — no upcast, no re-seal, no label.

    The seal is the writer's and is computed *by the model*, which is the same way the
    committed ``minimal.json`` fixtures are sealed and not the same as the digest of the
    body's bytes: ADR-056 hashes a contract's whole field set, so an omitted field with a
    default is hashed as that default rather than as an absence. That distinction is what
    makes this direction free — the reader fills the same default the writer's own digest
    already committed to — and it is also its limit, recorded in the module docstring of
    ``accretion.contracts.upcast``.

    The fields are taken as the difference between the ``complete`` and ``minimal`` fixtures
    rather than listed here, so a field that becomes required later removes itself from the
    set instead of failing.
    """

    complete = m0_document(ObjectiveContract, "complete")
    minimal = m0_document(ObjectiveContract, "minimal")
    optional = sorted(set(complete) - set(minimal))
    assert optional, "the complete fixture must populate fields the minimal one omits"

    document = {
        key: value
        for key, value in complete.items()
        if key not in optional and key != "content_hash"
    }
    document["content_hash"] = ObjectiveContract.model_validate(document).content_hash

    record = upcast(document, ObjectiveContract)

    assert UPCAST_DROPPED_KEYS_LABEL not in record.labels
    assert record.content_hash == document["content_hash"]
    for field in optional:
        default = ObjectiveContract.model_fields[field].get_default(call_default_factory=True)
        assert getattr(record, field) == default


def test_an_unknown_key_at_the_current_version_is_still_refused() -> None:
    """Both halves of the trigger, or the projection becomes a way to launder any document.

    A key the model does not declare is only *explained* by a version the reader is behind.
    At ``1.0.0`` there is no such explanation, so ``extra="forbid"`` stands — which is also
    what keeps the M0 ``invalid`` fixtures red, since each carries an ``_expect`` key at the
    current version.
    """

    document = m0_document(ObjectiveContract, "complete")
    document[UNKNOWN_KEY] = deepcopy(UNKNOWN_VALUE)
    document["content_hash"] = content_hash(document)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        upcast(document, ObjectiveContract)


@pytest.mark.parametrize("model", CONTRACT_INVENTORY, ids=IDS)
def test_content_hash_is_verified_against_the_original_payload(
    model: type[CanonicalContract],
) -> None:
    """A newer-minor document edited after sealing is refused, not projected and re-sealed.

    ``labels`` is the tampered field because every contract has one and a label is valid on
    all of them, so the *only* thing wrong with this document is that its body no longer
    matches its digest. Without the verification the projection would drop the unknown key,
    re-seal the edited body with the reader's own digest, and hand back a record that looks
    exactly as authentic as the one the writer sealed.
    """

    document = load_newer_minor(model)
    document["labels"] = {**document.get("labels", {}), "tampered": "after the seal"}

    with pytest.raises(ValueError, match="does not match the digest"):
        upcast(document, model)


def test_a_newer_minor_payload_whose_timestamp_carries_an_offset_still_upcasts() -> None:
    """A peer that writes its timestamps in its own zone is a peer, not a forger.

    The seal is over the field set (ADR-056), the row holds ``model_dump(mode="json")``, and
    pydantic's JSON mode does not convert an offset — so a digest recomputed over the stored
    *strings* disagrees with the writer's for every timestamp that is not already UTC. A
    reader that checked the bytes would refuse this document with the tamper message, which
    is the worst available answer: the document is intact and the accusation is false.

    The nested ``approved_at`` is here because the restoration has to follow the model's
    annotations into a nested contract, not merely across the header.
    """

    document = newer_minor_with_offset_timestamps()
    assert document["created_at"] == OFFSET_SPELLING
    assert document["objective_contract_ref"]["approved_at"].endswith("+02:00")

    record = upcast(document, ObjectiveContract)

    # The instants the spellings name, taken from the untouched UTC fixture so that the
    # expectation is the document's own value and not this test's arithmetic.
    complete = m0_document(ObjectiveContract, "complete")
    assert record.created_at == datetime.fromisoformat(complete["created_at"])
    assert record.created_at == datetime.fromisoformat(OFFSET_SPELLING)
    assert record.objective_contract_ref is not None
    assert record.objective_contract_ref.approved_at == datetime.fromisoformat(
        complete["objective_contract_ref"]["approved_at"]
    )

    # The projection is the same projection either way: the reader's seal over the record it
    # built from the +02:00 document equals its seal over the document spelled in UTC.
    assert record.labels[UPCAST_DROPPED_KEYS_LABEL] == UNKNOWN_KEY
    assert record.content_hash == content_hash(record)
    assert (
        record.content_hash
        == upcast(newer_minor_document(ObjectiveContract), ObjectiveContract).content_hash
    )


def test_a_timestamp_edited_to_a_different_instant_is_still_refused() -> None:
    """Restoring a spelling must never restore a *value*.

    ``+01:00`` rather than ``+02:00`` is the sharpest form of the mutation the fix could
    have introduced: the string still parses, still carries an offset and still looks like
    the document the writer sealed, and it names an instant an hour later. If the check ever
    compared instants loosely — or stopped comparing at all once a field was recognised as a
    timestamp — this document would be projected and re-sealed as authentic.
    """

    document = newer_minor_with_offset_timestamps()
    document["created_at"] = OFFSET_SPELLING.replace("+02:00", "+01:00")

    with pytest.raises(ValueError, match="does not match the digest"):
        upcast(document, ObjectiveContract)


def test_a_registered_upcaster_can_move_a_new_key_into_a_field_the_model_knows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seam ``UPCASTERS`` exists for, exercised rather than merely declared.

    Dropping a key is the default because an added optional field carries no meaning the
    current model is missing. A minor that *does* need translating registers a function
    here, and this proves the registry is consulted on the verified payload before anything
    is dropped — so a rescued key never reaches the drop list.
    """

    def rescue(payload: dict[str, Any]) -> dict[str, Any]:
        moved = dict(payload)
        note = moved.pop(UNKNOWN_KEY)["note"]
        moved["labels"] = {**moved["labels"], "rescued": note}
        return moved

    monkeypatch.setitem(UPCASTERS, (ObjectiveContract.CONTRACT_TYPE, 1), rescue)

    record = upcast(load_newer_minor(ObjectiveContract), ObjectiveContract)

    assert record.labels["rescued"] == UNKNOWN_VALUE["note"]
    assert record.labels[UPCAST_DROPPED_KEYS_LABEL] == ""


# --------------------------------------------------------------------------------------
# through the store
# --------------------------------------------------------------------------------------


async def setup_store() -> tuple[MemoryStore, ObjectiveContract]:
    """A fresh store holding the golden ``ObjectiveContract``, written the ordinary way.

    Seeding the project is part of setting up a v0.4 write — every one of the fifteen tables
    has ``project_id -> projects.id`` — so a store without it would refuse the put and the
    test would be measuring the foreign key.
    """

    store = MemoryStore()
    record = ObjectiveContract.model_validate(m0_document(ObjectiveContract, "complete"))
    assert record.project_id is not None
    await store.create_project(
        Project(
            project_id=record.project_id,
            name="v0.4 M8 upcast",
            repository_path=Path("/tmp/accretion-v04-m8"),
        )
    )
    await store.put_objective_contract(record)
    return store, record


async def test_the_stored_payload_is_never_rewritten() -> None:
    """Neither an ordinary read nor an upcasting one may touch the row it read.

    The second half is the one that matters. An upcast produces a *different*, lossy body
    with the reader's own digest on it, so a reader that wrote that back — or that projected
    the row's dict in place, which amounts to the same thing — would replace the writer's
    sealed document with a copy of it that had silently lost a field, under the writer's id,
    in an append-only table. The row is compared against a copy freshly loaded from disk
    rather than against the object handed to the store, so aliasing cannot hide the change.
    """

    store, record = await setup_store()
    rows = store.v04_contracts[TABLE]

    before = canonical_bytes(rows[record.contract_id].payload)
    assert await store.get_objective_contract(record.contract_id) is not None
    assert canonical_bytes(rows[record.contract_id].payload) == before

    # The same row, as a peer one minor ahead would have written it.
    newer = load_newer_minor(ObjectiveContract)
    assert newer["contract_id"] == record.contract_id
    rows[record.contract_id] = rows[record.contract_id]._replace(payload=newer)

    loaded = await store.get_objective_contract(record.contract_id)
    assert loaded is not None
    assert loaded.labels[UPCAST_DROPPED_KEYS_LABEL] == UNKNOWN_KEY

    stored = rows[record.contract_id].payload
    assert canonical_bytes(stored) == canonical_bytes(load_newer_minor(ObjectiveContract))
    assert UNKNOWN_KEY in stored
    assert stored["schema_version"] == NEWER_MINOR_VERSION


async def test_a_listing_upcasts_the_same_way_a_single_read_does() -> None:
    """``list_`` goes through the same loader, so the read boundary is one place and not two."""

    store, record = await setup_store()
    rows = store.v04_contracts[TABLE]
    rows[record.contract_id] = rows[record.contract_id]._replace(
        payload=load_newer_minor(ObjectiveContract)
    )

    listed = await store.list_objective_contracts(workspace_id=record.workspace_id)

    assert [item.contract_id for item in listed] == [record.contract_id]
    assert listed[0].labels[UPCAST_DROPPED_KEYS_LABEL] == UNKNOWN_KEY
