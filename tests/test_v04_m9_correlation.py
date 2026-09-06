"""AC4-M9-044 — the §16.2 correlation chain, walked end to end by ids.

Every link is walked over ONE real run: the M2 routed-graph fixture on a ``MemoryStore`` with a
``FAKE`` runtime, the real routing service, the real M3 feedback pipeline and the real M8
promotion evaluator. Nothing is asserted about a document this file constructed unless the
construction itself is named below, because the claim is about what the SHIPPED writers record,
and a chain assembled by the test would prove only that the test can assemble one.

The chain, in the order the walk follows it::

    task
      -> run                                (run.task_id)
      -> graph revision                     (NodeContract.run_graph_id, .graph_revision,
                                             agreeing with the run's own checkpoints)
      -> node contract                      (labels run_id / node_key / attempt)
      -> routing request                    (RoutingContext.node_contract_ref)
      -> routing receipt                    (receipt.routing_request_id, .node_contract_hash)
      -> RUNTIME_CALL_STARTED               (payload.routing_receipt_id + .node_contract_hash,
                                             event.node_id naming the projection node)
      -> ROUTING_DECISION_CREATED           (causation_id == the receipt's contract id)
      -> verification result                (execution_instance_id)
      -> experience record                  (source_node_execution_id, configuration_hash)
      -> training snapshot manifest         (included_experience_ids)
      -> promotion report                   (training_snapshot_id, holdout_definition_id)
      -> back to the run                    (ROUTER_PROMOTION_EVALUATED on the run's own log)

The last arrow is the one that makes this a *chain* rather than a list: the promotion the
evidence produced is announced on the event stream of the run that produced the evidence, so an
operator holding a run id can reach the promotion and an auditor holding a promotion report can
reach the run.

**One link is not walkable through the shipped writer, and this file pins the reason rather than
routing around it.** ``SnapshotBuilder`` dereferences ``ExperienceRecord.contract_id`` through
``get_experience`` (``routing/training_snapshot.py``), which resolves only for a record whose
contract id IS a P7 experience id; the M3 pipeline mints ``derived_id("experience",
experience_id, execution_instance_id, "1")`` for every node of a run and files it under the
experience id in the store's own column. So no experience a real routed run produces can enter a
training snapshot today, and the builder reports it as "nothing eligible" rather than as a join
that missed. ``test_the_snapshot_builder_cannot_yet_include_a_run_projected_record`` states that
precisely; the chain test seals its training snapshot from the run's records directly, through
the same contract the builder would have written, so that the rest of the chain is still proven
end to end and the gap is one named test rather than a silent hole.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_v04_m2_end_to_end import _wait_for_approval, _write_valid_output
from test_v04_m3_e2e import scripted_registry, setup_feedback_run
from test_v04_m4_train import VOCABULARY
from test_v04_m8_evaluator import (
    HOLDOUT_FIRST,
    HOLDOUT_WINDOW,
    PER_PROJECT,
    Corpus,
    ScriptedLoader,
    ScriptedPredictor,
    holdout_evaluation,
    router_version,
    seed_node,
    snapshot_over,
)
from test_v04_m8_promotion import build as build_free

from accretion.contracts import (
    ApprovalDecisionValue,
    EventType,
    PrincipalRef,
    PrincipalStatus,
    Project,
    Provider,
    RunState,
    VerificationStatus,
)
from accretion.contracts.routing import RouterStatus, RouterTrainingSnapshot
from accretion.feedback.experience import EXPERIENCE_ID_LABEL
from accretion.ids import new_id
from accretion.persistence.store import MemoryStore
from accretion.routing.artifacts import ArtifactStore
from accretion.routing.ope import default_promotion_config
from accretion.routing.promotion import PromotionEvaluator, RollbackDrill
from accretion.routing.training_snapshot import SnapshotBuilder, SnapshotRules
from accretion.runtimes.fake import FakeCallOutcome, FakeRuntime

OPERATOR = PrincipalRef(
    principal_id="usr_M9CORRELATIONOPERATOR000000",
    display_name="v0.4 M9 correlation operator",
    status=PrincipalStatus.ACTIVE,
)

HOLDOUT_PROJECTS = 6
"""Six holdout projects, as ``setup_corpus`` uses: enough rows for the gate to score."""


class Walked:
    """One routed run, driven to SUCCEEDED, with the store that recorded it."""

    def __init__(self, fixture, run) -> None:  # type: ignore[no-untyped-def]
        self.fixture = fixture
        self.run = run
        self.store: MemoryStore = fixture.store
        self.workspace_id: str = fixture.workspace_id


async def _routed_run(tmp_path: Path) -> Walked:
    """The M2 routed graph with M3's pipeline attached, driven to a verified success.

    The verifiers are scripted to PASS on their first and only call because the projection an
    ``INCONCLUSIVE`` verdict produces is ``PENDING`` and therefore not eligible for learning —
    a run whose evidence no snapshot could ever name would make the last two links of the chain
    untestable for a reason that has nothing to do with correlation. Everything else is the
    shipped scheduler.
    """

    fixture, _materializer = await setup_feedback_run(
        tmp_path,
        runtime_factory=lambda _store, _workspace: FakeRuntime(
            scripted_outcomes=[FakeCallOutcome(), FakeCallOutcome(hook=_write_valid_output)]
        ),
        verifiers=scripted_registry(VerificationStatus.PASS),
    )
    manager = fixture.manager
    run = await manager.start_run(
        fixture.task_id, Provider.FAKE, principal_id=fixture.principal_id
    )
    background = manager.background[run.run_id]
    for _ in range(2):
        if background.done():
            break
        approval = await _wait_for_approval(manager, run.run_id)
        await manager.resolve_approval(approval, ApprovalDecisionValue.APPROVE)
    await asyncio.wait_for(background, 30)

    final = await fixture.store.get_run(run.run_id)
    assert final is not None and final.state is RunState.SUCCEEDED, final
    return Walked(fixture, final)


async def _seed_holdout(walked: Walked) -> Corpus:
    """Six projects of holdout evidence in February, in the run's own workspace and store.

    The holdout has to come from projects the candidate was not fitted on (AC4-M8-036) and the
    run supplies exactly one project, so the counter-evidence is seeded rather than run: this
    file is about the chain, and executing six more runs would add minutes and no link.
    """

    holdout_projects: list[str] = []
    minted = 0
    for index in range(HOLDOUT_PROJECTS):
        project_id = new_id("project")
        holdout_projects.append(project_id)
        await walked.store.create_project(
            Project(
                project_id=project_id,
                name=f"v0.4 M9 holdout project {index}",
                repository_path=Path("/tmp/accretion-v04-m9-correlation"),
            )
        )
        for slot in range(PER_PROJECT):
            minted += 1
            await seed_node(
                walked.store,
                workspace_id=walked.workspace_id,
                project_id=project_id,
                label=f"holdout-{index}-{slot}",
                created_at=HOLDOUT_FIRST + timedelta(hours=minted),
                succeeded=slot != 3,
            )
    return Corpus(
        store=walked.store,
        workspace_id=walked.workspace_id,
        principal=OPERATOR,
        training_projects=(walked.run.project_id,),
        holdout_projects=tuple(holdout_projects),
    )


def _learnable(records):  # type: ignore[no-untyped-def]
    """The run's projections a snapshot may learn from, in store order."""

    return [record for record in records if record.eligible_for_learning]


@pytest.mark.acceptance("AC4-M9-044")
async def test_the_chain_from_the_task_to_the_promotion_report_is_walkable_by_ids(
    tmp_path: Path,
) -> None:
    """Every §16.2 hop, by id, over one run and the promotion its evidence authorised.

    The mutation this is written against: dropping ``routing_receipt_id`` from
    ``RUNTIME_CALL_STARTED``'s payload. That single field is what ties an executed call to the
    decision that produced it — the run page's whole routing index is built from it
    (ADR4-M9-002) — and without it the walk stops at the receipt with no way back to a node.
    """

    walked = await _routed_run(tmp_path)
    store, run = walked.store, walked.run

    # task -> run
    task = await store.get_task(walked.fixture.task_id)
    assert task is not None
    assert run.task_id == task.envelope.task_id

    # run -> graph revision -> node contracts
    contracts = [
        contract
        for contract in await store.list_node_contracts(workspace_id=walked.workspace_id)
        if contract.labels.get("run_id") == run.run_id
    ]
    assert contracts, "the routed run froze no node contract"
    checkpoints = await store.list_checkpoints(run.run_id)
    assert checkpoints, "a run that reached SUCCEEDED wrote no checkpoint"
    assert {contract.run_graph_id for contract in contracts} == {
        checkpoint.run_graph_id for checkpoint in checkpoints
    }
    assert {contract.graph_revision for contract in contracts} == {
        checkpoint.graph_revision for checkpoint in checkpoints
    }

    # node contract -> routing request -> receipt
    requests = {
        context.contract_id: context
        for context in await store.list_routing_requests(
            workspace_id=walked.workspace_id, project_id=run.project_id
        )
    }
    receipts = await store.list_routing_receipts(
        workspace_id=walked.workspace_id, project_id=run.project_id
    )
    assert receipts, "node routing recorded no receipt"
    by_hash = {contract.immutable_hash: contract for contract in contracts}
    for receipt in receipts:
        context = requests[receipt.routing_request_id]
        node = by_hash[receipt.node_contract_hash]
        assert context.node_contract_ref.node_contract_id == node.contract_id
        assert context.node_contract_ref.immutable_hash == node.immutable_hash

    # receipt -> the two events that name it
    events = await store.list_events(run.run_id)
    started = [
        event for event in events if event.normalized_type is EventType.RUNTIME_CALL_STARTED
    ]
    assert started, "no runtime call was dispatched"
    dispatched_receipts = set()
    for event in started:
        assert "routing_receipt_id" in event.payload, (
            "a routed runtime call must carry the receipt it was dispatched under; without "
            "the stamp there is no way back from an executed call to its decision"
        )
        receipt_id = str(event.payload["routing_receipt_id"])
        dispatched_receipts.add(receipt_id)
        receipt = next(item for item in receipts if item.contract_id == receipt_id)
        assert event.payload["node_contract_hash"] == receipt.node_contract_hash
        # The projection node id the graph endpoint reports, which is what makes the receipt
        # findable from the canvas the run page draws.
        node = by_hash[receipt.node_contract_hash]
        assert event.node_id == f"{run.run_id}:{node.labels['node_key']}"

    declared = {
        event.causation_id
        for event in events
        if event.normalized_type is EventType.ROUTING_DECISION_CREATED
    }
    assert declared == {receipt.contract_id for receipt in receipts}
    for event in events:
        if event.normalized_type is EventType.ROUTING_DECISION_CREATED:
            assert event.payload["receipt_id"] == event.causation_id

    # receipt -> verification result, through the execution instance the node contract names
    verifications = await store.list_verification_results(
        workspace_id=walked.workspace_id, project_id=run.project_id
    )
    assert verifications, "the run recorded no independent verification result"
    instances = {contract.execution_instance_id for contract in contracts}
    assert {result.execution_instance_id for result in verifications} <= instances

    # -> experience record
    records = await store.list_experience_records(workspace_id=walked.workspace_id)
    assert records, "the feedback pipeline projected no experience record"
    execution_to_receipt = {
        by_hash[receipt.node_contract_hash].execution_instance_id: receipt
        for receipt in receipts
    }
    for record in records:
        assert record.labels["run_id"] == run.run_id
        receipt = execution_to_receipt[record.source_node_execution_id]
        assert record.configuration_hash == receipt.selected_configuration_hash
        node = by_hash[receipt.node_contract_hash]
        assert record.labels["node_id"] == node.node_id
        assert record.project_id == run.project_id

    learnable = _learnable(records)
    assert learnable, "no projection of a verified run was eligible for learning"
    verified = learnable[0]
    assert verified.source_node_execution_id in instances
    assert execution_to_receipt[verified.source_node_execution_id].contract_id in (
        dispatched_receipts
    )

    # -> training snapshot manifest
    corpus = await _seed_holdout(walked)
    holdout = await snapshot_over(corpus, HOLDOUT_WINDOW)
    training = await store.put_router_training_snapshot(
        build_free(
            RouterTrainingSnapshot,
            workspace_id=walked.workspace_id,
            included_experience_ids=[record.contract_id for record in learnable],
            split={
                "training_project_ids": [run.project_id],
                "holdout_project_ids": sorted(corpus.holdout_projects),
                "validation_project_ids": [],
            },
            window_start="2026-01-01T00:00:00Z",
            window_end="2026-02-01T00:00:00Z",
        )
    )
    assert verified.contract_id in training.included_experience_ids
    manifest_projects = set()
    for experience_id in training.included_experience_ids:
        named = await store.get_experience_record(experience_id)
        assert named is not None, experience_id
        manifest_projects.add(named.project_id)
    assert manifest_projects == {run.project_id}

    # -> promotion report, and back to the run
    report = await _evaluate(walked, corpus, training, holdout, tmp_path)
    assert report.training_snapshot_id == training.contract_id
    assert report.holdout_definition_id == holdout.contract_id

    stored = await store.get_router_promotion_report(report.contract_id)
    assert stored is not None and stored.contract_id == report.contract_id
    announcements = [
        event
        for event in await store.list_events(run.run_id)
        if event.normalized_type is EventType.ROUTER_PROMOTION_EVALUATED
    ]
    assert [event.payload["report_id"] for event in announcements] == [report.contract_id]

    # The whole chain, once more as one closed walk: from the announced report back to the
    # task, through nothing but stored ids.
    walked_report = await store.get_router_promotion_report(
        str(announcements[0].payload["report_id"])
    )
    assert walked_report is not None
    snapshot = await store.get_router_training_snapshot(walked_report.training_snapshot_id)
    assert snapshot is not None
    record = await store.get_experience_record(snapshot.included_experience_ids[0])
    assert record is not None
    receipt = execution_to_receipt[record.source_node_execution_id]
    context = requests[receipt.routing_request_id]
    node = by_hash[receipt.node_contract_hash]
    assert context.node_contract_ref.node_contract_id == node.contract_id
    assert node.labels["run_id"] == run.run_id
    assert (await store.get_run(node.labels["run_id"])).task_id == task.envelope.task_id


async def _evaluate(walked: Walked, corpus: Corpus, training, holdout, tmp_path: Path):  # type: ignore[no-untyped-def]
    """Score the candidate against the incumbent on the seeded holdout, and seal the report.

    The two versions, the two artefacts and the scripted predictor are the M8 evaluator suite's
    own helpers, imported rather than rebuilt: this file is not re-testing the gate, it is
    following the id from the report the gate seals back to the evidence it was computed over.
    """

    artifacts = ArtifactStore(tmp_path / "promotion-artifacts")
    artifact_digest = artifacts.save(json.dumps({"artifact": "scripted"}).encode())
    calibration_digest = artifacts.save(json.dumps({"calibration": "scripted"}).encode())
    candidate_digest = artifacts.save(
        holdout_evaluation(
            training.contract_id,
            corpus.holdout_projects,
            rate=0.9,
            lcb=0.8,
            ece=0.02,
            false_acceptance=0.05,
        )
        .model_dump_json()
        .encode()
    )
    baseline_digest = artifacts.save(
        holdout_evaluation(
            training.contract_id,
            corpus.holdout_projects,
            rate=0.7,
            lcb=0.6,
            ece=0.03,
            false_acceptance=0.06,
        )
        .model_dump_json()
        .encode()
    )
    baseline = router_version(
        walked.workspace_id,
        snapshot_id=training.contract_id,
        status=RouterStatus.ACTIVE,
        artifact_digest=artifact_digest,
        calibration_digest=calibration_digest,
        holdout_digest=baseline_digest,
    )
    candidate = router_version(
        walked.workspace_id,
        snapshot_id=training.contract_id,
        status=RouterStatus.CANDIDATE,
        artifact_digest=artifact_digest,
        calibration_digest=calibration_digest,
        holdout_digest=candidate_digest,
    )
    for version in (baseline, candidate):
        await walked.store.put_router_model_version(version)

    loader = ScriptedLoader(
        walked.store,
        artifacts,
        {
            candidate.contract_id: ScriptedPredictor(mean=0.95, lower=0.9),
            baseline.contract_id: ScriptedPredictor(mean=0.1, lower=0.0),
        },
    )
    evaluator = PromotionEvaluator(
        walked.store,
        loader,
        artifacts,
        default_promotion_config(),
        RollbackDrill(loader, artifacts),
        vocabulary=VOCABULARY,
    )
    return await evaluator.evaluate(
        candidate.contract_id,
        baseline.contract_id,
        holdout.contract_id,
        OPERATOR,
        walked.run.run_id,
    )


async def test_the_snapshot_builder_cannot_yet_include_a_run_projected_record(
    tmp_path: Path,
) -> None:
    """The one link of §16.2 that the shipped writers do not join, pinned with its cause.

    ``SnapshotBuilder.build`` reads ``get_experience(record.contract_id)`` and skips a record
    whose experience is missing, treating it as retracted (ADR-054 b: the projection "is keyed
    by the same experience_id — carried as the header's contract_id"). That holds for a record
    built by the M4 and M8 fixtures, whose ``contract_id`` IS an experience id. It cannot hold
    for a real run: one run materialises ONE P7 experience and projects one record per routed
    node, so ``feedback/experience.py`` mints ``derived_id("experience", experience_id,
    execution_instance_id, "1")`` and passes the experience id to the store separately.

    The consequence is not a smaller snapshot, it is an empty one: the builder refuses the whole
    window with "no experience record ... is eligible for learning", which reads as "this run
    produced no evidence" when what happened is that its evidence could not be dereferenced.

    Deliberately unmarked. It claims no criterion because it is not a property anyone wants;
    it is the seam, stated where the next person to touch either module will see it. When the
    join is repaired this test goes red, and the chain test above should then seal its training
    snapshot with ``SnapshotBuilder`` instead.
    """

    walked = await _routed_run(tmp_path)
    records = await walked.store.list_experience_records(workspace_id=walked.workspace_id)
    learnable = _learnable(records)
    assert learnable, "the run must produce at least one learnable projection"
    record = learnable[0]

    # The record is eligible, and its evidence is in the store — under the experience id it
    # carries as a LABEL, not under its own contract id.
    assert record.eligible_for_learning
    assert await walked.store.get_experience(record.contract_id) is None
    assert await walked.store.get_experience(record.labels[EXPERIENCE_ID_LABEL]) is not None

    now = datetime.now(UTC)
    with pytest.raises(ValueError) as refusal:
        await SnapshotBuilder(walked.store).build(
            workspace_id=walked.workspace_id,
            window=(now - timedelta(days=1), now + timedelta(days=1)),
            split=build_free(
                RouterTrainingSnapshot,
                workspace_id=walked.workspace_id,
                included_experience_ids=[record.contract_id],
            ).split,
            rules=SnapshotRules(),
            created_by=OPERATOR,
            clock=lambda: now,
        )
    assert "is eligible for learning" in str(refusal.value)
