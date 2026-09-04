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

:class:`CanonicalContract` sits at the bottom of the module because it is the first
consumer of everything above it: the registry §3 header that every v0.4 contract inherits,
carrying the ``content_hash`` those rules produce and the semver ``schema_version`` whose
unknown majors are refused. It lives here rather than in ``routing.py`` because the header
and the hashing rule are one decision — ADR-056 and ADR-057 are the same freeze seen from
two sides — and because a later contract family (v0.5 robotics, v0.6 approvals) will
inherit the header without inheriting anything routing-shaped.
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
from typing import TYPE_CHECKING, ClassVar, Self
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from accretion.contracts import PrincipalRef, StrictModel
from accretion.ids import has_prefix

if TYPE_CHECKING:
    # A type reference only, and deliberately so. `ObjectiveContractRef` is one of the
    # nineteen v0.4 contracts and lives in `routing.py`, which imports this module at
    # runtime. Typing the optional header field with it here and resolving the forward
    # reference from `routing.py` — a `model_rebuild` call there, immediately after the
    # class exists — keeps the objective reference in the single module that owns the
    # family, instead of splitting one schema across two modules to dodge a cycle.
    from accretion.contracts.routing import ObjectiveContractRef

# Named rather than inlined: the whitespace-free separators are the single most
# easily-broken canonicalization rule (the `json` defaults insert spaces), and a
# mutation check flips this constant to prove the golden vectors actually depend on it.
_SEPARATORS = (",", ":")

DEFAULT_HASH_EXCLUSIONS: tuple[str, ...] = ("content_hash",)
"""Top-level keys omitted from a contract's own hash input — see :func:`content_hash`."""

CONTRACT_SCHEMA_VERSION = "1.0.0"
"""The semver ``schema_version`` every v0.4 contract is frozen at in M0 (registry §3)."""

SUPPORTED_SCHEMA_MAJOR = 1
"""The only major this reader accepts; an unknown major fails closed (registry §3.2)."""


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


class CanonicalContract(StrictModel):
    """The registry §3 canonical header, inherited by every v0.4 contract (ADR-057).

    Registry §3 states that *every* persisted contract introduced at or after v0.4 MUST
    embed or inherit ``contract_type``, ``schema_version``, ``contract_id``,
    ``content_hash``, ``created_at``, ``created_by``, ``workspace_id`` and ``project_id``,
    plus the optional ``supersedes_contract_id``, ``objective_contract_ref``, ``labels``
    and ``retention_class``. Writing that header once, here, is the difference between a
    rule and a habit: a contract that forgot a header field would still typecheck if each
    module restated the header for itself, and the registry §21 "one source of truth" rule
    would be violated on the very first copy.

    Four class-level knobs let a subclass state what it is without restating the header:

    * ``CONTRACT_TYPE`` — the canonical type string. A subclass sets it *and* redeclares
      ``contract_type`` with the same string as its default; the validator below refuses
      any instance whose ``contract_type`` disagrees with its own class, so a payload
      cannot be re-labelled by editing one field. The base leaves it empty, which makes
      :class:`CanonicalContract` itself uninstantiable — it is a header, not a record.
    * ``ID_KIND`` — the :mod:`accretion.ids` prefix registry key whose prefix
      ``contract_id`` must carry (ADR-055). ``None`` means the record has no id space of
      its own: it is minted and owned by the contract that embeds it, and ADR-055 lists no
      prefix for it. That is deliberate rather than an oversight, and the four models it
      applies to say so in their own docstrings.
    * ``PROJECT_SCOPED`` — whether ``project_id`` must be present. Registry §3 lists
      ``project_id`` as a header field, but SDD §7.12 makes it explicitly nullable for a
      workspace-scoped router model, and a workspace-wide training snapshot or promotion
      report belongs to no single project either. Rather than let every contract quietly
      omit it, the field is optional in the *type* and required by this flag, which
      defaults to ``True``: a new contract is project-scoped unless it argues otherwise.
    * ``DERIVED_HASH_FIELDS`` — documentation for the schema export and the tests; the
      actual computation lives in :meth:`seal_derived_hashes`.

    **Sealing.** ``content_hash`` is optional on input and computed on construction. A
    payload that omits it is sealed with the digest of everything else; a payload that
    carries one is *verified* against the recomputed digest and rejected if it disagrees.
    That single rule gives the freeze both of the properties it needs: a caller can build
    a contract without knowing how to hash it, and a fixture, a persisted row or a replayed
    receipt cannot be edited by hand without the edit being detected. The digest excludes
    only ``content_hash`` itself (see :func:`content_hash`), so every other field —
    including a nested reference's own digest — is inside what the contract commits to.

    Subclasses that carry a second, differently-scoped digest override
    :meth:`seal_derived_hashes`, which runs *before* the header digest so that
    ``content_hash`` commits to the derived value rather than racing it. The hook is a
    method rather than another validator on purpose: pydantic's ordering between base and
    subclass validators is an implementation detail, and a hash that depends on it would
    be a hash that depends on nothing.

    **Versioning.** ``schema_version`` is semver, defaulting to ``1.0.0``. An unknown
    *major* is rejected at parse time by a field validator, so the rejection happens before
    any other rule can produce a more confusing error, and the reader fails closed exactly
    as registry §3.2 requires ("Remove/rename field → Major → Reject unknown major").
    Registry §20.5 read-boundary upcasting is scheduled for M8, the first milestone with a
    second writer; until then ``extra="forbid"`` stands and an unknown major is an error
    rather than a projection.

    The model is not frozen. Immutability of a v0.4 record is enforced where it can be
    enforced — by the digest, and by the append-only store methods M0's PR3 adds — not by
    a Python attribute guard that any ``model_copy`` would step around.

    **Sealing happens on construction; requiring the seal is the reader's job.** A document
    built without a ``content_hash`` (or with an empty one) is sealed here with the digest of
    the body it arrived with, which is the right thing for a record being created and the
    wrong thing for a record being read back: a persisted payload that lost its digest to a
    partial write, a hand edit or a dropped column would otherwise come back as a validly
    sealed copy of whatever the body now says. So every store that reads these records must
    refuse a payload whose ``content_hash`` is absent or empty *before* it reaches
    ``model_validate`` (M0 PR3 does this in its ``get_``/``list_`` paths, with a test), and
    a document that does carry a digest is verified against the body and refused on mismatch.
    Subclass modules also need the header's forward reference resolved: import
    ``accretion.contracts.routing`` (which calls ``model_rebuild``) before defining a subclass
    outside it, or the first validation fails with a class-build error rather than anything
    that names the cause; a test pins that coupling.
    """

    CONTRACT_TYPE: ClassVar[str] = ""
    ID_KIND: ClassVar[str | None] = None
    PROJECT_SCOPED: ClassVar[bool] = True
    DERIVED_HASH_FIELDS: ClassVar[tuple[str, ...]] = ()

    contract_type: str = Field(
        default="",
        max_length=128,
        pattern=r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$",
    )
    schema_version: str = Field(
        default=CONTRACT_SCHEMA_VERSION,
        pattern=r"^\d+\.\d+\.\d+$",
    )
    contract_id: str = Field(min_length=1, max_length=64)
    content_hash: str = Field(default="", max_length=64)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: PrincipalRef
    workspace_id: str = Field(min_length=1, max_length=64)
    project_id: str | None = Field(default=None, min_length=1, max_length=64)
    supersedes_contract_id: str | None = Field(default=None, min_length=1, max_length=64)
    objective_contract_ref: ObjectiveContractRef | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    # Constrained to an upper-case token rather than typed as an enum, for exactly the
    # reason `ApprovalArtifactRef.retention_class` gives: registry §3 calls it a
    # "canonical-enum" but registry §5 defines no retention vocabulary and §20 schedules
    # none, so freezing a guessed set here would create the duplicate source of truth
    # registry §21 forbids. The token shape at least stops `standard` and `STANDARD` from
    # forking a digest before the vocabulary exists.
    retention_class: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$"
    )

    @field_validator("schema_version")
    @classmethod
    def _reject_unknown_major(cls, value: str) -> str:
        """Fail closed on an unknown major (registry §3.2; ADR-057).

        A field validator and not a model validator, so that the version is judged before
        any body rule runs. A payload written by a future major will usually *also* fail
        several body rules, and reporting those instead would send a reader looking for a
        field bug when the real answer is "this document is from a version you do not
        understand, and guessing is not allowed".
        """

        major = int(value.split(".", 1)[0])
        if major != SUPPORTED_SCHEMA_MAJOR:
            raise ValueError(
                f"schema_version {value!r} declares major {major}; this reader understands "
                f"major {SUPPORTED_SCHEMA_MAJOR} only and rejects an unknown major rather "
                "than guessing at its meaning (registry §3.2, ADR-057)"
            )
        return value

    def seal_derived_hashes(self) -> None:
        """Compute any digest the subclass carries beside the header ``content_hash``.

        Called once, during validation, immediately before the header digest is sealed.
        The base implementation does nothing; :class:`~accretion.contracts.routing.NodeContract`
        and :class:`~accretion.contracts.routing.ExecutionConfiguration` override it.
        """

    @model_validator(mode="after")
    def _validate_header_and_seal(self) -> Self:
        cls = type(self)
        if not cls.CONTRACT_TYPE:
            raise ValueError(
                f"{cls.__name__} declares no CONTRACT_TYPE; CanonicalContract is the "
                "registry §3 header and is not itself a record"
            )
        if self.contract_type != cls.CONTRACT_TYPE:
            # Unreachable through normal parsing: each subclass narrows `contract_type` to a
            # `Literal`, so a wrong value is a field error long before this runs. The check
            # stays because it guards the *other* failure — a subclass whose `CONTRACT_TYPE`
            # and whose `Literal` were edited apart — which nothing else would notice.
            raise ValueError(
                f"contract_type {self.contract_type!r} does not match {cls.__name__}'s "
                f"canonical type {cls.CONTRACT_TYPE!r}; a record cannot be relabelled by "
                "editing one field"
            )
        if cls.ID_KIND is not None and not has_prefix(self.contract_id, cls.ID_KIND):
            raise ValueError(
                f"contract_id {self.contract_id!r} does not carry the {cls.ID_KIND!r} "
                "identity prefix required by ADR-055"
            )
        if cls.PROJECT_SCOPED and self.project_id is None:
            raise ValueError(
                f"{cls.__name__} is project-scoped, so project_id is required by the "
                "registry §3 header"
            )

        self.seal_derived_hashes()

        computed = content_hash(self)
        if not self.content_hash:
            self.content_hash = computed
        elif self.content_hash != computed:
            raise ValueError(
                f"content_hash {self.content_hash!r} does not match the digest of this "
                f"payload ({computed!r}); the record was edited after it was sealed"
            )
        return self
