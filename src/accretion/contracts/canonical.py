"""Canonical JSON serialization and content hashing (ADR-056; registry §3.1, §20.2).

Every v0.4 contract carries a ``content_hash`` in its header, and a hash is only worth
anything if two processes that hold the same value agree on the bytes they hash. That
agreement cannot be left to `json.dumps` defaults: key order, whitespace, ASCII escaping
and number formatting are all free parameters, and each of them silently forks the digest.
This module fixes every one of them, once, so that no other module has to decide again.

The rules, in the order the normalizer applies them:

* **Objects.** Keys are sorted and must be strings (an :class:`~enum.Enum` key is taken as
  its value first). Sorting is by Unicode code point, which is Python's native ordering.
  RFC 8785 sorts by UTF-16 code unit; the two orders differ only between astral characters
  and the tail of the BMP, and registry decision 2 permits "a documented equivalent" —
  this paragraph is that documentation, and the TypeScript twin owed by the M9 Studio work
  must sort the same way rather than reusing a JS default.
* **Whitespace.** ``separators=(",", ":")`` — no space after either delimiter. The default
  ``", "``/``": "`` separators produce different bytes for identical values, so they are
  named in a module constant and covered by a mutation check.
* **Encoding.** ``ensure_ascii=False`` and a UTF-8 encode: a name written in Thai or an
  emoji in a label hashes as the characters it is, not as ``\\uXXXX`` escapes. The function
  returns ``bytes`` rather than ``str`` precisely so that the encoding is part of the
  contract instead of the caller's guess.
* **Integers stay integers.** ``1`` serializes as ``1``; a float ``1.0`` serializes as
  ``1.0``. They are *different inputs* and they hash differently. A pipeline that lets an
  integer count drift into a float is changing the value, and the hash is right to say so.
* **Floats use the shortest round-trip form**, which is exactly `repr` and exactly what
  `json.dumps` emits, so ``0.1`` is ``0.1`` and not ``0.1000000000000000055``.
* **NaN and the infinities are refused.** They are not JSON, they are not equal to
  themselves, and a hash over them would be a lie about determinism. Python's `json` would
  happily write the non-standard ``NaN`` token; we raise instead.
* **Decimals serialize as strings.** A decimal exists in a payload because someone needed
  exactness that binary floating point cannot give; turning it into a float to serialize it
  would throw away the reason it is there. ``Decimal("1.50")`` therefore hashes as
  ``"1.50"`` and is deliberately distinct from ``Decimal("1.5")`` — trailing zeros are
  significant digits, and the digest preserves what the writer wrote.
* **Datetimes normalize to RFC 3339 UTC with a literal ``Z``.** An offset-bearing timestamp
  is converted to UTC first, so ``12:00:00+02:00`` and ``10:00:00Z`` are the same instant
  and the same bytes. A *naive* datetime is refused: it names no instant, and quietly
  assuming UTC would let a local-time value hash as though it were universal.
* **Nulls are kept.** ``None`` is a value, not an absence: dropping it would make
  ``{"a": null}`` and ``{}`` collide, and those two say different things about a field.
* **Nesting is recursive.** Lists and tuples both become JSON arrays and keep their order,
  because array order is data.
* **Paths and UUIDs serialize as their strings.** ``Path("/w/out.txt")`` hashes as
  ``"/w/out.txt"`` and a :class:`~uuid.UUID` as its canonical lowercase hyphenated form —
  in both cases ``str(value)``, which is exactly what pydantic already writes into the
  ``definition`` JSON columns. Each has a single spelling, so admitting them invents no
  policy; refusing them would have left ``ArtifactRef.path`` unhashable.
* **Pydantic models are accepted** and dumped with ``model_dump(mode="python")`` — and
  emphatically *not* with ``mode="json"``. That distinction is the reason the rules above
  are worth writing down at all: pydantic's JSON mode stringifies a `datetime` by its own
  rules, which neither convert an offset to UTC nor reject a naive value, so a model would
  have slipped past the two datetime rules this module exists to enforce while a plain dict
  holding the identical value obeyed them. Python mode hands the real `datetime`, `Decimal`,
  `Path` and `UUID` objects back to the normalizer, so a value hashes the same whether it
  arrives as a model or as the equivalent dict — which is precisely the property a verifier
  needs when it recomputes a digest over a payload it *parsed* rather than built. For the
  timestamps this repository actually writes (``datetime.now(UTC)``, which pydantic also
  spells with a ``Z``) the bytes are identical to ``model_dump(mode="json")``, so a model and
  its persisted JSON still hash alike; the two diverge only on the non-UTC and naive values
  that the datetime rule is here to normalize away or refuse outright.

:func:`content_hash` adds the one rule that the header needs: a contract's own
``content_hash`` field is omitted from the input that produces it. Without that exclusion
the field would have to hold the hash of a document containing itself, which no value can
satisfy; with it, a receipt can be verified by recomputing the digest over everything
*except* the claimed digest. The exclusion is top-level only and deliberately so — a nested
reference's ``content_hash`` is part of what this contract commits to, and erasing it would
let a nested body change without changing the outer digest.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import PurePath
from uuid import UUID

from pydantic import BaseModel

# Named rather than inlined: the whitespace-free separators are the single most
# easily-broken canonicalization rule (the `json` defaults insert spaces), and a
# mutation check flips this constant to prove the golden vectors actually depend on it.
_SEPARATORS = (",", ":")

DEFAULT_HASH_EXCLUSIONS: tuple[str, ...] = ("content_hash",)
"""Top-level keys omitted from a contract's own hash input — see :func:`content_hash`."""


class CanonicalizationError(ValueError):
    """A value cannot be canonicalized, so it cannot be hashed.

    Subclasses :class:`ValueError` rather than :class:`TypeError` even for an
    unsupported type, because every rejection here is the same statement about the
    *value*: it has no single well-defined byte representation. Callers that want to
    distinguish "unhashable payload" from any other bad argument catch this class.
    """


def _normalize(value: object) -> object:
    """Project ``value`` onto the JSON primitives the canonical form is defined over.

    Everything ambiguous is resolved here rather than in `json.dumps`, so the dump step
    is a pure formatter with nothing left to decide.
    """

    # Pydantic first, and in *python* mode. `mode="json"` would let pydantic stringify
    # datetimes and decimals with its own rules before this function ever saw them, which
    # would silently exempt every model field from the datetime rules below — an offset
    # would survive un-normalized and a naive value would be accepted. Python mode returns
    # the live `datetime`/`Decimal`/`Path`/`UUID` objects, so a model and the equivalent
    # plain dict take exactly the same path and produce exactly the same bytes.
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="python"))

    # Enum before str: a `StrEnum` member is already a `str`, but a plain `Enum` is not,
    # and both must reduce to the declared value rather than to `repr` or member name.
    if isinstance(value, Enum):
        return _normalize(value.value)

    if value is None:
        return None

    # bool before int — `bool` is a subclass of `int`, and `True` must stay `true`.
    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise CanonicalizationError(
                f"non-finite float {value!r} has no canonical JSON form; "
                "NaN and the infinities cannot be hashed deterministically"
            )
        return value

    if isinstance(value, Decimal):
        # `is_finite()` covers NaN, sNaN and both infinities in one predicate.
        if not value.is_finite():
            raise CanonicalizationError(
                f"non-finite Decimal {value!r} has no canonical JSON form; "
                "NaN and the infinities cannot be hashed deterministically"
            )
        # `str` and not `float`: the exact digits, including significant trailing zeros,
        # are the reason the caller chose Decimal in the first place.
        return str(value)

    if isinstance(value, datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise CanonicalizationError(
                "naive datetime has no canonical form; timestamps must carry an offset "
                "so that they name an instant (registry §3.1)"
            )
        # Normalize the offset away, then spell UTC as `Z` rather than `+00:00`, so that
        # the same instant written in any zone produces the same bytes.
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    # `PurePath` covers every flavour, including the `Path` on `ArtifactRef.path`. `str()`
    # and not `as_posix()`: `str()` is what pydantic writes into the `definition` columns,
    # and matching it is what keeps a model and its persisted JSON hashing alike.
    if isinstance(value, PurePath):
        return str(value)

    # A UUID has one canonical spelling — lowercase, hyphenated — and `str()` produces it.
    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, str):
        return value

    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            # An Enum key reduces to its value; anything else must already be a string,
            # because `json` would otherwise coerce keys silently and two distinct keys
            # (`1` and `"1"`) could collapse into one.
            canonical_key = key.value if isinstance(key, Enum) else key
            if not isinstance(canonical_key, str):
                raise CanonicalizationError(
                    f"object key {key!r} is {type(key).__name__}, not str; "
                    "canonical JSON objects are keyed by strings only"
                )
            if canonical_key in normalized:
                raise CanonicalizationError(
                    f"object key {canonical_key!r} appears twice after normalization; "
                    "the canonical form would silently drop one of the values"
                )
            normalized[canonical_key] = _normalize(item)
        return normalized

    # `str` and `bytes` are Sequences too, hence the explicit exclusion; `str` is already
    # handled above and `bytes` has no canonical JSON spelling this module is willing to
    # invent (base64 vs hex is a contract decision, not a serialization one).
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_normalize(item) for item in value]

    raise CanonicalizationError(
        f"{type(value).__name__} has no canonical JSON form; convert it in the contract "
        "(or dump the pydantic model) before hashing"
    )


def _dump(normalized: object) -> bytes:
    """Format an already-normalized value. The only place the JSON knobs are set."""

    text = json.dumps(
        normalized,
        sort_keys=True,
        separators=_SEPARATORS,
        ensure_ascii=False,
        # Belt and braces: normalization already refused non-finite floats, so this can
        # only fire if that pass is ever weakened, and it fires loudly instead of writing
        # the non-standard `NaN` token into a hash input.
        allow_nan=False,
    )
    return text.encode("utf-8")


def canonical_json(value: object) -> bytes:
    """Return the canonical UTF-8 JSON bytes for ``value`` (ADR-056).

    Deterministic for equal inputs and sensitive to every difference that the contract
    treats as meaningful — key *order* is not such a difference, but an integer that
    became a float is.
    """

    return _dump(_normalize(value))


def content_hash(
    value: object,
    *,
    exclude: Sequence[str] = DEFAULT_HASH_EXCLUSIONS,
) -> str:
    """Return the SHA-256 hex digest of ``value`` in canonical form.

    ``exclude`` names **top-level** keys dropped before hashing. The default drops
    ``content_hash`` so that a contract can carry the digest of its own body: the field
    is not part of the input that produces it, and a verifier recomputes the digest over
    the same reduced document. Excluding a key that is absent is not an error — a payload
    that has not been hashed yet is the normal case at write time.

    Nested ``content_hash`` fields are untouched on purpose: a reference's digest is part
    of what the outer contract commits to.
    """

    normalized = _normalize(value)
    if isinstance(normalized, dict) and exclude:
        excluded = set(exclude)
        normalized = {key: item for key, item in normalized.items() if key not in excluded}
    return hashlib.sha256(_dump(normalized)).hexdigest()
