"""Read-boundary upcasting for stored v0.4 contract payloads (ADR-057; registry §20.5).

A stored v0.4 document is never mutated. That is the whole premise of the family: the
``content_hash`` in the header is the digest of the body as it was written, the store's
fourteen contract tables are append-only, and a record that could be edited in place would
make every receipt that quotes it a claim about nothing. So when a reader meets a payload
written by a *newer minor of the same major* — a peer that added optional fields this
binary has never heard of — it has exactly two honest options: refuse the document, or
project it onto the shape it does understand and say what it lost. Registry §20.5 chooses
the projection, and this module is it.

**What "newer minor" means and why the trigger is narrow.** Registry §3.2 classifies an
added optional field as Minor and a removed or renamed one as Major. So a document that
declares a newer ``(minor, patch)`` under major
:data:`~accretion.contracts.canonical.SUPPORTED_SCHEMA_MAJOR` and carries keys the model
does not declare is, by that classification, a document whose unknown keys are *additive
and optional* — which is exactly the case where dropping them preserves meaning. Both
halves of that trigger are load-bearing:

* Unknown keys at the **current** version are not a newer minor, they are a corrupt or
  foreign document, and they still fail closed on ``extra="forbid"``. The M0 ``invalid``
  fixtures carry an ``_expect`` key at ``1.0.0`` for exactly this reason, and they must
  stay red.
* An unknown **major** fails closed before anything else happens, by delegating to
  :meth:`~pydantic.BaseModel.model_validate` so that the refusal, and its message, are the
  ones :class:`~accretion.contracts.canonical.CanonicalContract` has always given. That
  delegation is not a formality: the projection below *rewrites* ``schema_version`` to the
  current one, so a major-2 document that reached it would be laundered into a valid-looking
  v1 record. The major comparison is what stands between those two facts.

**The hash is checked against the document as it was stored, before anything is dropped.**
This is the one rule that makes the projection safe rather than dangerous. The projected
body is a *different* body — fewer keys, a rewritten ``schema_version``, an added label —
so it cannot carry the writer's digest, and it is re-sealed by the model with the reader's.
A re-seal is a machine for turning a forgery into a valid record, and the only thing that
stops it here is that the writer's digest is verified over the writer's body first: a
document that has been edited since it was sealed is refused at that step and never reaches
the projection. Skip the verification and a tampered payload becomes a perfectly sealed
record of whatever it now says.

**"The document as it was stored" is the values it holds, not the bytes it is spelled in.**
ADR-056 defines the seal over a contract's *field set*: a writer computes
``content_hash(model)``, which reaches
:func:`~accretion.contracts.canonical.canonical_json` through
``model_dump(mode="python")`` and normalizes every timestamp to UTC with a literal ``Z``.
What a row actually holds is ``model_dump(mode="json")``
(:func:`~accretion.persistence.store._v04_payload`), and pydantic's JSON mode does *not*
convert an offset. The two coincide for every timestamp this repository writes — they are
all ``datetime.now(UTC)`` — and diverge the moment a peer writes one that is not: a writer
whose ``created_at`` is ``2026-03-01T11:00:00+02:00`` seals over ``09:00:00Z`` and stores
``11:00:00+02:00``, so a digest recomputed over the stored *string* would disagree with the
writer's and this reader would accuse a well-formed peer of forgery. So the timestamps are
parsed back into the instants they name before the digest is recomputed, by
:func:`_sealed_body`, which walks the model's own field annotations — including into nested
contracts and collections — rather than guessing from the shape of a string.

That restoration changes spelling and never content. ``datetime.fromisoformat`` inverts
exactly the RFC 3339 form both dumps emit; a string that does not parse, or that parses to a
naive value naming no instant, is left as the string it is, so a corrupt document stays on
the mismatch path instead of turning into a canonicalization error; and every other rule
where the two dumps could have differed — decimals as strings, paths, UUIDs, enum values —
already agrees between them. Its limit is a timestamp stored under a key this reader does
*not* declare: there is no annotation to read, the stored spelling is all there is, and a
future minor that adds an offset-bearing timestamp field will be refused here rather than
upcast. That is the direction to fail in — a refusal, never an acceptance — and the fix
belongs to the minor that first adds such a field, where a document can prove it.

The consequence is worth stating plainly, because it constrains what callers may do with
the result: **an upcast contract's digests are the reader's, not the writer's.** Its
``content_hash`` — and any ``DERIVED_HASH_FIELDS`` digest on
:class:`~accretion.contracts.canonical.CanonicalContract`, such as
``NodeContract.immutable_hash`` — commits to the projection, so two readers
at different versions will compute different values for the same stored document. An upcast
record is therefore safe to *read*, to route on and to explain with, and must never be
written back: doing so would replace the writer's sealed body with a lossy copy of it under
the same id. Nothing in this repository writes one back — the stores hand
:func:`upcast` the row's payload and keep the row exactly as it found it — and a store test
pins that.

**What is dropped is recorded, in the record itself.** Every v0.4 contract inherits
``labels`` from the registry §3 header, so the dropped key names go in
``labels[``:data:`UPCAST_DROPPED_KEYS_LABEL`\\ ``]`` on every contract without exception
rather than in a log line for some and a label for others. A label travels with the object
into whatever read it — an explanation, an API response, a debugging session six months
later — while a log line is separated from its subject by the first process boundary it
crosses. The label states what *this* projection dropped and replaces any value already
present, because a merged value would be a claim about a projection nobody performed.

**The other direction is free today and will not stay free.** A document that omits an
optional field loads with the default and its seal still verifies, because ADR-056 hashes a
contract's whole *field set* rather than the bytes it was stored as: an absent field with a
default is hashed as that default, which is exactly what the committed ``minimal.json``
fixtures show. That equivalence holds only while writer and reader declare the same fields.
The day a second minor exists, a document written at ``1.0.0`` and read by a binary at
``1.1.0`` will be sealed over the writer's field set and rehashed over the reader's larger
one, and it will be refused by its own digest — so that direction needs the same
verify-then-re-seal treatment as this one. It is deliberately not implemented here: at one
minor the branch is unreachable, no fixture could exercise it, and a payload missing a
declared field at the *current* version is a truncated document that must keep failing
closed. The fix belongs to the minor that first makes the case real, where a document from
the earlier minor can prove it.

**Scope.** The projection is top level. An unknown key nested inside a value object still
fails closed on that object's ``extra="forbid"``, which is the conservative direction:
this module knows that the *header* is versioned by ``schema_version`` and has no such
statement to make about the interior of a nested contract. :data:`UPCASTERS` is the seam
where that, and any field-level translation a future minor needs, is added without
reopening this function.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from types import UnionType
from typing import Annotated, Any, Union, get_args, get_origin

from pydantic import BaseModel

from accretion.contracts.canonical import (
    CONTRACT_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_MAJOR,
    CanonicalContract,
    content_hash,
)

UPCAST_DROPPED_KEYS_LABEL = "upcast_dropped_keys"
"""The header label an upcast record carries, naming the keys the projection dropped."""

DROPPED_KEY_SEPARATOR = ","
"""Separator for the label's value. Contract field names never contain a comma."""

_VERSION_PARTS = CONTRACT_SCHEMA_VERSION.split(".")
CURRENT_VERSION: tuple[int, int, int] = (
    int(_VERSION_PARTS[0]),
    int(_VERSION_PARTS[1]),
    int(_VERSION_PARTS[2]),
)
"""``CONTRACT_SCHEMA_VERSION`` as an ordered triple, so that "newer" is a comparison."""

type ContractUpcaster = Callable[[dict[str, Any]], dict[str, Any]]
"""A field-level translation: a verified payload in, a payload in current terms out."""

UPCASTERS: dict[tuple[str, int], ContractUpcaster] = {}
"""Explicit per-contract translations, keyed by ``(contract_type, major)``.

Empty at M8, and deliberately so: there is one major and one minor, so there is nothing yet
to translate and a speculative entry would be an untested guess about a field that does not
exist. The registry is here rather than added later because the *shape* of the decision is
what needs freezing — a future minor that gives an existing field a new meaning, or splits
one field into two, cannot be handled by dropping keys, and the alternative to this seam is
a chain of version conditionals growing inside :func:`upcast`.

An entry runs on the **verified** payload, before any unknown key is dropped, so it can move
a new key's content into a field the current model declares instead of losing it; whatever
it leaves unknown is then dropped and recorded as usual. It is keyed by the model's own
``CONTRACT_TYPE`` and not by the payload's ``contract_type`` field, because a payload that
disagrees with the model about what it is has already lost the argument.
"""


def _version_triple(value: object) -> tuple[int, int, int] | None:
    """Parse a semver ``schema_version``, or ``None`` if it is not one.

    Returning ``None`` rather than raising keeps the malformed case on the same path as the
    unknown major: hand the payload to the model, whose ``schema_version`` pattern rejects
    it with the error the reader has always produced. Deciding here would mean inventing a
    second, differently-worded refusal for a document that is refused either way.
    """

    if not isinstance(value, str):
        return None
    parts = value.split(".")
    if len(parts) != 3 or not all(part.isascii() and part.isdigit() for part in parts):
        return None
    return (int(parts[0]), int(parts[1]), int(parts[2]))


_UNION_ORIGINS = (UnionType, Union)
"""Both spellings of a union origin. ``X | None`` and ``Optional[X]`` differ at runtime."""


def _annotation_members(annotation: object) -> tuple[object, ...]:
    """Peel ``Annotated`` and union wrappers off ``annotation``, leaving the types inside.

    A field is matched against these members by the *value* it holds, so an
    ``ObjectiveContractRef | None`` reached with a mapping resolves to the model and the
    same field reached with ``None`` matches nothing and is left alone.
    """

    origin = get_origin(annotation)
    if origin is Annotated:
        return _annotation_members(get_args(annotation)[0])
    if origin in _UNION_ORIGINS:
        return tuple(
            member for arg in get_args(annotation) for member in _annotation_members(arg)
        )
    return (annotation,)


def _as_sealed(value: Any, annotation: object) -> Any:
    """Return ``value`` as the writer's model held it when it computed the seal.

    Only timestamps can differ between the two dumps (see the module docstring), so this is
    a `datetime` restoration and nothing else: it is driven by the declared type, and a
    value whose annotation says nothing about `datetime` is returned untouched, whatever it
    looks like. Recursion follows the annotations through nested contracts, mappings and
    sequences, because ``objective_contract_ref.approved_at`` is exactly as much a part of
    the outer contract's digest as ``created_at`` is.
    """

    members = _annotation_members(annotation)

    if isinstance(value, str):
        if not any(
            isinstance(member, type) and issubclass(member, datetime) for member in members
        ):
            return value
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            # Not a timestamp at all. The model will refuse it; leaving it as the string it
            # is keeps that refusal in the model's voice instead of raising here.
            return value
        if parsed.utcoffset() is None:
            # Naive: it names no instant, `canonical._normalize` refuses one outright, and
            # no writer's model could have sealed it — its own seal would have raised. Left
            # as a string so the document fails on the digest rather than on canonicalization.
            return value
        return parsed

    if isinstance(value, Mapping):
        for member in members:
            if isinstance(member, type) and issubclass(member, BaseModel):
                fields = member.model_fields
                return {
                    key: _as_sealed(item, fields[key].annotation) if key in fields else item
                    for key, item in value.items()
                }
            origin, args = get_origin(member), get_args(member)
            if isinstance(origin, type) and issubclass(origin, Mapping) and len(args) == 2:
                return {key: _as_sealed(item, args[1]) for key, item in value.items()}
        return value

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for member in members:
            origin, args = get_origin(member), get_args(member)
            if isinstance(origin, type) and issubclass(origin, Sequence) and args:
                return [_as_sealed(item, args[0]) for item in value]
        return value

    return value


def _sealed_body(payload: Mapping[str, Any], model: type[CanonicalContract]) -> dict[str, Any]:
    """The stored payload with its timestamps back in the form the writer hashed.

    A key ``model`` does not declare is copied through verbatim: this reader has no type for
    it and will not invent one, so a newer minor's own timestamp field hashes as the string
    the row spells it with (module docstring, last paragraph). ``payload`` is not modified —
    the caller's dict is the stored row.
    """

    fields = model.model_fields
    return {
        key: _as_sealed(value, fields[key].annotation) if key in fields else value
        for key, value in payload.items()
    }


def upcast[C: CanonicalContract](payload: dict[str, Any], model: type[C]) -> C:
    """Build ``model`` from a stored ``payload``, projecting a newer minor onto it.

    Returns the parsed contract. Raises :class:`ValueError` (including pydantic's
    :class:`~pydantic.ValidationError`) for every payload the reader must not accept: an
    unknown major, an unknown key at a version that does not explain it, a body that no
    longer matches the digest it carries, and anything the model itself refuses.

    ``payload`` is not modified. The caller's dict is the stored document — in
    ``MemoryStore`` it is literally the row — so mutating it would rewrite history as a
    side effect of reading it.
    """

    known = set(model.model_fields)
    unknown = sorted(key for key in payload if key not in known)
    version = _version_triple(payload.get("schema_version", CONTRACT_SCHEMA_VERSION))

    # Fail closed on an unknown (or unparseable) major *first*, and in the model's own
    # voice. This branch is what keeps the projection below from laundering a document
    # written by a major this reader cannot read into one that merely looks like a v1.
    if version is None or version[0] != SUPPORTED_SCHEMA_MAJOR:
        return model.model_validate(dict(payload))

    # Nothing to project: either the document names no field this model lacks, or it is not
    # a newer revision and its unknown keys are not explained by a version this reader is
    # behind. Both go to the model unaltered, which verifies the writer's digest against
    # the writer's body — the strongest check available, and the one worth keeping.
    if not unknown or version <= CURRENT_VERSION:
        return model.model_validate(dict(payload))

    declared = payload.get("content_hash")
    if not isinstance(declared, str) or not declared:
        raise ValueError(
            f"{model.__name__} payload declares schema_version "
            f"{payload.get('schema_version')!r} and carries no content_hash; an unsealed "
            "document cannot be upcast, because the projection would seal a body no writer "
            "ever committed to"
        )
    computed = content_hash(_sealed_body(payload, model))
    if declared != computed:
        raise ValueError(
            f"content_hash {declared!r} does not match the digest of this stored "
            f"{model.__name__} payload ({computed!r}); the document was edited after it was "
            "sealed and is refused rather than upcast (registry §20.5, ADR-057)"
        )

    projected = dict(payload)
    upcaster = UPCASTERS.get((model.CONTRACT_TYPE, version[0]))
    if upcaster is not None:
        projected = dict(upcaster(projected))
        unknown = sorted(key for key in projected if key not in known)

    for key in unknown:
        del projected[key]
    # The projection *is* a current-version document: it holds current-version fields only,
    # and claiming the writer's minor would advertise fields it no longer carries.
    projected["schema_version"] = CONTRACT_SCHEMA_VERSION
    # Every digest in the document commits to the body that was just reduced, so each is
    # dropped and re-sealed by the model over what the projection actually says.
    projected.pop("content_hash", None)
    for field in model.DERIVED_HASH_FIELDS:
        projected.pop(field, None)

    existing = projected.get("labels", {})
    if isinstance(existing, dict):
        projected["labels"] = {
            **existing,
            UPCAST_DROPPED_KEYS_LABEL: DROPPED_KEY_SEPARATOR.join(unknown),
        }

    return model.model_validate(projected)
