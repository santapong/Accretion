"""SDD §9.4 stage 3 over the store: the verified experience one node may learn from.

:class:`~accretion.routing.stages.NoEvidence` is the honest answer for a deployment that has
recorded nothing; this is the answer for one that has. It is the *only* production
implementation of :class:`~accretion.routing.stages.EvidenceRetriever`, and it is deliberately
a filter and nothing else — it computes no aggregate, invents no record and never widens the
set §10.1 permits a caller to read.

**Six filters, each of which is a way a routing memory stops being trustworthy.**

* **Membership.** A principal with no membership in the workspace retrieves nothing. §10.1
  makes the permission proof part of what makes a record eligible, and the alternative —
  trusting the ``PrincipalRef``'s own ``status`` — would take authority from a value that
  :func:`~accretion.routing.identity.principal_ref_for_run` synthesises without asking the
  store.
* **Heads only.** ``list_experience_records`` returns every generation of every projection,
  and a superseded generation is the *stale* attribution or the *unresolved* contradiction
  that a revision replaced. Learning from both would count one outcome twice and count the
  version that was corrected as though it still stood.
* **Signature.** §7.10 makes the contract signature the retrieval key: two nodes exchange
  evidence exactly when their signatures are equal. The comparison is on the whole value
  object rather than on any part of it, so a node cannot be handed the evidence of a node
  that merely shares its objective.
* **Eligibility and contradictions.** ``eligible_for_learning`` is the flag the projector set
  from a verified local outcome, and an ``OPEN`` contradiction is exactly the record §10.1
  excludes. ``RESOLVED`` is excluded too and that is not the same rule: a resolution
  adjudicates *which* side was right, and the losing row keeps its status, so a retriever
  that treated ``RESOLVED`` as clean would teach the router from the adjudicated-away side.
* **Visibility.** A ``PROJECT`` record is evidence inside its own project only, so a
  workspace-wide retrieval (``project_id is None``) sees none of them; a
  ``TEAM_WORKSPACE`` record reaches the whole workspace it was shared into and no further.
  ``Visibility`` has no third value, and this is checked rather than assumed because
  ``permission_provenance.scope`` is what the sharing principal actually granted.
* **Retraction and the future.** A retracted P7 ``Experience`` makes its projection
  ineligible by dereference (ADR-054 b), and a record sealed *after* the instant the router
  is deciding at is not evidence that was available to the decision. Both are exclusions the
  receipt would otherwise be unable to explain.

**Where the P7 row is looked up, and why not by ``contract_id``.** Migration 0020 moved the
projection's key off the primary key, so a *revision*'s ``contract_id`` names no ``experiences``
row. The projector writes the real id into the ``experience_id`` label
(:data:`~accretion.feedback.experience.EXPERIENCE_ID_LABEL`), and this reads it from there,
falling back to the contract id for a root generation written before the label existed. A
retriever that resolved by ``contract_id`` alone would find no row for every revision, read
that as "retracted", and silently drop exactly the records M3a.2 wrote to correct the others —
which is the failure ``list_experience_record_revisions``'s docstring warns about.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from accretion.contracts import PrincipalRef
from accretion.contracts.routing import (
    ContractSignature,
    ContradictionStatus,
    ExperienceRecord,
    Visibility,
)
from accretion.feedback.experience import EXPERIENCE_ID_LABEL
from accretion.persistence.store import StateStore

__all__ = ["StoreEvidenceRetriever"]


class StoreEvidenceRetriever:
    """Every stored experience record this node may treat as evidence, in a fixed order.

    The order is ``(created_at, contract_id)`` — the same total order every ``list_`` in this
    repository sorts by, and for the same reason: the scorer's evidence set reaches the
    receipt as ``experience_refs``, and two backends that returned one body of evidence in two
    orders would produce two receipts for one decision.
    """

    def __init__(self, store: StateStore) -> None:
        self.store = store

    async def retrieve(
        self,
        *,
        workspace_id: str,
        project_id: str | None,
        signature: ContractSignature,
        principal: PrincipalRef,
        as_of: datetime,
    ) -> Sequence[ExperienceRecord]:
        """The eligible, uncontradicted, visible records for ``signature``, oldest first.

        Returns an empty sequence for an empty store, an unknown signature or a principal who
        may not read the workspace, and raises for none of them: a cold start is a normal path
        in §9.4, and turning it into an exception would make the router report
        ``EVIDENCE_UNAVAILABLE`` (§15.1) for a project that genuinely has no history — the one
        confusion the ladder exists to prevent.
        """

        memberships = await self.store.list_workspace_memberships(
            workspace_id=workspace_id, principal_id=principal.principal_id
        )
        if not memberships:
            return ()

        records = await self.store.list_experience_records(
            workspace_id=workspace_id, project_id=project_id
        )
        superseded = {
            record.supersedes_contract_id
            for record in records
            if record.supersedes_contract_id is not None
        }
        eligible: list[ExperienceRecord] = []
        for record in records:
            if record.contract_id in superseded:
                continue
            if record.contract_signature != signature:
                continue
            if not record.eligible_for_learning:
                continue
            if record.contradiction_status is not ContradictionStatus.NONE:
                continue
            if record.created_at > as_of:
                continue
            if not _visible(record, project_id):
                continue
            if await self._retracted(record):
                continue
            eligible.append(record)
        return sorted(eligible, key=lambda record: (record.created_at, record.contract_id))

    async def _retracted(self, record: ExperienceRecord) -> bool:
        """Whether the P7 experience this record projects has been moderated away.

        A missing row is *not* a retraction. The projection's foreign key is ``RESTRICT``, so
        a record whose experience row is absent is a record the store would have refused, and
        answering "retracted" for one would hide a row for a reason that never happened.
        """

        experience = await self.store.get_experience(
            record.labels.get(EXPERIENCE_ID_LABEL, record.contract_id)
        )
        return experience is not None and experience.retracted


def _visible(record: ExperienceRecord, project_id: str | None) -> bool:
    """§7.10 read scope, decided from the record's own visibility.

    ``PROJECT`` needs the asking project to be named *and* to be the record's own: a
    workspace-wide retrieval that saw project-scoped records would share them with every other
    project in the workspace, which is precisely the grant their provenance withheld.
    """

    if record.visibility is Visibility.TEAM_WORKSPACE:
        return True
    return project_id is not None and record.project_id == project_id
