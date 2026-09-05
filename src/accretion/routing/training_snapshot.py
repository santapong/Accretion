"""The reproducible router training snapshot (SDD §10.1).

§10.1 requires a router version to record *exactly* which evidence it was fitted on and
under what rules, and :class:`~accretion.contracts.routing.RouterTrainingSnapshot` is that
list typed. This module is the half the contract cannot supply: the deterministic query
that decides membership, and the digest that makes "rebuild it and compare" a check rather
than a hope.

**What "reproducible" is made of here.** Four things, each of which is a way the property
is normally lost:

* *Order.* ``included_experience_ids`` is sorted lexicographically before it is written.
  ``MemoryStore`` and PostgreSQL both list by ``(created_at, contract_id)``, so an
  unsorted manifest would silently key on when a record happened to be written; two
  workspaces that gathered the same evidence in a different order would produce different
  digests for the same snapshot.
* *Time.* Nothing here calls :func:`datetime.now`. ``clock`` is injected and ``window`` is
  explicit, so building the same snapshot twice produces the same ``content_hash`` rather
  than two documents that differ only in when they were made.
* *Vocabulary.* The categorical indices in a materialised row are read against a
  :class:`~accretion.routing.features.Vocabulary` frozen at build time, and its digest is
  recorded. :func:`materialize` refuses a different one instead of quietly producing rows
  that no longer mean what the snapshot says they mean.
* *Identity.* ``contract_id`` is derived from the workspace, the window and the rules
  digest, so the same request yields the same id and a re-put is the no-op the append-only
  store already knows how to recognise — rather than a second row describing the same
  snapshot under a fresh random id.

**Membership, in the order the filters apply.** In the window; not retracted; then the
tallies are taken; then eligible for learning and not under an excluded contradiction
status; then deduplicated. The tallies sit in the middle deliberately: ``excluded_*`` is a
count of what this snapshot *declined*, and a count taken after the eligibility filter
would always be zero and would say nothing.

**Retraction and revision are read from the v0.2 record, never copied.** ADR-054 b makes
:class:`~accretion.contracts.routing.ExperienceRecord` a projection: it declares no
``retracted`` and no ``revision`` field because the ``Experience`` of the same id already
has both. So the builder dereferences, which is also what makes "highest revision" a rule
it can actually apply.

Nothing here trains, ranks or predicts.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from accretion.contracts import PrincipalRef
from accretion.contracts.canonical import canonical_json, content_hash
from accretion.contracts.routing import (
    ContradictionStatus,
    ExperienceRecord,
    PermissionProvenance,
    RouterTrainingSnapshot,
    SnapshotSplit,
    VerificationState,
    Visibility,
)
from accretion.experience.models import Experience

# `_PREFIXES` and `_encode_base32` are private to `accretion.ids`, and importing them is a
# deliberate, temporary borrow rather than an oversight. A derived id must carry the same
# `rts_` prefix and the same 26-character Crockford base32 shape `new_id` mints, and the
# alternative to borrowing is copying the alphabet and the encoder into this module — the
# second source of truth registry §21 forbids, and the one that would silently fork the
# identity scheme the first time either copy was touched. A sibling PR adds a shared
# `derived_id(kind, *parts)` to `ids.py`; when it lands, `_derived_id` below becomes a call
# to it and these two names go away.
from accretion.ids import _PREFIXES, _encode_base32
from accretion.persistence.store import StateStore
from accretion.routing.features import Vocabulary

__all__ = [
    "DEDUPLICATION_RULE",
    "DEFAULT_CONTRADICTION_TREATMENT",
    "SnapshotBuilder",
    "SnapshotRules",
    "TrainingRow",
    "TrainingTable",
    "label_for",
    "materialize",
]


DEDUPLICATION_RULE = (
    "one row per (source_node_execution_id, configuration_hash); highest revision"
)
"""SDD §10.1's "deduplication rules", stated once and written into every snapshot.

The key is the node execution and the configuration signature, not the experience id: one
node execution re-projected after an attribution pass is the same outcome twice, and
counting it twice would weight that outcome twice. "Highest revision" resolves the
collision towards the most recently corrected projection rather than towards whichever row
the store happened to return first.
"""

DEFAULT_CONTRADICTION_TREATMENT = (
    "OPEN contradictions are excluded: an unresolved contradiction is the one record §10.1 "
    "says a snapshot must not learn from. NONE and RESOLVED are kept and are not merged, "
    "because 'no contradiction was found' and 'one was found and adjudicated' are different "
    "claims about the same evidence."
)
"""The prose half of the contradiction rule. The machine-checkable half is the status list.

The contract carries both for a reason: the list alone cannot say *why* a status was
excluded, and prose alone cannot be enforced.
"""

_VISIBILITY_RANK: dict[Visibility, int] = {
    Visibility.PROJECT: 0,
    Visibility.TEAM_WORKSPACE: 1,
}
"""How wide a visibility is, narrowest first. Two values today; the order is what matters."""


def label_for(state: VerificationState | None) -> float | None:
    """The learning label for a verification state, or ``None`` when there is not one.

    ``PASS`` is 1.0 and ``FAIL`` is 0.0. Every other state — ``PENDING``, ``INCONCLUSIVE``,
    ``ERROR``, ``QUARANTINED`` and the absent ``final_run_status`` — is ``None`` and lands
    in *neither* label set.

    ``INCONCLUSIVE`` is the value this function exists for. Registry §5.1 is explicit that
    an inconclusive verdict is a judgement about the evidence and an error is the absence
    of one, and neither is a failure. Mapping either onto 0.0 would teach a router that
    "we could not tell" looks exactly like "it did not work", which is how a cautious
    verifier gets learned as a broken configuration.
    """

    if state is VerificationState.PASS:
        return 1.0
    if state is VerificationState.FAIL:
        return 0.0
    return None


@dataclass(frozen=True, slots=True)
class SnapshotRules:
    """The declared, hashable rules one snapshot was cut under.

    A frozen dataclass of tuples rather than a mapping, because :meth:`digest` feeds the
    snapshot's derived id: two builds that declared the same rules must produce the same
    id, and a dict whose iteration order depended on how the caller built it could not
    promise that. Use :meth:`over` when the caller has a mapping and does not care about
    its order.
    """

    provider_version_boundaries: tuple[tuple[str, str], ...] = ()
    excluded_contradiction_statuses: tuple[ContradictionStatus, ...] = (
        ContradictionStatus.OPEN,
    )
    contradiction_treatment: str = DEFAULT_CONTRADICTION_TREATMENT
    deduplication_rule: str = DEDUPLICATION_RULE
    vocabulary: Vocabulary = field(default_factory=Vocabulary)

    @classmethod
    def over(
        cls,
        *,
        provider_version_boundaries: Mapping[str, str] | None = None,
        excluded_contradiction_statuses: Iterable[ContradictionStatus] | None = None,
        contradiction_treatment: str = DEFAULT_CONTRADICTION_TREATMENT,
        deduplication_rule: str = DEDUPLICATION_RULE,
        vocabulary: Vocabulary | None = None,
    ) -> SnapshotRules:
        """Build rules from ordinary mappings and iterables, in canonical order."""

        boundaries = dict(provider_version_boundaries or {})
        statuses = tuple(excluded_contradiction_statuses or (ContradictionStatus.OPEN,))
        return cls(
            provider_version_boundaries=tuple(sorted(boundaries.items())),
            excluded_contradiction_statuses=statuses,
            contradiction_treatment=contradiction_treatment,
            deduplication_rule=deduplication_rule,
            vocabulary=vocabulary if vocabulary is not None else Vocabulary(),
        )

    def boundaries(self) -> dict[str, str]:
        """The provider version boundaries as the contract's ``dict[str, str]``."""

        return dict(self.provider_version_boundaries)

    def digest(self) -> str:
        """The digest of the declared rules, which is half of the snapshot's identity."""

        return content_hash(
            {
                "contradiction_treatment": self.contradiction_treatment,
                "deduplication_rule": self.deduplication_rule,
                "excluded_contradiction_statuses": [
                    status.value for status in self.excluded_contradiction_statuses
                ],
                "provider_version_boundaries": [
                    list(pair) for pair in self.provider_version_boundaries
                ],
                "vocabulary_digest": self.vocabulary.digest(),
            },
            exclude=(),
        )


@dataclass(frozen=True, slots=True)
class TrainingRow:
    """One materialised row of a snapshot: everything the evidence itself determines.

    Deliberately *not* a :class:`~accretion.routing.features.FeatureRow`. A full feature
    vector needs the routing context, the node contract, the candidate configuration and
    the objective that were in play, and an :class:`ExperienceRecord` references none of
    them — it is a projection of an outcome, not of a decision. What a row can be rebuilt
    from, byte for byte, a year later is the projection plus the ``Experience`` it is keyed
    by, and that is what this holds. M4.3's trainer joins these rows to contexts; the
    manifest digest only has to prove that *this evidence, in this order* is what the
    snapshot named.

    ``cost`` stays a :class:`~decimal.Decimal` all the way into the digest, because
    :func:`~accretion.contracts.canonical.canonical_json` serialises it as its exact digit
    string and a float round-trip would move the digest for no reason a reader could see.
    """

    experience_id: str
    project_id: str | None
    source_node_execution_id: str
    configuration_hash: str
    node_kind: str
    risk_class: str
    objective_digest: str
    capability_digest: str
    verification_spec_hash: str
    provider: str
    model_vocab_index: int
    revision: int
    visibility: str
    contradiction_status: str
    local_verification_status: str
    final_run_status: str | None
    quality: float | None
    cost: Decimal
    latency_ms: int
    attribution_score: float | None
    attribution_confidence: float

    def as_payload(self) -> dict[str, object]:
        """The canonicalizable form the manifest digest is taken over."""

        return {
            "attribution_confidence": self.attribution_confidence,
            "attribution_score": self.attribution_score,
            "capability_digest": self.capability_digest,
            "configuration_hash": self.configuration_hash,
            "contradiction_status": self.contradiction_status,
            "cost": self.cost,
            "experience_id": self.experience_id,
            "final_run_status": self.final_run_status,
            "latency_ms": self.latency_ms,
            "local_verification_status": self.local_verification_status,
            "model_vocab_index": self.model_vocab_index,
            "node_kind": self.node_kind,
            "objective_digest": self.objective_digest,
            "project_id": self.project_id,
            "provider": self.provider,
            "quality": self.quality,
            "revision": self.revision,
            "risk_class": self.risk_class,
            "source_node_execution_id": self.source_node_execution_id,
            "verification_spec_hash": self.verification_spec_hash,
            "visibility": self.visibility,
        }


@dataclass(frozen=True, slots=True)
class TrainingTable:
    """The rows a snapshot names, with their two label sets and their weights.

    Two label sets and not one. ``labels_local`` is what this node's own verifier said and
    ``labels_final`` is what the run concluded, and they disagree often enough to matter —
    a node can pass locally inside a run that later failed, and a router that learned only
    the second would be crediting this node for someone else's outcome. Both are aligned
    positionally with :attr:`rows`, and both hold ``None`` wherever there is no label
    (see :func:`label_for`).

    ``weights`` is the attribution confidence of each row. §9.6 makes attribution a
    derived, versioned view whose confidence is recorded precisely because it varies, and a
    table that weighted every row equally would let a record credited at 0.05 confidence
    argue as loudly as one at 0.95.
    """

    rows: tuple[TrainingRow, ...]
    labels_local: tuple[float | None, ...]
    labels_final: tuple[float | None, ...]
    weights: tuple[float, ...]

    @property
    def manifest_digest(self) -> str:
        """The digest of these rows, comparable with the snapshot's ``manifest_digest`` label."""

        return _manifest_digest(self.rows)


def _manifest_digest(rows: Sequence[TrainingRow]) -> str:
    """SHA-256 over the canonical JSON of the rows, in the order given.

    Over the rows' declared payloads rather than over the objects: a digest taken over a
    ``repr`` would move when a field was reordered and hold still when a
    :class:`~decimal.Decimal` was rounded, which is exactly backwards.
    """

    return content_hash([row.as_payload() for row in rows], exclude=())


def _derived_id(*parts: str) -> str:
    """A stable ``rts_`` id for a snapshot, in the shape :func:`~accretion.ids.new_id` mints.

    Derived and not minted: the same workspace, window, rules *and evidence* must name the
    same snapshot, so that a rebuild is a byte-identical re-put the append-only store
    recognises as a no-op rather than a second row claiming to be a different snapshot of
    the same evidence. The evidence is a part rather than an assumption, because a window
    closing is not the same as its records ceasing to change: two cuts that see different
    rows are two snapshots and must carry two ids. Twenty-six base32 characters from the
    low 130 bits of the digest, which is what ``has_prefix`` and ADR-055 require of any id
    in this repository.
    """

    digest = hashlib.sha256(canonical_json(list(parts))).digest()
    value = int.from_bytes(digest) & ((1 << 130) - 1)
    return f"{_PREFIXES['router_training_snapshot']}_{_encode_base32(value, 26)}"


def _narrowest_permission_proof(
    records: Sequence[ExperienceRecord],
) -> PermissionProvenance:
    """The narrowest provenance that covers every included record.

    Narrowest and not widest, and the difference is the whole point. Each record's
    provenance states the scope it was *permitted* to be shared at; a snapshot that pooled
    them may only claim a scope every one of them allows, which is the minimum. Taking the
    maximum would let one ``TEAM_WORKSPACE`` record license the sharing of a
    ``PROJECT``-scoped one, which is how a permission proof becomes a permission laundry.

    Ties are broken on the canonical bytes of the provenance so that two equally narrow but
    differently-worded proofs resolve the same way on every machine.
    """

    return min(
        (record.permission_provenance for record in records),
        key=lambda proof: (_VISIBILITY_RANK[proof.scope], canonical_json(proof)),
    )


def _training_row(
    record: ExperienceRecord, experience: Experience, vocab: Vocabulary
) -> TrainingRow:
    signature = record.contract_signature
    return TrainingRow(
        experience_id=record.contract_id,
        project_id=record.project_id,
        source_node_execution_id=record.source_node_execution_id,
        configuration_hash=record.configuration_hash,
        node_kind=signature.node_kind.value,
        risk_class=signature.risk_class.value,
        objective_digest=signature.objective_digest,
        capability_digest=signature.capability_digest,
        verification_spec_hash=signature.verification_spec_hash,
        provider=experience.provider.value,
        model_vocab_index=vocab.model_index(experience.runtime_model),
        revision=experience.revision,
        visibility=record.visibility.value,
        contradiction_status=record.contradiction_status.value,
        local_verification_status=record.local_verification_status.value,
        final_run_status=(
            None if record.final_run_status is None else record.final_run_status.value
        ),
        quality=record.outcomes.quality,
        cost=record.outcomes.cost,
        latency_ms=record.outcomes.latency_ms,
        attribution_score=record.attribution.score,
        attribution_confidence=record.attribution.confidence,
    )


def _table_over(rows: Sequence[TrainingRow], records: Sequence[ExperienceRecord]) -> TrainingTable:
    return TrainingTable(
        rows=tuple(rows),
        labels_local=tuple(label_for(record.local_verification_status) for record in records),
        labels_final=tuple(label_for(record.final_run_status) for record in records),
        weights=tuple(record.attribution.confidence for record in records),
    )


class SnapshotBuilder:
    """Builds one :class:`RouterTrainingSnapshot` from what a workspace's store already holds.

    Holds a store and nothing else. Every other input to a snapshot — the window, the
    split, the rules, the principal and the clock — is an argument to :meth:`build`, so
    that two builds that were given the same inputs are the same snapshot no matter what
    the builder was constructed with or when.
    """

    def __init__(self, store: StateStore) -> None:
        self.store = store

    async def build(
        self,
        *,
        workspace_id: str,
        window: tuple[datetime, datetime],
        split: SnapshotSplit,
        rules: SnapshotRules,
        created_by: PrincipalRef,
        clock: Callable[[], datetime],
    ) -> RouterTrainingSnapshot:
        """Gather the eligible evidence in ``window`` and seal it as a snapshot.

        ``window`` is half-open — ``window_start <= created_at < window_end`` — so that two
        adjacent windows partition time rather than overlapping on their shared instant,
        and one experience can never be evidence in two consecutive snapshots.

        Raises :class:`ValueError` when the window is empty or malformed, or when no record
        survives the filters: a snapshot over nothing is not a snapshot, and
        ``included_experience_ids`` is ``min_length=1`` for that reason.
        """

        window_start, window_end = window
        for name, moment in (("window_start", window_start), ("window_end", window_end)):
            if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
                raise ValueError(
                    f"{name} is naive and so names no instant; a snapshot window must be "
                    "reproducible on a machine in another zone"
                )
        if window_end <= window_start:
            raise ValueError(
                f"window_end {window_end.isoformat()} is not after window_start "
                f"{window_start.isoformat()}; a snapshot over an empty window includes "
                "nothing it could have observed"
            )

        excluded_statuses = set(rules.excluded_contradiction_statuses)
        considered: list[tuple[ExperienceRecord, Experience]] = []
        for record in await self.store.list_experience_records(workspace_id=workspace_id):
            if not window_start <= record.created_at < window_end:
                continue
            # ADR-054 b: the projection declares no `retracted`, so retraction is read from
            # the P7 experience whose id this record *is*. A projection whose experience is
            # gone is a record of nothing and is treated as retracted rather than trusted.
            experience = await self.store.get_experience(record.contract_id)
            if experience is None or experience.retracted:
                continue
            considered.append((record, experience))

        excluded_inconclusive = sum(
            1
            for record, _ in considered
            if record.local_verification_status is VerificationState.INCONCLUSIVE
        )
        excluded_open_contradictions = sum(
            1 for record, _ in considered if record.contradiction_status in excluded_statuses
        )

        eligible = [
            pair
            for pair in considered
            if pair[0].eligible_for_learning
            and pair[0].contradiction_status not in excluded_statuses
        ]
        included = _deduplicate(eligible)
        if not included:
            raise ValueError(
                f"no experience record in workspace {workspace_id!r} between "
                f"{window_start.isoformat()} and {window_end.isoformat()} is eligible for "
                "learning; a training snapshot over no evidence would name a fit that "
                "cannot be reproduced or falsified (§10.1)"
            )

        records = [record for record, _ in included]
        rows = tuple(
            _training_row(record, experience, rules.vocabulary)
            for record, experience in included
        )
        # Built through `model_validate` rather than the keyword constructor. The
        # pydantic mypy plugin does not carry `CanonicalContract`'s registry §3 header
        # fields onto a subclass declared in another module, so `RouterTrainingSnapshot(
        # contract_id=..., created_by=...)` needs a blanket `type: ignore[call-arg]` —
        # which would also hide a genuinely misspelled field. Validation is identical
        # either way, and `extra="forbid"` still refuses every key that is not a field.
        return RouterTrainingSnapshot.model_validate(
            {
                "contract_id": _derived_id(
                    workspace_id,
                    window_start.astimezone(UTC).isoformat(),
                    window_end.astimezone(UTC).isoformat(),
                    rules.digest(),
                    # The evidence is part of the identity, not only of the content. A
                    # closed window is not a closed set of records: a backfilled
                    # projection, or the re-projection after an attribution pass that
                    # `DEDUPLICATION_RULE` exists for, lands inside an already-cut window
                    # and moves the manifest. Without this part the second cut would claim
                    # the first cut's id with different content, and the append-only store
                    # would reject it as an immutability violation instead of storing a
                    # second snapshot.
                    _manifest_digest(rows),
                ),
                "created_at": clock(),
                "created_by": created_by,
                "workspace_id": workspace_id,
                "included_experience_ids": [record.contract_id for record in records],
                "permission_proof": _narrowest_permission_proof(records),
                "excluded_contradiction_statuses": list(
                    rules.excluded_contradiction_statuses
                ),
                "contradiction_treatment": rules.contradiction_treatment,
                "deduplication_rule": rules.deduplication_rule,
                "window_start": window_start,
                "window_end": window_end,
                "provider_version_boundaries": rules.boundaries(),
                "split": split,
                "labels": {
                    "excluded_inconclusive": str(excluded_inconclusive),
                    "excluded_open_contradictions": str(excluded_open_contradictions),
                    "manifest_digest": _manifest_digest(rows),
                    "row_count": str(len(rows)),
                    "vocab_digest": rules.vocabulary.digest(),
                },
            }
        )


def _deduplicate(
    pairs: Sequence[tuple[ExperienceRecord, Experience]],
) -> list[tuple[ExperienceRecord, Experience]]:
    """Apply :data:`DEDUPLICATION_RULE`, then sort the survivors lexicographically by id.

    The sort is the load-bearing line. Both store backends list by
    ``(created_at, contract_id)``, so a manifest that kept the store's order would key on
    *when* each record was written — and the same evidence gathered in a different order
    would hash differently, which is exactly the unreproducibility §10.1 exists to forbid.

    The tie-break inside a duplicate group is ``(revision, contract_id)``: highest revision
    wins, and two rows at the same revision resolve towards the later id rather than
    towards whichever the store returned first.
    """

    best: dict[tuple[str, str], tuple[ExperienceRecord, Experience]] = {}
    for record, experience in pairs:
        key = (record.source_node_execution_id, record.configuration_hash)
        incumbent = best.get(key)
        if incumbent is None or (experience.revision, record.contract_id) > (
            incumbent[1].revision,
            incumbent[0].contract_id,
        ):
            best[key] = (record, experience)
    return sorted(best.values(), key=lambda pair: pair[0].contract_id)


async def materialize(
    snapshot: RouterTrainingSnapshot, store: StateStore, vocab: Vocabulary
) -> TrainingTable:
    """Rebuild a stored snapshot's rows, labels and weights from the store.

    This is the operation §10.1's reproducibility claim is *about*: a snapshot that could
    be described but not rebuilt would make every promotion report that cited it an
    unfalsifiable claim. Reading it back and recomputing
    :attr:`TrainingTable.manifest_digest` must reproduce the snapshot's ``manifest_digest``
    label, and a caller that wants to check the claim compares the two.

    ``vocab`` is verified against the snapshot's ``vocab_digest`` and a mismatch raises.
    Absorbing a different vocabulary would silently re-map every categorical index and hand
    back rows that no longer mean what the snapshot says they mean — a failure that would
    surface later as an unexplained digest mismatch instead of here as a named one.

    The ids are re-sorted rather than trusted, so that a snapshot whose manifest was written
    by an older or a hand-edited writer still materialises in the canonical order.
    """

    recorded_vocab = snapshot.labels.get("vocab_digest")
    if recorded_vocab is not None and recorded_vocab != vocab.digest():
        raise ValueError(
            f"snapshot {snapshot.contract_id} was built under vocabulary "
            f"{recorded_vocab} and materialization was given {vocab.digest()}; a "
            "different vocabulary re-maps every categorical index and would produce rows "
            "that do not mean what this snapshot says they mean"
        )

    records: list[ExperienceRecord] = []
    rows: list[TrainingRow] = []
    for experience_id in sorted(snapshot.included_experience_ids):
        record = await store.get_experience_record(experience_id)
        experience = await store.get_experience(experience_id)
        if record is None or experience is None:
            raise ValueError(
                f"snapshot {snapshot.contract_id} names experience {experience_id}, which "
                "is no longer in the store; a snapshot that cannot be rebuilt cannot "
                "support the promotion report that cites it (§10.1)"
            )
        records.append(record)
        rows.append(_training_row(record, experience, vocab))
    return _table_over(rows, records)
