"""What the store-backed §9.4 retriever will and will not hand a router as evidence.

Every test here is a way a routing memory quietly stops being trustworthy, and the reason each
one is a *test* rather than a comment is that none of them fails loudly in production: a
retriever that returned superseded generations, project-scoped records to another project, or
records sealed after the decision it is informing would simply make the router a little more
confident about a little less, and nothing anywhere would raise.

The store is a fresh ``MemoryStore`` and every assertion is about what ``retrieve`` returned,
never about the objects that were handed to ``put_experience_record``. The fixtures are the
ones ``test_v04_m3_experience.py`` already uses, imported rather than re-declared: a second
copy of ``seed_experience`` would drift from the ``RESTRICT`` key it exists to satisfy.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from test_v04_m2_service import _routable_execution
from test_v04_m3_experience import (
    PRINCIPAL,
    PROJECT_ID,
    WORKSPACE_ID,
    build,
    make_node,
    seed_experience,
)

from accretion.contracts import (
    PrincipalRef,
    PrincipalStatus,
    Project,
    Provider,
    Run,
    RunState,
    WorkspaceEntity,
    WorkspaceMembership,
    WorkspaceRole,
)
from accretion.contracts.routing import (
    ContractSignature,
    ContradictionStatus,
    ExperienceRecord,
    Visibility,
)
from accretion.experience.models import ModerationAction, ModerationActionType
from accretion.feedback.evidence import StoreEvidenceRetriever
from accretion.feedback.experience import EXPERIENCE_ID_LABEL, record_signature_for
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.stages import node_signature

AS_OF = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
SEALED = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
OUTSIDER = PrincipalRef(
    principal_id="usr_3OUTSIDER00000000000000000",
    display_name="a principal of another workspace",
    status=PrincipalStatus.ACTIVE,
)


async def setup_evidence() -> tuple[
    MemoryStore, StoreEvidenceRetriever, Run, str, ContractSignature
]:
    """A workspace the principal belongs to, one P7 experience, and the signature to ask for.

    The membership row is part of the *precondition* and not of any single assertion: §10.1
    makes the permission proof part of what makes a record eligible, so a store with no
    membership would make every test below pass for the wrong reason.
    """

    store = MemoryStore()
    await store.create_project(
        Project(
            project_id=PROJECT_ID,
            name="v0.4 M3 evidence retrieval",
            repository_path=Path("/tmp/accretion-v04-m3-evidence"),
        )
    )
    await store.upsert_workspace(
        WorkspaceEntity(workspace_id=WORKSPACE_ID, name="M3 evidence")
    )
    await store.upsert_workspace_membership(
        WorkspaceMembership(
            membership_id=new_id("workspace_membership"),
            workspace_id=WORKSPACE_ID,
            principal_id=PRINCIPAL.principal_id,
            role=WorkspaceRole.DEVELOPER,
        )
    )
    run = Run(
        run_id=new_id("run"),
        task_id=new_id("task"),
        project_id=PROJECT_ID,
        provider=Provider.FAKE,
        state=RunState.SUCCEEDED,
        principal_id=PRINCIPAL.principal_id,
    )
    experience = await seed_experience(store, run, "evidence")
    signature = record_signature_for(make_node(run, node_key="implement-migration"))
    return store, StoreEvidenceRetriever(store), run, experience.experience_id, signature


async def put_record(
    store: MemoryStore,
    experience_id: str,
    signature: ContractSignature,
    **overrides: Any,
) -> ExperienceRecord:
    """One stored projection, eligible and uncontradicted unless a test says otherwise."""

    document: dict[str, Any] = {
        "contract_id": new_id("experience"),
        "workspace_id": WORKSPACE_ID,
        "project_id": PROJECT_ID,
        "created_at": SEALED.isoformat(),
        "contract_signature": signature.model_dump(mode="json"),
        "eligible_for_learning": True,
        "labels": {EXPERIENCE_ID_LABEL: experience_id},
    }
    document.update(overrides)
    record = build(ExperienceRecord, **document)
    return await store.put_experience_record(record, experience_id=experience_id)


async def retrieved(
    retriever: StoreEvidenceRetriever,
    signature: ContractSignature,
    *,
    project_id: str | None = PROJECT_ID,
    principal: PrincipalRef = PRINCIPAL,
    as_of: datetime = AS_OF,
) -> list[str]:
    """The contract ids ``retrieve`` answered with, in the order it answered."""

    return [
        record.contract_id
        for record in await retriever.retrieve(
            workspace_id=WORKSPACE_ID,
            project_id=project_id,
            signature=signature,
            principal=principal,
            as_of=as_of,
        )
    ]


async def test_an_empty_store_answers_with_no_records_and_raises_nothing() -> None:
    """A cold start is a normal path in §9.4 and must not look like a failed retrieval.

    Raising here would reach the routing service's ``except`` and be recorded as
    ``EVIDENCE_UNAVAILABLE`` — "there may have been history and we could not read it" — on a
    project whose honest answer is "there is none".
    """

    _store, retriever, _run, _experience_id, signature = await setup_evidence()

    assert await retrieved(retriever, signature) == []


async def test_only_records_carrying_this_nodes_signature_are_evidence_about_it() -> None:
    """§7.10 makes the whole signature the retrieval key, not any part of it.

    The distractor differs in exactly one digest, which is the case a substring or
    "same objective" match would get wrong: two nodes graded against different verification
    specs are not comparable outcomes even when everything else about them agrees (ADR-044).
    """

    store, retriever, _run, experience_id, signature = await setup_evidence()
    other = signature.model_copy(update={"verification_spec_hash": "f" * 64})
    wanted = await put_record(store, experience_id, signature)
    await put_record(store, experience_id, other)

    assert await retrieved(retriever, signature) == [wanted.contract_id]


async def test_a_record_that_is_not_eligible_for_learning_is_never_returned() -> None:
    """``eligible_for_learning`` is the projector's verdict and this layer may not revisit it."""

    store, retriever, _run, experience_id, signature = await setup_evidence()
    await put_record(store, experience_id, signature, eligible_for_learning=False)

    assert await retrieved(retriever, signature) == []


async def test_neither_an_open_nor_a_resolved_contradiction_is_returned() -> None:
    """``RESOLVED`` is excluded too, and that is a different rule from ``OPEN``.

    §10.1 excludes an unresolved contradiction because nobody has adjudicated it. A resolved
    one has been adjudicated, and the row that carries the status is the row that took part in
    the disagreement — training on it would teach the router from the side that lost.
    """

    store, retriever, _run, experience_id, signature = await setup_evidence()
    await put_record(
        store,
        experience_id,
        signature,
        contradiction_status=ContradictionStatus.OPEN.value,
        eligible_for_learning=False,
    )
    await put_record(
        store,
        experience_id,
        signature,
        contradiction_status=ContradictionStatus.RESOLVED.value,
    )

    assert await retrieved(retriever, signature) == []


async def test_a_superseded_generation_is_not_returned_beside_the_revision_replacing_it(
) -> None:
    """One line of a projection is one piece of evidence, whatever its history is.

    ``list_experience_records`` returns every generation, so a retriever that did not take
    heads would count one outcome twice and would count the attribution that was corrected
    alongside the correction.
    """

    store, retriever, _run, experience_id, signature = await setup_evidence()
    root = await put_record(store, experience_id, signature)
    revision = await put_record(
        store,
        experience_id,
        signature,
        supersedes_contract_id=root.contract_id,
        created_at=(SEALED + timedelta(minutes=5)).isoformat(),
    )

    assert await retrieved(retriever, signature) == [revision.contract_id]


async def test_a_revision_whose_id_names_no_experience_row_is_still_evidence() -> None:
    """Migration 0020's trap, closed by resolving the P7 row through the label.

    A revision's ``contract_id`` names no ``experiences`` row. A retriever that dereferenced
    the id would find nothing, read that as a retraction and silently drop exactly the records
    M3a.2 writes to correct the others — with no error, no counter and no way to notice.
    """

    store, retriever, _run, experience_id, signature = await setup_evidence()
    revision = await put_record(store, experience_id, signature)

    assert await store.get_experience(revision.contract_id) is None
    assert await retrieved(retriever, signature) == [revision.contract_id]


async def test_a_retracted_experience_takes_its_projection_out_of_the_evidence() -> None:
    """ADR-054 b: eligibility is decided by dereference and can only ever narrow."""

    store, retriever, _run, experience_id, signature = await setup_evidence()
    record = await put_record(store, experience_id, signature)
    assert await retrieved(retriever, signature) == [record.contract_id]

    await store.retract_experience(
        ModerationAction(
            action_id=new_id("moderation_action"),
            experience_id=experience_id,
            action=ModerationActionType.RETRACT,
            reason="the trajectory contained a secret",
            expected_revision=1,
            resulting_revision=2,
            actor="operator",
        )
    )

    assert await retrieved(retriever, signature) == []


async def test_a_project_scoped_record_is_invisible_to_a_workspace_wide_retrieval() -> None:
    """§7.10's two visibilities are two different grants, and only one crosses a project.

    Asked without a project the retriever sees the ``TEAM_WORKSPACE`` record only; asked with
    the record's own project it sees both. The project-scoped record is not missing — it was
    never shared that widely.
    """

    store, retriever, _run, experience_id, signature = await setup_evidence()
    project_scoped = await put_record(store, experience_id, signature)
    shared = await put_record(
        store,
        experience_id,
        signature,
        visibility=Visibility.TEAM_WORKSPACE.value,
        permission_provenance={
            **build(ExperienceRecord).permission_provenance.model_dump(mode="json"),
            "scope": Visibility.TEAM_WORKSPACE.value,
        },
        created_at=(SEALED + timedelta(minutes=1)).isoformat(),
    )

    assert await retrieved(retriever, signature, project_id=None) == [shared.contract_id]
    assert await retrieved(retriever, signature) == [
        project_scoped.contract_id,
        shared.contract_id,
    ]


async def test_a_principal_with_no_membership_in_the_workspace_retrieves_nothing() -> None:
    """§10.1's permission proof, asked of the store rather than of the reference.

    The outsider's ``PrincipalRef`` says ``ACTIVE`` — that value is synthesised from the run
    and is never authority — so a retriever that read the reference's own status would hand a
    whole workspace's history to a principal who is not in it.
    """

    store, retriever, _run, experience_id, signature = await setup_evidence()
    await put_record(store, experience_id, signature)

    assert await retrieved(retriever, signature, principal=OUTSIDER) == []


async def test_a_record_sealed_after_the_decision_instant_is_not_yet_evidence() -> None:
    """A router may only be informed by what existed when it decided.

    Without this the same routing request replayed at two moments would retrieve two bodies of
    evidence, and §8.2's "identical immutable inputs return the same receipt" would be false
    for a decision nobody amended.
    """

    store, retriever, _run, experience_id, signature = await setup_evidence()
    later = await put_record(
        store,
        experience_id,
        signature,
        created_at=(AS_OF + timedelta(minutes=1)).isoformat(),
    )

    assert await retrieved(retriever, signature) == []
    assert await retrieved(
        retriever, signature, as_of=AS_OF + timedelta(hours=1)
    ) == [later.contract_id]


async def test_records_are_returned_oldest_first_by_created_at_then_contract_id() -> None:
    """The order the receipt's ``experience_refs`` inherit, pinned so two backends agree.

    Two of the three records share an instant, which is the case that separates a real total
    order from one that happens to hold because a dict preserved insertion order.
    """

    store, retriever, _run, experience_id, signature = await setup_evidence()
    together = (SEALED + timedelta(minutes=2)).isoformat()
    first = await put_record(store, experience_id, signature)
    tied_a = await put_record(store, experience_id, signature, created_at=together)
    tied_b = await put_record(store, experience_id, signature, created_at=together)

    assert await retrieved(retriever, signature) == [
        first.contract_id,
        *sorted([tied_a.contract_id, tied_b.contract_id]),
    ]


async def test_the_key_a_projection_is_written_under_is_the_key_the_router_retrieves_with(
    tmp_path: Path,
) -> None:
    """The seam this whole module exists to close, asserted as an equality between two functions.

    :func:`~accretion.feedback.experience.record_signature_for` keys every record the projector
    writes; :func:`~accretion.routing.stages.node_signature` is what
    ``DefaultNodeRoutingService`` hands the retriever. Nothing anywhere compares them, and a
    disagreement raises nothing: retrieval simply returns an empty list forever, the receipt
    cites no experience, and the whole feedback loop reads as a permanent cold start.

    The node is a *really frozen* one from the M2 fixture rather than a hand-built contract,
    because the two things that used to differ — the objective digest and the capability digest
    — are exactly the two the freezer fills in.
    """

    execution = await _routable_execution(tmp_path)
    node = execution.frozen.node_contract

    assert record_signature_for(node) == node_signature(
        node, objective_digest=execution.frozen.objective_ref.objective_contract_hash
    )
