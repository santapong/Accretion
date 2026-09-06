"""The routing projection over a P7 experience (SDD §7.10, §9.6; ADR-054 b).

A :class:`~accretion.contracts.routing.ExperienceRecord` is not a record of a node. It is a
*projection* of the v0.2 P7 :class:`~accretion.experience.models.Experience` the run already
produced, carrying the handful of routing-scoped facts P7 has nowhere to put — the retrieval
signature, the configuration the outcome is evidence about, the two verification verdicts, the
attribution, the contradiction status and the sharing proof. Everything else is read through
the experience it names and is never copied here, because two copies of one fact are two facts
that will eventually disagree (registry §21).

Three rules shape this module, and each of them is a way the projection could quietly become
a lie:

* **The experience must exist before the projection does.** ``experience_records.experience_id``
  is a ``RESTRICT`` foreign key into ``experiences`` (migration 0020), so
  :meth:`ExperienceProjector.project` calls ``materialize`` *first* and files the record under
  the id that came back. A projection of an experience that does not exist is not a record with
  a dangling field, it is a record of nothing.
* **Nothing is ever edited.** A contradiction moving ``NONE`` → ``OPEN`` on a record that is
  already stored, and a resolution moving ``OPEN`` → ``RESOLVED``, are both **new rows** with
  their own derived ids and a ``supersedes_contract_id`` naming what they replace (registry
  §17, §9.6). The store refuses an in-place rewrite outright; this module never asks for one.
* **A contradiction is sealed into the row that carries it, not appended after it.** When the
  projection being written is itself contradicted, its ``contradiction_status`` is ``OPEN``
  *before* it is stored, so no generation of it was ever eligible for learning. Writing it clean
  and revising it afterwards would leave an eligible row in ``list_experience_records`` forever,
  and §10.1's exclusion of unresolved contradictions would then depend on every reader knowing
  to prefer the newest revision. The *other* side of a contradiction — a record stored before
  this one existed — is already written and therefore genuinely does get a revision.

Everything that decides eligibility is checked twice: once here, where the answer can be
explained, and once by :class:`~accretion.contracts.routing.ExperienceRecord`'s own validator,
which refuses an eligible record whose verification did not pass or whose contradiction is
open. The duplication is deliberate — this layer knows *why*, the contract knows *whether*, and
a bug in this layer cannot make the contract accept the record.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from accretion.contracts import PrincipalRef, Run
from accretion.contracts.canonical import content_hash
from accretion.contracts.refs import PolicyRef
from accretion.contracts.routing import (
    AttributionSummary,
    ContractSignature,
    ContradictionStatus,
    ExecutionConfiguration,
    ExperienceOutcomes,
    ExperienceRecord,
    FailureType,
    IndependentVerificationResult,
    NodeContract,
    PermissionProvenance,
    RoutingDecisionReceipt,
    VerificationState,
    Visibility,
)
from accretion.experience.models import ExperienceDetail
from accretion.ids import derived_id
from accretion.persistence.store import StateStore
from accretion.routing.stages import node_signature

__all__ = [
    "EXPERIENCE_ID_LABEL",
    "NODE_ID_LABEL",
    "NODE_KEY_LABEL",
    "PROJECTION_REVISION_LABEL",
    "RESOLUTION_LABEL",
    "RUN_ID_LABEL",
    "UNATTRIBUTED",
    "ContradictionDetector",
    "ExperienceMaterializer",
    "ExperienceProjector",
    "record_signature_for",
]

EXPERIENCE_ID_LABEL = "experience_id"
"""The P7 experience a stored projection is filed under, carried where a reader can see it.

``ExperienceRecord`` declares no field for its parent experience — it is a promoted column on
the row and nothing else (``persistence/store.py``'s ``_V04MemoryRow``) — and no ``list_`` in
that family returns it. So a consumer holding a record read back from the store has no way to
ask which experience it projects, which is exactly the resolution
``SnapshotBuilder.build`` needs and does not have. Recording it in the header's free-form
``labels`` costs one string, is inside the record's own digest, and makes the answer readable
without a new store surface.
"""

RUN_ID_LABEL = "run_id"
NODE_ID_LABEL = "node_id"
NODE_KEY_LABEL = "node_key"
"""Copied from the node contract's own labels (``routing/freeze.py``), and for the same reason
attribution needs them: the graph shape is keyed by ``node_id`` and a retry pair is keyed by
``(node_key, attempt)``, and neither is derivable from ``source_node_execution_id``, which is a
digest."""

ATTEMPT_LABEL = "attempt"
PROJECTION_REVISION_LABEL = "projection_revision"
"""Which generation of the projection this row is: ``"1"`` for the root, ``"2"`` and up for
revisions. Inside the derived id as well as in the labels, so two revisions of one record
cannot collide, and readable afterwards so an operator can order a chain without re-deriving
anything."""

RESOLUTION_LABEL = "contradiction_resolution"
CONTRADICTS_LABEL = "contradicts"

UNATTRIBUTED = AttributionSummary(
    score=None, confidence=0.0, method_version="unattributed/1"
)
"""The honest attribution of a record no attributor has looked at yet.

``score`` is ``None`` rather than ``0.0``, which :class:`AttributionSummary` exists to make
sayable: zero credit is a finding and no credit is an absence. ``method_version`` is mandatory
even here, so that a later re-attribution can tell a record it has already replaced from one it
has not.
"""

_VISIBILITY_WIDTH: Mapping[Visibility, int] = {
    Visibility.PROJECT: 0,
    Visibility.TEAM_WORKSPACE: 1,
}
"""How far each visibility reaches, and total over :class:`Visibility`.

Ordered rather than compared for equality, so the refusal below can say *wider* — which is the
direction that leaks. Totality is what makes the lookup safe without a membership check: a third
visibility would be a registry §3.2 change to a sealed enum and would raise a ``KeyError`` here,
which is the correct answer to "share this at a scope this code has never heard of"."""


def record_signature_for(node: NodeContract) -> ContractSignature:
    """SDD §7.10's retrieval key for a projection, derived the way the *router* derives it.

    A stored experience record is only ever read back through its signature, and the reader is
    :class:`~accretion.routing.service.DefaultNodeRoutingService`, which asks its
    :class:`~accretion.routing.stages.EvidenceRetriever` for
    ``node_signature(node, objective_digest=frozen.objective_ref.objective_contract_hash)``.
    So the projector writes under that same derivation and no other. A projection keyed on any
    other digest of the same node is not a differently-keyed record, it is an *unreachable*
    one: the comparison is an equality over a whole value object, a disagreement raises
    nothing anywhere, and retrieval simply returns an empty list forever while every receipt
    reads as a permanent cold start.

    ``objective_digest`` is a parameter of
    :func:`~accretion.routing.stages.node_signature` because ``objective_contract_ref`` is
    nullable, and the router's caller holds a non-nullable copy. A projector does not: it is
    handed the frozen node contract and nothing beside it. Hence the fallback — a digest of the
    objective *text*, wrapped in a one-key mapping so the hash commits to which field was
    hashed. A node whose objective revision was never pinned still gets a signature that is
    stable across runs and distinct per objective; it simply cannot be retrieved *as* an
    approved objective contract, which is the truth about it.
    """

    reference = node.objective_contract_ref
    return node_signature(
        node,
        objective_digest=(
            reference.objective_contract_hash
            if reference is not None
            else content_hash({"objective": node.objective}, exclude=())
        ),
    )


class ExperienceMaterializer(Protocol):
    """The one thing this module needs from
    :class:`~accretion.experience.service.ExperienceService`.

    Narrowed to a Protocol rather than depending on the class, because the projector's whole
    relationship with P7 is "give me the experience for this run, creating it if this is the
    first ask". Depending on the service would drag its retrieval, moderation and selection
    surface — and its feature gate, its git subprocess and its embedding index — into every
    caller and every test of a projection.
    """

    async def materialize(
        self, run_id: str, *, candidate_id: str | None = None
    ) -> ExperienceDetail: ...


class ContradictionDetector:
    """Which stored records materially disagree with a new one (SDD §7.10).

    Material disagreement is narrow on purpose: the *same* retrieval signature and the *same*
    configuration hash, decided ``PASS`` by one record and ``FAIL`` by the other. Both halves
    are load-bearing. Without the signature two unrelated nodes would contradict each other for
    having different outcomes, which is not a contradiction, it is a difference. Without the
    configuration hash the same node succeeding on one execution surface and failing on another
    would be flagged, which is not a contradiction either — it is the single most useful thing a
    router can learn.

    ``INCONCLUSIVE``, ``ERROR``, ``QUARANTINED`` and ``PENDING`` never contradict anything.
    Registry §5.1 makes an inconclusive verdict a judgement about the *evidence* rather than
    about the work, and a record that declined to decide cannot disagree with one that did.
    """

    def detect(
        self, new: ExperienceRecord, siblings: Sequence[ExperienceRecord]
    ) -> list[ExperienceRecord]:
        """Every sibling that contradicts ``new``, sorted by ``contract_id``.

        Sorted rather than left in the store's order because the result decides which rows get
        a revision and in which order they are written, and two backends listing the same rows
        in two orders would produce two histories of one contradiction.

        ``new`` itself is excluded by id *and* by execution instance. By id because a re-projection
        of an unchanged record is handed its own stored copy and must not be found contradicting
        itself; by execution instance because a revision chain of one node is several rows about
        one attempt, and a node cannot disagree with itself about what it did.
        """

        opposite = _CONTRADICTORY_OF.get(new.local_verification_status)
        if opposite is None:
            return []
        contradicting = [
            sibling
            for sibling in siblings
            if sibling.contract_id != new.contract_id
            and sibling.source_node_execution_id != new.source_node_execution_id
            and sibling.contract_signature == new.contract_signature
            and sibling.configuration_hash == new.configuration_hash
            and sibling.local_verification_status is opposite
        ]
        return sorted(contradicting, key=lambda record: record.contract_id)


_CONTRADICTORY_OF: Mapping[VerificationState, VerificationState] = {
    VerificationState.PASS: VerificationState.FAIL,
    VerificationState.FAIL: VerificationState.PASS,
}
"""The only pair of verdicts that can contradict each other."""


class ExperienceProjector:
    """Writes the §7.10 projection of one routed node, and revises it without editing it.

    ``created_by`` is the projector's own identity and is the author of every row it writes
    that no principal asked for — the ``OPEN`` revision a *new* record forces onto an older
    one is written by this service, not by the principal whose run happened to trigger it.
    Rows a principal did ask for carry that principal instead.
    """

    def __init__(
        self,
        store: StateStore,
        experiences: ExperienceMaterializer,
        clock: Callable[[], datetime],
        created_by: PrincipalRef,
    ) -> None:
        self.store = store
        self.experiences = experiences
        self.clock = clock
        self.created_by = created_by
        self.contradictions = ContradictionDetector()

    async def project(
        self,
        *,
        run: Run,
        node: NodeContract,
        receipt: RoutingDecisionReceipt,
        configuration: ExecutionConfiguration,
        local: IndependentVerificationResult | None,
        final_status: VerificationState | None,
        principal: PrincipalRef,
        attribution: AttributionSummary | None = None,
        outcomes: ExperienceOutcomes | None = None,
        failure_type: FailureType | None = None,
        visibility: Visibility = Visibility.PROJECT,
        permission_scope: Visibility = Visibility.PROJECT,
        justification: str | None = None,
    ) -> ExperienceRecord:
        """Project one routed node of ``run`` onto the run's P7 experience.

        The order is not negotiable. ``materialize`` runs first because its return value is the
        foreign key this row needs; the contradiction scan runs before the write because its
        answer is part of the row; the revisions of *other* records run after it because they
        are about a row that now exists.

        ``configuration`` is required even though ``receipt`` already names the hash, and it is
        checked against it. A projection recording an outcome under a configuration signature
        that is not the one the receipt selected would be evidence about the wrong execution
        surface, and nothing downstream could detect it: both values are valid digests.

        ``local`` is nullable and a missing verdict becomes ``PENDING`` rather than a failure or
        an absence. That is the honest reading — the node has not been verified — and it makes
        the record ineligible for learning by the contract's own rule rather than by a special
        case here.
        """

        if _VISIBILITY_WIDTH[visibility] > _VISIBILITY_WIDTH[permission_scope]:
            raise ValueError(
                f"visibility {visibility.value} is wider than the scope "
                f"{permission_scope.value} the permission provenance grants; §10.1 makes the "
                "provenance the proof that sharing was permitted, and a record shared more "
                "widely than it was permitted is shared without one"
            )
        configuration_hash = receipt.selected_configuration_hash
        if configuration_hash is None:
            raise ValueError(
                f"receipt {receipt.contract_id!r} selected no configuration, so there is no "
                "execution surface this outcome is evidence about; only a "
                "HUMAN_REVIEW_REQUIRED decision selects nothing and no node ran under it"
            )
        if configuration.configuration_hash != configuration_hash:
            raise ValueError(
                f"configuration {configuration.contract_id!r} has signature "
                f"{configuration.configuration_hash!r} while receipt {receipt.contract_id!r} "
                f"selected {configuration_hash!r}; projecting this outcome would file it "
                "against an execution surface the router never chose"
            )

        detail = await self.experiences.materialize(run.run_id)
        experience_id = detail.experience.experience_id

        parts: dict[str, Any] = {
            "contract_id": derived_id(
                "experience", experience_id, node.execution_instance_id, "1"
            ),
            "experience_id": experience_id,
            "node": node,
            "run": run,
            "created_by": principal,
            "created_at": self.clock(),
            "revision_ordinal": 1,
            "visibility": visibility,
            "permission_scope": permission_scope,
            "justification": justification,
            "configuration_hash": configuration_hash,
            "local": local,
            "local_status": local.status if local is not None else VerificationState.PENDING,
            "final_status": final_status,
            "attribution": attribution if attribution is not None else UNATTRIBUTED,
            "outcomes": outcomes if outcomes is not None else _measured(receipt, local),
            "failure_type": failure_type,
        }
        candidate = self._build(
            contradiction_status=ContradictionStatus.NONE, extra_labels={}, **parts
        )

        siblings = await self.store.list_experience_records(
            workspace_id=node.workspace_id, project_id=node.project_id
        )
        # Built twice rather than patched, because a record is sealed on construction: a
        # ``model_copy`` carrying a new contradiction status would keep the digest of the
        # document that had the old one, and the row would then disagree with its own hash.
        contradicting = self.contradictions.detect(candidate, siblings)
        if contradicting:
            candidate = self._build(
                contradiction_status=ContradictionStatus.OPEN,
                extra_labels={
                    CONTRADICTS_LABEL: ",".join(
                        record.contract_id for record in contradicting
                    )
                },
                **parts,
            )

        stored = await self.store.put_experience_record(
            candidate, experience_id=experience_id
        )
        for sibling in contradicting:
            await self._open_contradiction(sibling, against=stored)
        return stored

    async def resolve_contradiction(
        self,
        *,
        experience_id: str,
        workspace_id: str,
        record_id: str,
        resolution: str,
        principal: PrincipalRef,
    ) -> ExperienceRecord:
        """Move one record's line from ``OPEN`` to ``RESOLVED``, as a new row.

        ``record_id`` names the projection being adjudicated and **not** the experience alone,
        because one experience holds one projection per routed node of its run. Those lines are
        independent — node ``review`` can be contradicted while node ``implement`` is not — so
        "the newest row under this experience" is not the record a resolver meant; it is
        whichever node happened to be written last. The line's current head is followed from
        ``record_id`` through ``supersedes_contract_id``, so a caller may name the root it knows
        about rather than having to know which revision is current.

        ``workspace_id`` is a parameter because ``list_experience_record_revisions`` requires
        one and nothing maps an experience to its workspace: a projection is workspace-scoped
        and its P7 experience is not, so the caller that knows which tenant is asking has to
        say. Passing the wrong one returns an empty chain and raises below rather than reading
        another tenant's record.

        ``resolution`` is recorded in ``labels`` and is required to be non-empty. Registry §17
        makes the revision the audit trail, and a resolution with no stated reason is a
        contradiction that was closed rather than adjudicated.
        """

        if not resolution.strip():
            raise ValueError(
                "a contradiction is resolved by adjudicating it, so the resolution must say "
                "something; an empty reason closes the record without settling it"
            )
        chain = await self.store.list_experience_record_revisions(
            experience_id, workspace_id=workspace_id
        )
        head = _head_of(chain, record_id)
        if head is None:
            raise KeyError(record_id)
        if head.contradiction_status is not ContradictionStatus.OPEN:
            raise ValueError(
                f"record {head.contract_id!r} has contradiction status "
                f"{head.contradiction_status.value}; only an OPEN contradiction can be "
                "resolved, and re-resolving a settled one would write a second adjudication "
                "of a question that was already answered"
            )
        return await self._revise(
            head,
            experience_id=experience_id,
            created_by=principal,
            contradiction_status=ContradictionStatus.RESOLVED,
            attribution=head.attribution,
            discriminator=("resolved", resolution),
            extra_labels={RESOLUTION_LABEL: resolution},
        )

    async def reattribute(
        self,
        record: ExperienceRecord,
        *,
        experience_id: str,
        attribution: AttributionSummary,
        principal: PrincipalRef,
    ) -> ExperienceRecord:
        """Append a revision carrying a recomputed ``attribution`` (§9.6).

        The raw record is not touched, and this is the only method that writes an attribution
        onto an existing row. ``AC4-M3-026`` is the assertion that it stays that way: the root's
        stored bytes after a re-attribution are the bytes it was written with.
        """

        return await self._revise(
            record,
            experience_id=experience_id,
            created_by=principal,
            contradiction_status=record.contradiction_status,
            attribution=attribution,
            discriminator=(
                attribution.method_version,
                "none" if attribution.score is None else format(attribution.score, ".6f"),
                format(attribution.confidence, ".6f"),
            ),
            extra_labels={},
        )

    # ---------------------------------------------------------------- internals

    async def _open_contradiction(
        self, sibling: ExperienceRecord, *, against: ExperienceRecord
    ) -> ExperienceRecord:
        """Revise an already-stored record whose contradiction only exists now.

        Written by ``created_by`` — this service — and not by the principal behind the run that
        exposed it: the older record's owner did nothing, and attributing a state change to a
        principal who never asked for one would put a false author in the audit trail.

        A sibling that is already ``OPEN`` is returned untouched. Re-opening it would be a
        second row saying what the first one already says, and the store would accept it,
        because a different discriminator makes a different id.
        """

        if sibling.contradiction_status is ContradictionStatus.OPEN:
            return sibling
        return await self._revise(
            sibling,
            experience_id=sibling.labels.get(EXPERIENCE_ID_LABEL, sibling.contract_id),
            created_by=self.created_by,
            contradiction_status=ContradictionStatus.OPEN,
            attribution=sibling.attribution,
            discriminator=("contradicted-by", against.contract_id),
            extra_labels={CONTRADICTS_LABEL: against.contract_id},
        )

    async def _revise(
        self,
        record: ExperienceRecord,
        *,
        experience_id: str,
        created_by: PrincipalRef,
        contradiction_status: ContradictionStatus,
        attribution: AttributionSummary,
        discriminator: Sequence[str],
        extra_labels: Mapping[str, str],
    ) -> ExperienceRecord:
        """One new row superseding ``record``, differing only in what this revision changes.

        The id is derived from the superseded row *and* from what the revision decided, so two
        different recomputations are two rows while a repeat of one recomputation is a
        byte-identical re-put the append-only store accepts as a no-op. Deriving it from the
        superseded id alone would make the second recomputation an immutability error, and
        minting it fresh would make an idempotent retry impossible.
        """

        ordinal = int(record.labels.get(PROJECTION_REVISION_LABEL, "1")) + 1
        labels = dict(record.labels)
        labels.pop(CONTRADICTS_LABEL, None)
        labels.pop(RESOLUTION_LABEL, None)
        labels.update(extra_labels)
        labels[EXPERIENCE_ID_LABEL] = experience_id
        labels[PROJECTION_REVISION_LABEL] = str(ordinal)
        payload = _payload(record)
        payload.update(
            {
                "contract_id": derived_id(
                    "experience", experience_id, record.contract_id, *discriminator
                ),
                "content_hash": "",
                "created_at": self.clock(),
                "created_by": created_by,
                "supersedes_contract_id": record.contract_id,
                "labels": labels,
                "contradiction_status": contradiction_status,
                "attribution": attribution,
                "eligible_for_learning": _eligible(
                    record.local_verification_status, contradiction_status
                ),
            }
        )
        revision = ExperienceRecord.model_validate(payload)
        return await self.store.put_experience_record(
            revision, experience_id=experience_id
        )

    def _build(
        self,
        *,
        contract_id: str,
        experience_id: str,
        node: NodeContract,
        run: Run,
        created_by: PrincipalRef,
        created_at: datetime,
        revision_ordinal: int,
        visibility: Visibility,
        permission_scope: Visibility,
        justification: str | None,
        configuration_hash: str,
        local: IndependentVerificationResult | None,
        local_status: VerificationState,
        final_status: VerificationState | None,
        attribution: AttributionSummary,
        outcomes: ExperienceOutcomes,
        failure_type: FailureType | None,
        contradiction_status: ContradictionStatus,
        extra_labels: Mapping[str, str],
    ) -> ExperienceRecord:
        labels = {
            EXPERIENCE_ID_LABEL: experience_id,
            RUN_ID_LABEL: run.run_id,
            NODE_ID_LABEL: node.node_id,
            NODE_KEY_LABEL: node.labels.get(NODE_KEY_LABEL, node.node_id),
            ATTEMPT_LABEL: node.labels.get(ATTEMPT_LABEL, "1"),
            PROJECTION_REVISION_LABEL: str(revision_ordinal),
            **extra_labels,
        }
        payload: dict[str, Any] = {
            "contract_id": contract_id,
            "created_at": created_at,
            "created_by": created_by,
            "workspace_id": node.workspace_id,
            "project_id": node.project_id,
            "labels": labels,
            "visibility": visibility,
            "source_node_execution_id": node.execution_instance_id,
            "contract_signature": record_signature_for(node),
            "configuration_hash": configuration_hash,
            "local_verification_status": local_status,
            "final_run_status": final_status,
            "attribution": attribution,
            "outcomes": outcomes,
            "failure_type": failure_type,
            "contradiction_status": contradiction_status,
            "evidence_refs": (
                list(local.deterministic_evidence_refs) if local is not None else []
            ),
            "permission_provenance": PermissionProvenance(
                scope=permission_scope,
                policy=_policy_ref(node),
                granted_by=created_by,
                justification=justification or _justification(node, run, permission_scope),
            ),
            "eligible_for_learning": _eligible(local_status, contradiction_status),
        }
        return ExperienceRecord.model_validate(payload)


def _head_of(
    chain: Sequence[ExperienceRecord], record_id: str
) -> ExperienceRecord | None:
    """The current row of the supersession line ``record_id`` belongs to, or ``None``.

    Walks forward rather than backward: from the named row to whatever supersedes it, and on
    until nothing does. Bounded by the length of the chain, so a cycle — which the append-only
    store cannot produce, because a revision's id is derived from the row it supersedes — cannot
    hang this loop even if one were somehow written.
    """

    by_id = {record.contract_id: record for record in chain}
    successors = {
        record.supersedes_contract_id: record
        for record in chain
        if record.supersedes_contract_id is not None
    }
    current = by_id.get(record_id)
    if current is None:
        return None
    for _ in range(len(chain)):
        following = successors.get(current.contract_id)
        if following is None:
            return current
        current = following
    return current


def _eligible(
    local_status: VerificationState, contradiction_status: ContradictionStatus
) -> bool:
    """ADR-048's rule, in one place: verified, and not under an open contradiction.

    Both halves are necessary and neither is sufficient. A ``PASS`` under an open contradiction
    is exactly the record §10.1 requires a training snapshot to exclude, and a clean record that
    did not pass is not evidence a router may learn *from* — it is evidence about a failure,
    which is what ``failure_type`` and the §7.11 event carry.
    """

    return (
        local_status is VerificationState.PASS
        and contradiction_status is not ContradictionStatus.OPEN
    )


def _justification(
    node: NodeContract, run: Run, permission_scope: Visibility
) -> str:
    """Why this record may be shared, in the words of the authority that permitted it.

    Not decoration. :class:`PermissionProvenance` requires a non-empty justification because
    §10.1 asks for a *proof* of permission, and the other three fields are all identifiers: the
    scope, the policy and the principal say what was granted and by whom, and this says against
    which authorised objective. A caller with a better sentence passes one.
    """

    # `NodeContract` refuses to validate without one, so the fallback is unreachable through
    # the store; it is here because the field is typed optional on the shared header and a
    # `None` check that mypy can see is cheaper than an assertion that fails in production.
    objective = node.objective_contract_ref
    revision = objective.revision if objective is not None else 1
    contract_id = objective.objective_contract_id if objective is not None else "unknown"
    return (
        f"node {node.node_id} of run {run.run_id} was authorised against revision "
        f"{revision} of objective contract {contract_id}, and its outcome is shared at "
        f"{permission_scope.value} scope under the failure policy "
        f"{node.failure_policy_ref.policy_id} that contract names"
    )


def _policy_ref(node: NodeContract) -> PolicyRef:
    """The policy a projection is shared under: the node's own failure policy.

    Taken from the node contract rather than rebuilt from the receipt's ``policy_snapshot_id``,
    which is an ``id@version`` *label* and carries no content digest. §10.1 wants a proof, and a
    :class:`~accretion.contracts.refs.PolicyRef` without a digest is a claim about a document
    nobody can identify. The node contract already holds a fully sealed reference to the
    acceptance policy it was frozen against, so the proof is a read rather than a construction.
    """

    return node.failure_policy_ref


def _measured(
    receipt: RoutingDecisionReceipt, local: IndependentVerificationResult | None
) -> ExperienceOutcomes:
    """What the store can honestly say about cost, latency and quality without a meter.

    ``latency_ms`` is the interval the store actually witnessed: from the moment the routing
    decision was sealed to the moment the independent verdict was signed. That is the node's
    round trip as the durable record sees it, and it is a measurement rather than an estimate.
    A verdict signed before the receipt — two clocks, or a replayed receipt — floors at zero
    instead of recording a negative duration.

    ``quality`` is the fraction of the frozen spec's claim results that passed. It is nullable
    and is ``None`` when there are no claim results, because "no claim was decided" is an
    absence and a zero here would read as "everything failed".

    ``cost`` is ``0`` and means *no cost meter reached this projection*. Nothing in the v0.4
    store records what a node spent; the run manager does, and M3b passes measured outcomes
    through :meth:`ExperienceProjector.project`'s ``outcomes`` argument, which is why that
    argument exists. ``ExperienceOutcomes.cost`` is not nullable, so zero is the only
    representable value and this docstring is the place that says what it means.
    """

    latency_ms = 0
    if local is not None:
        elapsed = (local.signed_at - receipt.created_at).total_seconds() * 1_000.0
        latency_ms = max(0, int(elapsed))
    quality: float | None = None
    if local is not None and local.claim_results:
        passed = sum(
            1
            for claim in local.claim_results
            if claim.status is VerificationState.PASS
        )
        quality = passed / len(local.claim_results)
    return ExperienceOutcomes(quality=quality, cost=Decimal(0), latency_ms=latency_ms)


def _payload(record: ExperienceRecord) -> dict[str, Any]:
    """The record as a mapping of python objects, ready to be revised and re-validated.

    ``mode="python"`` and not ``mode="json"``: the enums and the ``Decimal`` come back as
    themselves, so a revision that changes one field cannot silently re-parse the other
    thirty through a string round trip. ``content_hash`` is dropped by the caller so the
    revision seals its own.
    """

    return record.model_dump(mode="python")
